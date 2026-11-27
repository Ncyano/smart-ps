# 模板验收集报告

- 生成时间：2026-11-27 11:43:27
- 通过：15/15
- 语义判定使用**桩**（总是判同一款）：本轮验证版式与闸门链路，不含模型判断质量。

| 用例 | 模板 | 画布 | 版式 | 确认前 | 确认后 | 成本 | 耗时 | 结论 |
|---|---|---|---|---|---|---|---|---|
| scene-ok | scene | 900×1200 | — | manual | deliverable | 0.0 | 1.7s | ✓ |
| white_bg-ok | white_bg | 1080×1080 | OK | manual | deliverable | 0.0 | 1.1s | ✓ |
| kv-ok | kv | 1080×1350 | OK | manual | manual | 0.0 | 1.1s | ✓ |
| kv-fail-nocopy | kv | 1080×1350 | FAIL | rejected | rejected | 0.0 | 1.0s | ✓ |
| price-ok | price | 1080×1350 | OK | manual | manual | 0.0 | 1.0s | ✓ |
| price-fail-noprice | price | 1080×1350 | FAIL | rejected | rejected | 0.0 | 1.1s | ✓ |
| price-manual-unsourced | price | 1080×1350 | OK | manual | manual | 0.0 | 1.1s | ✓ |
| patent-ok | patent | 1080×1350 | OK | manual | deliverable | 0.0 | 0.2s | ✓ |
| patent-fail-noevidence | patent | 1080×1350 | FAIL | rejected | rejected | 0.0 | 0.1s | ✓ |
| table-ok | table | 1080×1350 | OK | manual | manual | 0.0 | 0.2s | ✓ |
| table-fail-ragged | table | 1080×1350 | FAIL | rejected | rejected | 0.0 | 0.2s | ✓ |
| pair-ok | pair | 1080×1350 | OK | manual | deliverable | 0.0 | 0.3s | ✓ |
| pair-fail-nobefore | pair | 1080×1350 | FAIL | rejected | rejected | 0.0 | 0.1s | ✓ |
| screen-ok | screen | 1400×900 | OK | manual | deliverable | 0.0 | 0.3s | ✓ |
| screen-fail-badcorners | screen | 1400×900 | FAIL | rejected | rejected | 0.0 | 0.2s | ✓ |

## 分模板统计

- `scene`：1/1 通过，平均 1.7s
- `white_bg`：1/1 通过，平均 1.1s
- `kv`：2/2 通过，平均 1.1s
- `price`：3/3 通过，平均 1.1s
- `patent`：2/2 通过，平均 0.2s
- `table`：2/2 通过，平均 0.2s
- `pair`：2/2 通过，平均 0.2s
- `screen`：2/2 通过，平均 0.2s

## 说明

反例（`*-fail-*`）必须失败：缺字段、列数不一致、缺扫描件、四角贴满整图，
这些情况出图就是废稿，宁可报错。
