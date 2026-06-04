# 查询预处理（query_preprocess）

## 开发探针

使用本 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:query_preprocess:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

说明 `diagnostics.py` 中 `spec_id=preprocess` 的执行规则。

## 输出契约

- `diagnostic_results[].spec_id = preprocess`。
- 可输出 `non_rag_route_boundary`、`query_rewrite_drift`、`keyword_loss` 或 `uncertain`。

## 必须遵守

- 缺少真实 `rewrite_query` trace 时，不能声称 rewrite 与原 query 一致。
- 预处理根因必须绑定 query、rewrite 或 keyword 证据。
