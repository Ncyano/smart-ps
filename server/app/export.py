"""导出：按平台规格裁切输出，并把"这批图能不能对外"钉在文件旁边。

平台尺寸一律用 **cover 裁切**：等比缩放到刚好盖满目标画布，再居中裁掉多余部分。
为什么不加白边（contain）：电商主图/详情页都不接受留白边，宁可裁掉边缘。

## 导出目录里到底有什么

```
data/exports/<batch>/
  <batch>_c1_1080x1350.png      正式文件（只在 formal 或内部参考时给）
  preview/                      带「内部参考」水印的副本（授权不是 ok 时才生成）
  manifest.json                 每个文件的交付状态、来源、检查结论（机器读）
  清单.md                       同一份信息的人读版
  _仅内部参考.txt               授权为第三方/示例时强制写入
```

`manifest.json` 是这次新加的：旧版 export.json 只记了尺寸和授权等级，
事后想查"这张图当时为什么判可交付、价格是谁给的"完全查不到。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from .config import DATA_DIR
from . import gate as gatemod
from . import rights as rightsmod

EXPORT_DIR = DATA_DIR / "exports"

# 常用规格（后续从这里扩，前端直接列出来）
PRESETS: dict[str, tuple[int, int]] = {
    "original": (0, 0),          # 原尺寸
    "square_1080": (1080, 1080),  # 主图 1:1
    "detail_1080x1440": (1080, 1440),  # 详情页 3:4
    "story_1080x1920": (1080, 1920),   # 竖版 9:16
    "banner_1920x640": (1920, 640),    # 横版 banner
}


def _parse_size(spec: str | None) -> tuple[int, int] | None:
    """接受预设名或 "1080x1350" 这种字面量。返回 None 表示原尺寸。"""
    if not spec or spec in ("original", "原尺寸"):
        return None
    if spec in PRESETS:
        w, h = PRESETS[spec]
        return (w, h) if w and h else None
    if "x" in spec:
        try:
            w, h = (int(x) for x in spec.lower().split("x", 1))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    raise ValueError(f"无法识别的尺寸：{spec}（可用预设：{', '.join(PRESETS)}，或 1080x1350）")


def cover_fit(im: Image.Image, size: tuple[int, int] | None) -> Image.Image:
    """等比缩放到盖满目标，居中裁切。"""
    if not size:
        return im
    tw, th = size
    w, h = im.size
    scale = max(tw / w, th / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return im.crop((left, top, left + tw, top + th))


def _stamp(im: Image.Image, text: str = "内部参考 · 不可对外投放") -> Image.Image:
    """给预览副本盖水印。斜向平铺 + 底部实心标签，删不干净。

    为什么不直接在正式文件上盖章：设计稿可能还要改。但**内部参考**这件事
    必须让人一眼看出来，所以另出一份 preview/，转发时不容易搞混。
    """
    base = im.convert("RGBA")
    w, h = base.size
    # 水印颜色跟着底子走：浅底用深灰、深底用白，否则白底上一片白字等于没盖
    from PIL import ImageStat
    luma = ImageStat.Stat(base.convert("L")).mean[0]
    tile = (40, 40, 40, 26) if luma > 150 else (255, 255, 255, 30)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    try:
        from .templates.layout import font as _font
        f = _font(max(18, int(min(w, h) * 0.045)))
    except Exception:  # noqa: BLE001
        f = None
    step = max(260, int(min(w, h) * 0.55))
    for y in range(-h, h * 2, step):
        for x in range(-w, w * 2, step):
            if f is not None:
                d.text((x, y), text, font=f, fill=tile)
            d.line([(x, y + step * 0.28), (x + step * 1.4, y + step * 0.28)],
                   fill=(tile[0], tile[1], tile[2], 34), width=1)
    band_h = max(38, int(h * 0.05))
    d.rectangle([0, h - band_h, w, h], fill=(186, 32, 24, 226))
    band_text = f"内部参考 · 不可投放 · {datetime.now():%Y-%m-%d}"
    if f is not None:
        f2 = f
        tw = d.textlength(band_text, font=f2)
        d.text(((w - tw) / 2, h - band_h + band_h * 0.16), band_text, font=f2, fill=(255, 255, 255, 255))
    return Image.alpha_composite(base, layer).convert("RGB")


def _manifest(batch: dict, files: list[dict], gate_result: dict, formal: bool,
              size_label: str) -> dict:
    cands = {c["index"]: c for c in batch.get("candidates", [])}
    claims = (batch.get("claims_audit") or {}).get("data_items") or []
    items = []
    for f in files:
        if f.get("error"):
            items.append({"index": f.get("index"), "error": f["error"]})
            continue
        c = cands.get(f["index"]) or {}
        d = c.get("delivery") or {}
        items.append({
            "index": f["index"],
            "file": f["file"],
            "preview": f.get("preview", ""),
            "size": f.get("size"),
            "source_size": f.get("source_size"),
            "bytes": f.get("bytes"),
            "delivery": {
                "status": d.get("status"),
                "label": d.get("label"),
                "reason": d.get("reason"),
                "blockers": d.get("blockers"),
            },
            "checks": d.get("checks"),
            "reviewer": {
                "chosen": bool(c.get("chosen")),
                "subject_confirmed": bool(c.get("subject_confirmed")),
                "subject_confirmed_by": c.get("subject_confirmed_by") or "",
                "subject_confirmed_at": c.get("subject_confirmed_at") or "",
                "note": c.get("note") or "",
            },
            "scores": c.get("scores"),
        })
    return {
        "batch": batch.get("id"),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "formal": formal,
        "size": size_label,
        "template": {
            "id": batch.get("template"),
            "version": batch.get("template_version"),
            "compose": batch.get("template_compose"),
        },
        "engine": batch.get("engine"),
        "engine_note": batch.get("engine_note"),
        "rights": gate_result,
        "claims_audit": {
            "status": (batch.get("claims_audit") or {}).get("status"),
            "data_items": claims,
            "missing_fields": (batch.get("claims_audit") or {}).get("missing_fields"),
        },
        "category_check": batch.get("category_check"),
        "delivery_summary": batch.get("delivery"),
        "files": items,
    }


def _readme(manifest: dict) -> str:
    """人读版清单。导出文件夹转给设计/客户时，一眼能看清哪些能发。"""
    lines = [
        f"# 导出清单 · {manifest['batch']}",
        "",
        f"- 导出时间：{manifest['exported_at']}",
        f"- 模板：{manifest['template']['id']} v{manifest['template']['version']}"
        f"{'（确定性合成）' if manifest['template']['compose'] else ''}",
        f"- 引擎：{manifest['engine']}",
        f"- 规格：{manifest['size']}",
        f"- 导出类型：{'正式交付' if manifest['formal'] else '内部参考 / 待人工'}",
        f"- 素材授权：{manifest['rights']['label']}（{manifest['rights']['declared']}）",
        "",
        "| # | 文件 | 交付状态 | 卡在哪一项 |",
        "|---|---|---|---|",
    ]
    for it in manifest["files"]:
        if it.get("error"):
            lines.append(f"| {it['index']} | — | 失败 | {it['error']} |")
            continue
        d = it["delivery"]
        why = d.get("reason") or ""
        if d.get("blockers"):
            why += "（" + "、".join(d["blockers"]) + "）"
        lines.append(f"| {it['index']} | {it['file']} | {d.get('label') or '-'} | {why} |")
    if manifest["claims_audit"]["data_items"]:
        lines += ["", "## 数据来源", ""]
        for d in manifest["claims_audit"]["data_items"]:
            mark = "✓" if d.get("verified") else "✗ 无来源"
            lines.append(f"- {d.get('label')} `{d.get('text')}` {mark} {d.get('source') or ''}")
    if manifest["rights"].get("warning"):
        lines += ["", "> " + manifest["rights"]["warning"]]
    return "\n".join(lines) + "\n"


def export_batch(batch: dict, indexes: list[int], size: str | None = None,
                 fmt: str = "png", quality: int = 95, formal: bool = False) -> list[dict]:
    """把一个批次里选定的候选导出到 data/exports/<batch>/。返回文件清单。

    `size` 不传时用批次所属模板的默认规格——卖点图默认 `original`（已排版好，
    再裁会切字），白底主图默认 `square_1080`。

    `formal=True` 表示"正式交付"，调用方必须先过 `gate.formal_ready()`；
    这里再挡一次，防止有人绕过接口直接调函数。
    """
    if formal:
        ok, why = gatemod.formal_ready(batch)
        if not ok:
            raise ValueError(f"未通过正式交付闸门：{why}")

    if not size:
        size = batch.get("template_export") or None
    target = _parse_size(size)
    cands = {c["index"]: c for c in batch.get("candidates", [])}
    picked = [cands[i] for i in sorted(set(indexes)) if i in cands]
    if not picked:
        raise ValueError("没有可导出的候选（index 不存在）")
    if formal:
        # 正式交付**逐张**校验，不只看批次汇总：
        # 一批里有一张可交付，不代表你选的这张也可交付。
        bad = [c for c in picked if (c.get("delivery") or {}).get("status") != "deliverable"]
        if bad:
            why = "；".join(f"#{c['index']} {(c.get('delivery') or {}).get('reason', '未通过')}"
                           for c in bad[:2])
            raise ValueError(f"选中的图里有未通过闸门的：{why}")

    out_dir = EXPORT_DIR / batch["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 授权闸门：声明了"第三方未授权 / 示例"就在导出目录留一份说明 ──
    # 不硬拦（拦了也会被随手改声明绕过），但文件夹转给谁都能看到这行字。
    gate_result = rightsmod.gate(batch)
    if gate_result["internal_only"]:
        (out_dir / rightsmod.EXPORT_NOTICE).write_text(
            rightsmod.notice_text(batch, rightsmod.for_batch(batch)), encoding="utf-8"
        )

    # 只要不是正式交付，就额外给一份带水印的预览副本
    need_preview = not formal or gate_result["internal_only"]
    preview_dir = out_dir / "preview"

    results: list[dict] = []
    for c in picked:
        src = DATA_DIR / c["final_url"].lstrip("/").removeprefix("files/")
        if not src.is_file():
            results.append({"index": c["index"], "error": "源图缺失"})
            continue

        im = Image.open(src).convert("RGB")
        src_size = im.size
        im = cover_fit(im, target)

        name = f"{batch['id']}_c{c['index']}_{target[0]}x{target[1]}.{fmt}" if target \
            else f"{batch['id']}_c{c['index']}_orig.{fmt}"
        path = out_dir / name
        if fmt.lower() in ("jpg", "jpeg"):
            im.save(path, quality=quality, subsampling=1)
        else:
            im.save(path)

        preview_name = ""
        if need_preview:
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_name = "preview/" + Path(name).with_suffix(".jpg").name
            _stamp(im).save(out_dir / preview_name, quality=88)

        results.append(
            {
                "index": c["index"],
                "file": name,
                "preview": preview_name,
                "url": "/files/exports/" + batch["id"] + "/" + name,
                "preview_url": ("/files/exports/" + batch["id"] + "/" + preview_name) if preview_name else "",
                "size": list(im.size),
                "source_size": list(src_size),
                "bytes": path.stat().st_size,
                "delivery": (c.get("delivery") or {}).get("status"),
            }
        )

    # 人读 + 机读两份清单
    manifest = _manifest(batch, results, gate_result, formal, size or "original")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    (out_dir / "清单.md").write_text(_readme(manifest), encoding="utf-8")

    # 导出记录写进批次目录，便于追溯（保留历史，不只最后一次）
    log = out_dir / "export.json"
    prev = []
    if log.exists():
        try:
            prev = json.loads(log.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    prev.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "size": size or "original",
            "formal": formal,
            "rights": gate_result,          # 导出的每一条都带授权状态，便于事后追溯
            "delivery": batch.get("delivery"),
            "claims": (batch.get("claims_audit") or {}).get("data_items"),
            "files": results,
        }
    )
    log.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    return results
