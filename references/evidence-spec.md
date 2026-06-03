# Evidence Spec

Every `orchestrate` output contains `evidence_bundle` and `evidence_chain`.

Evidence record fields:

- `evidence_id`
- `evidence_type`
- `source_stage`
- `source`
- `content`
- `relation_to_query`
- `relation_to_answer`
- `quality`

Validation rules:

1. Every failed verdict with `candidate_cause` must bind at least one `evidence_id`.
2. Every verdict `evidence_ids` entry must exist in `evidence_bundle`.
3. Every `counterfactual.evidence_ids` entry must exist in `evidence_bundle`.
4. Every verdict must include `counterfactual`.
5. `candidate_cause` must be in the v3 enum.

If validation fails, the CLI returns one of:

- `E_CAUSE_NOT_IN_ENUM`
- `E_COUNTERFACTUAL_MISSING`
- `E_EVIDENCE_NOT_BOUND`
- `E_EVIDENCE_ID_INVALID`
