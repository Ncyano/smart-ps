"""定向重做：先判断"该修哪一层"，再决定要不要花钱。

## 为什么不能一律整图重跑

一张图不满意，可能坏在四个完全不同的层：

| 坏在哪 | 该怎么修 | 成本 |
|---|---|---|
| 文字排版（错字、溢出、位置） | 改结构化文案重排 | 0 元 |
| 数据（价格没来源、数字错） | 补 claims 后重排 | 0 元 |
| 主体（被模型重画了） | 从甲方原图重新抠出来合成 | 0 元 |
| 贴屏（四角/亮度） | 调四角与融合参数 | 0 元 |
| 背景（场景不对） | 只重做背景 | 1 张生成费 |
| 整体风格 | 整图重生成 | 整批生成费 |

旧做法只有一个"重跑"，把 0 元能修的问题也按最贵的方式解一遍。
这里按问题类型分流：能在本地修的立刻修（并留版本），只有真正需要模型
的才回给引擎。

## 版本

每次本地重做都写成 `final_{i}_v{n}.png`，旧版本不覆盖。
`cand["versions"]` 记每一步做了什么、参数是什么——这是"能复现"的前提。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import gate, review, store, templates
from .config import DATA_DIR
from .pipeline import fidelity as fid
from .pipeline import steps

# 修法 → 是否本地可做、花多少钱、走哪个模板
PLAN = {
    "relayout": {"label": "重排文字", "local": True, "cost": "0 元",
                 "how": "用同一张底图重新排版，不改像素内容"},
    "data": {"label": "补来源后重排", "local": True, "cost": "0 元",
             "how": "补 claims 出处，再重排"},
    "compose": {"label": "调贴屏参数", "local": True, "cost": "0 元",
                "how": "改四角/融合参数后重贴"},
    "evidence": {"label": "换证据件后重排", "local": True, "cost": "0 元",
                 "how": "换真实扫描件，重新排版"},
    "resynthesize": {"label": "从原图重新合成主体", "local": True, "cost": "0 元",
                     "how": "抠出甲方原图主体，贴回背景，不让模型再画一遍"},
    "regen_bg": {"label": "只重做背景", "local": False, "cost": "1 张生成费",
                 "how": "scene 模板重跑，主体保护区不变"},
    "regen": {"label": "整图重生成", "local": False, "cost": "整批生成费",
              "how": "换参考图或改提示词后重跑"},
    "review": {"label": "人工判断", "local": False, "cost": "—",
               "how": "自动手段判不了，交人"},
}


def _path(url: str | None) -> Path | None:
    """把 /files/... 的 URL 换回磁盘路径。"""
    if not url:
        return None
    rel = url.lstrip("/").removeprefix("files/")
    p = (DATA_DIR / rel).resolve()
    try:
        p.relative_to(DATA_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def plan(batch: dict, index: int, issues) -> dict:
    """给出一张图的修复方案：哪些本地能做，哪些要回引擎。"""
    norm = review.normalize(issues)
    fixes = []
    for it in norm:
        f = it.get("fix") or "review"
        if f not in fixes:
            fixes.append(f)

    local = [f for f in fixes if PLAN.get(f, {}).get("local")]
    remote = [f for f in fixes if f not in local]

    steps_out = [{"fix": f, **PLAN[f]} for f in fixes if f in PLAN]
    # 本地能修的优先：同一批里既有重排又有重生成时，先本地修一版给人看，
    # 不行再花钱。这个顺序能省掉大部分"其实只是字排歪了"的重生成。
    order = sorted(steps_out, key=lambda s: 0 if s["local"] else 1)
    cost = "；".join(sorted({s["cost"] for s in steps_out if not s["local"]})) or "0 元"

    tpl_id = batch.get("template") or "scene"
    next_tpl = None
    if "regen_bg" in remote:
        next_tpl = "scene"
    elif "regen" in remote:
        next_tpl = tpl_id

    return {
        "batch": batch.get("id"), "index": index,
        "issues": norm,
        "fixes": fixes,
        "steps": order,
        "local": local,
        "remote": remote,
        "cost": cost,
        "can_do_locally": bool(local),
        "needs_model": bool(remote),
        # 需要重跑时给前端一份能直接提交的请求骨架，省得人再拼一遍
        "resubmit": ({"template": next_tpl, "instruction": batch.get("instruction") or ""}
                     if next_tpl else None),
    }


# ─────────────────────────── 本地重做 ───────────────────────────


def _border_median(im: Image.Image) -> tuple[int, int, int]:
    w, h = im.size
    band = max(2, min(w, h) // 40)
    strips = [im.crop((0, 0, w, band)), im.crop((0, h - band, w, h)),
              im.crop((0, 0, band, h)), im.crop((w - band, 0, w, h))]
    px: list[tuple[int, int, int]] = []
    for s in strips:
        px.extend(list(s.convert("RGB").resize((16, 16)).getdata()))
    return tuple(int(sorted(p[i] for p in px)[len(px) // 2]) for i in range(3))  # type: ignore


def _resynth(base: Image.Image, source: Image.Image) -> tuple[Image.Image, dict]:
    """把甲方原图里的主体抠出来，贴回底图。0 成本，且文字一定不会变。

    为什么要这么做：模型每重画一次，包装上的字就有一次被改的风险。
    主体本来就在甲方素材里，抠一次贴回去，比让模型"尽量保持"可靠得多。
    """
    from .templates.builtin import _clean_mask, _foreground_mask

    src = fid.as_rgb(source)
    med = _border_median(src)
    mask, how = _foreground_mask(src, med)
    mask, dropped, frac = _clean_mask(mask)
    mask = mask.filter(ImageFilter.MinFilter(5))
    alpha = mask.filter(ImageFilter.GaussianBlur(2.0))
    bb = alpha.point(lambda v: 255 if v > 128 else 0).getbbox()
    if not bb:
        raise ValueError("主体抠不出来（可能是多实例或背景太接近主体），改用重生成")
    subject = src.crop(bb)
    sub_a = alpha.crop(bb)

    # 尺度：沿用上一版成图里主体的占比，避免"修完大小变了"
    canvas = base.convert("RGB")
    ratio = 0.62
    try:
        info = fid.analyze_source(fid._canvas(src, 320))
        if info.get("box"):
            _, _, bw, bh = info["box"]
            ratio = float(np.sqrt(bw * bh) / 320.0)
    except Exception:  # noqa: BLE001 估不出来就用默认占比
        pass
    target = max(24, int(min(canvas.width, canvas.height) * ratio))
    scale = min(target / subject.width, target / subject.height)
    nw, nh = max(1, int(subject.width * scale)), max(1, int(subject.height * scale))
    sub = subject.resize((nw, nh), Image.LANCZOS)
    sub_a = sub_a.resize((nw, nh), Image.LANCZOS)

    out = canvas.copy()
    x = (canvas.width - nw) // 2
    y = int(canvas.height * 0.52 - nh * 0.5)
    out.paste(sub, (x, y), sub_a)
    return out, {"mask": how, "size": [nw, nh], "at": [x, y],
                 "dropped_blobs": dropped, "drop_frac": round(frac, 4)}


def local_redo(batch: dict, index: int, issues, *, copy: dict | None = None,
               corners: list | None = None, evidence: Path | None = None,
               claims: list | None = None, reviewer: str = "审核人") -> dict:
    """在本机把能修的修掉，出新版本。不调生成模型，不花钱。"""
    cand = next((c for c in batch.get("candidates", []) if c.get("index") == index), None)
    if cand is None:
        raise ValueError(f"候选 {index} 不存在")
    p = plan(batch, index, issues)
    if not p["can_do_locally"]:
        raise ValueError("这些问题本地修不了，需要重新生成：" + "；".join(
            f"{s['label']}（{s['how']}）" for s in p["steps"]))

    tpl = templates.get(batch.get("template"))

    # 先把附加输入读出来（重做证据件/贴屏都要用），再决定拿什么当底图
    ctx_imgs: dict[str, Image.Image] = {}
    for key in ("before", "after", "scene", "evidence"):
        q = _path((batch.get("extra_inputs") or {}).get(key))
        if q is not None:
            ctx_imgs[key] = Image.open(q)

    base_p = _path(cand.get("raw_url")) or _path(cand.get("final_url"))
    if tpl.compose:
        # 合成类没有"模型原图"，它的底图是模板自己定的：
        # 拿已排过版的结果当底图会二次排版（字叠字），所以按类型重建。
        if tpl.id == "pair":
            base = ctx_imgs.get("after") or ctx_imgs.get("before")
        elif tpl.id == "screen":
            src_p = _path((batch.get("materials") or [{}])[0].get("url"))
            base = Image.open(src_p) if src_p else None
        else:                                  # patent / table：空白画布，全由 apply 画
            base = Image.new("RGB", tpl.canvas or (1080, 1350), "white")
        if base is None:
            raise ValueError("这类物料找不到底图（缺 before/after 或素材），只能重新生成")
        base = base.convert("RGB")
    else:
        if base_p is None:
            raise ValueError("找不到可用的底图（raw/final 都不在），只能重新生成")
        base = Image.open(base_p).convert("RGB")

    done: list[dict] = []
    # ① 主体重合成（先做：它在最底层，后面的排版要压在它上面）
    if "resynthesize" in p["fixes"]:
        src_p = _path((batch.get("materials") or [{}])[0].get("url"))
        if src_p is None:
            raise ValueError("找不到素材原图，无法重合成主体")
        base, meta = _resynth(base, Image.open(src_p))
        done.append({"fix": "resynthesize", **meta})

    # ② 换证据件
    if "evidence" in p["fixes"] and evidence is not None:
        batch.setdefault("extra_inputs", {})
        batch["extra_inputs"]["evidence"] = store.url_for(evidence)
        done.append({"fix": "evidence", "file": evidence.name})

    # ③ 重新排版（文案 / 四角 / 数据来源都走这一步）
    new_copy = dict(batch.get("copy") or {})
    if copy:
        new_copy.update(copy)
    new_corners = corners or batch.get("corners")

    ctx = {"copy": new_copy, "corners": new_corners,
           "claims": batch.get("claims_audit") or {},
           "before": None, "after": None, "scene": None, "evidence": None}
    ctx.update(ctx_imgs)

    final, layout = base, None
    if tpl.apply is not None:
        final, layout = tpl.apply(base, new_copy, ctx)
    if copy:
        batch["copy"] = new_copy
        done.append({"fix": "relayout", "fields": sorted(copy)})
    if corners:
        batch["corners"] = corners
        done.append({"fix": "compose", "corners": corners})
    if claims is not None:
        done.append({"fix": "data", "claims": claims})

    # ④ 落新版本
    n = len(cand.get("versions") or []) + 1
    out_dir = store.batch_dir(batch["id"]) / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"final_{index}_v{n}.png"
    final.save(out_path)
    cand.setdefault("versions", []).append({
        "version": n, "file": store.url_for(out_path),
        "at": review._now(), "steps": done, "reviewer": reviewer,
    })
    cand["final_url"] = store.url_for(out_path)
    cand["layout"] = layout

    # ⑤ 重算能本地算的分数（语义判定不能本地重跑，标清楚）
    try:
        src_p = _path((batch.get("materials") or [{}])[0].get("url"))
        if src_p is not None and not tpl.compose:
            audit = fid.assess(final, Image.open(src_p), ignore_spread=tpl.reframes)
            cand["scores"]["structure"] = audit["structure"]
            cand["scores"]["spread"] = audit["spread"]
            cand["scores"]["sharp"] = audit["sharp"]
            cand["scores"]["fidelity"] = audit["score"]
            cand["scores"]["quality"] = round(steps.quality_score(final), 3)
            cand["fidelity_checks"] = audit.get("checks")
            cand["fidelity_mode"] = audit["mode"]
            cand["tier"] = audit["tier"]
            cand["reasons"] = list(audit["reasons"])
    except Exception:  # noqa: BLE001 重算失败不该让重做失败
        pass

    entry = review.record(batch, index, issues, disposition="redone",
                          note=f"本地重做 v{n}：" + "、".join(d["fix"] for d in done),
                          reviewer=reviewer)
    cand["redo_note"] = "语义判定未重跑（本地重做不调视觉模型），交付前仍需人工确认"

    gate.apply(batch)
    store.save_batch(batch)
    return {"batch": batch, "entry": entry, "steps": done,
            "needs_model": p["needs_model"], "resubmit": p["resubmit"]}
