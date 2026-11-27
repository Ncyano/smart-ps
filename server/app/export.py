"""导出：把候选图按平台规格裁切输出。

平台尺寸一律用 **cover 裁切**：等比缩放到刚好盖满目标画布，再居中裁掉多余部分。
为什么不加白边（contain）：电商主图/详情页都不接受留白边，宁可裁掉边缘。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image

from .config import DATA_DIR
from . import store

EXPORT_DIR = DATA_DIR / "exports"

# 常用规格（后续从这里扩，前端直接列出来）
PRESETS: dict[str, tuple[int, int]] = {
    "original": (0, 0),          # 原尺寸
    "square_1080": (1080, 1080),  # 主图 1:1
    "detail_1080x1440": (1080, 1440),  # 详情页 3:4
    "story_1080x1920": (1080, 1920),   # 竖版 9:16
    "banner_1920x640": (1920, 640),    # 横版 banner
}


def _parse_size(spec: str | None) -> tuple[int, int] | None:
    """接受预设名或 "1080x1350" 这种字面量。返回 None 表示原尺寸。"""
    if not spec or spec in ("original", "原尺寸"):
        return None
    if spec in PRESETS:
        w, h = PRESETS[spec]
        return (w, h) if w and h else None
    if "x" in spec:
        try:
            w, h = (int(x) for x in spec.lower().split("x", 1))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    raise ValueError(f"无法识别的尺寸：{spec}（可用预设：{', '.join(PRESETS)}，或 1080x1350）")


def cover_fit(im: Image.Image, size: tuple[int, int] | None) -> Image.Image:
    """等比缩放到盖满目标，居中裁切。"""
    if not size:
        return im
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def export_batch(batch: dict, indexes: list[int], size: str | None = None,
                 fmt: str = "png", quality: int = 95) -> list[dict]:
    """把一个批次里选定的候选导出到 data/exports/<batch>/。返回文件清单。

    `size` 不传时用批次所属模板的默认规格——卖点图默认 `original`（已排版好，
    再裁会切字），白底主图默认 `square_1080`。
    """
    if not size:
        size = batch.get("template_export") or None
    target = _parse_size(size)
    cands = {c["index"]: c for c in batch.get("candidates", [])}
    picked = [cands[i] for i in sorted(set(indexes)) if i in cands]
    if not picked:
        raise ValueError("没有可导出的候选（index 不存在）")

    out_dir = EXPORT_DIR / batch["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for c in picked:
        src = DATA_DIR / c["final_url"].lstrip("/").removeprefix("files/")
        if not src.is_file():
            results.append({"index": c["index"], "error": "源图缺失"})
            continue

        im = Image.open(src).convert("RGB")
        src_size = im.size
        im = cover_fit(im, target)

        name = f"{batch['id']}_c{c['index']}_{target[0]}x{target[1]}.{fmt}" if target \
            else f"{batch['id']}_c{c['index']}_orig.{fmt}"
        path = out_dir / name
        if fmt.lower() in ("jpg", "jpeg"):
            im.save(path, quality=quality, subsampling=1)
        else:
            im.save(path)
        results.append(
            {
                "index": c["index"],
                "file": name,
                "url": "/files/exports/" + batch["id"] + "/" + name,
                "size": list(im.size),
                "source_size": list(src_size),
                "bytes": path.stat().st_size,
            }
        )

    # 导出记录写进批次目录，便于追溯
    log = out_dir / "export.json"
    prev = []
    if log.exists():
        try:
            prev = json.loads(log.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    prev.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "size": size or "original",
            "files": results,
        }
    )
    log.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    return results
