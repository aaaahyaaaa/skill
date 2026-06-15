# FindReason Judgement Summary

## 当前结论

`candidate_cause = answer_failure`，置信度：中等偏高。

这条更像答案层漏答，而不是 preprocess / knowledge / recall / rerank 的断点。历史 trace 里，用户问题、rewrite、keywords 都保留了“巨量千川 / 全域投放成本保障 / 多个佣金率 / 商品佣金率计算”；核心规则文档也从 recall 存活到 rerank 和 prompt。问题出在最终答案只回答了“双佣金取投广佣金率、单佣金取固定佣金率”，但漏掉了 prompt 里同一规则下更完整的限制：如果投放多个商品，或周期内调整商品佣金率，需要按“任意一个商品的任意一个所统计的佣金率”来判断，否则不满足成本保障条件。

因为目前没有 evaluator / expected answer，这个结论不能打到最高置信；如果人工 expected 只要求解释“双佣金 vs 单佣金”，历史答案可接受。如果 expected 要求完整解释“多个佣金率如何计算/判断”，则这就是明显的 `missing_aspect`。

## Case 摘要

- log_id: `021776144364758fdbddc0300ff0200fdd3c8ce0d66ad232827ea`
- workspace_id: `138`
- app_id: `1001883`
- replay_log_id: 未运行 replay，未返回新的 log_id
- 用户问题: 巨量千川全域投放成本保障中，当商品有多个佣金率时，商品佣金率如何计算
- workflow 输入: `{"sys":{"query":"巨量千川全域投放成本保障中，当商品有多个佣金率时，商品佣金率如何计算"},"user":{"task_id":"8642531e-d3ba-4a58-849d-239b85fbd3a6"}}`
- workflow 输出: 双佣金取“投广佣金率”，单佣金取“固定佣金率”，引用了成本保障规则和达人带货佣金优化手册。
- 评估器信号: 未提供。

## 答案症状

`secondary_findings.answer_issue_types = ["missing_aspect"]`

历史答案没有明显 unsupported claim，也没有明显把结论引到完全错误文档上；核心引用 `[1] 巨量千川「全域投放」成本保障规则` 可以支撑答案已有内容。但答案不完整：它没有告诉用户当“多个商品”或“投放周期内调整佣金率”导致存在多个可统计佣金率时，成本保障判断要按“任意一个商品的任意一个所统计佣金率”兜底，且不满足则无法获得成本保障。

## 上游证据链

- preprocess: 通过。rewrite 与原问题一致；keywords 保留了 `巨量千川`、`全域投放成本保障`、`多个佣金率`、`商品佣金率`、`计算`。
- recall: 通过。历史 trace 原始召回 `origin_doc_list=87`、`origin_faq_list=3`；归一化/实验观察 recall 为 `73` 条。核心规则文档 `1412463` 在 recall 中命中，当前 recall 实验同样命中。
- rerank: 通过。`1412463` 从 recall 存活到 rerank 和 prompt；rerank 观察里 `missing_from_rerank=[]`、`missing_from_prompt=[]`。
- answer: 不通过。prompt 已有完整规则，但最终答案只覆盖了其中一部分。

## 证据充分性

required assertions:

- A1: 双佣金模式下，统计“投广佣金率”。support_level: `direct_support`。
- A2: 单佣金模式下，统计“固定佣金率”。support_level: `direct_support`。
- A3: 如投放多个商品，或在成本保障周期内调整商品佣金率，需要保证全天 ROI 目标小于 `1 / 任意一个商品的任意一个所统计的佣金率`，否则视为不满足保障条件、无法获得成本保障。support_level: `direct_support`。

关键证据：

- `1412463` 巨量千川「全域投放」成本保障规则
  - 文档链接: https://support.oceanengine.com/support/content/139336
  - 生存链路: recall 命中 -> rerank 命中 -> prompt 命中。
  - 支撑片段: Q7 说明单佣金统计“固定佣金率”、双佣金统计“投广佣金率”；同时说明投放多个商品或周期内调整佣金率时，要看“任意一个商品的任意一个所统计的佣金率”。
- `354146` 巨量千川『全域投放』成本保障规则
  - 支撑作用: FAQ 入口型证据，指向同一成本保障规则，主题相关但自身不是完整规则片段。
- `366570` 全域商品投放成本保障金额怎么计算？
  - 支撑作用: 相邻证据，能说明成本保障计算背景，但不能替代 `1412463` 的佣金率规则。
- `2954445` 巨量千川「商品乘方-达人带货佣金优化」产品手册
  - 支撑作用: 佣金成本/达人佣金相关，相邻支撑；不是回答“全域投放成本保障中商品佣金率如何计算”的主证据。

这组证据足以产出严谨业务答案，不是“只解释 replay 变好”的那种半支撑。历史答案没有把 prompt 中的 A3 写出来，所以主因更适合落到 `answer_failure`。

## 候选根因对照

- `workflow_input_loss`: 不成立。输入、rewrite、keywords 都保留核心约束。
- `suspected_knowledge_missing`: 不成立。权威规则文档存在，且 prompt 已包含直接支撑片段。
- `retrieval_miss`: 不成立。核心文档历史 recall 和当前 recall 实验均命中。
- `rerank_drop`: 不成立。核心文档没有被 rerank 丢掉，也进入了 prompt。
- `answer_failure`: 成立。answer issue 是 `missing_aspect`，即证据足够但输出遗漏必要规则分支。

## 下一步

如果要把置信度从“中等偏高”提升到“高”，需要补 evaluator 失败理由或 expected answer，确认评估目标确实要求覆盖 A3。如果 expected 只考察双佣金/单佣金的取值规则，这条不应算 badcase；如果 expected 要求完整解释“多个佣金率”场景，当前归因就是 `answer_failure`。

本地证据包：

- `case_facts.json`: 历史 trace 事实和归一化 artifacts。
- `recall_experiment.json`: 当前 recall 对照实验。
- `rerank_experiment.json`: `1412463` 生存观察。
- `evidence_index.json`: 可索引证据包。
