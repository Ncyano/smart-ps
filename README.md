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
  pipeline/
    steps.py          度量与确定性回正（影调/饱和/锐化/评分）
    pack.py           素材打分、参考图打包、提示词组装
    run.py            六步编排
web/index.html        单页界面（无需构建）
config/               配置（config.toml 已被 .gitignore）
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
| POST | `/api/batches` | **跑一批**：素材 + 例图 + 文字。默认异步即时返回 job，`?sync=1` 同步返回结果 |
| POST | `/api/batches/{id}/review` | 审核：`{"index":2,"action":"choose\|reject\|reset"}` |
| POST | `/api/batches/{id}/export` | 导出：`{"size":"square_1080"}`，cover 裁切 |
| GET | `/api/batches` | 批次列表 |
| GET | `/api/batches/{id}` | 未完成返回 job 状态（stage/progress），完成返回批次详情 |
| GET | `/files/<path>` | 产物静态访问 |

`POST /api/batches` 示例：

```json
{
  "instruction": "背景纯白，主体居中，保留 Logo 与材质",
  "engine": "seedream",
  "candidates": 3,
  "materials": [{"name": "s1.png", "data_url": "data:image/png;base64,..."}],
  "exemplars": [{"name": "ref.png", "data_url": "data:image/png;base64,..."}]
}
```

返回里每个候选带分档（`A 可交付` / `B 待人工` / `C 不可用`）与逐条原因。

**分档不是算术平均，是三道硬门槛**：`structure`（主体结构，掩码加权 NCC 找主体模板）
≥0.72、`spread`（匹配框内主体铺展比）≤15%、`sharp`（主体区高频能量比）≥0.65 才给 A；
语义判定（视觉模型）与版式自检**只降不升**。`total` 只用于排序，不参与判定。
素材分两种模式：`object`（白底/单主体，三项全测）、`patch`（工厂图等多实例场景，
只测特征在不在，上限 B —— 不替人背书）。

已知做不到：10–20% 的细微比例改动测不准（调色会让"离背景色多远"漂 10–13%），
这一档一律给 B 而不是假装量准；"是不是同一款产品""文字有没有乱码"交给视觉模型，
其中 `aspect_ok` 实测不可信（17% 压扁它说"正常，把握 98"），只作提示不判死。
详见 `knowledge/04-参数与阈值.md` 与 `03-踩坑库.md` F 组。

## 这一版有什么、没有什么

**已有**

- 六步产线全部打通：题图分组选优 → 参考图打包（内容在前、风格在后）→ 多候选生成 → 三项评分 → 确定性回正 → 质检判定
- **多候选真正生效**：按引擎 `caps.batch` 拆分 + 线程池并发（方舟 `n` 参数无效，之前要 3 张只给 1 张）
- **异步任务 + 进度**：进程内线程池，状态机落盘 `job.json`，服务重启自动把中断任务标记为失败
- **审核 + 导出**：选中/淘汰/复位，按平台尺寸 cover 裁切导出
- StyleProfile 从例图统计（影调分位、饱和、高频能量、背景白度）
- 引擎抽象 + 自动回退（没 Key 不会报错，直接降级 mock 并说明原因）
- 批次可回读、可复现，产物与配置分离

**还没有（按里程碑排）**

- 真·云端联调：`seedream.py` 的请求/响应字段需按你拿到的官方文档校准一次
- 局部重做（rerun 单张）与改文案（现在只能整批重跑）
- 蒙版化局部提交 + 羽化贴回（现在整图提交，保真靠 prompt 约束）
- 本地 ONNX 算子（抠图/选区/擦除/放大）
- Planner（文字修饰 → 结构化指令，现在只做模板拼装）
- 异步任务与 SSE 进度（现在一次请求同步跑完）
- 本地服务 token 校验（现在只绑 127.0.0.1）

## 已知边界

- 素材质量分是相对值，只用于排序，不是绝对画质评价。
- 单页界面走的是整图 base64，单批 <= 96MB；批量大图场景后续换成直传 + 异步。
- 控制台中文若乱码，是 Windows 终端代码页问题，`run.ps1` 已切到 UTF-8。
