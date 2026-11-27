"""品类注册表：把"产品类别"和"物料类型"分开，并且可配置。

## 为什么要拆

旧版 `category.py` 只有两档（护肤 / 口服），是拿真实错误反推出来的：
气垫霜配了"每天一粒"。但项目素材还有器械、服装、门店价格、专利证据、
竞品对比、线下大屏——这些既不是护肤也不是口服，词表一扩再扩就会变成
一坨互斥字符串，谁也不敢改。

拆成两张表之后，两件事各归各位：

| 表 | 回答的问题 | 用在哪 |
|---|---|---|
| `product[]` | 文案说的动作，这个产品做得到吗 | 生成前拦截（`category.py`） |
| `material[]` | 这张图属于哪类物料、必须有哪些真实数据 | 生成前补数据（`claims.py`）、选模板 |

## 三条取舍

1. **弱信号不定性。** "保湿""提亮"两行都能用，只记命中不作判定。
   否则"保湿补水"四个字就能把中性文案判成护肤。
2. **没校验 ≠ 通过。** 旧版视觉模型不可用时返回"素材品类未校验"就接着跑，
   批次头一行小字，等于静默放行。现在 `CategoryCheck.status` 有独立的
   `unchecked`，由 `gate.py` 当成"待人工"，不能算过关。
3. **未知品类不猜。** 注册表里没有的品类（宠物粮、玩具）返回 `unknown` +
   原因，让人补配置，而不是拿现有词表硬套。

## 怎么扩

改 `config/taxonomy.toml`，不用动代码。加一个产品类别 = 加一段 `[[product]]`；
加一种物料 = 加一段 `[[material]]`。字段见文件内注释。
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import CONFIG_DIR

TAXONOMY_PATH = CONFIG_DIR / "taxonomy.toml"

UNKNOWN = "unknown"


@dataclass
class Product:
    id: str
    label: str
    # 使用动作/剂型这类强信号，命中即定性
    strong: list[str] = field(default_factory=list)
    # 明确品类名，说出名字就定性
    items: list[str] = field(default_factory=list)
    # 效果词，两行都能用，只记不判
    weak: list[str] = field(default_factory=list)
    # 给视觉模型看的形态判据（一句话）
    form_hint: str = ""
    # 冲突时给用户的改法建议
    advice: str = ""
    # 视觉模型返回的归类名（兼容旧 prompt 的 cosmetic / edible）
    vision_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def definite_words(self) -> list[str]:
        return [*self.strong, *self.items]


@dataclass
class Material:
    id: str
    label: str
    template: str = "scene"
    # 这类物料是否**必须**有真实数据来源（价格、专利号、检测值）
    requires_source: bool = False
    # 必填的文案/数据字段名
    source_fields: list[str] = field(default_factory=list)
    # 这类图能不能由生成模型补内容
    allow_generated_text: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── 内置默认。config/taxonomy.toml 存在时按 id 覆盖/追加 ──
_DEFAULT_PRODUCTS: list[Product] = [
    Product(
        id="skincare",
        label="涂抹类化妆品（护肤）",
        strong=[
            "上脸", "上妆", "妆效", "妆感", "遮瑕", "持妆", "不脱妆", "脱妆", "补妆",
            "卸妆", "薄涂", "点涂", "涂抹", "抹开", "拍开", "推开", "服帖", "卡粉",
            "奶油肌", "哑光", "水光", "雾面", "毛孔", "痘印", "红血丝",
            "肤感", "成膜", "贴肤", "浮粉", "斑驳", "底妆", "妆前",
        ],
        items=[
            "面霜", "气垫", "粉底", "精华", "乳液", "面膜", "眼霜", "洁面",
            "爽肤水", "身体乳", "防晒霜", "隔离霜", "粉饼", "散粉", "唇膏", "口红",
        ],
        weak=["护肤", "保湿", "补水", "修护", "提亮", "抗皱", "紧致", "淡纹", "亮肤", "水乳", "控油"],
        form_hint="旋盖广口罐 / 软管 / 泵瓶，内容物是膏体或液体",
        advice="要么换口服产品素材，要么把文案改成护肤卖点",
        vision_key="cosmetic",
    ),
    Product(
        id="oral",
        label="可食用产品（口服）",
        strong=[
            "每天一粒", "每日一粒", "一天一粒", "每天两粒", "每日两粒",
            "每粒", "一粒", "两粒", "三粒", "粒装",
            "软胶囊", "胶囊", "吞服", "口服液", "口服", "冲服", "温水送服",
            "嚼服", "咀嚼片", "泡腾片", "含片", "舌下含", "滴剂", "冲剂", "颗粒剂",
            "随餐服用", "空腹服用", "饭后服用", "疗程装",
        ],
        items=["保健食品", "膳食补充剂", "固体饮料"],
        weak=[
            "辅酶", "鱼油", "益生菌", "奶蓟草", "维生素", "钙片", "叶黄素",
            "胶原蛋白肽", "蛋白粉", "膳食纤维", "葡萄籽", "蜂胶", "褪黑素",
        ],
        form_hint="泡罩板 / 瓶装颗粒 / 安瓶口服液 / 粉剂袋",
        advice="要么换护肤品素材，要么改写成口服卖点",
        vision_key="edible",
    ),
    Product(
        id="device",
        label="器械 / 仪器",
        strong=[
            "使用方法", "充电", "档位", "开机", "接触皮肤", "导入", "射频", "微电流",
            "光疗", "按摩头", "清洁模式", "护理模式",
        ],
        items=["美容仪", "洁面仪", "脱毛仪", "按摩仪", "筋膜枪", "体温计", "血压计"],
        weak=["仪器", "设备", "家用"],
        form_hint="带按键/屏幕/充电口的手持设备或台式仪器",
        advice="器械不能写成可食用，也不能只写涂抹效果；按器械说明书写用法",
        vision_key="device",
    ),
    Product(
        id="apparel",
        label="服装 / 配饰",
        strong=["上身", "版型", "尺码", "肩宽", "胸围", "腰围", "裤长", "面料", "克重", "洗后"],
        items=["T恤", "卫衣", "外套", "裤装", "裙装", "内衣", "袜子", "鞋"],
        weak=["显瘦", "舒适", "透气"],
        form_hint="平铺或穿在模特身上的纺织品/鞋",
        advice="服装文案请按尺码与面料写，不要套护肤或口服的用法",
        vision_key="apparel",
    ),
]
# 兜底品类：注册表里没有的形态，模型归到这里，不硬套现有词表
OTHER_PRODUCT = Product(
    id="other",
    label="其它品类（未注册）",
    form_hint="不属于以上任何一类的产品",
    advice="请在 config/taxonomy.toml 里补一个 [[product]]，写清强信号词与形态判据",
    vision_key="other",
)

_DEFAULT_MATERIALS: list[Material] = [
    Material(id="scene", label="场景图", template="scene"),
    Material(id="white_bg", label="白底主图", template="white_bg"),
    Material(id="kv", label="卖点图", template="kv"),
    Material(
        id="price", label="价格图 / 价签", template="price", requires_source=True,
        source_fields=["price_now"], note="现价必须来自客户价目表，不能由模型或排版编造",
    ),
    Material(
        id="patent", label="专利 / 检测证据图", template="patent", requires_source=True,
        source_fields=["patent_no", "issuer"], note="必须附真实扫描件，证书内容不得生成",
    ),
    Material(
        id="table", label="表格对比海报", template="table", requires_source=True,
        source_fields=["rows"], note="表内数字须逐项有来源",
    ),
    Material(
        id="screen", label="线下大屏 / 灯箱", template="screen",
        note="需要真实场景照，四角定位置信度不足时必须人工校准",
    ),
    Material(
        id="pair", label="前后对比效果图", template="pair",
        note="真实拍摄才能当功效证据；生成/合成必须标「示意图」",
    ),
]


def _merge(base: list, over: list) -> list:
    """按 id 覆盖，新 id 追加。配置里写一个 id 就能改掉内置那一条。"""
    out = {x.id: x for x in base}
    for item in over:
        out[item.id] = item
    return list(out.values())


def _p(raw: dict) -> Product:
    return Product(
        id=str(raw.get("id") or "").strip(),
        label=str(raw.get("label") or raw.get("id") or ""),
        strong=[str(w) for w in (raw.get("strong") or [])],
        items=[str(w) for w in (raw.get("items") or [])],
        weak=[str(w) for w in (raw.get("weak") or [])],
        form_hint=str(raw.get("form_hint") or ""),
        advice=str(raw.get("advice") or ""),
        vision_key=str(raw.get("vision_key") or raw.get("id") or ""),
    )


def _m(raw: dict) -> Material:
    return Material(
        id=str(raw.get("id") or "").strip(),
        label=str(raw.get("label") or raw.get("id") or ""),
        template=str(raw.get("template") or "scene"),
        requires_source=bool(raw.get("requires_source") or False),
        source_fields=[str(x) for x in (raw.get("source_fields") or [])],
        allow_generated_text=bool(raw.get("allow_generated_text") or False),
        note=str(raw.get("note") or ""),
    )


class Taxonomy:
    def __init__(self, products: list[Product], materials: list[Material], source: str):
        self.products = [p for p in products if p.id]
        self.materials = [m for m in materials if m.id]
        self.source = source
        self._by_pid = {p.id: p for p in self.products}
        self._by_mid = {m.id: m for m in self.materials}

    # ── 查询 ──
    def product(self, pid: str | None) -> Product | None:
        return self._by_pid.get(str(pid or "").strip().lower())

    def material(self, mid: str | None) -> Material | None:
        return self._by_mid.get(str(mid or "").strip().lower())

    def material_for_template(self, template: str | None) -> Material | None:
        t = str(template or "").strip()
        return next((m for m in self.materials if m.template == t), None)

    def labels(self) -> dict[str, str]:
        return {p.id: p.label for p in self.products}

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "products": [p.to_dict() for p in self.products],
            "materials": [m.to_dict() for m in self.materials],
        }


_CACHE: tuple[float, Taxonomy] | None = None


def load(force: bool = False) -> Taxonomy:
    """读配置。文件坏了就退回内置表，并让 source 说明原因——不静默。"""
    global _CACHE
    try:
        mtime = TAXONOMY_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if not force and _CACHE and _CACHE[0] == mtime:
        return _CACHE[1]

    products = list(_DEFAULT_PRODUCTS)
    materials = list(_DEFAULT_MATERIALS)
    source = "内置默认（未找到 config/taxonomy.toml）"

    if TAXONOMY_PATH.exists():
        try:
            with TAXONOMY_PATH.open("rb") as f:
                raw = tomllib.load(f)
            products = _merge(products, [_p(x) for x in raw.get("product") or []])
            materials = _merge(materials, [_m(x) for x in raw.get("material") or []])
            source = f"{TAXONOMY_PATH.name}（{len(raw.get('product') or [])} 产品 / {len(raw.get('material') or [])} 物料）"
        except Exception as exc:  # noqa: BLE001 配置坏了必须能跑，但要说明
            source = f"内置默认（{TAXONOMY_PATH.name} 解析失败：{exc}）"

    tax = Taxonomy(products, materials, source)
    _CACHE = (mtime, tax)
    return tax


def invalidate() -> None:
    global _CACHE
    _CACHE = None


def path() -> Path:
    return TAXONOMY_PATH
