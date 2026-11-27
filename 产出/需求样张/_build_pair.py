"""把两张"不同天拍"的单人照拼成标准对比图（1080x1350）。

用法：python _build_pair.py
每张源图按目标半幅比例 (537:1350) 裁切，不拉伸；左右各自可指定纵向锚点。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
SEAM = 5
FONT_B = "C:/Windows/Fonts/msyhbd.ttc"
FONT_R = "C:/Windows/Fonts/msyh.ttc"
ROOT = Path(__file__).resolve().parent
AI = ROOT.parent / "ai人物" / "real13"

HALF_L, HALF_R = (W - SEAM) // 2, (W - SEAM) - (W - SEAM) // 2
ASPECT = HALF_L / H


def fit(path: Path, anchor_y: float = 0.25) -> Image.Image:
    """按目标比例裁切后缩放，anchor_y 决定纵向裁掉的部分从顶部开始占多少。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w / h > ASPECT:                       # 太宽：裁左右
        cw = int(round(h * ASPECT))
        x0 = (w - cw) // 2
        im = im.crop((x0, 0, x0 + cw, h))
    else:                                    # 太高：裁上下
        ch = int(round(w / ASPECT))
        extra = h - ch
        y0 = int(extra * anchor_y)
        im = im.crop((0, y0, w, y0 + ch))
    return im.resize((HALF_L, H), Image.LANCZOS)


def sharpness(im: Image.Image) -> float:
    import numpy as np
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = np.abs(4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1]
                 - g[1:-1, :-2] - g[1:-1, 2:])
    return float(lap.mean())


def match_texture(left: Image.Image, right: Image.Image):
    """两张源图清晰度不同会露馅：把更锐的那半柔化到同一水平，再补等量颗粒。"""
    from PIL import ImageFilter
    import numpy as np

    sl, sr = sharpness(left), sharpness(right)
    soft_im, hard_im = (left, right) if sl < sr else (right, left)
    target = min(sl, sr)
    for r in (0.4, 0.6, 0.8, 1.0, 1.3, 1.6, 2.0):
        cand = hard_im.filter(ImageFilter.GaussianBlur(r))
        if sharpness(cand) <= target * 1.06:
            break
    else:
        cand = hard_im.filter(ImageFilter.GaussianBlur(2.0))
    hard_im = cand
    if sl < sr:
        left, right = soft_im, hard_im
    else:
        left, right = hard_im, soft_im

    rng = np.random.default_rng(7)
    out = []
    for im in (left, right):
        a = np.asarray(im, dtype=np.float32)
        a = a + rng.normal(0, 1.6, (a.shape[0], a.shape[1], 1))
        out.append(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    print("  锐度", round(sl, 1), "->", round(sharpness(out[0]), 1), "|",
          round(sr, 1), "->", round(sharpness(out[1]), 1))
    return out


def build(job):
    left = fit(ROOT.parent / "ai人物" / "real13" / job["left"], job["ly"])
    right = fit(ROOT.parent / "ai人物" / "real13" / job["right"], job["ry"])
    left, right = match_texture(left, right)

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(left, (0, 0))
    canvas.paste(right, (HALF_L + SEAM, 0))
    d = ImageDraw.Draw(canvas)

    f_tag = ImageFont.truetype(FONT_B, 40)
    f_day = ImageFont.truetype(FONT_B, 17)
    f_legal = ImageFont.truetype(FONT_R, 13)

    # 标签底衬用半透明黑片，避免在浅色衣服上读不清
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for cx, (text, day) in zip((HALF_L // 2, HALF_L + SEAM + HALF_R // 2), job["tags"]):
        od.rounded_rectangle([cx - 96, H - 148, cx + 96, H - 62],
                             radius=12, fill=(0, 0, 0, 110))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(canvas)

    for cx, (text, day) in zip((HALF_L // 2, HALF_L + SEAM + HALF_R // 2), job["tags"]):
        d.text((cx, H - 116), text, font=f_tag, fill=(255, 255, 255), anchor="mm")
        d.text((cx, H - 80), day, font=f_day, fill=(235, 235, 235), anchor="mm")

    d.text((W / 2, H - 26), job["legal"], font=f_legal, fill=(255, 255, 255),
           anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))

    out = ROOT / job["out"]
    canvas.save(out, quality=95)
    print(out.name, canvas.size)


JOBS = [
    dict(out="01-效果图/1.png",
         left="_before.png",
         right="Edit_this_selfie_into_a_photo__2026-11-26T02-50-16.png",
         ly=0.24, ry=0.0,
         tags=(("使用前", "DAY 0"), ("使用后", "DAY 28")),
         legal="AI 生成对比示意图 · 实际效果因人而异 · 本品为化妆品，不具备医疗功效"),
    dict(out="01-效果图/2.png",
         left="_before_neck.png",
         right="Edit_this_photo_into_one_taken_2026-11-26T02-51-10.png",
         ly=0.30, ry=0.0,
         tags=(("使用前", "DAY 0"), ("使用后", "DAY 28")),
         legal="AI 生成对比示意图 · 实际效果因人而异 · 本品为化妆品，不具备医疗功效"),
]

if __name__ == "__main__":
    for j in JOBS:
        build(j)
