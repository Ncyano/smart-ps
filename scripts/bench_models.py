# -*- coding: utf-8 -*-
"""模型对照评测：同一批固定任务，横向比"哪款更省事"。

## 这个脚本回答什么

模型换代很快，凭名字推断能力最不靠谱。要回答的是**每类物料上**：

- 一次生成就能用的比例（first-pass 可用率）
- 主体保真（结构相关度、标签区是否被改）
- 平均耗时与单张可用图成本（不是单张生成成本）

最后一条是关键：便宜但每张都要返工两次的模型，实际更贵。

## 用法

```bash
# 只看会跑什么、要花多少钱，不真的调用（默认）
python scripts/bench_models.py

# 真跑（会花钱）
python scripts/bench_models.py --run --models doubao-seedream-5-0-flash-260915,doubao-seedream-5-0-pro

# 只跑某一类物料，单张
python scripts/bench_models.py --run -k white_bg -n 1
```

## 两条纪律

1. **没有 Key 就明说，不假装跑过。** 报告里会写"未执行"，不写分数。
2. **固定素材、固定提示词、固定尺寸**，只换模型。否则比的是提示词，不是模型。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app import config as cfgmod  # noqa: E402
from server.app.engines.base import EngineError, GenRequest, RefImage  # noqa: E402
from server.app.engines.seedream import SeedreamEngine  # noqa: E402
from server.app.pipeline import fidelity as fid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verify"
WORK = ROOT / "data" / "bench_models"

DEFAULT_MODELS = [
    "doubao-seedream-5-0-flash-260915",
    "doubao-seedream-5-0-pro",
]


# ─────────────────────────── 固定素材 ───────────────────────────


def product(tint: tuple[int, int, int] = (212, 194, 184)) -> Image.Image:
    """白底产品图。所有用例共用，保证"只换模型"这一个变量。"""
    im = Image.new("RGB", (900, 1200), (250, 250, 249))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((390, 200, 510, 280), radius=18, fill=(96, 92, 88))
    d.rounded_rectangle((320, 270, 580, 1010), radius=50, fill=tint)
    d.rectangle((360, 500, 540, 740), fill=(54, 52, 50))
    for frac in (0.53, 0.57, 0.61):
        d.rectangle((400, int(1200 * frac), 500, int(1200 * frac) + 10), fill=(232, 228, 220))
    return im


def person() -> Image.Image:
    """一张"人像素材"，用于前后对比类的取景测试。"""
    im = Image.new("RGB", (900, 1200), (236, 232, 228))
    d = ImageDraw.Draw(im)
    d.ellipse((330, 180, 570, 520), fill=(226, 200, 186))     # 头
    d.rounded_rectangle((300, 520, 600, 1100), radius=60, fill=(210, 206, 200))  # 身体
    d.ellipse((380, 300, 420, 340), fill=(70, 60, 56))        # 眼
    d.ellipse((480, 300, 520, 340), fill=(70, 60, 56))
    return im


def _url(im: Image.Image) -> str:
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


# 用例：每类物料一条，写清"这类图上什么算好"
CASES: list[dict] = [
    {
        "id": "white_bg",
        "template": "white_bg",
        "prompt": "把产品放到纯白无缝背景上，主体居中，保留原有形状、材质与包装文字",
        "size": "2K",
        "judge": "主体结构与标签区必须保住；构图偏差可接受",
    },
    {
        "id": "kv",
        "template": "kv",
        "prompt": "浅灰渐变背景，柔光自上而下，产品居中偏下，画面上方留出文字区；不要在图内写任何文字",
        "size": "2K",
        "judge": "文字区干净、产品未被重绘；背景有氛围",
    },
    {
        "id": "scene",
        "template": "scene",
        "prompt": "产品放在浅木色台面上，旁边有绿植与柔光窗影，生活场景，真实摄影质感",
        "size": "2K",
        "judge": "场景合理、主体不变；背景不抢主体",
    },
    {
        "id": "person",
        "template": "scene",
        "source": "person",
        "prompt": "同一人物同一角度，保持五官与肤色不变，背景换成浅灰影棚，柔光",
        "size": "2K",
        "judge": "身份特征不能变（换脸/瘦脸即不可用）",
    },
]


def _source(case: dict) -> Image.Image:
    return person() if case.get("source") == "person" else product()


# ─────────────────────────── 评测 ───────────────────────────


def evaluate(src: Image.Image, img: Image.Image) -> dict:
    """对一张结果做客观度量。不用视觉模型：模型质量对比要能重复。"""
    a = fid.assess(img, src)
    return {
        "structure": a.get("structure"),
        "spread": a.get("spread"),
        "sharp": a.get("sharp"),
        "tier": a.get("tier"),
        "label": (a.get("checks") or {}).get("label", {}).get("status"),
        "label_score": (a.get("checks") or {}).get("label", {}).get("score"),
        "subject": (a.get("checks") or {}).get("subject", {}).get("status"),
        # 一次就能用的判定：结构可信 + 标签没被改
        "usable": bool(a.get("tier") == "A" and
                       (a.get("checks") or {}).get("label", {}).get("status") == "pass"),
    }


def run_case(model: str, case: dict, cfg: dict, n: int) -> dict:
    eng_cfg = dict((cfg.get("engines") or {}).get("seedream") or {})
    eng_cfg["model"] = model
    engine = SeedreamEngine(eng_cfg)
    ok, why = engine.available()
    if not ok:
        return {"case": case["id"], "error": why, "images": []}

    src = _source(case)
    src_path = WORK / "src" / f"{case['id']}.png"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src.save(src_path)

    out_dir = WORK / model.replace("/", "_") / case["id"]
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    req = GenRequest(
        prompt=case["prompt"],
        negative="重绘包装文字，改变产品形状，卡通，插画，水印，多余文字",
        refs=[RefImage(path=src_path, role="product", label="产品实拍")],
        n=n, size=case.get("size") or "2K",
    )
    t0 = time.time()
    try:
        res = engine.generate(req, out_dir)
    except EngineError as exc:
        return {"case": case["id"], "error": str(exc), "images": [], "model": model}
    elapsed = round(time.time() - t0, 1)

    rows = []
    for p in res.images:
        with Image.open(p) as im:
            m = evaluate(src, im.convert("RGB"))
        m.update({"file": p.name, "path": str(p)})
        rows.append(m)

    return {
        "model": model,
        "case": case["id"],
        "cost_cny": res.cost_cny,
        "elapsed_s": elapsed,
        "images": rows,
        "tier": res.meta.get("tier") if isinstance(res.meta, dict) else None,
        "raw_meta": res.meta,
    }


def summarize(rows: list[dict], n_requested: int) -> dict:
    imgs = [i for r in rows for i in r.get("images") or []]
    usable = [i for i in imgs if i.get("usable")]
    cost = sum(r.get("cost_cny") or 0 for r in rows)
    secs = sum(r.get("elapsed_s") or 0 for r in rows)
    return {
        "请求": n_requested * len(rows),
        "产出": len(imgs),
        "一次可用": len(usable),
        "一次可用率": round(len(usable) / max(1, len(imgs)), 3),
        "总成本": round(cost, 2),
        # 关键指标：算"每张能用"的钱，而不是"每张生成"的钱
        "单张可用成本": round(cost / len(usable), 2) if usable else None,
        "平均耗时": round(secs / max(1, len(rows)), 1),
        "失败": sum(1 for r in rows if r.get("error")),
    }


def report(payload: dict) -> str:
    lines = [
        "# 模型对照评测",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：{', '.join(payload['models']) or '（未指定）'}",
        f"- 用例：{len(payload['cases'])} 类物料　每类 {payload['n']} 张",
        f"- 状态：{'已执行' if payload['ran'] else '**未执行**（' + payload['blocked'] + '）'}",
        "",
    ]
    if not payload["ran"]:
        lines += [
            "> 这份报告不含任何分数：没有真正调用模型时，写分数就是编。",
            "> 配好 API Key 后重跑：`python scripts/bench_models.py --run`",
            "",
            "## 计划跑的用例",
            "",
            "| 用例 | 物料 | 评判重点 |",
            "|---|---|---|",
        ]
        for c in CASES:
            lines.append(f"| {c['id']} | {c['template']} | {c['judge']} |")
        return "\n".join(lines) + "\n"

    for model, res in payload["results"].items():
        s = res["summary"]
        lines += [
            f"## {model}",
            "",
            f"| 指标 | 值 |",
            f"|---|---|",
            f"| 一次可用率 | {(str(round(s['一次可用率'] * 100)) + '%（' + str(s['一次可用']) + '/' + str(s['产出']) + '）') if s['产出'] else '—（' + str(s['失败']) + ' 次请求全部失败）'} |",
            f"| 总成本 | {s['总成本']} 元 |",
            f"| 单张可用成本 | {s['单张可用成本'] if s['单张可用成本'] is not None else '—'} 元 |",
            f"| 平均耗时 | {s['平均耗时']}s |",
            f"| 生成失败 | {s['失败']} |",
            "",
            "| 用例 | 结构 | 铺展 | 清晰 | 标签区 | 档 | 一次可用 | 成本 | 耗时 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in res["rows"]:
            if r.get("error"):
                lines.append(f"| {r['case']} | — | — | — | — | — | ✗ {r['error'][:60]} | — | — |")
                continue
            for i in r["images"]:
                lines.append(
                    f"| {r['case']} | {i['structure']} | {i['spread']} | {i['sharp']} | "
                    f"{i['label']} | {i['tier']} | {'✓' if i['usable'] else '✗'} | "
                    f"{r['cost_cny']} | {r['elapsed_s']}s |"
                )
        lines.append("")

    lines += ["## 怎么读这份表", "",
              "- **一次可用率** 低、但单张成本也低，要看单张可用成本：返工两次就不便宜了。",
              "- **标签区 fail/warn** 说明包装文字被重画，这类图不能投；不是「风格差一点」的问题。",
              "- 结论按物料类型分别下，不要合并成「哪个模型更好」——常见情况是 A 擅长背景、B 擅长保主体。",
              ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="真的调用模型（会花钱）")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("-n", type=int, default=2, help="每个用例每模型生成几张")
    ap.add_argument("-k", default="", help="只跑名字含该关键词的用例")
    args = ap.parse_args()

    cfg = cfgmod.load()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = [c for c in CASES if not args.k or args.k in c["id"]]

    ok, why = SeedreamEngine((cfg.get("engines") or {}).get("seedream")).available()
    payload = {"models": models, "cases": [c["id"] for c in cases], "n": args.n,
               "ran": False, "blocked": "", "results": {}}

    if not args.run:
        payload["blocked"] = "默认只做计划，不调用模型；加 --run 才真跑"
    elif not ok:
        payload["blocked"] = f"引擎不可用：{why}"
    else:
        payload["ran"] = True
        print(f"将调用 {len(models)} 个模型 × {len(cases)} 类物料 × {args.n} 张"
              f" ≈ {len(models) * len(cases) * args.n} 次请求")

    if payload["ran"]:
        for model in models:
            rows = []
            for c in cases:
                print(f"  → {model} / {c['id']} …", flush=True)
                r = run_case(model, c, cfg, args.n)
                rows.append(r)
                if r.get("error"):
                    print(f"     失败：{r['error'][:100]}")
            payload["results"][model] = {"rows": rows,
                                         "summary": summarize(rows, args.n)}
    else:
        print(f"未执行：{payload['blocked']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "models.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    (OUT / "models_report.md").write_text(report(payload), encoding="utf-8")
    print(f"\n报告：{OUT / 'models_report.md'}")
    return 0 if payload["ran"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
