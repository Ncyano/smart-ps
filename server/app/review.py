"""审核反馈闭环：把"这张不行"拆成可统计的结构化记录。

## 为什么不能只有通过/不通过

只记"不通过"，三个月后你只知道废图率是 40%，不知道：
- 是模型把包装字重画了（提示词/参考图的问题）
- 还是价格没来源（需求收集的问题）
- 还是四角贴歪了（参数的问题）

三类问题的修法完全不同，混在一个数字里就没法改。所以这里做两件事：

1. **问题类型枚举**，每类绑定一条修法（见 `ISSUES[x]["fix"]`）。
   修法决定 `redo.py` 走哪条路——本地重排 0 元，重生成要花钱。
2. **逐条落盘**到 `data/feedback/log.jsonl`（追加，不覆盖）。
   统计按模板 × 问题类型算，这是"用流程替代训练模型"的原料。

## 记什么

```
谁、什么时候、哪一批哪一张、哪类问题、具体说明、怎么处理的、最后用了哪版
```

`disposition` 记录处理方式：`rejected` 弃用 / `accepted` 带问题接受 /
`redone` 已重做 / `deferred` 挂着待定。**"带问题接受"必须留原因**——
否则以后没法解释为什么这张图有问题还在用。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

LOG_PATH = DATA_DIR / "feedback" / "log.jsonl"

# 问题类型 → (中文名, 修法)。修法对应 redo.PLAN 的一条路线。
ISSUES: dict[str, dict] = {
    "product_deform": {"label": "产品变形 / 被重绘", "fix": "resynthesize",
                       "hint": "从甲方原图重新合成主体，不要让模型再造一遍"},
    "pack_text": {"label": "包装文字被改", "fix": "resynthesize",
                  "hint": "文字属保护区，必须回原图裁切"},
    "evidence_wrong": {"label": "证书 / 报告内容不对", "fix": "evidence",
                       "hint": "证据类只能用真实扫描件，换件或重排"},
    "bg_mismatch": {"label": "背景不符要求", "fix": "regen_bg",
                    "hint": "只重做背景，主体不动"},
    "composition": {"label": "构图 / 位置偏差", "fix": "relayout",
                    "hint": "改排版参数或裁切，不必重生成"},
    "edge_unnatural": {"label": "边缘 / 透视 / 阴影不自然", "fix": "compose",
                       "hint": "调四角、羽化与亮度匹配"},
    "copy_wrong": {"label": "文案错误 / 读不清", "fix": "relayout",
                   "hint": "改结构化文案重排，0 成本"},
    "data_missing": {"label": "数据缺来源", "fix": "data",
                     "hint": "补 claims 出处后重排"},
    "style_off": {"label": "风格与目标不符", "fix": "regen",
                  "hint": "整图重生成，或补参考成图后再跑"},
    "other": {"label": "其他", "fix": "review", "hint": "人工判断"},
}

DISPOSITIONS = {
    "rejected": "弃用",
    "accepted": "带问题接受",
    "redone": "已重做",
    "deferred": "挂起待定",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize(items) -> list[dict]:
    """接受 ["product_deform"] 或 [{"type":..., "detail":...}] 两种写法。"""
    out: list[dict] = []
    for it in items or []:
        if isinstance(it, str):
            key, detail = it, ""
        elif isinstance(it, dict):
            key = str(it.get("type") or it.get("id") or "").strip()
            detail = str(it.get("detail") or "").strip()
        else:
            continue
        if key not in ISSUES:
            key = "other"
        out.append({"type": key, "label": ISSUES[key]["label"],
                    "detail": detail, "fix": ISSUES[key]["fix"]})
    return out


def record(batch: dict, index: int, issues, disposition: str = "rejected",
           note: str = "", reviewer: str = "审核人") -> dict:
    """把一次审核意见写进批次，并追加到日志。返回写进去的那条记录。"""
    cand = next((c for c in batch.get("candidates", []) if c.get("index") == index), None)
    if cand is None:
        raise ValueError(f"候选 {index} 不存在")

    norm = normalize(issues)
    if not norm:
        raise ValueError("至少选一个问题类型")
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition 只能是 {' / '.join(DISPOSITIONS)}")
    if disposition == "accepted" and not note.strip():
        # 带着问题还用，必须留原因，否则事后无法解释
        raise ValueError("「带问题接受」必须写一句原因")

    entry = {
        "at": _now(),
        "batch": batch.get("id"),
        "index": index,
        "template": batch.get("template"),
        "template_version": batch.get("template_version"),
        "engine": batch.get("engine"),
        "version": len(cand.get("versions") or []) + 1,
        "issues": norm,
        "disposition": disposition,
        "disposition_label": DISPOSITIONS[disposition],
        "note": note.strip(),
        "reviewer": reviewer,
    }

    cand.setdefault("feedback", []).append(entry)
    # 问题汇总到候选上，界面与闸门提示都读这个。
    # 注意 feedback 里存的是**记录条目**（每条含多个 issue），不是 issue 本身。
    flat = [i for e in cand["feedback"] for i in (e.get("issues") or [])]
    cand["issue_types"] = sorted({i.get("type") for i in flat if i.get("type")})
    # 未修问题按"每种类型的最新状态"算：
    # 先挂起、后重做修好了，就不该还挂着"待修"——否则改完看到的还是红的，
    # 人会以为没生效。
    latest: dict[str, dict] = {}
    for e in cand["feedback"]:
        for it in e.get("issues") or []:
            latest[it.get("type") or "other"] = {
                **it, "disposition": e.get("disposition"),
                "at": e.get("at"), "version": e.get("version"),
            }
    cand["open_issues"] = [v for v in latest.values()
                           if v.get("disposition") in ("rejected", "deferred")]

    if disposition == "rejected":
        cand["chosen"] = False
        cand["review"] = "rejected"

    batch.setdefault("feedback", []).append(entry)
    _append_log(entry)
    return entry


def _append_log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_log(limit: int = 5000) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows: list[dict] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def stats() -> dict:
    """按模板 × 问题类型统计。这是"下一版该改哪里"的依据。"""
    rows = load_log()
    by_type: dict[str, int] = {}
    by_tpl: dict[str, dict] = {}
    by_disp: dict[str, int] = {}
    for r in rows:
        tpl = r.get("template") or "?"
        bucket = by_tpl.setdefault(tpl, {"total": 0, "types": {}})
        bucket["total"] += 1
        by_disp[r.get("disposition") or "?"] = by_disp.get(r.get("disposition") or "?", 0) + 1
        for it in r.get("issues") or []:
            key = it.get("type") or "other"
            by_type[key] = by_type.get(key, 0) + 1
            bucket["types"][key] = bucket["types"].get(key, 0) + 1

    top = sorted(by_type.items(), key=lambda kv: -kv[1])
    return {
        "total": len(rows),
        "by_type": [{"type": k, "label": ISSUES.get(k, {}).get("label", k), "count": v}
                    for k, v in top],
        "by_template": by_tpl,
        "by_disposition": by_disp,
        # 单条意见平均挂几个问题：太高说明"整张重做"，太低说明反馈太粗
        "issues_per_entry": round(sum(len(r.get("issues") or []) for r in rows) / len(rows), 2)
        if rows else 0.0,
    }


def suggestions(stats_data: dict | None = None) -> list[str]:
    """把统计翻成人能执行的动作。只给可操作的，不喊口号。"""
    s = stats_data or stats()
    if not s["total"]:
        return ["还没有审核反馈数据。先跑几批，把问题类型打上标签，这里才会有东西。"]
    out: list[str] = []
    top = s["by_type"][:3]
    fix_hint = {
        "product_deform": "把保护区写进提示词并加参考图；合成类改为从原图裁切，不让模型重画",
        "pack_text": "同上，并在闸门里盯「标签区」这一项，别只看整体结构分",
        "bg_mismatch": "只重做背景（scene 模板），别整图重生成",
        "copy_wrong": "文案改成结构化字段后重排，不需要花生成费",
        "data_missing": "把价格/含量/专利号的出处作为提交必填项",
        "edge_unnatural": "检查四角与亮度匹配参数，先跑校准再贴",
        "style_off": "补一张目标参考成图，缺参考时风格贴合度无法判",
    }
    for t in top:
        hint = fix_hint.get(t["type"])
        if hint:
            out.append(f"{t['label']} 出现 {t['count']} 次 → {hint}")
    worst = sorted(s["by_template"].items(), key=lambda kv: -kv[1]["total"])[:2]
    for tpl, b in worst:
        out.append(f"模板 {tpl} 收到 {b['total']} 条意见，是该类工艺最该先改的地方")
    return out
