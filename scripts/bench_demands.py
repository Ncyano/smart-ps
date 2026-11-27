# -*- coding: utf-8 -*-
"""按素材例子里的 7 类需求各生 2 张，验证产线对需求的覆盖度。

用法：
    python scripts/bench_demands.py                 # 7 类 × 2 张 = 14 张
    python scripts/bench_demands.py --per 2 --engine mock
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app.pipeline.run import run_batch  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "素材例子"
OUT = ROOT / "产出" / "需求样张"

# (需求名, 目录前缀, 指令)
DEMANDS = [
    (
        "01-效果图",
        "制作不同期限",
        "保持人物与整体构图不变，只把背景换成干净的浅米白墙面，肤色更均匀自然，"
        "保留皮肤纹理与毛孔，不要美颜滤镜",
    ),
    (
        "02-成分检测海报",
        "制作产品检测",
        "做成成分检测说明海报：突出检测项目、检测结果与成分含量，版式清晰、层级分明，"
        "保留产品外观，文字要准确可读",
    ),
    (
        "03-线下价格图",
        "制作卖品线下价格图",
        "做成线下门店价格图：产品为主体，价格数字醒目清晰，可加促销标签，版式简洁适合打印",
    ),
    (
        "04-线下大屏",
        "制作线下大屏",
        "做成线下门店大屏海报：主体居中、背景干净、对比度高，适合远距离观看，"
        "不要密集文字",
    ),
    (
        "05-文案对比海报",
        "根据文案需求",
        "做成产品横向对比海报：并列展示多个产品与对比维度，排版整齐、分区清晰，"
        "保留产品外观特征",
    ),
    (
        "06-尺码表",
        "根据衣服每个款式",
        "做成服装尺码表：以表格形式列出各尺码对应的肩宽、胸围、衣长等数据，"
        "行列对齐，数字清晰可读",
    ),
    (
        "07-表格对比海报",
        "根据表格产品对比",
        "根据素材中的表格信息做成精美对比海报：保留全部数值与产品名，"
        "表格分区清晰，视觉美观",
    ),
]


def to_data_url(path: Path) -> str:
    im = Image.open(path).convert("RGB")
    if max(im.size) > 1600:
        im.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def find_dir(prefix: str) -> Path:
    for d in SRC.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            return d
    raise SystemExit(f"找不到目录：{prefix}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=2, help="每类生成几张")
    ap.add_argument("--engine", default="seedream")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    t_all = time.time()
    total_cost = 0.0

    print(f"按 7 类需求生图：每类 {args.per} 张，引擎 {args.engine}")
    print("=" * 84)

    for name, prefix, inst in DEMANDS:
        d = find_dir(prefix)
        imgs = sorted(
            [p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")],
            key=lambda p: p.name,
        )[: args.per]
        (OUT / name).mkdir(parents=True, exist_ok=True)

        for k, mat in enumerate(imgs, 1):
            t0 = time.time()
            tag = f"{name} #{k}"
            try:
                batch = run_batch(
                    BatchRequest(
                        instruction=inst,
                        engine=args.engine,
                        candidates=1,
                        materials=[ImagePayload(name=mat.name, data_url=to_data_url(mat))],
                        exemplars=[],
                    )
                )
                secs = time.time() - t0
                total_cost += float(batch["cost_cny"])
                c = batch["candidates"][0]
                final = ROOT / "data" / "batches" / batch["id"] / "final" / "final_1.png"
                dst = OUT / name / f"{k}.png"
                if final.exists():
                    Image.open(final).save(dst)
                results.append(
                    dict(demand=name, idx=k, material=mat.name, secs=round(secs, 1),
                         status=batch["status"], fid=c["scores"]["fidelity"],
                         style=c["scores"]["style"], verdict=c["verdict"], path=dst)
                )
                print(f"{tag:<22} {secs:5.1f}s  {batch['status']:<13} "
                      f"保真 {c['scores']['fidelity']:.3f}  风格 {c['scores']['style']:.3f}  "
                      f"{c['verdict']}")
            except Exception as exc:  # noqa: BLE001
                print(f"{tag:<22} 失败  {str(exc)[:110]}")
                results.append(dict(demand=name, idx=k, material=mat.name, secs=0,
                                    status="error", fid=None, style=None,
                                    verdict="失败", path=None))

    total = time.time() - t_all
    print("=" * 84)
    ok = sum(1 for r in results if r["status"] != "error")
    print(f"完成 {ok}/{len(results)}　总耗时 {total:.0f}s　成本 ¥{total_cost:.2f}")
    for name, _, _ in DEMANDS:
        rs = [r for r in results if r["demand"] == name]
        fids = [r["fid"] for r in rs if r["fid"] is not None]
        print(f"  {name:<18} {len(rs)} 张  "
              + ("保真 " + "/".join(f"{f:.2f}" for f in fids) if fids else "无产出"))
    print(f"\n产物目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
