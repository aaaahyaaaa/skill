from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "findreason.py"


def run_cli(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.update(
        {
            "FINDREASON_ENV_DISABLE": "true",
            "FINDREASON_TRACE_WORKFLOW_MAPPING": "false",
            "OPEN_PLAT_TRACE_TOKEN": "test-token",
        }
    )
    merged_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def run_module(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env.update(
        {
            "FINDREASON_ENV_DISABLE": "true",
            "FINDREASON_TRACE_WORKFLOW_MAPPING": "false",
            "OPEN_PLAT_TRACE_TOKEN": "test-token",
        }
    )
    merged_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "findreason", *args],
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def trace_payload() -> dict[str, Any]:
    support_doc = {"id": "d1", "title": "云图定义", "content": "云图是用于指标分析的数据产品。"}
    return {
        "code": 0,
        "msg": "",
        "data": {
            "spans": [
                {
                    "span_id": "workflow",
                    "parent_id": "",
                    "span_type": "workflow",
                    "span_name": "ExecuteWorkflow",
                    "input": {"sys": {"query": "云图是什么"}},
                    "output": {"end": "云图是天气图片。"},
                    "custom_tags": {"zhishang.workspace_id": "55", "zhishang.app_id": "100"},
                    "logid": "log-1",
                },
                {
                    "span_id": "pre",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGPreprocess",
                    "span_name": "知商预处理",
                    "output": {"query": "云图是什么", "rewrite_query": "云图是什么", "keyword": {"words": ["云图"]}},
                    "custom_tags": {"zhishang.workspace_id": "55"},
                    "logid": "log-1",
                },
                {
                    "span_id": "recall",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRecall",
                    "span_name": "知商召回",
                    "output": {"origin_doc_list": [support_doc], "origin_faq_list": []},
                    "custom_tags": {"zhishang.workspace_id": "55"},
                    "logid": "log-1",
                },
                {
                    "span_id": "rerank",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGRerank",
                    "span_name": "知商重排",
                    "output": {"rerank_docs": []},
                    "custom_tags": {"zhishang.workspace_id": "55"},
                    "logid": "log-1",
                },
                {
                    "span_id": "qa",
                    "parent_id": "workflow",
                    "span_type": "ZhiShangRAGQA",
                    "span_name": "知商问答",
                    "output": {"answer": "云图是天气图片。", "prompt_docs": []},
                    "custom_tags": {"zhishang.workspace_id": "55"},
                    "logid": "log-1",
                },
            ],
            "TracesAdvanceInfo": {"TraceID": "log-1", "Tokens": {"Input": 10, "Output": 5}},
            "has_more": False,
            "next_page_token": "",
        },
    }


def trace_payload_with_recall_template(recall_url: str) -> dict[str, Any]:
    payload = trace_payload()
    payload["data"]["spans"].append(
        {
            "span_id": "recall-http",
            "parent_id": "recall",
            "span_type": "http_client",
            "span_name": "ad-sirius.bytedance.net",
            "input": {
                "method": "POST",
                "url": recall_url,
                "body": json.dumps(
                    {
                        "oriQuery": "随心推如何设置铺底计划",
                        "keyWordInfo": {},
                        "recallRequests": [
                            {
                                "name": "doc_search",
                                "recallStrategy": "doc_search",
                                "isPrivateDoc": 0,
                                "maxCount": 30,
                                "recallLabels": ["内容中台应用-342"],
                                "params": {"score": "0.3", "min_score": "0.7"},
                                "contentMaxSize": 1200,
                                "level": ["L1"],
                            },
                            {
                                "name": "featured_search",
                                "recallStrategy": "featured_search",
                                "isPrivateDoc": 1,
                                "maxCount": 3,
                                "recallLabels": ["租户-FAQ"],
                                "params": {"score": "0.3", "精选": "0.7", "内容中台": "0.8"},
                                "contentMaxSize": 1200,
                                "level": ["L2"],
                            },
                        ],
                        "params": {"workspaceId": "55"},
                    },
                    ensure_ascii=False,
                ),
            },
            "output": {},
            "custom_tags": {"zhishang.workspace_id": "55"},
            "logid": "log-1",
        }
    )
    return payload


class RecallServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parent.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers), "body": None})
                raw = json.dumps({"code": 0, "data": {"authInfo": {"apiKey": "workspace-key"}}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                parent.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
                raw = json.dumps(
                    {
                        "recallResult": {
                            "doc_search": [
                                {
                                    "id": "wide-doc-1",
                                    "type": 2,
                                    "recallSource": "doc_search",
                                    "title": "铺底计划设置说明",
                                    "content": "用户核心问题是随心推如何设置铺底计划，正确答案应该说明铺底计划的设置步骤和入口。",
                                    "recallScore": 0.91,
                                    "chunkId": "1",
                                }
                            ],
                            "featured_search": [
                                {
                                    "id": "wide-faq-1",
                                    "type": 4,
                                    "recallSource": "featured_search",
                                    "title": "铺底计划 FAQ",
                                    "content": "随心推如何设置铺底计划：需要说明铺底计划的设置步骤和入口。",
                                    "recallScore": 0.82,
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RecallServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def workspace_info_url(self) -> str:
        return f"{self.base_url}/open-plat/api/workspace/get-workspace-info"

    @property
    def recall_url(self) -> str:
        return f"{self.base_url}/api/sirius_plugin/v1/recall"


class TraceServer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[dict[str, Any]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                body = self.rfile.read(length).decode("utf-8")
                parent.requests.append({"headers": dict(self.headers), "body": json.loads(body)})
                raw = json.dumps(parent.payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "TraceServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/open-plat/api/fornax/trace/detail"


class KnowledgeDetailServer:
    def __init__(self, records: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.records = records or {}
        self.requests: list[dict[str, Any]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                source = (query.get("source") or [""])[0]
                identifier = (query.get("identifier") or [""])[0]
                parent.requests.append(
                    {
                        "method": "GET",
                        "path": parsed.path,
                        "query": {"source": source, "identifier": identifier},
                        "headers": dict(self.headers),
                    }
                )
                record = parent.records.get((source, identifier))
                if record is None:
                    raw = json.dumps({"code": 404, "msg": "not found", "data": None}, ensure_ascii=False).encode("utf-8")
                else:
                    raw = json.dumps({"code": 0, "data": record}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "KnowledgeDetailServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/api/sirius_knowledge/v1/data/doc/record_id"


def case_file(tmp_path: Path, **overrides: Any) -> Path:
    payload: dict[str, Any] = {
        "case": {
            "query": "云图是什么",
            "expected_knowledge_ids": ["d1"],
            "judgement_evidence": {
                "signals": [{"key": "main_issue", "value": "答案事实错误", "confidence": 0.9}]
            },
        }
    }
    payload["case"].update(overrides)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_schema_exposes_v3_and_old_commands_are_absent(tmp_path: Path) -> None:
    result = run_module("schema", cwd=SKILL_ROOT, env={"HOME": str(tmp_path)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "v3"
    assert "ingest-fornax-trace" in payload["commands"]
    assert "orchestrate" in payload["commands"]
    assert "probe-self-oracle" in payload["commands"]["probes"]
    assert "probe-rerank-bypass" in payload["commands"]["probes"]
    combined = result.stdout + run_module("--help", cwd=SKILL_ROOT, env={"HOME": str(tmp_path)}).stdout
    for old in (
        "adapt-input",
        "fetch-fornax-trace",
        "rerank-experiment",
        "summarize-batch",
        "show-run",
        "env-info",
        "probe-by-judgement",
        "probe-by-claim",
        "probe-by-doc-title",
        "probe-rerank-tune",
        "--use-llm",
        "MODELHUB",
    ):
        assert old not in combined


def test_output_schema_accepts_current_oracle_status_sources() -> None:
    schema = json.loads((SKILL_ROOT / "references" / "output-schema.json").read_text(encoding="utf-8"))
    source_enum = schema["properties"]["oracle_status"]["properties"]["source"]["enum"]

    assert "insufficient_assertions" in source_enum
    assert "host_assertions" in source_enum


def test_capabilities_exclude_removed_weak_semantic_probes() -> None:
    manifest = json.loads((SKILL_ROOT / "references" / "capabilities.json").read_text(encoding="utf-8"))
    capabilities = {item["command"]: item for item in manifest["capabilities"]}

    assert "topk" in capabilities["probe-wide-recall"]["inputs"]
    for command in ("probe-by-judgement", "probe-by-claim", "probe-by-doc-title", "probe-rerank-tune"):
        assert command not in capabilities


def test_ingest_fetches_openplat_trace_and_emits_v3_summary(tmp_path: Path) -> None:
    with TraceServer(trace_payload()) as server:
        result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(case_file(tmp_path)),
            "--output-dir",
            str(tmp_path / "case-out"),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert server.requests[0]["headers"]["Authorization"] == "Bearer test-token"
    assert server.requests[0]["headers"]["x-zs-plt-open"] == "zs_open"
    assert server.requests[0]["body"] == {"workspaceId": 55, "logId": "log-1", "limit": 1000}
    assert payload["schema_version"] == "v3"
    assert payload["app_id"] == "100"
    assert payload["ingest_summary"]["trace_completeness"]["retrieval"] == "complete"
    assert payload["ingest_summary"]["suggested_probe_set"][0] == "probe-self-oracle"
    assert "probe-rerank-bypass" in payload["ingest_summary"]["suggested_probe_set"]
    assert payload["raw_artifacts"]["workflow_span_ios"]
    assert (tmp_path / "case-out" / "ingest.json").exists()
    assert (tmp_path / "case-out" / "attribution_record.json").exists()


def test_ingest_without_host_assertions_requests_probe_plan(tmp_path: Path) -> None:
    with TraceServer(trace_payload()) as server:
        result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(case_file(tmp_path, expected_knowledge_ids=[])),
            "--output-dir",
            str(tmp_path / "case-out"),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    suggested = payload["ingest_summary"]["suggested_probe_set"]
    actions = [item["action"] for item in payload["ingest_summary"]["host_action_required"]]
    assert "generate-probe-plan" in actions
    assert "run-probe-plan" in suggested
    for weak_probe in ("probe-by-judgement", "probe-by-claim", "probe-by-doc-title", "probe-rerank-tune"):
        assert weak_probe not in suggested


def test_cli_ingest_preserves_host_agent_answer_claim(tmp_path: Path) -> None:
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "case_input": {"query": "云图是什么", "workspace_id": "55", "app_id": "100"},
                "host_agent": {
                    "answer_claim": [
                        {
                            "text": "正确答案应说明云图是指标分析的数据产品。",
                            "role": "expected_required",
                            "basis": ["trace_query", "evaluator_reason"],
                            "why_required": "用户询问云图是什么。",
                            "confidence": 0.9,
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with TraceServer(trace_payload()) as server:
        result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(case_path),
            "--output-dir",
            str(tmp_path / "case-out"),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    claims = payload["raw_artifacts"]["attribution_request"]["host_agent"]["answer_claim"]
    assert claims == [
        {
            "text": "正确答案应说明云图是指标分析的数据产品",
            "role": "expected_required",
            "source": "host_agent.answer_claim",
            "basis": ["trace_query", "evaluator_reason"],
            "why_required": "用户询问云图是什么",
            "confidence": 0.9,
        }
    ]
    assert "extract_host_agent_answer_claim" not in [
        item["action"] for item in payload["ingest_summary"]["host_action_required"]
    ]


def test_cli_ingest_preserves_host_agent_answer_claim_in_case_object(tmp_path: Path) -> None:
    case_path = case_file(
        tmp_path,
        expected_knowledge_ids=[],
        host_agent={
            "answer_claim": [
                {
                    "text": "正确答案应说明云图是指标分析的数据产品。",
                    "role": "expected_required",
                    "confidence": 0.9,
                }
            ]
        },
    )
    with TraceServer(trace_payload()) as server:
        result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(case_path),
            "--output-dir",
            str(tmp_path / "case-out"),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    claims = payload["raw_artifacts"]["attribution_request"]["host_agent"]["answer_claim"]
    assert claims == [
        {
            "text": "正确答案应说明云图是指标分析的数据产品",
            "role": "expected_required",
            "source": "host_agent.answer_claim",
            "confidence": 0.9,
        }
    ]
    assert "extract_host_agent_answer_claim" not in [
        item["action"] for item in payload["ingest_summary"]["host_action_required"]
    ]


def test_report_explains_empty_assertion_matrix_needs_probe_plan() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {"query": "同店铺下两个千川号人群会不会互相影响", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "同店铺 千川号 人群 互相影响"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [{"id": "d1", "title": "千川人群说明"}]},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "会互相影响。"},
    }
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[], mode="final")
    report = payload["human_report_markdown"]

    assert "未提供 `host_agent.answer_claim` assertion set 中的 `expected_required` 断言" in report
    assert "先用 Agent attribution planning 生成 assertion set 和 probe-v1 计划" in report
    assert "run-probe-plan" in report


def test_case_report_uses_readable_numbered_sections_and_trace_appendix(tmp_path: Path) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {
            "query": "随心推铺底计划怎么设置",
            "workspace_id": "55",
            "app_id": "100",
        },
        "preprocess": {"rewrite_query": "随心推 铺底计划 设置"},
        "retrieval": {
            "knowledge_exists": None,
            "theoretical_recall_status": "ok",
            "upper_bound_scope": "open_label",
            "theoretical_recall_topk": 50,
            "theoretical_query_variants": ["随心推铺底计划怎么设置"],
            "theoretical_recall_counts": {"total": 1},
            "wide_recall_docs": [
                {
                    "id": "wide-setup-1",
                    "title": "随心推铺底计划设置指南",
                    "content": "随心推铺底计划支持在计划管理中进入铺底设置入口，并按步骤完成配置。",
                }
            ],
            "origin_doc_list": [],
        },
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "可以在计划管理设置。"},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "随心推铺底计划的正确答案应说明设置入口和步骤",
                    "role": "expected_required",
                },
                {"text": "答案只说可以在计划管理设置", "role": "answer_claim"},
            ]
        },
    }
    ingest = minimal_ingest(request)
    ingest["raw_artifacts"]["workflow_span_ios"] = [
        {
            "span_id": "workflow",
            "node_id": "node-1",
            "selected": True,
            "input": {"sys": {"query": "随心推铺底计划怎么设置"}},
            "output": {"answer": "可以在计划管理设置。"},
        }
    ]

    payload = orchestrate_v3(ingest=ingest, probes=[], mode="final")
    report = payload["human_report_markdown"]

    expected_order = [
        "## 1. 结论摘要",
        "## 2. 问题与答案观察",
        "## 3. 必要断言",
        "## 4. 断言覆盖矩阵",
        "## 5. 召回上界与知识判断",
        "### 理论召回上界与断言关系",
        "## 6. 阶段归因链路",
        "## 7. Probe 结果",
        "## 8. 下一步",
        "## 附录：原始 Trace 摘录",
    ]
    positions = [report.index(heading) for heading in expected_order]

    assert positions == sorted(positions)
    assert "原始答案：可以在计划管理设置。" in report
    assert report.index("## 附录：原始 Trace 摘录") > report.index("## 8. 下一步")
    assert "### 原始 Workflow 输入输出" not in report
    assert "输入边界判断：用户实际问题中的关键约束已进入 Workflow 原始输入。" in report
    assert "### 理论召回上界与断言关系" in report
    assert "支持该断言：`wide-setup-1` 随心推铺底计划设置指南" in report


def test_probe_rerank_bypass_is_auxiliary_without_assertion_gap(tmp_path: Path) -> None:
    with TraceServer(trace_payload()) as server:
        ingest_result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(case_file(tmp_path)),
            "--output-dir",
            str(tmp_path / "case-out"),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )
    assert ingest_result.returncode == 0, ingest_result.stderr
    probe_dir = tmp_path / "case-out" / "probes"
    probe_result = run_cli(
        "probe-rerank-bypass",
        "--ingest-file",
        str(tmp_path / "case-out" / "ingest.json"),
        "--output-dir",
        str(probe_dir),
        cwd=SKILL_ROOT,
        env={"HOME": str(tmp_path)},
    )
    assert probe_result.returncode == 0, probe_result.stderr
    orchestrate_result = run_cli(
        "orchestrate",
        "--ingest-file",
        str(tmp_path / "case-out" / "ingest.json"),
        "--probe-dir",
        str(probe_dir),
        "--output-dir",
        str(tmp_path / "case-out" / "final"),
        cwd=SKILL_ROOT,
        env={"HOME": str(tmp_path)},
    )
    assert orchestrate_result.returncode == 0, orchestrate_result.stderr
    payload = json.loads(orchestrate_result.stdout)
    assert payload["primary_cause"] is None
    assert payload["app_id"] == "100"
    report = payload["human_report_markdown"]
    assert "# FindReason 归因报告" in report
    assert "## 6. 阶段归因链路" in report
    assert "## 附录：原始 Trace 摘录" in report
    assert "- Workflow 输出：" not in report
    assert '"query": "云图是什么"' in report
    assert '"end": "云图是天气图片。"' in report
    assert "log_id：`log-1`" in report
    assert "app_id：`100`" in report
    assert "主因枚举：`null`" in report
    assert "doc-id-only evidence does not prove the required assertion was lost" in json.dumps(payload["evidence_chain"], ensure_ascii=False)
    assert "retrieved_but_reranked_out" not in payload["failure_patterns"]
    evidence_ids = {item["evidence_id"] for item in payload["evidence_bundle"]}
    assert evidence_ids
    for verdict in payload["evidence_chain"]:
        assert "counterfactual" in verdict
        assert set(verdict.get("evidence_ids") or []).issubset(evidence_ids)
        assert set((verdict.get("counterfactual") or {}).get("evidence_ids") or []).issubset(evidence_ids)
    assert (tmp_path / "case-out" / "final" / "attribution_record.json").exists()
    assert (tmp_path / "case-out" / "final" / "short_summary.json").exists()
    case_report = tmp_path / "case-out" / "final" / "case_report.md"
    assert case_report.exists()
    assert case_report.read_text(encoding="utf-8") == report
    assert not (tmp_path / "case-out" / "summary.md").exists()
    assert not (tmp_path / "case-out" / "summary.csv").exists()
    assert not (tmp_path / "case-out" / "summary.json").exists()


def test_trace_recall_template_builds_open_label_body() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {"query": "原始 query", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "改写 query"},
    }
    ingest = minimal_ingest(request)
    ingest["raw_artifacts"]["trace_detail"] = trace_payload_with_recall_template("https://ad-sirius.bytedance.net/api/sirius_plugin/v1/recall")

    template = v3._extract_trace_recall_template(ingest)
    assert template["source_span_id"] == "recall-http"
    assert template["recall_span_id"] == "recall"

    bodies = v3._build_open_label_recall_bodies(
        template_body=template["request_body"],
        request_dict=request,
        workspace_id="55",
        topk=50,
    )
    assert [body["oriQuery"] for body in bodies] == ["原始 query", "改写 query"]
    for body in bodies:
        assert body["params"]["workspaceId"] == "55"
        for recall_request in body["recallRequests"]:
            assert recall_request["recallLabels"] == []
            assert recall_request["level"] == []
            assert recall_request["maxCount"] >= 50
            for key in set(recall_request["params"]) & {"score", "精选", "内容中台", "min_score"}:
                assert recall_request["params"][key] == 0


def test_recall_response_parser_supports_direct_and_wrapper() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    direct = {"recallResult": {"doc_search": [{"id": "d1"}]}}
    wrapper = {"response": json.dumps(direct)}

    assert v3._extract_recall_result(direct)["doc_search"][0]["id"] == "d1"
    assert v3._extract_recall_result(wrapper)["doc_search"][0]["id"] == "d1"


def test_expected_assertions_ignore_query_labels_and_unstructured_reason() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {
            "query": "素材衰退，家居品类行业平均周期是多久呀",
            "workspace_id": "55",
            "app_id": "100",
            "error_points": ["是否回答=否", "事实正确性=未覆盖/无法判断"],
            "judgement": "评估器失败项：是否回答=否；事实正确性=未覆盖/无法判断",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "dimension": "coverage",
                    "label": "问题无遗漏",
                    "result": "否",
                    "reason": "用户的核心问题点仅一个：家居品类素材衰退的行业平均周期。Agent_Reply为空，未对该问题点做任何回应。",
                }
            ]
        },
    }

    points = v3._expected_knowledge_points(request)
    assert points == []


def test_legacy_assertion_fields_fail_fast() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {
            "query": "素材衰退，家居品类行业平均周期是多久呀",
            "workspace_id": "55",
            "app_id": "100",
            "expected_knowledge_points": ["不应再从 case_input.expected_knowledge_points 读取"],
            "judgement": "评估器失败项：是否回答=否；事实正确性=未覆盖/无法判断",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "问题无遗漏",
                    "result": "否",
                    "reason": "Agent_Reply为空。",
                    "assertions": ["家居品类素材衰退的行业平均周期应被明确回答。"],
                    "missing_expected_points": [
                        {"text": "答案遗漏了家居品类素材衰退平均周期。", "confidence": 0.87}
                    ],
                }
            ]
        },
        "qa": {
            "missing_expected_points": ["不应再从 qa.missing_expected_points 读取"],
            "unsupported_claims": ["不应再从 qa.unsupported_claims 读取"],
            "claim_alignments": [{"claim": "不应再从 qa.claim_alignments 读取", "status": "contradicted"}],
        },
    }

    with pytest.raises(v3.V3Error) as error:
        v3._normalize_assertion_inputs(request)
    assert error.value.error_code == "E_LEGACY_ASSERTION_INPUT"
    assert "case_input.expected_knowledge_points" in error.value.details["fields"]
    assert "qa.missing_expected_points" in error.value.details["fields"]
    assert "qa.unsupported_claims" in error.value.details["fields"]
    assert "qa.claim_alignments" in error.value.details["fields"]
    assert "judgement_evidence.signals[0].assertions" in error.value.details["fields"]
    assert "judgement_evidence.signals[0].missing_expected_points" in error.value.details["fields"]
    assert error.value.details["required_field"] == "host_agent.answer_claim"


def test_host_agent_answer_claim_generates_all_assertion_roles() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "host_agent": {
            "answer_claim": [
                {"text": "应说明短视频追投入口。", "role": "missing_expected"},
                {"text": "直播计划详情页可以追投。", "role": "answer_claim"},
                {"text": "直播计划详情页可以追投短视频。", "role": "unsupported_claim"},
                {"text": "全域随心推点击率应使用正确维度。", "role": "expected_required"},
            ],
        },
    }

    points = v3._expected_knowledge_points(request)
    by_text = {item["text"]: item for item in points}
    assert all(item["source"] == "host_agent.answer_claim" for item in points)
    assert by_text["应说明短视频追投入口"]["role"] == "expected_required"
    assert by_text["直播计划详情页可以追投短视频"]["role"] == "unsupported_claim"
    assert by_text["直播计划详情页可以追投"]["role"] == "answer_claim"
    assert by_text["全域随心推点击率应使用正确维度"]["role"] == "expected_required"


def test_oracle_claim_signal_uses_only_required_assertions() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {"query": "同店铺下两个千川号人群会不会互相影响", "workspace_id": "55", "app_id": "100"},
        "host_agent": {
            "answer_claim": [
                {"text": "正确输出应回答两个千川号的人群模型是否互相影响。", "role": "expected_required"},
                {"text": "同店铺下的不同千川号信用评分会相互影响。", "role": "answer_claim"},
                {"text": "用户问题限定在人群模型，不应只回答信用评分。", "role": "constraint_check"},
            ]
        },
    }

    signal = v3._oracle_signal_text(request, "claim_back_recall")

    assert "人群模型是否互相影响" in signal
    assert "信用评分" not in signal
    assert "不应只回答" not in signal


def test_probe_cache_key_changes_when_host_assertions_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result

    monkeypatch.setenv("HOME", str(tmp_path))
    base = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "retrieval": {"origin_doc_list": [{"id": "d1", "title": "云图定义", "content": "云图是指标分析数据产品。"}]},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": ""},
    }
    first = json.loads(json.dumps(base))
    first["host_agent"] = {"answer_claim": [{"text": "正确答案应说明云图是指标分析数据产品。", "role": "expected_required"}]}
    second = json.loads(json.dumps(base))
    second["host_agent"] = {"answer_claim": [{"text": "正确答案应说明铺底计划设置入口。", "role": "expected_required"}]}

    first_probe = build_probe_result("probe-self-oracle", ingest=minimal_ingest(first))
    second_probe = build_probe_result("probe-self-oracle", ingest=minimal_ingest(second))

    assert first_probe["telemetry"]["cache_hit"] is False
    assert second_probe["telemetry"]["cache_hit"] is False
    assert first_probe["telemetry"]["cache_key"] != second_probe["telemetry"]["cache_key"]
    assert first_probe["oracle_status"]["expected_knowledge_points"][0]["text"] == "正确答案应说明云图是指标分析数据产品"
    assert second_probe["oracle_status"]["expected_knowledge_points"][0]["text"] == "正确答案应说明铺底计划设置入口"


def test_empty_legacy_assertion_fields_do_not_block() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100", "expected_knowledge_points": []},
        "qa": {"answer_claims": [], "missing_expected_points": [], "unsupported_claims": [], "claim_alignments": []},
        "judgement_evidence": {"signals": [{"label": "是否回答", "assertions": [], "fact_points": []}]},
    }

    normalized = v3._normalize_assertion_inputs(request)
    assert normalized["host_agent"]["answer_claim"] == []


def test_internal_trace_legacy_assertion_fields_are_dropped_before_host_claims() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    trace_request = {
        "case_input": {"query": "q", "workspace_id": "55", "expected_knowledge_points": ["old internal"]},
        "qa": {"answer": "a", "answer_claims": [{"text": "old internal", "role": "expected_required"}]},
        "judgement_evidence": {"signals": [{"label": "x", "assertions": ["old internal"]}]},
    }
    case = {
        "host_agent": {
            "answer_claim": [{"text": "new host assertion", "role": "expected_required", "confidence": 0.8}]
        }
    }

    merged = v3._apply_host_case_fields(trace_request, case, "100", "log-1")
    normalized = v3._normalize_assertion_inputs(merged)
    points = v3._expected_knowledge_points(normalized)

    assert [point["text"] for point in points] == ["new host assertion"]
    assert "answer_claims" not in normalized["qa"]
    assert "expected_knowledge_points" not in normalized["case_input"]
    assert "assertions" not in normalized["judgement_evidence"]["signals"][0]


def test_probe_wide_recall_calls_sirius_open_label_without_leaking_token(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "请问随心推如何设置铺底计划",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "正确答案应该说明铺底计划的设置步骤和入口",
        },
        "host_agent": {
            "answer_claim": [
                {
                    "text": "随心推铺底计划的正确答案应说明设置步骤和入口。",
                    "role": "expected_required",
                    "confidence": 0.91,
                }
            ]
        },
        "qa": {
            "answer": "资料里没有铺底计划。",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "问题无遗漏",
                    "result": "否",
                    "reason": "用户核心问题是随心推如何设置铺底计划，正确答案应该说明铺底计划的设置步骤和入口。",
                }
            ]
        },
        "preprocess": {"rewrite_query": "随心推 设置 铺底计划"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [], "origin_faq_list": []},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
    }
    with RecallServer() as server:
        ingest = minimal_ingest(request)
        ingest["raw_artifacts"]["trace_detail"] = trace_payload_with_recall_template(server.recall_url)
        monkeypatch.setenv("OPEN_PLAT_TRACE_TOKEN", "bootstrap-token")
        monkeypatch.setenv("OPEN_PLAT_WORKSPACE_INFO_URL", server.workspace_info_url)
        monkeypatch.delenv("WORKFLOW_AUTH_TOKEN", raising=False)

        wide_probe = build_probe_result("probe-wide-recall", ingest=ingest, params={"topk": 50}, no_cache=True)

    assert wide_probe["status"] == "ok"
    retrieval = wide_probe["stage_signals"]["retrieval"]
    assert retrieval["upper_bound_scope"] == "open_label"
    assert retrieval["theoretical_recall_status"] == "ok"
    assert retrieval["theoretical_recall_counts"]["total"] == 2
    assert retrieval["wide_recall_docs"][0]["id"] == "wide-doc-1"
    assert retrieval["wide_recall_faqs"][0]["id"] == "wide-faq-1"
    assert "workspace-key" not in json.dumps(wide_probe, ensure_ascii=False)
    get_request = next(item for item in server.requests if item["method"] == "GET")
    post_requests = [item for item in server.requests if item["method"] == "POST"]
    assert get_request["headers"]["Authorization"] == "Bearer bootstrap-token"
    assert {item["headers"]["Authorization"] for item in post_requests} == {"Bearer workspace-key"}
    for item in post_requests:
        for recall_request in item["body"]["recallRequests"]:
            assert recall_request["recallLabels"] == []
            assert recall_request["level"] == []
            assert recall_request["maxCount"] >= 50
            assert recall_request["params"]["score"] == 0

    payload = orchestrate_v3(ingest=ingest, probes=[wide_probe], mode="final")
    report = payload["human_report_markdown"]
    assert "理论召回范围：`open_label`" in report
    assert "断言覆盖矩阵" in report
    assert "这些观察项只用于 answer grounding / scope / citation / consistency 检查" in report
    assert "角色" in report
    assert "线上召回缺失" in report
    assert payload["primary_cause"]["cause_code"] == "retrieval_miss"


def test_knowledge_detail_reference_infers_source_and_identifier_from_links() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    lark_ref = v3._infer_knowledge_detail_reference(
        {"url": "https://bytedance.feishu.cn/doc/doccnGGktnmTzIBO13yUVzmd8rg?from=from_copylink"}
    )
    ocean_ref = v3._infer_knowledge_detail_reference(
        {"documentLink": "https://support.oceanengine.com/support/content/140382?utm_source=x"}
    )

    assert lark_ref == {
        "source": "LARK_DOC",
        "identifier": "doccnGGktnmTzIBO13yUVzmd8rg",
        "basis": "url:feishu",
    }
    assert ocean_ref == {
        "source": "COGNITION",
        "identifier": "140382",
        "basis": "url:oceanengine_support_content",
    }


def test_probe_knowledge_detail_fetches_live_doc_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result

    request = {
        "case_input": {"query": "新客试投在哪里", "workspace_id": "55", "app_id": "100", "expected_knowledge_ids": ["55284"]},
        "retrieval": {
            "knowledge_exists": None,
            "origin_doc_list": [
                {
                    "id": "55284",
                    "title": "新客试投功能",
                    "url": "https://bytedance.feishu.cn/doc/doccnGGktnmTzIBO13yUVzmd8rg",
                }
            ],
        },
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": ""},
    }
    record = {
        "id": 55284,
        "source": "LARK_DOC",
        "identifier": "doccnGGktnmTzIBO13yUVzmd8rg",
        "title": "新客试投功能",
        "link": "https://bytedance.feishu.cn/doc/doccnGGktnmTzIBO13yUVzmd8rg",
        "content": "完成注册后，首次进入巨量千川首页，即可看到新客试投功能入口。",
        "splits": [{"content": "新客试投入口在巨量千川首页。"}],
    }
    with KnowledgeDetailServer({("LARK_DOC", "doccnGGktnmTzIBO13yUVzmd8rg"): record}) as server:
        monkeypatch.setenv("KNOWLEDGE_DETAIL_URL", server.url)
        monkeypatch.setenv("KNOWLEDGE_DETAIL_TOKEN", "detail-token")
        monkeypatch.setenv("HOME", str(tmp_path))

        probe = build_probe_result("probe-knowledge-detail", ingest=minimal_ingest(request), no_cache=True)

    assert server.requests == [
        {
            "method": "GET",
            "path": "/api/sirius_knowledge/v1/data/doc/record_id",
            "query": {"source": "LARK_DOC", "identifier": "doccnGGktnmTzIBO13yUVzmd8rg"},
            "headers": server.requests[0]["headers"],
        }
    ]
    assert server.requests[0]["headers"]["Authorization"] == "Bearer detail-token"
    assert probe["status"] == "ok"
    knowledge = probe["stage_signals"]["knowledge"]
    assert knowledge["knowledge_exists"] == "yes"
    assert knowledge["detail_provider"] == "http_doc_record"
    assert knowledge["content_available_doc_ids"] == ["55284"]
    detail = probe["raw_artifacts"]["knowledge_detail"]["fetched_docs"][0]
    assert detail["doc_id"] == "55284"
    assert detail["source"] == "LARK_DOC"
    assert detail["identifier"] == "doccnGGktnmTzIBO13yUVzmd8rg"
    assert "首次进入巨量千川首页" in detail["content_excerpt"]
    assert "detail-token" not in json.dumps(probe, ensure_ascii=False)


def test_probe_knowledge_detail_uses_raw_trace_doc_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result

    request = {
        "case_input": {"query": "新客试投在哪里", "workspace_id": "55", "app_id": "100", "expected_knowledge_ids": ["1514299"]},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [{"id": "1514299", "title": "新享投"}]},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": ""},
    }
    ingest = minimal_ingest(request)
    ingest["raw_artifacts"]["trace_evidence"] = {
        "origin_doc_list_raw": [
            {
                "id": "1514299",
                "title": "新享投",
                "url": "https://support.oceanengine.com/support/content/129985",
            }
        ]
    }
    record = {
        "id": 1514299,
        "source": "COGNITION",
        "identifier": "129985",
        "title": "新享投",
        "content": "新客试投功能入口：完成注册后，首次进入巨量千川首页即可看到。",
    }
    with KnowledgeDetailServer({("COGNITION", "129985"): record}) as server:
        monkeypatch.setenv("KNOWLEDGE_DETAIL_URL", server.url)
        monkeypatch.setenv("HOME", str(tmp_path))

        probe = build_probe_result("probe-knowledge-detail", ingest=ingest, no_cache=True)

    assert server.requests[0]["query"] == {"source": "COGNITION", "identifier": "129985"}
    knowledge = probe["stage_signals"]["knowledge"]
    assert knowledge["detail_status"] == "ok"
    assert knowledge["content_available_doc_ids"] == ["1514299"]


def minimal_ingest(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v3",
        "log_id": "log-x",
        "workspace_id": "55",
        "app_id": "100",
        "ingest_summary": {
            "trace_completeness": {stage: "complete" for stage in ("preprocess", "knowledge", "retrieval", "rerank", "context", "answer", "evaluation")},
            "suggested_probe_set": [],
            "skip_reason": {},
            "host_action_required": [],
        },
        "raw_artifacts": {"attribution_request": request, "workflow_span_ios": []},
    }


def test_expected_required_merges_semantic_duplicates() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import _expected_knowledge_points

    request = {
        "case_input": {
            "query": "用户截图显示抖音电商千川启航计划/全域投放-推商品，问一元试投在哪里。",
            "workspace_id": "55",
        },
        "host_agent": {
            "answer_claim": [
                {
                    "text": "正确输出应在用户截图所处的抖音电商千川启航计划/全域投放推商品场景下回答一元试投入口位置",
                    "role": "expected_required",
                    "basis": ["trace_query", "chat_history", "evaluator_reason"],
                },
                {
                    "text": "正确输出应覆盖电商启航计划的一元试投入口，例如活动中心-启航计划-马上试或对应电商端入口路径",
                    "role": "expected_required",
                    "basis": ["evaluator_reason", "chat_history"],
                    "confidence": 0.86,
                },
            ]
        },
    }

    points = _expected_knowledge_points(request)
    required = [point for point in points if point["role"] == "expected_required"]

    assert len(required) == 1
    merged = required[0]
    assert "电商千川启航计划" in merged["text"]
    assert "活动中心-启航计划-马上试" in merged["text"]
    assert merged["basis"] == ["trace_query", "chat_history", "evaluator_reason"]
    assert len(merged["merged_from"]) == 2


def test_input_boundary_has_no_business_term_whitelist() -> None:
    source = (SKILL_ROOT / "scripts" / "findreason_core" / "v3.py").read_text(encoding="utf-8")

    forbidden_terms = [
        "INPUT_BOUNDARY_TERMS",
        "GENERIC_BOUNDARY_TERMS",
        "启航计划",
        "全域投放",
        "推商品",
        "一元试投",
        "新客试投",
        "活动中心",
        "马上试",
        "电商端",
        "抖音电商",
        "巨量千川",
        "千川",
        "随心推",
        "铺底计划",
        "人群模型",
        "素材衰退",
    ]
    assert not any(term in source for term in forbidden_terms)


def test_workflow_input_loss_precedes_knowledge_attribution() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {
            "query": "用户截图显示抖音电商千川启航计划/全域投放-推商品，问这个一元试投在哪里。",
            "workspace_id": "55",
            "app_id": "100",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "范围最小",
                    "result": "否",
                    "reason": "用户上下文明确为抖音电商-千川启航计划活动页面与全域投放-推商品场景，正确答案应覆盖活动中心-启航计划-马上试或对应电商端入口路径。",
                }
            ]
        },
        "preprocess": {"rewrite_query": "巨量千川1元试投在哪里", "keywords": ["巨量千川", "1元试投"]},
        "retrieval": {
            "knowledge_exists": False,
            "theoretical_recall_status": "ok",
            "wide_recall_docs": [],
            "origin_doc_list": [],
        },
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "首次进入巨量千川首页即可看到新客试投入口。"},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "正确输出应在用户截图所处的抖音电商千川启航计划/全域投放推商品场景下回答一元试投入口位置，例如活动中心-启航计划-马上试或对应电商端入口路径。",
                    "role": "expected_required",
                    "basis": ["trace_query", "chat_history", "evaluator_reason"],
                }
            ]
        },
    }
    ingest = minimal_ingest(request)
    ingest["raw_artifacts"]["workflow_span_ios"] = [
        {
            "span_id": "workflow",
            "selected": True,
            "input": {"sys": {"query": "巨量千川1元试投在哪里"}},
            "output": {"answer": "首次进入巨量千川首页即可看到新客试投入口。"},
        }
    ]

    payload = orchestrate_v3(ingest=ingest, probes=[], mode="final")

    assert payload["primary_cause"]["cause_code"] == "workflow_input_loss"
    preprocess = next(item for item in payload["evidence_chain"] if item["stage"] == "preprocess")
    assert preprocess["status"] == "fail"
    assert preprocess["candidate_cause"] == "workflow_input_loss"
    assert "启航计划" in json.dumps(preprocess, ensure_ascii=False)
    assert "### 输入边界" in payload["human_report_markdown"]
    assert "Workflow 原始输入丢失" in payload["human_report_markdown"]


def test_preprocess_rewrite_drift_when_workflow_input_is_complete() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {
            "query": "电商启航计划全域投放推商品的一元试投入口在哪里",
            "workspace_id": "55",
            "app_id": "100",
        },
        "preprocess": {"rewrite_query": "巨量千川1元试投在哪里", "keywords": ["巨量千川", "1元试投"]},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": []},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "在首页。"},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "正确输出应覆盖电商启航计划全域投放推商品的一元试投入口。",
                    "role": "expected_required",
                    "basis": ["trace_query"],
                }
            ]
        },
    }
    ingest = minimal_ingest(request)
    ingest["raw_artifacts"]["workflow_span_ios"] = [
        {
            "span_id": "workflow",
            "selected": True,
            "input": {"sys": {"query": "电商启航计划全域投放推商品的一元试投入口在哪里"}},
            "output": {"answer": "在首页。"},
        }
    ]

    payload = orchestrate_v3(ingest=ingest, probes=[], mode="final")

    assert payload["primary_cause"]["cause_code"] == "query_rewrite_drift"
    assert payload["primary_cause"]["stage"] == "preprocess"


def test_missing_expected_ids_does_not_block_downstream_answer_cause() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "q"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [{"id": "d1", "title": "unsupported doc", "content": "unsupported answer evidence"}]},
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "unsupported doc", "content": "unsupported answer evidence"}],
            "prompt_docs": [{"id": "d1", "title": "unsupported doc", "content": "unsupported answer evidence"}],
        },
        "qa": {
            "answer": "unsupported",
            "prompt_supports_answer": True,
            "answer_satisfies_expected": False,
        },
        "host_agent": {"answer_claim": [{"text": "unsupported", "role": "unsupported_claim"}]},
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe], mode="final")

    assert payload["primary_cause"]["stage"] == "answer"
    assert payload["primary_cause"]["cause_code"] == "unsupported_claim"
    assert payload["needs_human_review"] is False
    assert payload["oracle_status"]["source"] == "insufficient_assertions"
    assert payload["oracle_status"]["inferred_doc_ids"] == []
    assert payload["oracle_status"]["expected_knowledge_points"][0]["role"] == "unsupported_claim"
    assert payload["case_assessment"]["status"] == "confirmed_badcase"
    knowledge = next(item for item in payload["evidence_chain"] if item["stage"] == "knowledge")
    assert knowledge["status"] == "indeterminate"
    assert "candidate_cause" not in knowledge
    answer = next(item for item in payload["evidence_chain"] if item["stage"] == "answer")
    assert answer["candidate_cause"] == "unsupported_claim"


def test_explicit_answer_satisfied_outputs_not_badcase_assessment() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "q"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [{"id": "d1", "title": "doc"}]},
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "doc"}],
            "prompt_docs": [{"id": "d1", "title": "doc"}],
        },
        "qa": {"answer": "good", "answer_satisfies_expected": True},
    }
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[], mode="final")

    assert payload["primary_cause"] is None
    assert payload["needs_human_review"] is False
    assert payload["case_assessment"]["status"] == "not_badcase"
    assert "不应认定为 badcase" in payload["human_report_markdown"]


def test_self_oracle_enables_upstream_rerank_drop_without_expected_ids() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "云图 指标分析",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "正确答案应该说明云图是指标分析数据产品",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "事实正确性",
                    "result": "错误",
                    "reason": "云图应该是指标分析数据产品，不是天气图片",
                }
            ]
        },
        "preprocess": {"rewrite_query": "云图 指标分析"},
        "retrieval": {
            "knowledge_exists": None,
            "origin_doc_list": [
                {"id": "d1", "title": "云图定义", "content": "云图是用于指标分析的数据产品。"}
            ],
        },
        "rerank": {
            "rerank_docs": [],
            "prompt_docs": [],
        },
        "qa": {
            "answer": "云图是天气图片。",
        },
        "host_agent": {"answer_claim": [{"text": "云图应被解释为用于指标分析的数据产品。", "role": "expected_required"}]},
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe], mode="final")

    assert payload["oracle_status"]["inferred_doc_ids"] == ["d1"]
    assert payload["primary_cause"]["stage"] == "rerank"
    assert payload["primary_cause"]["cause_code"] == "rerank_drop"
    assert payload["primary_cause"]["confidence"] < 0.86
    report = payload["human_report_markdown"]
    assert "重排断点" in report
    assert "云图应被解释为用于指标分析的数据产品" in report
    assert "命中 `d1` 云图定义" in report


def test_self_oracle_point_gap_selects_knowledge_missing() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "请问随心推如何设置铺底计划",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "正确答案应该说明铺底计划的设置步骤和入口",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "问题无遗漏",
                    "result": "否",
                    "reason": "用户核心问题是随心推如何设置铺底计划，正确答案应该说明铺底计划的设置步骤和入口。",
                }
            ]
        },
        "preprocess": {"rewrite_query": "随心推 设置 铺底计划"},
        "retrieval": {
            "knowledge_exists": None,
            "origin_doc_list": [
                {"id": "d1", "title": "小店随心推产品介绍", "content": "小店随心推支持创建投放订单和查看计划。"}
            ],
        },
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "小店随心推产品介绍", "content": "小店随心推支持创建投放订单和查看计划。"}],
            "prompt_docs": [{"id": "d1", "title": "小店随心推产品介绍", "content": "小店随心推支持创建投放订单和查看计划。"}],
        },
        "qa": {
            "answer": "资料里没有铺底计划。",
        },
        "host_agent": {"answer_claim": [{"text": "随心推铺底计划的正确答案应说明设置步骤和入口。", "role": "expected_required"}]},
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    wide_probe = {
        "schema_version": "v3",
        "log_id": "log-x",
        "workspace_id": "55",
        "probe_name": "probe-wide-recall",
        "status": "ok",
        "stage_signals": {
            "retrieval": {
                "theoretical_recall_status": "ok",
                "theoretical_recall_topk": 50,
                "theoretical_query_variants": ["请问随心推如何设置铺底计划", "随心推 设置 铺底计划"],
                "wide_recall_docs": [
                    {"id": "d2", "title": "小店随心推产品介绍", "content": "小店随心推支持创建投放订单和查看计划。"}
                ],
            }
        },
        "evidence_bundle": [
            {
                "evidence_id": "probe-wide-recall:ev_001",
                "evidence_type": "probe_output",
                "source_stage": "retrieval",
                "source": {"probe_name": "probe-wide-recall"},
                "content": {"theoretical_recall_status": "ok"},
                "quality": {"confidence": 0.8},
            }
        ],
        "raw_artifacts": {},
    }
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe, wide_probe], mode="final")

    assert payload["primary_cause"]["stage"] == "knowledge"
    assert payload["primary_cause"]["cause_code"] == "suspected_knowledge_missing"
    assert payload["oracle_status"]["missing_expected_points_from_theoretical_recall"]
    report = payload["human_report_markdown"]
    assert "理论召回上界也没有找到可承载这些必要断言的文档" in report
    assert "铺底计划" in report


def test_report_explains_upper_bound_doc_assertion_relationship() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "随心推铺底计划怎么设置",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "正确答案应该说明铺底计划的设置入口和步骤",
        },
        "preprocess": {"rewrite_query": "随心推 铺底计划 设置入口 步骤"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [], "origin_faq_list": []},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "资料里没有相关说明。"},
        "host_agent": {
            "answer_claim": [
                {"text": "随心推铺底计划的正确答案应说明设置入口和步骤。", "role": "expected_required"}
            ]
        },
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    wide_probe = {
        "schema_version": "v3",
        "log_id": "log-x",
        "workspace_id": "55",
        "probe_name": "probe-wide-recall",
        "status": "ok",
        "stage_signals": {
            "retrieval": {
                "theoretical_recall_status": "ok",
                "theoretical_recall_topk": 50,
                "theoretical_query_variants": ["随心推铺底计划怎么设置", "随心推 铺底计划 设置入口 步骤"],
                "wide_recall_docs": [
                    {
                        "id": "wide-setup-1",
                        "title": "随心推铺底计划设置指南",
                        "content": "随心推铺底计划支持在计划管理中进入铺底设置入口，并按步骤完成配置。",
                    }
                ],
            }
        },
        "evidence_bundle": [],
        "raw_artifacts": {},
    }
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe, wide_probe], mode="final")
    report = payload["human_report_markdown"]
    matrix_section = report.split("## 4. 断言覆盖矩阵", 1)[1].split("## 5. 召回上界与知识判断", 1)[0]

    assert "理论召回上界 |" not in matrix_section
    assert "### 理论召回上界与断言关系" in report
    assert "随心推铺底计划的正确答案应说明设置入口和步骤" in report
    assert "支持该断言：`wide-setup-1` 随心推铺底计划设置指南" in report
    assert "匹配词：" in report


def test_evaluator_dimensions_are_not_expected_knowledge_points() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "素材衰退，家居品类行业平均周期是多久呀",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "评估器结果：是否回答=否；问题无遗漏=否；相关性=否",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "是否回答",
                    "result": "否",
                    "reason": "Agent_Reply为空，未提供任何事实性信息或查询结果。",
                },
                {
                    "label": "问题无遗漏",
                    "result": "否",
                    "reason": "本例中甚至没有回复内容，显然未回答用户问题。",
                },
                {
                    "label": "相关性",
                    "result": "否",
                    "reason": "Agent_Reply为空，未对该问题点做任何回应，存在遗漏。",
                },
            ]
        },
        "preprocess": {"rewrite_query": "素材衰退 家居品类 行业平均周期 多久"},
        "retrieval": {
            "knowledge_exists": None,
            "origin_doc_list": [
                {"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退通常需要结合行业平均周期看。"}
            ],
        },
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退通常需要结合行业平均周期看。"}],
            "prompt_docs": [{"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退通常需要结合行业平均周期看。"}],
        },
        "qa": {"answer": ""},
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe], mode="final")

    points = payload["oracle_status"]["expected_knowledge_points"]
    assert points == []
    assert payload["oracle_status"]["source"] == "insufficient_assertions"
    assert payload["primary_cause"] is None
    assert payload["needs_human_review"] is True
    assert not any("Agent_Reply" in point["text"] or "是否回答" in point["text"] for point in points)


def test_host_answer_claims_drive_point_coverage() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "素材衰退，家居品类行业平均周期是多久呀",
            "workspace_id": "55",
            "app_id": "100",
            "judgement": "评估器结果：是否回答=否；相关性=否",
        },
        "judgement_evidence": {
            "signals": [
                {
                    "label": "是否回答",
                    "result": "否",
                    "reason": "Agent_Reply为空，未提供任何事实性信息或查询结果。",
                }
            ]
        },
        "preprocess": {"rewrite_query": "素材衰退 家居品类 行业平均周期 多久"},
        "retrieval": {
            "knowledge_exists": None,
            "origin_doc_list": [
                {"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退的行业平均周期为 14 天。"}
            ],
        },
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退的行业平均周期为 14 天。"}],
            "prompt_docs": [{"id": "d1", "title": "家居素材衰退周期", "content": "家居品类素材衰退的行业平均周期为 14 天。"}],
        },
        "qa": {
            "answer": "",
        },
        "host_agent": {
            "answer_claim": [
                {"text": "素材衰退的判定口径", "role": "expected_required"},
                {"text": "家居品类素材衰退的行业平均周期", "role": "expected_required", "confidence": 0.92},
            ],
        },
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe], mode="final")

    points = payload["oracle_status"]["expected_knowledge_points"]
    assert [point["text"] for point in points] == [
        "素材衰退的判定口径",
        "家居品类素材衰退的行业平均周期",
    ]
    assert all(point["source"] == "host_agent.answer_claim" for point in points)
    assert all(point["role"] == "expected_required" for point in points)
    assert not any("Agent_Reply" in row["text"] for row in payload["oracle_status"]["point_coverage"])


def test_expected_required_semantic_merge_keeps_refined_entry_assertion() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    support_doc = {
        "id": "qianchuan-entry",
        "title": "千川启航计划一元试投入口",
        "content": "在抖音电商千川启航计划/全域投放推商品场景下，一元试投可以从活动中心-启航计划-马上试进入，也可以使用对应电商端入口路径。",
    }
    request = {
        "case_input": {"query": "截图里一元试投在哪里", "workspace_id": "55", "app_id": "100"},
        "retrieval": {
            "knowledge_exists": None,
            "theoretical_recall_status": "ok",
            "wide_recall_docs": [support_doc],
            "origin_doc_list": [support_doc],
        },
        "rerank": {"rerank_docs": [support_doc], "prompt_docs": [support_doc]},
        "qa": {"answer": ""},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "正确输出应回答一元试投的具体入口，例如活动中心-启航计划-马上试。",
                    "role": "expected_required",
                    "basis": ["evaluator_reason"],
                    "confidence": 0.82,
                },
                {
                    "text": "正确输出应在用户截图所处的抖音电商千川启航计划/全域投放推商品场景下，回答一元试投的具体入口或对应电商端入口路径。",
                    "role": "expected_required",
                    "basis": ["trace_query", "chat_history"],
                    "confidence": 0.9,
                },
            ]
        },
    }

    normalized = v3._normalize_assertion_inputs(request)
    points = v3._expected_knowledge_points(normalized)
    coverage = v3._knowledge_point_coverage(normalized, points)

    assert [point["text"] for point in points] == [
        "正确输出应在用户截图所处的抖音电商千川启航计划/全域投放推商品场景下，回答一元试投的具体入口，例如活动中心-启航计划-马上试或对应电商端入口路径"
    ]
    assert len(coverage) == 1
    assert coverage[0]["missing_stage"] == "covered"
    assert sorted(points[0]["basis"]) == ["chat_history", "evaluator_reason", "trace_query"]
    assert [item["text"] for item in points[0]["merged_from"]] == [
        "正确输出应回答一元试投的具体入口，例如活动中心-启航计划-马上试",
        "正确输出应在用户截图所处的抖音电商千川启航计划/全域投放推商品场景下，回答一元试投的具体入口或对应电商端入口路径",
    ]


def test_expected_required_semantic_merge_keeps_distinct_entry_requirements() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    request = {
        "host_agent": {
            "answer_claim": [
                {
                    "text": "正确输出应回答一元试投的具体入口，例如活动中心-启航计划-马上试。",
                    "role": "expected_required",
                    "basis": ["evaluator_reason"],
                },
                {
                    "text": "正确输出应在用户截图所处的千川启航计划场景下，回答一元试投的预算设置入口。",
                    "role": "expected_required",
                    "basis": ["trace_query"],
                },
            ]
        }
    }

    points = v3._expected_knowledge_points(v3._normalize_assertion_inputs(request))

    assert [point["text"] for point in points] == [
        "正确输出应回答一元试投的具体入口，例如活动中心-启航计划-马上试",
        "正确输出应在用户截图所处的千川启航计划场景下，回答一元试投的预算设置入口",
    ]
    assert not any("merged_from" in point for point in points)


def test_orchestrate_uses_ingest_host_assertions_without_self_oracle() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    support_doc = {
        "id": "d1",
        "title": "新享投",
        "content": "2、功能入口 当客户完成注册后，首次进入巨量千川后，即可在首页查看到新客试投功能。",
    }
    request = {
        "case_input": {"query": "一元试投在哪里", "workspace_id": "55", "app_id": "100"},
        "retrieval": {
            "knowledge_exists": None,
            "theoretical_recall_status": "ok",
            "wide_recall_docs": [support_doc],
            "origin_doc_list": [support_doc],
        },
        "rerank": {"rerank_docs": [support_doc], "prompt_docs": [support_doc]},
        "qa": {"answer": "在首页。"},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "普通新客的一元试投/新客试投入口是在完成注册后首次进入巨量千川首页即可看到。",
                    "role": "expected_required",
                }
            ]
        },
    }
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[], mode="final")

    assert payload["oracle_status"]["source"] == "host_assertions"
    assert [point["text"] for point in payload["oracle_status"]["expected_knowledge_points"]] == [
        "普通新客的一元试投/新客试投入口是在完成注册后首次进入巨量千川首页即可看到"
    ]
    row = payload["oracle_status"]["point_coverage"][0]
    assert row["missing_stage"] == "covered"
    assert row["prompt_docs"][0]["support_status"] == "full_support"


def test_assertion_coverage_overrides_doc_id_only_rerank_drop() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    support_doc = {
        "id": "support-doc",
        "title": "新享投",
        "content": "2、功能入口 当客户完成注册后，首次进入巨量千川后，即可在首页查看到新客试投功能。",
    }
    dropped_expected_doc = {
        "id": "expected-doc",
        "title": "新享投历史说明",
        "content": "新享投是面向新客户的试投活动。",
    }
    request = {
        "case_input": {
            "query": "一元试投在哪里",
            "workspace_id": "55",
            "app_id": "100",
            "expected_knowledge_ids": ["expected-doc"],
        },
        "preprocess": {"rewrite_query": "一元试投 入口"},
        "retrieval": {
            "knowledge_exists": True,
            "theoretical_recall_status": "ok",
            "online_retrieval_hit": True,
            "expected_knowledge_hit": True,
            "wide_recall_docs": [support_doc],
            "origin_doc_list": [support_doc, dropped_expected_doc],
        },
        "rerank": {
            "rerank_docs": [support_doc],
            "prompt_docs": [support_doc],
            "expected_doc_survived_rerank": False,
        },
        "qa": {"answer": "在首页。"},
        "host_agent": {
            "answer_claim": [
                {
                    "text": "普通新客的一元试投/新客试投入口是在完成注册后首次进入巨量千川首页即可看到。",
                    "role": "expected_required",
                }
            ]
        },
    }
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[], mode="final")

    row = payload["oracle_status"]["point_coverage"][0]
    rerank = next(item for item in payload["evidence_chain"] if item["stage"] == "rerank")
    context = next(item for item in payload["evidence_chain"] if item["stage"] == "context")
    assert row["missing_stage"] == "covered"
    assert rerank["status"] == "pass"
    assert context["status"] == "pass"
    assert payload["primary_cause"] is None


def test_point_coverage_requires_answerable_support_span() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {
            "query": "同店铺下两个千川户是否会互相影响",
            "workspace_id": "55",
            "app_id": "100",
        },
        "preprocess": {"rewrite_query": "同店铺 两个千川户 互相影响"},
        "retrieval": {
            "knowledge_exists": None,
            "theoretical_recall_status": "ok",
            "wide_recall_docs": [
                {
                    "id": "good",
                    "title": "一个主体下开的不同的千川户相互影响不",
                    "content": "如果两个千川户是在同一个店铺下，那么它们的评分会相互影响。",
                }
            ],
            "origin_doc_list": [
                {
                    "id": "lexical",
                    "title": "同店铺千川户互相影响说明",
                    "content": "千川人群包支持从数据模块进入标签广场圈选人群包。",
                }
            ],
        },
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": ""},
        "host_agent": {
            "answer_claim": [
                {"text": "正确答案应说明同店铺下两个千川户是否会互相影响。", "role": "expected_required"}
            ]
        },
    }
    ingest = minimal_ingest(request)
    oracle_probe = build_probe_result("probe-self-oracle", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[oracle_probe], mode="final")

    row = payload["oracle_status"]["point_coverage"][0]
    assert row["missing_stage"] == "retrieval"
    assert row["origin_docs"] == []
    assert row["upper_bound_docs"][0]["id"] == "good"
    assert row["upper_bound_docs"][0]["support_status"] == "full_support"
    assert "两个千川户是在同一个店铺下" in row["upper_bound_docs"][0]["support_spans"][0]


def test_answer_cause_requires_prompt_support_precondition() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    base = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100", "expected_knowledge_ids": ["d1"]},
        "preprocess": {"rewrite_query": "q"},
        "retrieval": {"knowledge_exists": True, "online_retrieval_hit": True, "expected_knowledge_hit": True},
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "doc", "content": "support"}],
            "prompt_docs": [{"id": "d1", "title": "doc", "content": "support"}],
            "expected_doc_survived_rerank": True,
            "expected_doc_in_prompt": True,
        },
        "qa": {
            "answer": "bad",
            "answer_satisfies_expected": False,
        },
        "host_agent": {"answer_claim": [{"text": "bad", "role": "unsupported_claim"}]},
    }
    supported = json.loads(json.dumps(base))
    supported["qa"]["prompt_supports_answer"] = True
    selected = orchestrate_v3(ingest=minimal_ingest(supported), probes=[], mode="final")
    assert selected["primary_cause"]["stage"] == "answer"
    assert selected["primary_cause"]["cause_code"] == "unsupported_claim"

    unsupported = json.loads(json.dumps(base))
    unsupported["qa"]["prompt_supports_answer"] = False
    blocked = orchestrate_v3(ingest=minimal_ingest(unsupported), probes=[], mode="final")
    answer = next(item for item in blocked["evidence_chain"] if item["stage"] == "answer")
    assert answer["status"] == "indeterminate"
    assert "candidate_cause" not in answer


def _probe_plan_probe(stage_signals: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v3",
        "log_id": "log-x",
        "workspace_id": "55",
        "probe_name": "run-probe-plan",
        "status": "ok",
        "stage_signals": stage_signals,
        "evidence_bundle": [],
        "raw_artifacts": {},
    }


def _answer_ready_request(**qa_overrides: Any) -> dict[str, Any]:
    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100", "expected_knowledge_ids": ["d1"]},
        "preprocess": {"rewrite_query": "q"},
        "retrieval": {"knowledge_exists": True, "online_retrieval_hit": True, "expected_knowledge_hit": True},
        "rerank": {
            "rerank_docs": [{"id": "d1", "title": "doc", "content": "support"}],
            "prompt_docs": [{"id": "d1", "title": "doc", "content": "support"}],
            "expected_doc_survived_rerank": True,
            "expected_doc_in_prompt": True,
        },
        "qa": {"answer": "bad", "prompt_supports_answer": True, "answer_satisfies_expected": False},
        "host_agent": {"answer_claim": [{"text": "bad", "role": "unsupported_claim"}]},
    }
    request["qa"].update(qa_overrides)
    return request


def test_probe_plan_scope_violation_maps_answer_scope_violation() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = _answer_ready_request()
    probe = _probe_plan_probe({"answer": {"scope_violation": True}})
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[probe], mode="final")

    assert payload["primary_cause"]["stage"] == "answer"
    assert payload["primary_cause"]["cause_code"] == "answer_scope_violation"


def test_answer_span_scope_violation_miss_maps_answer_scope_violation() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3, run_probe_plan

    request = _answer_ready_request()
    request["case_input"]["query"] = "同店铺下的两个千川号人群会不会互相影响"
    request["qa"]["answer"] = "会的。同店铺下的两个千川账户，其信用评分会相互影响。"
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-answer-object",
                "direction": "scope_violation",
                "role": "constraint_check",
                "target_artifact": "answer_span",
                "query": "答案必须直接回答同店铺下两个千川号的人群是否会互相影响",
                "expected_hit_pattern": "人群",
                "if_hit": "answer keeps the requested audience object",
                "if_miss": "answer shifts from audience/persona to account score",
            }
        ],
    }

    ingest = minimal_ingest(request)
    probe = run_probe_plan(ingest=ingest, plan=plan, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[probe], mode="final")

    assert probe["content"]["probe_results"][0]["hit"] is False
    assert probe["stage_signals"]["answer"]["scope_violation"] is True
    assert payload["primary_cause"]["stage"] == "answer"
    assert payload["primary_cause"]["cause_code"] == "answer_scope_violation"


def test_answer_span_scope_violation_hit_supports_spaced_terms() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import run_probe_plan

    request = {
        "case_input": {"query": "同店铺下的两个千川号人群会不会互相影响", "workspace_id": "55", "app_id": "100"},
        "qa": {"answer": "会的。同店铺下的不同千川号，它们的信用评分是会相互影响的。"},
    }
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-scope-shift",
                "direction": "scope_violation",
                "role": "constraint_check",
                "target_artifact": "answer_span",
                "query": "信用评分 店铺综合评分 投放消耗",
                "expected_hit_pattern": "信用评分 综合评分 投放消耗",
                "if_hit": "答案包含信用评分解释，存在对象偏移风险。",
                "if_miss": "答案未出现信用评分对象偏移。",
            }
        ],
    }

    probe = run_probe_plan(ingest=minimal_ingest(request), plan=plan, no_cache=True)

    result = probe["content"]["probe_results"][0]
    assert result["hit"] is True
    assert "信用评分" in result["matched_terms"]
    assert probe["stage_signals"]["answer"]["scope_violation"] is True


def test_required_assertions_covered_can_satisfy_answer_precondition() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    doc = {"id": "d1", "title": "人群模型说明", "content": "同店铺下两个千川号的人群模型不会互相继承，需要分别学习。"}
    request = {
        "case_input": {"query": "同店铺下两个千川号人群会不会互相影响", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "同店铺 千川号 人群模型 影响"},
        "retrieval": {"knowledge_exists": True, "theoretical_recall_status": "ok", "wide_recall_docs": [doc], "origin_doc_list": [doc]},
        "rerank": {"rerank_docs": [doc], "prompt_docs": [doc]},
        "qa": {"answer": "会的，信用评分会互相影响。", "answer_satisfies_expected": False},
        "host_agent": {
            "answer_claim": [
                {"text": "正确答案应回答同店铺下两个千川号的人群模型是否会互相影响。", "role": "expected_required"},
                {"text": "用户问题限定在人群模型，不应只回答信用评分。", "role": "constraint_check"},
            ]
        },
    }
    probe = _probe_plan_probe({"answer": {"scope_violation": True}})
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[probe], mode="final")

    assert payload["oracle_status"]["point_coverage"][0]["missing_stage"] == "covered"
    assert payload["primary_cause"]["stage"] == "answer"
    assert payload["primary_cause"]["cause_code"] == "answer_scope_violation"


def test_trace_incomplete_pattern_requires_blocking_stage() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    doc = {"id": "d1", "title": "人群模型说明", "content": "同店铺下两个千川号的人群模型不会互相继承，需要分别学习。"}
    request = {
        "case_input": {"query": "同店铺下两个千川号人群会不会互相影响", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "同店铺 千川号 人群模型 影响"},
        "retrieval": {"knowledge_exists": True, "theoretical_recall_status": "ok", "wide_recall_docs": [doc], "origin_doc_list": [doc]},
        "rerank": {"rerank_docs": [doc], "prompt_docs": [doc]},
        "qa": {"answer": "会的，信用评分会互相影响。", "answer_satisfies_expected": False},
        "host_agent": {
            "answer_claim": [
                {"text": "正确答案应回答同店铺下两个千川号的人群模型是否会互相影响。", "role": "expected_required"},
                {"text": "用户问题限定在人群模型，不应只回答信用评分。", "role": "constraint_check"},
            ]
        },
    }
    ingest = minimal_ingest(request)
    ingest["ingest_summary"]["trace_completeness"]["knowledge"] = "missing_evidence"
    probe = _probe_plan_probe({"answer": {"scope_violation": True}})
    payload = orchestrate_v3(ingest=ingest, probes=[probe], mode="final")

    assert payload["primary_cause"]["cause_code"] == "answer_scope_violation"
    assert "trace_incomplete_blocking_attribution" not in payload["failure_patterns"]
    assert payload["needs_human_review"] is False


def test_probe_plan_coverage_gap_can_drive_rerank_stage() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3, run_probe_plan

    doc = {"id": "d1", "title": "云图定义", "content": "云图是用于指标分析的数据产品。"}
    request = {
        "case_input": {"query": "云图是什么", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "云图是什么"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [doc], "origin_faq_list": []},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "云图是天气图片。"},
        "host_agent": {
            "answer_claim": [{"text": "正确答案应说明云图是用于指标分析的数据产品。", "role": "expected_required"}]
        },
    }
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-origin",
                "direction": "coverage_gap",
                "role": "expected_required",
                "target_artifact": "online_origin_recall",
                "query": "云图是用于指标分析的数据产品",
                "expected_hit_pattern": "指标分析 数据产品",
                "if_hit": "origin supports required assertion",
                "if_miss": "origin misses required assertion",
            },
            {
                "probe_id": "P-rerank",
                "direction": "coverage_gap",
                "role": "expected_required",
                "target_artifact": "rerank_output",
                "query": "云图是用于指标分析的数据产品",
                "expected_hit_pattern": "指标分析 数据产品",
                "if_hit": "rerank keeps required assertion",
                "if_miss": "rerank drops required assertion",
            },
        ],
    }

    ingest = minimal_ingest(request)
    probe = run_probe_plan(ingest=ingest, plan=plan, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[probe], mode="final")

    assert probe["stage_signals"]["rerank"]["missing_expected_points_from_rerank"] == [
        "云图是用于指标分析的数据产品"
    ]
    assert payload["primary_cause"]["stage"] == "rerank"
    assert payload["primary_cause"]["cause_code"] == "rerank_drop"


def test_doc_id_only_rerank_bypass_does_not_select_primary_without_assertion_gap() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import build_probe_result, orchestrate_v3

    request = {
        "case_input": {"query": "云图是什么", "workspace_id": "55", "app_id": "100", "expected_knowledge_ids": ["d1"]},
        "preprocess": {"rewrite_query": "云图是什么"},
        "retrieval": {
            "knowledge_exists": True,
            "origin_doc_list": [{"id": "d1", "title": "云图定义", "content": "云图是用于指标分析的数据产品。"}],
        },
        "rerank": {"rerank_docs": [], "prompt_docs": [], "expected_doc_survived_rerank": False},
        "qa": {"answer": "云图是天气图片。"},
    }
    ingest = minimal_ingest(request)
    probe = build_probe_result("probe-rerank-bypass", ingest=ingest, no_cache=True)
    payload = orchestrate_v3(ingest=ingest, probes=[probe], mode="final")

    rerank = next(item for item in payload["evidence_chain"] if item["stage"] == "rerank")
    assert rerank["status"] == "indeterminate"
    assert "candidate_cause" not in rerank
    assert payload["primary_cause"] is None


def test_probe_plan_internal_contradiction_maps_answer_branching_unclear() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = _answer_ready_request()
    probe = _probe_plan_probe({"answer": {"branching_unclear": True}})
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[probe], mode="final")

    assert payload["primary_cause"]["stage"] == "answer"
    assert payload["primary_cause"]["cause_code"] == "answer_branching_unclear"


def test_probe_plan_internal_contradiction_miss_maps_knowledge_inconsistency() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "q"},
        "retrieval": {"knowledge_exists": None},
        "rerank": {"rerank_docs": [], "prompt_docs": []},
        "qa": {"answer": "矛盾的答案"},
        "host_agent": {"answer_claim": [{"text": "应说明唯一适用前提。", "role": "expected_required"}]},
    }
    probe = _probe_plan_probe({"knowledge": {"internal_inconsistency": True}})
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[probe], mode="final")

    assert payload["primary_cause"]["stage"] == "knowledge"
    assert payload["primary_cause"]["cause_code"] == "knowledge_internal_inconsistency"


def test_probe_plan_citation_miss_maps_suspected_knowledge_missing() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import orchestrate_v3

    doc = {"id": "d1", "title": "铺底计划设置说明", "content": "铺底计划的设置入口在计划管理页，按步骤设置即可。"}
    request = {
        "case_input": {"query": "铺底计划怎么设置", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "铺底计划 设置 入口"},
        "retrieval": {"knowledge_exists": None, "origin_doc_list": [doc]},
        "rerank": {"rerank_docs": [doc], "prompt_docs": [doc]},
        "qa": {"answer": "没有权威来源。"},
        "host_agent": {"answer_claim": [{"text": "应说明铺底计划的设置入口。", "role": "expected_required"}]},
    }
    probe = _probe_plan_probe({"knowledge": {"lacks_authoritative_source": True}})
    payload = orchestrate_v3(ingest=minimal_ingest(request), probes=[probe], mode="final")

    assert payload["primary_cause"]["stage"] == "knowledge"
    assert payload["primary_cause"]["cause_code"] == "suspected_knowledge_missing"


def test_run_probe_plan_rejects_non_probe_v1_schema() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import V3Error, run_probe_plan

    request = {"case_input": {"query": "q", "workspace_id": "55", "app_id": "100"}, "qa": {"answer": "a"}}
    with pytest.raises(V3Error) as error:
        run_probe_plan(ingest=minimal_ingest(request), plan={"schema_version": "v1", "probes": []})
    assert error.value.error_code == "E_PROBE_PLAN_SCHEMA"


def test_run_probe_plan_rejects_non_object_probe_item() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import V3Error, run_probe_plan

    request = {"case_input": {"query": "q", "workspace_id": "55", "app_id": "100"}, "qa": {"answer": "a"}}
    with pytest.raises(V3Error) as error:
        run_probe_plan(ingest=minimal_ingest(request), plan={"schema_version": "probe-v1", "probes": [None]})
    assert error.value.error_code == "E_PROBE_PLAN_INVALID"


def test_run_probe_plan_requires_direction_and_target() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import V3Error, run_probe_plan

    request = {"case_input": {"query": "q", "workspace_id": "55"}, "qa": {"answer": "a"}}
    for probe in (
        {"probe_id": "P-1", "target_artifact": "answer_span", "query": "a"},
        {"probe_id": "P-2", "direction": "scope_violation", "query": "a"},
    ):
        with pytest.raises(V3Error) as error:
            run_probe_plan(ingest=minimal_ingest(request), plan={"schema_version": "probe-v1", "probes": [probe]})
        assert error.value.error_code == "E_PROBE_PLAN_INVALID"


def test_run_probe_plan_answer_span_branching_signal() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import run_probe_plan

    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "qa": {"answer": "可以在直播详情页追投，也可以在短视频页追投。"},
    }
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-1",
                "direction": "internal_contradiction",
                "role": "consistency_check",
                "target_artifact": "answer_span",
                "query": "",
                "expected_hit_pattern": "追投",
                "if_hit": "answer mixes branches without clarifying premises",
                "if_miss": "answer is consistent",
            }
        ],
    }
    result = run_probe_plan(ingest=minimal_ingest(request), plan=plan, no_cache=True)

    assert result["status"] == "ok"
    assert result["probe_name"] == "run-probe-plan"
    executed = result["content"]["probe_results"][0]
    assert executed["hit"] is True
    assert executed["target_artifact"] == "answer_span"
    assert result["stage_signals"]["answer"]["branching_unclear"] is True


def test_run_probe_plan_matched_docs_include_support_spans() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import run_probe_plan

    request = {
        "case_input": {"query": "同店铺下两个千川户是否会互相影响", "workspace_id": "55", "app_id": "100"},
        "retrieval": {
            "origin_doc_list": [
                {
                    "id": "lexical",
                    "title": "同店铺千川户互相影响说明",
                    "content": "千川人群包支持从数据模块进入标签广场圈选人群包。",
                },
                {
                    "id": "good",
                    "title": "一个主体下开的不同的千川户相互影响不",
                    "content": "如果两个千川户是在同一个店铺下，那么它们的评分会相互影响。",
                },
            ]
        },
        "qa": {"answer": ""},
    }
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-1",
                "direction": "coverage_gap",
                "role": "expected_required",
                "target_artifact": "online_origin_recall",
                "query": "同店铺下两个千川户是否会互相影响",
                "expected_hit_pattern": "文档应直接回答同店铺下两个千川户是否会互相影响",
                "if_hit": "knowledge exists",
                "if_miss": "retrieval miss",
            }
        ],
    }
    result = run_probe_plan(ingest=minimal_ingest(request), plan=plan, no_cache=True)
    matched = result["content"]["probe_results"][0]["matched_docs"]

    assert [item["id"] for item in matched] == ["good"]
    assert matched[0]["support_status"] == "full_support"
    assert matched[0]["support_spans"]
    assert "评分会相互影响" in matched[0]["support_spans"][0]


def test_run_probe_plan_does_not_treat_unavailable_wide_recall_as_miss() -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    from findreason_core.v3 import run_probe_plan

    request = {
        "case_input": {"query": "千川赔付规则是什么", "workspace_id": "55", "app_id": "100"},
        "preprocess": {"rewrite_query": "千川赔付规则"},
        "qa": {"answer": "可以在广告管理中心申请广告投放。"},
    }
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-1",
                "direction": "scope_violation",
                "target_artifact": "kb_wide_recall",
                "query": "千川 广告管理中心 赔付规则",
                "expected_hit_pattern": "千川赔付规则",
                "if_hit": "scope ok",
                "if_miss": "scope violation",
            }
        ],
    }
    result = run_probe_plan(ingest=minimal_ingest(request), plan=plan, no_cache=True)
    executed = result["content"]["probe_results"][0]

    assert result["content"]["theoretical_recall_status"] == "not_configured"
    assert executed["executed"] is False
    assert executed["hit"] is None
    assert executed["skip_reason"] == "kb_wide_recall_unavailable"
    assert "answer" not in result["stage_signals"]


def test_run_probe_plan_cli_emits_stage_signals(tmp_path: Path) -> None:
    request = {
        "case_input": {"query": "q", "workspace_id": "55", "app_id": "100"},
        "qa": {"answer": "可以在直播详情页追投。"},
    }
    ingest = minimal_ingest(request)
    ingest_path = tmp_path / "ingest.json"
    ingest_path.write_text(json.dumps(ingest, ensure_ascii=False), encoding="utf-8")
    plan = {
        "schema_version": "probe-v1",
        "probes": [
            {
                "probe_id": "P-1",
                "direction": "internal_contradiction",
                "target_artifact": "answer_span",
                "query": "",
                "expected_hit_pattern": "追投",
                "if_hit": "x",
                "if_miss": "y",
            }
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = run_cli(
        "run-probe-plan",
        "--ingest-file",
        str(ingest_path),
        "--plan",
        f"@{plan_path}",
        "--output-dir",
        str(tmp_path / "out"),
        cwd=SKILL_ROOT,
        env={"HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["probe_name"] == "run-probe-plan"
    assert payload["stage_signals"]["answer"]["branching_unclear"] is True
    assert (tmp_path / "out" / "run-probe-plan.json").exists()



def test_judgement_signals_over_2kb_fails_before_trace_request(tmp_path: Path) -> None:
    too_large = [{"key": "grader_or_rubric", "value": "x" * 3000}]
    path = case_file(tmp_path, judgement_evidence={"signals": too_large})
    with TraceServer(trace_payload()) as server:
        result = run_cli(
            "ingest-fornax-trace",
            "--workspace-id",
            "55",
            "--log-id",
            "log-1",
            "--case-file",
            str(path),
            cwd=SKILL_ROOT,
            env={"OPEN_PLAT_TRACE_DETAIL_URL": server.url, "HOME": str(tmp_path)},
        )

    assert result.returncode == 2
    assert not server.requests
    error = json.loads(result.stderr)
    assert error["error_code"] == "E_EVIDENCE_TOO_LARGE"


def test_fetch_workflow_nodes_preserves_nodes_edges_and_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import findreason_core.v3 as v3

    def fake_resolve_workflow(request: Any) -> dict[str, Any]:
        return {
            "source": "rds",
            "database": "zs_open",
            "wip_id": "9",
            "version_id": "7",
            "status": 1,
            "input_schema": [{"key": "query"}],
            "workflow_config": {
                "nodes": [{"id": "start", "type": "Start"}, {"id": "qa", "type": "ZhiShangRAGQA"}],
                "edges": [{"source": "start", "target": "qa"}],
                "global_config": {"answer_model": "model-x"},
            },
        }

    monkeypatch.setattr(v3, "resolve_workflow", fake_resolve_workflow)
    payload = v3.fetch_workflow_nodes_v3(workspace_id="55", app_id="100", output_dir=str(tmp_path))

    assert payload["schema_version"] == "v3"
    assert payload["workflow"]["nodes"][0]["id"] == "start"
    assert payload["workflow"]["edges"][0]["target"] == "qa"
    assert payload["workflow"]["global_config"]["answer_model"] == "model-x"
    assert (tmp_path / "workflow_nodes.json").exists()


def test_static_trace_token_has_no_bearer_prefix_when_configured() -> None:
    config = json.loads((SKILL_ROOT / "config" / "runtime_defaults.json").read_text(encoding="utf-8"))
    token = config["OPEN_PLAT_TRACE_TOKEN"]
    assert not token.lower().startswith("bearer ")
