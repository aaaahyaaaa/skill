# Agent Attribution Planning v3.1

本 playbook 描述宿主 Agent 如何把 trace 现场和评估器线索转成可验证的 assertion set 与 `probe-v1` plan。CLI 只负责执行确定性检查、绑定 evidence 和最终 `orchestrate`，不负责语义发明。

## 输入

宿主 Agent 消费：

- `trace_artifacts`：真实 workflow 输入、输出、中间节点和脚本 I/O。trace 可包含 query、chat history、rewrite query、keywords、origin docs、ranked docs、prompt/context、citation mapping、final output、workflow graph。
- `judgement_evidence.signals`：评估器压缩摘要，只表达“怀疑哪里坏了”。
- `query_hint` / `answer_hint`：外部加工文本，仅在 trace 缺失时辅助理解。

真实 query 和 output 优先来自 trace。外部 `query` / `answer` 不得覆盖 trace。

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

## 输出

Agent planning 一次性输出两个对象：

1. Assertion set，落到兼容字段 `host_agent.answer_claim`。
2. `probe-v1` plan，交给 `run-probe-plan` 执行。

### Assertion Set

核心 role：

- `expected_required`：正确输出应覆盖的检查点。
- `answer_claim`：从 workflow output 中抽出的可验证命题 X。

兼容 role：

- `missing_expected`：legacy 输入，归一化为 `expected_required`，并保留 `evaluator_hint.omitted=true`。
- `constraint_check`、`citation_check`、`consistency_check`：用于 probe planning，不直接驱动上游归因。
- `unsupported_claim`：answer 阶段线索，不触发 `suspected_knowledge_missing`。

`expected_required` 可基于 `trace_query`、`chat_history`、`evaluator_reason`、`rewrite_query`、`keywords` 推断，但必须写清 `basis` 和 `why_required`。它是验证靶子，不是事实证据。

`answer_claim.text` 必须是命题 X：

```json
{
  "text": "品牌客户可以在后台的“品牌投放-品牌竞价”找到一元试投入口。",
  "role": "answer_claim",
  "basis": ["workflow_output"],
  "confidence": 0.8
}
```

不要写成：

```json
{
  "text": "答案称品牌客户可以在后台的“品牌投放-品牌竞价”找到一元试投入口。",
  "role": "answer_claim"
}
```

### Probe Plan

`probe-v1` plan 把 assertion set 和评估器怀疑点转成可执行检查。Plan 本身不是证据。

常用 mapping：

| 检查目的 | direction | target_artifact |
|-|-|-|
| 正确答案要求是否有 KB 上界支撑 | `coverage_gap` | `kb_wide_recall` |
| KB 支撑是否进入线上初召回 | `coverage_gap` | `online_origin_recall` |
| 初召回支撑是否经过 rank / rerank | `coverage_gap` | `rerank_output` |
| 支撑是否进入 prompt/context | `coverage_gap` | `prompt_context` |
| 输出是否越过用户约束范围 | `scope_violation` | `answer_span` 或 `kb_wide_recall` |
| 引用是否存在且支撑 claim | `citation_missing` | `online_origin_recall` / `rerank_output` / `prompt_context` |
| KB 或输出是否存在冲突 | `internal_contradiction` | `kb_wide_recall` / `answer_span` |

## 归因边界

### `expected_required`

`expected_required` 驱动上游覆盖链：

```text
KB / wide recall 不支撑 -> suspected_knowledge_missing 或 human_review
KB 支撑，origin 未召回 -> retrieval_miss
origin 支撑，rank/rerank 丢失 -> rerank_drop
rank 后支撑，prompt/context 丢失 -> context_assembly_error
prompt/context 支撑，output 未覆盖 -> partial_answer
prompt/context 支撑，output 越界 -> answer_scope_violation
```

如果 workflow 只输出文档，没有自然语言答案，最后一步检查 `output_artifact` 是否包含能支撑 `expected_required` 的文档；answer 阶段通常不适用。

### `answer_claim`

`answer_claim` 验证 output 是否 grounded：

```text
prompt/context 支撑 -> grounded
prompt 不支撑，且 KB / 引用也不支撑 -> unsupported_claim
prompt 不支撑，但 KB 另有支撑 -> answer grounding 问题，不反推 retrieval/rerank
引用文档不支持 claim -> wrong_citation
claim 与 expected_required 的约束不匹配 -> answer_scope_violation
claims 内部分支混用 -> answer_branching_unclear
```

### Citation

```text
没有引用且任务不要求引用 -> 不归因
引用链接不可用 -> wrong_citation，subtype=citation_unavailable
引用文档存在但不支持 claim -> wrong_citation
需要官方来源，KB 无权威来源 -> suspected_knowledge_missing
KB 有官方来源但 origin 未召回 -> retrieval_miss
origin 有但 prompt/context 未承接 -> context_assembly_error
```

### Consistency

```text
KB 自身冲突且无清晰适用前提 -> knowledge_internal_inconsistency
KB 有清晰前提但 output 混用分支 -> answer_branching_unclear
prompt/context 拼入冲突材料且未提供可区分前提 -> context_assembly_error 或 answer_branching_unclear
```

## 禁止事项

- 不要让评估器标签直接决定 `primary_cause`。
- 不要让 CLI 从 query、judgement 或 rubric 长文本中自动创造 `expected_required`。
- 不要把 `probe-v1` plan 当证据；只有执行结果的 hit/miss、matched docs、support spans 和 evidence IDs 才是证据。
- 不要在缺少上游证据时直接下沉到 answer cause。
