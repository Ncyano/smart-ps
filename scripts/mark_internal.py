# -*- coding: utf-8 -*-
"""给"对外演示 / 内部评测"用的图批量盖内部标识（不动机原文件）。

## 为什么要单独出一份

`交付/` 里有 5 张含第三方品牌素材、
若干含示例价格、1 张竞品对比长图属内部材料。这些图做模板演示、内部评测没问题，
但一旦流出去，就是拿别人的品牌素材做自己的宣传。

导出流程里已经有水印（`export._stamp`）和 `_仅内部参考.txt`，但那是**新导出**的图。
`交付/` 是历史产物，没有经过那道闸。所以这里给它们补一份"演示副本"：

- 原图**不修改**（原图是历史记录，改了就没法追溯）
- 副本统一加斜向水印 + 文件名单前缀 `内部_`
- 目录里写一份 `README-内部参考.md`，说明这三件事

## 用法

```
python scripts/mark_internal.py                 # 默认 交付/ → 交付_内部演示/
python scripts/mark_internal.py --src 交付 --out 演示图
python scripts/mark_internal.py --dry-run       # 只算不写
```
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from server.app.export import _stamp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TEXT = "内部参考 · 不可对外投放"

README = """# 内部参考副本 · 不可对外投放

本目录由 `scripts/mark_internal.py` 生成，用途是**内部评测与模板演示**。

## 为什么这些图不能对外

1. **含第三方品牌素材**：多个真实品牌，未获授权。
2. **数字是示例口径**：图内价格、含量、专利号、检测数据并非真实材料。
3. **竞品对比图属内部材料**：含真实竞品品牌与在售价格，对外发布有法律风险。

## 怎么用

- 内部评审、模板演示、培训：可以
- 客户提案、对外投放、社媒发布：**不可以**

正式物料请用自有产品素材 + 真实数据重新生成，走系统导出（会自动带
`_仅内部参考.txt` 与 `preview/` 水印副本）。

原图目录未被修改；本目录只是副本。
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="交付")
    ap.add_argument("--out", default="交付_内部演示")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = (ROOT / args.src).resolve()
    out = (ROOT / args.out).resolve()
    if not src.is_dir():
        print(f"源目录不存在：{src}")
        return 2
    if out == src:
        print("输出目录不能和源目录相同：本脚本不修改原图")
        return 2

    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in IMG_EXT]
    if not files:
        print(f"{src} 下没有图片")
        return 2

    print(f"{'[试运行] ' if args.dry_run else ''}{len(files)} 张 → {out}")
    if args.dry_run:
        for p in files[:5]:
            print("  ·", p.relative_to(src))
        return 0

    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in files:
        rel = p.relative_to(src)
        dest = out / rel.parent / f"内部_{p.stem}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(p) as im:
                _stamp(im.convert("RGB"), TEXT).save(dest, quality=88)
            n += 1
        except Exception as exc:  # noqa: BLE001 单张失败不影响其余
            print(f"  ! {rel}：{exc}")

    (out / "README-内部参考.md").write_text(README, encoding="utf-8")
    # 清单.md 是文字说明，一并带过去，避免脱离上下文
    if (src / "清单.md").is_file():
        shutil.copy2(src / "清单.md", out / "原交付清单.md")
    print(f"完成 {n}/{len(files)} 张，说明见 {out / 'README-内部参考.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
