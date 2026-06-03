# Retrieval Notes v3

Retrieval-stage causes are:

- `retrieval_miss`
- `permission_miss`

Do not use retrieval causes when knowledge existence is `no` or `unknown`. In those cases, retrieval must set `upstream_blocked_by=knowledge` or remain indeterminate until `probe-knowledge-detail` resolves the tri-state.

Useful evidence:

- `origin_doc_list`
- `origin_faq_list`
- `expected_knowledge_hit`
- `online_retrieval_hit`
- `probe-wide-recall` matches
- ACL/namespace signals from `probe-permission-check`
