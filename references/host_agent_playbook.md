# Host Agent Playbook v3

The host Agent owns orchestration and language understanding. The CLI owns deterministic evidence collection, probe normalization, and counterfactual attribution.

## Host Responsibilities

- Extract `query`, `judgement`, `workspace_id`, `app_id`, `log_id`, `case_id/source_row`, and strongly implied `expected_knowledge_ids` from pasted text, curl/body fragments, or sheet rows.
- Derive assertion-style `host_agent.answer_claim` before running probes. This is the only host assertion input and must use nested shape `{"host_agent": {"answer_claim": [...]}}`. Each item should include `text`, `role`, optional `source`, and optional `confidence`; valid roles are `expected_required`, `missing_expected`, `answer_claim`, and `unsupported_claim`. The CLI normalizes `source` to `host_agent.answer_claim`.
- Only `expected_required` and `missing_expected` are used to judge knowledge, retrieval, rerank, and context gaps. `unsupported_claim` is answer-stage evidence only.
- Do not create expected assertions from query text, evaluator dimensions, pass/fail labels, empty-answer diagnostics, or arbitrary long rubric/judgement fragments.
- Do not put assertions into `case_input.expected_knowledge_points`, `qa.answer_claims`, `qa.missing_expected_points`, `qa.unsupported_claims`, `qa.claim_alignments`, or `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`; non-empty legacy fields fail with `E_LEGACY_ASSERTION_INPUT`.
- Compress long grader/rubric output into `judgement_evidence.signals` under 2KB. Treat evaluator labels as observations, not trace evidence.
- Extract unsupported claims into `host_agent.answer_claim` with `role=unsupported_claim`; keep `wrong_citations` and answer precondition fields separately.
- Read `ingest_summary.suggested_probe_set` and run only the recommended probes unless the user asks for a specific branch.
- Render the final report from `orchestrate` JSON and explicitly show `needs_human_review` reasons.
- If no `expected_required` / `missing_expected` assertion is available, expect `oracle_status.source=insufficient_assertions`; supply assertions and rerun if upstream attribution is needed.

## Attribution Flow

1. Run `python -m findreason ingest-fornax-trace --workspace-id <ws> --log-id <log> --case-file <case.json> --output-dir <case-dir>`.
2. Inspect `ingest_summary.trace_completeness`, `suggested_probe_set`, and `host_action_required`.
3. Run recommended probes into `<case-dir>/probes/`. Probe commands can run in parallel except `replay-workflow`.
4. Run `python -m findreason orchestrate --ingest-file <case-dir>/ingest.json --probe-dir <case-dir>/probes --mode final --schema-version v3 --output-dir <case-dir>/final`.
5. Write the user-facing report from `primary_cause`, `evidence_chain`, `failure_patterns`, `next_actions`, and `raw_artifacts.workflow_span_ios`.

## Evidence Priority

Original Fornax middle-node trace evidence is authoritative. If trace includes `Start`, `End`, `ZhiShangRAGRecall`, `ZhiShangRAGRerank`, or `ZhiShangRAGQA`, do not replay workflow and do not overwrite `origin_doc_list`, `rerank_docs`, `prompt_docs`, or `answer`.

If trace lookup fails or lacks middle-node evidence, run `fetch-workflow-nodes` and then `replay-workflow`. Replay evidence is marked lower quality when it diverges from historical trace.

## Report Checklist

- Case identifiers: `log_id`, `workspace_id`, `app_id`, `case_id/source_row`.
- Trace summary: node completeness, origin/rerank/prompt counts, and workflow span input/output.
- Primary cause: stage, cause_code, confidence, owner, rationale.
- Assertion coverage matrix: assertion text, role, source, theoretical upper-bound recall, online initial recall, rerank, and prompt.
- Evidence chain: stage verdicts with counterfactuals and `upstream_blocked_by`.
- Failure patterns and next actions.
- Human review warning when `needs_human_review=true`.
