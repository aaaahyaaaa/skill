from ..diagnostics import diagnostic_step_from_results, run_diagnostic_spec, stage_verdicts_from_results
from ..models import AttributionRequest, DiagnosticResult, ReferenceChainStep, StageVerdict


def run_query_preprocess_skill(request: AttributionRequest) -> tuple[list[StageVerdict], list[DiagnosticResult], ReferenceChainStep]:
    results = run_diagnostic_spec("preprocess", request)
    verdicts = stage_verdicts_from_results(results)
    step = diagnostic_step_from_results(
        name="Query / 预处理诊断 Skill",
        request=request,
        results=results,
        skill_input={
            "query": request.case_input.query,
            "preprocess": request.preprocess.model_dump(mode="json"),
            "reference_evidence": request.reference.model_dump(mode="json"),
        },
    )
    return verdicts, results, step
