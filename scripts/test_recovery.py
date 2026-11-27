"""任务恢复与幂等的离线测试。

服务重启时最贵的损失不是时间，是**已经花掉的生成费**。所以这一层要证明两件事：

1. 检查点存在时，重启续跑**不会再调一次生成引擎**（花钱的那一步只做一次）
2. 同一个 `request_id` 重复提交，只跑一次（前端超时重发不该扣两次钱）

用法：python scripts/test_recovery.py
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app import jobs, store  # noqa: E402
from server.app.engines import registry  # noqa: E402
from server.app.engines.mock import MockEngine  # noqa: E402
from server.app.pipeline import judge  # noqa: E402
from server.app.pipeline.run import load_ckpt, run_batch, save_ckpt  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402

PASS = FAIL = 0
CALLS = {"n": 0}


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {extra}")


def _url(im: Image.Image) -> str:
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def fixture() -> Image.Image:
    im = Image.new("RGB", (600, 800), (248, 248, 246))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((170, 160, 430, 660), radius=30, fill=(206, 188, 178))
    d.rectangle((220, 330, 380, 430), fill=(56, 54, 52))
    return im


class CountingEngine(MockEngine):
    """包一层计数的 mock：用来证明"续跑没有重新生成"。"""

    def generate(self, req, out_dir):
        CALLS["n"] += 1
        return super().generate(req, out_dir)


def main() -> int:
    # 语义判定是网络调用，这个测试只关心检查点，桩掉省时间也省得联网
    judge.available = lambda cfg: (False, "测试未启用")  # type: ignore[assignment]
    orig_resolve = registry.resolve
    registry.resolve = lambda cfg, name=None: (CountingEngine(), "计数引擎")  # type: ignore[assignment]

    req = BatchRequest(
        instruction="背景纯白，主体居中",
        engine="mock", candidates=2, rights="owned",
        category="skincare",                      # 声明品类，避免调用视觉模型
        materials=[ImagePayload(name="m.png", data_url=_url(fixture()))],
    )

    print("\n【1】首轮跑完并落检查点")
    batch = run_batch(req)
    bid = batch["id"]
    check("生成了候选", len(batch["candidates"]) == 2, str(len(batch["candidates"])))
    check("引擎被调用过一次", CALLS["n"] == 1, str(CALLS))
    ckpt = load_ckpt(bid)
    check("检查点存在", bool(ckpt), "resume.json 缺失")
    check("检查点记录了素材路径", bool(ckpt and ckpt.get("materials")))

    print("\n【2】模拟崩溃：批结果丢了，检查点还在")
    (store.batch_dir(bid) / "batch.json").unlink()
    check("batch.json 已删除", store.get_batch(bid) is None)
    ck = load_ckpt(bid)
    ck["stage"] = "generated"          # 生成已完成、汇总没写成
    save_ckpt(bid, ck)

    jobs._run(bid, None, None, True)   # 等价于服务重启后续跑
    again = store.get_batch(bid)
    check("续跑后结果重新落盘", again is not None and bool(again.get("candidates")))
    check("续跑没有重新调用生成引擎", CALLS["n"] == 1, f"实际调用 {CALLS['n']} 次")
    check("候选数与首轮一致", len(again["candidates"]) == 2)
    check("交付闸门已重算", "delivery" in again, "缺 delivery")

    print("\n【3】没有检查点时，明确判失败而不是假装能续")
    jobs._update("fake-batch-no-ckpt", status="running", stage="开始处理")
    try:
        jobs.retry("fake-batch-no-ckpt")
        check("retry 应报错", False)
    except ValueError as exc:
        check("retry 明确报错", "检查点" in str(exc), str(exc))

    print("\n【4】幂等：同一 request_id 只跑一次")
    req2 = BatchRequest(
        instruction="白底主图", candidates=1, rights="owned",
        category="skincare", template="white_bg",
        materials=[ImagePayload(name="m.png", data_url=_url(fixture()))],
        request_id="req-fixed-0001",
    )
    before = CALLS["n"]
    j1 = jobs.submit(req2)
    j2 = jobs.submit(req2)
    check("两次提交拿到同一个批次 id", j1["id"] == j2["id"], f"{j1['id']} vs {j2['id']}")
    check("第二次标记为幂等命中", bool(j2.get("_idempotent")), str(j2.get("_idempotent")))
    check("没有多起一个任务", CALLS["n"] - before <= 1, f"多跑了 {CALLS['n'] - before} 次")

    print("\n【5】recover()：能续的续、不能续的才判失败")
    # 造一个"正在跑 + 有检查点"的任务
    ck2 = load_ckpt(j1["id"])
    if ck2:
        ck2["stage"] = "generated"
        save_ckpt(j1["id"], ck2)
        jobs._update(j1["id"], status="running", stage="开始处理")
    (store.batch_dir(j1["id"]) / "batch.json").unlink(missing_ok=True) if (store.batch_dir(j1["id"]) / "batch.json").exists() else None
    jobs._update("fake-batch-no-ckpt", status="running")
    res = jobs.recover()
    check("recover 返回分类计数", set(res) == {"resumed", "failed"}, str(res))
    check("至少续跑了一个", res["resumed"] >= 1, str(res))
    check("没有检查点的那条标为失败", res["failed"] >= 1, str(res))
    job = jobs.get("fake-batch-no-ckpt") or {}
    check("失败原因写明是重启中断", "重启" in (job.get("error") or ""), str(job.get("error")))

    registry.resolve = orig_resolve  # type: ignore[assignment]
    print(f"\n{'=' * 56}\n通过 {PASS}　失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
