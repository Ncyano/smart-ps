"""语义判定（视觉模型）：确定性度量答不了的那几个问题。

## 为什么要多一层

`fidelity.py` 只能回答"产品还在不在、糊没糊、比例歪没歪"，它是像素统计。
但设计师真正关心的是三件语义事：

1. 这**还是同一款产品**吗？（换包装、换 logo、换色，统计量看不出来）
2. 瓶身**文字有没有乱码**？（AI 生图最常见的翻车点）
3. 比例**明显被改**了吗？（17% 这种，`spread` 因为调色漂移测不准）

这三件只能靠看图说话，所以交给视觉模型。

## 实测能力边界（2026-11-26，doubao-seed-2-1-lite-260915）

| 用例 | 同一款 | 比例 | 把握 | 判对？ |
|---|---|---:|:--:|:--:|
| 真实候选 #1（单罐） | true | true | 99 | ✅ |
| 真实候选 #3（多罐陈列） | true | true | 98 | ✅ |
| 空白场景（无产品） | false | false | 100 | ✅ |
| 真人对照片（完全无关） | false | false | 100 | ✅ → C |
| 纵向压扁 17% | true | **true** | 98 | ❌ 没看出来 |
| 横向拉长 25% | true | **true** | 98 | ❌ 没看出来 |
| 旋转 90° | true | true | 99 | ❌ 没看出来 |

**结论：`same_product` 可信，`aspect_ok` 不可信。**
模型对比例极度过自信——17%/25% 的变形都说"比例正常，把握 98"。
所以 `aspect_ok` 只作提示，永远不要用它单独判死一张图；
比例的真判断目前只有两条路：`fidelity.spread` 抓 >32% 的明显变形，或者人眼。

`text_ok` 未单独构造负样本验证过（要造"文字乱码"的图），暂按"宁可错报 B 不要漏放"处理。

## 开通方法（已完成）

方舟控制台 → 模型广场 → 开通 `doubao-seed-2-1-lite`。当前 Key 已开通 seedream + 该模型。
成本约 0.002 元/张，远低于生成本身（0.12 元/张）。单张耗时 5–15s，产线里 4 路并发。
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from PIL import Image

from . import fidelity as fid

# 按"便宜且够用"排序。留空时依次尝试，第一个能通的就是它。
CANDIDATES = (
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-turbo-260628",
    "doubao-seed-2-0-lite-260428",
    "doubao-seed-2-1-pro-260915",
)

PROMPT = """你是电商图片质检员。图1是产品参考图，图2是待检成图（可能换过背景、光线、角度）。

只输出一个 JSON，不要解释、不要 markdown 代码块：
{"same_product": true/false, "aspect_ok": true/false, "text_ok": true/false, "confidence": 0-100, "reason": "不超过20字"}

判定口径：
- same_product：图2的主体是否为图1同一款产品。外观、包装结构、logo、材质、颜色一致才算 true；
  换成别款、包装被重新设计、logo 被改写都算 false。**允许**换背景、换光线、换拍摄角度。
- aspect_ok：图2产品的高宽比例是否与图1一致。明显变扁、拉长、变形为 false；轻微透视差异算 true。
- text_ok：图2产品上的文字与 logo 是否清晰可读、没有乱码/错字/糊成一片。
- confidence：你对以上判断的把握（0-100）。看不清就给低分。"""

# 探测结果缓存：避免每个批次都去撞一次 404
_PROBE: dict = {"until": 0.0, "ok": False, "why": "未探测", "model": ""}
_PROBE_TTL = 600.0


@dataclass
class JudgeResult:
    checked: bool = False
    same_product: bool | None = None
    aspect_ok: bool | None = None
    text_ok: bool | None = None
    confidence: int | None = None
    reason: str = ""
    model: str = ""
    cost_cny: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _data_url(im: Image.Image, max_side: int = 768) -> str:
    im = im.convert("RGB").copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _pick_model(cfg: dict) -> str:
    q = cfg.get("quality", {}) or {}
    want = str(q.get("judge_model") or "").strip()
    if want:
        return want
    if _PROBE["model"]:
        return str(_PROBE["model"])
    return CANDIDATES[0]


def _post(model: str, source: Image.Image, image: Image.Image, cfg: dict) -> dict:
    eng = (cfg.get("engines", {}) or {}).get("seedream", {}) or {}
    key = str(eng.get("api_key") or "").strip()
    base = str(eng.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    if not key:
        raise RuntimeError("未配置 API Key，无法调用视觉模型")

    max_side = int(((cfg.get("quality", {}) or {}).get("judge_max_side")) or 768)
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": _data_url(source, max_side)}},
                {"type": "image_url", "image_url": {"url": _data_url(image, max_side)}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "temperature": 0,
        "max_tokens": 220,
    }
    with httpx.Client(timeout=float(eng.get("timeout") or 180)) as c:
        r = c.post(f"{base}/chat/completions",
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}：{r.text[:300]}")
    return r.json()


def _parse(text: str) -> dict | None:
    """模型偶尔会包 ```json ... ``` 或加一句解释，宽松取第一个 JSON 对象。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return None

    def as_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "是", "yes", "1"):
                return True
            if s in ("false", "否", "no", "0"):
                return False
        if isinstance(v, (int, float)):
            return bool(v)
        return None

    out = {
        "same_product": as_bool(raw.get("same_product")),
        "aspect_ok": as_bool(raw.get("aspect_ok")),
        "text_ok": as_bool(raw.get("text_ok")),
        "reason": str(raw.get("reason") or "")[:60],
    }
    conf = raw.get("confidence")
    try:
        out["confidence"] = int(max(0, min(100, int(conf)))) if conf is not None else None
    except (TypeError, ValueError):
        out["confidence"] = None
    if out["same_product"] is None and out["text_ok"] is None:
        return None
    return out


def available(cfg: dict) -> tuple[bool, str]:
    """视觉模型能不能用。结果缓存 10 分钟，避免每个批次都撞一次 404。"""
    q = cfg.get("quality", {}) or {}
    if str(q.get("judge") or "auto").strip().lower() in ("off", "none", "0", "false"):
        return False, "已在配置里关闭（quality.judge = off）"
    if time.time() < _PROBE["until"]:
        return bool(_PROBE["ok"]), str(_PROBE["why"])

    model = _pick_model(cfg)
    eng = (cfg.get("engines", {}) or {}).get("seedream", {}) or {}
    key = str(eng.get("api_key") or "").strip()
    base = str(eng.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    if not key:
        _set_probe(False, "未配置 API Key", model)
        return False, "未配置 API Key"

    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": model,
                             "messages": [{"role": "user", "content": "hi"}],
                             "max_tokens": 2})
        if r.status_code == 200:
            _set_probe(True, "", model)
            return True, ""
        code = ""
        try:
            code = str(r.json().get("error", {}).get("code") or "")
        except ValueError:
            pass
        why = ("模型未开通（方舟控制台 → 模型广场 → 开通服务）"
               if code == "ModelNotOpen" else f"不可用：HTTP {r.status_code} {code}".strip())
        _set_probe(False, why, model)
        return False, why
    except httpx.HTTPError as exc:
        _set_probe(False, f"网络错误：{exc}", model)
        return False, f"网络错误：{exc}"


def _set_probe(ok: bool, why: str, model: str) -> None:
    _PROBE.update({"until": time.time() + _PROBE_TTL, "ok": ok, "why": why, "model": model})


def reset_probe() -> None:
    """改完配置/开通模型后调用，强制重新探测。"""
    _PROBE.update({"until": 0.0, "ok": False, "why": "未探测", "model": ""})


def judge(source: Image.Image, image: Image.Image, cfg: dict) -> JudgeResult:
    """判一张成图。任何异常都不抛出去——产线不能因为判定层挂了就停。"""
    ok, why = available(cfg)
    if not ok:
        return JudgeResult(checked=False, error=why, model=_pick_model(cfg))

    model = _pick_model(cfg)
    try:
        payload = _post(model, fid.as_rgb(source), fid.as_rgb(image), cfg)
        text = ""
        choices = payload.get("choices") or []
        if choices:
            text = str((choices[0].get("message") or {}).get("content") or "")
        parsed = _parse(text)
        if parsed is None:
            return JudgeResult(checked=False, error=f"返回无法解析：{text[:120]}", model=model)
        usage = payload.get("usage") or {}
        cost = 0.0
        tokens = int(usage.get("total_tokens") or 0)
        if tokens:
            cost = round(tokens / 1_000_000 * 0.8, 5)  # 粗估，仅用于记账
        return JudgeResult(checked=True, model=model, cost_cny=cost, **parsed)
    except Exception as exc:  # noqa: BLE001 判定层失败不影响交付
        return JudgeResult(checked=False, error=str(exc)[:200], model=model)


def apply_to_tier(result: JudgeResult, tier: str) -> tuple[str, list[str]]:
    """把语义判定并进档位。只降不升——视觉模型说好，也不该把 C 抬成 A。"""
    reasons: list[str] = []
    if not result.checked:
        return tier, reasons

    same, text, aspect = result.same_product, result.text_ok, result.aspect_ok
    conf = result.confidence if result.confidence is not None else 100

    if same is False:
        tier = "C"
        reasons.append(f"视觉模型判定不是同一款产品（把握 {conf}）——{result.reason}")
    elif text is False:
        tier = "C" if tier == "C" else "B"
        reasons.append(f"瓶身文字/Logo 可能乱码，需人工核对（把握 {conf}）——{result.reason}")
    elif aspect is False:
        # 只作提示：这个信号实测不可靠（17% 压扁它说"正常，把握 98"），
        # 但反过来它说"不对"时值得人看一眼，所以降 B 不降 C。
        tier = "C" if tier == "C" else "B"
        reasons.append(f"视觉模型认为比例被改动（把握 {conf}，仅供参考）——{result.reason}")
    return tier, reasons
