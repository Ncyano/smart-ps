# -*- coding: utf-8 -*-
"""评分器鉴别力测试（**已废弃，改用 bench_fidelity.py**）。

保留原因：它测的是旧的 `steps.fidelity`（整图灰度相关），留着当"反例存档"——
跑一遍就能看到旧度量分不开好坏：换背景 0.48 与主体被抹掉 0.42 几乎同分。

新评分器请用：`python scripts/bench_fidelity.py`

10 轮全绿不代表产线稳，可能只是门槛形同虚设。这里用可控变换造负样本，
检查 fidelity / style / quality 三项是否真的能区分好坏。

用法：python scripts/bench_score.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app.config import load  # noqa: E402
from server.app.pipeline import steps  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_sample() -> Image.Image:
    """取一张真实素材（全身人像），放大到 800 宽便于做变换。"""
    cands = [
        ROOT / "产出" / "ai人物" / "real8" / "slim_clean.png",
        ROOT / "产出" / "效果图" / "body.png",
    ]
    for p in cands:
        if p.exists():
            im = Image.open(p).convert("RGB")
            im.thumbnail((800, 1200), Image.LANCZOS)
            return im
    raise SystemExit("找不到可用素材")


def make_cases(src: Image.Image) -> list[tuple[str, Image.Image, bool]]:
    """(名称, 变换后的图, 人工判定是否该通过)"""
    w, h = src.size
    cases: list[tuple[str, Image.Image, bool]] = []

    # ── 应该通过 ──
    cases.append(("原图（基准）", src.copy(), True))
    mild = ImageEnhance.Contrast(src).enhance(1.06)
    mild = ImageEnhance.Color(mild).enhance(1.08)
    mild = mild.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=2))
    cases.append(("轻微增强（mock 实际输出）", mild, True))

    # 只换背景：主体完整保留，四周涂成浅米白
    bg = src.copy()
    d = ImageDraw.Draw(bg)
    d.rectangle((0, 0, w, int(h * 0.06)), fill=(237, 233, 228))
    d.rectangle((0, int(h * 0.06), int(w * 0.08), h), fill=(237, 233, 228))
    d.rectangle((int(w * 0.92), int(h * 0.06), w, h), fill=(237, 233, 228))
    cases.append(("只换背景（本项目核心用途）", bg, True))

    # ── 应该不通过 ──
    cases.append(("主体被抹掉（纯色）", Image.new("RGB", src.size, (237, 233, 228)), False))
    cases.append(("旋转 180°（主体结构错位）", src.rotate(180), False))
    cases.append(("严重模糊（细节丢失）", src.filter(ImageFilter.GaussianBlur(7)), False))
    cases.append(("强噪声（画质崩坏）", _noise(src, 45), False))
    cases.append(("过曝（高光死白）", _overexpose(src), False))

    small = src.resize((48, int(48 * h / w)), Image.LANCZOS)
    cases.append(("信息量仅 48px 宽", small.resize(src.size, Image.NEAREST), False))

    # 主体被换：中心区域贴一张纯色块
    swapped = src.copy()
    d = ImageDraw.Draw(swapped)
    d.rectangle((int(w * 0.3), int(h * 0.15), int(w * 0.7), int(h * 0.85)), fill=(20, 20, 90))
    cases.append(("主体被替换成色块", swapped, False))

    return cases


def _noise(im: Image.Image, sigma: float) -> Image.Image:
    a = np.asarray(im).astype(np.float32)
    rng = np.random.default_rng(7)
    a = a + rng.normal(0, sigma, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _overexpose(im: Image.Image) -> Image.Image:
    a = np.asarray(im).astype(np.float32) * 2.6 + 40
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def main() -> int:
    cfg = load()
    min_fid = float(cfg.get("quality", {}).get("min_fidelity", 0.90))
    min_style = float(cfg.get("quality", {}).get("min_style", 0.60))
    profile = steps.default_profile()

    src = load_sample()
    cases = make_cases(src)

    print(f"评分器鉴别力测试　阈值：fidelity ≥ {min_fid}　style ≥ {min_style}　quality 不参与判定")
    print(f"样本：{src.size[0]}x{src.size[1]}　profile：default（无例图时的行为）")
    print("=" * 88)
    print(f"{'变换':<28}{'保真':>8}{'风格':>8}{'质量':>8}{'判定':>10}{'人工':>8}{'一致':>8}")
    print("-" * 88)

    wrong = 0
    for name, im, should_pass in cases:
        fid = steps.fidelity(im, src)
        sty = steps.style_distance(im, profile)
        q = steps.quality_score(im)
        passed = fid >= min_fid and sty >= min_style
        agree = passed == should_pass
        if not agree:
            wrong += 1
        mark = "✓" if agree else "✗ 误判"
        print(f"{name:<28}{fid:>8.3f}{sty:>8.3f}{q:>8.3f}"
              f"{'通过' if passed else '拦截':>10}{'通过' if should_pass else '拦截':>8}{mark:>9}")

    print("-" * 88)
    print(f"一致 {len(cases) - wrong}/{len(cases)}，误判 {wrong} 条")

    # 关键边界：真实场景（只换背景）距离阈值还有多少余量
    bg_case = next(im for n, im, _ in cases if n.startswith("只换背景"))
    fid_bg = steps.fidelity(bg_case, src)
    print(f"\n关键观察：只换背景时 fidelity = {fid_bg:.3f}，"
          f"距阈值 {min_fid} 余量 {fid_bg - min_fid:+.3f}")
    if fid_bg - min_fid < 0.03:
        print("  → 余量过小：真实换背景稍有改动就会被误判为『主体被改写』")
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
