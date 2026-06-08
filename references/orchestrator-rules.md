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

`preprocess` 阶段内部先判断输入边界，再判断预处理节点：用户实际问题/评估器用户上下文线索中的关键约束如果没有进入 Workflow 原始输入，判 `workflow_input_loss`；只有 Workflow 原始输入完整时，才比较 `rewrite_query` / `keywords` 并判断 `query_rewrite_drift` 或 `keyword_loss`。

以下情况需要人工复核：preliminary mode、低置信度、重试后知识存在性仍为 unknown、trace 不完整阻塞归因、replay 与 trace 不一致、probe 证据相互矛盾、或主因为 null。
