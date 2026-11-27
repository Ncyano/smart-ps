"""场景照平面检测与人工校准：线下大屏 / 灯箱 / 立牌贴图定位。

## 为什么不能全自动

贴屏这件事，工艺上只有一半是算法：

| 环节 | 能不能自动 | 说明 |
|---|---|---|
| 找屏幕四角 | 多数能，但**必须给置信度** | 玻璃反光、斜拍、部分遮挡都会让轮廓跑偏 |
| 透视校正 | 能 | 四角定了就是纯几何 |
| 亮度/反射/边缘融合 | 不能 | 需要人看"像不像贴上去的" |

知识库 `02-工艺路线.md` 里写得很清楚：透视对齐只是第一步，后面还有
亮度匹配、反光叠加、边缘融合三道。全自动的结果就是"位置对了，但一眼假"。

所以这里的设计是：**自动检测 + 强制人工确认**。
置信度不足（< `MIN_CONFIDENCE`）直接拒绝出图，把检测到的四角返回给界面，
让人拖一遍再提交。校准结果按场景图哈希存盘，下次同角度直接复用。

## 置信度怎么算

三项相乘，任何一项差都会把总分拉下来：

- `area`  面积占比：太小可能是误检（一张海报里的画框），太大可能是整张图
- `rect`  矩形度：`1 - |1 - 四边形面积 / 最小外接矩形面积|`，透视下允许变形但不该离谱
- `contrast` 边缘对比：屏幕内外的亮度差，太低说明根本没找到边界

写死 `MIN_CONFIDENCE = 0.55` 是拿 6 张真实场景照试出来的：正拍灯箱 0.82–0.94，
斜拍 0.61–0.78，玻璃反光严重 0.3–0.5（这几张机器判的角全是错的）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .config import DATA_DIR

SCENE_DIR = DATA_DIR / "scenes"
MIN_CONFIDENCE = 0.55


@dataclass
class Calibration:
    corners: list[list[int]]        # 左上、右上、右下、左下（图像坐标）
    confidence: float
    area_ratio: float
    rect_score: float
    contrast: float
    method: str                     # auto / manual / fallback
    needs_manual: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _order(pts: np.ndarray) -> list[list[int]]:
    """把四个点排成 左上、右上、右下、左下。"""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    tl = pts[int(np.argmin(s))]
    br = pts[int(np.argmax(s))]
    tr = pts[int(np.argmin(d))]
    bl = pts[int(np.argmax(d))]
    return [[int(round(p[0])), int(round(p[1]))] for p in (tl, tr, br, bl)]


def _quad_metrics(corners: list[list[int]], w: int, h: int, contrast: float) -> tuple[float, float, float]:
    p = np.asarray(corners, dtype=np.float64)
    x, y = p[:, 0], p[:, 1]
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    area_ratio = float(area / max(1.0, w * h))
    rw = float(x.max() - x.min())
    rh = float(y.max() - y.min())
    rect = float(np.clip(1.0 - abs(1.0 - area / max(1.0, rw * rh)), 0.0, 1.0))
    a = float(np.clip((area_ratio - 0.04) / 0.30, 0.0, 1.0))   # 占比 4% 以下不可信
    b = rect
    c = float(np.clip(contrast / 28.0, 0.0, 1.0))
    return round(a * b * c, 3), round(area_ratio, 3), round(rect, 3)


def detect(im: Image.Image) -> Calibration:
    """自动找屏幕四角。找不到就返回全图 + 低置信度，让界面转人工。"""
    rgb = im.convert("RGB")
    w, h = rgb.size
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return Calibration([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                           0.0, 1.0, 1.0, 0.0, "fallback", True)

    small = rgb.copy()
    small.thumbnail((900, 900), Image.LANCZOS)
    sw, sh = small.size
    g = np.asarray(small.convert("L"), dtype=np.uint8)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    edges = cv2.Canny(g, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best: tuple[float, list[list[int]], tuple[float, float, float]] | None = None
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:12]:
        peri = cv2.arcLength(c, True)
        for eps in (0.02, 0.03, 0.05):
            ap = cv2.approxPolyDP(c, eps * peri, True)
            if len(ap) != 4 or not cv2.isContourConvex(ap):
                continue
            pts = ap.reshape(-1, 2).astype(np.float64) * np.array([w / sw, h / sh])
            corners = _order(pts)
            # 整张图的外框也是一个"完美矩形"，它的矩形度是 1.0，很容易胜出。
            # 但四角围住 92% 以上整图的，不是屏幕，是把场景本身当成了贴图区。
            q_area = abs((corners[0][0] * corners[2][1] - corners[2][0] * corners[0][1])
                         + (corners[1][0] * corners[3][1] - corners[3][0] * corners[1][1])) / 2
            if q_area / float(w * h) > 0.92:
                continue
            # 边缘对比：四边形外侧一圈与内侧一圈的亮度差
            q = np.asarray(corners)
            x0, y0 = q.min(axis=0)
            x1, y1 = q.max(axis=0)
            pad = max(4, int(min(x1 - x0, y1 - y0) * 0.02))
            inner = np.asarray(rgb.crop((int(x0 + pad), int(y0 + pad), int(x1 - pad), int(y1 - pad)))
                               .convert("L").resize((32, 32)), dtype=np.float32)
            outer = np.asarray(rgb.crop((max(0, int(x0) - 3 * pad), max(0, int(y0) - 3 * pad),
                                         min(w, int(x1) + 3 * pad), min(h, int(y1) + 3 * pad)))
                               .convert("L").resize((32, 32)), dtype=np.float32)
            contrast = float(abs(inner.mean() - outer.mean()))
            conf, area_ratio, rect = _quad_metrics(corners, w, h, contrast)
            if best is None or conf > best[0]:
                best = (conf, corners, (area_ratio, rect, contrast))
            break
    if best is None:
        return Calibration([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                           0.0, 1.0, 1.0, 0.0, "fallback", True)
    conf, corners, (area_ratio, rect, contrast) = best
    return Calibration(corners, conf, area_ratio, rect, round(contrast, 1),
                       "auto", conf < MIN_CONFIDENCE)


def evaluate(corners: list[list[int]], im: Image.Image, method: str = "manual") -> Calibration:
    """人工给定四角后重算置信度（人工给定本身就算一种依据）。"""
    rgb = im.convert("RGB")
    w, h = rgb.size
    q = np.asarray(corners, dtype=np.float64)
    x0, y0 = q.min(axis=0)
    x1, y1 = q.max(axis=0)
    pad = max(4, int(min(x1 - x0, y1 - y0) * 0.02))
    inner = np.asarray(rgb.crop((int(x0 + pad), int(y0 + pad), int(x1 - pad), int(y1 - pad)))
                       .convert("L").resize((32, 32)), dtype=np.float32)
    outer = np.asarray(rgb.crop((max(0, int(x0) - 3 * pad), max(0, int(y0) - 3 * pad),
                                 min(w, int(x1) + 3 * pad), min(h, int(y1) + 3 * pad)))
                       .convert("L").resize((32, 32)), dtype=np.float32)
    contrast = float(abs(inner.mean() - outer.mean()))
    conf, area_ratio, rect = _quad_metrics(_order(q).copy(), w, h, contrast)
    return Calibration(_order(q), conf, area_ratio, rect, round(contrast, 1), method, False)


def scene_key(im: Image.Image) -> str:
    """按 64×64 灰度缩略图取哈希：同角度同场景能命中，不同场景不会撞。"""
    g = im.convert("L").resize((64, 64), Image.LANCZOS)
    return hashlib.sha1(np.asarray(g, dtype=np.uint8).tobytes()).hexdigest()[:16]


def save(im: Image.Image, cal: Calibration) -> str:
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    key = scene_key(im)
    (SCENE_DIR / f"{key}.json").write_text(
        json.dumps({"key": key, **cal.to_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return key


def load(im: Image.Image) -> Calibration | None:
    key = scene_key(im)
    p = SCENE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return Calibration(corners=d["corners"], confidence=float(d.get("confidence") or 0),
                           area_ratio=float(d.get("area_ratio") or 0),
                           rect_score=float(d.get("rect_score") or 0),
                           contrast=float(d.get("contrast") or 0),
                           method=str(d.get("method") or "saved"),
                           needs_manual=bool(d.get("needs_manual")))
    except Exception:  # noqa: BLE001 存档坏了就当没有，重新检测
        return None
