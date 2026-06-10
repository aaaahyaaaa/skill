# v3 验证环节规范

宿主 Agent 可以在执行中动态维护诊断 backlog。每个 backlog item 至少记录 `trigger_source`、`trigger_observation`、`hypothesis`、`exp_kind`、`target_stage` 和 `expected_evidence`。未执行的 backlog item 不是证据；只有执行后的 `{stage}-exp` 结果才能进入 `orchestrate`。

用户可见验证环节统一使用 `{stage}-exp`：

| exp_kind | 作用 |
|-|-|
| `retrieval-exp` | query / rewrite / keyword / query variant / topK / open-label / permission 等召回链路验证 |
| `rerank-exp` | 重排生存观察、阈值、参数或排序恢复信号；第一版只覆盖 rerank |
| `answer-exp` | prompt 已有约束是否被 answer 覆盖，是否漏答、越界或弱化 |
| `citation-exp` | 引用是否存在、可用、未停用，并支撑 answer claim |
| `chunk-conflict-exp` | origin/rerank/prompt chunk 是否内部矛盾、是否影响必要断言、是否有清晰适用前提 |

`run-probe-plan` 保留为兼容执行器，用于静态 artifact 命中验证；它不是覆盖所有场景的通用 probe。

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
| `probe-knowledge-detail` | knowledge | `knowledge_exists: yes/no/unknown`、`retry_count`、`detail_provider`、`detail_status`、`content_available_doc_ids` |
| `probe-permission-check` | retrieval | `permission_miss`、`permission_available` |
| `probe-wide-recall` | retrieval | `theoretical_recall_status`、`theoretical_query_variants`、`wide_recall_docs`、`matched_expected_ids`、`retrieval_gap_detected` |
| `probe-rerank-bypass` | rerank | `bypass_would_restore`、`expected_doc_survived_rerank`；仅作 doc-id 生存观察，不是 curl 重跑 rerank，不能单独触发 `rerank_drop` |
| `probe-context-assembly` | context observation | `expected_doc_in_prompt`、`context_assembly_error`；仅作 prompt/context 观察，不产生顶层主因 |

`probe-knowledge-detail` 使用 trace/provided id 做三态判断，并通过源码固定 doc-record endpoint 尝试把 trace docs 中的飞书 / OceanEngine 链接解析为 `source + identifier`，调用不需要 token 的 `GET /api/sirius_knowledge/v1/data/doc/record_id?source=...&identifier=...` 形态详情接口，并在 `raw_artifacts.knowledge_detail.fetched_docs` 中记录 title/link/content excerpt/split count 等有界摘要。接口错误或链接无法解析时保持 `knowledge_exists=unknown` 或沿用 trace id 证据，不直接判 `suspected_knowledge_missing`。

`host_agent.answer_claim` 与 `probe-wide-recall` / trace artifact 一起构建断言覆盖矩阵：

- `host_agent.answer_claim`：唯一 assertion set 输入，使用嵌套结构 `{"host_agent": {"answer_claim": [...]}}`。每项必须是对象，包含当前字段 `text`、`role`，归一化后输出到 `expected_knowledge_points`，字段包括 `text`、`role`、`source` 和可选 `basis`、`why_required`、`merged_from`。核心 role 是 `expected_required` 和 `answer_claim`。`constraint_check`、`citation_check`、`consistency_check` 用于 probe-v1 planning，不直接驱动上游断点；`source` 归一化为 `host_agent.answer_claim`。
- `expected_required` 会在进入 `point_coverage` 前做保守语义去重：如果两条断言是“场景约束 + 同一入口/路径要求”的包含或细化关系，CLI 合成一条，`basis` 取并集，原始断言写入 `merged_from`；未合并的原始断言不会重复进入覆盖矩阵。
- CLI 不会把 `case_input.expected_knowledge_points`、`qa.answer_claims`、`qa.missing_expected_points`、`qa.unsupported_claims`、`qa.claim_alignments` 或 `judgement_evidence.signals[].assertions/fact_points/missing_expected_points` 当成断言来源。旧字段非空会报 `E_LEGACY_ASSERTION_INPUT`。
- CLI 不得从 query 文本、评估器标签、空回复诊断或任意 rubric / judgement 片段中生成期望断言。没有 `expected_required` 时，`oracle_status.source=insufficient_assertions`。
- `answer_claim.text` 必须是 workflow output 中抽取出的命题 X，不应写成“答案称 X”。`answer_claim` 只用于 answer grounding、scope、citation 和 consistency 检查，不驱动 knowledge / retrieval / rerank 归因。
- `probe-wide-recall` 应运行原 query + rewrite query，topK >= 50，并将结果视为理论召回上界。

输入边界不是 probe plan 创造的断言。`orchestrate` 会读取 `raw_artifacts.workflow_span_ios[].input` 作为 Workflow 原始输入，并把它与用户实际问题/评估器用户上下文线索对比；如果关键场景约束在进入 Workflow 前已经丢失，先记录输入边界风险。只有受影响的 `expected_required` 已绑定到 `point_coverage`，且理论召回上界可支撑、online origin recall 缺失时，才判 `workflow_input_loss`。如果同一断言已经进入 online origin / rerank / prompt，输入差异不能作为主因。`rewrite_query` / `keywords` 只作为预处理节点输出；若影响召回，仍并入 `workflow_input_loss`。
- `point_coverage`：逐必要断言匹配理论召回上界文档、`origin_doc_list/origin_faq_list`、`rerank_docs` 和 `prompt_docs`。文档匹配必须包含可回答的正文片段；标题命中或纯词面命中不计入。每个被接受的文档可包含 `support_status`（`full_support` 或 `partial_support`）、`support_score`、`support_spans`、`matched_terms` 和 `missing_constraints`。如果断言行带 `merged_from`，它代表多个原始 `expected_required` 已合并为一个覆盖靶子。人类报告应把线上阶段渲染为断言覆盖矩阵，把理论召回上界文档渲染为单独的断言关系小节，而不是无解释的阶段列。
- `missing_expected_points_from_theoretical_recall`：没有上界支撑文档的必要断言，视为部分知识缺失。
- `missing_expected_points_from_origin`：上界支持但线上 origin recall 未命中的必要断言。
- `missing_expected_points_from_rerank`：初召回中存在但被 rerank 丢失的必要断言 / 文档。
- `missing_expected_points_from_prompt`：上游保留但 prompt context 未承接的必要断言 / 文档；当前版本只作为 prompt/context 观察，不产生主因。

`rerank_drop` 必须以必要断言覆盖断点为准。召回文档或 expected doc ID 未进入 rerank / prompt，只能说明链路存在观察信号；如果没有 `missing_expected_points_from_rerank`，`orchestrate` 不应仅凭 doc-id survival 选择 `rerank_drop`。`context_assembly_error` / `prompt_truncation_or_context_drop` 当前不再是顶层 cause。

当前实现没有真正的线上 rerank 重跑实验。`probe-rerank-bypass` 只观察历史 trace/ingest 中的关键 doc ID 是否从初召回进入 rerank 或 prompt；它不能证明“修改 rerank 参数后线上会恢复”，也不能替代 curl 重跑。若后续新增真实 `rerank-live-exp`，必须从 trace 还原 rerank 请求体，使用当前 workspace 的 `authInfo.apiKey` 鉴权调用 rerank 接口，并把结果标记为“当前版本反事实实验”，不得覆盖原始 trace。

`replay-workflow` 不具备并行安全性。只有 ingest 表明 trace 查询失败或缺少中间节点证据时才运行。

## 运行验证计划（run-probe-plan / probe-v1 兼容）

`run-probe-plan` 是宿主 Agent 静态 artifact 验证计划的兼容执行器。宿主 Agent 负责构造验证项（它拥有语义意图）；CLI 只负责确定性执行，绝不决定主因。如果 plan 包含 `expected_required` 验证项，宿主 Agent 还必须把这些必要断言复制到 `host_agent.answer_claim`，并在最终 orchestration 前重新 ingest；否则断言覆盖矩阵会有意保持为空。

Plan 输入（`--plan @file` 或 JSON 字符串）使用 `schema_version: "probe-v1"`：

```json
{
  "schema_version": "probe-v1",
  "extracted": { "...": "optional host context" },
  "probe_execution_hint": { "topk_recommendation": 50 },
  "probes": [
    {
      "probe_id": "P-1",
      "display_name": "铺底计划引用检查",
      "exp_kind": "citation-exp",
      "trigger_source": "answer_citation",
      "trigger_observation": "答案引用未覆盖铺底计划入口 claim",
      "hypothesis": "存在权威来源但答案没有正确引用",
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
- 可选字段 `display_name`、`exp_kind`、`trigger_source`、`trigger_observation`、`hypothesis` 会透传到 `content.probe_results[]` 和 evidence content，但不会直接决定主因。
- `kb_wide_recall` 在所有 probes 间共享，只执行一次，并复用 `probe-wide-recall` 的 open-label 上界。
- 如果 `kb_wide_recall` 不可用（`theoretical_recall_status != "ok"`），相关 probes 标记为 `executed=false`、`hit=null`，不得产生由 hit/miss 派生的归因信号。
- 非 `probe-v1` schema 报 `E_PROBE_PLAN_SCHEMA`；非对象 plan 或缺少 `probes` list 报 `E_PROBE_PLAN_INVALID`。

执行器输出标准 envelope（`schema_version=v3`、`probe_name=run-probe-plan`、`stage_signals`、`evidence_bundle`、`raw_artifacts`），并在 `content.probe_results` 中包含每个验证项的 `hit`、`matched_docs`、`converged_direction`、`evidence_id` 和可选元数据。`matched_docs` 不是原始召回列表，只包含正文对 query / pattern 有 `full_support` 或 `partial_support` 片段的召回文档，并带上 `support_spans` 供审计。`direction` 到 signal / cause 的映射见 `references/cause-codes.md`。
