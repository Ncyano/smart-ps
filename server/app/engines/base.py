"""引擎契约。

所有云端/本地生成能力都实现同一个接口，上层（产线）不关心是 HTTP 还是本地推理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Capabilities:
    max_px: int = 2048 * 2048
    refs: int = 5  # 可接受的参考图上限
    mask: bool = False  # 是否支持蒙版入参
    transparent: bool = False  # 是否支持透明背景输出
    cost_cny: float = 0.0  # 单张成本（元）
    latency_p50: float = 0.0  # 典型耗时（秒）
    # 单次请求最多能产出几张。方舟的 n 参数实测无效（恒回 1 张），
    # 所以多候选必须由产线按这个值拆分请求并发执行。
    batch: int = 1


@dataclass
class RefImage:
    path: Path
    role: str  # "content" | "style"
    label: str = ""


@dataclass
class GenRequest:
    prompt: str
    negative: str = ""
    refs: list[RefImage] = field(default_factory=list)
    n: int = 3
    size: str = "2K"
    seed: int | None = None


@dataclass
class GenResult:
    images: list[Path]
    engine: str
    cost_cny: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class EngineError(RuntimeError):
    pass


class Engine:
    id: str = "base"
    caps: Capabilities = Capabilities()

    def available(self) -> tuple[bool, str]:
        """返回 (是否可用, 不可用原因)。缺 Key 时应当返回 False 而不是抛错。"""
        return True, ""

    def generate(self, req: GenRequest, out_dir: Path) -> GenResult:
        raise NotImplementedError
