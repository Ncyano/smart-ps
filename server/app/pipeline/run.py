"""产线编排：素材 + 例图 + 文字 → 成品。

流程：

```
落盘 → ① 风格蒸馏 → ② 素材选优 → ③ 打包与提示词
     → ③.4 合规与数据来源（硬拦截，生成前）
     → ③.5 品类一致（硬拦截，生成前）
     → ④ 生成候选（或合成类模板直接排版）
     → ⑤ 回正 / 语义判定 / 保真分项 / 交付闸门
```

两个顺序是刻意的：

1. **合规与品类在生成之前。** 文案写着"每天一粒"而素材是气垫霜，
   或者写着"全网最低"，后面每一张都是废的。在这里失败等于一分钱没花。
2. **闸门在最后、按项判。** 保真、版式、语义、授权、数据来源各自过一遍，
   任何一项没过都不能靠总分拉回来。见 `gate.py`。

检查点：每完成一步就把中间结果落到 `resume.json`。服务重启后可以从未完成的
阶段续跑，**不会重复付费**——最贵的 ④ 生成只做一次。见 `jobs.recover()`。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from .. import claims as claimsmod
from .. import gate
from .. import rights as rightsmod
from .. import store
from .. import templates
from ..config import load
from ..engines import registry
from ..engines.base import EngineError, GenRequest, RefImage
from . import pack as packmod
from . import steps
from . import fidelity as fid
from . import judge
from . import category

# 检查点里的阶段顺序。恢复时按这个判断"到哪一步了"
STEPS = ("materials", "profile", "scored", "prompt", "claims", "category", "generated")


def _ckpt_path(batch_id: str) -> Path:
    return store.batch_dir(batch_id) / "resume.json"


def _dump(obj):
    """Path / Image 之类不能直接 json 化，统一转成相对 data/ 的路径字符串。"""
    if isinstance(obj, Path):
        try:
            return {"__path__": obj.resolve().relative_to(store.DATA_DIR.resolve()).as_posix()}
        except ValueError:
            return {"__path__": str(obj)}
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    return obj


def _undump(obj):
    if isinstance(obj, dict):
        if "__path__" in obj and len(obj) == 1:
            p = Path(obj["__path__"])
            return p if p.is_absolute() else store.DATA_DIR / p
        return {k: _undump(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_undump(v) for v in obj]
    return obj


def save_ckpt(batch_id: str, st: dict) -> None:
    payload = {k: _dump(v) for k, v in st.items()}
    payload["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    _ckpt_path(batch_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ckpt(batch_id: str) -> dict | None:
    p = _ckpt_path(batch_id)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {k: _undump(v) for k, v in raw.items() if not k.startswith("_")}
    except Exception:  # noqa: BLE001 检查点坏了就当没有，重新跑
        return None


def _fan_out(engine, gen_req: GenRequest, out_dir: Path, n: int, retries: int = 1) -> dict:
    """按引擎的单次能力把 n 张拆成多次请求并发执行。

    为什么要拆：火山方舟的 `n` 参数实测无效（永远只回 1 张），
    之前直接把 candidates 塞进 GenRequest，用户要 3 张实际只得到 1 张。
    这里用 `caps.batch` 声明每个引擎一次能出几张，产线负责拆分与聚合。
    """
    per = max(1, min(int(engine.caps.batch or 1), n))
    plan, left, i = [], n, 0
    while left > 0:
        i += 1
        k = min(per, left)
        plan.append((i, k))
        left -= k

    def run_one(idx: int, k: int):
        req = replace(gen_req, n=k)
        sub = out_dir / f"r{idx}"
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                return idx, engine.generate(req, sub), None
            except Exception as exc:  # noqa: BLE001 单批失败不影响其余
                last = exc
        return idx, None, str(last)

    results: dict[int, object] = {}
    errors: list[str] = []
    if plan:
        with ThreadPoolExecutor(max_workers=min(len(plan), 4)) as pool:
            futures = [pool.submit(run_one, idx, k) for idx, k in plan]
            for fut in as_completed(futures):
                idx, res, err = fut.result()
                if err:
                    errors.append(f"第 {idx} 批：{err}")
                elif res is not None:
                    results[idx] = res

    images: list[Path] = []
    cost = 0.0
    metas: list[dict] = []
    for idx in sorted(results):
        res = results[idx]
        images.extend(res.images)          # type: ignore[attr-defined]
        cost += float(res.cost_cny)        # type: ignore[attr-defined]
        metas.append(res.meta)             # type: ignore[attr-defined]

    return {
        "images": images,
        "cost": cost,
        "errors": errors,
        "rounds": len(plan),
        "per_request": per,
        "meta": metas,
    }


def _payload_image(payload, dest: Path) -> Image.Image | None:
    if payload is None:
        return None
    try:
        p = store.save_payload(payload.data_url, payload.name, dest)
        return Image.open(p)
    except Exception:  # noqa: BLE001 附加输入坏了不该拖垮整批
        return None


def run_batch(req, cfg: dict | None = None, batch_id: str | None = None, on_progress=None,
              resume: dict | None = None) -> dict:
    """跑一批。`batch_id` 可由调用方预先分配（异步任务需要先返回 id）。

    `resume` 传入上次的检查点则从断点续跑；不传就从头跑。
    `on_progress(stage: str, progress: float)` 用于把进度推给任务表。
    """
    cfg = cfg or load()
    t0 = time.time()
    st: dict = dict(resume or {})

    def tick(stage: str, p: float) -> None:
        if on_progress:
            try:
                on_progress(stage, p)
            except Exception:  # noqa: BLE001 进度回调不能影响主流程
                pass

    if not st and not req.materials:
        raise ValueError("至少需要一张素材图")

    batch_id = batch_id or store.new_batch_id()
    work = store.batch_dir(batch_id)
    st["batch_id"] = batch_id
    # 续跑时只需要这些标量，不需要原始 base64 图（几十 MB）
    st.setdefault("meta", {
        "instruction": getattr(req, "instruction", ""),
        "engine": getattr(req, "engine", None),
        "category": getattr(req, "category", "auto"),
        "rights": getattr(req, "rights", "unknown"),
        "claims": [c.model_dump() if hasattr(c, "model_dump") else c
                   for c in (getattr(req, "claims", None) or [])],
        "request_id": getattr(req, "request_id", ""),
    })
    st["corners"] = getattr(req, "corners", None) or st.get("corners")

    tpl = templates.get(getattr(req, "template", None) or st.get("template"))
    copy = getattr(req, "copy_text", None) or st.get("copy") or {}
    st["template"] = tpl.id
    st["copy"] = copy

    # ── 落盘原始输入（恢复时跳过） ──
    if "materials" not in st:
        tick("正在保存素材", 0.05)
        st["materials"] = [store.save_payload(m.data_url, m.name, work / "materials")
                           for m in req.materials]
        st["exemplars"] = [store.save_payload(e.data_url, e.name, work / "exemplars")
                           for e in getattr(req, "exemplars", []) or []]
        st["extra"] = {}
        for key in ("before", "after", "scene", "evidence"):
            payload = getattr(req, key, None)
            if payload is not None:
                st["extra"][key] = store.save_payload(payload.data_url, payload.name, work / key)
        save_ckpt(batch_id, st)
    materials: list[Path] = st["materials"]
    exemplars: list[Path] = st.get("exemplars") or []

    # ── ① 风格蒸馏（例图） ──
    if "profile" not in st:
        tick("正在分析参考图风格", 0.12)
        st["profile"] = steps.profile_from(exemplars) if exemplars else steps.default_profile()
        save_ckpt(batch_id, st)
    profile = st["profile"]

    # ── ② 素材打分选优 ──
    if "scored" not in st:
        tick("正在给素材打分", 0.18)
        scored = [packmod.score_material(p, p.name) for p in materials]
        scored.sort(key=lambda s: s["quality"], reverse=True)
        st["scored"] = scored
        save_ckpt(batch_id, st)
    scored = st["scored"]

    # ── ③ 参考图打包 + 提示词 ──
    if "prompt" not in st:
        tick("正在组装参考图与提示词", 0.24)
        refs, ref_notes = packmod.pack_refs(scored, exemplars, profile)
        prompt, negative = packmod.build_prompt(req.instruction, profile)
        if tpl.prompt_extra:
            prompt = prompt + "\n" + "\n".join(tpl.prompt_extra)
        if tpl.negative_extra:
            negative = "，".join([negative] + [",".join(tpl.negative_extra)])
        st["refs"] = [{"path": str(r.path), "role": r.role, "label": r.label} for r in refs]
        st["ref_notes"] = ref_notes
        st["prompt"], st["negative"] = prompt, negative
        save_ckpt(batch_id, st)
    refs, ref_notes = st["refs"], st["ref_notes"]
    prompt, negative = st["prompt"], st["negative"]

    # ── ③.4 合规与数据来源：绝对化 / 疗效 / 第三方商标 → 直接拦，
    #      数字没来源 → 不拦，但本批不能标"可交付"（闸门会接住） ──
    if "claims" not in st:
        tick("正在检查合规与数据来源", 0.255)
        audit = claimsmod.assert_clean(
            copy, cfg, instruction=req.instruction,
            claims=getattr(req, "claims", None) or [],
            template=tpl.id,
        )
        st["claims"] = audit.to_dict()
        save_ckpt(batch_id, st)
    claims_audit = st["claims"]

    # ── ③.5 品类一致性：文案说"每天一粒"、素材却是气垫霜，这种串台必须生成前拦住 ──
    if "category" not in st:
        tick("正在校验文案与产品品类", 0.26)
        with Image.open(Path(scored[0]["path"])) as _probe:
            cat_check = category.assert_consistent(
                copy, _probe, cfg,
                declared=getattr(req, "category", "auto") or st.get("category_declared", "auto"),
                instruction=req.instruction,
                template=tpl.id,
            )
        st["category"] = cat_check.to_dict()
        save_ckpt(batch_id, st)
    cat_check = st["category"]

    extra_imgs: dict[str, Image.Image] = {}
    for key, p in (st.get("extra") or {}).items():
        try:
            extra_imgs[key] = Image.open(p)
        except Exception:  # noqa: BLE001
            pass

    # ── ④ 生成候选（或合成类模板：一次模型调用都不发） ──
    # 检查点里叫 produced。这一步最贵，续跑时必须原样复用，绝不能重跑。
    if "produced" not in st:
        want = max(1, min(int(getattr(req, "candidates", None) or st.get("want") or 3), 6))
        st["want"] = want
        if tpl.compose:
            tick("正在做确定性合成（不调生成模型）", 0.45)
            produced = {"images": [], "cost": 0.0, "errors": [], "rounds": 0,
                        "per_request": 0, "meta": [], "compose": True}
        else:
            engine, engine_note = registry.resolve(cfg, req.engine)
            st["engine"], st["engine_note"] = engine.id, engine_note
            gen_req = GenRequest(
                prompt=prompt,
                negative=negative,
                refs=[RefImage(**r) if isinstance(r, dict) else r for r in refs],
                n=want,
                size=str(getattr(req, "size", None)
                         or cfg.get("engines", {}).get("seedream", {}).get("size") or "2K"),
            )
            st["gen_size"] = gen_req.size
            tick(f"正在生成候选（{want} 张）", 0.30)
            produced = _fan_out(engine, gen_req, work / "candidates", want)
            if not produced["images"]:
                detail = "；".join(produced["errors"]) or "引擎没有返回任何图片"
                raise EngineError(f"生成失败（{want} 张全部未产出）：{detail}")
        st["produced"] = produced
        save_ckpt(batch_id, st)
        tick(f"已生成 {len(produced['images'])} 张，正在评分", 0.80)

    produced = st["produced"]
    engine_id = st.get("engine") or ("compose" if tpl.compose else "unknown")
    engine_note = st.get("engine_note") or ("确定性合成，未调用生成模型" if tpl.compose else "")
    gen_size = st.get("gen_size") or "2K"

    # ── ⑤ 回正 → 语义判定 → 评分筛选 → 交付闸门 ──
    tick("正在评分与回正", 0.84)
    final_dir = work / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    source = fid.as_rgb(Image.open(Path(scored[0]["path"])))
    min_style = float(cfg.get("quality", {}).get("min_style", 0.60))

    ctx = {
        "profile": profile,
        "copy": copy,
        "before": extra_imgs.get("before"),
        "after": extra_imgs.get("after"),
        "scene": extra_imgs.get("scene"),
        "evidence": extra_imgs.get("evidence"),
        "corners": getattr(req, "corners", None) or st.get("corners"),
        "claims": claims_audit,
    }

    if not produced["images"]:
        # 合成类：只出一张，基准图按模板给
        if tpl.id == "pair":
            base = ctx.get("after") or ctx.get("before")
            base_im = fid.as_rgb(base) if base is not None else Image.new("RGB", (1080, 1350), "white")
        elif tpl.id == "screen":
            # 贴屏的"画面"是用户给的成品，原样用，不做风格回正
            base_im = Image.open(Path(scored[0]["path"])).convert("RGB")
        elif tpl.canvas:
            base_im = Image.new("RGB", tpl.canvas, "white")
        else:
            base_im = Image.new("RGB", (1080, 1350), "white")
        bases = [base_im]
    else:
        # 合成类/贴屏：画面是"要投放的成品"，不再按例图风格回正——
        # 它是素材，不是候选。拿它去 tone_match 会把干净的 KV 压暗（实测 222→93）。
        bases = [steps.finalize(Image.open(p).convert("RGB"), profile) if not tpl.compose
                 else Image.open(p).convert("RGB")
                 for p in produced["images"]]

    finals: list[tuple[Path, Image.Image]] = []
    layouts: list[dict | None] = []
    for i, base_im in enumerate(bases):
        final, lr = base_im, None
        if tpl.apply is not None:
            final, lr = tpl.apply(final, copy, ctx)
        final_path = final_dir / f"final_{i + 1}.png"
        final.save(final_path)
        finals.append((final_path, final))
        layouts.append(lr)

    semantics: list[judge.JudgeResult] = []
    if tpl.compose:
        semantics = [judge.JudgeResult(checked=False, error="合成类物料：内容由给定素材与排版产生，未做语义判定")
                     for _ in finals]
        judge_ok, judge_why = True, ""
    else:
        judge_ok, judge_why = judge.available(cfg)
        if judge_ok and finals:
            tick(f"正在做语义判定（{len(finals)} 张）", 0.88)
            with ThreadPoolExecutor(max_workers=min(4, len(finals))) as pool:
                semantics = list(pool.map(lambda t: judge.judge(source, t[1], cfg), finals))
        else:
            semantics = [judge.JudgeResult(checked=False, error=judge_why) for _ in finals]

    tick("正在评分", 0.93)
    candidates: list[dict] = []
    for i, (final_path, final) in enumerate(finals):
        if tpl.compose:
            audit = {
                "structure": None, "spread": None, "sharp": None, "scale": None,
                "score": None, "tier": "B", "mode": "compose", "reliable": True,
                "reasons": ["合成类物料：保真判定不适用（内容来自给定素材与排版）"],
                "checks": {k: {"status": "skip", "detail": "合成类物料不测像素保真"}
                           for k in ("subject", "geometry", "label", "overall")},
            }
            style = None
        else:
            audit = fid.assess(final, source, ignore_spread=tpl.reframes)
            style = round(steps.style_distance(final, profile), 3)

        reasons = list(audit["reasons"])
        tier = audit["tier"]
        # 没给例图就没有风格目标，别拿默认画像去扣分——
        # 否则价签/证据这类"本来就不追求风格"的物料每张都会被标"风格差距偏大"。
        has_style_target = bool(exemplars)
        if style is not None and has_style_target and style < min_style:
            reasons.append(f"风格贴合 {style} < {min_style}：与例图差距偏大")
            if tier == "A":
                tier = "B"

        lr = layouts[i]
        if lr and not lr.get("ok", True):
            reasons.extend(lr.get("reasons") or ["版式自检未通过"])
            if tier == "A":
                tier = "B"

        jr = semantics[i]
        tier, sem_notes = judge.apply_to_tier(jr, tier) if not tpl.compose else (tier, [])
        reasons.extend(sem_notes)
        if not jr.checked and jr.error and judge_ok and not tpl.compose:
            reasons.append(f"语义判定失败：{jr.error}")

        scores = {
            "fidelity": audit["score"],
            "style": style,
            "quality": round(steps.quality_score(final), 3),
            "structure": audit["structure"],
            "spread": audit["spread"],
            "sharp": audit["sharp"],
        }
        # score 只用于排序：分档由 tier 与闸门决定，权重不参与判定
        base = audit["score"] if audit["score"] is not None else 0.60
        total = 0.60 * base + 0.25 * (style if style is not None else 0.6) + 0.15 * scores["quality"]
        if jr.checked and jr.confidence:
            total += 0.05 * (jr.confidence / 100.0)

        raw_url = store.url_for(produced["images"][i]) if produced["images"] else ""
        candidates.append(
            {
                "index": i + 1,
                "raw_url": raw_url,
                "final_url": store.url_for(final_path),
                "tier": tier,
                "verdict": {"A": "可交付", "B": "待人工", "C": "不可用"}[tier],
                "scores": scores,
                "reasons": reasons,
                "fidelity_mode": audit["mode"],
                "fidelity_checks": audit.get("checks"),
                "subject_box": audit.get("subject_box"),
                "match_box": audit.get("match_box"),
                "semantic": jr.to_dict(),
                "layout": lr,
                "total": round(total, 3),
                # suggested = 算法推荐；chosen = 人工选定。两者不要混
                "suggested": False,
                "chosen": False,
                "review": "pending",
                # 人工确认项：默认未确认，闸门因此判"待人工"
                "subject_confirmed": False,
                "subject_confirmed_by": "",
                "subject_confirmed_at": "",
            }
        )

    batch = {
        "id": batch_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "instruction": req.instruction,
        "engine": engine_id,
        "engine_note": engine_note,
        "cost_cny": round(float(produced["cost"]), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "status": "needs_review",
        "tiers": {k: sum(1 for c in candidates if c["tier"] == k) for k in ("A", "B", "C")},
        "template": tpl.id,
        "template_version": tpl.version,
        "template_export": tpl.export,
        "template_compose": tpl.compose,
        "copy": copy,
        "category_check": cat_check,
        "claims_audit": claims_audit,
        # 素材授权：决定这批图能不能标"可正式交付"。声明了第三方/示例就只算内部参考
        "rights": rightsmod.resolve(
            getattr(req, "rights", "unknown")).to_dict(),
        "semantic": {
            "checked": bool(judge_ok) and not tpl.compose,
            "model": (semantics[0].model if semantics else ""),
            "reason": "合成类物料未做语义判定" if tpl.compose else ("" if judge_ok else judge_why),
        },
        "requested": 1 if tpl.compose else max(1, int(st.get("want") or 1)),
        "produced": len(candidates),
        "gen_errors": produced["errors"],
        "style_profile": profile,
        "recipe": {
            "prompt": prompt,
            "negative": negative,
            "refs": ref_notes,
            "candidates": st.get("want") or 1,
            "size": gen_size,
            "rounds": produced["rounds"],
            "per_request": produced["per_request"],
            "template": {"id": tpl.id, "version": tpl.version,
                         "export": tpl.export, "canvas": list(tpl.canvas) if tpl.canvas else None,
                         "compose": tpl.compose},
        },
        "materials": [
            {
                "name": m["name"],
                "url": store.url_for(Path(m["path"])),
                "width": m["width"],
                "height": m["height"],
                "quality": m["quality"],
                "sharpness": m["sharpness"],
                "exposure": m["exposure"],
            }
            for m in scored
        ],
        "exemplars": [store.url_for(p) for p in exemplars],
        "candidates": candidates,
        "engine_meta": produced["meta"],
        "extra_inputs": {k: store.url_for(p) for k, p in (st.get("extra") or {}).items()},
    }

    # ── 交付闸门：逐项判定，写回 verdict / suggested / status ──
    gate.apply(batch)

    tick("正在写入结果", 0.96)
    store.save_batch(batch)
    st["stage"] = "done"
    save_ckpt(batch_id, st)
    return batch
