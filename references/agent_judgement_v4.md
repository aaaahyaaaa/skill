# Agent Judgement v4

v4 的核心目标是：通过自然语言推理和实验验证，从表象找到更接近本质的根因。代码只提供事实和实验，不替 Agent 做最终裁决。

## 工作流

1. 读取 `case_facts.json` 和 `agent_brief.md`。
2. 抽取 answer symptoms。
3. 根据表象列出候选根因。
4. 对每个候选根因写支持证据和反证证据。
5. 规划或执行 recall / rerank / replay / knowledge-detail 实验。
6. 运行 `synthesize-brief` 生成每条 case 的短版 `agent_judgement.md` 和 `evidence_index.json`。
7. 给用户写短版 judgement 回复，不要把 JSON 或长证据清单当作最终输出。

## 证据链读法

报告中可以使用 `recall` 这个人读名称，但 Agent 审计时要知道它由两路 trace 字段汇总：

- `origin_doc_list`：文档召回。
- `origin_faq_list`：FAQ / 精选召回。

同一个必要断言应放在一起追踪：它是否出现在 `recall`，是否保留到 `rerank_docs`，是否进入 `prompt_docs` / `qaPromptDocs`。不同必要断言分开写，避免把多个问题混成一个“阶段失败”。

rerank 复验必须看 `rank_shift_observations`：核心 doc、支撑哪条 assertion、recall/rerank/prompt rank 和 score、rank/score delta、是否进入 prompt、缺失原因和 context_boundary。没有真实 prompt 边界时写 `not_observed`，不要从节点名硬推具体脚本截断。

`prompt_docs` 是观察面，不是当前 v4 顶层 cause。即使暂不把 prompt/context 作为独立主因，也不能假设全部 `rerank_docs` 都给了模型；`maxInputToken`、`maxInputSize`、`topP` 等配置会影响最终进入 prompt 的证据。

## 证据充分性

归因报告还要判断证据组合是否足以产出严谨业务答案。不要只写“找到了相关证据”；要写清楚它支撑了什么、没支撑什么。

支撑等级：

- `direct_support`：证据直接支撑 required assertion，可以用于下结论。
- `partial_support`：证据支撑一部分，但单独不能完成回答。
- `adjacent_support`：主题相关，只能解释方向或召回改善，不能支撑结论。
- `insufficient`：看似相关，但不能作为有效支撑。
- `contradictory`：证据之间存在冲突，需要消歧。

报告应区分两句话：

- “这些证据足以解释 replay 为什么比历史答案好。”
- “这些证据足以产出严谨完整的业务答案。”

如果第一句成立但第二句不成立，要明确写出仍缺的权威证据。

Prompt sufficiency gate 至少分四档：`相关词命中`、`部分支撑`、`直接核心证据`、`冲突证据`。prompt 中有相关词或泛化文档，只能说明可能相关；只有关键 required assertions 已有 `direct_support`，且 Workflow 输出仍错，才允许把主因落到 `答案生成错误`。

关键文档状态也属于证据充分性的一部分。若 `knowledge_detail_experiment.json` 标出 `停止更新`、`历史版本`、`过期`、`已升级` 等信号，应把它作为知识冲突/过期风险写入证据链；若 `status_confirmed=false` 或 `status_reason=status_unconfirmed`，只能说状态未确认，不能当成已确认根因。

## 历史现场与实验

历史 Fornax trace 是 badcase 现场，优先用于说明“当时系统实际看见了什么”。`run-experiment --type replay` 是当前版本反事实实验，适合回答“如果现在用同一问题重跑，证据链会不会变化”。二者冲突时，不要用 replay 覆盖历史现场；应把差异写成实验发现。

例如历史 trace 说资料不足，但 replay 已召到相关 FAQ 并生成较好答案，这说明当前版本或运行条件下证据链可能已改善；它不能证明历史答案当时也有这些证据。

## Answer Symptom Extraction

先描述答案错在哪里，不要先贴标签。

常见症状：

- `missing_aspect`：问题要求多个方面，答案只覆盖一部分。
- `unsupported_claim`：答案写了证据不支持的事实或强结论。
- `wrong_citation`：引用文档存在但不支持对应 claim。
- `scope_violation`：把窄问题答成宽范围，或忽略限定条件。
- `branching_unclear`：不同分支前提混用，答案没有区分。
- `not_found_answer`：答案说查不到、没有、无法判断。
- `generic_answer`：答案给通用入口或泛解释，没有贴合场景。

症状只是起点，不是根因。

## 候选解释

每个候选解释至少回答：

- 它解释了哪个表象？
- 它需要哪些 trace 事实成立？
- 目前有哪些证据支持？
- 哪些证据会推翻它？
- 下一步该跑哪个实验？

候选解释可以跨阶段。例如“答案越界”可能来自 workflow 输入丢场景，也可能来自 rerank 丢掉窄场景文档，也可能是模型忽略 prompt 约束。不要一看到答案越界就直接归到 answer。

## Cause 口径

最终报告以中文 cause 为主，旧 slug 只作为兼容别名：

- `输入侧问题`（旧 slug: `workflow_input_loss`）
- `知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）
- `召回遗漏`（旧 slug: `retrieval_miss`）
- `重排丢失`（旧 slug: `rerank_drop`）
- `答案生成错误`（旧 slug: `answer_failure`）
- `无明显错误/评估器不准，需人工进一步核实`（slug: `evaluator_disputed_no_obvious_error`）

`输入侧问题` 不能只凭“workflow input/rewrite 看起来少了信息”就上调为主因。必须根据验证点改写后的 query 做实验，并观察到召回改善、排序改善，或 replay / 最终结果改善。否则只写低置信候选或待验证点。

第 6 类 `无明显错误/评估器不准，需人工进一步核实` 不能作为“看不出来”的兜底。只有前 5 类都没有明显证据，且评估器结论与 prompt evidence、Workflow 输出、被评估答案或人工标注存在可说明的冲突时，才能作为 cause。报告必须显式写出人工复核点；不要求固定范式，但要讲清楚为什么怀疑评估器不准以及要复核的地方。`评估器输出暂无` 本身不是第 6 类证据。

## 最终输出结构

最终给用户的回复应该像一次清晰复盘，而不是固定模板长报告。建议输出：

```markdown
## 当前归因结论
candidate_cause、置信度、一句话理由。

## 答案症状
答案具体错在哪里，对应 answer_issue_types。

## 上游证据链
preprocess / recall / rerank / replay 分别支持或反驳什么。

## 关键证据
只列真正影响判断的文档标题、链接或片段。

## 证据充分性判断
required assertions、support level、仍缺的权威证据。

## 仍缺证据
没有 evaluator、expected assertions 或需要业务确认时明确写出。
```

`agent_judgement.md` 也是这个短版结构，并且是每条 case 的唯一人读结论文件；不要再生成一份独立 `summary.md` / `summary` 与它重复。详细证据保留在 `case_facts.json`、`*_experiment.json` 和 `evidence_index.json`，由 Agent 使用，不要求用户阅读。

`agent_judgement.md` 不写面向 Agent 的报告要求或 checklist；这些规则留在 `SKILL.md`、`report_contract.md` 和 `agent_brief.md`。

证据展示必须可读：每个关键证据要有标题，并给出实际链接或援引片段。不要只输出 `prompt_doc_ids`、`rerank_doc_ids` 之类裸数组。

默认使用本地 Markdown + JSON：短版 `agent_judgement.md` 给人读，`evidence_index.json` 给索引和复现。Fornax 用历史 `log_id` 回查原始 trace；如果 replay 返回新 log_id / trace_id，必须写在报告里。

## 反思规则

- 如果两个候选根因都能解释表象，优先跑能区分它们的实验。
- 如果宽召回、recall、rerank、prompt 都没有同断言证据，不要强行说 answer 错。
- 如果答案症状明显但上游证据链不完整，报告应说“答案症状明确，根因仍需实验区分”。
- 如果 replay 与历史 trace 不一致，历史 trace 是 badcase 现场，replay 是当前版本对照实验。
