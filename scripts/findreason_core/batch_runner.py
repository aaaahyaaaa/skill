from __future__ import annotations

import csv
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence_kernel import collect_evidence, json_dumps, read_json_file, write_json
from .experiments import run_experiment
from .reporting import synthesize_brief


BATCH_SCHEMA_VERSION = "findreason-batch-v1"
DEFAULT_OUTPUT_ROOT = Path("/Users/bytedance/Documents/findreason-rag-attribution-runs")
DEFAULT_TRACE_ROOT = Path("/Users/bytedance/Documents/New project 2/trace_spans")
DEFAULT_BATCH_ID = "20260613_lark_OfEB_246"
DEFAULT_APP_ID = "1001883"
DEFAULT_SHEET_RANGE = "A:BB"
TRACE_MISSING_STATUS = "trace_missing_or_empty"
FINAL_CAUSES = {
    "workflow_input_loss",
    "suspected_knowledge_missing",
    "retrieval_miss",
    "rerank_drop",
    "answer_failure",
}
BADCASE_REVIEW_STATUSES = {
    "valid_badcase",
    "needs_human_review_evaluator_disputed",
    "not_badcase_evaluator_error",
}


class BatchRunnerError(Exception):
    def __init__(self, error_code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class BatchRunConfig:
    trace_root: Path
    output_root: Path = DEFAULT_OUTPUT_ROOT
    batch_id: str = DEFAULT_BATCH_ID
    cases: list[dict[str, Any]] | None = None
    sheet_token: str | None = None
    sheet_id: str | None = None
    start_row: int = 3
    end_row: int = 248
    app_id: str = DEFAULT_APP_ID
    dry_run: bool = False
    judgement_mode: str = "draft"
    repo_root: Path | None = None
    allow_repo_output: bool = False
    lark_cli: str = "lark-cli"
    lark_as: str = "user"
    window_size: int = 10
    run_recall: bool = True
    run_rerank: bool = True
    run_replay: bool = True
    synthesize: bool = True
    experiment_timeout_seconds: int = 90
    trace_timeout_seconds: int = 90
    force: bool = False

    def __post_init__(self) -> None:
        self.trace_root = Path(self.trace_root)
        self.output_root = Path(self.output_root)
        if self.repo_root is not None:
            self.repo_root = Path(self.repo_root)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def guard_output_root(output_root: Path, *, repo_root: Path | None = None, allow_repo_output: bool = False) -> Path:
    resolved = _resolve(output_root)
    if repo_root and not allow_repo_output:
        repo = _resolve(repo_root)
        if resolved == repo or _is_relative_to(resolved, repo):
            raise BatchRunnerError(
                "E_OUTPUT_ROOT_IN_REPO",
                "Refusing to write batch artifacts inside the repository.",
                details={"output_root": str(resolved), "repo_root": str(repo)},
            )
    return resolved


def _safe_component(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
    return text[:120] or "unknown"


def case_dir_name(case: dict[str, Any]) -> str:
    row = int(case.get("row_number") or case.get("source_row") or 0)
    return f"row_{row:03d}_{_safe_component(case.get('log_id'))}"


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], *, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized: dict[str, Any] = {}
            for key in fieldnames:
                value = row.get(key, "")
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, default=str)
                serialized[key] = value
            writer.writerow(serialized)


def _trace_file_for(trace_root: Path, log_id: str) -> Path:
    return trace_root / "spans" / f"{log_id}.json"


def _walk_for_spans(value: Any) -> list[Any]:
    if isinstance(value, dict):
        spans = value.get("spans")
        if isinstance(spans, list):
            return spans
        for child in value.values():
            found = _walk_for_spans(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_for_spans(child)
            if found:
                return found
    return []


def trace_status(trace_root: Path, log_id: str) -> tuple[str, Path | None, int]:
    trace_file = _trace_file_for(trace_root, log_id)
    if not trace_file.exists():
        return TRACE_MISSING_STATUS, None, 0
    try:
        payload = read_json_file(trace_file)
    except Exception:
        return TRACE_MISSING_STATUS, trace_file, 0
    spans = _walk_for_spans(payload)
    if not spans:
        return TRACE_MISSING_STATUS, trace_file, 0
    return "trace_available", trace_file, len(spans)


def _compact_jsonish(value: Any, *, limit: int = 1200) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value[:limit]
    return json.dumps(value, ensure_ascii=False, default=str)[:limit]


def case_payload(row: dict[str, Any], *, app_id: str = DEFAULT_APP_ID) -> dict[str, Any]:
    query = str(row.get("用户原问") or row.get("query") or "").strip()
    evaluator = {
        "raw": row.get("知识问答评估器判断结果", ""),
        "factual_result": row.get("[评估器]事实正确性判断结果", ""),
        "factual_score": row.get("事实正确性score", ""),
        "factual_human_machine_consistency": row.get("事实正确性人机一致", ""),
        "weight_note": "Evaluator factual correctness is a low-confidence symptom signal only; final cause must be trace-grounded.",
    }
    manual = {
        "factual_correctness": row.get("[标注]事实正确性", ""),
        "attribution_stage": row.get("归因阶段", ""),
        "main_cause": row.get("主因", ""),
    }
    return {
        "case_id": str(row.get("题目ID") or row.get("case_id") or ""),
        "source_row": str(row.get("row_number") or row.get("source_row") or ""),
        "message_id": str(row.get("message_id") or ""),
        "log_id": str(row.get("log_id") or ""),
        "workspace_id": str(row.get("workspace_id") or ""),
        "app_id": str(row.get("app_id") or app_id or ""),
        "query": query,
        "query_hint": _compact_jsonish(row.get("query_list")),
        "answer_hint": _compact_jsonish(row.get("agent_reply"), limit=3000),
        "chat_history": _compact_jsonish(row.get("chat_history"), limit=5000),
        "business_line": str(row.get("business_line") or ""),
        "judgement": json.dumps({"evaluator_signal": evaluator, "manual_label": manual}, ensure_ascii=False),
        "evaluator_signal": evaluator,
        "manual_label": manual,
    }


def _run_lark_json(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise BatchRunnerError(
            "E_LARK_READ_FAILED",
            "lark-cli failed while reading spreadsheet rows.",
            details={"cmd": args, "stderr": completed.stderr[-1000:], "stdout": completed.stdout[-1000:]},
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BatchRunnerError(
            "E_LARK_READ_FAILED",
            "lark-cli returned non-JSON output.",
            details={"cmd": args, "stdout": completed.stdout[-1000:]},
        ) from exc
    if not payload.get("ok"):
        raise BatchRunnerError("E_LARK_READ_FAILED", "lark-cli returned ok=false.", details={"cmd": args, "payload": payload})
    return payload


def _a1_range(start: int, end: int) -> str:
    return f"A{start}:BB{end}"


def _fetch_lark_rows(
    *,
    sheet_token: str,
    sheet_id: str,
    start_row: int,
    end_row: int,
    lark_cli: str,
    lark_as: str,
    window_size: int,
) -> list[dict[str, Any]]:
    header_payload = _run_lark_json(
        [
            lark_cli,
            "sheets",
            "+csv-get",
            "--spreadsheet-token",
            sheet_token,
            "--sheet-id",
            sheet_id,
            "--range",
            "A2:BB2",
            "--rows-json",
            "--max-chars",
            "200000",
            "--as",
            lark_as,
            "--format",
            "json",
        ]
    )
    header_rows = header_payload.get("data", {}).get("rows") or []
    if not header_rows:
        raise BatchRunnerError("E_LARK_HEADER_MISSING", "Spreadsheet header row A2:BB2 was empty.")
    header_values = header_rows[0].get("values") or {}
    column_to_field = {col: str(name) for col, name in header_values.items() if str(name).strip()}
    rows: list[dict[str, Any]] = []
    current = int(start_row)
    while current <= end_row:
        window_end = min(int(end_row), current + max(int(window_size), 1) - 1)
        payload = _run_lark_json(
            [
                lark_cli,
                "sheets",
                "+csv-get",
                "--spreadsheet-token",
                sheet_token,
                "--sheet-id",
                sheet_id,
                "--range",
                _a1_range(current, window_end),
                "--rows-json",
                "--max-chars",
                "1000000",
                "--as",
                lark_as,
                "--format",
                "json",
            ]
        )
        for raw_row in payload.get("data", {}).get("rows") or []:
            values = raw_row.get("values") or {}
            mapped = {"row_number": int(raw_row.get("row_number") or 0)}
            for col, field_name in column_to_field.items():
                mapped[field_name] = values.get(col, "")
            rows.append(_canonical_row(mapped))
        current = window_end + 1
    return rows


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(row)
    if not canonical.get("log_id"):
        canonical["log_id"] = canonical.get("log_id") or canonical.get("F") or canonical.get("日志ID") or ""
    if not canonical.get("workspace_id"):
        canonical["workspace_id"] = canonical.get("workspaceId") or canonical.get("G") or ""
    return canonical


def load_cases(config: BatchRunConfig) -> list[dict[str, Any]]:
    if config.cases is not None:
        return [_canonical_row(dict(case)) for case in config.cases]
    if not config.sheet_token or not config.sheet_id:
        raise BatchRunnerError("E_CASE_SOURCE_REQUIRED", "Provide cases or sheet_token + sheet_id.")
    return _fetch_lark_rows(
        sheet_token=config.sheet_token,
        sheet_id=config.sheet_id,
        start_row=config.start_row,
        end_row=config.end_row,
        lark_cli=config.lark_cli,
        lark_as=config.lark_as,
        window_size=config.window_size,
    )


def build_case_index(cases: list[dict[str, Any]], *, trace_root: Path, batch_dir: Path, app_id: str) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for raw in cases:
        row = _canonical_row(raw)
        log_id = str(row.get("log_id") or "").strip()
        status, trace_file, span_count = trace_status(trace_root, log_id)
        payload = case_payload(row, app_id=app_id)
        entry = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "row_number": int(row.get("row_number") or 0),
            "case_id": payload.get("case_id", ""),
            "message_id": payload.get("message_id", ""),
            "log_id": log_id,
            "workspace_id": str(row.get("workspace_id") or ""),
            "app_id": str(row.get("app_id") or app_id or ""),
            "query": payload.get("query", ""),
            "business_line": payload.get("business_line", ""),
            "trace_status": status,
            "trace_file": str(trace_file) if trace_file else "",
            "span_count": span_count,
            "case_dir": str(batch_dir / case_dir_name({"row_number": row.get("row_number"), "log_id": log_id})),
            "case_payload": payload,
        }
        index.append(entry)
    return index


def _empty_summary(total_cases: int) -> dict[str, int]:
    return {
        "total_cases": total_cases,
        "trace_available": 0,
        "trace_missing_or_empty": 0,
        "ok": 0,
        "failed": 0,
        "pending_agent_judgement": 0,
    }


def _summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary = _empty_summary(len(rows))
    for row in rows:
        if row.get("trace_status") == "trace_available":
            summary["trace_available"] += 1
        else:
            summary["trace_missing_or_empty"] += 1
        status = str(row.get("status") or "")
        if status == "ok":
            summary["ok"] += 1
        elif status == "failed":
            summary["failed"] += 1
        elif status == "pending_agent_judgement":
            summary["pending_agent_judgement"] += 1
    return summary


def _batch_markdown(summary: dict[str, Any], rows: list[dict[str, Any]], *, batch_dir: Path, dry_run: bool) -> str:
    missing = [row for row in rows if row.get("trace_status") != "trace_available"]
    lines = [
        "# FindReason Batch Summary",
        "",
        f"- batch_dir: `{batch_dir}`",
        f"- generated_at: `{_now()}`",
        f"- mode: `{'dry-run' if dry_run else 'executed'}`",
        "",
        "## Counts",
        "",
        "| metric | count |",
        "|---|---:|",
    ]
    for key in [
        "total_cases",
        "trace_available",
        "trace_missing_or_empty",
        "ok",
        "failed",
        "pending_agent_judgement",
    ]:
        lines.append(f"| {key} | {summary.get(key, 0)} |")
    if missing:
        lines.extend(["", "## Missing Trace Cases", "", "| row | log_id | query |", "|---:|---|---|"])
        for row in missing[:50]:
            query = str(row.get("query") or "").replace("|", "\\|")[:120]
            lines.append(f"| {row.get('row_number')} | `{row.get('log_id')}` | {query} |")
    lines.append("")
    return "\n".join(lines)


def _write_batch_outputs(
    *,
    batch_dir: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    dry_run: bool,
) -> None:
    _write_jsonl(batch_dir / "cases_index.jsonl", rows)
    _write_jsonl(batch_dir / "missing_trace_cases.jsonl", [row for row in rows if row.get("trace_status") != "trace_available"])
    write_json(
        batch_dir / "batch_summary.json",
        {
            "schema_version": BATCH_SCHEMA_VERSION,
            "generated_at": _now(),
            "dry_run": dry_run,
            "batch_dir": str(batch_dir),
            "summary": summary,
            "rows": rows,
        },
    )
    _write_csv(
        batch_dir / "batch_summary.csv",
        rows,
        fieldnames=[
            "row_number",
            "log_id",
            "workspace_id",
            "app_id",
            "business_line",
            "trace_status",
            "span_count",
            "status",
            "primary_cause",
            "confidence",
            "needs_human_review",
            "badcase_review_status",
            "human_review_reason",
            "human_review_context",
            "case_dir",
        ],
    )
    (batch_dir / "batch_summary.md").write_text(
        _batch_markdown(summary, rows, batch_dir=batch_dir, dry_run=dry_run),
        encoding="utf-8",
    )
    _write_jsonl(batch_dir / "eval_dataset.jsonl", [_eval_dataset_row(row) for row in rows])


def _eval_dataset_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "row_number": row.get("row_number"),
        "log_id": row.get("log_id"),
        "workspace_id": row.get("workspace_id"),
        "app_id": row.get("app_id"),
        "query": row.get("query"),
        "business_line": row.get("business_line"),
        "trace_status": row.get("trace_status"),
        "primary_cause": row.get("primary_cause", ""),
        "confidence": row.get("confidence", ""),
        "needs_human_review": row.get("needs_human_review", ""),
        "badcase_review_status": row.get("badcase_review_status", ""),
        "human_review_reason": row.get("human_review_reason", ""),
        "human_review_context": row.get("human_review_context", ""),
        "case_dir": row.get("case_dir"),
    }


def _progress(batch_dir: Path, event: str, **kwargs: Any) -> None:
    _append_jsonl(batch_dir / "progress.jsonl", {"ts": _now(), "event": event, **kwargs})


def _write_run_status(case_dir: Path, status: dict[str, Any]) -> None:
    write_json(case_dir / "run_status.json", status)


def _existing_judgement(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "judgement_result.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _row_with_judgement(row: dict[str, Any]) -> dict[str, Any]:
    case_dir = Path(str(row.get("case_dir") or ""))
    judgement = _existing_judgement(case_dir)
    if not judgement:
        return row
    enriched = dict(row)
    enriched["primary_cause"] = judgement.get("primary_cause", "")
    enriched["confidence"] = judgement.get("confidence", "")
    enriched["needs_human_review"] = judgement.get("needs_human_review", "")
    enriched["badcase_review_status"] = judgement.get("badcase_review_status", "")
    enriched["human_review_reason"] = judgement.get("human_review_reason", "")
    enriched["human_review_context"] = judgement.get("human_review_context", "")
    if enriched.get("status") == "pending_agent_judgement":
        enriched["status"] = "ok"
    return enriched


def _run_case(entry: dict[str, Any], *, config: BatchRunConfig) -> dict[str, Any]:
    case_dir = Path(str(entry["case_dir"]))
    if case_dir.exists() and (case_dir / "run_status.json").exists() and not config.force:
        status = read_json_file(case_dir / "run_status.json")
        row = dict(entry)
        row["status"] = status.get("status", "ok")
        return _row_with_judgement(row)
    case_dir.mkdir(parents=True, exist_ok=True)
    case = dict(entry["case_payload"])
    write_json(case_dir / "case.json", case)
    if entry.get("trace_status") != "trace_available":
        status = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "status": TRACE_MISSING_STATUS,
            "row_number": entry.get("row_number"),
            "log_id": entry.get("log_id"),
            "trace_file": entry.get("trace_file", ""),
            "notes": "Local trace JSON is missing or has no spans; keep this case in eval dataset but do not force trace-grounded cause.",
        }
        _write_run_status(case_dir, status)
        row = dict(entry)
        row["status"] = TRACE_MISSING_STATUS
        return row
    try:
        _progress(case_dir.parent, "collect_start", row_number=entry.get("row_number"), log_id=entry.get("log_id"))
        collect_evidence(
            workspace_id=str(entry.get("workspace_id") or case.get("workspace_id") or ""),
            log_id=str(entry.get("log_id") or ""),
            app_id=str(entry.get("app_id") or case.get("app_id") or config.app_id),
            case_file=str(case_dir / "case.json"),
            trace_file=str(entry.get("trace_file") or ""),
            output_dir=str(case_dir),
            timeout_seconds=config.trace_timeout_seconds,
        )
        facts_file = case_dir / "case_facts.json"
        query = str(case.get("query") or entry.get("query") or "")
        if config.run_recall:
            run_experiment(
                experiment_type="recall",
                facts_file=str(facts_file),
                output_dir=str(case_dir),
                query=query,
                timeout_seconds=config.experiment_timeout_seconds,
            )
        if config.run_rerank:
            run_experiment(experiment_type="rerank", facts_file=str(facts_file), output_dir=str(case_dir))
        if config.run_replay:
            run_experiment(
                experiment_type="replay",
                facts_file=str(facts_file),
                output_dir=str(case_dir),
                query=query,
                app_id=str(entry.get("app_id") or config.app_id),
                timeout_seconds=config.experiment_timeout_seconds,
            )
        if config.synthesize:
            synthesize_brief(facts_file=str(facts_file), experiment_dir=str(case_dir), output_dir=str(case_dir))
        status_value = "pending_agent_judgement" if config.judgement_mode == "agent" else "ok"
        status = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "status": status_value,
            "row_number": entry.get("row_number"),
            "log_id": entry.get("log_id"),
            "case_dir": str(case_dir),
            "judgement_mode": config.judgement_mode,
            "outputs": [
                "case.json",
                "case_facts.json",
                "recall_experiment.json",
                "rerank_experiment.json",
                "replay_experiment.json",
                "evidence_index.json",
                "agent_judgement.md",
            ],
        }
        _write_run_status(case_dir, status)
        row = dict(entry)
        row["status"] = status_value
        return _row_with_judgement(row)
    except Exception as exc:
        status = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "status": "failed",
            "row_number": entry.get("row_number"),
            "log_id": entry.get("log_id"),
            "error": repr(exc),
        }
        _write_run_status(case_dir, status)
        row = dict(entry)
        row["status"] = "failed"
        row["error"] = repr(exc)
        return row


def run_batch(config: BatchRunConfig) -> dict[str, Any]:
    repo_root = config.repo_root or Path.cwd()
    output_root = guard_output_root(config.output_root, repo_root=repo_root, allow_repo_output=config.allow_repo_output)
    batch_dir = output_root / config.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases(config)
    index_rows = build_case_index(cases, trace_root=config.trace_root, batch_dir=batch_dir, app_id=config.app_id)
    if config.dry_run:
        rows = [dict(row, status="dry_run") for row in index_rows]
        summary = _summary_from_rows(rows)
        summary["ok"] = 0
        summary["failed"] = 0
        summary["pending_agent_judgement"] = 0
        _write_batch_outputs(batch_dir=batch_dir, rows=rows, summary=summary, dry_run=True)
        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "status": "ok",
            "dry_run": True,
            "batch_dir": str(batch_dir),
            "summary": summary,
        }

    rows: list[dict[str, Any]] = []
    for entry in index_rows:
        row = _run_case(entry, config=config)
        rows.append(row)
        _progress(batch_dir, "case_done", row_number=row.get("row_number"), log_id=row.get("log_id"), status=row.get("status"))
    summary = _summary_from_rows(rows)
    _write_batch_outputs(batch_dir=batch_dir, rows=rows, summary=summary, dry_run=False)
    return {
        "schema_version": BATCH_SCHEMA_VERSION,
        "status": "ok",
        "dry_run": False,
        "batch_dir": str(batch_dir),
        "summary": summary,
    }


def summarize_batch(batch_dir: Path) -> dict[str, Any]:
    batch_dir = Path(batch_dir)
    rows: list[dict[str, Any]] = []
    index_path = batch_dir / "cases_index.jsonl"
    if not index_path.exists():
        raise BatchRunnerError("E_BATCH_INDEX_MISSING", "cases_index.jsonl does not exist.", details={"batch_dir": str(batch_dir)})
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        status_path = Path(str(row.get("case_dir") or "")) / "run_status.json"
        if status_path.exists():
            status = read_json_file(status_path)
            row["status"] = status.get("status", "")
        row = _row_with_judgement(row)
        rows.append(row)
    summary = _summary_from_rows(rows)
    distribution: dict[str, int] = {}
    confidence_distribution: dict[str, int] = {}
    badcase_review_status_distribution: dict[str, int] = {}
    business_distribution: dict[str, int] = {}
    for row in rows:
        cause = str(row.get("primary_cause") or "")
        if cause:
            distribution[cause] = distribution.get(cause, 0) + 1
        confidence = str(row.get("confidence") or "")
        if confidence:
            confidence_distribution[confidence] = confidence_distribution.get(confidence, 0) + 1
        badcase_review_status = str(row.get("badcase_review_status") or "")
        if badcase_review_status:
            badcase_review_status_distribution[badcase_review_status] = (
                badcase_review_status_distribution.get(badcase_review_status, 0) + 1
            )
        business = str(row.get("business_line") or "")
        if business:
            business_distribution[business] = business_distribution.get(business, 0) + 1
    payload = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "status": "ok",
        "generated_at": _now(),
        "batch_dir": str(batch_dir),
        "summary": summary,
        "cause_distribution": distribution,
        "confidence_distribution": confidence_distribution,
        "badcase_review_status_distribution": badcase_review_status_distribution,
        "business_distribution": business_distribution,
        "rows": rows,
    }
    _write_batch_outputs(batch_dir=batch_dir, rows=rows, summary=summary, dry_run=False)
    write_json(batch_dir / "batch_summary.json", payload)
    return payload


def print_batch_result(value: dict[str, Any]) -> None:
    print(json_dumps(value))
