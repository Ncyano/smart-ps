# -*- coding: utf-8 -*-
"""修掉右下角 AI 水印：取正上方同宽纹理回补 + 羽化 + 轻噪点"""
from pathlib import Path
from PIL import Image
import numpy as np

SRC = Path("C:/Users/Administrator/Desktop/Smart-ps/产出/ai人物/real3/Edit_this_photo__KEEP_EXACTLY__2026-11-20T02-23-01.png")
OUT = SRC.parent / "body_clean.png"


def rnd(seed):
    return np.random.default_rng(seed)


a = np.asarray(Image.open(SRC).convert("RGB")).astype(np.float32)
h, w, _ = a.shape
print("size", w, h)

# 水印区域（右下角）
x0, y0 = int(w * 0.845), int(h * 0.925)
pw, ph = w - x0, h - y0
sy = y0 - ph - 24                    # 正上方，避开衣褶亮部
patch = a[sy:sy + ph, x0:w].copy()
region = a[y0:y0 + ph, x0:w]

# 羽化遮罩
fy = fx = 14
mask = np.ones((ph, pw), np.float32)
yy = np.arange(ph)[:, None]
xx = np.arange(pw)[None, :]
mask *= np.clip(yy / fy, 0, 1)
mask *= np.clip((ph - 1 - yy) / fy, 0, 1)
mask *= np.clip(xx / fx, 0, 1)
mask *= np.clip((pw - 1 - xx) / fx, 0, 1)
mask = mask[..., None]

# 亮度对齐：让补丁与目标区均值一致
pm = patch.mean()
rm = region.mean()
patch = patch * (rm / max(pm, 1e-3))

out = a.copy()
out[y0:y0 + ph, x0:w] = region * (1 - mask) + patch * mask
# 加一点匹配噪声，避免过于干净
noise = rnd(7).normal(0, 1.6, (ph, pw, 1))
out[y0:y0 + ph, x0:w] += noise * mask

Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(OUT)
print("saved", OUT)
