#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any

from findreason_core.evidence_kernel import (
    EvidenceKernelError,
    collect_evidence,
    json_dumps,
    schema_payload,
)
from findreason_core.experiments import run_experiment
from findreason_core.reporting import synthesize_brief


def _print_json(value: Any) -> None:
    print(json_dumps(value))


def _cmd_collect_evidence(args: argparse.Namespace) -> int:
    payload = collect_evidence(
        workspace_id=args.workspace_id,
        log_id=args.log_id,
        app_id=args.app_id or "",
        case_file=args.case_file,
        trace_file=args.trace_file,
        output_dir=args.output_dir,
        limit=args.limit,
        timeout_seconds=args.trace_timeout_seconds,
    )
    _print_json(payload)
    return 0


def _cmd_run_experiment(args: argparse.Namespace) -> int:
    payload = run_experiment(
        experiment_type=args.type,
        facts_file=args.facts_file,
        output_dir=args.output_dir,
        query=args.query,
        app_id=args.app_id,
        target_doc_ids=args.target_doc_id,
        timeout_seconds=args.timeout_seconds,
    )
    _print_json(payload)
    return 0


def _cmd_schema(_: argparse.Namespace) -> int:
    _print_json(schema_payload())
    return 0


def _cmd_synthesize_brief(args: argparse.Namespace) -> int:
    payload = synthesize_brief(
        facts_file=args.facts_file,
        output_dir=args.output_dir,
        experiment_dir=args.experiment_dir,
    )
    _print_json(payload)
    return 0


def _add_collect_evidence_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("collect-evidence", help="Fetch/read trace and emit v4 case facts for Agent judgement.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--app-id", default="")
    parser.add_argument("--case-file")
    parser.add_argument("--trace-file", help="Use a local trace JSON file instead of fetching OpenPlat trace detail.")
    parser.add_argument("--output-dir")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--trace-timeout-seconds", type=int, default=90)
    parser.set_defaults(func=_cmd_collect_evidence)


def _add_run_experiment_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("run-experiment", help="Run or plan a v4 recall/rerank/replay experiment.")
    parser.add_argument("--type", choices=["recall", "rerank", "replay"], required=True)
    parser.add_argument("--facts-file", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--query", help="Concrete query override for recall or replay.")
    parser.add_argument("--app-id", help="Concrete app id override for replay.")
    parser.add_argument("--target-doc-id", action="append", help="Doc id to observe in rerank survival; can be repeated.")
    parser.add_argument("--timeout-seconds", type=int, default=90, help="HTTP timeout for live experiment calls.")
    parser.set_defaults(func=_cmd_run_experiment)


def _add_synthesize_brief_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "synthesize-brief",
        help="Write a concise v4 judgement summary and local evidence index from facts and experiments.",
    )
    parser.add_argument("--facts-file", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--experiment-dir",
        help="Directory containing recall_experiment.json, rerank_experiment.json, and replay_experiment.json. Defaults to facts-file directory.",
    )
    parser.set_defaults(func=_cmd_synthesize_brief)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="findreason",
        description="FindReason agent-judgement v4 evidence kernel. Code emits facts and experiments; Agent writes judgement.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_collect_evidence_parser(subparsers)
    _add_run_experiment_parser(subparsers)
    _add_synthesize_brief_parser(subparsers)
    schema = subparsers.add_parser("schema", help="Print v4 evidence-kernel schema metadata.")
    schema.set_defaults(func=_cmd_schema)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except EvidenceKernelError as exc:
        _print_json(exc.to_payload())
        return 1


if __name__ == "__main__":
    sys.exit(main())
