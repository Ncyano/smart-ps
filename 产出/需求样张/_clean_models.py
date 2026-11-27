"""把 06 的三张模特图右下角平台水印擦掉（背景是纯色棚景，取上方同区域回补）。"""
from pathlib import Path

import numpy as np
from PIL import Image

D = Path(__file__).resolve().parent / "06-尺码表"

FILES = {
    "811": "Full_body_studio_photo_of_a_yo_2026-11-26T02-33-58.png",
    "168": "Same_model__same_face__same_ha_2026-11-26T03-13-59.png",
    "338": "Half_body_photo_of_a_young_Asi_2026-11-26T02-34-24.png",
}


def clean(src: Path, dst: Path):
    a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    h, w, _ = a.shape
    x0, y0 = int(w * 0.80), int(h * 0.925)
    ph = h - y0
    pww = w - x0

    # 上方同宽同高，上下翻转，让渐变方向接得上
    sy = max(y0 - ph - 6, 0)
    patch = a[sy:sy + ph, x0:w][::-1, :, :].copy()

    # 逐行对齐色偏：以该行左侧干净背景为参考
    for r in range(ph):
        ref = a[y0 + r, x0 - 26:x0 - 6].mean(axis=0)
        patch[r] += ref - patch[r].mean(axis=0)

    region = a[y0:h, x0:w]

    # 左、上两条边大范围羽化（下、右贴着画布外，不需要）
    fy, fx = 46, 52
    yy = np.arange(ph)[:, None]
    xx = np.arange(pww)[None, :]
    m = np.clip(yy / fy, 0, 1) ** 1.3 * np.clip(xx / fx, 0, 1) ** 1.3
    m = m[..., None]

    out = a.copy()
    out[y0:h, x0:w] = region * (1 - m) + patch * m
    rng = np.random.default_rng(5)
    out[y0:h, x0:w] += rng.normal(0, 1.2, (ph, pww, 1)) * m
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(dst)
    print("cleaned", dst.name)


if __name__ == "__main__":
    for tag, name in FILES.items():
        clean(D / name, D / f"_model_{tag}.png")
