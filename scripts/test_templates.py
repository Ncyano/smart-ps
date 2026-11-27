"""工艺模板的离线测试（不联网、不花钱）。

模板的风险和别处不同：它**改像素**。错的模板会把好图做坏，而且做坏了还很"像样"，
不跑测试根本发现不了。这里钉住四件事：

1. 注册表：默认模板、未知 id 兜底
2. 卖点图：短文案要能排下、超长文案要**报溢出**（不能悄悄截断）、缺标题要报错
3. 白底主图：三种底（纯白 / 均匀非白 / 有纹理）走对分支，该拒的要拒
4. 重排标记：白底与卖点图必须 `reframes=True`，否则保真会把画幅变化误判成变形

跑法：python scripts/test_templates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import templates as T  # noqa: E402
from server.app.templates import layout as L  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILS.append(name)


def scene_photo(size: tuple[int, int] = (900, 900)) -> Image.Image:
    """造一张"产品 + 浅灰渐变台面"的图，模拟模型真实输出。"""
    w, h = size
    y = np.linspace(0, 1, h)[:, None]
    base = 226 - 22 * y
    a = np.dstack([base + 4, base + 1, base - 5]).repeat(w, axis=1)
    im = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.22, h * 0.30, w * 0.78, h * 0.72), fill=(58, 46, 34))     # 深色产品
    d.ellipse((w * 0.30, h * 0.36, w * 0.70, h * 0.62), fill=(196, 165, 108))   # 金色内盖
    return im


def white_photo(size: tuple[int, int] = (900, 900)) -> Image.Image:
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    w, h = size
    d.ellipse((w * 0.25, h * 0.28, w * 0.75, h * 0.70), fill=(60, 48, 36))
    return im


def noisy_photo(size: tuple[int, int] = (600, 600)) -> Image.Image:
    rng = np.random.default_rng(3)
    a = rng.normal(180, 28, (size[1], size[0], 3)).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(a)
    d = ImageDraw.Draw(im)
    d.ellipse((180, 180, 420, 420), fill=(60, 48, 36))
    return im


# ─── 1. 注册表 ───
def test_registry() -> None:
    check("内置三个模板", len(T.ALL) == 3, ", ".join(t.id for t in T.ALL))
    check("未知 id 兜底到 scene", T.get("nope").id == "scene")
    check("None 兜底到 scene", T.get(None).id == "scene")
    for t in T.ALL:
        d = t.to_dict()
        check(f"{t.id} 序列化完整", all(k in d for k in ("id", "version", "export", "reframes")))
        check(f"{t.id} 版本号存在", isinstance(t.version, int) and t.version >= 1)


def test_reframes() -> None:
    check("白底会改画幅 → reframes", T.get("white_bg").reframes is True)
    check("卖点图会改画幅 → reframes", T.get("kv").reframes is True)
    check("场景图不改画幅", T.get("scene").reframes is False)


# ─── 2. 卖点图 ───
def test_kv() -> None:
    kv = T.get("kv")
    short = {"title": "奶油肌气垫 遮瑕不卡粉", "subtitle": "轻薄贴肤 · 持妆一整天",
             "points": ["一层盖住痘印与红血丝", "微米级粉体贴肤"], "tag": "油皮亲妈"}
    im, rep = kv.apply(scene_photo(), short, {})
    check("短文案排版通过", rep["ok"] is True, str(rep["reasons"]))
    check("画布 1080×1350", im.size == (1080, 1350), str(im.size))
    check("文字落在文字区内", rep["stats"]["text_bottom"] < int(1350 * 0.60),
          str(rep["stats"]["text_bottom"]))

    # 文案区必须真的被磨砂+压过，否则照片上的白字会看不清。
    # 别用 "std 变小" 判：文字本身方差很高，会把指标带反（踩过）。
    # 改成和"无排版的纯裁切"对照——顶部只要被处理过，就必然有差异。
    plain = scene_photo()
    sc = max(1080 / plain.width, 1350 / plain.height)
    pw, ph = round(plain.width * sc), round(plain.height * sc)
    plain = plain.resize((pw, ph), Image.LANCZOS).crop(
        ((pw - 1080) // 2, (ph - 1350) // 2, (pw - 1080) // 2 + 1080, (ph - 1350) // 2 + 1350))
    diff = float(np.abs(np.asarray(im.crop((0, 0, 1080, 260)), dtype=np.float32)
                        - np.asarray(plain.crop((0, 0, 1080, 260)), dtype=np.float32)).mean())
    check("顶部已做磨砂处理（与纯裁切有差异）", diff > 8.0, f"平均差 {diff:.1f}")

    long = {"title": "超长标题" * 30, "points": ["很长的卖点" * 20] * 3}
    _, rep2 = kv.apply(scene_photo(), long, {})
    check("超长文案报溢出（不悄悄截断）", rep2["ok"] is False, str(rep2["reasons"][:2]))

    _, rep3 = kv.apply(scene_photo(), {"points": ["x"]}, {})
    check("缺标题要报错", rep3["ok"] is False and any("标题" in r for r in rep3["reasons"]))

    _, rep4 = kv.apply(scene_photo(), {"title": "只有标题"}, {})
    check("只有标题也能出图", rep4["ok"] is True and rep4["stats"]["text_bottom"] > 0)


# ─── 3. 白底主图 ───
def test_white_bg() -> None:
    wb = T.get("white_bg")

    im, rep = wb.apply(white_photo(), {}, {})
    check("纯白底走白底重排", rep["ok"] is True and "白底" in rep["stats"].get("branch", ""),
          str(rep["stats"]))
    check("纯白底输出 1080²", im.size == (1080, 1080), str(im.size))
    corners = [im.getpixel(p) for p in [(3, 3), (1076, 3), (3, 1076)]]
    check("四角纯白", all(c == (255, 255, 255) for c in corners), str(corners))

    im2, rep2 = wb.apply(scene_photo(), {}, {})
    check("均匀非白底能抠图", rep2["ok"] is True, str(rep2["stats"]))
    check("抠图后输出 1080²", im2.size == (1080, 1080), str(im2.size))
    check("抠图后背景为白", im2.getpixel((4, 4)) == (255, 255, 255), str(im2.getpixel((4, 4))))

    _, rep3 = wb.apply(noisy_photo(), {}, {})
    check("纹理底要被拒绝", rep3["ok"] is False and any("纹理" in r for r in rep3["reasons"]),
          str(rep3["reasons"][:1]))


# ─── 4. 排版算子 ───
def test_layout() -> None:
    im = Image.new("RGB", (400, 200), (255, 255, 255))
    d = ImageDraw.Draw(im)
    f, lines, ok = L.fit_block(d, "这是一段需要折行的中文文案" * 3, 300, 120, 40, 14, 1.3)
    check("折行后能装下", ok is True and len(lines) >= 2, f"{len(lines)} 行")
    check("每行不超宽", all(L.text_w(d, ln, f) <= 300.5 for ln in lines))

    _, _, ok2 = L.fit_block(d, "字" * 400, 100, 40, 30, 20, 1.3)
    check("极小框要报装不下", ok2 is False)

    dark = L.readable_on((20, 20, 20))
    light = L.readable_on((240, 240, 240))
    check("深底配浅字", dark[0] > 200, str(dark))
    check("浅底配深字", light[0] < 60, str(light))


def main() -> int:
    print(f"工艺模板测试　字体：{L.font_path(True) or '未找到中文字体（会退到默认字体）'}")
    print("=" * 68)
    test_registry()
    test_reframes()
    test_kv()
    test_white_bg()
    test_layout()
    print("=" * 68)
    print("全部通过" if not FAILS else f"失败 {len(FAILS)} 条：" + ", ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
