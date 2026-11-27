"""需求 → 结构化任务单。

## 为什么要多这一步

"做个卖点图"这五个字里藏着几十个决定：尺寸给哪个平台、用哪张素材、
产品哪些地方不许动、文案从哪来、什么算做完。这些决定如果只留在提示词里，
模型会**自己补齐**——补出来的产品细节是编的，补出来的数字是编的。

所以生成之前先把它写成一张单子：

```
物料类型 / 渠道与尺寸 / 素材与参考成图 / 三类区域
必须保持 / 允许改动 / 禁止项 / 文案与数据来源 / 验收条件
```

单子里每一项都能被后续环节核对：`gate.py` 按 `acceptance` 逐项判，
`record.py` 把它原样写进制作记录，事后能回答"当时要求的是什么"。

## 三类区域

| 区域 | 内容 | 谁来处理 |
|---|---|---|
| 保护区 `protect` | 产品外形与包装文字、人物身份、专利/检测原件 | 从甲方素材保留，模型不许重绘 |
| 生成区 `generate` | 背景、氛围、非关键道具、环境光 | 模型负责 |
| 排版区 `layout` | 标题、卖点、价格、表格、脚注 | 代码/PS 排版，模型不写字 |

分区不是画个框就完事。它同时决定了三件事：提示词里写什么约束、
闸门检查哪块像素、出了问题该走哪条修法（见 `redo.py`）。

## 一条刻意的取舍

这里**不抛异常**。缺字段交给闸门判 FAIL，原因是：
缺价格、缺扫描件这类问题，模板自己要排版时才知道具体缺哪一项，
报错信息比这里准。代价是价格图会先生成一次背景（0.12 元）再判失败。
"少花一毛钱"没有"报告里说清缺什么"重要——这条已经写在 knowledge/07。
"""

from __future__ import annotations

from datetime import datetime

# 物料类型 → 中文名（给任务单标题用，别拿渠道名当物料名）
_LABEL = {
    "scene": "场景图", "white_bg": "白底主图", "kv": "卖点图", "price": "价格图 / 价签",
    "patent": "专利 / 检测证据图", "table": "表格 / 对比海报", "pair": "前后对比",
    "screen": "线下大屏 / 灯箱",
}

# 物料类型 → 渠道说明。用 taxonomy 的 material id 当键。
_CHANNEL = {
    "scene": "电商详情 / 社媒",
    "white_bg": "电商主图（1:1）",
    "kv": "电商详情 / 社媒 / 线下海报",
    "price": "线下门店 / 货架价签",
    "patent": "详情页证据区 / 客服素材",
    "table": "详情页对比区 / 私域长图",
    "pair": "详情页效果区 / 社媒",
    "screen": "线下大屏 / 灯箱 / 立牌",
}

# 保护区：这类物料的"不能错"清单。写具体，不写"保持产品一致"这种空话。
_KEEP = {
    "scene": ["产品外形、颜色、材质（不得改比例）", "包装上的 Logo 与全部文字",
              "标签上的规格、成分、净含量数字"],
    "white_bg": ["产品外形与比例", "Logo、包装文字、标签数字", "材质高光与阴影方向"],
    "kv": ["产品外形与比例", "Logo 与包装文字", "标签数字"],
    "price": ["产品外形与包装", "门店/货架环境结构", "价签上的价格与日期口径"],
    "patent": ["原件上的全部文字、编号、印章、日期", "证书版式与签发机构名称"],
    "table": ["表内每一个数字", "产品图（不得替换成别的产品）"],
    "pair": ["两张原图里的人物身份、肤质细节", "拍摄角度与取景范围"],
    "screen": ["场景照里的建筑、道具、人物", "屏幕的物理形状与边框"],
}

# 生成区：允许模型动的部分。写清楚边界，比写"自由发挥"有用。
_MAY = {
    "scene": ["背景与场景（材质、颜色、环境光）", "非关键道具", "画面的整体影调"],
    "white_bg": ["背景（纯白无缝）", "主体在画布上的位置与留白"],
    "kv": ["背景与氛围", "非关键道具", "画面影调"],
    "price": ["背景（货架/堆头氛围）", "价签之外的装饰元素"],
    "patent": ["原件周围的外框、标题与标注"],
    "table": ["表格版式（列宽、行高、配色）"],
    "pair": ["两张图的裁切范围与统一色调", "标签与免责说明"],
    "screen": ["屏内画面", "屏幕在场景里的透视与亮度匹配"],
}

# 禁止项：这类物料最容易出的错，逐条写死。
_FORBID = {
    "scene": ["重绘包装文字或改数字", "添加不存在的认证/奖项标识", "改产品形状与比例"],
    "white_bg": ["拉伸/压扁主体去适配画布", "镜像翻转（包装文字会反）", "加投影以外的装饰"],
    "kv": ["让模型在图上写字（文字一律代码排）", "遮挡产品标签", "改产品比例"],
    "price": ["编造价格或活动日期", "把参考图里的价格当本品价格"],
    "patent": ["生成或改写证书内容", "把示意图当真证书用", "遮挡编号与印章"],
    "table": ["改动任何数据", "添加未提供的对比项"],
    "pair": ["瘦脸、磨皮、换脸、改年龄", "夸大效果幅度", "把示意图标成实拍"],
    "screen": ["改动场景结构与屏幕边框", "屏内画面溢出屏幕", "用硬边贴图（无羽化/无亮度匹配）"],
}

# 验收条件：按闸门的检查 id 组织，auto=False 的必须人工过。
_ACCEPT_COMMON = [
    ("rights", "素材授权可对外", True, "声明 owned / licensed，且确有授权文件"),
    ("claims", "合规文字", True, "无绝对化用语、无越界疗效宣称、无第三方商标"),
    ("data_source", "数据有来源", True, "每个价格/含量/专利号都绑定出处"),
    ("category", "品类一致", True, "文案说的动作，素材里的产品做得到"),
    ("required_fields", "必填字段齐全", True, "这类物料的必填项都在"),
    ("subject_confirm", "人工核对", False, "审核人比对主体与文案后点确认"),
]
_ACCEPT_BY_TPL = {
    "price": [("subject", "产品主体", True, "成图主体与素材是同一件"),
              ("geometry", "主体比例", True, "没有被拉伸或压扁")],
    "patent": [("layout", "版式自检", True, "原件完整、字段不溢出")],
    "table": [("layout", "版式自检", True, "列数一致、无溢出、无截断")],
    "pair": [("layout", "版式自检", True, "双联对齐、标注与免责完整")],
    "screen": [("layout", "版式自检", True, "四角置信度达标、无溢出、亮度匹配")],
}
_ACCEPT_GEN = [("subject", "产品主体", True, "结构相关度过线且标签区未被改"),
               ("geometry", "主体比例", True, "铺展比偏差 ≤25%"),
               ("label", "标签区", True, "标签文字/Logo 未被重绘"),
               ("layout", "版式自检", True, "文字不溢出、不压主体")]


def zones_for(template: str) -> dict:
    """三类区域的声明。`estimated` 表示框是按版式规则推的，不是量出来的。"""
    tpl = template or "scene"
    layout_band = {"kv": [0.0, 0.62], "price": [0.62, 1.0], "table": [0.10, 0.92],
                   "patent": [0.0, 0.08], "pair": [0.0, 0.12]}.get(tpl)
    return {
        "protect": {"desc": "产品/人物/证据：从甲方素材保留，模型不得重绘",
                    "source": "materials", "estimated": True},
        "generate": {"desc": "背景与氛围：模型负责", "estimated": True},
        "layout": {"desc": "文字与价格：代码排版，模型不写字",
                   "band": layout_band, "estimated": True},
        "keep": list(_KEEP.get(tpl, _KEEP["scene"])),
        "may_change": list(_MAY.get(tpl, _MAY["scene"])),
        "forbid": list(_FORBID.get(tpl, _FORBID["scene"])),
    }


def prompt_block(zones: dict) -> str:
    """把三类区域翻译成模型能读的约束段。

    只写风格词（"高级感""ins质感"）时，模型不知道哪里不许动，
    于是会顺手把包装上的字重画一遍。把禁改项逐条列出来，实测重绘率明显下降。
    """
    keep = "；".join(zones.get("keep") or [])
    may = "；".join(zones.get("may_change") or [])
    forbid = "；".join(zones.get("forbid") or [])
    lines = []
    if keep:
        lines.append(f"必须保持原样（不得重绘、不得改字）：{keep}。")
    if may:
        lines.append(f"允许改动：{may}。")
    if forbid:
        lines.append(f"禁止：{forbid}。")
    if zones.get("layout", {}).get("band"):
        lines.append("画面中的文字与价格由后期排版加入，生成时不要在图内写任何文字。")
    return "\n".join(lines)


def build(req, tpl, materials: list[str] | None = None,
          exemplars: list[str] | None = None) -> dict:
    """生成任务单。纯函数：只读请求与模板，不碰文件、不调网络。"""
    tpl_id = getattr(tpl, "id", "scene")
    zones = zones_for(tpl_id)
    copy = getattr(req, "copy_text", None) or {}
    claims = [c.model_dump() if hasattr(c, "model_dump") else dict(c)
              for c in (getattr(req, "claims", None) or [])]
    mats = [{"role": "product", "name": n, "primary": i == 0}
            for i, n in enumerate(materials or [])]
    refs = [{"role": "reference_image", "name": n} for n in (exemplars or [])]

    accept = []
    seen = set()
    for cid, label, auto, how in (_ACCEPT_COMMON + _ACCEPT_BY_TPL.get(tpl_id, _ACCEPT_GEN)):
        if cid in seen:
            continue
        seen.add(cid)
        accept.append({"id": cid, "label": label, "auto": auto, "how": how})

    unresolved: list[dict] = []
    instruction = (getattr(req, "instruction", "") or "").strip()
    if not tpl.compose and not instruction:
        unresolved.append({
            "field": "instruction", "why": "没有描述要做成什么样（背景/氛围/构图）",
            "fix": "补一句目标场景，例：浅灰背景 + 柔光，主体居中略偏左",
            "severity": "block",
        })
    if len(mats) > 1:
        unresolved.append({
            "field": "materials.primary",
            "why": f"给了 {len(mats)} 张素材，没有指定哪张是主素材",
            "fix": "系统按质量分自动取了第一张；如需指定请在素材顺序上把主图放第一位",
            "severity": "note",
        })
    if not refs:
        unresolved.append({
            "field": "exemplars", "why": "没有参考成图，风格只能按默认画像走",
            "fix": "补一张目标效果图，风格贴合度才可判", "severity": "note",
        })
    if tpl_id in ("kv", "price", "table", "patent") and not copy:
        unresolved.append({
            "field": "copy", "why": "这类物料必须有文案/数据，当前为空",
            "fix": "在表单里填标题与必填数据", "severity": "block",
        })

    return {
        "kind": tpl_id,
        "kind_label": _LABEL.get(tpl_id, tpl_id),
        "channel": _CHANNEL.get(tpl_id, ""),
        "size": list(tpl.canvas) if getattr(tpl, "canvas", None) else None,
        "export": getattr(tpl, "export", "original"),
        "compose": bool(getattr(tpl, "compose", False)),
        "template": {"id": tpl_id, "version": getattr(tpl, "version", 1)},
        "materials": mats,
        "refs": refs,
        "copy": copy,
        "claims": claims,
        "category_declared": getattr(req, "category", "auto"),
        "rights_declared": getattr(req, "rights", "unknown"),
        "zones": zones,
        "prompt_block": prompt_block(zones),
        "acceptance": accept,
        "unresolved": unresolved,
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }


def blocking(task: dict) -> list[dict]:
    """必须补上才能干活的部分（severity=block）。"""
    return [u for u in (task.get("unresolved") or []) if u.get("severity") == "block"]


def check(task: dict) -> dict:
    """给闸门用：任务单本身是否完整。"""
    if not task:
        return {"status": "unknown", "detail": "没有任务单（老批次），需求完整性未校验"}
    block = blocking(task)
    if block:
        return {"status": "fail",
                "detail": "；".join(f"{u['field']}：{u['why']}" for u in block[:2])}
    notes = [u for u in (task.get("unresolved") or []) if u.get("severity") == "note"]
    detail = f"任务单完整（{len(task.get('acceptance') or [])} 条验收条件）"
    if notes:
        # note 只提示、不降级：没给参考成图是正常选择，不能因此判"待人工"。
        # 真正会让结论变的是 block 项，那些才 fail。
        detail += "；备注：" + "；".join(f"{u['field']}（{u['why']}）" for u in notes[:2])
    return {"status": "pass", "detail": detail}
