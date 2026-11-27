"""参考图打包与提示词组装 —— 设计文档里最容易被忽略、却最影响保真的一步。"""

from __future__ import annotations

from pathlib import Path

from ..engines.base import RefImage
from . import steps

MAX_CONTENT = 3
MAX_STYLE = 2

_AVOID = [
    "改变产品形状或比例",
    "改写画面中的文字与 Logo",
    "添加与原场景无关的道具",
    "过度磨皮或塑料质感",
    "照搬例图的内容与构图",
]


def score_material(path: Path, name: str = "") -> dict:
    """素材打分：清晰度 + 曝光 + 尺寸。分数只用于排序，不追求绝对准确。"""
    im = steps._open_limited(path, 1024)  # noqa: SLF001
    hf = steps.high_freq_energy(im)
    l = steps.luma(im)
    exposure = float(l.mean() / 255.0)
    sharpness = float(min(hf / 12.0, 1.0))
    # 曝光过亮/过暗扣分，中间调最优
    exposure_ok = 1.0 - min(abs(exposure - 0.55) * 2.0, 1.0)
    quality = 0.7 * sharpness + 0.3 * exposure_ok
    return {
        "name": name or path.name,
        "path": str(path),
        "width": im.width,
        "height": im.height,
        "sharpness": round(sharpness, 3),
        "exposure": round(exposure, 3),
        "quality": round(float(quality), 3),
    }


def pack_refs(
    scored_materials: list[dict],
    exemplar_paths: list[Path],
    profile: dict | None = None,
) -> tuple[list[RefImage], list[dict]]:
    """内容在前、风格在后。返回 (RefImage 列表, 供前端展示的打包说明)。"""
    refs: list[RefImage] = []
    notes: list[dict] = []

    contents = scored_materials[:MAX_CONTENT]
    for i, m in enumerate(contents):
        label = "产品主体，形状/Logo/材质/比例需 1:1 保留" if i == 0 else "辅助角度，只用于补全不可见部分"
        refs.append(RefImage(path=Path(m["path"]), role="content", label=label))
        notes.append({"role": "content", "label": label, "file": m["name"]})

    # 风格图只用少量、且明确禁止照搬内容
    for p in exemplar_paths[:MAX_STYLE]:
        label = "仅参考色调、影调、光线质感与背景处理，禁止照搬内容"
        refs.append(RefImage(path=Path(p), role="style", label=label))
        notes.append({"role": "style", "label": label, "file": Path(p).name})

    return refs, notes


def build_prompt(instruction: str, profile: dict) -> tuple[str, str]:
    """把用户的话 + 风格目标拼成引擎 prompt。"""
    hints: list[str] = []
    comp = profile.get("composition", {})
    if comp.get("bg") == "pure_white":
        hints.append("背景处理为纯白，主体边缘干净")
    tone = profile.get("tone", {})
    if tone.get("p50", 128) > 210:
        hints.append("整体明亮通透，高光柔和")
    elif tone.get("p50", 128) < 90:
        hints.append("整体偏暗调，保留暗部层次")
    if profile.get("color", {}).get("sat", 0.2) < 0.15:
        hints.append("低饱和处理，色彩克制")

    body = (instruction or "按往期成品的风格整理这张素材").strip()
    if hints:
        body += "\n风格要求：" + "；".join(hints)
    return body, "，".join(_AVOID)
