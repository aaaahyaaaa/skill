# 宿主 Agent 操作手册 v3

宿主 Agent 负责编排、语言理解和临场判断。CLI 负责确定性的证据采集、probe 归一化和反事实归因。

推荐主线：

```text
trace hydration
  -> 动态诊断 backlog
  -> selected {stage}-exp
  -> orchestrate
  -> report
```

动态诊断 backlog 帮助 Agent 先看清 workflow 现场、记录 hypothesis 并选择验证环节，不直接决定 `primary_cause`；最终结论仍必须来自 assertion set、执行后的 `{stage}-exp`、evidence bundle 和 orchestrate counterfactual。

## 宿主职责

- 从粘贴文本、curl/body 片段或表格行中抽取 `judgement`、`workspace_id`、`app_id`、`log_id`、`case_id/source_row`，以及强暗示的 `expected_knowledge_ids`。外部问题线索写 `query_hint`，外部表格/评估答案写 `answer_hint`；Workflow 原始输入/输出必须优先来自 trace artifacts，ingest 只在 trace 缺少 workflow output 时把 `answer_hint` 规范化到内部 `qa.answer`。
- 将较长的 grader / rubric 输出压缩为 2KB 内的 `judgement_evidence.signals`。评估器只说明“怀疑哪里坏了”，不是 trace 证据，也不能直接决定主因。
- 先读取 trace artifacts 做现场观察，尤其是 workflow input/output、rewrite/keywords、origin/rerank/prompt 文档生存路径、停用或 stale 来源、引用支撑、prompt-vs-answer alignment 和 chunk 冲突风险；每个线索写入动态 backlog。
- 运行验证前，先执行 Agent attribution planning：消费 trace artifacts、动态 backlog 和 `judgement_evidence.signals`，生成 assertion set 与必要 `{stage}-exp`。详细规则见 `references/agent_attribution_planning.md`。
- 字段契约只保留最新约定字段。不要提供旧字段、旧 role 或旧 schema alias；外部输入出现过时断言字段时 CLI 会 fail fast。
- `host_agent.answer_claim` 是唯一 assertion set 输入字段，必须使用嵌套结构 `{"host_agent": {"answer_claim": [...]}}`。每项必须是对象并包含 `text`、`role`。核心 role 是 `expected_required` 和 `answer_claim`。
- `expected_required` 用于判断 knowledge、retrieval、rerank 缺口；prompt/context 生存状态只作为观察。CLI 会保守合并“场景约束 + 同一入口/路径要求”的包含/细化断言，`basis` 取并集，原始断言保存在 `merged_from`，覆盖矩阵只看合并后的行。`answer_claim` 是从 workflow output 中抽取出的可验证命题 X，用于 grounding、scope、citation 和 consistency 检查；文本不要写成“答案称 X”。
- 归因前先看输入边界：如果用户实际问题或评估器用户上下文包含关键场景约束，但 `raw_artifacts.workflow_span_ios[].input` 中的 Workflow 原始输入已丢失，先记录输入边界风险；只有受影响的 `expected_required` 在理论召回上界可支撑、但线上初召回缺失时，主因才停在 `workflow_input_loss`。如果正确断言已经进入 online origin / rerank / prompt，不能把该 badcase 归因为输入丢失，应继续判断 rerank 或 answer。
- 如果 ingest 返回 `host_action_required[].action=generate-probe-plan`，按照 Agent planning playbook 构造 assertion set 和兼容 `probe-v1` 验证计划，并把 `expected_required` / `answer_claim` 写入 `host_agent.answer_claim`。用更新后的 case 文件重新运行 ingest，再执行 `run-probe-plan` 或相关 `{stage}-exp`，最后再 `orchestrate`。
- 不要从 query 文本、评估器维度、通过/失败标签、空回复诊断或任意 rubric / judgement 长片段中让 CLI 创建期望断言；这一步必须由宿主 Agent 显式完成。
- 不要把断言放入 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`；旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- 先抽取答案症状：unsupported claim、wrong citation、missing aspect、scope violation / branching unclear。它们最终进入 `secondary_findings.answer_issue_types`，用于解释答案错在哪里；只有上游 evidence chain 通过时，顶层主因才会落到 `answer_failure`。
- 读取 `ingest_summary.suggested_probe_set`，除非用户要求特定分支，否则只运行推荐或 backlog 证明必要的 `{stage}-exp`。
- 从 `orchestrate` JSON 渲染最终报告，并显式展示 `needs_human_review` 原因。
- 如果没有 `expected_required` 断言，应预期 `oracle_status.source=insufficient_assertions`；若需要上游归因，补充 assertion set 后重跑。

## 现场观察面板

现场观察面板是宿主 Agent 的自由判断空间，用于形成动态诊断 backlog、选择后续 `{stage}-exp` 和报告重点。它不是独立证据类型，不写入 `primary_cause`。

必须观察：

- Workflow 现场：真实输入、输出、Start/End、RAG 节点是否完整，`query_hint` / `answer_hint` 是否只是外部线索。
- 预处理：rewrite query 是否改写、keywords 是否丢掉关键场景约束。
- 文档生存：摊平 `origin -> rerank -> prompt`，记录关键文档、FAQ、chunk 在每阶段是否存活，是否只标题命中但正文不支撑。
- 来源风险：标题或正文包含“停用 / 已升级 / 过期 / deprecated / stale”的文档，重复 chunk，非权威来源，引用链接不可用。
- 答案对齐：prompt 中是否已有关键限制、反例、预算/ROI/适用范围约束；最终答案是否遗漏、弱化、反向表达或引用了不支撑的文档。
- Chunk 冲突：origin/rerank/prompt 中是否存在互相冲突的 chunk、是否影响必要断言、是否有清晰适用前提。

每个 backlog item 至少包含 `trigger_source`、`trigger_observation`、`hypothesis`、`exp_kind`、`target_stage` 和 `expected_evidence`。评估器输入只是可选线索源，不是固定范式。

现场观察后的分支：

- 关键断言在召回链路不清楚：生成 `retrieval-exp`，验证 query/rewrite/keyword/topK/open-label/permission。
- origin 有支撑但 rerank 丢失：生成 `rerank-exp`，验证 bypass、阈值或排序恢复信号。
- rerank 后已有必要支撑但答案未覆盖、错引或越界：生成 `answer-exp` / `citation-exp`。
- 引用文档停用或不支撑 claim：生成 `citation-exp`。
- chunk 内部冲突：生成 `chunk-conflict-exp`。
- trace 缺中间节点：再考虑 `fetch-workflow-nodes` / `replay-workflow`，并标低证据质量。

如果宿主平台支持 subagent，父 Agent 可把互不依赖的 `{stage}-exp` 分配给子 Agent；父 Agent 仍负责 backlog 去重、证据整合和最终主因解释。如果平台不支持 subagent，父 Agent 按同一协议串行执行。

## 归因流程

1. 运行 `python -m findreason ingest-fornax-trace --workspace-id <ws> --log-id <log> --case-file <case.json> --output-dir <case-dir>`。
2. 检查 `ingest_summary.trace_completeness`、`suggested_probe_set`、`host_action_required` 和 `raw_artifacts`。
3. 先做 answer symptom extraction，再做动态诊断 backlog：读取 workflow input/output、rewrite/keywords、origin/rerank/prompt 数量、关键文档生存路径、停用或 stale 来源、引用支撑、prompt-vs-answer alignment、chunk 冲突风险。
4. 如果需要 `generate-probe-plan`，用 trace artifacts、backlog 和 evaluator signals 生成 assertion set 和兼容 `plan.json`，用 `host_agent.answer_claim` 更新 `case.json`，重新 ingest，然后执行 `run-probe-plan`。
5. 将推荐或 backlog 证明必要的 `{stage}-exp` 运行到 `<case-dir>/probes/`。除 `replay-workflow` 外，独立验证环节可以并行；不要固定运行所有 probes。
6. 运行 `python -m findreason orchestrate --ingest-file <case-dir>/ingest.json --probe-dir <case-dir>/probes --mode final --schema-version v3 --output-dir <case-dir>/final`。
7. 用 `primary_cause`、现场观察摘要、`evidence_chain`、`failure_patterns`、`next_actions` 和 `raw_artifacts.workflow_span_ios` 撰写面向用户的报告；报告保留 workflow input/output 摘录，完整原文放在 artifact。

## 证据优先级

原始 Fornax 中间节点 trace 证据最权威。真实 query、rewrite query、keywords、workflow output、召回、重排、prompt/context、引用和脚本 I/O 优先来自 trace artifacts。如果 trace 包含 `Start`、`End`、`ZhiShangRAGRecall`、`ZhiShangRAGRerank` 或 `ZhiShangRAGQA`，不要 replay workflow，也不要覆盖 `origin_doc_list`、`rerank_docs`、`prompt_docs` 或 `answer`。

如果 trace 查询失败或缺少中间节点证据，运行 `fetch-workflow-nodes` 后再运行 `replay-workflow`。当 replay 与历史 trace 不一致时，replay 证据质量标记为更低。

## 报告检查清单

- Case 标识：`log_id`、`workspace_id`、`app_id`、`case_id/source_row`。
- Trace 摘要：节点完整性、origin/rerank/prompt 数量、workflow span 输入/输出摘录。
- 现场观察摘要：文档生存路径、stale / 停用来源、引用支撑、prompt-vs-answer alignment。
- 主因：stage、cause_code、owner、rationale。
- 断言覆盖矩阵：断言文本、role、source、线上初召回、rerank、prompt；只展示去重后的 `expected_required`，`merged_from` 只作审计；只有带 `full_support` / `partial_support` 正文片段的文档才计入，并在单独的“理论召回上界与断言关系”小节列出支撑片段。
- 答案症状：展示 `secondary_findings.answer_issue_types` 和候选解释。
- 证据链：带 counterfactual 和 `upstream_blocked_by` 的阶段 verdict；context/prompt 只作为观察，不作为主因阶段。
- Failure patterns 和 next actions。
- 当 `needs_human_review=true` 时，报告中必须明确提示人工复核。
