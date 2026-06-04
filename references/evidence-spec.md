# 证据规范

每个 `orchestrate` 输出都包含 `evidence_bundle` 和 `evidence_chain`。

证据记录字段：

- `evidence_id`
- `evidence_type`
- `source_stage`
- `source`
- `content`
- `relation_to_query`
- `relation_to_answer`
- `quality`

校验规则：

1. 每个带 `candidate_cause` 的失败 verdict 必须至少绑定一个 `evidence_id`。
2. 每个 verdict 的 `evidence_ids` 都必须存在于 `evidence_bundle`。
3. 每个 `counterfactual.evidence_ids` 都必须存在于 `evidence_bundle`。
4. 每个 verdict 必须包含 `counterfactual`。
5. `candidate_cause` 必须属于 v3 枚举。

校验失败时，CLI 返回以下错误之一：

- `E_CAUSE_NOT_IN_ENUM`
- `E_COUNTERFACTUAL_MISSING`
- `E_EVIDENCE_NOT_BOUND`
- `E_EVIDENCE_ID_INVALID`
