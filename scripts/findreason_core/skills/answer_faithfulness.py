from ..diagnostics import diagnostic_step_from_results, run_diagnostic_spec, stage_verdicts_from_results
from ..models import AttributionRequest, DiagnosticResult, ReferenceChainStep, StageVerdict


def run_answer_faithfulness_skill(request: AttributionRequest) -> tuple[list[StageVerdict], list[DiagnosticResult], ReferenceChainStep]:
    results = run_diagnostic_spec("answer_faithfulness", request)
    verdicts = stage_verdicts_from_results(results)
    step = diagnostic_step_from_results(
        name="答案忠实性诊断 Skill",
        request=request,
        results=results,
        skill_input={
            "answer": request.qa.answer,
            "answer_claims": request.qa.answer_claims,
            "claim_alignments": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in request.qa.claim_alignments
            ],
            "missing_expected_points": request.qa.missing_expected_points,
            "alignment_status": request.qa.alignment_status,
            "alignment_error": request.qa.alignment_error,
            "qa": request.qa.model_dump(mode="json"),
            "prompt_docs": [doc.model_dump(mode="json") for doc in request.rerank.prompt_docs],
            "reference_evidence": request.reference.model_dump(mode="json"),
            "manual_error_points": request.case_input.error_points,
        },
    )
    return verdicts, results, step
