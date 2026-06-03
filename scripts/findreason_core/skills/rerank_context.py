from ..diagnostics import diagnostic_step_from_results, run_diagnostic_spec, stage_verdicts_from_results
from ..models import AttributionRequest, DiagnosticResult, ReferenceChainStep, StageVerdict


def run_rerank_context_skill(request: AttributionRequest) -> tuple[list[StageVerdict], list[DiagnosticResult], ReferenceChainStep]:
    results = run_diagnostic_spec("rerank_context", request)
    verdicts = stage_verdicts_from_results(results)
    step = diagnostic_step_from_results(
        name="重排与上下文诊断 Skill",
        request=request,
        results=results,
        skill_input={
            "retrieval": request.retrieval.model_dump(mode="json"),
            "rerank": request.rerank.model_dump(mode="json"),
            "expected_knowledge_ids": request.case_input.expected_knowledge_ids,
        },
    )
    return verdicts, results, step
