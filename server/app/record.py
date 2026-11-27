"""每张成图的制作记录：回答"这张图是怎么来的"。

## 为什么必须逐张记

一批四张图，用的可能是同一段提示词，但选了不同的候选、被人改过文案、
有的重做过两次。只记批次级信息，等于事后谁也说不清**这张**是怎么来的。

所以记录挂在每张图上，包含七块：

1. 素材与参考成图的关联（用了哪张原图、哪张参考）
2. 用户原始需求 + 解析后的结构化任务单
3. 模型 / 版本 / 提示词 / 参数
4. 模板、遮罩、合成与后处理步骤
5. 候选结果、审核结论与修改原因
6. 文案与数据的来源
7. 导出规格与版本

缺了任何一块，出了问题就只能靠回忆：是模型画错了、素材本身不行、
排版参数错了，还是需求当时就没说清——四种原因的修法完全不同。

记录是**人读 + 机读**两份。机读那份（`record.json`）供以后统计，
人读那份（`记录.md`）随图交付，客户问起来直接翻。
"""

from __future__ import annotations

import json
from pathlib import Path


def _pick(batch: dict, cand: dict) -> dict:
    return {
        # ① 素材与参考成图
        "inputs": {
            "materials": [{"name": m.get("name"), "url": m.get("url"),
                           "size": [m.get("width"), m.get("height")],
                           "quality": m.get("quality")}
                          for m in batch.get("materials") or []],
            "exemplars": batch.get("exemplars") or [],
            "extra": batch.get("extra_inputs") or {},
            "rights": batch.get("rights"),
        },
        # ② 需求
        "request": {
            "instruction": batch.get("instruction"),
            "task": batch.get("task"),
            "copy": batch.get("copy"),
            "category_check": batch.get("category_check"),
        },
        # ③ 模型与参数
        "engine": {
            "id": batch.get("engine"),
            "note": batch.get("engine_note"),
            "meta": batch.get("engine_meta"),
            "size": (batch.get("recipe") or {}).get("size"),
            "rounds": (batch.get("recipe") or {}).get("rounds"),
            "prompt": (batch.get("recipe") or {}).get("prompt"),
            "negative": (batch.get("recipe") or {}).get("negative"),
            "refs": (batch.get("recipe") or {}).get("refs"),
        },
        # ④ 模板 / 合成 / 后处理
        "process": {
            "template": (batch.get("recipe") or {}).get("template"),
            "style_profile": batch.get("style_profile"),
            "layout": cand.get("layout"),
            "regions": cand.get("regions"),
            "steps": ["落盘", "风格蒸馏", "素材打分", "参考图打包与提示词",
                      "合规与数据来源", "品类一致", "生成或合成",
                      "影调回正", "语义判定", "保真分项", "交付闸门"],
        },
        # ⑤ 结果与审核
        "result": {
            "index": cand.get("index"),
            "tier": cand.get("tier"),
            "scores": cand.get("scores"),
            "delivery": cand.get("delivery"),
            "versions": cand.get("versions") or [],
            "feedback": cand.get("feedback") or [],
            "confirmed_by": cand.get("subject_confirmed_by"),
            "confirmed_at": cand.get("subject_confirmed_at"),
        },
        # ⑥ 数据与文案来源
        "sources": {
            "claims_audit": batch.get("claims_audit"),
            "claims_declared": (batch.get("task") or {}).get("claims") or [],
        },
    }


def build(batch: dict, cand: dict, *, export: dict | None = None) -> dict:
    """生成一张图的完整制作记录。`export` 是导出时的规格信息。"""
    rec = _pick(batch, cand)
    versions = cand.get("versions") or []
    rec["output"] = {
        "batch": batch.get("id"),
        "file": (export or {}).get("file") or cand.get("final_url"),
        "spec": (export or {}).get("spec") or "original",
        "size": (export or {}).get("size"),
        "format": (export or {}).get("fmt") or "png",
        "version": f"v{len(versions) + 1}",
        "formal": bool((export or {}).get("formal")),
        "exported_at": (export or {}).get("at"),
    }
    return rec


def to_markdown(rec: dict) -> str:
    r = rec
    out = r.get("output") or {}
    task = (r.get("request") or {}).get("task") or {}
    eng = r.get("engine") or {}
    res = r.get("result") or {}
    d = res.get("delivery") or {}
    lines = [
        f"# 制作记录 · 候选 #{res.get('index')}",
        "",
        f"- 批次：`{out.get('batch')}`　版本：{out.get('version')}",
        f"- 文件：`{Path(str(out.get('file'))).name}`　规格：{out.get('spec')} "
        f"{out.get('size') or ''}　{'正式交付' if out.get('formal') else '内部参考'}",
        f"- 导出时间：{out.get('exported_at') or '—'}",
        "",
        "## 1. 素材与参考",
    ]
    for m in (r.get("inputs") or {}).get("materials") or []:
        lines.append(f"- 素材 `{m.get('name')}` {m.get('size')}（质量 {m.get('quality')}）")
    for e in (r.get("inputs") or {}).get("exemplars") or []:
        lines.append(f"- 参考成图 `{Path(str(e)).name}`")
    extra = (r.get("inputs") or {}).get("extra") or {}
    for k, v in extra.items():
        lines.append(f"- 附加输入 `{k}`：{Path(str(v)).name}")
    lines += ["", "## 2. 需求"]
    lines.append(f"- 原始需求：{r.get('request', {}).get('instruction') or '（未填）'}")
    if task:
        lines.append(f"- 物料类型：{task.get('kind_label')}（模板 {task.get('kind')} "
                     f"v{(task.get('template') or {}).get('version')}）")
        z = task.get("zones") or {}
        lines.append(f"- 保护区：{'；'.join(z.get('keep') or []) or '—'}")
        lines.append(f"- 生成区：{'；'.join(z.get('may_change') or []) or '—'}")
        lines.append(f"- 禁止项：{'；'.join(z.get('forbid') or []) or '—'}")
        acc = task.get("acceptance") or []
        lines.append(f"- 验收条件：{len(acc)} 条（"
                     + "、".join(a["label"] for a in acc[:6]) + "）")
    lines += ["", "## 3. 模型与参数"]
    lines.append(f"- 引擎：{eng.get('id')}（{eng.get('note') or '正常'}）")
    lines.append(f"- 尺寸：{eng.get('size')}　请求轮次：{eng.get('rounds')}")
    meta = eng.get("meta") or []
    if meta:
        lines.append(f"- 引擎返回元信息：`{json.dumps(meta, ensure_ascii=False)[:300]}`")
    lines.append("")
    lines.append("提示词：")
    lines.append("```")
    lines.append(str(eng.get("prompt") or "（合成类模板未使用提示词）"))
    lines.append("```")
    lines += ["## 4. 模板与后处理"]
    lines.append(f"- 版式自检：{'通过' if (r.get('process', {}).get('layout') or {}).get('ok') else '未通过/未执行'}")
    for reason in ((r.get("process", {}).get("layout") or {}).get("reasons") or []):
        lines.append(f"  - {reason}")
    reg = r.get("process", {}).get("regions") or {}
    if reg:
        lines.append(f"- 分区核查：{reg.get('detail') or '—'}")
    lines += ["", "## 5. 结果与审核"]
    lines.append(f"- 交付状态：{d.get('label')}（{d.get('status')}）")
    lines.append(f"- 原因：{d.get('reason')}")
    for ch in d.get("checks") or []:
        lines.append(f"  - [{ch.get('status')}] {ch.get('label')}：{(ch.get('detail') or '')[:90]}")
    for v in res.get("versions") or []:
        lines.append(f"- {v.get('version')} 版（{v.get('at')}）：" +
                     "、".join(str(s.get("fix")) for s in v.get("steps") or []))
    for fb in res.get("feedback") or []:
        types = "、".join(i.get("label") or "" for i in fb.get("issues") or [])
        lines.append(f"- 审核意见（{fb.get('at')} {fb.get('reviewer')}）：{types}；"
                     f"{fb.get('disposition_label')}；{fb.get('note') or ''}")
    if res.get("confirmed_by"):
        lines.append(f"- 人工确认：{res.get('confirmed_by')} @ {res.get('confirmed_at')}")
    lines += ["", "## 6. 文案与数据来源"]
    items = ((r.get("sources") or {}).get("claims_audit") or {}).get("data_items") or []
    if items:
        for it in items:
            mark = "✓" if it.get("verified") else "✗"
            lines.append(f"- {mark} {it.get('label')} {it.get('text')} ← "
                         f"{it.get('source') or '（无来源）'}")
    else:
        lines.append("- 本图文案未含价格/含量/专利号等可核数字")
    lines += ["", "## 7. 导出", f"- 规格：{out.get('spec')} {out.get('size') or ''} "
                                f"格式 {out.get('format')} 版本 {out.get('version')}", ""]
    return "\n".join(lines)


def write(out_dir: Path, recs: list[dict]) -> list[str]:
    """把记录写到导出目录。返回生成的文件名。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "record.json").write_text(
        json.dumps({"records": recs}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 制作记录", "", f"共 {len(recs)} 张。逐张的完整过程见 `record.json`。", ""]
    for r in recs:
        lines.append(to_markdown(r))
        lines.append("\n---\n")
    (out_dir / "记录.md").write_text("\n".join(lines), encoding="utf-8")
    return ["record.json", "记录.md"]
