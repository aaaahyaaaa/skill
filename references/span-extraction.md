# Span Extraction

`ingest-fornax-trace` parses OpenPlat trace detail responses shaped as:

```json
{"code": 0, "msg": "...", "data": {"spans": [], "TracesAdvanceInfo": {}, "has_more": false, "next_page_token": ""}}
```

Important span evidence:

| Span | Extracted evidence |
|-|-|
| root span with `parent_id=""` | `custom_tags.zhishang.app_id` fallback |
| `workflow` | full workflow input/output, stored in `raw_artifacts.workflow_span_ios` |
| `Start` | start input and workflow parameter mapping |
| `ZhiShangRAGPreprocess` | `rewrite_query`, keywords, route/model signals |
| `ZhiShangRAGRecall` | `origin_doc_list`, `origin_faq_list` |
| `ZhiShangRAGRerank` | `rerank_docs`, rerank input/request traces |
| `ZhiShangRAGQA` / `End` | `prompt_docs`, final answer |

The trace evidence is historical ground truth. Workflow replay can supplement missing evidence but must not replace middle-node trace evidence.
