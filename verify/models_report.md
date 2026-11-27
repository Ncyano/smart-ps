# 模型对照评测

- 生成时间：2026-11-27 11:32:45
- 模型：doubao-seedream-5-0-flash-260915, doubao-seedream-5-0-pro
- 用例：4 类物料　每类 1 张
- 状态：已执行

## doubao-seedream-5-0-flash-260915

| 指标 | 值 |
|---|---|
| 一次可用率 | 25%（1/4） |
| 总成本 | 0.48 元 |
| 单张可用成本 | 0.48 元 |
| 平均耗时 | 14.5s |
| 生成失败 | 0 |

| 用例 | 结构 | 铺展 | 清晰 | 标签区 | 档 | 一次可用 | 成本 | 耗时 |
|---|---|---|---|---|---|---|---|---|
| white_bg | 0.544 | 0.014 | 0.973 | pass | B | ✗ | 0.12 | 15.6s |
| kv | 0.624 | 0.157 | 1.339 | pass | B | ✗ | 0.12 | 14.1s |
| scene | 0.784 | 0.071 | 1.636 | pass | A | ✓ | 0.12 | 14.0s |
| person | 0.976 | None | None | unknown | B | ✗ | 0.12 | 14.3s |

## doubao-seedream-5-0-pro

| 指标 | 值 |
|---|---|
| 一次可用率 | 0%（0/0） |
| 总成本 | 0 元 |
| 单张可用成本 | — 元 |
| 平均耗时 | 0.0s |
| 生成失败 | 4 |

| 用例 | 结构 | 铺展 | 清晰 | 标签区 | 档 | 一次可用 | 成本 | 耗时 |
|---|---|---|---|---|---|---|---|---|
| white_bg | — | — | — | — | — | ✗ HTTP 404：{"error":{"code":"InvalidEndpointOrModel.NotFound", | — | — |
| kv | — | — | — | — | — | ✗ HTTP 404：{"error":{"code":"InvalidEndpointOrModel.NotFound", | — | — |
| scene | — | — | — | — | — | ✗ HTTP 404：{"error":{"code":"InvalidEndpointOrModel.NotFound", | — | — |
| person | — | — | — | — | — | ✗ HTTP 404：{"error":{"code":"InvalidEndpointOrModel.NotFound", | — | — |

## 怎么读这份表

- **一次可用率** 低、但单张成本也低，要看单张可用成本：返工两次就不便宜了。
- **标签区 fail/warn** 说明包装文字被重画，这类图不能投；不是「风格差一点」的问题。
- 结论按物料类型分别下，不要合并成「哪个模型更好」——常见情况是 A 擅长背景、B 擅长保主体。
