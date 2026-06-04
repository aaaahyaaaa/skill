# FindReason v3 报告模板

`orchestrate --output-dir` 会写入 `final/case_report.md`，内容与 `human_report_markdown` 一致。报告展示可读摘要，并保留原始 workflow input/output 的有界摘录；完整 trace、workflow span I/O、probe 原始输出保留在 `attribution_record.json`。

## 结论

- `primary_cause.stage` / `primary_cause.cause_code` / confidence / owner
- `case_assessment.status` 和一句话原因
- `needs_human_review` 与人工复核原因
- 主因选择依据，避免堆叠所有 evidence JSON

## Case 摘要

- `log_id`、`workspace_id`、`app_id`
- 真实 query 的短摘要
- trace 是否可用
- origin / faq / rerank / prompt 文档数量

### 原始 Workflow 输入输出

- selected workflow span id / node id
- 原始 workflow input 摘录
- 原始 workflow output 摘录
- 如内容被截断，报告应指向 `final/attribution_record.json.raw_artifacts.workflow_span_ios`

### 评估器线索

- 压缩后的 `judgement_evidence.signals`

## 断言覆盖

- `expected_required`：正确输出应覆盖的检查点
- answer / check 观察：`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`
- answer / check 观察只用于 answer grounding / scope / citation / consistency 检查，不进入上游覆盖矩阵
- 断言覆盖矩阵：只展示 `expected_required` 在线上 `origin -> rerank -> prompt` 的支撑状态
- 断言级断点：knowledge / retrieval / rerank / context / unavailable

## 召回上界

- oracle 来源、置信度、冲突状态
- 理论召回上界状态、范围、topK、query variants、召回数量
- 理论召回上界与必要断言的关系：doc ID、title、matched terms、support status、support spans

## Probe Plan 结果

- `run-probe-plan` 每个 probe 的 direction、target artifact、hit、converged direction
- plan 本身不是证据；只展示执行后的 hit/miss 和 matched support

## 阶段裁决

| 阶段 | 结论 | 关键依据 |
|-|-|-|
| preprocess | pass/fail/indeterminate | rewrite、route、keyword evidence |
| knowledge | pass/fail/indeterminate | knowledge exists 或必要断言上界支撑 |
| retrieval | pass/fail/indeterminate | online origin recall 是否覆盖必要断言 |
| rerank | pass/fail/indeterminate | 必要断言是否从 origin 到 rerank 丢失 |
| context | pass/fail/indeterminate | 必要断言是否从 rerank 到 prompt 丢失 |
| answer | pass/fail/indeterminate | prompt support、unsupported claim、citation、scope、consistency |
| evaluation | observation-only | evaluator signals |

## 下一步

- owner
- P0/P1 action
- 如果主因为 null，说明还缺哪类 evidence

## 边界

- 不把 evaluator reason 当最终事实裁判。
- 不把 `answer_claim` 反推为 `expected_required`。
- 不因 doc ID 没进 rerank / prompt 直接判 `rerank_drop` / `context_assembly_error`；必须证明必要断言支撑在对应阶段丢失。
