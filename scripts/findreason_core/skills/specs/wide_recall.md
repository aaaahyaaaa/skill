# 宽召回（wide_recall）

## 开发探针

使用本 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:wide_recall:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

从 Fornax trace 的 Sirius recall `http_client` 子 span 抽取原始 request body，构建 open-label 诊断宽召回证据。

open-label 构造规则：

- 原 query + rewrite query 双路调用，`topK >= 50`
- 保留 trace 中的 `recallStrategy/name/isPrivateDoc/contentMaxSize/params.workspaceId/keyWordInfo`
- 清空 `recallLabels` 和 `level`
- 将 `score`、`精选`、`内容中台`、`min_score` 等阈值降为 `0`

## 输出契约

- `status`：`ok`、`not_configured` 或 `error`。
- `upper_bound_scope=open_label`、`query_variants`、`theoretical_recall_counts`、`matched_expected_ids`、`wide_recall_docs`、`wide_recall_faqs`。

## 必须遵守

- Wide recall 只扩展诊断证据，不能污染线上 `origin_doc_list` 或 `origin_faq_list`。
- Wide recall 失败不是 `knowledge_missing` 证据。
- workspace apiKey 只能在内存用于 Sirius recall，不能写入报告或 JSON。
