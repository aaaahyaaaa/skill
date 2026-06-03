# retrieval

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:retrieval:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

说明 `diagnostics.py` 中 `spec_id=retrieval` 的知识存在和线上召回诊断规则。

## 输出契约

- `diagnostic_results[].spec_id = retrieval`。
- 可输出 `knowledge_missing`、`knowledge_topic_mismatch`、`retrieval_miss`、`permission_miss` 或 `uncertain`。

## 必须遵守

- 线上 replay、wide recall 和 knowledge detail 必须分开看。
- 失败必须引用期望 ID、文档 ID、召回列表、计数或权限标记。
- `knowledge_missing` 使用 evidence 的 `certainty=confirmed|suspected` 表达强弱，不再拆成两个最终 cause。
- `knowledge_topic_mismatch` 必须有非空 topK 和语义支撑判定；topK 为空或 contrastive probe 找到正确遗漏时不能判该 cause。
