# -*- coding: utf-8 -*-
"""
按素材例子的真实横向对比版式合成效果图：
全出血双联、中间细白缝、白字黑描边标签、底部一行合规小字。
不缩放二次渲染，直接输出 1080x1350。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import numpy as np

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "ai人物" / "real"

W, H = 1080, 1350
SEAM = 5
LW = (W - SEAM) // 2          # 537
RW = W - SEAM - LW            # 538

FONT_B = "C:/Windows/Fonts/msyhbd.ttc"
FONT_R = "C:/Windows/Fonts/msyh.ttc"

JOBS = [
    dict(src="face.png", out="face.png", tag="bottom", ypos=0.62, days=("DAY 0", "DAY 28"),
         path=ROOT.parent / "ai人物" / "real10" / "face_clean.png",
         legal="AI 生成对比示意图 · 实际效果因人而异 · 本品为化妆品，不具备医疗功效"),
    dict(src="body.png", out="body.png", tag="bottom", ypos=0.72, days=("DAY 0", "DAY 28"),
         path=ROOT.parent / "ai人物" / "real3" / "body_clean.png",
         legal="AI 生成对比示意图 · 实际效果因人而异 · 本品为普通食品，不具有疾病预防、治疗功能"),
    dict(src="slim.png", out="slim.png", tag="top", ypos=0.885, tag_y=810, scene="full",
         days=("DAY 0", "DAY 84"),
         path=ROOT.parent / "ai人物" / "real8" / "slim_clean.png",
         legal="AI 生成对比示意图 · 实际效果因人而异 · 配合规律饮食与运动"),
]


def find_seam(im):
    """在中心区域找白色竖缝，返回 (左半右边界, 右半左边界)"""
    a = np.asarray(im.convert("L")).astype(np.float32)
    w = a.shape[1]
    lo, hi = int(w * 0.42), int(w * 0.58)
    cols = a[:, lo:hi].mean(axis=0)
    idx = [i + lo for i, v in enumerate(cols) if v > 232]
    if not idx:
        c = int(np.argmax(cols)) + lo
        return c - 2, c + 3
    return idx[0], idx[-1] + 1


def cover(im, tw, th, ypos=0.5):
    """等比放大到铺满目标框，再按 ypos 取垂直位置"""
    sw, sh = im.size
    k = max(tw / sw, th / sh)
    nw, nh = int(round(sw * k)), int(round(sh * k))
    im = im.resize((nw, nh), Image.LANCZOS)
    x = (nw - tw) // 2
    y = int(round((nh - th) * ypos))
    y = max(0, min(y, nh - th))
    return im.crop((x, y, x + tw, y + th))


def build(job):
    p = Path(job["path"]) if job.get("path") else SRC / job["src"]
    im = Image.open(p).convert("RGB")
    l_end, r_start = find_seam(im)
    left = im.crop((0, 0, l_end, im.height))
    right = im.crop((r_start, 0, im.width, im.height))

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    yp = job.get("ypos", 0.5)
    canvas.paste(cover(left, LW, H, yp), (0, 0))
    canvas.paste(cover(right, RW, H, yp), (LW + SEAM, 0))

    d = ImageDraw.Draw(canvas)
    f_tag = ImageFont.truetype(FONT_B, 40)
    f_day = ImageFont.truetype(FONT_B, 17)
    f_legal = ImageFont.truetype(FONT_R, 13)

    tags = [("使用前", job["days"][0], LW / 2), ("使用后", job["days"][1], LW + SEAM + RW / 2)]
    for text, day, cx in tags:
        bottom = job.get("tag") == "bottom"
        cy = job.get("tag_y") or ((H - 116) if bottom else 82)
        d.text((cx, cy), text, font=f_tag, fill=(255, 255, 255),
               stroke_width=3, stroke_fill=(0, 0, 0), anchor="mm")
        dy = cy - 42 if bottom else cy + 40
        d.text((cx, dy), day, font=f_day, fill=(255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0), anchor="mm")

    d.text((W / 2, H - 26), job["legal"], font=f_legal, fill=(255, 255, 255),
           stroke_width=2, stroke_fill=(40, 40, 40), anchor="mm")

    out = ROOT / job["out"]
    canvas.save(out, quality=95)
    print(out.name, canvas.size)
    return out


def verify(out, scene="closeup"):
    """合成后立刻按 docs/04 的量化标准卡一遍，不合格直接点名。"""
    checker = ROOT.parents[1] / "scripts" / "check_compare.py"
    if not checker.exists():
        return
    import subprocess
    import sys as _sys
    cmd = [_sys.executable, str(checker), str(out)]
    if scene != "closeup":
        cmd += ["--scene", scene]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    body = (r.stdout or "").strip()
    if not body:
        return
    if r.returncode == 0:
        ok = [l.strip() for l in body.splitlines() if "硬指标" in l]
        print("  质检: " + (ok[0] if ok else "通过"))
    else:
        print(body)
        print("  ↑ 上述项不合格，按 docs/04 第 5 节重做源图")


if __name__ == "__main__":
    import sys
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for j in JOBS:
        if only and not any(only in x for x in (j["out"], j["src"])):
            continue
        verify(build(j), j.get("scene", "closeup"))
