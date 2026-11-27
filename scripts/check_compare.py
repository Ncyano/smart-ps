# -*- coding: utf-8 -*-
"""前后对比效果图的量化质检。

判定分三层：
  A 硬指标（本脚本自动判定，不通过即不可交付）
  B 软指标（自动算数，给人参考）
  C 生成约束（不可自动判定，见 docs/04 的签收表）

数值阈值全部来自 config/config.toml 的 [compare] 段，此处不写死。

用法：
    python scripts/check_compare.py 产出/效果图/body.png
    python scripts/check_compare.py 产出/效果图/*.png --json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.app.config import load  # noqa: E402

CFG = load().get("compare", {})


def c(key, default):
    v = CFG.get(key, default)
    return default if v is None else v


# ─────────────────────────── 基础工具 ───────────────────────────


def find_seam(im: Image.Image, lo_frac=0.42, hi_frac=0.58, thr=232):
    """在中心区找竖直白缝，返回 (起, 止) 像素列。"""
    a = np.asarray(im.convert("L")).astype(np.float32)
    w = a.shape[1]
    lo, hi = int(w * lo_frac), int(w * hi_frac)
    cols = a[:, lo:hi].mean(axis=0)
    idx = [i + lo for i, v in enumerate(cols) if v > thr]
    if not idx:
        cc = int(np.argmax(cols)) + lo
        return cc - 2, cc + 3
    return idx[0], idx[-1] + 1


def split_halves(im: Image.Image):
    s0, s1 = find_seam(im)
    return [im.crop((0, 0, s0, im.height)), im.crop((s1, 0, im.width, im.height))], (s0, s1)


def probes(im: Image.Image, k, top):
    w, h = im.size
    bw, bh = int(w * k), int(h * k)
    return [
        im.crop((0, 0, bw, bh)),
        im.crop((w - bw, 0, w, bh)),
        im.crop((0, h - bh, bw, h)),
        im.crop((w - bw, h - bh, w, h)),
        im.crop((0, 0, w, int(h * top))),
    ]


def bg_metrics(box: Image.Image) -> dict:
    a = np.asarray(box.convert("RGB"), dtype=np.float32)
    if a.size == 0:
        return dict(edge=0.0, hf=0.0, top1=0.0, p50=0.0)
    l = a @ np.array([0.299, 0.587, 0.114], np.float32)
    gx = np.abs(np.diff(l, axis=1))[:-1, :]
    gy = np.abs(np.diff(l, axis=0))[:, :-1]
    mag = np.hypot(gx, gy)
    q = (a // 8).astype(np.int32)
    flat = q[:, :, 0] * 1024 + q[:, :, 1] * 32 + q[:, :, 2]
    cnt = np.unique(flat, return_counts=True)[1]
    top1 = float(cnt.max() / flat.size)
    return dict(
        edge=float((mag > 24).mean()),
        hf=float(mag.mean()),
        top1=top1,
        p50=float(np.percentile(l, 50)),
    )


def head_line(im: Image.Image, thr: float = 95.0, frac: float = 0.02,
              minrun: int = 10):
    """头顶线在画布中的纵坐标比例（0~1）。

    用"中列一带出现连续深色行"来找头发，比 GrabCut 稳得多：
    GrabCut 在这个场景下会随 GMM 初始化漂移，同一张图的两半能差出 10% 以上，
    报出来的"身高差"其实是分割误差。衣柜顶沿这类水平细线只有几行，
    用「连续 ≥minrun 行」的约束就能排掉。
    """
    a = np.asarray(im.convert("L"), dtype=np.float32)
    h, w = a.shape
    if h < 32 or w < 32:
        return None
    band = a[:, int(w * 0.28): int(w * 0.72)]
    dark = (band < thr).mean(axis=1) > frac
    y = int(h * 0.05)
    while y < h:
        if dark[y]:
            s = y
            while y < h and dark[y]:
                y += 1
            if y - s >= minrun:
                return s / h
        else:
            y += 1
    return None


def headroom(im: Image.Image) -> tuple[float, bool]:
    """头顶留白占画布高比例。

    按列取背景基线（吸收墙面横向光照渐变），再逐行找第一个「明显非背景」行。
    返回 (比例, 是否头顶出画)。比例 < 0.02 视为特写/全景构图，头顶在画外，指标不适用。
    """
    w, h = im.size
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    bh = max(int(h * 0.10), 8)
    colbg = np.median(a[:bh], axis=0)                      # (w, 3) 每列背景基线
    dist = np.linalg.norm(a - colbg[None, :, :], axis=2)
    keep = np.ones(w, bool)
    keep[int(w * 0.47):int(w * 0.53)] = False              # 排除中缝
    frac = (dist[:, keep] > 26).mean(axis=1)
    rows = np.where(frac > 0.30)[0]
    if not len(rows):
        return 1.0, True
    r = float(rows[0] / h)
    return r, r < 0.02


def watermark_ratio(im: Image.Image, x0f, y0f) -> tuple[float, float]:
    """右下角「近白细笔画」占比，与正上方同尺寸区域对比。

    水印是叠加的白色文字/半透明字，特征是近白像素明显多于同列上方；
    衣服、皮肤、地板纹理的近白占比极低，因此不会误报。
    """
    w, h = im.size
    x0, y0 = int(w * x0f), int(h * y0f)
    box = np.asarray(im.crop((x0, y0, w, h)).convert("RGB"), dtype=np.float32)
    ph = box.shape[0]
    up = np.asarray(im.crop((x0, max(0, y0 - ph), w, y0)).convert("RGB"), dtype=np.float32)

    def ratio(a):
        if a.size == 0:
            return 0.0
        return float((a.min(axis=2) > 205).mean())

    return ratio(box), ratio(up)


# ─────────────────────────── 检查项 ───────────────────────────


class Report:
    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict] = []

    def add(self, code, name, value, limit, ok, unit="", note=""):
        self.rows.append(dict(code=code, name=name, value=value, limit=limit,
                              ok=ok, unit=unit, note=note))

    @property
    def hard_fail(self):
        return [r for r in self.rows if not r["ok"]]

    def text(self):
        out = [f"图: {self.path.name}"]
        for r in self.rows:
            flag = "PASS" if r["ok"] else "FAIL"
            out.append(f"  {r['code']:<7}{r['name']:<10}{flag}   "
                       f"{r['value']}{r['unit']}   限 {r['limit']}{r['note']}")
        n = len(self.rows)
        out.append(f"  {'-' * 46}")
        out.append(f"  硬指标 {n - len(self.hard_fail)}/{n} 通过"
                   + ("" if not self.hard_fail else
                      "   → " + "、".join(r["name"] for r in self.hard_fail) + " 不合格"))
        return "\n".join(out)


def check(path: Path, source: bool = False, scene: str = "closeup") -> Report:
    """scene: closeup=近景（面部/颈肩），full=全身镜面自拍（家居场景）"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    rep = Report(path)
    cw, ch = int(c("width", 1080)), int(c("height", 1350))

    if source:
        rep.rows.append(dict(code="A1", name="画布尺寸", value=f"{w}x{h}",
                             limit="源图模式跳过", ok=True, unit="",
                             note="生成源图，版式项不适用"))
        rep.rows.append(dict(code="A1", name="中缝", value="—", limit="源图模式跳过",
                             ok=True, unit="", note=""))
    else:
        # A1 画布
        rep.add("A1", "画布尺寸", f"{w}x{h}", f"{cw}x{ch}", w == cw and h == ch, "px")

        # A1 白缝
        s0, s1 = find_seam(im)
        sw = s1 - s0
        seam_ok = 4 <= sw <= 6 and abs((s0 + s1) / 2 - w / 2) <= 4
        rep.add("A1", "中缝", f"{sw}px@{s0}", f"{c('seam_px', 5)}px@居中",
                seam_ok, "", "" if seam_ok else "缝宽或位置偏移")

    # A3 背景洁净度（逐探针平均）
    halves, _ = split_halves(im)
    acc = []
    for half in halves:
        for pr in probes(half, float(c("bg_probe_corner", 0.22)), float(c("bg_probe_top", 0.16))):
            acc.append(bg_metrics(pr))
    edge = float(np.mean([x["edge"] for x in acc]))
    hf = float(np.mean([x["hf"] for x in acc]))
    top1 = float(np.mean([x["top1"] for x in acc]))
    p50 = float(np.mean([x["p50"] for x in acc]))

    emax = float(c("bg_edge_max_full" if scene == "full" else "bg_edge_max", 0.025))
    hmax = float(c("bg_hf_max_full" if scene == "full" else "bg_hf_max", 5.0))
    tmin = float(c("bg_top1_min_full" if scene == "full" else "bg_top1_min", 0.12))
    lmin, lmax = float(c("bg_luma_min", 80)), float(c("bg_luma_max", 210))

    rep.add("A3", "背景边缘", round(edge, 3), f"≤{emax}", edge <= emax, "",
            "" if edge <= emax else "背景有家具/杂物轮廓")
    rep.add("A3", "背景高频", round(hf, 2), f"≤{hmax}", hf <= hmax, "",
            "" if hf <= hmax else "背景纹理多，不像单色墙面")
    rep.add("A3", "背景主色", round(top1, 3), f"≥{tmin}", top1 >= tmin, "",
            "" if top1 >= tmin else "背景色块分散")
    rep.add("A3", "背景亮度", round(p50, 1), f"{lmin}~{lmax}", lmin <= p50 <= lmax, "",
            "" if lmin <= p50 <= lmax else "偏纯白影棚或过暗")

    # A4 水印
    box = c("wm_box", [0.85, 0.92])
    r_box, r_up = watermark_ratio(im, float(box[0]), float(box[1]))
    dmax = float(c("wm_edge_delta_max", 0.02))
    hit = r_box > dmax and r_box > r_up * 2 + 0.005
    rep.add("A4", "水印残留", f"{r_box:.4f}/{r_up:.4f}", f"≤{dmax}",
            not hit, "", "" if not hit else "右下角近白笔画异常，疑似叠加水印")

    # B 软指标（不参与硬性判定，仅提示）
    hr, off_canvas = headroom(im)
    if off_canvas:
        rep.rows.append(dict(code="B2", name="头顶留白", value="N/A",
                             limit="—", ok=True, unit="",
                             note="特写/全景构图，头顶在画外，不适用"))
    else:
        hmin, hmax2 = float(c("headroom_min", 0.08)), float(c("headroom_max", 0.16))
        soft_ok = hmin <= hr <= hmax2
        rep.rows.append(dict(code="B2", name="头顶留白", value=round(hr, 3),
                             limit=f"{hmin}~{hmax2}", ok=soft_ok, unit="",
                             note="" if soft_ok else "观感偏空/偏挤（软指标，建议调整 ypos）"))

    # B3 左右人物尺度一致（仅全身档，避免"像两个人/两个机位"）
    if scene == "full":
        hl, hr = head_line(halves[0]), head_line(halves[1])
        hmax3 = float(c("height_match_max", 0.03))
        if hl is not None and hr is not None:
            diff = abs(hr - hl)
            ok3 = diff <= hmax3
            rep.rows.append(dict(code="B3", name="左右头顶线差", value=round(diff, 3),
                                 limit=f"≤{hmax3}", ok=ok3, unit="",
                                 note="" if ok3 else "两半人物上下错位或尺度不一致，像换了机位/距离"))
        else:
            rep.rows.append(dict(code="B3", name="左右头顶线差", value="N/A",
                                 limit="—", ok=True, unit="", note="检测不到头顶（浅发色/特写），仅提示"))
    return rep


def main(argv):
    as_json = "--json" in argv
    src = "--src" in argv
    scene = "full" if ("--scene" in argv and "full" in argv) else "closeup"
    files = [Path(a) for a in argv if not a.startswith("--") and a not in ("full", "closeup")]
    if not files:
        print(__doc__)
        return 0

    results, bad = [], 0
    for f in files:
        if not f.exists():
            print(f"跳过（不存在）: {f}")
            continue
        r = check(f, source=src, scene=scene)
        results.append(r)
        if not as_json:
            print(r.text())
            print()
        if r.hard_fail:
            bad += 1

    if as_json:
        print(json.dumps([{"file": str(r.path), "rows": r.rows,
                           "fail": [x["code"] for x in r.hard_fail]} for r in results],
                         ensure_ascii=False, indent=2))
    else:
        print(f"共 {len(results)} 张，不合格 {bad} 张")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
