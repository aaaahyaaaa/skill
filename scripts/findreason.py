#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from findreason_core.v3 import (
    CAUSE_ENUM,
    SCHEMA_VERSION,
    STAGE_ORDER,
    V3Error,
    _normalize_assertion_inputs,
    _raise_if_legacy_assertion_inputs,
    build_ingest_output,
    build_probe_result,
    fetch_openplat_trace_detail,
    fetch_workflow_nodes_v3,
    json_dumps,
    load_ingest,
    load_probe_dir,
    orchestrate_v3,
    read_json_arg,
    replay_workflow_v3,
    run_probe_plan,
    validate_judgement_signals,
    write_ingest_cache,
    write_json,
)


PROBE_COMMANDS = [
    "probe-knowledge-detail",
    "probe-permission-check",
    "probe-wide-recall",
    "probe-rerank-bypass",
    "probe-context-assembly",
]


def _print_json(value: Any) -> None:
    print(json_dumps(value))


def _read_case_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("case"), dict):
        return payload["case"]
    if isinstance(payload.get("case_input"), dict):
        case = dict(payload["case_input"])
        for key in (
            "answer_hint",
            "judgement_evidence",
            "host_agent",
            "answer_claims",
            "missing_expected_points",
            "unsupported_claims",
            "claim_alignments",
            "expected_knowledge_points",
            "wrong_citations",
            "qa",
        ):
            if key in payload:
                case[key] = payload[key]
        return case
    return payload


def _trace_failure_ingest(args: argparse.Namespace, case: dict[str, Any], exc: V3Error) -> dict[str, Any]:
    _raise_if_legacy_assertion_inputs(case)
    app_id = str(args.app_id or case.get("app_id") or "unknown")
    case_input: dict[str, Any] = {
        "query": case.get("query") or "unknown query",
        "judgement": case.get("judgement", ""),
        "workspace_id": str(args.workspace_id),
        "app_id": app_id,
        "case_id": case.get("case_id") or args.log_id,
        "expected_knowledge_ids": case.get("expected_knowledge_ids", []),
    }
    for key in ("query_hint", "source_row", "question_scene", "expected_answer", "oracle_skip_self"):
        if case.get(key) not in (None, ""):
            case_input[key] = case[key]
    judgement_evidence = case.get("judgement_evidence") if isinstance(case.get("judgement_evidence"), dict) else {}
    host_agent = case.get("host_agent") if isinstance(case.get("host_agent"), dict) else {}
    qa: dict[str, Any] = {"wrong_citation": bool(case.get("wrong_citations"))}
    raw_host_fields: dict[str, Any] = {}
    answer_hint = str(case.get("answer_hint") or "").strip()
    if answer_hint:
        qa["answer"] = answer_hint
        raw_host_fields["answer_hint"] = answer_hint
    host_qa = case.get("qa") if isinstance(case.get("qa"), dict) else {}
    for key in (
        "prompt_supports_answer",
        "answer_satisfies_expected",
        "wrong_citation",
        "partial_answer",
        "hallucination",
        "grader_or_rubric_issue",
    ):
        if key in host_qa:
            qa[key] = host_qa[key]
    wrong_citations = case.get("wrong_citations", [])
    if wrong_citations:
        raw_host_fields["wrong_citations"] = wrong_citations
    attribution_request = _normalize_assertion_inputs(
        {
            "case_input": case_input,
            "judgement_evidence": judgement_evidence,
            "qa": qa,
            "host_agent": {"answer_claim": host_agent.get("answer_claim", [])},
            **({"raw_host_fields": raw_host_fields} if raw_host_fields else {}),
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "trace_lookup_failed",
        "log_id": args.log_id,
        "workspace_id": str(args.workspace_id),
        "app_id": app_id,
        "case": {
            "query": case_input.get("query", ""),
            "judgement": case_input.get("judgement", ""),
            "expected_knowledge_ids": case_input.get("expected_knowledge_ids", []),
            "host_agent": {
                "answer_claim": attribution_request.get("host_agent", {}).get("answer_claim", [])
                if isinstance(attribution_request.get("host_agent"), dict)
                else []
            },
            "wrong_citations": case.get("wrong_citations", []),
        },
        "ingest_summary": {
            "trace_completeness": {stage: "trace_lookup_failed" for stage in STAGE_ORDER},
            "suggested_probe_set": ["fetch-workflow-nodes", "replay-workflow"],
            "skip_reason": {},
            "host_action_required": [
                {
                    "action": "replay-workflow",
                    "reason": "trace lookup failed; use workflow replay fallback",
                    "priority": "P0",
                }
            ],
        },
        "raw_artifacts": {
            "trace_fetch_error": exc.to_payload(),
            "attribution_request": attribution_request,
        },
    }


def _cmd_ingest(args: argparse.Namespace) -> int:
    case = _read_case_file(args.case_file)
    validate_judgement_signals(case)
    try:
        trace_payload, meta = fetch_openplat_trace_detail(
            workspace_id=args.workspace_id,
            log_id=args.log_id,
            limit=args.limit,
            timeout_seconds=args.trace_timeout_seconds,
        )
        payload = build_ingest_output(
            workspace_id=args.workspace_id,
            log_id=args.log_id,
            app_id=args.app_id or "",
            case=case,
            trace_payload=trace_payload,
            fetch_meta=meta,
            raw=args.raw,
        )
    except V3Error as exc:
        if exc.error_code in {"E_TRACE_LOOKUP_FAILED", "E_TRACE_AUTH_REQUIRED"}:
            payload = _trace_failure_ingest(args, case, exc)
        else:
            raise
    paths = write_ingest_cache(payload, args.output_dir)
    payload.setdefault("raw_artifacts", {})["output_paths"] = paths
    _print_json(payload)
    return 0


def _cmd_orchestrate(args: argparse.Namespace) -> int:
    if args.schema_version != SCHEMA_VERSION:
        raise V3Error("E_SCHEMA_VERSION_MISMATCH", f"Only schema_version={SCHEMA_VERSION} is supported.", status_code=2)
    ingest = load_ingest(args.ingest_file, args.workspace_id, args.log_id)
    probes = load_probe_dir(args.probe_dir)
    only_stages = []
    for value in args.only_stages or []:
        only_stages.extend([item.strip() for item in value.split(",") if item.strip()])
    payload = orchestrate_v3(
        ingest=ingest,
        probes=probes,
        mode=args.mode,
        only_stages=only_stages or None,
    )
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "attribution_record.json", payload)
        report = payload.get("human_report_markdown") or ""
        (out / "case_report.md").write_text(str(report), encoding="utf-8")
        short = {
            "schema_version": SCHEMA_VERSION,
            "log_id": payload.get("log_id"),
            "workspace_id": payload.get("workspace_id"),
            "app_id": payload.get("app_id"),
            "primary_cause": payload.get("primary_cause"),
            "secondary_findings": payload.get("secondary_findings", {}),
            "failure_patterns": payload.get("failure_patterns", []),
            "needs_human_review": payload.get("needs_human_review"),
            "human_review_reasons": payload.get("human_review_reasons", []),
            "case_report_path": str(out / "case_report.md"),
        }
        write_json(out / "short_summary.json", short)
    _print_json(payload)
    return 0


def _common_probe_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in ("doc_ids", "claims", "titles", "judgement", "param_grid", "signals", "topk"):
        if hasattr(args, key):
            value = getattr(args, key)
            if value not in (None, "", []):
                params[key] = read_json_arg(value, value) if isinstance(value, str) else value
    if getattr(args, "rerank_tunable", False):
        params["rerank_tunable"] = True
    return params


def _cmd_probe(args: argparse.Namespace) -> int:
    ingest = load_ingest(args.ingest_file, args.workspace_id, args.log_id)
    payload = build_probe_result(
        args.command,
        ingest=ingest,
        params=_common_probe_params(args),
        no_cache=args.no_cache,
    )
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / f"{args.command}.json", payload)
    _print_json(payload)
    return 0


def _cmd_run_probe_plan(args: argparse.Namespace) -> int:
    ingest = load_ingest(args.ingest_file, args.workspace_id, args.log_id)
    plan = read_json_arg(args.plan, {})
    if not isinstance(plan, dict):
        raise V3Error("E_PROBE_PLAN_INVALID", "--plan must be a JSON object or @file.", status_code=2)
    payload = run_probe_plan(ingest=ingest, plan=plan, no_cache=args.no_cache)
    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "run-probe-plan.json", payload)
    _print_json(payload)
    return 0


def _cmd_fetch_workflow_nodes(args: argparse.Namespace) -> int:
    payload = fetch_workflow_nodes_v3(
        workspace_id=args.workspace_id,
        app_id=args.app_id,
        output_dir=args.output_dir,
    )
    _print_json(payload)
    return 0


async def _cmd_replay_workflow_async(args: argparse.Namespace) -> int:
    ingest = load_ingest(args.ingest_file, args.workspace_id, args.log_id)
    overrides = read_json_arg(args.override, {}) if args.override else {}
    if not isinstance(overrides, dict):
        raise V3Error("E_REPLAY_OVERRIDE_INVALID", "--override must be a JSON object or @file.", status_code=2)
    payload = await replay_workflow_v3(
        ingest=ingest,
        overrides=overrides,
        output_dir=args.output_dir,
        app_id=args.app_id,
        query=args.query,
    )
    _print_json(payload)
    return 0


def _cmd_replay_workflow(args: argparse.Namespace) -> int:
    return asyncio.run(_cmd_replay_workflow_async(args))


def _cmd_schema(args: argparse.Namespace) -> int:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "commands": {
            "ingest-fornax-trace": {
                "required": ["workspace_id", "log_id"],
                "optional": ["app_id", "case_file", "raw", "limit", "output_dir"],
            },
            "orchestrate": {
                "required": ["ingest_file or workspace_id+log_id"],
                "optional": ["probe_dir", "mode", "schema_version", "only_stages", "output_dir"],
            },
            "probes": PROBE_COMMANDS,
            "run-probe-plan": {
                "required": ["plan", "ingest_file or workspace_id+log_id"],
                "optional": ["output_dir", "no_cache"],
                "plan_schema_version": "probe-v1",
                "probe_optional_fields": ["display_name", "exp_kind", "trigger_source", "trigger_observation", "hypothesis"],
            },
            "fetch-workflow-nodes": {"required": ["workspace_id", "app_id"]},
            "replay-workflow": {
                "required": ["ingest_file or workspace_id+log_id"],
                "optional": ["app_id", "query", "override", "output_dir"],
                "exclusive": True,
            },
        },
        "stage_order": STAGE_ORDER,
        "cause_enum": sorted(CAUSE_ENUM),
        "output_contract": {
            "orchestrate_required": [
                "schema_version",
                "log_id",
                "workspace_id",
                "oracle_status",
                "case_assessment",
                "primary_cause",
                "secondary_findings",
                "failure_patterns",
                "needs_human_review",
                "human_review_reasons",
                "evidence_bundle",
                "evidence_chain",
                "next_actions",
                "telemetry",
                "deprecations",
                "raw_artifacts",
            ]
        },
    }
    _print_json(payload)
    return 0


def _add_ingest_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("ingest-fornax-trace", help="Fetch OpenPlat trace detail and emit v3 ingest evidence.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--app-id", default="")
    parser.add_argument("--case-file")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--trace-timeout-seconds", type=int, default=90)
    parser.add_argument("--output-dir")
    parser.set_defaults(func=_cmd_ingest)


def _add_orchestrate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("orchestrate", help="Merge v3 ingest/probe evidence and select primary cause.")
    parser.add_argument("--ingest-file")
    parser.add_argument("--workspace-id")
    parser.add_argument("--log-id")
    parser.add_argument("--probe-dir")
    parser.add_argument("--mode", choices=["preliminary", "final"], default="final")
    parser.add_argument("--schema-version", choices=[SCHEMA_VERSION], default=SCHEMA_VERSION)
    parser.add_argument("--only-stages", action="append")
    parser.add_argument("--output-dir")
    parser.set_defaults(func=_cmd_orchestrate)


def _add_probe_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], command: str) -> None:
    parser = subparsers.add_parser(command, help=f"Run v3 {command} probe.")
    parser.add_argument("--ingest-file")
    parser.add_argument("--workspace-id")
    parser.add_argument("--log-id")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-cache", action="store_true")
    if command in {"probe-knowledge-detail", "probe-permission-check", "probe-rerank-bypass", "probe-context-assembly"}:
        parser.add_argument("--doc-ids")
    if command == "probe-wide-recall":
        parser.add_argument("--topk", type=int, default=50)
    parser.set_defaults(func=_cmd_probe)


def _add_run_probe_plan_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("run-probe-plan", help="Execute a host-Agent probe-v1 plan and emit stage_signals evidence.")
    parser.add_argument("--ingest-file")
    parser.add_argument("--workspace-id")
    parser.add_argument("--log-id")
    parser.add_argument("--plan", required=True, help="probe-v1 plan JSON string or @file.")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-cache", action="store_true")
    parser.set_defaults(func=_cmd_run_probe_plan)


def _add_workflow_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    fetch = subparsers.add_parser("fetch-workflow-nodes", help="Fetch latest applications_wip.status=1 workflow nodes/edges/global_config.")
    fetch.add_argument("--workspace-id", required=True)
    fetch.add_argument("--app-id", required=True)
    fetch.add_argument("--output-dir")
    fetch.set_defaults(func=_cmd_fetch_workflow_nodes)

    replay = subparsers.add_parser("replay-workflow", help="Replay workflow only when trace lacks middle-node evidence.")
    replay.add_argument("--ingest-file")
    replay.add_argument("--workspace-id")
    replay.add_argument("--log-id")
    replay.add_argument("--app-id")
    replay.add_argument("--query")
    replay.add_argument("--override")
    replay.add_argument("--output-dir")
    replay.set_defaults(func=_cmd_replay_workflow)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FindReason RAG attribution CLI v3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_ingest_parser(subparsers)
    _add_orchestrate_parser(subparsers)
    for command in PROBE_COMMANDS:
        _add_probe_parser(subparsers, command)
    _add_run_probe_plan_parser(subparsers)
    _add_workflow_parsers(subparsers)
    schema = subparsers.add_parser("schema", help="Print v3 command/schema metadata.")
    schema.set_defaults(func=_cmd_schema)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return int(args.func(args) or 0)
    except V3Error as exc:
        print(json_dumps(exc.to_payload()), file=sys.stderr)
        return exc.status_code


if __name__ == "__main__":
    raise SystemExit(main())
