"""HTTP 服务（仅标准库）。

刻意不用 FastAPI：本机装不上依赖时这版也能直接跑。路由已按模块切好，
将来换成 FastAPI 只是把 Handler 里的分支搬成装饰器，业务层不用动。
"""

from __future__ import annotations

import json
import mimetypes
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config as cfgmod
from . import jobs
from . import store
from . import templates
from .config import DATA_DIR, WEB_DIR
from .engines.base import EngineError
from .engines.registry import describe
from .pipeline.run import run_batch
from .schemas import BatchRequest, ConfigPatch

MAX_BODY = 96 * 1024 * 1024  # 96MB，够一次传十几张手机原图


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartPS/0.1"
    protocol_version = "HTTP/1.1"

    # ─── 基础输出 ───
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json({"error": "文件不存在"}, 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"error": msg}, code)

    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
        pass

    def handle_one_request(self) -> None:
        """客户端提前断开（浏览器刷新、探活）不算错误，别刷栈。"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ─── GET ───
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")

        if path == "/api/health":
            return self._json({"ok": True, "service": "smart-ps", "version": "0.1.0"})

        if path == "/api/config":
            return self._json(cfgmod.masked(cfgmod.load()))

        if path == "/api/engines":
            return self._json({"engines": describe(cfgmod.load())})

        if path == "/api/templates":
            return self._json({"templates": [t.to_dict() for t in templates.ALL]})

        if path == "/api/batches":
            return self._json({"batches": jobs.list_recent()})

        if path.startswith("/api/batches/"):
            rest = unquote(path[len("/api/batches/") :]).strip("/")
            if "/" in rest:
                return self._err("未找到", 404)
            batch = store.get_batch(rest)
            if batch:
                return self._json(jobs.attach(batch))
            job = jobs.get(rest)
            if job:
                return self._json(job)
            return self._err("批次不存在", 404)

        if path.startswith("/files/"):
            rel = unquote(path[len("/files/") :])
            try:
                target = (DATA_DIR / rel).resolve()
                target.relative_to(DATA_DIR.resolve())
            except (ValueError, OSError):
                return self._err("非法路径", 403)
            return self._file(target)

        return self._err("未找到", 404)

    # ─── POST ───
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._err("请求体过大（>96MB）", 413)

        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return self._err("请求体不是合法 JSON")

        if path == "/api/config":
            try:
                patch = ConfigPatch(**payload).model_dump(exclude_none=True)
            except Exception as exc:
                return self._err(f"配置格式错误：{exc}")
            cfgmod.save(patch)
            return self._json({"ok": True, "config": cfgmod.masked(cfgmod.load())})

        if path == "/api/batches":
            try:
                req = BatchRequest(**payload)
            except Exception as exc:
                return self._err(f"参数错误：{exc}", 422)

            # ?sync=1：同步跑完再返回（调试、自动化测试用）。
            # 默认异步：先给 id，前端轮询 GET /api/batches/{id}。
            sync = "sync=1" in (urlparse(self.path).query or "")
            if sync:
                try:
                    batch = run_batch(req)
                except (EngineError, ValueError) as exc:
                    return self._err(str(exc), 400)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    return self._err(f"内部错误：{exc}", 500)
                return self._json(batch)

            job = jobs.submit(req)
            return self._json(job, 202)

        # ── 审核与导出 ──
        if path.startswith("/api/batches/"):
            rest = unquote(path[len("/api/batches/") :]).strip("/")
            parts = rest.split("/")
            batch = store.get_batch(parts[0])
            if not batch:
                return self._err("批次不存在", 404)

            if len(parts) == 2 and parts[1] == "review":
                return self._review(batch, payload)

            if len(parts) == 2 and parts[1] == "export":
                return self._export(batch, payload)

        return self._err("未找到", 404)

    # ─── 审核：选定 / 淘汰 / 复位 ───
    def _review(self, batch: dict, payload: dict) -> None:
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            return self._err("index 必须是整数", 422)
        action = str(payload.get("action") or "").strip()
        note = str(payload.get("note") or "").strip()

        target = next((c for c in batch.get("candidates", []) if c.get("index") == index), None)
        if target is None:
            return self._err(f"候选 {index} 不存在", 404)

        if action == "choose":
            for c in batch["candidates"]:
                c["chosen"] = c["index"] == index
                c["review"] = "chosen" if c["index"] == index else (
                    c.get("review") if c.get("review") == "rejected" else "pending"
                )
        elif action == "reject":
            target["chosen"] = False
            target["review"] = "rejected"
        elif action == "reset":
            target["chosen"] = False
            target["review"] = "pending"
        else:
            return self._err("action 只能是 choose / reject / reset", 422)

        if note:
            target["note"] = note
        target["reviewed_at"] = datetime.now().isoformat(timespec="seconds")

        chosen = [c["index"] for c in batch["candidates"] if c.get("chosen")]
        batch["review_state"] = {
            "chosen": chosen,
            "rejected": [c["index"] for c in batch["candidates"] if c.get("review") == "rejected"],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        store.save_batch(batch)
        return self._json(batch)

    # ─── 导出 ───
    def _export(self, batch: dict, payload: dict) -> None:
        from .export import export_batch  # 局部导入：没导出需求时不加载

        indexes = payload.get("indexes")
        if not indexes:
            indexes = [c["index"] for c in batch.get("candidates", []) if c.get("chosen")]
        if not indexes:
            return self._err("没有指定候选，也没有已选定的候选", 422)
        try:
            files = export_batch(
                batch,
                [int(i) for i in indexes],
                size=payload.get("size"),
                fmt=str(payload.get("fmt") or "png"),
                quality=int(payload.get("quality") or 95),
            )
        except ValueError as exc:
            return self._err(str(exc), 422)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._err(f"导出失败：{exc}", 500)
        return self._json({"batch": batch["id"], "files": files})


def main() -> None:
    cfg = cfgmod.load()
    host = str(cfg.get("app", {}).get("host") or "127.0.0.1")
    port = int(cfg.get("app", {}).get("port") or 8765)
    (DATA_DIR / "batches").mkdir(parents=True, exist_ok=True)
    fixed = jobs.recover()

    srv = ThreadingHTTPServer((host, port), Handler)
    print("=" * 62)
    print("  智能 PS · 初版框架")
    print(f"  打开：http://{host}:{port}")
    print(f"  配置：{cfgmod.CONFIG_PATH}")
    print(f"  数据：{DATA_DIR}")
    print("  API 填入口：界面右上角「设置」，或编辑上面这个 config.toml")
    if fixed:
        print(f"  已把 {fixed} 个中断任务标记为失败")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
