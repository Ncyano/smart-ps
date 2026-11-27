"""线下大屏两种载体的成品构建。

这一版换了做法：**先把广告位定死，再按广告位的形状出内容**。
场景用"正对镜头、纯白空白面板"的灯箱照片（面板是标准矩形），
面板四角由亮度阈值逐行逐列扫描得到，误差 1-2px，透视变换几乎无变形。

地铁灯箱：_assets/scene4/Photorealistic_photo_of_a_metr_2026-11-26T06-12-15.png
商场灯箱：_assets/scene4/Photorealistic_photo_inside_a__2026-11-26T06-12-47.png
"""
from pathlib import Path

import cv2
import numpy as np

from _screen_kv import make_kv, warp_onto

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "04-线下大屏"
BK = ROOT / "_assets" / "backup"
BK.mkdir(parents=True, exist_ok=True)

SCENE_METRO = ROOT / "_assets/scene4/Photorealistic_photo_of_a_metr_2026-11-26T06-12-15.png"
SCENE_MALL = ROOT / "_assets/scene4/Photorealistic_photo_inside_a__2026-11-26T06-12-47.png"

# 白面板四角：先从亮度 >190 取白区，再向外扫到第一个暗像素（<45）——
# 那条暗线就是"边框内檐"与"画面开口"的真实分界。广告必须盖到这条线，
# 否则面板边缘那圈 6-20px 的浅灰内檐会露出来，就是用户圈的"漏光"。
# 每条边再向外 1.5px，抵消透视插值的取整误差。
# 实测（内点率 >85%）：
#   METRO  L=441 R=984 T≈118 B≈1361   → 543 x 1243，比 0.437
#   MALL   L=254 R=762 T≈214 B=1259   → 508 x 1045，比 0.486
QUAD_METRO = [(439.5, 117), (985.5, 114), (985.5, 1361), (439.5, 1362.5)]
QUAD_MALL = [(252.5, 213.5), (763.5, 212.5), (763.5, 1260.5), (252.5, 1260.5)]


def strip_watermark(path, box):
    im = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = box
    mask = np.zeros(im.shape[:2], np.uint8)
    mask[y0:y1, x0:x1] = 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), 1)
    fix = cv2.inpaint(im, mask, 6, cv2.INPAINT_NS)
    cv2.imencode(".png", fix)[1].tofile(str(path))


def main():
    # KV 尺寸严格按面板宽高比：商场 489x1024 → 0.477；地铁 508x1241 → 0.409
    kv_mall = make_kv(1080, 2222, prod_h=0.44, prod_cy=0.50)
    warp_onto(SCENE_MALL, kv_mall, QUAD_MALL, BK / "_s_mall.png",
              gain=1.0, tex=2.0, blur_kv=0.8, lift=9.0, env=0.085,
              ambient=0.032, spill=0.50, refl=0.07,
              backlight=0.10, backlight_span=0.10,
              edge_dark=0.82, edge_w=3.0, band=0.03)

    kv_metro = make_kv(1080, 2473, prod_h=0.42, prod_cy=0.50)
    warp_onto(SCENE_METRO, kv_metro, QUAD_METRO, BK / "_s_metro.png",
              gain=1.0, tex=1.9, blur_kv=0.75, lift=9.0, env=0.080,
              ambient=0.032, spill=0.55, refl=0.07,
              backlight=0.10, backlight_span=0.09,
              edge_dark=0.82, edge_w=3.0, band=0.03)

    for name, src in [("1.png", "_s_metro.png"), ("2.png", "_s_mall.png")]:
        p = BK / src
        strip_watermark(p, (890, 1440, 1024, 1536))
        cv2.imencode(".png", cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR))[1].tofile(
            str(OUT / name))
        print("saved", name)

    # 商场灯箱是落地立牌，必须补地面倒影与接触阴影，否则会"浮"在地面上
    from _floor_reflect import main as add_floor
    add_floor()


if __name__ == "__main__":
    main()
