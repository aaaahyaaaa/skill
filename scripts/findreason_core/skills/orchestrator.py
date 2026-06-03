from __future__ import annotations

from ..models import (
    AttributionRequest,
    AttributionResponse,
    CausalPathStep,
    ConfidenceBreakdown,
    EvidenceRecord,
    ImmediateFailure,
    ReferenceChainStep,
    Stage,
    StageVerdict,
    VerdictStatus,
)
from ..diagnostics import diagnostic_results_from_verdicts, hydrate_response_views


UPSTREAM_STAGE_ORDER = [Stage.PREPROCESS, Stage.KNOWLEDGE, Stage.RETRIEVAL, Stage.RERANK, Stage.CONTEXT, Stage.ANSWER, Stage.EVALUATION]
DOWNSTREAM_ORDER = list(reversed(UPSTREAM_STAGE_ORDER))


def build_rule_candidate(
    verdicts: list[StageVerdict],
    reference_chain: list[ReferenceChainStep],
    request: AttributionRequest | None = None,
    diagnostic_results=None,
) -> AttributionResponse:
    failures = [verdict for verdict in verdicts if verdict.status == VerdictStatus.FAIL]
    if not failures:
        uncertain_stages = [verdict.stage.value for verdict in verdicts if verdict.status == VerdictStatus.UNCERTAIN]
        fallback = _fallback_primary_candidate(verdicts, request)
        result = AttributionResponse(
            case_id=request.case_input.case_id if request else None,
            field_map=request.field_map if request else {},
            immediate_failure=ImmediateFailure(
                stage=fallback.stage,
                cause=fallback.candidate_cause,
                explanation=fallback.suggested_action,
                evidence_refs=_evidence_refs(fallback),
            ),
            primary_cause=fallback.candidate_cause,
            primary_stage=fallback.stage,
            secondary_causes=[],
            causal_path=_causal_path(fallback, fallback),
            confidence=fallback.confidence,
            confidence_breakdown=_confidence_breakdown(fallback, [fallback], request),
            owner=fallback.owner,
            suggested_action=f"{fallback.suggested_action} 原始 uncertain 阶段：{', '.join(uncertain_stages) or 'none'}。",
            evidence=fallback.evidence,
            stage_verdicts=verdicts,
            reference_chain=reference_chain,
            need_reference_refresh=_need_reference_refresh(request),
            reference_refresh_reason=_reference_refresh_reason(request),
            need_human_review=True,
        )
    else:
        immediate = _immediate_failure(failures)
        primary = _primary_cause(failures, immediate)
        secondary = [verdict.candidate_cause for verdict in failures if verdict is not primary]
        result = AttributionResponse(
            case_id=request.case_input.case_id if request else None,
            field_map=request.field_map if request else {},
            immediate_failure=ImmediateFailure(
                stage=immediate.stage,
                cause=immediate.candidate_cause,
                explanation=immediate.suggested_action,
                evidence_refs=_evidence_refs(immediate),
            ),
            primary_cause=primary.candidate_cause,
            primary_stage=primary.stage,
            secondary_causes=secondary,
            causal_path=_causal_path(primary, immediate),
            confidence=primary.confidence,
            confidence_breakdown=_confidence_breakdown(primary, failures, request),
            owner=primary.owner,
            suggested_action=primary.suggested_action,
            evidence=primary.evidence,
            stage_verdicts=verdicts,
            reference_chain=reference_chain,
            need_reference_refresh=_need_reference_refresh(request),
            reference_refresh_reason=_reference_refresh_reason(request),
            need_human_review=primary.confidence < 0.75 or _low_field_mapping_confidence(request),
        )

    result.reference_chain.append(
        ReferenceChainStep(
            name="Agent Orchestrator",
            status="pass" if result.confidence >= 0.6 else "fallback",
            summary=f"按 preprocess -> knowledge -> retrieval -> rerank -> context -> answer -> evaluation 合并 Skill 输出，症状为 {result.immediate_failure.cause}，主因为 {result.primary_cause}。",
            evidence=[
                EvidenceRecord(
                    stage=result.primary_stage or Stage.KNOWLEDGE,
                    field="orchestrator.primary_cause",
                    reason="Orchestrator 先定位 immediate_failure，再判断是否有上游候选能解释该失败点。",
                    value={
                        "immediate_failure": result.immediate_failure.model_dump(mode="json"),
                        "primary_cause": result.primary_cause,
                        "primary_stage": result.primary_stage.value if result.primary_stage else None,
                        "secondary_causes": result.secondary_causes,
                        "causal_path": [step.model_dump(mode="json") for step in result.causal_path],
                    },
                )
            ],
            suggested_next_action=result.suggested_action,
            skill_input={
                "stage_verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
                "reverse_order": [stage.value for stage in DOWNSTREAM_ORDER],
            },
            skill_output=result.model_dump(mode="json", exclude={"reference_chain"}),
        )
    )
    result.diagnostic_results = diagnostic_results or diagnostic_results_from_verdicts(verdicts)
    if request:
        result.contrastive_probe_summary = request.contrastive_probe.summary
        result.retrieval_gap_summary = request.contrastive_probe.retrieval_gap_summary
        result.knowledge_verdict = _knowledge_verdict_from_request(request)
        result.knowledge_gap_confidence = _knowledge_gap_confidence(result.primary_cause, request)
        result.counterfactual_lift = request.contrastive_probe.counterfactual_lift
    hydrate_response_views(result)
    return result


def _fallback_primary_candidate(verdicts: list[StageVerdict], request: AttributionRequest | None) -> StageVerdict:
    if request:
        probe = request.contrastive_probe
        if probe.retrieval_gap_detected:
            return StageVerdict(
                stage=Stage.RETRIEVAL,
                status=VerdictStatus.FAIL,
                candidate_cause="retrieval_miss",
                confidence=0.66,
                owner="retrieval_strategy_owner",
                suggested_action="真实检索缺口 probe 命中了线上未命中的知识，优先排查 query 构造、召回通道、索引新鲜度和过滤条件。",
                evidence=[
                    EvidenceRecord(
                        stage=Stage.RETRIEVAL,
                        field="contrastive_probe.retrieval_gap",
                        reason="真实检索缺口 probe 找到了线上未命中的知识命中。",
                        value=probe.model_dump(mode="json"),
                    )
                ],
            )
        if _looks_like_weak_knowledge_missing(request):
            return StageVerdict(
                stage=Stage.KNOWLEDGE,
                status=VerdictStatus.FAIL,
                candidate_cause="knowledge_missing",
                confidence=0.58,
                owner="knowledge_operation_owner",
                suggested_action="同库高 topK、知识详情和人工参考均未命中支撑知识，优先复核知识是否缺失或知识命名/索引方式不一致。",
                evidence=[
                    EvidenceRecord(
                        stage=Stage.KNOWLEDGE,
                        field="knowledge_gap_confidence",
                        reason="当前只有知识缺失的弱推定证据。",
                        value={
                            "certainty": "suspected",
                            "wide_recall_docs": len(request.retrieval.wide_recall_docs),
                            "reference_docs": len(request.reference.support_docs),
                            "knowledge_detail_status": request.knowledge_detail.status,
                            "expected_knowledge_ids": request.case_input.expected_knowledge_ids,
                        },
                    )
                ],
            )
        if request.case_input.judgement and request.qa.answer.strip():
            return StageVerdict(
                stage=Stage.ANSWER,
                status=VerdictStatus.FAIL,
                candidate_cause="unsupported_claim",
                confidence=0.52,
                owner="manual_review",
                suggested_action="judgement 指向答案问题但未被证据充分证明；应先复核 judgement，再做 claim-support 对齐。",
                evidence=[
                    EvidenceRecord(
                        stage=Stage.ANSWER,
                        field="judgement_validation",
                        reason="judgement 只能作为线索；当前未找到强失败证据，降级为答案待复核主因候选。",
                        value={
                            "unsupported_type": "needs_review",
                            "judgement": request.case_input.judgement,
                        },
                    )
                ],
            )
    uncertain = next((verdict for verdict in verdicts if verdict.status == VerdictStatus.UNCERTAIN), None)
    stage = uncertain.stage if uncertain else Stage.ANSWER
    if stage == Stage.EVALUATION:
        stage = Stage.ANSWER
    candidate_cause = _fallback_cause_for_stage(stage)
    return StageVerdict(
        stage=stage,
        status=VerdictStatus.FAIL,
        candidate_cause=candidate_cause,
        confidence=0.5,
        owner="manual_review",
        suggested_action="没有阶段达到强失败证据门槛；输出低置信主因候选并要求人工复核，而不是顶层 uncertain。",
        evidence=[
            EvidenceRecord(
                stage=stage,
                field="orchestrator.fallback_primary_candidate",
                reason="顶层禁止 uncertain；保留阶段级 uncertain 作为证据缺口，并给出人工复核候选。",
                value={"uncertain_stage": stage.value, "fallback_cause": candidate_cause, "uncertain_action": uncertain.suggested_action if uncertain else ""},
            )
        ],
    )


def _immediate_failure(failures: list[StageVerdict]) -> StageVerdict:
    return sorted(failures, key=lambda verdict: DOWNSTREAM_ORDER.index(verdict.stage) if verdict.stage in DOWNSTREAM_ORDER else 999)[0]


def _primary_cause(failures: list[StageVerdict], immediate: StageVerdict) -> StageVerdict:
    return sorted(failures, key=lambda verdict: UPSTREAM_STAGE_ORDER.index(verdict.stage) if verdict.stage in UPSTREAM_STAGE_ORDER else 999)[0]


def _fallback_cause_for_stage(stage: Stage) -> str:
    return {
        Stage.PREPROCESS: "query_rewrite_drift",
        Stage.KNOWLEDGE: "knowledge_missing",
        Stage.RETRIEVAL: "retrieval_miss",
        Stage.RERANK: "rerank_drop",
        Stage.CONTEXT: "context_assembly_error",
        Stage.ANSWER: "unsupported_claim",
        Stage.EVALUATION: "unsupported_claim",
    }.get(stage, "unsupported_claim")


def _causal_path(primary: StageVerdict, immediate: StageVerdict) -> list[CausalPathStep]:
    steps: list[StageVerdict]
    if primary is immediate:
        steps = [primary]
    else:
        steps = sorted([primary, immediate], key=lambda verdict: UPSTREAM_STAGE_ORDER.index(verdict.stage) if verdict.stage in UPSTREAM_STAGE_ORDER else 999)
    return [
        CausalPathStep(
            order=index + 1,
            stage=verdict.stage,
            event=verdict.candidate_cause,
            evidence_refs=_evidence_refs(verdict),
        )
        for index, verdict in enumerate(steps)
    ]


def _evidence_refs(verdict: StageVerdict) -> list[str]:
    refs: list[str] = []
    for evidence in verdict.evidence:
        if evidence.source_path:
            refs.append(evidence.source_path)
        else:
            refs.append(f"evidence.{evidence.stage.value}.{evidence.field}")
    return refs


def _confidence_breakdown(
    primary: StageVerdict | None,
    failures: list[StageVerdict],
    request: AttributionRequest | None,
) -> ConfidenceBreakdown:
    field_confidences = [entry.confidence for entry in (request.field_map.values() if request else [])]
    field_mapping_confidence = sum(field_confidences) / len(field_confidences) if field_confidences else 0.0
    evidence_quality = 1.0 if primary and primary.evidence else 0.0
    reference_confidence = request.reference.confidence if request and request.reference.confidence is not None else 0.0
    if reference_confidence == 0.0 and request and (request.reference.support_docs or request.case_input.expected_knowledge_ids):
        reference_confidence = 0.8
    return ConfidenceBreakdown(
        evidence_quality=evidence_quality,
        metric_strength=primary.confidence if primary else 0.0,
        reference_confidence=reference_confidence,
        counterfactual_lift=0.0,
        cross_skill_consistency=0.8 if len(failures) > 1 else 0.5 if failures else 0.0,
        field_mapping_confidence=field_mapping_confidence,
        penalty=0.0 if field_mapping_confidence >= 0.8 else 0.2,
    )


def _low_field_mapping_confidence(request: AttributionRequest | None) -> bool:
    if not request or not request.field_map:
        return False
    required = [request.field_map.get(field) for field in ("query", "workspace_id", "app_id")]
    return any(entry is not None and entry.confidence < 0.8 for entry in required)


def _need_reference_refresh(request: AttributionRequest | None) -> bool:
    return bool(request and request.reference.confidence is not None and request.reference.confidence < 0.7)


def _reference_refresh_reason(request: AttributionRequest | None) -> str | None:
    if _need_reference_refresh(request):
        return "reference_confidence below threshold"
    return None


def _looks_like_weak_knowledge_missing(request: AttributionRequest) -> bool:
    return (
        request.retrieval.knowledge_exists is None
        and not request.reference.support_docs
        and not request.retrieval.wide_recall_docs
        and request.knowledge_detail.status not in {"error", "ok", "partial"}
        and not request.contrastive_probe.retrieval_gap_detected
    )


def _knowledge_verdict_from_request(request: AttributionRequest) -> str:
    if request.retrieval.knowledge_exists is False:
        return "confirmed_missing"
    if _looks_like_weak_knowledge_missing(request):
        return "suspected_missing"
    knowledge_detail_support = bool(
        request.knowledge_detail.extracted_evidence.get("expected_knowledge_docs")
        or request.knowledge_detail.extracted_evidence.get("matched_expected_ids")
    )
    if request.reference.support_docs or request.retrieval.wide_recall_docs or knowledge_detail_support:
        return "knowledge_exists"
    return "unknown"


def _knowledge_gap_confidence(primary_cause: str, request: AttributionRequest) -> float:
    if primary_cause == "knowledge_missing":
        if request.retrieval.knowledge_exists is False:
            return 0.88
        return 0.66 if request.case_input.expected_knowledge_ids else 0.58
    if request.contrastive_probe.retrieval_gap_detected:
        return max(0.0, min(1.0, 0.5 + request.contrastive_probe.counterfactual_lift))
    return 0.0
