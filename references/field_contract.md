# 字段契约 v3

## `ingest` 输入

`ingest-fornax-trace` 必填：

- `workspace_id`
- `log_id`

可通过 `--case-file` 提供的宿主字段：

- `query` / `query_hint`
- `judgement`
- `app_id`
- `case_id`
- `source_row`
- `expected_knowledge_ids`
- `judgement_evidence.signals`
- `wrong_citations`
- `host_agent.answer_claim`
- `answer` / `answer_hint`
- `qa.prompt_supports_answer`
- `qa.answer_satisfies_expected`

`judgement_evidence.signals` 序列化后必须小于等于 2KB。

外部 `query` / `answer` 只作为 hint。`case_input.query` 优先表示用户实际问题或评估器问题线索，不得被当作 Workflow 原始输入；真实 Workflow 原始输入/输出以 `raw_artifacts.workflow_span_ios[].input/output` 为权威。`rewrite_query`、`keywords` 是 Workflow 内部预处理节点输出，用于判断预处理是否丢语义，不能覆盖 Workflow 原始输入。

当用户实际问题或评估器用户上下文线索与 Workflow 原始输入存在关键约束差异时，orchestrate 应先记录输入边界风险，但不能直接输出 `workflow_input_loss`。只有受影响的 `expected_required` 已绑定到 `point_coverage`，且该断言在理论召回上界可支撑、online origin recall 缺失时，才能把该风险升级为 `workflow_input_loss` 主因。如果同一断言已被 online origin / rerank / prompt 支撑，说明输入差异没有切断正确证据链，应继续比较 `rewrite_query` / `keywords` 或下游阶段。

字段契约只保留最新约定字段。不得为旧字段、旧 role、旧 env var 或旧 schema alias 做自动映射；外部输入出现过时断言字段时应 fail fast，并要求宿主 Agent 按当前契约重写。

`host_agent.answer_claim` 是宿主 Agent 产出的唯一 assertion set 输入字段，必须使用嵌套 JSON 结构 `{"host_agent": {"answer_claim": [...]}}`。每项必须包含当前字段 `text`、`role`，可选 `basis`、`why_required`、`source` 与 `confidence`；合并输出可包含 `merged_from`。核心 role 是 `expected_required` 和 `answer_claim`。CLI 会把 `source` 归一化为 `host_agent.answer_claim`。

`expected_required` 表示模型基于 trace query、chat_history、评估器 reason、rewrite query、keywords 等上下文推断出的正确输出应覆盖检查点。它驱动 knowledge、retrieval、rerank、context 归因，但本身不是事实证据。CLI 会在覆盖计算前对 `expected_required` 做保守语义去重：只有“场景约束 + 同一入口/路径要求”的包含或细化关系才合并，合并行的 `basis` 为原断言并集，`merged_from` 保留原始断言，原始断言不再重复进入 `point_coverage`。`answer_claim` 表示 workflow output 中抽取出的可验证命题 X，文本不得写成“答案称 X”。`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check` 用于答案、引用、一致性和范围检查，不直接作为上游必要事实断点。

评估器维度、通过/失败标签、空回复诊断、query 文本、非结构化 rubric / judgement 片段都只是观察项，不得直接决定主因。宿主 Agent 可以读取这些线索来生成 assertion set 和 `probe-v1` plan。

不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`。旧断言字段非空会报 `E_LEGACY_ASSERTION_INPUT`。

没有 `expected_required` 断言时，输出 `oracle_status.source=insufficient_assertions`，并要求宿主 Agent 补充 assertion set 后再判断上游阶段。

## `ingest` 输出

必需顶层字段：

- `schema_version`
- `log_id`
- `workspace_id`
- `app_id`
- `case`
- `ingest_summary`
- `raw_artifacts`

`ingest_summary` 包含 `trace_completeness`、`suggested_probe_set`、`skip_reason` 和 `host_action_required`。

## `orchestrate` 输出

必需顶层字段：

- `schema_version`
- `log_id`
- `workspace_id`
- `primary_cause`
- `oracle_status`
- `case_assessment`
- `failure_patterns`
- `needs_human_review`
- `human_review_reasons`
- `evidence_bundle`
- `evidence_chain`
- `next_actions`
- `telemetry`
- `deprecations`
- `raw_artifacts`

`primary_cause` 要么是包含 `stage`、`cause_code`、`confidence`、`owner`、`selection_rationale` 的对象，要么在归因被阻塞时为 `null`。
