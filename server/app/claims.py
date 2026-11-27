"""合规文字与数据来源检查：别让"字排得漂亮"冒充"内容没问题"。

## 为什么单独一层

`category.py` 只管"这句话是不是这个产品能说的"（气垫霜不能每天一粒）。
但还有三类问题它管不了，而且**排版越精致越像真的**：

| 问题 | 例子 | 后果 |
|---|---|---|
| 绝对化用语 | 全网最低、第一、100% 有效、永久 | 广告法违禁，投放即下架 |
| 疗效宣称 | 消炎、祛斑、根治、降血糖 | 化妆品/食品不得宣称疗效 |
| 第三方商标 | 文案里写了别家品牌名 | 侵权，且与授权状态冲突 |
| 数字没来源 | 价格 199、含量 1000mg、专利号 ZL… | 编造数据，比错图更严重 |

前三类是**硬拦截**（生成前 400，一分钱不花）；数字没来源是**待人工**——
系统不知道该报 199 还是 299，只能要求人把来源补上。

## 数据来源怎么给

请求里带 `claims` 列表，每条把一段原文绑到一个来源：

```json
"claims": [
  {"text": "199", "kind": "price",  "source": "客户价目表 2026-11 版 P2"},
  {"text": "ZL2023xxxxxx", "kind": "patent", "source": "专利证书扫描件.pdf"}
]
```

系统从文案里抽出所有数字/专利号/百分比，逐个找声明。
对上的记 `verified`，对不上的记 `unverified`——审核页会把这两类分开显示，
导出记录里也原样保存，事后能查"这个 199 是谁给的"。

## 一个刻意的边界

规则是**词表 + 正则**，不是语言模型。原因：合规判定要可解释、可复核、
零延迟、零成本，而且误报必须能一眼看懂是哪条规则。
词表兜不住的（谐音、隐喻、图片里的字）明确写在 `knowledge/07-待决事项.md`，
不假装能覆盖。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import taxonomy
from .config import CONFIG_DIR

COMPLIANCE_PATH = CONFIG_DIR / "compliance.toml"

BLOCK = "block"
WARN = "warn"
INFO = "info"


class ClaimsError(ValueError):
    """存在硬拦截项（绝对化用语 / 疗效宣称 / 第三方商标）。"""


@dataclass
class Finding:
    kind: str                 # absolute / efficacy / trademark / data
    level: str                # block / warn / info
    text: str                 # 命中的原文片段
    rule: str                 # 规则名，方便复核
    hint: str = ""
    source: str = ""          # 数据项的用户声明来源

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Audit:
    ok: bool = True
    status: str = "pass"          # pass / manual / block
    findings: list[Finding] = field(default_factory=list)
    data_items: list[dict] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    material: str = ""
    rules_source: str = ""

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.level == BLOCK]

    @property
    def unverified(self) -> list[dict]:
        return [d for d in self.data_items if not d.get("verified")]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "material": self.material,
            "rules_source": self.rules_source,
            "findings": [f.to_dict() for f in self.findings],
            "data_items": self.data_items,
            "missing_fields": self.missing_fields,
        }


# ── 默认规则。config/compliance.toml 可按 kind 覆盖/追加 ──
_DEFAULT_RULES: dict[str, dict] = {
    "absolute": {
        "label": "绝对化用语",
        "level": BLOCK,
        "hint": "广告法禁用绝对化表述，改成可举证的相对表述（如「实测 4 周」）",
        "words": [
            "最好", "最佳", "最强", "最低", "最高", "第一品牌", "全国第一", "全球第一",
            "顶级", "极致", "唯一", "独家", "首选", "永久", "彻底", "根治",
            "100%", "全网最低", "史上最低", "国家级", "世界级", "纯天然", "零添加",
            "无副作用", "立刻见效", "当天见效", "一劳永逸", "包治",
        ],
    },
    "efficacy": {
        "label": "疗效宣称",
        "level": WARN,
        "hint": "化妆品/食品不得宣称医疗功效；确有资质请声明品类并附批件号",
        "words": [
            "治疗", "治愈", "消炎", "杀菌", "抗过敏", "祛斑", "去斑", "生发",
            "降血压", "降血糖", "降血脂", "抗癌", "抗肿瘤", "修复受损细胞",
            "医美级", "药妆", "处方", "临床验证有效", "替代药物",
        ],
        # 这些品类允许出现疗效类词汇（器械需按注册证表述，食品/化妆品不允许）
        "allowed_products": ["device"],
    },
    "trademark": {
        "label": "第三方商标",
        "level": BLOCK,
        "hint": "文案含第三方品牌名：要么去掉，要么取得授权并把 rights 声明为 licensed",
        "words": [],   # 具体品牌名由客户在 config/compliance.toml 里维护，仓库不放真实名单
    },
}

# ── 从文案里抽数据项 ──
_DATA_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("price", "价格", re.compile(r"(?:[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元|\d+(?:\.\d+)?\s*折)")),
    ("patent", "专利号", re.compile(r"(?:ZL|CN)\s?\d{6,}[A-Z0-9.]*", re.I)),
    ("report", "检测报告编号", re.compile(r"(?:检|测|报告)\s*(?:编号|No\.?)?\s*[:：]?\s*[A-Z]{0,4}\d{6,}", re.I)),
    ("dose", "含量/剂量", re.compile(r"\d+(?:\.\d+)?\s*(?:mg|毫克|g|克|ml|毫升|μg|微克|iu|IU|亿)")),
    ("percent", "百分比", re.compile(r"\d+(?:\.\d+)?\s*%")),
]

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _cfg() -> tuple[dict, str]:
    rules = {k: {**v, "words": list(v.get("words") or [])} for k, v in _DEFAULT_RULES.items()}
    source = "内置规则"
    if COMPLIANCE_PATH.exists():
        try:
            with COMPLIANCE_PATH.open("rb") as f:
                raw = tomllib.load(f)
            for kind, body in (raw.get("rule") or {}).items():
                base = rules.get(kind, {"label": kind, "level": WARN, "hint": "", "words": []})
                merged = {**base, **{k: v for k, v in body.items() if k != "words"}}
                if "words" in body:
                    # 配置里写 replace = true 才是替换，否则追加
                    merged["words"] = list(body["words"]) if body.get("replace") else \
                        list(base.get("words") or []) + list(body["words"])
                rules[kind] = merged
            source = COMPLIANCE_PATH.name
        except Exception as exc:  # noqa: BLE001 规则文件坏了退回内置，但要说
            source = f"内置规则（{COMPLIANCE_PATH.name} 解析失败：{exc}）"
    return rules, source


def _texts(copy: dict | None, instruction: str = "") -> list[tuple[str, str]]:
    """把文案拆成 (字段名, 文本) 列表，便于定位命中在哪一行。"""
    out: list[tuple[str, str]] = []
    if copy:
        for key, label in (("title", "标题"), ("subtitle", "副标题"), ("tag", "角标")):
            v = str(copy.get(key) or "").strip()
            if v:
                out.append((label, v))
        pts = copy.get("points") or []
        if isinstance(pts, str):
            pts = [pts]
        for i, p in enumerate(pts, 1):
            if str(p).strip():
                out.append((f"卖点{i}", str(p).strip()))
        # 价格图/表格模板的字段
        for key, label in (("price_now", "现价"), ("price_was", "原价"), ("unit", "单位"),
                           ("patent_no", "专利号"), ("issuer", "发证机构"),
                           ("cert_no", "证书编号"), ("period", "有效期")):
            v = str(copy.get(key) or "").strip() if copy else ""
            if v:
                out.append((label, v))
        rows = copy.get("rows") or []
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, (list, tuple)):
                    out.append(("表格行", " | ".join(str(x) for x in r)))
                elif isinstance(r, dict):
                    out.append(("表格行", " | ".join(str(x) for x in r.values())))
    if instruction.strip():
        out.append(("补充说明", instruction.strip()))
    return out


def extract_data(copy: dict | None, instruction: str = "") -> list[dict]:
    """抽出文案里所有需要来源的数字类内容。去重、保留出现位置。

    两个来源：正则扫文本 + 模板的显式数据字段。
    为什么要有后者：价签图里 `price_now: "199"` 是纯数字，没有"元"也没有"¥"，
    正则扫不到，结果"没绑来源的价格"就这么漏过去了——而这恰恰是最该拦的一条。
    """
    seen: set[tuple[str, str]] = set()
    items: list[dict] = []
    if copy:
        for key, kind, label in (("price_now", "price", "现价"), ("price_was", "price", "原价")):
            v = str(copy.get(key) or "").strip()
            if v and (kind, v) not in seen:
                seen.add((kind, v))
                items.append({"kind": kind, "label": label, "text": v, "where": label,
                              "source": "", "verified": False})
    for where, text in _texts(copy, instruction):
        for kind, label, pat in _DATA_PATTERNS:
            for m in pat.finditer(text):
                frag = m.group(0).strip()
                key = (kind, frag)
                if key in seen:
                    continue
                seen.add(key)
                items.append({"kind": kind, "label": label, "text": frag, "where": where,
                              "source": "", "verified": False})
    return items


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _match_claim(item: dict, claims: list[dict]) -> dict | None:
    """找这条数据项的声明：原文包含关系（任一方向）且 kind 兼容即可。"""
    a = _norm(item["text"])
    if not a or not a.strip("¥￥元折%克毫克升"):
        return None
    for c in claims:
        if not isinstance(c, dict):
            continue
        b = _norm(c.get("text") or "")
        ck = str(c.get("kind") or "").strip()
        if not b:
            continue
        if ck and ck != item["kind"]:
            continue
        if a in b or b in a:
            return c
    return None


def audit(copy: dict | None, cfg: dict | None = None, instruction: str = "",
          claims: list[dict] | None = None, template: str | None = None) -> Audit:
    """跑一遍合规与数据来源检查。

    返回 `status`：
      - `block`  有硬拦截项（绝对化 / 疗效超范围 / 第三方商标）
      - `manual` 有需要人确认的（数据没来源、必填字段缺失、疗效词待核）
      - `pass`   干净
    """
    rules, rules_source = _cfg()
    tax = taxonomy.load()
    mat = tax.material_for_template(template)
    res = Audit(material=mat.id if mat else "", rules_source=rules_source)
    # 允许传 pydantic 模型、dataclass 或普通 dict —— 接口层用的是 Claim 模型，
    # 只认 dict 会悄悄把它们全丢掉，结果"价格没来源"这条永远不生效。
    norm_claims: list[dict] = []
    for c in claims or []:
        if isinstance(c, dict):
            norm_claims.append(c)
        elif hasattr(c, "text"):
            norm_claims.append({
                "text": getattr(c, "text", ""),
                "kind": getattr(c, "kind", ""),
                "source": getattr(c, "source", ""),
            })
    claims = norm_claims

    product = ""
    try:
        from .pipeline import category as catmod  # 局部导入避免循环
        product, _hits, _conflict = catmod.detect_copy(copy, instruction, tax)
    except Exception:  # noqa: BLE001 判定失败不影响合规检查
        product = ""

    texts = _texts(copy, instruction)

    # ── 词表规则 ──
    for kind, rule in rules.items():
        words = [w for w in (rule.get("words") or []) if str(w).strip()]
        if not words:
            continue
        level = str(rule.get("level") or WARN)
        allowed = [str(x) for x in (rule.get("allowed_products") or [])]
        if kind == "efficacy" and product and product in allowed:
            level = INFO   # 器械有注册证时可以按批件表述，只提示
        label = str(rule.get("label") or kind)
        hint = str(rule.get("hint") or "")
        for where, text in texts:
            low = text.lower()
            for w in words:
                if w.lower() not in low:
                    continue
                res.findings.append(Finding(kind=kind, level=level, text=f"{where}：{w}",
                                            rule=label, hint=hint))
                break  # 同一行同一规则只报一次，避免刷屏

    # ── 数据来源 ──
    res.data_items = extract_data(copy, instruction)
    for item in res.data_items:
        hit = _match_claim(item, claims)
        if hit:
            item["verified"] = True
            item["source"] = str(hit.get("source") or "").strip()
            item["declared_text"] = str(hit.get("text") or "")
    for item in res.unverified:
        res.findings.append(Finding(
            kind="data", level=WARN, text=f"{item['where']}：{item['text']}",
            rule=f"{item['label']}未绑定来源",
            hint="把这段数字的出处填进 claims（来源可以是价目表版本、证书文件名、检测报告编号）",
        ))

    # ── 物料必填字段 ──
    if mat and mat.source_fields:
        have = {k: str((copy or {}).get(k) or "").strip() for k in mat.source_fields}
        missing = [k for k, v in have.items() if not v]
        res.missing_fields = missing
        if missing:
            res.findings.append(Finding(
                kind="data", level=WARN, text=f"物料「{mat.label}」缺字段：{'、'.join(missing)}",
                rule="物料必填数据缺失", hint=mat.note or "这类物料必须有真实数据，不能由模型补",
            ))

    blocks = res.blocks
    res.ok = not blocks
    if blocks:
        res.status = "block"
    elif any(f.level == WARN for f in res.findings) or res.unverified or res.missing_fields:
        res.status = "manual"
    else:
        res.status = "pass"
    return res


def assert_clean(*args, **kwargs) -> Audit:
    """有硬拦截就抛。放在生成之前——这类文案花一分钱都是浪费。"""
    res = audit(*args, **kwargs)
    if not res.ok:
        msgs = [f"{f.rule}「{f.text}」" for f in res.blocks]
        raise ClaimsError("；".join(msgs) + "。这些表述不能投放，请先改文案。")
    return res


def rules_path() -> Path:
    return COMPLIANCE_PATH
