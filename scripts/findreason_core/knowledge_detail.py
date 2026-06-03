from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .models import AttributionRequest, EvidenceDoc, KnowledgeDetailEvidence
from .workflow_replay import _safe_error, _truncate

DEFAULT_KNOWLEDGE_DETAIL_URL = "https://ad-sirius.bytedance.net/api/sirius_knowledge/v1/search/docs"
MIN_COMPLETE_CONTENT_CHARS = 80


def _is_numeric_id(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"\d+", value.strip()))


def _content_is_complete(value: str | None) -> bool:
    return bool(value and len(value.strip()) >= MIN_COMPLETE_CONTENT_CHARS)


def _raw_doc_id(doc: Any) -> str | None:
    if isinstance(doc, EvidenceDoc):
        return doc.id
    if isinstance(doc, dict):
        raw_id = doc.get("id") or doc.get("doc_id") or doc.get("knowledge_id") or doc.get("document_id")
        return str(raw_id) if raw_id is not None else None
    return None


def _raw_doc_content(doc: Any) -> str:
    if isinstance(doc, EvidenceDoc):
        return doc.content
    if isinstance(doc, dict):
        return str(doc.get("content") or doc.get("text") or doc.get("chunk") or doc.get("summary") or "")
    return ""


def _doc_needs_detail(doc: Any) -> str | None:
    doc_id = _raw_doc_id(doc)
    if not _is_numeric_id(doc_id) or _content_is_complete(_raw_doc_content(doc)):
        return None
    return doc_id.strip() if doc_id else None


def _collect_detail_ids(request: AttributionRequest) -> tuple[list[str], list[str]]:
    requested: list[str] = []
    skipped: list[str] = []
    seen_requested: set[str] = set()
    seen_skipped: set[str] = set()

    def add_requested(doc_id: str | None) -> None:
        normalized_id = str(doc_id).strip() if doc_id is not None else ""
        if _is_numeric_id(normalized_id) and normalized_id not in seen_requested:
            requested.append(normalized_id)
            seen_requested.add(normalized_id)

    def add_skipped(doc_id: str | None) -> None:
        if doc_id and doc_id not in seen_skipped and doc_id not in seen_requested:
            skipped.append(doc_id)
            seen_skipped.add(doc_id)

    for expected_id in request.case_input.expected_knowledge_ids:
        add_requested(expected_id)

    candidates: list[Any] = []
    candidates.extend(request.workflow_replay.extracted_evidence.get("origin_doc_list", []) or [])
    candidates.extend(request.wide_recall.extracted_evidence.get("wide_recall_docs", []) or [])
    candidates.extend(request.wide_recall.extracted_evidence.get("wide_recall_faqs", []) or [])
    candidates.extend(request.retrieval.origin_doc_list)
    candidates.extend(request.retrieval.wide_recall_docs)

    for candidate in candidates:
        doc_id = _raw_doc_id(candidate)
        detail_id = _doc_needs_detail(candidate)
        if detail_id:
            add_requested(detail_id)
        else:
            add_skipped(doc_id)
    return requested, skipped


def _parse_detail_content(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    match = re.match(r"\s*Title:(.*?)\nContent:(.*)\s*$", value, flags=re.DOTALL)
    if match:
        return {
            "title": match.group(1).strip(),
            "content": match.group(2).strip(),
        }
    return {"title": "", "content": value.strip()}


def _extract_detail_docs(response_payload: Any, requested_ids: list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    if isinstance(response_payload, dict):
        raw_contents = response_payload.get("contents")
        if raw_contents is None and isinstance(response_payload.get("data"), dict):
            raw_contents = response_payload["data"].get("contents")
    else:
        raw_contents = None

    contents = raw_contents if isinstance(raw_contents, list) else []
    details: dict[str, dict[str, str]] = {}
    for doc_id, raw_content in zip(requested_ids, contents):
        parsed = _parse_detail_content(raw_content)
        if parsed:
            details[doc_id] = {"id": doc_id, **parsed}
    missing_ids = [doc_id for doc_id in requested_ids if doc_id not in details]
    return details, missing_ids


def _apply_detail_to_dict(doc: dict[str, Any], details: dict[str, dict[str, str]]) -> bool:
    doc_id = _raw_doc_id(doc)
    if not doc_id or doc_id not in details:
        return False
    detail = details[doc_id]
    if detail.get("title"):
        doc["title"] = detail["title"]
    if detail.get("content"):
        doc["content"] = detail["content"]
    return True


def _apply_detail_to_doc(doc: EvidenceDoc, details: dict[str, dict[str, str]]) -> EvidenceDoc:
    if not doc.id or doc.id not in details:
        return doc
    detail = details[doc.id]
    return doc.model_copy(
        update={
            "title": detail.get("title") or doc.title,
            "content": detail.get("content") or doc.content,
        }
    )


def _detail_to_doc(doc_id: str, detail: dict[str, str], source: str) -> EvidenceDoc:
    return EvidenceDoc(
        id=doc_id,
        title=detail.get("title", ""),
        content=detail.get("content", ""),
        source=source,
    )


def _merge_knowledge_details(
    request: AttributionRequest,
    detail: KnowledgeDetailEvidence,
    details: dict[str, dict[str, str]],
) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    enriched.knowledge_detail = detail
    if not details:
        return enriched

    for key in ("origin_doc_list",):
        items = enriched.workflow_replay.extracted_evidence.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    _apply_detail_to_dict(item, details)

    for key in ("wide_recall_docs", "wide_recall_faqs"):
        items = enriched.wide_recall.extracted_evidence.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    _apply_detail_to_dict(item, details)

    enriched.retrieval.origin_doc_list = [
        _apply_detail_to_doc(doc, details) for doc in enriched.retrieval.origin_doc_list
    ]
    enriched.retrieval.origin_faq_list = [
        _apply_detail_to_doc(doc, details) for doc in enriched.retrieval.origin_faq_list
    ]
    enriched.retrieval.wide_recall_docs = [
        _apply_detail_to_doc(doc, details) for doc in enriched.retrieval.wide_recall_docs
    ]

    expected_detail_docs = [
        _detail_to_doc(doc_id, details[doc_id], "knowledge_detail:expected_knowledge_id")
        for doc_id in enriched.case_input.expected_knowledge_ids
        if doc_id in details
    ]
    if expected_detail_docs:
        existing_ids = {doc.id for doc in enriched.reference.support_docs if doc.id}
        enriched.reference.support_docs.extend(doc for doc in expected_detail_docs if doc.id not in existing_ids)
        if enriched.reference.source in {"", "none"}:
            enriched.reference.source = "knowledge_detail"
        enriched.retrieval.knowledge_exists = True
    return enriched


async def run_knowledge_detail(request: AttributionRequest) -> AttributionRequest:
    endpoint = os.getenv("KNOWLEDGE_DETAIL_URL", DEFAULT_KNOWLEDGE_DETAIL_URL)
    requested_ids, skipped_ids = _collect_detail_ids(request)
    request_payload = {"doc_ids": requested_ids}
    if os.getenv("FINDREASON_LIVE", "true").lower() in {"0", "false", "no"}:
        detail = KnowledgeDetailEvidence(
            enabled=False,
            status="not_configured",
            endpoint=endpoint,
            request_payload=request_payload,
            extracted_evidence={
                "requested_ids": requested_ids,
                "hydrated_docs": [],
                "expected_knowledge_docs": [],
                "matched_expected_ids": [],
                "missing_ids": [],
                "skipped_ids": skipped_ids,
                "hit_count": 0,
                "failed_count": 0,
            },
            notes="live=false，跳过知识详情在线补全。",
        )
        return _merge_knowledge_details(request, detail, {})
    if not requested_ids:
        detail = KnowledgeDetailEvidence(
            enabled=False,
            status="not_needed",
            endpoint=endpoint,
            request_payload=request_payload,
            extracted_evidence={
                "requested_ids": [],
                "hydrated_docs": [],
                "expected_knowledge_docs": [],
                "matched_expected_ids": [],
                "missing_ids": [],
                "skipped_ids": skipped_ids,
                "hit_count": 0,
                "failed_count": 0,
            },
            notes="未发现需要按知识 ID 补全正文的数字型文档。",
        )
        return _merge_knowledge_details(request, detail, {})

    try:
        timeout_seconds = float(os.getenv("KNOWLEDGE_DETAIL_TIMEOUT_SECONDS", "30"))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(endpoint, json=requested_ids)
            if response.status_code >= 400:
                raise RuntimeError(f"Knowledge detail HTTP {response.status_code}: {response.text[:500]}")
            response_payload = response.json()
        details, missing_ids = _extract_detail_docs(response_payload, requested_ids)
        status = "ok" if not missing_ids else "partial"
        hydrated_docs = list(details.values())
        matched_expected_ids = [
            doc_id for doc_id in request.case_input.expected_knowledge_ids if doc_id in details
        ]
        expected_docs = [details[doc_id] for doc_id in matched_expected_ids]
        detail = KnowledgeDetailEvidence(
            enabled=True,
            status=status,
            endpoint=endpoint,
            request_payload=request_payload,
            response_payload=_truncate(response_payload),
            extracted_evidence={
                "requested_ids": requested_ids,
                "hydrated_docs": hydrated_docs,
                "expected_knowledge_docs": expected_docs,
                "matched_expected_ids": matched_expected_ids,
                "missing_ids": missing_ids,
                "skipped_ids": skipped_ids,
                "hit_count": len(hydrated_docs),
                "failed_count": len(missing_ids),
            },
            notes="已按知识 ID 补全召回文档正文。" if status == "ok" else "部分知识 ID 未返回正文，已补全可用结果。",
        )
        return _merge_knowledge_details(request, detail, details)
    except Exception as exc:
        detail = KnowledgeDetailEvidence(
            enabled=True,
            status="error",
            endpoint=endpoint,
            request_payload=request_payload,
            extracted_evidence={
                "requested_ids": requested_ids,
                "hydrated_docs": [],
                "expected_knowledge_docs": [],
                "matched_expected_ids": [],
                "missing_ids": requested_ids,
                "skipped_ids": skipped_ids,
                "hit_count": 0,
                "failed_count": len(requested_ids),
            },
            error=_safe_error(exc),
            notes="知识详情补全失败，继续使用已有召回证据归因。",
        )
        return _merge_knowledge_details(request, detail, {})
