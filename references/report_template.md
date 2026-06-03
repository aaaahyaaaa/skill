# FindReason v3 Case Summary Template

Use this template after `orchestrate` finishes. Codex or any other host Agent should render this human-readable report from the v3 JSON output. The CLI also writes `case_report.md` whenever `orchestrate --output-dir` is provided, and includes the same text in `human_report_markdown`.

## 1. 结论

- `primary_stage`
- `primary_cause`
- confidence
- owner
- `needs_human_review`
- one-sentence root-cause summary

## 2. Case 信息

- `log_id`
- `workspace_id`
- `app_id`
- query
- judgement / evaluation signal if available
- whether trace evidence or replay evidence was used

## 3. 原始 Workflow 输入输出

- workflow span id / node id
- original workflow input as JSON
- original workflow output as JSON

## 4. 证据概览

- trace status and whether middle-node spans are authoritative
- `origin_doc_list` / `origin_faq_list` / `rerank_docs` / `prompt_docs` counts
- expected doc IDs and where they appeared
- assertion coverage matrix for online stages only: initial recall, rerank, and prompt; include assertion role and source
- theoretical upper-bound recall to assertion relationship: for each required assertion, list the supporting upper-bound doc IDs/titles plus matched terms and scores when available
- theoretical upper-bound recall status, topK, and query variants
- exact doc IDs/titles that were initially recalled but dropped by rerank when the primary cause is `rerank_drop`
- required assertions with no supporting upper-bound or initial-recall doc; recommend adding or rewriting corresponding KB content
- unsupported claims as answer-stage observations, not as KB补充项
- workflow span input/output availability

## 5. 归因链路

| 阶段 | 结论 | 关键依据 |
|---|---|---|
| preprocess | pass/fail/indeterminate | rewrite, route, keyword evidence |
| knowledge | pass/fail/indeterminate | knowledge exists yes/no/unknown |
| retrieval | pass/fail/indeterminate | online recall, wide recall, permission evidence |
| rerank | pass/fail/indeterminate | recall -> rerank survival |
| context | pass/fail/indeterminate | rerank -> prompt survival, truncation/noise |
| answer | pass/fail/indeterminate | prompt support, unsupported claims, citations |
| evaluation | observation-only | judgement/rubric notes |

## 6. 主因解释

- Why the primary stage failed.
- Why earlier stages are not primary.
- Which later stages are blocked downstream symptoms.
- Counterfactual: what would change if the primary stage were fixed.

## 7. 修改建议

- owner
- P0 action
- optional P1/P2 follow-up checks

## 8. 附件

Link local artifacts:

- `ingest.json`
- `probes/*.json`
- `final/attribution_record.json`
- `final/short_summary.json`
- `final/case_report.md`
