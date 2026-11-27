"""主体保真评估：只看产品，不看背景。

## 为什么重写

旧版 `steps.fidelity` = 整图缩到 96×96 后的灰度归一化相关。它的隐含假设是
「成图和素材构图一致」，但本项目干的活恰恰是换背景、挪位置、改比例——
于是主体一根线都没动，只要背景变了，相关系数就塌到 0.5 附近。

实测（2026-11-26，真实 Seedream 4 张候选，第三方品牌的面霜罐）：
产品 logo 完整可读、螺纹清晰，旧分 0.472–0.553，四张全被拦。
反过来，把主体纵向压扁 17%（模拟"减重"）仍拿 0.997 —— 它只比背景，不比产品。

## 这一版只管三件确定的事

设计过程中试过很多"聪明"的做法（各向异性模板精修、二阶矩、掩码相关），
共同结局是：好图负样本的分数一起抬，阈值失去意义。教训写在下面：

- **不用相关系数判断"是不是同一款产品"**。它是语义问题，相关系数答不了。
  真要做，用视觉模型（`judge.py`，需在方舟控制台开通多模态模型）。
- 相关系数还有两个固有偏差：小模板、平滑区域天然更容易拿高分。

所以这里只回答三个有资格回答、且都被 `scripts/bench_fidelity.py` 验证过的问题：

| 信号 | 问题 | 做法 | 能到多准 |
|---|---|---|---|
| `structure` | 产品还在画面里吗？ | 主体模板在成图里找最佳匹配（掩码加权 NCC） | 0.9+ 可信；0.4–0.72 只能"存疑" |
| `spread`    | 比例明显歪了吗？ | 匹配框内量主体铺展的宽高比，与素材比 | 只抓 >25% 的明显变形 |
| `sharp`     | 产品被糊了吗？ | 匹配框内的高频能量 / 素材主体 | 好图 0.9–1.7，糊图 0.5 |

`spread` 为什么只能抓明显变形：产线每张图都会过 `tone_match` 调色，而调色会让
"离背景色多远"这个判据整体漂移，实测同一张图能漂出 10–13%。所以 10–20% 区间
一律不下结论——宁可给 B 让人看一眼，也不假装量准了。

## 两种模式，因为"素材"本身就有两种

素材库里既有白底商品图，也有「产品工厂素材」那种一画面几十个罐子的流水线照片。
后者不存在"一个主体框"，硬找框去比对，前提就错了。

| 模式 | 何时用 | 能测什么 | 档位上限 |
|---|---|---|---|
| `object` | 白底图、单产品场景图 | 上面三项 | A |
| `patch`  | 工厂图、多实例复杂场景 | 只能测"产品特征在不在" | B |

`patch` 模式刻意不给 A：测不到比例就不该替人背书。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 尺度搜索网格（相对主体尺寸的缩放）
_ISO_SCALES = (0.20, 0.26, 0.32, 0.38, 0.45, 0.52, 0.60, 0.70, 0.82, 0.95, 1.00, 1.15, 1.30)
# patch 模式：成图里产品可能比素材里大也可能小，跨度要够宽
_PATCH_SCALES = (0.40, 0.50, 0.65, 0.80, 1.00, 1.25, 1.50, 1.90, 2.40)

# 分档阈值（全部由 bench_fidelity.py 的实测分布推出，不是拍脑袋）
# structure: 1.00=同一张、0.85=缩到28%、0.63=缩到45%、0.27=产品被抹
TIER_C_STRUCTURE = 0.40
TIER_A_STRUCTURE = 0.72
# spread: 匹配框内主体的宽高比相对偏差。
# 这条只抓"明显变形"，不抓细微比例——实测产线每张图都会过 tone_match，
# 调色本身就能让掩码几何漂 10–13%（等于原图的 mock 产物被误报"拉长 14%"）。
# 所以 10–20% 区间一律不下结论，交结构分给 B、交人看。
# 实测：同一张图 0–2%、裁掉一半 45%、旋转 90° 197%。
TIER_B_SPREAD = 0.25
TIER_C_SPREAD = 0.32
# A 档对比例更严：超过 15% 就不给"直接用"，即便结构分很高
TIER_A_SPREAD = 0.15
# sharp: 主体区域清晰度相对素材的比值。好图实测 0.90–1.68，模糊 6px 只有 0.50
TIER_SHARP = 0.65
PATCH_B = {"structure": 0.45}


# ─────────────────────────── 素材读入与主体定位 ───────────────────────────


def as_rgb(im: Image.Image) -> Image.Image:
    """透明底要先垫白，否则 RGBA→RGB 会把背景压成黑，主体提取全错。"""
    if im.mode in ("P", "LA", "RGBA"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")
    return im.convert("RGB")


def _canvas(im: Image.Image, long_side: int) -> Image.Image:
    out = im.copy()
    out.thumbnail((long_side, long_side), Image.LANCZOS)
    return out


def _luma(a: np.ndarray) -> np.ndarray:
    return a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _edge(l: np.ndarray) -> np.ndarray:
    """梯度幅值。对色调/曝光变化不敏感，是搜主体最稳的通道。"""
    gx = np.zeros_like(l)
    gy = np.zeros_like(l)
    gx[:, 1:-1] = l[:, 2:] - l[:, :-2]
    gy[1:-1, :] = l[2:, :] - l[:-2, :]
    return np.hypot(gx, gy)


def _integral(a: np.ndarray) -> np.ndarray:
    """必须 float64：float32 累加到 1e8 量级后平方和会抵消，var 失真 → NCC 爆表。"""
    return np.pad(a.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def _win(ii: np.ndarray, h: int, w: int) -> np.ndarray:
    return ii[h:, w:] - ii[:-h, w:] + ii[:-h, :-w] - ii[h:, :-w]


def _ncc(plane: np.ndarray, tmpl: np.ndarray, weight: np.ndarray | None = None) -> np.ndarray:
    """掩码加权的归一化互相关图（线性，不绕回），取值 [-1, 1]。

    `weight` 是模板坐标系下的主体掩码。给了就只在这些像素上比——模板把背景
    清零、分母却照样把成图里的阴影台面算进方差，这正是"真实吻合只拿 0.49"
    的原因。别用翻转模板做卷积：结果会整体偏移 (h-1, w-1)。
    """
    H, W = plane.shape
    h, w = tmpl.shape
    if h > H or w > W or h < 4 or w < 4:
        return np.zeros((1, 1), dtype=np.float32)

    t = tmpl.astype(np.float32)
    wt = np.ones_like(t) if weight is None else np.clip(weight.astype(np.float32), 0.0, 1.0)
    sw = float(wt.sum())
    if sw < 1e-3:
        return np.zeros((1, 1), dtype=np.float32)

    t = t - float((t * wt).sum() / sw)
    var_t = float((wt * t * t).sum())
    if var_t < 1e-6:
        return np.zeros((1, 1), dtype=np.float32)

    shape = (H + h - 1, W + w - 1)
    f_plane = np.fft.rfft2(plane, shape)
    f_wt = np.fft.rfft2(wt, shape)
    sl = (slice(0, H - h + 1), slice(0, W - w + 1))

    num = np.fft.irfft2(f_plane * np.conj(np.fft.rfft2(wt * t, shape)), shape)[sl]
    s1 = np.fft.irfft2(f_plane * np.conj(f_wt), shape)[sl]
    s2 = np.fft.irfft2(np.fft.rfft2(plane * plane, shape) * np.conj(f_wt), shape)[sl]

    var_p = s2 - s1 * s1 / sw
    ncc = num / np.sqrt(np.maximum(var_p, 1e-6) * var_t)
    # 平坦区域方差趋零，除出来数值上就是 1.0 —— 搜索会被吸到纯色背景上，
    # 然后报"结构完全吻合"（实测 mock 产物全部 structure=1.0 但匹配框是一块空白）。
    # 方差不足的位置一律判无效。
    ncc = np.where(var_p > 1.0, ncc, -1.0)
    return np.clip(ncc, -1.0, 1.0).astype(np.float32)


def _box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """大核均值：把"成片的高频区域"浮出来，孤立细边（水印、线条）会被摊平。"""
    a = a.astype(np.float32)
    H, W = a.shape
    k = max(3, min(k, H, W))
    if k % 2 == 0:
        k -= 1
    pad = k // 2
    blur = _win(_integral(a), k, k) / float(k * k)
    return np.pad(blur, pad, mode="edge")


def _resize(a: np.ndarray, w: int, h: int) -> np.ndarray:
    w, h = max(4, int(w)), max(4, int(h))
    return np.asarray(
        Image.fromarray(a.astype(np.float32), mode="F").resize((w, h), Image.BILINEAR),
        dtype=np.float32,
    )


def _search(plane: np.ndarray, tmpl: np.ndarray,
            weight: np.ndarray | None = None) -> tuple[float, tuple[int, int]]:
    m = _ncc(plane, tmpl, weight)
    idx = int(np.argmax(m))
    y, x = np.unravel_index(idx, m.shape)
    return float(m[y, x]), (int(x), int(y))


def _pct_box(xs: np.ndarray, ys: np.ndarray, W: int, H: int, pad: float) -> tuple[int, int, int, int]:
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    return (
        int(max(0, x0 - px)), int(max(0, y0 - py)),
        int(min(W, x1 + px)) - int(max(0, x0 - px)),
        int(min(H, y1 + py)) - int(max(0, y0 - py)),
    )


def _subject_mask(l: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, float, bool]:
    """在给定框内估前景掩码，用于把背景从模板里剔掉。

    框是估的，边角必然夹带背景。背景进了模板，NCC 就变成「主体+背景 vs
    主体+背景」——换背景的项目里这等于自废武功。

    返回 (软掩码, 掩码占比, 是否可信)。掩码糊满某条边 = 框把主体切了。
    """
    x, y, w, h = box
    crop = l[y : y + h, x : x + w]
    if crop.size < 64:
        return np.ones_like(crop), 1.0, False

    border = np.concatenate([crop[0, :], crop[-1, :], crop[:, 0], crop[:, -1]])
    bgv = float(np.median(border))
    d = np.abs(crop - bgv)
    hard = d > max(12.0, float(np.percentile(d, 88)) * 0.38)

    frac = float(hard.mean())
    touch = sum([
        hard[0].mean() > 0.55, hard[-1].mean() > 0.55,
        hard[:, 0].mean() > 0.55, hard[:, -1].mean() > 0.55,
    ])
    soft = np.clip(_box_blur(hard.astype(np.float32), 7), 0.0, 1.0)
    ok = 0.08 <= frac <= 0.88 and touch == 0
    return soft, frac, bool(ok)


def analyze_source(src: Image.Image) -> dict:
    """判素材属于哪种模式。

    object：白底，或有明确的单块高细节区域（占比 12%–72% 且填充率够）
    patch ：都不满足 —— 工厂图/复杂场景，只能取特征块
    """
    a = np.asarray(src, dtype=np.float32)
    H, W = a.shape[:2]
    p = max(4, int(min(H, W) * 0.05))
    corners = np.concatenate([
        a[:p, :p].reshape(-1, 3), a[:p, -p:].reshape(-1, 3),
        a[-p:, :p].reshape(-1, 3), a[-p:, -p:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    near_white = float(np.mean(a.min(axis=2) > 245))

    if near_white > 0.45:
        d = np.abs(a - bg).max(axis=2)
        m = d > max(16.0, float(np.percentile(d, 96)) * 0.22)
        if m.sum() < 0.004 * H * W:
            return {"mode": "patch", "box": None, "reliable": False,
                    "near_white": near_white, "reason": "白底但未找到主体"}
        ys, xs = np.nonzero(m)
        box = _pct_box(xs, ys, W, H, 0.04)
        if (box[2] * box[3]) / (H * W) < 0.03:
            return {"mode": "patch", "box": None, "reliable": False,
                    "near_white": near_white, "reason": "白底主体过小"}
        _, frac, ok = _subject_mask(_luma(a), box)
        return {"mode": "object", "box": box, "reliable": ok, "mask_frac": frac,
                "near_white": near_white,
                "reason": "" if ok else f"白底主体掩码异常（占比 {frac * 100:.0f}%）"}

    e = _edge(_luma(a))
    dens = _box_blur(e, max(9, min(H, W) // 8))
    m = dens > max(float(np.percentile(dens, 99)) * 0.55, 1e-3)
    if m.sum() < 0.002 * H * W:
        return {"mode": "patch", "box": None, "reliable": False,
                "near_white": near_white, "reason": "场景素材，无单一细节主体"}

    ys, xs = np.nonzero(m)
    box = _pct_box(xs, ys, W, H, 0.03)
    ratio = (box[2] * box[3]) / float(H * W)
    fill = float(m[box[1] : box[1] + box[3], box[0] : box[0] + box[2]].mean())

    if 0.12 <= ratio <= 0.72 and fill > 0.30:
        _, frac, ok = _subject_mask(_luma(a), box)
        return {"mode": "object", "box": box, "reliable": ok, "mask_frac": frac,
                "near_white": near_white,
                "reason": "场景素材，主体范围为估计值" if ok else "场景素材，主体掩码不够干净"}
    return {"mode": "patch", "box": None, "reliable": False, "near_white": near_white,
            "reason": f"多实例场景（细节区占比 {ratio * 100:.0f}%、填充 {fill * 100:.0f}%），改按特征块比对"}


# ─────────────────────────── 特征块（patch 模式） ───────────────────────────


def _key_patches(edge: np.ndarray, k: int = 10) -> list[np.ndarray]:
    """从素材里挑 k 个"最像产品"的块：细节密、彼此不重叠。

    取 10 块而不是 3–6：工厂图里细节最密的地方往往是机器和工服，罐子排在后面。
    实测 6 块时最高分只有 0.33，放到 10 块才捞到罐子花纹（0.55）。
    """
    H, W = edge.shape
    side = max(24, min(H, W) // 3)
    dens = _box_blur(edge, max(9, min(H, W) // 10))
    work = dens.copy()
    out: list[np.ndarray] = []
    for _ in range(k):
        idx = int(np.argmax(work))
        y, x = np.unravel_index(idx, work.shape)
        peak = float(work[y, x])
        if peak <= 1e-3:
            break
        y0 = int(np.clip(y - side // 2, 0, H - side))
        x0 = int(np.clip(x - side // 2, 0, W - side))
        patch = edge[y0 : y0 + side, x0 : x0 + side]
        if patch.std() > 0.6:  # 太平的块没有区分度
            out.append(patch)
        rr = side // 2
        work[max(0, y - rr) : y + rr, max(0, x - rr) : x + rr] = 0.0
        if peak < 0.02 * float(dens.max()):
            break
    return out


def _patch_match(img_edge: np.ndarray, patches: list[np.ndarray]) -> tuple[float, list[float]]:
    """每块各自找最佳匹配，返回 (最高分, 全部分数)。

    取最高分而不是中位数：10 块里可能只有 2 块真拍到产品，其余是机器和工服，
    取中位数等于让背景投票（实测中位数 0.31 / 最高分 0.55 / 空白 0.17）。
    """
    best: list[float] = []
    H, W = img_edge.shape
    for p in patches:
        top = -1.0
        for s in _PATCH_SCALES:
            tw, th = int(p.shape[1] * s), int(p.shape[0] * s)
            if tw >= W or th >= H or tw < 8 or th < 8:
                continue
            ncc, _ = _search(img_edge, _resize(p, tw, th))
            top = max(top, ncc)
        if top > -1.0:
            best.append(top)
    if not best:
        return 0.0, []
    best.sort(reverse=True)
    return float(best[0]), best


# ─────────────────────────── 主体铺展度 ───────────────────────────


def _spread(l: np.ndarray, box: tuple[int, int, int, int]) -> tuple[float, float] | None:
    """量主体在框里铺开的宽高比 —— 人就靠这个判断"压扁了没有"。

    注意这不是在搜索模板，只是阈值化后取分位数：不引入搜索自由度，
    因此不会被"小模板天然高分"这类偏差带跑。
    """
    x, y, w, h = box
    if w < 8 or h < 8:
        return None
    p = l[y : y + h, x : x + w]
    border = np.concatenate([p[0, :], p[-1, :], p[:, 0], p[:, -1]])
    bgv = float(np.median(border))
    d = np.abs(p - bgv)
    m = d > max(12.0, float(np.percentile(d, 88)) * 0.38)
    if m.sum() < 20:
        return None
    ys, xs = np.nonzero(m)
    ey = (np.percentile(ys, 98) - np.percentile(ys, 2)) / h
    ex = (np.percentile(xs, 98) - np.percentile(xs, 2)) / w
    if ey < 0.02 or ex < 0.02:
        return None
    return float(ex), float(ey)


def _sharpness(l: np.ndarray) -> float:
    return float(np.abs(np.diff(l, axis=0)).mean() + np.abs(np.diff(l, axis=1)).mean())


# ─────────────────────────── 主评估 ───────────────────────────


def assess(im: Image.Image, source: Image.Image, canvas: int = 320,
           ignore_spread: bool = False) -> dict:
    """评估成图 `im` 相对素材 `source` 的主体保真。

    `ignore_spread=True` 用于重排类模板（白底主图、卖点图）：这些模板会主动改
    画幅与主体占比，此时"产品占框比例"必然变化，`spread` 会误报变形（实测 90%）。
    重排类只保留结构分与清晰度。
    """
    src = _canvas(as_rgb(source), canvas)
    img = _canvas(as_rgb(im), canvas)

    info = analyze_source(src)
    s_l = _luma(np.asarray(src, dtype=np.float32))
    s_e = _edge(s_l)
    i_l = _luma(np.asarray(img, dtype=np.float32))
    i_e = _edge(i_l)

    if info["mode"] == "patch":
        score, all_scores = _patch_match(i_e, _key_patches(s_e))
        tier = "B" if score >= PATCH_B["structure"] else "C"
        reasons = []
        if score >= PATCH_B["structure"]:
            reasons.append(f"产品特征命中（块匹配 {score:.2f}）；素材为多实例场景，"
                           f"无法核验比例与细节，需人工确认")
        else:
            reasons.append(f"产品特征未命中（块匹配 {score:.2f}）：成图里可能不是这个产品")
        return {
            "structure": round(score, 3), "spread": None, "sharp": None, "scale": None,
            "score": round(float(np.clip(score, 0, 1)), 3), "tier": tier,
            "reasons": reasons,
            "mode": "patch", "patches": len(all_scores),
            "subject_box": None, "match_box": None, "reliable": False,
        }

    bx, by, bw, bh = info["box"]
    soft, _, _ = _subject_mask(s_l, (bx, by, bw, bh))
    tpl_e = s_e[by : by + bh, bx : bx + bw]
    if tpl_e.size < 64 or float(soft.sum()) < 16:
        tpl_e, soft = s_e, np.ones_like(s_e, dtype=np.float32)

    # ① 找主体：只在等比尺度上搜，不放开各向异性。
    #    放开过——基准会把变形吸收一半、小模板天然高分，两个偏差一起把结果带沟里。
    H, W = i_e.shape
    best = (-2.0, None, None)
    for s in _ISO_SCALES:
        tw, th = int(tpl_e.shape[1] * s), int(tpl_e.shape[0] * s)
        if tw >= W or th >= H or tw < 6 or th < 6:
            continue
        ncc, pos = _search(i_e, _resize(tpl_e, tw, th), weight=_resize(soft, tw, th))
        if ncc > best[0]:
            best = (ncc, s, pos)

    if best[1] is None:
        return _result(0.0, None, None, info, "无法匹配（尺寸或主体异常）")

    structure, s0, (ax, ay) = best
    tw, th = int(tpl_e.shape[1] * s0), int(tpl_e.shape[0] * s0)

    # ② 比例：匹配框内量铺展比，和素材主体的铺展比对照
    spread_dev = None
    wider = None
    s_spr = _spread(s_l, info["box"])
    i_spr = _spread(i_l, (ax, ay, tw, th))
    if s_spr and i_spr and s_spr[1] > 0.02 and not ignore_spread:
        s_ratio = s_spr[0] / s_spr[1]
        i_ratio = i_spr[0] / i_spr[1]
        spread_dev = abs(i_ratio / s_ratio - 1.0)
        wider = i_ratio > s_ratio

    # ③ 清晰度：只看主体区域，整幅图会被背景纹理带偏
    sharp_ratio = None
    src_sharp = _sharpness(s_l[by : by + bh, bx : bx + bw])
    img_sharp = _sharpness(i_l[ay : ay + th, ax : ax + tw])
    if src_sharp > 1e-3:
        sharp_ratio = img_sharp / src_sharp

    rel_scale = float(np.sqrt(tw * th) / np.sqrt(W * H))
    return _result(structure, spread_dev, sharp_ratio, info, "",
                   box=(ax, ay, tw, th), rel_scale=rel_scale, wider=wider)


def _result(structure: float, spread_dev: float | None, sharp_ratio: float | None,
            info: dict, note: str, box=None, rel_scale: float = 0.0,
            wider: bool | None = None) -> dict:
    reasons: list[str] = []

    if structure < TIER_C_STRUCTURE:
        reasons.append(f"主体结构对不上（{structure:.2f}）：产品可能没进画面或被替换")
    elif structure < TIER_A_STRUCTURE:
        reasons.append(f"主体结构只对上 {structure:.2f}：可能被重绘，需人看一眼")

    if spread_dev is not None:
        if spread_dev > TIER_C_SPREAD:
            axis = "纵向压扁" if wider is False else "横向拉长"
            reasons.append(f"{axis}约 {spread_dev * 100:.0f}%：明显超出可交付范围")
        elif spread_dev > TIER_B_SPREAD:
            reasons.append(f"比例偏差 {spread_dev * 100:.0f}%：肉眼不易判断，建议核对")

    if sharp_ratio is not None and sharp_ratio < TIER_SHARP:
        reasons.append(f"产品区域清晰度只有素材的 {sharp_ratio * 100:.0f}%：可能被糊掉")

    hard_c = (
        structure < TIER_C_STRUCTURE
        or (spread_dev is not None and spread_dev > TIER_C_SPREAD)
        or (sharp_ratio is not None and sharp_ratio < TIER_SHARP)
    )
    ok_a = (
        structure >= TIER_A_STRUCTURE
        and (spread_dev is None or spread_dev <= TIER_A_SPREAD)
        and (sharp_ratio is None or sharp_ratio >= TIER_SHARP)
    )
    tier = "C" if hard_c else ("A" if ok_a else "B")

    # score 只用于排序，不参与判定
    parts = [0.7 * min(structure, 1.0)]
    parts.append(0.2 * (1.0 - min((spread_dev or 0.0) / 0.25, 1.0)))
    parts.append(0.1 * min(sharp_ratio if sharp_ratio is not None else 1.0, 1.0))
    score = float(np.clip(sum(parts), 0.0, 1.0))

    if note:
        reasons.insert(0, note)
    if not info.get("reliable") and tier == "A":
        tier = "B"
        reasons.append("素材主体范围为估计值，建议人工确认")

    return {
        "structure": round(structure, 3),
        "spread": round(spread_dev, 3) if spread_dev is not None else None,
        "sharp": round(sharp_ratio, 3) if sharp_ratio is not None else None,
        "scale": round(rel_scale, 3),
        "score": round(score, 3),
        "tier": tier,
        "reasons": reasons,
        "mode": "object",
        "subject_box": info.get("box"),
        "match_box": box,
        "reliable": bool(info.get("reliable")),
    }


def quick(im: Image.Image, source: Image.Image) -> float:
    """兼容旧调用点，只返回 score。新代码请用 assess()。"""
    return assess(im, source)["score"]
