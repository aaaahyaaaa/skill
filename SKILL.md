---
name: findreason-rag-attribution
description: FindReason RAG 归因 skill。对 RAG 答错、答漏、答非所问的 badcase 做证据采集和规则归因，输出 primary_cause、evidence_chain、failure_patterns、next_actions。触发词包括 RAG 归因、findreason、fornax trace、为什么答错了、case 复盘、归因报告、知识缺失、召回缺失、rerank 误杀、unsupported claim、wrong citation、partial answer。
---

# FindReason RAG 归因 v3

这是一个“证据优先”的归因引擎。宿主 Agent 负责理解 case、编排步骤和撰写最终自然语言报告；随 skill 提供的 CLI 负责采集 trace / probe 证据，并输出稳定 JSON。skill 本身不调用 LLM，不负责最终用户报告，也不负责批量 fan-out。

## 安全与鉴权

不要打印原始 token、API key 或 Authorization header。OpenPlat token 通过进程环境变量或宿主本地 env 文件配置，值不带 `Bearer`；CLI 会自动拼接 `Authorization: Bearer <token>`。提交到仓库的默认配置不得包含真实密钥。

## Skill 命令行

需要访问内部 API、线上 workflow 或 RDS 时，先通过 SSO 获取认证。

设置 npm registry：

```bash
export npm_config_registry=https://bnpm.byted.org/
```

获取 JWT：

```bash
npx -y skills get-jwt
npx -y skills -h
```

可选参数：

- `--region`：可选值为 `cn`、`i18n`、`boe`、`sandbox`

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
17. `expected_knowledge_ids` 是可选字段。缺失时，宿主必须运行 `probe-self-oracle`；skill 不得把“没有提供期望知识”当成 retrieval / rerank / context 通过的证据。
18. self-oracle 推断出的知识必须携带 `oracle_source` 和 `confidence`；当 oracle 证据驱动 cause 时，verdict 置信度需要折算 oracle 置信度。
19. 如果人工提供的 expected knowledge 与 self-oracle 推断结果都存在但没有交集，设置 `needs_human_review=true`，原因写“人工提供的 expected knowledge 与 self-oracle 推断结果冲突”。
20. `host_agent.answer_claim` 是宿主 Agent 唯一的断言输入。使用嵌套 JSON 结构 `{"host_agent": {"answer_claim": [...]}}`。每项应为对象，包含 `text`、`role`，可选 `source` 和 `confidence`；合法 role 包括 `expected_required`、`missing_expected`、`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`。`*_check` role 用于 `probe-v1` 计划（范围、引用、一致性探针）。CLI 会把 `source` 归一化为 `host_agent.answer_claim`。
21. CLI 不得从 query 文本、评估器标签、空回复诊断、rubric / judgement 长文本片段中生成期望断言。如果没有 `expected_required` / `missing_expected` 断言，输出 `oracle_status.source=insufficient_assertions`；除非答案阶段证据本身足够，否则保持 `primary_cause=null`，并要求宿主 Agent 补充断言。
22. 只有 `expected_required` 和 `missing_expected` 驱动 knowledge、retrieval、rerank、context 归因。`unsupported_claim` 只作为 answer 阶段证据，不能触发 `suspected_knowledge_missing`。
23. 人类报告必须列出断言覆盖和阶段断点。断言覆盖矩阵应聚焦线上阶段（`origin -> rerank -> prompt`）；理论召回上界不应作为孤立的“命中文档”列，除非明确绑定了每个上界文档支持的必要断言。报告应单独展示“理论召回上界与断言关系”，包含支撑文档 ID、标题、命中词、支撑状态、支撑片段和分数。只有文档正文包含 `full_support` 或 `partial_support` 片段时，召回文档才算覆盖断言；标题命中或纯词面命中不得进入 `upper_bound_docs/origin_docs/rerank_docs/prompt_docs`。`probe-wide-recall` 必须从 trace 的 Sirius recall 请求模板构建理论召回上界，用原 query + rewrite query 运行 topK >= 50，并通过清空 `recallLabels/level`、保留 trace 召回策略的方式设置 `upper_bound_scope=open_label`。如果上界只能部分支持必要断言，则将未被支持的必要断言归为部分知识缺失，并建议补充或改写对应 KB 内容。如果上界支持必要断言但线上 origin recall 未命中，则归因到 retrieval。只有必要断言的支撑证据在线上 origin recall 出现、但在 rerank 前后丢失时，才判 `rerank_drop`。

## 宿主 Agent 执行流程

1. 将粘贴文本、表格行、curl/body 或用户描述标准化为 `query`、`judgement`、`workspace_id`、`app_id`、`log_id`，可选 `expected_knowledge_ids`，嵌套 `host_agent.answer_claim`，可选 `qa` 答案状态字段，以及结构化 `judgement_evidence.signals`。
   宿主 Agent 只能把确定性断言汇总到 `host_agent.answer_claim`。不要把断言放进 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points`。
   `source` 可省略；CLI 会归一化为 `host_agent.answer_claim`。`role` 表示事实必须覆盖、答案遗漏、普通答案 claim、未支持 claim 等语义差异。

断言输入示例：

```json
{
  "host_agent": {
    "answer_claim": [
      {
        "text": "正确答案应覆盖的事实断言",
        "role": "expected_required",
        "confidence": 0.9
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

3. 读取 `ingest_summary.suggested_probe_set` 和 `host_action_required`。除非用户明确要求某个分支，否则只运行推荐 probes。默认优先运行 `probe-self-oracle`。
   `probe-wide-recall --topk 50` 建议与 self-oracle 一起运行，并使用 trace 中的 Sirius recall body 作为 open-label 理论召回上界。
   如果 `host_action_required` 包含 `generate-probe-plan`，宿主 Agent 必须用 probe-v1 提示词从用户问题和答案中反向构造探针查询。把代表必要答案断言的每个 `expected_required` / `missing_expected` probe 复制到 `host_agent.answer_claim`，重新运行 `ingest-fornax-trace --case-file`，然后执行：

```bash
python -m findreason run-probe-plan --ingest-file /tmp/findreason-case/ingest.json --plan @plan.json --output-dir /tmp/findreason-case/probes
```

   如果没有这一步宿主抽取，CLI 会有意保持断言覆盖矩阵为空，因为它不能自行从 query 文本中发明必要断言。

4. 运行最终仲裁：

```bash
python -m findreason orchestrate \
  --ingest-file /tmp/findreason-case/ingest.json \
  --probe-dir /tmp/findreason-case/probes \
  --mode final \
  --schema-version v3 \
  --output-dir /tmp/findreason-case/final
```

5. 从 JSON 输出撰写人类报告。若存在 `raw_artifacts.workflow_span_ios`，报告中必须包含 `span_type=workflow` 的输入/输出。

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
python -m findreason probe-rerank-tune --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-context-assembly --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-judgement --ingest-file /tmp/findreason-case/ingest.json --judgement "评估器失败项：事实正确性=否" --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-claim --ingest-file /tmp/findreason-case/ingest.json --claims @claims.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-by-doc-title --ingest-file /tmp/findreason-case/ingest.json --titles @titles.json --output-dir /tmp/findreason-case/probes
```

Probe-plan 执行器（`probe-v1`）：

宿主 Agent 把探针查询反向构造成 `probe-v1` 计划（方向包括 `relevance_gap`、`coverage_gap`、`scope_violation`、`citation_missing`、`internal_contradiction`）。`run-probe-plan` 会针对请求的 `target_artifact`（`kb_wide_recall`、`online_origin_recall`、`rerank_output`、`prompt_context`、`answer_span`）执行每个查询，记录确定性的 hit/miss 事实，并输出供 `orchestrate` 消费的 `stage_signals`。执行器绝不决定主因。把它的 JSON 输出放入 `--probe-dir`。

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

`orchestrate` 输出 `schema_version`、`oracle_status`、`case_assessment`、`primary_cause` 对象或 `null`、`failure_patterns`、`needs_human_review`、`human_review_reasons`、`evidence_bundle`、`evidence_chain`、`next_actions`、`telemetry`、`deprecations` 和 `raw_artifacts`。`oracle_status` 可包含 `expected_knowledge_points`、`point_coverage`、`missing_expected_points_from_origin`、`missing_expected_points_from_rerank` 和 `missing_expected_points_from_prompt`。

当 `orchestrate` 使用 `--output-dir` 时，CLI 会为单个 case 写入 `attribution_record.json` 和 `short_summary.json`。不会生成批量 `summary.md`、`summary.csv` 或 `summary.json`。

## 参考文档

- `references/cause-codes.md`：v3 cause 枚举、owner、触发条件和边界。
- `references/probe-spec.md`：probe 输入、输出、缓存和失败语义。
- `references/orchestrator-rules.md`：counterfactual 和主因选择规则。
- `references/workflow-ops.md`：workflow 节点获取和 replay 行为。
- `references/span-extraction.md`：Fornax span 抽取映射。
- `references/evidence-spec.md`：evidence bundle schema 和校验规则。
- `references/output-schema.json`：供宿主侧校验使用的 v3 输出 schema。
- `references/host_agent_playbook.md`：宿主 Agent 职责和报告组织指南。
- `references/capabilities.json`：v3 capability manifest。
