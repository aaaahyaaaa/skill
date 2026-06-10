# 宽召回说明 v3

`probe-wide-recall` 使用 Fornax trace 中真实的 Sirius recall 请求作为模板。它会用原 query 和 rewrite query 调用 `https://ad-sirius.bytedance.net/api/sirius_plugin/v1/recall`，并设置 `topK >= 50` 与 `upper_bound_scope=open_label`。

`open_label` 表示：

- 保留 trace 请求中的 `recallStrategy`、`name`、`isPrivateDoc`、`contentMaxSize`、`params.workspaceId` 和 `keyWordInfo`
- 将每个 recall 请求的 `recallLabels=[]`、`level=[]`
- 设置 `maxCount=max(50, original maxCount)`
- 将阈值类参数（`score`、`精选`、`内容中台`、`min_score`）降为 `0`

`retrieval-exp` 通过固定 workspace info endpoint 的 `get-workspace-info?workspaceId=<id>` 获取当前 workspace 的 `authInfo.apiKey`，并只使用源码固定 OpenPlat token 作为 bootstrap token。apiKey 只在内存中使用，不能写入报告或 JSON；获取失败时不得 fallback 到跨空间 token。

探针输出的解释方式：

- 期望知识点在 open-label 宽召回中仍无法覆盖：支持该知识点的局部 `suspected_knowledge_missing`
- 期望知识点出现在 open-label 宽召回中，但不在线上 origin recall 中：支持 `retrieval_miss`
- 必要断言在 origin recall 中有可回答支撑，但同一断言不在 rerank 中：支持 `rerank_drop`
- 仅有文档 ID 出现在 origin recall、未进入 rerank：记录为观察，不单独归因到 rerank。
- 期望知识点出现在 rerank 中，但不在 prompt docs 中：记录为 prompt/context 观察，不再产生顶层主因
- 没有 expected doc 且知识存在性未知：不要推断知识不存在；需要知识详情或人工复核

宽召回失败本身不是知识库缺失的证明。如果 trace 缺少 Sirius recall 请求模板，探针返回 `not_configured`；workflow replay 仍是单独兜底，只在 trace 证据缺失或 trace 查询失败时使用。
