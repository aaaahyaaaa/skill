from ..models import AttributionRequest, EvidenceRecord, ReferenceChainStep, Stage


def run_input_adapter(request: AttributionRequest) -> ReferenceChainStep:
    missing = [
        field
        for field, value in {
            "query": request.case_input.query,
            "workspace_id": request.case_input.workspace_id,
            "app_id": request.case_input.app_id,
        }.items()
        if not str(value or "").strip()
    ]
    status = "missing" if missing else "pass"
    return ReferenceChainStep(
        name="输入适配",
        status=status,
        summary="缺少必填输入。" if missing else "已完成 SourceAdapter、FieldMap、query、judgement、workspace_id、app_id 输入适配与 judgement signals 映射。",
        evidence=[
            EvidenceRecord(
                stage=Stage.PREPROCESS,
                field="case_input",
                reason="输入适配基于 SourceAdapter / FieldMap 生成标准 AttributionCase；judgement 由 Mapper 生成开放 signals。",
                value={
                    "case_input": request.case_input.model_dump(mode="json"),
                    "field_map": {key: value.model_dump(mode="json") for key, value in request.field_map.items()},
                    "judgement_evidence": request.judgement_evidence.model_dump(mode="json"),
                },
            )
        ],
        suggested_next_action="补齐缺失字段：" + "、".join(missing) if missing else "继续构建运行级证据。",
        skill_input=request.case_input.model_dump(mode="json"),
        skill_output={
            "status": status,
            "missing_fields": missing,
            "required_fields": ["query", "workspace_id", "app_id"],
            "normalized_case_input": request.case_input.model_dump(mode="json"),
            "field_map": {key: value.model_dump(mode="json") for key, value in request.field_map.items()},
            "judgement_evidence": request.judgement_evidence.model_dump(mode="json"),
        },
    )
