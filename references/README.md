# FindReason Skill Specs

这个目录保存给宿主 Agent 和人阅读的简洁说明。可执行规则源头是 `backend/app/diagnostics.py` 里的 typed diagnostic registry。

## 输出契约

- 工具型步骤产出 `evidence_chain`：输入适配、pipeline replay、Sirius open-label wide recall、knowledge detail、reference evidence。
- 诊断型步骤产出 `diagnostic_results`：每个结果绑定 `spec_id`、`matched_rule_id`、`candidate_cause`、证据要求和实际 evidence。
- 仲裁步骤产出 `arbitration`：`immediate_failure`、`primary_cause`、`causal_path`、置信度和下一步动作。
- 原始 badcase 复现优先使用 OpenPlat trace detail 导出的 trace，再用 `ingest-fornax-trace` 转为标准 `AttributionRequest`；有中间节点证据时不执行 workflow replay，避免当前 workflow 版本漂移。
- `host_agent.answer_claim` 是宿主 Agent 唯一的断言输入，使用 `{"host_agent": {"answer_claim": [...]}}` 嵌套结构，包含 `text/role/source/confidence`；`source` 会统一归一化为 `host_agent.answer_claim`，禁止由 query、评价标签、空回复诊断或 rubric 长句碎片兜底生成，也不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points` 或 `judgement_evidence.signals[].assertions`，旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- `probe-wide-recall` 使用 trace 中真实 Sirius recall 请求作为模板，清空标签/层级并以 topK >= 50 执行原 query + 改写 query，用于判断必要断言在知识库上界、线上初召回、重排和 Prompt 之间的断点。

## 必须遵守

- Markdown 不是规则实现，不允许和 registry 写出两套口径。
- 新增或修改根因时，先改 `diagnostics.py`，再同步这里的说明。
