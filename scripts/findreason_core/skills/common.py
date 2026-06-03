from typing import Any, Iterable, List

from ..models import AttributionRequest, EvidenceRecord, ReferenceChainStep, StageVerdict, VerdictStatus


def verdict_status(verdicts: Iterable[StageVerdict]) -> str:
    items = list(verdicts)
    if any(item.status == VerdictStatus.FAIL for item in items):
        return "fail"
    if items and all(item.status == VerdictStatus.PASS for item in items):
        return "pass"
    return "uncertain"


def verdict_summary(verdicts: Iterable[StageVerdict]) -> str:
    parts: List[str] = []
    for verdict in verdicts:
        if verdict.status == VerdictStatus.FAIL:
            parts.append(f"{verdict.stage.value}: {verdict.candidate_cause}")
        elif verdict.status == VerdictStatus.UNCERTAIN:
            parts.append(f"{verdict.stage.value}: {verdict.suggested_action}")
    return "；".join(parts) or "未发现明确失败点。"


def flatten_evidence(verdicts: Iterable[StageVerdict]) -> list[EvidenceRecord]:
    return [item for verdict in verdicts for item in verdict.evidence]


def step_from_verdicts(
    name: str,
    request: AttributionRequest,
    verdicts: list[StageVerdict],
    skill_input: Any,
) -> ReferenceChainStep:
    status = verdict_status(verdicts)
    summary = verdict_summary(verdicts)
    action = next((verdict.suggested_action for verdict in verdicts if verdict.status == VerdictStatus.FAIL), "")
    if not action:
        action = next((verdict.suggested_action for verdict in verdicts if verdict.status == VerdictStatus.UNCERTAIN), "继续下游归因。")
    return ReferenceChainStep(
        name=name,
        status=status,
        summary=summary,
        evidence=flatten_evidence(verdicts),
        suggested_next_action=action,
        skill_input=skill_input,
        skill_output={
            "status": status,
            "summary": summary,
            "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
            "case_id": request.case_input.case_id,
        },
    )
