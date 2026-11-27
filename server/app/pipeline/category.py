"""品类一致性校验：防止把"每天一粒"的文案套到气垫霜罐上。

## 为什么必须单独有一层

`fidelity` 管"产品还在不在、比例歪没歪、糊没糊"，全是像素与几何。
`judge` 管"是不是同一款、文字有没有乱码"。
但这两层都**看不出文案和产品品类错位**——素材是气垫霜，文案写
"每天一粒 1000mg 辅酶Q10"，图做得再精致也是废稿，而且系统会照样判 A 可交付。

实测踩过：拿第三方品牌金盖气垫霜的图，配上口服保健品的文案，
四张候选全部"可交付"，因为它们确实长得都对，只是说的不是一件事。

## 品类表在 `taxonomy.py`

这里只做判定，不存词表。加品类改 `config/taxonomy.toml`。

## 四种结论，别混

| status | 含义 | 下游怎么处理 |
|---|---|---|
| `consistent` | 文案与素材同一品类 | 通过 |
| `conflict` | 确定不一致 | 生成前拦截（一分钱不花） |
| `unchecked` | 文案定了品类，但素材没看成 | **不算通过**，`gate.py` 标待人工 |
| `skipped` | 文案没有品类指向（纯场景图） | 通过（没有对照组就没有冲突） |

`unchecked` 单独一档是这次改的重点：旧版把"没校验"写进 reasons 就当没事了，
批次头一行小字没人看。没校验过的合规项，不能算过关。
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import asdict, dataclass, field

import httpx
from PIL import Image

from .. import taxonomy
from . import fidelity as fid
from . import judge

SKIN = "skincare"   # 兼容旧调用点
ORAL = "oral"
UNKNOWN = taxonomy.UNKNOWN

# "60 粒装 / 100粒" 这种也在说剂型，但写成"数字+粒"的词表列不全，用正则兜
_PILL_RE = re.compile(r"\d+\s*粒(?:装|瓶|盒)?")
_DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|毫克|μg|微克|iu|IU)")


class CategoryError(ValueError):
    """文案与素材品类冲突。带上可直接给用户看的中文说明。"""


@dataclass
class CategoryCheck:
    ok: bool = True
    status: str = "skipped"                # consistent / conflict / unchecked / skipped
    copy_category: str = UNKNOWN
    image_category: str = UNKNOWN
    source: str = ""                       # 判定依据：声明 / 词表 / 视觉模型 / 未校验
    copy_hits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    checked_image: bool = False            # 素材侧是否真的看过（花钱了没）
    model: str = ""
    unchecked_reason: str = ""             # status=unchecked 时必须写清为什么
    material: str = ""                     # 物料类型 id（来自模板）
    requires_source: bool = False           # 这类物料是否必须带真实数据来源
    labels: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def needs_manual(self) -> bool:
        return self.status == "unchecked"


def _hits(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if w and w.lower() in low]


def detect_copy(copy: dict | None, instruction: str = "",
                tax: taxonomy.Taxonomy | None = None) -> tuple[str, list[str], list[str]]:
    """从文案/指令里判品类。返回 (品类 id, 命中词, 冲突说明)。

    只用**强信号**定性：剂型名/使用动作 → 该品类；效果词一律不定性。
    两个品类同时被强信号命中 → 文案自身矛盾，返回 unknown + 说明。
    """
    tax = tax or taxonomy.load()
    parts: list[str] = []
    if copy:
        parts.append(str(copy.get("title") or ""))
        parts.append(str(copy.get("subtitle") or ""))
        parts.append(str(copy.get("tag") or ""))
        pts = copy.get("points") or []
        if isinstance(pts, str):
            pts = [pts]
        parts.extend(str(p) for p in pts)
    parts.append(str(instruction or ""))
    text = " ".join(parts)

    strong_hits: dict[str, list[str]] = {}
    weak_hits: dict[str, list[str]] = {}
    for p in tax.products:
        s = _hits(text, p.definite_words)
        if p.id == ORAL:
            s += [m.group(0) for m in _PILL_RE.finditer(text)]
        w = _hits(text, p.weak)
        # 有剂量数字 + 该品类的成分弱信号 → 升级为强信号（"辅酶Q10 1000mg"）
        if w and _DOSE_RE.search(text):
            s = s + [f"{x}+剂量" for x in w[:2]]
        if s:
            strong_hits[p.id] = s
        if w:
            weak_hits[p.id] = w

    if len(strong_hits) > 1:
        detail = "；".join(f"{tax.labels().get(k, k)}：{'/'.join(v[:3])}" for k, v in strong_hits.items())
        return UNKNOWN, [w for v in strong_hits.values() for w in v], [
            f"文案自身矛盾：同时出现多个品类的说法（{detail}），一句话说不清这是什么产品"
        ]
    if strong_hits:
        pid, hits = next(iter(strong_hits.items()))
        return pid, hits, []
    # 只剩弱信号 → 不下结论，命中词留着供人工参考
    return UNKNOWN, [w for v in weak_hits.values() for w in v], []


def _vision_prompt(tax: taxonomy.Taxonomy) -> str:
    lines = [
        "看图回答这个产品**属于哪一类**。只输出一个 JSON，不要解释：",
        '{"category": "<下面的 id 之一，或 other>", "form": "不超过10字描述形态", "reason": "不超过15字"}',
        "",
    ]
    for p in tax.products:
        lines.append(f'- {p.vision_key or p.id}：{p.label}——{p.form_hint or "按形态判断"}')
    lines.append('- other：不属于以上任何一类')
    lines.append("")
    lines.append("判据看**容器与内容物形态**，不要看包装上写的功效。")
    return "\n".join(lines)


def _data_url(im: Image.Image, max_side: int = 640) -> str:
    im = im.convert("RGB").copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def detect_image(im: Image.Image, cfg: dict) -> tuple[str, str, str]:
    """看素材图判品类。返回 (品类 id, 形态描述, 模型名)。失败一律 UNKNOWN，不阻断。"""
    ok, _why = judge.available(cfg)
    if not ok:
        return UNKNOWN, "", ""

    tax = taxonomy.load()
    model = judge._pick_model(cfg)  # noqa: SLF001 同一套模型选择逻辑，不重复实现
    eng = (cfg.get("engines", {}) or {}).get("seedream", {}) or {}
    key = str(eng.get("api_key") or "").strip()
    base = str(eng.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_url(fid.as_rgb(im))}},
            {"type": "text", "text": _vision_prompt(tax)},
        ]}],
        "temperature": 0,
        "max_tokens": 120,
    }
    try:
        with httpx.Client(timeout=float(eng.get("timeout") or 120)) as c:
            r = c.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"},
                       json=body)
        if r.status_code >= 400:
            return UNKNOWN, "", model
        choices = r.json().get("choices") or []
        text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return UNKNOWN, "", model
        raw = json.loads(m.group(0))
        cat = str(raw.get("category") or "").strip().lower()
        form = str(raw.get("form") or "")[:20]
        # 模型可能回 vision_key（cosmetic/edible），也可能回 id
        for p in tax.products:
            if cat in (p.id, p.vision_key):
                return p.id, form, model
        return UNKNOWN, form, model
    except Exception:  # noqa: BLE001 判定层挂了不能阻断出图
        return UNKNOWN, "", model


def check(copy: dict | None, source: Image.Image | None, cfg: dict,
          declared: str = "auto", instruction: str = "",
          template: str | None = None) -> CategoryCheck:
    """校验文案与素材是否同一品类。

    `declared`：用户在界面上声明的品类（auto 或注册表里的 id）。声明优先，
    因为它是最可靠的信号，还能省掉一次模型调用。
    `template`：工艺模板 id，用来查物料类型与必填数据（物料层在 claims.py 执行）。
    """
    tax = taxonomy.load()
    mat = tax.material_for_template(template)
    d = str(declared or "auto").strip().lower()
    declared_prod = tax.product(d)

    copy_cat, hits, conflict = detect_copy(copy, instruction, tax)
    res = CategoryCheck(
        copy_category=copy_cat, copy_hits=hits, reasons=list(conflict),
        material=mat.id if mat else "", requires_source=bool(mat and mat.requires_source),
        labels=tax.labels(),
    )

    # 文案自身就矛盾，后面的素材判定没意义了
    if copy_cat == UNKNOWN and conflict:
        res.ok = False
        res.status = "conflict"
        res.source = "词表"
        return res

    # 文案没指向任何品类（比如场景图根本没有文案）→ 不必看素材。
    # 没有对照物就不会有冲突，看了纯属白花一次模型调用。
    if copy_cat == UNKNOWN:
        res.status = "skipped"
        res.source = "文案未指向具体品类，跳过素材判定"
        return res

    # 素材侧：有声明用声明，否则看图
    if declared_prod is not None:
        res.image_category = declared_prod.id
        res.source = "用户声明"
    elif source is not None:
        img_cat, form, model = detect_image(source, cfg)
        res.image_category = img_cat
        res.checked_image = bool(model) and img_cat != UNKNOWN
        res.model = model
        res.source = "视觉模型" if res.checked_image else "未校验"
        if form:
            res.reasons.append(f"素材形态：{form}")

    cp = tax.product(copy_cat)
    ip = tax.product(res.image_category)
    cp_label = cp.label if cp else copy_cat
    ip_label = ip.label if ip else res.image_category

    if copy_cat != UNKNOWN and res.image_category != UNKNOWN and copy_cat != res.image_category:
        res.ok = False
        res.status = "conflict"
        res.reasons.insert(0,
            f"文案是「{cp_label}」口径（命中「{'、'.join(hits[:3])}」），"
            f"但素材是「{ip_label}」。{(cp.advice if cp else '')}")
    elif copy_cat != UNKNOWN and res.image_category == UNKNOWN:
        res.status = "unchecked"
        why = ("视觉模型未启用或调用失败" if not res.checked_image
               else "视觉模型把素材判为其它品类，注册表里没有对应项")
        res.unchecked_reason = (
            f"素材品类未校验（{why}）：文案说的是「{cp_label}」，"
            f"但系统没看出素材是什么。请声明品类，或人工确认后再标可交付"
        )
        res.reasons.append(res.unchecked_reason)
    else:
        res.status = "consistent"

    return res


def assert_consistent(copy: dict | None, source: Image.Image | None, cfg: dict,
                      declared: str = "auto", instruction: str = "",
                      template: str | None = None) -> CategoryCheck:
    """冲突就抛异常。放在生成之前调用——文案错了，后面每一张都是废的。

    注意：`unchecked` **不抛**。没校验过不该阻断出图（草稿仍有价值），
    但也不能算通过——由 `gate.py` 标成待人工。
    """
    res = check(copy, source, cfg, declared=declared, instruction=instruction, template=template)
    if not res.ok:
        raise CategoryError("；".join(res.reasons) or "文案与素材品类不一致")
    return res
