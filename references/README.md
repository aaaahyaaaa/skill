# FindReason References

这个目录保存 FindReason v3 的宿主 Agent playbook、CLI 契约和证据裁决说明。当前主线是：

```text
trace hydration
  -> evaluator compression
  -> Agent attribution planning
  -> CLI/probe evidence execution
  -> orchestrate
```

## 核心口径

- Trace artifacts 是 workflow 现场的权威来源。外部 `query` / `answer` 只作为用户实际问题/评估问题线索或 hint；Workflow 原始输入/输出必须从 trace 的 `workflow_span_ios` 读取。
- 输入边界先于 RAG 链路归因，但不能只凭 query 差异判主因：只有用户约束未进入 Workflow 原始输入，且受影响的 `expected_required` 在理论召回上界可支撑、线上初召回缺失时，才判 `workflow_input_loss`。如果同一断言已经进入线上 origin / rerank / prompt，输入差异只作为风险信号，继续判断下游。
- 评估器输出只压缩为 `judgement_evidence.signals`，用于说明“怀疑哪里坏了”；它不直接决定 `primary_cause`。
- `host_agent.answer_claim` 是向后兼容字段，语义上表示宿主 Agent 产出的 assertion set。
- 主设计只使用 `expected_required` 和 `answer_claim` 两个核心 role。`missing_expected` 仅作为 legacy 输入映射到 `expected_required`。
- `expected_required` 驱动 knowledge / retrieval / rerank / context 覆盖链路；`answer_claim` 用于 output grounding、scope、citation 和 consistency 检查。
- `probe-v1` plan 是实验计划，不是证据。只有 `run-probe-plan` 执行后的 hit/miss、matched docs、support spans 和 evidence IDs 才能进入 `orchestrate`。

## 当前正式入口

- 正式 CLI 入口是 `scripts/findreason.py`，正式归因实现集中在 `scripts/findreason_core/v3.py`，输出契约以 `schema_version: "v3"` 为准。
- `references/` 是当前唯一文档源。新增 cause、probe、字段契约或报告口径时，先更新 `references/` 与 v3 测试。
- 旧 agent/diagnostics 路径已移除，包括 `agent_graph`、`diagnostics`、`attribution`、旧 `skills/*` wrapper、旧 `skills/specs/*`、未接入 v3 的旧 `wide_recall.py` / `knowledge_detail.py` / `rerank_experiment.py`。
- 需要恢复旧能力时，应先按 v3 语义重新接入 `v3.py`、`references/` 和测试，不能重新引入并行规则源。

## 主要文档

- `agent_attribution_planning.md`：宿主 Agent 如何从 trace artifacts 和评估器信号生成 assertion set 与 `probe-v1` plan。
- `field_contract.md`：case 输入、ingest 输出和 orchestrate 输出字段契约。
- `probe-spec.md`：probe 输出格式、缓存、失败语义和 `run-probe-plan` 契约。
- `cause-codes.md`：v3 cause enum、owner 和边界。
- `orchestrator-rules.md`：counterfactual 与 primary cause 选择规则。
- `host_agent_playbook.md`：端到端宿主 Agent 操作流程。
- `output-schema.json`：orchestrate JSON 输出 schema。
- `capabilities.json`：CLI capability manifest。

## 已移除旧能力

`probe-by-judgement`、`probe-by-claim`、`probe-by-doc-title`、`probe-rerank-tune` 已从 CLI 和 capability manifest 中移除。语义判断、问题拆解和探针规划由宿主 Agent 按 `agent_attribution_planning.md` 完成，并交给 `run-probe-plan` 执行确定性检查。
