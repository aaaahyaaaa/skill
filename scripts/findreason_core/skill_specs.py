from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .diagnostics import format_executable_diagnostic_specs


SKILL_SPEC_NAMES = (
    "input_adapter",
    "pipeline_replay",
    "wide_recall",
    "knowledge_detail",
    "reference_evidence",
    "query_preprocess",
    "retrieval",
    "rerank_context",
    "answer_faithfulness",
    "evaluator_rubric",
    "orchestrator",
)

SPEC_DIR = Path(__file__).parent / "skills" / "specs"


@lru_cache
def load_skill_spec(name: str) -> str:
    spec_path = SPEC_DIR / f"{name}.md"
    return spec_path.read_text(encoding="utf-8").strip()


def format_skill_specs_for_prompt(names: Iterable[str] = SKILL_SPEC_NAMES) -> str:
    _ = tuple(names)
    return format_executable_diagnostic_specs()
