from ..models import AttributionRequest, EvidenceRecord, ReferenceChainStep, Stage, WideRecallEvidence


def _status(wide_recall: WideRecallEvidence) -> str:
    if wide_recall.status == "ok":
        return "pass"
    if wide_recall.status == "not_configured":
        return "missing"
    if wide_recall.status == "error":
        return "fail"
    return "uncertain"


def run_wide_recall_skill(request: AttributionRequest) -> ReferenceChainStep:
    wide_recall = request.wide_recall
    status = _status(wide_recall)
    extracted = wide_recall.extracted_evidence
    output = {
        "status": wide_recall.status,
        "endpoint": wide_recall.endpoint,
        "query_variants": wide_recall.query_variants,
        "matched_expected_ids": wide_recall.matched_expected_ids,
        "auth_token_source": wide_recall.auth_token_source,
        "extracted_evidence": extracted,
        "response_payload": wide_recall.response_payload,
        "error": wide_recall.error,
        "notes": wide_recall.notes,
    }
    docs = extracted.get("wide_recall_docs", [])
    faqs = extracted.get("wide_recall_faqs", [])
    return ReferenceChainStep(
        name="WideRecallTool",
        status=status,
        summary=wide_recall.notes or "未返回诊断宽召回状态。",
        evidence=[
            EvidenceRecord(
                stage=Stage.RETRIEVAL,
                field="wide_recall",
                reason="WideRecallTool 使用 trace Sirius recall 模板、高 topK 和 query variants 构建同知识库 open-label 宽召回证据。",
                value={
                    "status": wide_recall.status,
                    "query_variants": wide_recall.query_variants,
                    "wide_recall_doc_count": len(docs) if isinstance(docs, list) else 0,
                    "wide_recall_faq_count": len(faqs) if isinstance(faqs, list) else 0,
                    "matched_expected_ids": wide_recall.matched_expected_ids,
                    "error": wide_recall.error,
                },
            )
        ],
        suggested_next_action="确认 trace 中存在 Sirius recall 子 span，并配置 OpenPlat bootstrap token 或 WORKFLOW_AUTH_TOKEN 后重跑。" if status == "missing" else "检查 Sirius recall endpoint、workspace apiKey 或请求 payload。" if status == "fail" else "对比线上 trace 初召回与 open-label 宽召回结果。",
        skill_input=wide_recall.request_payload,
        skill_output=output,
    )
