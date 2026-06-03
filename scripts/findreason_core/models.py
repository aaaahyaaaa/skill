from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Stage(str, Enum):
    EVALUATION = "evaluation"
    ANSWER = "answer"
    CONTEXT = "context"
    RERANK = "rerank"
    RETRIEVAL = "retrieval"
    PREPROCESS = "preprocess"
    KNOWLEDGE = "knowledge"
    UNKNOWN = "unknown"


class VerdictStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class EvidenceDoc(BaseModel):
    id: Optional[str] = None
    title: str = ""
    content: str = ""
    rank: Optional[int] = None
    score: Optional[float] = None
    source: str = ""


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    judgement: str = ""
    workspace_id: str = Field(..., min_length=1)
    app_id: str = Field(..., min_length=1)
    retrieve_query_list: List[str] = Field(default_factory=list)
    case_id: Optional[str] = None
    source_row: Optional[str] = None
    is_knowledge_qa: Optional[bool] = None
    question_scene: Optional[str] = None
    expected_knowledge_ids: List[str] = Field(default_factory=list)
    expected_knowledge_points: List[str] = Field(default_factory=list)
    expected_answer: Optional[str] = None
    error_points: List[str] = Field(default_factory=list)


class WorkflowOverrides(BaseModel):
    topk: Optional[int] = None


class InputParamItem(BaseModel):
    key: str
    type: str = "String"
    value: Any = None


class AdaptedInputFields(BaseModel):
    query: str = ""
    judgement: str = ""
    workspace_id: str = ""
    app_id: str = ""
    retrieve_query_list: List[str] = Field(default_factory=list)
    case_id: Optional[str] = None
    source_row: Optional[str] = None


class InputAdaptRequest(BaseModel):
    input: str = ""
    workspace_id: str = ""
    app_id: str = ""
    judgement: str = ""
    case_id: Optional[str] = None
    source_row: Optional[str] = None


class InputAdaptResponse(BaseModel):
    status: str
    source: str
    case_input: Optional[CaseInput] = None
    adapted_fields: AdaptedInputFields = Field(default_factory=AdaptedInputFields)
    input_params: List[InputParamItem] = Field(default_factory=list)
    workflow_overrides: WorkflowOverrides = Field(default_factory=WorkflowOverrides)
    error: Optional[str] = None
    notes: str = ""


class JudgementSignal(BaseModel):
    key: str
    value: Any = None
    source: str = "judgement"
    confidence: Optional[float] = None
    evidence_text: str = ""


class JudgementEvidence(BaseModel):
    source_type: str = "empty"
    raw_text: str = ""
    mapper_status: str = "empty"
    signals: List[JudgementSignal] = Field(default_factory=list)
    unmapped_notes: str = ""
    error: Optional[str] = None


class FieldMapEntry(BaseModel):
    source_path: str = ""
    source_label: str = ""
    raw_value: Any = None
    normalized_value: Any = None
    confidence: float = 0.0
    missing_reason: Optional[str] = None


class ImmediateFailure(BaseModel):
    stage: Stage = Stage.UNKNOWN
    cause: str = "uncertain"
    explanation: str = ""
    evidence_refs: List[str] = Field(default_factory=list)


class CausalPathStep(BaseModel):
    order: int
    stage: Stage
    event: str
    evidence_refs: List[str] = Field(default_factory=list)


class ConfidenceBreakdown(BaseModel):
    evidence_quality: float = 0.0
    metric_strength: float = 0.0
    reference_confidence: float = 0.0
    counterfactual_lift: float = 0.0
    cross_skill_consistency: float = 0.0
    field_mapping_confidence: float = 0.0
    penalty: float = 0.0


class PreprocessEvidence(BaseModel):
    rewrite_query: str = ""
    keywords: List[str] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    answer_model: str = ""
    rewrite_drift: bool = False
    keyword_loss: bool = False
    filter_error: bool = False
    permission_error: bool = False
    model_route_error: bool = False
    notes: str = ""


class RetrievalEvidence(BaseModel):
    origin_doc_list: List[EvidenceDoc] = Field(default_factory=list)
    origin_faq_list: List[EvidenceDoc] = Field(default_factory=list)
    wide_recall_docs: List[EvidenceDoc] = Field(default_factory=list)
    expected_knowledge_hit: Optional[bool] = None
    online_retrieval_hit: Optional[bool] = None
    knowledge_exists: Optional[bool] = None
    topic_mismatch: bool = False
    topic_mismatch_doc_ids: List[str] = Field(default_factory=list)
    topic_mismatch_reason: str = ""
    permission_miss: bool = False
    notes: str = ""


class RerankEvidence(BaseModel):
    rerank_docs: List[EvidenceDoc] = Field(default_factory=list)
    prompt_docs: List[EvidenceDoc] = Field(default_factory=list)
    expected_doc_survived_rerank: Optional[bool] = None
    expected_doc_in_prompt: Optional[bool] = None
    threshold_too_strict: bool = False
    prompt_truncation: bool = False
    context_assembly_error: bool = False
    noise_overload: bool = False
    parameter_experiment: "RerankParameterExperimentEvidence" = Field(default_factory=lambda: RerankParameterExperimentEvidence())
    notes: str = ""


class RerankTargetObservation(BaseModel):
    target_doc_id: str = ""
    survived: bool = False
    best_rank: Optional[int] = None
    best_score: Optional[float] = None
    title: str = ""


class RerankExperimentVariant(BaseModel):
    variant_id: str
    description: str = ""
    parameter_diff: Dict[str, Any] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str = "not_run"
    target_observations: List[RerankTargetObservation] = Field(default_factory=list)
    survived_target_count: int = 0
    best_rank: Optional[int] = None
    best_score: Optional[float] = None
    rank_lift: Optional[int] = None
    score_lift: Optional[float] = None
    top_doc_ids: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class RerankParameterExperimentEvidence(BaseModel):
    enabled: bool = False
    status: str = "not_run"
    endpoint: str = ""
    baseline_parameters: Dict[str, Any] = Field(default_factory=dict)
    baseline: Optional[RerankExperimentVariant] = None
    variants: List[RerankExperimentVariant] = Field(default_factory=list)
    target_doc_ids: List[str] = Field(default_factory=list)
    best_variant: Optional[RerankExperimentVariant] = None
    parameter_issue_supported: Optional[bool] = None
    notes: str = ""


class ClaimAlignment(BaseModel):
    claim: str = ""
    support_status: str = "uncertain"
    support_doc_ids: List[str] = Field(default_factory=list)
    reason: str = ""


class ContradictionEvidence(BaseModel):
    kind: str = ""
    status: str = "unverified"
    claim: str = ""
    conflicting_claim: str = ""
    doc_ids: List[str] = Field(default_factory=list)
    query_variant: str = ""
    reason: str = ""


class QaEvidence(BaseModel):
    answer: str = ""
    prompt_supports_answer: Optional[bool] = None
    answer_satisfies_expected: Optional[bool] = None
    unsupported_claims: List[str] = Field(default_factory=list)
    answer_claims: List[str] = Field(default_factory=list)
    claim_alignments: List[ClaimAlignment] = Field(default_factory=list)
    missing_expected_points: List[str] = Field(default_factory=list)
    alignment_status: str = "not_run"
    alignment_error: Optional[str] = None
    hallucination: bool = False
    wrong_citation: bool = False
    partial_answer: bool = False
    grader_or_rubric_issue: bool = False
    answer_self_contradiction: bool = False
    answer_reference_conflict: bool = False
    conflict_status: str = "not_checked"
    contradictions: List[ContradictionEvidence] = Field(default_factory=list)
    notes: str = ""


class EvaluationEvidence(BaseModel):
    grader_or_rubric_issue: bool = False
    label_conflict: bool = False
    rubric_scope_mismatch: bool = False
    evaluator_missing_evidence: bool = False
    missing_evidence_items: List[str] = Field(default_factory=list)
    notes: str = ""


class ReferenceEvidence(BaseModel):
    source: str = "none"
    support_docs: List[EvidenceDoc] = Field(default_factory=list)
    support_claims: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    notes: str = ""


class WorkflowReplayEvidence(BaseModel):
    enabled: bool = False
    status: str = "not_configured"
    endpoint: str = ""
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_payload: Any = None
    extracted_evidence: Dict[str, Any] = Field(default_factory=dict)
    resolved_app: Dict[str, Any] = Field(default_factory=dict)
    input_schema: List[Dict[str, Any]] = Field(default_factory=list)
    node_traces: List[Dict[str, Any]] = Field(default_factory=list)
    auth_token_source: str = ""
    error: Optional[str] = None
    notes: str = ""


class WideRecallEvidence(BaseModel):
    enabled: bool = False
    status: str = "not_configured"
    endpoint: str = ""
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_payload: Any = None
    extracted_evidence: Dict[str, Any] = Field(default_factory=dict)
    query_variants: List[str] = Field(default_factory=list)
    matched_expected_ids: List[str] = Field(default_factory=list)
    auth_token_source: str = ""
    error: Optional[str] = None
    notes: str = ""


class KnowledgeDetailEvidence(BaseModel):
    enabled: bool = False
    status: str = "not_needed"
    endpoint: str = ""
    request_payload: Dict[str, Any] = Field(default_factory=dict)
    response_payload: Any = None
    extracted_evidence: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    notes: str = ""


class ContrastiveProbeEvidence(BaseModel):
    enabled: bool = False
    status: str = "not_run"
    retrieval_gap_status: str = "not_checked"
    retrieval_gap_detected: Optional[bool] = None
    retrieval_gap_summary: str = ""
    counterfactual_lift: float = 0.0
    summary: str = ""
    notes: str = ""


class AttributionRequest(BaseModel):
    case_input: CaseInput
    workflow_overrides: WorkflowOverrides = Field(default_factory=WorkflowOverrides)
    field_map: Dict[str, FieldMapEntry] = Field(default_factory=dict)
    judgement_evidence: JudgementEvidence = Field(default_factory=JudgementEvidence)
    preprocess: PreprocessEvidence = Field(default_factory=PreprocessEvidence)
    retrieval: RetrievalEvidence = Field(default_factory=RetrievalEvidence)
    rerank: RerankEvidence = Field(default_factory=RerankEvidence)
    qa: QaEvidence = Field(default_factory=QaEvidence)
    evaluation: EvaluationEvidence = Field(default_factory=EvaluationEvidence)
    reference: ReferenceEvidence = Field(default_factory=ReferenceEvidence)
    workflow_replay: WorkflowReplayEvidence = Field(default_factory=WorkflowReplayEvidence)
    wide_recall: WideRecallEvidence = Field(default_factory=WideRecallEvidence)
    knowledge_detail: KnowledgeDetailEvidence = Field(default_factory=KnowledgeDetailEvidence)
    contrastive_probe: ContrastiveProbeEvidence = Field(default_factory=ContrastiveProbeEvidence)


class EvidenceRecord(BaseModel):
    stage: Stage
    field: str
    reason: str
    value: Any = None
    source_path: Optional[str] = None


class StageVerdict(BaseModel):
    stage: Stage
    status: VerdictStatus
    candidate_cause: str
    confidence: float
    owner: str
    suggested_action: str
    evidence: List[EvidenceRecord] = Field(default_factory=list)


class EvidenceRequirementReport(BaseModel):
    field: str
    description: str
    required: bool = True


class DiagnosticResult(BaseModel):
    spec_id: str
    domain: str
    stage: Stage
    status: VerdictStatus
    candidate_cause: str
    confidence: float
    owner: str
    suggested_action: str
    matched_rule_id: Optional[str] = None
    evidence_requirements: List[EvidenceRequirementReport] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)

    def to_stage_verdict(self) -> StageVerdict:
        return StageVerdict(
            stage=self.stage,
            status=self.status,
            candidate_cause=self.candidate_cause,
            confidence=self.confidence,
            owner=self.owner,
            suggested_action=self.suggested_action,
            evidence=self.evidence,
        )


class SkillProbe(BaseModel):
    marker: str
    skill_name: str
    observed_input_keys: List[str] = Field(default_factory=list)
    observed_output_keys: List[str] = Field(default_factory=list)
    instruction: str = ""


class ReferenceChainStep(BaseModel):
    name: str
    status: str
    summary: str = ""
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    suggested_next_action: str = ""
    skill_input: Any = None
    skill_output: Any = None
    skill_probe: Optional[SkillProbe] = None


class AgentTraceStep(BaseModel):
    step_index: int
    tool_name: str
    status: str
    summary: str = ""
    planner_reason: str = ""
    duration_ms: float = 0.0
    error: Optional[str] = None
    input: Any = None
    output: Any = None


class AttributionResponse(BaseModel):
    run_id: Optional[str] = None
    case_id: Optional[str] = None
    field_map: Dict[str, FieldMapEntry] = Field(default_factory=dict)
    immediate_failure: ImmediateFailure = Field(default_factory=ImmediateFailure)
    primary_cause: str
    primary_stage: Optional[Stage]
    secondary_causes: List[str] = Field(default_factory=list)
    causal_path: List[CausalPathStep] = Field(default_factory=list)
    confidence: float
    confidence_breakdown: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    owner: str
    suggested_action: str
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    stage_verdicts: List[StageVerdict] = Field(default_factory=list)
    reference_chain: List[ReferenceChainStep] = Field(default_factory=list)
    evidence_chain: List[ReferenceChainStep] = Field(default_factory=list)
    diagnostic_results: List[DiagnosticResult] = Field(default_factory=list)
    arbitration: Dict[str, Any] = Field(default_factory=dict)
    agent_trace: List[AgentTraceStep] = Field(default_factory=list)
    need_reference_refresh: bool = False
    reference_refresh_reason: Optional[str] = None
    need_human_review: bool = False
    contrastive_probe_summary: str = ""
    retrieval_gap_summary: str = ""
    knowledge_verdict: str = ""
    knowledge_gap_confidence: float = 0.0
    counterfactual_lift: float = 0.0
