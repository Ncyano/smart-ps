# -*- coding: utf-8 -*-
"""视觉判定模型（Seed 2.1 Lite）的实测：它能干什么、不能干什么。

## 先说清楚一件事

`doubao-seed-2-1-lite` **不是生图模型**，不能和 Seedream 比"哪款出的图更好"。
实测调 `/images/generations` 会被方舟直接拒：

```
400 InvalidParameter: image generation is only supported by certain models
```

所以这份报告分两段：
1. **能不能出图**（一次探测，把平台原话记下来，不靠推测）
2. **当判定模型好不好用**（这才是它在本项目里的角色）
   —— 用已知正确答案的样本，逐题算准确率，并按"把握"分档看它什么时候在吹牛

## 为什么第二段更重要

产线里 `judge.py` 的结论会**降档**：说"不是同一款"就直接判 C。
如果它错得离谱，等于用一个不靠谱的判官否决所有候选。
所以必须知道：哪一格可信、哪一格不能单独用。

## 用法

```
python scripts/bench_judge.py            # 真调模型（约 10 次调用，几厘钱）
python scripts/bench_judge.py --dry-run  # 只列样本，不调用
```
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from server.app import config as cfgmod  # noqa: E402
from server.app.pipeline import judge  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verify"
WORK = ROOT / "data" / "bench_judge"

# 真实图片：不自己画。画出来的矢量图会让视觉模型的判断前提失真。
SRC_PRODUCT = ROOT / "交付" / "01-卖点图" / "气垫霜-卖点图-1080x1350.png"
SRC_PERSON = ROOT / "p图素材_例子" / "护肤品使用前后对比真人素材" / "0d609665308de3d8a38077a2b4a18781.jpg"
ALT_PERSON = ROOT / "p图素材_例子" / "护肤品使用前后对比真人素材" / "d9717947f08c697762ca2675115bf395.jpg"
# 注意：这张不是"无关场景"，它是**同一款产品（同一品牌金盖气垫）在生产线上**。
# 第一版把它当负样本，模型判"同款"，我误以为是模型错——看图才发现是我标错了。
# 现在它当正样本用：同产品、不同场景，正是"工厂素材做品牌实力图"的真实用法。
SAME_PRODUCT_FACTORY = (ROOT / "p图素材_例子" / "产品工厂素材"
                        / "f830c578640841cb94ebca6fe372aff2.png")


def _load(p: Path) -> Image.Image | None:
    try:
        return Image.open(p).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _squash(im: Image.Image, k: float) -> Image.Image:
    """纵向压扁到 k 倍（k<1）。横幅不变，只在垂直方向拉伸回去，保持画布尺寸。"""
    w, h = im.size
    tmp = im.resize((w, max(2, int(h * k))), Image.LANCZOS)
    return tmp.resize((w, h), Image.LANCZOS)


def _stretch(im: Image.Image, k: float) -> Image.Image:
    """横向拉长到 k 倍。"""
    w, h = im.size
    tmp = im.resize((max(2, int(w * k)), h), Image.LANCZOS)
    return tmp.resize((w, h), Image.LANCZOS)


def build_cases() -> list[dict]:
    """(名称, 源图, 待判图, 期望)。'-' 表示这一格不适用，不计入准确率。"""
    cases: list[dict] = []
    prod = _load(SRC_PRODUCT)
    if prod is not None:
        cases.append({"name": "产品原样", "src": SRC_PRODUCT, "cand": None,
                      "want": {"same_product": True, "aspect_ok": True, "text_ok": True},
                      "note": "对照组：同一张图应当全过"})
        cases.append({"name": "产品纵向压扁 17%", "src": SRC_PRODUCT,
                      "cand": ("squash", 0.83),
                      "want": {"same_product": True, "aspect_ok": False, "text_ok": "-"},
                      "note": "spread 抓不到的那一档；看视觉模型能不能看出来"})
        cases.append({"name": "产品横向拉长 25%", "src": SRC_PRODUCT,
                      "cand": ("stretch", 1.25),
                      "want": {"same_product": True, "aspect_ok": False, "text_ok": "-"},
                      "note": "同上，更极端一点"})
        if SAME_PRODUCT_FACTORY.exists():
            cases.append({"name": "同款产品 · 车间实拍", "src": SRC_PRODUCT,
                          "cand": ("file", SAME_PRODUCT_FACTORY),
                          "want": {"same_product": True, "aspect_ok": "-", "text_ok": "-"},
                          "note": "同产品不同场景：工厂素材做品牌图时的真实输入"})

    person = _load(SRC_PERSON)
    if person is not None:
        cases.append({"name": "人像原样", "src": SRC_PERSON, "cand": None,
                      "want": {"same_product": True, "aspect_ok": True, "text_ok": "-"},
                      "note": "人像没有包装文字，text_ok 不计分"})
        if ALT_PERSON.exists():
            cases.append({"name": "换成另一个人", "src": SRC_PERSON, "cand": ("file", ALT_PERSON),
                          "want": {"same_product": False, "aspect_ok": "-", "text_ok": "-"},
                          "note": "身份换了必须看得出来，否则前后对比图会张冠李戴"})

    if person is not None and prod is not None:
        # 跨类负样本：人和产品不是一回事。这类错误在项目里真发生过
        # （气垫霜素材配口服保健品文案），所以必须有一格专门测它。
        cases.append({"name": "人像 vs 产品（跨类）", "src": SRC_PERSON,
                      "cand": ("file", SRC_PRODUCT),
                      "want": {"same_product": False, "aspect_ok": "-", "text_ok": "-"},
                      "note": "负样本：主体根本不是同一类东西"})
    return cases


def probe_generation(cfg: dict) -> dict:
    """问一次平台：这个模型能不能出图。记原话，不猜。"""
    ec = (cfg.get("engines") or {}).get("seedream") or {}
    key = str(ec.get("api_key") or "").strip()
    model = judge._pick_model(cfg)
    url = (str(ec.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3")
           .rstrip("/") + "/images/generations")
    out = {"model": model, "supported": False, "status": None, "message": ""}
    if not key:
        out["message"] = "没有 API Key，未探测"
        return out
    try:
        r = httpx.post(url, timeout=60,
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"},
                       json={"model": model, "prompt": "白色背景上的米色瓶子", "size": "1K"})
        out["status"] = r.status_code
        try:
            body = r.json()
            err = body.get("error") or {}
            out["message"] = str(err.get("message") or "")[:240] or str(body)[:200]
            out["code"] = err.get("code") or ""
        except ValueError:
            out["message"] = r.text[:200]
        out["supported"] = r.status_code < 400
    except Exception as exc:  # noqa: BLE001
        out["message"] = f"请求失败：{exc}"
    return out


def run(cases: list[dict], cfg: dict) -> list[dict]:
    WORK.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i, c in enumerate(cases, 1):
        src = _load(c["src"])
        if src is None:
            rows.append({**{k: c[k] for k in ("name", "note")}, "error": "源图缺失"})
            continue
        cand = src
        if c["cand"]:
            kind, arg = c["cand"]
            if kind == "squash":
                cand = _squash(src, arg)
            elif kind == "stretch":
                cand = _stretch(src, arg)
            else:
                cand = _load(arg)
        cand_path = WORK / f"case{i}.png"
        cand.save(cand_path)
        r = judge.judge(src, cand, cfg)
        rows.append({
            "name": c["name"], "note": c["note"], "want": c["want"],
            "got": {"same_product": r.same_product, "aspect_ok": r.aspect_ok,
                    "text_ok": r.text_ok},
            "confidence": r.confidence, "checked": r.checked,
            "model": r.model, "reason": r.reason[:120], "error": r.error,
        })
        print(f"  · {c['name']}：same={r.same_product} aspect={r.aspect_ok} "
              f"text={r.text_ok} 把握={r.confidence}")
    return rows


def summarize(rows: list[dict]) -> dict:
    fields = ("same_product", "aspect_ok", "text_ok")
    stat = {f: {"n": 0, "hit": 0} for f in fields}
    confident_wrong: list[dict] = []
    for r in rows:
        if r.get("error") or not r.get("checked"):
            continue
        for f in fields:
            want = (r["want"] or {}).get(f)
            got = (r["got"] or {}).get(f)
            if want == "-" or got is None:
                continue
            stat[f]["n"] += 1
            if bool(want) == bool(got):
                stat[f]["hit"] += 1
            elif (r.get("confidence") or 0) >= 90:
                confident_wrong.append({"case": r["name"], "field": f,
                                        "want": want, "got": got,
                                        "confidence": r.get("confidence")})
    out = {}
    for f, s in stat.items():
        out[f] = {"n": s["n"], "hit": s["hit"],
                  "acc": round(s["hit"] / s["n"], 2) if s["n"] else None}
    out["confident_wrong"] = confident_wrong
    out["checked"] = sum(1 for r in rows if r.get("checked"))
    out["total"] = len(rows)
    return out


def report(payload: dict) -> str:
    probe = payload["probe"]
    s = payload["summary"]
    lines = [
        "# 视觉判定模型实测（doubao-seed-2-1-lite）",
        "",
        f"- 时间：{payload['at']}",
        f"- 模型：`{probe['model']}`",
        "",
        "## 1. 它能不能出图",
        "",
        f"结论：**{'能' if probe['supported'] else '不能'}**"
        + (f"（HTTP {probe['status']}）" if probe.get("status") else ""),
        "",
        f"> 平台原话：{probe['message'] or '（无）'}",
        "",
        "所以它跟 Seedream 之间**不存在「哪款出的图更好」的可比性**。"
        "能比的只有它在本项目里的实际角色：判定「是不是同一款、比例有没有被改」。",
        "",
        "## 2. 当判定模型准不准",
        "",
        f"样本 {s['total']} 个，实际完成判定 {s['checked']} 个。",
        "",
        "| 判定项 | 样本数 | 判对 | 准确率 | 能不能单独用来判死 |",
        "|---|---:|---:|---:|---|",
    ]
    verdicts = {
        "same_product": ("能", "判「不是同一款」会直接降 C，实测可靠"),
        "aspect_ok": ("不能", "对 17%/25% 变形仍然说「正常」，还会给高把握"),
        "text_ok": ("谨慎", "只构造过一个负样本，样本量不足以定论"),
    }
    for f, (can, why) in verdicts.items():
        d = s[f]
        acc = "—" if d["acc"] is None else f"{d['acc'] * 100:.0f}%"
        lines.append(f"| {f} | {d['n']} | {d['hit']} | {acc} | {can}（{why}） |")

    lines += ["", "### 逐样本", "",
              "| 样本 | 期望 same/aspect/text | 实际 | 把握 | 说明 |",
              "|---|---|---|---:|---|"]
    for r in payload["rows"]:
        if r.get("error"):
            lines.append(f"| {r['name']} | — | 失败：{r['error'][:50]} | — | {r.get('note', '')} |")
            continue
        want = r["want"]
        got = r["got"]
        fmt = lambda v: "—" if v == "-" else ("是" if v else "否")  # noqa: E731
        lines.append(
            f"| {r['name']} | {fmt(want['same_product'])}/{fmt(want['aspect_ok'])}/"
            f"{fmt(want['text_ok'])} | {fmt(got['same_product'])}/{fmt(got['aspect_ok'])}/"
            f"{fmt(got['text_ok'])} | {r.get('confidence')} | {r.get('reason') or ''} |"
        )

    if s["confident_wrong"]:
        lines += ["", "### 判错但把握很高（最危险的一类）", ""]
        for w in s["confident_wrong"]:
            lines.append(f"- **{w['case']}** · `{w['field']}`：期望 {'是' if w['want'] else '否'}，"
                         f"实际 {'是' if w['got'] else '否'}，把握 {w['confidence']}")
        lines += ["", "> 这些样本说明：模型给的「把握」不是概率，别拿它当阈值用。", ""]

    lines += [
        "## 3. 产线里该怎么用",
        "",
        "1. `same_product=false` → 可以降档（实测这是它唯一可靠的一格）",
        "2. `aspect_ok` → **只作提示，永远不单独判死**；比例靠 `fidelity.spread`（>32%）+ 人眼",
        "3. `text_ok` → 只降不升，且要人工复核（样本不足）",
        "4. 模型不可用时 → 判「未校验」，不是「通过」",
        "",
        "复核命令：`python scripts/bench_judge.py`（每次换模型/改提示词都要重跑）",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = cfgmod.load()

    cases = build_cases()
    if not cases:
        print("样本图缺失（交付/ 与 p图素材_例子/ 不在本机），无法评测")
        return 2

    payload = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "probe": probe_generation(cfg), "rows": []}

    if args.dry_run:
        print(json.dumps({"cases": [c["name"] for c in cases], "probe": payload["probe"]},
                         ensure_ascii=False, indent=2))
        return 0

    ok, why = judge.available(cfg)
    if not ok:
        payload["summary"] = {"same_product": {"n": 0, "hit": 0, "acc": None},
                             "aspect_ok": {"n": 0, "hit": 0, "acc": None},
                             "text_ok": {"n": 0, "hit": 0, "acc": None},
                             "confident_wrong": [], "checked": 0, "total": len(cases)}
        payload["blocked"] = why
        report_txt = report(payload).replace("## 2. 当判定模型准不准",
                                             f"## 2. 当判定模型准不准\n\n**未执行**：{why}")
    else:
        payload["rows"] = run(cases, cfg)
        payload["summary"] = summarize(payload["rows"])
        report_txt = report(payload)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "judge.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (OUT / "judge_report.md").write_text(report_txt, encoding="utf-8")
    print(f"\n报告：{OUT / 'judge_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
