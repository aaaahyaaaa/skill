# 重排与上下文（rerank_context）

## 开发探针

使用此 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:rerank_context:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

说明 `diagnostics.py` 中 `spec_id=rerank_context` 的重排存活和 prompt 组装诊断规则。

## 输出契约

- `diagnostic_results[].spec_id = rerank_context`。
- 可输出 `rerank_drop`、`rerank_tunable`、`context_assembly_error` 或 `uncertain`。

## 必须遵守

- 只有线上召回已命中时，才允许判重排丢弃。
- 只有证据已通过 rerank 却未进 prompt 时，才允许判上下文组装问题。
- 参数或阈值可恢复目标文档时统一输出 `rerank_tunable`，用 evidence 的 `tunable_param=threshold|feature` 区分。
- prompt 截断统一输出 `context_assembly_error`，用 evidence 的 `truncated=true` 区分。
