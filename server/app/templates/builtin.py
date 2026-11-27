"""内置模板：场景图 / 白底主图 / 卖点图。

每个模板只做三件事：**改提示词约束**、**定画布与导出规格**、**确定性后处理**。
不做 AI 重绘、不生成文字、不编数据（`knowledge/02-工艺路线.md` 三条总原则）。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter
import numpy as np

from . import layout as L
from .base import LayoutReport, Template

# ─────────────────────────── 1. 场景图（默认） ───────────────────────────

SCENE = Template(
    id="scene",
    name="场景图",
    desc="产品放进使用场景，保留主体、换背景与光线。最通用，也是目前的默认行为。",
    prompt_extra=[
        "产品主体保持原样：形状、比例、Logo、材质、文字位置都不要改。",
        "场景与光线自然，产品与环境的接触处要有合理的阴影与反光。",
    ],
    negative_extra=["改变产品形状或比例", "在画面上生成任何文字、水印、角标"],
    export="detail_1080x1440",
)


# ─────────────────────────── 2. 白底主图 ───────────────────────────

_WHITE_MARGIN = 0.055


def _clean_mask(hard: Image.Image, min_area_frac: float = 0.012) -> tuple[Image.Image, int, float]:
    """去掉掩码里的小碎块（生成图常带白色噪点/白斑）。

    在长边 256 的降采样图上做连通域，只保留面积 ≥ 总前景 1.2% 的块，
    再放大回原尺寸。多产品（比如 12 个罐子）也能保留多块，不像"只留最大块"
    那样把其余产品一起删掉。

    返回 (清理后的掩码, 被删掉的碎块数, 被删面积占比)。
    碎块数是个很有用的信号：背景上的能删，**主体上的删不掉**，只能重生成。
    """
    small = hard.copy()
    small.thumbnail((256, 256), Image.NEAREST)
    a = np.asarray(small) > 127
    H, W = a.shape
    seen = np.zeros_like(a, dtype=bool)
    out = np.zeros_like(a, dtype=bool)
    comps: list[list[tuple[int, int]]] = []
    for y in range(H):
        for x in range(W):
            if not a[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            pix: list[tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                pix.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < H and 0 <= nx < W and a[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            comps.append(pix)

    total = sum(len(c) for c in comps) or 1
    kept, dropped, dropped_px = 0, 0, 0
    for c in comps:
        if len(c) >= min_area_frac * total:
            kept += 1
            for cy, cx in c:
                out[cy, cx] = True
        else:
            dropped += 1
            dropped_px += len(c)
    if kept == 0:  # 兜底：全都太小就保留最大一块
        if comps:
            for cy, cx in max(comps, key=len):
                out[cy, cx] = True
        kept = 1
    img = Image.fromarray((out * 255).astype(np.uint8), "L").resize(hard.size, Image.NEAREST)
    return img, dropped, dropped_px / max(1, H * W)


def _foreground_mask(rgb: Image.Image, med: tuple[int, int, int]) -> tuple[Image.Image, str]:
    """取前景掩码：有 cv2 就用 grabCut，没有就退回颜色键控。

    为什么优先 grabCut：真实生成图的底几乎都是**渐变**（22 个像素级的亮度差），
    用单一颜色当背景会把渐变的两端判成主体，抠出来一块灰斑。grabCut 靠区域统计，
    对渐变稳得多。

    为什么还要留着颜色键控：本仓库承诺"装不上依赖也能跑"。cv2 缺失时行为退化，
    但不至于整条产线挂掉。
    """
    try:
        import cv2  # noqa: PLC0415 局部导入：没装也不影响其它模板
    except ImportError:
        cv2 = None  # type: ignore[assignment]

    if cv2 is not None:
        # 在缩略图上跑再放大：2048² 直接 grabCut 要 30 多秒，缩到 640 只差在边缘 1–2px，
        # 后面还有 2px 收缩与羽化，肉眼看不出差别。
        h0, w0 = rgb.height, rgb.width
        small = rgb.copy()
        small.thumbnail((640, 640), Image.LANCZOS)
        img = np.asarray(small)[:, :, ::-1].copy()  # RGB → BGR
        h, w = img.shape[:2]
        inset = int(min(h, w) * 0.05)
        rect = (inset, inset, max(4, w - 2 * inset), max(4, h - 2 * inset))
        gc = np.zeros((h, w), np.uint8)
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(img, gc, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
            m = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
            if 0.02 < float((m > 0).mean()) < 0.95:
                out = Image.fromarray(m, "L").resize((w0, h0), Image.LANCZOS)
                return out.point(lambda v: 255 if v > 127 else 0), "grabcut"
        except Exception:  # noqa: BLE001 抠图失败就退到颜色键控
            pass

    d = np.abs(np.asarray(rgb, dtype=np.float32) - np.array(med, dtype=np.float32)).max(axis=2)
    return Image.fromarray(((d > 18).astype(np.uint8) * 255), "L"), "colorkey"


def _white_bg(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """白底主图：把「均匀背景」键控成纯白，主体居中，四周留白。

    为什么不是「把背景涂白」这么简单：
      - 商家白底图和模型输出都可能是**中灰/浅米**底，不是 255。只把 >242 的像素刷白，
        结果是"一块白、一块灰"，比不处理更难看。
      - 有纹理的场景底不能抠：标签上的白色高光会被当背景抠掉打洞（踩坑库 A3）。

    所以先量背景的**均匀度**再决定：
      - 四边亮度波动 <10 → 视为均匀底，用颜色相似度做软掩码，背景推向纯白
      - 波动大 → 拒绝，交给重生成或人工（宁可不动，也不做坏）

    再叠一道自检：键控后保留的主体面积必须在 5%–90%，否则说明主体被一起抠掉了。
    """
    rep = LayoutReport()
    rgb = im.convert("RGB")
    w, h = rgb.size
    probe = max(8, int(min(w, h) * 0.06))
    strips = [
        rgb.crop((0, 0, w, probe)), rgb.crop((0, h - probe, w, h)),
        rgb.crop((0, 0, probe, h)), rgb.crop((w - probe, 0, w, h)),
    ]

    px = []
    for s in strips:
        px.extend(list(s.convert("RGB").resize((16, 16)).getdata()))
    med = tuple(int(sorted(p[i] for p in px)[len(px) // 2]) for i in range(3))
    lumas = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in px]
    mean_l = sum(lumas) / len(lumas)
    std_l = (sum((v - mean_l) ** 2 for v in lumas) / len(lumas)) ** 0.5

    # 纹理要看**高频**，不能看方差：柔和渐变（摄影棚底）方差也不小，
    # 但拉大看是平滑的，完全可以抠；墙纸/木纹那种才是真纹理。
    hf = 0.0
    for s in strips:
        small = np.asarray(s.convert("L").resize((64, 64)), dtype=np.float32)
        hf += float(np.abs(np.diff(small, axis=0)).mean() + np.abs(np.diff(small, axis=1)).mean())
    hf /= len(strips)

    rep.stats.update({"border_rgb": list(med), "border_luma": round(mean_l, 1),
                      "border_std": round(std_l, 1), "border_hf": round(hf, 2)})

    if hf > 4.0:
        rep.fail(f"背景有明显纹理（高频 {hf:.1f} > 4.0），抠不干净，建议重生成或人工处理")
        return rgb, rep.to_dict()
    if mean_l < 40:
        rep.fail(f"背景过暗（{mean_l:.0f}），不像摄影棚底，建议重生成")
        return rgb, rep.to_dict()

    side = 1080
    inner = int(side * (1 - _WHITE_MARGIN * 2))

    # 分支一：本来就是白底 —— 只裁切重排，**绝不键控**。
    # 白底产品图上的镜面高光本身就是纯白，一键控就被当背景抠掉，边缘啃出缺口
    # （踩坑库 A3 的原坑）。白底只需要"找到外框 + 重新算留白"，不碰像素。
    if mean_l >= 246:
        a = np.asarray(rgb, dtype=np.float32)
        # 阈值 240 而不是 244：白底图底下常有一层很淡的投影（实测 238），
        # 按 244 判会把投影算进主体，裁出来的框比瓶身宽 13%，
        # 后面按框缩放就把主体横向拉开了——保真度量随即报"结构只对上 0.50"。
        nonwhite = a.min(axis=2) < 240
        if nonwhite.mean() < 0.005:      # 产品本身很浅，退回宽松阈值
            nonwhite = a.min(axis=2) < 246
        ys, xs = np.nonzero(nonwhite)
        if xs.size < 16:
            rep.fail("白底但没找到产品（非白像素过少）")
            return rgb, rep.to_dict()
        x0, x1 = np.percentile(xs, [0.5, 99.5])
        y0, y1 = np.percentile(ys, [0.5, 99.5])
        pad = 0.02
        w0, h0 = x1 - x0, y1 - y0
        box = (max(0, int(x0 - w0 * pad)), max(0, int(y0 - h0 * pad)),
               min(w, int(x1 + w0 * pad)), min(h, int(y1 + h0 * pad)))
        sub = rgb.crop(box)
        scale = min(inner / sub.width, inner / sub.height)
        nw, nh = max(1, int(sub.width * scale)), max(1, int(sub.height * scale))
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(sub.resize((nw, nh), Image.LANCZOS), ((side - nw) // 2, (side - nh) // 2))
        rep.stats["branch"] = "白底重排（未键控）"
        rep.stats["subject_ratio"] = round(max(nw, nh) / side, 3)
        return canvas, rep.to_dict()

    # 分支二：非纯白底（常见：模型给的中灰/浅米底、柔和渐变底）—— 抠出主体再贴白
    a = np.asarray(rgb, dtype=np.float32)
    hard, how = _foreground_mask(rgb, med)
    rep.stats["mask_method"] = how
    hard, n_drop, frac_drop = _clean_mask(hard)   # 去背景上的白斑/噪点
    rep.stats["noise_blobs"] = n_drop
    hard = hard.filter(ImageFilter.MinFilter(5))   # 收 2px，把边界的混色去掉
    cov = float(np.asarray(hard).mean() / 255.0)
    rep.stats["subject_cover"] = round(cov, 3)
    rep.stats["branch"] = "非白底抠图"
    if not 0.04 <= cov <= 0.90:
        rep.fail(f"抠图后主体占比异常（{cov * 100:.0f}%），可能把主体也抠掉了，未做处理")
        return rgb, rep.to_dict()

    if frac_drop > 0.015:
        # 背景上的碎块能删，压在主体上的删不掉——只能让人重生成
        rep.fail(f"生成图有 {n_drop} 处噪点碎块（占 {frac_drop * 100:.1f}%）："
                 f"背景上的已清理，主体上的无法修复，建议重新生成")

    # 羽化 2.6px：掩码是在 640 缩略图上抠的，放大到 2048 会变成 3px 台阶，
    # 羽化能把它化开（1.2px 不够，阴影边缘仍看得见锯齿）
    alpha = hard.filter(ImageFilter.GaussianBlur(2.6))
    white = Image.new("RGB", (w, h), (255, 255, 255))
    keyed = Image.composite(rgb, white, alpha)

    # 按主体外接框居中，四周留白 5.5%
    bb = alpha.point(lambda v: 255 if v > 128 else 0).getbbox()
    if not bb:
        rep.fail("未找到主体范围，未做重排")
        return rgb, rep.to_dict()
    subject = keyed.crop(bb)
    scale = min(inner / subject.width, inner / subject.height)
    nw, nh = max(1, int(subject.width * scale)), max(1, int(subject.height * scale))
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(subject.resize((nw, nh), Image.LANCZOS), ((side - nw) // 2, (side - nh) // 2))
    rep.stats["subject_ratio"] = round(max(nw, nh) / side, 3)
    return canvas, rep.to_dict()


WHITE_BG = Template(
    id="white_bg",
    name="白底主图",
    desc="纯白无缝背景、产品居中、无投影，输出 1080×1080，四周留白约 5.5%。",
    version=1,
    prompt_extra=[
        "背景必须是纯白色 #FFFFFF，完全均匀：没有渐变、没有纹理、没有噪点或白斑。",
        "参考图里的背景不要沿用，一律换成纯白摄影棚底。",
        "产品居中、占画面高度约 75%，四周留白均匀。",
        "悬浮无投影（no shadow on the floor），产品边缘干净、不发灰。",
        "产品主体保持原样：形状、比例、Logo、材质不要改。",
    ],
    negative_extra=["灰色背景", "背景渐变", "背景噪点或白斑", "地面投影", "道具", "在画面上生成文字或水印"],
    export="square_1080",
    canvas=(1080, 1080),
    reframes=True,
    apply=_white_bg,
)


# ─────────────────────────── 3. 卖点图（KV） ───────────────────────────

_KV = (1080, 1350)
_SAFE = 72


def _kv(im: Image.Image, copy: dict, ctx: dict) -> tuple[Image.Image, dict]:
    """卖点图：产品在下，上方压一块「模糊 + 渐隐」底，再排标题 / 副标题 / 卖点 / 角标。

    两遍走：
      ① 只排版不绘制（量出文字块到底占多高）
      ② 按实际高度做模糊+渐变，再落字

    为什么要两遍：第一版按固定 52% 高度做渐变，结果三条卖点落在渐变已经淡掉的
    明亮区域，白字压浅色几乎看不清。**可读性得由几何保证，不能靠调色碰运气。**
    """
    rep = LayoutReport()
    W, H = _KV
    src = im.convert("RGB")

    scale = max(W / src.width, H / src.height)
    nw, nh = max(1, round(src.width * scale)), max(1, round(src.height * scale))
    canvas = src.resize((nw, nh), Image.LANCZOS).crop(
        ((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H)
    )

    base = L.mean_color(canvas, (0, 0, W, int(H * 0.42)))
    probe = ImageDraw.Draw(canvas)
    max_w = W - _SAFE * 2

    title = str(copy.get("title") or "").strip()
    subtitle = str(copy.get("subtitle") or "").strip()
    points = copy.get("points") or []
    if isinstance(points, str):
        points = [p for p in points.replace("；", "\n").replace(";", "\n").split("\n") if p.strip()]
    points = [str(p).strip() for p in points if str(p).strip()][:3]

    if not title:
        rep.fail("卖点图缺少标题（copy.title）")
        return canvas, rep.to_dict()

    # ① 排一遍：算出每一行的字体、位置、是否装得下
    plan: list[tuple[str, object, int, bool]] = []  # (文字, 字体, y, 是否项目符号)
    y = int(H * 0.075)

    f, lines, ok = L.fit_block(probe, title, max_w, int(H * 0.19), 84, 46, 1.22, bold=True)
    if not ok:
        rep.fail("标题过长，最小字号仍装不下")
    for ln in lines:
        plan.append((ln, f, y, False))
        y += int(f.size * 1.22)

    if subtitle:
        y += 10
        f2, lines2, ok2 = L.fit_block(probe, subtitle, max_w, int(H * 0.09), 42, 28, 1.30, bold=False)
        if not ok2:
            rep.fail("副标题过长")
        for ln in lines2:
            plan.append((ln, f2, y, False))
            y += int(f2.size * 1.30)

    if points:
        y += int(H * 0.022)
        for p in points:
            f3 = L.font(34, False)
            if L.text_w(probe, p, f3) > max_w - 34:
                f3, wl, ok3 = L.fit_block(probe, p, max_w - 34, int(H * 0.08), 34, 24, 1.28, bold=False)
                if not ok3:
                    rep.fail(f"卖点过长：{p[:12]}…")
                for ln in wl:
                    plan.append((ln, f3, y, True))
                    y += int(f3.size * 1.28)
            else:
                plan.append((p, f3, y, True))
                y += int(f3.size * 1.28)
            y += 8

    text_bottom = y
    if text_bottom > int(H * 0.60):
        rep.fail(f"文案超出文字区（到 {text_bottom}px，上限 {int(H * 0.60)}px）")

    # ② 按实际文字高度做底：整段文字区保持"接近不透明的暗底"，到底部再快速淡出。
    #    第一版做成线性渐变，结果淡出刚好发生在卖点那几行，白字压在浅色磨砂上——
    #    标题能读、卖点看不清。可读性必须由几何保证：文字在哪，暗底就必须盖到哪。
    fade_end = min(H, text_bottom + int(H * 0.12))
    ramp = np.ones(H, dtype=np.float32)
    if fade_end > text_bottom:
        ramp[text_bottom:fade_end] = np.linspace(1.0, 0.0, fade_end - text_bottom, dtype=np.float32)
    ramp[fade_end:] = 0.0
    mask = Image.fromarray((np.repeat(ramp[:, None], W, axis=1) * 255).astype(np.uint8), "L")

    canvas = Image.composite(canvas.filter(ImageFilter.GaussianBlur(16)), canvas, mask)
    dark = tuple(int(c * 0.30) for c in base)
    canvas = Image.composite(Image.new("RGB", (W, H), dark), canvas,
                             mask.point(lambda v: int(v * 0.82)))

    # ③ 落字：底一定够暗，所以固定白字 + 2px 投影（白字压在杂乱画面上才不发飘）
    fg = (252, 250, 246)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for text, fnt, ty, is_point in plan:
        x = _SAFE + 34 if is_point else _SAFE
        sd.text((x + 1, ty + 2), text, font=fnt, fill=(0, 0, 0, 86))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB")

    d = ImageDraw.Draw(canvas)
    for text, fnt, ty, is_point in plan:
        if is_point:
            d.line([(_SAFE + 4, ty + int(fnt.size * 0.52)), (_SAFE + 22, ty + int(fnt.size * 0.52))],
                   fill=fg, width=3)
            d.text((_SAFE + 34, ty), text, font=fnt, fill=fg)
        else:
            d.text((_SAFE, ty), text, font=fnt, fill=fg)

    tag = str(copy.get("tag") or "").strip()
    if tag:
        tw = L.text_w(ImageDraw.Draw(canvas), tag, L.font(26))
        canvas = L.badge(canvas, (W - _SAFE - int(tw + 48), H - _SAFE - 62),
                         tag, 26, (255, 255, 255), (26, 24, 22))
        rep.stats["tag"] = tag

    rep.stats["text_bottom"] = text_bottom
    rep.stats["lines"] = len(plan)
    return canvas, rep.to_dict()


KV = Template(
    id="kv",
    name="卖点图",
    desc="1080×1350，产品占下方，上方渐隐底排标题/副标题/3 条卖点，右下角标。文案由代码绘制。",
    version=1,
    prompt_extra=[
        "构图上主体偏下：产品整体位于画面下方 55% 区域，上方留出干净、低对比的空间给文案。",
        "上方空间不要出现任何文字、标签、醒目的高光或复杂纹理。",
        "产品主体保持原样：形状、比例、Logo、材质不要改。",
    ],
    negative_extra=["画面上方出现文字", "水印", "角标", "改变产品形状或比例"],
    export="original",
    canvas=_KV,
    reframes=True,
    copy_fields=["title", "subtitle", "points", "tag"],
    apply=_kv,
)


ALL: list[Template] = [SCENE, WHITE_BG, KV]
_BY_ID = {t.id: t for t in ALL}


def get(tid: str | None) -> Template:
    return _BY_ID.get(str(tid or "scene").strip()) or SCENE
