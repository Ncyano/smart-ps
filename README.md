# 智能 PS · 自动 P 图产线（初版框架）

素材图 + 往期例图 + 文字修饰 → 成品图。

现在这一版**开箱即跑、不联网也能跑通全链路**：没有 API Key 时自动用本地 mock 引擎，
填上火山方舟 Key 后切到云端模型（Seedream 5.0 Flash，约 0.12 元/张）。

- 设计文档：[框架设计](docs/superpowers/specs/2026-11-09-smart-ps-design.md) · [产线设计](docs/03-自动P图产线设计.md)
- 选型文档：[技术选型](docs/01-技术选型.md) · [本地模型](docs/02-本地模型选型.md)

## 快速开始

```powershell
.\run.ps1
# 或
python -m server.app.main
```

浏览器打开 <http://127.0.0.1:8765>。

只依赖 Python 标准库 + 本机已有的 Pillow / numpy / httpx / pydantic（不需要 pip install）。

跑一遍离线自检：

```powershell
python scripts/smoke_test.py     # 直接调产线，不启服务
python scripts/http_test.py      # 走 HTTP 接口（需先启动服务）
```

## ★ API 填入口（三个位置，改一个就生效）

**1. 界面里填（推荐）** — 右上角「设置 / API Key」，填 Key、Base URL、模型 ID，保存后写入 `config/config.toml`。
接口只回传掩码，不回传明文。

**2. 配置文件** — 复制模板后编辑：

```powershell
copy config\config.example.toml config\config.toml
```

```toml
[engine]
default = "seedream"          # 从 mock 切到云端

[engines.seedream]
api_key = "sk-..."            # ← 就填这一行
base_url = "https://ark.cn-beijing.volces.com/api/v3"
model = "doubao-seedream-5-0-flash-260915"   # 必须带日期后缀，否则 404
size = "2K"
```

**3. 环境变量兜底** — `$env:ARK_API_KEY = "sk-..."`，不落盘，适合临时测试。

代码里的入口在 `server/app/config.py` 的 `DEFAULT`，接口字段集中在 `server/app/engines/seedream.py`
（`_build_body` / `_parse` 两个函数）——**接口若变更，只改这两个函数，上层不用动**。

## 前后对比效果图标准（已量化）

`产出/效果图/` 那类「使用前 / 使用后」双联图的制图标准，已经从经验沉淀成可判定的数值：
[效果图制图标准](docs/04-效果图制图标准.md)。

阈值放在 `config/config.toml` 的 `[compare]` 段（代码里不写死），交付前用检测器卡一遍：

```bash
python scripts/check_compare.py 产出/效果图/*.png     # 成品：版式 + 背景 + 水印
python scripts/check_compare.py --src 源图.png         # 生成源图：跳过版式项
python scripts/check_compare.py 图.png --json          # 机器可读，可挂 CI
```

退出码 0 = 全通过，1 = 有 A 级硬指标不合格（会点名具体是哪项）。

判定分三层：**A 硬指标**自动拦截（画布、中缝、背景边缘密度/高频/主色集中度/亮度、水印残留）；
**B 软指标**只提示（头顶留白、人物头宽比）；**C 生成约束**人工签收（五官、发丝、锁骨、衣褶、分屏位置不变）。

阈值是在 5 个已知好坏的样本上标定的，实测能干净分开「干净墙面」与「家居杂物背景」，
标定表和局限写在文档 §4。

## 技能：前后对比效果图

上面这套规则已经打包成项目级技能，做效果图时会自动带上：

```
.workbuddy/skills/effect-compare-image/
  SKILL.md                        执行入口：工作流 + 12 条铁律 + 交付检查
  references/prompt-templates.md  可直接复制的提示词（换背景/减重两档/肤色/水印修补）
  references/qa-and-thresholds.md 三层判定、两档阈值、标定数据、检测器用法
  references/pitfalls.md          全部已知陷阱与失败解法留档
```

换台机器或新开会话时，让助手"按 effect-compare-image 技能做效果图"即可复用。


## 项目知识库（Agent Wiki）

接手这个项目（或新开一个 AI 会话）时，**从这里开始**：

```
knowledge/
├── README.md            导航：每篇什么时候读
├── 00-项目上下文.md       项目是什么、做到哪一步
├── 01-需求图谱.md         7 类旧需求 + 13 类新需求
├── 02-工艺路线.md      ★ 核心方法论：什么交给 AI、什么交给代码
├── 03-踩坑库.md        ★ 30 条「症状→根因→解法」
├── 04-参数与阈值.md       实测参数值
├── 05-资产索引.md         脚本/配置/产出地图
├── 06-术语表.md           KV、quad、内檐、lift…
└── 07-待决事项.md         未定问题与已知缺陷
```

零依赖检索（纯标准库，BM25）：

```bash
python scripts/kb_search.py "错位"
python scripts/kb_search.py "倒影 羽化" -n 8
python scripts/kb_search.py --list          # 列出全部章节
```

根目录的 `AGENTS.md` 是给 AI 助手的开工约定（三条铁律 + 技术约定 + 质检要求）。

## 目录结构

```
server/app/
  main.py             HTTP 服务（仅标准库）+ 路由
  config.py           ★ API 填入口 & 配置读写
  schemas.py          API 入参模型
  store.py            data/ 落盘、批次读写
  engines/
    base.py           Engine / Capabilities / GenRequest 契约
    mock.py           本地确定性引擎（0 元、不联网）
    seedream.py       ★ 火山方舟适配器
    registry.py       引擎注册、路由、不可用时回退
  task.py             ★ 需求 → 结构化任务单（三类区域 / 验收条件 / 待确认项）
  region.py           三类区域事后核查（文字带细节密度，只提示不判死）
  review.py           ★ 审核反馈闭环（问题类型 → 修法，落日志可统计）
  redo.py             ★ 定向重做（本地 0 元先修，修不了才回引擎）
  record.py           ★ 逐张制作记录（七块，导出时写 record.json / 记录.md）
  cases.py            ★ 案例检索：同类物料的历史成功/踩坑记录（只认真实数据）
  rights.py           素材授权五级声明 → 三级交付范围
  gate.py             ★ 交付闸门：逐项判定，全过才可交付
  claims.py           ★ 合规文字 + 数据来源检查（生成前硬拦截）
  taxonomy.py         ★ 品类 / 物料注册表（读 config/taxonomy.toml）
  scenes.py           ★ 场景四角检测与人工校准存档
  pipeline/
    steps.py          度量与确定性回正（影调/饱和/锐化/评分）
    pack.py           素材打分、参考图打包、提示词组装
    run.py            编排（含检查点，可断点续跑）
    fidelity.py       保真：主体 / 比例 / 标签区三项
    judge.py          语义判定（视觉模型，只降不升）
    category.py       品类一致性（生成前拦截）
web/index.html        单页界面（无需构建）
templates/            工艺模板（生成类 3 + 合成类 5，见下）
config/               配置（config.toml 已被 .gitignore）
  taxonomy.toml       ★ 品类与物料注册表（加品类改这里）
  compliance.toml     ★ 合规词表（绝对化 / 疗效 / 第三方商标）
verify/               ★ 按模板的验收集与报告（scripts/verify_templates.py 生成）
data/batches/<id>/    每批的素材、候选、成品、batch.json
scripts/              冒烟测试 / HTTP 测试 / ★ check_compare.py / kb_search.py 知识库检索
docs/04-效果图制图标准.md  ★ 前后对比图的量化标准与标定数据
knowledge/            ★ 项目知识库（踩坑库 / 工艺路线 / 参数实测值）
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活检查 |
| GET | `/api/config` | 当前配置（Key 掩码） |
| POST | `/api/config` | 改配置（写回 config.toml） |
| GET | `/api/engines` | 引擎列表与可用性 |
| GET | `/api/templates` | 工艺模板列表（含所需文案字段与额外输入） |
| GET | `/api/taxonomy` | 品类 / 物料注册表 |
| GET | `/api/feedback` | 反馈统计：按问题类型 / 模板计数，附改进建议 |
| GET | `/api/cases?template=&issues=` | 同类物料的历史案例（成功 + 踩坑），供任务单与界面展示 |
| POST | `/api/batches/{id}/feedback` | 结构化审核意见：`{"index":1,"issues":["pack_text"],"disposition":"rejected","note":"瓶身字被改"}` |
| POST | `/api/batches/{id}/redo` | 定向重做：`{"index":1,"issues":[...],"plan_only":true}` 只出方案；不带 `plan_only` 执行本地能修的部分 |
| POST | `/api/scenes/detect` | 场景照找四角，回置信度；不足时前端转人工校准 |
| POST | `/api/batches` | **跑一批**：素材 + 例图 + 文字。默认异步即时返回 job，`?sync=1` 同步返回结果 |
| POST | `/api/batches/{id}/review` | 审核：`{"index":2,"action":"choose\|reject\|reset\|confirm\|unconfirm"}` |
| POST | `/api/batches/{id}/export` | 导出：`{"size":"square_1080","formal":false}`，cover 裁切 |
| POST | `/api/batches/{id}/rights` | 补授权声明：`{"rights":"owned","by":"张三","evidence":"合同 A-01"}`，记人与时间 |
| POST | `/api/batches/{id}/retry` | 有检查点就续跑（不重复扣费） |
| GET | `/api/batches` | 批次列表 |
| GET | `/api/batches/{id}` | 未完成返回 job 状态（stage/progress），完成返回批次详情 |
| GET | `/files/<path>` | 产物静态访问 |

`POST /api/batches` 示例：

```json
{
  "instruction": "背景纯白，主体居中，保留 Logo 与材质",
  "engine": "seedream",
  "candidates": 3,
  "template": "price",
  "category": "skincare",
  "rights": "owned",
  "copy": {"title": "奶油肌气垫", "price_now": "199", "price_was": "299", "unit": "元/盒"},
  "claims": [{"text": "199", "kind": "price", "source": "价目表 2026-11 版 P2"}],
  "materials": [{"name": "s1.png", "data_url": "data:image/png;base64,..."}],
  "exemplars": [{"name": "ref.png", "data_url": "data:image/png;base64,..."}],
  "request_id": "任意唯一值，重复提交不会重复扣费"
}
```

返回里每个候选带 `delivery`：`status`（`deliverable` / `manual` / `internal_only` / `rejected`）、
`blockers`（卡在哪几项）、以及 `checks[]` 逐项结论。`tier`（A/B/C）仍然保留，但它只反映像素质量。

**分档不是算术平均，是三道硬门槛**：`structure`（主体结构，掩码加权 NCC 找主体模板）
≥0.72、`spread`（匹配框内主体铺展比）≤15%、`sharp`（主体区高频能量比）≥0.65 才给 A；
语义判定（视觉模型）与版式自检**只降不升**。`total` 只用于排序，不参与判定。
素材分两种模式：`object`（白底/单主体，三项全测）、`patch`（工厂图等多实例场景，
只测特征在不在，上限 B —— 不替人背书）。

已知做不到：10–20% 的细微比例改动测不准（调色会让"离背景色多远"漂 10–13%），
这一档一律给 B 而不是假装量准；"是不是同一款产品""文字有没有乱码"交给视觉模型，
其中 `aspect_ok` 实测不可信（17% 压扁它说"正常，把握 98"），只作提示不判死。
详见 `knowledge/04-参数与阈值.md` 与 `03-踩坑库.md` F 组。

## 交付状态：按项判，不按分数

旧版用 A/B/C 一档定"能不能发"，而这一档是保真、风格、版式算出来的——**只能说明像不像，
不能说明能不能发**。三项里两项满分、第三项很差，也能凑出一个高分。这类错误发出去收不回。

现在 `gate.py` 把结论拆成逐项检查，全部有证据通过才叫 `deliverable`：

| 检查项 | 判定依据 | 没过的后果 |
|---|---|---|
| 素材授权 | `rights` 声明 | `third_party` / `sample` → 仅内部参考（与质量无关） |
| 合规文字 | 绝对化用语 / 疗效宣称 / 第三方商标 | 生成前 400 拦截，不是事后标记 |
| 数据来源 | 价格、含量、专利号是否绑了 `claims` | 待人工，导出记录里留下"无来源" |
| 品类一致 | 文案 vs 素材品类 | 冲突直接拦；**未校验只算"未校验"，不算通过** |
| 产品主体 | 保真 `structure` | 对不上 → 不可用 |
| 比例 / 标签区 | 铺展比、标签区边缘相关 | 比例 >32% → 不可用；标签对不上 → 不可用 |
| 版式自检 | 文字溢出、抠图异常 | 不可用 |
| 语义判定 | 视觉模型"是不是同一款" | 不是同一款 → 不可用；没启用 → 待人工 |
| 人工确认 | 审核人点「内容已核对」（记名字与时间） | 待人工 |

汇总规则是**不做加权、不做抵消**：有硬失败 → 不可用；授权仅内部 → 仅内部；
任何一项 warn / unknown → 待人工；全过 → 可交付。

**声明 ≠ 核验。** 授权声明必须记下「谁说的、什么时候、凭什么」：
`rights_by` + `rights_evidence` 写进 `rights_log`，闸门说明里带出这句话。
只有等级、没有署名的声明，正式导出会被拦（409）——出事时得能回答"谁说有授权的"。
`verified` 是另一件事，只有真看过授权原件才为 true，措辞由「未经核验」改为「已核验」。

`unknown` 单独一档是这次改动的重点：视觉模型没开通、品类没看成、没人核对过，
都不是"没问题"，是"没人看过"。把没看过的当通过，是这类系统最容易犯的错。

导出分两种：

- **正式导出** `formal: true`：必须授权 ok 且至少一张可交付，否则 409 并说明卡在哪。
- **内部参考导出**：允许带待人工的图，但会额外生成 `preview/` 带水印副本、
  `manifest.json`、`清单.md`，授权为第三方/示例时还会写 `_仅内部参考.txt`。

## 需求先变成任务单，再动模型

"做个卖点图"五个字里藏着几十个决定。这些决定如果只留在提示词里，模型会**自己补齐**——
补出来的产品细节是编的，补出来的数字也是编的。

所以生成之前先把需求整理成一张单子（`task.py`），落盘在 `batch["task"]`：

```
物料类型 / 渠道与尺寸 / 素材与参考成图 / 三类区域
必须保持 / 允许改动 / 禁止项 / 文案与数据来源 / 验收条件 / 待确认项
```

**三类区域**是关键，它一条声明同时管住三个环节：

| 区域 | 内容 | 谁处理 | 落到哪 |
|---|---|---|---|
| 保护区 | 产品外形、包装文字、标签数字、人物身份、证书原件 | 从甲方素材保留 | 提示词约束 + 闸门「标签/细节」项 |
| 生成区 | 背景、氛围、非关键道具、环境光 | 模型 | 提示词 |
| 排版区 | 标题、卖点、价格、表格、脚注 | 代码排版 | 模板 `apply()` + 版式自检 |

`unresolved` 里分两档：`block`（缺了不能开工，闸门判不可用）与 `note`（只是提示，
例如没给参考成图）。**note 不降级**——没给例图是正常选择，不能因此判待人工。

## 审核反馈闭环与定向重做

旧版审核只有"通过 / 不通过"。三个月后你只知道废图率 40%，不知道是模型重画了包装字、
还是价格没来源、还是四角贴歪了——三类修法完全不同。

`review.py` 把意见拆成结构化记录：

```
谁 · 什么时候 · 哪一批哪一张 · 哪类问题 · 具体说明 · 怎么处理的 · 用了哪一版
```

每类问题绑定一条修法（`fix`），`redo.py` 按它分流：

| 坏在哪 | 问题类型 | 修法 | 成本 |
|---|---|---|---|
| 文字排版 | `copy_wrong` | 改文案重排 | 0 元 |
| 数据 | `data_missing` | 补来源后重排 | 0 元 |
| 主体被重画 | `product_deform` / `pack_text` | 从原图抠出主体重新合成 | 0 元 |
| 贴屏 | `edge_unnatural` | 调四角与融合参数 | 0 元 |
| 背景 | `bg_mismatch` | 只重做背景（scene） | 1 张生成费 |
| 整体风格 | `style_off` | 整图重生成 | 整批生成费 |

本地重做会写新版本 `final_{i}_v{n}.png`，旧版不覆盖，`cand["versions"]` 记每一步做了什么。
`GET /api/feedback` 按模板 × 问题类型出统计，并给出可执行的改进建议。

## 每张图的制作记录

导出的每张图都带一份 `record.json`（机读）与 `记录.md`（人读），七块内容：

1. 素材与参考成图的关联　2. 原始需求 + 结构化任务单　3. 模型 / 版本 / 提示词 / 参数
4. 模板 / 分区核查 / 后处理步骤　5. 候选结果、审核结论、修改原因　6. 文案与数据来源
7. 导出规格与版本

客户问"这张怎么来的"，翻记录就有；出了问题也能分清是模型、素材、排版还是需求的问题。

## 工艺模板（8 个）

| 模板 | 输入 | 是否调模型 | 要点 |
|---|---|---|---|
| `scene` | 素材 | 是 | 换场景，主体不动 |
| `white_bg` | 素材 | 是 | 纯白底重排，白底图只裁不键控 |
| `kv` | 素材 + 文案 | 是 | 上方文字区暗底 + 白字，字号按高度自适应 |
| `price` | 素材 + 现价 | 是（只生成背景） | 现价必填且要有来源，价签由 PIL 画 |
| `patent` | 原件扫描件 + 引用字段 | 否 | **没有扫描件就拒绝出图**，不生成证书内容 |
| `table` | 表头 + 行数据 | 否 | 逐格量宽高，溢出即报错 |
| `pair` | before + after | 否 | 默认标「示意图」，真实拍摄需显式声明 |
| `screen` | 场景照 + 画面 | 否 | 自动找四角，置信度 <0.55 转人工校准 |

## 按模板的验收集

```bash
python scripts/verify_templates.py          # 15 个用例（含 5 个必须失败的反例）
python scripts/verify_templates.py -k price # 只跑某类
```

结果写到 `verify/report.md`：每个模板的画布、版式结论、确认前后状态、成本与耗时。
反例包括缺标题、缺现价、列数不一致、缺扫描件、四角贴满整图——这些情况必须失败。

## 用真实数据做的两次校准

这两个脚本不打分、不美化，只回答"现在到底行不行"。

```bash
python scripts/bench_judge.py        # 判定模型（Seed 2.1 Lite）逐格准确率
python scripts/calibrate_gate.py     # 拿 18 张真实交付图过一遍闸门
```

**判定模型**（`verify/judge_report.md`）：`same_product` 7/7、`aspect_ok` 2/4
（压扁 17%、拉长 25% 都说"比例正常"，把握 99）、`text_ok` 只有 1 个负样本，不足以定论。
所以产线里 `aspect_ok` 只作提示，永不单独判死。

**闸门校准**（`verify/gate_calibration.md`）：`交付/` 18 张全部落「仅内部参考」
（都是第三方品牌素材），其中 9 张还卡在「数据来源」——因为在图上的价格与含量
没有绑定出处。这证明授权与来源两项对真实图确实生效，**不是靠人记得**。
这份报告**不证明**保真阈值合适：历史图没有可追溯素材，像素项标 `skip`。

## 这一版有什么、没有什么

**已有**

- 六步产线全部打通：题图分组选优 → 参考图打包（内容在前、风格在后）→ 多候选生成 → 三项评分 → 确定性回正 → 质检判定
- **多候选真正生效**：按引擎 `caps.batch` 拆分 + 线程池并发（方舟 `n` 参数无效，之前要 3 张只给 1 张）
- **异步任务 + 进度 + 断点续跑**：状态机落盘 `job.json`，检查点落 `resume.json`；
  服务重启时**能从已完成的那一步继续**（最贵的生成只做一次），同一 `request_id` 重复提交不会重复扣费
- **审核 + 导出**：选中/淘汰/复位，按平台尺寸 cover 裁切导出
- StyleProfile 从例图统计（影调分位、饱和、高频能量、背景白度）
- 引擎抽象 + 自动回退（没 Key 不会报错，直接降级 mock 并说明原因）
- 批次可回读、可复现，产物与配置分离
- **交付闸门**（授权 / 合规 / 数据来源 / 品类 / 保真 / 版式 / 语义 / 人工确认，逐项判定）
- **合规与数据来源检查**：绝对化用语与疗效宣称生成前拦截，数字必须绑定出处
- **品类注册表**：加品类改 `config/taxonomy.toml`，不用改代码
- **8 个工艺模板 + 按模板验收集**
- **需求任务单**（物料/渠道/三区/验收条件/待确认项），闸门按它判「需求完整度」
- **审核反馈闭环**：10 类问题 → 修法路由，日志可统计，界面一键出方案
- **定向重做**：文案/数据/主体/贴屏本地 0 元修好，写新版本不覆盖旧版
- **逐张制作记录**：导出时写 `record.json` + `记录.md`
- **配对台账**：交付 33 张 → 12 可配对 / 21 待确认（素材池只有 11 张，覆盖不了）
- **模型对照评测脚本**：固定样本 + 一次可用率 / 单张可用成本
- **判定模型实测**：`same_product` 可信、`aspect_ok` 不可信（有数字）
- **闸门真实样本校准**：18 张真实交付图逐张过，落 `verify/gate_calibration.md`
- **授权声明留痕**：声明人 / 时间 / 依据 / 是否核验，正式导出要求能追到人
- **案例检索**：同类物料的历史成功与踩坑记录进提示词（只认真实审核数据）
- **配对台账升级**：结构相似度作线索 + `配对决定.csv` 人工确认合并
- **内部演示副本**：`scripts/mark_internal.py` 批量盖章，原图不动

**还没有（按里程碑排）**

- 真·云端联调：`seedream.py` 的请求/响应字段需按你拿到的官方文档校准一次
- 局部重做（rerun 单张）与改文案（现在只能整批重跑）
- 标签区"局部糊"自动识别（现在只判内容是否被改，模糊靠清晰度项 + 人工确认）
- 蒙版化局部提交 + 羽化贴回（现在整图提交，保真靠 prompt 约束）
- 本地 ONNX 算子（抠图/选区/擦除/放大）
- Planner（文字修饰 → 结构化指令，现在只做模板拼装）
- 异步任务与 SSE 进度（现在一次请求同步跑完）
- 本地服务 token 校验（现在只绑 127.0.0.1）

## 已知边界

- **分区核查只提示、不判死**：文字带细节密度阈值（2.2 倍）只在合成样例上标过，
  真实照片上没做过标定。它不参与可交付判定，只写进制作记录供人看。
- **配对台账用关键词规则，不是语义**：33 张交付里 12 张可配对、21 张待确认，
  原因是素材池只有 11 张（没有大屏 / 表格 / 尺码 / 海报的素材）。待确认要人看一眼，
  台账里不编配对——编一次，后面的评测就全歪。
- **Seed 2.1 Lite 不能出图**：拿它去调生图接口，方舟直接回
  `400 InvalidParameter: image generation is only supported by certain models`。
  它在本项目里的角色是**判定**，不是生成，所以"两款模型哪个出图更好"这个问题本身不成立。
  它的判定能力已单独实测（`verify/judge_report.md`）：`same_product` 7/7 可信、
  `aspect_ok` 2/4（17%/25% 变形仍说"正常"，把握 99）、`text_ok` 样本不足。
- **模型对照只跑了 flash**：`doubao-seedream-5-0-flash` 4 类物料各 1 张，一次可用 1/4；
  `doubao-seedream-5-0-pro` 本账号**未开通**（4 次全 404），所以没有 pro 的数据。
  报告见 `verify/models_report.md`，脚本默认仍只打印计划，`--run` 才花钱。
- 本地重做**不重跑语义判定**（不调视觉模型），所以重做后的图仍需人工确认一次。
- **配对台账的相似度只是线索**：交付图是排过版的成品，与素材结构天然对不上，
  同产品常常只有 0.1–0.4。待确认 21 张实测相似度 0.03–0.11，说明素材池里
  **真的没有对应素材**，不是脚本偷懒。人工决定填 `verify/配对决定.csv` 后重跑合并。
- 素材质量分是相对值，只用于排序，不是绝对画质评价。
- 单页界面走的是整图 base64，单批 <= 96MB；批量大图场景后续换成直传 + 异步。
- 控制台中文若乱码，是 Windows 终端代码页问题，`run.ps1` 已切到 UTF-8。
