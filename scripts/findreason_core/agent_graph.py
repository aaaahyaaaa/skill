from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypedDict

from .answer_alignment import align_answer_evidence
from .judgement_mapper import map_judgement
from .knowledge_detail import run_knowledge_detail
from .models import (
    AgentTraceStep,
    AttributionRequest,
    AttributionResponse,
    DiagnosticResult,
    ReferenceChainStep,
    StageVerdict,
)
from .run_store import record_step
from .source_adapters import apply_source_adapter
from .skills.answer_faithfulness import run_answer_faithfulness_skill
from .skills.common import verdict_status, verdict_summary
from .skills.evaluator_rubric import run_evaluator_rubric_skill
from .skills.input_adapter import run_input_adapter
from .skills.knowledge_detail import run_knowledge_detail_skill
from .skills.orchestrator import build_rule_candidate
from .skills.pipeline_replay import run_pipeline_replay_skill
from .skills.query_preprocess import run_query_preprocess_skill
from .skills.reference_evidence import run_reference_evidence_skill
from .skills.rerank_context import run_rerank_context_skill
from .skills.retrieval import run_retrieval_skill
from .skills.wide_recall import run_wide_recall_skill
from .wide_recall import run_wide_recall
from .workflow_replay import replay_workflow


ALLOWED_TOOLS = [
    "input_adapter",
    "pipeline_replay",
    "wide_recall",
    "knowledge_detail",
    "reference_evidence",
    "diagnostics",
    "orchestrator",
]
MAX_AGENT_STEPS = 8


class AgentState(TypedDict, total=False):
    request: AttributionRequest
    reference_chain: list[ReferenceChainStep]
    verdicts: list[StageVerdict]
    diagnostic_results: list[DiagnosticResult]
    rule_candidate: AttributionResponse
    completed_tools: list[str]
    agent_trace: list[AgentTraceStep]
    run_id: str | None


@dataclass
class AgentGraphResult:
    request: AttributionRequest
    rule_candidate: AttributionResponse
    agent_trace: list[AgentTraceStep]
    run_id: str | None = None


def _next_step_index(state: AgentState) -> int:
    return len(state.get("agent_trace", [])) + 1


def _append_trace(state: AgentState, step: AgentTraceStep) -> None:
    state.setdefault("agent_trace", []).append(step)
    record_step(state.get("run_id"), step)


def _rule_next_tool(completed: list[str]) -> str | None:
    for tool_name in ALLOWED_TOOLS:
        if tool_name not in completed:
            return tool_name
    return None


async def _choose_next_tool(state: AgentState) -> str | None:
    completed = list(state.get("completed_tools", []))
    rule_next = _rule_next_tool(completed)
    if not rule_next:
        return None

    planner_reason = f"规则 planner 选择 {rule_next}。"
    next_tool = rule_next
    planner_output: dict[str, Any] = {"rule_next_tool": rule_next}

    if rule_next == "input_adapter":
        planner_reason = "input_adapter 是固定首步；使用规则 planner。"

    _append_trace(
        state,
        AgentTraceStep(
            step_index=_next_step_index(state),
            tool_name="planner",
            status="pass",
            summary=f"下一步：{next_tool}",
            planner_reason=planner_reason,
            input={"completed_tools": completed, "allowed_tools": ALLOWED_TOOLS},
            output={**planner_output, "next_tool": next_tool},
        ),
    )
    return next_tool


async def _run_input_adapter(state: AgentState) -> dict[str, Any]:
    request = apply_source_adapter(state["request"])
    request = await map_judgement(request)
    request = apply_source_adapter(request)
    step = run_input_adapter(request)
    state["request"] = request
    state.setdefault("reference_chain", []).append(step)
    return {"status": step.status, "summary": step.summary, "step": step.model_dump(mode="json")}


async def _run_pipeline_replay(state: AgentState) -> dict[str, Any]:
    request = await replay_workflow(state["request"])
    step = run_pipeline_replay_skill(request)
    state["request"] = request
    state.setdefault("reference_chain", []).append(step)
    return {"status": step.status, "summary": step.summary, "step": step.model_dump(mode="json")}


async def _run_wide_recall_tool(state: AgentState) -> dict[str, Any]:
    request = await run_wide_recall(state["request"])
    step = run_wide_recall_skill(request)
    state["request"] = request
    state.setdefault("reference_chain", []).append(step)
    return {"status": step.status, "summary": step.summary, "step": step.model_dump(mode="json")}


async def _run_knowledge_detail_tool(state: AgentState) -> dict[str, Any]:
    request = await run_knowledge_detail(state["request"])
    step = run_knowledge_detail_skill(request)
    state["request"] = request
    state.setdefault("reference_chain", []).append(step)
    return {"status": step.status, "summary": step.summary, "step": step.model_dump(mode="json")}


async def _run_reference_evidence(state: AgentState) -> dict[str, Any]:
    step = run_reference_evidence_skill(state["request"])
    state.setdefault("reference_chain", []).append(step)
    return {"status": step.status, "summary": step.summary, "step": step.model_dump(mode="json")}


async def _run_diagnostics(state: AgentState) -> dict[str, Any]:
    state["request"] = await align_answer_evidence(state["request"])
    runners = [
        run_query_preprocess_skill,
        run_retrieval_skill,
        run_rerank_context_skill,
        run_answer_faithfulness_skill,
        run_evaluator_rubric_skill,
    ]
    all_verdicts: list[StageVerdict] = []
    all_diagnostic_results: list[DiagnosticResult] = []
    steps: list[ReferenceChainStep] = []
    for runner in runners:
        verdicts, diagnostic_results, step = runner(state["request"])
        all_verdicts.extend(verdicts)
        all_diagnostic_results.extend(diagnostic_results)
        steps.append(step)
    state["verdicts"] = all_verdicts
    state["diagnostic_results"] = all_diagnostic_results
    state.setdefault("reference_chain", []).extend(steps)
    return {
        "status": verdict_status(all_verdicts),
        "summary": verdict_summary(all_verdicts),
        "diagnostic_results": [item.model_dump(mode="json") for item in all_diagnostic_results],
        "steps": [step.model_dump(mode="json") for step in steps],
    }


def _run_orchestrator(state: AgentState) -> dict[str, Any]:
    rule_candidate = build_rule_candidate(
        verdicts=list(state.get("verdicts", [])),
        reference_chain=list(state.get("reference_chain", [])),
        request=state["request"],
        diagnostic_results=list(state.get("diagnostic_results", [])),
    )
    state["rule_candidate"] = rule_candidate
    return {
        "status": "fallback" if rule_candidate.primary_cause == "uncertain" else "pass",
        "summary": rule_candidate.suggested_action,
        "rule_candidate": rule_candidate.model_dump(mode="json"),
    }


async def _execute_tool(tool_name: str, state: AgentState) -> dict[str, Any]:
    if tool_name == "input_adapter":
        return await _run_input_adapter(state)
    if tool_name == "pipeline_replay":
        return await _run_pipeline_replay(state)
    if tool_name == "wide_recall":
        return await _run_wide_recall_tool(state)
    if tool_name == "knowledge_detail":
        return await _run_knowledge_detail_tool(state)
    if tool_name == "reference_evidence":
        return await _run_reference_evidence(state)
    if tool_name == "diagnostics":
        return await _run_diagnostics(state)
    if tool_name == "orchestrator":
        return _run_orchestrator(state)
    raise ValueError(f"Unknown agent tool: {tool_name}")


async def _run_tool_with_trace(tool_name: str, state: AgentState) -> None:
    started_at = perf_counter()
    try:
        output = await _execute_tool(tool_name, state)
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        status = str(output.get("status") or "pass")
        summary = str(output.get("summary") or f"{tool_name} completed")
        _append_trace(
            state,
            AgentTraceStep(
                step_index=_next_step_index(state),
                tool_name=tool_name,
                status=status,
                summary=summary,
                duration_ms=duration_ms,
                input={"tool_name": tool_name},
                output=output,
            ),
        )
    except Exception as exc:
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        _append_trace(
            state,
            AgentTraceStep(
                step_index=_next_step_index(state),
                tool_name=tool_name,
                status="error",
                summary=f"{tool_name} failed",
                duration_ms=duration_ms,
                error=str(exc)[:500],
                input={"tool_name": tool_name},
                output={},
            ),
        )
        raise
    state.setdefault("completed_tools", []).append(tool_name)


async def run_agent_graph(
    request: AttributionRequest,
    run_id: str | None = None,
    max_agent_steps: int = MAX_AGENT_STEPS,
) -> AgentGraphResult:
    state: AgentState = {
        "request": request,
        "reference_chain": [],
        "verdicts": [],
        "diagnostic_results": [],
        "completed_tools": [],
        "agent_trace": [],
        "run_id": run_id,
    }

    tool_steps = 0
    while tool_steps < max_agent_steps:
        next_tool = await _choose_next_tool(state)
        if next_tool is None:
            break
        await _run_tool_with_trace(next_tool, state)
        tool_steps += 1
        if next_tool == "orchestrator":
            break

    if "rule_candidate" not in state:
        if "diagnostics" not in state.get("completed_tools", []):
            await _run_diagnostics(state)
        _run_orchestrator(state)
        _append_trace(
            state,
            AgentTraceStep(
                step_index=_next_step_index(state),
                tool_name="agent_guardrail",
                status="fallback",
                summary="Agent reached max steps before orchestrator; forced deterministic arbitration.",
                planner_reason=f"max_agent_steps={max_agent_steps}",
                input={"completed_tools": state.get("completed_tools", [])},
                output={"primary_cause": state["rule_candidate"].primary_cause},
            ),
        )

    rule_candidate = state["rule_candidate"]
    rule_candidate.agent_trace = list(state.get("agent_trace", []))
    rule_candidate.run_id = run_id
    return AgentGraphResult(
        request=state["request"],
        rule_candidate=rule_candidate,
        agent_trace=list(state.get("agent_trace", [])),
        run_id=run_id,
    )
