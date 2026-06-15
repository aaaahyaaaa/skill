#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from findreason_core.batch_runner import BatchRunConfig, BatchRunnerError, run_batch, summarize_batch
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


def _cmd_batch_run(args: argparse.Namespace) -> int:
    payload = run_batch(
        BatchRunConfig(
            sheet_token=args.sheet_token,
            sheet_id=args.sheet_id,
            trace_root=Path(args.trace_root),
            output_root=Path(args.output_root),
            batch_id=args.batch_id,
            start_row=args.start_row,
            end_row=args.end_row,
            app_id=args.app_id,
            dry_run=args.dry_run,
            judgement_mode=args.judgement_mode,
            repo_root=Path(__file__).resolve().parents[1],
            allow_repo_output=args.allow_repo_output,
            window_size=args.window_size,
            run_recall=not args.skip_recall,
            run_rerank=not args.skip_rerank,
            run_replay=not args.skip_replay,
            synthesize=not args.skip_synthesize,
            experiment_timeout_seconds=args.experiment_timeout_seconds,
            trace_timeout_seconds=args.trace_timeout_seconds,
            force=args.force,
        )
    )
    _print_json(payload)
    return 0


def _cmd_batch_summarize(args: argparse.Namespace) -> int:
    payload = summarize_batch(Path(args.batch_dir))
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


def _add_batch_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "batch-run",
        help="Run a reusable Lark-sheet badcase batch against local trace files outside the repo.",
    )
    parser.add_argument("--sheet-token", required=True)
    parser.add_argument("--sheet-id", required=True)
    parser.add_argument("--trace-root", required=True)
    parser.add_argument("--output-root", default="/Users/bytedance/Documents/findreason-rag-attribution-runs")
    parser.add_argument("--batch-id", default="20260613_lark_OfEB_246")
    parser.add_argument("--start-row", type=int, default=3)
    parser.add_argument("--end-row", type=int, default=248)
    parser.add_argument("--app-id", default="1001883")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--judgement-mode", choices=["draft", "agent"], default="draft")
    parser.add_argument("--allow-repo-output", action="store_true")
    parser.add_argument("--window-size", type=int, default=10)
    parser.add_argument("--skip-recall", action="store_true")
    parser.add_argument("--skip-rerank", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--skip-synthesize", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--experiment-timeout-seconds", type=int, default=90)
    parser.add_argument("--trace-timeout-seconds", type=int, default=90)
    parser.set_defaults(func=_cmd_batch_run)


def _add_batch_summarize_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "batch-summarize",
        help="Regenerate batch summary files from run_status.json and judgement_result.json outputs.",
    )
    parser.add_argument("--batch-dir", required=True)
    parser.set_defaults(func=_cmd_batch_summarize)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="findreason",
        description="FindReason agent-judgement v4 evidence kernel. Code emits facts and experiments; Agent writes judgement.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_collect_evidence_parser(subparsers)
    _add_run_experiment_parser(subparsers)
    _add_synthesize_brief_parser(subparsers)
    _add_batch_run_parser(subparsers)
    _add_batch_summarize_parser(subparsers)
    schema = subparsers.add_parser("schema", help="Print v4 evidence-kernel schema metadata.")
    schema.set_defaults(func=_cmd_schema)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except BatchRunnerError as exc:
        _print_json(exc.to_payload())
        return 1
    except EvidenceKernelError as exc:
        _print_json(exc.to_payload())
        return 1


if __name__ == "__main__":
    sys.exit(main())
