# Span 抽取说明

`ingest-fornax-trace` 解析如下 OpenPlat trace detail 返回：

```json
{"code": 0, "msg": "...", "data": {"spans": [], "TracesAdvanceInfo": {}, "has_more": false, "next_page_token": ""}}
```

重要 span 证据：

| Span | 抽取证据 |
|-|-|
| `parent_id=""` 的 root span | `custom_tags.zhishang.app_id` 兜底 |
| `workflow` | 完整 workflow 输入/输出，存入 `raw_artifacts.workflow_span_ios` |
| `Start` | 起始输入与 workflow 参数映射 |
| `ZhiShangRAGPreprocess` | `rewrite_query`、关键词、route/model 信号 |
| `ZhiShangRAGRecall` | `origin_doc_list`、`origin_faq_list` |
| `ZhiShangRAGRerank` | `rerank_docs`、rerank 输入/请求轨迹 |
| `ZhiShangRAGQA` / `End` | `prompt_docs`、最终 answer |

Trace 证据是历史现场事实。Workflow replay 可以补充缺失证据，但不能替代 trace 中已有的中间节点证据。
