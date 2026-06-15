# Report Contract v4

这份文档定义 FindReason v4 的短版人读报告和证据索引要求。目标是让用户看到类似一次清晰复盘回复的结论，而不是被迫阅读 JSON、长表格或 doc id 数组。

## 默认产物

默认用本地 Markdown + JSON：

- `agent_judgement.md`：每条 case 的唯一短版人读 judgement 结论文件，适合直接改写成 Agent 最终回复。它应该像一次清楚的复盘结论，不应该像固定字段表、提示词或 checklist。
- `evidence_index.json`：本地可索引证据包，适合后续批量检索、对账和复盘。

不要同时维护 `summary.md` / `summary` 和 `agent_judgement.md` 两份人读结论；summary 是 `agent_judgement.md` 内的第一段能力，不是独立产物。两份结论容易在 candidate cause、置信度和证据解释上漂移。

如果 `synthesize-brief` 产出的 `agent_judgement.md` 仍是 `candidate_cause: 待 Agent 判断`，Agent 完成归因后必须改写同一个文件，把最终 cause、置信度和证据解释落进去。最终聊天回复应与该文件一致。

`agent_judgement.md` 不应包含面向 Agent 的写作要求或 checklist，例如“Agent 最终回复要求”“给用户输出短版结论”“明确写 candidate_cause”。这类规则属于 skill/reference/agent_brief，不属于 case 结论正文。

飞书文档适合作为分享/发布层；只有用户明确要求分享、评审、沉淀到知识库时，才把 Markdown 发布或改写成飞书文档。不要把飞书文档当作唯一证据存储。

## 索引策略

- Fornax 平台：按历史 `log_id` 回查原始 trace，是历史现场的源头。
- 本地 JSON：保存本次归因实际使用的 facts、实验和证据索引，是最适合批量搜索和复现的证据包。
- Replay：如果 `replay_experiment.json` 中返回新的 `log_id` / `trace_id`，报告必须写出；如果没有返回，报告必须明确写“replay 未返回新的 log_id”。

## 必填字段

最终给用户的回复和 `agent_judgement.md` 必须保持短版结构，建议 5-7 个短段落或短列表。下面这些信息要覆盖，但不要机械套模板，也不要为了凑字段牺牲可读性：

- `log_id`
- `workspace_id`
- `app_id`
- 原始 query
- Workflow 输入
- Workflow 输出
- 包装后的输出 / `answer_hint`
- 总结提炼后的评估器信号
- 实验结果摘要：recall / rerank survival / replay，没跑也要写明没跑
- 证据充分性判断：required assertions、关键证据的 support level、仍缺的权威证据
- 反证意识：每个关键候选根因的支持证据、已跑实验、可推翻证据、当前判断；不要求输出大表格
- 归因整理：最终候选 cause、置信度、仍缺证据或人工复核理由
- `badcase_review_status`：`valid_badcase`、`needs_human_review_evaluator_disputed` 或 `not_badcase_evaluator_error`
- `human_review_reason` 与 `human_review_context`：当评估器事实正确性结论可能有误时必须提供，供人工判断该 case 是否真的算 badcase

## 证据展示

不要这样写：

```json
{"prompt_doc_ids": ["378686", "383923", "369451"]}
```

应该这样写：

```markdown
- 支撑证据：
  - 799191 【基础中台】抖音月付分期免息-交易方向
    - 文档链接：[打开文档](https://example.com/doc)
    - 命中片段：分期还款需要收取一定比例手续费，手续费默认由用户承担...
    - 命中词：抖音月付分期
```

规则：

- doc id 可以出现，但只能作为审计补充。
- 每个关键证据必须有标题，并至少有实际链接或援引片段。
- 没有链接时要写“文档链接：未提供”，并给出片段。
- 没有片段时要写“援引片段：未提供”，不能假装有支撑。
- 同一个必要断言的 `recall -> rerank -> prompt` 生存情况写在一起。
- 不同必要断言分开展示，避免把多个问题混成一个阶段失败。

## 证据充分性

报告必须判断“证据组合是否足以产出严谨业务答案”。这一步不改变顶层 cause，而是补充说明证据质量。

步骤：

- 将用户问题拆成 required assertions，即正确答案必须覆盖的具体断言。
- `chat_history` 只能用于判断上下文是否丢进 Workflow 输入，并通过上下文增强 query 的 recall / replay 对照验证 `workflow_input_loss`；不能用于支撑 `answer_failure` 的答案正误判断。
- `answer_failure` 只在必要断言已经进入 `prompt_docs` / `qaPromptDocs`，但答案仍漏答、误答、错引、越界、编造或把弱证据写强时成立。
- 对关键证据标注支撑等级：`direct_support`、`partial_support`、`adjacent_support`、`insufficient`、`contradictory`。
- 说明每条证据支撑了哪条 required assertion，哪些断言仍未被权威支撑。
- 区分“证据足以解释 replay 为什么更好”和“证据足以产出严谨业务答案”。前者不等于后者。
- 如果证据有用但不完整，要写出缺失的精确权威证据，例如“缺少明确说明是否自动扣费/自动续费/到期后如何处理的规则文档”。

## 归因说明

报告中的归因整理可以用短段落，不要为了形式输出大而空的表格。每个候选解释至少说明：它解释了哪个表象、什么证据支持、已跑什么实验、什么证据会推翻它、当前是否保留。

顶层 cause 只能从 5 类中选择：

- `workflow_input_loss`
- `suspected_knowledge_missing`
- `retrieval_miss`
- `rerank_drop`
- `answer_failure`

如果证据不能支撑唯一主因，应写低置信或人工复核，不要强行落标签。

## Badcase 复核状态

`badcase_review_status` 独立于五类 cause，用于处理“评估器可能误判，该 case 可能不算 badcase”的情况。

- `valid_badcase`：评估器或人工指出的问题与证据链一致，按五类 cause 继续归因。
- `needs_human_review_evaluator_disputed`：评估器事实正确性结论与 prompt evidence、Workflow 输出或人工标注存在明显冲突，需要人工确认是否真是 badcase。
- `not_badcase_evaluator_error`：仅在人工确认或明确人工标注后使用，表示评估器判断有误，该样本不应进入 badcase 根因统计。

请求人工复核时，必须提供足够上下文：query、judged answer、Workflow input/output、evaluator claim、关键 prompt 证据、为什么怀疑评估器误判。不要只写“需人工复核”。
