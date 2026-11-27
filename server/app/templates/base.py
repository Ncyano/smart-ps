"""模板的数据结构与通用后处理契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image


@dataclass
class Template:
    id: str
    name: str
    desc: str
    version: int = 1
    # 这类图必须写死的提示词约束 / 必须避免的东西
    prompt_extra: list[str] = field(default_factory=list)
    negative_extra: list[str] = field(default_factory=list)
    # 建议的生成尺寸与默认导出规格
    gen_size: str = "2K"
    export: str = "detail_1080x1440"
    # 画布（后处理输出尺寸）。None = 不重排，保持模型输出尺寸
    canvas: tuple[int, int] | None = None
    # 是否会主动改画幅/主体占比（白底重排、卖点图裁切）。
    # 为 True 时保真判定要关掉"比例"那一项，否则必然误报变形。
    reframes: bool = False
    # 需要用户填的文案字段
    copy_fields: list[str] = field(default_factory=list)
    # 是否需要额外输入（before / after / scene / corners / evidence）。
    # 界面按这个列表显示对应的上传位，缺了由模板自己判 fail。
    inputs: list[str] = field(default_factory=list)
    # True = 不调生成模型，纯确定性合成（专利证据图、表格、成对对比）。
    # 这类物料本来就不该让模型画内容，命中的是"不编造数据"这条铁律。
    compose: bool = False
    # 确定性后处理：im + copy + 上下文 → (im, 版式报告)
    apply: Callable[[Image.Image, dict, dict], tuple[Image.Image, dict]] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "desc": self.desc,
            "version": self.version,
            "prompt_extra": self.prompt_extra,
            "negative_extra": self.negative_extra,
            "gen_size": self.gen_size,
            "export": self.export,
            "canvas": list(self.canvas) if self.canvas else None,
            "copy_fields": self.copy_fields,
            "inputs": self.inputs,
            "compose": self.compose,
            "reframes": self.reframes,
            "has_layout": self.apply is not None,
        }


@dataclass
class LayoutReport:
    """版式自检结果。宁可标红让人看，也不要悄悄溢出。"""

    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def fail(self, why: str) -> None:
        self.ok = False
        self.reasons.append(why)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": self.reasons, "stats": self.stats}
