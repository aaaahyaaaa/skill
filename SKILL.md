---
name: findreason-rag-attribution
description: FindReason RAG 归因 skill。对 RAG 答错、答漏、答非所问的 badcase 做证据采集和规则归因，输出 primary_cause、evidence_chain、failure_patterns、next_actions。触发词包括 RAG 归因、findreason、fornax trace、为什么答错了、case 复盘、归因报告、知识缺失、召回缺失、rerank 误杀、unsupported claim、wrong citation、partial answer。
---

# FindReason RAG 归因 v3

这是一个“证据优先”的归因引擎。宿主 Agent 负责理解 case、编排步骤、解释报告和面向用户沟通；随 skill 提供的 CLI 负责采集 trace / probe 证据，输出稳定 JSON，并在 `orchestrate --output-dir` 时生成唯一人读诊断报告 `final/case_report.md`。skill 本身不调用 LLM，也不负责批量 fan-out。

当前正式 CLI 入口是 `scripts/findreason.py`，正式归因实现集中在 `scripts/findreason_core/v3.py`；旧 `agent_graph` / `diagnostics` / `skills/specs` 路径已移除，不再作为主流程规则源。

本 skill 不替代宿主 Agent 的临场判断。Agent 应先读 trace 现场、观察文档生存路径和答案对齐，再决定 assertion set、probe 计划和最终报告结构；CLI 的职责是把已选定的证据检查做成可复核 JSON。

## 核心原则

- 证据优先：未执行的 hypothesis / backlog item 不是证据；只有 `{stage}-exp` 执行后的 hit/miss、matched docs、support spans、实验结果和 evidence ID 能进入 `orchestrate`。
- Agent 主导语义：宿主 Agent 负责理解评估器线索、trace 现场、答案、引用和 chunk 冲突，并决定要验证哪个环节。
- CLI 保持确定性：CLI 只采集 trace、执行验证、绑定 evidence、归一化 stage signals，并按 counterfactual 规则仲裁。
- 单主因，多发现：`primary_cause` 仍是单一枚举；答案漏答、错引、越界、chunk 冲突等复合问题通过 findings 展示。
- 不新增 subskill：当前通过 `references/` 做模块索引；如果宿主平台支持 subagent，可按同一 `{stage}-exp` 协议并行分配验证任务。

## 适用范围

适用于 RAG badcase 归因：答错、答漏、答非所问、知识缺失、召回缺失、重排误杀、prompt/context 丢证据、unsupported claim、wrong citation、partial answer 和召回 chunk 冲突风险。

不适用于非 RAG 路由、纯产品策略判断、无法获取 trace 且无法 replay 的 case、或没有任何可验证 artifact 的主观评价。评估器输入只是可选线索源，不是假定所有 case 都有，也不能直接决定主因。

## 任务路由

宿主 Agent 维护动态诊断 backlog。每看到一个线索，就记录 `trigger_source`、`trigger_observation`、`hypothesis`、`exp_kind`、`target_stage` 和 `expected_evidence`，再选择对应 `{stage}-exp`：

| 验证环节 | 使用场景 | 典型动作 |
|-|-|-|
| `retrieval-exp` | 验证召回链路是否能拿到更好证据 | query / rewrite / keyword / query variant / topK / open-label / permission |
| `rerank-exp` | 验证 rerank 是否丢掉 origin 中已有证据 | 重排生存观察、阈值、参数或排序恢复信号 |
| `answer-exp` | 验证 prompt 已有约束是否被 answer 覆盖 | answer span 覆盖、漏答、越界、弱化表达 |
| `citation-exp` | 验证引用是否存在、可用、支撑 claim | stale / 停用来源、引用文档不支撑 claim |
| `chunk-conflict-exp` | 验证召回/prompt chunk 是否内部矛盾 | 冲突断言、doc/chunk、支撑片段、适用前提 |

`run-probe-plan` 保留为兼容执行器，用于把宿主 Agent 已选择的静态 artifact 验证落成 JSON 证据；它不是覆盖所有场景的通用 probe。

## 模块索引

- `references/host_agent_playbook.md`：宿主 Agent 动态 backlog、subagent 分工和报告组织。
- `references/agent_attribution_planning.md`：如何把线索转成 assertion set 与 `{stage}-exp`。
- `references/probe-spec.md`：验证环节、`run-probe-plan` 兼容 schema 和 probe 输出。
- `references/report_template.md`：唯一人读诊断报告结构、验证过程卡片、阶段裁决和 findings 展示。
- `references/env.md`：凭证来源和禁用的 fallback。
- `references/cause-codes.md` / `references/orchestrator-rules.md`：cause 枚举、counterfactual 和主因选择。

## 安全与鉴权

不要打印原始 token、API key 或 Authorization header。提交到仓库的默认配置不得包含真实密钥。

凭证路由：

- `ingest-fornax-trace` 调 OpenPlat trace detail，只读取 `OPEN_PLAT_ZS_OPEN_TOKEN`；值不带 `Bearer`，CLI 自动拼接 `Authorization: Bearer <token>` 并发送 `x-zs-plt-open: zs_open`。
- `retrieval-exp` 的 wide recall、workflow fetch/replay 先用 `OPEN_PLAT_ZS_OPEN_TOKEN` 调 `get-workspace-info?workspaceId=<id>`，再使用当前 workspace 的 `authInfo.apiKey`；不得 fallback 到跨空间 `WORKFLOW_AUTH_TOKEN`，也不得使用 SSO/JWT。
- `probe-knowledge-detail` 的 doc record 接口不需要 token，不发送 Authorization。
- `E_TRACE_AUTH_REQUIRED` 表示缺少 `OPEN_PLAT_ZS_OPEN_TOKEN`，不是缺少 `app_id`；不能因此跳过 trace 主链路直接猜主因。

## 全局规则

1. 任何归因 case 都必须先运行 `ingest-fornax-trace`，最后运行 `orchestrate`。`ingest-fornax-trace --raw` 只用于查看原始 trace，不属于归因流程。
2. 候选原因只能使用 `references/cause-codes.md` 中的 v3 枚举。
3. 每个阶段 verdict 必须包含 `counterfactual`。
4. 如果上游阶段阻塞了下游判断，下游 verdict 必须设置 `upstream_blocked_by`。
5. 主因选择按 `preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation` 顺序遍历，选择第一个失败且 `counterfactual.downstream_would_change=true` 的阶段。
6. 如果上游 counterfactual 不可用，不要下沉到下游答案问题。输出 `primary_cause=null`、`needs_human_review=true`。
7. `judgement_evidence.signals` 必须小于等于 2KB。超限时 CLI 返回 `E_EVIDENCE_TOO_LARGE`，不会截断内容。
8. Fornax trace 的中间节点证据是权威证据。只有 trace 查询失败或缺少中间节点证据时，才使用 workflow replay。
9. probe 命令彼此独立，可以并行运行；`ingest` 和 `orchestrate` 必须串行；`replay-workflow` 是独占 fallback，不能与 probes 并行。
10. 所有输出契约都是带 `schema_version: "v3"` 的 JSON。
11. 宿主 Agent 负责语言理解任务：输入抽取、judgement 压缩、unsupported claim 抽取、引用抽取、answer span 对齐，以及最终面向用户的报告。
12. 批量 fan-out 属于宿主 Agent；本 skill 每次只处理一个 case。
13. 证据绑定会被校验：candidate cause 必须绑定 evidence ID，counterfactual 的 evidence ID 必须能回指 `evidence_bundle`。
14. 知识存在性是三态：`yes / no / unknown`。重试后仍为 unknown 时输出 `indeterminate` + 人工复核，不判 `suspected_knowledge_missing`。
15. 用户报告中必须显式展示 `needs_human_review=true`。
16. 归因范围只覆盖 RAG 答案问题。
17. 字段契约只保留最新约定字段。不得为旧字段、旧 role、旧 env var 或旧 schema alias 做自动兼容映射；外部输入出现过时断言字段时应 fail fast，并要求宿主 Agent 按当前契约重写。
18. `expected_knowledge_ids` 是可选字段。缺失时，宿主必须运行 `probe-self-oracle`；skill 不得把“没有提供期望知识”当成 retrieval / rerank / context 通过的证据。
19. self-oracle 推断出的知识必须携带 `oracle_source` 和 `confidence`；当 oracle 证据驱动 cause 时，verdict 置信度需要折算 oracle 置信度。
20. 如果人工提供的 expected knowledge 与 self-oracle 推断结果都存在但没有交集，设置 `needs_human_review=true`，原因写“人工提供的 expected knowledge 与 self-oracle 推断结果冲突”。
21. `host_agent.answer_claim` 是宿主 Agent 产出的唯一 assertion set 输入字段。使用嵌套 JSON 结构 `{"host_agent": {"answer_claim": [...]}}`。每项必须为对象，包含当前字段 `text`、`role`，可选 `basis`、`why_required`、`source`、`confidence`；合并输出还可包含 `merged_from`。核心 role 是 `expected_required` 和 `answer_claim`。`constraint_check`、`citation_check`、`consistency_check` 只用于 `probe-v1` 计划（范围、引用、一致性探针），不直接驱动上游归因。CLI 会把 `source` 归一化为 `host_agent.answer_claim`。
22. `expected_required` 由宿主 Agent 基于 trace query、chat_history、评估器 reason、rewrite query、keywords 等上下文推断，表示正确输出应覆盖的检查点；它是归因靶子，不是事实证据。CLI 会对 `expected_required` 做保守语义去重：只有“场景约束 + 同一入口/路径要求”的包含或细化关系才合并，`basis` 取并集，原始断言放入 `merged_from` 供审计，且只有合并后的断言进入 `point_coverage`。`answer_claim` 必须是从 workflow output 中抽取出的可验证命题 X，不能写成“答案称 X”的元叙述。
23. 外部传入的 `query` / `answer` 只是 hint，不等同于 Workflow 原始输入/输出。归因主链路必须从 trace 的 `raw_artifacts.workflow_span_ios` 读取 Workflow 原始输入/输出；如果用户实际问题或评估器用户上下文线索包含关键场景约束，但 Workflow 原始输入已经丢失这些约束，先记录输入边界风险。只有受影响的 `expected_required` 在理论召回上界可支撑、但线上初召回缺失时，才判 `preprocess.workflow_input_loss`。如果同一断言已经进入 online origin / rerank / prompt，不能把 badcase 归因为输入丢失。`rewrite_query`、`keywords` 是 Workflow 内部预处理节点输出，只用于判断 `query_rewrite_drift` / `keyword_loss`，不能反过来覆盖 Workflow 原始输入。
24. CLI 不得从 query 文本、评估器标签、空回复诊断、rubric / judgement 长文本片段中生成期望断言。如果没有 `expected_required` 断言，输出 `oracle_status.source=insufficient_assertions`；除非答案阶段证据本身足够，否则保持 `primary_cause=null`，并要求宿主 Agent 补充 assertion set。
25. 只有 `expected_required` 驱动 knowledge、retrieval、rerank、context 归因。`answer_claim`、`unsupported_claim` 和 `*_check` 只作为 answer / citation / consistency / scope 检查对象，不能触发 `suspected_knowledge_missing`。
26. 人类报告必须列出断言覆盖和阶段断点。断言覆盖矩阵应聚焦线上阶段（`origin -> rerank -> prompt`）；理论召回上界不应作为孤立的“命中文档”列，除非明确绑定了每个上界文档支持的必要断言。报告应单独展示“理论召回上界与断言关系”，包含支撑文档 ID、标题、命中词、支撑状态、支撑片段和分数。只有文档正文包含 `full_support` 或 `partial_support` 片段时，召回文档才算覆盖断言；标题命中或纯词面命中不得进入 `upper_bound_docs/origin_docs/rerank_docs/prompt_docs`。`probe-wide-recall` 必须从 trace 的 Sirius recall 请求模板构建理论召回上界，用原 query + rewrite query 运行 topK >= 50，并通过清空 `recallLabels/level`、保留 trace 召回策略的方式设置 `upper_bound_scope=open_label`。如果上界只能部分支持必要断言，则将未被支持的必要断言归为部分知识缺失，并建议补充或改写对应 KB 内容。如果上界支持必要断言但线上 origin recall 未命中，则归因到 retrieval。只有必要断言的支撑证据在线上 origin recall 出现、但在 rerank 前后丢失时，才判 `rerank_drop`；单纯某个召回文档或 expected doc ID 没进入 rerank / prompt 只能作为观察，不能单独决定主因。
27. `probe-rerank-bypass` 在报告中必须展示为“重排生存观察”，不是 curl 重跑实验。它只观察关键 doc ID 是否从初召回进入 rerank / prompt；如果没有断言级 `missing_expected_points_from_rerank`，不能单独触发 `rerank_drop`。

## Agent 现场侦查

在生成 assertion set 或运行 probes 前，宿主 Agent 应先做一次现场侦查。现场侦查是为了释放 Agent 的临场判断，不直接产出 `primary_cause`，也不绕过 evidence binding。

- 先拉取或读取 trace，确认中间节点是否完整：Workflow 原始输入/输出、rewrite query、keywords、origin docs / FAQ、rerank docs、prompt docs、final answer、citation mapping。
- trace 完整时，先摊平 `origin -> rerank -> prompt` 文档生存路径，记录每个关键候选是否存活、在哪个阶段丢失、是否只是 doc ID 命中但正文不支撑断言。
- 标记高风险来源：标题或正文包含“停用 / 已升级 / 过期 / deprecated / stale”的文档、重复 chunk、非权威来源、引用链接不可用或引用文档不支撑 answer claim。
- 做 prompt-vs-answer alignment：prompt 中是否已有关键限制、反例、预算/ROI/适用范围约束；最终答案是否遗漏、弱化或反向表达这些限制。
- 根据现场观察选择下一步：需要上游覆盖证据时生成 `expected_required` 和 probe-v1 plan；需要答案检查时生成 `answer_claim` / `citation_check` / `constraint_check`；trace 缺中间节点时才考虑 replay。

## 宿主 Agent 执行流程

1. 将粘贴文本、表格行、curl/body 或用户描述标准化为 `judgement`、`workspace_id`、`app_id`、`log_id`，可选 `query_hint` / `answer_hint`、`expected_knowledge_ids`、嵌套 `host_agent.answer_claim`、可选 `qa` 答案状态字段，以及结构化 `judgement_evidence.signals`。外部表格/评估答案统一写 `answer_hint`；ingest 只在 trace 没有 workflow output 时把它规范化到内部 `qa.answer`。评估器只压缩为“怀疑哪里坏了”的信号，不能直接决定主因。
   宿主 Agent 只能把 assertion set 汇总到 `host_agent.answer_claim`。不要把断言放进 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`。
   `source` 可省略；CLI 会归一化为 `host_agent.answer_claim`。`role=expected_required` 表示正确输出应覆盖的检查点，`role=answer_claim` 表示 workflow output 中抽取出的命题 X。

断言输入示例：

```json
{
  "host_agent": {
    "answer_claim": [
      {
        "text": "正确输出应覆盖的事实断言",
        "role": "expected_required",
        "basis": ["trace_query", "evaluator_reason"],
        "confidence": 0.9
      },
      {
        "text": "workflow 输出中的可验证命题 X",
        "role": "answer_claim",
        "basis": ["workflow_output"],
        "confidence": 0.8
      }
    ]
  },
  "qa": {
    "prompt_supports_answer": true,
    "answer_satisfies_expected": false
  }
}
```

2. 运行 ingest：

```bash
python -m findreason ingest-fornax-trace \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

3. 先读 `raw_artifacts.trace_summary`、`raw_artifacts.workflow_span_ios`、`raw_artifacts.trace_evidence` 和 `raw_artifacts.attribution_request` 做 Agent 现场侦查，形成动态诊断 backlog：真实 workflow input/output、rewrite/keywords、origin/rerank/prompt 数量、关键文档生存路径、停用或 stale 来源、引用支撑、prompt-vs-answer alignment、chunk 冲突风险。现场观察只决定下一步 `{stage}-exp` 和 assertion，不直接决定主因。

4. 读取 `ingest_summary.suggested_probe_set` 和 `host_action_required`。结合动态 backlog 选择验证环节：除非用户明确要求某个分支，否则只运行推荐或现场观察证明必要的 `{stage}-exp`；不要固定跑所有 probes。默认优先运行 `probe-self-oracle`。
   `probe-wide-recall --topk 50` 建议与 self-oracle 一起运行，并使用 trace 中的 Sirius recall body 作为 open-label 理论召回上界。
   如果 `host_action_required` 包含 `generate-probe-plan`，宿主 Agent 必须按照 `references/agent_attribution_planning.md` 从 trace artifacts、动态 backlog 和 `judgement_evidence.signals` 生成 assertion set 与兼容 `probe-v1` 计划。把每个 `expected_required` 和 `answer_claim` 写入 `host_agent.answer_claim`，重新运行 `ingest-fornax-trace --case-file`，然后执行：

```bash
python -m findreason run-probe-plan --ingest-file /tmp/findreason-case/ingest.json --plan @plan.json --output-dir /tmp/findreason-case/probes
```

   如果没有这一步宿主抽取，CLI 会有意保持断言覆盖矩阵为空，因为它不能自行从 query 文本或评估器标签中发明必要断言。

5. 运行最终仲裁：

```bash
python -m findreason orchestrate \
  --ingest-file /tmp/findreason-case/ingest.json \
  --probe-dir /tmp/findreason-case/probes \
  --mode final \
  --schema-version v3 \
  --output-dir /tmp/findreason-case/final
```

6. 读取 `final/case_report.md` 作为唯一人读诊断报告。报告应优先展示结论、现场输入与答案、验证过程、阶段裁决、关键证据与文档、下一步和审计 JSON 索引；完整 `raw_artifacts.workflow_span_ios` 和验证原始输出保留在 `attribution_record.json` 中。宿主 Agent 可以基于这份报告向用户再做摘要，但不要再默认生成 `agent_run_process.md` 或 `diagnostic_timeline.md`。

## 命令

基础命令：

```bash
python -m findreason ingest-fornax-trace --workspace-id 89 --log-id 20260601191946A85794168A7D7BF20EB0 --limit 1000
python -m findreason orchestrate --ingest-file /tmp/findreason-case/ingest.json --probe-dir /tmp/findreason-case/probes
```

Probe 命令：

```bash
python -m findreason probe-self-oracle --ingest-file /tmp/findreason-case/ingest.json --signals judgement_back_recall,claim_back_recall,query_wide_recall --topk 50 --output-dir /tmp/findreason-case/probes
python -m findreason probe-knowledge-detail --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-permission-check --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-wide-recall --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-rerank-bypass --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-context-assembly --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
```

评估器和答案 claim 的语义拆解使用 Agent planning playbook + `run-probe-plan`。旧弱 probe（`probe-rerank-tune`、`probe-by-judgement`、`probe-by-claim`、`probe-by-doc-title`）已移除。

验证计划执行器（兼容 `probe-v1`）：

宿主 Agent 把静态 artifact 验证构造成兼容 `probe-v1` 计划（方向包括 `relevance_gap`、`coverage_gap`、`scope_violation`、`citation_missing`、`internal_contradiction`），可选填 `display_name`、`exp_kind`、`trigger_source`、`trigger_observation`、`hypothesis`。`run-probe-plan` 会针对请求的 `target_artifact`（`kb_wide_recall`、`online_origin_recall`、`rerank_output`、`prompt_context`、`answer_span`）执行每个查询，记录确定性的 hit/miss 事实，并输出供 `orchestrate` 消费的 `stage_signals`。执行器绝不决定主因。把它的 JSON 输出放入 `--probe-dir`。

```bash
python -m findreason run-probe-plan --ingest-file /tmp/findreason-case/ingest.json --plan @plan.json --output-dir /tmp/findreason-case/probes
```

Workflow 命令：

```bash
python -m findreason fetch-workflow-nodes --workspace-id <workspace_id> --app-id <app_id>
python -m findreason replay-workflow --ingest-file /tmp/findreason-case/ingest.json --override @override.json
```

只查看原始 trace：

```bash
python -m findreason ingest-fornax-trace --workspace-id <workspace_id> --log-id <log_id> --raw
```

查看 schema：

```bash
python -m findreason schema
```

## 输出契约

`ingest-fornax-trace` 输出 `schema_version`、`log_id`、`workspace_id`、`app_id`、`case`、`ingest_summary` 和 `raw_artifacts`。`ingest_summary` 包含 `trace_completeness`、`suggested_probe_set`、`skip_reason` 和 `host_action_required`。

`orchestrate` 输出 `schema_version`、`oracle_status`、`case_assessment`、`primary_cause` 对象或 `null`、`failure_patterns`、`needs_human_review`、`human_review_reasons`、`evidence_bundle`、`evidence_chain`、`next_actions`、`telemetry`、`deprecations` 和 `raw_artifacts`。`oracle_status` 可包含 `expected_knowledge_points`、`point_coverage`、`missing_expected_points_from_origin`、`missing_expected_points_from_rerank` 和 `missing_expected_points_from_prompt`。当 `expected_required` 被保守合并时，合并后的 `expected_knowledge_points[]/point_coverage[]` 行包含 `merged_from`，原始断言不再作为独立 coverage 行参与归因。

当 `orchestrate` 使用 `--output-dir` 时，CLI 会为单个 case 写入 `attribution_record.json`、`short_summary.json` 和唯一人读报告 `case_report.md`。不会生成 `agent_run_process.md`、`diagnostic_timeline.md`，也不会生成批量 `summary.md`、`summary.csv` 或 `summary.json`。

## 参考文档

- `references/cause-codes.md`：v3 cause 枚举、owner、触发条件和边界。
- `references/probe-spec.md`：probe 输入、输出、缓存和失败语义。
- `references/agent_attribution_planning.md`：宿主 Agent 如何把 trace artifacts 与评估器信号转成 assertion set 和 probe-v1 plan。
- `references/report_template.md`：唯一人读诊断报告结构，包括验证过程卡片、阶段裁决、关键证据与文档、审计 JSON 索引。
- `references/orchestrator-rules.md`：counterfactual 和主因选择规则。
- `references/workflow-ops.md`：workflow 节点获取和 replay 行为。
- `references/span-extraction.md`：Fornax span 抽取映射。
- `references/evidence-spec.md`：evidence bundle schema 和校验规则。
- `references/output-schema.json`：供宿主侧校验使用的 v3 输出 schema。
- `references/host_agent_playbook.md`：宿主 Agent 职责和报告组织指南。
- `references/capabilities.json`：v3 capability manifest。
