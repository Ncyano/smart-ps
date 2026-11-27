"""Mock 引擎：没有 API Key 也能跑通整条产线。

它不是"假图"，而是本地确定性的基础修饰（对比/饱和/锐化三档）——
用来验证 选优 → 打包 → 生成 → 评分 → 回正 → 交付 的链路是否通，
也用来给真实引擎做对照基线。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from .base import Capabilities, Engine, EngineError, GenRequest, GenResult

# (对比, 饱和, 锐化强度)
_VARIANTS = [(1.06, 1.08, 1.0), (1.02, 1.04, 1.6), (1.10, 1.12, 0.6)]


class MockEngine(Engine):
    id = "mock"
    caps = Capabilities(max_px=2048 * 2048, refs=5, cost_cny=0.0, latency_p50=0.5, batch=3)

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    def available(self) -> tuple[bool, str]:
        return True, ""

    def generate(self, req: GenRequest, out_dir: Path) -> GenResult:
        content = next((r.path for r in req.refs if r.role == "content"), None)
        if content is None:
            raise EngineError("mock 引擎需要至少一张 role=content 的参考图")

        out_dir.mkdir(parents=True, exist_ok=True)
        base = Image.open(content).convert("RGB")
        if max(base.size) > 2048:
            base.thumbnail((2048, 2048), Image.LANCZOS)

        images: list[Path] = []
        for i in range(min(req.n, len(_VARIANTS))):
            contrast, color, sharpen = _VARIANTS[i]
            im = ImageEnhance.Contrast(base).enhance(contrast)
            im = ImageEnhance.Color(im).enhance(color)
            im = im.filter(
                ImageFilter.UnsharpMask(radius=1.2, percent=int(60 * sharpen), threshold=2)
            )
            p = out_dir / f"cand_{i + 1}.png"
            im.save(p)
            images.append(p)

        return GenResult(
            images=images,
            engine=self.id,
            cost_cny=0.0,
            meta={"note": "mock 引擎：本地确定性修饰，用于打通链路（未调用任何云 API）"},
        )
