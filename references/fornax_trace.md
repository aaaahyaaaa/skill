# Fornax Trace 说明 v3

`ingest-fornax-trace` 会直接调用 OpenPlat trace detail API：

```http
POST http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail
Content-Type: application/json
x-zs-plt-open: zs_open
Authorization: Bearer <OPEN_PLAT_ZS_OPEN_TOKEN>
```

请求体：

```json
{"workspaceId": 89, "logId": "20260601191946A85794168A7D7BF20EB0", "limit": 1000}
```

响应结构：

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

Ingest 命令会抽取：

- 当 `--app-id` 缺失时，从根 span 的 `custom_tags.zhishang.app_id` 获取 app_id fallback
- `rewrite_query`
- `origin_doc_list`、`origin_faq_list`
- `rerank_docs`
- `prompt_docs`
- 最终 answer
- 节点顺序和 workflow span 输入 / 输出
- 可用时的 trace token cost metadata

如果 trace 查询失败或缺少中间节点证据，`ingest_summary.host_action_required` 会建议 `replay-workflow`。Replay 只作为 fallback 证据。
