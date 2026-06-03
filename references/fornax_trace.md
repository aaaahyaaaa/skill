# Fornax Trace v3

`ingest-fornax-trace` calls the OpenPlat trace detail API directly:

```http
POST http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail
Content-Type: application/json
x-zs-plt-open: zs_open
Authorization: Bearer <OPEN_PLAT_TRACE_TOKEN>
```

Request body:

```json
{"workspaceId": 89, "logId": "20260601191946A85794168A7D7BF20EB0", "limit": 1000}
```

Response shape:

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "spans": [],
    "TracesAdvanceInfo": {},
    "has_more": false,
    "next_page_token": ""
  }
}
```

The ingest command extracts:

- root span `custom_tags.zhishang.app_id` when `--app-id` is missing
- `rewrite_query`
- `origin_doc_list`, `origin_faq_list`
- `rerank_docs`
- `prompt_docs`
- final answer
- node order and workflow span input/output
- trace token cost metadata when present

If trace lookup fails or lacks middle-node evidence, `ingest_summary.host_action_required` recommends `replay-workflow`. Replay is fallback evidence only.
