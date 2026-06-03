from __future__ import annotations

import json
import re
from typing import Any

from ..models import AttributionRequest, FieldMapEntry


FIELD_ALIASES: dict[str, list[str]] = {
    "query": ["原始问题", "query", "Query", "问题", "用户问题"],
    "workspace_id": ["workspaceId", "workspace_id", "WorkspaceID", "workspace"],
    "app_id": ["appId", "app_id", "AppID", "应用ID"],
    "judgement": ["人工结论", "人工/评估器结论", "review_result", "evaluator_result", "judgement"],
    "expected_knowledge_ids": ["期望知识ID", "召回正确id/标题", "expected_knowledge_id", "expected_id"],
}


def apply_source_adapter(request: AttributionRequest) -> AttributionRequest:
    adapted = request.model_copy(deep=True)
    judgement_json = _manual_judgement_json(adapted.case_input.judgement)
    raw_sheet_row = judgement_json.get("raw_sheet_row") if isinstance(judgement_json.get("raw_sheet_row"), dict) else {}

    field_map = dict(adapted.field_map)
    for field in ("query", "workspace_id", "app_id", "judgement", "expected_knowledge_ids"):
        field_map.setdefault(field, _field_map_entry(field, adapted, judgement_json, raw_sheet_row))

    task_id = str(judgement_json.get("task_id") or "").strip()
    if task_id and not adapted.case_input.case_id:
        adapted.case_input.case_id = task_id
    source_row = judgement_json.get("source_row") or adapted.case_input.source_row
    if source_row is not None and adapted.case_input.source_row is None:
        adapted.case_input.source_row = str(source_row)

    expected_entry = field_map.get("expected_knowledge_ids")
    expected_values = _string_list(expected_entry.normalized_value if expected_entry else None)
    if expected_values:
        merged = list(adapted.case_input.expected_knowledge_ids)
        for value in expected_values:
            if value not in merged:
                merged.append(value)
        adapted.case_input.expected_knowledge_ids = merged

    adapted.field_map = field_map
    return adapted


def _manual_judgement_json(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    match = re.search(r"(?:^|\n)manual_judgement_json[：:]\s*(\{.*\})\s*$", text, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _field_map_entry(
    field: str,
    request: AttributionRequest,
    judgement_json: dict[str, Any],
    raw_sheet_row: dict[str, Any],
) -> FieldMapEntry:
    raw_value, label = _raw_value(field, request, judgement_json, raw_sheet_row)
    normalized = _normalized_value(field, raw_value, request)
    missing = None if _has_value(normalized) else f"{field} not found in source"
    source_path = f"raw_case.raw_sheet_row.{label}" if label and label in raw_sheet_row else f"case_input.{field}"
    if label and label not in raw_sheet_row and judgement_json:
        source_path = f"manual_judgement_json.{label}"
    return FieldMapEntry(
        source_path=source_path,
        source_label=label or field,
        raw_value=raw_value,
        normalized_value=normalized,
        confidence=1.0 if missing is None else 0.0,
        missing_reason=missing,
    )


def _raw_value(
    field: str,
    request: AttributionRequest,
    judgement_json: dict[str, Any],
    raw_sheet_row: dict[str, Any],
) -> tuple[Any, str]:
    for label in FIELD_ALIASES[field]:
        if label in raw_sheet_row and _has_value(raw_sheet_row[label]):
            return raw_sheet_row[label], label

    case_value = {
        "query": request.case_input.query,
        "workspace_id": request.case_input.workspace_id,
        "app_id": request.case_input.app_id,
        "judgement": request.case_input.judgement,
        "expected_knowledge_ids": request.case_input.expected_knowledge_ids,
    }.get(field)
    if _has_value(case_value):
        return case_value, field

    if field == "expected_knowledge_ids":
        primary_review = judgement_json.get("primary_review") if isinstance(judgement_json.get("primary_review"), dict) else {}
        retrieval = primary_review.get("retrieval") if isinstance(primary_review.get("retrieval"), dict) else {}
        expected_id = retrieval.get("expected_knowledge_id") or judgement_json.get("expected_knowledge_id")
        if _has_value(expected_id):
            return expected_id, "primary_review.retrieval.expected_knowledge_id"

    return None, field


def _normalized_value(field: str, raw_value: Any, request: AttributionRequest) -> Any:
    if field == "expected_knowledge_ids":
        return _string_list(raw_value)
    if isinstance(raw_value, str):
        return raw_value.strip()
    return raw_value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text or text in {"无", "未标注"}:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return _string_list(parsed)
    return [part.strip() for part in re.split(r"[,，;；\s]+", text) if part.strip()]


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() not in {"无", "未标注", "未知"}
    if isinstance(value, list):
        return bool(value)
    return True
