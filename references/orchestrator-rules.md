# v3 编排规则

阶段顺序为：

```text
preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation
```

对每个阶段，`orchestrate` 都会输出一个 verdict，其中包含 `stage`、`status`、`evidence_ids`、`counterfactual` 和 `upstream_blocked_by`。失败 verdict 还会包含 `candidate_cause`、`confidence` 和 `owner`。

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

以下情况需要人工复核：preliminary mode、低置信度、重试后知识存在性仍为 unknown、trace 不完整阻塞归因、replay 与 trace 不一致、probe 证据相互矛盾、或主因为 null。
