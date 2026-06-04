# 编排器（orchestrator）

## 开发探针

使用本 skill 时，在 Case Execution Observer 中暴露 `SKILL_PROBE_USED:orchestrator:dev`，并记录捕获到的 `skill_input` 和 `skill_output`。

## 职责

合并 `diagnostic_results`，输出最终 arbitration。

## 输出契约

- `immediate_failure`：最靠近下游的可观察失败症状。
- `primary_cause`：按 `preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation` 选择的最上游失败根因。
- `causal_path`、`secondary_causes`、`confidence_breakdown`、`need_human_review`。

## 必须遵守

- 上游 stage 有失败时主因永远落在更上游；只有上游全部 passed，才能停在 answer 或 evaluation。
- 工具型 evidence step 缺失不能直接成为产品根因。
