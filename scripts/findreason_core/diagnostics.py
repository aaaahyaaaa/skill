from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable

from .models import (
    AttributionRequest,
    AttributionResponse,
    DiagnosticResult,
    EvidenceRecord,
    EvidenceRequirementReport,
    ReferenceChainStep,
    SkillProbe,
    Stage,
    StageVerdict,
    VerdictStatus,
)


@dataclass(frozen=True)
class EvidenceRequirement:
    field: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class DiagnosticRule:
    rule_id: str
    cause: str
    owner: str
    evidence_requirements: tuple[EvidenceRequirement, ...]
    positive_example: str
    negative_example: str


@dataclass(frozen=True)
class DiagnosticSpec:
    spec_id: str
    domain: str
    stage: Stage
    rules: tuple[DiagnosticRule, ...]


def _req(field: str, description: str) -> EvidenceRequirement:
    return EvidenceRequirement(field=field, description=description)


DIAGNOSTIC_SPECS: tuple[DiagnosticSpec, ...] = (
    DiagnosticSpec(
        spec_id="preprocess",
        domain="query_preprocess",
        stage=Stage.PREPROCESS,
        rules=(
            DiagnosticRule(
                "preprocess.non_rag_route_boundary",
                "non_rag_route_boundary",
                "agent_router_owner",
                (_req("case_input.is_knowledge_qa", "输入样本明确标记为非知识问答。"),),
                "is_knowledge_qa=false 且 question_scene=广告审核。",
                "缺少 is_knowledge_qa 时不能推断为非 RAG。",
            ),
            DiagnosticRule(
                "preprocess.query_rewrite_drift",
                "query_rewrite_drift",
                "rag_preprocess_or_workflow_owner",
                (_req("preprocess.rewrite_query", "真实 rewrite_query 与原 query 的差异。"),),
                "rewrite_query 将核心实体改成另一个业务概念。",
                "没有 rewrite_query trace 时只能返回 uncertain。",
            ),
            DiagnosticRule(
                "preprocess.keyword_loss",
                "keyword_loss",
                "rag_preprocess_or_workflow_owner",
                (_req("preprocess.keywords", "关键词抽取结果缺少核心业务实体。"),),
                "query 包含云图，但 keywords 不含云图或同义实体。",
                "只有人工口头判断关键词少了，不足以失败。",
            ),
        ),
    ),
    DiagnosticSpec(
        spec_id="retrieval",
        domain="knowledge_retrieval",
        stage=Stage.RETRIEVAL,
        rules=(
            DiagnosticRule(
                "retrieval.knowledge_missing",
                "knowledge_missing",
                "knowledge_operation_owner",
                (_req("retrieval.knowledge_exists", "目标知识库不存在支撑知识，或多路证据均未发现支撑知识。"),),
                "人工锚点和知识详情确认没有相关知识，或多路检索没有任何支撑证据。",
                "knowledge_detail HTTP 失败不能作为知识不存在证据。",
            ),
            DiagnosticRule(
                "retrieval.knowledge_topic_mismatch",
                "knowledge_topic_mismatch",
                "knowledge_operation_owner",
                (_req("retrieval.topic_mismatch", "线上 topK 非空，但语义支撑判定显示候选只是相邻主题。"),),
                "topK 召回非空，但所有候选都不能支撑 query/expected points，且没有 contrastive retrieval gap。",
                "topK 为空时不能判主题错配；contrastive probe 命中正确知识时应判 retrieval_miss。",
            ),
            DiagnosticRule(
                "retrieval.retrieval_miss",
                "retrieval_miss",
                "retrieval_strategy_owner",
                (_req("retrieval.online_retrieval_hit", "线上召回是否命中期望知识。"),),
                "KnowledgeDetailTool 找到期望知识，但 origin_doc_list 未命中。",
                "只有 judgement 写召回不准，不能伪造成线上未命中。",
            ),
            DiagnosticRule(
                "retrieval.permission_miss",
                "permission_miss",
                "knowledge_permission_owner",
                (_req("retrieval.permission_miss", "正确知识存在但线上不可见的权限标记。"),),
                "同知识库宽召回命中，线上因 workspace 权限过滤不可见。",
                "没有 workspace/app/user 权限证据时不能判权限过滤。",
            ),
        ),
    ),
    DiagnosticSpec(
        spec_id="rerank_context",
        domain="rerank_context",
        stage=Stage.RERANK,
        rules=(
            DiagnosticRule(
                "rerank_context.rerank_drop",
                "rerank_drop",
                "rerank_strategy_owner",
                (_req("rerank.expected_doc_survived_rerank", "期望文档从 recall 到 rerank 的存活状态。"),),
                "online_retrieval_hit=true 但 expected_doc_survived_rerank=false。",
                "召回是否命中未知时不能直接判 rerank 丢弃。",
            ),
            DiagnosticRule(
                "rerank_context.rerank_tunable",
                "rerank_tunable",
                "rerank_strategy_owner",
                (_req("rerank.parameter_experiment", "重排参数扰动实验中 target doc 的存活、排名或分数 lift，或人工阈值可调证据。"),),
                "baseline 未保留目标文档，但参数 variant 让目标文档恢复或显著提升。",
                "没有 counterfactual lift 时不能把普通 rerank_drop 说成参数问题。",
            ),
            DiagnosticRule(
                "rerank_context.context_assembly_error",
                "context_assembly_error",
                "workflow_or_prompt_context_owner",
                (_req("rerank.expected_doc_in_prompt", "期望文档是否进入最终 prompt。"),),
                "expected_doc_survived_rerank=true 但 expected_doc_in_prompt=false。",
                "没有 prompt_docs 时只能要求补证据。",
            ),
        ),
    ),
    DiagnosticSpec(
        spec_id="answer",
        domain="answer_faithfulness",
        stage=Stage.ANSWER,
        rules=(
            DiagnosticRule(
                "answer.unsupported_claim",
                "unsupported_claim",
                "prompt_or_model_owner",
                (_req("qa.unsupported_claims", "最终答案中未被 prompt 或 reference 支撑的 claim。"),),
                "答案声称云图是天气图，但 reference 只支持指标分析产品。",
                "未做 claim 对齐时不能判 unsupported_claim。",
            ),
            DiagnosticRule(
                "answer.wrong_citation",
                "wrong_citation",
                "prompt_or_model_owner",
                (_req("qa.wrong_citation", "答案引用或证据映射错误。"),),
                "答案内容正确但引用了不支持该 claim 的文档。",
                "没有引用字段或映射关系时不能判 wrong_citation。",
            ),
            DiagnosticRule(
                "answer.partial_answer",
                "partial_answer",
                "prompt_or_model_owner",
                (_req("qa.partial_answer", "答案遗漏必要内容的标记。"),),
                "prompt 已包含完整支撑，但答案只回答了一半。",
                "支撑证据本身不完整时不要归因答案遗漏。",
            ),
        ),
    ),
    DiagnosticSpec(
        spec_id="evaluation",
        domain="evaluator_rubric",
        stage=Stage.EVALUATION,
        rules=(),
    ),
)


def cause_to_spec() -> dict[str, DiagnosticSpec]:
    return {rule.cause: spec for spec in DIAGNOSTIC_SPECS for rule in spec.rules}


def _rule_for_cause(cause: str) -> tuple[DiagnosticSpec | None, DiagnosticRule | None]:
    for spec in DIAGNOSTIC_SPECS:
        for rule in spec.rules:
            if rule.cause == cause:
                return spec, rule
    return None, None


def _fallback_spec(stage: Stage) -> DiagnosticSpec:
    return next((spec for spec in DIAGNOSTIC_SPECS if spec.stage == stage), DIAGNOSTIC_SPECS[0])


def diagnostic_result_from_verdict(verdict: StageVerdict) -> DiagnosticResult:
    spec, rule = _rule_for_cause(verdict.candidate_cause)
    if spec is None:
        spec = _fallback_spec(verdict.stage)
    requirements = rule.evidence_requirements if rule else ()
    return DiagnosticResult(
        spec_id=spec.spec_id,
        domain=spec.domain,
        stage=verdict.stage,
        status=verdict.status,
        candidate_cause=verdict.candidate_cause,
        confidence=verdict.confidence,
        owner=verdict.owner,
        suggested_action=verdict.suggested_action,
        matched_rule_id=rule.rule_id if rule and verdict.status == VerdictStatus.FAIL else None,
        evidence_requirements=[
            EvidenceRequirementReport(field=item.field, description=item.description, required=item.required)
            for item in requirements
        ],
        evidence=verdict.evidence,
        metrics={"evidence_count": len(verdict.evidence), "has_required_evidence": bool(verdict.evidence)},
    )


def diagnostic_results_from_verdicts(verdicts: Iterable[StageVerdict]) -> list[DiagnosticResult]:
    return [diagnostic_result_from_verdict(verdict) for verdict in verdicts]


def arbitration_view(result: AttributionResponse) -> dict[str, object]:
    return {
        "primary_cause": result.primary_cause,
        "primary_stage": result.primary_stage.value if result.primary_stage else None,
        "immediate_failure": result.immediate_failure.model_dump(mode="json"),
        "secondary_causes": result.secondary_causes,
        "causal_path": [step.model_dump(mode="json") for step in result.causal_path],
        "confidence": result.confidence,
        "confidence_breakdown": result.confidence_breakdown.model_dump(mode="json"),
        "owner": result.owner,
        "suggested_action": result.suggested_action,
        "need_human_review": result.need_human_review,
    }


STEP_SKILL_NAMES = {
    "输入适配": "input_adapter",
    "PipelineReplayTool": "pipeline_replay",
    "WideRecallTool": "wide_recall",
    "KnowledgeDetailTool": "knowledge_detail",
    "Reference Evidence": "reference_evidence",
    "Query / 预处理诊断 Skill": "query_preprocess",
    "知识与召回诊断 Skill": "retrieval",
    "重排与上下文诊断 Skill": "rerank_context",
    "答案忠实性诊断 Skill": "answer_faithfulness",
    "Evaluator / Rubric 诊断 Skill": "evaluator_rubric",
    "Agent Orchestrator": "orchestrator",
}


def _observed_keys(value) -> list[str]:
    if isinstance(value, dict):
        return list(value.keys()) or ["<empty_object>"]
    if isinstance(value, list):
        return ["<list>"] if value else ["<empty_list>"]
    if value is None:
        return ["<null>"]
    return [type(value).__name__]


def _skill_name_for_step(step: ReferenceChainStep) -> str:
    if step.name in STEP_SKILL_NAMES:
        return STEP_SKILL_NAMES[step.name]
    for label, skill_name in STEP_SKILL_NAMES.items():
        if label in step.name:
            return skill_name
    return step.name


def _attach_dev_skill_probe(step: ReferenceChainStep) -> ReferenceChainStep:
    skill_name = _skill_name_for_step(step)
    step.skill_probe = step.skill_probe or SkillProbe(
        marker=f"SKILL_PROBE_USED:{skill_name}:dev",
        skill_name=skill_name,
        observed_input_keys=_observed_keys(step.skill_input),
        observed_output_keys=_observed_keys(step.skill_output),
        instruction="Dev probe: this step was executed and its skill_input / skill_output were captured for the frontend execution observer.",
    )
    return step


def attach_dev_skill_probes(steps: list[ReferenceChainStep]) -> list[ReferenceChainStep]:
    return [_attach_dev_skill_probe(step) for step in steps]


def hydrate_response_views(result: AttributionResponse) -> AttributionResponse:
    evidence_step_names = {"输入适配", "PipelineReplayTool", "WideRecallTool", "KnowledgeDetailTool", "Reference Evidence"}
    result.reference_chain = attach_dev_skill_probes(result.reference_chain)
    result.evidence_chain = result.evidence_chain or [step for step in result.reference_chain if step.name in evidence_step_names]
    result.evidence_chain = attach_dev_skill_probes(result.evidence_chain)
    result.diagnostic_results = result.diagnostic_results or diagnostic_results_from_verdicts(result.stage_verdicts)
    result.arbitration = arbitration_view(result)
    return result


SPEC_ID_ALIASES = {
    "knowledge_retrieval": "retrieval",
    "answer_faithfulness": "answer",
    "evaluator_rubric": "evaluation",
}


ACTION_BY_CAUSE = {
    "non_rag_route_boundary": "先将该 case 分流到非 RAG 或工具规划路径，再决定是否需要 RAG 专项归因。",
    "query_rewrite_drift": "优化 rewrite prompt，或对这类 query 关闭/绕过 rewrite。",
    "keyword_loss": "调优关键词抽取，并强制保留业务实体。",
    "knowledge_missing": "补录或改写缺失知识，并将该 badcase 作为回归样本复测。",
    "knowledge_topic_mismatch": "补录正主知识或调整知识标题/索引描述，避免相邻主题知识覆盖真实问题。",
    "retrieval_miss": "检查 query 构造、embedding/索引新鲜度、topK、filter 和召回通道覆盖。",
    "permission_miss": "审计 workspace、app、用户角色、知识状态和检索权限过滤条件。",
    "rerank_drop": "检查 rerank 模型、分数阈值、topK、去重和多路召回优先级规则。",
    "rerank_tunable": "复核参数扰动实验中 lift 最大的 rerank 参数，并用小流量或离线集回归验证。",
    "context_assembly_error": "检查 prompt_docs 构造、topK 交接、token budget 和上下文拼接顺序。",
    "unsupported_claim": "基于同一份 prompt_docs 检查答案 prompt、模型选择、引用策略和兜底/拒答指令。",
    "wrong_citation": "修正引用映射和答案引用策略。",
    "partial_answer": "补齐答案覆盖和拒答/总结策略。",
    "grader_or_rubric_issue": "复核 grader prompt、rubric 维度和人工标签，确认是否是评估口径错配。",
    "label_conflict": "对齐人工标签与评估器标签。",
    "rubric_scope_mismatch": "调整 rubric 维度或样本归属。",
    "evaluator_missing_evidence": "补齐评估器证据引用并对齐人工标签、rubric 维度和最终答案证据。",
}


def _spec_for_id(spec_id: str) -> DiagnosticSpec:
    normalized = SPEC_ID_ALIASES.get(spec_id, spec_id)
    for spec in DIAGNOSTIC_SPECS:
        if spec.spec_id == normalized:
            return spec
    raise KeyError(f"unknown diagnostic spec: {spec_id}")


def _record(stage: Stage, field: str, reason: str, value=None) -> EvidenceRecord:
    return EvidenceRecord(stage=stage, field=field, reason=reason, value=value)


def _contains_expected(ids: list[str], docs: list[object]) -> bool | None:
    normalized = {getattr(doc, "id", None) for doc in docs if getattr(doc, "id", None)}
    if not ids or not normalized:
        return None
    return any(expected_id in normalized for expected_id in ids)


def _doc_ids(docs: list[object]) -> list[str]:
    return [str(getattr(doc, "id", "")).strip() for doc in docs if str(getattr(doc, "id", "")).strip()]


def _knowledge_detail_support(request: AttributionRequest) -> bool:
    return bool(
        request.knowledge_detail.extracted_evidence.get("expected_knowledge_docs")
        or request.knowledge_detail.extracted_evidence.get("matched_expected_ids")
    )


def _topic_mismatch_signal(request: AttributionRequest) -> tuple[bool, dict]:
    extracted = request.knowledge_detail.extracted_evidence
    detail_signal = extracted.get("topic_mismatch")
    detail_doc_ids = extracted.get("topic_mismatch_doc_ids") or []
    detail_reason = str(extracted.get("topic_mismatch_reason") or "").strip()
    enabled = bool(request.retrieval.topic_mismatch or detail_signal)
    doc_ids = request.retrieval.topic_mismatch_doc_ids or [str(item) for item in detail_doc_ids]
    reason = request.retrieval.topic_mismatch_reason or detail_reason or request.retrieval.notes
    return enabled, {"topic_mismatch_doc_ids": doc_ids, "topic_mismatch_reason": reason}


def _missing_evidence_items(request: AttributionRequest) -> list[str]:
    items = list(request.evaluation.missing_evidence_items)
    extracted = request.workflow_replay.extracted_evidence.get("missing_evidence_items")
    if isinstance(extracted, list):
        for item in extracted:
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
    return items


def _diagnostic_result(
    spec: DiagnosticSpec,
    stage: Stage,
    status: VerdictStatus,
    cause: str,
    confidence: float,
    owner: str,
    action: str,
    evidence: list[EvidenceRecord] | None = None,
    metrics: dict | None = None,
) -> DiagnosticResult:
    _, rule = _rule_for_cause(cause)
    requirements = rule.evidence_requirements if rule else ()
    return DiagnosticResult(
        spec_id=spec.spec_id,
        domain=spec.domain,
        stage=stage,
        status=status,
        candidate_cause=cause,
        confidence=confidence,
        owner=owner,
        suggested_action=action,
        matched_rule_id=rule.rule_id if rule and status == VerdictStatus.FAIL else None,
        evidence_requirements=[
            EvidenceRequirementReport(field=item.field, description=item.description, required=item.required)
            for item in requirements
        ],
        evidence=evidence or [],
        metrics=metrics or {},
    )


def _failed(spec: DiagnosticSpec, cause: str, confidence: float, evidence: list[EvidenceRecord]) -> DiagnosticResult:
    _, rule = _rule_for_cause(cause)
    owner = rule.owner if rule else "manual_review"
    return _diagnostic_result(
        spec=spec,
        stage=rule_stage(cause, spec.stage),
        status=VerdictStatus.FAIL,
        cause=cause,
        confidence=confidence,
        owner=owner,
        action=ACTION_BY_CAUSE.get(cause, "补充证据后复核该根因。"),
        evidence=evidence,
    )


def _passed(spec: DiagnosticSpec, stage: Stage, evidence: list[EvidenceRecord] | None = None) -> DiagnosticResult:
    return _diagnostic_result(
        spec=spec,
        stage=stage,
        status=VerdictStatus.PASS,
        cause="none",
        confidence=0.0,
        owner="",
        action="该层证据未发现明确失败点。",
        evidence=evidence,
    )


def _uncertain(
    spec: DiagnosticSpec,
    stage: Stage,
    action: str,
    evidence: list[EvidenceRecord] | None = None,
    cause: str = "uncertain",
) -> DiagnosticResult:
    return _diagnostic_result(
        spec=spec,
        stage=stage,
        status=VerdictStatus.UNCERTAIN,
        cause=cause,
        confidence=0.0,
        owner="manual_review",
        action=action,
        evidence=evidence,
    )


def rule_stage(cause: str, fallback: Stage) -> Stage:
    _, rule = _rule_for_cause(cause)
    if rule:
        spec, _ = _rule_for_cause(cause)
        if spec and spec.spec_id == "rerank_context":
            return Stage.CONTEXT if cause == "context_assembly_error" else Stage.RERANK
        if spec and spec.spec_id == "retrieval":
            return Stage.KNOWLEDGE if cause in {"knowledge_missing", "knowledge_topic_mismatch"} else Stage.RETRIEVAL
        return spec.stage
    return fallback


def execute_preprocess(request: AttributionRequest) -> list[DiagnosticResult]:
    spec = _spec_for_id("preprocess")
    if request.case_input.is_knowledge_qa is False:
        return [
            _failed(
                spec,
                "non_rag_route_boundary",
                0.83,
                [
                    _record(Stage.PREPROCESS, "is_knowledge_qa", "输入样本被标记为非知识问答。", False),
                    _record(Stage.PREPROCESS, "question_scene", "问题场景应优先由路由边界处理。", request.case_input.question_scene),
                ],
            )
        ]

    preprocess = request.preprocess
    query = request.case_input.query.strip()
    rewrite_query = preprocess.rewrite_query.strip()
    rewrite_trace_value = {
        "query": query,
        "rewrite_query": rewrite_query or None,
        "trace_state": "missing" if not rewrite_query else "same_as_query" if rewrite_query == query else "different_from_query",
        "manual_notes": preprocess.notes,
    }
    if preprocess.rewrite_drift:
        if not rewrite_query:
            return [
                _uncertain(
                    spec,
                    Stage.PREPROCESS,
                    "补充真实 rewrite_query trace 后再判断 query 构造或改写漂移。",
                    [_record(Stage.PREPROCESS, "rewrite_query", "未提供 rewrite_query trace，不能判断是否与原 query 一致；人工 query 构造异常只能作为待复核线索。", rewrite_trace_value)],
                    cause="query_construction_uncertain",
                )
            ]
        if rewrite_query == query:
            return [
                _uncertain(
                    spec,
                    Stage.PREPROCESS,
                    "rewrite_drift 标记与 rewrite_query trace 冲突，请复核 query 构造标注或真实 rewrite 产物。",
                    [_record(Stage.PREPROCESS, "rewrite_query", "rewrite_drift=true 但 rewrite_query 与原 query 相同，不能据此判定改写漂移。", rewrite_trace_value)],
                    cause="query_construction_uncertain",
                )
            ]
        return [
            _failed(
                spec,
                "query_rewrite_drift",
                0.8,
                [_record(Stage.PREPROCESS, "rewrite_query", "Query rewrite trace 显示改写结果与原 query 不同，且被标记为改写漂移。", rewrite_trace_value)],
            )
        ]
    checks = [
        ("keyword_loss", preprocess.keyword_loss, "keywords", "召回前丢失了关键关键词。"),
    ]
    for cause, enabled, field, reason in checks:
        if enabled:
            return [_failed(spec, cause, 0.8, [_record(Stage.PREPROCESS, field, reason, getattr(preprocess, field, True) if hasattr(preprocess, field) else True)])]
    if rewrite_query:
        reason = "rewrite_query trace 存在且与原 query 一致，暂未发现改写漂移。" if rewrite_query == query else "rewrite_query trace 存在且与原 query 不同，当前没有 rewrite_drift 标记；需结合召回效果判断是否合理。"
        return [_passed(spec, Stage.PREPROCESS, [_record(Stage.PREPROCESS, "rewrite_query", reason, rewrite_trace_value)])]
    if preprocess.keywords or preprocess.filters or preprocess.answer_model:
        return [_passed(spec, Stage.PREPROCESS)]
    return [
        _uncertain(
            spec,
            Stage.PREPROCESS,
            "补充真实 rewrite_query、keywords、filters 和权限证据。",
            [_record(Stage.PREPROCESS, "rewrite_query", "未提供 rewrite_query trace，不能判断是否与原 query 一致。", rewrite_trace_value)],
        )
    ]


def execute_retrieval(request: AttributionRequest) -> list[DiagnosticResult]:
    spec = _spec_for_id("retrieval")
    retrieval = request.retrieval
    topk_docs = retrieval.origin_doc_list + retrieval.origin_faq_list
    recall_hit = retrieval.online_retrieval_hit
    if recall_hit is None:
        recall_hit = _contains_expected(request.case_input.expected_knowledge_ids, topk_docs)
    expected_ids = request.case_input.expected_knowledge_ids
    knowledge_detail_support = _knowledge_detail_support(request)
    support_found = bool(retrieval.wide_recall_docs or request.reference.support_docs or knowledge_detail_support)
    retrieval_gap_detected = bool(request.contrastive_probe.retrieval_gap_detected)
    reference_support = bool(request.reference.support_docs)
    wide_recall_support = bool(retrieval.wide_recall_docs)
    knowledge_detail_status = request.knowledge_detail.status
    knowledge_detail_error = knowledge_detail_status == "error"
    knowledge_exists = retrieval.knowledge_exists if retrieval.knowledge_exists is not None else (True if support_found else None)
    topic_mismatch, topic_mismatch_value = _topic_mismatch_signal(request)
    results: list[DiagnosticResult] = []
    if retrieval.permission_miss:
        results.append(_failed(spec, "permission_miss", 0.84, [_record(Stage.RETRIEVAL, "permission_miss", "正确知识存在，但线上召回不可见。", True)]))
    elif knowledge_exists is True and recall_hit is False:
        results.append(
            _failed(
                spec,
                "retrieval_miss",
                0.86,
                [
                    _record(Stage.RETRIEVAL, "knowledge_exists", "人工或参考证据表明支撑知识存在。", True),
                    _record(Stage.RETRIEVAL, "online_retrieval_hit", "线上 origin_doc_list/origin_faq_list 未命中支撑证据。", False),
                ],
            )
        )
    elif retrieval_gap_detected:
        results.append(
            _failed(
                spec,
                "retrieval_miss",
                0.84,
                [
                    _record(Stage.RETRIEVAL, "online_retrieval_hit", "线上主链路未命中 probe 发现的新知识/文档。", False),
                    _record(Stage.RETRIEVAL, "retrieval_gap_probe", "真实检索缺口 probe 命中了线上未命中的知识。", request.contrastive_probe.model_dump(mode="json")),
                ],
            )
        )
    elif retrieval.expected_knowledge_hit is False:
        results.append(_failed(spec, "retrieval_miss", 0.78, [_record(Stage.RETRIEVAL, "expected_knowledge_hit", "期望知识 ID 未出现在在线召回结果中。", False)]))
    elif recall_hit is True or retrieval.expected_knowledge_hit is True:
        results.append(_passed(spec, Stage.RETRIEVAL))
    else:
        results.append(_uncertain(spec, Stage.RETRIEVAL, "补充线上召回列表和同知识库宽召回证据。"))

    if retrieval.knowledge_exists is False:
        results.append(
            _failed(
                spec,
                "knowledge_missing",
                0.88,
                [
                    _record(
                        Stage.KNOWLEDGE,
                        "knowledge_exists",
                        "目标知识库中未找到可支撑答案的知识。",
                        {"knowledge_exists": False, "certainty": "confirmed"},
                    )
                ],
            )
        )
    elif (
        topk_docs
        and topic_mismatch
        and retrieval.knowledge_exists is None
        and not retrieval_gap_detected
        and not reference_support
        and not wide_recall_support
        and not knowledge_detail_support
    ):
        value = {
            "topk_doc_ids": _doc_ids(topk_docs),
            "reference_support": reference_support,
            "wide_recall_support": wide_recall_support,
            "knowledge_detail_support": knowledge_detail_support,
            **topic_mismatch_value,
        }
        results.append(
            _failed(
                spec,
                "knowledge_topic_mismatch",
                0.72,
                [_record(Stage.KNOWLEDGE, "topic_mismatch", "线上 topK 非空，但语义支撑判定显示候选只是相邻主题。", value)],
            )
        )
    elif (
        retrieval.knowledge_exists is None
        and not retrieval_gap_detected
        and not reference_support
        and not wide_recall_support
        and not knowledge_detail_support
        and not knowledge_detail_error
    ):
        results.append(
            _failed(
                spec,
                "knowledge_missing",
                0.66 if expected_ids else 0.58,
                [
                    _record(
                        Stage.KNOWLEDGE,
                        "knowledge_gap_confidence",
                        "多路检索与知识详情均未提供支撑知识，当前只能弱推定知识缺失。",
                        {
                            "certainty": "suspected",
                            "reference_support": reference_support,
                            "wide_recall_support": wide_recall_support,
                            "knowledge_detail_support": knowledge_detail_support,
                            "knowledge_detail_status": knowledge_detail_status,
                            "expected_knowledge_ids": expected_ids,
                        },
                    )
                ],
            )
        )
    elif request.reference.source in {"none", ""} and not request.reference.support_docs and retrieval.knowledge_exists is None:
        results.append(_uncertain(spec, Stage.KNOWLEDGE, "补充人工期望知识 ID 或同知识库参考证据。"))
    else:
        results.append(_passed(spec, Stage.KNOWLEDGE))
    return results


def execute_rerank_context(request: AttributionRequest) -> list[DiagnosticResult]:
    spec = _spec_for_id("rerank_context")
    retrieval = request.retrieval
    rerank = request.rerank
    recall_hit = retrieval.online_retrieval_hit
    if recall_hit is None:
        recall_hit = _contains_expected(request.case_input.expected_knowledge_ids, retrieval.origin_doc_list + retrieval.origin_faq_list)
    results: list[DiagnosticResult] = []
    experiment = rerank.parameter_experiment
    if experiment.parameter_issue_supported:
        best_variant = experiment.best_variant
        diff_keys = set((best_variant.parameter_diff or {}).keys()) if best_variant else set()
        tunable_param = "threshold" if any("min_score" in key for key in diff_keys) else "feature"
        results.append(
            _failed(
                spec,
                "rerank_tunable",
                0.88 if tunable_param == "threshold" else 0.84,
                [
                    _record(
                        Stage.RERANK,
                        "parameter_experiment",
                        "重排参数扰动实验显示 target doc 在参数 variant 下恢复或排名提升。",
                        {"tunable_param": tunable_param, "experiment": experiment.model_dump(mode="json")},
                    ),
                    _record(Stage.RERANK, "best_variant", "实验中 lift 最大的参数组合。", best_variant.model_dump(mode="json") if best_variant else None),
                ],
            )
        )
    elif recall_hit is True and rerank.expected_doc_survived_rerank is False:
        evidence = [
            _record(Stage.RERANK, "online_retrieval_hit", "正确证据已出现在召回结果中。", True),
            _record(Stage.RERANK, "expected_doc_survived_rerank", "正确证据在进入 prompt 候选前被重排阶段丢弃。", False),
        ]
        if experiment.status not in {"not_run", "missing_rerank_request", "missing_target_doc"}:
            evidence.append(
                _record(Stage.RERANK, "parameter_experiment", "重排参数扰动实验未证明参数可修复该 rerank drop。", experiment.model_dump(mode="json"))
            )
        results.append(_failed(spec, "rerank_drop", 0.86, evidence))
    elif rerank.threshold_too_strict:
        results.append(
            _failed(
                spec,
                "rerank_tunable",
                0.78,
                [_record(Stage.RERANK, "rerank_tunable", "人工证据标记重排阈值过严。", {"tunable_param": "threshold", "source_signal": "manual_threshold_flag"})],
            )
        )
    elif rerank.expected_doc_survived_rerank is True:
        results.append(_passed(spec, Stage.RERANK))
    else:
        results.append(_uncertain(spec, Stage.RERANK, "补充召回命中状态和重排存活状态。"))
    if rerank.expected_doc_survived_rerank is True and rerank.expected_doc_in_prompt is False:
        results.append(_failed(spec, "context_assembly_error", 0.87, [_record(Stage.CONTEXT, "expected_doc_survived_rerank", "正确证据已经通过重排阶段。", True), _record(Stage.CONTEXT, "expected_doc_in_prompt", "正确证据没有进入最终 prompt。", False)]))
    elif rerank.prompt_truncation:
        results.append(
            _failed(
                spec,
                "context_assembly_error",
                0.82,
                [_record(Stage.CONTEXT, "context_assembly_error", "证据在构造最终 prompt 时被截断或丢失。", {"truncated": True})],
            )
        )
    elif rerank.context_assembly_error or rerank.noise_overload:
        results.append(_failed(spec, "context_assembly_error", 0.76, [_record(Stage.CONTEXT, "context_assembly_error", "上下文组装或噪声覆盖问题被标记。", {"context_assembly_error": rerank.context_assembly_error, "noise_overload": rerank.noise_overload})]))
    elif rerank.expected_doc_in_prompt is True:
        results.append(_passed(spec, Stage.CONTEXT))
    else:
        results.append(_uncertain(spec, Stage.CONTEXT, "补充 prompt_docs 和证据存活状态。"))
    return results


def execute_answer(request: AttributionRequest) -> list[DiagnosticResult]:
    spec = _spec_for_id("answer")
    qa = request.qa
    if qa.prompt_supports_answer is True and qa.answer_satisfies_expected is False:
        evidence = [_record(Stage.ANSWER, "prompt_supports_answer", "最终 prompt 已经包含足够支撑证据。", True), _record(Stage.ANSWER, "answer_satisfies_expected", "答案仍未满足人工预期。", False)]
        if qa.claim_alignments:
            evidence.append(_record(Stage.ANSWER, "claim_alignments", "答案 claim 与 prompt/reference 证据的自动对齐结果。", [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in qa.claim_alignments]))
        if qa.wrong_citation:
            evidence.append(_record(Stage.ANSWER, "wrong_citation", "答案引用或映射证据错误。", True))
            cause = "wrong_citation"
        elif qa.partial_answer:
            evidence.append(_record(Stage.ANSWER, "partial_answer", "已有证据可回答，但答案遗漏必要内容。", True))
            if qa.missing_expected_points:
                evidence.append(_record(Stage.ANSWER, "missing_expected_points", "答案遗漏的关键 expected points。", qa.missing_expected_points))
            cause = "partial_answer"
        else:
            unsupported_type = "unsupported_claim" if qa.unsupported_claims else "hallucination" if qa.hallucination else "generic_generation_error"
            evidence.append(
                _record(
                    Stage.ANSWER,
                    "unsupported_claim",
                    "答案存在未支撑、幻觉或泛化生成错误。",
                    {
                        "unsupported_claims": qa.unsupported_claims,
                        "hallucination": qa.hallucination,
                        "unsupported_type": unsupported_type,
                    },
                )
            )
            cause = "unsupported_claim"
        return [_failed(spec, cause, 0.88, evidence)]
    if qa.unsupported_claims or qa.hallucination or qa.wrong_citation or qa.partial_answer:
        return [
            _failed(
                spec,
                "unsupported_claim",
                0.68,
                [
                    _record(
                        Stage.ANSWER,
                        "answer_flags",
                        "已有答案层错误标记，但 prompt 支撑关系尚未完全证明。",
                        {
                            "unsupported_claims": qa.unsupported_claims,
                            "hallucination": qa.hallucination,
                            "wrong_citation": qa.wrong_citation,
                            "partial_answer": qa.partial_answer,
                            "unsupported_type": "needs_review",
                            "alignment_status": qa.alignment_status,
                            "alignment_error": qa.alignment_error,
                        },
                    )
                ],
            )
        ]
    if qa.answer_satisfies_expected is True:
        return [_passed(spec, Stage.ANSWER)]
    return [_uncertain(spec, Stage.ANSWER, "补充答案人工判断和 prompt 支撑证据。")]


def execute_evaluation(request: AttributionRequest) -> list[DiagnosticResult]:
    spec = _spec_for_id("evaluation")
    evaluation = request.evaluation
    qa = request.qa
    missing_evidence_items = _missing_evidence_items(request)
    observations: list[EvidenceRecord] = []
    if evaluation.grader_or_rubric_issue or qa.grader_or_rubric_issue:
        observations.append(_record(Stage.EVALUATION, "grader_or_rubric_issue", "评估器或 rubric 口径被标记；仅作为观察项，不自动归因。", True))
    if evaluation.label_conflict:
        observations.append(_record(Stage.EVALUATION, "label_conflict", "人工标签与评估器结论冲突；仅作为观察项，不自动归因。", True))
    if evaluation.rubric_scope_mismatch:
        observations.append(_record(Stage.EVALUATION, "rubric_scope_mismatch", "Rubric 覆盖范围可能不匹配；仅作为观察项，不自动归因。", True))
    if evaluation.evaluator_missing_evidence:
        observations.append(
            _record(
                Stage.EVALUATION,
                "missing_evidence_items",
                "评估器证据引用缺失；仅作为观察项，不自动归因。",
                {"missing_evidence_items": missing_evidence_items, "missing_count": len(missing_evidence_items)},
            )
        )
    if qa.answer_satisfies_expected is True and qa.prompt_supports_answer is True:
        return [_passed(spec, Stage.EVALUATION, observations)]
    return [_uncertain(spec, Stage.EVALUATION, "评估器和 rubric 信息仅作为观察项；主因需由 RAG 链路或答案证据决定。", observations)]


def run_diagnostic_spec(spec_id: str, request: AttributionRequest) -> list[DiagnosticResult]:
    normalized = SPEC_ID_ALIASES.get(spec_id, spec_id)
    executors = {
        "preprocess": execute_preprocess,
        "retrieval": execute_retrieval,
        "rerank_context": execute_rerank_context,
        "answer": execute_answer,
        "evaluation": execute_evaluation,
    }
    return executors[normalized](request)


def run_all_diagnostics(request: AttributionRequest) -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    for spec_id in ("answer", "rerank_context", "retrieval", "preprocess", "evaluation"):
        results.extend(run_diagnostic_spec(spec_id, request))
    return results


def stage_verdicts_from_results(results: Iterable[DiagnosticResult]) -> list[StageVerdict]:
    return [result.to_stage_verdict() for result in results]


def diagnostic_step_from_results(
    name: str,
    request: AttributionRequest,
    results: list[DiagnosticResult],
    skill_input,
) -> ReferenceChainStep:
    verdicts = stage_verdicts_from_results(results)
    if any(result.status == VerdictStatus.FAIL for result in results):
        status = "fail"
    elif results and all(result.status == VerdictStatus.PASS for result in results):
        status = "pass"
    else:
        status = "uncertain"
    summary_parts = []
    for result in results:
        if result.status == VerdictStatus.FAIL:
            summary_parts.append(f"{result.stage.value}: {result.candidate_cause}")
        elif result.status == VerdictStatus.UNCERTAIN:
            summary_parts.append(f"{result.stage.value}: {result.suggested_action}")
    summary = "；".join(summary_parts) or "未发现明确失败点。"
    action = next((result.suggested_action for result in results if result.status == VerdictStatus.FAIL), "")
    if not action:
        action = next((result.suggested_action for result in results if result.status == VerdictStatus.UNCERTAIN), "继续下游归因。")
    return ReferenceChainStep(
        name=name,
        status=status,
        summary=summary,
        evidence=[item for verdict in verdicts for item in verdict.evidence],
        suggested_next_action=action,
        skill_input=skill_input,
        skill_output={
            "status": status,
            "summary": summary,
            "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
            "diagnostic_results": [result.model_dump(mode="json") for result in results],
            "case_id": request.case_input.case_id,
        },
    )


def format_executable_diagnostic_specs() -> str:
    payload = []
    for spec in DIAGNOSTIC_SPECS:
        payload.append(
            {
                "spec_id": spec.spec_id,
                "domain": spec.domain,
                "stage": spec.stage.value,
                "rules": [
                    {
                        "rule_id": rule.rule_id,
                        "cause": rule.cause,
                        "owner": rule.owner,
                        "evidence_requirements": [
                            {
                                "field": requirement.field,
                                "description": requirement.description,
                                "required": requirement.required,
                            }
                            for requirement in rule.evidence_requirements
                        ],
                        "positive_example": rule.positive_example,
                        "negative_example": rule.negative_example,
                    }
                    for rule in spec.rules
                ],
            }
        )
    return "Executable Diagnostic Specs\n" + json.dumps(payload, ensure_ascii=False, indent=2)
