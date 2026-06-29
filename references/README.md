# FindReason v4 References

当前主线是 agent-judgement v4：

```text
collect evidence
  -> extract answer symptoms
  -> propose candidate root causes
  -> run recall / rerank / replay experiments
  -> Agent writes judgement
```

代码不再输出固定 `primary_cause`，也不再用“最早断点”硬裁决作为最终产品行为。旧 v3 文档只作为历史背景，不是当前入口。

## 核心口径

- Trace artifacts 是现场事实来源；外部 query、评估器信号和人工备注只是线索。
- `recall` 是人读名称，等于 `origin_doc_list + origin_faq_list`；报告中可以合并展示，但审计时保留两路原始字段。
- 线上召回链路要以 trace 中实际接口为准：常见 RAG QA 现场可能仍走旧 `/searchDoc` 主链路，新 `/recall`、`/rerank` 是拆分能力，不要只按接口名猜阶段。
- `qaPromptDocs` 不一定等于全部 `reRankDocs`；prompt/context 不作为 v4 顶层 cause，但必须作为证据是否真正给到模型的观察面。
- 代码只产出事实和实验结果，不替 Agent 选择根因。
- app-detail workflow 节点只是定位索引，Agent 必须建立 RAG stage map：query/input、preprocess、recall、rerank、prompt/context、generation、postprocess/final_output、evaluator/judged_object；不要把 workflow 节点类型直接等同于 RAG 阶段。
- Agent 先做 answer symptom extraction，再从表象生成多个候选解释。
- 每个候选解释都要有支持证据、反证证据和下一步实验。
- 最终报告以中文 cause 为主，旧 slug 只作为兼容别名；`输入侧问题` 必须通过验证 query 的召回改善、排序改善或 replay / 最终结果改善来上调主因。
- `无明显错误/评估器不准，需人工进一步核实`（slug: `evaluator_disputed_no_obvious_error`）不能作为“看不出来”的兜底；报告要写清人工复核点，`评估器输出暂无` 本身不是第 6 类证据。
- 如果证据不足，报告应写“不足以判断”的原因，而不是强行落标签。
- 报告必须展示可读证据：文档标题 + 实际链接或援引片段；不要只贴 `prompt_doc_ids` 这类裸 id 数组。
- 默认本地 JSON 作为可索引证据包，Fornax 按历史 `log_id` 回查原始 trace，飞书文档只作为分享/发布层。
- 默认执行面是本地脚本 + 宿主 LLM：优先 `--trace-file` 和本地 JSON/Markdown 产物；OpenPlat trace detail、live recall/replay、飞书文档发布都只是显式可选能力。

## 当前文档

- `agent_judgement_v4.md`：Agent 如何先建立 RAG stage map，再从现场侦查、答案症状和实验结果写 judgement。
- `symptom_to_root_cause.md`：表象到候选根因的对照表，来自用户指定飞书文档第三节的方法论。
- `evidence_kernel.md`：代码层边界，说明哪些事必须代码化，哪些 RAG 阶段识别必须留给 Agent。
- `recall_chain.md`：召回、重排、进入 prompt 的真实链路、字段解释和排查抓手。
- `report_contract.md`：报告必填字段、证据索引、可读证据展示和归因表约束。
- `capabilities.json`：v4 CLI capability manifest。
- `cases/019eee75-local-trace-workflow-input-loss.md`：本地 trace JSON 已有时的完整归因案例。
- `cases/019eef8d-rerun-input-knowledge-missing.md`：原始 Fornax trace 不可用、需按原始输入/再生产物重跑时的完整归因案例。
- `cases/019ece69-logid-trace-retrieval-miss.md`：可通过 workspace + log_id 拉 trace，并深挖 expected doc 的完整归因案例。

## 已退出主流程

- `orchestrate`
- 固定 `candidate_cause` / `primary_cause`
- earliest failing stage 硬裁决
- 旧 v3 回归测试作为目标行为
- 把答案症状零散摊成多个顶层 cause
- 旧 v3 reference 文档已经移除；需要历史细节时查 git history，不要让当前 Agent 读取旧规则作为工作流依据。
