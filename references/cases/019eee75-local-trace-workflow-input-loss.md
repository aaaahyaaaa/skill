# Case 019eee75: local trace JSON -> 输入侧问题 (workflow_input_loss)

## 适用场景

mode: `local_trace_json`

当用户提供或本机已经存在 trace JSON 时，优先走本地文件，不要再卡在 Fornax trace 拉取。这个案例来自 thread `019eee75-59cc-7b82-9dae-368f21808b14`：本地 trace 文件已存在，Agent 需要用最新 v4 skill 重新固化证据，再做上下文增强实验。

关键输入：

- workspace_id: `138`
- app_id: `1001883`
- log_id: `20260414111829010124134106210682`
- local trace: `/Users/bytedance/Documents/New project 2/trace_spans/spans/20260414111829010124134106210682.json`
- source row: Lark `Dms0cp` row 33
- workflow input: `巨量千川 素材追投 放量追投 控成本追投 占比 选择规则`
- judged user context: `素材追投 不是 只有 在控本计划里 才能看到么这个选项么` and `素材追投 本身 包括 放量追投 和 控成本追投 2种对吧，那占比怎么样的`

## 执行链路

1. 先确认 case 元数据，不要只用 log_id 猜问题。这个案例从 Lark sheet 读取 row 33，并额外保存 `case_row_33.json`，因为旧批量 run 的 `source_row` 是旧表 row 4，容易串案。

2. 用本地 trace 重建当前证据包。注意用 `python3`：

```bash
python3 -m findreason collect-evidence \
  --workspace-id 138 \
  --log-id 20260414111829010124134106210682 \
  --app-id 1001883 \
  --trace-file "/Users/bytedance/Documents/New project 2/trace_spans/spans/20260414111829010124134106210682.json" \
  --case-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260613_lark_OfEB_246/row_004_20260414111829010124134106210682/case.json \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest
```

3. 读取 `case_facts.json`，先看 workflow span，不要直接贴 JSON 给用户。这个案例有 3 段 workflow 调用；表格被评估的是第一段“占比/选择规则”，另外两段更贴近“是否只有控本计划可见”。

4. 做上下文增强 recall / replay 对照。只有根据验证点改写后的 query（验证 query）确实带来召回改善、排序改善，或 replay / 最终结果改善时，才把主因上调到 `输入侧问题`（旧 slug: `workflow_input_loss`）。

```bash
python3 -m findreason run-experiment \
  --type recall \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/case_facts.json \
  --query "巨量千川 控本计划 素材追投入口 放量投放 控成本投放 追投按钮 放量追投 控成本追投 区别" \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/experiments/context_recall \
  --timeout-seconds 30

python3 -m findreason run-experiment \
  --type replay \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/case_facts.json \
  --query "素材追投不是只有在控本计划里才能看到这个选项吗？素材追投本身包括放量追投和控成本追投两种吗，占比怎么选？" \
  --app-id 1001883 \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/experiments/context_replay \
  --timeout-seconds 60
```

5. 合成短版草稿，再改写同一个 `agent_judgement.md`。不要另建 `summary.md`。

```bash
python3 -m findreason synthesize-brief \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/case_facts.json \
  --experiment-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest/experiments/context_recall \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/codex_agent_20260622/row_033_latest
```

## 证据链

选中的 workflow 输入保留了 `素材追投 / 放量追投 / 控成本追投 / 占比 / 选择规则`，但丢了用户第一问里的“是否只有控本计划可见 / 入口 / 按钮”表达。workflow 输出只解释素材追投、放量追投、控成本追投、选择规则和任务上限，没有回答“素材追投入口是否只有控本计划能看到”。

原始 workflow 的 RAG 并不空：`recall=58`、`rerank_docs=18`、`prompt_docs=12`。`369449 千川全域商品投放一键起量&素材追投` 和 `2020384/2150662 巨量千川-直播「素材追投调控」产品手册` 进入 prompt，足以支撑追投方式和基本选择规则。

关键反差在增强 query：带上“控本计划 / 入口 / 按钮 / 放量投放 / 控成本投放”后，recall 命中 `1747061 商品投放调控任务说明（一键起量&素材追投）`，片段直接包含“功能入口：竞价投放-全域投放-推商品-（控成本投放）-点击单条计划-创意-视频，选择追投”。这个证据是第一问的 `direct_support`，而它没有进入原始选中 workflow 的 `origin_doc_list`、`rerank_docs` 或 `prompt_docs`。

replay 结果是 `skipped_authoritative_trace`：历史 trace 已有中间节点，工具按 v4 规则跳过真正 replay，所以报告必须写“replay 未返回新的 log_id”。

## 候选根因

`输入侧问题`（旧 slug: `workflow_input_loss`）：支持。用户真实上下文有两个子问题，第一问的“控本计划/入口/按钮”没有进入选中 workflow input；根据验证点改写后的 query 能补召回直接支撑证据，因此满足主因上调门槛。

`召回遗漏`（旧 slug: `retrieval_miss`）：只作为下游现象保留。原始链路没有 `1747061`，但不是因为知识不存在，而是 query 没保住入口子意图。

`重排丢失`（旧 slug: `rerank_drop`）：不支持为主因。关键入口证据没有进入原始 recall，谈不上被 rerank 丢掉；其他素材追投核心文档已进入 prompt。

`答案生成错误`（旧 slug: `answer_failure`）：可解释“占比”没有说清，但不能解释第一问为什么完全没进 workflow 输出。

`知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）：不支持。增强 query 能召回直接证据。

## 最终 judgement

最终 cause: `输入侧问题`（旧 slug: `workflow_input_loss`）

confidence: high

badcase_review_status: `valid_badcase`

短版结论：这个 case 的问题不是 RAG 全空或 rerank 误杀，而是 workflow 输入把用户两段追问压成了关键词袋。选中 workflow 只围绕“素材追投 / 两种追投 / 占比选择”回答，漏掉了“素材追投是否只有控本计划可见”的入口问题。根据验证点改写后的 query 能召回入口文档 `1747061`，所以主因应落在 `输入侧问题`。

## 反证与下一步

能推翻 `输入侧问题` 的证据：原始选中 workflow input、rewrite 或 prompt 中已经明确保留“是否只有控本计划可见/入口/按钮”，并且 `1747061` 或等价直接证据已经进入 prompt，但答案仍漏答。那时应转向 `答案生成错误`。

下一步建议：将 workflow 输入构造改成问题保真格式，至少保留两个子问题：`是否只有控本/控成本计划可见素材追投入口`、`放量追投和控成本追投如何选择/是否有固定占比`。
