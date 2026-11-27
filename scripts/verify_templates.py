# -*- coding: utf-8 -*-
"""按模板跑的验收集：每类物料固定输入 + 期望，可重复执行。

## 这个脚本回答什么问题

单个模板"能不能跑通"很好验证，难的是三件事：

1. **换个模板，画布对不对**（1080×1350 / 1080×1080 / 保持场景尺寸）
2. **版式自检是不是真在管事**（缺字段、文字超框时必须报错，而不是悄悄出图）
3. **闸门是不是按项判**（全通过才可交付；缺输入/缺来源必须卡住）

所以用例分成"正例"和"反例"两类，反例必须失败——一个只在顺利时通过的验收集
没有意义。

## 关于语义判定的桩

离线跑不打视觉模型（花钱、也不稳定），所以这里把 `judge` 换成"总是判同一款"的桩。
报告里会标明：**本轮验证的是版式与闸门链路，不含模型判断质量**。
真实语义判定的鉴别力见 `scripts/test_judge.py`。

用法：
    python scripts/verify_templates.py            # 跑全部，写 verify/report.md
    python scripts/verify_templates.py -k price   # 只跑名字含 price 的用例
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app import gate  # noqa: E402
from server.app.pipeline import category, judge  # noqa: E402
from server.app.pipeline.run import run_batch  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verify"


# ─────────────────────────── 造素材 ───────────────────────────


def _url(im: Image.Image) -> str:
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def product(w: int = 900, h: int = 1200, tint: tuple[int, int, int] = (212, 194, 184)) -> Image.Image:
    """一张"白底产品实拍"：纯白无缝底 + 圆角瓶身 + 瓶盖 + 标签。

    刻意做成白底单主体：`fidelity.analyze_source()` 只在"白底"或"单块高细节区域"
    两种情况下给 object 模式。用浅灰底画一个光板矩形会被判成多实例场景，
    验收集就测不到"主体一致"这条路径了。
    """
    im = Image.new("RGB", (w, h), (250, 250, 249))
    d = ImageDraw.Draw(im)
    cx = w // 2
    # 瓶盖
    d.rounded_rectangle((cx - w * 0.13, h * 0.17, cx + w * 0.13, h * 0.24),
                        radius=int(w * 0.02), fill=(96, 92, 88))
    # 瓶身
    d.rounded_rectangle((cx - w * 0.23, h * 0.23, cx + w * 0.23, h * 0.84),
                        radius=int(w * 0.055), fill=tint)
    # 标签：深色块 + 几行字，制造真实细节（纯色块细节密度不够）
    d.rectangle((cx - w * 0.19, h * 0.42, cx + w * 0.19, h * 0.62), fill=(54, 52, 50))
    for i, frac in enumerate((0.45, 0.48, 0.51, 0.55, 0.58)):
        ww = w * (0.30 if i % 2 == 0 else 0.22)
        d.rectangle((cx - ww / 2, h * frac, cx + ww / 2, h * frac + h * 0.008),
                    fill=(232, 228, 220))
    # 底部投影
    d.ellipse((cx - w * 0.26, h * 0.82, cx + w * 0.26, h * 0.88), fill=(238, 237, 234))
    return im


def scene_photo(w: int = 1400, h: int = 900) -> Image.Image:
    """一张"线下场景照"：灰墙 + 一块亮屏。"""
    im = Image.new("RGB", (w, h), (118, 118, 123))
    d = ImageDraw.Draw(im)
    d.rectangle((w * 0.14, h * 0.17, w * 0.86, h * 0.84), fill=(236, 236, 233))
    return im


def payload(im: Image.Image, name: str) -> ImagePayload:
    return ImagePayload(name=name, data_url=_url(im))


# ─────────────────────────── 用例 ───────────────────────────
# expect:
#   canvas        期望成品尺寸
#   layout_ok     版式自检是否应通过
#   status        确认主体后的闸门结论
#   reason_has    版式报告或闸门说明里应出现的关键词
#   cost_zero     是否应零成本（合成类模板）

CASES: list[dict] = [
    {
        "id": "scene-ok", "template": "scene", "engine": "mock", "candidates": 2,
        "copy": None, "expect": {"canvas": [900, 1200], "layout_ok": None,
                                 "status": "deliverable", "cost_zero": False},
    },
    {
        "id": "white_bg-ok", "template": "white_bg", "engine": "mock", "candidates": 1,
        "copy": None, "expect": {"canvas": [1080, 1080], "layout_ok": True,
                                 "status": "deliverable"},
    },
    {
        # 卖点图上方压了整块文字区，保真只能给到"基本对上"。这类图的结论
        # 就该是待人工——版式对不代表主体没被重绘，得有人看一眼。
        "id": "kv-ok", "template": "kv", "engine": "mock", "candidates": 1,
        "copy": {"title": "奶油肌气垫 遮瑕不卡粉", "subtitle": "轻薄贴肤 · 持妆一整天",
                 "points": ["一层盖住痘印与红血丝", "微米级粉体贴肤", "含保湿成分"],
                 "tag": "油皮亲妈"},
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "manual",
                   "reason_has": "主体"},
    },
    {
        "id": "kv-fail-nocopy", "template": "kv", "engine": "mock", "candidates": 1,
        "copy": {"subtitle": "只有副标题"},
        "expect": {"canvas": [1080, 1350], "layout_ok": False, "status": "rejected",
                   "reason_has": "缺少标题"},
    },
    {
        "id": "price-ok", "template": "price", "engine": "mock", "candidates": 1,
        "copy": {"title": "奶油肌气垫", "price_now": "199", "price_was": "299",
                 "unit": "元/盒", "tag": "限时", "period": "11.1–11.30"},
        "claims": [{"text": "199", "kind": "price", "source": "价目表 v3 P2"},
                   {"text": "299", "kind": "price", "source": "价目表 v3 P2"}],
        # 价签占了下半幅，主体比对只能到"基本对上"，与卖点图同理：交人确认。
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "manual",
                   "reason_has": "主体"},
    },
    {
        "id": "price-fail-noprice", "template": "price", "engine": "mock", "candidates": 1,
        "copy": {"title": "奶油肌气垫"},
        "expect": {"canvas": [1080, 1350], "layout_ok": False, "status": "rejected",
                   "reason_has": "缺现价"},
    },
    {
        "id": "price-manual-unsourced", "template": "price", "engine": "mock", "candidates": 1,
        "copy": {"title": "奶油肌气垫", "price_now": "199"},
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "manual",
                   "reason_has": "来源"},
    },
    {
        "id": "patent-ok", "template": "patent", "compose": True,
        "copy": {"title": "检测报告 · 原件引用", "patent_no": "No.2026-0881",
                 "subtitle": "保湿性能检测", "issuer": "某检测中心", "period": "2026-03"},
        "evidence": True,
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "deliverable",
                   "cost_zero": True},
    },
    {
        "id": "patent-fail-noevidence", "template": "patent", "compose": True,
        "copy": {"title": "检测报告", "patent_no": "No.2026-0881"},
        "expect": {"canvas": [1080, 1350], "layout_ok": False, "status": "rejected",
                   "reason_has": "扫描件"},
    },
    {
        "id": "table-ok", "template": "table", "compose": True,
        "copy": {"title": "四款对比", "columns": ["项目", "本品", "A 牌", "B 牌"],
                 "rows": [["含量", "1000mg", "800mg", "500mg"],
                          ["单价", "199 元", "259 元", "229 元"],
                          ["专利", "有", "无", "无"]],
                 "footnote": "数据来自客户提供的对比资料"},
        "claims": [{"text": "1000mg", "kind": "dose", "source": "配方表"},
                   {"text": "199", "kind": "price", "source": "价目表 v3"}],
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "manual",
                   "reason_has": "来源"},
    },
    {
        "id": "table-fail-ragged", "template": "table", "compose": True,
        "copy": {"title": "四款对比", "columns": ["项目", "本品", "A 牌"],
                 "rows": [["含量", "1000mg"], ["单价", "199 元", "259 元"]]},
        "expect": {"canvas": [1080, 1350], "layout_ok": False, "status": "rejected",
                   "reason_has": "列数"},
    },
    {
        "id": "pair-ok", "template": "pair", "compose": True,
        "copy": {"title": "28 天使用对比"},
        "before": True, "after": True,
        "expect": {"canvas": [1080, 1350], "layout_ok": True, "status": "deliverable",
                   "cost_zero": True},
    },
    {
        "id": "pair-fail-nobefore", "template": "pair", "compose": True,
        "copy": {"title": "28 天使用对比"},
        "after": True,
        "expect": {"canvas": [1080, 1350], "layout_ok": False, "status": "rejected",
                   "reason_has": "使用前"},
    },
    {
        "id": "screen-ok", "template": "screen", "compose": True,
        "copy": None, "scene": True,
        "expect": {"canvas": [1400, 900], "layout_ok": True, "status": "deliverable",
                   "cost_zero": True},
    },
    {
        "id": "screen-fail-badcorners", "template": "screen", "compose": True,
        "copy": None, "scene": True,
        "corners": [[0, 0], [1399, 0], [1399, 899], [0, 899]],
        "expect": {"canvas": [1400, 900], "layout_ok": False, "status": "rejected",
                   "reason_has": "面积"},
    },
]


# ─────────────────────────── 桩：语义判定 ───────────────────────────


def install_stub_judge() -> None:
    def _avail(cfg):
        return True, ""

    def _judge(src, cand, cfg):
        return judge.JudgeResult(checked=True, same_product=True, aspect_ok=True,
                                 text_ok=True, confidence=95, model="stub", reason="验收集桩")

    judge.available = _avail          # type: ignore[assignment]
    judge.judge = _judge              # type: ignore[assignment]
    # 品类检测也得桩掉：不然它会拿空 Key 真发一次请求，结果恒为"未校验"，
    # 把每条用例都拖成待人工，验收集就永远测不到"全通过"这条路。
    category.detect_image = lambda im, cfg: ("skincare", "瓶装膏体（桩）", "stub")  # type: ignore[assignment]


# ─────────────────────────── 跑 ───────────────────────────


def run_case(case: dict) -> dict:
    src = product()
    req_kw = dict(
        instruction="按验收集要求出图",
        template=case["template"],
        materials=[payload(src, "m.png")],
        rights="owned",
        source="selfcheck",
        copy=case.get("copy"),
        claims=case.get("claims") or [],
        corners=case.get("corners"),
    )
    if case.get("engine"):
        req_kw["engine"] = case["engine"]
    if case.get("candidates"):
        req_kw["candidates"] = case["candidates"]
    if case.get("before"):
        req_kw["before"] = payload(product(tint=(228, 216, 208)), "before.png")
    if case.get("after"):
        req_kw["after"] = payload(product(tint=(206, 184, 174)), "after.png")
    if case.get("scene"):
        req_kw["scene"] = payload(scene_photo(), "scene.png")
    if case.get("evidence"):
        req_kw["evidence"] = payload(product(tint=(250, 248, 244)), "evidence.png")

    t0 = time.time()
    batch = run_batch(BatchRequest(**req_kw))
    cand = batch["candidates"][0]
    before_status = cand["delivery"]["status"]

    # 模拟审核人确认主体——这是唯一能把待人工翻成可交付的动作
    cand["subject_confirmed"] = True
    cand["subject_confirmed_by"] = "验收集"
    cand["subject_confirmed_at"] = "2026-01-01T00:00:00"
    gate.apply(batch)
    after_status = cand["delivery"]["status"]

    exp = case["expect"]
    reasons = " ".join((cand.get("layout") or {}).get("reasons") or [])
    gate_text = cand["delivery"]["reason"] + " " + reasons

    problems: list[str] = []
    final_path = ROOT / "data" / cand["final_url"].lstrip("/").removeprefix("files/")
    with Image.open(final_path) as _im:
        size = list(_im.size)
    if exp.get("canvas") and size != exp["canvas"]:
        problems.append(f"画布 {size} ≠ 期望 {exp['canvas']}")
    if exp.get("layout_ok") is not None and bool((cand.get("layout") or {}).get("ok")) != exp["layout_ok"]:
        problems.append(f"版式自检 {(cand.get('layout') or {}).get('ok')} ≠ 期望 {exp['layout_ok']}")
    if exp.get("status") and after_status != exp["status"]:
        problems.append(f"闸门 {after_status} ≠ 期望 {exp['status']}")
    if exp.get("reason_has") and exp["reason_has"] not in gate_text:
        problems.append(f"说明里缺少「{exp['reason_has']}」：{gate_text[:120]}")
    if exp.get("cost_zero") and batch["cost_cny"] > 0:
        problems.append(f"合成类模板不应花钱，实际 {batch['cost_cny']}")

    return {
        "id": case["id"],
        "template": case["template"],
        "canvas": size,
        "layout_ok": (cand.get("layout") or {}).get("ok"),
        "status_before_confirm": before_status,
        "status_after_confirm": after_status,
        "blockers": cand["delivery"]["blockers"],
        "engine": batch["engine"],
        "cost_cny": batch["cost_cny"],
        "seconds": round(time.time() - t0, 1),
        "batch": batch["id"],
        "reason": cand["delivery"]["reason"],
        "problems": problems,
        "ok": not problems,
    }


def report(results: list[dict]) -> str:
    ok = sum(1 for r in results if r["ok"])
    lines = [
        "# 模板验收集报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 通过：{ok}/{len(results)}",
        "- 语义判定使用**桩**（总是判同一款）：本轮验证版式与闸门链路，不含模型判断质量。",
        "",
        "| 用例 | 模板 | 画布 | 版式 | 确认前 | 确认后 | 成本 | 耗时 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['template']} | {r['canvas'][0]}×{r['canvas'][1]} | "
            f"{'OK' if r['layout_ok'] else ('FAIL' if r['layout_ok'] is False else '—')} | "
            f"{r['status_before_confirm']} | {r['status_after_confirm']} | "
            f"{r['cost_cny']} | {r['seconds']}s | {'✓' if r['ok'] else '✗ ' + '；'.join(r['problems'])} |"
        )
    by_tpl: dict[str, list[dict]] = {}
    for r in results:
        by_tpl.setdefault(r["template"], []).append(r)
    lines += ["", "## 分模板统计", ""]
    for tpl, items in by_tpl.items():
        good = sum(1 for i in items if i["ok"])
        avg = sum(i["seconds"] for i in items) / len(items)
        lines.append(f"- `{tpl}`：{good}/{len(items)} 通过，平均 {avg:.1f}s")
    lines += ["", "## 说明", "",
              "反例（`*-fail-*`）必须失败：缺字段、列数不一致、缺扫描件、四角贴满整图，",
              "这些情况出图就是废稿，宁可报错。",
              ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--filter", default="", help="只跑 id 含该子串的用例")
    args = ap.parse_args()

    install_stub_judge()
    cases = [c for c in CASES if args.filter in c["id"]]
    OUT.mkdir(exist_ok=True)
    results = []
    for c in cases:
        r = run_case(c)
        results.append(r)
        print(f"{'✓' if r['ok'] else '✗'} {r['id']:<26} {r['status_before_confirm']:<15}"
              f"→ {r['status_after_confirm']:<13} {r['seconds']}s"
              + ("" if r["ok"] else "  " + "；".join(r["problems"])))

    md = report(results)
    (OUT / "report.md").write_text(md, encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    bad = sum(1 for r in results if not r["ok"])
    print(f"\n通过 {len(results) - bad}/{len(results)}　报告：{OUT / 'report.md'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
