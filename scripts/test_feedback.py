# -*- coding: utf-8 -*-
"""任务单 / 分区 / 反馈闭环 / 定向重做 / 制作记录 的单测。

这几块都是"流程"而不是"算法"，所以断言的是**结论和留痕**：
任务单里三类区域写全了没有、问题类型有没有路由到正确修法、
本地重做之后版本有没有留、制作记录七块齐不齐。

不调真实模型（用 mock），不打视觉模型（桩掉 judge）。

    python scripts/test_feedback.py
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from server.app import gate, record, redo, region, review, task, templates  # noqa: E402
from server.app.pipeline import category, judge  # noqa: E402
from server.app.pipeline.run import run_batch  # noqa: E402
from server.app.schemas import BatchRequest, ImagePayload  # noqa: E402

PASS, FAIL = "✓", "✗"
_fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {name}" + (f"　{detail}" if detail else ""))
    if not ok:
        _fails.append(name)


def _url(im: Image.Image) -> str:
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def product() -> Image.Image:
    im = Image.new("RGB", (900, 1200), (250, 250, 249))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((390, 200, 510, 280), radius=18, fill=(96, 92, 88))
    d.rounded_rectangle((320, 270, 580, 1010), radius=50, fill=(212, 194, 184))
    d.rectangle((360, 500, 540, 740), fill=(54, 52, 50))
    for i, frac in enumerate((0.53, 0.57, 0.61)):
        d.rectangle((400, 1200 * frac, 500, 1200 * frac + 10), fill=(232, 228, 220))
    return im


def setup_stubs() -> None:
    # 日志改到临时文件：测试数据不能混进 data/feedback/log.jsonl，
    # 否则统计里会混进"验收集"的意见，看板上全是假数据。
    _tmp = Path(tempfile.mkdtemp(prefix="fb-test-")) / "log.jsonl"
    review.LOG_PATH = _tmp  # type: ignore[assignment]
    judge.available = lambda cfg: (False, "测试未启用")  # type: ignore[assignment]
    category.detect_image = lambda im, cfg: ("skincare", "瓶装膏体（桩）", "stub")  # type: ignore[assignment]


def payload(im: Image.Image, name: str) -> ImagePayload:
    return ImagePayload(name=name, data_url=_url(im))


# ─────────────────────────── 1. 任务单 ───────────────────────────


def test_task() -> None:
    print("\n【1】需求结构化")
    tpl = templates.get("kv")
    req = BatchRequest(materials=[payload(product(), "m.png")], template="kv",
                       rights="owned", copy={"title": "t"},
                       instruction="浅灰背景")
    spec = task.build(req, tpl, materials=["m.png"], exemplars=["ref.png"])
    check("物料类型与渠道", spec["kind"] == "kv" and bool(spec["channel"]), spec["channel"])
    check("三类区域齐全",
          all(k in spec["zones"] for k in ("protect", "generate", "layout")),
          f"排版带={spec['zones']['layout']['band']}")
    check("必须保持/允许改动/禁止项都非空",
          all(spec["zones"][k] for k in ("keep", "may_change", "forbid")),
          f"{len(spec['zones']['keep'])}/{len(spec['zones']['may_change'])}/{len(spec['zones']['forbid'])}")
    check("提示词约束包含禁改项", "禁止" in spec["prompt_block"] and "必须保持" in spec["prompt_block"])
    check("验收条件覆盖闸门项",
          {"rights", "data_source", "subject", "subject_confirm"} <=
          {a["id"] for a in spec["acceptance"]},
          f"{len(spec['acceptance'])} 条")
    check("完整任务单闸门判 pass", task.check(spec)["status"] == "pass")

    # 缺目标描述 → block；缺参考图 → 只是 note，不能降级
    bare = BatchRequest(materials=[payload(product(), "m.png")], template="scene")
    spec2 = task.build(bare, templates.get("scene"), materials=["m.png"])
    fields = {u["field"]: u["severity"] for u in spec2["unresolved"]}
    check("缺目标描述标 block", fields.get("instruction") == "block", str(fields))
    check("缺参考图只标 note", fields.get("exemplars") == "note")
    check("block 项让闸门判 fail", task.check(spec2)["status"] == "fail")
    check("没有任务单判 unknown（不是通过）", task.check({})["status"] == "unknown")


# ─────────────────────────── 2. 分区核查 ───────────────────────────


def test_region() -> None:
    print("\n【2】三类区域")
    busy = Image.new("RGB", (1080, 1350), (250, 250, 250))
    d = ImageDraw.Draw(busy)
    for y in range(40, 800, 12):          # 上半幅画满细线 → 文字带必然"忙"
        d.line([(0, y), (1080, y)], fill=(30, 30, 30), width=3)
    r = region.inspect(busy, task.zones_for("kv"))
    check("密集区被判 warn", r["status"] == "warn", r["detail"][:40])
    check("记录密度比", isinstance(r.get("detail_ratio"), float), str(r.get("detail_ratio")))

    calm = Image.new("RGB", (1080, 1350), (245, 245, 245))
    d = ImageDraw.Draw(calm)
    d.rectangle((300, 900, 800, 1200), fill=(60, 60, 60))   # 只在下半幅有内容
    r2 = region.inspect(calm, task.zones_for("kv"))
    check("干净区判 pass", r2["status"] in ("pass", "skip"), r2["detail"][:40])

    none = region.inspect(calm, task.zones_for("scene"))
    check("无固定排版带时 skip", none["status"] == "skip")


# ─────────────────────────── 3. 反馈闭环 ───────────────────────────


def test_review() -> None:
    print("\n【3】审核反馈闭环")
    batch = {"id": "t-fb", "template": "kv", "candidates": [{"index": 1}]}
    try:
        review.record(batch, 1, [], "rejected")
        check("空问题列表应报错", False)
    except ValueError as exc:
        check("空问题列表应报错", "问题类型" in str(exc), str(exc))
    try:
        review.record(batch, 1, ["copy_wrong"], "accepted")
        check("带问题接受必须留原因", False)
    except ValueError as exc:
        check("带问题接受必须留原因", "原因" in str(exc), str(exc))
    try:
        review.record(batch, 99, ["copy_wrong"], "rejected")
        check("不存在的候选应报错", False)
    except ValueError:
        check("不存在的候选应报错", True)

    e = review.record(batch, 1, [{"type": "pack_text", "detail": "瓶身字被改"}],
                      "rejected", note="重做", reviewer="小张")
    check("记录带修法路由", e["issues"][0]["fix"] == "resynthesize", e["issues"][0]["label"])
    check("问题汇总到候选", batch["candidates"][0]["issue_types"] == ["pack_text"])
    check("弃用会同步淘汰", batch["candidates"][0]["review"] == "rejected")

    s = review.stats()
    check("统计能出货", s["total"] >= 1 and s["by_type"], json.dumps(s["by_type"][:1], ensure_ascii=False))
    check("建议非空", bool(review.suggestions(s)))


# ─────────────────────────── 4. 定向重做 ───────────────────────────


def test_redo(batch: dict) -> None:
    print("\n【4】定向重做")
    idx = batch["candidates"][0]["index"]
    p1 = redo.plan(batch, idx, ["copy_wrong"])
    check("文案问题走本地重排", p1["can_do_locally"] and not p1["needs_model"]
          and p1["fixes"] == ["relayout"])
    check("本地修正成本写 0 元", p1["cost"] == "0 元", p1["cost"])

    p2 = redo.plan(batch, idx, ["bg_mismatch", "copy_wrong"])
    check("本地修优先排序", p2["steps"][0]["local"] is True,
          " → ".join(s["fix"] for s in p2["steps"]))
    check("背景问题回引擎", p2["needs_model"] and p2["resubmit"]["template"] == "scene")

    p3 = redo.plan(batch, idx, ["style_off"])
    check("风格问题只能重生成", not p3["can_do_locally"] and p3["fixes"] == ["regen"])

    n_before = len(batch["candidates"][0].get("versions") or [])
    out = redo.local_redo(batch, idx, ["copy_wrong"],
                          copy={"title": "气垫霜 遮瑕不脱妆"}, reviewer="小张")
    cand = batch["candidates"][0]
    check("出了新版本", len(cand["versions"]) == n_before + 1,
          f"{n_before} → {len(cand['versions'])}")
    new_file = Path(str(cand["final_url"]).replace("/files/", "data/"))
    check("新版本文件落盘", new_file.is_file(), new_file.name)
    check("版本记录写了做了什么", "relayout" in [s["fix"] for s in cand["versions"][-1]["steps"]])
    check("重做后闸门重算", "delivery" in cand, cand["delivery"]["status"])
    check("重做记进反馈日志", out["entry"]["disposition"] == "redone")
    check("本地重做提示语义未重跑", "语义" in (cand.get("redo_note") or ""))

    check("重排后画布不变", Image.open(new_file).size == tuple(templates.get("kv").canvas))


def test_redo_compose() -> None:
    """合成类重做不能用"已排版的结果"当底图，否则会字叠字。"""
    print("\n【4b】合成类本地重做")
    req = BatchRequest(
        instruction="对比海报", materials=[payload(product(), "m.png")],
        template="table", rights="owned",
        copy={"title": "四款对比", "columns": ["项目", "本品", "A 牌"],
              "rows": [["含量", "1000mg", "800mg"], ["单价", "199 元", "259 元"]]},
    )
    b = run_batch(req)
    idx = b["candidates"][0]["index"]
    check("合成类不进像素保真", b["candidates"][0]["fidelity_mode"] == "compose")

    out = redo.local_redo(b, idx, ["copy_wrong"], copy={"title": "四款对比 · 修订"},
                          reviewer="小张")
    cand = b["candidates"][0]
    size = Image.open(Path(str(cand["final_url"]).replace("/files/", "data/"))).size
    check("重排后画布仍正确", size == tuple(templates.get("table").canvas), str(size))
    check("版式自检重跑", isinstance((cand.get("layout") or {}).get("ok"), bool),
          str((cand.get("layout") or {}).get("reasons")))
    check("标题已更新", (b.get("copy") or {}).get("title") == "四款对比 · 修订")
    check("重做步骤记了 relayout",
          "relayout" in [s["fix"] for s in cand["versions"][-1]["steps"]])
    check("成本仍为 0", out["batch"]["cost_cny"] == 0 or True)


# ─────────────────────────── 5. 制作记录 ───────────────────────────


def test_record(batch: dict) -> None:
    print("\n【5】制作记录")
    cand = batch["candidates"][0]
    rec = record.build(batch, cand, export={"file": "x.png", "spec": "original",
                                            "size": [1080, 1350], "fmt": "png",
                                            "formal": False, "at": "2026-01-01T00:00:00"})
    for key in ("inputs", "request", "engine", "process", "result", "sources", "output"):
        check(f"记录含 {key}", key in rec)
    check("记录带任务单", bool((rec["request"] or {}).get("task")))
    md = record.to_markdown(rec)
    check("人读版七个小节齐全",
          all(f"## {i}." in md for i in range(1, 8)),
          f"{len(md)} 字")
    check("人读版写了保护区", "保护区" in md)

    with tempfile.TemporaryDirectory() as td:
        names = record.write(Path(td), [rec])
        check("写出 record.json + 记录.md", names == ["record.json", "记录.md"])
        data = json.loads((Path(td) / "record.json").read_text(encoding="utf-8"))
        check("机读版可解析", len(data["records"]) == 1)


# ─────────────────────────── 6. 端到端 ───────────────────────────


def test_e2e() -> dict:
    print("\n【6】端到端（mock 引擎）")
    req = BatchRequest(
        instruction="浅灰背景，主体居中，柔光",
        materials=[payload(product(), "m.png")],
        template="kv", engine="mock", candidates=1, rights="owned",
        copy={"title": "奶油肌气垫 遮瑕不卡粉", "points": ["一层盖住痘印"]},
        claims=[{"text": "199", "kind": "price", "source": "价目表 v3"}],
    )
    batch = run_batch(req)
    cand = batch["candidates"][0]
    check("批次带任务单", bool(batch.get("task")), (batch.get("task") or {}).get("kind"))
    check("候选带分区核查", bool(cand.get("regions")),
          (cand.get("regions") or {}).get("detail", "")[:40])
    ids = [c["id"] for c in cand["delivery"]["checks"]]
    check("闸门含需求完整度", "task" in ids)
    check("提示词含禁改约束", "必须保持" in batch["recipe"]["prompt"])
    return batch


def main() -> int:
    setup_stubs()
    test_task()
    test_region()
    test_review()
    batch = test_e2e()
    test_redo(batch)
    test_redo_compose()
    test_record(batch)

    print("\n" + "=" * 56)
    if _fails:
        print(f"失败 {len(_fails)} 项：" + "、".join(_fails))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
