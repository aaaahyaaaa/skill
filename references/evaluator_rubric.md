# evaluator_rubric

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:evaluator_rubric:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

说明 `diagnostics.py` 中 `spec_id=evaluation` 的评估器、rubric 和人工标签观察项。

## 输出契约

- `diagnostic_results[].spec_id = evaluation`。
- `grader_or_rubric_issue`、`label_conflict`、`rubric_scope_mismatch`、`evaluator_missing_evidence` 只作为 evidence observation 输出。
- evaluation 不输出 fail verdict，也不作为 primary cause。

## 必须遵守

- 评估层问题不能掩盖 RAG 链路缺证据。
- 评估器问题必须先和答案支撑、人工标签、trace 证据联动复核，不能单独归因。
- `evaluator_missing_evidence` 缺失项列表只写入 evidence，不自动判根因。
