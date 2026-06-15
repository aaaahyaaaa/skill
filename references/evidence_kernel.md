# Evidence Kernel v4

Evidence Kernel 是 FindReason v4 的代码层。它只回答“现场事实是什么、实验结果是什么”，不回答“最终根因是什么”。

## 代码负责

- 拉取 Fornax trace detail。
- 解析 Workflow 原始输入/输出、rewrite、keywords、answer。
- 归一化：
  - `origin_doc_list`
  - `origin_faq_list`
  - `rerank_docs`
  - `prompt_docs`
- 生成人读 `recall` 计数：`origin_doc_list + origin_faq_list`。
- 保存 `case_facts.json` 和 `agent_brief.md`。
- 运行或规划 recall / rerank / replay 实验。
- 保留 doc id、title、content、url、rank、score、source 等可审计字段。
- 合成短版 `agent_judgement.md` 与 `evidence_index.json` 证据索引。

## 代码不负责

- 不选择 `primary_cause`。
- 不执行“最早断点”裁决。
- 不把答案症状直接折叠成阶段标签。
- 不把 evaluator 的失败维度当成根因。
- 不用 Python 规则替代 Agent 的候选解释和反思。
- 不把 doc id 数组当作人读证据；报告层必须展示标题、链接或援引片段。

## 输出

`case_facts.json` 是事实包：

- `case`：用户问题、评估线索、case id。
- `trace`：trace 来源、中间节点、workflow input/output。
- `preprocess`：rewrite query、keywords。
- `artifacts`：四类 RAG 文档列表。
- `counts`：recall / rerank / prompt 数量。
- `answer`：workflow 输出答案。
- `citation_observations`：引用相关观察。
- `experiment_inputs.recall_templates`：从 trace 里抽取的 recall/searchDoc 请求模板，Authorization 和 token 类 header 会脱敏。
- `agent_contract`：提醒 Agent 代码只产事实，不产根因。

`case.chat_history` 如果存在，只能作为 `workflow_input_loss` 的对照材料：用来检查用户上下文是否丢在 Workflow input / rewrite / keywords 之前。它不能作为 `answer_failure` 的答案正误依据。

`agent_brief.md` 是给宿主 Agent 的简明工作单。它应该帮助 Agent 快速进入“表象 -> 候选根因 -> 实验验证”的思考，并展示 case-specific 的输入输出、评估器摘要和可读证据样例。

`agent_judgement.md` 是 `synthesize-brief` 生成的短版人读 judgement 文件，也是每条 case 的唯一人读结论。它必须像 Agent 最终回复一样直接可读：包含当前结论入口、答案症状、上游证据链、关键证据、证据充分性判断、本地证据包和仍缺证据。不要把长表格、完整 JSON、大段证据清单，或“Agent 最终回复要求”这类写作 checklist 放进这个文件。

`evidence_index.json` 是本地索引用事实包。它保存 trace/replay/实验中出现的文档 title、url、snippet、rank、score，并包含 `sufficiency_review_contract`，提醒下游用 `direct_support` / `partial_support` / `adjacent_support` / `insufficient` / `contradictory` 标注证据充分性。推荐把本地 JSON 作为可索引证据来源；Fornax 平台按历史 log_id 回查原始 trace；飞书文档只作为发布/评审层。

## Recall 命名

`recall` 是人读层名称，不是线上单一字段。证据内核应同时保留：

- `origin_doc_list`：文档召回。
- `origin_faq_list`：FAQ / 精选召回。
- `recall`：二者汇总，便于报告表达。

线上接口链路以 trace 为准。常见 RAG QA 现场可能仍从旧 `/api/sirius_plugin/v1/searchDoc` 进入 `RecallController`，再路由到 `doc_search`、`self_dataset_search` 或 `featured_search`；新 `/api/sirius_plugin/v1/recall` 是拆分后的 recall 能力，不能仅凭接口名反推历史现场。

## Prompt 观察面

v4 当前不把 prompt/context 作为顶层归因阶段，但代码仍应保留 `prompt_docs` / `qaPromptDocs`。原因是最终给 LLM 的不一定是全部 `rerank_docs`：`maxInputToken`、`maxInputSize`、`topP` 等参数会影响证据是否进入 prompt。

因此，Agent 判断 `answer_failure` 时应看必要断言是否真的进入 `prompt_docs`；如果证据只停在 `rerank_docs`，报告应把它写成证据边界或下一步实验，而不是直接认定模型忽略了证据。

判断 `answer_failure` 时不得引用 `chat_history` 补强断言。`answer_failure` 只基于 judged answer、当前 Workflow 输入 / rewrite、评估器信号和 `prompt_docs` / `qaPromptDocs` 的证据充分性。如果怀疑上下文丢失，应构造上下文增强 query 跑 recall / replay 对照；只有增强后召回更多可明确回答问题的直接支撑文档，且原 Workflow 输入确实缺少这些上下文时，才增强 `workflow_input_loss` 判断。

## 实验

`run-experiment --type recall`：
不传 `--query` 时只规划 query variants；传入 `--query` 且 `case_facts` 中有 `experiment_inputs.recall_templates` 时，会复用 trace 中 recall/searchDoc 请求模板执行 query override。实验结果只描述召回命中、过滤和证据覆盖，不直接输出根因。

`run-experiment --type rerank`：
输出 recall 到 `rerank_docs` / `prompt_docs` 的 doc-id 生存观察。doc id 消失只是观察；Agent 仍必须检查是否丢失“同一必要断言”的支撑。

`run-experiment --type replay`：
真实调用 workflow replay，返回当前版本运行下的 origin/rerank/prompt/answer 证据。replay 是实验结果，不覆盖原始 Fornax 现场。

真实 case 验证时应同时保存历史 `case_facts.json` 和 `replay_experiment.json`：前者回答“历史现场是什么”，后者回答“当前版本重跑是什么”。如果两者 recall/rerank/prompt/answer 不一致，报告应把这种差异当作反事实实验发现，而不是合并成一个阶段事实。
