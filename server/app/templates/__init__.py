"""工艺模板：把 `knowledge/02-工艺路线.md` 里靠经验的做法固化成可选、可验收的配置。

## 为什么要有这一层

现在产线只有一种玩法：给素材 + 一句话 → 出场景图。但真实需求有 6 类路线
（场景 / img2img / 白底 / 排版 / 透视合成 / 证据类），每类的**提示词约束、画布、
后处理、导出规格**都不同。全塞进一句 instruction 里，等于让用户每次重新发明做法。

模板就是把这些差异写成配置：

| 字段 | 作用 |
|---|---|
| `prompt_extra` | 这类图必须写死的约束（如"纯白无缝背景、主体居中、悬浮无投影"） |
| `negative_extra` | 这类图必须避免的（如"不要文字、不要水印"） |
| `canvas` / `export` | 画布尺寸与默认导出规格 |
| `apply()` | 确定性后处理（本例：卖点图上方叠文案区） |
| `copy_fields` | 这个模板需要用户填哪些文案 |

**不做的事**：不用 AI 重绘主体、不生成文字、不编造数据（`02-工艺路线.md` 三条总原则）。
"""

from .base import Template
from .builtin import ALL as _BUILTIN
from .extra import EXTRA as _EXTRA

ALL: list[Template] = [*_BUILTIN, *_EXTRA]
_BY_ID = {t.id: t for t in ALL}


def get(tid: str | None) -> Template:
    return _BY_ID.get(str(tid or "scene").strip()) or _BY_ID["scene"]


__all__ = ["Template", "ALL", "get"]
