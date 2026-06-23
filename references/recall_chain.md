# Recall Chain Reference

这份文档解释 FindReason v4 在报告里说的 `recall` 到底对应线上哪条链路。它的目标是让人和 Agent 都能读懂 trace 字段、线上接口和排查抓手之间的关系。

来源参考：用户整理的召回链路 Wiki `https://bytedance.larkoffice.com/wiki/Zq6vw0Z94il3ZKk3uklceP9EnAd`。

## 读法

FindReason 报告里使用人读名称 `recall`，但审计时必须保留 trace 原始字段：

- `origin_doc_list`：文档召回结果。
- `origin_faq_list`：FAQ / 精选类召回结果。
- `recall`：人读汇总名，等于 `origin_doc_list + origin_faq_list`。
- `rerank_docs`：重排后的候选证据。
- `prompt_docs` / `qaPromptDocs`：最终被放进回答 prompt 的证据。

报告可以把同一个必要断言在 `recall -> rerank -> prompt` 的存活情况写在一起；不同断言分开展示。

## 主链路

线上 RAG QA 主流程可以按下面的语义理解：

```text
用户问题
  -> RecallDoc.run
  -> 构造 RecallAndPostRequest
  -> DocRecallUtils.docRecall
  -> /api/sirius_plugin/v1/searchDoc
  -> RecallController 并行执行 recallRequests
  -> doc_search / self_dataset_search / featured_search
  -> /api/sirius_knowledge/v1/search/doc 或 FAQ 检索
  -> post 策略链、去重、rank
  -> reRankDocs
  -> RagAnswerService.getDocsPrompt
  -> qaPromptDocs
  -> LLM answer
```

注意：插件侧有新的拆分接口 `/api/sirius_plugin/v1/recall` 和 `/api/sirius_plugin/v1/rerank`，但排查线上 RAG QA 现场时，优先以 trace 中实际命中的链路为准。当前常见主链路仍可能是旧 `/searchDoc`。

## RecallAndPostRequest

常见字段含义：

- `oriQuery`：用户原始问题。为空时可能 fallback 到 `query[0]`。
- `query`：历史查询列表，旧逻辑仍可能作为 fallback。
- `keyWordInfo`：关键词和同义词。`self_dataset_search` 会用 `highSynonymsWord` 做 query 别名替换；`doc_search` 会用关键词和同义词构造文本检索 query list。
- `recallRequests`：召回策略列表，每个元素映射到一个策略 bean，可并行执行。
- `businessDocPost` / `fieldDocPost`：旧 `/searchDoc` 中的 post 策略链。
- `params`：上下文参数集合，可能影响标签、密级、caller、user_email、权限码、认知标签和实体增强。
- `context`：旧接口上下文对象，`RecallController` 仍可能从中补 `sceneId/sourceId/recall_label/private_doc_levels/public_doc_levels/department/caller/appId/user_email/auth_codes`。

## RecallStrategy

关键字段：

- `name`：写入 `recallResult` 的 key，也会进入 `CorpusDoc.recallSource`。
- `recallStrategy`：真正决定实现，例如 `doc_search`、`self_dataset_search`、`featured_search`。
- `isPrivateDoc`：是否召回私有知识。
- `maxCount`：每个策略的召回上限；向量链路中对应 VikingDB `topk`。
- `recallLabels`：标签过滤，优先级高于 `params.recall_label`。
- `level`：显式文档密级；非空时优先级最高。
- `contentMaxSize`：chunk merge 后内容长度；向量链路默认常见值是 1200。

## SearchDocRequest

`doc_search` 和 `self_dataset_search` 都会构造知识服务请求，但语义不同：

| 字段 | `doc_search` | `self_dataset_search` |
|-|-|-|
| `type` | `1`，文本检索 | `2`，向量召回 |
| `queryList` | 来自关键词、原 query、同义词 | 来自原 query 和 `highSynonymsWord` 替换后的 query |
| `maxCount` | ES `size` | VikingDB `topk` |
| `labels` | ES filter | VikingDB dsl filter，之后 MySQL 再校验 |
| `permissionLevel` | ES filter | VikingDB filter，之后 MySQL 再校验 |
| `cognitionTagIds` | ES filter + MySQL 校验 | VikingDB filter + MySQL 校验 |
| `authCodes` | ES filter + MySQL 校验 | VikingDB filter + MySQL 校验 |

`self_dataset_search` 的核心过程：

1. 命中 `RecallStrategy.recallStrategy=self_dataset_search`。
2. 用 `keyWordInfo.highSynonymsWord` 扩展 query。
3. 推导标签、密级、权限码、认知标签。
4. 构造 `SearchDocRequest(type=2)`。
5. 用 `lark-encoder` 生成 embedding。
6. 并行调用 VikingDB。
7. 按 item id 去重，保留更高分 chunk。
8. 回填 MySQL `DocRecord`。
9. MySQL 真值层再次校验标签、密级、认知标签和 auth codes。
10. merge chunk，补齐标题、链接、标签、更新时间等展示字段。

## Permission Boundary

`permissionLevel` 常见推导顺序：

1. `RecallStrategy.level` 非空，直接使用。
2. `isPrivateDoc == 1` 时使用 `params.private_doc_levels`。
3. label 以 `内容中台应用-` 开头时使用 `params.public_doc_levels`。
4. 非 API 请求且用户部门属于高权限列表时，公共路径可默认扩到 `L1,L2,L3`。
5. 其他公共知识路径默认 `L1,L2`，或使用 `params.public_doc_levels`。

所以判断 `召回遗漏`（旧 slug: `retrieval_miss`）时，不要只看“知识是否存在”。还要看 trace 里的 label、密级、caller、auth_codes、cognition_tag 是否把正确知识过滤掉。

## Rerank And Prompt

常见 post / rerank 行为：

- `default_unique`：按 `CorpusDoc.id` 去重，合并来源，保留更高 recallScore。
- `levenshtein_unique`：按正文相似度近似去重。
- `lark_rank_v2` / `lark_rank_v3`：模型 rank，失败且 `degrade=true` 时才降级。
- `vikingdb_base_multilingual_rerank`：火山知识库 rerank。
- `reciprocal_rank`：按多个来源排名做 RRF 融合。

最终给 LLM 的不一定是全部 `reRankDocs`。`RagAnswerService.getDocsPrompt` 会按顺序选择文档，并受这些参数影响：

- `maxInputToken`：prompt 文档内容总长度阈值。
- `maxInputSize`：最多进入 prompt 的文档数量。
- `topP`：基于 fineScore 累计截断。
- `timeProcess`：可能把更新时间写入文档内容。

因此，v4 当前不把 prompt/context 作为顶层归因阶段，但 Agent 仍必须把 `qaPromptDocs` 当作证据观察面：如果必要断言存在于 `rerank_docs`，却没有进入 `prompt_docs`，这会影响 answer judgement 的边界和下一步实验。

## 排查抓手

看召回为空：

- 查 `recallStrategy` 是否真的包含 `self_dataset_search` 或 `doc_search`。
- 查 `SearchDocRequest` 里的 `labels`、`permissionLevel`、`cognitionTagIds`、`authCodes` 是否过窄。
- 向量链路继续看 embedding 和 VikingDB 召回指标。

看结果被过滤：

- 查 MySQL 真值兜底过滤。
- 查低质量标题过滤，例如“已废弃”“已过期”“知商测试”等。
- 查租户私有标签，`租户-xx-私有` 只允许同 caller 租户保留。

看重排异常：

- 查 `lark_rank_v3` 是否失败，以及是否配置 `degrade=true`。
- 区分 `levenshtein_unique.params.min_score` 是去重阈值，不是输出过滤阈值。
- 新 rerank 服务中模型精排为空时可能 fallback 到原始召回结果。

看 prompt 文档少：

- 查 `maxInputToken` 是否太小。
- 查 `maxInputSize` 是否限制数量。
- 查 `topP` 和 fineScore 分布，尤其是已选多篇后的累计分数截断。

## 报告写法

推荐按断言组织，而不是按 doc id 零散堆叠：

```markdown
### 必要断言：<expected_required>

- recall：
  - origin_doc_list 命中/未命中哪些同断言支撑。
  - origin_faq_list 命中/未命中哪些同断言支撑。
- rerank：
  - 同断言支撑是否保留，rank/score 如何变化。
- prompt：
  - 同断言支撑是否进入 qaPromptDocs。
- 解释：
  - 如果 recall 已缺失，优先考虑 query/权限/标签/知识边界。
  - 如果 recall 有而 rerank 无，重点看去重、rank、阈值和 post 策略。
  - 如果 rerank 有但 prompt 无，作为观察面说明输入给模型的证据边界。
```
