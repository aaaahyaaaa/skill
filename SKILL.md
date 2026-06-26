---
name: findreason-rag-attribution
description: FindReason RAG 归因 skill。用于 RAG badcase、Fornax trace、为什么答错/答漏/答非所问、知识缺失、召回缺失、rerank 误杀、unsupported claim、wrong citation、scope violation 等复盘场景。当前 v4 是 agent-judgement 工作流：代码只采集 trace、归一化 recall/rerank/prompt 证据并运行实验；最终归因解释由 Agent 基于表象、候选根因和实验结果生成。
---

# FindReason Agent Judgement v4

FindReason v4 不再让 CLI 选择单一 `primary_cause`，也不再用“最早断点”硬裁决替代分析。代码负责把真实现场和实验结果整理成可审计事实；宿主 Agent 负责从表象出发，提出多个合理根因解释，再用 recall / rerank / replay 实验支持或反证。

旧 v3 的 `orchestrate` / `candidate_cause` / `primary_cause` 路径已退出主流程。不要为了兼容旧 CLI 或旧测试恢复这条路径。

## 分工

代码负责：

- 拉取、解析、固化 Fornax trace。
- 通过 OpenPlat app-detail 解析 `workflowConfigV2`，输出真实 workflow 节点拓扑、节点证据映射和 prompt 观测状态。
- 归一化 `origin_doc_list`、`origin_faq_list`、`rerank_docs`、`prompt_docs`。
- 把 `origin_doc_list + origin_faq_list` 汇总为人读层的 `recall`，同时保留原始字段用于审计。具体线上召回链路见 `references/recall_chain.md`。
- 运行或规划 recall variant matrix、rerank rank-shift、replay 和 knowledge-detail 状态实验。
- 输出 `case_facts.json`、`*_experiment.json`、`agent_brief.md`。
- 用 `synthesize-brief` 生成短版 `agent_judgement.md` 和 `evidence_index.json` 本地证据索引；若存在 `knowledge_detail_experiment.json`，自动把关键文档状态信号写进索引。

Agent 负责：

- 现场侦查怎么想。
- answer symptom extraction 怎么做。
- 哪些候选解释合理。
- 怎么规划下一步 probe / experiment。
- 怎么写人读报告。
- 不同阶段的边界、反思流程和最终 judgement。
- 根据 `trace.node_evidence_map` / `trace.agent_span_read_plan` 决定哪些节点和 span 值得深读；不要把脚本的候选读取建议当成最终 cause。

## 主流程

1. 用 `collect-evidence` 固化 trace 和 RAG artifacts。
2. 读取 `case_facts.json` 与 `agent_brief.md`，先看 `Workflow 节点诊断` 和 `按 cause 的 span 读取入口`，决定要深读哪些节点/span。
3. 抽答案症状，再列候选根因。
4. 对每个候选根因写明支持证据、反证证据和下一步实验。
5. 需要时用 `run-experiment` 规划或运行 recall / rerank / replay / knowledge-detail 实验。
6. 运行 `synthesize-brief` 生成短版 judgement summary 和证据索引。
7. Agent 基于事实和实验结果给用户输出短版结论，风格类似一次清晰复盘回复；不要要求用户阅读 JSON 或长报告。

## Cause 枚举

最终报告以中文 cause 为主，旧 slug 只作为兼容别名：

- `输入侧问题`（旧 slug: `workflow_input_loss`）
- `知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）
- `召回遗漏`（旧 slug: `retrieval_miss`）
- `重排丢失`（旧 slug: `rerank_drop`）
- `答案生成错误`（旧 slug: `answer_failure`）
- `无明显错误/评估器不准，需人工进一步核实`（slug: `evaluator_disputed_no_obvious_error`）

`输入侧问题` 覆盖整个链路输入侧的失真：Workflow input 构造丢上下文、限定词/实体/子问题丢失、rewrite 错误或不完整、keywords / query variants 未保真、多意图未拆解等。但它不能只凭“看起来改写丢了”就作为主因。只有根据验证点改写后的 query 能够在实验中带来召回改善、排序改善，或 replay / 最终结果改善时，才能把主因上调为 `输入侧问题`。如果改写 query 没改善实验结果，只能写成低置信候选或待验证点。

第 6 类 `无明显错误/评估器不准，需人工进一步核实` 不能作为“看不出来”的兜底。只有在前 5 类都没有明显证据，且评估器结论与 prompt evidence、Workflow 输出、被评估答案或人工标注之间存在可说明的冲突时，才能归到该类。报告必须显式写出人工复核点；不要求固定范式，但要讲清楚为什么怀疑评估器不准、人工需要复核哪里、复核后可能如何改变结论。`评估器输出暂无` 本身不是第 6 类证据，应继续看链路证据或写低置信待补证。

## Trace 获取模式

遇到“找不到原始 trace”时不要停在找文件。先按已有输入判断属于哪种模式，再进入对应案例：

- 本地已有 trace JSON：优先 `--trace-file`，见 `references/cases/019eee75-local-trace-workflow-input-loss.md`。
- 原始 Fornax trace 不可用，只有原始输入 / app / workspace：先定位已有再生产物；没有产物时用 replay 重跑原始输入，见 `references/cases/019eef8d-rerun-input-knowledge-missing.md`。
- 可以通过 workspace + log_id 获取 trace：直接 `collect-evidence` 拉取，再按 target doc 深挖，见 `references/cases/019ece69-logid-trace-retrieval-miss.md`。

这三个案例是完整分析过程示例，不是模板答案。复用时必须替换具体 log_id、query、run 目录和 target doc；最终 cause 仍然要由当前证据决定。

## 工具最小化原则

默认只使用本地 Python 脚本、用户提供的本地文件和宿主 LLM 推理；不要把 Fornax、飞书、bytedcli、浏览器、MCP、子 Agent 等能力当作 skill 的必需执行面。外部能力只作为显式可选补充，并且必须能被本地文件输入替代。

当前真实依赖分层：

- 必需：本地 `python3 -m findreason` CLI、`case_facts.json`、`agent_brief.md`、`*_experiment.json`、`agent_judgement.md`。
- 可选线上接口：`collect-evidence` 在未提供 `--trace-file` 时会用 OpenPlat trace detail API 拉取 trace；有本地 trace 时优先传 `--trace-file`。
- 可选 live recall：`run-experiment --type recall --query ...` 在 trace 中有 recall/searchDoc 请求模板时，会用 `httpx` 复用模板调用线上 recall/searchDoc；不传 `--query` 时只生成本地实验计划。
- 可选 live replay：`run-experiment --type replay` 只有在历史 trace 缺少中间节点证据时才尝试当前版本 workflow replay，内部只走接口链路：OpenPlat workspace info API 先用固定 open-api token 获取 workspace 级 `authInfo.apiKey`，再用该 workspace apiKey 和 `workspaceId` header 调 OpenPlat app detail API 获取 `workflowConfigV2`，最后用同一个 workspace apiKey 调 open-exec workflow API 执行重跑；历史 trace 足够时会跳过 replay。
- 发布层：飞书文档只用于用户明确要求分享/评审发布时；归因本身不要依赖飞书文档作为唯一证据库。

因此，最稳的默认路径是：单 case 本地 case 文件 + 本地 trace 文件 + LLM 读 `agent_brief.md` / `agent_judgement.md` 完成最终判断。只有用户明确要“重新拉线上 trace / 重跑 recall / 重跑 workflow”时，才启用对应可选能力。

## 命令

```bash
python3 -m findreason collect-evidence \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

如果已有本地 trace JSON，可跳过线上拉取：

```bash
python3 -m findreason collect-evidence \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --trace-file /path/to/trace.json \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

实验入口：

```bash
python3 -m findreason run-experiment --type recall --facts-file /tmp/findreason-case/case_facts.json --query "<实验 query>" --context-query "<query_list/chat_history 改写 query>" --output-dir /tmp/findreason-case
python3 -m findreason run-experiment --type rerank --facts-file /tmp/findreason-case/case_facts.json --target-doc-id <doc_id> --output-dir /tmp/findreason-case
python3 -m findreason run-experiment --type knowledge-detail --facts-file /tmp/findreason-case/case_facts.json --target-doc-id <doc_id> --output-dir /tmp/findreason-case
python3 -m findreason run-experiment --type replay --facts-file /tmp/findreason-case/case_facts.json --query "<真实用户问题>" --app-id <app_id> --version-id <version_id> --output-dir /tmp/findreason-case
```

不传 `--query` / `--context-query` 时，recall 实验只输出 query variant 规划和历史 baseline。传入 `--query` 且 trace 中有 recall/searchDoc 请求模板时，代码会输出 `recall_variant_matrix`：`baseline_trace_recall` 只总结历史 `origin_doc_list + origin_faq_list`，`workflow_query_override` 复用 trace template 执行，`topK/maxCount_relaxed` 默认把可识别 topK/maxCount 提到至少 50，`label_relaxed` / `threshold_relaxed` 只有在模板中明确找到字段时执行，否则标记 `unsupported`。`--context-query` 可重复，用于 query_list/chat_history 改写 query 的观察；命中核心文档只说明 counterfactual recall 改善，不能自动上调 `输入侧问题`。

rerank 实验读取 `case.core_documents[]`（`doc_id`、`supported_assertion`、`title_hint`），也兼容 `--target-doc-id`。输出 `rank_shift_observations`，每个核心 doc 都要看 recall/rerank/prompt rank、score、delta、是否进入 prompt、缺失原因和真实 `context_boundary`；不要硬写具体脚本截断，除非 `workflow_topology` / `node_evidence_map` / `prompt_observation` 真的确认了该边界。

knowledge-detail 实验默认只处理关键 doc：`case.core_documents`、`--target-doc-id`、prompt 关键证据和评估器/业务文本中显式提到的 doc。它尝试调用 docDetail/knowledge detail 并抽取 `status_signals`、`status_confirmed`、`last_modified`、`status_reason`。接口失败或字段不足时必须写 `status_confirmed=false`、`status_reason=status_unconfirmed`，不能把状态装作已确认。

`--version-id` / `--app-version` 只用于 replay 的 app-detail 解析。用户提供时会作为 `appVersion` 传给 `/open-plat/api/app/get-app-detail`；用户没有提供时不传 `appVersion`，由平台接口返回最新版本。

`/open-plat/api/app/get-app-detail` 不在固定 `x-zs-plt-open: zs_open` 白名单鉴权路径内。调用它时必须先通过 `/open-plat/api/workspace/get-workspace-info` 拿到 workspace 级 `authInfo.apiKey`，再带 `Authorization: Bearer <workspace apiKey>` 和 header `workspaceId: <workspace_id>` 请求 app-detail；query params 中仍保留 `workspaceId`、`appId` 和可选 `appVersion`。如果只用固定 open-api token 调 app-detail，入口 `AuthFilter` 会落到 Kani/cookie 登录态兜底，常见报错是「鉴权失败，用户登录状态异常」。

报告合成入口：

```bash
python3 -m findreason synthesize-brief \
  --facts-file /tmp/findreason-case/case_facts.json \
  --experiment-dir /tmp/findreason-case \
  --output-dir /tmp/findreason-case
```

默认产物：

- `agent_judgement.md`：每条 case 的唯一短版人读 judgement 结论文件，包含当前结论入口、答案症状、上游证据链、关键证据和本地证据包位置；不要再额外维护一份 `summary.md` / `summary` 人读副本，避免两份结论漂移。
- `evidence_index.json`：本地可索引证据包，包含 trace/replay/实验文档的 title、url、snippet、rank、score、doc_id_aliases、status_signals、status_confirmed、last_modified、status_reason。

查看 v4 manifest：

```bash
python3 -m findreason schema
```

## 现场侦查

先看表象，再找根因，不要一开始就落标签。

- 输入表象：用户真实问题、评估器上下文、Workflow 原始输入、rewrite、keywords 是否一致。
- `chat_history` 只能用于判断 `输入侧问题`（旧 slug: `workflow_input_loss`）：对比用户上下文是否进入 Workflow input / rewrite / keywords；不得用它支撑 `答案生成错误`（旧 slug: `answer_failure`）的答案正误判断。
- 如果怀疑 `输入侧问题`，必须根据验证点改写后的 query 做 recall / rerank / replay 对照，检查是否带来召回改善、排序改善，或 replay / 最终结果改善。
- 如果只是发现 Workflow input、rewrite、keywords 可能少了信息，但改写 query 没改善实验结果，只能把 `输入侧问题` 写成低置信候选或待验证点，不能直接判主因。
- 答案表象：漏答、错引、越界、自相矛盾、无依据断言、把弱证据写成强结论。
- 证据生存：同一必要断言是否从 `recall` 进入 `rerank_docs`，再进入 `prompt_docs` / `qaPromptDocs`。
- 重排 rank-shift：对每个核心 doc 固定记录它支撑哪条 assertion、recall rank/score、rerank rank/score、prompt rank/score、rank/score 变化、是否进入 prompt、缺失原因和上下文边界。
- Workflow-aware 侦查：优先使用 `case_facts.json.trace.workflow_topology` 的 app-detail 真实节点信息；`node_evidence_map[].inferred_role` 只是辅助说明。Agent 应根据真实节点 type/name、span input/output 和证据字段位置决定深读哪些 span。
- Prompt 观测：`trace.prompt_observation.status=not_observed` 只表示解析面未看到 `prompt_docs` / `qaPromptDocs`，不得直接写成“全部证据被过滤”或“模型零证据生成”。先回查 `agent_span_read_plan` 推荐的大模型、知商问答、脚本和后处理节点。
- 召回边界：报告里可叫 `recall`，但审计时要区分 `origin_doc_list` 与 `origin_faq_list`；线上链路可能来自旧 `/searchDoc`，也可能来自新拆分 `/recall` 能力，以 trace 为准。
- 知识边界：宽召回是否仍无权威支撑，还是只有相邻主题。
- 证据充分性：把用户问题拆成 required assertions，按 `相关词命中`、`部分支撑`、`直接核心证据`、`冲突证据` 四档判断 prompt sufficiency；明确仍缺哪条权威文档或业务规则。只有关键 required assertions 已经有 direct support 且 Workflow 输出仍错，才允许把主因落到 `答案生成错误`。
- 知识状态：对关键 doc 读取 docDetail/knowledge detail 全文或记录信息，标注标题/正文中的 `停止更新`、`历史版本`、`临时`、`下线`、`废弃`、`已升级`、`过期`、`活动时效`、`不再维护`、`停止维护`、`旧版` 等信号；线上状态接口拿不到时明确写 `status_unconfirmed`。
- 实验反思：每个候选根因都要写“什么证据会推翻它”。

## 输出要求

每条 case 都必须产出一个短版人读结论，默认写入 `agent_judgement.md`，Agent 给用户的最终回复也应从这个结论改写而来。它应该像一次清楚的人工复盘：先说结论和核心原因，再解释证据链，不要像字段表或 checklist。不要把 `case_facts.json`、`recall_experiment.json`、`rerank_experiment.json`、`replay_experiment.json` 原文贴给用户，也不要让用户自己去读这些 JSON。JSON 是 Agent 的证据包，不是用户的阅读界面。

如果 `synthesize-brief` 只生成了 `candidate_cause: 待 Agent 判断` 的草稿，Agent 在完成归因后必须更新同一个 `agent_judgement.md`，把它改成最终短版结论；不要另建 `summary.md` 或在聊天里给一份、文件里留一份未完成草稿。

`agent_judgement.md` 是结论文件，不是提示词或 checklist。不要在里面写“Agent 最终回复要求”“给用户输出短版结论”这类面向 Agent 的写作要求；这类规则只保留在 `SKILL.md`、`references/report_contract.md` 或 `agent_brief.md`。

最终回复建议使用 5-7 个短段落或短列表。下面这些信息要在内容上覆盖，但不要求固定标题、固定顺序或固定表格：

- log_id、workspace_id、app_id；如果 replay 返回新的 log_id / trace_id，也必须写出；如果没有返回，要明确写“replay 未返回新的 log_id”。
- 原始 query、Workflow 输入、Workflow 输出、包装后的输出 / `answer_hint`。
- 总结提炼后的评估器信号，而不是整段原始 evaluator 文本直接堆叠；评估器事实正确性是低置信诊断线索，不是最终事实裁决。
- 表象摘要：答案具体错在哪里。
- 候选根因：至少列出 2 个合理解释，除非证据已明显排除。
- 证据对照：trace facts / recall / rerank / replay 分别支持或反驳什么。
- 证据充分性判断：列出 required assertions，给关键证据标注 `direct_support`、`partial_support`、`adjacent_support`、`insufficient` 或 `contradictory`，并说明是否足以产出严谨业务答案。
- rerank rank-shift：核心 doc、支撑 assertion、recall/rerank/prompt rank 和 score、是否进入 prompt、缺失原因、是否有真实 context boundary。
- 知识状态：关键 doc 的 `status_signals`、`status_confirmed`、`last_modified`、`status_reason`；未确认时写 `status_unconfirmed`。
- 反证意识：关键候选根因要写支持证据、已跑实验、什么会推翻它、当前判断；不要求用大表格。
- 归因整理：最终候选 cause 必须落在 6 个中文 cause 之一：`输入侧问题`、`知识缺失或证据不足`、`召回遗漏`、`重排丢失`、`答案生成错误`、`无明显错误/评估器不准，需人工进一步核实`；旧英文 slug 只作为兼容别名。
- 当前 judgement：最可信根因、置信度、仍缺的证据。
- `badcase_review_status`：`valid_badcase`、`needs_human_review_evaluator_disputed` 或 `not_badcase_evaluator_error`。这个字段独立于 cause，用于表达评估器是否可能误判或人工是否已确认。
- 如果 cause 是 `无明显错误/评估器不准，需人工进一步核实`，报告必须显式写出人工复核点；不要求固定范式，但要讲清楚为什么怀疑评估器不准、人工需要复核哪里、复核后可能如何改变结论。
- 如果使用 `needs_human_review_evaluator_disputed`，必须给出足够人工复核上下文，例如 query、judged answer、Workflow input/output、evaluator claim、关键 prompt 证据、为什么怀疑评估器误判。
- `not_badcase_evaluator_error` 只在人工确认或明确人工标注后使用。
- 下一步实验：如果证据不足，明确下一步该跑什么。

证据充分性要求：

- 先把正确答案应覆盖的点拆成 required assertions。
- `答案生成错误`（旧 slug: `answer_failure`）的 required assertions 只能来自 judged answer、当前 Workflow 输入 / rewrite、评估器信号和 prompt evidence；不要从 `chat_history` 补充答案正误依据。
- Prompt sufficiency gate 至少区分四档：`相关词命中`、`部分支撑`、`直接核心证据`、`冲突证据`。看到 prompt 里有相关词或泛化文档，不等于有足够回答用户问题的关键证据。
- 只有关键 required assertions 已经在 prompt evidence 中有 `direct_support`，且 Workflow 输出仍漏答、错引、越界或编造，才允许主因落到 `答案生成错误`。
- 对每条关键证据说明它支撑了哪条断言，以及支撑等级：`direct_support` 表示直接支撑；`partial_support` 表示只能支撑一部分；`adjacent_support` 表示主题相关但不能直接下结论；`insufficient` 表示看似相关但不能使用；`contradictory` 表示与其他证据冲突。
- 明确区分“证据足以解释为什么 replay 比历史答案好”和“证据足以产出严谨业务答案”。前者不等于后者。
- 如果证据组合有用但不完整，要写出仍缺的权威证据，例如“缺少明确说明是否自动扣费/自动续费/到期后如何处理的业务规则”。

证据展示硬约束：

- 不要在报告中只贴 `{"prompt_doc_ids": [...]}` 或类似裸 id 数组。
- 每个关键证据必须至少展示文档标题，并展示实际链接或援引片段；doc id 只能作为审计补充。
- 同一个必要断言的 recall / rerank / prompt 生存情况写在一起；不同断言分开展示。

证据索引建议：

- 默认使用本地 JSON 作为可索引事实来源：`case_facts.json`、`recall_experiment.json`、`rerank_experiment.json`、`replay_experiment.json`、`evidence_index.json`。
- Fornax 平台适合按历史 `log_id` 回查原始 trace；不要依赖飞书文档作为唯一证据库。
- 飞书文档适合作为分享/评审发布层；若用户明确要求发布，再把短版 `agent_judgement.md` 转成飞书文档。

## References

- `references/agent_judgement_v4.md`：v4 归因思路和 Agent 工作流。
- `references/symptom_to_root_cause.md`：表象到根因的对照框架。
- `references/evidence_kernel.md`：代码证据内核的边界。
- `references/recall_chain.md`：线上召回、重排、进入 prompt 的字段和接口链路。
- `references/report_contract.md`：报告字段、证据索引、可读证据展示的硬约束。
- `references/cases/`：三类 trace 获取模式的完整分析案例。
- `references/README.md`：当前 reference 索引。
