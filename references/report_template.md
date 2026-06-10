# FindReason v3 人读诊断报告契约

`orchestrate --output-dir` 默认只写一份人读 Markdown：`final/case_report.md`，内容与 `human_report_markdown` 一致。不要再默认生成 `agent_run_process.md` 或 `diagnostic_timeline.md`；JSON 继续承担审计和机器消费。

人读报告采用 adaptive narrative：根据 case 的真实断点、答案症状和可用证据组织说明，不再强制输出固定 7 段模板。固定的是证据和裁决协议，不是报告长相。

## 硬约束

报告必须满足以下约束：

- 结论先行：唯一 `primary_cause` 或 `null`、主因阶段、owner、是否需要人工复核。
- 解释最早断点：说明为什么主因停在该阶段，以及为什么不是更下游答案层。
- 解释答案症状：若存在 `secondary_findings.answer_issue_types`，必须说明它是主因还是下游表现。
- 引用决定性 evidence：至少展示阶段裁决、必要断言覆盖矩阵、理论召回上界关系或执行过的 `{stage}-exp` 验证卡片。
- 保留现场：用户问题、答案摘要、Workflow 原始输入/输出、线上 origin / rerank / prompt 数量。
- 给出 next action：owner + P0/P1 action；主因为 `null` 时说明缺哪类 evidence。
- 保留审计入口：`final/attribution_record.json`、`final/short_summary.json`、`final/case_report.md`。

## 推荐结构

当前 CLI 的默认叙事结构是：

1. `## 结论`
2. `## 关键解释`
3. `## 证据与验证`
4. `## 下一步`
5. `## 审计索引`

这只是默认实现，不是对宿主 Agent 的强制模板。宿主 Agent 可以根据 case 改写最终说明，但不得省略上面的硬约束。

## 关键解释

这一节负责把结构化裁决翻译成人能读懂的因果解释：

- 如果主因在 upstream：说明答案症状只是下游表现，不能覆盖更早断点。
- 如果主因是 `answer_failure`：说明上游 evidence chain 已通过或必要支撑已通过 rerank，但答案仍不满足期望。
- 如果 `primary_cause=null`：说明当前缺的是 assertion、trace、理论召回上界、probe 证据，还是知识三态仍为 unknown。

避免把 `exp_kind`、`target_artifact`、`hit`、`converged_direction`、probe id 等 runtime tag 直接塞进正文。

## 证据与验证

证据区可按实际 case 选择展示，但以下内容是默认优先级：

- 现场输入与答案：`log_id`、`workspace_id`、`app_id`、用户问题、答案摘要、Workflow 原始输入/输出。
- 输入边界：用户实际问题、Workflow 原始输入、预处理输出，以及关键约束是否丢失。
- 验证过程：执行后的 `run-probe-plan` / `{stage}-exp` 渲染为中文卡片。
- 阶段裁决：preprocess / knowledge / retrieval / rerank / answer 的状态和中文化 counterfactual。
- 答案症状：`unsupported_claim`、`wrong_citation`、`missing_aspect`、`scope_violation`。
- 必要断言：`expected_required` 与 answer/check 观察分开展示。
- 断言覆盖矩阵：聚焦线上 `origin -> rerank -> prompt`；prompt/context miss 只作为观察，不产生顶层主因。
- 理论召回上界与断言关系：doc ID、title、matched terms、support status、support spans。
- 召回 chunk 冲突风险：列出阶段、doc/chunk、冲突片段；只有影响必要断言且无清晰适用前提时才升级为知识问题。

每个执行后的验证项渲染为中文卡片：

- `我为什么查这个`：来自 `trigger_observation`，缺失时用 `hypothesis`。
- `我查了哪里`：把 artifact 显示为“理论召回上界 / 线上初召回 / 重排结果 / Prompt 上下文 / 最终答案”。
- `我怎么验证`：说明用哪个 query / pattern 在目标 artifact 中查找。
- `验证结果`：显示“命中 / 未命中 / 未执行 / 无法判断”。
- `支撑证据`：若有 `matched_docs`，每个文档单独展示标题、链接和命中片段。
- `这说明什么`：优先使用中文 `if_hit/if_miss`；英文结论必须转成中文解释。

`probe_id` 只留在 JSON。展示名优先用 `display_name`；没有时从 `probe_id` 派生短名，并清理 `P-`、`P_` 和时间戳式前缀。

## 文档证据格式

```markdown
- 2033060 巨量千川「破圈尖货」产品手册
  - 文档链接：[打开文档](https://ad-sirius.bytedance.net/api/sirius_knowledge/v1/data/doc/record_id?source=COGNITION&identifier=2033060)
  - 命中片段：...
```

优先使用 doc 自带 `url/link/doc_url/source_url`；没有链接但 doc id 是数字时，用知识详情接口 URL 作为可核对链接。

## Rerank 边界

`probe-rerank-bypass` 只比较关键 doc ID 是否从初召回进入重排/Prompt，是“重排生存观察”，不是 curl 重跑 rerank，也不是线上 rerank 参数实验。报告中写“重排观察：关键文档是否在重排后消失”，并明确这只是 doc ID 生存观察。不能单凭该观察选择 `rerank_drop`。
