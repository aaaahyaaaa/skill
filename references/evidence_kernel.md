# Evidence Kernel v4

Evidence Kernel 是 FindReason v4 的代码层。它只回答“现场事实是什么、实验结果是什么”，不回答“最终根因是什么”。

## 代码负责

- 拉取 Fornax trace detail。
- 解析 Workflow 原始输入/输出、rewrite、keywords、answer。
- 解析 app-detail `workflowConfigV2` 中的真实 workflow 节点、边和节点输入输出字段，并映射 Fornax trace span。
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
- `trace.workflow_topology`：来自 app-detail 的真实节点、边、顺序和映射状态。
- `trace.node_evidence_map`：每个真实节点对应的 trace spans、input/output keys、证据字段位置和计数。
- `trace.prompt_observation`：说明 `prompt_docs` / `qaPromptDocs` 是从知商问答、大模型、脚本/后处理节点观测到，还是未观测到。`not_observed` 只表示解析面未看到，不等于模型没有证据。
- `trace.agent_span_read_plan`：按 6 类中文 cause 给 Agent 的候选 span 读取入口；它不是 CLI 的 cause 裁决。
- `preprocess`：rewrite query、keywords。
- `artifacts`：四类 RAG 文档列表。
- `counts`：recall / rerank / prompt 数量。
- `answer`：workflow 输出答案。
- `citation_observations`：引用相关观察。
- `experiment_inputs.recall_templates`：从 trace 里抽取的 recall/searchDoc 请求模板，Authorization 和 token 类 header 会脱敏。
- `agent_contract`：提醒 Agent 代码只产事实，不产根因。

`case.chat_history` 如果存在，只能作为 `输入侧问题`（旧 slug: `workflow_input_loss`）的对照材料：用来检查用户上下文、限定词、实体或子问题是否在 Workflow input / rewrite / keywords / query variants 中失真。它不能作为 `答案生成错误`（旧 slug: `answer_failure`）的答案正误依据。

`agent_brief.md` 是给宿主 Agent 的简明工作单。它应该帮助 Agent 快速进入“表象 -> 候选根因 -> 实验验证”的思考，并展示 case-specific 的输入输出、评估器摘要和可读证据样例。

`agent_judgement.md` 是 `synthesize-brief` 生成的短版人读 judgement 文件，也是每条 case 的唯一人读结论。它必须像 Agent 最终回复一样直接可读：包含当前结论入口、答案症状、上游证据链、关键证据、证据充分性判断、本地证据包和仍缺证据。不要把长表格、完整 JSON、大段证据清单，或“Agent 最终回复要求”这类写作 checklist 放进这个文件。

`evidence_index.json` 是本地索引用事实包。它只保存 trace/replay/实验中出现的文档 title、url、snippet、rank、score 等可检索证据，不承载报告 checklist 或 review contract。证据充分性标注规则保留在 `SKILL.md` 和 `report_contract.md`，推荐把本地 JSON 作为可索引证据来源；Fornax 平台按历史 log_id 回查原始 trace；飞书文档只作为发布/评审层。

## Recall 命名

`recall` 是人读层名称，不是线上单一字段。证据内核应同时保留：

- `origin_doc_list`：文档召回。
- `origin_faq_list`：FAQ / 精选召回。
- `recall`：二者汇总，便于报告表达。

线上接口链路以 trace 为准。常见 RAG QA 现场可能仍从旧 `/api/sirius_plugin/v1/searchDoc` 进入 `RecallController`，再路由到 `doc_search`、`self_dataset_search` 或 `featured_search`；新 `/api/sirius_plugin/v1/recall` 是拆分后的 recall 能力，不能仅凭接口名反推历史现场。

## Prompt 观察面

v4 当前不把 prompt/context 作为顶层归因阶段，但代码仍应保留 `prompt_docs` / `qaPromptDocs`。原因是最终给 LLM 的不一定是全部 `rerank_docs`：`maxInputToken`、`maxInputSize`、`topP` 等参数会影响证据是否进入 prompt。

因此，Agent 判断 `答案生成错误`（旧 slug: `answer_failure`）时应看必要断言是否真的进入 `prompt_docs`；如果证据只停在 `rerank_docs`，报告应把它写成证据边界或下一步实验，而不是直接认定模型忽略了证据。

如果 `trace.prompt_observation.status=not_observed`，不得写成“全部证据被过滤”或“模型零证据生成”。应先根据 `trace.node_evidence_map` 和 `trace.agent_span_read_plan` 回查候选大模型、知商问答、脚本和后处理节点的原始 input/output。只有定位到真实 prompt-entry 边界且该边界为空，才能写成 confirmed empty。

判断 `答案生成错误` 时不得引用 `chat_history` 补强断言。`答案生成错误` 只基于 judged answer、当前 Workflow 输入 / rewrite、评估器信号和 `prompt_docs` / `qaPromptDocs` 的证据充分性。如果怀疑输入侧失真，应根据验证点改写后的 query 跑 recall / rerank / replay 对照；只有观察到召回改善、排序改善，或 replay / 最终结果改善时，才能把主因上调为 `输入侧问题`。如果改写 query 没改善实验结果，只能写成低置信候选或待验证点。

最终报告以中文 cause 为主，旧 slug 只作为兼容别名。第 6 类 `无明显错误/评估器不准，需人工进一步核实`（slug: `evaluator_disputed_no_obvious_error`）不能作为“看不出来”的兜底；报告必须显式写出人工复核点，不要求固定范式，但要讲清楚为什么怀疑评估器不准以及要复核的地方。`评估器输出暂无` 本身不是第 6 类证据。

## 实验

默认优先本地执行：有本地 trace 时传 `collect-evidence --trace-file`，再按需运行本地 rerank 生存观察、`synthesize-brief` 和宿主 LLM judgement。这样单 case 归因不依赖 Fornax、飞书、浏览器、MCP 或子 Agent 工具即可完成主要分析。

`run-experiment --type recall`：
不传 `--query` 时只规划 query variants；传入 `--query` 且 `case_facts` 中有 `experiment_inputs.recall_templates` 时，会复用 trace 中 recall/searchDoc 请求模板执行 query override。实验结果只描述召回命中、过滤和证据覆盖，不直接输出根因。

`run-experiment --type rerank`：
输出 recall 到 `rerank_docs` / `prompt_docs` 的 doc-id 生存观察。doc id 消失只是观察；Agent 仍必须检查是否丢失“同一必要断言”的支撑。

`run-experiment --type replay`：
真实调用 workflow replay，返回当前版本运行下的 origin/rerank/prompt/answer 证据。replay 的线上链路只走接口：OpenPlat workspace info API 先用固定 open-api token 获取 workspace 级 `authInfo.apiKey`，OpenPlat app detail API 再用该 workspace apiKey 和 `workspaceId` header 获取 `workflowConfigV2`，open-exec workflow API 最后用同一个 workspace apiKey 执行重跑。用户提供 `--version-id` / `--app-version` 时作为 `appVersion` 传给 app detail；未提供时不传 `appVersion`，由平台接口取最新版本。replay 是实验结果，不覆盖原始 Fornax 现场。

`/open-plat/api/app/get-app-detail` 不是固定 `x-zs-plt-open: zs_open` token 的白名单接口。调用 app-detail 时需要同时保留 query params `workspaceId` / `appId` / 可选 `appVersion`，并在 header 中带 `workspaceId` 与 `Authorization: Bearer <workspace apiKey>`；否则入口 `AuthFilter` 会落到 Kani/cookie 登录态兜底，可能报「鉴权失败，用户登录状态异常」。

真实 case 验证时应同时保存历史 `case_facts.json` 和 `replay_experiment.json`：前者回答“历史现场是什么”，后者回答“当前版本重跑是什么”。如果两者 recall/rerank/prompt/answer 不一致，报告应把这种差异当作反事实实验发现，而不是合并成一个阶段事实。
