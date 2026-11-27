"""把散在各处的交付级成品，归到项目根目录的 `交付/` 下。

## 为什么要有这一步

成图目前分散在三个地方，而且命名是 `1.png` / `2.png` 这种：

```
产出/模板-卖点图.png
产出/需求样张/04-线下大屏/1.png
产出/需求样张/01-效果图/1.png
产出/审核界面-*.png
```

发给运营或客户时，没人分得清哪个是哪个。本脚本做三件事：
1. 按「需求类型」分目录（01-卖点图、02-白底主图 …）
2. 改成能直接看懂的文件名（`气垫霜-卖点图.png` 而不是 `模板-卖点图.png`）
3. 生成 `交付/清单.md`：每张图的工艺、尺寸、大小、使用注意

## 关键约定

- **只复制，不移动**：`产出/` 是构建脚本的输出目录，移动会让脚本找不到自己的产物
- **幂等**：每次运行清空 `交付/` 重建，所以重新生成成图后重跑一次就同步了
- **缺失不报错**：某个样张还没做出来时只跳过并提示，不让整批失败

用法：
    python scripts/collect_deliverables.py
    python scripts/collect_deliverables.py --dry-run   # 只看会复制什么
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "交付"

# (源文件, 子目录, 目标文件名, 一句话说明, 工艺要点)
DELIVERABLES: list[tuple[str, str, str, str, str]] = [
    # ── 产线模板产出 ──
    ("产出/模板-卖点图.png", "01-卖点图", "气垫霜-卖点图-1080x1350.png",
     "气垫霜卖点图，真实生成 + 代码排版",
     "Seedream 出场景图 → 磨砂暗底 → PIL 排标题/副标题/3 卖点/角标"),
    ("产出/模板-白底主图.png", "02-白底主图", "气垫霜-白底主图-1080x1080.png",
     "电商白底主图，纯白无缝、居中、留白均匀",
     "grabCut 抠图（缩到 640 再抠，否则 2048² 要 33s）→ 贴纯白 → 重算 5.5% 留白"),

    # ── 旧 7 类需求样张 ──
    ("产出/需求样张/01-效果图/1.png", "03-效果图-前后对比", "效果图-面部-双联.png",
     "面部提亮前后对比（左：使用前，右：使用后）",
     "左用原片，右用原片 img2img 生成「另一天」；禁止瘦脸/变年轻/磨皮"),
    ("产出/需求样张/01-效果图/2.png", "03-效果图-前后对比", "效果图-颈肩-双联.png",
     "颈肩/体态变化前后对比",
     "同上；减重幅度按素材三档标定，躯干正面仅 5–13%"),

    ("产出/需求样张/03-线下价格图/1.png", "04-线下价格图", "价格图-货架标价.png",
     "门店货架特写，价签清晰可读",
     "三层货架 + 三排白色价签条，价签由代码绘制（模型画字必糊）"),
    ("产出/需求样张/03-线下价格图/2.png", "04-线下价格图", "价格图-堆头大价签.png",
     "门店堆头 + 大价签牌（会员日/划线原价）",
     "同上；图中应标注「价格为门店标价示意，以实际收银价为准」"),

    ("产出/需求样张/04-线下大屏/1.png", "05-线下大屏", "大屏-地铁灯箱.png",
     "广告画面合成到真实灯箱场景（全项目难度最高，返工 9 轮）",
     "空屏位场景 → 边框内檐暗线 + RANSAC 逐边拟合 → 外扩 1.5px/羽化 0.6px → 透视贴 + 亮度场 + 环境反射 + 光溢出"),
    ("产出/需求样张/04-线下大屏/2.png", "05-线下大屏", "大屏-商场立牌.png",
     "广告画面合成到商场立式灯箱（含地面倒影）",
     "同上 + 接触阴影 + 地面倒影（只取中下段 560px，铺满到底边）"),

    ("产出/需求样张/05-文案对比海报/1.png", "06-文案对比海报", "对比海报-竖版.png",
     "两款产品卖点对比，VS 分栏 + 六行卖点",
     "大标题 + VS 分栏 + 六行「图标 + 粗体卖点 + 补充说明」+ 底部看点条"),
    ("产出/需求样张/05-文案对比海报/2.png", "06-文案对比海报", "对比海报-横版.png",
     "同上，横版构图",
     "同上"),

    ("产出/需求样张/06-尺码表/1.png", "07-尺码表", "尺码表-款A.png",
     "服装尺码表，含迷你柱状图",
     "一款一图一表；Playwright 读 scrollHeight 验证无溢出"),
    ("产出/需求样张/06-尺码表/2.png", "07-尺码表", "尺码表-款B.png",
     "同上，另一款配色",
     "同上"),

    ("产出/需求样张/07-表格对比海报/1.png", "08-表格对比海报", "表格对比-竖版.png",
     "九款商品共用一张图，九行九张产品图",
     "HTML 排版 + Playwright 截图；每款各一张产品图，不能共用"),
    ("产出/需求样张/07-表格对比海报/2.png", "08-表格对比海报", "表格对比-横版.png",
     "同上，横版",
     "同上"),

    # ── 旧项目效果图 ──
    ("产出/效果图/face.png", "03-效果图-前后对比", "效果图-面部-旧版.png",
     "早期面部对比成品",
     "同 01 效果图工艺，属旧 7 类交付"),
    ("产出/效果图/body.png", "03-效果图-前后对比", "效果图-身体-旧版.png",
     "早期身体塑形对比成品",
     "同 01 效果图工艺"),
    ("产出/效果图/slim.png", "03-效果图-前后对比", "效果图-塑形-旧版.png",
     "早期塑形对比成品",
     "同 01 效果图工艺"),

    # ── 界面截图（证明产品形态，不是客户物料） ──
    ("产出/审核界面-卖点图模板.png", "09-界面截图", "界面-卖点图模板.png",
     "审核界面：模板选择 + 文案表单 + 版式徽章",
     "单页 HTML，无构建链；Playwright 截图"),
    ("产出/审核界面-语义已启用.png", "09-界面截图", "界面-语义校验已启用.png",
     "审核界面：语义校验（同一款/文字乱码）已生效",
     "批次头显示模型与判定口径，卡片显示语义徽章"),
    ("产出/审核界面-真实候选B档.png", "09-界面截图", "界面-真实候选-B档.png",
     "审核界面：真实引擎候选，B 档（存疑）标注",
     "含结构/比例/清晰三项分数与原因"),
    ("产出/审核界面-mock-A档.png", "09-界面截图", "界面-mock-A档.png",
     "审核界面：A 档（可交付）标注",
     "mock 产物，用于验证界面渲染"),
    ("产出/审核界面-语义未校验状态.png", "09-界面截图", "界面-语义未校验.png",
     "审核界面：视觉模型未开通时的降级提示",
     "整层关闭时只在批次头提示，卡片不加噪声"),

    # ── 竞品对比长图（⚠️ 含真实竞品品牌，见清单顶部警示） ──
    ("产出/海报/poster.png", "10-竞品对比长图", "竞品对比-胶原蛋白肽9款横评.png",
     "9 款胶原蛋白肽横向对比长图，含参数表 + 卖点区 + 脚注",
     "HTML 排版 → Playwright 整页截图（源：产出/海报/poster.html）"),
]


def image_size(p: Path) -> str:
    try:
        from PIL import Image
        with Image.open(p) as im:
            return f"{im.width}×{im.height}"
    except Exception:
        return "—"


def main() -> int:
    dry = "--dry-run" in sys.argv
    if not dry and OUT.exists():
        shutil.rmtree(OUT)
    if not dry:
        OUT.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    missing: list[str] = []

    for src_rel, sub, name, desc, craft in DELIVERABLES:
        src = ROOT / src_rel
        if not src.is_file():
            missing.append(src_rel)
            continue
        dst = OUT / sub / name
        size_txt = image_size(src)
        kb = src.stat().st_size / 1024
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        rows.append({
            "sub": sub, "name": name, "desc": desc, "craft": craft,
            "size": size_txt, "kb": kb, "src": src_rel,
        })

    # ── 清单 ──
    total_kb = sum(r["kb"] for r in rows)
    lines = [
        "# 交付成图清单",
        "",
        f"> 由 `scripts/collect_deliverables.py` 生成，**不要手改**（重跑会覆盖）。",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　·　"
        f"共 **{len(rows)} 张**，合计 {total_kb / 1024:.1f} MB。",
        "",
        "## 先说三件事",
        "",
        "1. **不是所有图都能直接投放。** 素材与品牌多处是第三方（第三方品牌 / 第三方品牌 / "
        "竞品E / 第三方品牌 等），只作**版式与工艺参考**，正式物料必须换自有产品图。",
        "2. **图内的价格、含量、专利号、检测数据全是示例口径**，落地要换真实材料——"
        "这是项目铁律第三条（数据照抄素材，不编造）。",
        "3. **大屏类（05）的屏内画面是合成的**，交付给客户时要说明是效果图，"
        "不是实拍。",
        "4. ⚠️ **`10-竞品对比长图` 是内部竞品分析材料**：含真实竞品品牌与在售价格"
        "（竞品A / 竞品B / 竞品C / 竞品D 等）。**不可对外发布**，"
        "法务风险由使用方承担。",
        "",
        "## 目录",
        "",
    ]
    subs: dict[str, list[dict]] = {}
    for r in rows:
        subs.setdefault(r["sub"], []).append(r)
    for sub in sorted(subs):
        lines.append(f"- **{sub}**（{len(subs[sub])} 张）")

    for sub in sorted(subs):
        lines += ["", f"## {sub}", "", "| 文件 | 尺寸 | 大小 | 说明 |", "|---|---|---:|---|"]
        for r in subs[sub]:
            lines.append(
                f"| `{r['name']}` | {r['size']} | {r['kb']:.0f} KB | {r['desc']} |"
            )
        lines += ["", "**工艺**：", ""]
        for r in subs[sub]:
            lines.append(f"- `{r['name']}`：{r['craft']}")

    lines += [
        "",
        "---",
        "",
        "## 来源与重新生成",
        "",
        "每张图的源文件与构建脚本（重新生成时改这些，再跑一次本脚本）：",
        "",
        "| 交付文件 | 源文件 |",
        "|---|---|",
    ]
    for r in rows:
        lines.append(f"| `{r['sub']}/{r['name']}` | `{r['src']}` |")

    lines += [
        "",
        "```bash",
        "python scripts/collect_deliverables.py      # 刷新 交付/",
        "python 产出/需求样张/_build_screen.py        # 重建大屏（含倒影）",
        "python scripts/check_compare.py 产出/效果图/*.png   # 效果图量化质检",
        "```",
        "",
        "> 本目录是**打包出口**，不参与版本管理（`.gitignore` 已排除）：",
        "> 二进制图每次生成都变，放 Git 只会让仓库虚胖。要备份请整目录拷走。",
    ]

    if not dry:
        (OUT / "清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── 控制台输出 ──
    print("=" * 66)
    for sub in sorted(subs):
        print(f"\n{sub}/")
        for r in subs[sub]:
            mark = "  " if not dry else "· "
            print(f"{mark}{r['name']:<34} {r['size']:>11}  {r['kb']:>6.0f} KB")
    print("\n" + "=" * 66)
    print(f"共 {len(rows)} 张，合计 {total_kb / 1024:.1f} MB"
          + ("（dry-run，未写盘）" if dry else f" → {OUT}"))
    if missing:
        print(f"\n跳过 {len(missing)} 个不存在的源文件：")
        for m in missing:
            print(f"  - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
