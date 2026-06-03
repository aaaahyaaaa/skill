from typing import Callable, List

from .diagnostics import run_all_diagnostics, run_diagnostic_spec, stage_verdicts_from_results
from .models import AttributionRequest, AttributionResponse, Stage, StageVerdict


def _first_stage_result(spec_id: str, stage: Stage, request: AttributionRequest) -> StageVerdict:
    results = run_diagnostic_spec(spec_id, request)
    for result in results:
        if result.stage == stage:
            return result.to_stage_verdict()
    raise RuntimeError(f"diagnostic spec {spec_id} did not emit {stage.value}")


def evaluate_answer(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("answer_faithfulness", Stage.ANSWER, request)


def evaluate_evaluator_rubric(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("evaluator_rubric", Stage.EVALUATION, request)


def evaluate_context(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("rerank_context", Stage.CONTEXT, request)


def evaluate_rerank(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("rerank_context", Stage.RERANK, request)


def evaluate_retrieval(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("knowledge_retrieval", Stage.RETRIEVAL, request)


def evaluate_preprocess(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("preprocess", Stage.PREPROCESS, request)


def evaluate_knowledge(request: AttributionRequest) -> StageVerdict:
    return _first_stage_result("knowledge_retrieval", Stage.KNOWLEDGE, request)


EVALUATORS: List[Callable[[AttributionRequest], StageVerdict]] = [
    evaluate_answer,
    evaluate_context,
    evaluate_rerank,
    evaluate_retrieval,
    evaluate_preprocess,
    evaluate_knowledge,
    evaluate_evaluator_rubric,
]


def run_attribution(request: AttributionRequest) -> AttributionResponse:
    diagnostic_results = run_all_diagnostics(request)
    verdicts = stage_verdicts_from_results(diagnostic_results)
    from .skills.orchestrator import build_rule_candidate

    return build_rule_candidate(
        verdicts=verdicts,
        reference_chain=[],
        request=request,
        diagnostic_results=diagnostic_results,
    )
