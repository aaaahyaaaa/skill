# knowledge_detail

## Dev Probe

When this skill is used, expose `SKILL_PROBE_USED:knowledge_detail:dev` with the captured `skill_input` and `skill_output` in the Case Execution Observer.

## 职责

按数字型知识 ID 补全文档 title/content，形成 reference evidence。

## 输出契约

- `status`：`ok`、`partial`、`not_needed` 或 `error`。
- `requested_ids`、`hydrated_docs`、`expected_knowledge_docs`、`matched_expected_ids`、`missing_ids`、`skipped_ids`。

## 必须遵守

- 详情补全能证明期望知识存在，但不能证明线上召回命中。
- 详情接口失败不能推出知识缺失。
