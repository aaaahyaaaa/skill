# Field Contract v3

## Ingest Input

`ingest-fornax-trace` requires:

- `workspace_id`
- `log_id`

Optional host fields can be provided through `--case-file`:

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

`judgement_evidence.signals` must serialize to <= 2KB.
`host_agent.answer_claim` is the only host assertion input. It must use nested JSON shape `{"host_agent": {"answer_claim": [...]}}`. Each item should contain host Agent supplied assertions with `text`, `role`, optional `source`, and optional `confidence`. Valid roles are `expected_required`, `missing_expected`, `answer_claim`, and `unsupported_claim`; the CLI normalizes `source` to `host_agent.answer_claim`.
Only `expected_required` and `missing_expected` drive knowledge, retrieval, rerank, and context attribution. `unsupported_claim` is answer-stage evidence only.
Evaluator dimensions, pass/fail labels, empty-answer diagnostics, query text, and unstructured rubric/judgement fragments are observations and must not become expected assertions.
Do not put assertions into `case_input.expected_knowledge_points`, `qa.answer_claims`, `qa.missing_expected_points`, `qa.unsupported_claims`, `qa.claim_alignments`, or `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`. Non-empty legacy assertion fields fail with `E_LEGACY_ASSERTION_INPUT`.
When no required assertion is available, output `oracle_status.source=insufficient_assertions` and ask the host Agent to supply assertions before judging upstream stages.

## Ingest Output

Required top-level fields:

- `schema_version`
- `log_id`
- `workspace_id`
- `app_id`
- `case`
- `ingest_summary`
- `raw_artifacts`

`ingest_summary` includes `trace_completeness`, `suggested_probe_set`, `skip_reason`, and `host_action_required`.

## Orchestrate Output

Required top-level fields:

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

`primary_cause` is either an object with `stage`, `cause_code`, `confidence`, `owner`, and `selection_rationale`, or `null` when attribution is blocked.
