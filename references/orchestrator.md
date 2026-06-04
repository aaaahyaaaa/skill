# 编排器说明 v3

旧的 full-run arbitration 已被 `orchestrate` 替代。

`orchestrate` 消费：

- v3 ingest JSON
- 零个或多个 v3 probe JSON 文件

它输出：

- `primary_cause` 对象或 `null`
- `evidence_bundle`
- `evidence_chain`
- `failure_patterns`
- `needs_human_review`
- `next_actions`

主因选择规则定义在 `references/orchestrator-rules.md`。
