from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .models import AgentTraceStep, AttributionRequest, AttributionResponse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured = os.getenv("FINDREASON_RUN_DB")
    if configured:
        return Path(configured)
    return Path.home() / ".findreason" / "runs.sqlite3"


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attribution_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            request_json TEXT NOT NULL,
            response_json TEXT,
            error TEXT
        )
        """
    )
    _migrate_runs_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attribution_steps (
            run_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            planner_reason TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, step_index)
        )
        """
    )
    conn.commit()


def _migrate_runs_schema(conn: sqlite3.Connection) -> None:
    return


def create_run(request: AttributionRequest) -> str:
    run_id = str(uuid4())
    timestamp = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO attribution_runs (
                run_id, status, created_at, updated_at, request_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, "running", timestamp, timestamp, _json(request)),
        )
        conn.commit()
    return run_id


def record_step(run_id: str | None, step: AgentTraceStep) -> None:
    if not run_id:
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO attribution_steps (
                run_id, step_index, tool_name, status, summary, planner_reason,
                duration_ms, input_json, output_json, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step.step_index,
                step.tool_name,
                step.status,
                step.summary,
                step.planner_reason,
                step.duration_ms,
                _json(step.input),
                _json(step.output),
                step.error,
                _now(),
            ),
        )
        conn.execute(
            "UPDATE attribution_runs SET updated_at = ? WHERE run_id = ?",
            (_now(), run_id),
        )
        conn.commit()


def finish_run(run_id: str | None, response: AttributionResponse) -> None:
    if not run_id:
        return
    with _connect() as conn:
        conn.execute(
            """
            UPDATE attribution_runs
            SET status = ?, updated_at = ?, response_json = ?, error = NULL
            WHERE run_id = ?
            """,
            ("completed", _now(), _json(response), run_id),
        )
        conn.commit()


def fail_run(run_id: str | None, error: str) -> None:
    if not run_id:
        return
    with _connect() as conn:
        conn.execute(
            """
            UPDATE attribution_runs
            SET status = ?, updated_at = ?, error = ?
            WHERE run_id = ?
            """,
            ("failed", _now(), error[:1000], run_id),
        )
        conn.commit()


def _loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        run = conn.execute(
            "SELECT * FROM attribution_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            return None
        steps = conn.execute(
            """
            SELECT * FROM attribution_steps
            WHERE run_id = ?
            ORDER BY step_index ASC
            """,
            (run_id,),
        ).fetchall()
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "request": _loads(run["request_json"]),
        "response": _loads(run["response_json"]),
        "error": run["error"],
        "steps": [
            {
                "step_index": step["step_index"],
                "tool_name": step["tool_name"],
                "status": step["status"],
                "summary": step["summary"],
                "planner_reason": step["planner_reason"],
                "duration_ms": step["duration_ms"],
                "input": _loads(step["input_json"]),
                "output": _loads(step["output_json"]),
                "error": step["error"],
                "created_at": step["created_at"],
            }
            for step in steps
        ],
    }
