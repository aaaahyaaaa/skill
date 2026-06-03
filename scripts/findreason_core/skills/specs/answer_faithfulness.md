# answer_faithfulness

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:answer_faithfulness:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

说明 `diagnostics.py` 中 `spec_id=answer` 的答案忠实性和完整性诊断规则。
答案层诊断前会先由 `AnswerEvidenceAligner` 尝试把 `qa.answer` 拆成 claim，并使用 `prompt_docs` 与 `reference_evidence` 生成 claim 支撑关系。

## 输入契约

- `qa.answer`：线上答案或人工提供的答案文本。
- `qa.answer_claims`、`qa.claim_alignments`、`qa.missing_expected_points`：自动 claim 对齐后的结构化证据状态。
- `rerank.prompt_docs` 与 `reference.support_docs/support_claims`：claim 对齐允许使用的证据来源。

## 输出契约

- `diagnostic_results[].spec_id = answer`。
- 可输出 `unsupported_claim`、`wrong_citation`、`partial_answer` 或 `uncertain`。

## 必须遵守

- 答案层失败必须绑定答案 claim、prompt 支撑关系或人工答案层标记。
- 泛化生成错误、幻觉和答案待复核统一输出 `unsupported_claim`，用 evidence 的 `unsupported_type` 和 `hallucination` 区分。
- 没有 `prompt_docs` 或 `reference_evidence` 时，不能把答案直接判成强支撑的 unsupported claim，只能作为低置信待复核证据。
- 宿主 Agent 的答案对齐只产结构化证据状态；最终根因必须由 typed diagnostic rule 产生。
- rubric/grader 冲突属于 `evaluation`，不要在答案层吞掉。
