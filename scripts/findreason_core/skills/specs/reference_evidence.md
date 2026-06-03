# reference_evidence

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:reference_evidence:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

汇总人工锚点、线上 replay、诊断宽召回和知识详情补全，供诊断 registry 使用。

## 输出契约

- `status`：有任一可用锚点或 trace evidence 时为 `pass`，否则为 `missing`。
- `expected_knowledge_ids`、`support_docs`、`support_claims`、各证据来源计数和 matched ids。

## 必须遵守

- 明确区分人工期望 ID、线上 replay 命中、wide recall 命中和 knowledge detail 补全。
- 只有 judgement notes 时只能作为线索，不能支撑强根因。
