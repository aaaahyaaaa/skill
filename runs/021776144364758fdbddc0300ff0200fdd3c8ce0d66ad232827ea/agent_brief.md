# FindReason Agent Judgement Brief

## Case

- log_id: `021776144364758fdbddc0300ff0200fdd3c8ce0d66ad232827ea`
- workspace_id: `138`
- app_id: `1001883`
- original_query: unknown query
- workflow_rewrite: 巨量千川全域投放成本保障中，当商品有多个佣金率时，商品佣金率如何计算
- workflow_keywords: 巨量千川, 全域投放成本保障, 商品, 多个佣金率, 商品佣金率, 计算
- wrapped_output / answer_hint: 未提供
- trace_answer_excerpt: 当商品有多个佣金率时，用于判断成本保障条件的商品佣金率按以下方式确定： 若商品设置了双佣金模式（例如投广佣金率和日常佣金率），则以其中的“投广佣金率”进行计算。 若商品仅为单佣金模式，则使用其“固定佣金率”。 **参考来源** [1][巨量千川「全域投放」成本保障规则](https://support.oceanengine.com/support/content/139336) [2][巨量千川「商品乘方-达人带货佣金优化」产品手册](https://support.oceanengine.com/support/content/204726)

### Workflow IO

workflow input:
```json
{
  "sys": {
    "query": "巨量千川全域投放成本保障中，当商品有多个佣金率时，商品佣金率如何计算"
  },
  "user": {
    "task_id": "8642531e-d3ba-4a58-849d-239b85fbd3a6"
  }
}
```
workflow output:
```json
{
  "end": "当商品有多个佣金率时，用于判断成本保障条件的商品佣金率按以下方式确定：\n\n若商品设置了双佣金模式（例如投广佣金率和日常佣金率），则以其中的“投广佣金率”进行计算。  \n若商品仅为单佣金模式，则使用其“固定佣金率”。\n\n**参考来源**\n\n[1][巨量千川「全域投放」成本保障规则](https://support.oceanengine.com/support/content/139336)\n[2][巨量千川「商品乘方-达人带货佣金优化」产品手册](https://support.oceanengine.com/support/content/204726)"
}
```

### Evaluator Signals

- 未提供评估器信号。

## Evidence Facts

- trace_source: openplat_trace_detail
- has_middle_node_trace: `True`
- recall: `73` (origin_doc_list=`70`, origin_faq_list=`3`)
- rerank_docs: `17`
- prompt_docs: `12`

### Readable Evidence Samples

Reports must not cite raw doc id arrays alone. Use title plus link or snippet.

Prompt evidence samples:
- 354146 巨量千川『全域投放』成本保障规则
  - 文档链接: 未提供
  - 援引片段: 巨量千川『全域投放』成本保障规则 尊敬的用户，你好： 以下是『全域投放』成本保障规则，你可以点开链接详细了解。[巨量千川「全域投放」成本保障规则](https://qianchuan.jinritemai.com/support/content/139336?graphId=395&pageId=229&spaceId=122&timestamp=1703074969492)
- 366583 巨量千川账户如何投放全域投放商品
  - 文档链接: 未提供
  - 援引片段: 巨量千川账户如何投放全域投放商品 如你想实现单个计划既能投放到内容场、又能投放到商城，优先选择店铺首个巨量千川账户（「人店川一体」账户），并在创编时选择「人店一体」抖音号（创建计划时会带有可投商城提示）
- 366570 全域商品投放成本保障金额怎么计算？
  - 文档链接: 未提供
  - 援引片段: 全域商品投放成本保障金额怎么计算？ 成本保障周期：客户投放全域投放的单个自然日当日。 成本保障条件： （1）转化订单数：≥10，整体支付ROI出价期间的转化订单数以平台展示的整体成交订单数为准，净成交出价投放期间的转化订单数以平台展示的净成交订单数为准，存在两种出价的将统计对应期间的转化订单数之和。 （2）ROI差值：实际ROI＜全天目标ROI*80%，全天目标ROI=GMV/总出价金额 （3）ROI修改：不论计划启停状态 商品投放：相邻两次ROI修改的操作时间间隔≥1小时。（修改操作包括修改ROI目标数值，或将整体支付ROI目标切换为净成交ROI目标，将净成交ROI目标切换为整体支付ROI目标） 直播投放：相邻两次修改目标ROI数值的操作时间间隔≥15分钟，且全天目标ROl数值修改总次数≤24次；且“整...
- 1412463 巨量千川「全域投放」成本保障规则
  - 文档链接: 未提供
  - 援引片段: Title：巨量千川「全域投放」成本保障规则 Headings：第二章 实施细则 ||推直播|推商品| | ---- | ---- | ---- | |成本保障金额计算逻辑|成本保障金额=总实际消耗金额-总出价金额>总出价金额 = (A时间段GMV/A时间段目标ROI)+(B时间段GMV/B时间段目标ROI)+ ...以此类推。|成本保障金额=总实际消耗金额-总出价金额>总出价金额 = (A时间段GMV/A时间段目标ROI)+(B时间段GMV/B时间段目标ROI)+ ...以此类推。| 表二：控成本投放-综合成本优化（产品内测中，仅限受邀客户可使用） ||推直播|推商品| | ---- | ---- | ---- | |成本保障周期| 客户投放全域投放的单个自然日当日。| 客户投放全域投放的单个自然日当日...
- 1412463 巨量千川「全域投放」成本保障规则
  - 文档链接: 未提供
  - 援引片段: Title：巨量千川「全域投放」成本保障规则 Headings：附：常见问题 Q5：投放巨量千川“竞价投放-全域投放-推直播间”期间，1小时内调整多少次目标会影响成本保障? A：在直播全域投放投放期间，使用控成本投放策略时，需同时满足以下要求： * 不论计划启停状态,相邻两次修改目标ROI数值的操作时间间隔≥15分钟，且全天目标ROl数值修改总次数≤24次； * 不论计划启停状态，不同ROI优化目标之间相互切换时，相邻两次切换的间隔时间≥1 小时 举例：3.16日某美妆客户自早晨7点到下午15点持续开播，其中8点投放全域投放“控成本投放-投放成本优化-净成交ROI”优化目标并设置目标值为3，9:30切换为“控成本投放-投放成本优化-支付ROI”且目标数值仍为3，9:40调整目标数值为5，因为9:40修改目...

## Required Report Contract

- Include original query, workflow input, workflow output, wrapped output, log_id, app_id, workspace_id, and evaluator signal summary.
- Include a `hypothesis -> experiment -> falsification -> current judgement` section.
- Include evidence sufficiency: required assertions, support level for key evidence, and missing authoritative evidence.
- Include attribution organization across the 5 compressed causes: `workflow_input_loss`, `suspected_knowledge_missing`, `retrieval_miss`, `rerank_drop`, `answer_failure`.
- Save local JSON artifacts for indexing; use Fornax by log_id for raw historical trace audit.
- If replay returns a new log_id or trace_id, surface it in the report; otherwise explicitly say replay returned no new log_id.
- Human evidence must show document title and link or an actual cited snippet. Doc ids may appear for audit, but never as the only evidence.
- Distinguish evidence that explains replay improvement from evidence that is sufficient for a rigorous business answer.

## Agent Reasoning Contract

Do not jump to an earliest failing stage. Start from answer symptoms, propose multiple candidate explanations, then use experiments to support or falsify each explanation.

Historical trace is the badcase scene. Replay is a current-version counterfactual experiment; use it to compare evidence availability, but do not overwrite the historical recall/rerank/prompt/answer facts.

For each candidate explanation, write:

1. Symptom observed in the answer or evaluator note.
2. Candidate root cause and why it is plausible.
3. Evidence already supporting it.
4. Evidence that would falsify it.
5. Next recall / rerank / replay experiment.

## Symptom To Root Cause Seeds

### preprocess

- Symptom patterns:
  - user context contains a scene constraint but the answer uses a generic or different-scene path
  - the answer adds or removes qualifiers and changes scope
  - multi-device, account-type, or multi-intent questions are answered as one narrow intent
- Candidate explanations:
  - workflow input lost user-context constraints before retrieval
  - rewrite query drift added or removed scene qualifiers
  - keywords dropped key entities or the query was not decomposed into sub-intents
- Evidence to check:
  - compare original user question and evaluator context with workflow_span_ios[].input
  - inspect rewrite_query and keywords
  - run recall experiments with original query, rewrite query, and decomposed query variants

### knowledge

- Symptom patterns:
  - the answer says it cannot find a deeper or official explanation
  - grader says materials do not mention the required fact
  - nearby-topic documents are found but no exact-topic authority exists
  - knowledge sources contain conflicting statements without applicable premises
- Candidate explanations:
  - KB lacks sufficient authoritative support
  - KB topic is adjacent but not exact
  - KB has internal inconsistency that cannot be disambiguated
- Evidence to check:
  - run open-label wide recall with topK >= 50
  - inspect whether matched documents actually support the required assertion, not just the title
  - check official/citable source availability and contradiction premises

### retrieval

- Symptom patterns:
  - the answer says not found but the expected document exists
  - a missing sub-question has support in KB
  - validator uses a KB document to refute the answer but that document was not in online recall
  - industry/private-domain content is filtered out for the current path
- Candidate explanations:
  - online recall missed existing knowledge
  - a sub-topic query did not enter recall
  - ACL, namespace, label, or workspace filtering hid the target document
- Evidence to check:
  - compare open-label wide recall with recall artifacts
  - separate origin_doc_list and origin_faq_list but report them together as recall
  - check visibility differences across workspace/app/user path

### rerank

- Symptom patterns:
  - scene or sub-topic document is recalled but not used
  - multi-subquestion answer covers only one part although another support document was recalled
  - a precise low-frequency document is pushed below generic high-frequency documents
- Candidate explanations:
  - rerank pushed scenario-specific evidence out of the usable set
  - topK, dedup, or diversity behavior dropped a secondary topic
  - rerank scoring overweights surface title match and underweights semantic exactness
- Evidence to check:
  - compare same-assertion support in recall versus rerank_docs
  - inspect rank and score movement for target documents
  - run rerank variants or bypass observation as experiments, not as final judgment

### answer

- Symptom patterns:
  - prompt has all required points but answer omits some
  - answer expands a narrow question into a platform-wide conclusion
  - answer mixes branches or contradicts itself despite clear premises
  - answer cites a document that does not support the claim
  - answer makes an authoritative claim that conflicts with prompt evidence
- Candidate explanations:
  - model failed to cover prompt-supported aspects
  - model violated scope constraints
  - model made unsupported or over-generalized claims
  - model selected the wrong citation or omitted an available official link
- Evidence to check:
  - extract answer symptoms before upstream experiments
  - compare answer spans with prompt_docs and citation mapping
  - treat answer symptoms as observations until recall/rerank/replay experiments explain or fail to explain them
