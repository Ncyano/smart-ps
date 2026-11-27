"""API 数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImagePayload(BaseModel):
    name: str = "image.png"
    data_url: str  # data:image/png;base64,xxx


class BatchRequest(BaseModel):
    # 不要叫 copy —— 会和 BaseModel.copy() 撞名（pydantic v2 会告警）
    model_config = ConfigDict(populate_by_name=True)

    instruction: str = ""
    engine: str | None = None
    candidates: int = Field(default=3, ge=1, le=6)
    materials: list[ImagePayload]
    exemplars: list[ImagePayload] = Field(default_factory=list)
    # 工艺模板：scene（默认）/ white_bg / kv，见 server/app/templates/
    template: str = "scene"
    # 文案：卖点图用 {title, subtitle, points[], tag}；其它模板忽略
    copy_text: dict | None = Field(default=None, alias="copy")
    # 产品品类：auto（默认，靠词表 + 视觉模型判）/ skincare / oral。
    # 显式声明最可靠，还能省掉一次模型调用。用于拦截"气垫霜配每天一粒"这类串台。
    category: str = "auto"
    # 素材授权：owned 自有 / licensed 已授权 / third_party 第三方未授权 /
    # sample 示例 / unknown 未声明（默认）。决定能否标"可正式交付"，见 rights.py
    rights: str = "unknown"


class ConfigPatch(BaseModel):
    app: dict | None = None
    engine: dict | None = None
    engines: dict | None = None
    quality: dict | None = None
