# v3 编排规则

主因阶段顺序为：

```text
preprocess -> knowledge -> retrieval -> rerank -> answer
```

`orchestrate` 仍可在 `evidence_chain` 中保留 `context` 和 `evaluation` 观察 verdict，但它们不产生顶层 `candidate_cause`。失败 verdict 只允许输出 5 类 cause：`workflow_input_loss`、`suspected_knowledge_missing`、`retrieval_miss`、`rerank_drop`、`answer_failure`。

推荐诊断流程：

1. 先做 answer symptom extraction：抽取 `unsupported_claim`、`wrong_citation`、`missing_aspect`、`scope_violation`，写入 `secondary_findings.answer_issue_types`。
2. 再做 upstream evidence chain：检查 preprocess / knowledge / retrieval / rerank 的断言级证据链。
3. 最后选择 `candidate_cause`：选择最早断点；如果上游都通过或必要证据已通过 rerank，才落到 `answer_failure`。

主因选择规则：

1. 按阶段顺序遍历。
2. 跳过带 `upstream_blocked_by` 的 verdict。
3. 跳过 `not_probed`。
4. 选择第一个失败、counterfactual 可用且 `downstream_would_change=true` 的阶段。
5. 如果更早阶段未解决，不选择下游失败作为主因。
6. 如果没有有效的上游 counterfactual，返回 `primary_cause=null` 和 `needs_human_review=true`。

`preprocess` 阶段内部先判断输入边界，再判断预处理节点。输入边界失败本身只是风险信号，只有同时满足以下条件才升级为 `workflow_input_loss` 主因：

1. 用户实际问题/评估器用户上下文线索中的关键约束没有进入 Workflow 原始输入。
2. 受影响的 `expected_required` 已绑定到 `point_coverage`。
3. 该断言在理论召回上界可支撑，但 online origin recall 没有支撑文档。

如果同一断言已经被 online origin / rerank / prompt 支撑，说明 Workflow 原始输入差异没有切断正确证据链，preprocess 不应阻断下游归因。若理论召回上界也不支撑该断言，则优先判断知识缺口或人工复核。

`answer_failure` 的前置条件：

1. `qa.answer_satisfies_expected=false`。
2. `qa.prompt_supports_answer=true`，或断言覆盖显示必要支撑已通过 rerank。
3. knowledge / retrieval / rerank 没有更早断点。

答案层细节只进入 `secondary_findings.answer_issue_types`。即使答案存在多种症状，只要上游有更早断点，顶层主因仍停在上游。

Prompt/context 当前不是独立归因阶段。`missing_expected_points_from_prompt`、`expected_doc_in_prompt=false`、prompt truncation 和 noise overload 只作为 trace 观察，不选择 `context_assembly_error`，也不阻塞答案层裁决。

以下情况需要人工复核：preliminary mode、重试后知识存在性仍为 unknown、trace 不完整阻塞归因、replay 与 trace 不一致、probe 证据相互矛盾、或主因为 null。
