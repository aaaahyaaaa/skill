# 宿主 Agent 操作手册 v3

宿主 Agent 负责编排和语言理解。CLI 负责确定性的证据采集、probe 归一化和反事实归因。

## 宿主职责

- 从粘贴文本、curl/body 片段或表格行中抽取 `judgement`、`workspace_id`、`app_id`、`log_id`、`case_id/source_row`，以及强暗示的 `expected_knowledge_ids`。外部 `query` / `answer` 只能作为用户实际问题/评估问题线索或 `query_hint` / `answer_hint`；Workflow 原始输入/输出必须优先来自 trace artifacts。
- 将较长的 grader / rubric 输出压缩为 2KB 内的 `judgement_evidence.signals`。评估器只说明“怀疑哪里坏了”，不是 trace 证据，也不能直接决定主因。
- 运行 probes 前，先执行 Agent attribution planning：消费 trace artifacts 和 `judgement_evidence.signals`，同时生成 assertion set 与 `probe-v1` plan。详细规则见 `references/agent_attribution_planning.md`。
- `host_agent.answer_claim` 是向后兼容字段，语义上表示 assertion set，必须使用嵌套结构 `{"host_agent": {"answer_claim": [...]}}`。核心 role 是 `expected_required` 和 `answer_claim`；`missing_expected` 只作为 legacy 输入，归一化为 `expected_required`。
- `expected_required` 用于判断 knowledge、retrieval、rerank、context 缺口。CLI 会保守合并“场景约束 + 同一入口/路径要求”的包含/细化断言，`basis` 取并集，原始断言保存在 `merged_from`，覆盖矩阵只看合并后的行。`answer_claim` 是从 workflow output 中抽取出的可验证命题 X，用于 grounding、scope、citation 和 consistency 检查；文本不要写成“答案称 X”。
- 归因前先看输入边界：如果用户实际问题或评估器用户上下文包含关键场景约束，但 `raw_artifacts.workflow_span_ios[].input` 中的 Workflow 原始输入已丢失，先记录输入边界风险；只有受影响的 `expected_required` 在理论召回上界可支撑、但线上初召回缺失时，主因才停在 `workflow_input_loss`。如果正确断言已经进入 online origin / rerank / prompt，不能把该 badcase 归因为输入丢失，应继续判断 rerank、context 或 answer。
- 如果 ingest 返回 `host_action_required[].action=generate-probe-plan`，按照 Agent planning playbook 构造 `probe-v1` plan，并把 `expected_required` / `answer_claim` 写入 `host_agent.answer_claim`。用更新后的 case 文件重新运行 ingest，再执行 `run-probe-plan`，最后再 `orchestrate`。
- 不要从 query 文本、评估器维度、通过/失败标签、空回复诊断或任意 rubric / judgement 长片段中让 CLI 创建期望断言；这一步必须由宿主 Agent 显式完成。
- 不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`；旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- 将 unsupported claims 抽到 assertion set；`wrong_citations` 和答案前置条件字段单独保留。
- 读取 `ingest_summary.suggested_probe_set`，除非用户要求特定分支，否则只运行推荐 probes。
- 从 `orchestrate` JSON 渲染最终报告，并显式展示 `needs_human_review` 原因。
- 如果没有 `expected_required` 断言，应预期 `oracle_status.source=insufficient_assertions`；若需要上游归因，补充 assertion set 后重跑。

## 归因流程

1. 运行 `python -m findreason ingest-fornax-trace --workspace-id <ws> --log-id <log> --case-file <case.json> --output-dir <case-dir>`。
2. 检查 `ingest_summary.trace_completeness`、`suggested_probe_set` 和 `host_action_required`。
3. 如果需要 `generate-probe-plan`，用 trace artifacts + evaluator signals 生成 assertion set 和 `plan.json`，用 `host_agent.answer_claim` 更新 `case.json`，重新 ingest，然后执行 `run-probe-plan`。
4. 将推荐 probes 运行到 `<case-dir>/probes/`。除 `replay-workflow` 外，probe 命令可以并行。
5. 运行 `python -m findreason orchestrate --ingest-file <case-dir>/ingest.json --probe-dir <case-dir>/probes --mode final --schema-version v3 --output-dir <case-dir>/final`。
6. 用 `primary_cause`、`evidence_chain`、`failure_patterns`、`next_actions` 和 `raw_artifacts.workflow_span_ios` 撰写面向用户的报告；报告保留 workflow input/output 摘录，完整原文放在 artifact。

## 证据优先级

原始 Fornax 中间节点 trace 证据最权威。真实 query、rewrite query、keywords、workflow output、召回、重排、prompt/context、引用和脚本 I/O 优先来自 trace artifacts。如果 trace 包含 `Start`、`End`、`ZhiShangRAGRecall`、`ZhiShangRAGRerank` 或 `ZhiShangRAGQA`，不要 replay workflow，也不要覆盖 `origin_doc_list`、`rerank_docs`、`prompt_docs` 或 `answer`。

如果 trace 查询失败或缺少中间节点证据，运行 `fetch-workflow-nodes` 后再运行 `replay-workflow`。当 replay 与历史 trace 不一致时，replay 证据质量标记为更低。

## 报告检查清单

- Case 标识：`log_id`、`workspace_id`、`app_id`、`case_id/source_row`。
- Trace 摘要：节点完整性、origin/rerank/prompt 数量、workflow span 输入/输出摘录。
- 主因：stage、cause_code、confidence、owner、rationale。
- 断言覆盖矩阵：断言文本、role、source、线上初召回、rerank、prompt；只展示去重后的 `expected_required`，`merged_from` 只作审计；只有带 `full_support` / `partial_support` 正文片段的文档才计入，并在单独的“理论召回上界与断言关系”小节列出支撑片段。
- 证据链：带 counterfactual 和 `upstream_blocked_by` 的阶段 verdict。
- Failure patterns 和 next actions。
- 当 `needs_human_review=true` 时，报告中必须明确提示人工复核。
