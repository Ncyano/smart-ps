"""进程内任务队列（带检查点恢复与幂等）。

为什么不直接用 Celery / Redis：
  这个项目承诺"开箱即跑、不装依赖"，多两个常驻服务会让部署复杂度翻倍。
  单机 2–4 个并发生成任务，进程内线程池完全够用。
  真的上多机时，把 `submit()` 换成往 Redis 推一条消息即可，上层接口不变。

状态机：
    queued → running → done
                     ↘ failed

状态落盘在 data/batches/<id>/job.json，所以浏览器刷新、前端重连都能恢复进度。

## 服务重启怎么办

旧版直接把 queued/running 标成 failed，用户要重跑，**已经花掉的生成费白花了**。
现在 `pipeline/run.py` 每完成一步就写 `resume.json`，最贵的那步（④ 生成）
一旦完成，重启后从断点续跑，不再调用模型。

判断依据：检查点存在、`stage != done`、且已经有素材落盘 → 可以续跑；
否则（连素材都还没存完）才标失败。

## 幂等

前端可能因为超时重发同一个请求。重复提交会**重复扣费**，所以支持 `request_id`：
同一个 id 再来，直接返回上次的任务，不再排队。

索引存在 `data/requests.json`，只记 id → batch_id，不存请求体（可能几十 MB）。
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import store
from .config import DATA_DIR, load
from .pipeline.run import load_ckpt, run_batch

_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}
_POOL: ThreadPoolExecutor | None = None
REQUESTS_PATH = DATA_DIR / "requests.json"


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


# ─── 幂等索引 ───


def _read_index() -> dict:
    if not REQUESTS_PATH.exists():
        return {}
    try:
        return json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_index(idx: dict) -> None:
    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def find_by_request(request_id: str) -> dict | None:
    rid = str(request_id or "").strip()
    if not rid:
        return None
    bid = _read_index().get(rid)
    return get(bid) if bid else None


# ─── 提交 ───


def submit(req, cfg: dict | None = None) -> dict:
    """创建任务并立即返回。真正的生成在后台线程里跑。"""
    rid = str(getattr(req, "request_id", "") or "").strip()
    if rid:
        prev = find_by_request(rid)
        if prev:
            prev["_idempotent"] = True
            return prev

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
        "request_id": rid,
        "summary": {},
        "request": {
            "instruction": getattr(req, "instruction", ""),
            "engine": getattr(req, "engine", None),
            "candidates": getattr(req, "candidates", 0),
            "template": getattr(req, "template", ""),
            "materials": len(getattr(req, "materials", []) or []),
            "exemplars": len(getattr(req, "exemplars", []) or []),
        },
    }
    with _LOCK:
        _MEM[batch_id] = job
        _write(batch_id, job)
        if rid:
            idx = _read_index()
            idx[rid] = batch_id
            _write_index(idx)

    _pool().submit(_run, batch_id, req, cfg, False)
    return dict(job)


def _run(batch_id: str, req=None, cfg: dict | None = None, resume: bool = False) -> None:
    t0 = time.time()

    def progress(stage: str, p: float) -> None:
        _update(batch_id, stage=stage, progress=round(float(p), 3),
                elapsed_s=round(time.time() - t0, 2))

    try:
        _update(batch_id, status="running",
                stage="从检查点续跑" if resume else "开始处理", progress=0.02)
        ckpt = load_ckpt(batch_id) if resume else None
        if resume and ckpt:
            req = _resume_req(ckpt) if req is None else req
        batch = run_batch(req, cfg=cfg, batch_id=batch_id, on_progress=progress, resume=ckpt)
        summary = batch.get("delivery", {}).get("summary", {})
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
                "deliverable": summary.get("deliverable", 0),
                "manual": summary.get("manual", 0),
                "rejected": summary.get("rejected", 0),
                "formal_ready": bool(batch.get("delivery", {}).get("formal_ready")),
                "engine": batch.get("engine", ""),
                "engine_note": batch.get("engine_note", ""),
            },
        )
    except Exception as exc:  # noqa: BLE001 任务失败必须落到状态里，不能只打日志
        # 已经有检查点的，留着让人点"重试"，不要直接判废
        ckpt = load_ckpt(batch_id)
        resumable = bool(ckpt and ckpt.get("stage") != "done" and ckpt.get("materials"))
        _update(
            batch_id,
            status="failed",
            stage="失败（可重试）" if resumable else "失败",
            error=str(exc)[:800],
            resumable=resumable,
            elapsed_s=round(time.time() - t0, 2),
        )


def _resume_req(ckpt: dict) -> SimpleNamespace:
    """从检查点拼一个够用的请求对象。

    为什么不是把原始请求（含 base64 图）存进 resume.json：一批素材可能 90MB，
    每次检查点都重写一遍纯属自找麻烦。真正需要重跑的步骤已经缓存在检查点里，
    这里只需要几个标量。
    """
    meta = ckpt.get("meta") or {}
    return SimpleNamespace(
        instruction=meta.get("instruction") or "",
        engine=meta.get("engine"),
        candidates=int(ckpt.get("want") or 3),
        template=ckpt.get("template") or "scene",
        copy_text=ckpt.get("copy") or {},
        category=meta.get("category") or "auto",
        rights=meta.get("rights") or "unknown",
        claims=meta.get("claims") or [],
        materials=[],
        exemplars=[],
        before=None, after=None, scene=None, evidence=None,
        corners=ckpt.get("corners"),
        request_id=meta.get("request_id") or "",
    )


def retry(batch_id: str) -> dict:
    """手动重试：有检查点就续跑，没有就明确报错（不猜）。"""
    ckpt = load_ckpt(batch_id)
    if not ckpt or not ckpt.get("materials"):
        raise ValueError("没有可用的检查点，无法续跑；请重新提交")
    _update(batch_id, status="queued", stage="已排队（续跑）", progress=0.0, error="")
    _pool().submit(_run, batch_id, None, None, True)
    return get(batch_id) or {"id": batch_id}


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
            "resumable": bool(job.get("resumable")),
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
                        "resumable": bool(job.get("resumable")),
                    },
                }
            )
    return items


def recover() -> dict:
    """服务启动时调用：能续跑的续跑，不能的才标中断。

    返回 {"resumed": n, "failed": n}。
    """
    resumed = failed = 0
    root = DATA_DIR / "batches"
    if not root.exists():
        return {"resumed": 0, "failed": 0}
    for p in root.iterdir():
        f = p / "job.json"
        if not f.exists():
            continue
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if job.get("status") not in ("queued", "running"):
            continue
        ckpt = load_ckpt(p.name)
        resumable = bool(ckpt and ckpt.get("stage") != "done" and ckpt.get("materials"))
        if resumable:
            job["status"] = "queued"
            job["stage"] = "服务重启，从检查点续跑"
            job["resumable"] = True
            with _LOCK:
                _MEM[p.name] = job
            _pool().submit(_run, p.name, None, None, True)
            resumed += 1
        else:
            job["status"] = "failed"
            job["stage"] = "服务重启，任务已中断"
            job["error"] = job.get("error") or "服务重启导致任务中断，请重新提交"
            failed += 1
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        f.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        # 内存缓存也要同步，否则 get() 还会返回重启前的旧状态
        with _LOCK:
            _MEM[p.name] = job
    return {"resumed": resumed, "failed": failed}
