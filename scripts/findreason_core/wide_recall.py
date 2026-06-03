from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .models import AttributionRequest, EvidenceDoc, WideRecallEvidence
from .workflow_replay import (
    DEFAULT_WORKFLOW_REPLAY_URL,
    _business_error,
    _extract_evidence,
    _safe_error,
    _truncate,
    _workflow_headers,
    _workflow_endpoint,
    build_workflow_payload,
    resolve_workflow_auth_token,
    resolve_workflow,
)

MAX_QUERY_VARIANTS = 5


def _normalize_variant(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"\s+", " ", value).strip()
    return "" if normalized in {"无", "未标注", "unknown"} else normalized


def _query_variants(request: AttributionRequest) -> list[str]:
    candidates = [
        request.case_input.query,
        request.preprocess.rewrite_query,
        *request.case_input.retrieve_query_list,
    ]
    variants: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_variant(candidate)
        if not normalized or normalized in seen:
            continue
        variants.append(normalized[:200])
        seen.add(normalized)
        if len(variants) >= MAX_QUERY_VARIANTS:
            break
    return variants


def _build_wide_recall_payload(request: AttributionRequest, input_schema: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[str], list[str]]:
    variants = _query_variants(request)
    payload, missing_fields = build_workflow_payload(
        request,
        input_schema or [
            {"key": "RankQuery", "type": "String", "required": True},
            {"key": "RetrieveQueryList", "type": "Array<String>", "required": False},
            {"key": "topk", "type": "Number", "required": False},
        ],
        query_variants=variants,
        topk_env="WIDE_RECALL_TOPK",
        default_topk=50,
    )
    return payload, variants, missing_fields


def _as_wide_doc(doc: dict[str, Any], fallback_source: str) -> dict[str, Any] | None:
    try:
        evidence_doc = EvidenceDoc.model_validate(doc)
    except Exception:
        return None
    source = evidence_doc.source or fallback_source
    if source.startswith("workflow_replay"):
        source = source.replace("workflow_replay", "wide_recall", 1)
    elif not source.startswith("wide_recall"):
        source = f"wide_recall:{source}"
    return evidence_doc.model_copy(update={"source": source}).model_dump(mode="json")


def _extract_wide_recall_evidence(response_payload: Any, variants: list[str], expected_ids: list[str]) -> dict[str, Any]:
    extracted = _extract_evidence(response_payload, include_output_as_origin=True)
    docs = [
        wide_doc
        for item in extracted.get("origin_doc_list", [])
        if isinstance(item, dict)
        for wide_doc in [_as_wide_doc(item, "wide_recall:doc")]
        if wide_doc
    ]
    faqs = [
        wide_doc
        for item in extracted.get("origin_faq_list", [])
        if isinstance(item, dict)
        for wide_doc in [_as_wide_doc(item, "wide_recall:faq")]
        if wide_doc
    ]
    found_ids = {str(item.get("id")) for item in docs + faqs if item.get("id") is not None}
    matched_expected_ids = [expected_id for expected_id in expected_ids if expected_id in found_ids]
    return {
        "wide_recall_docs": docs,
        "wide_recall_faqs": faqs,
        "matched_expected_ids": matched_expected_ids,
        "query_variants": variants,
        "answer": extracted.get("answer", ""),
        "reasoning": extracted.get("reasoning", ""),
    }


def _merge_wide_recall_evidence(request: AttributionRequest, wide_recall: WideRecallEvidence) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    enriched.wide_recall = wide_recall
    if wide_recall.status != "ok":
        return enriched

    extracted = wide_recall.extracted_evidence
    wide_docs = [
        EvidenceDoc.model_validate(doc)
        for doc in extracted.get("wide_recall_docs", [])
        if isinstance(doc, dict)
    ]
    wide_faqs = [
        EvidenceDoc.model_validate(doc)
        for doc in extracted.get("wide_recall_faqs", [])
        if isinstance(doc, dict)
    ]
    if wide_docs or wide_faqs:
        enriched.retrieval.wide_recall_docs = wide_docs + wide_faqs
        enriched.retrieval.knowledge_exists = True
    return enriched


async def run_wide_recall(request: AttributionRequest) -> AttributionRequest:
    endpoint = os.getenv("WIDE_RECALL_WORKFLOW_URL") or DEFAULT_WORKFLOW_REPLAY_URL
    token = None
    auth_token_source = "not_configured"
    request_payload, variants, missing_fields = _build_wide_recall_payload(request)

    try:
        token, auth_token_source = await resolve_workflow_auth_token(request.case_input.workspace_id)
    except Exception as exc:
        wide_recall = WideRecallEvidence(
            enabled=True,
            status="error",
            endpoint=endpoint,
            request_payload=request_payload,
            query_variants=variants,
            auth_token_source=auth_token_source,
            error=_safe_error(exc),
            notes="获取 workspace 级 workflow apiKey 失败，未调用诊断宽召回。",
        )
        return _merge_wide_recall_evidence(request, wide_recall)

    if not token:
        wide_recall = WideRecallEvidence(
            enabled=False,
            status="not_configured",
            endpoint=endpoint,
            request_payload=request_payload,
            query_variants=variants,
            auth_token_source=auth_token_source,
            notes="未配置 OPEN_PLAT_BOOTSTRAP_TOKEN 或 WORKFLOW_AUTH_TOKEN，跳过诊断宽召回。",
        )
        return _merge_wide_recall_evidence(request, wide_recall)

    try:
        resolved_app = resolve_workflow(request)
        input_schema = [item for item in resolved_app.get("input_schema", []) if isinstance(item, dict)]
        endpoint = _workflow_endpoint(resolved_app)
        request_payload, variants, missing_fields = _build_wide_recall_payload(request, input_schema)
        if missing_fields:
            wide_recall = WideRecallEvidence(
                enabled=True,
                status="error",
                endpoint=endpoint,
                request_payload=request_payload,
                query_variants=variants,
                auth_token_source=auth_token_source,
                error=f"Workflow Start schema contains unmapped required fields: {', '.join(missing_fields)}",
                notes="诊断宽召回 schema 输入缺少可映射字段，已停止调用 workflow。",
            )
            return _merge_wide_recall_evidence(request, wide_recall)
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                endpoint,
                headers=_workflow_headers(token),
                json=request_payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Wide recall HTTP {response.status_code}: {response.text[:500]}")
            response_payload = response.json()
            business_error = _business_error(response_payload)
            if business_error:
                wide_recall = WideRecallEvidence(
                    enabled=True,
                    status="error",
                    endpoint=endpoint,
                    request_payload=request_payload,
                    response_payload=_truncate(response_payload),
                    query_variants=variants,
                    auth_token_source=auth_token_source,
                    error=business_error,
                    notes="诊断宽召回返回业务失败，未构建出宽召回证据。",
                )
                return _merge_wide_recall_evidence(request, wide_recall)

        extracted = _extract_wide_recall_evidence(
            response_payload,
            variants,
            request.case_input.expected_knowledge_ids,
        )
        wide_recall = WideRecallEvidence(
            enabled=True,
            status="ok",
            endpoint=endpoint,
            request_payload=request_payload,
            response_payload=_truncate(response_payload),
            extracted_evidence=extracted,
            query_variants=variants,
            matched_expected_ids=extracted["matched_expected_ids"],
            auth_token_source=auth_token_source,
            notes="已通过 WideRecallTool 执行高 topK 诊断宽召回。",
        )
    except Exception as exc:
        wide_recall = WideRecallEvidence(
            enabled=True,
            status="error",
            endpoint=endpoint,
            request_payload=request_payload,
            query_variants=variants,
            auth_token_source=auth_token_source,
            error=_safe_error(exc),
            notes="诊断宽召回失败，不将失败视为知识不存在。",
        )
    return _merge_wide_recall_evidence(request, wide_recall)
