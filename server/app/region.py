"""三类区域的事后核查：文字落在哪里、落的那块有多"忙"。

`task.zones_for()` 负责**声明**分区，这个模块负责在出图后量一次：
排版区那条带子上，底图本身的细节密度有多高。密度高说明文字要压在
产品标签/人脸/密集纹理上——要么读不清，要么挡了不能挡的东西。

## 为什么这一项只出提示、不进闸门

细节密度高不等于错。卖点图本来就常把文字压在虚化的背景上，照片虚化后
密度也不低；反过来，一张纯净渐变底上的白字也可能因为对比不足读不清。
拿它当硬门槛会大量误杀（实测阈值挪 20% 结论就翻），所以只作为
**给审核人看的一条证据**，写进制作记录，不参与可交付判定。

真正管"文字有没有挡主体"的是模板自己的版式自检（`text_bottom` 上限）。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 带内梯度强度超过整图中位数的多少倍，算"细节密集"
_BUSY_RATIO = 2.2


def _detail(im: Image.Image) -> np.ndarray:
    l = np.asarray(im.convert("L").resize((320, max(1, int(320 * im.height / im.width)))),
                   dtype=np.float32)
    gx = np.abs(np.diff(l, axis=1, prepend=l[:, :1]))
    gy = np.abs(np.diff(l, axis=0, prepend=l[:1, :]))
    return np.hypot(gx, gy)


def inspect(base: Image.Image, zones: dict) -> dict:
    """量排版区这条带子。`base` 必须是**排版之前**的底图。

    先排版再量没有意义：那时带子里最密的细节就是文字自己。
    """
    band = (zones.get("layout") or {}).get("band")
    if not band:
        return {"status": "skip", "detail": "这类物料没有固定排版带，不做分区核查"}
    try:
        h = base.height
        y0, y1 = int(h * float(band[0])), int(h * float(band[1]))
        y0, y1 = max(0, min(y0, h - 2)), max(y0 + 1, min(y1, h))
        d = _detail(base)
        band_d = d[y0:y1]
        if band_d.size < 16:
            return {"status": "skip", "detail": "排版带过窄，未量"}
        med = float(np.median(d))
        if med < 1e-3:
            return {"status": "skip", "detail": "底图过于平坦，密度比无意义"}
        ratio = float(band_d.mean() / med)
    except Exception as exc:  # noqa: BLE001 量不出来不该拖垮出图
        return {"status": "skip", "detail": f"分区核查失败：{exc}"}

    out = {"band": list(band), "detail_ratio": round(ratio, 2), "busy_cut": _BUSY_RATIO}
    if ratio >= _BUSY_RATIO:
        out.update(status="warn",
                   detail=f"排版带细节密度是整图的 {ratio:.1f} 倍：文字要压在密集处，"
                          f"可能读不清或挡住标签，建议换版面或加深色底")
    else:
        out.update(status="pass", detail=f"排版带细节密度 {ratio:.1f} 倍，文字区相对干净")
    return out


def check(regions: dict | None) -> dict:
    """给闸门/界面用的归一化结论。只做提示，不参与可交付判定。"""
    if not regions:
        return {"status": "skip", "detail": "没有分区核查记录"}
    return {"status": regions.get("status") or "skip",
            "detail": regions.get("detail") or ""}
