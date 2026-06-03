# Orchestrator Notes v3

The old full-run arbitration has been replaced by `orchestrate`.

`orchestrate` consumes:

- v3 ingest JSON
- zero or more v3 probe JSON files

It emits:

- `primary_cause` object or `null`
- `evidence_bundle`
- `evidence_chain`
- `failure_patterns`
- `needs_human_review`
- `next_actions`

Primary cause selection is defined in `references/orchestrator-rules.md`.
