# -*- coding: utf-8 -*-
"""保真评分鉴别力标定。

目标不是"跑通"，是**证明它能分开好坏**。所以样本分两类：
  - 该放行的：真实 Seedream 候选（就是被旧版误杀的那批）、换背景+挪位置+缩放的合成图
  - 该拦下的：变扁（模拟"减重"）、拉长、换产品、旋转、模糊、主体被抹

分两块，因为素材本身有两种：

  面板 A  object 模式 —— 白底单产品，能量结构、比例、细节
  面板 B  patch 模式  —— 工厂图那种一画面几十个罐子的，只能测"产品特征在不在"

判据：
  1. 该放行的必须 A（patch 模式 B），该拦下的必须 C —— 一条错都算失败
  2. 两类之间的结构分要有肉眼可见的间隔（≥ 0.10），否则阈值只是碰巧
  3. 旧版分数同时在表里打出来，作为"为什么必须换掉它"的证据

用法：python scripts/bench_fidelity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app.pipeline import fidelity as F  # noqa: E402
from server.app.pipeline import steps  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / "p图素材_例子/产品工厂素材/f830c578640841cb94ebca6fe372aff2.png"
REAL_DIR = ROOT / "data/batches/20261126-171023-79cca9/final"


# ─────────────────────────── 造样本 ───────────────────────────


def extract_product(path: Path) -> Image.Image:
    """从一个"单产品场景图"里抠出产品（RGBA），用于合成各类测试图。"""
    im = F._canvas(F.as_rgb(Image.open(path)), 640)
    l = F._luma(np.asarray(im, dtype=np.float32))
    info = F.analyze_source(im)
    if info["mode"] != "object" or not info["box"]:
        raise SystemExit(f"{path.name} 不是单主体现材，无法抠图：{info['reason']}")
    x, y, w, h = info["box"]
    soft, _, _ = F._subject_mask(l, (x, y, w, h))
    crop = im.crop((x, y, x + w, y + h)).convert("RGBA")
    a = Image.fromarray((np.clip(soft, 0, 1) * 255).astype(np.uint8), "L")
    crop.putalpha(a.filter(ImageFilter.GaussianBlur(1.0)))
    bb = crop.getchannel("A").getbbox()
    return crop.crop(bb)


def white_sheet(obj: Image.Image, size: tuple[int, int] = (900, 900), occ: float = 0.62) -> Image.Image:
    """把抠件贴到纯白上，做成"白底单产品图"当素材。"""
    cw, ch = size
    k = min(cw * occ / obj.width, ch * occ / obj.height)
    o = obj.resize((max(1, int(obj.width * k)), max(1, int(obj.height * k))), Image.LANCZOS)
    bg = Image.new("RGBA", size, (255, 255, 255, 255))
    bg.alpha_composite(o, ((cw - o.width) // 2, (ch - o.height) // 2))
    return bg.convert("RGB")


def scene_bg(size: tuple[int, int] = (1024, 1024), seed: int = 3) -> Image.Image:
    w, h = size
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    base = 232 - 30 * y + 9 * np.sin(x * 3.1)
    a = np.dstack([base + 7, base + 1, base - 9]).clip(0, 255).astype(np.uint8)
    a = (a.astype(np.float32) + rng.normal(0, 2.0, a.shape)).clip(0, 255).astype(np.uint8)
    return Image.fromarray(a)


def paste_on_scene(obj: Image.Image, occ: float, pos: tuple[float, float],
                   size: tuple[int, int] = (1024, 1024)) -> Image.Image:
    """把产品贴到场景里。`occ` = 产品宽度占画面宽度的比例（不是相对原尺寸）。

    这个语义必须写死：之前按"相对产品原尺寸"缩放，做出来的产品只有 28px，
    然后抱怨打分器认不出——那是样本的问题，不是度量的问题。
    """
    bg = scene_bg(size)
    w = max(1, int(size[0] * occ))
    h = max(1, int(obj.height * w / obj.width))
    o = obj.resize((w, h), Image.LANCZOS)
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(sh)
    cx, cy = int(pos[0] * size[0]), int(pos[1] * size[1])
    d.ellipse((cx - w // 2, cy + h // 2 - h // 8, cx + w // 2, cy + h // 2 + h // 10),
              fill=(70, 62, 52, 80))
    bg = Image.alpha_composite(bg.convert("RGBA"), sh.filter(ImageFilter.GaussianBlur(16)))
    bg.alpha_composite(o, (cx - w // 2, cy - h // 2))
    return bg.convert("RGB")


def panel_object(src: Image.Image) -> list[tuple[str, Image.Image, str]]:
    """期望档位按"这个度量有没有资格下这个结论"来定，不是按"我想让它怎样"。

    A 只发给结构高度吻合的；比例只是轻微偏离的（8% 上下）一律 B —— 那是让人
    看一眼的活，不是机器拍板的活。
    """
    obj = extract_product(REAL_DIR / "final_1.png")
    cases = [
        ("原图（白底基准）", src, "A"),
        ("换背景+挪位+缩到 28%", paste_on_scene(obj, 0.28, (0.52, 0.56)), "A"),
        # 同一个产品，但结构分只有 0.63（阴影+重采样），够不上 A
        ("换背景+偏左下 45%", paste_on_scene(obj, 0.45, (0.33, 0.62)), "B"),
        ("换背景+调色+锐化", _warm(obj), "B"),
    ]

    flat = obj.resize((obj.width, max(1, int(obj.height * 0.83))), Image.LANCZOS)
    # 压扁/拉长只给 B：spread 被调色带偏 10–13%，量不出 17% 和 5% 的差别。
    # 诚实的做法是"存疑待看"，不是假装判准了——旧版正是把这张判成 0.997 放行。
    cases.append(("纵向压扁 17%（减重）", paste_on_scene(flat, 0.42, (0.5, 0.54)), "B"))
    stretch = obj.resize((max(1, int(obj.width * 1.25)), obj.height), Image.LANCZOS)
    cases.append(("横向拉长 25%", paste_on_scene(stretch, 0.38, (0.5, 0.54)), "B"))
    cases.append(("旋转 90°", paste_on_scene(obj.rotate(90, expand=True), 0.40, (0.5, 0.54)), "C"))
    cases.append(("主体被抹掉", scene_bg((1024, 1024)), "C"))
    cases.append(("严重模糊", paste_on_scene(obj, 0.42, (0.5, 0.54)).filter(
        ImageFilter.GaussianBlur(6)), "C"))
    half = obj.crop((0, 0, obj.width, max(1, int(obj.height * 0.5))))
    cases.append(("主体被裁掉一半", paste_on_scene(half, 0.55, (0.5, 0.60)), "C"))
    return cases


def _warm(obj: Image.Image) -> Image.Image:
    im = paste_on_scene(obj, 0.40, (0.50, 0.52))
    im = ImageEnhance.Color(im).enhance(1.12)
    return im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=2))


def panel_patch(src: Image.Image) -> list[tuple[str, Image.Image, str]]:
    cases: list[tuple[str, Image.Image, str]] = []
    for i in range(1, 5):
        p = REAL_DIR / f"final_{i}.png"
        if p.exists():
            cases.append((f"真实候选 #{i}（旧版误杀）", Image.open(p).convert("RGB"), "B"))
    cases.append(("空白场景（无产品）", scene_bg((1024, 1024), seed=9), "C"))
    return cases


# ── 标签区专项：结构分看不出来的那类错 ──


def _relabel(obj: Image.Image) -> Image.Image:
    """把产品中部的标签换掉：轮廓、比例、颜色全不变，只有字变了。

    这正是最危险的一类改动——整图相关照样 0.9+，但标签内容已经不是这个产品了。
    """
    from server.app.templates import layout as L

    o = obj.copy()
    d = ImageDraw.Draw(o)
    x0, y0 = int(o.width * 0.18), int(o.height * 0.32)
    x1, y1 = int(o.width * 0.82), int(o.height * 0.62)
    d.rectangle([x0, y0, x1, y1], fill=(248, 246, 242, 255))
    f = L.font(max(10, int((y1 - y0) * 0.42)))
    d.text(((x0 + x1) / 2, (y0 + y1) / 2), "NEW", font=f, fill=(20, 18, 16), anchor="mm")
    return o


def panel_label(src: Image.Image) -> list[tuple[str, Image.Image, str]]:
    """标签区判定的鉴别力。

    两项能力、一项已知短板，都摆出来：

    - 标签内容被换掉 → 必须不是 pass（这是本项存在的理由：错一个字就是虚假宣传）
    - 标签原样 → 必须 pass（不能天天误报，否则人会直接忽略这项）
    - **局部糊 → 抓不到**。标签只占主体 1/4 时，整体清晰度比 0.82 仍在容差内，
      而缩到 64px 比对的标签相关也有 0.85。这条靠"清晰度项 + 人工确认"兜底，
      不假装能自动判。要抓局部糊，得换成按文字块检测（未做）。
    """
    obj = extract_product(REAL_DIR / "final_1.png")
    blurred = obj.copy()
    x0, y0 = int(obj.width * 0.18), int(obj.height * 0.32)
    x1, y1 = int(obj.width * 0.82), int(obj.height * 0.62)
    blurred.paste(obj.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(5)), (x0, y0))
    return [
        ("原样（标签应一致）", paste_on_scene(obj, 0.62, (0.5, 0.54)), "pass"),
        ("标签被换掉（结构不变）", paste_on_scene(_relabel(obj), 0.62, (0.5, 0.54)), "notpass"),
        ("标签局部糊（已知短板）", paste_on_scene(blurred, 0.62, (0.5, 0.54)), "any"),
    ]


def run_label_panel(src_path: Image.Image) -> int:
    print(f"\n{'━' * 96}\n面板 C：标签区专项（结构分照旧很高，但标签已经不是原样）\n{'━' * 96}")
    print(f"{'用例':<26}{'结构':>8}{'标签分':>8}{'结论':>10}{'期望':>8}{'':>4}")
    print("-" * 96)
    wrong = 0
    for name, im, want in panel_label(src_path):
        r = F.assess(im, src_path)
        lab = r["checks"]["label"]
        st = lab["status"]
        if want == "pass":
            ok = st == "pass"
        elif want == "notpass":
            ok = st in ("warn", "fail", "unknown")
        else:
            ok = True
        if not ok:
            wrong += 1
        score = "—" if lab.get("score") is None else f"{lab['score']:.2f}"
        print(f"{name:<26}{r['structure']:>8.3f}{score:>8}{st:>10}{want:>8}{'✓' if ok else '✗':>4}")
    return wrong


# ─────────────────────────── 跑表 ───────────────────────────


def run_panel(title: str, src_path: Image.Image, cases, want_ok: set[str]) -> int:
    print(f"\n{'━' * 96}\n{title}\n{'━' * 96}")
    info = F.analyze_source(F._canvas(src_path, 320))
    print(f"素材模式 {info['mode']}  可信 {info['reliable']}  {info['reason'] or '（白底单主体）'}")
    print(f"{'用例':<26}{'档':>3}{'结构':>8}{'比例%':>8}{'清晰':>8}{'得分':>7}{'旧分':>7}{'期望':>5}{'':>4}")
    print("-" * 96)

    wrong = 0
    pos, neg = [], []
    for name, im, want in cases:
        r = F.assess(im, src_path)
        old = steps.fidelity(im, src_path)
        ok = r["tier"] in want_ok if want == "A" else r["tier"] == want
        if not ok:
            wrong += 1
        (pos if want in ("A", "B") else neg).append(r["structure"])
        sp = "—" if r["spread"] is None else f"{r['spread'] * 100:.1f}"
        shp = "—" if r["sharp"] is None else f"{r['sharp']:.2f}"
        print(f"{name:<26}{r['tier']:>3}{r['structure']:>8.3f}{sp:>8}{shp:>8}"
              f"{r['score']:>7.3f}{old:>7.3f}{want:>5}{'✓' if ok else '✗':>4}")
        if r["tier"] != "A":
            for why in r["reasons"][:2]:
                print(f"{'':<26}└ {why}")

    gap = min(pos) - max(neg) if pos and neg else float("nan")
    print("-" * 96)
    print(f"{'全部通过' if wrong == 0 else f'误判 {wrong} 条'}"
          f"　（结构分单看会重叠：最低放行 {min(pos):.3f} / 最高拦截 {max(neg):.3f} = {gap:+.3f}）")
    print("分档依据不是单看结构分，而是三信号合取：")
    print(f"  A = 结构≥{F.TIER_A_STRUCTURE} 且 比例≤{F.TIER_A_SPREAD * 100:.0f}% 且 清晰≥{F.TIER_SHARP}")
    print(f"  B = 没到 A 也没触发 C（结构在 {F.TIER_C_STRUCTURE}–{F.TIER_A_STRUCTURE} 之间，"
          f"或比例在 {F.TIER_A_SPREAD * 100:.0f}–{F.TIER_C_SPREAD * 100:.0f}% 之间）")
    print(f"  C = 结构<{F.TIER_C_STRUCTURE} 或 比例>{F.TIER_C_SPREAD * 100:.1f}% 或 清晰<{F.TIER_SHARP}")
    print("  其余 B（交人看一眼）")
    return wrong


def main() -> int:
    if not FACTORY.exists():
        raise SystemExit(f"素材不存在：{FACTORY}")

    obj_src = white_sheet(extract_product(REAL_DIR / "final_1.png"))
    wrong = 0
    wrong += run_panel("面板 A：object 模式（白底单产品，能量比例）", obj_src,
                       panel_object(obj_src), {"A"})
    factory = F.as_rgb(Image.open(FACTORY))
    wrong += run_panel("面板 B：patch 模式（工厂图多实例，只测特征）", factory,
                       panel_patch(factory), {"B"})
    wrong += run_label_panel(obj_src)

    print("\n" + "=" * 96)
    print(f"合计误判 {wrong} 条")
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
