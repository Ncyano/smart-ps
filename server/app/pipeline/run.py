"""产线编排：素材 + 例图 + 文字 → 成品。

六步：分组选优 → 参考图打包 → 生成候选 → 评分筛选 → 回正 → 质检交付。
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from .. import store
from .. import templates
from ..config import load
from ..engines import registry
from ..engines.base import EngineError, GenRequest
from . import pack as packmod
from . import steps
from . import fidelity as fid
from . import judge
from . import category
from .. import rights as rightsmod


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


def run_batch(req, cfg: dict | None = None, batch_id: str | None = None, on_progress=None) -> dict:
    """跑一批。`batch_id` 可由调用方预先分配（异步任务需要先返回 id）。

    `on_progress(stage: str, progress: float)` 用于把进度推给任务表。
    """
    cfg = cfg or load()
    t0 = time.time()

    def tick(stage: str, p: float) -> None:
        if on_progress:
            try:
                on_progress(stage, p)
            except Exception:  # noqa: BLE001 进度回调不能影响主流程
                pass

    if not req.materials:
        raise ValueError("至少需要一张素材图")

    batch_id = batch_id or store.new_batch_id()
    work = store.batch_dir(batch_id)

    # ── 落盘原始输入 ──
    tick("正在保存素材", 0.05)
    materials = [store.save_payload(m.data_url, m.name, work / "materials") for m in req.materials]
    exemplars = [store.save_payload(e.data_url, e.name, work / "exemplars") for e in req.exemplars]

    # ── ① 风格蒸馏（例图） ──
    tick("正在分析参考图风格", 0.12)
    profile = steps.profile_from(exemplars) if exemplars else steps.default_profile()

    # ── ② 素材打分选优 ──
    tick("正在给素材打分", 0.18)
    scored = [packmod.score_material(p, p.name) for p in materials]
    scored.sort(key=lambda s: s["quality"], reverse=True)

    # ── ③ 参考图打包 + 提示词 ──
    tick("正在组装参考图与提示词", 0.24)
    refs, ref_notes = packmod.pack_refs(scored, exemplars, profile)
    prompt, negative = packmod.build_prompt(req.instruction, profile)

    # 工艺模板：把这类图必须写死的约束追加进去（模板只管约束，不改流程）
    tpl = templates.get(req.template)
    if tpl.prompt_extra:
        prompt = prompt + "\n" + "\n".join(tpl.prompt_extra)
    if tpl.negative_extra:
        negative = "，".join([negative] + [",".join(tpl.negative_extra)])
    copy = req.copy_text or {}

    # ── ③.5 品类一致性：文案说"每天一粒"、素材却是气垫霜，这种串台必须生成前拦住 ──
    # 位置讲究：文案错了后面每一张都是废的，在这里失败等于一分钱没花。
    tick("正在校验文案与产品品类", 0.26)
    with Image.open(Path(scored[0]["path"])) as _probe:
        cat_check = category.check(
            copy, _probe, cfg,
            declared=getattr(req, "category", "auto"),
            instruction=req.instruction,
        )
    if not cat_check.ok:
        raise category.CategoryError("；".join(cat_check.reasons))

    # ── ④ 生成候选（按引擎能力拆分并发） ──
    engine, engine_note = registry.resolve(cfg, req.engine)
    want = max(1, min(int(req.candidates or 1), 6))
    gen_req = GenRequest(
        prompt=prompt,
        negative=negative,
        refs=refs,
        n=want,
        size=str(cfg.get("engines", {}).get("seedream", {}).get("size") or "2K"),
    )
    tick(f"正在生成候选（{want} 张）", 0.30)
    produced = _fan_out(engine, gen_req, work / "candidates", want)
    if not produced["images"]:
        detail = "；".join(produced["errors"]) or "引擎没有返回任何图片"
        raise EngineError(f"生成失败（{want} 张全部未产出）：{detail}")
    tick(f"已生成 {len(produced['images'])} 张，正在评分", 0.80)



    # ── ⑤ 回正 → 语义判定 → 评分筛选 ──
    # 顺序讲究：先全部回正落盘，再并发做语义判定（每张 2–4 秒的网络调用，
    # 串在循环里 4 张就是十几秒），最后统一算分。
    tick("正在评分与回正", 0.84)
    final_dir = work / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    # 注意用 as_rgb：素材若是透明 PNG，直接 convert("RGB") 会把背景压成黑
    source = fid.as_rgb(Image.open(Path(scored[0]["path"])))

    min_style = float(cfg.get("quality", {}).get("min_style", 0.60))
    verdict_of = {"A": "可交付", "B": "待人工", "C": "不可用"}

    finals: list[tuple[Path, Image.Image]] = []
    layouts: list[dict | None] = []
    for i, raw_path in enumerate(produced["images"]):
        final = steps.finalize(Image.open(raw_path).convert("RGB"), profile)
        # 模板后处理（卖点图排版 / 白底重排）。版式自检失败不阻断，但要记下来
        lr = None
        if tpl.apply is not None:
            final, lr = tpl.apply(final, copy, {"profile": profile})
        final_path = final_dir / f"final_{i + 1}.png"
        final.save(final_path)
        finals.append((final_path, final))
        layouts.append(lr)

    # 语义判定（视觉模型）。没开通就整体跳过，产线照常。
    judge_ok, judge_why = judge.available(cfg)
    semantics: list[judge.JudgeResult] = []
    if judge_ok and finals:
        tick(f"正在做语义判定（{len(finals)} 张）", 0.88)
        with ThreadPoolExecutor(max_workers=min(4, len(finals))) as pool:
            semantics = list(pool.map(lambda t: judge.judge(source, t[1], cfg), finals))
    else:
        semantics = [judge.JudgeResult(checked=False, error=judge_why) for _ in finals]

    tick("正在评分", 0.93)
    candidates: list[dict] = []
    for i, (final_path, final) in enumerate(finals):
        audit = fid.assess(final, source, ignore_spread=tpl.reframes)
        style = round(steps.style_distance(final, profile), 3)

        reasons = list(audit["reasons"])
        tier = audit["tier"]
        if style < min_style:
            reasons.append(f"风格贴合 {style} < {min_style}：与例图差距偏大")
            if tier == "A":
                tier = "B"

        # 版式自检：文字溢出/背景不达标这类问题肉眼一眼可见，有就必须拦掉 A
        lr = layouts[i]
        if lr and not lr.get("ok", True):
            reasons.extend(lr.get("reasons") or ["版式自检未通过"])
            if tier == "A":
                tier = "B"

        # 语义判定只降不升：模型说"没问题"也不能把 C 抬成 A
        jr = semantics[i]
        tier, sem_notes = judge.apply_to_tier(jr, tier)
        reasons.extend(sem_notes)
        if not jr.checked and jr.error and judge_ok:
            # 整层没开通的话由批次头统一说明，不必每张卡片重复一遍
            reasons.append(f"语义判定失败：{jr.error}")

        scores = {
            "fidelity": audit["score"],
            "style": style,
            "quality": round(steps.quality_score(final), 3),
            "structure": audit["structure"],
            "spread": audit["spread"],
            "sharp": audit["sharp"],
        }
        # score 只用于排序：分档由 tier 决定，权重不参与判定
        total = 0.60 * audit["score"] + 0.25 * style + 0.15 * scores["quality"]
        if jr.checked and jr.confidence:
            total += 0.05 * (jr.confidence / 100.0)

        candidates.append(
            {
                "index": i + 1,
                "raw_url": store.url_for(produced["images"][i]),
                "final_url": store.url_for(final_path),
                "tier": tier,
                "verdict": verdict_of[tier],
                "scores": scores,
                "reasons": reasons,
                "fidelity_mode": audit["mode"],
                "subject_box": audit.get("subject_box"),
                "match_box": audit.get("match_box"),
                "semantic": jr.to_dict(),
                "layout": lr,
                "total": round(total, 3),
                # suggested = 算法推荐；chosen = 人工选定。两者不要混
                "suggested": False,
                "chosen": False,
                "review": "pending",
            }
        )

    if candidates:
        # 先看档位再看总分：一个 A 永远优先于一个分更高的 B
        rank = {"A": 2, "B": 1, "C": 0}
        best = max(candidates, key=lambda c: (rank.get(c["tier"], 0), c["total"]))
        if best["tier"] != "C":
            best["suggested"] = True

    tiers = {k: sum(1 for c in candidates if c["tier"] == k) for k in ("A", "B", "C")}

    batch = {
        "id": batch_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "instruction": req.instruction,
        "engine": engine.id,
        "engine_note": engine_note,
        "cost_cny": round(float(produced["cost"]), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "status": "ok" if any(c["verdict"] == "可交付" for c in candidates) else "needs_review",
        "tiers": tiers,
        "template": tpl.id,
        "template_version": tpl.version,
        "template_export": tpl.export,
        "copy": copy,
        "category_check": cat_check.to_dict(),
        # 素材授权：决定这批图能不能标"可正式交付"。声明了第三方/示例就只算内部参考
        "rights": rightsmod.resolve(getattr(req, "rights", "unknown")).to_dict(),
        "semantic": {
            "checked": bool(judge_ok),
            "model": (semantics[0].model if semantics else ""),
            "reason": "" if judge_ok else judge_why,
        },
        "requested": want,
        "produced": len(candidates),
        "gen_errors": produced["errors"],
        "style_profile": profile,
        "recipe": {
            "prompt": prompt,
            "negative": negative,
            "refs": ref_notes,
            "candidates": want,
            "size": gen_req.size,
            "rounds": produced["rounds"],
            "per_request": produced["per_request"],
            "template": {"id": tpl.id, "version": tpl.version,
                         "export": tpl.export, "canvas": list(tpl.canvas) if tpl.canvas else None},
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
    }

    tick("正在写入结果", 0.96)
    store.save_batch(batch)
    return batch
