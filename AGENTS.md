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

**6. 交付状态按项判，分数不能抵消。**
`gate.py` 逐项检查授权、合规、数据来源、品类、主体、比例、标签、版式、语义、人工确认。
任何一项 `fail` 就是不可用，`warn`/`unknown` 就是待人工——**不许用总分把它们拉回来**。
`unknown` 不是通过：没校验过的东西不能算过关。
接口 `export` 的 `formal: true` 只认全过 + 授权 ok。

**7. 文案里的数字必须能指出来源。**
价格、含量、专利号、检测值由 `claims.py` 抽出，逐条要求绑定 `claims`。
缺来源不拦生成（草稿仍有价值），但**永远不能进可交付**，导出记录会留下"无来源"。
绝对化用语与疗效宣称是硬拦截，生成前 400。

**8. 合成类物料不许模型碰内容。**
专利证书、检测报告、表格、价格数字：要么用真实扫描件，要么用代码排版。
`patent` 模板没有扫描件直接拒绝出图——模型画一张"看起来像证书"的图，
比画错产品严重得多。


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

## 当前进度（2026-11-27）

已完成：多候选并发（修掉「要 3 张只给 1 张」，已用真实引擎验证 4/4）、
异步任务 + 进度 + **检查点续跑（重启不重复付费）**、审核（选/淘汰/核对）、导出（cover 裁切）、
**保真分档 + 主体/比例/标签区三项独立检查**、
**语义判定接入视觉模型**（同款/换产品可判，比例不可判）、
**工艺模板 8 个**（scene / white_bg / kv / price / patent / table / pair / screen）、
**品类注册表**（可配置，未校验单独一档）、
**交付闸门**（逐项判定，分数不能抵消）、
**合规与数据来源**（绝对化/疗效/商标硬拦截，数字要绑来源）、
**授权与示例闸门**（内部参考强制留痕 + 水印预览）、
**按模板验收集**（`verify/report.md`）。

未做：局部重做单张、改文案、权限、试点部署。
详细施工顺序见 `搭建步骤.md`。

## 关键文件

| 文件 | 作用 |
|---|---|
| `server/app/config.py` | API Key 填入口 + `DEFAULT` 配置 |
| `server/app/gate.py` | ★ 交付闸门：逐项判定，写回 delivery/verdict |
| `server/app/claims.py` | ★ 合规词表 + 数据来源核对 |
| `server/app/taxonomy.py` | ★ 品类/物料注册表（读 config/taxonomy.toml） |
| `server/app/scenes.py` | ★ 场景四角检测、置信度、校准存档 |
| `server/app/rights.py` | 授权五级 → 三级交付范围 |
| `server/app/templates/builtin.py` | 生成类模板（scene/white_bg/kv） |
| `server/app/templates/extra.py` | 合成类模板（pair/price/patent/table/screen） |
| `server/app/export.py` | 导出 + manifest + 水印预览 + 正式闸门 |
| `server/app/jobs.py` | 任务状态、检查点续跑、幂等 |
| `server/app/pipeline/run.py` | 编排（检查点、生成前拦截） |
| `server/app/pipeline/fidelity.py` | 保真三项 + 标签区 + 重排归一化 |
| `config/taxonomy.toml` | 品类与物料配置（加品类改这里） |
| `config/compliance.toml` | 合规词表（追加/替换） |
| `verify/report.md` | 按模板验收集报告（每次跑更新） |
| `产出/需求样张/_screen_kv.py` | KV 排版 + 透视贴屏（融合参数全在这） |
| `项目方案.md` | 立项方案（对外） |

## 写文档的地方

- 新的实战经验、踩坑 → `knowledge/03-踩坑库.md`（当天写，格式：症状/根因/解法/教训）
- 参数改了 → 同步 `knowledge/04-参数与阈值.md`
- 新增脚本 → 登记到 `knowledge/05-资产索引.md`
- 对外设计文档 → `docs/`

## 当前最紧的三件事

1. 标签区**局部模糊**抓不到：64px 归一化比对能认出"字被换了"，但把标签糊掉
   相似度还有 0.85。整体清晰度项也兜不住（只占主体 1/4）。见 `knowledge/07-待决事项.md` §3
2. 细微比例改动（10–30%）没有任何手段能自动发现——`spread` 抓不到、
   视觉模型看不出还说"把握 98"。见 `knowledge/07-待决事项.md` §2
3. 13 类新需求缺三类素材（院线 / 真人前后 / 门店价格）
