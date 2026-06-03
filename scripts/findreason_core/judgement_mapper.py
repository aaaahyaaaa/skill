from __future__ import annotations

import json
import re
from typing import Any

from .models import AttributionRequest, JudgementEvidence, JudgementSignal


SIGNAL_CONFIDENCE_HIGH = 0.9
SIGNAL_CONFIDENCE_MEDIUM = 0.75


def _normalize_empty(value: str) -> str:
    trimmed = value.strip()
    return "" if trimmed in {"无", "未标注", "未知", "空"} else trimmed


def _read_label(text: str, label: str) -> str:
    escaped_label = re.escape(label)
    match = re.search(rf"(?:^|\n){escaped_label}[：:]\s*(.*)", text)
    return _normalize_empty(match.group(1)) if match else ""


def _parse_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parsed = _parse_json_value(value)
        if isinstance(parsed, list):
            return _string_values(parsed)
        return [value.strip()] if value.strip() else []
    return [str(value).strip()] if str(value).strip() else []


def _cn_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip()
    if text in {"是", "有", "有知识", "准确", "正确", "true", "True", "1"}:
        return True
    if text in {"否", "无", "无知识", "不准确", "不正确", "false", "False", "0"}:
        return False
    return None


def _infer_source_type(text: str) -> str:
    if not text.strip():
        return "empty"
    has_human = bool(re.search(r"人工|审核人|标注|reviewer", text, re.IGNORECASE))
    has_evaluator = bool(re.search(r"评估器|evaluator|grader|score|rubric", text, re.IGNORECASE))
    if has_human and has_evaluator:
        return "mixed"
    if has_evaluator:
        return "evaluator"
    if has_human:
        return "human"
    return "unknown"


def _add_signal(
    signals: list[JudgementSignal],
    key: str,
    value: Any,
    evidence_text: str,
    confidence: float = SIGNAL_CONFIDENCE_MEDIUM,
) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    if isinstance(value, list) and not value:
        return
    signals.append(
        JudgementSignal(
            key=key,
            value=value,
            source="judgement",
            confidence=confidence,
            evidence_text=evidence_text.strip(),
        )
    )


def _fallback_from_fixed_labels(text: str, error: str | None = None) -> JudgementEvidence:
    if not text.strip():
        return JudgementEvidence(source_type="empty", raw_text="", mapper_status="empty", signals=[])

    signals: list[JudgementSignal] = []
    judgement_json = _parse_json_object(_read_label(text, "manual_judgement_json"))
    primary_review = judgement_json.get("primary_review") if isinstance(judgement_json.get("primary_review"), dict) else {}
    retrieval = primary_review.get("retrieval") if isinstance(primary_review.get("retrieval"), dict) else {}

    retrieve_queries = _string_values(judgement_json.get("retrieve_query_list")) or _string_values(_read_label(text, "RetrieveQueryList"))
    _add_signal(signals, "retrieve_query", retrieve_queries, _read_label(text, "RetrieveQueryList") or "manual_judgement_json.retrieve_query_list")

    is_knowledge_qa = _cn_bool(primary_review.get("is_knowledge_qa"))
    if is_knowledge_qa is None:
        is_knowledge_qa = _cn_bool(_read_label(text, "是否知识问答"))
    _add_signal(signals, "is_knowledge_qa", is_knowledge_qa, _read_label(text, "是否知识问答") or "primary_review.is_knowledge_qa")

    question_scene = str(primary_review.get("question_scene") or _read_label(text, "问题场景") or "").strip()
    _add_signal(signals, "question_scene", question_scene, question_scene, SIGNAL_CONFIDENCE_HIGH)

    retrieval_accurate = _cn_bool(retrieval.get("is_accurate"))
    if retrieval_accurate is None:
        retrieval_accurate = _cn_bool(_read_label(text, "召回是否准确"))
    _add_signal(signals, "retrieval_accurate", retrieval_accurate, _read_label(text, "召回是否准确") or "primary_review.retrieval.is_accurate")

    has_knowledge = _cn_bool(retrieval.get("has_knowledge"))
    if has_knowledge is None:
        has_knowledge = _cn_bool(_read_label(text, "是否有知识"))
    _add_signal(signals, "has_knowledge", has_knowledge, _read_label(text, "是否有知识") or "primary_review.retrieval.has_knowledge")

    expected_id = str(retrieval.get("expected_knowledge_id") or _read_label(text, "期望知识ID") or "").strip()
    _add_signal(signals, "expected_knowledge_id", expected_id, _read_label(text, "期望知识ID") or "primary_review.retrieval.expected_knowledge_id", SIGNAL_CONFIDENCE_HIGH)

    query_construction = str(primary_review.get("query_construction") or _read_label(text, "query构造") or "").strip()
    _add_signal(signals, "query_construction", query_construction, _read_label(text, "query构造") or "primary_review.query_construction")

    problem_identification = primary_review.get("problem_identification") or _read_label(text, "问题识别")
    _add_signal(signals, "problem_identification", problem_identification, _read_label(text, "问题识别") or "primary_review.problem_identification")

    answer_failures = primary_review.get("answer_failures")
    answer_failure_text = _read_label(text, "答案失败摘要")
    if not answer_failure_text and isinstance(answer_failures, list) and answer_failures:
        answer_failure_text = "；".join(str(item) for item in answer_failures)
    _add_signal(signals, "answer_failure", answer_failure_text, answer_failure_text)

    return JudgementEvidence(
        source_type=_infer_source_type(text),
        raw_text=text,
        mapper_status="fallback",
        signals=signals,
        unmapped_notes="使用固定标签兼容解析结果；未识别内容保留在 raw_text。",
        error=error,
    )


def _first_signal(signals: list[JudgementSignal], *keys: str) -> JudgementSignal | None:
    wanted = set(keys)
    return next((signal for signal in signals if signal.key in wanted), None)


def _signal_strings(signals: list[JudgementSignal], *keys: str) -> list[str]:
    values: list[str] = []
    for signal in signals:
        if signal.key in keys:
            values.extend(_string_values(signal.value))
    return values


def _append_unique(current: list[str], values: list[str]) -> list[str]:
    merged = list(current)
    seen = set(merged)
    for value in values:
        if value and value not in seen:
            merged.append(value)
            seen.add(value)
    return merged


def _project_judgement(request: AttributionRequest, evidence: JudgementEvidence) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    enriched.judgement_evidence = evidence
    signals = evidence.signals

    enriched.case_input.expected_knowledge_ids = _append_unique(
        enriched.case_input.expected_knowledge_ids,
        _signal_strings(signals, "expected_knowledge_id", "expected_knowledge_ids"),
    )
    enriched.case_input.retrieve_query_list = _append_unique(
        enriched.case_input.retrieve_query_list,
        _signal_strings(signals, "retrieve_query", "retrieve_query_list"),
    )

    question_scene = _first_signal(signals, "question_scene")
    if question_scene and not enriched.case_input.question_scene:
        values = _string_values(question_scene.value)
        enriched.case_input.question_scene = values[0] if values else None

    is_knowledge_qa = _first_signal(signals, "is_knowledge_qa")
    if is_knowledge_qa and enriched.case_input.is_knowledge_qa is None:
        enriched.case_input.is_knowledge_qa = _cn_bool(is_knowledge_qa.value)

    answer_failure = _first_signal(signals, "answer_failure")
    if answer_failure:
        values = _string_values(answer_failure.value)
        if values and not enriched.case_input.expected_answer:
            enriched.case_input.expected_answer = values[0]
        enriched.case_input.error_points = _append_unique(enriched.case_input.error_points, values)

    has_knowledge = _first_signal(signals, "has_knowledge")
    if has_knowledge and enriched.retrieval.knowledge_exists is None:
        enriched.retrieval.knowledge_exists = _cn_bool(has_knowledge.value)

    query_construction = _first_signal(signals, "query_construction")
    if query_construction:
        values = _string_values(query_construction.value)
        if values:
            note = f"judgement query_construction：{values[0]}"
            enriched.preprocess.notes = "；".join(part for part in [enriched.preprocess.notes, note] if part)
            if values[0] != "正确":
                enriched.preprocess.rewrite_drift = True

    return enriched


async def map_judgement(request: AttributionRequest) -> AttributionRequest:
    raw_text = request.case_input.judgement.strip()
    if request.judgement_evidence.signals:
        evidence = request.judgement_evidence.model_copy(update={"raw_text": request.judgement_evidence.raw_text or raw_text})
        return _project_judgement(request, evidence)
    if not raw_text:
        return _project_judgement(
            request,
            JudgementEvidence(source_type="empty", raw_text="", mapper_status="empty", signals=[]),
        )

    return _project_judgement(request, _fallback_from_fixed_labels(raw_text))
