"""线下大屏 KV 生成器（第二版，严格对齐素材结构）。

素材结构（`素材例子/制作线下大屏.../` 三张 NuiBay 大屏）：
    纯黑底 → 品牌名（白）→ 主标题（金色大字）→ 副标题（白）
    → 产品居中，从底部打紫红光，两侧各一道竖向波浪光线
    → 产品底下椭圆光环
    → 三个细白框数据徽章 → 免责小字 → 品牌 slogan（金）→ 底部媒体条

为什么不用模型直接生成整屏：模型会在瓶身和标题上写乱码，且没有信息层级。
这里文字全部 PIL 绘制，产品是单独渲染的透明 PNG。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
PROD = ROOT / "_products" / "bottle_v3.png"
FB = "C:/Windows/Fonts/msyhbd.ttc"
FR = "C:/Windows/Fonts/msyh.ttc"

BLACK = (6, 4, 9)
MAGENTA = (226, 56, 148)
GOLD = (233, 200, 138)
WHITE = (255, 255, 255)


# ---------------------------------------------------------------- 工具
def font(size, bold=True):
    return ImageFont.truetype(FB if bold else FR, max(8, int(size)))


def text_c(d, cx, cy, s, f, fill, spacing=0):
    """居中绘制，cy 为文字视觉中心（anchor=mm），spacing 为字间距。"""
    if not spacing:
        d.text((cx, cy), s, font=f, fill=fill, anchor="mm")
        return
    widths = [d.textlength(ch, font=f) for ch in s]
    total = sum(widths) + spacing * (len(s) - 1)
    x = cx - total / 2
    for ch, w in zip(s, widths):
        d.text((x + w / 2, cy), ch, font=f, fill=fill, anchor="mm")
        x += w + spacing


def screen_add(base, layer):
    a = np.asarray(base, np.float32)
    b = np.asarray(layer, np.float32)
    out = 255 - (255 - a) * (255 - b) / 255
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def radial(W, H, cx, cy, rx, ry, color, power=2.0):
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    a = (np.clip(1 - d, 0, 1) ** power)[..., None]
    rgb = np.array(color, np.float32) * a
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- 背景：黑 + 洋红光线
def background(W, H, base_y=None):
    by = H * 0.735 if base_y is None else base_y
    img = Image.new("RGB", (W, H), BLACK)

    # 产品背后竖向背光
    img = screen_add(img, radial(W, H, W * 0.5, H * 0.60, W * 0.30, H * 0.34, (58, 12, 40), 1.5))
    img = screen_add(img, radial(W, H, W * 0.5, H * 0.62, W * 0.17, H * 0.26, (128, 26, 86), 1.9))

    # 产品底部强光池
    yy, xx = np.mgrid[0:H, 0:W]
    pool = np.exp(-(((xx - W * 0.5) / (W * 0.20)) ** 2 + ((yy - by) / (H * 0.022)) ** 2))
    img = screen_add(img, Image.fromarray(
        (pool[..., None] * np.array(MAGENTA, np.float32) * 0.95).astype(np.uint8), "RGB"))

    # 两侧竖向波浪光线（素材最显著的视觉母题）
    lines = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(lines)
    for side in (-1, 1):
        for amp, freq, off, wd, br in [(0.030, 6.2, 0.075, 8, 255), (0.048, 4.6, 0.145, 4, 160)]:
            pts = []
            for t in np.linspace(0, 1, 160):
                y = H * (1.04 - 0.76 * t)
                x = W * 0.5 + side * (W * (0.075 + off + 0.26 * t) + W * amp * np.sin(t * freq))
                pts.append((x, y))
            c = tuple(int(v * br / 255) for v in MAGENTA)
            d.line(pts, fill=c, width=wd)
    img = screen_add(img, lines.filter(ImageFilter.GaussianBlur(2.5)))
    img = screen_add(img, lines.filter(ImageFilter.GaussianBlur(11)))

    # 横向弧光
    arc = Image.new("RGB", (W, H), (0, 0, 0))
    d2 = ImageDraw.Draw(arc)
    for side in (-1, 1):
        pts = []
        for t in np.linspace(0, 1, 90):
            x = W * 0.5 + side * (W * 0.10 + W * 0.44 * t)
            y = by - H * (0.060 * np.sin(np.pi * t * 0.9))
            pts.append((x, y))
        d2.line(pts, fill=tuple(int(v * 0.55) for v in MAGENTA), width=5)
    img = screen_add(img, arc.filter(ImageFilter.GaussianBlur(7)))
    return img


def halo(W, H, cy, rx, ry, color, strength=1.0):
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for k in range(8, 0, -1):
        f = k / 8
        d.ellipse([W * 0.5 - rx * f, cy - ry * f, W * 0.5 + rx * f, cy + ry * f],
                  fill=tuple(int(c * strength * (1 - f + 0.18)) for c in color))
    return img.filter(ImageFilter.GaussianBlur(20))


# ---------------------------------------------------------------- 主函数
def make_kv(W=1080, H=2160, brand_en="BRAND", brand_cn="示例品牌",
            title="高纯深海鱼油", sub="示例品牌 rTG 型鱼油",
            kicker="专注血脂健康  内在更轻盈",
            badges=(("1000mg", "每粒含量"), ("99.9%", "高纯度"), ("rTG", "易吸收型")),
            tail=("科学力量  成就更好的你", "SCIENCE FOR A HEALTHIER YOU"),
            footer="BRAND  示例品牌   ·   全国连锁药房有售",
            prod_h=0.455, prod_cy=0.505):
    # 先算产品位置，背景光池要跟着产品底走
    p = Image.open(PROD).convert("RGBA")
    p = p.crop(p.split()[3].getbbox())
    ph = int(H * prod_h)
    pw = int(p.width * ph / p.height)
    p = p.resize((pw, ph), Image.LANCZOS)
    px = (W - pw) // 2
    py = int(H * prod_cy) - ph // 2
    bottom = py + ph

    img = background(W, H, base_y=bottom).convert("RGBA")

    # 落地光环
    img = screen_add(img.convert("RGB"), halo(W, H, bottom - int(H * 0.003), W * 0.20, H * 0.014,
                                             MAGENTA, 1.0)).convert("RGBA")

    # 轮廓柔光
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([px - W * 0.035, py + ph * 0.08, px + pw + W * 0.035, bottom], fill=(160, 32, 106, 58))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(58)))

    img.alpha_composite(p, (px, py))

    # 产品底部打光：只在瓶身 alpha 内叠加自下而上的洋红
    yy, xx = np.mgrid[0:H, 0:W]
    ramp = np.clip((yy - (bottom - ph * 0.38)) / (ph * 0.38), 0, 1) ** 2.2
    pmask = np.zeros((H, W), np.float32)
    hard = p.getchannel("A").filter(ImageFilter.MinFilter(5))
    arr = np.asarray(hard).astype(np.float32) / 255
    pmask[py:py + ph, px:px + pw] = arr
    tint = (ramp * pmask)[..., None] * np.array(MAGENTA, np.float32) * 0.38
    img = screen_add(img.convert("RGB"),
                     Image.fromarray(np.clip(tint, 0, 255).astype(np.uint8), "RGB")).convert("RGBA")

    d = ImageDraw.Draw(img)
    cx = W // 2

    # 品牌（白）
    text_c(d, cx, H * 0.052, brand_en, font(H * 0.0200), WHITE, spacing=W * 0.0125)

    # 主标题（金）
    fs = int(H * 0.0470)
    while d.textlength(title, font=font(fs)) > W * 0.88 and fs > 20:
        fs -= 4
    text_c(d, cx, H * 0.100, title, font(fs), GOLD, spacing=W * 0.004)

    # 副标题（白）
    text_c(d, cx, H * 0.158, sub, font(H * 0.0230), (238, 232, 238), spacing=W * 0.003)

    # kicker + 两侧短横线
    ky = H * 0.202
    kf = font(H * 0.0142, False)
    text_c(d, cx, ky, kicker, kf, (172, 162, 172), spacing=W * 0.0015)
    kw = d.textlength(kicker, font=kf) + W * 0.0015 * (len(kicker) - 1)
    for sx in (-1, 1):
        d.line([cx + sx * (kw / 2 + W * 0.030), ky, cx + sx * (kw / 2 + W * 0.115), ky],
               fill=(122, 96, 112), width=2)

    # 三个数据框
    by0 = H * 0.775
    bh = H * 0.0560
    bw, gap = W * 0.238, W * 0.028
    bx = cx - (bw * 3 + gap * 2) / 2
    for i, (v, lab) in enumerate(badges):
        x = bx + i * (bw + gap)
        d.rectangle([x, by0, x + bw, by0 + bh], outline=(224, 212, 224), width=2)
        fs2 = int(H * 0.0245)
        while d.textlength(v, font=font(fs2)) > bw * 0.82 and fs2 > 10:
            fs2 -= 2
        text_c(d, x + bw / 2, by0 + bh * 0.335, v, font(fs2), WHITE)
        text_c(d, x + bw / 2, by0 + bh * 0.735, lab, font(H * 0.0125, False), (196, 176, 194))

    # 免责小字
    for i, s2 in enumerate([
        "* 本品为膳食营养补充剂，不能代替药物。",
        "* 数据来自品牌方公开资料，实际以产品标签为准。",
    ]):
        text_c(d, cx, H * 0.868 + i * H * 0.0165, s2, font(H * 0.0102, False), (116, 102, 114))

    # 品牌 slogan（金）
    text_c(d, cx, H * 0.9200, tail[0], font(H * 0.0180), GOLD, spacing=W * 0.0035)
    text_c(d, cx, H * 0.9510, tail[1], font(H  * 0.0086, False), (150, 138, 148), spacing=W * 0.0030)

    # 底部媒体条
    d.rectangle([0, H - int(H * 0.017), W, H], fill=(10, 8, 12))
    text_c(d, cx, H - H * 0.0104, footer, font(H * 0.0092, False), (152, 142, 152), spacing=2)
    return img.convert("RGB")


# ---------------------------------------------------------------- 透视贴屏（光影融合版）
def warp_onto(scene_path, kv, quad, out_path, *, gain=1.0, tex=2.4, blur_kv=0.9,
              lift=7.0, env=0.15, spill=0.75, edge_dark=0.80, edge_w=3.0, ambient=0.0, tint_mix=0.35,
              band=0.05, refl=0.10, gamma=1.0, backlight=0.22, backlight_span=0.10):
    """把 KV 贴到场景屏位，并按"发光面板"的物理关系融合。

    让贴图不像"贴上去"的关键不是对齐，而是这几件事：
    1. 黑位不是 0：真实屏即使显示黑色，也会反射环境光、有最低亮度 → lift + 环境漫反射
    2. 保留原屏的亮度场（背光透射的明暗渐变），用比值调制而不是重算
    3. KV 要按场景的景深模糊，锐利的贴图一眼假
    4. 边缘要压暗（边框遮挡与内阴影），不能硬切
    5. 屏幕的色温要被现场光染一点，光还要溢出到周围墙面
    """
    import cv2

    scene = cv2.imdecode(np.fromfile(str(scene_path), np.uint8), cv2.IMREAD_COLOR)
    H, W = scene.shape[:2]

    kv_b = kv.filter(ImageFilter.GaussianBlur(blur_kv))
    src = np.float32([[0, 0], [kv_b.width, 0], [kv_b.width, kv_b.height], [0, kv_b.height]])
    M = cv2.getPerspectiveTransform(src, np.float32(quad))
    warped = cv2.warpPerspective(np.asarray(kv_b)[:, :, ::-1], M, (W, H), flags=cv2.INTER_AREA)

    hard = np.zeros((H, W), np.uint8)
    cv2.fillPoly(hard, [np.int32(quad)], 255)
    # 真实灯箱里的海报是满铺到边框内沿的，四周不存在漏光。
    # 所以遮罩绝不内缩，只做 1.3px 羽化，避免贴图边缘出现锯齿硬线。
    # 羽化只给 0.6px：羽化过宽会让面板的亮色从边缘"透"出来，形成一圈浅灰的漏光线
    m3 = (cv2.GaussianBlur(hard, (0, 0), 0.6).astype(np.float32) / 255)[..., None]

    # 原屏亮度场：比值调制，保留背光透射的明暗
    gray = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY).astype(np.float32)
    low = cv2.GaussianBlur(gray, (0, 0), 16)
    ref = float(np.median(low[hard > 128])) or 1.0
    lum = np.clip(low / ref, 0.78, 1.22)[..., None]

    # 屏幕自发光
    lit = (warped.astype(np.float32) + lift) * lum * gain * (1 / 1.0)
    if gamma != 1.0:
        lit = np.clip(lit / 255.0, 0, 1) ** gamma * 255

    # 灯箱背光透射：整块面板有一层均匀的底光，深色创意也不会黑到死
    if ambient:
        lit = 255 - (255 - lit) * (255 - np.array(ambient, np.float32) * 255) / 255

    # 色温：被现场光染一点
    around = scene[hard > 128].reshape(-1, 3).mean(axis=0)
    mid = scene.reshape(-1, 3).mean(axis=0)
    tin = 1.0 + (around / np.maximum(mid, 1) - 1.0) * tint_mix
    lit = lit * tin

    # 环境漫反射：场景强模糊后 screen 叠加，黑位因此不再是死黑
    env_img = cv2.GaussianBlur(scene, (0, 0), 30).astype(np.float32) * env
    lit = 255 - (255 - lit) * (255 - env_img) / 255

    # 玻璃面板的环境倒影：取屏位左侧的景物（通道/商场）映射到屏面，模拟对面景物的映像
    if refl:
        q = np.array(quad, np.float32)
        x0, x1 = int(q[:, 0].min()), int(q[:, 0].max())
        y0, y1 = int(q[:, 1].min()), int(q[:, 1].max())
        sx1 = max(0, x0 - 8)
        sx0 = max(0, sx1 - (x1 - x0))
        src_img = scene[y0:y1, sx0:sx1]
        if src_img.size:
            rf = cv2.resize(src_img, (W, H), interpolation=cv2.INTER_LINEAR)
            rf = cv2.GaussianBlur(rf, (0, 0), 26).astype(np.float32) * refl
            lit = 255 - (255 - lit) * (255 - rf) / 255

    # 顶部灯管反射带（真实灯箱上沿总有一条亮带）
    if band:
        q = np.array(quad, np.float32)
        y0, y1 = q[:, 1].min(), q[:, 1].max()
        gyy = np.mgrid[0:H, 0:W][0].astype(np.float32)
        rel = np.clip((gyy - y0) / max(y1 - y0, 1), 0, 1)
        b = np.exp(-((rel - 0.045) / 0.075) ** 2) * band * 255
        lit = 255 - (255 - lit) * (255 - b[..., None]) / 255

    # 屏幕纹理：摩尔纹 + 像素噪声
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    rng = np.random.default_rng(7)
    lit += (np.sin(yy * 2.35) * np.sin(xx * 0.55) * tex * 0.45
            + rng.normal(0, tex, (H, W)))[..., None]

    # 边缘压暗 + 灯箱背光衰减：真实灯箱越靠边越暗（灯管在中间），
    # 同时这一层渐变能让海报边缘"渐隐"进边框阴影，不会露出硬接缝
    dist = cv2.distanceTransform(hard, cv2.DIST_L2, 5)
    ek = np.clip(dist / max(edge_w, 0.5), 0, 1)
    lit *= (edge_dark + (1 - edge_dark) * ek)[..., None]
    if backlight:
        span = min(W, H) * backlight_span
        bl_f = np.clip(dist / span, 0, 1) ** 0.75
        lit *= (1 - backlight + backlight * bl_f)[..., None]
    lit = np.clip(lit, 0, 255)

    # 光溢出：屏照亮周围墙面
    base = scene.astype(np.float32)
    if spill:
        src_sp = (np.clip(lit, 0, 255) * m3).astype(np.uint8)
        halo = cv2.GaussianBlur(src_sp, (0, 0), 26).astype(np.float32) * spill
        base = 255 - (255 - base) * (255 - halo) / 255

    res = np.clip(base * (1 - m3) + lit * m3, 0, 255).astype(np.uint8)
    cv2.imencode(".png", res)[1].tofile(str(out_path))
    return out_path

