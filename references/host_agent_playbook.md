# 宿主 Agent 操作手册 v3

宿主 Agent 负责编排和语言理解。CLI 负责确定性的证据采集、probe 归一化和反事实归因。

## 宿主职责

- 从粘贴文本、curl/body 片段或表格行中抽取 `query`、`judgement`、`workspace_id`、`app_id`、`log_id`、`case_id/source_row`，以及强暗示的 `expected_knowledge_ids`。
- 运行 probes 前，先生成断言式 `host_agent.answer_claim`。这是唯一的宿主断言输入，必须使用嵌套结构 `{"host_agent": {"answer_claim": [...]}}`。每项应包含 `text`、`role`，可选 `source` 和 `confidence`；合法 role 包括 `expected_required`、`missing_expected`、`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`。CLI 会把 `source` 归一化为 `host_agent.answer_claim`。
- 只有 `expected_required` 和 `missing_expected` 用于判断 knowledge、retrieval、rerank、context 缺口。`unsupported_claim` 只作为 answer 阶段证据。
- 如果 ingest 返回 `host_action_required[].action=generate-probe-plan`，先使用 probe-v1 提示词从用户问题和答案中构造 probes。把必要的 `expected_required` / `missing_expected` probes 复制到 `host_agent.answer_claim`，用更新后的 case 文件重新运行 ingest，再执行 `run-probe-plan`，最后再 `orchestrate`。
- 不要从 query 文本、评估器维度、通过/失败标签、空回复诊断或任意 rubric / judgement 长片段中创建期望断言。
- 不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`；旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- 将较长的 grader / rubric 输出压缩为 2KB 内的 `judgement_evidence.signals`。评估器标签只作为观察项，不作为 trace 证据。
- 将 unsupported claims 抽到 `host_agent.answer_claim`，使用 `role=unsupported_claim`；`wrong_citations` 和答案前置条件字段单独保留。
- 读取 `ingest_summary.suggested_probe_set`，除非用户要求特定分支，否则只运行推荐 probes。
- 从 `orchestrate` JSON 渲染最终报告，并显式展示 `needs_human_review` 原因。
- 如果没有 `expected_required` / `missing_expected` 断言，应预期 `oracle_status.source=insufficient_assertions`；若需要上游归因，补充断言后重跑。

## 归因流程

1. 运行 `python -m findreason ingest-fornax-trace --workspace-id <ws> --log-id <log> --case-file <case.json> --output-dir <case-dir>`。
2. 检查 `ingest_summary.trace_completeness`、`suggested_probe_set` 和 `host_action_required`。
3. 如果需要 `generate-probe-plan`，生成 `plan.json`，用 `host_agent.answer_claim` 更新 `case.json`，重新 ingest，然后执行 `run-probe-plan`。
4. 将推荐 probes 运行到 `<case-dir>/probes/`。除 `replay-workflow` 外，probe 命令可以并行。
5. 运行 `python -m findreason orchestrate --ingest-file <case-dir>/ingest.json --probe-dir <case-dir>/probes --mode final --schema-version v3 --output-dir <case-dir>/final`。
6. 用 `primary_cause`、`evidence_chain`、`failure_patterns`、`next_actions` 和 `raw_artifacts.workflow_span_ios` 撰写面向用户的报告。

## 证据优先级

原始 Fornax 中间节点 trace 证据最权威。如果 trace 包含 `Start`、`End`、`ZhiShangRAGRecall`、`ZhiShangRAGRerank` 或 `ZhiShangRAGQA`，不要 replay workflow，也不要覆盖 `origin_doc_list`、`rerank_docs`、`prompt_docs` 或 `answer`。

如果 trace 查询失败或缺少中间节点证据，运行 `fetch-workflow-nodes` 后再运行 `replay-workflow`。当 replay 与历史 trace 不一致时，replay 证据质量标记为更低。

## 报告检查清单

- Case 标识：`log_id`、`workspace_id`、`app_id`、`case_id/source_row`。
- Trace 摘要：节点完整性、origin/rerank/prompt 数量、workflow span 输入/输出。
- 主因：stage、cause_code、confidence、owner、rationale。
- 断言覆盖矩阵：断言文本、role、source、线上初召回、rerank、prompt；只有带 `full_support` / `partial_support` 正文片段的文档才计入，并在单独的“理论召回上界与断言关系”小节列出支撑片段。
- 证据链：带 counterfactual 和 `upstream_blocked_by` 的阶段 verdict。
- Failure patterns 和 next actions。
- 当 `needs_human_review=true` 时，报告中必须明确提示人工复核。
