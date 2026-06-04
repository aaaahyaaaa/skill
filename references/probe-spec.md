# v3 探针规范

所有 probes 都输出 JSON，包含：

- `schema_version: "v3"`
- `log_id`、`workspace_id`、`probe_name`、`status`
- `stage_signals`：供 `orchestrate` 消费的归一化信号
- `evidence_bundle`：一条或多条证据记录
- `raw_artifacts`：probe 专属原始数据
- `telemetry.latency_ms`、`telemetry.cache_key`、`telemetry.cache_hit`

Probe 缓存位于 `~/.findreason/cache/<workspace_id>/<log_id>/`。传入 `--no-cache` 可强制重算。

| Probe | 阶段 | 关键信号 |
|-|-|-|
| `probe-self-oracle` | knowledge | `inferred_expected_docs`、`expected_knowledge_points`、`point_coverage`、`oracle_status`、`oracle_confidence` |
| `probe-knowledge-detail` | knowledge | `knowledge_exists: yes/no/unknown`、`retry_count` |
| `probe-permission-check` | retrieval | `permission_miss`、`permission_available` |
| `probe-wide-recall` | retrieval | `theoretical_recall_status`、`theoretical_query_variants`、`wide_recall_docs`、`matched_expected_ids`、`retrieval_gap_detected` |
| `probe-rerank-bypass` | rerank | `bypass_would_restore`、`expected_doc_survived_rerank` |
| `probe-rerank-tune` | rerank | `rerank_tunable`、`tunable_param` |
| `probe-context-assembly` | context | `expected_doc_in_prompt`、`context_assembly_error` |
| `probe-by-judgement` | retrieval | 宿主提供的 judgement 证据可用性 |
| `probe-by-claim` | answer | `host_agent.answer_claim`、`wrong_citation`、答案前置条件 |
| `probe-by-doc-title` | retrieval | trace 文档中的标题 / ID 精确匹配 |

`probe-self-oracle` 默认先于其他 probes 运行。只有宿主 Agent 提供必要断言或 expected IDs 时，它才会推断期望文档。它使用以下信号：

- `judgement_back_recall`：judgement / rubric 只作为观察项；断言必须已经体现在 `host_agent.answer_claim` 中。
- `claim_back_recall`：`host_agent.answer_claim` 项，包括用 role 标记的 unsupported claims。
- `query_wide_recall`：原 query / rewrite query 只作为召回 query variant，不能作为 expected assertion。

当前 P0 实现会在 trace-local candidate docs 中匹配这些信号，并输出 `evidence_type=inferred_oracle`。输出契约兼容后续接入实时 KB recall 后端。

它会与 `probe-wide-recall` 一起构建断言覆盖矩阵：

- `host_agent.answer_claim`：唯一的宿主断言输入，使用嵌套结构 `{"host_agent": {"answer_claim": [...]}}`。每项会归一化为 `expected_knowledge_points` 输出，字段包括 `text`、`role`、`source` 和可选 `confidence`。支持的 role 包括 `expected_required`、`missing_expected`、`answer_claim`、`unsupported_claim`、`constraint_check`、`citation_check`、`consistency_check`；`source` 归一化为 `host_agent.answer_claim`。
- CLI 不会把 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points` 当成断言来源。旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- CLI 不得从 query 文本、评估器标签、空回复诊断或任意 rubric / judgement 片段中生成期望断言。没有必要断言时，`oracle_status.source=insufficient_assertions`。
- `probe-wide-recall` 应运行原 query + rewrite query，topK >= 50，并将结果视为理论召回上界。
- `point_coverage`：逐必要断言匹配理论召回上界文档、`origin_doc_list/origin_faq_list`、`rerank_docs` 和 `prompt_docs`。文档匹配必须包含可回答的正文片段；标题命中或纯词面命中不计入。每个被接受的文档可包含 `support_status`（`full_support` 或 `partial_support`）、`support_score`、`support_spans`、`matched_terms` 和 `missing_constraints`。人类报告应把线上阶段渲染为断言覆盖矩阵，把理论召回上界文档渲染为单独的断言关系小节，而不是无解释的阶段列。
- `missing_expected_points_from_theoretical_recall`：没有上界支撑文档的必要断言，视为部分知识缺失。
- `missing_expected_points_from_origin`：上界支持但线上 origin recall 未命中的必要断言。
- `missing_expected_points_from_rerank`：初召回中存在但被 rerank 丢失的必要断言 / 文档。
- `missing_expected_points_from_prompt`：上游保留但 prompt context 未承接的必要断言 / 文档。

`replay-workflow` 不具备并行安全性。只有 ingest 表明 trace 查询失败或缺少中间节点证据时才运行。

## 运行探针计划（run-probe-plan / probe-v1）

`run-probe-plan` 是宿主 Agent probe plan 的执行器。宿主 Agent 负责反向构造 probe queries（它拥有语义意图）；CLI 只负责确定性执行，绝不决定主因。如果 plan 包含 `expected_required` / `missing_expected` probes，宿主 Agent 还必须把这些必要断言复制到 `host_agent.answer_claim`，并在最终 orchestration 前重新 ingest；否则断言覆盖矩阵会有意保持为空。

Plan 输入（`--plan @file` 或 JSON 字符串）使用 `schema_version: "probe-v1"`：

```json
{
  "schema_version": "probe-v1",
  "extracted": { "...": "optional host context" },
  "probe_execution_hint": { "topk_recommendation": 50 },
  "probes": [
    {
      "probe_id": "P-1",
      "direction": "citation_missing",
      "role": "citation_check",
      "target_artifact": "online_origin_recall",
      "query": "铺底计划 设置入口",
      "expected_hit_pattern": "铺底计划",
      "if_hit": "存在权威来源，但答案没有引用",
      "if_miss": "KB 中没有权威或可引用来源"
    }
  ]
}
```

- `direction` ∈ `relevance_gap`、`coverage_gap`、`scope_violation`、`citation_missing`、`internal_contradiction`；否则报 `E_PROBE_DIRECTION_INVALID`。
- `target_artifact` ∈ `kb_wide_recall`、`online_origin_recall`、`rerank_output`、`prompt_context`、`answer_span`；否则报 `E_PROBE_TARGET_INVALID`。
- `kb_wide_recall` 在所有 probes 间共享，只执行一次，并复用 `probe-wide-recall` 的 open-label 上界。
- 如果 `kb_wide_recall` 不可用（`theoretical_recall_status != "ok"`），相关 probes 标记为 `executed=false`、`hit=null`，不得产生由 hit/miss 派生的归因信号。
- 非 `probe-v1` schema 报 `E_PROBE_PLAN_SCHEMA`；非对象 plan 或缺少 `probes` list 报 `E_PROBE_PLAN_INVALID`。

执行器输出标准 probe envelope（`schema_version=v3`、`probe_name=run-probe-plan`、`stage_signals`、`evidence_bundle`、`raw_artifacts`），并在 `content.probe_results` 中包含每个 probe 的 `hit`、`matched_docs`、`converged_direction` 和 `evidence_id`。`matched_docs` 不是原始召回列表，只包含正文对 probe query / pattern 有 `full_support` 或 `partial_support` 片段的召回文档，并带上 `support_spans` 供审计。`direction` 到 signal / cause 的映射见 `references/cause-codes.md`。
