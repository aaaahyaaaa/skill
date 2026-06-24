from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from .models import (
    AttributionRequest,
    CaseInput,
    ClaimAlignment,
    EvidenceDoc,
    FieldMapEntry,
    JudgementEvidence,
    JudgementSignal,
    PreprocessEvidence,
    QaEvidence,
    ReferenceEvidence,
    RerankEvidence,
    RetrievalEvidence,
    WorkflowReplayEvidence,
)

MIDDLE_NODE_TYPES = {
    "Start",
    "End",
    "ZhiShangRAGPreprocess",
    "ZhiShangRAGRecall",
    "ZhiShangRAGRerank",
    "ZhiShangRAGQA",
}

RAG_NODE_TYPES = {
    "ZhiShangRAGPreprocess",
    "ZhiShangRAGRecall",
    "ZhiShangRAGRerank",
    "ZhiShangRAGQA",
}

PROMPT_DOC_KEYS = {"prompt_docs", "promptDocs", "qaPromptDocs"}


class FornaxTraceIngestRequest(BaseModel):
    trace_file: str
    workspace_id: str = ""
    app_id: str = ""
    query: str = ""
    judgement: str = ""
    log_id: str = ""
    case_id: Optional[str] = None
    source_row: Optional[str] = None
    fornax_space_id: str = ""
    fornax_space_name: str = ""
    expected_knowledge_points: list[str] = Field(default_factory=list)
    error_points: list[str] = Field(default_factory=list)
    detect_citation_mismatches: bool = True


class FornaxTraceIngestResponse(BaseModel):
    attribution_request: AttributionRequest
    trace_summary: dict[str, Any] = Field(default_factory=dict)
    trace_evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_report_markdown: str = ""


def load_trace_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _decode_jsonish(value: Any) -> Any:
    current = value
    for _ in range(5):
        if not isinstance(current, str):
            return current
        stripped = current.strip()
        if not stripped:
            return current
        if not ((stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]"))):
            return current
        try:
            current = json.loads(stripped)
        except json.JSONDecodeError:
            return current
    return current


def _trace_spans(payload: Any) -> list[dict[str, Any]]:
    decoded = _decode_jsonish(payload)
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    if isinstance(decoded, dict):
        for key in ("spans", "data", "items", "result"):
            value = decoded.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _trace_spans(value)
                if nested:
                    return nested
    return []


def _span_output(span: dict[str, Any] | None) -> Any:
    return _decode_jsonish((span or {}).get("output"))


def _span_input(span: dict[str, Any] | None) -> Any:
    return _decode_jsonish((span or {}).get("input"))


def _find_span(spans: list[dict[str, Any]], *span_types: str, names: tuple[str, ...] = ()) -> dict[str, Any] | None:
    wanted_types = {item for item in span_types if item}
    for span in spans:
        span_type = str(span.get("span_type") or span.get("type") or "")
        span_name = str(span.get("span_name") or span.get("name") or "")
        if span_type in wanted_types:
            return span
        if names and any(name in span_name for name in names):
            return span
    return None


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
        else:
            text = str(value).strip()
        if text:
            return text
    return ""


def _custom_tags(span: dict[str, Any]) -> dict[str, Any]:
    tags = span.get("custom_tags") or {}
    return tags if isinstance(tags, dict) else {}


def _span_type(span: dict[str, Any]) -> str:
    return str(span.get("span_type") or span.get("type") or "")


def _span_name(span: dict[str, Any]) -> str:
    return str(span.get("span_name") or span.get("name") or "")


def _middle_node_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [span for span in spans if _span_type(span) in MIDDLE_NODE_TYPES]


def _first_tag(spans: list[dict[str, Any]], *keys: str) -> str:
    for span in spans:
        tags = _custom_tags(span)
        for key in keys:
            value = tags.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _root_tag(spans: list[dict[str, Any]], *keys: str) -> str:
    for span in spans:
        if str(span.get("parent_id") or "") != "":
            continue
        tags = _custom_tags(span)
        for key in keys:
            value = tags.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _span_node_id(span: dict[str, Any] | None) -> str:
    if not span:
        return ""
    tags = _custom_tags(span)
    value = tags.get("zhishang.node_id") or tags.get("node_id") or tags.get("nodeId")
    return str(value or "").strip()


def _resolve_workflow_mapping(workspace_id: str, app_id: str) -> dict[str, Any]:
    if os.getenv("FINDREASON_TRACE_WORKFLOW_MAPPING", "true").lower() in {"0", "false", "no"}:
        return {
            "mapping_status": "workflow_config_disabled",
        }
    if not workspace_id or not app_id or not workspace_id.isdigit() or not app_id.isdigit():
        return {
            "mapping_status": "workflow_config_skipped",
            "mapping_error": "workspace_id/app_id missing or non-numeric",
        }
    try:
        from .workflow_replay import resolve_workflow

        request = AttributionRequest(
            case_input=CaseInput(
                query="trace mapping",
                workspace_id=workspace_id,
                app_id=app_id,
            )
        )
        resolved = resolve_workflow(request)
        resolved["mapping_status"] = "workflow_config_loaded"
        return resolved
    except Exception as exc:
        return {
            "mapping_status": "workflow_config_error",
            "mapping_error": str(exc)[:500],
        }


def _workflow_nodes_by_id(resolved_app: dict[str, Any]) -> dict[str, dict[str, Any]]:
    config = resolved_app.get("workflow_config") if isinstance(resolved_app.get("workflow_config"), dict) else {}
    nodes = config.get("nodes") if isinstance(config.get("nodes"), list) else []
    return {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict) and node.get("id")}


def _trace_node_mapping(spans: list[dict[str, Any]], resolved_app: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    nodes_by_id = _workflow_nodes_by_id(resolved_app)
    entries: list[dict[str, Any]] = []
    matched = 0
    node_id_count = 0
    for index, span in enumerate(spans):
        node_id = _span_node_id(span)
        if node_id:
            node_id_count += 1
        mapped_node = nodes_by_id.get(node_id) if node_id else None
        if mapped_node:
            matched += 1
        entries.append(
            {
                "span_id": span.get("span_id"),
                "parent_id": span.get("parent_id"),
                "span_type": span.get("span_type") or span.get("type"),
                "span_name": span.get("span_name") or span.get("name"),
                "node_id": node_id,
                "mapped": bool(mapped_node),
                "mapped_node": mapped_node or {},
                "trace_order": index,
                "status": span.get("status"),
                "status_code": span.get("status_code"),
                "duration": span.get("duration"),
                "service_name": span.get("service_name"),
            }
        )
    if matched:
        return entries, "mapped_by_zhishang_node_id"
    if node_id_count and nodes_by_id:
        return entries, "node_id_unmatched_fallback_span_type"
    if resolved_app.get("mapping_status") == "workflow_config_error":
        return entries, "workflow_config_error_fallback_span_type"
    return entries, "fallback_span_type"


def _role_identity(entry: dict[str, Any]) -> str:
    node = entry.get("mapped_node") if isinstance(entry.get("mapped_node"), dict) else {}
    parts = [
        entry.get("span_type"),
        entry.get("span_name"),
        entry.get("node_id"),
        node.get("type"),
        node.get("name"),
        node.get("id"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _find_mapped_span(
    spans: list[dict[str, Any]],
    node_mapping: list[dict[str, Any]],
    role_terms: tuple[str, ...],
    fallback_types: tuple[str, ...],
    names: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    spans_by_id = {str(span.get("span_id") or ""): span for span in spans}
    for entry in node_mapping:
        identity = _role_identity(entry)
        if any(term.lower() in identity for term in role_terms):
            span = spans_by_id.get(str(entry.get("span_id") or ""))
            if span:
                return span
    return _find_span(spans, *fallback_types, names=names)


def _workflow_span_io_entry(span: dict[str, Any], selected: bool = False) -> dict[str, Any]:
    return {
        "span_id": span.get("span_id"),
        "parent_id": span.get("parent_id"),
        "span_name": span.get("span_name") or span.get("name"),
        "span_type": span.get("span_type") or span.get("type"),
        "node_id": _span_node_id(span),
        "selected": selected,
        "input": _span_input(span),
        "output": _span_output(span),
    }


def _workflow_span_ios(spans: list[dict[str, Any]], selected_span: dict[str, Any] | None) -> list[dict[str, Any]]:
    workflow_spans = [span for span in spans if _span_type(span) == "workflow"]
    selected_id = str((selected_span or {}).get("span_id") or (workflow_spans[0].get("span_id") if workflow_spans else ""))
    return [_workflow_span_io_entry(span, str(span.get("span_id") or "") == selected_id) for span in workflow_spans]


def _doc_id(doc: dict[str, Any]) -> str | None:
    value = (
        doc.get("identifier")
        or doc.get("knowledge_id")
        or doc.get("knowledgeId")
        or doc.get("id")
        or doc.get("doc_id")
        or doc.get("docId")
    )
    return str(value) if value not in (None, "") else None


def _doc_keys(doc: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("identifier", "id", "doc_id", "docId", "knowledge_id", "knowledgeId"):
        value = doc.get(key)
        if value not in (None, ""):
            keys.add(str(value))
    return keys


def _doc_score(doc: dict[str, Any]) -> float | None:
    for key in ("score", "recallScore", "fineScore", "rankScore"):
        value = doc.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _doc_url(doc: dict[str, Any]) -> str:
    return str(doc.get("url") or doc.get("docUrl") or doc.get("source_url") or "")


def _evidence_doc(doc: dict[str, Any], rank: int, source_prefix: str) -> EvidenceDoc:
    recall_source = str(doc.get("recallSource") or doc.get("source") or "")
    url = _doc_url(doc)
    source_parts = [source_prefix]
    if recall_source:
        source_parts.append(recall_source)
    identifier = str(doc.get("identifier") or "")
    if identifier and identifier != (_doc_id(doc) or ""):
        source_parts.append(f"identifier={identifier}")
    if url:
        source_parts.append(f"url={url}")
    return EvidenceDoc(
        id=_doc_id(doc),
        title=str(doc.get("title") or doc.get("doc_title") or ""),
        content=str(doc.get("content") or doc.get("text") or ""),
        rank=rank,
        score=_doc_score(doc),
        source="|".join(source_parts),
    )


def _evidence_docs(raw_docs: Any, source_prefix: str) -> list[EvidenceDoc]:
    if not isinstance(raw_docs, list):
        return []
    return [_evidence_doc(doc, index + 1, source_prefix) for index, doc in enumerate(raw_docs) if isinstance(doc, dict)]


def _raw_doc_dedupe_key(doc: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(doc.get("id") or ""),
        str(doc.get("identifier") or ""),
        str(doc.get("chunkId") or doc.get("chunk_id") or ""),
        str(doc.get("title") or doc.get("doc_title") or ""),
        str(doc.get("content") or doc.get("text") or "")[:200],
    )


def _dedupe_raw_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for doc in docs:
        key = _raw_doc_dedupe_key(doc)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(doc)
    return deduped


def _collect_prompt_docs_from_value(value: Any) -> list[dict[str, Any]]:
    decoded = _decode_jsonish(value)
    docs: list[dict[str, Any]] = []
    if isinstance(decoded, dict):
        for key, child in decoded.items():
            if key in PROMPT_DOC_KEYS:
                prompt_docs = _decode_jsonish(child)
                if isinstance(prompt_docs, list):
                    docs.extend(item for item in prompt_docs if isinstance(item, dict))
                elif isinstance(prompt_docs, dict):
                    docs.append(prompt_docs)
            else:
                docs.extend(_collect_prompt_docs_from_value(child))
    elif isinstance(decoded, list):
        for item in decoded:
            docs.extend(_collect_prompt_docs_from_value(item))
    return _dedupe_raw_docs(docs)


def _collect_prompt_docs_from_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for span in spans:
        docs.extend(_collect_prompt_docs_from_value(_span_output(span)))
    return _dedupe_raw_docs(docs)


def _raw_doc_lists(recall_output: Any, rerank_output: Any, qa_output: Any, end_output: Any = None) -> dict[str, list[dict[str, Any]]]:
    recall = recall_output if isinstance(recall_output, dict) else {}
    rerank = rerank_output if isinstance(rerank_output, dict) else {}
    qa = qa_output if isinstance(qa_output, dict) else {}
    end = end_output if isinstance(end_output, dict) else {}
    prompt_docs = qa.get("prompt_docs") or qa.get("promptDocs") or end.get("prompt_docs") or end.get("promptDocs") or end.get("output") or []
    return {
        "origin_doc_list": [item for item in recall.get("origin_doc_list") or [] if isinstance(item, dict)],
        "origin_faq_list": [item for item in recall.get("origin_faq_list") or [] if isinstance(item, dict)],
        "rerank_docs": [item for item in rerank.get("rerank_docs") or [] if isinstance(item, dict)],
        "prompt_docs": [item for item in prompt_docs if isinstance(item, dict)],
    }


def _children_by_parent(spans: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    children: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        parent_id = str(span.get("parent_id") or "")
        if parent_id:
            children.setdefault(parent_id, []).append(span)
    return children


def _descendant_spans(children_map: dict[str, list[dict[str, Any]]], parent_id: str) -> list[dict[str, Any]]:
    descendants: list[dict[str, Any]] = []
    queue = list(children_map.get(parent_id, []))
    while queue:
        child = queue.pop(0)
        descendants.append(child)
        queue.extend(children_map.get(str(child.get("span_id") or ""), []))
    return descendants


def _first_child(children: list[dict[str, Any]], *span_types: str) -> dict[str, Any] | None:
    wanted = {span_type for span_type in span_types if span_type}
    for child in children:
        if _span_type(child) in wanted:
            return child
    return None


def _rank_query_from_payload(value: Any) -> str:
    payload = value if isinstance(value, dict) else {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    sys_payload = payload.get("sys") if isinstance(payload.get("sys"), dict) else {}
    return _first_nonempty(
        payload.get("RankQuery"),
        payload.get("rankQuery"),
        payload.get("query"),
        user.get("RankQuery"),
        user.get("rankQuery"),
        user.get("query"),
        sys_payload.get("query"),
    )


def _workflow_segments(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    children_map = _children_by_parent(spans)
    segments: list[dict[str, Any]] = []
    for workflow_span in [span for span in spans if _span_type(span) == "workflow"]:
        children = children_map.get(str(workflow_span.get("span_id") or ""), [])
        all_children = _descendant_spans(children_map, str(workflow_span.get("span_id") or ""))
        start_span = _first_child(children, "Start")
        recall_span = _first_child(children, "ZhiShangRAGRecall")
        rerank_span = _first_child(children, "ZhiShangRAGRerank")
        qa_span = _first_child(children, "ZhiShangRAGQA")
        end_span = _first_child(children, "End")
        if not any((start_span, recall_span, rerank_span, qa_span, end_span)):
            continue
        segments.append(
            {
                "workflow_span": workflow_span,
                "start_span": start_span,
                "recall_span": recall_span,
                "rerank_span": rerank_span,
                "qa_span": qa_span,
                "end_span": end_span,
                "all_child_spans": all_children,
            }
        )
    return segments


def _is_faq_doc(doc: dict[str, Any]) -> bool:
    recall_source = str(doc.get("recallSource") or doc.get("source") or "")
    return doc.get("type") == 4 or recall_source == "featured_search"


def _doc_text(doc: dict[str, Any]) -> str:
    return " ".join(str(doc.get(key) or "") for key in ("title", "doc_title", "content", "text"))


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z][A-Za-z0-9]+|\d+|[\u4e00-\u9fff]{2,}", query)
    generic = {
        "什么",
        "哪些",
        "如何",
        "需要",
        "推荐",
        "使用",
        "开启",
        "满足",
        "条件",
        "预算",
        "出价",
        "策略",
        "素材",
        "创意",
        "要求",
        "不同",
        "额外",
        "配置",
        "关键项",
        "普通",
        "项目",
        "相比",
        "人群",
        "定向",
        "自动",
        "优化",
        "手动",
        "设置",
    }
    cleaned: list[str] = []
    for term in terms:
        lowered = term.lower()
        if lowered in generic or len(lowered) < 2:
            continue
        if lowered not in cleaned:
            cleaned.append(lowered)
    return cleaned


def _doc_matches_query(doc: dict[str, Any], query: str) -> bool:
    terms = _query_terms(query)
    if not terms:
        return False
    haystack = _doc_text(doc).lower()
    ascii_terms = [term for term in terms if re.search(r"[a-z0-9]", term)]
    if ascii_terms:
        return all(term in haystack for term in ascii_terms[:3])
    return any(term in haystack for term in terms[:5])


def _segment_query(segment: dict[str, Any]) -> str:
    start_input = _span_input(segment.get("start_span"))
    start_output = _span_output(segment.get("start_span"))
    rerank_input = _span_input(segment.get("rerank_span"))
    workflow_input = _span_input(segment.get("workflow_span"))
    return _first_nonempty(
        _rank_query_from_payload(start_input),
        _rank_query_from_payload(start_output),
        rerank_input.get("query") if isinstance(rerank_input, dict) else "",
        _rank_query_from_payload(workflow_input),
    )


def _segment_raw_docs(segment: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw_docs = _raw_doc_lists(
        _span_output(segment.get("recall_span")),
        _span_output(segment.get("rerank_span")),
        _span_output(segment.get("qa_span")),
        _span_output(segment.get("end_span")),
    )
    if not raw_docs["prompt_docs"]:
        raw_docs["prompt_docs"] = _collect_prompt_docs_from_spans(segment.get("all_child_spans") or [])
    return raw_docs


def _doc_key_overlaps(doc: dict[str, Any], docs: list[dict[str, Any]]) -> bool:
    doc_keys = _doc_keys(doc)
    return bool(doc_keys and any(doc_keys & _doc_keys(candidate) for candidate in docs))


def _segment_analysis(segment: dict[str, Any]) -> dict[str, Any]:
    query = _segment_query(segment)
    raw_docs = _segment_raw_docs(segment)
    origin_candidates = [
        doc
        for doc in raw_docs["origin_doc_list"][:10]
        if not _is_faq_doc(doc) and _doc_matches_query(doc, query)
    ]
    final_docs = raw_docs["rerank_docs"] or raw_docs["prompt_docs"]
    dropped = [doc for doc in origin_candidates if not _doc_key_overlaps(doc, final_docs)]
    final_faq_count = sum(1 for doc in final_docs[:5] if _is_faq_doc(doc))
    final_doc_count = sum(1 for doc in final_docs[:5] if not _is_faq_doc(doc))
    score = len(dropped) * 20
    if dropped and final_faq_count:
        score += min(final_faq_count, 5) * 3
    if dropped and final_doc_count == 0:
        score += 10
    return {
        "workflow_span_id": (segment.get("workflow_span") or {}).get("span_id"),
        "query": query,
        "score": score,
        "origin_doc_count": len(raw_docs["origin_doc_list"]),
        "origin_faq_count": len(raw_docs["origin_faq_list"]),
        "rerank_doc_count": len(raw_docs["rerank_docs"]),
        "prompt_doc_count": len(raw_docs["prompt_docs"]),
        "dropped_relevant_docs": [
            {
                "id": _doc_id(doc),
                "keys": sorted(_doc_keys(doc)),
                "title": str(doc.get("title") or doc.get("doc_title") or ""),
                "rank": index + 1,
            }
            for index, doc in enumerate(dropped)
        ],
        "final_top_docs": [
            {
                "id": _doc_id(doc),
                "keys": sorted(_doc_keys(doc)),
                "title": str(doc.get("title") or doc.get("doc_title") or ""),
                "rank": index + 1,
                "is_faq": _is_faq_doc(doc),
            }
            for index, doc in enumerate(final_docs[:8])
        ],
    }


def _select_trace_segment(spans: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    segments = _workflow_segments(spans)
    analyses = [_segment_analysis(segment) for segment in segments]
    if not segments:
        return None, [], {}
    best_index = max(range(len(segments)), key=lambda index: analyses[index]["score"])
    best_analysis = analyses[best_index]
    if best_analysis.get("score", 0) > 0:
        return segments[best_index], analyses, best_analysis
    evidence_index = max(
        range(len(segments)),
        key=lambda index: (
            analyses[index]["origin_doc_count"]
            + analyses[index]["origin_faq_count"]
            + analyses[index]["rerank_doc_count"]
            + analyses[index]["prompt_doc_count"]
        ),
    )
    evidence_analysis = analyses[evidence_index]
    evidence_count = (
        evidence_analysis["origin_doc_count"]
        + evidence_analysis["origin_faq_count"]
        + evidence_analysis["rerank_doc_count"]
        + evidence_analysis["prompt_doc_count"]
    )
    if evidence_count <= 0:
        return None, analyses, {}
    return segments[evidence_index], analyses, evidence_analysis


def _normalize_url(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^https?://", "", text)
    text = text.rstrip("/")
    return text


def _answer_citations(answer: str) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for match in re.finditer(r"\[\[(\d+)\]\]\(([^)]+)\)", answer):
        start = max(answer.rfind("\n", 0, match.start()), answer.rfind("。", 0, match.start()), answer.rfind("；", 0, match.start()))
        context = answer[start + 1 : match.end()].strip()
        citations.append({"number": match.group(1), "url": match.group(2), "context": context})
    return citations


def _quoted_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in (r"[“\"]([^”\"]{2,80})[”\"]", r"「([^」]{2,80})」", r"`([^`]{2,80})`"):
        for match in re.finditer(pattern, text):
            phrase = match.group(1).strip()
            if phrase and phrase not in phrases:
                phrases.append(phrase)
    return phrases


def _doc_matches_url(doc: dict[str, Any], url: str) -> bool:
    doc_url = _normalize_url(_doc_url(doc))
    wanted = _normalize_url(url)
    return bool(doc_url and wanted and (doc_url == wanted or doc_url in wanted or wanted in doc_url))


def _detect_citation_mismatches(answer: str, prompt_docs: list[dict[str, Any]]) -> tuple[list[ClaimAlignment], list[str], bool]:
    alignments: list[ClaimAlignment] = []
    unsupported_claims: list[str] = []
    wrong_citation = False
    if not answer or not prompt_docs:
        return alignments, unsupported_claims, wrong_citation

    for citation in _answer_citations(answer):
        cited_docs = [doc for doc in prompt_docs if _doc_matches_url(doc, citation["url"])]
        if not cited_docs:
            continue
        cited_doc = cited_docs[0]
        cited_content = str(cited_doc.get("content") or "")
        missing_phrases = [phrase for phrase in _quoted_phrases(citation["context"]) if phrase not in cited_content]
        if not missing_phrases:
            alignments.append(
                ClaimAlignment(
                    claim=citation["context"],
                    support_status="supported_by_citation",
                    support_doc_ids=[_doc_id(cited_doc) or citation["number"]],
                    reason="引用文档正文覆盖该句中带引号的关键短语。",
                )
            )
            continue

        phrase_sources: dict[str, list[str]] = {}
        for phrase in missing_phrases:
            phrase_sources[phrase] = [
                _doc_id(doc) or str(index + 1)
                for index, doc in enumerate(prompt_docs)
                if phrase in str(doc.get("content") or "")
            ]
        if any(phrase_sources.values()):
            wrong_citation = True
            unsupported_claims.append(
                f"引用 {citation['url']} 未覆盖 {', '.join(missing_phrases)}；这些短语来自其他 prompt docs 或未被引用文档支持。"
            )
            alignments.append(
                ClaimAlignment(
                    claim=citation["context"],
                    support_status="wrong_citation",
                    support_doc_ids=[_doc_id(cited_doc) or citation["number"]],
                    reason=f"引用文档未覆盖关键短语：{', '.join(missing_phrases)}；其他来源：{phrase_sources}",
                )
            )
        else:
            alignments.append(
                ClaimAlignment(
                    claim=citation["context"],
                    support_status="partial_support",
                    support_doc_ids=[_doc_id(cited_doc) or citation["number"]],
                    reason=f"引用文档未覆盖关键短语：{', '.join(missing_phrases)}。",
                )
            )
    return alignments, unsupported_claims, wrong_citation


def _answer_claims(answer: str) -> list[str]:
    cleaned = re.sub(r"\*\*|__", "", answer)
    parts = re.split(r"(?:\n+\s*\d+\.\s*|。|\n{2,})", cleaned)
    claims = [re.sub(r"\s+", " ", part).strip() for part in parts]
    return [claim for claim in claims if len(claim) >= 8][:12]


def _field_entry(source_path: str, source_label: str, value: Any, confidence: float = 1.0) -> FieldMapEntry:
    return FieldMapEntry(
        source_path=source_path,
        source_label=source_label,
        raw_value=value,
        normalized_value=value,
        confidence=confidence,
    )


def ingest_fornax_trace(payload: Any, request: FornaxTraceIngestRequest) -> FornaxTraceIngestResponse:
    spans = _trace_spans(payload)
    middle_spans = _middle_node_spans(spans)
    middle_node_types = sorted({_span_type(span) for span in middle_spans if _span_type(span)})
    rag_node_types = sorted({_span_type(span) for span in middle_spans if _span_type(span) in RAG_NODE_TYPES})
    has_middle_node_trace = bool(middle_spans)
    fornax_evidence_status = "authoritative" if has_middle_node_trace else "insufficient"
    selected_segment, workflow_segment_summaries, selected_segment_summary = _select_trace_segment(spans)
    workspace_id = _first_nonempty(request.workspace_id, _root_tag(spans, "zhishang.workspace_id", "workspaceId", "workspace_id"), _first_tag(spans, "zhishang.workspace_id", "workspaceId", "workspace_id"))
    app_id = _first_nonempty(request.app_id, _root_tag(spans, "zhishang.app_id", "appId", "app_id"), _first_tag(spans, "zhishang.app_id", "appId", "app_id"))
    resolved_app = _resolve_workflow_mapping(workspace_id, app_id)
    node_mapping, mapping_status = _trace_node_mapping(spans, resolved_app)
    preprocess_span = _find_mapped_span(spans, node_mapping, ("preprocess", "ragpreprocess", "预处理"), ("ZhiShangRAGPreprocess",), names=("预处理",))
    recall_span = _find_mapped_span(spans, node_mapping, ("recall", "ragrecall", "召回"), ("ZhiShangRAGRecall",), names=("召回",))
    rerank_span = _find_mapped_span(spans, node_mapping, ("rerank", "ragrerank", "重排"), ("ZhiShangRAGRerank",), names=("重排",))
    qa_span = _find_mapped_span(spans, node_mapping, ("ragqa", "qa", "answer", "问答"), ("ZhiShangRAGQA",), names=("问答",))
    workflow_span = _find_span(spans, "workflow", names=("ExecuteWorkflow",))
    end_span = _find_mapped_span(spans, node_mapping, ("end", "结束"), ("End",), names=("结束",))
    if selected_segment:
        recall_span = selected_segment.get("recall_span") or recall_span
        rerank_span = selected_segment.get("rerank_span") or rerank_span
        qa_span = selected_segment.get("qa_span") or qa_span
        workflow_span = selected_segment.get("workflow_span") or workflow_span
        end_span = selected_segment.get("end_span") or end_span
    workflow_span_ios = _workflow_span_ios(spans, workflow_span)

    preprocess_input = _span_input(preprocess_span)
    preprocess_output = _span_output(preprocess_span)
    recall_output = _span_output(recall_span)
    rerank_output = _span_output(rerank_span)
    qa_input = _span_input(qa_span)
    qa_output = _span_output(qa_span)
    workflow_input = _span_input(workflow_span)
    workflow_output = _span_output(workflow_span)
    end_output = _span_output(end_span)

    pre_in = preprocess_input if isinstance(preprocess_input, dict) else {}
    pre_out = preprocess_output if isinstance(preprocess_output, dict) else {}
    qa_in = qa_input if isinstance(qa_input, dict) else {}
    qa_out = qa_output if isinstance(qa_output, dict) else {}
    workflow_in = workflow_input if isinstance(workflow_input, dict) else {}
    workflow_out = workflow_output if isinstance(workflow_output, dict) else {}

    query = _first_nonempty(
        request.query,
        selected_segment_summary.get("query") if selected_segment_summary else "",
        qa_out.get("query"),
        qa_in.get("query"),
        pre_out.get("query"),
        pre_in.get("query"),
        (workflow_in.get("sys") or {}).get("query") if isinstance(workflow_in.get("sys"), dict) else "",
    )
    answer = _first_nonempty(qa_out.get("answer"), workflow_out.get("end"), end_output if isinstance(end_output, str) else "")
    log_id = _first_nonempty(request.log_id, *(span.get("logid") for span in spans), request.case_id)
    case_id = request.case_id or log_id or "fornax-trace"
    fornax_space_id = _first_nonempty(request.fornax_space_id, _first_tag(spans, "fornax_space_id"))

    raw_docs = _raw_doc_lists(recall_output, rerank_output, qa_output, end_output)
    if not raw_docs["prompt_docs"] and selected_segment:
        raw_docs["prompt_docs"] = _collect_prompt_docs_from_spans(selected_segment.get("all_child_spans") or [])
    if not raw_docs["prompt_docs"]:
        raw_docs["prompt_docs"] = _collect_prompt_docs_from_spans(spans)
    dropped_relevant_docs = selected_segment_summary.get("dropped_relevant_docs", []) if selected_segment_summary else []
    selected_dropped_doc_ids = [str(doc.get("id")) for doc in dropped_relevant_docs if doc.get("id")]
    final_top_docs = selected_segment_summary.get("final_top_docs", []) if selected_segment_summary else []
    selected_final_faq_count = sum(1 for doc in final_top_docs[:5] if doc.get("is_faq"))
    selected_final_doc_count = sum(1 for doc in final_top_docs[:5] if not doc.get("is_faq"))
    selected_segment_note = ""
    if selected_dropped_doc_ids:
        selected_segment_note = (
            f"已选择 workflow span {selected_segment_summary.get('workflow_span_id')}："
            f"召回命中正式文档 {', '.join(selected_dropped_doc_ids)}，但 rerank/End top 结果中未保留；"
            f"最终 top5 中 FAQ={selected_final_faq_count}, 正式文档={selected_final_doc_count}。"
        )
    prompt_docs = raw_docs["prompt_docs"]
    claim_alignments: list[ClaimAlignment] = []
    unsupported_claims: list[str] = []
    wrong_citation = False
    if request.detect_citation_mismatches:
        claim_alignments, unsupported_claims, wrong_citation = _detect_citation_mismatches(answer, prompt_docs)

    judgement_signals: list[JudgementSignal] = []
    if wrong_citation:
        judgement_signals.append(
            JudgementSignal(
                key="wrong_citation",
                value=True,
                confidence=0.85,
                evidence_text="Fornax prompt_docs 与答案引用短语自动比对发现引用文档未覆盖完整 claim。",
            )
        )

    keywords: list[str] = []
    keyword_payload = pre_out.get("keyword")
    if isinstance(keyword_payload, dict):
        words = keyword_payload.get("words")
        if isinstance(words, list):
            keywords = [str(item) for item in words if str(item).strip()]

    attribution_request = AttributionRequest(
        case_input=CaseInput(
            query=query or "unknown query",
            judgement=request.judgement,
            workspace_id=workspace_id or "unknown",
            app_id=app_id or "unknown",
            retrieve_query_list=[query] if query else [],
            case_id=case_id,
            source_row=request.source_row,
            expected_knowledge_ids=selected_dropped_doc_ids,
            expected_knowledge_points=request.expected_knowledge_points,
            error_points=request.error_points,
        ),
        field_map={
            "log_id": _field_entry("fornax.trace.logid", "log_id", log_id or case_id, 1.0 if log_id else 0.5),
            "fornax_space_id": _field_entry("fornax.trace.custom_tags.fornax_space_id", "fornax_space_id", fornax_space_id, 1.0 if fornax_space_id else 0.0),
            "fornax_space_name": _field_entry("fornax.trace.lookup", "fornax_space_name", request.fornax_space_name, 1.0 if request.fornax_space_name else 0.0),
        },
        judgement_evidence=JudgementEvidence(
            source_type="evaluator" if request.judgement else "fornax_trace",
            raw_text=request.judgement,
            mapper_status="fornax_trace_ingest",
            signals=judgement_signals,
        ),
        preprocess=PreprocessEvidence(
            rewrite_query=str(pre_out.get("rewrite_query") or query or ""),
            keywords=keywords,
            answer_model=str((pre_out.get("answer_model") or {}).get("endpoint") or (pre_out.get("answer_model") or {}).get("modelName") or ""),
        ),
        retrieval=RetrievalEvidence(
            origin_doc_list=_evidence_docs(raw_docs["origin_doc_list"], "origin_doc_list"),
            origin_faq_list=_evidence_docs(raw_docs["origin_faq_list"], "origin_faq_list"),
            expected_knowledge_hit=True if selected_dropped_doc_ids else None,
            online_retrieval_hit=bool(raw_docs["origin_doc_list"] or raw_docs["origin_faq_list"]),
            knowledge_exists=True if selected_dropped_doc_ids else None,
            notes="; ".join(
                item
                for item in [
                    f"Fornax trace origin_doc_list={len(raw_docs['origin_doc_list'])}, origin_faq_list={len(raw_docs['origin_faq_list'])}",
                    selected_segment_note,
                ]
                if item
            ),
        ),
        rerank=RerankEvidence(
            rerank_docs=_evidence_docs(raw_docs["rerank_docs"], "rerank_docs"),
            prompt_docs=_evidence_docs(raw_docs["prompt_docs"], "prompt_docs"),
            expected_doc_survived_rerank=False if selected_dropped_doc_ids else bool(raw_docs["rerank_docs"]),
            expected_doc_in_prompt=False if selected_dropped_doc_ids else bool(raw_docs["prompt_docs"]),
            threshold_too_strict=bool(selected_dropped_doc_ids),
            noise_overload=bool(selected_dropped_doc_ids and selected_final_faq_count),
            notes=f"Fornax trace rerank_docs={len(raw_docs['rerank_docs'])}, prompt_docs={len(raw_docs['prompt_docs'])}",
        ),
        qa=QaEvidence(
            answer=answer,
            prompt_supports_answer=False if wrong_citation else None,
            answer_satisfies_expected=False if wrong_citation else None,
            unsupported_claims=unsupported_claims,
            answer_claims=_answer_claims(answer),
            claim_alignments=claim_alignments,
            alignment_status="fornax_trace_heuristic" if claim_alignments else "not_run",
            wrong_citation=wrong_citation,
            notes="由 Fornax ZhiShangRAGQA span 的 answer/prompt_docs 构建。",
        ),
        reference=ReferenceEvidence(
            source="fornax_trace_prompt_docs",
            support_docs=_evidence_docs(raw_docs["prompt_docs"], "prompt_docs"),
            confidence=0.82 if raw_docs["prompt_docs"] else None,
            notes="support_docs 来自原始 Fornax trace 的 prompt_docs。",
        ),
        workflow_replay=WorkflowReplayEvidence(
            enabled=True,
            status="ok" if has_middle_node_trace else "partial" if spans else "missing",
            extracted_evidence={
                "trace_source": "openplat_trace_detail",
                "fornax_evidence_status": fornax_evidence_status,
                "has_middle_node_trace": has_middle_node_trace,
                "middle_node_types": middle_node_types,
                "rag_node_types": rag_node_types,
                "log_id": log_id,
                "fornax_space_id": fornax_space_id,
                "workflow_span_ios": workflow_span_ios,
                "selected_workflow_span_io": next((item for item in workflow_span_ios if item.get("selected")), workflow_span_ios[0] if workflow_span_ios else {}),
                "resolved_app": resolved_app,
                "mapping_status": mapping_status,
                "workflow_segments": workflow_segment_summaries,
                "selected_workflow_segment": selected_segment_summary,
                "counts": {
                    "origin_doc_list": len(raw_docs["origin_doc_list"]),
                    "origin_faq_list": len(raw_docs["origin_faq_list"]),
                    "rerank_docs": len(raw_docs["rerank_docs"]),
                    "prompt_docs": len(raw_docs["prompt_docs"]),
                },
            },
            resolved_app=resolved_app,
            node_traces=node_mapping,
            notes=(
                "从 Fornax 原始 trace 摄取到 RAG/Start/End 中间节点；这些节点证据是历史 badcase 的权威证据。"
                if has_middle_node_trace
                else "Fornax trace 已获取，但没有可归因的 RAG/Start/End 中间节点；允许后续 live workflow replay 作为补充证据。"
            ),
        ),
    )

    summary = {
        "span_count": len(spans),
        "log_id": log_id,
        "fornax_space_id": fornax_space_id,
        "fornax_space_name": request.fornax_space_name,
        "workspace_id": workspace_id,
        "app_id": app_id,
        "query": query,
        "workflow_spans": [
            {
                "span_type": span.get("span_type") or span.get("type"),
                "span_name": span.get("span_name") or span.get("name"),
                "status": span.get("status"),
                "duration": span.get("duration"),
            }
            for span in spans
            if str(span.get("service_name") or "").endswith("open_platform_engine")
            or str(span.get("span_type") or "").startswith("ZhiShang")
            or str(span.get("span_type") or "") in {"Start", "End", "workflow"}
        ],
        "counts": attribution_request.workflow_replay.extracted_evidence["counts"],
        "workflow_segment_count": len(workflow_segment_summaries),
        "workflow_segments": workflow_segment_summaries,
        "selected_workflow_segment": selected_segment_summary,
        "workflow_span_count": len(workflow_span_ios),
        "selected_workflow_span_id": next((item.get("span_id") for item in workflow_span_ios if item.get("selected")), ""),
        "mapping_status": mapping_status,
        "resolved_app": resolved_app,
        "fornax_evidence_status": fornax_evidence_status,
        "has_middle_node_trace": has_middle_node_trace,
        "middle_node_types": middle_node_types,
        "rag_node_types": rag_node_types,
        "citation_mismatches": unsupported_claims,
    }
    evidence = {
        "preprocess": pre_out,
        "counts": summary["counts"],
        "answer": answer,
        "prompt_docs_raw": raw_docs["prompt_docs"],
        "rerank_docs_raw": raw_docs["rerank_docs"],
        "origin_faq_list_raw": raw_docs["origin_faq_list"],
        "origin_doc_list_raw": raw_docs["origin_doc_list"],
        "workflow_span_ios": workflow_span_ios,
        "node_mapping": node_mapping,
        "resolved_app": resolved_app,
    }
    return FornaxTraceIngestResponse(
        attribution_request=attribution_request,
        trace_summary=summary,
        trace_evidence=evidence,
        evidence_report_markdown=render_evidence_report(summary, answer, raw_docs["prompt_docs"], workflow_span_ios),
    )


def render_evidence_report(summary: dict[str, Any], answer: str, prompt_docs: list[dict[str, Any]], workflow_span_ios: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "# Fornax Trace Evidence",
        "",
        f"- log_id: {summary.get('log_id') or ''}",
        f"- fornax_space: {summary.get('fornax_space_name') or ''} / {summary.get('fornax_space_id') or ''}",
        f"- workspace_id: {summary.get('workspace_id') or ''}",
        f"- app_id: {summary.get('app_id') or ''}",
        f"- spans: {summary.get('span_count')}",
        f"- counts: {json.dumps(summary.get('counts') or {}, ensure_ascii=False)}",
        f"- mapping_status: {summary.get('mapping_status') or ''}",
        "",
        "## Workflow Span I/O",
        "",
    ]
    for item in workflow_span_ios or []:
        lines.extend(
            [
                f"### workflow span {item.get('span_id') or ''}",
                "",
                f"- selected: `{bool(item.get('selected'))}`",
                f"- node_id: `{item.get('node_id') or ''}`",
                "",
                "#### input",
                "",
                "```json",
                json.dumps(item.get("input"), ensure_ascii=False, indent=2, default=str),
                "```",
                "",
                "#### output",
                "",
                "```json",
                json.dumps(item.get("output"), ensure_ascii=False, indent=2, default=str),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Answer",
            "",
            answer or "",
            "",
            "## Prompt Docs",
        ]
    )
    for index, doc in enumerate(prompt_docs, 1):
        lines.extend(
            [
                "",
                f"### [{index}] {doc.get('title') or ''}",
                f"- id: {_doc_id(doc) or ''}",
                f"- url: {_doc_url(doc)}",
                f"- recallSource: {doc.get('recallSource') or ''}",
                f"- score: {_doc_score(doc) if _doc_score(doc) is not None else ''}",
                "",
                str(doc.get("content") or ""),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
