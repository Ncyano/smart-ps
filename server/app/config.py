"""配置层：唯一需要你手工填的地方。

优先级：config/config.toml > 内置默认值。
Web 界面的「设置」面板通过 POST /api/config 写回同一个文件。
"""

from __future__ import annotations

import copy
import threading
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_LOCK = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# ★ API 填入口（代码里的默认值；改这里或改 config/config.toml 都行）
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT: dict[str, Any] = {
    "app": {"host": "127.0.0.1", "port": 8765},
    "engine": {"default": "mock", "candidates": 3},
    "engines": {
        "mock": {"enabled": True},
        "seedream": {
            "enabled": False,
            "api_key": "",  # ← 火山方舟 API Key，填这里
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-seedream-5-0-flash-260915",
            "size": "2K",
            "watermark": False,
            "timeout": 180,
        },
    },
    "quality": {
        "min_fidelity": 0.90,
        "min_style": 0.60,
        "judge": "auto",
        "judge_model": "",
        "judge_max_side": 768,
    },
    # 前后对比效果图标准，见 docs/04-效果图制图标准.md
    # scripts/check_compare.py 读这一段做 A 级硬指标判定
    "compare": {
        "width": 1080,
        "height": 1350,
        "seam_px": 5,
        "jpeg_quality": 95,
        "tag_size": 40,
        "tag_stroke": 3,
        "day_size": 17,
        "day_stroke": 2,
        "legal_size": 13,
        "legal_margin_bottom": 26,
        "bg_probe_corner": 0.22,
        "bg_probe_top": 0.16,
        "bg_edge_max": 0.025,
        "bg_hf_max": 5.0,
        "bg_top1_min": 0.12,
        "bg_luma_min": 80.0,
        "bg_luma_max": 210.0,
        "bg_edge_max_full": 0.055,
        "bg_hf_max_full": 6.0,
        "bg_top1_min_full": 0.10,
        "headroom_min": 0.08,
        "headroom_max": 0.18,
        "height_match_max": 0.03,
        "wm_box": [0.85, 0.92],
        "wm_edge_delta_max": 0.02,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    with _LOCK:
        if not CONFIG_PATH.exists():
            return copy.deepcopy(DEFAULT)
        try:
            with CONFIG_PATH.open("rb") as f:
                user = tomllib.load(f)
        except Exception:
            return copy.deepcopy(DEFAULT)
    return _deep_merge(DEFAULT, user)


def save(patch: dict) -> dict:
    """把 patch 合并进当前配置并落盘，返回合并结果。"""
    merged = _deep_merge(load(), patch)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        CONFIG_PATH.write_text(_dump_toml(merged), encoding="utf-8")
    return merged


def masked(cfg: dict) -> dict:
    """给前端看的配置：绝不回传完整 Key。"""
    out = copy.deepcopy(cfg)
    sd = out.setdefault("engines", {}).setdefault("seedream", {})
    key = str(sd.get("api_key") or "")
    sd["api_key"] = ("*" * 12) if key else ""
    sd["api_key_set"] = bool(key)
    return out


# ─── 极简 TOML 写出（只覆盖本项目的 str/bool/int/float/list/nested 结构） ───


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(i) for i in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _dump_toml(data: dict) -> str:
    lines: list[str] = []
    for section, body in data.items():
        if not isinstance(body, dict):
            lines.append(f"{section} = {_fmt(body)}")
            continue
        simple = {k: v for k, v in body.items() if not isinstance(v, dict)}
        nested = {k: v for k, v in body.items() if isinstance(v, dict)}
        if simple:
            lines.append(f"[{section}]")
            lines.extend(f"{k} = {_fmt(v)}" for k, v in simple.items())
            lines.append("")
        for name, sub in nested.items():
            lines.append(f"[{section}.{name}]")
            lines.extend(f"{k} = {_fmt(v)}" for k, v in sub.items())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
