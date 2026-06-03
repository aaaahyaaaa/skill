from ..models import AttributionRequest, EvidenceRecord, ReferenceChainStep, Stage


def run_reference_evidence_skill(request: AttributionRequest) -> ReferenceChainStep:
    expected_ids = request.case_input.expected_knowledge_ids
    support_docs = request.reference.support_docs
    replay_docs = request.workflow_replay.extracted_evidence.get("origin_doc_list", [])
    replay_output_docs = request.workflow_replay.extracted_evidence.get("workflow_output_doc_list", [])
    replay_output_faqs = request.workflow_replay.extracted_evidence.get("workflow_output_faq_list", [])
    wide_docs = request.wide_recall.extracted_evidence.get("wide_recall_docs", [])
    wide_faqs = request.wide_recall.extracted_evidence.get("wide_recall_faqs", [])
    hydrated_docs = request.knowledge_detail.extracted_evidence.get("hydrated_docs", [])
    expected_detail_docs = request.knowledge_detail.extracted_evidence.get("expected_knowledge_docs", [])
    judgement_signals = request.judgement_evidence.signals
    status = "pass" if expected_ids or support_docs or replay_docs or wide_docs or wide_faqs or hydrated_docs or judgement_signals else "missing"
    return ReferenceChainStep(
        name="Reference Evidence",
        status=status,
        summary="已有人工锚点、线上 replay 召回证据、诊断宽召回证据或知识详情正文。" if status == "pass" else "缺少人工锚点和参考证据。",
        evidence=[
            EvidenceRecord(
                stage=Stage.KNOWLEDGE,
                field="reference_evidence",
                reason="Reference Evidence 区分人工期望知识、PipelineReplayTool 线上召回和 WideRecallTool 诊断宽召回。",
                value={
                    "expected_knowledge_ids": expected_ids,
                    "support_doc_count": len(support_docs),
                    "workflow_origin_doc_count": len(replay_docs) if isinstance(replay_docs, list) else 0,
                    "workflow_output_doc_count": len(replay_output_docs) if isinstance(replay_output_docs, list) else 0,
                    "workflow_output_faq_count": len(replay_output_faqs) if isinstance(replay_output_faqs, list) else 0,
                    "wide_recall_doc_count": len(wide_docs) if isinstance(wide_docs, list) else 0,
                    "wide_recall_faq_count": len(wide_faqs) if isinstance(wide_faqs, list) else 0,
                    "wide_recall_matched_expected_ids": request.wide_recall.matched_expected_ids,
                    "knowledge_detail_hit_count": len(hydrated_docs) if isinstance(hydrated_docs, list) else 0,
                    "knowledge_detail_matched_expected_ids": request.knowledge_detail.extracted_evidence.get("matched_expected_ids", []),
                    "judgement_mapper_status": request.judgement_evidence.mapper_status,
                    "judgement_signal_count": len(judgement_signals),
                },
            )
        ],
        suggested_next_action="继续进入五类诊断 Skill。" if status == "pass" else "补充人工期望知识 ID 或同知识库宽召回证据。",
        skill_input={
            "case_input": request.case_input.model_dump(mode="json"),
            "reference": request.reference.model_dump(mode="json"),
            "workflow_replay_extracted_evidence": request.workflow_replay.extracted_evidence,
            "wide_recall_extracted_evidence": request.wide_recall.extracted_evidence,
            "knowledge_detail_extracted_evidence": request.knowledge_detail.extracted_evidence,
            "judgement_evidence": request.judgement_evidence.model_dump(mode="json"),
        },
        skill_output={
            "status": status,
            "expected_knowledge_ids": expected_ids,
            "support_docs": [doc.model_dump(mode="json") for doc in support_docs],
            "support_claims": request.reference.support_claims,
            "judgement_evidence": request.judgement_evidence.model_dump(mode="json"),
            "workflow_replay_doc_count": len(replay_docs) if isinstance(replay_docs, list) else 0,
            "workflow_output_doc_count": len(replay_output_docs) if isinstance(replay_output_docs, list) else 0,
            "workflow_output_faq_count": len(replay_output_faqs) if isinstance(replay_output_faqs, list) else 0,
            "wide_recall_doc_count": len(wide_docs) if isinstance(wide_docs, list) else 0,
            "wide_recall_faq_count": len(wide_faqs) if isinstance(wide_faqs, list) else 0,
            "wide_recall_matched_expected_ids": request.wide_recall.matched_expected_ids,
            "knowledge_detail_hit_count": len(hydrated_docs) if isinstance(hydrated_docs, list) else 0,
            "knowledge_detail_matched_expected_ids": request.knowledge_detail.extracted_evidence.get("matched_expected_ids", []),
            "knowledge_detail_docs": hydrated_docs if isinstance(hydrated_docs, list) else [],
            "knowledge_detail_expected_docs": expected_detail_docs if isinstance(expected_detail_docs, list) else [],
        },
    )
