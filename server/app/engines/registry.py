"""引擎注册与路由。

预留的扩展位：Qwen / GPT Image / Nano 都按 base.Engine 实现后在这里注册即可，
产线代码不需要改动。
"""

from __future__ import annotations

from .base import Engine, EngineError
from .mock import MockEngine
from .seedream import SeedreamEngine


def build_all(cfg: dict) -> dict[str, Engine]:
    engines_cfg = cfg.get("engines", {})
    return {
        "mock": MockEngine(engines_cfg.get("mock")),
        "seedream": SeedreamEngine(engines_cfg.get("seedream")),
        # TODO(M4): "qwen": QwenImageEngine(...), "openai": GptImageEngine(...)
    }


def resolve(cfg: dict, requested: str | None) -> tuple[Engine, str]:
    """返回 (引擎, 提示信息)。请求的引擎不可用时回退到 mock，并把原因带出去。"""
    engines = build_all(cfg)
    name = (requested or cfg.get("engine", {}).get("default") or "mock").strip()
    engine = engines.get(name)
    if engine is None:
        raise EngineError(f"未知引擎：{name}（可选：{', '.join(engines)}）")

    ok, why = engine.available()
    if ok:
        return engine, ""
    return engines["mock"], f"{name} 不可用：{why}。已回退到 mock，本次不会产生费用。"


def describe(cfg: dict) -> list[dict]:
    out = []
    for name, engine in build_all(cfg).items():
        ok, why = engine.available()
        out.append(
            {
                "id": name,
                "available": ok,
                "reason": why,
                "caps": {
                    "refs": engine.caps.refs,
                    "transparent": engine.caps.transparent,
                    "cost_cny": engine.caps.cost_cny,
                    "latency_p50": engine.caps.latency_p50,
                },
            }
        )
    return out
