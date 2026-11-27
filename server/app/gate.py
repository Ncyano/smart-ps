"""交付闸门：把"能不能发出去"从分数里拆出来，做成逐项硬门槛。

## 为什么不能用一个总分

旧版候选卡的结论是 `A 可交付 / B 待人工 / C 不可用`，由保真、风格、版式
三项算出来。问题是**这些分数只回答像素问题**：

- 保真 0.95 的图，文案可能写着"每天一粒"，而素材是气垫霜（真出过）
- 风格 0.88 的图，价格可能是模型编的（模板只保证排版准，不保证数字真）
- 结构 0.9 的图，素材可能是未授权的第三方品牌

分数高只能说明"看起来像"，不能说明"发出去不惹事"。而一个总分是可以被
另一项拉高的——三项里两项满分，第三项 0.4 也能凑到 0.8。这类错误
**发出去就收不回**，不能用加权平均来兜。

## 做法：逐项判定，全部通过才算可交付

每张候选都过一遍下面这些检查，每项只有四种状态：

| 状态 | 含义 |
|---|---|
| `pass` | 有证据通过 |
| `warn` | 有疑点，人可以看一眼放过 |
| `unknown` | **没校验过**（不是通过）。旧版最大的坑就在这 |
| `fail` | 确定不通过 |

汇总规则（不做加权、不做抵消）：

```
有 hard fail            → rejected（不可用）
授权是 internal/sample  → internal_only（仅内部参考，无论多好）
有任何 warn / unknown   → manual（待人工，写清卡在哪一项）
全部 pass               → deliverable（可交付）
```

`unknown` 之所以单独一档：视觉模型没开通、素材品类没看成、审核人没确认主体，
这些都不是"没问题"，是"没人看过"。把没看过的当通过，是这类系统最容易犯的错。

## 人工确认这一项

`subject_confirmed` 默认 false，必须由审核人在界面上点"主体已核对"才变 true，
并记录是谁、什么时候。不点就永远是 manual——这不是麻烦，这是留痕。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import rights as rightsmod

PASS, WARN, UNKNOWN, FAIL, SKIP = "pass", "warn", "unknown", "fail", "skip"

DELIVERABLE = "deliverable"
MANUAL = "manual"
REJECTED = "rejected"
INTERNAL = "internal_only"

_LABEL = {
    DELIVERABLE: "可交付",
    MANUAL: "待人工",
    REJECTED: "不可用",
    INTERNAL: "仅内部参考",
}


@dataclass
class Check:
    id: str
    label: str
    status: str
    detail: str = ""
    hard: bool = False          # hard fail 直接判不可用
    action: str = ""            # 用户该做什么才能变成 pass

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Delivery:
    status: str = MANUAL
    label: str = _LABEL[MANUAL]
    checks: list[Check] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)   # 卡住的检查 id
    reason: str = ""

    @property
    def deliverable(self) -> bool:
        return self.status == DELIVERABLE

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "label": self.label,
            "checks": [c.to_dict() for c in self.checks],
            "blockers": self.blockers,
            "reason": self.reason,
            "deliverable": self.deliverable,
        }


def _mk(cid: str, label: str, status: str, detail: str = "", hard: bool = False,
        action: str = "") -> Check:
    return Check(id=cid, label=label, status=status, detail=detail, hard=hard, action=action)


def _rights_check(batch: dict) -> Check:
    r = rightsmod.for_batch(batch)
    if r.level == "ok":
        return _mk("rights", "素材授权", PASS, f"{r.declared}（{r.note}）", hard=True)
    if r.level == "internal":
        return _mk("rights", "素材授权", FAIL, r.note, hard=True,
                   action="换自有素材，或取得书面授权后重新声明为 licensed")
    return _mk("rights", "素材授权", UNKNOWN, "未声明素材来源与授权状态", hard=True,
               action="在批次里补声明 owned / licensed / third_party / sample")


def _claims_check(batch: dict) -> list[Check]:
    a = batch.get("claims_audit") or {}
    status = str(a.get("status") or "pass")
    out: list[Check] = []

    blocks = [f for f in (a.get("findings") or []) if f.get("level") == "block"]
    if blocks:
        out.append(_mk("claims", "合规文字", FAIL,
                       "；".join(f"{f.get('rule')}「{f.get('text')}」" for f in blocks[:3]),
                       hard=True, action="改文案：绝对化用语与第三方商标不能投放"))
    else:
        # 数据来源单独一项在下面判，这里只留"文字本身有没有问题"的词表命中。
        # 混在一起会导致补上来源之后这一项还挂着旧结论。
        warns = [f for f in (a.get("findings") or [])
                 if f.get("level") == "warn" and f.get("kind") != "data"]
        if warns:
            detail = "；".join(f"{f.get('rule')}「{f.get('text')}」" for f in warns[:3])
            out.append(_mk("claims", "合规文字", UNKNOWN, detail, hard=True,
                           action="逐条确认：疗效表述是否有批件、是否属于允许范围"))
        else:
            out.append(_mk("claims", "合规文字", PASS,
                           "未命中绝对化用语/疗效宣称/第三方商标", hard=True))

    # 数据来源独立一项：合规词表干净不代表数字有出处
    data = a.get("data_items") or []
    if data:
        bad = [d for d in data if not d.get("verified")]
        if bad:
            out.append(_mk("data_source", "数据来源", UNKNOWN,
                           "、".join(f"{d.get('label')} {d.get('text')}" for d in bad[:4])
                           + " 没有出处", hard=True,
                           action="在 claims 里补来源（价目表版本 / 证书文件名 / 报告编号）"))
        else:
            src = "；".join(f"{d.get('text')}←{d.get('source')}" for d in data[:2])
            out.append(_mk("data_source", "数据来源", PASS, f"{len(data)} 项均有出处：{src}", hard=True))
    else:
        out.append(_mk("data_source", "数据来源", PASS, "文案未含价格/含量/专利号等可核数字", hard=True))

    missing = a.get("missing_fields") or []
    if missing:
        out.append(_mk("required_fields", "物料必填数据", UNKNOWN,
                       f"缺：{'、'.join(missing)}", hard=True,
                       action="补齐这类物料的必填字段（价格、专利号、表格数据）"))
    return out


def _category_check(batch: dict) -> Check:
    c = batch.get("category_check") or {}
    st = str(c.get("status") or "")
    if st == "conflict" or c.get("ok") is False:
        return _mk("category", "品类一致", FAIL,
                   "；".join(c.get("reasons") or [])[:200], hard=True,
                   action="换素材或改文案，让文案说的是这个产品能做的事")
    if st == "unchecked":
        return _mk("category", "品类一致", UNKNOWN,
                   c.get("unchecked_reason") or "素材品类未校验", hard=True,
                   action="在任务里声明品类，或人工确认文案与产品一致")
    if st == "consistent":
        return _mk("category", "品类一致", PASS,
                   f"{c.get('copy_category')} / {c.get('image_category')}（依据：{c.get('source')}）",
                   hard=True)
    return _mk("category", "品类一致", PASS, c.get("source") or "文案无品类指向，跳过", hard=True)


def _fidelity_checks(cand: dict) -> list[Check]:
    s = cand.get("scores") or {}
    f = cand.get("fidelity_checks") or {}
    if cand.get("fidelity_mode") == "compose":
        return [_mk("fidelity", "像素保真", SKIP, "合成类物料：内容来自给定素材与排版，不适用")]
    out: list[Check] = []

    subj = f.get("subject") or {}
    st = str(subj.get("status") or "")
    if st == FAIL:
        out.append(_mk("subject", "产品主体", FAIL, subj.get("detail") or "主体对不上", hard=True,
                       action="重新生成，或换一张更清晰的素材"))
    elif st == UNKNOWN:
        out.append(_mk("subject", "产品主体", UNKNOWN,
                       subj.get("detail") or "素材为多实例场景，无法核验主体细节", hard=True,
                       action="人工比对成图与素材的主体细节"))
    elif st == WARN:
        out.append(_mk("subject", "产品主体", WARN, subj.get("detail") or "主体结构存疑", hard=True,
                       action="放大看标签与边缘，确认没被重绘"))
    else:
        out.append(_mk("subject", "产品主体", PASS, subj.get("detail") or f"结构 {s.get('structure')}",
                       hard=True))

    geo = f.get("geometry") or {}
    gs = str(geo.get("status") or "")
    if gs in (FAIL, WARN):
        out.append(_mk("geometry", "比例", gs, geo.get("detail") or "", hard=(gs == FAIL),
                       action="比例明显偏了就重生成；轻微偏差人工目测"))
    elif gs == UNKNOWN:
        out.append(_mk("geometry", "比例", UNKNOWN, geo.get("detail") or "未测量", hard=False,
                       action="重排类模板会主动改比例，属预期；否则人工核对"))
    else:
        out.append(_mk("geometry", "比例", PASS, geo.get("detail") or "在容差内"))

    lbl = f.get("label") or {}
    ls = str(lbl.get("status") or "")
    if ls == FAIL:
        out.append(_mk("label", "标签/细节", FAIL, lbl.get("detail") or "标签区域糊了或被改", hard=True,
                       action="重生成；标签必须清晰可读且与素材一致"))
    elif ls == UNKNOWN:
        out.append(_mk("label", "标签/细节", UNKNOWN, lbl.get("detail") or "细节未测量", hard=False,
                       action="放大看标签文字与 Logo"))
    elif ls == WARN:
        out.append(_mk("label", "标签/细节", WARN, lbl.get("detail") or "", hard=False,
                       action="核对标签文字是否与素材一致"))
    else:
        out.append(_mk("label", "标签/细节", PASS, lbl.get("detail") or ""))
    return out


def _layout_check(cand: dict) -> Check:
    lr = cand.get("layout")
    if not lr:
        return _mk("layout", "版式自检", SKIP, "该模板无确定性排版")
    if lr.get("ok"):
        return _mk("layout", "版式自检", PASS, "文字未溢出、底板可读")
    return _mk("layout", "版式自检", FAIL, "；".join(lr.get("reasons") or ["版式异常"]), hard=True,
               action="文字超框就改短文案；抠图异常就重生成")


def _semantic_check(batch: dict, cand: dict) -> Check:
    if batch.get("template_compose"):
        return _mk("semantic", "语义判定", SKIP,
                   "合成类物料：内容由给定素材与排版产生，不做同款判定")
    s = cand.get("semantic") or {}
    if s.get("same_product") is False:
        return _mk("semantic", "语义判定", FAIL, f"不是同一款产品：{s.get('reason') or ''}", hard=True,
                   action="成图里的产品被换掉了，重生成或换引擎")
    if s.get("text_ok") is False:
        return _mk("semantic", "语义判定", WARN, f"文字可疑：{s.get('reason') or ''}", hard=True,
                   action="放大看画面上的字有没有乱码")
    if s.get("checked"):
        return _mk("semantic", "语义判定", PASS,
                   f"{s.get('model') or '视觉模型'} 判为同一款（{s.get('confidence') or '-'}）", hard=True)
    return _mk("semantic", "语义判定", UNKNOWN, s.get("error") or "未启用语义判定", hard=True,
               action="配好视觉模型后重跑，或人工比对")


def _confirm_check(batch: dict, cand: dict) -> Check:
    who = cand.get("subject_confirmed_by") or ""
    label = "内容人工确认" if batch.get("template_compose") else "主体人工确认"
    if cand.get("subject_confirmed"):
        at = cand.get("subject_confirmed_at") or ""
        return _mk("subject_confirm", label, PASS, f"{who or '审核人'} 已于 {at} 确认")
    return _mk("subject_confirm", label, UNKNOWN,
               "还没有人核对过内容" if batch.get("template_compose") else "还没有人核对过主体细节",
               action="点「已核对」并留一句核对说明（会记名字和时间）")


def evaluate(batch: dict, cand: dict) -> Delivery:
    """对一张候选做逐项判定。不做加权、不做抵消。"""
    checks: list[Check] = [_rights_check(batch), _category_check(batch)]
    checks.extend(_claims_check(batch))
    checks.extend(_fidelity_checks(cand))
    checks.append(_layout_check(cand))
    checks.append(_semantic_check(batch, cand))
    checks.append(_confirm_check(batch, cand))

    blockers = [c.id for c in checks if c.status in (FAIL, UNKNOWN, WARN)]
    hard_fail = [c for c in checks if c.hard and c.status == FAIL]
    unknown = [c for c in checks if c.status == UNKNOWN]
    warn = [c for c in checks if c.status == WARN]

    rights = rightsmod.for_batch(batch)
    if rights.level == "internal":
        status = INTERNAL
        reason = f"素材授权为「{rights.label}」，与图片质量无关，不能对外投放"
    elif hard_fail:
        status = REJECTED
        reason = "硬门槛未过：" + "；".join(f"{c.label}（{c.detail}）" for c in hard_fail[:3])
    elif unknown or warn:
        status = MANUAL
        # 把所有卡住的项名列出来，再给前两项的具体说明。
        # 只说"待人工"而不说卡在哪，是最容易让人放弃看的写法。
        stuck = unknown or warn
        names = "、".join(c.label for c in stuck)
        detail = "；".join(f"{c.label}：{c.detail[:70]}" for c in stuck[:2])
        reason = f"待人工（卡在：{names}）：{detail}"
    else:
        status = DELIVERABLE
        reason = "全部检查项有证据通过"

    return Delivery(status=status, label=_LABEL[status], checks=checks,
                    blockers=blockers, reason=reason)


def apply(batch: dict) -> dict:
    """对批次里每张候选跑闸门，写回 delivery / verdict / suggested。"""
    summary = {DELIVERABLE: 0, MANUAL: 0, REJECTED: 0, INTERNAL: 0}
    verdict_of = {DELIVERABLE: "可交付", MANUAL: "待人工", REJECTED: "不可用", INTERNAL: "仅内部参考"}

    for c in batch.get("candidates", []):
        d = evaluate(batch, c)
        c["delivery"] = d.to_dict()
        c["verdict"] = verdict_of[d.status]
        summary[d.status] = summary.get(d.status, 0) + 1

    # 推荐只在"可交付"里挑；一张都不可交付就不推荐，别拿待人工冒充推荐
    best = None
    for c in batch.get("candidates", []):
        c["suggested"] = False
        if (c.get("delivery") or {}).get("status") != DELIVERABLE:
            continue
        if best is None or (c.get("total") or 0) > (best.get("total") or 0):
            best = c
    if best is not None:
        best["suggested"] = True

    rights = rightsmod.for_batch(batch)
    formal_ready = bool(best is not None and rights.level == "ok")
    batch["delivery"] = {
        "summary": summary,
        "formal_ready": formal_ready,
        "rights_level": rights.level,
        "reason": "" if formal_ready else (
            "没有可交付候选" if best is None else f"素材授权为「{rights.label}」"),
    }
    batch["status"] = "ok" if formal_ready else (
        "internal_only" if rights.level == "internal" and best is not None else "needs_review"
    )
    return batch


def formal_ready(batch: dict) -> tuple[bool, str]:
    """正式交付闸门。导出 formal=true 时调用。"""
    d = batch.get("delivery") or {}
    if d.get("formal_ready"):
        return True, ""
    rights = rightsmod.for_batch(batch)
    if rights.level != "ok":
        return False, f"素材授权为「{rights.label}」：{rights.note}。只能导出内部参考版本。"
    reason = d.get("reason") or "没有通过全部硬门槛的候选"
    # 把具体卡在哪一项说清楚，别只说"不可交付"
    detail, idx = "", None
    for c in batch.get("candidates", []):
        dd = c.get("delivery") or {}
        if dd.get("status") in (MANUAL, REJECTED):
            detail = dd.get("reason") or ""
            idx = c.get("index")
            break
    return False, (reason + (f"（例：候选 #{idx} {detail}）" if detail else ""))
