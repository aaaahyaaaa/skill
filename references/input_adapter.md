# input_adapter

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:input_adapter:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

把页面或表格 payload 标准化为 `case_input`、`source_metadata`、`field_map` 和 `judgement_evidence`。

## 输出契约

- `status`：`pass` 或 `missing`。
- `missing_fields`：缺失的 `query`、`workspace_id`、`app_id`。
- `normalized_case_input`、`source_metadata`、`field_map`、`judgement_evidence`。

## 必须遵守

- Judgement Mapper 只抽取开放 signals，不做根因归因。
- 保留 source row、raw value、field mapping confidence，不能用分析师总结列伪造 trace。
