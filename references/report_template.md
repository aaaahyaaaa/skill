# FindReason v3 单 case 摘要模板

在 `orchestrate` 完成后使用此模板。Codex 或其他宿主 Agent 应从 v3 JSON 输出渲染人类可读报告。CLI 在提供 `orchestrate --output-dir` 时也会写入 `case_report.md`，并在 `human_report_markdown` 中包含同一份文本。

## 1. 结论

- `primary_stage`
- `primary_cause`
- confidence
- owner
- `needs_human_review`
- 一句话根因摘要

## 2. case 信息

- `log_id`
- `workspace_id`
- `app_id`
- query
- 可用的 judgement / evaluation signal
- 使用的是 trace 证据还是 replay 证据

## 3. 原始 workflow 输入输出

- workflow span id / node id
- 原始 workflow input（JSON）
- 原始 workflow output（JSON）

## 4. 证据概览

- trace 状态，以及中间节点 spans 是否为权威证据
- `origin_doc_list` / `origin_faq_list` / `rerank_docs` / `prompt_docs` 数量
- expected doc IDs 以及它们出现的位置
- 仅针对线上阶段的断言覆盖矩阵：初召回、rerank、prompt；包含断言 role 和 source
- 理论召回上界与断言关系：对每个必要断言列出支撑的上界文档 ID / title，以及可用的 matched terms 和 scores
- 理论召回上界状态、topK 和 query variants
- 当主因是 `rerank_drop` 时，列出初召回已命中但被 rerank 丢弃的准确 doc IDs / titles
- 没有上界支撑或初召回支撑的必要断言；建议补充或改写对应 KB 内容
- unsupported claims 只能作为 answer 阶段观察项，不能写成 KB 补充项
- workflow span input/output 是否可用

## 5. 归因链路

| 阶段 | 结论 | 关键依据 |
|---|---|---|
| preprocess | pass/fail/indeterminate | rewrite、route、keyword evidence |
| knowledge | pass/fail/indeterminate | knowledge exists yes/no/unknown |
| retrieval | pass/fail/indeterminate | online recall、wide recall、permission evidence |
| rerank | pass/fail/indeterminate | recall -> rerank survival |
| context | pass/fail/indeterminate | rerank -> prompt survival、truncation/noise |
| answer | pass/fail/indeterminate | prompt support、unsupported claims、citations |
| evaluation | observation-only | judgement/rubric notes |

## 6. 主因解释

- 主因阶段为什么失败。
- 为什么更上游阶段不是主因。
- 哪些下游阶段只是被阻塞后的症状。
- Counterfactual：如果修复主因阶段，下游会发生什么变化。

## 7. 修改建议

- owner
- P0 action
- 可选 P1/P2 follow-up checks

## 8. 附件

链接本地 artifacts：

- `ingest.json`
- `probes/*.json`
- `final/attribution_record.json`
- `final/short_summary.json`
- `final/case_report.md`
