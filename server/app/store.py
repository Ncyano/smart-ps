"""存储层：一切落在 data/ 下，目录即工程。

data/
  batches/<batch_id>/
    batch.json
    materials/     原始素材
    exemplars/     往期例图
    candidates/    引擎原始输出
    final/         回正后的成品
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
import uuid
from pathlib import Path

from .config import DATA_DIR

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def batch_dir(batch_id: str) -> Path:
    p = DATA_DIR / "batches" / batch_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_batch(batch: dict) -> None:
    p = batch_dir(batch["id"]) / "batch.json"
    p.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")


def get_batch(batch_id: str) -> dict | None:
    p = DATA_DIR / "batches" / batch_id / "batch.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_batches(limit: int = 50) -> list[dict]:
    root = DATA_DIR / "batches"
    if not root.exists():
        return []
    items: list[dict] = []
    for p in sorted(root.iterdir(), reverse=True)[:limit]:
        f = p / "batch.json"
        if f.exists():
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return items


def save_payload(data_url: str, name: str, dest: Path) -> Path:
    """把 data:image/...;base64,xxx 落成文件。"""
    dest.mkdir(parents=True, exist_ok=True)
    payload = data_url.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"图片解码失败 {name}: {exc}") from exc
    safe = _SAFE.sub("_", Path(name).name) or "image.png"
    target = dest / f"{uuid.uuid4().hex[:8]}_{safe}"
    target.write_bytes(raw)
    return target


def url_for(path: Path) -> str:
    """转成前端可访问的 URL。"""
    try:
        rel = path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return ""
    return "/files/" + rel.as_posix()
