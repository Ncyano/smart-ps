# AGENTS.md

> 给在本仓库工作的 AI 助手的约定。**开工前先读完这一页，再按需查 `knowledge/`。**

## 这是什么项目

给 P 图团队做的「AI 出图 + 代码排版」批量生产应用。
两条业务线：`素材例子/`（旧 7 类，已交付）和 `p图素材_例子/`（新 13 类，待开工）。

## 开工流程

```
1. 读 knowledge/README.md          ← 知识库导航（1 分钟）
2. 读 knowledge/00-项目上下文.md    ← 全局认知（3 分钟）
3. 按任务查 knowledge/02-工艺路线.md（决策树：这类图该怎么做）
4. 动手前必读 knowledge/03-踩坑库.md
```

**不要重新发明做法。** 工艺路线里每一条都是打了 5–9 轮才定下来的。

检索（零依赖）：

```bash
python scripts/kb_search.py "错位"
python scripts/kb_search.py "倒影 羽化" -n 8
python scripts/kb_search.py --list
```

## 五条铁律

**1. 不让生成模型画文字。**
模型画瓶标出乱码、画价格会糊、画卖点会冒违禁成分（真实出现过 `MARYANUM (Cannabis Sativa)`）。
所有文字由 PIL / HTML 后置绘制。见 `knowledge/02-工艺路线.md` §1。

**2. 商品主体不重绘。**
保真优先：抠图 + 透明图层合成。只有场景/氛围允许整图生成。

**3. 数据照抄素材，不编造。**
价格、含量、专利号、检测数据必须来自真实材料。样张里的品牌和数字都是**示例**。

**4. 文案说的必须是这个产品能做的事。**
气垫霜不能说"每天一粒"，胶囊不能说"上脸遮瑕、持妆"。
这类错图做得再精致也是废稿——而且像素质检**四层全过**（产品在、字清晰、版式对、风格像）。
`pipeline/category.py` 会在**生成前**拦截，但词表只兜得住常见品类：
接新品类（器械、宠物、食品）先自己把这层想清楚。
见 `knowledge/03-踩坑库.md` G 组。

**5. 素材授权状态必须显式声明。**
`owned` 自有 / `licensed` 已授权 / `third_party` 第三方未授权 / `sample` 示例。
声明为后两者时，导出目录会强制写入 `_仅内部参考.txt`，`export.json` 记录授权状态。
**不填就是 `unknown`**，导出只给警告——这层靠制度配合，不靠代码强制。
发图前没填授权，等于默认"这批图可能不能对外"。
见 `server/app/rights.py`。

## 技术约定

| 项 | 约定 |
|---|---|
| Python | `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe` |
| 依赖 | 后端**只用标准库**，不要引入框架；图像处理可用 opencv / pillow / numpy |
| 中文路径 | OpenCV 必须用 `cv2.imdecode(np.fromfile(p, np.uint8), ...)`，`imread` 会返回 None |
| 配置 | 改质量口径改 `config/config.toml` 的 `[compare]`；三处（toml / example / `config.py:DEFAULT`）要同步 |
| API Key | 存在 `config/config.toml`（已 gitignore）。**永远不要写进文档、日志或提交** |
| 模型名 | `doubao-seedream-5-0-flash-260915`，**日期后缀不能省**，否则 404 |

## 质检要求

任何图交付前必须有**可判定的验收**，不能靠"我觉得还行"。

| 类型 | 验收 |
|---|---|
| 前后对比图 | `python scripts/check_compare.py 产出/效果图/*.png`，退出码 0 |
| 贴屏类 | 四角放大自查；外侧环带 >200 亮像素数必须为 0；缩小到 30% 看轮廓 |
| 倒影类 | 差分图（加/不加相减），确认衰减到底边无断点 |
| 表格类 | Playwright 读 `scrollHeight` 验证无溢出 |

## 当前进度（2026-11-26）

已完成：多候选并发（修掉「要 3 张只给 1 张」，已用真实引擎验证 4/4）、
异步任务 + 进度、审核（选/淘汰）、导出（cover 裁切）、**保真分档 A/B/C**、
**语义判定接入视觉模型**（同款/换产品可判，比例不可判）、
**工艺模板**（`scene` / `white_bg` / `kv`，含版式自检）、
**品类一致性校验**（文案 vs 素材，生成前拦截，防「气垫霜配每天一粒」）、
**授权与示例闸门**（内部参考素材在导出目录强制留说明文件）。

未做：成对输入（img2img 前后对比）、透视贴屏模板、局部重做单张、改文案、权限。
详细施工顺序见 `搭建步骤.md`。

## 常用命令

```bash
.\run.ps1                                        # 启动服务（8765）
python scripts/smoke_test.py                     # 离线自检
python scripts/check_compare.py 产出/效果图/*.png # 效果图质检
python 产出/需求样张/_build_screen.py             # 重建 04 大屏（含倒影）
python 产出/需求样张/_cut_bottle.py               # 产品图抠图

# 接口速查（默认异步，?sync=1 同步）
# POST /api/batches                       {instruction, engine, candidates, template, category, copy, materials[], exemplars[]}
# GET  /api/batches/{id}                  未完成返回 stage/progress，完成返回批次
# GET  /api/templates                     可用工艺模板
# POST /api/batches/{id}/review           {index, action: choose|reject|reset}
# POST /api/batches/{id}/export           {indexes[], size}
```

## 关键文件

| 文件 | 作用 |
|---|---|
| `server/app/config.py` | API Key 填入口 + `DEFAULT` 配置 |
| `server/app/engines/seedream.py` | 方舟适配器，接口变更只改 `_build_body` / `_parse` |
| `产出/需求样张/_screen_kv.py` | KV 排版 + 透视贴屏（融合参数全在这） |
| `产出/需求样张/_build_screen.py` | 04 大屏一键构建 |
| `config/config.toml` | 实际配置（含 `[compare]` 阈值） |
| `项目方案.md` | 立项方案（对外） |

## 写文档的地方

- 新的实战经验、踩坑 → `knowledge/03-踩坑库.md`（当天写，格式：症状/根因/解法/教训）
- 参数改了 → 同步 `knowledge/04-参数与阈值.md`
- 新增脚本 → 登记到 `knowledge/05-资产索引.md`
- 对外设计文档 → `docs/`

## 当前最紧的三件事

1. 细微比例改动（10–30%）没有任何手段能自动发现——`spread` 抓不到、
   视觉模型看不出还说"把握 98"。见 `knowledge/07-待决事项.md` §2
2. 13 类新需求缺三类素材（院线 / 真人前后 / 门店价格）
3. 样张品牌与参数全是示例，落地要换真值
