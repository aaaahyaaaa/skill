# FindReason v3 报告模板

`orchestrate --output-dir` 会写入 `final/case_report.md`，内容与 `human_report_markdown` 一致。报告正文优先展示结论、答案观察、必要断言和阶段归因；原始 workflow input/output 只放在附录中做有界摘录。完整 trace、workflow span I/O、probe 原始输出保留在 `attribution_record.json`。

## 1. 结论摘要

- `primary_cause.stage` / `primary_cause.cause_code` / confidence / owner
- `case_assessment.status` 和一句话原因
- `needs_human_review` 与人工复核原因
- 主因选择依据，避免堆叠所有 evidence JSON

## 2. 问题与答案观察

- `log_id`、`workspace_id`、`app_id`
- 真实 query 的短摘要
- 原始答案和答案状态：已给出答案 / 未给出答案
- answer-side 检查信号：`prompt_supports_answer`、`answer_satisfies_expected`、`partial_answer`、`wrong_citation`、`scope_violation`、`branching_unclear`
- trace 是否可用
- origin / faq / rerank / prompt 文档数量

### 评估器线索

- 压缩后的 `judgement_evidence.signals`

### 输入边界

- 用户实际问题/评估问题线索
- Workflow 原始输入
- 预处理输出（rewrite query / keywords）
- 如果用户约束未进入 Workflow 原始输入，报告先写输入边界风险；只有受影响断言在理论召回上界可支撑、但线上初召回缺失时，才写 `workflow_input_loss`
- 如果同一断言已经进入 online origin / rerank / prompt，报告写“输入差异未证明影响输出”，继续展示下游主因
- 如果 Workflow 输入完整但预处理输出丢失，报告写 `query_rewrite_drift` 或 `keyword_loss`

## 3. 必要断言

- `expected_required`：正确输出应覆盖的检查点
- answer / check 观察：`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`
- answer / check 观察只用于 answer grounding / scope / citation / consistency 检查，不进入上游覆盖矩阵

## 4. 断言覆盖矩阵

- 只展示去重后的 `expected_required` 在线上 `origin -> rerank -> prompt` 的支撑状态
- 如果行内有 `merged_from`，表示多个原始断言已按“场景约束 + 同一入口/路径要求”保守合并，原始断言只作审计不重复计入矩阵
- 断言级断点：knowledge / retrieval / rerank / context / unavailable

## 5. 召回上界与知识判断

- oracle 来源、置信度、冲突状态
- 理论召回上界状态、范围、topK、query variants、召回数量
- 理论召回上界与必要断言的关系：doc ID、title、matched terms、support status、support spans
- 如果理论上界也不能支撑必要断言，更偏向 knowledge missing；如果理论上界能支撑但线上 origin 未命中，更偏向 retrieval miss

## 6. 阶段归因链路

| 阶段 | 结论 | 关键依据 |
|-|-|-|
| preprocess | pass/fail/indeterminate | rewrite、route、keyword evidence |
| knowledge | pass/fail/indeterminate | knowledge exists 或必要断言上界支撑 |
| retrieval | pass/fail/indeterminate | online origin recall 是否覆盖必要断言 |
| rerank | pass/fail/indeterminate | 必要断言是否从 origin 到 rerank 丢失 |
| context | pass/fail/indeterminate | 必要断言是否从 rerank 到 prompt 丢失 |
| answer | pass/fail/indeterminate | prompt support、unsupported claim、citation、scope、consistency |
| evaluation | observation-only | evaluator signals |

## 7. Probe 结果

- `run-probe-plan` 每个 probe 的 direction、target artifact、hit、converged direction
- plan 本身不是证据；只展示执行后的 hit/miss 和 matched support

## 8. 下一步

- owner
- P0/P1 action
- 如果主因为 null，说明还缺哪类 evidence

## 附录：原始 Trace 摘录

- selected workflow span id / node id
- workflow input 摘录
- workflow output 摘录
- 如内容被截断，报告应指向 `final/attribution_record.json.raw_artifacts.workflow_span_ios`

## 边界

- 不把 evaluator reason 当最终事实裁判。
- 不把 `answer_claim` 反推为 `expected_required`。
- 不因 doc ID 没进 rerank / prompt 直接判 `rerank_drop` / `context_assembly_error`；必须证明必要断言支撑在对应阶段丢失。
