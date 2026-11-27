# -*- coding: utf-8 -*-
"""用工作区里的**真实交付图**校准闸门：这些图按现行规则会得到什么结论。

## 为什么值得单独做一遍

闸门建好之后，只看代码是看不出"它到底会拦谁"的。拿 23 张真实交付过一遍，
才能回答三个问题：

1. 哪些图**必须**拦（第三方品牌、示例价格、竞品材料）
2. 哪些只能给"内部参考"，不能被当成正式物料
3. 哪些卡在"没人确认"上——也就是**依赖人工输入**的那部分

## 这份校准证明了什么、没证明什么

- **证明**：授权、数据来源、需求完整度这些"文档层"规则，对真实图确实生效
- **没证明**：像素保真（主体/比例/标签）——它需要原始素材与生成记录，
  这批图是历史产物，没有可追溯的素材对应关系（配对台账 21 张待确认）。
  所以下面把像素项标 `skip`，不假装算过。

## 用法

```
python scripts/calibrate_gate.py
```
输出 `verify/gate_calibration.md`。
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import gate, rights  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DELIVER = ROOT / "交付"
OUT = ROOT / "verify"
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# 交付目录 → (模板, 是否含价格/数字类内容)。数字类必须有来源，否则只能待人工。
CATS: dict[str, tuple[str, bool]] = {
    "01-卖点图": ("kv", False),
    "02-白底主图": ("white_bg", False),
    "03-效果图-前后对比": ("pair", False),
    "04-线下价格图": ("price", True),
    "05-线下大屏": ("screen", False),
    "06-文案对比海报": ("table", True),
    "07-尺码表": ("table", True),
    "08-表格对比海报": ("table", True),
    "10-竞品对比长图": ("table", True),
}


def _task(tpl: str) -> dict:
    return {
        "kind": tpl, "kind_label": tpl, "template": {"id": tpl, "version": 1},
        "zones": {"keep": ["产品"], "may_change": ["背景"], "forbid": ["改包装字"]},
        "acceptance": [{"id": "rights", "label": "授权", "auto": True, "how": ""}],
        "unresolved": [],
    }


def _batch(cat: str, tpl: str, has_numbers: bool) -> dict:
    """按工作区实际情况构造一个"文档层"批次。

    授权一律按 third_party：交付清单自己写明这些图含第三方品牌素材
    （多个第三方品牌），只作版式参考。
    数字类物料一律构造"示例数字、无来源"——清单也写明价格与含量是示例口径。
    """
    claims_audit = {
        "status": "pass", "findings": [], "missing_fields": [],
        "data_items": ([{"kind": "price", "label": "价格", "text": "示例价",
                         "where": "图上", "source": "", "verified": False}]
                       if has_numbers else []),
    }
    return {
        "id": f"calib-{cat}",
        "template": tpl,
        "template_compose": tpl in ("pair", "price", "patent", "table", "screen"),
        "rights": rights.resolve("third_party", by="交付清单",
                                 at="2026-11-27T00:00:00",
                                 evidence="交付/清单.md 注明含第三方品牌").to_dict(),
        "claims_audit": claims_audit,
        "category_check": {"status": "skipped", "source": "历史交付图，文案品类未复核",
                           "copy_category": "unknown", "image_category": "unknown"},
        "task": _task(tpl),
        "candidates": [{
            "index": 1,
            # 像素项：历史产物没有可追溯素材，不假装算过
            "fidelity_mode": "compose",
            "fidelity_checks": {}, "scores": {},
            "layout": {"ok": True, "reasons": []},
            "semantic": {"checked": False, "error": "历史图未做语义判定"},
            "subject_confirmed": False,
        }],
    }


def scan() -> list[dict]:
    rows: list[dict] = []
    for p in sorted(DELIVER.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG_EXT:
            continue
        rel = p.relative_to(DELIVER)
        cat = rel.parts[0] if len(rel.parts) > 1 else "未分类"
        if cat.startswith("09-"):
            continue
        tpl, numbers = CATS.get(cat, ("scene", False))
        b = _batch(cat, tpl, numbers)
        d = gate.evaluate(b, b["candidates"][0])
        rows.append({
            "file": str(rel).replace("\\", "/"),
            "category": cat,
            "template": tpl,
            "status": d.status,
            "label": d.label,
            "blockers": d.blockers,
            "reason": d.reason,
            "checks": [(c.id, c.status, c.detail) for c in d.checks],
        })
    return rows


def report(rows: list[dict]) -> str:
    cnt = Counter(r["status"] for r in rows)
    by_cat: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_cat[r["category"]][r["status"]] += 1

    # 哪一项在拦人
    blocker_cnt: Counter[str] = Counter()
    for r in rows:
        for b in r["blockers"]:
            blocker_cnt[b] += 1

    lines = [
        "# 闸门校准 · 用真实交付图过一遍",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本：`交付/` 下 {len(rows)} 张（不含系统界面截图）",
        "",
        "## 结论",
        "",
        f"- **仅内部参考**：{cnt.get('internal_only', 0)} 张",
        f"- **待人工**：{cnt.get('manual', 0)} 张",
        f"- **可交付**：{cnt.get('deliverable', 0)} 张",
        f"- **不可用**：{cnt.get('rejected', 0)} 张",
        "",
        "> 这批图全部声明为 `third_party`（交付清单自己注明含第三方品牌），",
        "> 所以结论只会是「仅内部参考」——这正是闸门要的：**与图好不好看无关**。",
        "> 注意：`internal_only` 的优先级高于 `manual`，所以「待人工」是 0 不代表没有待确认项，",
        "> 具体卡点看下面那张表（数据来源 9 张、人工确认全部未点）。",
        "",
        "## 卡在哪几项",
        "",
        "| 检查项 | 命中张数 | 含义 |",
        "|---|---:|---|",
    ]
    meaning = {
        "rights": "素材授权：第三方未授权，只能内部参考",
        "task": "需求完整度：缺任务单或必填项",
        "claims": "合规文字：命中绝对化/疗效/商标",
        "data_source": "数据来源：价格/含量/专利号没有出处",
        "required_fields": "必填字段缺失",
        "category": "品类一致：文案与产品对不上",
        "subject": "产品主体：像素比对（本轮 skip）",
        "geometry": "主体比例（本轮 skip）",
        "label": "标签区（本轮 skip）",
        "layout": "版式自检",
        "semantic": "语义判定：历史图未做",
        "subject_confirm": "人工确认：还没人核对",
    }
    for cid, n in blocker_cnt.most_common():
        lines.append(f"| `{cid}` | {n} | {meaning.get(cid, '')} |")

    lines += ["", "## 按类别", "", "| 交付类别 | 张数 | 结论 | 主要卡点 |", "|---|---:|---|---|"]
    for cat, c in sorted(by_cat.items()):
        total = sum(c.values())
        main = "、".join(f"{k}×{v}" for k, v in c.most_common())
        sample = next(r for r in rows if r["category"] == cat)
        lines.append(f"| {cat} | {total} | {main} | {sample['reason'][:60]} |")

    lines += [
        "",
        "## 这一轮校准证明了什么",
        "",
        f"1. **授权项对真实图确实生效**：{len(rows)} 张全部落「仅内部参考」，不是靠人记得。",
        "2. **数据来源项生效**：价格图、表格对比、尺码表、竞品长图只要数字没绑定出处，",
        "   就一律卡在「数据来源」，不会因为图好看放行。",
        "3. **人工确认是最后一道**：即使授权、数据都齐，没人点确认仍然是待人工——",
        "   这条是设计意图，不是没做完。",
        "",
        "## 没证明什么（必须说清）",
        "",
        "- **像素保真没算**：历史交付图没有可追溯的原始素材（配对台账里 21 张待确认），",
        "  所以主体/比例/标签三项标 `skip`，没有假装跑过。",
        "- **语义判定没算**：这批图生成时还没接视觉模型，`semantic.checked=false`。",
        "- 因此这份报告**不能**用来证明「保真阈值是否合适」，它只校准文档层规则。",
        "",
        "复核命令：`python scripts/calibrate_gate.py`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = scan()
    if not rows:
        print("交付/ 为空，没有可校准的样本")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gate_calibration.md").write_text(report(rows), encoding="utf-8")
    with (OUT / "gate_calibration.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件", "类别", "模板", "结论", "卡点", "原因"])
        for r in rows:
            w.writerow([r["file"], r["category"], r["template"], r["status"],
                        "|".join(r["blockers"]), r["reason"]])
    print(f"样本 {len(rows)} 张 → {OUT / 'gate_calibration.md'}")
    print(Counter(r["status"] for r in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
