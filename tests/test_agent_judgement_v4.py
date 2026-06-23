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


def test_cli_does_not_expose_batch_commands() -> None:
    import argparse
    import importlib.util

    cli_path = SKILL_ROOT / "scripts" / "findreason.py"
    spec = importlib.util.spec_from_file_location("_findreason_cli_no_batch_test", cli_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    parser = module.build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))

    assert "batch-run" not in subparsers.choices
    assert "batch-summarize" not in subparsers.choices
    version_args = parser.parse_args(
        [
            "run-experiment",
            "--type",
            "replay",
            "--facts-file",
            "/tmp/case_facts.json",
            "--query",
            "query",
            "--app-id",
            "1001883",
            "--app-version",
            "7",
        ]
    )
    assert version_args.version_id == "7"


def test_reference_case_playbooks_cover_trace_acquisition_modes() -> None:
    cases_dir = SKILL_ROOT / "references" / "cases"
    expected = {
        "019eee75-local-trace-workflow-input-loss.md": [
            "019eee75-59cc-7b82-9dae-368f21808b14",
            "local_trace_json",
            "--trace-file",
            "workflow_input_loss",
            "输入侧问题",
            "验证 query",
            "python3 -m findreason collect-evidence",
        ],
        "019eef8d-rerun-input-knowledge-missing.md": [
            "019eef8d-bf8e-7e90-a605-98a95d636ed9",
            "rerun_from_original_input",
            "暂停计划、预算撞线、密集上调ROI",
            "suspected_knowledge_missing",
            "知识缺失或证据不足",
            "replay_experiment.json",
        ],
        "019ece69-logid-trace-retrieval-miss.md": [
            "019ece69-e987-7212-b341-222bcd4ff6ec",
            "logid_trace_fetch",
            "021780901906425fdbddc03001b0c040000000000000034bbaafd",
            "208332",
            "retrieval_miss",
            "召回遗漏",
        ],
    }

    for filename, required_strings in expected.items():
        text = (cases_dir / filename).read_text(encoding="utf-8")
        for heading in [
            "## 适用场景",
            "## 执行链路",
            "## 证据链",
            "## 候选根因",
            "## 最终 judgement",
            "## 反证与下一步",
        ]:
            assert heading in text
        for required in required_strings:
            assert required in text

    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "references/cases/" in skill_text
    openai_yaml = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "python3 -m findreason" in openai_yaml
    assert "local python -m findreason" not in openai_yaml


def test_cause_taxonomy_contract_is_chinese_with_guardrails() -> None:
    files = [
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "references" / "report_contract.md",
        SKILL_ROOT / "references" / "evidence_kernel.md",
        SKILL_ROOT / "scripts" / "findreason_core" / "agent_judgement_contract.py",
        SKILL_ROOT / "agents" / "openai.yaml",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)

    for label in [
        "输入侧问题",
        "知识缺失或证据不足",
        "召回遗漏",
        "重排丢失",
        "答案生成错误",
        "无明显错误/评估器不准，需人工进一步核实",
    ]:
        assert label in text

    for old_slug in [
        "workflow_input_loss",
        "suspected_knowledge_missing",
        "retrieval_miss",
        "rerank_drop",
        "answer_failure",
        "evaluator_disputed_no_obvious_error",
    ]:
        assert old_slug in text

    assert "中文 cause 为主" in text
    assert "旧 slug" in text
    assert "根据验证点改写后的 query" in text
    assert "召回改善、排序改善，或 replay / 最终结果改善" in text
    assert "只能写成低置信候选或待验证点" in text
    assert "人工复核点" in text
    assert "不要求固定范式" in text
    assert "评估器输出暂无" in text
    assert "不是第 6 类证据" in text
    assert "不能作为“看不出来”的兜底" in text
    assert "顶层 cause 只能从 5 类中选择" not in text
    assert "独立于五类 root cause" not in text


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


def test_agent_brief_is_case_specific_working_note() -> None:
    from findreason_core.agent_judgement_contract import judgement_brief_markdown

    brief = judgement_brief_markdown(
        {
            "schema_version": "agent-judgement-v4",
            "log_id": "log",
            "workspace_id": "138",
            "app_id": "1001883",
            "case": {
                "query": "query",
                "answer_hint": "包装后的答案",
                "chat_history": json.dumps([{"role": "user", "content": "用户补充了一个重要上下文"}], ensure_ascii=False),
                "judgement": "factual_correctness=score:0; reason:评估器认为事实错误",
            },
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

    assert "# Agent Brief" in brief
    assert "这是一份给 Agent 快速进入现场的工作单" in brief
    assert "用户问：query" in brief
    assert "审计锚点" in brief
    assert "Workflow 摘要" in brief
    assert "被评估答案 / answer_hint" in brief
    assert "评估器线索是低置信诊断线索" in brief
    assert "chat_history 只用于判断 `输入侧问题`" in brief
    assert "旧 slug: `workflow_input_loss`" in brief
    assert "根据验证点改写后的 query" in brief
    assert "召回改善、排序改善，或 replay / 最终结果改善" in brief
    assert "不得用 chat_history 支撑 `答案生成错误`" in brief
    assert "旧 slug: `answer_failure`" in brief
    assert "`badcase_review_status`" in brief
    assert "evaluator_disputed_no_obvious_error" in brief
    assert "人工复核点" in brief
    assert "评估器输出暂无" in brief
    assert "Doc A" in brief
    assert "https://example.com/doc-a" in brief
    assert "This is the cited support snippet" in brief
    assert "Workflow 现场" not in brief
    assert "证据链速览" not in brief
    assert "```json" not in brief
    assert "origin_doc_list" not in brief
    assert "rerank_docs" not in brief
    assert "Required Report Contract" not in brief
    assert "Symptom To Root Cause Seeds" not in brief
    assert "Do not jump to an earliest failing stage" not in brief


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
    assert "# FindReason Judgement" in report
    assert "自动合成的短版结论草稿" in report
    assert "candidate_cause: 待 Agent 判断" not in report
    assert "置信度: 待 Agent 判断" not in report
    assert "审计锚点" in report
    assert "被评估目标" in report
    assert "factual_correctness" in report
    assert "Readable Doc" in report
    assert "https://example.com/doc" in report
    assert "Readable support snippet" in report
    assert "评估器与复核" in report
    assert "badcase_review_status" in report
    assert "中文 cause 为主" in report
    assert "输入侧问题" in report
    assert "无明显错误/评估器不准，需人工进一步核实" in report
    assert "人工复核点" in report
    assert "评估器输出暂无" in report
    assert "第 6 类证据" in report
    assert "召回改善、排序改善，或 replay / 最终结果改善" in report
    assert "五类 root cause" not in report
    assert "现场事实" not in report
    assert "workflow 输入：`" not in report
    assert "workflow 输出：`" not in report
    assert "preprocess：" not in report
    assert "recall：共" not in report
    assert "rerank/prompt：" not in report
    assert "实验：recall" not in report
    assert "本地证据包" not in report
    assert "case_facts.json" not in report
    assert "Required Report Contract" not in report
    assert "Symptom To Root Cause Seeds" not in report
    assert "Agent 最终回复要求" not in report
    assert "给用户输出短版结论" not in report
    assert "明确写 `candidate_cause`" not in report
    assert '"prompt_doc_ids"' not in report
    assert "Recall 代表证据" not in report
    assert "Rerank 代表证据" not in report
    assert "Prompt 代表证据" not in report
    assert len(report.splitlines()) <= 100
    assert index["report_rule"].startswith("Human reports must cite")
    assert "sufficiency_review_contract" not in index
    assert "badcase_review_contract" not in index
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


def test_replay_experiment_skips_when_authoritative_trace_exists(
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
        "case": {"query": "query"},
        "trace": {"has_middle_node_trace": True},
        "counts": {"origin_doc_list": 1, "origin_faq_list": 0, "recall": 1, "rerank_docs": 1, "prompt_docs": 1},
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)

    async def fail_if_called(request: object) -> object:
        raise AssertionError("live replay should be skipped for authoritative historical trace")

    monkeypatch.setattr(experiments, "replay_workflow", fail_if_called)

    result = run_experiment(experiment_type="replay", facts_file=str(facts_file), output_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["mode"] == "skipped_authoritative_trace"
    assert result["counts"] == facts["counts"]
    assert result["artifacts"] == {}
    assert json.loads((tmp_path / "replay_experiment.json").read_text(encoding="utf-8"))["mode"] == "skipped_authoritative_trace"


def test_resolve_workflow_uses_openplat_app_detail_with_user_version(monkeypatch: pytest.MonkeyPatch) -> None:
    import findreason_core.workflow_replay as workflow_replay
    from findreason_core.models import AttributionRequest, CaseInput

    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "data": {
                    "appId": 1001883,
                    "workspaceId": 138,
                    "name": "接口应用",
                    "versionId": 7,
                    "versionType": 1,
                    "workflowConfigV2": json.dumps(
                        {
                            "nodes": [
                                {
                                    "id": "start",
                                    "type": "Start",
                                    "data": {
                                        "output_params": [
                                            {"key": "query", "type": "String", "required": True}
                                        ]
                                    },
                                }
                            ],
                            "edges": [],
                        },
                        ensure_ascii=False,
                    ),
                },
            }

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, endpoint: str, *, params: dict[str, object], headers: dict[str, str]) -> FakeResponse:
            calls.append({"endpoint": endpoint, "params": params, "headers": headers, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr(workflow_replay.httpx, "Client", FakeClient)

    request = AttributionRequest(
        case_input=CaseInput(query="query", workspace_id="138", app_id="1001883", version_id="7")
    )

    resolved = workflow_replay.resolve_workflow(request)

    assert resolved["source"] == "openplat_app_detail"
    assert resolved["app_id"] == "1001883"
    assert resolved["workspace_id"] == "138"
    assert resolved["version_id"] == "7"
    assert resolved["app_name"] == "接口应用"
    assert resolved["input_schema"][0]["key"] == "query"
    assert calls[0]["params"] == {"appId": "1001883", "workspaceId": "138", "appVersion": "7"}
    assert calls[0]["headers"]["Authorization"] == "Bearer 37160d0535224506965a54e58e0685c4"
    assert calls[0]["headers"]["x-zs-plt-open"] == "zs_open"


def test_resolve_workflow_omits_app_version_when_user_does_not_provide_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import findreason_core.workflow_replay as workflow_replay
    from findreason_core.models import AttributionRequest, CaseInput

    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "data": {
                    "appId": 1001883,
                    "workspaceId": 138,
                    "name": "最新版本应用",
                    "versionId": 9,
                    "versionType": 0,
                    "workflowConfigV2": json.dumps({"nodes": [], "edges": []}, ensure_ascii=False),
                },
            }

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, endpoint: str, *, params: dict[str, object], headers: dict[str, str]) -> FakeResponse:
            calls.append({"endpoint": endpoint, "params": params, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(workflow_replay.httpx, "Client", FakeClient)

    request = AttributionRequest(case_input=CaseInput(query="query", workspace_id="138", app_id="1001883"))

    resolved = workflow_replay.resolve_workflow(request)

    assert resolved["source"] == "openplat_app_detail"
    assert resolved["version_id"] == "9"
    assert calls[0]["params"] == {"appId": "1001883", "workspaceId": "138"}


def test_replay_experiment_passes_version_id_from_facts_to_workflow_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import findreason_core.experiments as experiments
    from findreason_core.evidence_kernel import write_json
    from findreason_core.experiments import run_experiment

    seen: dict[str, object] = {}
    facts = {
        "schema_version": "agent-judgement-v4",
        "log_id": "log",
        "workspace_id": "138",
        "app_id": "1001883",
        "case": {"query": "query", "version_id": "7"},
        "trace": {"has_middle_node_trace": False},
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)

    async def fake_replay_workflow(request: object) -> object:
        seen["version_id"] = getattr(request.case_input, "version_id", None)
        enriched = request.model_copy(deep=True)
        enriched.workflow_replay.status = "ok"
        enriched.workflow_replay.extracted_evidence = {
            "origin_doc_list": [],
            "origin_faq_list": [],
            "rerank_docs": [],
            "prompt_docs": [],
        }
        return enriched

    monkeypatch.setattr(experiments, "replay_workflow", fake_replay_workflow)

    result = run_experiment(experiment_type="replay", facts_file=str(facts_file), output_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert seen["version_id"] == "7"


def test_v3_hard_judgement_path_is_disabled() -> None:
    from findreason_core.v3 import V3Error, orchestrate_v3

    with pytest.raises(V3Error) as exc:
        orchestrate_v3()
    assert exc.value.error_code == "E_V3_ATTRIBUTION_DISABLED"
