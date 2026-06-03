from ..diagnostics import diagnostic_step_from_results, run_diagnostic_spec, stage_verdicts_from_results
from ..models import AttributionRequest, DiagnosticResult, ReferenceChainStep, StageVerdict


def run_retrieval_skill(request: AttributionRequest) -> tuple[list[StageVerdict], list[DiagnosticResult], ReferenceChainStep]:
    results = run_diagnostic_spec("knowledge_retrieval", request)
    verdicts = stage_verdicts_from_results(results)
    step = diagnostic_step_from_results(
        name="知识与召回诊断 Skill",
        request=request,
        results=results,
        skill_input={
            "expected_knowledge_ids": request.case_input.expected_knowledge_ids,
            "retrieval": request.retrieval.model_dump(mode="json"),
            "reference_evidence": request.reference.model_dump(mode="json"),
            "workflow_replay_extracted_evidence": request.workflow_replay.extracted_evidence,
            "wide_recall": request.wide_recall.model_dump(mode="json"),
            "knowledge_detail": request.knowledge_detail.model_dump(mode="json"),
        },
    )
    return verdicts, results, step
