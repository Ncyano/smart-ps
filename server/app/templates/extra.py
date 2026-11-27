"""五类补充模板：成对对比 / 价格图 / 证据图 / 表格海报 / 线下大屏。

共同点：**内容全部由代码排版，模型不画字、不编数**。
区别只在输入与版式：

| 模板 | 输入 | 是否调模型 | 关键约束 |
|---|---|---|---|
| `pair`   | before + after | 否（成对合成） | 必标「示意图」，除非声明真实拍摄 |
| `price`  | 产品图 + 现价 | 是（只生成背景/场景） | 现价必填，不能由排版编 |
| `patent` | 原件扫描件 | 否 | 没有扫描件就拒绝出图 |
| `table`  | 表头 + 行数据 | 否 | 逐格量宽高，溢出即 fail |
| `screen` | 场景照 + 画面 | 否 | 四角置信度不足必须人工校准 |

`price` 之所以还调模型：价签需要"产品在货架/堆头上的实拍感"，
这部分是场景生成；价格数字、字号、颜色全由 PIL 画。

其他四个 `compose=True`：一次模型调用都不发。这类物料一旦让模型碰内容，
就会出现"证书编号看着像真的但其实是编的"——比排版难看严重得多。
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .. import scenes as scenemod
from . import layout as L
from .base import LayoutReport, Template

WHITE = (255, 255, 255)
INK = (26, 24, 22)
DIM = (110, 106, 100)
RED = (186, 32, 24)      # 价格用红：中文电商惯例，涨价/降价都是红，别按欧美绿红反着来
LINE = (222, 220, 214)


def _cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比盖满 + 居中裁切。和 export.cover_fit 同语义，模板内部不 import 导出层。"""
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    return im.crop(((nw - tw) // 2, (nh - th) // 2, (nw - tw) // 2 + tw, (nh - th) // 2 + th))


def _fit_text(d: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int,
              hi: int, lo: int, bold: bool = True, gap: float = 1.3):
    return L.fit_block(d, text, max_w, max_h, hi, lo, gap, bold=bold)


# ─────────────────────────── 1. 成对前后对比 ───────────────────────────

_PAIR = (1080, 1350)


def _pair(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """双联前后对比：左「使用前」右「使用后」，中间一道缝。

    为什么不由模型生成"使用后"：这类图最容易被当成功效证据。
    真实拍摄的 before/after 是证据，模型生成的"变好看了"是广告示意。
    所以这里只做合成与标注，并且在图内**强制**写清是哪种。
    """
    rep = LayoutReport()
    W, H = _PAIR
    before = ctx.get("before")
    after = ctx.get("after")

    if before is None:
        rep.fail("缺少「使用前」原图（成对模板必须两张都在，不能只给结果）")
        return Image.new("RGB", _PAIR, WHITE), rep.to_dict()
    if after is None:
        # 允许用本批生成的候选当"使用后"，但要在报告里说清楚来源
        after = im
        rep.stats["after_source"] = "本批生成"
    else:
        rep.stats["after_source"] = "用户提供"

    head = int(H * 0.075)
    foot = int(H * 0.085)
    body_h = H - head - foot
    half = W // 2
    canvas = Image.new("RGB", (W, H), WHITE)

    left = _cover(before.convert("RGB"), (half, body_h))
    right = _cover(after.convert("RGB"), (half, body_h))
    canvas.paste(left, (0, head))
    canvas.paste(right, (half, head))

    # 中缝：4px 白线 + 1px 灰边，避免两半颜色直接相接看着像一张歪图
    d0 = ImageDraw.Draw(canvas)
    d0.line([(half - 1, head), (half - 1, head + body_h)], fill=LINE, width=1)
    d0.line([(half + 2, head), (half + 2, head + body_h)], fill=LINE, width=1)

    title = str(copy.get("title") or "").strip()
    d = ImageDraw.Draw(canvas)
    if title:
        f, lines, ok = _fit_text(d, title, W - 120, int(head * 0.8), 44, 26)
        if not ok:
            rep.fail("标题过长，装不下")
        y = (head - int(f.size * 1.25 * len(lines))) // 2
        for ln in lines:
            tw = L.text_w(d, ln, f)
            d.text(((W - tw) / 2, y), ln, font=f, fill=INK)
            y += int(f.size * 1.25)

    # 角标：底色暗，白字，位置固定在两半的底部
    tag_font = L.font(34)
    for label, cx in (("使用前", half // 2), ("使用后", half + half // 2)):
        tw = L.text_w(d, label, tag_font)
        bw, bh = int(tw + 52), int(34 * 1.9)
        box = (cx - bw // 2, head + body_h - bh - 36, cx + bw // 2, head + body_h - 36)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.rounded_rectangle(box, radius=bh // 2, fill=(18, 17, 16, 208))
        ld.text((cx, (box[1] + box[3]) // 2), label, font=tag_font, fill=(252, 250, 246), anchor="mm")
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")
        d = ImageDraw.Draw(canvas)

    # 免责声明：默认"示意图"。只有用户明确声明是真实拍摄才换措辞。
    real = str(copy.get("evidence") or "").strip().lower() in ("real", "实拍", "真实拍摄", "true")
    note = str(copy.get("footnote") or "").strip() or (
        "真实拍摄，未经修图" if real else "示意图：效果因人而异，不作为功效证明"
    )
    fnote, _l, okn = _fit_text(d, note, W - 100, int(foot * 0.55), 24, 18, bold=False)
    if not okn:
        rep.fail("脚注过长")
    tw = L.text_w(d, note, fnote)
    d.text(((W - tw) / 2, head + body_h + (foot - int(fnote.size * 1.3)) // 2), note,
           font=fnote, fill=DIM if not real else (30, 90, 60))
    rep.stats.update({"evidence": "real" if real else "demo", "seam_px": 4})
    return canvas, rep.to_dict()


PAIR = Template(
    id="pair",
    name="前后对比（成对）",
    desc="左「使用前」右「使用后」，双联 1080×1350。默认标注「示意图」，真实拍摄需显式声明。",
    prompt_extra=[],
    export="original",
    canvas=_PAIR,
    reframes=True,
    compose=True,
    inputs=["before", "after"],
    copy_fields=["title", "evidence", "footnote"],
    apply=_pair,
)


# ─────────────────────────── 2. 价格图 ───────────────────────────

_PRICE = (1080, 1350)


def _price(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """价格图：上半产品图，下半白底价签。价格数字由代码画，字号可核。

    为什么价签单独一块白底：价格要**一眼看清**。压在照片上不管加多少描边，
    在花哨背景上都会糊；白底 + 红字 + 固定留白是货架价签的既有规范。
    """
    rep = LayoutReport()
    W, H = _PRICE
    pad = 72
    panel_y = int(H * 0.585)

    now = str(copy.get("price_now") or "").strip()
    if not now:
        rep.fail("缺现价（copy.price_now）：价格图没有价格就是废图，且不能由系统编")
        return _cover(im.convert("RGB"), _PRICE), rep.to_dict()

    canvas = Image.new("RGB", (W, H), WHITE)
    # 上半区用 contain 不用 cover：价签区只有画面 58% 高，cover 会把竖构图的产品
    # 上下裁掉一截（实测只剩中间一条），而价签图最不能缺的就是完整产品。
    top_area = ImageOps.contain(im.convert("RGB"), (W, panel_y), Image.LANCZOS)
    canvas.paste(top_area, ((W - top_area.width) // 2, (panel_y - top_area.height) // 2))
    d = ImageDraw.Draw(canvas)

    # 活动角标
    tag = str(copy.get("tag") or "").strip()
    y = panel_y + 44
    if tag:
        f_tag = L.font(26)
        tw = L.text_w(d, tag, f_tag)
        canvas = L.badge(canvas, (pad, y), tag, 26, WHITE, RED)
        d = ImageDraw.Draw(canvas)
        rep.stats["tag"] = tag
        del tw

    title = str(copy.get("title") or "").strip()
    y += 74
    if title:
        f, lines, ok = _fit_text(d, title, W - pad * 2, 88, 38, 24)
        if not ok:
            rep.fail("标题过长")
        for ln in lines[:2]:
            d.text((pad, y), ln, font=f, fill=INK)
            y += int(f.size * 1.28)

    # 价格行：¥ + 大数字 + 单位，逐段量宽再落字
    y = max(y + 18, panel_y + 176)
    size = 132
    while size > 68:
        f_big = L.font(size)
        f_cur = L.font(int(size * 0.42))
        f_unit = L.font(int(size * 0.26), bold=False)
        unit = str(copy.get("unit") or "").strip()
        w_total = L.text_w(d, "¥", f_cur) + L.text_w(d, now, f_big) + (L.text_w(d, unit, f_unit) + 16 if unit else 0)
        if w_total <= W - pad * 2:
            break
        size -= 6
    else:
        rep.fail(f"价格文本过长（{now}），最小字号仍装不下")
        f_big, f_cur, f_unit = L.font(68), L.font(28), L.font(18, bold=False)

    x = pad
    d.text((x, y + int(size * 0.22)), "¥", font=f_cur, fill=RED)
    x += L.text_w(d, "¥", f_cur)
    d.text((x, y - int(size * 0.10)), now, font=f_big, fill=RED)
    x += L.text_w(d, now, f_big)
    unit = str(copy.get("unit") or "").strip()
    if unit:
        d.text((x + 12, y + int(size * 0.46)), unit, font=f_unit, fill=DIM)
    price_bottom = y + int(size * 1.05)

    # 原价：灰色 + 中划线。划线必须压在文字中线，不能凭感觉写死 y
    was = str(copy.get("price_was") or "").strip()
    if was:
        f_was = L.font(34, bold=False)
        txt = f"原价 ¥{was}"
        tw = L.text_w(d, txt, f_was)
        wy = price_bottom + 14
        d.text((pad, wy), txt, font=f_was, fill=DIM)
        d.line([(pad, wy + int(34 * 0.62)), (pad + tw, wy + int(34 * 0.62))], fill=DIM, width=2)
        price_bottom = wy + 44

    period = str(copy.get("period") or "").strip()
    if period:
        f_p, plines, okp = _fit_text(d, period, W - pad * 2, 60, 24, 18, bold=False)
        if not okp:
            rep.fail("活动时间过长")
        d.text((pad, price_bottom + 12), period, font=f_p, fill=DIM)
        price_bottom += 12 + int(f_p.size * 1.3)

    if price_bottom > H - 60:
        rep.fail(f"价签内容超出下边界（{price_bottom}px > {H - 60}px），请精简文案")

    foot = "价格以门店实际标价为准" if not str(copy.get("footnote") or "").strip() \
        else str(copy.get("footnote")).strip()
    f_foot = L.font(20, bold=False)
    d.text((pad, H - 46), foot, font=f_foot, fill=(150, 146, 140))
    rep.stats.update({"price_now": now, "price_was": was, "price_bottom": price_bottom})
    return canvas, rep.to_dict()


PRICE = Template(
    id="price",
    name="价格图 / 价签",
    desc="上产品图 + 下白底价签：现价红字大号、原价划线、活动角标。现价必填且必须有来源。",
    prompt_extra=[
        "构图给下方留出干净空间：产品位于画面上方 60%，下方保持简洁背景。",
        "画面里不要出现任何文字、价格、数字或标签。",
    ],
    negative_extra=["画面内出现价格数字", "价签", "文字", "水印"],
    export="detail_1080x1440",
    canvas=_PRICE,
    reframes=True,
    copy_fields=["title", "price_now", "price_was", "unit", "period", "tag"],
    apply=_price,
)


# ─────────────────────────── 3. 专利 / 检测证据图 ───────────────────────────

_PATENT = (1080, 1350)


def _patent(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """证据图：原件扫描件 + 引用信息。没有任何一个像素是生成的。

    为什么要单独一个模板：专利号、检测报告编号这类东西，**排版准确不等于内容真实**。
    模型画一张"看起来像证书"的图，比画错产品严重得多——那是伪造证明文件。
    所以这里只接受真实扫描件，没有就拒绝出图。
    """
    rep = LayoutReport()
    W, H = _PATENT
    ev = ctx.get("evidence")
    if ev is None:
        rep.fail("未附原件扫描件：证据图不允许由模型生成证书内容")
        return Image.new("RGB", _PATENT, WHITE), rep.to_dict()

    canvas = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(canvas)

    head_h = 150
    d.rectangle([0, 0, W, head_h], fill=(24, 22, 20))
    title = str(copy.get("title") or "专利 / 检测证书 · 原件引用").strip()
    f, lines, ok = _fit_text(d, title, W - 220, int(head_h * 0.6), 42, 24)
    if not ok:
        rep.fail("标题过长")
    ty = (head_h - int(f.size * 1.25 * len(lines))) // 2
    for ln in lines:
        d.text((60, ty), ln, font=f, fill=(250, 248, 244))
        ty += int(f.size * 1.25)
    f_badge = L.font(22, bold=False)
    d.text((W - 60 - L.text_w(d, "未经修改", f_badge), head_h - 46), "未经修改",
           font=f_badge, fill=(190, 186, 180))

    # 扫描件：contain 放进框里，四周留白 + 细描边（不要 cover，裁掉证书边角就不算原件了）
    box = (60, head_h + 44, W - 60, int(H * 0.64))
    area = ImageOps.contain(ev.convert("RGB"), (box[2] - box[0], box[3] - box[1]), Image.LANCZOS)
    ax = (W - area.width) // 2
    ay = box[1] + (box[3] - box[1] - area.height) // 2
    d.rectangle([ax - 1, ay - 1, ax + area.width, ay + area.height], outline=LINE, width=2)
    canvas.paste(area, (ax, ay))
    rep.stats["evidence_size"] = list(area.size)

    # 引用信息：只印用户给的字段，缺了就留空并标出来，不猜
    rows = [
        ("专利 / 证书号", str(copy.get("patent_no") or copy.get("cert_no") or "").strip()),
        ("名称", str(copy.get("subtitle") or "").strip()),
        ("发证机构", str(copy.get("issuer") or "").strip()),
        ("有效期", str(copy.get("period") or "").strip()),
    ]
    y = box[3] + 40
    f_lab = L.font(26, bold=False)
    f_val = L.font(28)
    filled = 0
    for label, val in rows:
        if not val:
            continue
        filled += 1
        # 标签右对齐到 250：中文字数不同也能对齐，不用空格凑
        lw = L.text_w(d, label, f_lab)
        d.text((250 - lw, y), label, font=f_lab, fill=DIM)
        vf, vlines, okv = _fit_text(d, val, W - 60 - 300, 120, 28, 20)
        if not okv:
            rep.fail(f"字段过长：{label}")
        vy = y
        for ln in vlines[:2]:
            d.text((300, vy), ln, font=vf, fill=INK)
            vy += int(vf.size * 1.3)
        y = max(y + 44, vy + 6)
    if filled == 0:
        rep.fail("引用信息全空：至少要给证书号与发证机构，否则这张图只是一张扫描件")

    d.line([(60, H - 92), (W - 60, H - 92)], fill=LINE, width=1)
    note = str(copy.get("footnote") or "").strip() or \
        "本图为原件扫描件引用，未作任何修改；证书内容以原件为准。"
    fn, nl, okn = _fit_text(d, note, W - 120, 60, 22, 16, bold=False)
    if not okn:
        rep.fail("脚注过长")
    ny = H - 74
    for ln in nl[:2]:
        d.text((60, ny), ln, font=fn, fill=DIM)
        ny += int(fn.size * 1.3)
    rep.stats["fields_filled"] = filled
    return canvas, rep.to_dict()


PATENT = Template(
    id="patent",
    name="专利 / 检测证据图",
    desc="1080×1350：原件扫描件 + 证书号/机构/有效期引用。必须上传真实扫描件，拒绝生成证书。",
    export="original",
    canvas=_PATENT,
    reframes=True,
    compose=True,
    inputs=["evidence"],
    copy_fields=["title", "patent_no", "subtitle", "issuer", "period", "footnote"],
    apply=_patent,
)


# ─────────────────────────── 4. 表格对比海报 ───────────────────────────

_TABLE = (1080, 1350)


def _table(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """表格海报：表头 + 行数据，逐格量宽高。溢出就是 fail，不许悄悄截断。"""
    rep = LayoutReport()
    W, H = _TABLE
    margin = 60
    cols = copy.get("columns") or []
    rows = copy.get("rows") or []
    if isinstance(cols, str):
        cols = [c for c in cols.replace("｜", "|").split("|") if c.strip()]
    cols = [str(c).strip() for c in cols]
    norm_rows: list[list[str]] = []
    for r in rows:
        if isinstance(r, (list, tuple)):
            norm_rows.append([str(x).strip() for x in r])
        elif isinstance(r, dict):
            norm_rows.append([str(x).strip() for x in r.values()])
        elif isinstance(r, str):
            norm_rows.append([x.strip() for x in r.split("|")])
    if not cols or not norm_rows:
        rep.fail("表格缺表头或数据行（copy.columns / copy.rows）")
        return Image.new("RGB", _TABLE, WHITE), rep.to_dict()
    bad = [i for i, r in enumerate(norm_rows, 1) if len(r) != len(cols)]
    if bad:
        rep.fail(f"第 {'、'.join(map(str, bad))} 行列数与表头不一致（{len(cols)} 列）")
        return Image.new("RGB", _TABLE, WHITE), rep.to_dict()

    canvas = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(canvas)

    title = str(copy.get("title") or "").strip()
    y = margin
    if title:
        f, lines, ok = _fit_text(d, title, W - margin * 2, 110, 46, 28)
        if not ok:
            rep.fail("标题过长")
        for ln in lines[:2]:
            d.text((margin, y), ln, font=f, fill=INK)
            y += int(f.size * 1.28)
        y += 18
    subtitle = str(copy.get("subtitle") or "").strip()
    if subtitle:
        fs, sl, oks = _fit_text(d, subtitle, W - margin * 2, 70, 26, 18, bold=False)
        if not oks:
            rep.fail("副标题过长")
        for ln in sl[:2]:
            d.text((margin, y), ln, font=fs, fill=DIM)
            y += int(fs.size * 1.3)
        y += 10

    # 行很少时表格会缩在上半截，下面一大片空白像没做完。往下放一点，视觉上居中。
    if title or subtitle:
        y += int(min(140, max(0, (H - y) * 0.06)))

    table_w = W - margin * 2
    first_w = int(table_w * (0.30 if len(cols) > 2 else 0.5))
    rest = (table_w - first_w) // max(1, len(cols) - 1)
    widths = [first_w] + [rest] * (len(cols) - 1)
    widths[-1] += table_w - sum(widths)
    x0, top = margin, y

    # 先量高度：每格按 26px 折行，行高取该行最高的那一格
    f_cell = L.font(26)
    f_head = L.font(26)
    head_h = 84
    heights: list[int] = []
    wrapped: list[list[list[str]]] = []
    for r in norm_rows:
        cell_lines, need = [], 0
        for j, cell in enumerate(r):
            lines = L.wrap(d, cell, f_cell, widths[j] - 26) or [""]
            if len(lines) > 4:
                rep.fail(f"单元格文字过长（{len(lines)} 行 > 4）：{cell[:12]}…")
                lines = lines[:4]
            cell_lines.append(lines)
            need = max(need, len(lines) * int(26 * 1.35) + 30)
        wrapped.append(cell_lines)
        heights.append(max(66, need))

    total_h = head_h + sum(heights)
    avail = H - top - margin - 90
    # 行少的时候把行高撑开一点，否则整张海报下半截全是空白，像没做完。
    # 上限 46px/行：再高就变成"稀疏表格"，反而不像价格表。
    if total_h < avail * 0.80 and heights:
        extra = (avail * 0.80 - total_h) / len(heights)
        heights = [h + int(min(extra, 46)) for h in heights]
        total_h = head_h + sum(heights)
    if total_h > avail:
        rep.fail(f"表格超出画布（需 {total_h}px，可用 {avail}px）：删行或缩短文字")
    rep.stats.update({"rows": len(norm_rows), "cols": len(cols),
                      "table_h": total_h, "avail_h": avail})

    # 表头
    d.rectangle([x0, top, x0 + table_w, top + head_h], fill=(28, 26, 24))
    cx = x0
    for j, c in enumerate(cols):
        lines = L.wrap(d, c, f_head, widths[j] - 24)[:2]
        ty = top + (head_h - len(lines) * int(26 * 1.3)) // 2
        for ln in lines:
            tw = L.text_w(d, ln, f_head)
            d.text((cx + (widths[j] - tw) / 2, ty), ln, font=f_head, fill=(250, 248, 244))
            ty += int(26 * 1.3)
        cx += widths[j]

    # 数据行：斑马纹 + 网格
    ry = top + head_h
    for i, (r, hh) in enumerate(zip(norm_rows, heights)):
        if i % 2 == 1:
            d.rectangle([x0, ry, x0 + table_w, ry + hh], fill=(246, 245, 241))
        cx = x0
        for j, cell in enumerate(r):
            lines = wrapped[i][j]
            ty = ry + (hh - len(lines) * int(26 * 1.35)) // 2
            for ln in lines:
                if j == 0:
                    d.text((cx + 14, ty), ln, font=f_cell, fill=INK)
                else:
                    tw = L.text_w(d, ln, f_cell)
                    d.text((cx + (widths[j] - tw) / 2, ty), ln, font=f_cell, fill=INK)
                ty += int(26 * 1.35)
            cx += widths[j]
        d.line([(x0, ry), (x0 + table_w, ry)], fill=LINE, width=1)
        ry += hh
    d.rectangle([x0, top, x0 + table_w, ry], outline=(60, 58, 54), width=2)

    note = str(copy.get("footnote") or "").strip() or "数据来源：客户提供的对比资料，单位与口径见原始文件"
    fn, nlines, okn = _fit_text(d, note, W - margin * 2, 70, 22, 16, bold=False)
    if not okn:
        rep.fail("脚注过长")
    ny = min(H - margin - int(fn.size * 1.3) * len(nlines), ry + 26)
    for ln in nlines[:2]:
        d.text((margin, ny), ln, font=fn, fill=DIM)
        ny += int(fn.size * 1.3)
    return canvas, rep.to_dict()


TABLE = Template(
    id="table",
    name="表格对比海报",
    desc="表头 + 多行数据，斑马纹与网格，逐格量宽高，溢出即报错。数字必须有来源。",
    export="original",
    canvas=_TABLE,
    reframes=True,
    compose=True,
    copy_fields=["title", "subtitle", "columns", "rows", "footnote"],
    apply=_table,
)


# ─────────────────────────── 5. 线下大屏 / 灯箱 ───────────────────────────

def _homography(dst: np.ndarray, src: np.ndarray) -> tuple[float, ...]:
    """求 dst→src 的透视变换系数（8 个），供 PIL PERSPECTIVE 用。

    PIL 的 `Image.transform(..., Image.QUAD, data)` 里 data 是**源图上的**四边形，
    不是目标位置。一开始想拿它把矩形画面贴到场景的四角上，结果四个角是场景坐标，
    源图里根本没有那些点 → 采样到图外，整张变黑（实测一半黑块）。
    正确做法是求一个把目标四边形映回源矩形的单应矩阵，再逐像素反采样。
    """
    A, bvec = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        bvec.append(u)
        A.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        bvec.append(v)
    coef = np.linalg.solve(np.asarray(A, dtype=np.float64), np.asarray(bvec, dtype=np.float64))
    return tuple(float(c) for c in coef)


def _warp_quad(im: Image.Image, corners: list[list[int]],
               origin: tuple[int, int]) -> tuple[Image.Image, Image.Image, tuple[int, int]]:
    """把画面透视贴进四边形。返回 (透视后画面, 羽化掩码, 目标尺寸)。

    `origin` 是四边形包围盒左上角：掩码必须画在**包围盒局部坐标**里，
    否则四边形在场景里的偏移会把掩码顶出画布，只剩一个斜角。
    """
    tl, tr, br, bl = corners
    tw = int(max(math.dist(tl, tr), math.dist(bl, br)))
    th = int(max(math.dist(tl, bl), math.dist(tr, br)))
    tw, th = max(8, tw), max(8, th)
    ox, oy = origin
    dst = np.asarray([[p[0] - ox, p[1] - oy] for p in (tl, tr, br, bl)], dtype=np.float64)
    src = np.asarray([[0, 0], [im.width, 0], [im.width, im.height], [0, im.height]], dtype=np.float64)
    coef = _homography(dst, src)
    warped = im.convert("RGB").transform((tw, th), Image.PERSPECTIVE, coef, Image.BICUBIC)

    mask = Image.new("L", (tw, th), 0)
    md = ImageDraw.Draw(mask)
    md.polygon([tuple(map(float, p)) for p in dst], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(2.4))
    return warped, mask, (tw, th)


def _screen(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """贴屏：自动检测四角 → 置信度不足转人工 → 透视 + 亮度匹配 + 边缘检查。

    三道检查缺一不可（`knowledge/02-工艺路线.md`）：位置对但亮度不对，
    看起来就是"贴上去的一张纸"。所以这里除了透视，还要按场景亮度做一次
    乘法匹配，并量贴图内外侧的亮度差。
    """
    rep = LayoutReport()
    scene = ctx.get("scene")
    if scene is None:
        rep.fail("缺少场景照：贴屏必须有真实场景图")
        return im.convert("RGB"), rep.to_dict()
    scene = scene.convert("RGB")

    corners = ctx.get("corners")
    if corners:
        cal = scenemod.evaluate(corners, scene, method="manual")
    else:
        cal = scenemod.load(scene) or scenemod.detect(scene)
    rep.stats["calibration"] = cal.to_dict()

    # 自动检测的角要过置信度；人工拖过的角信人，但面积得讲道理——
    # 四角围住 95% 以上整张图，那不是屏幕，是把图贴回自己身上。
    if cal.needs_manual or (cal.method != "manual" and cal.confidence < scenemod.MIN_CONFIDENCE):
        rep.fail(f"场景四角定位置信度 {cal.confidence:.2f} < {scenemod.MIN_CONFIDENCE}："
                 f"请在界面上拖动四角确认后重新提交")
        return scene, rep.to_dict()
    if not 0.02 <= cal.area_ratio <= 0.92:
        rep.fail(f"四角围出的面积占整图 {cal.area_ratio * 100:.0f}%，不像一块屏幕：请重新校准")
        return scene, rep.to_dict()
    scenemod.save(scene, cal)

    xs = [p[0] for p in cal.corners]
    ys = [p[1] for p in cal.corners]
    x0, x1 = max(0, min(xs)), min(scene.width, max(xs))
    y0, y1 = max(0, min(ys)), min(scene.height, max(ys))
    warped, mask, (tw, th) = _warp_quad(im.convert("RGB"), cal.corners, (x0, y0))
    rep.stats["patch_size"] = [tw, th]

    # 取贴图区域在场景里的包围盒，做亮度匹配（乘法，不是加法：加法会把黑位抬灰）
    region = scene.crop((x0, y0, x1, y1)).convert("L").resize((tw, th), Image.LANCZOS)
    la = np.asarray(region, dtype=np.float32)
    wa = np.asarray(warped.convert("L"), dtype=np.float32)
    m = np.asarray(mask, dtype=np.float32) > 8
    if m.sum() > 64 and wa[m].mean() > 1:
        factor = float(np.clip(la[m].mean() / wa[m].mean(), 0.72, 1.28))
    else:
        factor = 1.0
    arr = np.clip(np.asarray(warped, dtype=np.float32) * factor, 0, 255).astype(np.uint8)
    warped = Image.fromarray(arr, "RGB")
    rep.stats["luma_factor"] = round(factor, 3)

    # 只贴四边形区域：patch 就是透视后的画面（尺寸 = 四边形包围盒），掩码同尺寸
    out = scene.copy()
    patch = Image.new("RGB", (tw, th), WHITE)
    patch.paste(warped, (0, 0))
    out.paste(patch, (x0, y0), mask)

    # 边缘检查一：四角之外**一个像素都不能变**。
    # 旧版这里查的是"环带有没有过曝"，结果画面本身偏白就必然误报（实测 19.8%）。
    # 真正要防的是"贴图溢出屏幕边框"，所以直接比合成前后的差异。
    mx = np.asarray(mask, dtype=np.uint8)
    # 四边形可能贴到画面边缘外，只比可见的那部分
    rx1, ry1 = min(scene.width, x0 + tw), min(scene.height, y0 + th)
    rw, rh = max(1, rx1 - x0), max(1, ry1 - y0)
    mx_vis = mx[:rh, :rw]
    outside = np.asarray(Image.fromarray(mx_vis).filter(ImageFilter.MaxFilter(3))) < 8
    reg_box = (x0, y0, rx1, ry1)
    reg_before = np.asarray(scene.crop(reg_box), dtype=np.int16)
    reg_after = np.asarray(out.crop(reg_box), dtype=np.int16)
    d_all = np.abs(reg_after - reg_before).max(axis=2)
    leak = float((d_all[outside] > 4).mean()) if outside.any() else 0.0
    rep.stats["leak_ratio"] = round(leak, 5)
    if leak > 0.002:
        rep.fail(f"贴图溢出屏幕边界（外部 {leak * 100:.1f}% 像素被改动）：检查四角顺序")

    # 边缘检查二：贴图覆盖范围必须与四角围出的面积一致。
    # 为什么不是查"羽化过渡带"：四边形通常正好填满自己的包围盒，画布上根本没有
    # 外侧空间，过渡带只存在于画布边缘之外，量出来恒为 0.2px（实测误报）。
    # 真正会出错的是"贴上去的比屏幕大/小一圈"，那用面积比一比就出来了。
    def _shoelace(pts):
        s2 = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            s2 += x1 * y2 - x2 * y1
        return abs(s2) / 2.0

    quad_area = max(1.0, _shoelace([(p[0] - x0, p[1] - y0) for p in cal.corners]))
    cover = float((mx > 127).sum()) / quad_area
    rep.stats["cover_ratio"] = round(cover, 3)
    if not 0.97 <= cover <= 1.03:
        rep.fail(f"贴图覆盖范围与四角围出的面积差 {abs(cover - 1) * 100:.0f}%："
                 f"贴上去会露出边框或溢出屏幕")

    # 边缘检查三：画面与屏幕的亮度匹配余量。
    # 这里不能拿"屏幕内 vs 屏幕外（墙/边框）"比——那块亮差是屏幕本身的对比度，
    # 跟贴得好不好没关系（实测直接报 114，全是误报）。
    # 要问的是：把画面压到屏幕的亮度水平，需要改多少？
    # 需要改 1.6 倍以上，说明画面和场景的光根本不是一个环境，贴上去一定假。
    need = (la[m].mean() / wa[m].mean()) if (m.any() and wa[m].mean() > 1) else 1.0
    rep.stats["luma_need"] = round(float(need), 2)
    if need > 1.6 or need < 0.62:
        rep.fail(f"画面亮度是屏幕的 {need:.2f} 倍，差距过大：贴上去会像另一张图，"
                 f"先调色或换画面")

    return out, rep.to_dict()


SCREEN = Template(
    id="screen",
    name="线下大屏 / 灯箱",
    desc="把画面透视贴进真实场景照：自动找四角，置信度不足必须人工校准；含亮度匹配与边缘检查。",
    export="original",
    canvas=None,
    reframes=True,
    compose=True,
    inputs=["scene", "corners"],
    copy_fields=[],
    apply=_screen,
)

EXTRA: list[Template] = [PAIR, PRICE, PATENT, TABLE, SCREEN]
