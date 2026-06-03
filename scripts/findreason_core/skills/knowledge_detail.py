from ..models import AttributionRequest, EvidenceRecord, KnowledgeDetailEvidence, ReferenceChainStep, Stage


def _status(knowledge_detail: KnowledgeDetailEvidence) -> str:
    if knowledge_detail.status in {"ok", "not_needed"}:
        return "pass"
    if knowledge_detail.status == "partial":
        return "uncertain"
    if knowledge_detail.status == "error":
        return "fail"
    return "uncertain"


def run_knowledge_detail_skill(request: AttributionRequest) -> ReferenceChainStep:
    knowledge_detail = request.knowledge_detail
    status = _status(knowledge_detail)
    extracted = knowledge_detail.extracted_evidence
    hydrated_docs = extracted.get("hydrated_docs", [])
    requested_ids = extracted.get("requested_ids", [])
    matched_expected_ids = extracted.get("matched_expected_ids", [])
    missing_ids = extracted.get("missing_ids", [])
    output = {
        "status": knowledge_detail.status,
        "endpoint": knowledge_detail.endpoint,
        "extracted_evidence": extracted,
        "response_payload": knowledge_detail.response_payload,
        "error": knowledge_detail.error,
        "notes": knowledge_detail.notes,
    }
    return ReferenceChainStep(
        name="KnowledgeDetailTool",
        status=status,
        summary=knowledge_detail.notes or "未返回知识详情补全状态。",
        evidence=[
            EvidenceRecord(
                stage=Stage.KNOWLEDGE,
                field="knowledge_detail",
                reason="KnowledgeDetailTool 按 doc_corpus 知识 ID 查询并补全召回文档正文。",
                value={
                    "status": knowledge_detail.status,
                    "requested_ids": requested_ids,
                    "matched_expected_ids": matched_expected_ids,
                    "hit_count": len(hydrated_docs) if isinstance(hydrated_docs, list) else 0,
                    "missing_ids": missing_ids,
                    "error": knowledge_detail.error,
                },
            )
        ],
        suggested_next_action="继续进入 Reference Evidence。" if status == "pass" else "检查知识详情接口、知识 ID 是否为 doc_corpus.id，或保留已有召回证据继续归因。",
        skill_input=knowledge_detail.request_payload,
        skill_output=output,
    )
