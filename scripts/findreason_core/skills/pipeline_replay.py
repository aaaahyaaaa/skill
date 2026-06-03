from ..models import AttributionRequest, EvidenceRecord, ReferenceChainStep, Stage, WorkflowReplayEvidence


def _status(replay: WorkflowReplayEvidence) -> str:
    if replay.status == "ok":
        return "pass"
    if replay.status == "partial":
        return "uncertain"
    if replay.status == "not_configured":
        return "missing"
    if replay.status == "error":
        return "fail"
    return "uncertain"


def run_pipeline_replay_skill(request: AttributionRequest) -> ReferenceChainStep:
    replay = request.workflow_replay
    status = _status(replay)
    replay_output = {
        "status": replay.status,
        "endpoint": replay.endpoint,
        "resolved_app": replay.resolved_app,
        "input_schema": replay.input_schema,
        "node_traces": replay.node_traces,
        "auth_token_source": replay.auth_token_source,
        "extracted_evidence": replay.extracted_evidence,
        "response_payload": replay.response_payload,
        "error": replay.error,
        "notes": replay.notes,
    }
    return ReferenceChainStep(
        name="PipelineReplayTool",
        status=status,
        summary=replay.notes or "未返回 workflow replay 状态。",
        evidence=[
            EvidenceRecord(
                stage=Stage.RETRIEVAL if replay.status == "ok" else Stage.PREPROCESS,
                field="workflow_replay",
                reason="PipelineReplayTool 使用 workflow completions 重跑当前输入并抽取运行级证据。",
                value=replay_output,
            )
        ],
        suggested_next_action="在项目配置或环境变量中配置 WORKFLOW_AUTH_TOKEN 后重跑。" if status == "missing" else "检查 workflow endpoint、鉴权或请求 payload。" if status == "fail" else "接入 origin/rerank/prompt trace 后再判断链路丢失。" if replay.status == "partial" else "继续使用重跑证据诊断。",
        skill_input=replay.request_payload,
        skill_output=replay_output,
    )
