# 召回说明 v3

Retrieval 阶段的原因包括：

- `retrieval_miss`
- `permission_miss`

当知识存在性为 `no` 或 `unknown` 时，不要使用 retrieval 类原因。此时 retrieval 必须设置 `upstream_blocked_by=knowledge`，或在 `probe-knowledge-detail` 解析三态前保持不确定。

可用证据：

- `origin_doc_list`
- `origin_faq_list`
- `expected_knowledge_hit`
- `online_retrieval_hit`
- `probe-wide-recall` 命中结果
- 来自 `probe-permission-check` 的 ACL / namespace 信号
