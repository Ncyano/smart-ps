# -*- coding: utf-8 -*-
"""本地产线压测：用真实素材在本地 mock 引擎上连跑 N 轮，看稳定性。

不是测"画得好不好"（mock 只做确定性修饰），而是测：
  链路是否每轮都能跑完、耗时是否抖动、评分是否可复现、产物是否完整可读。

用法：
    python scripts/bench_local.py            # 默认 10 轮
    python scripts/bench_local.py --rounds 5
"""

from __future__ import annotations

import argparse
import base64
import io
import statistics
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from server.app.pipeline.run import run_batch  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MATERIAL_DIR = ROOT / "素材例子" / (
    "制作不同期限  不同卖品效果图   保健品可以前后变化脖子身体变白 有变化   "
    "上脸的护肤品只能面部变白、有变化   还有减肥产品等"
)

# 轮换指令：模拟本项目实际会下的单子，避免 10 轮全同一个 prompt
INSTRUCTIONS = [
    "背景换成干净的浅米白墙面，主体与人物保持不变",
    "整体统一到暖调，提亮面部，保留皮肤纹理与毛孔",
    "主体居中，四周留白均匀，不要裁掉头顶和脚",
    "降低背景对比度，让主体更突出，背景不要抢视线",
    "整体提亮一档，阴影保留层次，不要死黑也不要过曝",
    "轻微去黄，肤色更干净，保持真实的冷暖关系",
    "背景简化到单一色块，去掉杂物与家具轮廓",
    "拉伸到 4:5 竖版构图，人物比例不变",
    "提高清晰度但不要过度锐化，避免塑料感",
    "输出可直接用于信息流投放的成品，标注合规小字",
]


def to_data_url(path: Path) -> str:
    im = Image.open(path).convert("RGB")
    if max(im.size) > 1600:
        im.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pick_materials(rounds: int) -> list[Path]:
    files = sorted(
        [p for p in MATERIAL_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")],
        key=lambda p: p.name,
    )
    if not files:
        raise SystemExit(f"素材目录为空：{MATERIAL_DIR}")
    return [files[i % len(files)] for i in range(rounds)]


def check_artifacts(batch: dict) -> list[str]:
    """产物完整性：每张 final 能打开、尺寸有效、不是纯色。"""
    problems: list[str] = []
    work = ROOT / "data" / "batches" / batch["id"] / "final"
    if not batch["candidates"]:
        problems.append("没有候选产出")
        return problems
    for i in range(1, len(batch["candidates"]) + 1):
        p = work / f"final_{i}.png"
        if not p.exists():
            problems.append(f"final_{i}.png 缺失")
            continue
        if p.stat().st_size < 8 * 1024:
            problems.append(f"final_{i}.png 过小（{p.stat().st_size}B）")
            continue
        try:
            im = Image.open(p).convert("RGB")
            w, h = im.size
            if w < 64 or h < 64:
                problems.append(f"final_{i}.png 尺寸异常 {w}x{h}")
            extrema = im.resize((32, 32)).getextrema()
            if all(lo == hi for lo, hi in extrema):
                problems.append(f"final_{i}.png 是纯色图")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"final_{i}.png 打不开：{exc}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--candidates", type=int, default=3)
    ap.add_argument("--engine", default="mock", help="mock（本地、0 元）或 seedream（云端、按张计费）")
    args = ap.parse_args()

    mats = pick_materials(args.rounds)
    rows: list[dict] = []
    failures: list[str] = []
    t_all = time.time()

    label = "本地 mock（0 元、不联网）" if args.engine == "mock" else f"云端 {args.engine}（按张计费）"
    print(f"压测：{args.rounds} 轮 / 每轮 {args.candidates} 候选 / 引擎 {label}")
    print("=" * 78)

    for i, path in enumerate(mats, 1):
        inst = INSTRUCTIONS[(i - 1) % len(INSTRUCTIONS)]
        t0 = time.time()
        try:
            batch = run_batch(
                BatchRequest(
                    instruction=inst,
                    engine=args.engine,
                    candidates=args.candidates,
                    materials=[ImagePayload(name=path.name, data_url=to_data_url(path))],
                    exemplars=[],
                )
            )
            cost = time.time() - t0
            best = max(batch["candidates"], key=lambda c: c["total"])
            deliverable = sum(1 for c in batch["candidates"] if c["verdict"] == "可交付")
            problems = check_artifacts(batch)
            if problems:
                failures.append(f"第 {i} 轮（{path.name}）：" + "；".join(problems))
            rows.append(
                dict(
                    idx=i,
                    material=path.name,
                    secs=round(cost, 2),
                    batch=batch["id"],
                    status=batch["status"],
                    best_total=best["total"],
                    fidelity=best["scores"]["fidelity"],
                    style=best["scores"]["style"],
                    quality=best["scores"]["quality"],
                    deliverable=deliverable,
                    n=len(batch["candidates"]),
                    cny=float(batch["cost_cny"]),
                    raw_url=best["final_url"],
                )
            )
            print(
                f"{i:>{len(str(args.rounds))}}/{args.rounds}  {cost:6.2f}s  {batch['status']:<12} "
                f"总分 {best['total']:.3f}  保真 {best['scores']['fidelity']:.3f}  "
                f"风格 {best['scores']['style']:.3f}  可交付 {deliverable}/{len(batch['candidates'])}  "
                f"¥{float(batch['cost_cny']):.2f}"
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"第 {i} 轮（{path.name}）抛异常：{exc}")
            print(f"{i:>{len(str(args.rounds))}}/{args.rounds}  失败  {exc}")
            traceback.print_exc()

    total = time.time() - t_all
    print("=" * 78)

    if not rows:
        print("全部失败，无可汇总数据")
        for f in failures:
            print("  -", f)
        return 1

    secs = [r["secs"] for r in rows]
    totals = [r["best_total"] for r in rows]
    fids = [r["fidelity"] for r in rows]
    deliv = sum(r["deliverable"] for r in rows)
    cands = sum(r["n"] for r in rows)
    ok_rounds = sum(1 for r in rows if r["status"] == "ok")

    print(f"轮次完成   : {len(rows)}/{args.rounds}")
    print(f"总耗时     : {total:.1f}s（均值 {statistics.mean(secs):.2f}s，"
          f"最快 {min(secs):.2f}s，最慢 {max(secs):.2f}s，"
          f"抖动 {statistics.pstdev(secs):.2f}s）")
    print(f"状态 ok    : {ok_rounds}/{len(rows)}")
    print(f"可交付候选 : {deliv}/{cands}（{deliv / cands * 100:.0f}%）")
    print(f"总分       : 均值 {statistics.mean(totals):.3f}  "
          f"区间 {min(totals):.3f}~{max(totals):.3f}  抖动 {statistics.pstdev(totals):.4f}")
    print(f"保真       : 均值 {statistics.mean(fids):.3f}  "
          f"区间 {min(fids):.3f}~{max(fids):.3f}")
    cny = sum(r["cny"] for r in rows)
    if cny > 0:
        print(f"实际成本   : ¥{cny:.2f}（{cands} 张，均 ¥{cny / cands:.3f}/张）")
    else:
        print(f"实际成本   : ¥0（本地引擎，未调用任何云 API）")

    if failures:
        print(f"\n异常 {len(failures)} 条：")
        for f in failures:
            print("  -", f)
    else:
        print("\n无异常：所有轮次链路跑通、产物完整可读。")

    # 同一素材重复出现时，检查评分是否可复现（只有确定性引擎才有这个意义）
    if args.engine == "mock":
        seen: dict[str, list[float]] = {}
        for r in rows:
            seen.setdefault(r["material"], []).append(r["best_total"])
        repeat = {k: v for k, v in seen.items() if len(v) > 1}
        if repeat:
            spread = max(max(v) - min(v) for v in repeat.values())
            print(f"可复现性   : 同素材重复轮次总分最大偏差 {spread:.4f}"
                  f"（mock 为确定性引擎，应接近 0）")

    print("\n产物目录: data/batches/<批次id>/final/")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
