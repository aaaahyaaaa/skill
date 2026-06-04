# 知识详情（knowledge_detail）

## 开发探针

使用本 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:knowledge_detail:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

按数字型知识 ID 补全文档 title/content，形成 reference evidence。

## 输出契约

- `status`：`ok`、`partial`、`not_needed` 或 `error`。
- `requested_ids`、`hydrated_docs`、`expected_knowledge_docs`、`matched_expected_ids`、`missing_ids`、`skipped_ids`。

## 必须遵守

- 详情补全能证明期望知识存在，但不能证明线上召回命中。
- 详情接口失败不能推出知识缺失。
