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
- 归一化 `origin_doc_list`、`origin_faq_list`、`rerank_docs`、`prompt_docs`。
- 把 `origin_doc_list + origin_faq_list` 汇总为人读层的 `recall`，同时保留原始字段用于审计。具体线上召回链路见 `references/recall_chain.md`。
- 运行或规划 recall / rerank / replay 实验。
- 输出 `case_facts.json`、`*_experiment.json`、`agent_brief.md`。
- 用 `synthesize-brief` 生成短版 `agent_judgement.md` 和 `evidence_index.json` 本地证据索引。

Agent 负责：

- 现场侦查怎么想。
- answer symptom extraction 怎么做。
- 哪些候选解释合理。
- 怎么规划下一步 probe / experiment。
- 怎么写人读报告。
- 不同阶段的边界、反思流程和最终 judgement。

## 主流程

1. 用 `collect-evidence` 固化 trace 和 RAG artifacts。
2. 读取 `case_facts.json` 与 `agent_brief.md`，先抽答案症状，再列候选根因。
3. 对每个候选根因写明支持证据、反证证据和下一步实验。
4. 需要时用 `run-experiment` 规划或运行 recall / rerank / replay 实验。
5. 运行 `synthesize-brief` 生成短版 judgement summary 和证据索引。
6. Agent 基于事实和实验结果给用户输出短版结论，风格类似一次清晰复盘回复；不要要求用户阅读 JSON 或长报告。

## 命令

```bash
python -m findreason collect-evidence \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

如果已有本地 trace JSON，可跳过线上拉取：

```bash
python -m findreason collect-evidence \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --trace-file /path/to/trace.json \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case
```

实验入口：

```bash
python -m findreason run-experiment --type recall --facts-file /tmp/findreason-case/case_facts.json --query "<实验 query>" --output-dir /tmp/findreason-case
python -m findreason run-experiment --type rerank --facts-file /tmp/findreason-case/case_facts.json --target-doc-id <doc_id> --output-dir /tmp/findreason-case
python -m findreason run-experiment --type replay --facts-file /tmp/findreason-case/case_facts.json --query "<真实用户问题>" --app-id <app_id> --output-dir /tmp/findreason-case
```

不传 `--query` 时，recall 实验只输出 query variant 规划；传入 `--query` 且 trace 中有 recall/searchDoc 请求模板时，代码会复用模板执行一次 query override。rerank 实验输出 doc-id 生存观察，不直接等价为同断言丢失。

报告合成入口：

```bash
python -m findreason synthesize-brief \
  --facts-file /tmp/findreason-case/case_facts.json \
  --experiment-dir /tmp/findreason-case \
  --output-dir /tmp/findreason-case
```

默认产物：

- `agent_judgement.md`：每条 case 的唯一短版人读 judgement 结论文件，包含当前结论入口、答案症状、上游证据链、关键证据和本地证据包位置；不要再额外维护一份 `summary.md` / `summary` 人读副本，避免两份结论漂移。
- `evidence_index.json`：本地可索引证据包，包含 trace/replay/实验文档的 title、url、snippet、rank、score。

查看 v4 manifest：

```bash
python -m findreason schema
```

## 现场侦查

先看表象，再找根因，不要一开始就落标签。

- 输入表象：用户真实问题、评估器上下文、Workflow 原始输入、rewrite、keywords 是否一致。
- `chat_history` 只能用于判断 `workflow_input_loss`：对比用户上下文是否进入 Workflow input / rewrite / keywords；不得用它支撑 `answer_failure` 的答案正误判断。
- 如果怀疑 `workflow_input_loss`，用上下文增强 query 做 recall / replay 对照，检查是否比原 query 召回更多能明确回答问题的直接支撑文档。
- 答案表象：漏答、错引、越界、自相矛盾、无依据断言、把弱证据写成强结论。
- 证据生存：同一必要断言是否从 `recall` 进入 `rerank_docs`，再进入 `prompt_docs` / `qaPromptDocs`。
- 召回边界：报告里可叫 `recall`，但审计时要区分 `origin_doc_list` 与 `origin_faq_list`；线上链路可能来自旧 `/searchDoc`，也可能来自新拆分 `/recall` 能力，以 trace 为准。
- 知识边界：宽召回是否仍无权威支撑，还是只有相邻主题。
- 证据充分性：把用户问题拆成 required assertions，判断关键证据组合是完整支撑、部分支撑还是只解释了 replay 改善；明确仍缺哪条权威文档或业务规则。
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
- 反证意识：关键候选根因要写支持证据、已跑实验、什么会推翻它、当前判断；不要求用大表格。
- 归因整理：最终候选 cause 必须落在 `workflow_input_loss`、`suspected_knowledge_missing`、`retrieval_miss`、`rerank_drop`、`answer_failure` 这 5 类之一；证据不足时写低置信或人工复核，不强行裁决。
- 当前 judgement：最可信根因、置信度、仍缺的证据。
- `badcase_review_status`：`valid_badcase`、`needs_human_review_evaluator_disputed` 或 `not_badcase_evaluator_error`。这个字段独立于五类 cause，用于表达评估器是否可能误判。
- 如果使用 `needs_human_review_evaluator_disputed`，必须给出 `human_review_reason` 和 `human_review_context`，至少包含 query、judged answer、Workflow input/output、evaluator claim、关键 prompt 证据、为什么怀疑评估器误判。
- `not_badcase_evaluator_error` 只在人工确认或明确人工标注后使用。
- 下一步实验：如果证据不足，明确下一步该跑什么。

证据充分性要求：

- 先把正确答案应覆盖的点拆成 required assertions。
- `answer_failure` 的 required assertions 只能来自 judged answer、当前 Workflow 输入 / rewrite、评估器信号和 prompt evidence；不要从 `chat_history` 补充答案正误依据。
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
- `references/README.md`：当前 reference 索引。
