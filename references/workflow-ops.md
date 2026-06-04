# Workflow 操作说明

`fetch-workflow-nodes` 读取 `applications_wip` 中 `status = 1` 的最新已发布 workflow 配置，按最新 `id` 排序。

命令返回：

- `workflow.nodes`
- `workflow.edges`
- `workflow.global_config`
- `workflow.input_schema`
- `wip_id`、`version_id` 和状态元数据

当 trace spans 需要映射到应用特定 workflow 节点，或 replay 与历史 trace 出现差异时，使用该命令。

`replay-workflow` 只是兜底手段。如果 `ingest-fornax-trace` 已找到 `Start`、`End`、`ZhiShangRAGRecall`、`ZhiShangRAGRerank`、`ZhiShangRAGQA` 等中间节点证据，不要 replay，也不要覆盖 trace 证据。
