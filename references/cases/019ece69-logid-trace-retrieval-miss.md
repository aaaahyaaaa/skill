# Case 019ece69: logid trace fetch -> 召回遗漏 (retrieval_miss)

## 适用场景

mode: `logid_trace_fetch`

当用户给出 workspace + log_id，且线上 trace 可以获取时，直接用 `collect-evidence` 固化历史现场。后续如果用户补充 expected doc / FAQ id，要把 target doc 当作一等证据，从 final output 一层层查到 raw recall 和 query counterfactual。这个案例来自 thread `019ece69-e987-7212-b341-222bcd4ff6ec`。

关键输入：

- workspace_id: `55`
- log_id: `021780901906425fdbddc03001b0c040000000000000034bbaafd`
- parsed app_id: `1002496`
- workflow query: `智擎版项目唯一性规则介绍`
- expected content id: `208332`
- internal row id: `3514618`
- expected title: `短剧行业-全域投放产品说明｜巨量营销智擎版`
- support URL: `https://support.oceanengine.com/support/content/208332`

## 执行链路

1. 先确认 CLI 和环境。当前机器要用 `python3 -m findreason`，不要用 `python -m findreason`。

```bash
python3 -m findreason collect-evidence --help
python3 -m findreason synthesize-brief --help
```

2. 用 workspace + log_id 拉取 trace。这里不需要本地 `--trace-file`。

```bash
python3 -m findreason collect-evidence \
  --workspace-id 55 \
  --log-id 021780901906425fdbddc03001b0c040000000000000034bbaafd \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd \
  --trace-timeout-seconds 120
```

3. 先跑基础实验，建立证据包：

```bash
python3 -m findreason run-experiment \
  --type recall \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --query "智擎版项目唯一性规则介绍" \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd \
  --timeout-seconds 120

python3 -m findreason run-experiment \
  --type rerank \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --target-doc-id 208311 \
  --target-doc-id 2186504 \
  --target-doc-id 568730 \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd

python3 -m findreason run-experiment \
  --type replay \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --query "智擎版项目唯一性规则介绍" \
  --app-id 1002496 \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd
```

4. 用户补充 `208332` 是答案之一后，不要只查 normalized top docs。按顺序查：

- `agent_judgement.md` / final output 是否出现 `208332`
- `case_facts.json`、`recall_experiment.json`、`evidence_index.json` 是否包含 `208332`
- `rerank_experiment.json` 中 target survival 是否 `in_recall / in_rerank / in_prompt`
- `208332` 是不是 public identifier，而不是内部 id
- 用 query counterfactual 验证知识是否存在、是否可召回

5. 对 expected doc 单独跑 survival 和 recall counterfactual：

```bash
python3 -m findreason run-experiment \
  --type rerank \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --target-doc-id 208332 \
  --target-doc-id 208311 \
  --target-doc-id 2186504 \
  --target-doc-id 568730 \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd

python3 -m findreason run-experiment \
  --type recall \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --query "短剧行业 全域投放 产品说明 巨量营销智擎版 项目唯一性" \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/probes/recall_expected_208332_shortdrama \
  --timeout-seconds 120

python3 -m findreason run-experiment \
  --type recall \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --query "智擎版 项目唯一性 全域投放 短剧 同一抖音号 同一剧目 只能创建一个项目" \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/probes/recall_expected_208332_global \
  --timeout-seconds 120

python3 -m findreason run-experiment \
  --type recall \
  --facts-file /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/case_facts.json \
  --query "短剧行业-全域投放产品说明｜巨量营销智擎版" \
  --output-dir /Users/bytedance/Documents/findreason-rag-attribution-runs/20260616_ws55_021780901906425fdbddc03001b0c040000000000000034bbaafd/probes/recall_expected_208332_title \
  --timeout-seconds 120
```

## 证据链

基础 trace 固化成功，解析到实际 `app_id=1002496`。workflow 输入是 `智擎版项目唯一性规则介绍`，RAG 有 26 条 doc + 5 条 FAQ 召回，15 条进入 rerank/prompt。`208311 项目唯一性规则｜巨量营销智擎版` 进入 rerank/prompt，说明通用项目唯一性证据没有缺失。

用户补充 expected doc 后，直接搜索原始证据包确认：`208332` 没有出现在 `case_facts.json`、原始 query 的 `recall_experiment.json`、`rerank_docs`、`prompt_docs` 或 `evidence_index.json`。target survival 也显示 `208332` 的 `in_recall=false`、`in_rerank=false`、`in_prompt=false`。

id 语义不能猜。`208332` 是 support content id / public identifier，对应内部 row id `3514618`，标题是 `短剧行业-全域投放产品说明｜巨量营销智擎版`，URL 是 `https://support.oceanengine.com/support/content/208332`。

counterfactual recall 证明知识存在且可召回：

- 原始 query `智擎版项目唯一性规则介绍`: `208332` 命中 0。
- `短剧行业 全域投放 产品说明 巨量营销智擎版 项目唯一性`: `208332` 命中 14 条，最高 rank=1。
- `智擎版 项目唯一性 全域投放 短剧 同一抖音号 同一剧目 只能创建一个项目`: `208332` 命中 8 条，最高 rank=5。
- `短剧行业-全域投放产品说明｜巨量营销智擎版`: `208332` 命中 14 条，最高 rank=1。

## 候选根因

`知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）：不支持。`208332` 在知识库中存在，且增强 query / 标题 query 都能召回。

`重排丢失`（旧 slug: `rerank_drop`）：不支持。`208332` 没有进入当前 recall，所以不是从 rerank 阶段被丢掉。

`答案生成错误`（旧 slug: `answer_failure`）：不是主因。生成层没法使用没进入 prompt 的 `208332`。

`输入侧问题`（旧 slug: `workflow_input_loss`）：有条件成立。如果原始用户上下文包含“短剧 / 全域投放 / 同一抖音号同一剧目”等信息，但 workflow input 只剩 `智擎版项目唯一性规则介绍`，并且根据验证点改写后的 query 能改善召回、排序或最终结果，才可上移到 `输入侧问题`。本 case 当前缺少完整 case-file / chat_history，不能直接坐实。

`召回遗漏`（旧 slug: `retrieval_miss`）：当前最可信。原始 query 未召回 expected doc；补全短剧全域投放语义后稳定命中。

## 最终 judgement

最终 cause: `召回遗漏`（旧 slug: `retrieval_miss`）

confidence: medium to high

badcase_review_status: `valid_badcase`

短版结论：`208332` 没有出现在最终结果，也没有进入原始 recall/rerank/prompt。它对应的知识并不存在缺失或权限不可用，因为增强 query 能稳定召回。直接断点是当前 query/rewrite 没覆盖“短剧 / 全域投放 / 同一抖音号同一剧目”这个子意图，导致 expected doc 没被召回；如果后续证明这些约束原本在用户上下文里，且验证 query 能改善召回、排序或最终结果，才可以把上游根因升级为 `输入侧问题`。

## 反证与下一步

能推翻 `召回遗漏` 的证据：在 raw recall 或 rerank HTTP 请求输入里找到 `208332` / `3514618`，但它没有进 rerank 输出或 prompt。那时应改判 `重排丢失` 或 prompt 截断问题。

下一步建议：在 query rewrite / RetrieveQueryList 阶段增加子意图扩展。遇到“智擎版项目唯一性规则介绍”时，至少拆出“标准投放唯一性规则”“全域投放唯一性规则”“短剧 ROI3 / 同抖音号同剧目唯一性”等 query variant，并用 `208332` 做回归样例。
