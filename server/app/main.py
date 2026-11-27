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

from PIL import Image

from . import config as cfgmod
from . import gate
from . import jobs
from . import redo
from . import review
from . import rights
from . import scenes
from . import store
from . import taxonomy
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

        if path == "/api/taxonomy":
            return self._json(taxonomy.load().to_dict())

        if path == "/api/feedback":
            # 反馈统计：下次该改哪里，看这里而不是凭印象
            return self._json({"stats": review.stats(),
                               "suggestions": review.suggestions(),
                               "issues": review.ISSUES,
                               "dispositions": review.DISPOSITIONS})

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

        if path == "/api/scenes/detect":
            # 贴屏校准第一步：传一张场景照，回四角 + 置信度。
            # 置信度不足时前端把四角画出来让人拖，不要自动出图。
            payload_url = str(payload.get("data_url") or "")
            if not payload_url.startswith("data:"):
                return self._err("需要 data_url（data:image/...;base64,...）", 422)
            try:
                p = store.save_payload(payload_url, str(payload.get("name") or "scene.png"),
                                       DATA_DIR / "scenes" / "incoming")
                with Image.open(p) as im:
                    cal = scenes.load(im) or scenes.detect(im)
                    if cal.method == "auto" and not cal.needs_manual:
                        scenes.save(im, cal)
                    w, h = im.size
            except Exception as exc:  # noqa: BLE001
                return self._err(f"场景解析失败：{exc}", 422)
            return self._json({"calibration": cal.to_dict(), "size": [w, h],
                               "min_confidence": scenes.MIN_CONFIDENCE})

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

            # 结构化审核意见：问题类型 + 处理方式，落日志可统计
            if len(parts) == 2 and parts[1] == "feedback":
                try:
                    index = int(payload.get("index"))
                except (TypeError, ValueError):
                    return self._err("index 必须是整数", 422)
                try:
                    entry = review.record(
                        batch, index,
                        payload.get("issues") or [],
                        disposition=str(payload.get("disposition") or "rejected"),
                        note=str(payload.get("note") or ""),
                        reviewer=str(payload.get("reviewer") or payload.get("by") or "审核人"),
                    )
                except ValueError as exc:
                    return self._err(str(exc), 422)
                gate.apply(batch)
                store.save_batch(batch)
                return self._json({"ok": True, "entry": entry, "batch": batch})

            # 定向重做：先出方案（要不要花钱），再执行本地能修的那部分
            if len(parts) == 2 and parts[1] == "redo":
                try:
                    index = int(payload.get("index"))
                except (TypeError, ValueError):
                    return self._err("index 必须是整数", 422)
                issues = payload.get("issues") or []
                if payload.get("plan_only"):
                    return self._json(redo.plan(batch, index, issues))
                corners = payload.get("corners")
                try:
                    out = redo.local_redo(
                        batch, index, issues,
                        copy=payload.get("copy") or None,
                        corners=[list(p) for p in corners] if corners else None,
                        reviewer=str(payload.get("reviewer") or payload.get("by") or "审核人"),
                    )
                except ValueError as exc:
                    return self._err(str(exc), 422)
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    return self._err(f"重做失败：{exc}", 500)
                return self._json({"batch": out["batch"], "steps": out["steps"],
                                   "entry": out["entry"], "needs_model": out["needs_model"],
                                   "resubmit": out["resubmit"]})

            # 手动重试：有检查点就续跑，不重复付费
            if len(parts) == 2 and parts[1] == "retry":
                try:
                    return self._json(jobs.retry(parts[0]))
                except ValueError as exc:
                    return self._err(str(exc), 422)

            # 补声明素材授权：老批次或忘记填的，导出前后都能补，不必重新生成
            if len(parts) == 2 and parts[1] == "rights":
                declared = str(payload.get("rights") or "").strip()
                r = rights.resolve(declared)
                batch["rights"] = r.to_dict()
                store.save_batch(batch)
                return self._json({"ok": True, "rights": batch["rights"], "batch": batch["id"]})

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
        elif action in ("confirm", "unconfirm"):
            # 主体/内容人工确认。这是唯一能把"待人工"翻成"可交付"的动作，
            # 所以必须记人和时间——不记名的确认等于没确认。
            target["subject_confirmed"] = action == "confirm"
            target["subject_confirmed_by"] = str(payload.get("by") or "审核人") if action == "confirm" else ""
            target["subject_confirmed_at"] = datetime.now().isoformat(timespec="seconds") \
                if action == "confirm" else ""
        else:
            return self._err("action 只能是 choose / reject / reset / confirm / unconfirm", 422)

        if note:
            target["note"] = note
        target["reviewed_at"] = datetime.now().isoformat(timespec="seconds")

        chosen = [c["index"] for c in batch["candidates"] if c.get("chosen")]
        batch["review_state"] = {
            "chosen": chosen,
            "rejected": [c["index"] for c in batch["candidates"] if c.get("review") == "rejected"],
            "confirmed": [c["index"] for c in batch["candidates"] if c.get("subject_confirmed")],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        # 确认/淘汰会改变闸门结论，必须重算一次，不能留着旧结论
        gate.apply(batch)
        store.save_batch(batch)
        return self._json(batch)

    # ─── 导出 ───
    def _export(self, batch: dict, payload: dict) -> None:
        from .export import export_batch  # 局部导入：没导出需求时不加载

        formal = bool(payload.get("formal"))
        if formal:
            # 正式交付：必须是"授权 ok + 有候选全项通过"。这条不能被任何分数绕过。
            ok, why = gate.formal_ready(batch)
            if not ok:
                return self._err(f"未通过正式交付闸门：{why}", 409)

        indexes = payload.get("indexes")
        if not indexes:
            keep = ("deliverable",) if formal else ("deliverable", "manual", "internal_only")
            indexes = [c["index"] for c in batch.get("candidates", [])
                       if c.get("chosen") and (c.get("delivery") or {}).get("status") in keep]
        if not indexes:
            return self._err("没有指定候选，也没有已选定且通过闸门的候选", 422)
        try:
            files = export_batch(
                batch,
                [int(i) for i in indexes],
                size=payload.get("size"),
                fmt=str(payload.get("fmt") or "png"),
                quality=int(payload.get("quality") or 95),
                formal=formal,
            )
        except ValueError as exc:
            return self._err(str(exc), 422)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._err(f"导出失败：{exc}", 500)
        gate_result = rights.gate(batch)
        return self._json({"batch": batch["id"], "files": files, "rights": gate_result,
                           "formal": formal,
                           "delivery": batch.get("delivery")})


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
    if fixed.get("resumed"):
        print(f"  已从检查点续跑 {fixed['resumed']} 个中断任务（不重复扣费）")
    if fixed.get("failed"):
        print(f"  已把 {fixed['failed']} 个无法续跑的任务标记为失败")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
