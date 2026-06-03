# query_preprocess

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:query_preprocess:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

说明 `diagnostics.py` 中 `spec_id=preprocess` 的执行规则。

## 输出契约

- `diagnostic_results[].spec_id = preprocess`。
- 可输出 `non_rag_route_boundary`、`query_rewrite_drift`、`keyword_loss` 或 `uncertain`。

## 必须遵守

- 缺少真实 `rewrite_query` trace 时，不能声称 rewrite 与原 query 一致。
- 预处理根因必须绑定 query、rewrite 或 keyword 证据。
