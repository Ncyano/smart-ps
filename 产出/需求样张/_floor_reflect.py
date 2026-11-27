"""给商场灯箱加地面倒影与接触阴影。

真实场景里，抛光大理石地面上一定会看到：
1. 底座与地面的接触阴影（极窄、极暗的一圈）
2. 灯箱的镜像倒影 —— 越靠近接触线越清晰、越亮，越往画面下方越模糊、越淡
   倒影的高度被地面透视压得极扁（这里约 0.17 的压缩比），这是物理正确的

没有这两样，灯箱就会"浮"在地面上。
"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DST = ROOT / "04-线下大屏" / "2.png"

# 灯箱在场景中的位置（实测）
TOP_Y = 140          # 面板顶
BASE_Y = 1331        # 底座与地面的接触线
X0, X1 = 252, 763    # 灯箱左右


def add_contact_shadow(img, x0, x1, base_y, height=26, strength=0.55):
    """底座与地面交接处的一圈环境光遮蔽。"""
    H, W = img.shape[:2]
    y1 = min(H, base_y + height)
    band = img[base_y:y1, x0:x1].astype(np.float32)
    n = y1 - base_y
    ramp = np.exp(-(np.linspace(0, 1, n) / 0.35) ** 2)[:, None, None]
    # 左右也做轻微收边，避免出现矩形硬边
    xs = np.arange(x0, x1, dtype=np.float32)
    half = (x1 - x0) / 2
    side = np.clip((half - np.abs(xs - (x0 + half))) / 18.0, 0, 1)[None, :, None]
    band *= (1 - strength * ramp * side)
    img[base_y:y1, x0:x1] = np.clip(band, 0, 255).astype(np.uint8)
    return img


def add_reflection(img, x0, x1, top_y, base_y, src_span=560,
                   a_top=0.82, a_bot=0.02, blur_near=1.2, blur_far=7.0, feather=52,
                   dim=0.92):
    """把灯箱垂直翻转、按透视压扁、逐渐模糊后铺到地面上。"""
    H, W = img.shape[:2]
    # 相机在灯箱前方、视高有限，地面上只可能映出灯箱的中下段。
    # 把整只灯箱压到地面高度会变成一条糊掉的暗带；只取中下段才有"镜面感"。
    src_top = max(top_y, base_y - src_span)
    box = img[src_top:base_y, x0:x1].astype(np.float32)
    bh, bw = box.shape[:2]
    # 倒影必须一直铺到画面底边，否则会在中途截断出一条水平硬线
    refl_h = H - base_y
    if refl_h <= 6:
        return img

    flip = cv2.flip(box, 0)                                # 镜像：flip[0]=底座底
    flip = cv2.resize(flip, (bw, refl_h), interpolation=cv2.INTER_AREA)

    near = cv2.GaussianBlur(flip, (0, 0), blur_near)
    far = cv2.GaussianBlur(flip, (0, 0), blur_far)
    t = (np.linspace(0, 1, refl_h) ** 0.75)[:, None, None]
    refl = (near * (1 - t) + far * t) * dim          # 反射光本身有损耗，整体压暗一档

    # 纵向衰减：接触线处最实，越往画面下方越淡
    a = (a_top * (1 - np.linspace(0, 1, refl_h)) ** 1.25)[:, None, None]

    # 左右羽化，避免矩形硬边
    ef = np.ones((1, bw), np.float32)
    f = min(feather, bw // 3)
    ef[0, :f] = np.sin(np.linspace(0, np.pi / 2, f)) ** 1.4
    ef[0, -f:] = ef[0, :f][::-1]
    a = a * ef[..., None]

    roi = img[base_y:base_y + refl_h, x0:x1].astype(np.float32)
    out = roi * (1 - a) + refl * a
    img[base_y:base_y + refl_h, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)
    return img


def main():
    """读 04-线下大屏/2.png → 加倒影 → 覆盖写回。由 _build_screen.py 末尾调用。"""
    im = cv2.imdecode(np.fromfile(str(DST), np.uint8), cv2.IMREAD_COLOR)
    im = add_reflection(im.copy(), X0, X1, TOP_Y, BASE_Y)
    im = add_contact_shadow(im, X0 + 8, X1 - 8, BASE_Y, height=28, strength=0.50)
    cv2.imencode(".png", im)[1].tofile(str(DST))
    print("saved", DST)


if __name__ == "__main__":
    main()
