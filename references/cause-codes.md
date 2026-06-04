# v3 原因码

`candidate_cause` 必须是以下 16 个值之一。

| 阶段 | cause_code | 负责人 | 必要条件 |
|-|-|-|-|
| preprocess | `non_rag_route_boundary` | agent_router_owner | Case 不是知识问答，或应该路由到 RAG 之外。 |
| preprocess | `query_rewrite_drift` | rag_preprocess_or_workflow_owner | rewrite 改变了用户意图，并影响下游召回。 |
| preprocess | `keyword_loss` | rag_preprocess_or_workflow_owner | 核心实体 / 短语在召回前丢失。 |
| knowledge | `suspected_knowledge_missing` | kb_owner | `knowledge_exists=no`、理论召回无法支撑必要断言，或必要引用缺少权威 / 可引用来源；状态为 `unknown` 时不得使用。 |
| knowledge | `knowledge_topic_mismatch` | kb_owner | TopK 中有相邻主题文档，但缺少精确主题。 |
| knowledge | `knowledge_internal_inconsistency` | kb_owner | KB 中存在冲突说法，且没有清晰的适用前提（probe `internal_contradiction` 未命中能消歧的 KB 来源）。 |
| retrieval | `retrieval_miss` | retrieval_strategy_owner | 知识存在，但线上召回漏掉期望文档。 |
| retrieval | `permission_miss` | knowledge_permission_owner | 正确知识存在，但线上被 ACL / namespace 隐藏。 |
| rerank | `rerank_drop` | rerank_strategy_owner | 召回已命中，但期望文档没有通过 rerank。 |
| rerank | `rerank_tunable` | rerank_strategy_owner | 参数或 tunable 证据显示 rerank 可恢复目标文档。 |
| context | `context_assembly_error` | workflow_or_prompt_context_owner | 期望文档通过 rerank 但未进入 prompt context，或 context 被截断 / 噪声污染。 |
| answer | `unsupported_claim` | prompt_or_model_owner | `prompt_supports_answer=true`、`answer_satisfies_expected=false`，且存在 unsupported claims。 |
| answer | `wrong_citation` | prompt_or_model_owner | 在答案阶段前置条件满足时，答案引用了错误文档。 |
| answer | `partial_answer` | prompt_or_model_owner | Prompt 支持完整答案，但输出遗漏必要方面。 |
| answer | `answer_scope_violation` | prompt_or_model_owner | 答案超出问题约束下 KB 可支持的范围（probe `scope_violation` miss）。 |
| answer | `answer_branching_unclear` | prompt_or_model_owner | KB 已澄清分支前提，但答案混用分支且未区分（probe `internal_contradiction` hit）。 |

答案类原因要求同时满足 `qa.prompt_supports_answer=true` 和 `qa.answer_satisfies_expected=false`。如果 `prompt_supports_answer=false`，主因必须停留在上游。

Evaluation 在 v3 中只作为观察层，没有官方 cause code。

## `probe-v1` direction 到 cause 的映射关系

宿主 Agent 通过 `run-probe-plan`（`schema_version=probe-v1`）规划 probes。每个 direction 会路由到一个 `target_artifact`，执行确定性 hit/miss 检查，并折叠进 `stage_signals`：

| direction | target_artifact | hit / miss 结果 |
|-|-|-|
| `relevance_gap` | kb_wide_recall | 进入确定性的 `point_coverage` 链路（knowledge / retrieval / rerank / context）。 |
| `coverage_gap` | kb_wide_recall / online_origin_recall | 进入确定性的 `point_coverage` 链路。 |
| `scope_violation` | kb_wide_recall | miss → `answer_scope_violation`。 |
| `citation_missing` | online_origin_recall / rerank_output | hit → `wrong_citation`；miss → `suspected_knowledge_missing`（缺少权威来源）。 |
| `internal_contradiction` | answer_span / kb_wide_recall | hit → `answer_branching_unclear`；miss → `knowledge_internal_inconsistency`。 |
