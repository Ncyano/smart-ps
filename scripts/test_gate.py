"""交付闸门 / 合规检查 / 品类注册表的离线测试（不联网、不花钱）。

这一层不产出图片，只决定"这张图能不能发出去"。所以测的是判定逻辑：
- 授权、数据来源、品类、版式、语义，任何一项没过都不能算通过
- 高分不能抵消硬伤（这是整个改动的核心）
- 合规词表该拦的拦、该提示的提示，但不误伤器械类
- 品类注册表能按配置扩，未校验必须单独一档

用法：python scripts/test_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app import claims as C  # noqa: E402
from server.app import gate as G  # noqa: E402
from server.app import rights  # noqa: E402
from server.app import taxonomy  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def base_batch(**kw) -> dict:
    """一个"什么都没问题"的批次，用来做单变量对照。"""
    b = {
        "id": "test-batch",
        "template": "scene",
        "template_compose": False,
        # 授权要带声明人与时间：只有等级、没有署名的声明，正式导出会被拦
        "rights": {"declared": "owned", "declared_by": "测试",
                   "declared_at": "2026-01-01T00:00:00", "evidence": "验收用例",
                   "verified": False},
        "category_check": {"ok": True, "status": "skipped", "source": "文案未指向具体品类",
                           "copy_category": "unknown", "image_category": "unknown"},
        "claims_audit": {"status": "pass", "findings": [], "data_items": [], "missing_fields": []},
        # 任务单：真实批次一定带这个（run.py ⓪ 生成）。缺了闸门判 unknown——
        # 没校验过不等于通过，这条在【8】里单独测。
        "task": {
            "kind": "scene", "kind_label": "场景图",
            "template": {"id": "scene", "version": 1},
            "zones": {"keep": ["产品外形"], "may_change": ["背景"], "forbid": ["改包装字"]},
            "acceptance": [{"id": "rights", "label": "授权", "auto": True, "how": ""}],
            "unresolved": [],
        },
        "candidates": [{
            "index": 1, "total": 0.95, "tier": "A",
            "scores": {"structure": 0.93, "spread": 0.02, "sharp": 1.0},
            "fidelity_mode": "object",
            "fidelity_checks": {
                "subject": {"status": "pass", "detail": "一致"},
                "geometry": {"status": "pass", "detail": "2%"},
                "label": {"status": "pass", "detail": "一致"},
                "overall": {"status": "pass", "detail": ""},
            },
            "layout": {"ok": True, "reasons": []},
            "semantic": {"checked": True, "same_product": True, "text_ok": True,
                         "confidence": 95, "model": "stub"},
            "subject_confirmed": True, "subject_confirmed_by": "测试",
            "subject_confirmed_at": "2026-01-01T00:00:00",
        }],
    }
    for k, v in kw.items():
        if k == "cand":
            b["candidates"][0].update(v)
        else:
            b[k] = v
    return b


def main() -> int:
    print("\n【1】闸门：全通过才可交付")
    batch = base_batch()
    d = G.evaluate(batch, batch["candidates"][0])
    check("全部通过 → deliverable", d.status == G.DELIVERABLE, d.reason)
    check("可交付时 formal_ready", (G.apply(base_batch()) or {}).get("delivery", {}).get("formal_ready") is True)

    print("\n【2】闸门：高分不能抵消硬伤")
    b = base_batch(cand={"total": 0.99,
                         "fidelity_checks": {"subject": {"status": "fail", "detail": "主体换了"},
                                             "geometry": {"status": "pass", "detail": ""},
                                             "label": {"status": "pass", "detail": ""}}})
    d = G.evaluate(b, b["candidates"][0])
    check("总分 0.99 + 主体 fail → rejected", d.status == G.REJECTED, d.reason)

    print("\n【3】闸门：没校验过的不能算通过")
    b = base_batch()
    b["category_check"] = {"ok": True, "status": "unchecked", "unchecked_reason": "素材品类未校验"}
    d = G.evaluate(b, b["candidates"][0])
    check("品类 unchecked → manual", d.status == G.MANUAL, d.status)
    check("原因里点名「品类」", "品类" in d.reason, d.reason)

    b = base_batch()
    b["candidates"][0]["subject_confirmed"] = False
    d = G.evaluate(b, b["candidates"][0])
    check("未人工确认 → manual", d.status == G.MANUAL, d.status)
    check("确认项进入 blockers", "subject_confirm" in d.blockers, str(d.blockers))

    print("\n【4】闸门：数据来源")
    b = base_batch()
    b["claims_audit"] = {
        "status": "manual", "findings": [{"kind": "data", "level": "warn",
                                          "rule": "现价未绑定来源", "text": "现价：199"}],
        "data_items": [{"label": "现价", "text": "199", "verified": False, "source": ""}],
        "missing_fields": [],
    }
    d = G.evaluate(b, b["candidates"][0])
    check("价格无来源 → manual", d.status == G.MANUAL, d.status)
    check("原因里点名「数据来源」", "数据来源" in d.reason, d.reason)

    b["claims_audit"]["data_items"][0].update({"verified": True, "source": "价目表 v3"})
    d = G.evaluate(b, b["candidates"][0])
    check("补上来源 → deliverable", d.status == G.DELIVERABLE, d.reason)

    print("\n【5】闸门：授权等级优先于质量")
    b = base_batch(rights={"declared": "third_party"})
    d = G.evaluate(b, b["candidates"][0])
    check("第三方未授权 → internal_only", d.status == G.INTERNAL, d.status)
    ok, why = G.formal_ready(b)
    check("不能正式交付，且说明原因", not ok and "仅内部参考" in why, why)

    print("\n【6】闸门：版式/语义")
    b = base_batch(cand={"layout": {"ok": False, "reasons": ["文字超出文字区"]}})
    d = G.evaluate(b, b["candidates"][0])
    check("版式失败 → rejected", d.status == G.REJECTED, d.reason)

    b = base_batch(cand={"semantic": {"checked": True, "same_product": False,
                                      "reason": "换了产品", "confidence": 90}})
    d = G.evaluate(b, b["candidates"][0])
    check("语义判定不是同一款 → rejected", d.status == G.REJECTED, d.reason)

    b = base_batch(cand={"semantic": {"checked": False, "error": "未启用"}})
    d = G.evaluate(b, b["candidates"][0])
    check("语义未启用 → manual（不是 pass）", d.status == G.MANUAL, d.status)

    print("\n【7】闸门：合成类模板跳过像素保真")
    b = base_batch(template="patent", template_compose=True,
                   cand={"fidelity_mode": "compose",
                         "fidelity_checks": {k: {"status": "skip", "detail": ""}
                                             for k in ("subject", "geometry", "label", "overall")},
                         "semantic": {"checked": False, "error": "合成类"}})
    d = G.evaluate(b, b["candidates"][0])
    check("合成类 → deliverable", d.status == G.DELIVERABLE, d.reason)
    check("保真项标为 skip", any(c.id == "fidelity" and c.status == G.SKIP for c in d.checks))

    print("\n【8】合规：绝对化 / 疗效 / 商标")
    a = C.audit({"title": "全网最低 每天一粒"}, None, template="kv")
    check("绝对化用语 → block", a.status == "block", a.status)
    check("block 不可生成", not a.ok)
    try:
        C.assert_clean({"title": "全网最低"}, None)
        check("assert_clean 应抛异常", False)
    except C.ClaimsError as exc:
        check("assert_clean 抛 ClaimsError", "绝对化" in str(exc), str(exc))

    a = C.audit({"title": "消炎祛斑 一片见效"}, None, template="kv")
    check("疗效宣称（化妆品）→ manual", a.status == "manual", a.status)

    a = C.audit({"title": "消炎祛斑"}, None, template="kv", claims=None)
    a_dev = C.audit({"title": "消炎祛斑"}, "治疗", instruction="", template="kv")
    _ = a_dev
    # 器械类允许按注册证表述：把品类声明成 device 后应降为提示
    from server.app.pipeline import category as catmod
    old = catmod.detect_copy
    catmod.detect_copy = lambda copy, instr="", tax=None: ("device", ["治疗"], [])
    a_dev = C.audit({"title": "治疗"}, None, template="kv")
    catmod.detect_copy = old
    check("疗效词 + 器械 → 只提示不拦", a_dev.status in ("pass", "manual"), a_dev.status)
    check("器械疗效未被判 block", a_dev.status != "block", a_dev.status)

    a = C.audit({"title": "全网最低"}, None)
    check("绝对化仍然拦", a.status == "block")

    print("\n【9】合规：数据来源")
    a = C.audit({"title": "限时 199 元", "price_now": "199"}, None, template="price")
    check("价格无来源 → manual", a.status == "manual", a.status)
    check("数据项被抽出", any(d["text"] == "199" for d in a.data_items), str(a.data_items))
    a = C.audit({"title": "限时 199 元", "price_now": "199"}, None, template="price",
                claims=[{"text": "199", "kind": "price", "source": "价目表 v3"}])
    check("绑定来源后视为已核", all(d["verified"] for d in a.data_items), str(a.data_items))
    check("来源被记录", a.data_items and "价目表" in a.data_items[0]["source"])

    a = C.audit({"title": "含 1000mg 辅酶", "points": ["专利号 ZL202310123456.7"]},
                None, template="kv")
    check("含量/专利号都能抽出来",
          {d["kind"] for d in a.data_items} >= {"dose", "patent"}, str(a.data_items))

    print("\n【10】品类注册表")
    tax = taxonomy.load()
    check("注册表加载了产品条目", len(tax.products) >= 4, str(len(tax.products)))
    check("物料能按模板反查", (tax.material_for_template("price") or {}).id == "price")
    check("价格类物料要求来源", tax.material_for_template("price").requires_source is True)
    check("场景图不要求来源", tax.material_for_template("scene").requires_source is False)
    check("未知品类返回 None", tax.product("pet_food") is None)
    check("按 id 能取到标签", tax.product("skincare").label.startswith("涂抹"))

    print("\n【11】品类一致性")
    from server.app.pipeline import category as cat

    pid, hits, conflict = cat.detect_copy({"title": "每天一粒 1000mg 辅酶Q10"})
    check("口服文案判为 oral", pid == "oral", pid)
    pid, hits, conflict = cat.detect_copy({"title": "上脸服帖 不卡粉"})
    check("护肤文案判为 skincare", pid == "skincare", pid)
    pid, hits, conflict = cat.detect_copy({"title": "保湿补水"})
    check("只有效果词 → 不定性", pid == "unknown", (pid, hits))
    pid, hits, conflict = cat.detect_copy({"title": "每天一粒 上脸"})
    check("自相矛盾 → unknown + 说明", pid == "unknown" and bool(conflict), str(conflict))

    from PIL import Image
    img = Image.new("RGB", (64, 64), "white")
    res = cat.check({"title": "每天一粒"}, img, {"engines": {}}, declared="skincare", template="kv")
    check("声明护肤 vs 口服文案 → conflict", res.status == "conflict" and not res.ok, res.status)
    res = cat.check({"title": "保湿补水"}, img, {"engines": {}}, template="kv")
    check("中性文案 → skipped", res.status == "skipped", res.status)
    res = cat.check({"title": "每天一粒"}, img, {"engines": {}}, declared="oral", template="kv")
    check("文案与声明一致 → consistent", res.status == "consistent", res.status)
    res = cat.check({"title": "每天一粒"}, img, {"engines": {}}, template="kv")
    check("无声明且无模型 → unchecked + 原因",
          res.status == "unchecked" and bool(res.unchecked_reason), res.unchecked_reason)

    print("\n【8】闸门：授权声明的留痕（声明≠核验）")
    b = base_batch()
    b["rights"] = {"declared": "owned"}
    d = G.evaluate(b, b["candidates"][0])
    check("无署名的声明仍判 pass（等级够）", d.status == G.DELIVERABLE, d.reason[:50])
    G.apply(b)                     # 正式闸门读 batch["delivery"]，先跑一遍
    ok, why = G.formal_ready(b)
    check("但正式导出被拦", not ok and "声明人" in why, why[:60])
    # 界面靠 delivery.formal_ready 决定按钮亮不亮，口径必须与导出闸门一致，
    # 否则会出现"按钮能点、点下去 409"
    check("界面口径同步（按钮应为灰）", b["delivery"]["formal_ready"] is False,
          str(b["delivery"].get("reason"))[:40])
    r = rights.stamp({}, "owned", by="张三", evidence="合同 A-01")
    b["rights"] = r
    G.apply(b)
    ok, why = G.formal_ready(b)
    check("补了声明人后可正式导出", ok, why[:60])
    check("声明里记了人、时间、依据",
          r["declared_by"] == "张三" and bool(r["declared_at"]) and "合同" in r["evidence"],
          r["provenance"][:50])
    check("未核验要说出来", "未经核验" in r["provenance"], r["provenance"][-20:])
    r2 = rights.stamp({}, "owned", by="张三", verified=True, verified_by="法务")
    check("核验后措辞改为已核验", "已核验" in r2["provenance"], r2["provenance"][-24:])

    print("\n【9】闸门：需求完整度（任务单缺失 / 缺项）")
    b = base_batch()
    b.pop("task")
    d = G.evaluate(b, b["candidates"][0])
    check("没有任务单 → 待人工（不是通过）", d.status == G.MANUAL and "需求" in d.reason, d.reason[:60])
    b = base_batch(task={"kind": "kv", "unresolved": [
        {"field": "copy", "why": "这类物料必须有文案", "severity": "block"}],
        "acceptance": []})
    d = G.evaluate(b, b["candidates"][0])
    check("任务单缺 block 项 → rejected", d.status == G.REJECTED, d.reason[:60])
    b = base_batch(task={"kind": "scene", "unresolved": [
        {"field": "exemplars", "why": "没有参考图", "severity": "note"}],
        "acceptance": [{"id": "rights", "label": "授权", "auto": True, "how": ""}]})
    d = G.evaluate(b, b["candidates"][0])
    check("只有 note → 仍可交付（note 不降级）", d.status == G.DELIVERABLE, d.reason[:60])

    print(f"\n{'=' * 56}\n通过 {PASS}　失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
