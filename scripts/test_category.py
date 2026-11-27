"""品类一致性校验的离线测试（不联网、不花钱）。

这层的价值全在"该拦的拦住、不该拦的别误伤"，所以要同时测两个方向：
- 气垫霜配"每天一粒" → 必须拦
- 气垫霜配"遮瑕持妆" → 必须放
误伤的代价是设计师每次都被弹窗，久了就绕过去，护栏等于没有。

用法：python scripts/test_category.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app.pipeline import category as C  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


ORAL_COPY = {
    "title": "每天一粒 高含量辅酶Q10",
    "subtitle": "99.9% 纯度 · rTG 型易吸收",
    "points": ["每粒 1000mg，含量是国内常见的 4 倍", "rTG 结构，吸收率高于普通软胶囊"],
    "tag": "专利配方",
}
SKIN_COPY = {
    "title": "奶油肌气垫 遮瑕不卡粉",
    "subtitle": "轻薄贴肤 · 持妆一整天",
    "points": ["一层盖住痘印与红血丝", "微米级粉体贴肤，干皮不卡纹", "补妆同时养肤"],
    "tag": "油皮亲妈",
}


def fake_image(cat: str):
    """替换掉看图的模型调用，返回指定品类。"""
    def _f(im, cfg):
        return cat, "模拟形态", "fake-model"
    return _f


def main() -> int:
    print("\n【1】文案侧词表：口服口径")
    cat, hits, conflict = C.detect_copy(ORAL_COPY)
    check("每天一粒 → oral", cat == C.ORAL, f"得到 {cat} hits={hits}")
    check("命中词说得清（含每粒/软胶囊）", any("粒" in h for h in hits) or "软胶囊" in hits, str(hits))

    cat, hits, _ = C.detect_copy({"points": ["温水送服，每日两粒"]})
    check("温水送服+每日两粒 → oral", cat == C.ORAL, f"得到 {cat}")

    cat, _, _ = C.detect_copy({"title": "辅酶Q10", "subtitle": "每瓶 60 粒装"})
    check("仅成分+粒装（无服用动作）→ 仍判 oral", cat == C.ORAL, f"得到 {cat}")

    print("\n【2】文案侧词表：护肤口径")
    cat, hits, conflict = C.detect_copy(SKIN_COPY)
    check("气垫霜卖点 → skincare", cat == C.SKIN, f"得到 {cat} hits={hits}")
    check("无冲突说明", not conflict, str(conflict))

    cat, _, _ = C.detect_copy({"points": ["上脸不搓泥，妆效自然"]})
    check("上脸+妆效 → skincare", cat == C.SKIN, f"得到 {cat}")

    print("\n【3】不该误伤：中性文案")
    cat, _, _ = C.detect_copy({"title": "新品上市", "subtitle": "限时优惠"})
    check("纯营销词 → unknown（不瞎判）", cat == C.UNKNOWN, f"得到 {cat}")

    cat, _, _ = C.detect_copy({"points": ["保湿补水"]})
    check("仅效果词短语 → unknown", cat == C.UNKNOWN, f"得到 {cat}")

    cat, _, _ = C.detect_copy({"points": ["保湿补水", "提亮修护"]})
    check("效果词堆叠仍 → unknown（不定性，避免误伤）", cat == C.UNKNOWN, f"得到 {cat}")

    cat, hits, _ = C.detect_copy({"title": "高保湿面霜 50g"})
    check("出现品类名『面霜』→ skincare", cat == C.SKIN, f"得到 {cat} hits={hits}")

    cat, hits, _ = C.detect_copy({"subtitle": "60 粒装 每瓶"})
    check("『60 粒装』→ oral", cat == C.ORAL, f"得到 {cat} hits={hits}")

    print("\n【4】文案自身矛盾")
    cat, hits, conflict = C.detect_copy(
        {"title": "每天一粒", "points": ["上脸服帖不卡粉"]}
    )
    check("口服+护肤同时出现 → 判矛盾", cat == C.UNKNOWN and bool(conflict), f"cat={cat}")
    res = C.check({"title": "每天一粒", "points": ["上脸服帖"]}, None, {})
    check("矛盾时 ok=False", res.ok is False, str(res.reasons))

    print("\n【5】跨品类冲突（素材=气垫霜）")
    orig = C.detect_image
    C.detect_image = fake_image(C.SKIN)
    try:
        res = C.check(ORAL_COPY, object(), {})
        check("口服文案 × 护肤素材 → 拦", res.ok is False, str(res.to_dict()))
        check("原因说人话（提到气垫霜/每天一粒）",
              any("气垫" in r or "每天一粒" in r for r in res.reasons), str(res.reasons))

        res = C.check(SKIN_COPY, object(), {})
        check("护肤文案 × 护肤素材 → 放", res.ok is True, str(res.reasons))

        C.detect_image = fake_image(C.ORAL)
        res = C.check(SKIN_COPY, object(), {})
        check("护肤文案 × 口服素材 → 拦", res.ok is False, str(res.reasons))
        res = C.check(ORAL_COPY, object(), {})
        check("口服文案 × 口服素材 → 放", res.ok is True, str(res.reasons))

        C.detect_image = fake_image(C.UNKNOWN)
        res = C.check(ORAL_COPY, object(), {})
        check("素材品类不明 → 不拦（不瞎猜），但标注未校验",
              res.ok is True and any("未校验" in r for r in res.reasons), str(res.reasons))
    finally:
        C.detect_image = orig

    print("\n【6】用户显式声明优先（省一次模型调用）")
    called = {"n": 0}

    def counting(im, cfg):
        called["n"] += 1
        return C.SKIN, "", "fake"

    C.detect_image = counting
    try:
        res = C.check(ORAL_COPY, object(), {}, declared="oral")
        check("声明 oral → 不冲突放行", res.ok is True, str(res.reasons))
        check("声明生效时不调模型", called["n"] == 0, f"被调了 {called['n']} 次")
        check("依据标为『用户声明』", res.source == "用户声明", res.source)

        res = C.check(ORAL_COPY, object(), {}, declared="skincare")
        check("声明 skincare × 口服文案 → 仍拦（文案写错了）", res.ok is False, str(res.reasons))
    finally:
        C.detect_image = orig

    print("\n【7】assert_consistent 抛异常")
    C.detect_image = fake_image(C.SKIN)
    try:
        try:
            C.assert_consistent(ORAL_COPY, object(), {})
            check("冲突时应抛 CategoryError", False, "没有抛")
        except C.CategoryError as exc:
            check("冲突时抛 CategoryError", True)
            check("异常信息含中文解释", "气垫" in str(exc) or "护肤" in str(exc), str(exc))
        try:
            C.assert_consistent(SKIN_COPY, object(), {})
            check("一致时不抛", True)
        except C.CategoryError as exc:
            check("一致时不该抛", False, str(exc))
    finally:
        C.detect_image = orig

    print("\n【8】无模型可用时降级")
    orig_avail = C.judge.available
    C.judge.available = lambda cfg: (False, "模型未开通")
    try:
        res = C.check(SKIN_COPY, object(), {})
        check("无模型 → 不阻断，标注未校验", res.ok is True, str(res.to_dict()))
        check("checked_image=False", res.checked_image is False, "")
    finally:
        C.judge.available = orig_avail

    print("\n" + "=" * 62)
    if FAIL == 0:
        print(f"全部通过（{PASS} 条）")
    else:
        print(f"失败 {FAIL} 条 / 共 {PASS + FAIL} 条")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
