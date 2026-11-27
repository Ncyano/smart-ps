# 智能 PS 工具 · 框架设计

日期：2026-11-09　状态：设计稿（待评审）　配套：[技术选型](../01-技术选型.md)

---

## 0. 一句话定位

**一个会在本地做合成、只把该重绘的那块丢给云端的修图工具。** 用户说人话，工具把它拆成可预览、可撤销、可单步重跑的编辑步骤。

## 1. 我替你做的三个决定（可以推翻）

| 决定 | 取值 | 理由 |
|---|---|---|
| 形态 | **本地 Web 应用**（浏览器打开 `127.0.0.1`），结构上预留 PS 插件外壳 | 你有浏览器、没有显卡；同一套内核以后能被 UXP 面板直接调用，不用重写 |
| 后端语言 | **Python + FastAPI** | ONNX / OpenCV / 抠图生态都在 Python，云 API 也只是一层 HTTP |
| 前端栈 | **React + Konva**（Canvas 2D 图层） | 图层、蒙版、撤销栈用 Canvas 最直接；不上 WebGL，本机核显也够 |

这三点如果你有偏好（比如一定要 Photoshop 插件优先、或者要 Node 全栈），现在推翻成本最低。

## 2. 全系统只有一条主线原则

> **云端只负责「看不懂的那一小块」，其余全部本地做。**

云端编辑模型最大的毛病不是画得不好，而是**顺手改了不该改的地方**——换背景把人脸也重画了、改材质把透视带歪了。这类问题在提示词层面治不好，只能在架构层面治：

```
选区域 → 本地生成精确蒙版 → 只把「蒙版区域 + 周边上下文」提交云端
       → 云端返回重绘结果 → 本地按羽化蒙版贴回原图 → 保护区域像素级不变
```

这条链路带来三个副产品：

1. **保真**：蒙版外的像素逐位不变，可以拿去做对比图和验收。
2. **省钱**：只提交裁剪区域，或提交整图但只取蒙版区，输出尺寸可控。
3. **可回退**：每一步都是独立算子，出问题只重跑那一步，不重跑整条链。

## 3. 架构图

```
┌───────────────────────── 浏览器（前端外壳，可换成 UXP 面板）─────────────────────────┐
│  React + Konva：图层栈 / 蒙版笔刷 / 前后对比 / 历史时间线 / 成本与耗时显示             │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │ HTTP + SSE（仅 127.0.0.1 + token）
┌──────────────────────────────────────▼───────────────────────────────────────────┐
│                        Core Service（FastAPI，本机常驻）                            │
│                                                                                   │
│   ┌───────────┐    ┌─────────────┐    ┌────────────┐    ┌───────────────────┐    │
│   │  Planner  │───▶│  EditPlan   │───▶│  Executor  │───▶│   Compositor      │    │
│   │ VLM 解析   │    │  JSON DAG   │    │ 步骤调度/重试│    │ 蒙版融合 + 羽化     │    │
│   └───────────┘    └─────────────┘    └─────┬──────┘    └─────────┬─────────┘    │
│        ▲                                    │                     │              │
│        │                    ┌───────────────┴──────┐              │              │
│        │                    ▼                      ▼              ▼              │
│   ┌────┴──────┐   ┌──────────────────┐   ┌─────────────────┐  ┌──────────────┐   │
│   │ 能力探测   │   │ Local Ops（CPU）  │   │  Engine Router  │  │  Document    │   │
│   │ 图像分析   │   │ 抠图/分割/擦除/   │   │ Flash/Pro/Qwen/ │  │  图层+历史    │   │
│   │ VLM 看图   │   │ 放大/修脸/合成    │   │ GPT/Nano + 降级链 │  │  SQLite      │   │
│   └───────────┘   └──────────────────┘   └────────┬────────┘  └──────────────┘   │
│                                                    │                              │
└────────────────────────────────────────────────────┼──────────────────────────────┘
                                                     │ HTTPS
                                       ┌─────────────▼──────────────┐
                                       │ 火山方舟 / 百炼 / OpenAI    │
                                       └────────────────────────────┘
```

## 4. 五个核心抽象

整个框架的骨架就是这五个东西，其余都是实现细节。

### 4.1 Document / Layer（文档模型）

仿 PS 的非破坏性结构：

```python
Document: id, size, layers[], active_layer, meta
Layer:    id, kind(image|mask|text|adjust|group), name,
          source(asset_id | parent_layer_id | op_node_id),
          transform, opacity, blend_mode, visible, alpha_mask
```

- 每个图层记录**它是怎么来的**（`origin: op_node_id`），而不是只有像素。
- 重跑某步 = 让依赖它的图层重新求值，历史里其余步骤不动。
- 保存为「文档 + 素材引用」，不存整张位图快照；工程文件小、可回溯。

### 4.2 EditPlan（编辑计划）

Planner 的输出、Executor 的输入、前端预览的对象，同一个 JSON。

```json
{
  "version": 1,
  "intent": "把背景换成办公室，人别动，脸保留原样",
  "steps": [
    {"id": "s1", "op": "segment", "params": {"target": "person", "mode": "subject"}, "out": "m_person"},
    {"id": "s2", "op": "invert",  "in": ["m_person"], "out": "m_bg"},
    {"id": "s3", "op": "feather", "in": ["m_bg"], "params": {"radius": 3}, "out": "m_bg_f"},
    {"id": "s4", "op": "cloud_edit",
     "in": ["image:base", "m_bg_f"],
     "params": {"engine": "seedream-flash", "instruction": "现代办公室背景，冷白光，景深虚化", "protect": ["m_person"], "size": "2K"},
     "out": "bg_new"},
    {"id": "s5", "op": "composite", "in": ["image:base", "bg_new", "m_bg_f"], "out": "image:result"}
  ],
  "estimate": {"cost_cny": 0.12, "seconds": 8, "engine": "seedream-flash"}
}
```

规则：

- **step id 即节点 id**，`in`/`out` 构成 DAG，天然支持并行（s1 与 s4 之外的独立分支）与缓存。
- **op 是白名单**，Planner 只能选已注册的算子；选了不存在的算子直接拒绝，不让模型自由发挥。
- **estimate 强制执行**：先把价格与耗时算给用户看，超过阈值（如单次 > 0.5 元）要求确认。
- **命名空间固定**：`image:base` / `layer:<id>` / `mask:<id>` / `asset:<id>` / `mask:selection`（用户手画的选区）。Planner 必须直接消费 `mask:selection`，**不允许重新 segment 一遍**——否则用户圈的范围就白圈了。

### 4.3 Operator（算子）

统一的算子签名，本地算子和云端引擎在 Executor 眼里是同一种东西：

```python
class Operator(Protocol):
    name: str                       # "matting" / "cloud_edit" / ...
    params_schema: dict             # JSON Schema，用于校验与自动生成前端表单
    def run(self, ctx: RunContext, inputs: dict, params: dict) -> dict: ...
```

已规划的算子：

| 分类 | 算子 | 实现 |
|---|---|---|
| 选区 | `segment`（主体/点/框）、`matte`（发丝级抠图）、`mask_edit`（增删改蒙版） | SAM / BiRefNet |
| 变换 | `crop` `resize` `rotate` `perspective` | OpenCV |
| 内容 | `inpaint`（擦除物体）、`cloud_edit`（指令重绘）、`generate`（新建图层）、`outpaint`（扩图） | LaMa / 云端 |
| 质感 | `upscale`、`face_restore`、`denoise`、`color_match`、`relight` | Real-ESRGAN / CodeFormer / 自研 |
| 合成 | `feather` `composite` `blend` `invert` | NumPy + OpenCV |

### 4.4 Engine（云模型适配器）

```python
class Engine(Protocol):
    id: str                          # "seedream-flash" / "qwen-image-3"
    caps: Capabilities               # 见下
    def edit(self, req: EditRequest) -> EditResult: ...
    def generate(self, req: GenerateRequest) -> GenerateResult: ...
```

`Capabilities` 是路由和降级的唯一依据：

```python
Capabilities(max_px=2048*2048, refs=10, mask=True, transparent=True,
             cost_cny=0.12, latency_p50=6.0, text_strength="low",
             regions=["cn"])
```

| 引擎 id | 定位 | 成本 | 关键 caps |
|---|---|---|---|
| `seedream-flash` | 默认档 | 0.12 元 | refs=10, transparent=True, 2K |
| `seedream-pro` | 成品档 | 0.3 元 | 更大画幅、质感更强 |
| `qwen-image-3` | 中文文字档 | 0.25 元 | 中文原生、多图融合 |
| `gpt-image-2` / `nano-banana-pro` | 天花板开关 | 0.3–1.5 元 | 4K、强指令跟随 |
| `local-stub` | 离线开发/测试 | 0 | 返回确定性假图，保证无网也能开发 |

**路由策略**（按顺序判定，命中即停）：

1. 计划显式指定引擎 → 用它（用户手动切"精修"就走这条）。
2. 含中文文字排版意图 → `qwen-image-3`。
3. 要求 4K 或"最高质量" → `gpt-image-2` / `nano`。
4. 其余 → `seedream-flash`。
5. 失败/超时/审核拒绝 → 提出降级建议（`[flash → pro → qwen]`），**推 `needs_confirm` 事件等用户点，不自动烧钱**。

> 降级会改变价格（0.12 → 0.3 元），静默执行等于绕过了 estimate 的确认机制。这一条优先于"自动重试"的便利性。

### 4.5 Job（任务）

所有耗时操作都异步化：

```
POST /api/jobs {plan}  →  {job_id}
GET  /api/jobs/{id}/events  →  SSE: step_started / step_done / progress / cost / error / done
```

- 每步产出落盘，SSE 推给前端做「逐步点亮」的进度条——用户能看见"正在抠图 → 正在重绘背景 → 正在融合"。
- 失败可**单步重试**，只重跑该节点及其下游。
- 缓存键：`sha256(engine_id + instruction + size + mask_hash + 输入图哈希)`，命中直接复用，成本 0。

## 5. 一条真实请求的完整链路

> 用户圈住人像，说"背景换成办公室，人别动，脸保留原样"

```
1. 前端    取当前图层 + 选区蒙版 → POST /api/plan
2. Planner 把「原图缩略图 + 选区 + 用户话」交给 VLM
           → 返回 EditPlan（segment → invert → feather → cloud_edit → composite）
           → 校验算子白名单、补全默认参数、算出 estimate(0.12 元 / 8s)
3. 前端    显示计划卡片：「抠出人物 · 重绘背景 · 融合，约 0.12 元 / 8 秒 [执行]」
4. 用户    点执行 → POST /api/jobs
5. Executor
   s1 segment(person)  本地 BiRefNet，约 2s         → m_person
   s2 invert           本地，毫秒                    → m_bg
   s3 feather(3px)     本地，毫秒                    → m_bg_f
   s4 cloud_edit       裁剪到 m_bg 包围盒 + 30px 上下文
                       提交 seedream-flash，指令 + protect=m_person
                       返回 → 还原到原图坐标            → bg_new
   s5 composite        按 m_bg_f 羽化贴回，蒙版外像素不变 → image:result
6. 前端    新图层「背景重绘 #1」入栈，可对比、可调不透明度、可撤销
7. 计费    实际 0.12 元入账；缓存命中则记 0
```

## 6. 接口契约

前后端共享 `packages/contracts/`，用 JSON Schema 单手维护，自动生成 TS 类型与 Python 校验器。

```http
POST   /api/documents                  新建文档
GET    /api/documents/{id}             取文档 + 图层树
PATCH  /api/documents/{id}/layers      图层增删改（前端直接编辑时用）
POST   /api/plan                       自然语言 → EditPlan（不执行）
POST   /api/jobs                       提交 EditPlan
GET    /api/jobs/{id}/events           SSE 进度
POST   /api/jobs/{id}/steps/{sid}/retry 单步重试
POST   /api/assets                     上传素材
GET    /api/engines                    引擎列表 + 能力 + 当前价格
GET    /api/health                     本地服务存活（前端启动探测）
```

**本地服务安全**（容易漏、但必须做）：

- 只监听 `127.0.0.1`，不监听 `0.0.0.0`。
- 启动时生成一次性 token 写进本地配置，前端每次请求带 `X-Local-Token`——防止任意网页通过 `localhost:PORT` 打你的服务（CSRF / 端口扫描）。
- 云端 API Key 只存本地加密配置，绝不下发前端。

## 7. 目录结构

```
smart-ps/
├─ apps/web/                    # React + Vite + Konva 前端
│  ├─ src/canvas/               # 画布、图层、蒙版笔刷
│  ├─ src/panels/               # 计划卡片、历史、引擎/成本面板
│  └─ src/api/                  # 由 contracts 生成的客户端
├─ services/core/               # FastAPI 本地服务
│  ├─ app/api/                  # 路由（documents / plan / jobs / engines）
│  ├─ app/core/                 # document、layer、dag、executor、history
│  ├─ app/ops/                  # 本地算子：matting / segment / inpaint / upscale / face / compose
│  ├─ app/engines/              # base + seedream / qwen / openai / local_stub + router
│  ├─ app/planner/              # VLM → EditPlan，含 prompt 与 schema 校验
│  ├─ app/jobs/                 # 队列、SSE、计费、缓存
│  └─ app/store/                # SQLite(文档/任务/账单) + 素材库
├─ packages/contracts/          # EditPlan / Capabilities / API JSON Schema
├─ models/                      # ONNX 权重（.gitignore，不提交）
├─ data/                        # 工程文件、素材、缓存
└─ docs/                        # 本设计与技术选型
```

## 8. 本地算子的性能预期（M1 实测校准）

i5-12600K 纯 CPU，**以下是待实测的估算**，不是承诺值。具体模型选型、许可与加速路径见 [本地模型选型](../../02-本地模型选型.md)。

| 算子 | 默认模型 | 输入 | 估算耗时 |
|---|---|---|---|
| matting（BiRefNet_lite） | MIT | 1024² | 1.5–4 s |
| segment（SAM 2.1 tiny，编码器缓存后 decoder） | Apache-2.0 | 1024² | 2–5 s / 首次；点击 ~50 ms |
| inpaint（LaMa） | Apache-2.0 | 1024² | 2–5 s |
| upscale（Real-ESRGAN，可走 ncnn-Vulkan） | BSD-3 | 1024²→4× | 15–40 s（CPU）/ 更快（核显） |
| face_restore（GFPGAN v1.4） | Apache-2.0 | 512² 人脸 | 2–6 s |
| composite / feather | 自研 | 4K | < 0.3 s |

**设计含义**：交互路径上只用抠图、选集、合成（秒级，可实时预览）；放大、修脸这类重算子走后台 Job 并显示进度。默认档总权重约 940 MB，与安装包分离、首次使用时下载。

## 9. 里程碑

| 阶段 | 交付 | 完成标准 |
|---|---|---|
| **M0 骨架** | 文档/图层模型、Konva 画布、撤销栈、SQLite 持久化 | 能建文档、加图层、画蒙版、存盘重开不丢 |
| **M1 本地能力** | 抠图、擦除、放大、合成 + 算子注册表 | 全程不联网完整走完「抠图 → 擦背景 → 导出透明 PNG」 |
| **M2 首个云端引擎** | `seedream-flash` 适配器 + 局部化提交 + `align_patch` 几何校验 + 融合 | 指令换背景，蒙版外像素逐位不变（有自动化比对）；补丁尺寸/错位能被检出并进入重试路径 |
| **M3 智能层** | Planner（VLM）+ EditPlan 校验 + 计划预览卡片 | 一句中文话，产出合法计划，改错话不改参数也能纠正 |
| **M4 路由与成本** | 三引擎路由、降级链、缓存、计费面板 | 同一指令可手动切档，成本透明可见 |
| **M5 外壳扩展** | 可选 PS（UXP）面板 / 批量处理 / 项目模板 | 同一内核不改代码，换壳可用 |

每个阶段都以「可运行 + 可验收」结束，不做只有代码没有入口的阶段。

## 10. 非目标（明确不做）

- 不做 PS 的全功能替代：不做完整的混合模式集合、CMYK/印刷色彩管理、3D、视频。
- 不做移动端；不做多人实时协作。
- 不做模型训练/微调（一期）；不自建 GPU 推理（等 C 档再说）。
- 不追求"一键全自动"：始终保留**计划预览 + 手动蒙版**，专业用户要的是可控。

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| CPU 推理慢 | 放大/修脸不可交互 | 重算子后台化 + 进度；小图优先；保留接 GPU 云的开关 |
| 云端"顺手改" | 结果不可用 | 局部化提交 + 蒙版融合 + 蒙版外像素不变的可断言测试 |
| 内容审核拒绝 | 步骤失败 | 降级链 + 明确报错与建议改写；不静默重试烧钱 |
| 模型/价格频繁变动 | 适配层返工 | 一切差异收敛进 `Capabilities` + 每个引擎一份契约测试 |
| 成本失控 | 用户不敢点 | estimate 前置、缓存、单次阈值确认、账单面板 |
| 本地服务被滥用 | 安全 | 127.0.0.1 + 一次性 token + Key 不下发 |

## 12. 留给你定的（会改变设计）

1. **形态**：认可"本地 Web 应用为主、PS 面板为后续外壳"吗？
2. **首期功能**：M1–M2 先做哪三条？（换背景 / 擦物体 / 老照片修复 / 证件照 / 批量尺寸…）
3. **云端账号**：火山方舟和百炼的 API Key 能拿到吗？（决定 M2 走 Flash 还是先用 `local-stub` 打通链路）
4. **给谁用**：只自己用，还是打包给别人用？（后者要加配置向导、Key 托管、错误上报）

---

## 13. 评审后收敛的决策（我的判断，可推翻）

设计评审提出了七个文档没答清的问题。六个我直接定了，都标为「我的判断」，理由写在下面。

### 13.1 云端产物**冻结**，本地算子**可重跑**

`cloud_edit` 既不幂等又按张计费，所以"纯引用式非破坏"在云端节点上不成立。

- 本地算子：只存引用，随时重算（确定性、免费）。
- 云端节点：**首次成功即落盘冻结**成资产，`Layer.source` 指向该资产；重跑必须显式点，并先走缓存（同键命中则 0 元）。
- **撤销、打开旧工程、重新求值都不触发网络调用**——这是硬规则，写进验收。

### 13.2 分辨率体系

- 工作分辨率：长边 ≤ 2048（可调），超出先整体降采样，交互路径只在这个尺度上跑。
- 提交云端：按蒙版包围盒 + 30px 上下文裁切，再缩放到引擎档位内的最大边。**Flash 三档同价，所以永远提交它能接受的最大分辨率**——同样的钱买更清楚的补丁。
- 贴回：补丁 ↔ 底图坐标必须 1:1 映射；需要放大回填时用 Lanczos + 羽化过渡，并在 UI 标注"该区域为放大素材"。
- 验收基准图：固定 1024×1024 合成图 + 一张 4000×3000 实拍图，两档覆盖。

### 13.3 补丁几何保真：假定**不对齐**，加 `align_patch`

云端编辑模型会轻微重构构图。所以 M2 必带一步本地校验：

1. 补丁尺寸与提交尺寸不符 → 直接重采样到提交尺寸；
2. 沿蒙版外圈取一圈"只应保持不变"的像素带，与底图比对（SSIM + 平均色差）；
3. 超阈值 → 该步标 `needs_review`，给出「重试 / 缩小蒙版 / 换引擎」三个选项，而不是硬贴。

这条决定了 M2 的验收标准，也决定了 §11"蒙版外像素不变"到底靠什么断言。

### 13.4 文字与调整层的边界

- M0 只做 **位图层 + 蒙版层**；调整层做非破坏参数层，但先只支持亮度/对比/饱和/色温四个参数。
- **可编辑文本层推迟到 M4 之后**：它意味着字体、CJK 断行、导出栅格化一整套工作，不是 M0 的量。
- 澄清一处容易混的地方：路由表里的"中文文字走 `qwen-image-3`"指的是**整块重绘海报类内容**，与"可编辑文本层"是两件事，不冲突。

### 13.5 降级要用户点头

见 §4.4：降级不自动执行，推 `needs_confirm`。宁可多一次点击，不要一次意外的 0.3 元。

### 13.6 本地服务的安全与并发

- ONNX 推理放独立 worker 进程（避免阻塞 SSE），按算力串行排队，不做并发抢占。
- 前端与服务同源代理，token 注入不落 localStorage。
- 权重首次使用时按需下载，带校验和与断点续传，失败不阻塞其余算子。

### 13.7 唯一还悬着的那条

**图片能不能出本机**。这不是技术偏好，是产品约束：如果「证件照、客户素材」这类场景必须纯本地，那 M1 的本地算子就得按产品级验收（擦除/放大/修脸都要能交付），里程碑顺序也要改，`local-stub` 会从开发替身变成正式降级路径。**这条我不替你定。**
