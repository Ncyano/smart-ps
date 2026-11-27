"""案例检索：把"以前同类物料踩过什么坑、怎么修的"带回当前任务。

## 为什么不是把提示词越写越长

提示词里堆规则，模型读不完也记不住；真正管用的是**同类案例**：
同一类物料上，上次因为什么被退、最后怎么过的。这类信息量小、针对性强。

## 案例从哪来（只用真实数据，不编）

1. **成功案例**：历史批次里 `deliverable` 且已人工确认的候选
2. **问题案例**：`data/feedback/log.jsonl` 里的结构化审核意见（含修法与处置）

没有数据就返回空，并在提示里写"暂无历史案例"——**不要拿示例充当案例**，
那是给模型喂假先例。

## 检索口径

按 `模板 + 问题类型` 过滤，同类物料优先。不做到向量检索：
数据量在几十到几百条时，模板+问题类型已经足够准，且结论可解释。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import review
from .config import DATA_DIR


def _batches(limit: int = 200) -> list[dict]:
    out: list[dict] = []
    root = DATA_DIR / "batches"
    if not root.is_dir():
        return out
    paths = sorted(root.glob("*/batch.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in paths[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 坏文件跳过，不影响检索
            continue
    return out


def _usable_batches() -> list[dict]:
    """能当案例的批次：真实提交 + 非 mock。

    自检/验收集的图是链路测试，mock 图是本地修饰，两者都不能当设计先例——
    混进来会让设计师看到一堆白底几何图的"历史成功案例"。
    """
    out = []
    for b in _batches():
        if (b.get("source") or "api") != "api":
            continue
        if b.get("engine") == "mock":
            continue
        out.append(b)
    return out


def successes(template: str = "", limit: int = 3) -> list[dict]:
    """可交付且已人工确认的候选。没有确认过的不算"成功案例"。"""
    out: list[dict] = []
    for b in _usable_batches():
        if template and b.get("template") != template:
            continue
        for c in b.get("candidates") or []:
            d = c.get("delivery") or {}
            if d.get("status") != "deliverable" or not c.get("subject_confirmed"):
                continue
            out.append({
                "batch": b.get("id"), "index": c.get("index"),
                "template": b.get("template"), "url": c.get("final_url"),
                "confirmed_by": c.get("subject_confirmed_by"),
                "notes": b.get("instruction") or "",
            })
            if len(out) >= limit:
                return out
    return out


def failures(template: str = "", issues: list[str] | None = None,
             limit: int = 3) -> list[dict]:
    """历史审核意见里，同类物料、同类问题的记录（含修法）。"""
    want = {i for i in (issues or []) if i}
    out: list[dict] = []
    for e in reversed(review.load_log(2000)):
        if template and e.get("template") != template:
            continue
        its = [i for i in e.get("issues") or [] if not want or i.get("type") in want]
        if not its:
            continue
        out.append({
            "batch": e.get("batch"), "index": e.get("index"),
            "template": e.get("template"),
            "issues": [{"type": i.get("type"), "label": i.get("label"),
                        "fix": i.get("fix"), "detail": i.get("detail")} for i in its],
            "disposition": e.get("disposition_label") or e.get("disposition"),
            "note": e.get("note") or "",
            "at": e.get("at"),
        })
        if len(out) >= limit:
            break
    return out


def brief(template: str, issues: list[str] | None = None) -> dict:
    """给任务单/界面用的一份简报。"""
    s = successes(template, 3)
    f = failures(template, issues, 3)
    return {
        "template": template,
        "successes": s,
        "failures": f,
        "prompt_note": prompt_note(template, f),
    }


def prompt_note(template: str, failures: list[dict]) -> str:
    """一段能塞进提示词的话。没有真实案例就返回空串。

    注意只写"出现过什么、修法是什么"，不写"请参考这张图"——
    本系统没有把历史图当参考图送进模型的通路，写了就是骗自己。
    """
    if not failures:
        return ""
    stat: dict[str, dict] = {}
    for e in failures:
        for i in e.get("issues") or []:
            key = i.get("type") or "other"
            row = stat.setdefault(key, {"label": i.get("label") or key,
                                        "fix": i.get("fix") or "", "n": 0})
            row["n"] += 1
    parts = []
    for row in list(stat.values())[:3]:
        parts.append(f"{row['label']}（{row['n']} 次，修法：{row['fix'] or '待定'}）")
    return ("本项目在同类物料上出现过以下问题，本次请重点规避："
            + "；".join(parts) + "。")


def stats() -> dict:
    """案例库规模。数字太小就别指望检索有用，界面上要说实话。"""
    b = _usable_batches()
    ok = sum(1 for x in b for c in x.get("candidates") or []
             if (c.get("delivery") or {}).get("status") == "deliverable"
             and c.get("subject_confirmed"))
    return {"batches": len(b), "confirmed_success": ok,
            "feedback": len(review.load_log(5000)),
            "usable": ok >= 3 or len(review.load_log(5000)) >= 3}
