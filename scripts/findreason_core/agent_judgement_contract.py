from __future__ import annotations

import json
import re
from typing import Any


SYMPTOM_TO_ROOT_CAUSE_SEEDS: list[dict[str, Any]] = [
    {
        "stage_hint": "preprocess",
        "symptom_patterns": [
            "user context contains a scene constraint but the answer uses a generic or different-scene path",
            "the answer adds or removes qualifiers and changes scope",
            "multi-device, account-type, or multi-intent questions are answered as one narrow intent",
        ],
        "candidate_explanations": [
            "输入侧问题: workflow input lost user-context constraints before retrieval",
            "输入侧问题: rewrite query drift added or removed scene qualifiers",
            "输入侧问题: keywords dropped key entities or the query was not decomposed into sub-intents",
        ],
        "evidence_to_check": [
            "compare original user question and evaluator context with workflow_span_ios[].input",
            "inspect rewrite_query and keywords",
            "run recall/rerank/replay experiments with original query, rewrite query, and decomposed query variants; promote only if the verified query improves recall, ranking, or final output",
        ],
    },
    {
        "stage_hint": "knowledge",
        "symptom_patterns": [
            "the answer says it cannot find a deeper or official explanation",
            "grader says materials do not mention the required fact",
            "nearby-topic documents are found but no exact-topic authority exists",
            "knowledge sources contain conflicting statements without applicable premises",
        ],
        "candidate_explanations": [
            "知识缺失或证据不足: KB lacks sufficient authoritative support",
            "知识缺失或证据不足: KB topic is adjacent but not exact",
            "知识缺失或证据不足: KB has internal inconsistency that cannot be disambiguated",
        ],
        "evidence_to_check": [
            "run open-label wide recall with topK >= 50",
            "inspect whether matched documents actually support the required assertion, not just the title",
            "check official/citable source availability and contradiction premises",
        ],
    },
    {
        "stage_hint": "retrieval",
        "symptom_patterns": [
            "the answer says not found but the expected document exists",
            "a missing sub-question has support in KB",
            "validator uses a KB document to refute the answer but that document was not in online recall",
            "industry/private-domain content is filtered out for the current path",
        ],
        "candidate_explanations": [
            "召回遗漏: online recall missed existing knowledge",
            "召回遗漏: a sub-topic query did not enter recall",
            "召回遗漏: ACL, namespace, label, or workspace filtering hid the target document",
        ],
        "evidence_to_check": [
            "compare open-label wide recall with recall artifacts",
            "separate origin_doc_list and origin_faq_list but report them together as recall",
            "check visibility differences across workspace/app/user path",
        ],
    },
    {
        "stage_hint": "rerank",
        "symptom_patterns": [
            "scene or sub-topic document is recalled but not used",
            "multi-subquestion answer covers only one part although another support document was recalled",
            "a precise low-frequency document is pushed below generic high-frequency documents",
        ],
        "candidate_explanations": [
            "重排丢失: rerank pushed scenario-specific evidence out of the usable set",
            "重排丢失: topK, dedup, or diversity behavior dropped a secondary topic",
            "重排丢失: rerank scoring overweights surface title match and underweights semantic exactness",
        ],
        "evidence_to_check": [
            "compare same-assertion support in recall versus rerank_docs",
            "inspect rank and score movement for target documents",
            "run rerank variants or bypass observation as experiments, not as final judgment",
        ],
    },
    {
        "stage_hint": "answer",
        "symptom_patterns": [
            "prompt has all required points but answer omits some",
            "answer expands a narrow question into a platform-wide conclusion",
            "answer mixes branches or contradicts itself despite clear premises",
            "answer cites a document that does not support the claim",
            "answer makes an authoritative claim that conflicts with prompt evidence",
        ],
        "candidate_explanations": [
            "答案生成错误: model failed to cover prompt-supported aspects",
            "答案生成错误: model violated scope constraints",
            "答案生成错误: model made unsupported or over-generalized claims",
            "答案生成错误: model selected the wrong citation or omitted an available official link",
        ],
        "evidence_to_check": [
            "extract answer symptoms before upstream experiments",
            "compare answer spans with prompt_docs and citation mapping",
            "treat answer symptoms as observations until recall/rerank/replay experiments explain or fail to explain them",
        ],
    },
]


def _short(value: Any, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _json_preview(value: Any, limit: int = 900) -> str:
    if value in ("", None, [], {}):
        return "未采集到"
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        text = text[: limit - 20].rstrip() + "\n... <truncated>"
    return text


def _selected_workflow_io(case_facts: dict[str, Any]) -> tuple[Any, Any]:
    trace = case_facts.get("trace") if isinstance(case_facts.get("trace"), dict) else {}
    ios = trace.get("workflow_span_ios")
    if not isinstance(ios, list):
        return "", ""
    selected = next((item for item in ios if isinstance(item, dict) and item.get("selected")), None)
    item = selected or next((item for item in ios if isinstance(item, dict)), {})
    if not isinstance(item, dict):
        return "", ""
    return item.get("input", ""), item.get("output", "")


def _workflow_query(workflow_input: Any) -> str:
    if not isinstance(workflow_input, dict):
        return ""
    sys_input = workflow_input.get("sys") if isinstance(workflow_input.get("sys"), dict) else {}
    query = sys_input.get("query") or workflow_input.get("query")
    return str(query or "").strip()


def _evaluator_summary(text: str) -> list[str]:
    rows: list[str] = []
    for match in re.finditer(r"([a-zA-Z_]+)=score:(\d+); reason:([^\n]+)", text or ""):
        status = "pass" if match.group(2) == "1" else "fail"
        rows.append(f"- `{match.group(1)}`: {status}; {_short(match.group(3), 220)}")
    if rows:
        return rows
    if text:
        return [f"- raw_evaluator_note: {_short(text, 500)}"]
    return ["- 未提供评估器信号。"]


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _artifact_docs(case_facts: dict[str, Any], key: str) -> list[dict[str, Any]]:
    artifacts = case_facts.get("artifacts") if isinstance(case_facts.get("artifacts"), dict) else {}
    docs = artifacts.get(key)
    return [item for item in docs if isinstance(item, dict)] if isinstance(docs, list) else []


def _doc_lines(doc: dict[str, Any]) -> list[str]:
    doc_id = str(doc.get("id") or doc.get("doc_id") or "").strip()
    title = str(doc.get("title") or doc.get("doc_title") or doc.get("name") or "(untitled)")
    url = str(doc.get("url") or doc.get("link") or "").strip()
    content = _short(doc.get("content") or doc.get("content_preview") or "", 360)
    lines = [f"- {doc_id} {title}" if doc_id else f"- {title}"]
    lines.append(f"  - 文档链接: [打开文档]({url})" if url else "  - 文档链接: 未提供")
    lines.append(f"  - 援引片段: {content or '未提供'}")
    return lines


def _workflow_diagnostic_lines(trace: dict[str, Any]) -> list[str]:
    topology = trace.get("workflow_topology") if isinstance(trace.get("workflow_topology"), dict) else {}
    node_map = trace.get("node_evidence_map") if isinstance(trace.get("node_evidence_map"), list) else []
    prompt_observation = trace.get("prompt_observation") if isinstance(trace.get("prompt_observation"), dict) else {}
    if not topology and not node_map:
        return ["- 未采集到 app-detail 节点拓扑；如需深挖，先回查 raw trace 和 workflow span。"]

    lines = [
        f"- mapping_status: `{topology.get('mapping_status') or 'unknown'}`",
        f"- app/version: {topology.get('app_name') or '未提供'} / {topology.get('version_id') or '未提供'}",
        f"- 节点/边: `{topology.get('node_count', 0)}` / `{topology.get('edge_count', 0)}`",
        f"- prompt_observation: `{prompt_observation.get('status') or 'not_observed'}`；{_short(prompt_observation.get('note'), 260)}",
    ]
    for item in node_map[:10]:
        if not isinstance(item, dict):
            continue
        node = item.get("node") if isinstance(item.get("node"), dict) else {}
        counts = item.get("evidence_counts") if isinstance(item.get("evidence_counts"), dict) else {}
        spans = item.get("trace_spans") if isinstance(item.get("trace_spans"), list) else []
        span_ids = [str(span.get("span_id")) for span in spans if isinstance(span, dict) and span.get("span_id")]
        evidence_bits = []
        if counts.get("origin_doc_list") or counts.get("origin_faq_list"):
            evidence_bits.append(f"recall {int(counts.get('origin_doc_list') or 0) + int(counts.get('origin_faq_list') or 0)}")
        if counts.get("rerank_docs"):
            evidence_bits.append(f"rerank {counts.get('rerank_docs')}")
        if counts.get("prompt_docs"):
            evidence_bits.append(f"prompt {counts.get('prompt_docs')}")
        if counts.get("answer"):
            evidence_bits.append(f"answer {counts.get('answer')}")
        lines.append(
            "- "
            f"{node.get('name') or '(unnamed)'}"
            f" / type={node.get('type') or 'unknown'}"
            f" / node_id={node.get('id') or ''}"
            f" / inferred_role={item.get('inferred_role') or 'unknown'}"
            f" / spans={', '.join(span_ids[:4]) or '未映射'}"
            f" / evidence={', '.join(evidence_bits) or '未观测到关键证据字段'}"
        )
    if len(node_map) > 10:
        lines.append(f"- 其余 `{len(node_map) - 10}` 个节点已省略；完整节点证据见 `case_facts.json.trace.node_evidence_map`。")
    return lines


def _agent_read_plan_lines(trace: dict[str, Any]) -> list[str]:
    read_plan = trace.get("agent_span_read_plan") if isinstance(trace.get("agent_span_read_plan"), list) else []
    if not read_plan:
        return ["- 未生成按 cause 的 span 读取建议；请直接回查 workflow_topology 和 raw trace。"]
    lines: list[str] = []
    for item in read_plan:
        if not isinstance(item, dict):
            continue
        candidates = item.get("candidate_nodes") if isinstance(item.get("candidate_nodes"), list) else []
        node_names = []
        for candidate in candidates[:4]:
            if not isinstance(candidate, dict):
                continue
            label = str(candidate.get("node_name") or candidate.get("node_type") or candidate.get("node_id") or "").strip()
            spans = ", ".join(str(span_id) for span_id in (candidate.get("span_ids") or [])[:3])
            node_names.append(f"{label}{f'({spans})' if spans else ''}")
        lines.append(f"- {item.get('cause')}: {', '.join(node_names) or '暂无候选节点'}")
    return lines


def judgement_brief_markdown(case_facts: dict[str, Any]) -> str:
    case = case_facts.get("case") if isinstance(case_facts.get("case"), dict) else {}
    counts = case_facts.get("counts") if isinstance(case_facts.get("counts"), dict) else {}
    trace = case_facts.get("trace") if isinstance(case_facts.get("trace"), dict) else {}
    preprocess = case_facts.get("preprocess") if isinstance(case_facts.get("preprocess"), dict) else {}
    workflow_input, workflow_output = _selected_workflow_io(case_facts)
    prompt_docs = _artifact_docs(case_facts, "prompt_docs")
    recall_docs = _artifact_docs(case_facts, "origin_doc_list") + _artifact_docs(case_facts, "origin_faq_list")
    evidence_samples = prompt_docs[:4] if prompt_docs else recall_docs[:4]
    evaluator_lines = _evaluator_summary(str(case.get("judgement") or ""))
    answer_hint = case.get("answer_hint") or ""
    chat_history = case.get("chat_history") or ""
    query = (
        str(case.get("query") or case.get("query_hint") or "").strip()
        or _workflow_query(workflow_input)
        or _short(preprocess.get("rewrite_query"), 300)
        or "未采集到"
    )
    lines = [
        "# Agent Brief",
        "",
        "这是一份给 Agent 快速进入现场的工作单，不是最终报告。最终给人读的结论写在 `agent_judgement.md`。",
        "",
        "## 现场一句话",
        "",
        f"用户问：{query}",
        "",
        f"历史答案：{_short(case_facts.get('answer'), 600) or '未采集到'}",
        "",
        "## 被评估目标",
        "",
        f"- 被评估答案 / answer_hint: {_short(answer_hint, 700) or '未提供'}",
        f"- chat_history: {'已提供，仅用于 输入侧问题（旧 slug: workflow_input_loss）对照' if _has_text(chat_history) else '未提供'}",
        "",
        "## 审计锚点",
        "",
        f"- log_id: `{case_facts.get('log_id', '')}`",
        f"- workspace_id: `{case_facts.get('workspace_id', '')}`",
        f"- app_id: `{case_facts.get('app_id', '')}`",
        f"- trace_source: {trace.get('source', 'openplat_trace_detail')}",
        f"- has_middle_node_trace: `{trace.get('has_middle_node_trace')}`",
        "",
        "## Workflow 摘要",
        "",
        f"- 输入摘要：{_json_preview(workflow_input)}",
        f"- 输出摘要：{_json_preview(workflow_output)}",
        "",
        "## Workflow 节点诊断",
        "",
        "- 这里优先使用 app-detail 的真实节点信息；`inferred_role` 只是辅助说明，不是最终归因。",
        *_workflow_diagnostic_lines(trace),
        "",
        "## 按 cause 的 span 读取入口",
        "",
        "- 这些是给 Agent 的候选读取入口，不是 CLI 的硬裁决。",
        *_agent_read_plan_lines(trace),
        "",
        "## 评估器线索",
        "",
        "- 评估器线索是低置信诊断线索，不是事实正确性的最终裁决。",
        *evaluator_lines,
        "",
        "## 上游摘要",
        "",
        f"- rewrite: {_short(preprocess.get('rewrite_query'), 500) or '未采集到'}",
        f"- keywords: {', '.join(map(str, preprocess.get('keywords') or [])) or '未采集到'}",
        f"- recall 总数: `{counts.get('recall', 0)}`",
        f"- 重排文档数: `{counts.get('rerank_docs', 0)}`",
        f"- prompt 证据数: `{counts.get('prompt_docs', 0)}`",
        "",
        "## 可读证据样例",
        "",
        *[line for doc in evidence_samples for line in _doc_lines(doc)],
        "",
        "## 归因时先想这几件事",
        "",
        "- 先写清楚答案具体错在哪里，再判断是不是上游导致。",
        "- 最终 cause 以中文 cause 为主，旧 slug 只作为兼容别名：`workflow_input_loss` -> `输入侧问题`，`suspected_knowledge_missing` -> `知识缺失或证据不足`，`retrieval_miss` -> `召回遗漏`，`rerank_drop` -> `重排丢失`，`answer_failure` -> `答案生成错误`，`evaluator_disputed_no_obvious_error` -> `无明显错误/评估器不准，需人工进一步核实`。",
        "- chat_history 只用于判断 `输入侧问题`（旧 slug: `workflow_input_loss`）：对比用户上下文是否进入 Workflow input/rewrite/keywords。",
        "- 如怀疑 `输入侧问题`，必须根据验证点改写后的 query 做 recall/rerank/replay 对照；只有看到召回改善、排序改善，或 replay / 最终结果改善，才能把它上调为主因。",
        "- 如果只是发现 Workflow input、rewrite、keywords 可能少了信息，但改写 query 没改善实验结果，只能写成低置信候选或待验证点。",
        "- 判断 `答案生成错误`（旧 slug: `answer_failure`）只看被评估答案、Workflow input/rewrite、评估器信号和 prompt_docs；不得用 chat_history 支撑 `答案生成错误`。",
        "- 如果核心断言在 prompt_docs 中有直接支撑，但答案仍漏答、错引、越界或编造，优先考虑 `答案生成错误`。",
        "- Prompt sufficiency 至少分 `相关词命中`、`部分支撑`、`直接核心证据`、`冲突证据` 四档；prompt 有相关词或泛化文档，不等于可以判 `答案生成错误`。",
        "- 判断 `答案生成错误` 前，必须确认关键 required assertions 已有 `direct_support` 且 Workflow 输出仍错。",
        "- 对核心 doc 看 `rank_shift_observations`：支撑 assertion、recall/rerank/prompt rank 和 score、是否进入 prompt、缺失原因和 context_boundary。",
        "- 对关键 doc 看 knowledge-detail 状态：`status_signals`、`status_confirmed`、`last_modified`、`status_reason`；状态未确认时写 `status_unconfirmed`。",
        "- 如果证据只是相邻主题或不够权威，保留 `知识缺失或证据不足`（旧 slug: `suspected_knowledge_missing`）或低置信复核。",
        "- 证据链判断要按同一条 required assertion 串起来，不要只比较 doc id。",
        "- `无明显错误/评估器不准，需人工进一步核实`（slug: `evaluator_disputed_no_obvious_error`）不能作为“看不出来”的兜底；只有前 5 类都没有明显证据，且评估器结论与 prompt evidence、Workflow 输出、被评估答案或人工标注存在可说明的冲突时，才能作为 cause。",
        "- 归到第 6 类时，报告必须显式写出人工复核点；不要求固定范式，但要讲清楚为什么怀疑评估器不准、人工需要复核哪里、复核后可能如何改变结论。",
        "- `badcase_review_status` 独立于 cause；若需要人工确认评估器是否误判，设置为 `needs_human_review_evaluator_disputed`，并在报告里写清人工复核点。",
        "- `评估器输出暂无` 本身不是第 6 类证据；这种情况应继续看链路证据，或写低置信待补证。",
        "- `not_badcase_evaluator_error` 只在人工确认或明确人工标注后使用。",
    ]
    return "\n".join(lines).rstrip() + "\n"
