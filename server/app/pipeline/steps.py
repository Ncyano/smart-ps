"""图像度量与本地后处理的基础算子（numpy + Pillow，无重依赖）。

这里的每个函数都对应设计文档里「确定性那一条腿」：不生成、只校正。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

W = np.array([0.299, 0.587, 0.114], dtype=np.float32)


# ─────────────────────────── 基础度量 ───────────────────────────


def luma(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"), dtype=np.float32) @ W


def high_freq_energy(im: Image.Image) -> float:
    l = luma(im)
    return float(np.abs(np.diff(l, axis=0)).mean() + np.abs(np.diff(l, axis=1)).mean())


def saturation(im: Image.Image) -> float:
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    return float(np.mean((mx - mn) / np.maximum(mx, 1.0)))


def near_white_ratio(im: Image.Image) -> float:
    a = np.asarray(im.convert("RGB"))
    return float(np.mean(a.min(axis=2) > 245))


def image_stats(im: Image.Image) -> dict:
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    l = a @ W
    p5, p50, p95 = np.percentile(l, [5, 50, 95])
    return {
        "p5": float(p5),
        "p50": float(p50),
        "p95": float(p95),
        "contrast": float(l.std() / 255.0),
        "sat": saturation(im),
        "hf": high_freq_energy(im),
        "mean_rgb": [float(x) for x in a.reshape(-1, 3).mean(axis=0)],
        "near_white": near_white_ratio(im),
    }


def _open_limited(path: Path, long_side: int = 1024) -> Image.Image:
    im = Image.open(path).convert("RGB")
    im.thumbnail((long_side, long_side), Image.LANCZOS)
    return im


# ─────────────────────────── 风格画像 ───────────────────────────


def profile_from(paths: list[Path]) -> dict:
    """从往期例图统计出 StyleProfile。例图越多越稳，20–50 张足够。"""
    stats = []
    for p in paths:
        try:
            stats.append(image_stats(_open_limited(p)))
        except Exception:
            continue
    if not stats:
        return default_profile()

    med = lambda k: float(np.median([s[k] for s in stats]))  # noqa: E731
    mean_rgb = np.median(np.array([s["mean_rgb"] for s in stats]), axis=0)

    return {
        "version": 1,
        "source": {"count": len(stats)},
        "tone": {"p5": med("p5"), "p50": med("p50"), "p95": med("p95")},
        "color": {
            "sat": med("sat"),
            "contrast": med("contrast"),
            "mean_rgb": [float(x) for x in mean_rgb],
        },
        "texture": {"hf": med("hf")},
        "composition": {
            "bg": "pure_white" if med("near_white") > 0.55 else "scene",
            "near_white": med("near_white"),
            "subject_ratio": 0.0,  # 需要分割才能算准，M1 补
        },
    }


def default_profile() -> dict:
    """没有例图时的中性目标：不改变风格，只做轻微整理。"""
    return {
        "version": 1,
        "source": {"count": 0},
        "tone": {"p5": 8.0, "p50": 128.0, "p95": 246.0},
        "color": {"sat": 0.22, "contrast": 0.22, "mean_rgb": [128.0, 128.0, 128.0]},
        "texture": {"hf": 6.0},
        "composition": {"bg": "scene", "near_white": 0.0, "subject_ratio": 0.0},
    }


# ─────────────────────────── 确定性回正 ───────────────────────────


def tone_match(im: Image.Image, profile: dict) -> Image.Image:
    """把亮度分位数对齐到目标带；按增益缩放 RGB，尽量保住色相。

    **纯白/纯黑底必须跳过**：分位数重映射假设"画面上有完整的明暗分布"，
    而白底产品图的 p5 本来就在 250 以上，按目标曲线一插值会把整片白底压成
    中灰（实测 255 → 128）。这不是参数问题，是方法用错了地方。
    """
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    l = a @ W
    cur = np.percentile(l, [5, 50, 95])

    # 底色占绝对主导时不做重映射。
    # 典型：白底产品图 = 一大片 255 + 一块深色产品，直方图双峰。这时 p50 落在 255，
    # 按目标曲线会把它映射到 128 —— 整片白底压成中灰（实测）。
    # 判据必须用"背景像素占比"，不能只看分位数：这张图 p5=31，分位数护栏根本不触发。
    frac_white = float((l > 248).mean())
    frac_dark = float((l < 10).mean())
    if frac_white > 0.35 or frac_dark > 0.50 or cur[0] > 198 or cur[2] < 58:
        return im

    tune = profile.get("tone", {})
    tgt = np.array([tune.get("p5", 8.0), tune.get("p50", 128.0), tune.get("p95", 246.0)], dtype=np.float32)

    xs = np.array([0.0, cur[0], cur[1], cur[2], 255.0], dtype=np.float32)
    ys = np.array([0.0, tgt[0], tgt[1], tgt[2], 255.0], dtype=np.float32)
    xs = np.maximum.accumulate(xs + np.arange(5, dtype=np.float32) * 1e-3)
    ys = np.clip(ys, 0, 255)

    lut = np.interp(np.arange(256, dtype=np.float32), xs, ys)
    new_l = lut[np.clip(l, 0, 255).astype(np.uint8)]
    gain = (new_l + 1.0) / (l + 1.0)
    out = np.clip(a * gain[..., None], 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def finalize(im: Image.Image, profile: dict) -> Image.Image:
    """回正主流程：影调 → 饱和 → 锐化。顺序不能反。"""
    im = tone_match(im, profile)

    target_sat = float(profile.get("color", {}).get("sat", 0.22))
    cur_sat = saturation(im)
    k = 1.0 + float(np.clip((target_sat - cur_sat) * 1.2, -0.35, 0.35))
    im = ImageEnhance.Color(im).enhance(k)

    target_hf = float(profile.get("texture", {}).get("hf", 6.0))
    cur_hf = max(high_freq_energy(im), 1e-3)
    amount = float(np.clip(target_hf / cur_hf, 0.6, 1.6))
    im = im.filter(ImageFilter.UnsharpMask(radius=1.1, percent=int(55 * amount), threshold=2))
    return im


# ─────────────────────────── 评分 ───────────────────────────


def style_distance(im: Image.Image, profile: dict) -> float:
    """1.0 = 完全贴合目标风格，0.0 = 完全不像。"""
    s = image_stats(im)
    tune = profile.get("tone", {})
    d_tone = (
        abs(s["p5"] - tune.get("p5", s["p5"]))
        + abs(s["p50"] - tune.get("p50", s["p50"]))
        + abs(s["p95"] - tune.get("p95", s["p95"]))
    ) / (255.0 * 3)
    d_sat = abs(s["sat"] - float(profile.get("color", {}).get("sat", s["sat"])))
    d_hf = abs(s["hf"] - float(profile.get("texture", {}).get("hf", s["hf"]))) / max(s["hf"], 1.0)
    dist = 0.6 * d_tone + 0.3 * min(d_sat * 2, 1.0) + 0.1 * min(d_hf, 1.0)
    return float(np.clip(1.0 - dist, 0.0, 1.0))


def fidelity(im: Image.Image, source: Image.Image, size: int = 96) -> float:
    """内容保真：素材主体是否被改写。v0 用下采样灰度相关性做近似。"""
    a = luma(im.resize((size, size), Image.LANCZOS))
    b = luma(source.resize((size, size), Image.LANCZOS))
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom < 1e-6:
        return 0.0
    r = float((a * b).sum() / denom)
    return float(np.clip((r + 1.0) / 2.0, 0.0, 1.0))


def quality_score(im: Image.Image) -> float:
    """技术质量：清晰度为主，v0 只做相对标定。"""
    hf = high_freq_energy(im)
    return float(np.clip(hf / 12.0, 0.0, 1.0))
