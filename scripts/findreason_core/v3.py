from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "v3-disabled"


class V3Error(RuntimeError):
    def __init__(self, error_code: str, message: str, *, status_code: int = 1, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


def _disabled(name: str) -> None:
    raise V3Error(
        "E_V3_ATTRIBUTION_DISABLED",
        (
            f"{name} belongs to the removed v3 hard-judgement path. "
            "Use findreason collect-evidence / run-experiment and let the Agent produce judgement."
        ),
        status_code=2,
    )


def orchestrate_v3(*_: Any, **__: Any) -> dict[str, Any]:
    _disabled("orchestrate_v3")


def run_probe_plan(*_: Any, **__: Any) -> dict[str, Any]:
    _disabled("run_probe_plan")


def build_probe_result(*_: Any, **__: Any) -> dict[str, Any]:
    _disabled("build_probe_result")


def build_ingest_output(*_: Any, **__: Any) -> dict[str, Any]:
    _disabled("build_ingest_output")
