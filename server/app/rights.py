"""素材授权与交付范围：这张图到底能不能发给客户。

## 为什么要有这一层

仓库公开前做脱敏时发现：9 个文件里含真实竞品品牌与定价，全靠人工审查，
**系统零拦截**。一张含第三方商标的图走完产线，照样能点「导出」，
导出记录里也不会留下任何来源痕迹。

结果就是：图做得越像样，发错的风险越大。而这类错误**发出去就收不回**。

## 五个声明值 → 三级交付范围

| 声明 | 等级 | 能否正式交付 |
|---|---|---|
| `owned` 自有产品实拍 | ok | 是 |
| `licensed` 已获授权的第三方素材 | ok | 是（授权凭证自行归档） |
| `third_party` 第三方品牌素材，未授权 | internal | 否，仅内部参考 |
| `sample` 示例 / 演示素材 | internal | 否，仅内部参考 |
| `unknown` 未声明（默认） | unconfirmed | 未确认，导出时警告 |

## 一个刻意的取舍：不硬拦

`unknown` 不阻止导出，只在返回里给警告、在导出记录里标 `rights_unconfirmed`。
原因：拦了也拦不住——设计师随手选个「自有」就绕过去了，最后得到一堆假声明。
真正能起作用的是**声明之后的行为**：说了「第三方」，导出目录里就强制留一个
`_仅内部参考.txt`，谁转发这个文件夹都会看到。

所以这一层的价值不在"禁止"，在"留痕"。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

OWNED = "owned"
LICENSED = "licensed"
THIRD_PARTY = "third_party"
SAMPLE = "sample"
UNKNOWN = "unknown"

# 声明值 → (等级, 能否正式交付, 中文标签, 说明)
_TABLE: dict[str, tuple[str, bool | None, str, str]] = {
    OWNED: ("ok", True, "可正式交付", "自有产品实拍，文案数据由客户提供"),
    LICENSED: ("ok", True, "可正式交付", "第三方素材已获授权，授权凭证请自行归档"),
    THIRD_PARTY: ("internal", False, "仅内部参考", "含未授权的第三方品牌素材，不可对外投放"),
    SAMPLE: ("internal", False, "仅内部参考", "示例 / 演示素材，品牌与数字均为示例口径"),
    UNKNOWN: ("unconfirmed", None, "未声明", "未声明素材来源与授权状态"),
}

ALIASES = {
    "own": OWNED, "self": OWNED, "真实": OWNED, "自有": OWNED,
    "authorized": LICENSED, "授权": LICENSED,
    "third": THIRD_PARTY, "thirdparty": THIRD_PARTY, "第三方": THIRD_PARTY,
    "demo": SAMPLE, "示例": SAMPLE,
    "": UNKNOWN, "auto": UNKNOWN,
}

EXPORT_NOTICE = "_仅内部参考.txt"


@dataclass
class Rights:
    declared: str
    level: str            # ok / internal / unconfirmed
    can_deliver: bool | None
    label: str
    note: str
    invalid: bool = False   # 传了无法识别的值

    @property
    def internal_only(self) -> bool:
        return self.level == "internal"

    @property
    def warning(self) -> str:
        if self.level == "ok":
            return ""
        if self.internal_only:
            return f"仅内部参考：{self.note}。导出目录已写入 {EXPORT_NOTICE}，请勿用于对外投放。"
        return f"授权状态未确认：{self.note}。对外交付前请补充声明（owned / licensed / third_party / sample）。"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["warning"] = self.warning
        return d


def resolve(declared: str | None) -> Rights:
    """把用户声明转成交付范围。无法识别的值按 unknown 处理，但标记 invalid。"""
    raw = str(declared or "").strip().lower()
    key = ALIASES.get(raw, raw)
    invalid = False
    if key not in _TABLE:
        invalid = True
        key = UNKNOWN
    level, can, label, note = _TABLE[key]
    return Rights(declared=raw or UNKNOWN, level=level, can_deliver=can,
                  label=label, note=note, invalid=invalid)


def for_batch(batch: dict) -> Rights:
    """从 batch.json 读声明。老批次没有这个字段 → 按 unknown。"""
    r = batch.get("rights")
    if isinstance(r, dict):
        return resolve(r.get("declared"))
    return resolve(None)


def gate(batch: dict) -> dict:
    """导出前的闸门结果。调用方据此决定是否写内部标记、是否带警告。"""
    r = for_batch(batch)
    return {
        "declared": r.declared,
        "level": r.level,
        "can_deliver": r.can_deliver,
        "label": r.label,
        "note": r.note,
        "warning": r.warning,
        "internal_only": r.internal_only,
        "notice_file": EXPORT_NOTICE if r.internal_only else "",
    }


def notice_text(batch: dict, rights: Rights) -> str:
    """写进导出目录的说明文件。给人看，所以用完整句子。"""
    return (
        "【内部参考物料 · 不可对外投放】\n"
        "\n"
        f"批次：{batch.get('id', '')}\n"
        f"时间：{batch.get('created_at', '')}\n"
        f"原因：{rights.note}\n"
        f"声明：{rights.declared}（等级 {rights.level}）\n"
        "\n"
        "处理建议：\n"
        "1. 如需对外投放，请替换为自有产品素材，或取得品牌方书面授权后\n"
        "   重新声明为 licensed 并重新生成。\n"
        "2. 图内价格、含量、专利号、检测数据若为示例口径，必须换成真实材料。\n"
        "3. 本说明由系统在导出时自动写入，删除它不会改变物料的授权状态。\n"
    )
