# Case 019eef8d: rerun from original input -> 知识缺失或证据不足 (suspected_knowledge_missing)

## 适用场景

mode: `rerun_from_original_input`

当原始 Fornax trace 拉不到，用户只给 workspace、app、原始问题和评估器线索时，不要反复说“请提供 trace”。先用精确 query / app / workspace 搜本机再生产物；如果确实没有 `case_facts.json`，再用当前 app 对原始输入做 workflow replay。这个案例来自 thread `019eef8d-bf8e-7e90-a605-98a95d636ed9`。

关键输入：

- workspace_id: `138`
- app_id: `1001883`
- 原始问题: `暂停计划、预算撞线、密集上调ROI这三种情况哪种对新计划影响最大？`
- 评估器输出: `暂无` / 未覆盖 / 无法判断方向
- log_id: `02177604934454026050340cd514000f344a61a5df83331afe790`
- regenerated local trace: `/Users/bytedance/Documents/New project 2/trace_spans/spans/02177604934454026050340cd514000f344a61a5df83331afe790.json`
- evidence dir: `/Users/bytedance/Documents/findreason-rag-attribution-runs/20260622_lark_row36_latest`

## 执行链路

1. 先把“没有原始 trace”拆成两种情况：没有 Fornax 原始现场，不等于本机没有再生 trace / run 目录。先精确搜索原始 query、log_id、workspace/app，不要只看当前仓库根目录。

```bash
rg -l --hidden "暂停计划、预算撞线、密集上调ROI|密集上调ROI|预算撞线" \
  /Users/bytedance/Documents/findreason-rag-attribution-runs \
  /tmp/workflow_outputs_D2_D52.csv \
  /tmp/findreason_evalset_50_candidate.csv
```

2. 如果命中已有再生 run，先读现成证据包，而不是重跑一遍相同链路。本案例命中 `/Users/bytedance/Documents/findreason-rag-attribution-runs/20260622_lark_row36_latest`，其中已有 `case_facts.json`、`agent_brief.md`、`agent_judgement.md`、`recall_experiment_original_query.json`、`recall_experiment_context_enhanced.json`、`rerank_experiment.json`、`replay_experiment.json`。

3. 如果没有任何 run，但有 app_id + workspace_id + query，可以创建一个最小 replay facts 文件后执行 replay；如果 replay 返回 `blocked` 或鉴权错误，就把缺失输入/权限写进报告，不能编造 trace。

```json
{
  "schema_version": "agent-judgement-v4",
  "workspace_id": "138",
  "app_id": "1001883",
  "case": {
    "query": "暂停计划、预算撞线、密集上调ROI这三种情况哪种对新计划影响最大？"
  },
  "trace": {
    "has_middle_node_trace": false
  }
}
```

```bash
python3 -m findreason run-experiment \
  --type replay \
  --facts-file /tmp/minimal_replay_facts.json \
  --query "暂停计划、预算撞线、密集上调ROI这三种情况哪种对新计划影响最大？" \
  --app-id 1001883 \
  --output-dir /tmp/findreason-rerun-from-input \
  --timeout-seconds 60
```

4. 对已有事实包，按实际结构抽取，不要假设字段名。这个 case 的 recall 实验结果在 `artifacts.recall_docs` / `artifacts.origin_doc_list` 下，不能只查 `documents`。

5. 核查 `replay_experiment.json`。本案例是 `skipped_authoritative_trace`：再生 trace 已有中间节点证据，所以未执行新的 workflow replay，未返回新的 replay log_id。

## 证据链

Workflow 输入完整保留了问题：`暂停计划、预算撞线、密集上调ROI这三种情况哪种对新计划影响最大？`。rewrite / keywords 也保留了“暂停计划、预算撞线、密集上调ROI、新计划、影响最大”。因此不能把主因先贴成 `输入侧问题`（旧 slug: `workflow_input_loss`）。

Workflow 输出和包装层 `answer_hint` 都给出确定排序：`密集上调 ROI 影响最大`、`预算撞线补预算即可恢复`、`暂停计划可逆`。评估器“暂无/未覆盖/无法判断”的信号是有用线索：资料没有直接支持这个排序。

trace 里的证据链不空：`recall=107`、`rerank_docs=18`、`prompt_docs=12`。prompt 中有局部证据：

- `2979770 千川乘方-基础投放-FAQ`：支持“调高 ROI 会让跑量变慢”。
- `368757`：支持“暂停计划会影响跑量但可重启”。
- `1747058` / `1579025` / `2947992`：支持“预算撞线会导致无法继续投放或跑量受限”。

这些证据只能说明三种操作各有风险，不能直接推出“密集上调 ROI 一定影响最大”。上下文增强 recall 能补充“频繁暂停重启会影响跑量”一类证据，但仍没有找到“三者相对影响排序”的权威比较规则。

## 候选根因

`输入侧问题`（旧 slug: `workflow_input_loss`）：不支持。query、rewrite、keywords 都保留了关键问题；上下文增强后也没有改善到能直接回答排序规则，所以不能上调为主因。

`重排丢失`（旧 slug: `rerank_drop`）：不支持为主因。ROI、预算、暂停的局部证据已经进了 prompt；被丢掉的相邻文档没有提供“最大排序”规则。

`答案生成错误`（旧 slug: `answer_failure`）：是次级表现。模型把局部证据强行归纳成“密集上调 ROI 最大”，但 prompt 本身缺少可引用的相对排序规则。

`知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）：最支持。知识库/证据包缺少“暂停计划、预算撞线、密集上调 ROI 对新计划影响大小排序”的直接权威比较。

`召回遗漏`（旧 slug: `retrieval_miss`）：低支持。上下文增强后仍没有找到排序规则，更像知识侧缺口或答案边界问题，而不是简单漏召。

## 最终 judgement

最终 cause: `知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）

confidence: medium

badcase_review_status: `valid_badcase`

短版结论：这个 case 的核心不是输入丢失，也不是 rerank 丢证据，而是证据不足以支撑确定排序。prompt 里有 ROI、预算、暂停的局部材料，但没有“三者谁对新计划影响最大”的权威比较。答案把局部证据写成了确定结论，所以表象上有 `答案生成错误`，更靠前的根因是 `知识缺失或证据不足`。

## 反证与下一步

能推翻 `知识缺失或证据不足` 的证据：找到进入 prompt 的文档明确写出“新计划阶段三类操作的影响排序”或等价规则，并且它能直接支撑“密集上调 ROI 最大”。如果这种证据已经进 prompt，则主因应改判 `答案生成错误`。

下一步建议：补一条业务规则，说明在新计划 / 冷启动 / 全域投放场景下，暂停、预算撞线、频繁调 ROI 的影响边界和优先级。答法上，在没有直接比较证据时应说“资料未直接比较最大项，三者风险分别是...”，不要输出单一最大结论。
