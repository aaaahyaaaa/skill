# Wide Recall Notes v3

`probe-wide-recall` uses the real Sirius recall request found in the Fornax trace as its template. It calls `https://ad-sirius.bytedance.net/api/sirius_plugin/v1/recall` with original query + rewrite query, `topK >= 50`, and `upper_bound_scope=open_label`.

Open-label means:

- keep the trace request's `recallStrategy`, `name`, `isPrivateDoc`, `contentMaxSize`, `params.workspaceId`, and `keyWordInfo`
- set each recall request's `recallLabels=[]` and `level=[]`
- set `maxCount=max(50, original maxCount)`
- lower threshold-like params (`score`, `精选`, `内容中台`, `min_score`) to `0`

The probe gets the workspace apiKey from `get-workspace-info?workspaceId=<id>` using `OPEN_PLAT_TRACE_TOKEN`/`OPEN_PLAT_BOOTSTRAP_TOKEN` as the bootstrap token. The apiKey is only used in memory and must not be written to reports or JSON.

The probe output should be interpreted as:

- expected knowledge point is not covered by open-label wide recall -> supports partial `suspected_knowledge_missing` for that point
- expected knowledge point appears in open-label wide recall but not online origin recall -> supports `retrieval_miss`
- expected knowledge point appears in online origin recall but not rerank -> supports `rerank_drop`
- expected knowledge point appears in rerank but not prompt docs -> supports `context_assembly_error`
- no expected doc and knowledge existence is unknown -> do not infer knowledge absence; require knowledge detail or human review

Wide recall failure alone is never proof of KB absence. If the trace lacks a Sirius recall request template, the probe returns `not_configured`; workflow replay remains a separate fallback only when trace evidence is missing or trace lookup fails.
