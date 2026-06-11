from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))


def minimal_trace() -> dict:
    return {
        "data": {
            "spans": [
                {
                    "span_id": "workflow",
                    "span_type": "workflow",
                    "input": json.dumps({"sys": {"query": "短视频制作和分期免息怎么收费"}}, ensure_ascii=False),
                    "output": json.dumps({"end": "测试答案"}, ensure_ascii=False),
                },
                {
                    "span_id": "recall",
                    "span_type": "ZhiShangRAGRecall",
                    "output": json.dumps(
                        {
                            "origin_doc_list": [
                                {
                                    "id": "799191",
                                    "title": "分期免息规则",
                                    "content": "分期免息无需消费者承担手续费。",
                                    "url": "https://example.com/doc",
                                }
                            ],
                            "origin_faq_list": [{"id": "faq1", "title": "FAQ", "content": "免费试用到期可能收费。"}],
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "rerank",
                    "span_type": "ZhiShangRAGRerank",
                    "output": json.dumps(
                        {
                            "rerank_docs": [
                                {"id": "799191", "title": "分期免息规则", "content": "分期免息无需消费者承担手续费。"}
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "qa",
                    "span_type": "ZhiShangRAGQA",
                    "output": json.dumps(
                        {
                            "answer": "测试答案",
                            "prompt_docs": [
                                {"id": "799191", "title": "分期免息规则", "content": "分期免息无需消费者承担手续费。"}
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    }


def trace_with_recall_template() -> dict:
    trace = minimal_trace()
    trace["data"]["spans"].append(
        {
            "span_id": "recall-http",
            "span_type": "http",
            "input": json.dumps(
                {
                    "url": "https://example.test/api/sirius_plugin/v1/recall",
                    "headers": {"Authorization": "Bearer secret-token", "workspaceId": "138"},
                    "body": {
                        "oriQuery": "old query",
                        "query": ["old query"],
                        "recallRequests": [
                            {
                                "name": "self_dataset_search",
                                "recallStrategy": "self_dataset_search",
                                "maxCount": 30,
                            }
                        ],
                        "params": {"workspaceId": "138"},
                    },
                },
                ensure_ascii=False,
            ),
        }
    )
    return trace


def test_collect_evidence_builds_case_facts_without_hard_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setenv("FINDREASON_TRACE_WORKFLOW_MAPPING", "false")
    facts = build_case_facts(
        workspace_id="138",
        log_id="smoke-log",
        app_id="1001883",
        case={"query": "短视频制作和分期免息怎么收费"},
        trace_payload=minimal_trace(),
        trace_meta={"source": "unit"},
    )

    assert facts["schema_version"] == "agent-judgement-v4"
    assert facts["counts"] == {"origin_doc_list": 1, "origin_faq_list": 1, "recall": 2, "rerank_docs": 1, "prompt_docs": 1}
    assert facts["agent_contract"]["hard_selection_disabled"] is True
    assert "primary_cause" not in facts
    assert "candidate_cause" not in json.dumps(facts, ensure_ascii=False)


def test_collect_evidence_extracts_recall_template_for_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setenv("FINDREASON_TRACE_WORKFLOW_MAPPING", "false")
    facts = build_case_facts(
        workspace_id="138",
        log_id="smoke-log",
        app_id="1001883",
        case={"query": "短视频制作和分期免息怎么收费"},
        trace_payload=trace_with_recall_template(),
        trace_meta={"source": "unit"},
    )

    templates = facts["experiment_inputs"]["recall_templates"]
    assert len(templates) == 1
    assert templates[0]["endpoint"].endswith("/api/sirius_plugin/v1/recall")
    assert templates[0]["request_body"]["oriQuery"] == "old query"
    assert templates[0]["headers"]["Authorization"] == "Bearer <redacted>"


def test_agent_brief_contains_symptom_to_experiment_contract() -> None:
    from findreason_core.agent_judgement_contract import judgement_brief_markdown

    brief = judgement_brief_markdown(
        {
            "schema_version": "agent-judgement-v4",
            "log_id": "log",
            "workspace_id": "138",
            "app_id": "1001883",
            "case": {"query": "query"},
            "counts": {"recall": 2, "origin_doc_list": 1, "origin_faq_list": 1, "rerank_docs": 1, "prompt_docs": 1},
            "trace": {
                "has_middle_node_trace": True,
                "workflow_span_ios": [
                    {
                        "selected": True,
                        "input": {"sys": {"query": "workflow query"}},
                        "output": {"end": "workflow answer"},
                    }
                ],
            },
            "preprocess": {"rewrite_query": "rewrite query", "keywords": ["rewrite"]},
            "artifacts": {
                "prompt_docs": [
                    {
                        "id": "doc-a",
                        "title": "Doc A",
                        "url": "https://example.com/doc-a",
                        "content": "This is the cited support snippet.",
                    }
                ]
            },
        }
    )

    assert "Do not jump to an earliest failing stage" in brief
    assert "Candidate explanations" in brief
    assert "Evidence to check" in brief
    assert "Historical trace is the badcase scene" in brief
    assert "workflow input" in brief
    assert "wrapped_output / answer_hint" in brief
    assert "Doc A" in brief
    assert "https://example.com/doc-a" in brief
    assert "This is the cited support snippet" in brief
    assert "hypothesis -> experiment -> falsification -> current judgement" in brief
    assert "evidence sufficiency" in brief
    assert "required assertions" in brief


def test_synthesize_brief_writes_readable_report_and_index(tmp_path: Path) -> None:
    from findreason_core.evidence_kernel import write_json
    from findreason_core.reporting import synthesize_brief

    facts = {
        "schema_version": "agent-judgement-v4",
        "log_id": "log",
        "workspace_id": "138",
        "app_id": "1001883",
        "case": {
            "query": "原始问题",
            "answer_hint": "包装后的答案",
            "judgement": "factual_correctness=score:0; reason:事实错误\nknowledge_is_answered=score:1; reason:有回答",
        },
        "trace": {
            "workflow_span_ios": [
                {"selected": True, "input": {"sys": {"query": "workflow 输入"}}, "output": {"end": "workflow 输出"}}
            ]
        },
        "preprocess": {"rewrite_query": "rewrite", "keywords": ["kw"]},
        "answer": "trace answer",
        "counts": {"recall": 1, "origin_doc_list": 1, "origin_faq_list": 0, "rerank_docs": 1, "prompt_docs": 1},
        "citation_observations": {"wrong_citation": True, "claim_alignments": []},
        "artifacts": {
            "origin_doc_list": [
                {
                    "id": "doc-a",
                    "title": "Readable Doc",
                    "url": "https://example.com/doc",
                    "content": "Readable support snippet for the report.",
                    "rank": 1,
                }
            ],
            "origin_faq_list": [],
            "rerank_docs": [
                {
                    "id": "doc-a",
                    "title": "Readable Doc",
                    "url": "https://example.com/doc",
                    "content": "Readable support snippet for the report.",
                    "rank": 1,
                }
            ],
            "prompt_docs": [
                {
                    "id": "doc-a",
                    "title": "Readable Doc",
                    "url": "https://example.com/doc",
                    "content": "Readable support snippet for the report.",
                    "rank": 1,
                }
            ],
        },
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)
    write_json(tmp_path / "recall_experiment.json", {"status": "ok", "counts": {"recall_docs": 0}, "artifacts": {"recall_docs": []}})
    write_json(tmp_path / "rerank_experiment.json", {"status": "observed", "missing_from_rerank": [], "missing_from_prompt": []})
    write_json(tmp_path / "replay_experiment.json", {"status": "ok", "counts": {}, "answer": "replay answer", "artifacts": {"prompt_docs": []}})

    result = synthesize_brief(facts_file=str(facts_file), output_dir=str(tmp_path))
    report = (tmp_path / "agent_judgement.md").read_text(encoding="utf-8")
    index = json.loads((tmp_path / "evidence_index.json").read_text(encoding="utf-8"))

    assert result["status"] == "ok"
    assert "# FindReason Judgement Summary" in report
    assert "candidate_cause: 待 Agent 判断" in report
    assert "置信度: 待 Agent 判断" in report
    assert "Case 摘要" in report
    assert "workflow 输入" in report
    assert "workflow 输出" in report
    assert "包装后的输出" in report
    assert "factual_correctness" in report
    assert "上游证据链" in report
    assert "Readable Doc" in report
    assert "https://example.com/doc" in report
    assert "Readable support snippet" in report
    assert "证据充分性判断" in report
    assert "required_assertions" in report
    assert "direct_support" in report
    assert "missing_authoritative_evidence" in report
    assert "本地证据包" in report
    assert "Agent 最终回复要求" not in report
    assert "给用户输出短版结论" not in report
    assert "明确写 `candidate_cause`" not in report
    assert '"prompt_doc_ids"' not in report
    assert "Recall 代表证据" not in report
    assert "Rerank 代表证据" not in report
    assert "Prompt 代表证据" not in report
    assert len(report.splitlines()) <= 100
    assert index["report_rule"].startswith("Human reports must cite")
    assert index["sufficiency_review_contract"]["support_levels"] == [
        "direct_support",
        "partial_support",
        "adjacent_support",
        "insufficient",
        "contradictory",
    ]
    assert result["outputs"]["agent_judgement"].endswith("agent_judgement.md")


def test_experiment_plans_do_not_emit_hard_cause(tmp_path: Path) -> None:
    from findreason_core.evidence_kernel import write_json
    from findreason_core.experiments import run_experiment

    facts = {
        "schema_version": "agent-judgement-v4",
        "log_id": "log",
        "workspace_id": "138",
        "app_id": "1001883",
        "case": {"query": "query"},
        "counts": {"recall": 2, "origin_doc_list": 1, "origin_faq_list": 1, "rerank_docs": 1, "prompt_docs": 1},
        "artifacts": {
            "origin_doc_list": [{"id": "doc-a", "title": "A", "content": "A support", "rank": 1}],
            "origin_faq_list": [{"id": "faq-b", "title": "B", "content": "B support", "rank": 1}],
            "rerank_docs": [{"id": "doc-a", "title": "A", "content": "A support", "rank": 1}],
            "prompt_docs": [],
        },
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)

    recall = run_experiment(experiment_type="recall", facts_file=str(facts_file))
    assert recall["status"] == "planned"
    assert recall["experiment_type"] == "recall"

    rerank = run_experiment(experiment_type="rerank", facts_file=str(facts_file), target_doc_ids=["doc-a", "faq-b"])
    assert rerank["status"] == "observed"
    assert rerank["experiment_type"] == "rerank"
    assert rerank["survival"][0]["doc_id"] == "doc-a"
    assert rerank["survival"][0]["in_rerank"] is True
    assert rerank["survival"][1]["doc_id"] == "faq-b"
    assert rerank["survival"][1]["in_rerank"] is False
    assert rerank["missing_from_rerank"] == ["faq-b"]

    for result in (recall, rerank):
        assert "primary_cause" not in result
        assert "candidate_cause" not in result


def test_recall_experiment_executes_trace_template_with_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import findreason_core.experiments as experiments
    from findreason_core.evidence_kernel import write_json
    from findreason_core.experiments import run_experiment

    facts = {
        "schema_version": "agent-judgement-v4",
        "log_id": "log",
        "workspace_id": "138",
        "app_id": "1001883",
        "case": {"query": "old query"},
        "experiment_inputs": {
            "recall_templates": [
                {
                    "kind": "split_recall",
                    "endpoint": "https://example.test/api/sirius_plugin/v1/recall",
                    "request_body": {
                        "oriQuery": "old query",
                        "query": ["old query"],
                        "recallRequests": [{"name": "self_dataset_search", "recallStrategy": "self_dataset_search"}],
                        "params": {},
                    },
                }
            ]
        },
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)
    posted: dict[str, object] = {}

    async def fake_resolve_token(workspace_id: str) -> tuple[str, str]:
        assert workspace_id == "138"
        return "unit-token", "unit"

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self) -> dict:
            return {
                "code": 0,
                "data": {
                    "recallResult": {
                        "self_dataset_search": [
                            {"id": "doc-42", "title": "目标知识", "content": "可以支撑必要断言。"}
                        ]
                    }
                },
            }

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            posted["timeout"] = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, endpoint: str, *, headers: dict, json: dict) -> FakeResponse:
            posted["endpoint"] = endpoint
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(experiments, "resolve_workflow_auth_token", fake_resolve_token)
    monkeypatch.setattr(experiments.httpx, "Client", FakeClient)

    result = run_experiment(
        experiment_type="recall",
        facts_file=str(facts_file),
        query="new concrete query",
    )

    assert result["status"] == "ok"
    assert result["counts"]["recall_docs"] == 1
    assert result["artifacts"]["recall_docs"][0]["id"] == "doc-42"
    assert posted["json"]["oriQuery"] == "new concrete query"
    assert posted["json"]["query"] == ["new concrete query"]
    assert posted["json"]["params"]["workspaceId"] == "138"
    assert posted["headers"]["Authorization"] == "Bearer unit-token"
    assert "primary_cause" not in result
    assert "candidate_cause" not in result


def test_v3_hard_judgement_path_is_disabled() -> None:
    from findreason_core.v3 import V3Error, orchestrate_v3

    with pytest.raises(V3Error) as exc:
        orchestrate_v3()
    assert exc.value.error_code == "E_V3_ATTRIBUTION_DISABLED"
