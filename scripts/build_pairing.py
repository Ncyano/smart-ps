# -*- coding: utf-8 -*-
"""阶段一：把散落的图片分成三类，建一对多映射台账。

## 三类是什么

- **素材**：真实产品/人物/场景/工厂/报告。是"拿什么做"。
- **参考成图**：已经带排版与文案的成品。是"做成什么样"。
- **AI 交付**：本项目已经生成的图。是"待检查、待批改的结果"。

一句话：素材给你原料，参考成图给你目标，交付是待检结果。
三者混在一起，评测就没有基准——你分不清"这张不好"是参考差、素材差还是模型差。

## 输出

```
verify/配对台账.csv   机读：每个交付文件 → 建议模板 / 候选素材 / 候选参考 / 匹配依据 / 状态
verify/配对台账.md    人读：按物料类型分组的汇总，以及待确认清单
```

**匹配是关键词规则，不是语义理解。** 所以每条都带"匹配依据"和状态：
`可配对` 表示规则命中了确定项，`待确认` 表示没命中或命中多个。
宁可标待确认，也不要在台账里编一个看起来合理的配对——台账是后面
评测与验收的地基，编一次，后面全歪。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVER = ROOT / "交付"
MAT_POOL = ROOT / "p图素材_例子"
REQ_REF = ROOT / "素材例子"
EARLY = ROOT / "产出" / "需求样张"
OUT = ROOT / "verify"

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 交付目录 → (建议模板, 匹配关键词)。关键词用于在素材/参考里找对应项。
DELIVER_KIND: dict[str, tuple[str, list[str]]] = {
    "01-卖点图": ("kv", ["卖点", "痛点", "成分"]),
    "02-白底主图": ("white_bg", ["白底", "产品", "工厂"]),
    "03-效果图-前后对比": ("pair", ["前后", "对比", "效果", "真人"]),
    "04-线下价格图": ("price", ["价格", "线下", "药店", "货架"]),
    "05-线下大屏": ("screen", ["大屏", "灯箱", "线下", "门店"]),
    "06-文案对比海报": ("table", ["对比", "文案", "海报"]),
    "07-尺码表": ("table", ["尺码"]),
    "08-表格对比海报": ("table", ["表格", "对比"]),
    "09-界面截图": ("", ["界面"]),   # 系统界面截图，不是投放物料，下面单独排除
    "10-竞品对比长图": ("table", ["竞品", "对比"]),
}

# 需求样例目录 → 关键词（这些是"参考成图"）
REQ_KEYWORDS = {
    "制作不同期限": ["效果", "前后", "对比"],
    "制作产品检测": ["检测", "专利", "成分"],
    "制作卖品线下价格图": ["价格", "线下"],
    "制作线下大屏": ["大屏", "线下"],
    "对比维度做精美海报": ["对比", "海报"],
    "做精美尺码表": ["尺码"],
    "表格产品对比": ["表格", "对比"],
}


# 关键词 → 模板，**按特异性排序**。顺序重要：
# "04-线下大屏" 同时命中 线下（价格图的词）和大屏，必须先看大屏，
# 否则大屏图会被判成价格图——台账里最忌讳这种"看着合理"的错配。
_PRIORITY: list[tuple[str, list[str]]] = [
    ("screen", ["大屏", "灯箱", "立牌"]),
    ("table", ["表格", "尺码", "竞品", "文案对比"]),
    ("price", ["价格", "价签", "售价"]),
    ("pair", ["前后", "效果"]),
    ("kv", ["卖点", "痛点", "成分"]),
    ("white_bg", ["白底"]),
    ("scene", ["场景", "送礼"]),
]


def _infer(cat: str) -> tuple[str, list[str]]:
    """从目录名推模板与关键词。

    交付目录和早期样张目录的命名不一样（`03-效果图-前后对比` vs `01-效果图`），
    直接查表会大量漏；这里按**特异性从高到低**匹配，命中的词再拿去搜素材/参考。
    """
    if cat in DELIVER_KIND:
        return DELIVER_KIND[cat]
    hits: list[str] = []
    tpl = ""
    for t, words in _PRIORITY:
        for w in words:
            if w in cat:
                tpl = tpl or t
                if w not in hits:
                    hits.append(w)
    if not tpl:
        tail = cat.split("-", 1)[-1]
        return "", [tail]
    # 补上同类模板的通用词：早期目录名只有"价格"，但找素材还得靠"线下/药店"
    for _k, (t, ws) in DELIVER_KIND.items():
        if t == tpl:
            for w in ws:
                if w not in hits:
                    hits.append(w)
    return tpl, hits


def _imgs(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.rglob("*") if p.suffix.lower() in IMG_EXT and p.is_file())


def load_material_index() -> list[dict]:
    """读 p图素材_例子 已有的分类清单（人工标注过，比规则准）。"""
    csv_path = MAT_POOL / "_分类清单.csv"
    rows: list[dict] = []
    if csv_path.is_file():
        for r in csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()):
            cat = (r.get("类别") or "").strip()
            name = (r.get("文件") or "").strip()
            path = MAT_POOL / cat / name
            if not path.is_file():
                hits = list(MAT_POOL.glob(f"*/{name}"))
                path = hits[0] if hits else path
            rows.append({
                "path": path, "category": cat, "kind": (r.get("类型") or "").strip(),
                "sub": (r.get("细分") or "").strip(), "desc": (r.get("内容") or "").strip(),
            })
        return rows

    # 没有清单就按目录名粗分：素材 vs 成图
    mat_cats = ("工厂", "线下素材", "药店", "真人素材", "送礼")
    for cat_dir in sorted(p for p in MAT_POOL.iterdir() if p.is_dir() and not p.name.startswith("_")):
        kind = "素材" if any(k in cat_dir.name for k in mat_cats) else "成图"
        for p in _imgs(cat_dir):
            rows.append({"path": p, "category": cat_dir.name, "kind": kind,
                         "sub": "", "desc": ""})
    return rows


def reference_index() -> list[dict]:
    """参考成图 = p图素材里的"成图" + 素材例子（按需求类别放的样例）。"""
    rows = [r for r in MAT_POOL_ROWS if r["kind"] == "成图"]
    for p in _imgs(REQ_REF):
        rows.append({"path": p, "category": p.parent.name[:18], "kind": "成图",
                     "sub": "需求样例", "desc": p.parent.name})
    return rows


# 泛词：单靠它们命中不能算配对成功。"线下""对比""产品"几乎每张图都有，
# 拿它当依据会把尺码表配到真人对比素材上——台账里最危险的就是这种错配。
GENERIC = {"线下", "产品", "对比", "效果", "海报", "场景", "门店", "成分", "演示"}


def _hit(text: str, words: list[str]) -> tuple[int, str, bool]:
    """返回 (命中数, 命中的词（多个用顿号连）, 是否有强命中)。"""
    score, strong = 0, False
    hits: list[str] = []
    for w in words:
        if w and w in text:
            score += 1
            if w not in hits:
                hits.append(w)
            if w not in GENERIC:
                strong = True
    return score, "、".join(hits[:3]), strong


def build() -> list[dict]:
    delivered = []
    for p in _imgs(DELIVER):
        rel = p.relative_to(DELIVER)
        cat = rel.parts[0] if len(rel.parts) > 1 else "未分类"
        delivered.append({"path": p, "category": cat, "name": p.name})
    # 早期样张也算交付（它们同样是"待检结果"）。
    # 类别取子目录名（01-效果图 / 03-线下价格图 …），否则 46 张全挤成"早期样张"，
    # 汇总表里看不出它们是哪类物料。
    for p in _imgs(EARLY):
        if p.name.startswith("_"):
            continue
        rel = p.relative_to(EARLY)
        cat = rel.parts[0] if len(rel.parts) > 1 else "早期样张"
        delivered.append({"path": p, "category": cat, "name": p.name})

    # 界面截图不在物料体系里（它是系统截图），不参与配对，免得台账里一堆"待确认"
    delivered = [d for d in delivered
                 if not d["category"].startswith("09-") and not d["category"].startswith("_")]

    refs = reference_index()
    rows: list[dict] = []
    for d in delivered:
        tpl, words = _infer(d["category"])
        text = f"{d['category']} {d['name']}"

        # 只收"强命中"的候选：泛词命中一律不配对，标待确认交人看
        mat_scored = []
        for m in MAT_POOL_ROWS:
            if m["kind"] != "素材":
                continue
            score, hit, strong = _hit(f"{m['category']} {m['desc']} {m['sub']}", words)
            # 素材侧必须强命中：只靠"产品""线下"这类词配上的素材，
            # 实际上跟这张交付图没关系，配上去只会误导后面做评测的人。
            if score and strong:
                mat_scored.append((score, hit, strong, m))
        mat_scored.sort(key=lambda x: (-x[0], not x[2]))

        ref_scored = []
        for r in refs:
            score, hit, strong = _hit(f"{r['category']} {r['desc']}", words)
            if score and (strong or score >= 2):
                ref_scored.append((score, hit, strong, r))
        ref_scored.sort(key=lambda x: (-x[0], not x[2]))

        mats = [m for _, _h, _s, m in mat_scored[:3]]
        ref = ref_scored[0] if ref_scored else None
        reasons = []
        if mats:
            reasons.append(f"素材命中「{mat_scored[0][1]}」×{mat_scored[0][0]}")
        else:
            reasons.append("素材只命中泛词或无命中")
        if ref:
            reasons.append(f"参考命中「{ref[1]}」×{ref[0]}")
        else:
            reasons.append("参考只命中泛词或无命中")
        status = "可配对" if (mats and ref) else "待确认"

        rows.append({
            "交付文件": str(d["path"].relative_to(ROOT)).replace("\\", "/"),
            "类别": d["category"],
            "建议模板": tpl,
            "候选素材": " | ".join(str(m["path"].relative_to(ROOT)).replace("\\", "/") for m in mats),
            "候选参考": str(ref[3]["path"].relative_to(ROOT)).replace("\\", "/") if ref else "",
            "匹配依据": "；".join(reasons) or "规则未命中",
            "状态": status,
        })
    return rows


def report(rows: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["类别"]].append(r)
    ok = sum(1 for r in rows if r["状态"] == "可配对")
    lines = [
        "# 配对台账（阶段一）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 交付文件：{len(rows)} 张　可配对：{ok}　待确认：{len(rows) - ok}",
        f"- 素材池：{len([m for m in MAT_POOL_ROWS if m['kind'] == '素材'])} 张　"
        f"参考池：{len([m for m in MAT_POOL_ROWS if m['kind'] == '成图']) + len(_imgs(REQ_REF))} 张",
        "",
        "> 匹配靠关键词规则，不是语义理解。`待确认` 不是失败，是**需要人看一眼**——",
        "> 台账编一个看起来合理的配对，后面的评测与验收就全歪了。",
        "",
        "## 按类别",
        "",
        "| 交付类别 | 张数 | 可配对 | 待确认 | 建议模板 |",
        "|---|---:|---:|---:|---|",
    ]
    for cat, items in sorted(by_cat.items()):
        o = sum(1 for i in items if i["状态"] == "可配对")
        lines.append(f"| {cat} | {len(items)} | {o} | {len(items) - o} | "
                     f"{items[0]['建议模板'] or '—'} |")

    todo = [r for r in rows if r["状态"] != "可配对"]
    lines += ["", "## 待确认清单", ""]
    if todo:
        lines += ["| 交付文件 | 类别 | 原因 |", "|---|---|---|"]
        for r in todo[:40]:
            lines.append(f"| `{Path(r['交付文件']).name}` | {r['类别']} | {r['匹配依据']} |")
        if len(todo) > 40:
            lines.append(f"| … | | 还有 {len(todo) - 40} 条，见 CSV |")
    else:
        lines.append("全部命中，没有待确认项。")

    lines += ["", "## 能配对的样例（用于阶段二的评测基准）", "",
              "| 交付文件 | 模板 | 素材 | 参考 |", "|---|---|---|---|"]
    for r in [x for x in rows if x["状态"] == "可配对"][:20]:
        m = r["候选素材"].split(" | ")[0] if r["候选素材"] else ""
        lines.append(f"| `{Path(r['交付文件']).name}` | {r['建议模板']} | "
                     f"`{Path(m).name if m else '—'}` | `{Path(r['候选参考']).name}` |")
    return "\n".join(lines) + "\n"


MAT_POOL_ROWS: list[dict] = []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    global MAT_POOL_ROWS
    MAT_POOL_ROWS = load_material_index()
    rows = build()

    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "配对台账.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["交付文件"])
        w.writeheader()
        w.writerows(rows)
    (OUT / "配对台账.md").write_text(report(rows), encoding="utf-8")

    if not args.quiet:
        print(report(rows))
    print(f"台账：{csv_path}　/　{OUT / '配对台账.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
