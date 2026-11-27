"""火山方舟 Seedream 适配器 —— 真正的「API 填入口」在这里消费配置。

配置位置（三选一，优先级从高到低）：
  1. Web 界面 → 设置 → 填写 API Key（写入 config/config.toml）
  2. 直接编辑 config/config.toml 的 [engines.seedream]
  3. 环境变量 ARK_API_KEY（作为兜底，不落盘）

模型建议：
  doubao-seedream-5-0-flash-260915  0.12 元/张，1K/1.5K/2K 同价，支持多参考图
  ★模型 ID 必须带日期后缀。写成 doubao-seedream-5-0-flash 会返回 404
    InvalidEndpointOrModel.NotFound（报错信息说的是"不存在或无权限"，实际只是少了后缀）
  doubao-seedream-5-0-pro    0.3 元/张，质感更强

注意：请求体字段以官方文档为准（https://docs.volcengine.com/docs/ark/image-generation-api）。
若接口变更，只需要改本文件里的 _build_body / _parse 两个函数，上层不用动。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx

from .base import Capabilities, Engine, EngineError, GenRequest, GenResult

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"


class SeedreamEngine(Engine):
    id = "seedream"
    caps = Capabilities(
        max_px=2048 * 2048, refs=10, mask=False, transparent=True,
        cost_cny=0.12, latency_p50=6.0,
        # 方舟的 n 参数实测无效（恒回 1 张），多候选由产线拆成多次并发请求
        batch=1,
    )

    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}

    # ─── 可用性 ───
    @property
    def api_key(self) -> str:
        return str(self.cfg.get("api_key") or os.environ.get("ARK_API_KEY") or "").strip()

    @property
    def base_url(self) -> str:
        return str(self.cfg.get("base_url") or _DEFAULT_BASE).rstrip("/")

    @property
    def model(self) -> str:
        return str(self.cfg.get("model") or "doubao-seedream-5-0-flash-260915")

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未填写 API Key（设置 → 引擎 → 火山方舟 API Key，或写入 config/config.toml）"
        return True, ""

    # ─── 生成 ───
    def generate(self, req: GenRequest, out_dir: Path) -> GenResult:
        ok, why = self.available()
        if not ok:
            raise EngineError(why)

        body = self._build_body(req)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = float(self.cfg.get("timeout") or 180)
        url = f"{self.base_url}/images/generations"

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise EngineError(f"请求失败：{exc}") from exc

        if resp.status_code >= 400:
            raise EngineError(f"HTTP {resp.status_code}：{resp.text[:600]}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise EngineError(f"返回不是 JSON：{resp.text[:300]}") from exc

        images = self._parse(payload, out_dir)
        if not images:
            raise EngineError(f"未解析到图片，原始返回：{str(payload)[:600]}")

        return GenResult(
            images=images,
            engine=self.id,
            cost_cny=self.caps.cost_cny * len(images),
            meta={"model": self.model, "usage": payload.get("usage", {})},
        )

    # ─── 请求体 ───
    def _build_body(self, req: GenRequest) -> dict[str, Any]:
        prompt = self._compose_prompt(req)
        body: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": req.size or self.cfg.get("size") or "2K",
            "response_format": "url",
            "watermark": bool(self.cfg.get("watermark", False)),
        }
        if req.seed is not None:
            body["seed"] = req.seed

        # 参考图：内容在前、风格在后（顺序直接影响保真，别乱）
        refs = [r for r in req.refs][: self.caps.refs]
        if refs:
            encoded = [self._data_url(r.path) for r in refs]
            body["image"] = encoded if len(encoded) > 1 else encoded[0]
        return body

    def _compose_prompt(self, req: GenRequest) -> str:
        """把「角色标注」显式写进 prompt —— 这是防污染的第一道闸。"""
        lines: list[str] = []
        content_idx = [i + 1 for i, r in enumerate(req.refs) if r.role == "content"]
        style_idx = [i + 1 for i, r in enumerate(req.refs) if r.role == "style"]
        if content_idx:
            nums = "、".join(f"图{n}" for n in content_idx)
            lines.append(f"{nums} 是内容参考：产品/主体的形状、Logo、材质、比例需严格保留，不要重新设计。")
        if style_idx:
            nums = "、".join(f"图{n}" for n in style_idx)
            lines.append(f"{nums} 是风格参考：只借鉴色调、影调、光线质感与背景处理方式，禁止照搬其内容与构图。")
        if req.prompt:
            lines.append(req.prompt)
        if req.negative:
            lines.append(f"需要避免：{req.negative}")
        return "\n".join(lines)

    @staticmethod
    def _data_url(path: Path) -> str:
        raw = path.read_bytes()
        suffix = path.suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
        return f"data:image/{mime};base64,{base64.b64encode(raw).decode()}"

    # ─── 响应解析 ───
    def _parse(self, payload: dict, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        items = payload.get("data") or payload.get("images") or []
        saved: list[Path] = []

        for i, item in enumerate(items):
            if isinstance(item, str):
                item = {"url": item}
            target = out_dir / f"cand_{i + 1}.png"

            b64 = item.get("b64_json") or item.get("image_base64")
            if b64:
                target.write_bytes(base64.b64decode(b64))
                saved.append(target)
                continue

            url = item.get("url") or item.get("image_url")
            if url:
                try:
                    with httpx.Client(timeout=120, follow_redirects=True) as client:
                        r = client.get(url)
                        r.raise_for_status()
                    target.write_bytes(r.content)
                    saved.append(target)
                except httpx.HTTPError:
                    continue  # 单张下载失败不影响其余

        return saved
