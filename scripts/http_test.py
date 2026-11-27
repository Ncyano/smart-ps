"""HTTP 端到端测试：走真实接口，验证上传/生成/静态文件访问。

前置：先启动服务（python -m server.app.main）
用法：python scripts/http_test.py
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

BASE = "http://127.0.0.1:8765"


def data_url(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def get(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.read()


def sample(seed: int, size=(640, 800)) -> Image.Image:
    im = Image.new("RGB", size, (40, 44, 58))
    d = ImageDraw.Draw(im)
    d.ellipse((150, 180, 490, 620), fill=(200 - seed * 9, 176, 166))
    d.rectangle((240, 340, 400, 520), fill=(74, 118, 198))
    return im


def main() -> int:
    print("health :", get("/api/health").decode())

    # ① 同步模式（?sync=1）：一次拿到完整结果，便于校验产线本身
    batch = post(
        "/api/batches?sync=1",
        {
            "instruction": "背景纯白，主体居中，保留 Logo 与材质",
            "engine": "mock",
            "candidates": 3,
            "materials": [{"name": f"s{i}.png", "data_url": data_url(sample(i))} for i in range(2)],
            "exemplars": [{"name": "ref.png", "data_url": data_url(sample(9))}],
        },
    )

    print("batch  :", batch["id"], batch["status"], f"{batch['cost_cny']} 元")
    print("pack   :", [r["role"] for r in batch["recipe"]["refs"]])

    for c in batch["candidates"]:
        raw = get(c["final_url"])
        print(
            f"cand{c['index']}: {len(raw):>7} bytes  "
            f"保真 {c['scores']['fidelity']} 风格 {c['scores']['style']} {c['verdict']}"
        )

    detail = json.loads(get(f"/api/batches/{batch['id']}").decode())
    print("回读   :", detail["id"], "候选数", len(detail["candidates"]))

    # ② 异步模式：立即返回 job，轮询到完成
    job = post(
        "/api/batches",
        {
            "instruction": "异步任务校验",
            "engine": "mock",
            "candidates": 2,
            "materials": [{"name": "s0.png", "data_url": data_url(sample(1))}],
        },
    )
    print("异步   :", job["id"], job["status"], job["stage"])

    stages = []
    # 上限放到 150 次（约 45s）：接了语义判定后每张候选多 5–15s 的视觉模型调用，
    # 原来的 60 次（18s）会撞在"其实还在跑"的时刻上误报失败。
    for _ in range(150):
        cur = json.loads(get(f"/api/batches/{job['id']}").decode())
        st = cur.get("job", {}).get("stage") if "job" in cur else cur.get("stage")
        if st and (not stages or stages[-1] != st):
            stages.append(st)
        status = cur.get("job", {}).get("status") if "job" in cur else cur.get("status")
        if status in ("done", "failed"):
            break
        time.sleep(0.3)
    print("进度   :", " → ".join(stages[-4:]))
    print("结果   :", status, "候选", len(cur.get("candidates", [])))

    print("列表   :", len(json.loads(get("/api/batches").decode())["batches"]), "个批次")
    assert status == "done", f"异步任务未完成：{status} {cur.get('job', {}).get('error')}"
    print("\nOK — HTTP 链路打通（同步 + 异步）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
