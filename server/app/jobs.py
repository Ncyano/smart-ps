"""进程内任务队列。

为什么不直接用 Celery / Redis：
  这个项目承诺"开箱即跑、不装依赖"，多两个常驻服务会让部署复杂度翻倍。
  单机 2–4 个并发生成任务，进程内线程池完全够用。
  真的上多机时，把 `submit()` 换成往 Redis 推一条消息即可，上层接口不变。

状态机：
    queued → running → done
                     ↘ failed

状态落盘在 data/batches/<id>/job.json，所以：
  - 浏览器刷新、前端重连都能恢复进度
  - 服务重启后，把残留的 queued/running 标记为 interrupted（见 recover()）
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import store
from .config import DATA_DIR, load
from .pipeline.run import run_batch

_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}
_POOL: ThreadPoolExecutor | None = None


def _job_path(batch_id: str) -> Path:
    return store.batch_dir(batch_id) / "job.json"


def _write(batch_id: str, job: dict) -> None:
    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _job_path(batch_id).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def _update(batch_id: str, **fields) -> dict:
    with _LOCK:
        job = _MEM.setdefault(batch_id, {"id": batch_id})
        job.update(fields)
        _write(batch_id, job)
        return dict(job)


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        try:
            workers = int(load().get("app", {}).get("workers") or 2)
        except Exception:  # noqa: BLE001
            workers = 2
        _POOL = ThreadPoolExecutor(max_workers=max(1, min(workers, 8)), thread_name_prefix="gen")
    return _POOL


def submit(req, cfg: dict | None = None) -> dict:
    """创建任务并立即返回。真正的生成在后台线程里跑。"""
    batch_id = store.new_batch_id()
    job = {
        "id": batch_id,
        "status": "queued",
        "stage": "已排队",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
        "elapsed_s": 0.0,
        "error": "",
        "summary": {},
        "request": {
            "instruction": getattr(req, "instruction", ""),
            "engine": getattr(req, "engine", None),
            "candidates": getattr(req, "candidates", 0),
            "edit_type": getattr(req, "edit_type", "auto"),
            "materials": len(getattr(req, "materials", []) or []),
            "exemplars": len(getattr(req, "exemplars", []) or []),
        },
    }
    with _LOCK:
        _MEM[batch_id] = job
        _write(batch_id, job)

    _pool().submit(_run, batch_id, req, cfg)
    return dict(job)


def _run(batch_id: str, req, cfg: dict | None) -> None:
    t0 = time.time()

    def progress(stage: str, p: float) -> None:
        _update(batch_id, stage=stage, progress=round(float(p), 3),
                elapsed_s=round(time.time() - t0, 2))

    try:
        _update(batch_id, status="running", stage="开始处理", progress=0.02)
        batch = run_batch(req, cfg=cfg, batch_id=batch_id, on_progress=progress)
        deliverable = sum(1 for c in batch.get("candidates", []) if c.get("verdict") == "可交付")
        _update(
            batch_id,
            status="done",
            stage="已完成",
            progress=1.0,
            elapsed_s=round(time.time() - t0, 2),
            summary={
                "produced": batch.get("produced", 0),
                "requested": batch.get("requested", 0),
                "cost_cny": batch.get("cost_cny", 0.0),
                "deliverable": deliverable,
                "engine": batch.get("engine", ""),
                "engine_note": batch.get("engine_note", ""),
            },
        )
    except Exception as exc:  # noqa: BLE001 任务失败必须落到状态里，不能只打日志
        _update(
            batch_id,
            status="failed",
            stage="失败",
            error=str(exc)[:800],
            elapsed_s=round(time.time() - t0, 2),
        )


def get(batch_id: str) -> dict | None:
    with _LOCK:
        if batch_id in _MEM:
            return dict(_MEM[batch_id])
    p = _job_path(batch_id)
    if p.exists():
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
            with _LOCK:
                _MEM[batch_id] = job
            return job
        except Exception:  # noqa: BLE001
            return None
    return None


def is_running(batch_id: str) -> bool:
    job = get(batch_id) or {}
    return job.get("status") in ("queued", "running")


def attach(batch: dict) -> dict:
    """把任务状态附到批次详情上，前端一次请求就能拿到全部信息。"""
    job = get(batch.get("id", "")) or {}
    if job:
        batch["job"] = {
            "status": job.get("status"),
            "stage": job.get("stage"),
            "progress": job.get("progress", 0.0),
            "elapsed_s": job.get("elapsed_s", 0.0),
            "error": job.get("error", ""),
        }
    return batch


def list_recent(limit: int = 50) -> list[dict]:
    """任务列表：已完成读 batch.json，未完成（还在跑）读 job.json。"""
    root = DATA_DIR / "batches"
    if not root.exists():
        return []
    items: list[dict] = []
    for p in sorted((q for q in root.iterdir() if q.is_dir()), reverse=True)[:limit]:
        bid = p.name
        bf = p / "batch.json"
        if bf.exists():
            try:
                items.append(attach(json.loads(bf.read_text(encoding="utf-8"))))
                continue
            except Exception:  # noqa: BLE001
                pass
        job = get(bid)
        if job:
            items.append(
                {
                    "id": bid,
                    "status": job.get("status"),
                    "created_at": job.get("created_at", ""),
                    "instruction": (job.get("request") or {}).get("instruction", ""),
                    "engine": (job.get("request") or {}).get("engine") or "-",
                    "candidates": [],
                    "job": {
                        "status": job.get("status"),
                        "stage": job.get("stage"),
                        "progress": job.get("progress", 0.0),
                        "elapsed_s": job.get("elapsed_s", 0.0),
                        "error": job.get("error", ""),
                    },
                }
            )
    return items


def recover() -> int:
    """服务启动时调用：上次崩溃/重启遗留的 running 任务标记为中断。"""
    fixed = 0
    root = DATA_DIR / "batches"
    if not root.exists():
        return 0
    for p in root.iterdir():
        f = p / "job.json"
        if not f.exists():
            continue
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if job.get("status") in ("queued", "running"):
            job["status"] = "failed"
            job["stage"] = "服务重启，任务已中断"
            job["error"] = job.get("error") or "服务重启导致任务中断，请重新提交"
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            f.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            fixed += 1
    return fixed
