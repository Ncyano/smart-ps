"""排版算子：字体、折行、自动缩号、渐变底、角标。

踩过的坑（`knowledge/03-踩坑库.md` C 组）在这里逐个规避：

- `rounded_rectangle(fill=(0,0,0,0))` 画在 RGB 画布上会变成**纯黑块**
  → 所有装饰先画在 RGBA 图层，再 `alpha_composite`
- 中文必须指定字体文件，`anchor="mm"` 居中，不能用默认位图字体
- 文字溢出必须**可判定**，不能"看着差不多" → `fit_block` 返回 ok 标志
"""

from __future__ import annotations

import glob
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 按优先级找中文字体。缺失时退到默认字体（会很难看，但不会崩）
_FONT_CANDIDATES = {
    True: [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
    False: [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ],
}
_CACHE: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}
_FONT_PATH: dict[bool, str] = {}


def font_path(bold: bool = True) -> str:
    if bold in _FONT_PATH:
        return _FONT_PATH[bold]
    for p in _FONT_CANDIDATES[bold]:
        if Path(p).exists():
            _FONT_PATH[bold] = p
            return p
    # 兜底：扫一遍系统字体目录里第一个 ttc/ttf（只做一次）
    for pattern in ("/usr/share/fonts/**/*CJK*.tt[cf]", "/System/Library/Fonts/**/*.ttc"):
        hit = glob.glob(pattern, recursive=True)
        if hit:
            _FONT_PATH[bold] = hit[0]
            return hit[0]
    _FONT_PATH[bold] = ""
    return ""


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    key = (bold, size)
    if key in _CACHE:
        return _CACHE[key]
    p = font_path(bold)
    try:
        f = ImageFont.truetype(p, size) if p else ImageFont.load_default()
    except OSError:
        f = ImageFont.load_default()
    _CACHE[key] = f
    return f


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> float:
    return float(draw.textlength(s, font=f))


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont,
         max_w: float) -> list[str]:
    """按像素宽度折行。中文逐字断，英文尽量不切断单词。"""
    text = (text or "").strip()
    if not text:
        return []
    lines: list[str] = []
    cur = ""
    for ch in text:
        probe = cur + ch
        if text_w(draw, probe, f) <= max_w or not cur:
            cur = probe
            continue
        # 英文单词中间不留断点：把最后一个空格之后的部分挪到下一行
        if ch.isalnum() and " " in cur:
            head, _, tail = cur.rpartition(" ")
            if head:
                lines.append(head)
                cur = tail + ch
                continue
        lines.append(cur)
        cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_block(draw: ImageDraw.ImageDraw, text: str, max_w: float, max_h: float,
              max_size: int, min_size: int, line_gap: float = 1.32,
              bold: bool = True) -> tuple[ImageFont.FreeTypeFont, list[str], bool]:
    """从大到小试字号，直到整段文字装进 max_w × max_h。

    返回 (字体, 行列表, 是否装得下)。装不下也返回最小字号的结果——
    画出来让人看见溢出，比悄悄截断强。
    """
    text = (text or "").strip()
    if not text:
        return font(min_size, bold), [], True
    size = max_size
    while size > min_size:
        f = font(size, bold)
        lines = wrap(draw, text, f, max_w)
        h = len(lines) * size * line_gap
        widest = max((text_w(draw, ln, f) for ln in lines), default=0)
        if h <= max_h and widest <= max_w:
            return f, lines, True
        size -= 2
    f = font(min_size, bold)
    return f, wrap(draw, text, f, max_w), False


def luminance(rgb: tuple[int, int, int]) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0


def readable_on(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """深底配白字，浅底配近黑字。取 0.55 作分界（比 0.5 稍偏亮，避免灰底配白字看不清）。"""
    return (252, 250, 246) if luminance(rgb) < 0.55 else (28, 26, 24)


def mean_color(im: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    from PIL import ImageStat

    return tuple(int(v) for v in ImageStat.Stat(im.crop(box).convert("RGB")).mean[:3])  # type: ignore[return-value]


def scrim(size: tuple[int, int], color: tuple[int, int, int], top_alpha: int = 226,
          fade_to: float = 0.55) -> Image.Image:
    """自上而下的渐隐遮罩，用来在照片上压出可读的文字区。

    用 RGBA 图层再 alpha_composite —— 直接在 RGB 上画半透明会得到黑块。
    """
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(h):
        t = y / max(1, h - 1)
        a = int(top_alpha * max(0.0, 1.0 - t / fade_to) ** 1.25)
        if a:
            d.line([(0, y), (w, y)], fill=(color[0], color[1], color[2], a))
    return layer


def badge(im: Image.Image, xy: tuple[int, int], text: str, size: int,
          fg: tuple[int, int, int], bg: tuple[int, int, int]) -> Image.Image:
    """圆角标签。返回新图（RGBA 合成后转 RGB）。"""
    pad_x, pad_y = int(size * 0.9), int(size * 0.45)
    tmp = ImageDraw.Draw(im)
    f = font(size)
    tw = text_w(tmp, text, f)
    w = int(tw + pad_x * 2)
    h = int(size + pad_y * 2)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([xy[0], xy[1], xy[0] + w, xy[1] + h], radius=h // 2,
                        fill=(bg[0], bg[1], bg[2], 232))
    d.text((xy[0] + w / 2, xy[1] + h / 2 + 1), text, font=f, fill=fg, anchor="mm")
    out = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    return out
