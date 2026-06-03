# v3 Orchestrator Rules

Stage order is:

```text
preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation
```

For each stage, `orchestrate` emits one verdict containing `stage`, `status`, `evidence_ids`, `counterfactual`, and `upstream_blocked_by`. Failed verdicts also include `candidate_cause`, `confidence`, and `owner`.

Primary cause selection:

1. Walk stages in order.
2. Skip verdicts with `upstream_blocked_by`.
3. Skip `not_probed`.
4. Select the first fail whose counterfactual is available and `downstream_would_change=true`.
5. If an earlier stage is unresolved, do not select a downstream fail.
6. If no valid upstream counterfactual exists, return `primary_cause=null` and `needs_human_review=true`.

Human review is required for preliminary mode, low confidence, unknown knowledge existence after retry, trace incompleteness blocking attribution, replay divergence, contradictory probe evidence, or null primary cause.
