"""品类一致性校验：防止把"每天一粒"的文案套到气垫霜罐上。

## 为什么必须单独有一层

`fidelity` 管"产品还在不在、比例歪没歪、糊没糊"，全是像素与几何。
`judge` 管"是不是同一款、文字有没有乱码"。
但这两层都**看不出文案和产品品类错位**——素材是气垫霜，文案写
"每天一粒 1000mg 辅酶Q10"，图做得再精致也是废稿，而且系统会照样判 A 可交付。

实测踩过：拿 第三方品牌 金盖气垫霜的图，配上口服保健品的文案，
四张候选全部"可交付"，因为它们确实长得都对，只是说的不是一件事。

## 两层判定（便宜的先上）

| 层 | 成本 | 能抓什么 |
|---|---|---|
| 文案词表 | 0 | "每天一粒 / 软胶囊 / 吞服"这类口服铁证；以及文案自身矛盾 |
| 视觉模型看素材 | ≈0.001 元 | 素材是**涂抹类化妆品**还是**可食用产品** |

素材侧的品类是粗分类（罐装膏体 vs 胶囊/药片/口服液/粉剂），形态差异极大，
不像"比例"那种病态任务——这个判断视觉模型很稳。

## 位置：在生成之前

文案错了，后面每一张都是废的。所以校验放在 ③ 之后、④ 生成之前，
冲突直接抛 `CategoryError`，一分钱不花。这是"快速失败"最该在的地方。
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import asdict, dataclass, field

import httpx
from PIL import Image

from . import fidelity as fid
from . import judge

# 品类取值
SKIN = "skincare"   # 涂抹类化妆品：气垫、面霜、精华、乳液、面膜
ORAL = "oral"       # 可食用：胶囊、药片、口服液、粉剂、饮料
UNKNOWN = "unknown"

# ── 口服强信号：剂型名与服用动作。护肤品文案不可能出现这些 ──
_ORAL_STRONG = (
    "每天一粒", "每日一粒", "一天一粒", "每天两粒", "每日两粒",
    "每粒", "一粒", "两粒", "三粒", "粒装",
    "软胶囊", "胶囊", "吞服", "口服液", "口服", "冲服", "温水送服",
    "嚼服", "咀嚼片", "泡腾片", "含片", "舌下含", "滴剂", "冲剂", "颗粒剂",
    "随餐服用", "空腹服用", "饭后服用", "疗程装",
)
# "60 粒装 / 100粒" 这种也在说剂型，但写成"数字+粒"的词表列不全，用正则兜
_PILL_RE = re.compile(r"\d+\s*粒(?:装|瓶|盒)?")
# 口服成分弱信号：**不定性**，只在同时出现剂量数字时才升级为口服
_ORAL_WEAK = (
    "辅酶", "鱼油", "益生菌", "奶蓟草", "维生素", "钙片", "叶黄素",
    "胶原蛋白肽", "蛋白粉", "膳食纤维", "葡萄籽", "蜂胶", "褪黑素",
)
_DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|毫克|μg|微克|iu|IU)")

# ── 护肤强信号：分两类，都足以定性 ──
# ① 使用动作与妆效：食品文案不会这么写
_SKIN_ACT = (
    "上脸", "上妆", "妆效", "妆感", "遮瑕", "持妆", "不脱妆", "脱妆", "补妆",
    "卸妆", "薄涂", "点涂", "涂抹", "抹开", "拍开", "推开", "服帖", "卡粉",
    "奶油肌", "哑光", "水光", "雾面", "毛孔", "痘印", "红血丝",
    "肤感", "成膜", "贴肤", "浮粉", "斑驳", "底妆", "妆前",
)
# ② 明确品类名：说出品类名就等于定了品类
_SKIN_ITEM = (
    "面霜", "气垫", "粉底", "精华", "乳液", "面膜", "眼霜", "洁面",
    "爽肤水", "身体乳", "防晒霜", "隔离霜", "粉饼", "散粉", "唇膏", "口红",
)
# 效果词：护肤口服都能用（"保湿""提亮"），**一律不定性**，只作提示
_SKIN_WEAK = (
    "护肤", "保湿", "补水", "修护", "提亮", "抗皱", "紧致", "淡纹",
    "亮肤", "水乳", "控油",
)

_CATEGORY_LABEL = {SKIN: "涂抹类化妆品（护肤）", ORAL: "可食用产品（口服）", UNKNOWN: "未定"}


class CategoryError(ValueError):
    """文案与素材品类冲突。带上可直接给用户看的中文说明。"""


@dataclass
class CategoryCheck:
    ok: bool = True
    copy_category: str = UNKNOWN
    image_category: str = UNKNOWN
    source: str = ""                       # 判定依据：声明 / 词表 / 视觉模型 / 未校验
    copy_hits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    checked_image: bool = False            # 素材侧是否真的看过（花钱了没）
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [w for w in words if w.lower() in low]


def detect_copy(copy: dict | None, instruction: str = "") -> tuple[str, list[str], list[str]]:
    """从文案/指令里判品类。返回 (品类, 命中词, 冲突说明)。

    只用**强信号**定性：剂型名/服用动作 → 口服；使用动作/品类名 → 护肤。
    效果词（保湿/提亮/抗皱）两行都能用，**一律不定性**——
    否则"保湿补水"这一短语就能凑出 2 个命中，把中性文案误判成护肤。
    """
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

    oral_s = _hits(text, _ORAL_STRONG)
    oral_s += [m.group(0) for m in _PILL_RE.finditer(text)]
    skin_s = _hits(text, _SKIN_ACT + _SKIN_ITEM)
    oral_w = _hits(text, _ORAL_WEAK)
    skin_w = _hits(text, _SKIN_WEAK)

    # 有剂量数字 + 保健品成分 → 按口服处理（"辅酶Q10 1000mg"这种）
    if _DOSE_RE.search(text) and oral_w:
        oral_s = oral_s + [f"{w}+剂量" for w in oral_w[:2]]

    if oral_s and skin_s:
        return UNKNOWN, oral_s + skin_s, [
            f"文案自身矛盾：同时出现口服口径（{'/'.join(oral_s[:3])}）"
            f"和护肤口径（{'/'.join(skin_s[:3])}），一句话说不清这是什么产品"
        ]
    if oral_s:
        return ORAL, oral_s, []
    if skin_s:
        return SKIN, skin_s, []
    # 只剩弱信号 → 不下结论，命中词留着供人工参考
    return UNKNOWN, oral_w + skin_w, []


_IMG_PROMPT = """看图回答这个产品**属于哪一类**。只输出一个 JSON，不要解释：
{"category": "cosmetic" | "edible" | "other", "form": "不超过10字描述形态", "reason": "不超过15字"}

- cosmetic：涂抹在皮肤上的化妆品——气垫、面霜、粉底、精华、乳液、面膜、口红等（罐装、瓶装、软管装膏体/液体）
- edible：可以吃/喝的产品——胶囊、药片、软胶囊、口服液、粉剂、饮料、食品
- other：既不是化妆品也不是食品（器械、日用品、玩具等）

判据看**容器与内容物形态**：旋盖广口罐 + 膏体 = cosmetic；泡罩板/瓶装颗粒/安瓶口服液 = edible。"""


def _data_url(im: Image.Image, max_side: int = 640) -> str:
    im = im.convert("RGB").copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def detect_image(im: Image.Image, cfg: dict) -> tuple[str, str, str]:
    """看素材图判品类。返回 (品类, 形态描述, 模型名)。失败一律 UNKNOWN，不阻断。

    只问粗分类——容器形态（广口罐膏体 vs 泡罩板/口服液瓶）差异极大，
    这正是视觉模型稳的那类判断，不像量比例那种病态任务。
    """
    ok, _why = judge.available(cfg)
    if not ok:
        return UNKNOWN, "", ""

    model = judge._pick_model(cfg)  # noqa: SLF001 同一套模型选择逻辑，不重复实现
    eng = (cfg.get("engines", {}) or {}).get("seedream", {}) or {}
    key = str(eng.get("api_key") or "").strip()
    base = str(eng.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_url(fid.as_rgb(im))}},
            {"type": "text", "text": _IMG_PROMPT},
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
        cat = str(raw.get("category") or "").lower()
        form = str(raw.get("form") or "")[:20]
        if cat == "cosmetic":
            return SKIN, form, model
        if cat == "edible":
            return ORAL, form, model
        return UNKNOWN, form, model
    except Exception:  # noqa: BLE001 判定层挂了不能阻断出图
        return UNKNOWN, "", model


def check(copy: dict | None, source: Image.Image | None, cfg: dict,
          declared: str = "auto", instruction: str = "") -> CategoryCheck:
    """校验文案与素材是否同一品类。

    `declared`：用户在界面上声明的品类（auto / skincare / oral）。声明优先，
    因为它是最可靠的信号，还能省掉一次模型调用。
    """
    d = str(declared or "auto").strip().lower()
    declared_cat = {"skincare": SKIN, "skin": SKIN, "oral": ORAL, "edible": ORAL}.get(d, UNKNOWN)

    copy_cat, hits, conflict = detect_copy(copy, instruction)
    res = CategoryCheck(copy_category=copy_cat, copy_hits=hits, reasons=list(conflict))

    # 文案自身就矛盾，后面的素材判定没意义了
    if copy_cat == UNKNOWN and conflict:
        res.ok = False
        res.source = "词表"
        return res

    # 文案没指向任何品类（比如场景图根本没有文案）→ 不必看素材。
    # 没有对照物就不会有冲突，看了纯属白花一次模型调用。
    if copy_cat == UNKNOWN:
        res.source = "文案未指向具体品类，跳过素材判定"
        return res

    # 素材侧：有声明用声明，否则看图
    if declared_cat != UNKNOWN:
        res.image_category = declared_cat
        res.source = "用户声明"
    elif source is not None:
        img_cat, form, model = detect_image(source, cfg)
        res.image_category = img_cat
        res.checked_image = bool(model) and img_cat != UNKNOWN
        res.model = model
        res.source = "视觉模型" if res.checked_image else "未校验"
        if form:
            res.reasons.append(f"素材形态：{form}")

    # 只有两边都定了才判冲突
    if copy_cat != UNKNOWN and res.image_category != UNKNOWN and copy_cat != res.image_category:
        res.ok = False
        hit = "、".join(hits[:3])
        if copy_cat == ORAL:
            res.reasons.insert(0,
                f"文案是**口服口径**（命中「{hit}」），但素材是{_CATEGORY_LABEL[res.image_category]}。"
                f"气垫霜/面霜不能说「每天一粒」——要么换口服产品素材，要么把文案改成护肤卖点。")
        else:
            res.reasons.insert(0,
                f"文案是**护肤口径**（命中「{hit}」），但素材是{_CATEGORY_LABEL[res.image_category]}。"
                f"胶囊/口服液不能说「上脸、遮瑕、持妆」——要么换护肤品素材，要么改写成口服卖点。")
    elif copy_cat != UNKNOWN and res.image_category == UNKNOWN and not res.checked_image:
        res.reasons.append("素材品类未校验（视觉模型不可用），请自行确认文案与产品一致")

    return res


def assert_consistent(copy: dict | None, source: Image.Image | None, cfg: dict,
                      declared: str = "auto", instruction: str = "") -> CategoryCheck:
    """冲突就抛异常。放在生成之前调用——文案错了，后面每一张都是废的。"""
    res = check(copy, source, cfg, declared=declared, instruction=instruction)
    if not res.ok:
        raise CategoryError("；".join(res.reasons) or "文案与素材品类不一致")
    return res
