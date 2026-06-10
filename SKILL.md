---
name: findreason-rag-attribution
description: FindReason RAG 归因 skill。对 RAG 答错、答漏、答非所问的 badcase 做证据采集和规则归因，输出 primary_cause、evidence_chain、failure_patterns、next_actions。触发词包括 RAG 归因、findreason、fornax trace、为什么答错了、case 复盘、归因报告、知识缺失、召回缺失、rerank 误杀、unsupported claim、wrong citation、partial answer。
---

# FindReason RAG 归因 v3

## 概览

FindReason 是一个证据优先的 RAG badcase 归因 skill。宿主 Agent 负责理解 case、抽取断言、选择验证步骤和面向用户解释；CLI 负责采集 trace / probe 证据、归一化 stage signals，并在 `orchestrate` 中做可复核的反事实仲裁。

当前正式 CLI 入口是 `scripts/findreason.py`，正式归因实现集中在 `scripts/findreason_core/v3.py`。`references/` 是字段、原因码、验证、编排和报告口径的规则源；`SKILL.md` 只保留操作主线和跨阶段硬约束。


## 适用范围

适用于 RAG badcase 归因：答错、答漏、答非所问、知识缺失、召回缺失、重排误杀、prompt/context 丢证据、unsupported claim、wrong citation、partial answer 和召回 chunk 冲突风险。

不适用于非 RAG 路由、纯产品策略判断、无法获取 trace 且无法 replay 的 case、或没有任何可验证 artifact 的主观评价。评估器输入只是可选线索源，不是假定所有 case 都有，也不能直接决定主因。

## 执行流程

1. 将用户输入、表格行、curl/body 或评估器线索整理为当前字段契约允许的 case 输入；字段细节只看 `references/field_contract.md`。
2. 运行 `ingest-fornax-trace` 拉取并固化 Fornax trace 证据。
3. 读取 `raw_artifacts` 做现场侦查，形成动态诊断 backlog；现场观察只决定下一步验证，不直接决定主因。
4. 如果需要断言或静态 artifact 验证，由宿主 Agent 生成 assertion set 和必要的 `{stage}-exp` / `probe-v1` 计划；断言统一放入 `host_agent.answer_claim`，详细语义见 `references/agent_attribution_planning.md`。
5. 运行推荐或现场观察证明必要的验证命令；独立 probes 可以并行，`replay-workflow` 只能作为独占 fallback。`replay-workflow` 会清空旧 `judgement_evidence.signals` 后构造 replay-only request，旧 signals 不是 workflow 输入，也不是当前重跑评估结果。
6. 最后运行 `orchestrate` 合并 ingest / probe 证据，输出 `primary_cause`、`evidence_chain`、`failure_patterns`、`next_actions` 和审计 JSON。
7. 如果使用 `orchestrate --output-dir`，读取 `final/case_report.md` 作为唯一人读报告。报告采用 adaptive narrative：固定证据和裁决协议，不固定 7 段模板；硬约束见 `references/report_template.md`。

## 全局规则

1. 每个 case 必须先运行 `ingest-fornax-trace`，最后运行 `orchestrate`；`ingest-fornax-trace --raw` 只用于查看原始 trace，不属于归因流程。
2. 本 skill 每次只处理一个 RAG 答案 badcase；批量 fan-out、语言理解、断言抽取和面向用户解释由宿主 Agent 负责。
3. Fornax trace 中间节点是首选证据；只有 trace 查询失败或缺少关键中间节点时，才使用独占 fallback `replay-workflow`。如果 replay 缺少真实 `app_id` 或真实用户 `query`，必须显式补齐；CLI 会返回 `host_action_required`，不要用旧评估信号猜输入。
4. CLI 运行配置使用源码固定常量；不要设置 env 期望改变运行行为。鉴权 token、OpenPlat endpoint、workspace info endpoint、workflow endpoint/database 和 knowledge detail endpoint 都以源码常量为准。
5. 字段契约只接受 `references/field_contract.md` 中定义的当前内容；`SKILL.md` 不列字段清单，也不写兼容细则。
6. 候选原因只使用 `references/cause-codes.md` 的 v3 枚举；evidence 绑定、verdict、`counterfactual` 和主因选择以 `references/evidence-spec.md`、`references/orchestrator-rules.md` 为准。
7. 主因遍历和观察层边界以 `references/orchestrator-rules.md` 为准；如果上游 `counterfactual` 证据不足，不继续下沉猜答案问题，输出 `primary_cause=null` 并进入 `needs_human_review`。
8. 断言输入按 `references/field_contract.md`：宿主 Agent 把需要验证的断言放入 `host_agent.answer_claim`；各 role 的含义、如何生成断言、哪些断言能驱动阶段归因，统一参考 `references/agent_attribution_planning.md`。

## 阶段验证路由

宿主 Agent 维护动态诊断 backlog。每看到一个线索，就记录 `trigger_source`、`trigger_observation`、`hypothesis`、`exp_kind`、`target_stage` 和 `expected_evidence`，再选择对应验证环节。

| 验证环节 | 使用场景 | 典型动作 |
|-|-|-|
| `retrieval-exp` | 验证召回链路是否能拿到更好证据 | query / rewrite / keyword / query variant / topK / open-label / permission |
| `rerank-exp` | 验证 rerank 是否丢掉 origin 中已有证据 | 重排生存观察、阈值、参数或排序恢复信号 |
| `answer-exp` | 验证 prompt 已有约束是否被 answer 覆盖 | answer span 覆盖、漏答、越界、弱化表达 |
| `citation-exp` | 验证引用是否存在、可用、支撑 claim | stale / 停用来源、引用文档不支撑 claim |
| `chunk-conflict-exp` | 验证召回/prompt chunk 是否内部矛盾 | 冲突断言、doc/chunk、支撑片段、适用前提 |

`run-probe-plan` 是执行器，用于把宿主 Agent 已选择的静态 artifact 验证落成 JSON 证据；它不负责语言理解，也不直接决定主因。验证细节见 `references/probe-spec.md`。

## 现场侦查

在生成 assertion set 或运行 probes 前，宿主 Agent 应先做一次现场侦查。现场侦查是为了释放 Agent 的临场判断，不绕过 evidence binding。

- 确认 trace 中 Workflow 原始输入/输出、rewrite query、keywords、origin docs / FAQ、rerank docs、prompt docs、final answer、citation mapping 是否完整。
- 摊平 `origin -> rerank -> prompt` 文档生存路径，记录关键候选在哪个阶段出现或丢失，以及是否只是 doc ID 命中但正文不支撑断言。
- 标记高风险来源：停用、过期、重复 chunk、非权威来源、引用链接不可用或引用文档不支撑 answer claim。
- 做 prompt-vs-answer alignment：已有证据是否覆盖关键限制、反例、预算/ROI、适用范围，最终答案是否遗漏、弱化或反向表达。
- 根据现场观察选择下一步验证；未执行的 hypothesis / backlog item 不是证据，只有执行后的结果才能进入 `orchestrate`。

## 命令速查

基础流程：

```bash
python -m findreason ingest-fornax-trace \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file /path/to/case.json \
  --output-dir /tmp/findreason-case

python -m findreason orchestrate \
  --ingest-file /tmp/findreason-case/ingest.json \
  --probe-dir /tmp/findreason-case/probes \
  --mode final \
  --schema-version v3 \
  --output-dir /tmp/findreason-case/final
```

常用验证命令：

```bash
python -m findreason probe-knowledge-detail --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-permission-check --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-wide-recall --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-rerank-bypass --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason probe-context-assembly --ingest-file /tmp/findreason-case/ingest.json --output-dir /tmp/findreason-case/probes
python -m findreason run-probe-plan --ingest-file /tmp/findreason-case/ingest.json --plan @plan.json --output-dir /tmp/findreason-case/probes
```

Workflow fallback：

```bash
python -m findreason fetch-workflow-nodes --workspace-id <workspace_id> --app-id <app_id>
python -m findreason replay-workflow \
  --ingest-file /tmp/findreason-case/ingest.json \
  --app-id <app_id> \
  --query "<真实用户问题>" \
  --override @override.json \
  --output-dir /tmp/findreason-case/probes
```

如果 `case_input.query` 是 `unknown query` 且有 `query_hint`，replay 默认使用 `query_hint`；如果 `app_id` 不是数字或仍然缺真实 query，命令只返回 `blocked` 和 `host_action_required`，不会发起线上 workflow 调用。replay 输出里的 `node_traces`、`retrieval/rerank/answer` stage signals 才代表当前重跑结果；`judgement_evidence.signals` 只是历史评估线索。

只查看原始 trace：

```bash
python -m findreason ingest-fornax-trace --workspace-id <workspace_id> --log-id <log_id> --raw
```

查看 schema：

```bash
python -m findreason schema
```

## Reference 索引

- `references/README.md`：v3 当前主线、核心口径和已移除旧能力。
- `references/field_contract.md`：case 输入、ingest 输出和 orchestrate 输出字段契约。
- `references/cause-codes.md`：v3 cause 枚举、owner、触发条件和边界。
- `references/orchestrator-rules.md`：阶段顺序、`counterfactual` 和主因选择规则。
- `references/evidence-spec.md`：evidence bundle schema 和校验规则。
- `references/host_agent_playbook.md`：宿主 Agent 职责、现场观察和端到端操作流程。
- `references/agent_attribution_planning.md`：如何把 trace artifacts 与评估器信号转成 assertion set 和验证计划。
- `references/probe-spec.md`：probe 输入、输出、缓存、失败语义和 `run-probe-plan` 兼容契约。
- `references/report_template.md`：人读报告契约；固定硬约束，不固定章节范式。
- `references/workflow-ops.md`：workflow 节点获取和 replay 行为。
- `references/span-extraction.md`：Fornax span 抽取映射。
- `references/output-schema.json`：供宿主侧校验使用的 v3 输出 schema。
- `references/capabilities.json`：v3 capability manifest。
