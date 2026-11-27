"""03 第二版：门店堆头 + 大价签。

底图是一张「空白价签牌」实拍，牌子本身是暖白（约 213,195,177），
所以这里不覆盖白底，直接把文字印在牌面上，避免"贴了一张白纸"的观感。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "_assets" / "Photorealistic_photo_of_a_prom_2026-11-26T02-55-16.png"
OUT = ROOT / "03-线下价格图" / "2.png"

W, H = 1080, 1350
FB = "C:/Windows/Fonts/msyhbd.ttc"
FR = "C:/Windows/Fonts/msyh.ttc"

GREEN = (16, 62, 50)
CREAM = (246, 241, 229)
RED = (176, 42, 39)
DARK = (30, 33, 30)
GREY = (108, 108, 102)


def main():
    im = Image.open(SRC).convert("RGB").resize((W, H), Image.LANCZOS)
    d = ImageDraw.Draw(im)

    bx0, bx1 = 196, 846
    by0, by1 = 140, 600

    # 顶部绿条（印刷感）
    d.rectangle([bx0, by0, bx1, by0 + 76], fill=GREEN)
    f_h = ImageFont.truetype(FB, 32)
    d.text((bx0 + 24, by0 + 40), "门店会员日 · 胶原专区", font=f_h, fill=CREAM, anchor="lm")
    f_tag = ImageFont.truetype(FB, 20)
    d.text((bx1 - 22, by0 + 40), "11.28 — 12.05", font=f_tag, fill=(190, 218, 202), anchor="rm")

    f_name = ImageFont.truetype(FB, 40)
    f_sub = ImageFont.truetype(FR, 21)
    d.text((bx0 + 22, by0 + 96), "品牌A 示例产品", font=f_name, fill=DARK)
    d.text((bx0 + 22, by0 + 150), "8 瓶 / 盒 · 每瓶 50ml · 五重抗氧复配", font=f_sub, fill=GREY)

    # 价格
    f_cur = ImageFont.truetype(FB, 32)
    f_int = ImageFont.truetype(FB, 96)
    f_dec = ImageFont.truetype(FB, 48)
    f_unit = ImageFont.truetype(FB, 24)
    py = by0 + 196
    x = bx0 + 22
    d.text((x, py + 44), "¥", font=f_cur, fill=RED)
    d.text((x + 34, py), "69", font=f_int, fill=RED)
    w_int = d.textlength("69", font=f_int)
    d.text((x + 34 + w_int - 6, py + 34), ".16", font=f_dec, fill=RED)
    w_dec = d.textlength(".16", font=f_dec)
    d.text((x + 34 + w_int + w_dec - 6, py + 66), "起 / 盒", font=f_unit, fill=(86, 86, 80))

    # 原价、立减
    f_old = ImageFont.truetype(FR, 23)
    oy = py + 118
    old = "原价 ¥89.16"
    d.text((x, oy), old, font=f_old, fill=(122, 122, 116))
    ow = d.textlength(old, font=f_old)
    d.line([x, oy + 15, x + ow, oy + 15], fill=(122, 122, 116), width=2)
    f_save = ImageFont.truetype(FB, 25)
    d.text((x + ow + 22, oy - 4), "会员立减 ¥20", font=f_save, fill=GREEN)

    # 右侧要点
    f_note = ImageFont.truetype(FR, 17)
    notes = ["全国门店同享，售完即止", "礼盒装，折合单瓶价以实际定价为准"]
    for i, t in enumerate(notes):
        d.text((bx1 - 22, by0 + 330 + i * 27), t, font=f_note, fill=GREY, anchor="ra")

    # 底部门店信息条（同时盖住平台水印）
    d.rectangle([0, H - 64, W, H], fill=GREEN)
    f_bar = ImageFont.truetype(FR, 19)
    d.text((W / 2, H - 32), "华康药房 · 保健品专区｜价格为门店标价示意，以实际收银价为准",
           font=f_bar, fill=(196, 220, 208), anchor="mm")

    im.save(OUT, quality=95)
    print(OUT, im.size)


if __name__ == "__main__":
    main()
