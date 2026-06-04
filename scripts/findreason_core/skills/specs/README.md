# FindReason Skill 规格

这个目录保存给宿主 Agent 和人阅读的简洁说明。可执行规则源头是 `backend/app/diagnostics.py` 里的 typed diagnostic registry。

## 输出契约

- 工具型步骤产出 `evidence_chain`：输入适配、pipeline replay、wide recall、knowledge detail、reference evidence。
- 诊断型步骤产出 `diagnostic_results`：每个结果绑定 `spec_id`、`matched_rule_id`、`candidate_cause`、证据要求和实际 evidence。
- 仲裁步骤产出 `arbitration`：`immediate_failure`、`primary_cause`、`causal_path`、置信度和下一步动作。

## 必须遵守

- Markdown 不是规则实现，不允许和 registry 写出两套口径。
- 新增或修改根因时，先改 `diagnostics.py`，再同步这里的说明。
