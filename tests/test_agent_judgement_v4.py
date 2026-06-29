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


def trace_with_model_prompt_docs_only() -> dict:
    return {
        "data": {
            "spans": [
                {
                    "span_id": "workflow",
                    "span_type": "workflow",
                    "input": json.dumps({"sys": {"query": "巨量千川人群管理中怎么创建人群包"}}, ensure_ascii=False),
                    "output": json.dumps({"end": "测试答案"}, ensure_ascii=False),
                },
                {
                    "span_id": "rag-recall",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRecall",
                    "span_name": "知商召回1",
                    "output": json.dumps(
                        {
                            "origin_doc_list": [
                                {
                                    "id": "3641558",
                                    "identifier": "210445",
                                    "title": "巨量千川_「人群分析」产品使用手册",
                                    "content": "点击确认创建人群包。",
                                }
                            ],
                            "origin_faq_list": [],
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "rag-rerank",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRerank",
                    "span_name": "知商重排1",
                    "output": json.dumps(
                        {
                            "rerank_docs": [
                                {
                                    "id": "3641558",
                                    "identifier": "210445",
                                    "title": "巨量千川_「人群分析」产品使用手册",
                                    "content": "点击确认创建人群包。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "model",
                    "parent_id": "workflow",
                    "span_type": "model",
                    "span_name": "LLM",
                    "output": json.dumps(
                        {
                            "prompt_docs": [
                                {
                                    "id": "3641558",
                                    "identifier": "210445",
                                    "chunkId": "19",
                                    "type": 2,
                                    "recallSource": "self_dataset_search",
                                    "title": "巨量千川_「人群分析」产品使用手册",
                                    "content": "点击确认创建人群包。",
                                }
                            ],
                            "docs": [{"title": "model side docs"}],
                            "doc_string": "model prompt text",
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    }


def trace_with_zero_score_workflow_segment() -> dict:
    return {
        "data": {
            "spans": [
                {
                    "span_id": "workflow",
                    "span_type": "workflow",
                    "input": json.dumps({"sys": {"query": "如何进行千川乘方全店推广"}}, ensure_ascii=False),
                    "output": json.dumps({"end": "测试答案"}, ensure_ascii=False),
                },
                {
                    "span_id": "recall-sub-query",
                    "parent_id": "workflow",
                    "span_type": "recall_sub_query",
                    "span_name": "RecallSubQuery",
                    "output": json.dumps({"query": "如何进行千川乘方全店推广"}, ensure_ascii=False),
                },
                {
                    "span_id": "rag-recall",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRecall",
                    "span_name": "知商召回1",
                    "output": json.dumps(
                        {
                            "origin_doc_list": [
                                {
                                    "id": "201294",
                                    "identifier": "2953471",
                                    "title": "千川乘方产品手册",
                                    "content": "点击：乘方-选择商品-全店托管。系统自动优选全店潜力商品进行投放。",
                                }
                            ],
                            "origin_faq_list": [
                                {
                                    "id": "2176387",
                                    "identifier": "727404",
                                    "type": 4,
                                    "title": "千川乘方核心优势",
                                    "content": "使用乘方即可享受电商技术服务费减免至0.6%",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "rag-rerank",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRerank",
                    "span_name": "知商重排1",
                    "output": json.dumps(
                        {
                            "rerank_docs": [
                                {
                                    "id": "201294",
                                    "identifier": "2953471",
                                    "title": "千川乘方产品手册",
                                    "content": "点击：乘方-选择商品-全店托管。系统自动优选全店潜力商品进行投放。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "span_id": "end",
                    "parent_id": "workflow",
                    "span_type": "End",
                    "span_name": "结束",
                    "output": json.dumps({"end": "测试答案"}, ensure_ascii=False),
                },
            ]
        }
    }


def fake_resolved_workflow_config() -> dict:
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "order": 0, "input_keys": [], "output_keys": ["query"]},
        {"id": "pre", "type": "ZhiShangRAGPreprocess", "name": "知商预处理1", "order": 1, "input_keys": ["query"], "output_keys": ["query", "keywords"]},
        {"id": "recall", "type": "ZhiShangRAGRecall", "name": "知商召回1", "order": 2, "input_keys": ["query"], "output_keys": ["origin_doc_list", "origin_faq_list"]},
        {"id": "rerank", "type": "ZhiShangRAGRerank", "name": "知商重排1", "order": 3, "input_keys": ["docs"], "output_keys": ["rerank_docs"]},
        {"id": "qa", "type": "ZhiShangRAGQA", "name": "知商问答1", "order": 4, "input_keys": ["docs"], "output_keys": ["answer", "qaPromptDocs"]},
        {"id": "end", "type": "End", "name": "结束", "order": 5, "input_keys": ["answer"], "output_keys": ["end"]},
    ]
    edges = [
        {"source": "start", "target": "pre", "type": ""},
        {"source": "pre", "target": "recall", "type": ""},
        {"source": "recall", "target": "rerank", "type": ""},
        {"source": "rerank", "target": "qa", "type": ""},
        {"source": "qa", "target": "end", "type": ""},
    ]
    return {
        "source": "unit_app_detail",
        "mapping_status": "workflow_config_loaded",
        "app_name": "RAG 问答",
        "version_id": "32",
        "workflow_config": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "node_order": [node["id"] for node in nodes],
            "global_config": {},
        },
    }


def fake_custom_resolved_workflow_config() -> dict:
    nodes = [
        {"id": "start", "type": "Start", "name": "开始", "order": 0, "input_keys": [], "output_keys": ["query"]},
        {"id": "recall", "type": "ZhiShangRAGRecall", "name": "知商召回1", "order": 1, "input_keys": ["query"], "output_keys": ["origin_doc_list"]},
        {"id": "rerank", "type": "ZhiShangRAGRerank", "name": "知商重排1", "order": 2, "input_keys": ["docs"], "output_keys": ["rerank_docs"]},
        {"id": "script1", "type": "Script", "name": "脚本1", "order": 3, "input_keys": ["docs"], "output_keys": ["prompt_docs"]},
        {"id": "model", "type": "Model", "name": "大模型1", "order": 4, "input_keys": ["prompt"], "output_keys": ["answer", "prompt_docs"]},
        {"id": "post", "type": "Script", "name": "脚本2(后处理)", "order": 5, "input_keys": ["answer"], "output_keys": ["end"]},
        {"id": "end", "type": "End", "name": "结束", "order": 6, "input_keys": ["end"], "output_keys": ["end"]},
    ]
    edges = [
        {"source": "start", "target": "recall", "type": ""},
        {"source": "recall", "target": "rerank", "type": ""},
        {"source": "rerank", "target": "script1", "type": ""},
        {"source": "script1", "target": "model", "type": ""},
        {"source": "model", "target": "post", "type": ""},
        {"source": "post", "target": "end", "type": ""},
    ]
    return {
        "source": "unit_app_detail",
        "mapping_status": "workflow_config_loaded",
        "app_name": "千川线上 AB 实验专用",
        "version_id": "8",
        "workflow_config": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "node_order": [node["id"] for node in nodes],
            "global_config": {},
        },
    }


def span(span_id: str, span_type: str, node_id: str, output: dict | None = None, *, parent_id: str = "workflow", name: str = "", input_payload: dict | None = None) -> dict:
    return {
        "span_id": span_id,
        "parent_id": parent_id,
        "span_type": span_type,
        "span_name": name,
        "custom_tags": {"zhishang.node_id": node_id},
        "input": json.dumps(input_payload or {}, ensure_ascii=False),
        "output": json.dumps(output or {}, ensure_ascii=False),
    }


def standard_app_detail_trace(*, include_prompt: bool = True) -> dict:
    qa_output = {"answer": "点击确认完成创建"}
    if include_prompt:
        qa_output["qaPromptDocs"] = [
            {"id": "3641558", "identifier": "210445", "title": "巨量千川_「人群分析」产品使用手册", "content": "点击确认创建人群包。"}
        ]
    return {
        "data": {
            "spans": [
                {"span_id": "workflow", "span_type": "workflow", "input": json.dumps({"sys": {"query": "怎么创建人群包"}}, ensure_ascii=False), "output": json.dumps({"end": "点击确认完成创建"}, ensure_ascii=False)},
                span("start-span", "Start", "start", {"query": "怎么创建人群包"}, name="开始"),
                span("pre-span", "ZhiShangRAGPreprocess", "pre", {"query": "怎么创建人群包", "keywords": ["人群包"]}, name="知商预处理1"),
                span("recall-span", "ZhiShangRAGRecall", "recall", {"origin_doc_list": [{"id": "3641558", "identifier": "210445", "title": "巨量千川_「人群分析」产品使用手册", "content": "点击确认创建人群包。"}], "origin_faq_list": []}, name="知商召回1"),
                span("rerank-span", "ZhiShangRAGRerank", "rerank", {"rerank_docs": [{"id": "3641558", "identifier": "210445", "title": "巨量千川_「人群分析」产品使用手册", "content": "点击确认创建人群包。"}]}, name="知商重排1"),
                span("qa-span", "ZhiShangRAGQA", "qa", qa_output, name="知商问答1"),
                span("end-span", "End", "end", {"end": "点击确认完成创建"}, name="结束"),
            ]
        }
    }


def custom_app_detail_trace() -> dict:
    prompt_doc = {"id": "3641558", "identifier": "210445", "title": "巨量千川_「人群分析」产品使用手册", "content": "点击确认创建人群包。"}
    return {
        "data": {
            "spans": [
                {"span_id": "workflow", "span_type": "workflow", "input": json.dumps({"sys": {"query": "怎么创建人群包"}}, ensure_ascii=False), "output": json.dumps({"end": "点击确认完成创建"}, ensure_ascii=False)},
                span("start-span", "Start", "start", {"query": "怎么创建人群包"}, name="开始"),
                span("recall-span", "ZhiShangRAGRecall", "recall", {"origin_doc_list": [prompt_doc], "origin_faq_list": []}, name="知商召回1"),
                span("rerank-span", "ZhiShangRAGRerank", "rerank", {"rerank_docs": [prompt_doc]}, name="知商重排1"),
                span("script-span", "Script", "script1", {"prompt_docs": [prompt_doc], "prompt": "整理证据"}, name="脚本1"),
                span("model-span", "model", "model", {"prompt_docs": [prompt_doc], "answer": "点击确认完成创建"}, name="大模型1"),
                span("post-span", "Script", "post", {"end": "点击确认完成创建"}, name="脚本2(后处理)"),
                span("end-span", "End", "end", {"end": "点击确认完成创建"}, name="结束"),
            ]
        }
    }


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
    recall_args = parser.parse_args(
        [
            "run-experiment",
            "--type",
            "recall",
            "--facts-file",
            "/tmp/case_facts.json",
            "--query",
            "workflow query",
            "--context-query",
            "context query",
        ]
    )
    assert recall_args.context_query == ["context query"]
    knowledge_args = parser.parse_args(
        [
            "run-experiment",
            "--type",
            "knowledge-detail",
            "--facts-file",
            "/tmp/case_facts.json",
        ]
    )
    assert knowledge_args.type == "knowledge-detail"


def test_schema_exposes_skill_release_marker() -> None:
    from findreason_core.evidence_kernel import schema_payload

    payload = schema_payload()

    assert payload["skill_release_marker"] == "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1"
    assert "JSON-only evidence input" in payload["skill_release_policy"]


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
    assert facts["skill_release_marker"] == "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1"
    assert "JSON-only evidence input" in facts["skill_release_policy"]
    assert facts["counts"] == {"origin_doc_list": 1, "origin_faq_list": 1, "recall": 2, "rerank_docs": 1, "prompt_docs": 1}
    assert facts["agent_contract"]["hard_selection_disabled"] is True
    assert facts["agent_contract"]["skill_release_marker"] == facts["skill_release_marker"]
    assert "primary_cause" not in facts
    assert "candidate_cause" not in json.dumps(facts, ensure_ascii=False)


def test_collect_evidence_falls_back_to_model_span_prompt_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setenv("FINDREASON_TRACE_WORKFLOW_MAPPING", "false")
    facts = build_case_facts(
        workspace_id="138",
        log_id="model-prompt-docs-log",
        app_id="1001883",
        case={"query": "巨量千川人群管理中怎么创建人群包"},
        trace_payload=trace_with_model_prompt_docs_only(),
        trace_meta={"source": "unit"},
    )

    assert facts["counts"] == {"origin_doc_list": 1, "origin_faq_list": 0, "recall": 1, "rerank_docs": 1, "prompt_docs": 1}
    assert facts["artifacts"]["prompt_docs"][0]["id"] == "210445"
    assert facts["trace"]["summary"]["selected_workflow_segment"]["prompt_doc_count"] == 1


def test_collect_evidence_builds_app_detail_driven_workflow_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core import fornax_trace
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setattr(fornax_trace, "_resolve_workflow_mapping", lambda workspace_id, app_id: fake_resolved_workflow_config())
    facts = build_case_facts(
        workspace_id="138",
        log_id="standard-workflow-log",
        app_id="1001883",
        case={"query": "怎么创建人群包"},
        trace_payload=standard_app_detail_trace(),
        trace_meta={"source": "unit"},
    )

    trace = facts["trace"]
    assert trace["workflow_topology"]["source"] == "unit_app_detail"
    assert trace["workflow_topology"]["mapping_status"] == "mapped_by_zhishang_node_id"
    assert trace["workflow_topology"]["node_count"] == 6
    qa_node = next(item for item in trace["node_evidence_map"] if item["node"]["id"] == "qa")
    assert qa_node["node"]["type"] == "ZhiShangRAGQA"
    assert qa_node["node"]["name"] == "知商问答1"
    assert qa_node["trace_spans"][0]["span_id"] == "qa-span"
    assert qa_node["evidence_counts"]["prompt_docs"] == 1
    assert trace["prompt_observation"]["status"] == "rag_qa_prompt_docs_found"
    assert trace["agent_span_read_plan"]
    assert "candidate_cause" not in json.dumps(trace["agent_span_read_plan"], ensure_ascii=False)


def test_collect_evidence_maps_custom_script_model_postprocess_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core import fornax_trace
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setattr(fornax_trace, "_resolve_workflow_mapping", lambda workspace_id, app_id: fake_custom_resolved_workflow_config())
    facts = build_case_facts(
        workspace_id="138",
        log_id="custom-workflow-log",
        app_id="1001883",
        case={"query": "怎么创建人群包"},
        trace_payload=custom_app_detail_trace(),
        trace_meta={"source": "unit"},
    )

    trace = facts["trace"]
    model_node = next(item for item in trace["node_evidence_map"] if item["node"]["id"] == "model")
    post_node = next(item for item in trace["node_evidence_map"] if item["node"]["id"] == "post")
    assert model_node["node"]["type"] == "Model"
    assert model_node["node"]["name"] == "大模型1"
    assert model_node["evidence_counts"]["prompt_docs"] == 1
    assert post_node["node"]["name"] == "脚本2(后处理)"
    assert post_node["evidence_counts"]["answer"] == 1
    assert trace["prompt_observation"]["status"] == "model_span_prompt_docs_found"
    answer_plan = next(item for item in trace["agent_span_read_plan"] if item["cause"] == "答案生成错误")
    answer_plan_text = json.dumps(answer_plan, ensure_ascii=False)
    assert "大模型1" in answer_plan_text
    assert "脚本2(后处理)" in answer_plan_text


def test_collect_evidence_marks_prompt_not_observed_without_claiming_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core import fornax_trace
    from findreason_core.agent_judgement_contract import judgement_brief_markdown
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setattr(fornax_trace, "_resolve_workflow_mapping", lambda workspace_id, app_id: fake_resolved_workflow_config())
    facts = build_case_facts(
        workspace_id="138",
        log_id="no-prompt-log",
        app_id="1001883",
        case={"query": "怎么创建人群包"},
        trace_payload=standard_app_detail_trace(include_prompt=False),
        trace_meta={"source": "unit"},
    )

    assert facts["trace"]["prompt_observation"]["status"] == "not_observed"
    brief = judgement_brief_markdown(facts)
    assert "prompt_observation: `not_observed`" in brief
    assert "全部" not in brief or "过滤" not in brief


def test_collect_evidence_falls_back_when_app_detail_node_id_unmatched(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core import fornax_trace
    from findreason_core.agent_judgement_contract import judgement_brief_markdown
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setattr(fornax_trace, "_resolve_workflow_mapping", lambda workspace_id, app_id: fake_resolved_workflow_config())
    trace = standard_app_detail_trace()
    for item in trace["data"]["spans"]:
        if isinstance(item, dict) and isinstance(item.get("custom_tags"), dict):
            item["custom_tags"]["zhishang.node_id"] = f"missing-{item['custom_tags']['zhishang.node_id']}"
    facts = build_case_facts(
        workspace_id="138",
        log_id="mapping-fallback-log",
        app_id="1001883",
        case={"query": "怎么创建人群包"},
        trace_payload=trace,
        trace_meta={"source": "unit"},
    )

    assert facts["trace"]["workflow_topology"]["mapping_status"] == "node_id_unmatched_fallback_span_type"
    assert any(item["mapping_status"] == "unmapped_trace_span_fallback" for item in facts["trace"]["node_evidence_map"])
    brief = judgement_brief_markdown(facts)
    assert "mapping_status: `node_id_unmatched_fallback_span_type`" in brief


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


def test_collect_evidence_uses_workflow_segment_recall_when_score_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    from findreason_core.evidence_kernel import build_case_facts

    monkeypatch.setenv("FINDREASON_TRACE_WORKFLOW_MAPPING", "false")
    facts = build_case_facts(
        workspace_id="138",
        log_id="segment-log",
        app_id="1001883",
        case={"query": "如何进行千川乘方全店推广"},
        trace_payload=trace_with_zero_score_workflow_segment(),
        trace_meta={"source": "unit"},
    )

    assert facts["counts"] == {"origin_doc_list": 1, "origin_faq_list": 1, "recall": 2, "rerank_docs": 1, "prompt_docs": 0}
    assert facts["artifacts"]["origin_doc_list"][0]["title"] == "千川乘方产品手册"
    assert facts["artifacts"]["origin_faq_list"][0]["title"] == "千川乘方核心优势"
    assert facts["trace"]["summary"]["selected_workflow_segment"]["workflow_span_id"] == "workflow"
    assert facts["raw_trace_evidence"]["origin_doc_list_raw"][0]["identifier"] == "2953471"


def test_agent_brief_is_case_specific_working_note() -> None:
    from findreason_core.agent_judgement_contract import judgement_brief_markdown

    brief = judgement_brief_markdown(
        {
            "schema_version": "agent-judgement-v4",
            "skill_release_marker": "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1",
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
    assert "这是输出文件之一" in brief
    assert "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1" in brief
    assert "不能作为后续分析的证据来源、导航或结论依据" in brief
    assert "用户问：query" in brief
    assert "审计锚点" in brief
    assert "Workflow 摘要" in brief
    assert "RAG 阶段定位" in brief
    assert "被评估答案 / answer_hint" in brief
    assert "评估器线索是低置信诊断线索" in brief
    assert "Doc A" in brief
    assert "https://example.com/doc-a" in brief
    assert "This is the cited support snippet" in brief
    assert "归因时先想这几件事" not in brief
    assert "按 cause 的 span 读取入口" not in brief
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
    assert result["skill_release_marker"] == "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1"
    assert "# FindReason Judgement" in report
    assert "skill_release_marker=`findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1`" in report
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
    assert index["skill_release_marker"] == "findreason-rag-attribution@2026-06-29-json-only-workflow-aware-v1"
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
    posted: dict[str, object] = {"calls": []}

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
            posted["calls"].append({"endpoint": endpoint, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(experiments, "resolve_workflow_auth_token", fake_resolve_token)
    monkeypatch.setattr(experiments.httpx, "Client", FakeClient)

    result = run_experiment(
        experiment_type="recall",
        facts_file=str(facts_file),
        query="new concrete query",
        context_queries=["context rich query"],
    )

    assert result["status"] == "ok"
    assert result["counts"]["recall_docs"] == 1
    assert result["artifacts"]["recall_docs"][0]["id"] == "doc-42"
    matrix = result["recall_variant_matrix"]
    assert [item["variant_id"] for item in matrix[:3]] == [
        "baseline_trace_recall",
        "workflow_query_override",
        "topk_relaxed",
    ]
    topk_variant = next(item for item in matrix if item["variant_id"] == "topk_relaxed")
    assert topk_variant["status"] == "ok"
    assert topk_variant["request_payload"]["recallRequests"][0]["maxCount"] == 50
    assert next(item for item in matrix if item["variant_id"] == "label_relaxed")["status"] == "unsupported"
    assert next(item for item in matrix if item["variant_id"] == "threshold_relaxed")["status"] == "unsupported"
    assert next(item for item in matrix if item["variant_id"] == "context_query_1")["variant_type"] == "context_query"
    assert "workflow_input_loss" in next(item for item in matrix if item["variant_id"] == "context_query_1")["notes"]
    workflow_call = posted["calls"][0]
    assert workflow_call["json"]["oriQuery"] == "new concrete query"
    assert workflow_call["json"]["query"] == ["new concrete query"]
    assert workflow_call["json"]["params"]["workspaceId"] == "138"
    assert workflow_call["headers"]["Authorization"] == "Bearer unit-token"
    assert "primary_cause" not in result
    assert "candidate_cause" not in result


def test_rerank_experiment_outputs_rank_shift_with_alias_matching(tmp_path: Path) -> None:
    from findreason_core.evidence_kernel import write_json
    from findreason_core.experiments import run_experiment

    facts = {
        "schema_version": "agent-judgement-v4",
        "log_id": "log",
        "workspace_id": "138",
        "app_id": "1001883",
        "case": {
            "query": "query",
            "core_documents": [
                {
                    "doc_id": "identifier-1",
                    "supported_assertion": "支撑准入规则",
                    "title_hint": "准入规则",
                }
            ],
        },
        "trace": {
            "prompt_observation": {"status": "not_observed", "locations": []},
            "node_evidence_map": [
                {"node": {"id": "qa", "type": "ZhiShangRAGQA", "name": "问答节点"}, "inferred_role": "rag_answer"}
            ],
        },
        "artifacts": {
            "origin_doc_list": [
                {
                    "id": "internal-1",
                    "doc_id_aliases": ["internal-1", "identifier-1"],
                    "title": "准入规则",
                    "content": "核心规则",
                    "rank": 2,
                    "score": 0.9,
                }
            ],
            "origin_faq_list": [],
            "rerank_docs": [],
            "prompt_docs": [],
        },
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)

    result = run_experiment(experiment_type="rerank", facts_file=str(facts_file))

    observation = result["rank_shift_observations"][0]
    assert observation["core_doc"]["doc_id"] == "identifier-1"
    assert observation["core_doc"]["supported_assertion"] == "支撑准入规则"
    assert observation["recall"]["rank"] == 2
    assert observation["rerank"]["rank"] is None
    assert observation["missing_reason"] == "missing_from_rerank"
    assert observation["context_boundary"]["status"] == "not_observed"
    assert "Script1" not in json.dumps(observation, ensure_ascii=False)
    assert result["missing_from_rerank"] == ["identifier-1"]


def test_knowledge_detail_experiment_extracts_status_signals_and_unconfirmed(
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
        "case": {
            "query": "query",
            "core_documents": [
                {"doc_id": "doc-ok", "title_hint": "旧 FAQ"},
                {"doc_id": "doc-fail", "title_hint": "失败文档"},
            ],
        },
        "artifacts": {
            "origin_doc_list": [
                {"id": "doc-ok", "title": "旧 FAQ", "content": "", "rank": 1},
                {"id": "doc-fail", "title": "失败文档", "content": "", "rank": 2},
            ],
            "origin_faq_list": [],
            "rerank_docs": [],
            "prompt_docs": [],
        },
    }
    facts_file = tmp_path / "case_facts.json"
    write_json(facts_file, facts)

    async def fake_resolve_token(workspace_id: str) -> tuple[str, str]:
        return "unit-token", "unit"

    class FakeResponse:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint
            self.status_code = 500 if "doc-fail" in endpoint else 200
            self.text = "failed" if self.status_code >= 400 else "{}"

        def json(self) -> dict:
            return {
                "code": 0,
                "data": {
                    "title": "旧 FAQ",
                    "content": "该手册已停止更新，属于历史版本。",
                    "modifyTime": "2026-06-01",
                },
            }

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, endpoint: str, *, headers: dict, json: dict) -> FakeResponse:
            assert headers["Authorization"] == "Bearer unit-token"
            return FakeResponse(endpoint)

    monkeypatch.setattr(experiments, "resolve_workflow_auth_token", fake_resolve_token)
    monkeypatch.setattr(experiments.httpx, "Client", FakeClient)

    result = run_experiment(experiment_type="knowledge-detail", facts_file=str(facts_file), output_dir=str(tmp_path))

    assert result["counts"] == {"key_docs": 2, "confirmed": 1, "unconfirmed": 1}
    ok = next(item for item in result["knowledge_details"] if item["doc_id"] == "doc-ok")
    failed = next(item for item in result["knowledge_details"] if item["doc_id"] == "doc-fail")
    assert ok["status_confirmed"] is True
    assert ok["status_signals"] == ["停止更新", "历史版本"]
    assert ok["last_modified"] == "2026-06-01"
    assert failed["status_confirmed"] is False
    assert failed["status_reason"] == "status_unconfirmed"
    assert (tmp_path / "knowledge_detail_experiment.json").exists()


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

    class FakeWorkspaceResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {"code": 0, "data": {"authInfo": {"apiKey": "workspace-api-key"}}}

    class FakeAppDetailResponse:
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

        def get(self, endpoint: str, *, params: dict[str, object], headers: dict[str, str]) -> object:
            calls.append({"endpoint": endpoint, "params": params, "headers": headers, "timeout": self.timeout})
            if endpoint == workflow_replay.OPEN_PLAT_WORKSPACE_INFO_URL:
                return FakeWorkspaceResponse()
            return FakeAppDetailResponse()

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
    assert resolved["auth_token_source"] == "workspace_api_key:workspace_info_api:fixed_source_constant"
    assert calls[0]["endpoint"] == workflow_replay.OPEN_PLAT_WORKSPACE_INFO_URL
    assert calls[0]["params"] == {"workspaceId": "138"}
    assert calls[0]["headers"]["Authorization"] == "Bearer 37160d0535224506965a54e58e0685c4"
    assert calls[0]["headers"]["x-zs-plt-open"] == "zs_open"
    assert calls[1]["endpoint"] == workflow_replay.OPEN_PLAT_APP_DETAIL_URL
    assert calls[1]["params"] == {"appId": "1001883", "workspaceId": "138", "appVersion": "7"}
    assert calls[1]["headers"]["Authorization"] == "Bearer workspace-api-key"
    assert calls[1]["headers"]["workspaceId"] == "138"
    assert "x-zs-plt-open" not in calls[1]["headers"]


def test_resolve_workflow_omits_app_version_when_user_does_not_provide_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import findreason_core.workflow_replay as workflow_replay
    from findreason_core.models import AttributionRequest, CaseInput

    calls: list[dict[str, object]] = []

    class FakeWorkspaceResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, object]:
            return {"code": 0, "data": {"authInfo": {"apiKey": "workspace-api-key"}}}

    class FakeAppDetailResponse:
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

        def get(self, endpoint: str, *, params: dict[str, object], headers: dict[str, str]) -> object:
            calls.append({"endpoint": endpoint, "params": params, "headers": headers})
            if endpoint == workflow_replay.OPEN_PLAT_WORKSPACE_INFO_URL:
                return FakeWorkspaceResponse()
            return FakeAppDetailResponse()

    monkeypatch.setattr(workflow_replay.httpx, "Client", FakeClient)

    request = AttributionRequest(case_input=CaseInput(query="query", workspace_id="138", app_id="1001883"))

    resolved = workflow_replay.resolve_workflow(request)

    assert resolved["source"] == "openplat_app_detail"
    assert resolved["version_id"] == "9"
    assert calls[0]["params"] == {"workspaceId": "138"}
    assert calls[1]["params"] == {"appId": "1001883", "workspaceId": "138"}
    assert calls[1]["headers"]["Authorization"] == "Bearer workspace-api-key"
    assert calls[1]["headers"]["workspaceId"] == "138"
    assert "x-zs-plt-open" not in calls[1]["headers"]


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
