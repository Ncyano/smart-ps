"""语义判定层的离线测试（不需要网络，也不需要开通模型）。

为什么单独测：这条链路现在**在真机上永远走不到成功分支**（账号没开通视觉模型），
不写桩测试就等于交付了一段没被执行过的代码。这里用假的 HTTP 响应把
"解析 / 降级 / 只降不升" 三条规则钉死。

跑法：python scripts/test_judge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from server.app.pipeline import judge  # noqa: E402

CFG = {"engines": {"seedream": {"api_key": "test-key"}}, "quality": {"judge": "auto"}}
IMG = Image.new("RGB", (64, 64), (200, 200, 200))
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILS.append(name)


# ─── 解析：模型返回的 JSON 各种歪法 ───
def test_parse() -> None:
    r = judge._parse('{"same_product": true, "aspect_ok": false, "text_ok": true, "confidence": 88, "reason": "比例偏扁"}')
    check("标准 JSON", r is not None and r["same_product"] is True and r["aspect_ok"] is False
          and r["confidence"] == 88)

    r = judge._parse('```json\n{"same_product": false, "text_ok": true, "reason": "换款了"}\n```')
    check("带代码围栏", r is not None and r["same_product"] is False)

    r = judge._parse('判断如下：{"same_product": "false", "aspect_ok": "true", "text_ok": "true"} 完毕')
    check("带前后解释 + 字符串布尔", r is not None and r["same_product"] is False
          and r["aspect_ok"] is True)

    check("垃圾返回", judge._parse("我看不清这张图") is None)
    check("空返回", judge._parse("") is None)
    check("缺关键字段", judge._parse('{"reason": "x"}') is None)


# ─── 分档合并：只降不升 ───
def test_apply() -> None:
    ok = judge.JudgeResult(checked=True, same_product=True, aspect_ok=True, text_ok=True, confidence=90)
    t, notes = judge.apply_to_tier(ok, "A")
    check("全过不改档", t == "A" and notes == [])

    t, _ = judge.apply_to_tier(ok, "C")
    check("全过也不抬档", t == "C")

    bad = judge.JudgeResult(checked=True, same_product=False, confidence=95, reason="换款")
    t, notes = judge.apply_to_tier(bad, "A")
    check("不是同一款 → C", t == "C" and any("不是同一款" in n for n in notes))

    txt = judge.JudgeResult(checked=True, text_ok=False, confidence=70, reason="文字糊")
    t, _ = judge.apply_to_tier(txt, "A")
    check("文字可疑 → B", t == "B")

    asp = judge.JudgeResult(checked=True, aspect_ok=False, confidence=80, reason="变扁")
    t, _ = judge.apply_to_tier(asp, "A")
    check("比例可疑 → B", t == "B")

    unchecked = judge.JudgeResult(checked=False, error="模型未开通")
    t, notes = judge.apply_to_tier(unchecked, "A")
    check("没判定就不改档", t == "A" and notes == [])


# ─── 端到端：假 HTTP ───
class _Resp:
    def __init__(self, code: int, payload: dict):
        self.status_code = code
        self._p = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._p


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return self._resp


def _patch(resp) -> None:
    import httpx
    judge.httpx.Client = lambda *a, **k: _FakeClient(resp)
    judge.httpx.HTTPError = httpx.HTTPError


def test_judge_ok() -> None:
    payload = {"choices": [{"message": {"content":
        '{"same_product": true, "aspect_ok": true, "text_ok": false, "confidence": 77, "reason": "标签有错字"}'
    }}], "usage": {"total_tokens": 1500}}
    _patch(_Resp(200, payload))
    judge.reset_probe()
    r = judge.judge(IMG, IMG, CFG)
    check("成功分支：已判定", r.checked is True and r.text_ok is False and r.confidence == 77)
    check("成功分支：能记费用", r.cost_cny > 0)


def test_judge_not_open() -> None:
    _patch(_Resp(404, {"error": {"code": "ModelNotOpen", "message": "not activated"}}))
    judge.reset_probe()
    r = judge.judge(IMG, IMG, CFG)
    check("未开通：降级不抛错", r.checked is False and "未开通" in r.error)

    ok, why = judge.available(CFG)
    check("未开通：可用性缓存生效", ok is False and "未开通" in why)


def test_judge_off() -> None:
    judge.reset_probe()
    cfg = {**CFG, "quality": {"judge": "off"}}
    ok, why = judge.available(cfg)
    check("配置关闭：直接不可用", ok is False and "关闭" in why)


def main() -> int:
    print(f"语义判定层离线测试　模型候选 {judge.CANDIDATES[0]}")
    print("=" * 60)
    test_parse()
    test_apply()
    test_judge_ok()
    test_judge_not_open()
    test_judge_off()
    print("=" * 60)
    print(f"{'全部通过' if not FAILS else f'失败 {len(FAILS)} 条：' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
