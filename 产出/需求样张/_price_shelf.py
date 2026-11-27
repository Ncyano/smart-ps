"""把空白货架照片做成本项目要求的"线下价格图"：在货架前沿的价签条上画出真实价签。

价格、品牌、规格全部由这里写死，保证数字不会糊、不会错。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SHELF = ROOT / "_assets" / "Photorealistic_close_up_photo__2026-11-26T02-52-42.png"
OUT = ROOT / "03-线下价格图" / "1.png"

W, H = 1080, 1350
FB = "C:/Windows/Fonts/msyhbd.ttc"
FR = "C:/Windows/Fonts/msyh.ttc"

# 每层货架：裁切后坐标里的价签条上沿 y（在 1080x1350 坐标系内），以及该层的在售 SKU
SHELVES = [
    dict(y=372, items=[
        ("善存 维生素D3", "60 粒", "89"),
        ("竞品C 钙片", "90 片", "159"),
        ("竞品B 鱼油", "100 粒", "238"),
        ("Blackmores 维骨力", "120 粒", "268"),
    ]),
    dict(y=802, items=[
        ("示例品牌 白芸豆", "60 粒", "199"),
        ("竞品A 胶原", "30 支", "350"),
        ("竞品C 蔓越莓", "30 粒", "215"),
        ("Doppelherz 辅酶", "50 粒", "186"),
    ]),
    dict(y=1176, items=[
        ("善存 男士多维", "60 片", "128"),
        ("竞品B 益生菌", "30 袋", "168"),
        ("竞品C 睡眠片", "60 粒", "149"),
        ("Blackmores 鱼油", "400 粒", "320"),
    ]),
]


def crop_base() -> Image.Image:
    im = Image.open(SHELF).convert("RGB")
    w, h = im.size
    y0, ch = 40, 1190          # 避开右下角平台水印
    cw = int(round(ch * W / H))
    x0 = (w - cw) // 2
    return im.crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.LANCZOS)


def barcode(d: ImageDraw.ImageDraw, x: int, y0: int, h: int):
    """价签左下角的条码块，纯装饰，但真实价签都有。"""
    import random
    rnd = random.Random(x)
    cx = x
    while cx < x + 46:
        bw = rnd.choice((1, 1, 2, 3))
        if rnd.random() > 0.35:
            d.rectangle([cx, y0, cx + bw - 1, y0 + h - 4], fill=(40, 40, 40))
        cx += bw + rnd.choice((1, 2))


def draw_label(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, item):
    brand, spec, price = item
    d.rectangle([x, y, x + w, y + h], fill=(253, 253, 251), outline=(196, 196, 192), width=1)
    d.line([x + 1, y + h + 1, x + w - 1, y + h + 1], fill=(150, 150, 146))

    f_brand = ImageFont.truetype(FR, 15)
    f_spec = ImageFont.truetype(FR, 12)
    f_cur = ImageFont.truetype(FB, 12)
    f_price = ImageFont.truetype(FB, 26)
    f_unit = ImageFont.truetype(FR, 12)

    # 上排：品牌 + 规格；下排：条码 + 大号价格
    d.text((x + 10, y + 4), brand, font=f_brand, fill=(38, 38, 38))
    d.text((x + 10, y + 23), spec, font=f_spec, fill=(126, 126, 126))

    barcode(d, x + 11, y + 40, 20)

    unit_w = d.textlength("元", font=f_unit)
    cur_w = d.textlength("¥", font=f_cur)
    num_w = d.textlength(price, font=f_price)
    right = x + w - 8
    px = right - unit_w - 3 - num_w
    d.text((px, y + 32), price, font=f_price, fill=(16, 16, 16))
    d.text((px - cur_w, y + 42), "¥", font=f_cur, fill=(16, 16, 16))
    d.text((right - unit_w, y + 48), "元", font=f_unit, fill=(126, 126, 126))


def main():
    im = crop_base()
    d = ImageDraw.Draw(im)
    lab_h, lab_w = 66, 216
    for sh in SHELVES:
        y = sh["y"]
        n = len(sh["items"])
        gap = (W - 40 - lab_w * n) / max(n - 1, 1)
        for i, item in enumerate(sh["items"]):
            x = int(24 + i * (lab_w + gap))
            draw_label(d, x, y, lab_w, lab_h, item)

    # 顶部与底部各留一条门店信息（不在照片上，作为窄版压条）
    f_t = ImageFont.truetype(FB, 30)
    f_s = ImageFont.truetype(FR, 17)
    d.rectangle([0, 0, W, 74], fill=(22, 62, 52))
    d.text((36, 22), "华康药房 · 保健品专区", font=f_t, fill=(255, 255, 255))
    d.text((W - 36, 30), "全场正品 · 价签即实付", font=f_s, fill=(196, 224, 210), anchor="ra")

    d.rectangle([0, H - 60, W, H], fill=(22, 62, 52))
    d.text((W / 2, H - 30), "价格为门店标价示意 · 以实际收银价为准", font=f_s, fill=(198, 222, 210), anchor="mm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT, quality=95)
    print(OUT, im.size)


if __name__ == "__main__":
    main()
