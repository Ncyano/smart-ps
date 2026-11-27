"""离线冒烟测试：不联网、不填 Key，验证整条产线能跑通。

用法：python scripts/smoke_test.py
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app import gate  # noqa: E402
from server.app.pipeline.run import run_batch  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402


def data_url(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def make_material(seed: int, size: tuple[int, int] = (900, 1200)) -> Image.Image:
    """造一张"拍摄素材"：暗背景 + 主体，seed 用来制造差异。"""
    im = Image.new("RGB", size, (38 + seed * 6, 42, 58))
    d = ImageDraw.Draw(im)
    d.ellipse((240, 280, 660, 920), fill=(198 - seed * 8, 178, 168))
    d.rectangle((330, 500, 570, 780), fill=(72, 116, 196))
    d.line((100, 1000, 800, 1040), fill=(230, 220, 200), width=6)
    return im


def make_exemplar(seed: int) -> Image.Image:
    """造一张"往期例图"：亮调、暖色、纯白背景。"""
    im = Image.new("RGB", (800, 800), (247, 246, 243))
    d = ImageDraw.Draw(im)
    d.ellipse((190 + seed * 8, 210, 590, 610), fill=(212, 122, 112))
    d.rectangle((300, 380, 500, 560), fill=(96, 132, 200))
    return im


def main() -> int:
    materials = [ImagePayload(name=f"shot_{i}.png", data_url=data_url(make_material(i))) for i in range(2)]
    exemplars = [ImagePayload(name=f"ref_{i}.png", data_url=data_url(make_exemplar(i))) for i in range(2)]

    req = BatchRequest(
        instruction="背景换成纯白，主体居中，保留原有形状与纹理",
        engine="mock",
        candidates=3,
        materials=materials,
        exemplars=exemplars,
        rights="owned",          # 授权声明，否则闸门只能给"未声明"
    )
    batch = run_batch(req)

    print("=" * 60)
    print(f"批次       : {batch['id']}")
    print(f"引擎       : {batch['engine']}  {batch['engine_note']}")
    print(f"状态       : {batch['status']}")
    print(f"成本/耗时  : {batch['cost_cny']} 元 / {batch['elapsed_s']}s")
    print(f"素材评分   : {[m['quality'] for m in batch['materials']]}")
    print(f"参考图打包 : {[r['role'] for r in batch['recipe']['refs']]}")
    print(f"风格画像   : p50={batch['style_profile']['tone']['p50']:.1f} "
          f"sat={batch['style_profile']['color']['sat']:.3f} "
          f"bg={batch['style_profile']['composition']['bg']}")
    print("-" * 60)
    for c in batch["candidates"]:
        d = c["delivery"]
        print(f"候选{c['index']}  {'★' if c['suggested'] else ' '}  "
              f"总分 {c['total']:.3f}  保真 {c['scores']['fidelity']}  "
              f"风格 {c['scores']['style']}  质量 {c['scores']['quality']}  "
              f"{d['label']}　卡在：{'、'.join(d['blockers']) or '—'}")

    print("-" * 60)
    print("闸门明细（候选 1）")
    for ch in batch["candidates"][0]["delivery"]["checks"]:
        print(f"  [{ch['status']:<7}] {ch['label']:<8} {ch['detail'][:56]}")

    # 人工确认后重算：这是唯一能把"待人工"变"可交付"的路径
    batch["candidates"][0]["subject_confirmed"] = True
    batch["candidates"][0]["subject_confirmed_by"] = "冒烟测试"
    batch["candidates"][0]["subject_confirmed_at"] = "2026-01-01T00:00:00"
    gate.apply(batch)
    after = batch["candidates"][0]["delivery"]
    print("-" * 60)
    print(f"确认主体后：候选1 → {after['label']}（正式交付闸门 {batch['delivery']['formal_ready']}）")
    print(f"汇总       : {batch['delivery']['summary']}")
    print("=" * 60)
    print(f"成品目录: {Path('data/batches') / batch['id'] / 'final'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
