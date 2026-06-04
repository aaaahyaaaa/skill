# 字段契约 v3

## `ingest` 输入

`ingest-fornax-trace` 必填：

- `workspace_id`
- `log_id`

可通过 `--case-file` 提供的宿主字段：

- `query`
- `judgement`
- `app_id`
- `case_id`
- `source_row`
- `expected_knowledge_ids`
- `judgement_evidence.signals`
- `wrong_citations`
- `host_agent.answer_claim`
- `qa.prompt_supports_answer`
- `qa.answer_satisfies_expected`

`judgement_evidence.signals` 序列化后必须小于等于 2KB。

`host_agent.answer_claim` 是唯一的宿主断言输入，必须使用嵌套 JSON 结构 `{"host_agent": {"answer_claim": [...]}}`。每项应包含宿主 Agent 给出的断言：`text`、`role`，可选 `source` 与 `confidence`。合法 role 包括 `expected_required`、`missing_expected`、`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`；CLI 会把 `source` 归一化为 `host_agent.answer_claim`。

只有 `expected_required` 和 `missing_expected` 驱动 knowledge、retrieval、rerank、context 归因。`unsupported_claim` 只作为 answer 阶段证据。`constraint_check`、`citation_check`、`consistency_check` 用于 `probe-v1` 计划和答案/知识观察，不直接作为必要事实断点。

评估器维度、通过/失败标签、空回复诊断、query 文本、非结构化 rubric / judgement 片段都只是观察项，不得变成期望断言。

不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`。旧断言字段非空会报 `E_LEGACY_ASSERTION_INPUT`。

没有必要断言时，输出 `oracle_status.source=insufficient_assertions`，并要求宿主 Agent 补充断言后再判断上游阶段。

## `ingest` 输出

必需顶层字段：

- `schema_version`
- `log_id`
- `workspace_id`
- `app_id`
- `case`
- `ingest_summary`
- `raw_artifacts`

`ingest_summary` 包含 `trace_completeness`、`suggested_probe_set`、`skip_reason` 和 `host_action_required`。

## `orchestrate` 输出

必需顶层字段：

- `schema_version`
- `log_id`
- `workspace_id`
- `primary_cause`
- `failure_patterns`
- `needs_human_review`
- `human_review_reasons`
- `evidence_bundle`
- `evidence_chain`
- `next_actions`
- `telemetry`
- `deprecations`
- `raw_artifacts`

`primary_cause` 要么是包含 `stage`、`cause_code`、`confidence`、`owner`、`selection_rationale` 的对象，要么在归因被阻塞时为 `null`。
