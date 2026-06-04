# Pipeline 复放（pipeline_replay）

## 开发探针

使用此 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:pipeline_replay:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

复放线上 workflow，抽取运行级 trace evidence。

## 输出契约

- `status`：`ok`、`partial`、`not_configured` 或 `error`。
- `request_payload`、`endpoint`、`extracted_evidence`、`error`。
- `extracted_evidence` 可包含 `answer`、`origin_doc_list`、`origin_faq_list`、`rerank_docs`、`prompt_docs`、`workflow_output_doc_list`、`workflow_output_faq_list`、`trace_completeness`。

## 必须遵守

- replay 失败只能降低证据质量，不能推出知识不存在。
- 下游诊断优先消费抽取后的 evidence，而不是原始响应。
- `workflow_output_*` 只是 workflow 输出样本，不能当作线上 `origin_*`、`rerank_docs` 或 `prompt_docs`。
