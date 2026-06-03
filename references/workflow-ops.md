# Workflow Ops

`fetch-workflow-nodes` reads the latest published workflow configuration from `applications_wip` where `status = 1`, ordered by newest `id`.

The command returns:

- `workflow.nodes`
- `workflow.edges`
- `workflow.global_config`
- `workflow.input_schema`
- `wip_id`, `version_id`, and status metadata

Use this when trace spans need to be mapped to application-specific workflow nodes or when replay diverges from the historical trace.

`replay-workflow` is a fallback only. If `ingest-fornax-trace` found middle-node evidence such as `Start`, `End`, `ZhiShangRAGRecall`, `ZhiShangRAGRerank`, or `ZhiShangRAGQA`, do not replay and do not overwrite trace evidence.
