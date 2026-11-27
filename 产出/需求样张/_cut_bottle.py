"""从模型出的白底产品图切出可用透明图。

白底药瓶的抠图有三个反复踩的坑，这里按顺序处理：
1. 瓶底右下的**地面投影**会被当成主体保留 → 用"三段全亮 + 位置"判定剔除
2. 瓶身两侧的**柔和阴影**会留下半透明灰片 → alpha 实心化（>150 后闭运算 + 最大连通域 + 膨胀）
3. 半透明边缘的 RGB 仍是白底 → 去白边（unmatt），再把透明区用 inpaint 填成瓶身色
"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "_products" / "gen3" / "Studio_product_photograph_of_o_2026-11-26T04-06-14.png"
DST = ROOT / "_products" / "bottle_v3.png"


def main():
    im = cv2.imdecode(np.fromfile(str(SRC), np.uint8), cv2.IMREAD_COLOR)
    H, W = im.shape[:2]
    b, g, r = im[:, :, 0], im[:, :, 1], im[:, :, 2]
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    mn = np.minimum(np.minimum(b, g), r)

    # 1) 背景：接近纯白且与图像边缘连通
    light = ((gray > 228) & (mn > 218)).astype(np.uint8)
    edge = np.zeros_like(light)
    edge[:3, :] = edge[-3:, :] = 1
    edge[:, :3] = edge[:, -3:] = 1
    n, lab, st, _ = cv2.connectedComponentsWithStats(light | edge, 8)
    bgm = np.zeros_like(light)
    for i in range(1, n):
        x, y, w, h, ar = st[i]
        if x <= 3 or y <= 3 or x + w >= W - 3 or y + h >= H - 3:
            bgm[lab == i] = 1
    alpha = ((1 - cv2.dilate(bgm, np.ones((3, 3), np.uint8), 1)) * 255).astype(np.uint8)

    # 2) 地面投影：瓶底以下、三段全亮的白块
    yy, xx = np.mgrid[0:H, 0:W]
    white = mn > 178
    alpha[((yy > 1200) & (xx > 638) & white) | ((yy > 1300) & (xx > 655) & white)] = 0
    alpha[(yy > 1255) & (xx > 668)] = 0
    alpha[yy > 1345] = 0
    alpha[(yy > 1328) & (xx > 598)] = 0
    alpha[:, 716:] = 0

    # 3) 实心化，清掉两侧半透明灰片
    solid = ((alpha > 150) * 255).astype(np.uint8)
    solid = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(solid, 8)
    solid = ((lab2 == (1 + int(np.argmax(st2[1:, 4])))) * 255).astype(np.uint8)
    solid = cv2.dilate(solid, np.ones((3, 3), np.uint8), 1)
    alpha = cv2.GaussianBlur(solid, (5, 5), 1.5)

    # 4) 去白边：半透明边缘的 unmatt 结果不可靠（alpha 小的时候误差被放大 3-10 倍，
    #    在深色底上就是一圈白色锯齿）。改为把"实心区的颜色"直接外延到边缘与透明区，
    #    边缘像素的 RGB 等于最近的瓶身色，白色彻底消失。
    rgb = im.astype(np.float32)
    af = (alpha.astype(np.float32) / 255)[..., None]
    rgb = np.clip((rgb - (1 - af) * 255) / np.maximum(af, 1e-3), 0, 255).astype(np.uint8)

    core = (alpha > 200).astype(np.uint8)          # 实心核心
    need = (core == 0).astype(np.uint8)            # 需要被填充的像素
    dist, label = cv2.distanceTransformWithLabels(
        need, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    zy, zx = np.where(need == 0)                   # 与 label 编号同序
    lut = rgb[zy, zx].astype(np.uint8)
    lut = np.vstack([lut[0], lut])                 # label 从 1 开始
    filled = lut[np.clip(label, 0, len(lut) - 1)]
    rgb = np.where((core == 0)[..., None], filled, rgb).astype(np.uint8)

    # 4.5) 瓶底/瓶身轮廓上会残留一条玻璃反光高光，在深色底上像白色锯齿
    #      → 轮廓 2px 环带压暗，把硬边融进背景
    er = cv2.erode((alpha > 160).astype(np.uint8), np.ones((5, 5), np.uint8), iterations=2)
    rim = ((alpha > 25).astype(np.uint8) - er).clip(0, 1)
    rim = cv2.GaussianBlur(rim, (3, 3), 0.8).astype(np.float32)[..., None]
    rgb = np.clip(rgb.astype(np.float32) * (1 - 0.30 * rim), 0, 255).astype(np.uint8)

    # 5) 标签上的抽象几何色块，缩到 1/2 尺寸后会像污渍
    #    → 双边滤波抹平色块边缘（保边不保色），圆柱明暗保留
    mask = np.zeros((H, W), np.uint8)
    mask[505:955, 398:632] = 255
    mask = cv2.bitwise_and(mask, ((alpha > 200).astype(np.uint8)) * 255)
    soft = cv2.bilateralFilter(rgb, 19, 75, 19)
    soft = cv2.bilateralFilter(soft, 19, 75, 19)
    mm = (cv2.GaussianBlur(mask, (0, 0), 6).astype(np.float32) / 255)[..., None]
    rgb = np.clip(rgb * (1 - mm) + soft * mm, 0, 255).astype(np.uint8)

    out = np.dstack([rgb, alpha])
    cv2.imencode(".png", out)[1].tofile(str(DST))
    print("saved", DST, (alpha > 20).sum())


if __name__ == "__main__":
    main()
