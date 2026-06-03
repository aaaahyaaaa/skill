---
name: findreason-rag-attribution
description: FindReason RAG 归因 skill。对 RAG 答错、答漏、答非所问的 badcase 做证据采集和规则归因，输出 primary_cause、evidence_chain、failure_patterns、next_actions。触发词包括 RAG 归因、findreason、fornax trace、为什么答错了、case 复盘、归因报告、知识缺失、召回缺失、rerank 误杀、unsupported claim、wrong citation、partial answer。
---

# FindReason RAG Attribution v3

This skill is an Evidence-first Attribution Engine. The host Agent orchestrates the case; the bundled CLI gathers trace/probe evidence and emits stable JSON. The skill does not call an LLM, does not write the final natural-language report, and does not fan out batch work.

## Security and Authentication

Never print raw tokens, API keys, or Authorization headers. Configure OpenPlat tokens through exported environment variables or a host-local env file, without `Bearer`; the CLI builds `Authorization: Bearer <token>`. Keep committed defaults free of real secrets.

# Skills CLI
Get authentications via SSO for internal API authentication when live workflow or RDS access is needed.
# Set the npm registry to BNPM
`export npm_config_registry=https://bnpm.byted.org/`
# Get JWT
`npx -y skills get-jwt`
`npx -y skills -h`
# Options
`--region` optional, valid values: `cn`, `i18n`, `boe`, `sandbox`

## Global Rules

1. Any attribution case must run `ingest-fornax-trace` first and `orchestrate` last. `ingest-fornax-trace --raw` is only for raw trace inspection and is not attribution.
2. Candidate causes are limited to the 13-value v3 enum in `references/cause-codes.md`.
3. Every stage verdict must include `counterfactual`.
4. If an upstream stage blocks downstream judgment, downstream verdicts must set `upstream_blocked_by`.
5. Primary cause selection walks `preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation` and chooses the first fail whose counterfactual says downstream would change.
6. If upstream counterfactuals are unavailable, do not sink to downstream answer failures. Emit `primary_cause=null`, `needs_human_review=true`.
7. `judgement_evidence.signals` must be <= 2KB. The CLI returns `E_EVIDENCE_TOO_LARGE` and does not truncate.
8. Fornax trace middle-node evidence is authoritative. Use workflow replay only when trace lookup fails or lacks middle-node evidence.
9. Probe commands are independent and can run in parallel. `ingest` and `orchestrate` are serial. `replay-workflow` is exclusive and must not run in parallel with probes.
10. Output contract is JSON with `schema_version: "v3"`.
11. Host Agent handles language tasks: input extraction, judgement compression, unsupported claim extraction, citation extraction, answer span alignment, and the final user-facing report.
12. Batch fan-out belongs to the host Agent; this skill handles one case at a time.
13. Evidence binding is validated: candidate causes need evidence IDs, and counterfactual evidence IDs must reference `evidence_bundle`.
14. Knowledge existence is tri-state: `yes / no / unknown`. Unknown after retry becomes `indeterminate` + human review, not `suspected_knowledge_missing`.
15. `needs_human_review=true` must be shown explicitly in user reports.
16. Scope is RAG answer attribution only.
17. `expected_knowledge_ids` is optional. If it is missing, the host must run `probe-self-oracle`; the skill must not treat "no expected knowledge provided" as proof that retrieval/rerank/context passed.
18. Self-oracle inferred knowledge must carry `oracle_source` and `confidence`; verdict confidence is folded by oracle confidence when oracle evidence drives a cause.
19. If both provided expected knowledge and self-oracle inference exist and do not overlap, set `needs_human_review=true` with reason `provided expected knowledge contradicts self-oracle inference`.
20. `host_agent.answer_claim` is the only host Agent assertion input. Use nested JSON shape `{"host_agent": {"answer_claim": [...]}}`. Each item should be an object with `text`, `role`, optional `source`, and optional `confidence`; valid roles are `expected_required`, `missing_expected`, `answer_claim`, and `unsupported_claim`. The CLI normalizes `source` to `host_agent.answer_claim`.
21. The CLI must not create expected assertions from query text, evaluator labels, empty-answer diagnostics, or arbitrary rubric/judgement fragments. If no `expected_required` / `missing_expected` assertion is available, emit `oracle_status.source=insufficient_assertions`, keep `primary_cause=null` unless answer-stage evidence is independently sufficient, and ask the host Agent to supply assertions.
22. Only `expected_required` and `missing_expected` drive knowledge, retrieval, rerank, and context attribution. `unsupported_claim` is answer-stage evidence only and must not trigger `suspected_knowledge_missing`.
23. Human reports must list assertion coverage and stage gaps. The assertion coverage matrix should focus on online stages (`origin -> rerank -> prompt`); theoretical recall upper bound must not appear as a standalone "hit doc" column unless it explicitly binds each upper-bound document to the required assertion it supports. Reports should include a separate theoretical-upper-bound/assertion relationship section with supporting doc IDs, titles, matched terms, and scores when available. `probe-wide-recall` must build the theoretical recall upper bound from the trace Sirius recall request template, run original query + rewrite query with topK >= 50, and use `upper_bound_scope=open_label` by clearing `recallLabels/level` while preserving the trace recall strategy. If the upper bound only partially supports required assertions, classify unsupported required assertions as partial knowledge missing and recommend adding or rewriting the corresponding KB content. If the upper bound supports a required assertion but online origin recall misses it, classify retrieval. Only classify `rerank_drop` when the required assertion's support is present in online origin recall but lost before/inside rerank.

## Host Agent Flow

1. Normalize pasted text, table rows, curl/body, or user description into `query`, `judgement`, `workspace_id`, `app_id`, `log_id`, optional `expected_knowledge_ids`, nested `host_agent.answer_claim`, optional `qa` answer-state fields, and structured `judgement_evidence.signals`.
   The host Agent should summarize deterministic assertions into `host_agent.answer_claim` only. Do not put assertions into `case_input.expected_knowledge_points`, `qa.answer_claims`, `qa.missing_expected_points`, `qa.unsupported_claims`, `qa.claim_alignments`, or `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`.
   `source` may be omitted; the CLI normalizes it to `host_agent.answer_claim`. `role` carries the semantic difference between required facts, missing facts, ordinary answer claims, and unsupported claims.

Assertion input example:

```json
{
  "host_agent": {
    "answer_claim": [
      {
        "text": "正确答案应覆盖的事实断言",
        "role": "expected_required",
        "confidence": 0.9
      }
    ]
  },
  "qa": {
    "prompt_supports_answer": true,
    "answer_satisfies_expected": false
  }
}
```
2. Run ingest:

```bash
python -m findreason ingest-fornax-trace \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

3. Read `ingest_summary.suggested_probe_set` and `host_action_required`. Run only the recommended probes, unless the user explicitly asks for a focused branch. `probe-self-oracle` is recommended first by default.
   `probe-wide-recall --topk 50` is recommended with self-oracle and should use the trace Sirius recall body as an open-label theoretical recall upper bound.
4. Run final arbitration:

```bash
python -m findreason orchestrate \
  --ingest-file /tmp/findreason-case/ingest.json \
  --probe-dir /tmp/findreason-case/probes \
  --mode final \
  --schema-version v3 \
  --output-dir /tmp/findreason-case/final
```

5. Write the human report from the JSON output. Include `span_type=workflow` input/output from `raw_artifacts.workflow_span_ios` when available.

## Commands

Skeleton commands:

```bash
python -m findreason ingest-fornax-trace --workspace-id 89 --log-id 20260601191946A85794168A7D7BF20EB0 --limit 1000
python -m findreason orchestrate --ingest-file /tmp/findreason-case/ingest.json --probe-dir /tmp/findreason-case/probes
```

Probe commands:

```bash
python -m findreason probe-self-oracle --ingest-file /tmp/findreason-case/ingest.json --signals judgement_back_recall,claim_back_recall,query_wide_recall --topk 50 --output-dir /tmp/findreason-case/probes
python -m findreason probe-knowledge-detail --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-permission-check --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-wide-recall --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-rerank-bypass --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-rerank-tune --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-context-assembly --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-judgement --ingest-file /tmp/findreason-case/ingest.json --judgement "评估器失败项：事实正确性=否" --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-claim --ingest-file /tmp/findreason-case/ingest.json --claims @claims.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-doc-title --ingest-file /tmp/findreason-case/ingest.json --titles @titles.json --output-dir /tmp/findreason-case/probes
```

Workflow commands:

```bash
python -m findreason fetch-workflow-nodes --workspace-id <workspace_id> --app-id <app_id>
python -m findreason replay-workflow --ingest-file /tmp/findreason-case/ingest.json --override @override.json
```

Raw trace only:

```bash
python -m findreason ingest-fornax-trace --workspace-id <workspace_id> --log-id <log_id> --raw
```

Schema discovery:

```bash
python -m findreason schema
```

## Output Contract

`ingest-fornax-trace` emits `schema_version`, `log_id`, `workspace_id`, `app_id`, `case`, `ingest_summary`, and `raw_artifacts`. `ingest_summary` includes `trace_completeness`, `suggested_probe_set`, `skip_reason`, and `host_action_required`.

`orchestrate` emits `schema_version`, `oracle_status`, `case_assessment`, `primary_cause` object or `null`, `failure_patterns`, `needs_human_review`, `human_review_reasons`, `evidence_bundle`, `evidence_chain`, `next_actions`, `telemetry`, `deprecations`, and `raw_artifacts`. `oracle_status` may include `expected_knowledge_points`, `point_coverage`, `missing_expected_points_from_origin`, `missing_expected_points_from_rerank`, and `missing_expected_points_from_prompt`.

The CLI writes one case-local record: `attribution_record.json` plus `short_summary.json` when orchestrate uses `--output-dir`. It does not generate batch `summary.md`, `summary.csv`, or `summary.json`.

## References

- `references/cause-codes.md`: v3 cause enum, owners, trigger conditions, and boundaries.
- `references/probe-spec.md`: probe inputs, outputs, cache, and failure semantics.
- `references/orchestrator-rules.md`: counterfactual and primary-cause selection rules.
- `references/workflow-ops.md`: workflow node fetch and replay behavior.
- `references/span-extraction.md`: Fornax span extraction mapping.
- `references/evidence-spec.md`: evidence bundle schema and validation.
- `references/output-schema.json`: v3 output schema for host-side validation.
- `references/host_agent_playbook.md`: host Agent responsibilities and report composition guidance.
- `references/capabilities.json`: v3 capability manifest.
