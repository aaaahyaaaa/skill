from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))


def _case(row_number: int, log_id: str, query: str = "如何查看计划赔付金额") -> dict[str, object]:
    return {
        "row_number": row_number,
        "题目ID": f"qid-{row_number}",
        "message_id": f"mid-{row_number}",
        "log_id": log_id,
        "workspace_id": "138",
        "用户原问": query,
        "query_list": json.dumps([{"content": query}], ensure_ascii=False),
        "agent_reply": json.dumps([{"content": "历史答案"}], ensure_ascii=False),
        "chat_history": json.dumps([{"role": "user", "content": query}], ensure_ascii=False),
        "business_line": "千川",
        "知识问答评估器判断结果": json.dumps({"factual_correctness": {"score": 0}}, ensure_ascii=False),
        "[标注]事实正确性": "否",
        "[评估器]事实正确性判断结果": "否",
        "事实正确性score": "0",
        "事实正确性人机一致": "是",
        "归因阶段": "",
        "主因": "",
    }


def _trace_file(trace_root: Path, log_id: str, *, spans: list[dict[str, object]] | None = None) -> Path:
    target = trace_root / "spans" / f"{log_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"data": {"logId": log_id, "spans": [{"span_id": "s"}] if spans is None else spans}}),
        encoding="utf-8",
    )
    return target


def test_batch_dry_run_writes_index_without_case_dirs(tmp_path: Path) -> None:
    from findreason_core.batch_runner import BatchRunConfig, run_batch

    trace_root = tmp_path / "trace_spans"
    _trace_file(trace_root, "log-ok")
    output_root = tmp_path / "external-runs"

    result = run_batch(
        BatchRunConfig(
            cases=[_case(3, "log-ok"), _case(4, "log-missing")],
            trace_root=trace_root,
            output_root=output_root,
            batch_id="unit_batch",
            dry_run=True,
            repo_root=SKILL_ROOT,
        )
    )

    batch_dir = output_root / "unit_batch"
    assert result["summary"]["total_cases"] == 2
    assert result["summary"]["trace_available"] == 1
    assert result["summary"]["trace_missing_or_empty"] == 1
    assert (batch_dir / "cases_index.jsonl").exists()
    assert (batch_dir / "missing_trace_cases.jsonl").exists()
    assert not (batch_dir / "row_003_log-ok").exists()
    assert not (batch_dir / "row_004_log-missing").exists()


def test_batch_rejects_output_root_inside_repo(tmp_path: Path) -> None:
    from findreason_core.batch_runner import BatchRunConfig, BatchRunnerError, run_batch

    trace_root = tmp_path / "trace_spans"
    _trace_file(trace_root, "log-ok")

    with pytest.raises(BatchRunnerError) as exc:
        run_batch(
            BatchRunConfig(
                cases=[_case(3, "log-ok")],
                trace_root=trace_root,
                output_root=SKILL_ROOT / "runs" / "bad-place",
                batch_id="unit_batch",
                dry_run=True,
                repo_root=SKILL_ROOT,
            )
        )

    assert exc.value.error_code == "E_OUTPUT_ROOT_IN_REPO"


def test_case_payload_serializes_source_row_for_collect_evidence() -> None:
    from findreason_core.batch_runner import case_payload
    from findreason_core.evidence_kernel import build_case_facts

    payload = case_payload(_case(7, "log-ok"))

    assert payload["source_row"] == "7"
    assert payload["judgement"]
    assert payload["evaluator_signal"]["factual_score"] == "0"
    assert payload["manual_label"]["factual_correctness"] == "否"

    facts = build_case_facts(
        workspace_id="138",
        log_id="log-ok",
        app_id="1001883",
        case=payload,
        trace_payload={"data": {"spans": []}},
        trace_meta={"source": "unit"},
    )

    assert facts["case"]["judgement"] == payload["judgement"]


def test_batch_summary_counts_match_input_cases(tmp_path: Path) -> None:
    from findreason_core.batch_runner import BatchRunConfig, run_batch

    trace_root = tmp_path / "trace_spans"
    _trace_file(trace_root, "log-ok")
    _trace_file(trace_root, "log-empty", spans=[])

    result = run_batch(
        BatchRunConfig(
            cases=[_case(3, "log-ok"), _case(4, "log-empty"), _case(5, "log-missing")],
            trace_root=trace_root,
            output_root=tmp_path / "external-runs",
            batch_id="unit_batch",
            dry_run=True,
            repo_root=SKILL_ROOT,
        )
    )

    summary = result["summary"]
    assert summary["total_cases"] == 3
    assert summary["trace_available"] == 1
    assert summary["trace_missing_or_empty"] == 2
    assert summary["ok"] == 0
    assert summary["failed"] == 0
    assert summary["trace_available"] + summary["trace_missing_or_empty"] == summary["total_cases"]

    summary_json = json.loads((tmp_path / "external-runs" / "unit_batch" / "batch_summary.json").read_text())
    assert summary_json["summary"] == summary


def test_cli_exposes_batch_run_command() -> None:
    import findreason as _unused  # noqa: F401
    import findreason_core.batch_runner as batch_runner
    import importlib.util

    cli_path = SKILL_ROOT / "scripts" / "findreason.py"
    spec = importlib.util.spec_from_file_location("_findreason_cli_for_test", cli_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    parser = module.build_parser()
    args = parser.parse_args(
        [
            "batch-run",
            "--sheet-token",
            "token",
            "--sheet-id",
            "sid",
            "--trace-root",
            "/tmp/traces",
            "--output-root",
            "/tmp/out",
            "--batch-id",
            "bid",
            "--dry-run",
            "--judgement-mode",
            "agent",
        ]
    )

    assert args.command == "batch-run"
    assert args.func == module._cmd_batch_run
    assert batch_runner.DEFAULT_BATCH_ID == "20260613_lark_OfEB_246"


def test_batch_execute_writes_case_outputs_and_balanced_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import findreason_core.batch_runner as batch_runner
    from findreason_core.evidence_kernel import write_json

    trace_root = tmp_path / "trace_spans"
    _trace_file(trace_root, "log-ok")

    def fake_collect_evidence(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        facts = {
            "schema_version": "agent-judgement-v4",
            "log_id": kwargs["log_id"],
            "workspace_id": kwargs["workspace_id"],
            "app_id": kwargs["app_id"],
            "case": {"query": "query"},
            "counts": {},
            "artifacts": {},
        }
        write_json(out / "case_facts.json", facts)
        (out / "agent_brief.md").write_text("# Agent Brief\n", encoding="utf-8")
        return facts

    def fake_run_experiment(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        experiment_type = str(kwargs["experiment_type"])
        payload = {"status": "ok", "experiment_type": experiment_type}
        write_json(out / f"{experiment_type}_experiment.json", payload)
        return payload

    def fake_synthesize_brief(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        write_json(out / "evidence_index.json", {"docs": []})
        (out / "agent_judgement.md").write_text("# FindReason Judgement\n", encoding="utf-8")
        return {"status": "ok"}

    monkeypatch.setattr(batch_runner, "collect_evidence", fake_collect_evidence)
    monkeypatch.setattr(batch_runner, "run_experiment", fake_run_experiment)
    monkeypatch.setattr(batch_runner, "synthesize_brief", fake_synthesize_brief)

    result = batch_runner.run_batch(
        batch_runner.BatchRunConfig(
            cases=[_case(3, "log-ok"), _case(4, "log-missing")],
            trace_root=trace_root,
            output_root=tmp_path / "external-runs",
            batch_id="unit_batch",
            dry_run=False,
            judgement_mode="draft",
            repo_root=SKILL_ROOT,
        )
    )

    summary = result["summary"]
    assert summary["total_cases"] == 2
    assert summary["ok"] == 1
    assert summary["trace_missing_or_empty"] == 1
    assert summary["ok"] + summary["failed"] + summary["trace_missing_or_empty"] == summary["total_cases"]

    ok_dir = tmp_path / "external-runs" / "unit_batch" / "row_003_log-ok"
    missing_dir = tmp_path / "external-runs" / "unit_batch" / "row_004_log-missing"
    assert (ok_dir / "case.json").exists()
    assert (ok_dir / "case_facts.json").exists()
    assert (ok_dir / "recall_experiment.json").exists()
    assert (ok_dir / "rerank_experiment.json").exists()
    assert (ok_dir / "replay_experiment.json").exists()
    assert (ok_dir / "evidence_index.json").exists()
    assert json.loads((ok_dir / "run_status.json").read_text())["status"] == "ok"
    assert json.loads((missing_dir / "run_status.json").read_text())["status"] == "trace_missing_or_empty"


def test_summarize_marks_pending_case_ok_when_agent_judgement_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import findreason_core.batch_runner as batch_runner
    from findreason_core.evidence_kernel import write_json

    trace_root = tmp_path / "trace_spans"
    _trace_file(trace_root, "log-ok")

    def fake_collect_evidence(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        facts = {
            "schema_version": "agent-judgement-v4",
            "log_id": kwargs["log_id"],
            "workspace_id": kwargs["workspace_id"],
            "app_id": kwargs["app_id"],
            "case": {"query": "query"},
            "counts": {},
            "artifacts": {},
        }
        write_json(out / "case_facts.json", facts)
        (out / "agent_brief.md").write_text("# Agent Brief\n", encoding="utf-8")
        return facts

    def fake_run_experiment(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        experiment_type = str(kwargs["experiment_type"])
        write_json(out / f"{experiment_type}_experiment.json", {"status": "ok"})
        return {"status": "ok"}

    def fake_synthesize_brief(**kwargs: object) -> dict[str, object]:
        out = Path(str(kwargs["output_dir"]))
        write_json(out / "evidence_index.json", {"docs": []})
        (out / "agent_judgement.md").write_text("# FindReason Judgement\n", encoding="utf-8")
        return {"status": "ok"}

    monkeypatch.setattr(batch_runner, "collect_evidence", fake_collect_evidence)
    monkeypatch.setattr(batch_runner, "run_experiment", fake_run_experiment)
    monkeypatch.setattr(batch_runner, "synthesize_brief", fake_synthesize_brief)

    batch_runner.run_batch(
        batch_runner.BatchRunConfig(
            cases=[_case(3, "log-ok")],
            trace_root=trace_root,
            output_root=tmp_path / "external-runs",
            batch_id="unit_batch",
            dry_run=False,
            judgement_mode="agent",
            repo_root=SKILL_ROOT,
        )
    )

    case_dir = tmp_path / "external-runs" / "unit_batch" / "row_003_log-ok"
    write_json(
        case_dir / "judgement_result.json",
        {
            "row_number": 3,
            "log_id": "log-ok",
            "primary_cause": "answer_failure",
            "confidence": "high",
            "needs_human_review": False,
            "badcase_review_status": "needs_human_review_evaluator_disputed",
            "human_review_reason": "评估器事实正确性结论和 prompt 证据不一致",
            "human_review_context": {
                "query": "如何查看计划赔付金额",
                "judged_answer": "历史答案",
                "evaluator_claim": "事实错误",
                "prompt_evidence": ["直接支撑答案的文档片段"],
                "why_evaluator_may_be_wrong": "评估器忽略了 prompt 中的直接支撑证据",
            },
            "evidence_summary": "trace grounded",
        },
    )

    payload = batch_runner.summarize_batch(tmp_path / "external-runs" / "unit_batch")

    assert payload["summary"]["ok"] == 1
    assert payload["summary"]["pending_agent_judgement"] == 0
    assert payload["cause_distribution"] == {"answer_failure": 1}
    assert payload["badcase_review_status_distribution"] == {"needs_human_review_evaluator_disputed": 1}
    assert payload["rows"][0]["badcase_review_status"] == "needs_human_review_evaluator_disputed"
    assert payload["rows"][0]["human_review_reason"] == "评估器事实正确性结论和 prompt 证据不一致"
    assert payload["rows"][0]["human_review_context"]["why_evaluator_may_be_wrong"] == "评估器忽略了 prompt 中的直接支撑证据"

    eval_rows = [
        json.loads(line)
        for line in (case_dir.parent / "eval_dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert eval_rows[0]["badcase_review_status"] == "needs_human_review_evaluator_disputed"
    assert eval_rows[0]["human_review_context"]["evaluator_claim"] == "事实错误"

    summary_json = json.loads((case_dir.parent / "batch_summary.json").read_text())
    assert summary_json["summary"]["ok"] == 1
    assert summary_json["badcase_review_status_distribution"] == {"needs_human_review_evaluator_disputed": 1}
