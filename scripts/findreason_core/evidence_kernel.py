from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .artifacts import decode_jsonish, normalize_rag_artifacts, to_jsonable
from .fornax_trace import FornaxTraceIngestRequest, ingest_fornax_trace


SCHEMA_VERSION = "agent-judgement-v4"
SKILL_RELEASE_MARKER = "findreason-rag-attribution@2026-07-01-lean-artifacts-v1"
SKILL_RELEASE_POLICY = "Lean JSON-only evidence input; workflow-aware RAG stage map; agent_judgement.md is the only human-readable case report."
OPEN_PLAT_ZS_OPEN_TOKEN = "37160d0535224506965a54e58e0685c4"
OPEN_PLAT_TRACE_DETAIL_URL = "http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail"
RECALL_ENDPOINT_MARKERS = ("/api/sirius_plugin/v1/recall", "/api/sirius_plugin/v1/searchDoc")


class EvidenceKernelError(Exception):
    def __init__(self, error_code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "skill_release_marker": SKILL_RELEASE_MARKER,
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def read_json_file(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def read_json_arg(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    text = str(value)
    if text.startswith("@"):
        return read_json_file(text[1:])
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json_dumps(value) + "\n", encoding="utf-8")


def _numeric_if_possible(value: str) -> int | str:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


def fetch_trace_detail(*, workspace_id: str, log_id: str, limit: int = 1000, timeout_seconds: int = 90) -> tuple[dict[str, Any], dict[str, Any]]:
    if not OPEN_PLAT_ZS_OPEN_TOKEN:
        raise EvidenceKernelError("E_TRACE_AUTH_REQUIRED", "Missing fixed OpenPlat trace token.")
    request_payload = {"workspaceId": _numeric_if_possible(workspace_id), "logId": log_id, "limit": int(limit or 1000)}
    authorization = OPEN_PLAT_ZS_OPEN_TOKEN
    if not authorization.lower().startswith("bearer "):
        authorization = f"Bearer {authorization}"
    try:
        with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
            response = client.post(
                OPEN_PLAT_TRACE_DETAIL_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-zs-plt-open": "zs_open",
                    "Authorization": authorization,
                },
                json=request_payload,
            )
    except Exception as exc:
        raise EvidenceKernelError(
            "E_TRACE_LOOKUP_FAILED",
            "OpenPlat trace detail lookup failed.",
            details={"endpoint": OPEN_PLAT_TRACE_DETAIL_URL, "request_payload": request_payload, "error": str(exc)[:500]},
        ) from exc
    if response.status_code >= 400:
        raise EvidenceKernelError(
            "E_TRACE_LOOKUP_FAILED",
            f"OpenPlat trace detail HTTP {response.status_code}.",
            details={"endpoint": OPEN_PLAT_TRACE_DETAIL_URL, "request_payload": request_payload, "response": response.text[:1000]},
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise EvidenceKernelError(
            "E_TRACE_LOOKUP_FAILED",
            "OpenPlat trace detail returned non-JSON response.",
            details={"endpoint": OPEN_PLAT_TRACE_DETAIL_URL, "request_payload": request_payload, "response": response.text[:1000]},
        ) from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise EvidenceKernelError(
            "E_TRACE_LOOKUP_FAILED",
            f"OpenPlat trace detail returned code={payload.get('code')}.",
            details={"endpoint": OPEN_PLAT_TRACE_DETAIL_URL, "request_payload": request_payload, "msg": str(payload.get("msg") or "")[:1000]},
        )
    return payload if isinstance(payload, dict) else {"data": {"spans": []}}, {
        "endpoint": OPEN_PLAT_TRACE_DETAIL_URL,
        "request_payload": request_payload,
        "authorization_header": "Bearer <redacted>",
        "x_zs_plt_open_header": "zs_open",
    }


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    decoded = decode_jsonish(value)
    if isinstance(decoded, dict):
        found.append(decoded)
        for child in decoded.values():
            found.extend(_walk_dicts(child))
    elif isinstance(decoded, list):
        for child in decoded:
            found.extend(_walk_dicts(child))
    return found


def _sanitize_headers(value: Any) -> dict[str, str]:
    decoded = decode_jsonish(value)
    if not isinstance(decoded, dict):
        return {}
    sanitized: dict[str, str] = {}
    for key, raw in decoded.items():
        text_key = str(key)
        lower = text_key.lower()
        if lower == "authorization":
            sanitized[text_key] = "Bearer <redacted>" if raw else ""
        elif "token" in lower or "secret" in lower or "apikey" in lower or "api-key" in lower:
            sanitized[text_key] = "<redacted>" if raw else ""
        else:
            sanitized[text_key] = str(raw)
    return sanitized


def _request_body_from_mapping(mapping: dict[str, Any]) -> dict[str, Any] | None:
    body_keys = ("body", "request_body", "requestBody", "json", "payload", "data")
    for key in body_keys:
        decoded = decode_jsonish(mapping.get(key))
        if isinstance(decoded, dict) and (
            "recallRequests" in decoded
            or "businessDocPost" in decoded
            or "businessPostRequests" in decoded
            or "fieldDocPost" in decoded
        ):
            return to_jsonable(decoded)
    if "recallRequests" in mapping:
        return to_jsonable(mapping)
    return None


def _url_from_mapping(mapping: dict[str, Any]) -> str:
    for key in ("url", "endpoint", "request_url", "requestUrl"):
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_recall_templates(trace_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract reusable recall/searchDoc request shapes from trace without leaking auth secrets."""
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for span in _walk_dicts(trace_payload):
        candidates: list[dict[str, Any]] = [span]
        decoded_input = decode_jsonish(span.get("input"))
        if isinstance(decoded_input, dict):
            candidates.append(decoded_input)
        for candidate in candidates:
            endpoint = _url_from_mapping(candidate)
            if not endpoint or not any(marker in endpoint for marker in RECALL_ENDPOINT_MARKERS):
                continue
            request_body = _request_body_from_mapping(candidate)
            if not request_body:
                continue
            key = endpoint + "\n" + json.dumps(request_body, sort_keys=True, ensure_ascii=False, default=str)
            if key in seen:
                continue
            seen.add(key)
            kind = "split_recall" if "/api/sirius_plugin/v1/recall" in endpoint else "search_doc"
            templates.append(
                {
                    "kind": kind,
                    "endpoint": endpoint,
                    "request_body": request_body,
                    "headers": _sanitize_headers(candidate.get("headers")),
                    "source_span_id": str(span.get("span_id") or span.get("id") or ""),
                    "source_span_type": str(span.get("span_type") or span.get("type") or ""),
                    "note": "Template is extracted from trace for recall experiments; auth headers are redacted.",
                }
            )
    return templates


def normalize_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("case"), dict):
        return payload["case"]
    if isinstance(payload.get("case_input"), dict):
        case = dict(payload["case_input"])
        for key in (
            "query_hint",
            "answer_hint",
            "chat_history",
            "judgement",
            "judgement_evidence",
            "qa",
            "host_agent",
            "source_row",
            "case_id",
            "core_documents",
            "expected_knowledge_ids",
            "expected_knowledge_points",
            "version_id",
            "versionId",
            "app_version",
            "appVersion",
        ):
            if key in payload:
                case[key] = payload[key]
        return case
    return dict(payload)


def build_case_facts(
    *,
    workspace_id: str,
    log_id: str,
    app_id: str = "",
    case: dict[str, Any] | None = None,
    trace_payload: dict[str, Any] | None = None,
    trace_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case = normalize_case_payload(case or {})
    request = FornaxTraceIngestRequest(
        trace_file="openplat_trace_detail",
        workspace_id=str(workspace_id),
        app_id=str(app_id or case.get("app_id") or ""),
        query=str(case.get("query") or case.get("query_hint") or "unknown query"),
        judgement=str(case.get("judgement") or ""),
        log_id=str(log_id),
        case_id=case.get("case_id"),
        source_row=case.get("source_row"),
        expected_knowledge_points=[],
        error_points=[],
    )
    parsed = ingest_fornax_trace(trace_payload or {"data": {"spans": []}}, request)
    attribution_request = parsed.attribution_request
    retrieval = attribution_request.retrieval
    rerank = attribution_request.rerank
    normalized = normalize_rag_artifacts(
        origin_doc_list=retrieval.origin_doc_list,
        origin_faq_list=retrieval.origin_faq_list,
        rerank_docs=rerank.rerank_docs,
        prompt_docs=rerank.prompt_docs,
    )
    workflow_evidence = attribution_request.workflow_replay.extracted_evidence
    trace_summary = parsed.trace_summary
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_release_marker": SKILL_RELEASE_MARKER,
        "skill_release_policy": SKILL_RELEASE_POLICY,
        "artifact_type": "case_facts",
        "status": "ok",
        "log_id": str(log_id),
        "workspace_id": str(workspace_id),
        "app_id": str(app_id or case.get("app_id") or ""),
        "case": {
            "query": case.get("query") or request.query,
            "query_hint": case.get("query_hint") or "",
            "judgement": case.get("judgement") or "",
            "answer_hint": case.get("answer_hint") or "",
            "chat_history": case.get("chat_history") or "",
            "source_row": case.get("source_row") or "",
            "case_id": case.get("case_id") or "",
            "core_documents": case.get("core_documents") if isinstance(case.get("core_documents"), list) else [],
            "expected_knowledge_ids": case.get("expected_knowledge_ids") if isinstance(case.get("expected_knowledge_ids"), list) else [],
            "expected_knowledge_points": case.get("expected_knowledge_points") if isinstance(case.get("expected_knowledge_points"), list) else [],
            "version_id": case.get("version_id") or case.get("versionId") or case.get("app_version") or case.get("appVersion") or "",
        },
        "trace": {
            "source": "openplat_trace_detail",
            "fetch_meta": trace_meta or {},
            "summary": trace_summary,
            "has_middle_node_trace": bool(trace_summary.get("has_middle_node_trace")),
            "middle_node_types": trace_summary.get("middle_node_types", []),
            "workflow_span_ios": workflow_evidence.get("workflow_span_ios", []),
            "workflow_topology": workflow_evidence.get("workflow_topology", trace_summary.get("workflow_topology", {})),
            "node_evidence_map": workflow_evidence.get("node_evidence_map", trace_summary.get("node_evidence_map", [])),
            "prompt_observation": workflow_evidence.get("prompt_observation", trace_summary.get("prompt_observation", {})),
            "agent_span_read_plan": workflow_evidence.get("agent_span_read_plan", trace_summary.get("agent_span_read_plan", [])),
        },
        "preprocess": {
            "rewrite_query": attribution_request.preprocess.rewrite_query,
            "keywords": attribution_request.preprocess.keywords,
        },
        "artifacts": normalized["artifacts"],
        "counts": normalized["counts"],
        "recall_field_note": normalized["recall_field_note"],
        "answer": attribution_request.qa.answer,
        "citation_observations": {
            "wrong_citation": attribution_request.qa.wrong_citation,
            "claim_alignments": [item.model_dump(mode="json") for item in attribution_request.qa.claim_alignments],
        },
        "experiment_inputs": {
            "recall_templates": extract_recall_templates(trace_payload or {"data": {"spans": []}}),
        },
        "raw_trace_evidence": to_jsonable(parsed.trace_evidence),
        "agent_contract": {
            "code_role": "produce facts and experiment outputs only",
            "agent_role": "extract symptoms, compare candidate explanations, plan experiments, reflect, and write the human judgement",
            "hard_selection_disabled": True,
            "skill_release_marker": SKILL_RELEASE_MARKER,
            "analysis_input_policy": SKILL_RELEASE_POLICY,
        },
    }


def collect_evidence(
    *,
    workspace_id: str,
    log_id: str,
    app_id: str = "",
    case_file: str | None = None,
    case_payload: dict[str, Any] | None = None,
    trace_file: str | None = None,
    output_dir: str | None = None,
    limit: int = 1000,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    case_source = case_payload if case_payload is not None else read_json_file(case_file)
    case = normalize_case_payload(case_source)
    if trace_file:
        trace_payload = read_json_file(trace_file)
        trace_meta = {"source": "trace_file", "path": str(trace_file)}
    else:
        trace_payload, trace_meta = fetch_trace_detail(
            workspace_id=workspace_id,
            log_id=log_id,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )
    facts = build_case_facts(
        workspace_id=workspace_id,
        log_id=log_id,
        app_id=app_id or str(case.get("app_id") or ""),
        case=case,
        trace_payload=trace_payload,
        trace_meta=trace_meta,
    )
    if output_dir:
        out = Path(output_dir)
        write_json(out / "case_facts.json", facts)
    return facts


def schema_payload() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_release_marker": SKILL_RELEASE_MARKER,
        "skill_release_policy": SKILL_RELEASE_POLICY,
        "commands": {
            "collect-evidence": {
                "purpose": "Fetch or read trace, parse workflow-aware RAG artifacts, and persist case facts for Agent judgement.",
                "inputs": ["workspace_id", "log_id", "app_id", "case_json", "case_file_legacy", "trace_file", "output_dir"],
                "outputs": ["case_facts.json"],
                "trace_outputs": [
                    "workflow_topology",
                    "node_evidence_map",
                    "prompt_observation",
                    "agent_span_read_plan",
                ],
            },
            "run-experiment": {
                "purpose": "Plan or run recall/rerank/replay experiments without selecting a root cause.",
                "types": ["recall", "rerank", "replay", "knowledge-detail"],
                "options": {
                    "recall": ["--query", "--context-query", "--timeout-seconds"],
                    "rerank": ["--target-doc-id"],
                    "knowledge-detail": ["--target-doc-id", "--timeout-seconds"],
                    "replay": ["--query", "--app-id", "--version-id", "--timeout-seconds"],
                },
                "outputs": ["<type>_experiment.json when a real experiment or local observation artifact is persisted"],
                "conditional_outputs": {
                    "replay": "skipped_authoritative_trace returns status in memory and is summarized from case_facts.json; it does not force an empty replay_experiment.json."
                },
            },
            "synthesize-brief": {
                "purpose": "Write a concise human judgement summary plus evidence_index.json from facts and experiment files.",
                "inputs": ["facts_file", "output_dir", "experiment_dir"],
                "outputs": ["agent_judgement.md", "evidence_index.json"],
                "constraints": [
                    "The report must be concise and directly readable as an Agent final-response draft.",
                    "The report must include answer symptoms, upstream evidence summary, evidence sufficiency review, key evidence snippets, log_id, app_id, workspace_id, evaluator signal summary, and attribution organization.",
                    "Evidence sufficiency must distinguish evidence that explains replay improvement from evidence that is enough for a rigorous business answer.",
                    "The report must cite documents with title plus link or snippet; doc ids alone are not acceptable human evidence.",
                    "The local JSON artifacts remain the searchable evidence store; do not make users read raw JSON for the final judgement.",
                ],
            },
            "schema": {"purpose": "Print this v4 evidence-kernel manifest."},
        },
        "removed_contracts": [
            "No fixed primary_cause selection.",
            "No earliest-failing-stage hard arbitration.",
            "No compatibility promise for old v3 CLI output or tests.",
            "No generated agent_brief.md; use case_facts.json for evidence and agent_judgement.md for the human-readable report.",
            "No default persisted case.json; embed source case context under case_facts.json.case.",
        ],
        "raw_artifacts": ["origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"],
        "experiment_inputs": ["recall_templates"],
        "human_aliases": {"recall": "origin_doc_list + origin_faq_list"},
        "report_outputs": ["agent_judgement.md", "evidence_index.json"],
        "execution_modes": {
            "default_preferred": "local_scripts_plus_llm",
            "local_trace": "Use collect-evidence --trace-file with local trace JSON to avoid live trace fetching.",
            "online_opt_in": "Use OpenPlat trace detail, live recall/searchDoc, OpenPlat app detail, workspace info, and open-exec workflow replay only when explicitly needed.",
        },
        "live_dependencies": [
            {
                "name": "OpenPlat trace detail API",
                "required": False,
                "used_by": "collect-evidence without --trace-file",
                "local_replacement": "--trace-file",
            },
            {
                "name": "recall/searchDoc HTTP endpoint from trace template",
                "required": False,
                "used_by": "run-experiment --type recall --query",
                "local_replacement": "omit --query to produce a local recall experiment plan",
            },
            {
                "name": "OpenPlat app detail API, workspace info API, and open-exec workflow replay API",
                "required": False,
                "used_by": "run-experiment --type replay when historical trace lacks middle-node evidence",
                "local_replacement": "authoritative historical trace with middle-node evidence",
            },
            {
                "name": "Sirius knowledge docDetail API",
                "required": False,
                "used_by": "run-experiment --type knowledge-detail for key-doc status enrichment",
                "local_replacement": "status_confirmed=false with status_reason=status_unconfirmed",
            },
        ],
    }
