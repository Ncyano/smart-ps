"""API 数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImagePayload(BaseModel):
    name: str = "image.png"
    data_url: str  # data:image/png;base64,xxx


class Claim(BaseModel):
    """一条数据来源声明：某段原文（价格/专利号/含量）出自哪里。"""

    text: str
    kind: str = ""      # price / patent / report / dose / percent，留空表示不限
    source: str = ""    # 价目表版本、证书文件名、报告编号——人看得懂就行


class BatchRequest(BaseModel):
    # 不要叫 copy —— 会和 BaseModel.copy() 撞名（pydantic v2 会告警）
    model_config = ConfigDict(populate_by_name=True)

    instruction: str = ""
    engine: str | None = None
    candidates: int = Field(default=3, ge=1, le=6)
    materials: list[ImagePayload]
    exemplars: list[ImagePayload] = Field(default_factory=list)
    # 工艺模板：scene（默认）/ white_bg / kv / price / patent / table / pair / screen
    template: str = "scene"
    # 文案：卖点图用 {title, subtitle, points[], tag}；其它模板按 copy_fields
    copy_text: dict | None = Field(default=None, alias="copy")
    # 产品品类：auto（默认，靠注册表 + 视觉模型判）或 config/taxonomy.toml 里的 id
    category: str = "auto"
    # 素材授权：owned 自有 / licensed 已授权 / third_party 第三方未授权 /
    # sample 示例 / unknown 未声明（默认）。决定能否标"可正式交付"，见 rights.py
    rights: str = "unknown"
    # 数据来源声明：价格、含量、专利号的出处。缺了就是"待人工"，见 claims.py
    claims: list[Claim] = Field(default_factory=list)
    # 成对输入（pair 模板）：使用前 / 使用后
    before: ImagePayload | None = None
    after: ImagePayload | None = None
    # 贴屏（screen 模板）：真实场景照 + 人工校准的四角（TL, TR, BR, BL）
    scene: ImagePayload | None = None
    corners: list[list[int]] | None = None
    # 证据图（patent 模板）：原件扫描件，必填
    evidence: ImagePayload | None = None
    # 幂等键：同一 request_id 重复提交只跑一次，不重复付费
    request_id: str = ""


class ConfigPatch(BaseModel):
    app: dict | None = None
    engine: dict | None = None
    engines: dict | None = None
    quality: dict | None = None
