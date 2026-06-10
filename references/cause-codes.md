# v3 原因码

`candidate_cause` 只能是以下 5 个顶层值之一。旧版细标签不再作为主因输出，只能作为阶段信号、答案症状或报告观察。

| stage | cause_code | owner | definition | positive_signals | boundary / exclusion |
|-|-|-|-|-|-|
| preprocess | `workflow_input_loss` | `workflow_input_owner` | 用户真实问题或评估器上下文中的关键场景约束在 Workflow 原始输入、rewrite 或 keywords 中丢失/弱化，导致理论可支撑的必要断言在线上初召回缺失。 | workflow input boundary 失败且 impact.causal=true；rewrite 改变用户意图；关键实体、限定词、时态、范围、角色或业务对象在召回前丢失。 | 必须有 counterfactual 证据证明输入失真切断线上证据链。若受影响断言已被 online origin / rerank / prompt 支撑，不能归因到此类；若理论召回上界也不支撑该断言，应落到知识缺口或人工复核。 |
| knowledge | `suspected_knowledge_missing` | `kb_owner` | KB 无法充分、权威、可适用地支撑必要断言。包含知识不存在、主题不精确、权威来源缺失、KB 冲突且无法消歧。 | `knowledge_exists=no`；理论召回上界无法覆盖必要断言；缺少权威/可引用来源；相邻主题命中但精确主题缺失；KB 冲突影响 `expected_required` 且没有清晰适用前提。 | `knowledge_exists=unknown` 不得直接判知识缺失。若知识存在且线上 origin recall 未命中，应判 `retrieval_miss`；若 origin 有支撑但 rerank 丢失，应判 `rerank_drop`。 |
| retrieval | `retrieval_miss` | `retrieval_strategy_owner` | 理论召回或 KB 可支撑必要断言，但线上 origin recall 未召回同断言支撑。权限/namespace 隐藏也并入此类。 | 理论召回上界命中但 online origin recall miss；正确知识存在但线上召回漏掉；ACL / namespace / 知识状态过滤导致当前 workspace/app/user 路径不可见。 | 若检索前输入已失真且满足 counterfactual，应优先 `workflow_input_loss`。若 origin recall 已有同断言支撑但 rerank 后消失，应判 `rerank_drop`。 |
| rerank | `rerank_drop` | `rerank_strategy_owner` | online origin recall 已有同断言支撑，但 rerank output 不再保留。参数或 tunable 证据显示 rerank 可恢复目标文档也并入此类。 | `missing_expected_points_from_rerank` 非空；origin 中有可回答支撑而 rerank 丢失同一必要断言；bypass / tunable / 参数证据显示可恢复。 | 仅 doc ID 没通过 rerank 只能作为观察，不能单独触发。若必要断言已经通过 rerank，则不判此类。 |
| answer | `answer_failure` | `answer_owner` | rerank output 已具备必要证据，按当前系统假设已完整交给模型，但答案仍编造、错引、漏答、越界或混用前提。 | `qa.answer_satisfies_expected=false` 且 `qa.prompt_supports_answer=true`，或断言覆盖显示必要支撑已通过 rerank；`secondary_findings.answer_issue_types` 包含答案症状。 | 若 knowledge / retrieval / rerank 有更早断点，答案症状只能作为观察。prompt/context 不再作为独立主因；如未来证明 rerank 后证据并非完整进入模型，再重新引入 prompt 阶段。 |

## Answer Layer

`answer_failure` 是唯一答案层顶级 cause。答案细节写入 `secondary_findings.answer_issue_types`：

| issue_type | meaning |
|-|-|
| `unsupported_claim` | 答案写了证据不支持的断言，或把弱证据写成强结论。 |
| `wrong_citation` | 存在可用支撑，但答案引用了错误文档、错误片段或不支撑结论的来源。 |
| `missing_aspect` | 证据足够，但答案遗漏用户问题要求的必要方面、限制条件或关键维度。 |
| `scope_violation` | 答案超出用户问题约束、扩大适用范围，或混用不同分支前提。 |

答案症状先由宿主 Agent / 大模型从 badcase 输入、评估器证据、answer span、citation 和 scope 检查中抽取；CLI 只负责把这些症状纳入 evidence-first 裁决。若上游 evidence chain 有更早断点，`answer_issue_types` 只解释“答案错在哪里”，不改变顶层主因。

## Prompt / Context 边界

当前版本假设 rerank 后证据会完整进入模型，因此 `context_assembly_error`、`prompt_truncation_or_context_drop` 不再是顶层 `candidate_cause`。`missing_expected_points_from_prompt`、`expected_doc_in_prompt=false`、prompt truncation 和 noise overload 仅保留为 trace 观察或报告提示，不参与主因选择，也不阻塞 answer 裁决。

Evaluation 在 v3 中只作为观察层，没有官方 cause code。`non_rag_route_boundary` 也不进入 RAG attribution cause，应作为 `case_assessment` 或人工复核处理。

## `probe-v1` direction 到 cause / issue 的映射

宿主 Agent 通过 `run-probe-plan`（`schema_version=probe-v1`）规划 probes。每个 direction 会路由到一个 `target_artifact`，执行确定性 hit/miss 检查，并折叠进 `stage_signals`：

| direction | target_artifact | hit / miss 结果 |
|-|-|-|
| `relevance_gap` | `kb_wide_recall` | 进入确定性的 `point_coverage` 链路，驱动 knowledge / retrieval / rerank；prompt_context 只作观察。 |
| `coverage_gap` | `kb_wide_recall` / `online_origin_recall` / `rerank_output` / `prompt_context` | required assertion 的 hit/miss 可驱动 knowledge / retrieval / rerank；`prompt_context` miss 只产生 prompt 观察。 |
| `scope_violation` | `answer_span` / `kb_wide_recall` | 命中或未命中证明答案越界时，写入 `answer_issue_types.scope_violation`；最终只有上游通过时才可能选择 `answer_failure`。 |
| `citation_missing` | `online_origin_recall` / `rerank_output` | hit → `answer_issue_types.wrong_citation`；miss → `suspected_knowledge_missing`（缺少权威来源）。 |
| `internal_contradiction` | `answer_span` | hit → `answer_issue_types.scope_violation`（答案混用分支前提）。 |
| `internal_contradiction` | `online_origin_recall` / `rerank_output` / `prompt_context` / `kb_wide_recall` | hit → `chunk_internal_conflict` 风险；只有影响 `expected_required` 且没有清晰适用前提时，才并入 `suspected_knowledge_missing`。 |
