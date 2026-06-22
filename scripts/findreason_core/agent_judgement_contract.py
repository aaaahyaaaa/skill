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
            "workflow input lost user-context constraints before retrieval",
            "rewrite query drift added or removed scene qualifiers",
            "keywords dropped key entities or the query was not decomposed into sub-intents",
        ],
        "evidence_to_check": [
            "compare original user question and evaluator context with workflow_span_ios[].input",
            "inspect rewrite_query and keywords",
            "run recall experiments with original query, rewrite query, and decomposed query variants",
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
            "KB lacks sufficient authoritative support",
            "KB topic is adjacent but not exact",
            "KB has internal inconsistency that cannot be disambiguated",
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
            "online recall missed existing knowledge",
            "a sub-topic query did not enter recall",
            "ACL, namespace, label, or workspace filtering hid the target document",
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
            "rerank pushed scenario-specific evidence out of the usable set",
            "topK, dedup, or diversity behavior dropped a secondary topic",
            "rerank scoring overweights surface title match and underweights semantic exactness",
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
            "model failed to cover prompt-supported aspects",
            "model violated scope constraints",
            "model made unsupported or over-generalized claims",
            "model selected the wrong citation or omitted an available official link",
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
        f"- chat_history: {'已提供，仅用于 workflow_input_loss 对照' if _has_text(chat_history) else '未提供'}",
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
        "- chat_history 只用于判断 `workflow_input_loss`：对比用户上下文是否进入 Workflow input/rewrite/keywords。",
        "- 如怀疑 `workflow_input_loss`，用上下文增强 query 做 recall/replay 对照，检查是否召回更多能明确回答问题的直接支撑文档。",
        "- 判断 `answer_failure` 只看被评估答案、Workflow input/rewrite、评估器信号和 prompt_docs；不得用 chat_history 支撑 `answer_failure`。",
        "- 如果核心断言在 prompt_docs 中有直接支撑，但答案仍漏答、错引、越界或编造，优先考虑 `answer_failure`。",
        "- 如果证据只是相邻主题或不够权威，保留 `suspected_knowledge_missing` 或低置信复核。",
        "- 证据链判断要按同一条 required assertion 串起来，不要只比较 doc id。",
        "- 如果评估器事实正确性结论与 prompt evidence 对不上，设置 `badcase_review_status` 为 `needs_human_review_evaluator_disputed`，并给出 query、judged answer、workflow input/output、evaluator claim、关键 prompt 证据和误判理由供人工复核。",
        "- `not_badcase_evaluator_error` 只在人工确认或明确人工标注后使用。",
    ]
    return "\n".join(lines).rstrip() + "\n"
