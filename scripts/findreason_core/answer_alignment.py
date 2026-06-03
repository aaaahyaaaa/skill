from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .models import AttributionRequest, ClaimAlignment

SUPPORTED_STATUSES = {"supported", "partial", "partially_supported"}
UNSUPPORTED_STATUSES = {"unsupported", "contradicted"}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _claim_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return _string_list(value)
    if not isinstance(value, list):
        return []
    claims: list[str] = []
    for item in value:
        if isinstance(item, str):
            claims.extend(_string_list(item))
        elif isinstance(item, dict):
            claims.extend(_string_list(item.get("claim") or item.get("text") or item.get("answer_claim")))
    return claims


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return merged


def _has_alignment_evidence(request: AttributionRequest) -> bool:
    return bool(request.rerank.prompt_docs or request.reference.support_docs or request.reference.support_claims)


def _normalize_status(value: Any) -> str:
    normalized = str(value or "uncertain").strip().lower()
    if normalized in {"support", "supported_by_prompt", "supported_by_reference"}:
        return "supported"
    if normalized in {"partially-supported", "partially_supported"}:
        return "partially_supported"
    if normalized in {"not_supported", "no_support", "unsupported_by_prompt"}:
        return "unsupported"
    if normalized in {"contradict", "conflict", "conflicted"}:
        return "contradicted"
    if normalized in SUPPORTED_STATUSES or normalized == "uncertain":
        return normalized
    return "uncertain"


def _alignment_from_mapping(value: dict[str, Any]) -> ClaimAlignment | None:
    claim = value.get("claim") or value.get("text") or value.get("answer_claim")
    if not claim:
        return None
    try:
        return ClaimAlignment(
            claim=str(claim).strip(),
            support_status=_normalize_status(value.get("support_status") or value.get("status")),
            support_doc_ids=_string_list(
                value.get("support_doc_ids")
                or value.get("supporting_doc_ids")
                or value.get("doc_ids")
                or value.get("evidence_doc_ids")
            ),
            reason=str(value.get("reason") or value.get("explanation") or "").strip(),
        )
    except ValidationError:
        return None


def _claim_alignments(value: Any) -> list[ClaimAlignment]:
    if not isinstance(value, list):
        return []
    alignments: list[ClaimAlignment] = []
    for item in value:
        if isinstance(item, str):
            alignments.append(ClaimAlignment(claim=item, support_status="uncertain"))
        elif isinstance(item, dict):
            alignment = _alignment_from_mapping(item)
            if alignment is not None:
                alignments.append(alignment)
    return alignments


def _apply_alignment_payload(request: AttributionRequest, payload: dict[str, Any]) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    qa = enriched.qa

    answer_claims = _claim_strings(payload.get("answer_claims")) or _claim_strings(payload.get("claims"))
    if answer_claims:
        qa.answer_claims = answer_claims

    alignments = _claim_alignments(payload.get("claim_alignments") or payload.get("claims"))
    if alignments:
        qa.claim_alignments = alignments

    unsupported_from_alignments = [
        item.claim for item in alignments if item.support_status in UNSUPPORTED_STATUSES and item.claim
    ]
    unsupported_claims = _string_list(payload.get("unsupported_claims"))
    qa.unsupported_claims = _merge_unique(qa.unsupported_claims, unsupported_claims, unsupported_from_alignments)

    missing_expected_points = _string_list(payload.get("missing_expected_points"))
    if missing_expected_points:
        qa.missing_expected_points = _merge_unique(qa.missing_expected_points, missing_expected_points)

    prompt_supports_answer = _optional_bool(payload.get("prompt_supports_answer"))
    if prompt_supports_answer is not None:
        qa.prompt_supports_answer = prompt_supports_answer
    elif alignments and all(item.support_status in SUPPORTED_STATUSES for item in alignments):
        qa.prompt_supports_answer = True

    answer_satisfies_expected = _optional_bool(payload.get("answer_satisfies_expected"))
    if answer_satisfies_expected is not None:
        qa.answer_satisfies_expected = answer_satisfies_expected
    elif qa.unsupported_claims or qa.missing_expected_points:
        qa.answer_satisfies_expected = False
    elif alignments and all(item.support_status == "supported" for item in alignments):
        qa.answer_satisfies_expected = True

    partial_answer = _optional_bool(payload.get("partial_answer"))
    qa.partial_answer = qa.partial_answer or bool(partial_answer) or bool(qa.missing_expected_points)

    if _optional_bool(payload.get("hallucination")) is True:
        qa.hallucination = True
    if _optional_bool(payload.get("wrong_citation")) is True:
        qa.wrong_citation = True

    qa.alignment_status = "ok"
    qa.alignment_error = None
    return enriched


def _has_host_alignment(request: AttributionRequest) -> bool:
    qa = request.qa
    return bool(
        qa.answer_claims
        or qa.claim_alignments
        or qa.unsupported_claims
        or qa.missing_expected_points
        or qa.prompt_supports_answer is not None
        or qa.answer_satisfies_expected is not None
    )


def _derive_from_host_alignment(request: AttributionRequest) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    qa = enriched.qa
    unsupported_from_alignments = [
        item.claim for item in qa.claim_alignments if item.support_status in UNSUPPORTED_STATUSES and item.claim
    ]
    qa.unsupported_claims = _merge_unique(qa.unsupported_claims, unsupported_from_alignments)
    if qa.prompt_supports_answer is None and qa.claim_alignments:
        qa.prompt_supports_answer = all(item.support_status in SUPPORTED_STATUSES for item in qa.claim_alignments)
    if qa.answer_satisfies_expected is None:
        if qa.unsupported_claims or qa.missing_expected_points:
            qa.answer_satisfies_expected = False
        elif qa.claim_alignments and all(item.support_status == "supported" for item in qa.claim_alignments):
            qa.answer_satisfies_expected = True
    qa.partial_answer = qa.partial_answer or bool(qa.missing_expected_points)
    qa.alignment_status = "host_agent"
    qa.alignment_error = None
    return enriched


async def align_answer_evidence(request: AttributionRequest) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    if not enriched.qa.answer.strip():
        enriched.qa.alignment_status = "not_needed"
        enriched.qa.alignment_error = None
        return enriched
    if _has_host_alignment(enriched):
        return _derive_from_host_alignment(enriched)
    if not _has_alignment_evidence(enriched):
        enriched.qa.alignment_status = "insufficient_evidence"
        enriched.qa.alignment_error = None
        return enriched
    enriched.qa.alignment_status = "fallback"
    enriched.qa.alignment_error = None
    return enriched
