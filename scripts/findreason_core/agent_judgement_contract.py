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
    lines = [
        "# FindReason Agent Judgement Brief",
        "",
        "## Case",
        "",
        f"- log_id: `{case_facts.get('log_id', '')}`",
        f"- workspace_id: `{case_facts.get('workspace_id', '')}`",
        f"- app_id: `{case_facts.get('app_id', '')}`",
        f"- original_query: {case.get('query') or case.get('query_hint') or ''}",
        f"- workflow_rewrite: {_short(preprocess.get('rewrite_query'), 500)}",
        f"- workflow_keywords: {', '.join(map(str, preprocess.get('keywords') or [])) or '未采集到'}",
        f"- wrapped_output / answer_hint: {_short(case.get('answer_hint'), 500) or '未提供'}",
        f"- trace_answer_excerpt: {_short(case_facts.get('answer'), 500)}",
        "",
        "### Workflow IO",
        "",
        "workflow input:",
        "```json",
        _json_preview(workflow_input),
        "```",
        "workflow output:",
        "```json",
        _json_preview(workflow_output),
        "```",
        "",
        "### Evaluator Signals",
        "",
        *_evaluator_summary(str(case.get("judgement") or "")),
        "",
        "## Evidence Facts",
        "",
        f"- trace_source: {trace.get('source', 'openplat_trace_detail')}",
        f"- has_middle_node_trace: `{trace.get('has_middle_node_trace')}`",
        f"- recall: `{counts.get('recall', 0)}` (origin_doc_list=`{counts.get('origin_doc_list', 0)}`, origin_faq_list=`{counts.get('origin_faq_list', 0)}`)",
        f"- rerank_docs: `{counts.get('rerank_docs', 0)}`",
        f"- prompt_docs: `{counts.get('prompt_docs', 0)}`",
        "",
        "### Readable Evidence Samples",
        "",
        "Reports must not cite raw doc id arrays alone. Use title plus link or snippet.",
        "",
        "Prompt evidence samples:",
        *[line for doc in _artifact_docs(case_facts, "prompt_docs")[:5] for line in _doc_lines(doc)],
        "",
        "## Required Report Contract",
        "",
        "- Include original query, workflow input, workflow output, wrapped output, log_id, app_id, workspace_id, and evaluator signal summary.",
        "- Include a `hypothesis -> experiment -> falsification -> current judgement` section.",
        "- Include evidence sufficiency: required assertions, support level for key evidence, and missing authoritative evidence.",
        "- Include attribution organization across the 5 compressed causes: `workflow_input_loss`, `suspected_knowledge_missing`, `retrieval_miss`, `rerank_drop`, `answer_failure`.",
        "- Save local JSON artifacts for indexing; use Fornax by log_id for raw historical trace audit.",
        "- If replay returns a new log_id or trace_id, surface it in the report; otherwise explicitly say replay returned no new log_id.",
        "- Human evidence must show document title and link or an actual cited snippet. Doc ids may appear for audit, but never as the only evidence.",
        "- Distinguish evidence that explains replay improvement from evidence that is sufficient for a rigorous business answer.",
        "",
        "## Agent Reasoning Contract",
        "",
        "Do not jump to an earliest failing stage. Start from answer symptoms, propose multiple candidate explanations, then use experiments to support or falsify each explanation.",
        "",
        "Historical trace is the badcase scene. Replay is a current-version counterfactual experiment; use it to compare evidence availability, but do not overwrite the historical recall/rerank/prompt/answer facts.",
        "",
        "For each candidate explanation, write:",
        "",
        "1. Symptom observed in the answer or evaluator note.",
        "2. Candidate root cause and why it is plausible.",
        "3. Evidence already supporting it.",
        "4. Evidence that would falsify it.",
        "5. Next recall / rerank / replay experiment.",
        "",
        "## Symptom To Root Cause Seeds",
        "",
    ]
    for item in SYMPTOM_TO_ROOT_CAUSE_SEEDS:
        lines.extend(
            [
                f"### {item['stage_hint']}",
                "",
                "- Symptom patterns:",
                *[f"  - {text}" for text in item["symptom_patterns"]],
                "- Candidate explanations:",
                *[f"  - {text}" for text in item["candidate_explanations"]],
                "- Evidence to check:",
                *[f"  - {text}" for text in item["evidence_to_check"]],
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
