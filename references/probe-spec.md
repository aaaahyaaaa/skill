# v3 Probe Spec

All probes emit JSON with:

- `schema_version: "v3"`
- `log_id`, `workspace_id`, `probe_name`, `status`
- `stage_signals`: normalized signals consumed by `orchestrate`
- `evidence_bundle`: one or more evidence records
- `raw_artifacts`: probe-specific raw data
- `telemetry.latency_ms`, `telemetry.cache_key`, `telemetry.cache_hit`

Probe cache lives at `~/.findreason/cache/<workspace_id>/<log_id>/`. Pass `--no-cache` to recompute.

| Probe | Stage | Key signal |
|-|-|-|
| `probe-self-oracle` | knowledge | `inferred_expected_docs`, `expected_knowledge_points`, `point_coverage`, `oracle_status`, `oracle_confidence` |
| `probe-knowledge-detail` | knowledge | `knowledge_exists: yes/no/unknown`, `retry_count` |
| `probe-permission-check` | retrieval | `permission_miss`, `permission_available` |
| `probe-wide-recall` | retrieval | `theoretical_recall_status`, `theoretical_query_variants`, `wide_recall_docs`, `matched_expected_ids`, `retrieval_gap_detected` |
| `probe-rerank-bypass` | rerank | `bypass_would_restore`, `expected_doc_survived_rerank` |
| `probe-rerank-tune` | rerank | `rerank_tunable`, `tunable_param` |
| `probe-context-assembly` | context | `expected_doc_in_prompt`, `context_assembly_error` |
| `probe-by-judgement` | retrieval | host-supplied judgement evidence availability |
| `probe-by-claim` | answer | `host_agent.answer_claim`, `wrong_citation`, answer preconditions |
| `probe-by-doc-title` | retrieval | exact title/id matches in trace docs |

`probe-self-oracle` runs before other probes by default. It infers expected documents only when the host Agent has supplied required assertions or expected IDs. It uses these signals:

- `judgement_back_recall`: judgement/rubric observations only; assertions must already be represented in `host_agent.answer_claim`.
- `claim_back_recall`: `host_agent.answer_claim` items, including unsupported claims marked by role.
- `query_wide_recall`: original/rewrite query only as a recall query variant, not as an expected assertion.

The current P0 implementation matches those signals against trace-local candidate docs and emits `evidence_type=inferred_oracle`. The output contract is compatible with a later live KB recall backend.

Together with `probe-wide-recall`, it also builds an assertion coverage matrix:

- `host_agent.answer_claim`: the only host assertion input, using nested shape `{"host_agent": {"answer_claim": [...]}}`. Each item is normalized into `expected_knowledge_points` output with `text`, `role`, `source`, and optional `confidence`. Supported roles are `expected_required`, `missing_expected`, `answer_claim`, and `unsupported_claim`; `source` is normalized to `host_agent.answer_claim`.
- The CLI does not use `case_input.expected_knowledge_points`, `qa.answer_claims`, `qa.missing_expected_points`, `qa.unsupported_claims`, `qa.claim_alignments`, or `judgement_evidence.signals[].assertions/fact_points/missing_expected_points` as assertion sources. Non-empty legacy fields fail with `E_LEGACY_ASSERTION_INPUT`.
- The CLI must not create expected assertions from query text, evaluator labels, empty-answer diagnostics, or arbitrary rubric/judgement fragments. If no required assertion is available, `oracle_status.source=insufficient_assertions`.
- `probe-wide-recall` should run original query + rewrite query at topK >= 50 and treat the result as the theoretical recall upper bound.
- `point_coverage`: per-required-assertion matches in theoretical upper-bound docs, `origin_doc_list/origin_faq_list`, `rerank_docs`, and `prompt_docs`. Human reports should render online stages as the assertion coverage matrix and render theoretical upper-bound docs as a separate assertion relationship section, not as an unexplained standalone stage column.
- `missing_expected_points_from_theoretical_recall`: required assertions with no supporting upper-bound doc. These are treated as partial knowledge missing.
- `missing_expected_points_from_origin`: required assertions supported by the upper bound but missed by online origin recall.
- `missing_expected_points_from_rerank`: required assertions/docs present in initial recall but lost by rerank.
- `missing_expected_points_from_prompt`: required assertions/docs that survive upstream but are absent from prompt context.

`replay-workflow` is not parallel-safe. Run it only after ingest indicates trace failure or missing middle-node evidence.
