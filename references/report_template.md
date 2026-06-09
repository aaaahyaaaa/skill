# FindReason v3 人读诊断报告模板

`orchestrate --output-dir` 默认只写一份人读 Markdown：`final/case_report.md`，内容与 `human_report_markdown` 一致。不要再默认生成 `agent_run_process.md` 或 `diagnostic_timeline.md`；JSON 继续承担审计和机器消费。

报告目标是让用户看懂“Agent 为什么这么查、查了哪里、怎么验证、最终为什么判这个阶段”。避免把 `exp_kind`、`target_artifact`、`hit`、`converged_direction`、probe id 等 runtime tag 直接塞进正文。

## 1. 结论摘要

- 主因阶段、主因枚举、confidence、owner。
- `case_assessment.status` 与一句话原因。
- `needs_human_review` 与人工复核原因。
- 主因选择依据，避免堆叠完整 evidence JSON。

## 2. 现场输入与答案

- `log_id`、`workspace_id`、`app_id`。
- 用户问题、答案摘要、答案状态。
- 答案检查信号用中文标签展示，例如“Prompt 是否支撑答案”“答案是否满足期望”“是否漏答”“是否错引”。
- trace 是否可用，以及 origin / faq / rerank / prompt 文档数量。
- Workflow 原始完整输入和 Workflow 原始完整输出，直接放在本节，避免读者跑到报告末尾找现场。
- 输入边界：用户实际问题、Workflow 原始输入、预处理输出，以及关键约束是否丢失。

## 3. 验证过程：我怎么查的

每个执行后的验证项渲染为中文卡片，而不是英文表格：

- `我为什么查这个`：来自 `trigger_observation`，缺失时用 `hypothesis`。
- `我查了哪里`：把 artifact 显示为“理论召回上界 / 线上初召回 / 重排结果 / Prompt 上下文 / 最终答案”。
- `我怎么验证`：说明用哪个 query / pattern 在目标 artifact 中查找。
- `验证结果`：显示“命中 / 未命中 / 未执行 / 无法判断”。
- `支撑证据`：若有 `matched_docs`，每个文档单独展示标题、链接和命中片段，链接必须是独立 Markdown 链接，不能和正文片段挤在同一串 URL 中。
- `这说明什么`：优先使用中文 `if_hit/if_miss`；英文结论必须转成中文解释。

`probe_id` 只留在 JSON。展示名优先用 `display_name`；没有时从 `probe_id` 派生短名，并清理 `P-`、`P_` 和时间戳式前缀。

## 4. 阶段裁决：问题最早断在哪

阶段、状态和原因必须中文化：

| 内部阶段 | 报告展示 |
|-|-|
| `preprocess` | 输入/改写 |
| `knowledge` | 知识库 |
| `retrieval` | 初召回 |
| `rerank` | 重排 |
| `context` | Prompt 拼接 |
| `answer` | 答案生成 |
| `evaluation` | 评估器 |

状态展示为“通过 / 失败 / 证据不足 / 上游阻塞 / 未验证”。阶段裁决里不要直接输出英文 counterfactual reason；常见英文 reason 要映射为中文。

## 5. 关键证据与文档

- Answer findings：展示错引、漏答、越界、分支前提不清等伴随发现。
- 评估器线索：只作为观察，不决定主因。
- 必要断言：`expected_required` 与 answer/check 观察分开展示。
- 断言覆盖矩阵：聚焦线上 `origin -> rerank -> prompt`。
- 理论召回上界与断言关系：doc ID、title、matched terms、support status、support spans。
- 召回 chunk 冲突风险：列出阶段、doc/chunk、冲突片段；只有影响必要断言且无清晰适用前提时才升级为知识内部不一致。

文档证据格式：

```markdown
- 2033060 巨量千川「破圈尖货」产品手册
  - 文档链接：[打开文档](https://ad-sirius.bytedance.net/api/sirius_knowledge/v1/data/doc/record_id?source=COGNITION&identifier=2033060)
  - 命中片段：...
```

优先使用 doc 自带 `url/link/doc_url/source_url`；没有链接但 doc id 是数字时，用知识详情接口 URL 作为可核对链接。

## 6. 下一步建议

- owner。
- P0/P1 action。
- 如果主因为 `null`，说明还缺哪类 evidence。

## 7. 审计 JSON 索引

- `final/case_report.md`：唯一人读报告。
- `final/short_summary.json`：结构化摘要。
- `final/attribution_record.json`：完整审计。
- `final/attribution_record.json.raw_artifacts.probe_outputs`：验证原始输出。
- `final/attribution_record.json.raw_artifacts.workflow_span_ios`：Workflow input/output 完整值。

本节只放审计入口，不再重复展示 Workflow 输入/输出；完整现场已经在第 2 节展示。

## Rerank 边界

`probe-rerank-bypass` 只比较关键 doc ID 是否从初召回进入重排/Prompt，是“重排生存观察”，不是 curl 重跑 rerank，也不是线上 rerank 参数实验。报告中写“重排观察：关键文档是否在重排后消失”，并明确这只是 doc ID 生存观察。不能单凭该观察选择 `rerank_drop`。
