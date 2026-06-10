# Agent Attribution Planning v3.1

本 playbook 描述宿主 Agent 如何把 trace 现场、评估器线索和执行中发现的异常转成 assertion set 与动态 `{stage}-exp`。CLI 只负责执行确定性检查、绑定 evidence 和最终 `orchestrate`，不负责语义发明。

## 输入

宿主 Agent 消费：

- `trace_artifacts`：真实 workflow 输入、输出、中间节点和脚本 I/O。trace 可包含 Workflow 原始输入、用户上下文、rewrite query、keywords、origin docs、ranked docs、prompt/context、citation mapping、final output、workflow graph。
- `judgement_evidence.signals`：评估器压缩摘要，只表达“怀疑哪里坏了”。
- `query_hint` / `answer_hint`：外部加工文本，仅在 trace 缺失时辅助理解。

用户实际问题、评估器问题线索、Workflow 原始输入和预处理输出必须分开看。外部 `query` / `answer` 不得覆盖 trace；`case_input.query` 如果来自表格或评估器，只能作为用户实际问题/评估问题线索。Workflow 原始输入必须从 `raw_artifacts.workflow_span_ios[].input` 读取；`rewrite_query` / `keywords` 是 Workflow 内部预处理节点输出。

## Artifact 抽象

不要锁死固定节点名。先把 workflow 现场归一为可选 artifacts：

| Artifact | 含义 |
|-|-|
| `input_artifact` | workflow 真实输入、query、chat history |
| `preprocess_artifact?` | rewrite query、keywords、route |
| `retrieval_artifact?` | origin docs / FAQ / 多路召回 |
| `rank_artifact?` | rerank、merge、script reorder 输出 |
| `context_artifact?` | prompt docs、assembled context |
| `output_artifact` | 最终输出；可能是自然语言答案、文档列表或结构化结果 |
| `citation_artifact?` | 引用链接、doc mapping、source spans |
| `workflow_graph_artifact?` | nodes、edges、global_config |
| `script_io_artifact?` | 复杂脚本输入输出 |

缺失的 artifact 表示对应阶段 `not_applicable` 或证据不足，不是自动失败。

## 动态诊断 Backlog

Agent attribution planning 是持续发生的。宿主 Agent 在读取输入、评估器、trace、答案、引用和 chunk 时，随时把线索加入动态诊断 backlog。Backlog 的目标是决定要构造哪些 assertion、哪些 `{stage}-exp`、报告重点放在哪里；它不是 `primary_cause`，也不能替代执行后的 evidence。

观察顺序：

1. Workflow input/output：从 trace 的 `workflow_span_ios` 或 Start/End span 读取真实输入输出，和外部 `query_hint` / `answer_hint` 分开。
2. Preprocess：记录 rewrite query 是否改变语义、keywords 是否保留关键限制。
3. 文档生存路径：摊平 `origin -> rerank -> prompt`，对每个候选记录 doc id、chunk id、title、source、score、support snippet、阶段存活状态。
4. 来源风险：标记标题或正文含“停用 / 已升级 / 过期 / deprecated / stale”的来源、重复 chunk、非权威来源、引用链接不可用或引用文档不支撑 claim。
5. prompt-vs-answer alignment：检查 prompt 中出现但答案遗漏的限制、反例、预算/ROI/适用范围约束；检查答案是否弱化或反向表达 prompt 已有约束。

每个 backlog item 至少包含：

- `trigger_source`：线索来源，例如 evaluator、trace、rewrite、origin、rerank、prompt、answer、citation、chunk。
- `trigger_observation`：观察到的现象。
- `hypothesis`：待验证假设。
- `exp_kind`：`retrieval-exp`、`rerank-exp`、`answer-exp`、`citation-exp`、`chunk-conflict-exp`。
- `target_stage`：预期影响的阶段。
- `expected_evidence`：执行后希望看到的证据类型。

输出时把 backlog 转成两类计划输入：

- `expected_required`：正确输出必须覆盖的检查点，通常来自真实 workflow 输入、评估器用户上下文、prompt 中的关键限制和现场观察发现的生存路径断点。
- `{stage}-exp`：验证召回、重排、答案、引用或 chunk 冲突 hypothesis。兼容 `run-probe-plan` 的静态 artifact 验证项可继续用 `probe-v1` 表达。

输入边界优先级：

```text
用户实际问题 / 评估器用户上下文
        |
        v
Workflow 原始输入
        |
        v
预处理输出 rewrite_query / keywords
```

如果用户实际问题或评估器用户上下文中的关键场景约束没有进入 Workflow 原始输入，先记录输入边界风险；只有受影响的 `expected_required` 在理论召回上界可支撑、但 online origin recall 缺失时，才归因到 `workflow_input_loss`。如果同一断言已经被 online origin / rerank / prompt 支撑，继续判断下游阶段。若 Workflow 原始输入保留了这些约束，但 rewrite / keywords 丢失并影响召回，也并入 `workflow_input_loss`。

## 输出

Agent planning 输出两个对象，并可在执行过程中增量更新：

1. Assertion set，落到唯一输入字段 `host_agent.answer_claim`。
2. `{stage}-exp` backlog；其中静态 artifact 验证可合并成兼容 `probe-v1` plan，交给 `run-probe-plan` 执行。

### Assertion Set

核心 role：

- `expected_required`：正确输出应覆盖的检查点。
- `answer_claim`：从 workflow output 中抽出的可验证命题 X。

检查 role：

- `constraint_check`、`citation_check`、`consistency_check`：用于 probe planning，不直接驱动上游归因。
- `unsupported_claim`：answer 阶段线索，不触发 `suspected_knowledge_missing`。

不要输出旧 role 或字段别名。字段契约只保留最新约定字段，CLI 不会把旧 role 自动映射成当前 role。

`expected_required` 可基于 `trace_query`、`chat_history`、`evaluator_reason`、`rewrite_query`、`keywords` 推断，但必须写清 `basis` 和 `why_required`。它是验证靶子，不是事实证据。宿主 Agent 可以把“场景约束”和“入口要求”拆成相邻断言交给 CLI；CLI 只会在两条断言构成“场景约束 + 同一入口/路径要求”的包含或细化关系时保守合并，合并后的 `basis` 取并集，原始断言放到 `merged_from` 审计字段，覆盖矩阵只保留合并行。

`answer_claim.text` 必须是命题 X：

```json
{
  "text": "品牌客户可以在后台的“品牌投放-品牌竞价”找到一元试投入口。",
  "role": "answer_claim",
  "basis": ["workflow_output"]
}
```

不要写成：

```json
{
  "text": "答案称品牌客户可以在后台的“品牌投放-品牌竞价”找到一元试投入口。",
  "role": "answer_claim"
}
```

### 验证环节

`{stage}-exp` 把 assertion set、评估器怀疑点和 trace 观察转成可执行验证。Backlog / plan 本身不是证据。

常用 mapping：

| 检查目的 | direction | target_artifact |
|-|-|-|
| `retrieval-exp`：正确答案要求是否有 KB 上界支撑 | `coverage_gap` | `kb_wide_recall` |
| `retrieval-exp`：KB 支撑是否进入线上初召回 | `coverage_gap` | `online_origin_recall` |
| `rerank-exp`：初召回支撑是否经过 rank / rerank | `coverage_gap` | `rerank_output` |
| `context-exp`：支撑是否进入 prompt/context | `coverage_gap` | `prompt_context` |
| `answer-exp`：输出是否越过用户约束范围 | `scope_violation` | `answer_span` 或 `kb_wide_recall` |
| `citation-exp`：引用是否存在且支撑 claim | `citation_missing` | `online_origin_recall` / `rerank_output` / `prompt_context` |
| `chunk-conflict-exp`：召回/prompt chunk 是否存在冲突 | `internal_contradiction` | `online_origin_recall` / `rerank_output` / `prompt_context` |

现场观察到的典型分支：

| 观察 | assertion / probe 倾向 |
|-|-|
| origin 有支撑、rerank 或 prompt 丢失 | `expected_required` + `rerank-exp` / `context-exp` |
| rerank 后已有必要支撑但答案遗漏 | `expected_required` + `answer-exp`，记录 `missing_aspect` |
| 答案引用停用或 stale 文档 | `answer_claim` + `citation-exp`，检查引用是否支撑且是否有更新来源 |
| prompt 混入冲突材料但答案未区分前提 | `chunk-conflict-exp` 或 `answer-exp` |
| 文档只标题命中、正文无支撑 | 不计入覆盖；生成更具体的 `expected_required` 或 wide recall 检查 |

## 归因边界

### `expected_required`

`expected_required` 驱动上游覆盖链：

```text
用户约束未进入 Workflow 原始输入，且受影响断言上界可支撑但线上初召回缺失 -> workflow_input_loss
Workflow 输入保留约束，但 rewrite / keywords 丢失并影响召回 -> workflow_input_loss
KB / wide recall 不支撑 -> suspected_knowledge_missing 或 human_review
KB 支撑，origin 未召回 -> retrieval_miss
origin 支撑，rank/rerank 丢失 -> rerank_drop
rank 后支撑，prompt/context 丢失 -> prompt/context 观察，不作为顶层主因
rerank 后有必要支撑，output 未覆盖 -> answer_failure + missing_aspect
rerank 后有必要支撑，output 越界 -> answer_failure + scope_violation
```

如果 workflow 只输出文档，没有自然语言答案，最后一步检查 `output_artifact` 是否包含能支撑 `expected_required` 的文档；answer 阶段通常不适用。

### `answer_claim`

`answer_claim` 验证 output 是否 grounded：

```text
rerank 后支撑 -> grounded
rerank 后不支撑，且 KB / 引用也不支撑 -> 上游知识或召回问题；unsupported_claim 只作为答案症状
prompt 不支撑，但 KB 另有支撑 -> answer grounding 问题，不反推 retrieval/rerank
引用文档不支持 claim -> answer_failure + wrong_citation
claim 与 expected_required 的约束不匹配 -> answer_failure + scope_violation
claims 内部分支混用 -> answer_failure + scope_violation
```

### Citation

```text
没有引用且任务不要求引用 -> 不归因
引用链接不可用 -> answer_failure + wrong_citation，subtype=citation_unavailable
引用文档存在但不支持 claim -> answer_failure + wrong_citation
需要官方来源，KB 无权威来源 -> suspected_knowledge_missing
KB 有官方来源但 origin 未召回 -> retrieval_miss
origin 有但 rerank 丢失 -> rerank_drop
rerank 有但 prompt/context 未承接 -> prompt/context 观察
```

### Consistency

```text
KB 自身冲突且无清晰适用前提 -> suspected_knowledge_missing
KB 有清晰前提但 output 混用分支 -> answer_failure + scope_violation
prompt/context 拼入冲突材料且未提供可区分前提 -> chunk_conflict 观察；若答案混用分支则 answer_failure + scope_violation
```

## 禁止事项

- 不要让评估器标签直接决定 `primary_cause`。
- 不要让 CLI 从 query、judgement 或 rubric 长文本中自动创造 `expected_required`。
- 不要把 `probe-v1` plan 当证据；只有执行结果的 hit/miss、matched docs、support spans 和 evidence IDs 才是证据。
- 不要在缺少上游证据时直接下沉到 answer cause。
