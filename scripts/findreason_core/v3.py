from __future__ import annotations

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
import re
import time
from typing import Any

import httpx

from .env import load_runtime_env
from .fornax_trace import (
    FornaxTraceIngestRequest,
    _root_tag,
    _trace_spans,
    ingest_fornax_trace,
)
from .models import AttributionRequest, EvidenceDoc
from .workflow_replay import replay_workflow, resolve_workflow


SCHEMA_VERSION = "v3"
DEFAULT_OPEN_PLAT_TRACE_DETAIL_URL = "http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail"
DEFAULT_OPEN_PLAT_WORKSPACE_INFO_URL = "https://zhishang.bytedance.net/open-plat/api/workspace/get-workspace-info"
DEFAULT_SIRIUS_RECALL_URL = "https://ad-sirius.bytedance.net/api/sirius_plugin/v1/recall"
SIRIUS_RECALL_PATH = "/api/sirius_plugin/v1/recall"
RECALL_THRESHOLD_KEYS = {"score", "精选", "内容中台", "min_score"}
JUDGEMENT_SIGNALS_LIMIT_BYTES = 2048

STAGE_ORDER = ["preprocess", "knowledge", "retrieval", "rerank", "context", "answer", "evaluation"]
CAUSE_ENUM = {
    "non_rag_route_boundary",
    "query_rewrite_drift",
    "keyword_loss",
    "suspected_knowledge_missing",
    "knowledge_topic_mismatch",
    "knowledge_internal_inconsistency",
    "retrieval_miss",
    "permission_miss",
    "rerank_drop",
    "rerank_tunable",
    "context_assembly_error",
    "unsupported_claim",
    "wrong_citation",
    "partial_answer",
    "answer_scope_violation",
    "answer_branching_unclear",
}
CAUSE_OWNER = {
    "non_rag_route_boundary": "agent_router_owner",
    "query_rewrite_drift": "rag_preprocess_or_workflow_owner",
    "keyword_loss": "rag_preprocess_or_workflow_owner",
    "suspected_knowledge_missing": "kb_owner",
    "knowledge_topic_mismatch": "kb_owner",
    "knowledge_internal_inconsistency": "kb_owner",
    "retrieval_miss": "retrieval_strategy_owner",
    "permission_miss": "knowledge_permission_owner",
    "rerank_drop": "rerank_strategy_owner",
    "rerank_tunable": "rerank_strategy_owner",
    "context_assembly_error": "workflow_or_prompt_context_owner",
    "unsupported_claim": "prompt_or_model_owner",
    "wrong_citation": "prompt_or_model_owner",
    "partial_answer": "prompt_or_model_owner",
    "answer_scope_violation": "prompt_or_model_owner",
    "answer_branching_unclear": "prompt_or_model_owner",
}
CAUSE_PATTERN = {
    "non_rag_route_boundary": "query_understanding_break",
    "query_rewrite_drift": "query_understanding_break",
    "keyword_loss": "query_understanding_break",
    "suspected_knowledge_missing": "knowledge_gap_in_kb",
    "knowledge_topic_mismatch": "knowledge_topic_drift",
    "knowledge_internal_inconsistency": "knowledge_internal_inconsistency",
    "retrieval_miss": "near_miss_retrieval",
    "permission_miss": "permission_or_namespace_block",
    "rerank_drop": "retrieved_but_reranked_out",
    "rerank_tunable": "retrieved_but_reranked_out",
    "context_assembly_error": "retrieved_but_not_in_prompt",
    "unsupported_claim": "retrieved_but_not_used",
    "wrong_citation": "citation_misalignment",
    "partial_answer": "partial_coverage_answered_as_complete",
    "answer_scope_violation": "answer_scope_overreach",
    "answer_branching_unclear": "answer_branching_ambiguous",
}


class V3Error(Exception):
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


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json_dumps(value), encoding="utf-8")


def read_json_arg(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    if value.startswith("@"):
        return read_json(value[1:])
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def read_text_arg(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def _numeric_if_possible(value: str) -> int | str:
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _cache_root(workspace_id: str, log_id: str) -> Path:
    return Path.home() / ".findreason" / "cache" / str(workspace_id) / str(log_id)


def _cache_path(workspace_id: str, log_id: str, name: str) -> Path:
    return _cache_root(workspace_id, log_id) / name


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return _as_list(parsed)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _case_payload(case_file: str | None) -> dict[str, Any]:
    if not case_file:
        return {}
    payload = read_json(case_file)
    if isinstance(payload, dict) and isinstance(payload.get("case"), dict):
        return payload["case"]
    if isinstance(payload, dict) and isinstance(payload.get("case_input"), dict):
        case = dict(payload["case_input"])
        for key in (
            "judgement_evidence",
            "host_agent",
            "answer_claims",
            "missing_expected_points",
            "unsupported_claims",
            "claim_alignments",
            "expected_knowledge_points",
            "wrong_citations",
            "qa",
        ):
            if key in payload:
                case[key] = payload[key]
        return case
    return payload if isinstance(payload, dict) else {}


def validate_judgement_signals(case: dict[str, Any]) -> None:
    evidence = case.get("judgement_evidence") if isinstance(case.get("judgement_evidence"), dict) else {}
    signals = evidence.get("signals") if isinstance(evidence, dict) else None
    if signals is None:
        signals = case.get("judgement_signals")
    if signals is None:
        return
    size = len(json.dumps(signals, ensure_ascii=False, default=_json_default).encode("utf-8"))
    if size > JUDGEMENT_SIGNALS_LIMIT_BYTES:
        raise V3Error(
            "E_EVIDENCE_TOO_LARGE",
            "judgement_evidence.signals exceeds 2KB; host Agent must compress and retry.",
            status_code=2,
            details={"limit_bytes": JUDGEMENT_SIGNALS_LIMIT_BYTES, "actual_bytes": size},
        )


def _openplat_token() -> tuple[str, str]:
    for name in ("OPEN_PLAT_TRACE_TOKEN", "OPEN_PLAT_BOOTSTRAP_TOKEN"):
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", "not_configured"


def fetch_openplat_trace_detail(
    *,
    workspace_id: str,
    log_id: str,
    limit: int = 1000,
    timeout_seconds: int = 90,
) -> tuple[dict[str, Any], dict[str, Any]]:
    load_runtime_env()
    token, token_source = _openplat_token()
    if not token:
        raise V3Error(
            "E_TRACE_AUTH_REQUIRED",
            "Missing OpenPlat trace token. Configure OPEN_PLAT_TRACE_TOKEN without the Bearer prefix.",
            status_code=2,
            details={"token_source": token_source},
        )
    endpoint = os.getenv("OPEN_PLAT_TRACE_DETAIL_URL", DEFAULT_OPEN_PLAT_TRACE_DETAIL_URL)
    request_payload = {"workspaceId": _numeric_if_possible(workspace_id), "logId": log_id, "limit": int(limit or 1000)}
    authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    try:
        with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
            response = client.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-zs-plt-open": "zs_open",
                    "Authorization": authorization,
                },
                json=request_payload,
            )
    except Exception as exc:
        raise V3Error(
            "E_TRACE_LOOKUP_FAILED",
            "OpenPlat trace detail lookup failed.",
            details={"endpoint": endpoint, "request_payload": request_payload, "error": str(exc)[:500]},
        ) from exc
    if response.status_code >= 400:
        raise V3Error(
            "E_TRACE_LOOKUP_FAILED",
            f"OpenPlat trace detail HTTP {response.status_code}.",
            details={"endpoint": endpoint, "request_payload": request_payload, "response": response.text[:1000]},
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise V3Error(
            "E_TRACE_LOOKUP_FAILED",
            "OpenPlat trace detail returned non-JSON response.",
            details={"endpoint": endpoint, "request_payload": request_payload, "response": response.text[:1000]},
        ) from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise V3Error(
            "E_TRACE_LOOKUP_FAILED",
            f"OpenPlat trace detail returned code={payload.get('code')}.",
            details={"endpoint": endpoint, "request_payload": request_payload, "msg": str(payload.get("msg") or "")[:1000]},
        )
    meta = {
        "endpoint": endpoint,
        "request_payload": request_payload,
        "token_source": token_source,
        "authorization_header": "Bearer <redacted>",
    }
    return payload if isinstance(payload, dict) else {"data": {"spans": []}}, meta


def _authorization_header(token: str) -> str:
    text = str(token or "").strip()
    return text if text.lower().startswith("bearer ") else f"Bearer {text}"


def _workspace_info_url(workspace_id: str) -> str:
    endpoint = os.getenv("OPEN_PLAT_WORKSPACE_INFO_URL", DEFAULT_OPEN_PLAT_WORKSPACE_INFO_URL).strip()
    if "{workspaceId}" in endpoint:
        return endpoint.replace("{workspaceId}", str(workspace_id))
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}workspaceId={workspace_id}"


def _deep_find_api_key(value: Any) -> str:
    if isinstance(value, dict):
        for container_key in ("authInfo", "auth_info", "auth"):
            container = value.get(container_key)
            if isinstance(container, dict):
                for key in ("apiKey", "api_key", "apikey"):
                    candidate = container.get(key)
                    if candidate not in (None, ""):
                        return str(candidate).strip()
        for key in ("apiKey", "api_key", "apikey"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
        for child in value.values():
            found = _deep_find_api_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _deep_find_api_key(item)
            if found:
                return found
    return ""


def _resolve_workspace_api_key(workspace_id: str, *, timeout_seconds: int = 30) -> tuple[str, str]:
    load_runtime_env()
    direct_token = os.getenv("WORKFLOW_AUTH_TOKEN", "").strip()
    if direct_token:
        return direct_token, "WORKFLOW_AUTH_TOKEN"
    bootstrap_token, bootstrap_source = _openplat_token()
    if not bootstrap_token:
        return "", "not_configured"
    endpoint = _workspace_info_url(workspace_id)
    with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
        response = client.get(
            endpoint,
            headers={
                "Content-Type": "application/json",
                "x-zs-plt-open": "zs_open",
                "Authorization": _authorization_header(bootstrap_token),
            },
        )
    if response.status_code >= 400:
        raise RuntimeError(f"workspace info HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise RuntimeError(f"workspace info code={payload.get('code')}: {str(payload.get('msg') or '')[:300]}")
    api_key = _deep_find_api_key(payload)
    if not api_key:
        raise RuntimeError("workspace info did not contain authInfo.apiKey")
    return api_key, f"workspace_info:{bootstrap_source}"


def _span_id(span: dict[str, Any]) -> str:
    return str(span.get("span_id") or span.get("spanId") or span.get("id") or "").strip()


def _span_parent_id(span: dict[str, Any]) -> str:
    return str(span.get("parent_id") or span.get("parentId") or "").strip()


def _span_type_text(span: dict[str, Any]) -> str:
    return " ".join(
        str(span.get(key) or "")
        for key in ("span_type", "type", "span_name", "name", "operation_name")
    )


def _extract_trace_recall_template(ingest: dict[str, Any]) -> dict[str, Any]:
    trace_detail = (ingest.get("raw_artifacts") or {}).get("trace_detail")
    if not isinstance(trace_detail, dict):
        return {}
    spans = _trace_spans(trace_detail)
    recall_span_ids = {
        _span_id(span)
        for span in spans
        if "ZhiShangRAGRecall" in _span_type_text(span) or "知商召回" in _span_type_text(span)
    }
    candidates: list[dict[str, Any]] = []
    for span in spans:
        parsed_input = _parse_json_like(span.get("input"))
        if not isinstance(parsed_input, dict):
            continue
        url = str(parsed_input.get("url") or span.get("url") or "")
        if SIRIUS_RECALL_PATH not in url:
            continue
        body = _parse_json_like(parsed_input.get("body"))
        if not isinstance(body, dict) or not isinstance(body.get("recallRequests"), list):
            continue
        parent_id = _span_parent_id(span)
        candidates.append(
            {
                "endpoint": url,
                "request_body": body,
                "source_span_id": _span_id(span),
                "parent_span_id": parent_id,
                "recall_span_id": parent_id if parent_id in recall_span_ids else "",
                "parent_is_recall": parent_id in recall_span_ids,
            }
        )
    if not candidates:
        return {}
    candidates.sort(key=lambda item: (not item.get("parent_is_recall"), item.get("source_span_id") or ""))
    return candidates[0]


def _numeric_max_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _zero_recall_thresholds(params: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(params)
    for key in list(updated.keys()):
        if str(key) in RECALL_THRESHOLD_KEYS:
            updated[key] = 0
    return updated


def _build_open_label_recall_bodies(
    *,
    template_body: dict[str, Any],
    request_dict: dict[str, Any],
    workspace_id: str,
    topk: int,
) -> list[dict[str, Any]]:
    variants = _upper_bound_query_variants(request_dict)
    if not variants and template_body.get("oriQuery"):
        variants = [str(template_body.get("oriQuery"))]
    bodies: list[dict[str, Any]] = []
    for query in variants:
        body = deepcopy(template_body)
        body["oriQuery"] = query
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        params = deepcopy(params)
        params["workspaceId"] = str(workspace_id)
        body["params"] = params
        requests = body.get("recallRequests") if isinstance(body.get("recallRequests"), list) else []
        widened_requests: list[Any] = []
        for item in requests:
            if not isinstance(item, dict):
                widened_requests.append(item)
                continue
            widened = deepcopy(item)
            widened["recallLabels"] = []
            widened["level"] = []
            widened["maxCount"] = max(topk, _numeric_max_count(widened.get("maxCount")))
            widened["params"] = _zero_recall_thresholds(widened.get("params") if isinstance(widened.get("params"), dict) else {})
            widened_requests.append(widened)
        body["recallRequests"] = widened_requests
        bodies.append(body)
    return bodies


def _extract_recall_result(response_payload: Any) -> dict[str, list[dict[str, Any]]]:
    payload = _parse_json_like(response_payload)
    if isinstance(payload, dict) and "response" in payload:
        nested = _extract_recall_result(payload.get("response"))
        if nested:
            return nested
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, str)):
        nested = _extract_recall_result(payload.get("data"))
        if nested:
            return nested
    if not isinstance(payload, dict):
        return {}
    recall_result = payload.get("recallResult") or payload.get("recall_result")
    if isinstance(recall_result, dict):
        return {
            str(source): [item for item in docs if isinstance(item, dict)]
            for source, docs in recall_result.items()
            if isinstance(docs, list)
        }
    docs = payload.get("docs")
    if isinstance(docs, list):
        return {"docs": [item for item in docs if isinstance(item, dict)]}
    return {}


def _wide_recall_doc_key(doc: dict[str, Any], recall_source: str) -> str:
    return "|".join(
        [
            str(doc.get("id") or doc.get("doc_id") or doc.get("docId") or ""),
            str(doc.get("chunkId") or doc.get("chunk_id") or ""),
            str(recall_source or doc.get("recallSource") or ""),
        ]
    ) or _stable_hash(doc)


def _normalize_sirius_recall_doc(doc: dict[str, Any], *, recall_source: str, rank: int) -> dict[str, Any]:
    score = doc.get("recallScore", doc.get("fineScore", doc.get("score")))
    try:
        normalized_score = float(score) if score not in (None, "") else None
    except (TypeError, ValueError):
        normalized_score = None
    return {
        "id": str(doc.get("id") or doc.get("doc_id") or doc.get("docId") or "").strip(),
        "title": str(doc.get("title") or doc.get("name") or "").strip(),
        "content": str(doc.get("content") or doc.get("text") or "").strip(),
        "rank": rank,
        "score": normalized_score,
        "source": f"wide_recall_open_label:{recall_source or doc.get('recallSource') or 'unknown'}",
        "recall_source": str(recall_source or doc.get("recallSource") or ""),
        "chunk_id": str(doc.get("chunkId") or doc.get("chunk_id") or ""),
        "type": doc.get("type"),
        "labels": doc.get("labels") if isinstance(doc.get("labels"), list) else [],
        "url": doc.get("url"),
    }


def _split_sirius_recall_docs(recall_result: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    docs: list[dict[str, Any]] = []
    faqs: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for source, items in recall_result.items():
        counts[source] = len(items)
        for index, raw_doc in enumerate(items, start=1):
            key = _wide_recall_doc_key(raw_doc, source)
            if key in seen:
                continue
            seen.add(key)
            normalized = _normalize_sirius_recall_doc(raw_doc, recall_source=source, rank=index)
            if str(raw_doc.get("type")) == "4" or str(raw_doc.get("recallSource") or source) == "featured_search":
                faqs.append(normalized)
            else:
                docs.append(normalized)
    return docs, faqs, counts


def _bounded_payload(value: Any, *, max_bytes: int = 200000) -> Any:
    raw = json.dumps(value, ensure_ascii=False, default=_json_default)
    if len(raw.encode("utf-8")) <= max_bytes:
        return value
    return {"truncated": True, "size_bytes": len(raw.encode("utf-8")), "preview": raw[:4000]}


def _run_sirius_open_label_wide_recall(
    *,
    ingest: dict[str, Any],
    request_dict: dict[str, Any],
    topk: int,
) -> dict[str, Any]:
    workspace_id = str(ingest.get("workspace_id") or (request_dict.get("case_input") or {}).get("workspace_id") or "")
    template = _extract_trace_recall_template(ingest)
    query_variants = _upper_bound_query_variants(request_dict)
    if not template:
        return {
            "status": "not_configured",
            "error": "trace does not contain a Sirius recall http_client request template",
            "notes": "未在 trace 的 ZhiShangRAGRecall 子 span 中找到 /api/sirius_plugin/v1/recall 请求模板。",
            "query_variants": query_variants,
            "upper_bound_scope": "open_label",
        }
    bodies = _build_open_label_recall_bodies(
        template_body=template["request_body"],
        request_dict=request_dict,
        workspace_id=workspace_id,
        topk=topk,
    )
    query_variants = [str(body.get("oriQuery") or "") for body in bodies if str(body.get("oriQuery") or "").strip()]
    try:
        api_key, auth_source = _resolve_workspace_api_key(workspace_id)
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc)[:500],
            "notes": "获取 workspace apiKey 失败，未调用 Sirius 宽召回。",
            "query_variants": query_variants,
            "upper_bound_scope": "open_label",
            "auth_token_source": "workspace_info_error",
            "request_payload": {"endpoint": template.get("endpoint"), "bodies": bodies},
            "source_template": {key: template.get(key) for key in ("source_span_id", "parent_span_id", "recall_span_id")},
        }
    if not api_key:
        return {
            "status": "not_configured",
            "error": "",
            "notes": "未配置 OPEN_PLAT_TRACE_TOKEN/OPEN_PLAT_BOOTSTRAP_TOKEN 或 WORKFLOW_AUTH_TOKEN，跳过 Sirius 宽召回。",
            "query_variants": query_variants,
            "upper_bound_scope": "open_label",
            "auth_token_source": auth_source,
            "request_payload": {"endpoint": template.get("endpoint"), "bodies": bodies},
            "source_template": {key: template.get(key) for key in ("source_span_id", "parent_span_id", "recall_span_id")},
        }
    endpoint = template.get("endpoint") or DEFAULT_SIRIUS_RECALL_URL
    all_docs: list[dict[str, Any]] = []
    all_faqs: list[dict[str, Any]] = []
    per_query_counts: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = {
        "Content-Type": "application/json",
        "workspaceId": workspace_id,
        "Authorization": _authorization_header(api_key),
    }
    with httpx.Client(timeout=90) as client:
        for body in bodies:
            response = client.post(endpoint, headers=headers, json=body)
            if response.status_code >= 400:
                raise RuntimeError(f"Sirius recall HTTP {response.status_code}: {response.text[:500]}")
            payload = response.json()
            recall_result = _extract_recall_result(payload)
            docs, faqs, counts = _split_sirius_recall_docs(recall_result)
            for item in [*docs, *faqs]:
                key = "|".join([str(item.get("id") or ""), str(item.get("chunk_id") or ""), str(item.get("recall_source") or "")]) or _stable_hash(item)
                if key in seen:
                    continue
                seen.add(key)
                if str(item.get("type")) == "4" or str(item.get("recall_source")) == "featured_search":
                    all_faqs.append(item)
                else:
                    all_docs.append(item)
            per_query_counts.append(
                {
                    "query": body.get("oriQuery"),
                    "counts_by_source": counts,
                    "doc_count": len(docs),
                    "faq_count": len(faqs),
                }
            )
            responses.append({"query": body.get("oriQuery"), "recall_result_counts": counts, "response_payload": _bounded_payload(payload)})
    return {
        "status": "ok",
        "error": "",
        "notes": "已基于 trace 中真实 Sirius recall 请求模板执行 open-label 理论召回上界。",
        "query_variants": query_variants,
        "upper_bound_scope": "open_label",
        "auth_token_source": auth_source,
        "request_payload": {"endpoint": endpoint, "bodies": bodies},
        "response_payload": responses,
        "source_template": {key: template.get(key) for key in ("source_span_id", "parent_span_id", "recall_span_id")},
        "wide_recall_docs": all_docs,
        "wide_recall_faqs": all_faqs,
        "counts_by_query": per_query_counts,
        "counts": {
            "wide_recall_docs": len(all_docs),
            "wide_recall_faqs": len(all_faqs),
            "total": len(all_docs) + len(all_faqs),
        },
    }


def _trace_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _parse_json_like(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    for _ in range(2):
        if not isinstance(text, str):
            return text
        stripped = text.strip()
        if not stripped or stripped[0] not in "[{\"":
            return text
        try:
            parsed = json.loads(stripped)
        except Exception:
            return value
        if isinstance(parsed, str):
            text = parsed
            continue
        return parsed
    return value


def _root_app_id(spans: list[dict[str, Any]]) -> str:
    return _root_tag(spans, "zhishang.app_id", "app_id", "appId")


def _has_span_type(spans: list[dict[str, Any]], *span_types: str) -> bool:
    wanted = set(span_types)
    for span in spans:
        if str(span.get("span_type") or span.get("type") or "") in wanted:
            return True
    return False


def _doc_ids(docs: list[dict[str, Any]] | list[EvidenceDoc]) -> set[str]:
    values: set[str] = set()
    for doc in docs or []:
        if isinstance(doc, EvidenceDoc):
            doc_id = doc.id
        elif isinstance(doc, dict):
            doc_id = doc.get("id") or doc.get("doc_id") or doc.get("docId")
        else:
            doc_id = None
        if doc_id not in (None, ""):
            values.add(str(doc_id))
    return values


def _docs_from_request(request_dict: dict[str, Any], section: str, key: str) -> list[dict[str, Any]]:
    data = request_dict.get(section) if isinstance(request_dict.get(section), dict) else {}
    values = data.get(key) if isinstance(data, dict) else []
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def _counts_from_request(request_dict: dict[str, Any]) -> dict[str, int]:
    return {
        "origin_doc_list": len(_docs_from_request(request_dict, "retrieval", "origin_doc_list")),
        "origin_faq_list": len(_docs_from_request(request_dict, "retrieval", "origin_faq_list")),
        "rerank_docs": len(_docs_from_request(request_dict, "rerank", "rerank_docs")),
        "prompt_docs": len(_docs_from_request(request_dict, "rerank", "prompt_docs")),
    }


def _apply_host_case_fields(request_dict: dict[str, Any], case: dict[str, Any], app_id: str, log_id: str) -> dict[str, Any]:
    _raise_if_legacy_assertion_inputs(case)
    request_dict = json.loads(json.dumps(request_dict, ensure_ascii=False, default=_json_default))
    request_dict = _drop_legacy_assertion_fields(request_dict)
    case_input = request_dict.setdefault("case_input", {})
    if app_id:
        case_input["app_id"] = app_id
    if log_id:
        case_input["case_id"] = case_input.get("case_id") or log_id
    for key in ("query", "judgement", "workspace_id", "expected_answer", "source_row", "question_scene", "oracle_skip_self"):
        if case.get(key) not in (None, ""):
            case_input[key] = case[key]
    if "is_knowledge_qa" in case:
        case_input["is_knowledge_qa"] = case["is_knowledge_qa"]
    expected_ids = _string_list(case.get("expected_knowledge_ids"))
    if expected_ids:
        existing = _string_list(case_input.get("expected_knowledge_ids"))
        case_input["expected_knowledge_ids"] = list(dict.fromkeys([*existing, *expected_ids]))
    if case.get("error_points"):
        case_input["error_points"] = _string_list(case.get("error_points"))

    judgement_evidence = case.get("judgement_evidence") if isinstance(case.get("judgement_evidence"), dict) else {}
    signals = judgement_evidence.get("signals") if isinstance(judgement_evidence, dict) else case.get("judgement_signals")
    if signals is not None:
        request_dict["judgement_evidence"] = {
            "source_type": judgement_evidence.get("source_type", "host_agent") if isinstance(judgement_evidence, dict) else "host_agent",
            "raw_text": judgement_evidence.get("raw_text", case.get("judgement", "")) if isinstance(judgement_evidence, dict) else case.get("judgement", ""),
            "mapper_status": "host_agent_supplied",
            "signals": signals,
            "unmapped_notes": judgement_evidence.get("unmapped_notes", "") if isinstance(judgement_evidence, dict) else "",
        }

    host_agent = case.get("host_agent") if isinstance(case.get("host_agent"), dict) else {}
    answer_claim = _as_list(host_agent.get("answer_claim"))
    if answer_claim:
        request_dict.setdefault("host_agent", {})["answer_claim"] = answer_claim

    qa = request_dict.setdefault("qa", {})
    host_qa = case.get("qa") if isinstance(case.get("qa"), dict) else {}
    for key in (
        "prompt_supports_answer",
        "answer_satisfies_expected",
        "wrong_citation",
        "partial_answer",
        "hallucination",
        "grader_or_rubric_issue",
    ):
        if key in host_qa:
            qa[key] = host_qa[key]
    wrong_citations = _as_list(case.get("wrong_citations"))
    if wrong_citations:
        qa["wrong_citation"] = True
        request_dict.setdefault("raw_host_fields", {})["wrong_citations"] = wrong_citations
    return request_dict


def _infer_expected_doc_flow(request_dict: dict[str, Any]) -> dict[str, Any]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    expected = set(_string_list(case_input.get("expected_knowledge_ids")))
    if not expected:
        return request_dict
    retrieval = request_dict.setdefault("retrieval", {})
    rerank = request_dict.setdefault("rerank", {})
    origin_ids = _doc_ids(_docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list"))
    rerank_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "rerank_docs"))
    prompt_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "prompt_docs"))
    any_known = bool(expected & (origin_ids | rerank_ids | prompt_ids))
    if any_known and retrieval.get("knowledge_exists") is None:
        retrieval["knowledge_exists"] = True
    if retrieval.get("expected_knowledge_hit") is None:
        retrieval["expected_knowledge_hit"] = bool(expected & origin_ids)
    if retrieval.get("online_retrieval_hit") is None:
        retrieval["online_retrieval_hit"] = bool(expected & origin_ids)
    if expected & origin_ids and rerank.get("expected_doc_survived_rerank") is None:
        rerank["expected_doc_survived_rerank"] = bool(expected & rerank_ids)
    if (expected & (origin_ids | rerank_ids)) and rerank.get("expected_doc_in_prompt") is None:
        rerank["expected_doc_in_prompt"] = bool(expected & prompt_ids)
    return request_dict


def _trace_completeness(spans: list[dict[str, Any]], request_dict: dict[str, Any], trace_summary: dict[str, Any]) -> dict[str, str]:
    counts = _counts_from_request(request_dict)
    has_middle = bool(trace_summary.get("has_middle_node_trace"))
    result = {
        "preprocess": "complete" if _has_span_type(spans, "ZhiShangRAGPreprocess") or request_dict.get("preprocess", {}).get("rewrite_query") else "missing_node",
        "knowledge": "complete" if request_dict.get("retrieval", {}).get("knowledge_exists") is not None or request_dict.get("case_input", {}).get("expected_knowledge_ids") else "missing_evidence",
        "retrieval": "complete" if _has_span_type(spans, "ZhiShangRAGRecall") or counts["origin_doc_list"] or counts["origin_faq_list"] else "missing_node",
        "rerank": "complete" if _has_span_type(spans, "ZhiShangRAGRerank") or counts["rerank_docs"] else "missing_node",
        "context": "complete" if counts["prompt_docs"] or _has_span_type(spans, "ZhiShangRAGQA", "End") else "missing_evidence",
        "answer": "complete" if request_dict.get("qa", {}).get("answer") else "missing_evidence",
        "evaluation": "complete" if request_dict.get("judgement_evidence", {}).get("signals") or request_dict.get("case_input", {}).get("judgement") else "missing_optional",
    }
    if not has_middle:
        for stage in ("preprocess", "retrieval", "rerank", "context", "answer"):
            if result[stage] == "missing_node":
                result[stage] = "trace_missing_node"
    return result


def _suggest_probes(request_dict: dict[str, Any], completeness: dict[str, str], trace_summary: dict[str, Any]) -> tuple[list[str], dict[str, str], list[dict[str, str]]]:
    suggested: list[str] = []
    skip_reason: dict[str, str] = {}
    host_actions: list[dict[str, str]] = []
    counts = _counts_from_request(request_dict)
    case_input = request_dict.get("case_input", {})
    retrieval = request_dict.get("retrieval", {})
    rerank = request_dict.get("rerank", {})
    qa = request_dict.get("qa", {})

    def add(name: str) -> None:
        if name not in suggested:
            suggested.append(name)

    expected_ids = _string_list(case_input.get("expected_knowledge_ids"))
    oracle_skip_self = bool(case_input.get("oracle_skip_self")) and bool(expected_ids)
    if not oracle_skip_self:
        add("probe-self-oracle")
        add("probe-wide-recall")
    if not trace_summary.get("has_middle_node_trace"):
        host_actions.append({"action": "replay-workflow", "reason": "fornax trace lacks middle-node evidence", "priority": "P0"})
        add("replay-workflow")
        add("fetch-workflow-nodes")
    if completeness["knowledge"] != "complete" or retrieval.get("knowledge_exists") is None:
        add("probe-knowledge-detail")
    if completeness["retrieval"] != "complete" or retrieval.get("online_retrieval_hit") is False or case_input.get("expected_knowledge_ids"):
        add("probe-wide-recall")
    if retrieval.get("permission_miss"):
        add("probe-permission-check")
    if rerank.get("expected_doc_survived_rerank") is False or rerank.get("threshold_too_strict"):
        add("probe-rerank-bypass")
        add("probe-rerank-tune")
    if rerank.get("expected_doc_survived_rerank") is True and rerank.get("expected_doc_in_prompt") is False:
        add("probe-context-assembly")
    if _host_answer_claim_items(request_dict) or qa.get("wrong_citation") or qa.get("partial_answer"):
        add("probe-by-claim")
    elif qa.get("answer"):
        host_actions.append(
            {
                "action": "generate-probe-plan",
                "reason": "answer exists but host did not supply probe-v1 plan or host_agent.answer_claim; use the host Agent prompt to extract question points, answer facts, and probe queries",
                "priority": "P0",
            }
        )
        host_actions.append({"action": "extract_host_agent_answer_claim", "reason": "copy expected_required/missing_expected probes from the host probe-v1 plan into host_agent.answer_claim before orchestrate", "priority": "P0"})
        add("run-probe-plan")
    if request_dict.get("judgement_evidence", {}).get("signals") or case_input.get("judgement"):
        add("probe-by-judgement")
    if case_input.get("expected_knowledge_ids") and not counts["prompt_docs"]:
        add("probe-by-doc-title")

    all_probes = {
        "probe-knowledge-detail",
        "probe-permission-check",
        "probe-wide-recall",
        "probe-rerank-bypass",
        "probe-rerank-tune",
        "probe-context-assembly",
        "probe-self-oracle",
        "probe-by-judgement",
        "probe-by-claim",
        "probe-by-doc-title",
        "run-probe-plan",
        "fetch-workflow-nodes",
        "replay-workflow",
    }
    for probe_name in sorted(all_probes - set(suggested)):
        skip_reason[probe_name] = "current trace and host fields do not indicate this probe is needed"
    return suggested, skip_reason, host_actions


def build_ingest_output(
    *,
    workspace_id: str,
    log_id: str,
    app_id: str = "",
    case: dict[str, Any] | None = None,
    trace_payload: dict[str, Any],
    fetch_meta: dict[str, Any] | None = None,
    raw: bool = False,
) -> dict[str, Any]:
    case = case or {}
    validate_judgement_signals(case)
    data = _trace_data(trace_payload)
    spans = _trace_spans(trace_payload)
    resolved_app_id = app_id or _text(case.get("app_id")) or _root_app_id(spans)
    trace_advance_info = data.get("TracesAdvanceInfo") or data.get("traces_advance_info") or data.get("trace_advance_info") or {}
    base_summary = {
        "trace_completeness": {stage: "raw_only" for stage in STAGE_ORDER},
        "suggested_probe_set": [],
        "skip_reason": {},
        "host_action_required": [],
    }
    if raw:
        return {
            "schema_version": SCHEMA_VERSION,
            "log_id": log_id,
            "workspace_id": str(workspace_id),
            "app_id": str(resolved_app_id or ""),
            "ingest_summary": base_summary,
            "raw_artifacts": {
                "trace_detail": trace_payload,
                "trace_fetch": fetch_meta or {},
                "span_count": len(spans),
                "trace_advance_info": trace_advance_info,
                "has_more": data.get("has_more"),
                "next_page_token": data.get("next_page_token"),
            },
        }

    ingest_request = FornaxTraceIngestRequest(
        trace_file="",
        workspace_id=str(workspace_id),
        app_id=str(resolved_app_id or ""),
        query=_text(case.get("query")),
        judgement=_text(case.get("judgement")),
        case_id=_text(case.get("case_id")) or log_id,
        source_row=_text(case.get("source_row")) or None,
        fornax_space_id=_text(case.get("fornax_space_id")),
        fornax_space_name=_text(case.get("fornax_space_name")),
        error_points=_string_list(case.get("error_points")),
        detect_citation_mismatches=False,
    )
    old_response = ingest_fornax_trace(trace_payload, ingest_request)
    request_dict = _apply_host_case_fields(
        old_response.attribution_request.model_dump(mode="json"),
        case,
        str(resolved_app_id or ""),
        log_id,
    )
    request_dict = _normalize_assertion_inputs(request_dict)
    request_dict = _infer_expected_doc_flow(request_dict)
    trace_summary = old_response.trace_summary
    completeness = _trace_completeness(spans, request_dict, trace_summary)
    suggested, skip_reason, host_actions = _suggest_probes(request_dict, completeness, trace_summary)
    return {
        "schema_version": SCHEMA_VERSION,
        "log_id": log_id,
        "workspace_id": str(workspace_id),
        "app_id": str(request_dict.get("case_input", {}).get("app_id") or resolved_app_id or ""),
        "case": {
            "query": request_dict.get("case_input", {}).get("query", ""),
            "judgement": request_dict.get("case_input", {}).get("judgement", ""),
            "expected_knowledge_ids": request_dict.get("case_input", {}).get("expected_knowledge_ids", []),
            "host_agent": {
                "answer_claim": request_dict.get("host_agent", {}).get("answer_claim", [])
                if isinstance(request_dict.get("host_agent"), dict)
                else []
            },
            "wrong_citations": (request_dict.get("raw_host_fields") or {}).get("wrong_citations", []),
        },
        "ingest_summary": {
            "trace_completeness": completeness,
            "suggested_probe_set": suggested,
            "skip_reason": skip_reason,
            "host_action_required": host_actions,
        },
        "raw_artifacts": {
            "trace_fetch": fetch_meta or {},
            "trace_detail": trace_payload,
            "trace_summary": trace_summary,
            "trace_evidence": old_response.trace_evidence,
            "attribution_request": request_dict,
            "workflow_span_ios": old_response.trace_evidence.get("workflow_span_ios", []),
            "workflow_node_order": [
                item.get("node_id") or item.get("span_type") or item.get("span_name")
                for item in old_response.trace_evidence.get("node_mapping", [])
            ],
            "trace_advance_info": trace_advance_info,
            "has_more": data.get("has_more"),
            "next_page_token": data.get("next_page_token"),
            "span_count": len(spans),
        },
    }


def write_ingest_cache(output: dict[str, Any], output_dir: str | None = None) -> dict[str, str]:
    workspace_id = str(output.get("workspace_id") or "")
    log_id = str(output.get("log_id") or "")
    cache_dir = _cache_root(workspace_id, log_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "ingest": str(cache_dir / "ingest.json"),
        "attribution_record": str(cache_dir / "attribution_record.json"),
    }
    write_json(paths["ingest"], output)
    trace_detail = (output.get("raw_artifacts") or {}).get("trace_detail")
    if trace_detail:
        paths["trace_snapshot"] = str(cache_dir / "trace_snapshot.json")
        write_json(paths["trace_snapshot"], trace_detail)
    trace_summary = (output.get("raw_artifacts") or {}).get("trace_summary")
    if trace_summary:
        paths["trace_summary"] = str(cache_dir / "trace_summary.json")
        write_json(paths["trace_summary"], trace_summary)
    short = {
        "schema_version": SCHEMA_VERSION,
        "log_id": log_id,
        "workspace_id": workspace_id,
        "app_id": output.get("app_id", ""),
        "query": (output.get("case") or {}).get("query", ""),
        "suggested_probe_set": (output.get("ingest_summary") or {}).get("suggested_probe_set", []),
        "host_action_required": (output.get("ingest_summary") or {}).get("host_action_required", []),
    }
    write_json(paths["attribution_record"], short)
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json(out_dir / "ingest.json", output)
        write_json(out_dir / "attribution_record.json", short)
        paths["output_ingest"] = str(out_dir / "ingest.json")
        paths["output_attribution_record"] = str(out_dir / "attribution_record.json")
    return paths


def load_ingest(ingest_file: str | None, workspace_id: str | None = None, log_id: str | None = None) -> dict[str, Any]:
    if ingest_file:
        return read_json(ingest_file)
    if workspace_id and log_id:
        path = _cache_path(workspace_id, log_id, "ingest.json")
        if path.exists():
            return read_json(path)
    raise V3Error("E_INGEST_NOT_FOUND", "Missing ingest file. Provide --ingest-file or workspace/log cache.", status_code=2)


class EvidenceBuilder:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.counter = 1

    def add(
        self,
        *,
        evidence_type: str,
        source_stage: str,
        source: dict[str, Any],
        content: Any,
        confidence: float = 0.7,
        evidence_id: str | None = None,
    ) -> str:
        evidence_id = evidence_id or f"ev_{self.counter:03d}"
        self.counter += 1
        existing = {item["evidence_id"] for item in self.items}
        if evidence_id in existing:
            evidence_id = f"{evidence_id}_{self.counter:03d}"
        self.items.append(
            {
                "evidence_id": evidence_id,
                "evidence_type": evidence_type,
                "source_stage": source_stage,
                "source": source,
                "content": content,
                "relation_to_query": {
                    "semantic_relevance": "unknown",
                    "topic_match": None,
                    "constraint_match": None,
                    "coverage": "unknown",
                },
                "relation_to_answer": {
                    "supports_claim_ids": [],
                    "contradicts_claim_ids": [],
                    "missing_for_claim_ids": [],
                },
                "quality": {
                    "freshness": "valid",
                    "permission_available": None,
                    "confidence": confidence,
                },
            }
        )
        return evidence_id

    def add_probe_items(self, probe: dict[str, Any]) -> None:
        for item in probe.get("evidence_bundle") or []:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or f"{probe.get('probe_name', 'probe')}:ev_{self.counter:03d}")
            self.add(
                evidence_id=evidence_id,
                evidence_type=str(item.get("evidence_type") or "probe_output"),
                source_stage=str(item.get("source_stage") or _stage_from_probe(str(probe.get("probe_name") or ""))),
                source=item.get("source") if isinstance(item.get("source"), dict) else {"probe_name": probe.get("probe_name")},
                content=item.get("content"),
                confidence=float(((item.get("quality") or {}).get("confidence") if isinstance(item.get("quality"), dict) else 0.7) or 0.7),
            )

    def ids_for(self, stage: str) -> list[str]:
        return [item["evidence_id"] for item in self.items if item.get("source_stage") == stage]

    def ensure_stage_evidence(self, stage: str, content: Any, confidence: float = 0.6) -> list[str]:
        existing = self.ids_for(stage)
        if existing:
            return existing[:3]
        return [
            self.add(
                evidence_type="diagnostic_signal",
                source_stage=stage,
                source={"name": "orchestrate"},
                content=content,
                confidence=confidence,
            )
        ]


def _base_evidence(request_dict: dict[str, Any], ingest: dict[str, Any], builder: EvidenceBuilder) -> None:
    raw = ingest.get("raw_artifacts") if isinstance(ingest.get("raw_artifacts"), dict) else {}
    builder.add(
        evidence_type="trace_summary",
        source_stage="preprocess",
        source={"name": "ingest-fornax-trace"},
        content={
            "trace_completeness": (ingest.get("ingest_summary") or {}).get("trace_completeness", {}),
            "span_count": raw.get("span_count"),
            "workflow_node_order": raw.get("workflow_node_order", []),
        },
        confidence=0.82,
    )
    if raw.get("workflow_span_ios"):
        builder.add(
            evidence_type="trace_span_io",
            source_stage="context",
            source={"span_type": "workflow"},
            content={"workflow_span_ios": raw.get("workflow_span_ios")},
            confidence=0.86,
        )
    counts = _counts_from_request(request_dict)
    if counts["origin_doc_list"] or counts["origin_faq_list"]:
        builder.add(
            evidence_type="trace_docs",
            source_stage="retrieval",
            source={"field": "origin_doc_list/origin_faq_list"},
            content={
                "counts": counts,
                "doc_ids": sorted(_doc_ids(_docs_from_request(request_dict, "retrieval", "origin_doc_list"))),
                "faq_ids": sorted(_doc_ids(_docs_from_request(request_dict, "retrieval", "origin_faq_list"))),
            },
            confidence=0.86,
        )
    if counts["rerank_docs"]:
        builder.add(
            evidence_type="trace_docs",
            source_stage="rerank",
            source={"field": "rerank_docs"},
            content={
                "counts": counts,
                "doc_ids": sorted(_doc_ids(_docs_from_request(request_dict, "rerank", "rerank_docs"))),
                "expected_doc_survived_rerank": request_dict.get("rerank", {}).get("expected_doc_survived_rerank"),
            },
            confidence=0.86,
        )
    if counts["prompt_docs"]:
        builder.add(
            evidence_type="trace_docs",
            source_stage="context",
            source={"field": "prompt_docs"},
            content={
                "counts": counts,
                "doc_ids": sorted(_doc_ids(_docs_from_request(request_dict, "rerank", "prompt_docs"))),
                "expected_doc_in_prompt": request_dict.get("rerank", {}).get("expected_doc_in_prompt"),
            },
            confidence=0.86,
        )
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    answer_claims = _expected_knowledge_points(request_dict)
    unsupported_claims = [item.get("text") for item in answer_claims if str(item.get("role") or "") == "unsupported_claim" and item.get("text")]
    if qa.get("answer") or answer_claims or qa.get("wrong_citation") or qa.get("partial_answer"):
        builder.add(
            evidence_type="answer_signal",
            source_stage="answer",
            source={"field": "host_agent.answer_claim/qa"},
            content={
                "answer_present": bool(qa.get("answer")),
                "prompt_supports_answer": qa.get("prompt_supports_answer"),
                "answer_satisfies_expected": qa.get("answer_satisfies_expected"),
                "answer_claims": answer_claims,
                "unsupported_claims": unsupported_claims,
                "wrong_citation": qa.get("wrong_citation"),
                "partial_answer": qa.get("partial_answer"),
            },
            confidence=0.78,
        )
    judgement = request_dict.get("judgement_evidence") if isinstance(request_dict.get("judgement_evidence"), dict) else {}
    if judgement.get("signals"):
        builder.add(
            evidence_type="host_judgement_signal",
            source_stage="evaluation",
            source={"field": "judgement_evidence.signals"},
            content={"signals": judgement.get("signals")},
            confidence=0.65,
        )


def _stage_from_probe(probe_name: str) -> str:
    mapping = {
        "probe-knowledge-detail": "knowledge",
        "probe-permission-check": "retrieval",
        "probe-wide-recall": "retrieval",
        "probe-rerank-bypass": "rerank",
        "probe-rerank-tune": "rerank",
        "probe-context-assembly": "context",
        "probe-self-oracle": "knowledge",
        "probe-by-judgement": "retrieval",
        "probe-by-claim": "answer",
        "probe-by-doc-title": "retrieval",
        "fetch-workflow-nodes": "preprocess",
        "replay-workflow": "context",
    }
    return mapping.get(probe_name, "evaluation")


def _canonicalize_assertion_records(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["source"] = _canonical_assertion_source(copied.get("source"))
        normalized.append(copied)
    return normalized


def _refresh_point_coverage_after_merge(merged: dict[str, Any], state: dict[str, Any]) -> None:
    raw = merged.setdefault("raw_oracle_fields", {})
    points = raw.get("expected_knowledge_points")
    if not isinstance(points, list) or not points:
        points = _expected_knowledge_points(merged)
    else:
        points = _canonicalize_assertion_records(points)
    if not points:
        return
    coverage = _knowledge_point_coverage(merged, points)
    coverage = _canonicalize_assertion_records(coverage)
    raw["expected_knowledge_points"] = points
    raw["point_coverage"] = coverage
    theoretical_gap_points = [str(item.get("text") or "") for item in coverage if item.get("missing_stage") == "knowledge" and item.get("text")]
    retrieval_gap_points = [str(item.get("text") or "") for item in coverage if item.get("missing_stage") == "retrieval" and item.get("text")]
    rerank_gap_points = [str(item.get("text") or "") for item in coverage if item.get("missing_stage") == "rerank" and item.get("text")]
    context_gap_points = [str(item.get("text") or "") for item in coverage if item.get("missing_stage") == "context" and item.get("text")]
    upper_bound_unavailable_points = [
        str(item.get("text") or "") for item in coverage if item.get("missing_stage") == "upper_bound_unavailable" and item.get("text")
    ]
    retrieval = merged.setdefault("retrieval", {})
    retrieval["partial_knowledge_missing"] = bool(theoretical_gap_points)
    retrieval["knowledge_gap_points"] = theoretical_gap_points
    retrieval["point_retrieval_gap_points"] = retrieval_gap_points
    retrieval["upper_bound_unavailable_points"] = upper_bound_unavailable_points
    if "theoretical_recall_status" not in retrieval:
        retrieval["theoretical_recall_status"] = "not_configured"
    rerank = merged.setdefault("rerank", {})
    rerank["missing_expected_points_from_rerank"] = rerank_gap_points
    rerank["missing_expected_points_from_prompt"] = context_gap_points
    oracle_status = state.get("oracle_status")
    if isinstance(oracle_status, dict):
        oracle_status["expected_knowledge_points"] = points
        oracle_status["point_coverage"] = coverage
        oracle_status["missing_expected_points_from_theoretical_recall"] = [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "required_terms": item.get("required_terms", [])}
            for item in coverage
            if item.get("missing_stage") == "knowledge"
        ]
        oracle_status["missing_expected_points_from_origin"] = [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "upper_bound_docs": item.get("upper_bound_docs", [])}
            for item in coverage
            if item.get("missing_stage") == "retrieval"
        ]
        oracle_status["missing_expected_points_from_rerank"] = [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "origin_docs": item.get("origin_docs", [])}
            for item in coverage
            if item.get("missing_stage") == "rerank"
        ]
        oracle_status["missing_expected_points_from_prompt"] = [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "rerank_docs": item.get("rerank_docs", [])}
            for item in coverage
            if item.get("missing_stage") == "context"
        ]
        oracle_status["upper_bound_unavailable_points"] = [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source")}
            for item in coverage
            if item.get("missing_stage") == "upper_bound_unavailable"
        ]


def _merge_probe_signals(request_dict: dict[str, Any], probes: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = json.loads(json.dumps(request_dict, ensure_ascii=False, default=_json_default))
    state: dict[str, Any] = {"probes": {}}
    for probe in probes:
        probe_name = str(probe.get("probe_name") or "")
        state["probes"][probe_name] = probe
        signals = probe.get("stage_signals") if isinstance(probe.get("stage_signals"), dict) else {}
        oracle_status = signals.get("oracle_status") if isinstance(signals.get("oracle_status"), dict) else probe.get("oracle_status")
        knowledge = signals.get("knowledge") if isinstance(signals.get("knowledge"), dict) else {}
        retrieval = signals.get("retrieval") if isinstance(signals.get("retrieval"), dict) else {}
        rerank = signals.get("rerank") if isinstance(signals.get("rerank"), dict) else {}
        context = signals.get("context") if isinstance(signals.get("context"), dict) else {}
        answer = signals.get("answer") if isinstance(signals.get("answer"), dict) else {}
        if isinstance(oracle_status, dict):
            state["oracle_status"] = oracle_status
            state["oracle_confidence"] = oracle_status.get("confidence", 0.0)
        if knowledge:
            state["knowledge_exists_state"] = knowledge.get("knowledge_exists")
            state["knowledge_retry_count"] = knowledge.get("retry_count", 0)
            inferred_ids = _string_list(knowledge.get("inferred_expected_ids"))
            if inferred_ids:
                state["inferred_expected_ids"] = inferred_ids
                merged.setdefault("case_input", {})["inferred_expected_knowledge_ids"] = inferred_ids
                existing = _string_list(merged.setdefault("case_input", {}).get("expected_knowledge_ids"))
                merged["case_input"]["expected_knowledge_ids"] = list(dict.fromkeys([*existing, *inferred_ids]))
                merged.setdefault("raw_oracle_fields", {})["inferred_expected_docs"] = knowledge.get("inferred_expected_docs", [])
                merged["raw_oracle_fields"]["oracle_confidence"] = knowledge.get("oracle_confidence")
            if knowledge.get("expected_knowledge_points") is not None:
                merged.setdefault("raw_oracle_fields", {})["expected_knowledge_points"] = knowledge.get("expected_knowledge_points", [])
            if knowledge.get("point_coverage") is not None:
                merged.setdefault("raw_oracle_fields", {})["point_coverage"] = knowledge.get("point_coverage", [])
            if knowledge.get("partial_knowledge_missing") is True:
                merged.setdefault("retrieval", {})["partial_knowledge_missing"] = True
                merged["retrieval"]["knowledge_gap_points"] = _string_list(knowledge.get("missing_expected_points_from_theoretical_recall"))
            if knowledge.get("knowledge_exists") == "yes":
                merged.setdefault("retrieval", {})["knowledge_exists"] = True
            elif knowledge.get("knowledge_exists") == "no":
                merged.setdefault("retrieval", {})["knowledge_exists"] = False
            elif knowledge.get("knowledge_exists") == "unknown":
                merged.setdefault("retrieval", {})["knowledge_exists"] = None
        if retrieval.get("permission_miss") is True:
            merged.setdefault("retrieval", {})["permission_miss"] = True
        if retrieval.get("online_retrieval_hit") is not None:
            merged.setdefault("retrieval", {})["online_retrieval_hit"] = retrieval.get("online_retrieval_hit")
        if retrieval.get("wide_recall_docs"):
            merged.setdefault("retrieval", {})["wide_recall_docs"] = retrieval.get("wide_recall_docs")
            merged.setdefault("retrieval", {})["knowledge_exists"] = True
        if retrieval.get("wide_recall_faqs"):
            merged.setdefault("retrieval", {})["wide_recall_faqs"] = retrieval.get("wide_recall_faqs")
            merged.setdefault("retrieval", {})["knowledge_exists"] = True
        if retrieval.get("theoretical_recall_status") is not None:
            merged.setdefault("retrieval", {})["theoretical_recall_status"] = retrieval.get("theoretical_recall_status")
        if retrieval.get("theoretical_query_variants") is not None:
            merged.setdefault("retrieval", {})["theoretical_query_variants"] = retrieval.get("theoretical_query_variants")
        if retrieval.get("theoretical_recall_topk") is not None:
            merged.setdefault("retrieval", {})["theoretical_recall_topk"] = retrieval.get("theoretical_recall_topk")
        if retrieval.get("upper_bound_scope") is not None:
            merged.setdefault("retrieval", {})["upper_bound_scope"] = retrieval.get("upper_bound_scope")
        if retrieval.get("theoretical_recall_counts") is not None:
            merged.setdefault("retrieval", {})["theoretical_recall_counts"] = retrieval.get("theoretical_recall_counts")
        if retrieval.get("retrieval_gap_detected") is not None:
            merged.setdefault("contrastive_probe", {})["retrieval_gap_detected"] = retrieval.get("retrieval_gap_detected")
        for key in (
            "inferred_expected_ids",
            "oracle_origin_hit_ids",
            "oracle_missing_from_origin_ids",
            "partial_retrieval_miss",
            "knowledge_gap_points",
            "partial_knowledge_missing",
            "point_retrieval_gap_points",
        ):
            if key in retrieval:
                merged.setdefault("retrieval", {})[key] = retrieval.get(key)
        if rerank.get("expected_doc_survived_rerank") is not None:
            merged.setdefault("rerank", {})["expected_doc_survived_rerank"] = rerank.get("expected_doc_survived_rerank")
        for key in (
            "inferred_expected_ids",
            "oracle_rerank_hit_ids",
            "oracle_missing_from_rerank_ids",
            "partial_rerank_drop",
            "missing_expected_points_from_rerank",
        ):
            if key in rerank:
                merged.setdefault("rerank", {})[key] = rerank.get(key)
        if rerank.get("rerank_tunable") is True:
            merged.setdefault("rerank", {})["threshold_too_strict"] = True
            state["rerank_tunable"] = True
        if context.get("expected_doc_in_prompt") is not None:
            merged.setdefault("rerank", {})["expected_doc_in_prompt"] = context.get("expected_doc_in_prompt")
        for key in (
            "inferred_expected_ids",
            "oracle_prompt_hit_ids",
            "oracle_missing_from_prompt_ids",
            "partial_context_miss",
            "missing_expected_points_from_prompt",
        ):
            if key in context:
                merged.setdefault("rerank", {})[key] = context.get(key)
        if context.get("context_assembly_error") is True:
            merged.setdefault("rerank", {})["context_assembly_error"] = True
        if answer.get("wrong_citation") is True:
            merged.setdefault("qa", {})["wrong_citation"] = True
        if answer.get("partial_answer") is True:
            merged.setdefault("qa", {})["partial_answer"] = True
        if answer.get("scope_violation") is True:
            merged.setdefault("qa", {})["scope_violation"] = True
        if answer.get("branching_unclear") is True:
            merged.setdefault("qa", {})["branching_unclear"] = True
        if knowledge.get("lacks_authoritative_source") is True:
            merged.setdefault("retrieval", {})["lacks_authoritative_source"] = True
        if knowledge.get("internal_inconsistency") is True:
            merged.setdefault("retrieval", {})["knowledge_internal_inconsistency"] = True
        if answer.get("prompt_supports_answer") is not None:
            merged.setdefault("qa", {})["prompt_supports_answer"] = answer.get("prompt_supports_answer")
        if answer.get("answer_satisfies_expected") is not None:
            merged.setdefault("qa", {})["answer_satisfies_expected"] = answer.get("answer_satisfies_expected")
    _refresh_point_coverage_after_merge(merged, state)
    return merged, state


def _counterfactual(available: bool, reason: str = "", if_fixed: str = "", downstream: bool = False, evidence_ids: list[str] | None = None) -> dict[str, Any]:
    payload = {
        "available": available,
        "reason": reason,
        "evidence_ids": evidence_ids or [],
    }
    if available:
        payload["if_fixed"] = if_fixed
        payload["downstream_would_change"] = downstream
    return payload


def _verdict(
    *,
    stage: str,
    status: str,
    evidence_ids: list[str],
    counterfactual: dict[str, Any],
    candidate_cause: str | None = None,
    confidence: float | None = None,
    upstream_blocked_by: str | None = None,
    block_downstream: bool = True,
) -> dict[str, Any]:
    if candidate_cause and candidate_cause not in CAUSE_ENUM:
        raise V3Error("E_CAUSE_NOT_IN_ENUM", f"candidate_cause={candidate_cause} is not in v3 cause enum.")
    item: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "evidence_ids": evidence_ids,
        "counterfactual": counterfactual,
        "upstream_blocked_by": upstream_blocked_by,
        "block_downstream": block_downstream,
    }
    if candidate_cause:
        item["candidate_cause"] = candidate_cause
        item["confidence"] = confidence if confidence is not None else 0.6
        item["owner"] = CAUSE_OWNER[candidate_cause]
    return item


def _first_upstream_block(verdicts: list[dict[str, Any]], stage: str) -> str | None:
    for verdict in verdicts:
        if verdict["stage"] == stage:
            return None
        if verdict["status"] in {"fail", "indeterminate"} and verdict.get("block_downstream", True):
            return verdict["stage"]
    return None


def _oracle_adjusted_confidence(base: float, probe_state: dict[str, Any]) -> float:
    oracle_confidence = probe_state.get("oracle_confidence")
    if oracle_confidence in (None, ""):
        return base
    try:
        return round(base * float(oracle_confidence), 4)
    except (TypeError, ValueError):
        return base


def _required_assertions_all_covered(request_dict: dict[str, Any], probe_state: dict[str, Any]) -> bool:
    raw_oracle = request_dict.get("raw_oracle_fields") if isinstance(request_dict.get("raw_oracle_fields"), dict) else {}
    rows = raw_oracle.get("point_coverage")
    if not isinstance(rows, list):
        oracle_state = probe_state.get("oracle_status") if isinstance(probe_state.get("oracle_status"), dict) else {}
        rows = oracle_state.get("point_coverage")
    if not isinstance(rows, list):
        return False
    required_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("role") or "expected_required") in REQUIRED_ASSERTION_ROLES
    ]
    return bool(required_rows) and all(row.get("missing_stage") == "covered" for row in required_rows)


def _infer_verdicts(
    request_dict: dict[str, Any],
    ingest: dict[str, Any],
    probe_state: dict[str, Any],
    builder: EvidenceBuilder,
    *,
    mode: str,
    only_stages: set[str] | None = None,
) -> list[dict[str, Any]]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    preprocess = request_dict.get("preprocess") if isinstance(request_dict.get("preprocess"), dict) else {}
    retrieval = request_dict.get("retrieval") if isinstance(request_dict.get("retrieval"), dict) else {}
    rerank = request_dict.get("rerank") if isinstance(request_dict.get("rerank"), dict) else {}
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    completeness = (ingest.get("ingest_summary") or {}).get("trace_completeness") or {}
    expected_ids = _string_list(case_input.get("expected_knowledge_ids"))
    inferred_expected_ids = _string_list(case_input.get("inferred_expected_knowledge_ids"))
    counts = _counts_from_request(request_dict)
    recall_count = counts["origin_doc_list"] + counts["origin_faq_list"]
    oracle_state = probe_state.get("oracle_status") if isinstance(probe_state.get("oracle_status"), dict) else {}
    assertion_insufficient = oracle_state.get("source") == "insufficient_assertions"
    required_assertions_all_covered = _required_assertions_all_covered(request_dict, probe_state)
    verdicts: list[dict[str, Any]] = []

    def join_short(values: list[str], limit: int = 3) -> str:
        if not values:
            return ""
        shown = values[:limit]
        suffix = f" 等 {len(values)} 项" if len(values) > limit else ""
        return "；".join(shown) + suffix

    def missing_status(stage: str) -> str:
        if only_stages is not None and stage not in only_stages:
            return "not_probed"
        if mode == "preliminary" and completeness.get(stage, "").startswith("missing"):
            return "not_probed"
        return "indeterminate"

    # preprocess
    ev = builder.ensure_stage_evidence("preprocess", {"preprocess": preprocess})
    if only_stages is not None and "preprocess" not in only_stages:
        verdicts.append(_verdict(stage="preprocess", status="not_probed", evidence_ids=ev, counterfactual=_counterfactual(False, "stage excluded by --only-stages")))
    elif case_input.get("is_knowledge_qa") is False:
        verdicts.append(_verdict(stage="preprocess", status="fail", candidate_cause="non_rag_route_boundary", confidence=0.86, evidence_ids=ev, counterfactual=_counterfactual(True, "route would switch to non-RAG path", "route to the correct non-RAG/tool workflow", True, ev)))
    elif preprocess.get("rewrite_drift"):
        verdicts.append(_verdict(stage="preprocess", status="fail", candidate_cause="query_rewrite_drift", confidence=0.8, evidence_ids=ev, counterfactual=_counterfactual(True, "rewrite drift changes retrieval input", "preserve the original user intent in rewrite_query", True, ev)))
    elif preprocess.get("keyword_loss"):
        verdicts.append(_verdict(stage="preprocess", status="fail", candidate_cause="keyword_loss", confidence=0.8, evidence_ids=ev, counterfactual=_counterfactual(True, "keyword loss changes retrieval candidates", "preserve key entities before retrieval", True, ev)))
    elif preprocess.get("rewrite_query") or preprocess.get("keywords"):
        verdicts.append(_verdict(stage="preprocess", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "stage healthy")))
    else:
        verdicts.append(_verdict(stage="preprocess", status=missing_status("preprocess"), evidence_ids=ev, counterfactual=_counterfactual(False, "rewrite_query/keywords evidence missing")))

    # knowledge
    ev = builder.ensure_stage_evidence(
        "knowledge",
        {
            "knowledge_exists": retrieval.get("knowledge_exists"),
            "probe_state": probe_state.get("knowledge_exists_state"),
            "oracle_status": probe_state.get("oracle_status"),
            "knowledge_gap_points": retrieval.get("knowledge_gap_points", []),
        },
    )
    knowledge_gap_points = _string_list(retrieval.get("knowledge_gap_points"))
    if only_stages is not None and "knowledge" not in only_stages:
        verdicts.append(_verdict(stage="knowledge", status="not_probed", evidence_ids=ev, counterfactual=_counterfactual(False, "stage excluded by --only-stages")))
    elif assertion_insufficient and not expected_ids and not inferred_expected_ids:
        verdicts.append(
            _verdict(
                stage="knowledge",
                status="indeterminate",
                evidence_ids=ev,
                counterfactual=_counterfactual(False, "expected assertions missing; host Agent must supply fact assertions"),
                block_downstream=False,
            )
        )
    elif retrieval.get("topic_mismatch"):
        verdicts.append(_verdict(stage="knowledge", status="fail", candidate_cause="knowledge_topic_mismatch", confidence=0.72, evidence_ids=ev, counterfactual=_counterfactual(True, "correct topic document would unblock retrieval and answer", "add or retitle the exact-topic knowledge", True, ev)))
    elif retrieval.get("knowledge_internal_inconsistency"):
        verdicts.append(_verdict(stage="knowledge", status="fail", candidate_cause="knowledge_internal_inconsistency", confidence=0.7, evidence_ids=ev, counterfactual=_counterfactual(True, "KB contains conflicting statements without clear applicable premises", "deduplicate or disambiguate the conflicting KB entries", True, ev)))
    elif retrieval.get("partial_knowledge_missing") and knowledge_gap_points:
        detail = join_short(knowledge_gap_points)
        verdicts.append(
            _verdict(
                stage="knowledge",
                status="fail",
                candidate_cause="suspected_knowledge_missing",
                confidence=_oracle_adjusted_confidence(0.84, probe_state),
                evidence_ids=ev,
                counterfactual=_counterfactual(
                    True,
                    f"recall stage has no supporting docs for required assertions: {detail}",
                    f"add or rewrite KB entries for: {detail}",
                    True,
                    ev,
                ),
            )
        )
    elif _string_list(retrieval.get("upper_bound_unavailable_points")):
        detail = join_short(_string_list(retrieval.get("upper_bound_unavailable_points")))
        verdicts.append(
            _verdict(
                stage="knowledge",
                status="indeterminate",
                evidence_ids=ev,
                counterfactual=_counterfactual(
                    False,
                    f"theoretical recall upper bound is unavailable for required assertions: {detail}",
                ),
                block_downstream=True,
            )
        )
    elif retrieval.get("lacks_authoritative_source"):
        verdicts.append(_verdict(stage="knowledge", status="fail", candidate_cause="suspected_knowledge_missing", confidence=0.7, evidence_ids=ev, counterfactual=_counterfactual(True, "no authoritative/citable KB source backs the required extended references", "add an authoritative KB entry that can be cited for the required references", True, ev)))
    elif retrieval.get("knowledge_exists") is False:
        verdicts.append(_verdict(stage="knowledge", status="fail", candidate_cause="suspected_knowledge_missing", confidence=_oracle_adjusted_confidence(0.82, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, "adding the missing knowledge would change downstream recall", "add the missing knowledge to the target workspace KB", True, ev)))
    elif retrieval.get("knowledge_exists") is True:
        verdicts.append(_verdict(stage="knowledge", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "knowledge exists")))
    elif not expected_ids and not inferred_expected_ids:
        verdicts.append(
            _verdict(
                stage="knowledge",
                status="indeterminate",
                evidence_ids=ev,
                counterfactual=_counterfactual(False, "knowledge unlabeled; continue with downstream trace evidence"),
                block_downstream=False,
            )
        )
    else:
        verdicts.append(_verdict(stage="knowledge", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "knowledge existence unknown after retry")))

    # retrieval
    block = _first_upstream_block(verdicts, "retrieval")
    ev = builder.ensure_stage_evidence("retrieval", {"retrieval": retrieval, "oracle_status": probe_state.get("oracle_status")})
    if block:
        verdicts.append(_verdict(stage="retrieval", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, f"blocked by upstream stage {block}"), upstream_blocked_by=block))
    elif assertion_insufficient and not expected_ids and not inferred_expected_ids:
        verdicts.append(_verdict(stage="retrieval", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "expected assertions missing; retrieval cannot be judged against a target"), block_downstream=False))
    elif retrieval.get("permission_miss"):
        verdicts.append(_verdict(stage="retrieval", status="fail", candidate_cause="permission_miss", confidence=0.84, evidence_ids=ev, counterfactual=_counterfactual(True, "fixing ACL/namespace would expose correct docs to online retrieval", "make the expected knowledge visible to this workspace/app/user path", True, ev)))
    elif _string_list(retrieval.get("point_retrieval_gap_points")):
        detail = join_short(_string_list(retrieval.get("point_retrieval_gap_points")))
        verdicts.append(
            _verdict(
                stage="retrieval",
                status="fail",
                candidate_cause="retrieval_miss",
                confidence=_oracle_adjusted_confidence(0.84, probe_state),
                evidence_ids=ev,
                counterfactual=_counterfactual(
                    True,
                    f"theoretical recall can support required assertions but online origin recall missed them: {detail}",
                    f"fix online recall query/index/filter/topK for: {detail}",
                    True,
                    ev,
                ),
            )
        )
    elif required_assertions_all_covered:
        verdicts.append(_verdict(stage="retrieval", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "required assertions are covered in the online evidence chain; doc-id-only retrieval mismatch ignored")))
    elif retrieval.get("oracle_missing_from_origin_ids"):
        missing_ids = _string_list(retrieval.get("oracle_missing_from_origin_ids"))
        verdicts.append(_verdict(stage="retrieval", status="fail", candidate_cause="retrieval_miss", confidence=_oracle_adjusted_confidence(0.84, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, f"self-oracle expected docs missed online retrieval: {', '.join(missing_ids[:10])}", "fix query/index/filter/topK so inferred expected docs enter recall", True, ev)))
    elif retrieval.get("online_retrieval_hit") is False or retrieval.get("expected_knowledge_hit") is False or request_dict.get("contrastive_probe", {}).get("retrieval_gap_detected"):
        verdicts.append(_verdict(stage="retrieval", status="fail", candidate_cause="retrieval_miss", confidence=0.84, evidence_ids=ev, counterfactual=_counterfactual(True, "correct knowledge exists but online topK missed it", "fix query/index/filter/topK so the expected doc enters recall", True, ev)))
    elif retrieval.get("online_retrieval_hit") is True or retrieval.get("expected_knowledge_hit") is True:
        verdicts.append(_verdict(stage="retrieval", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "retrieval hit expected evidence")))
    elif not expected_ids and recall_count > 0:
        verdicts.append(
            _verdict(
                stage="retrieval",
                status="pass",
                evidence_ids=ev,
                counterfactual=_counterfactual(False, "retrieval returned candidates without expected knowledge labels"),
            )
        )
    elif not expected_ids and completeness.get("retrieval") == "complete" and recall_count == 0:
        verdicts.append(
            _verdict(
                stage="retrieval",
                status="fail",
                candidate_cause="retrieval_miss",
                confidence=0.72,
                evidence_ids=ev,
                counterfactual=_counterfactual(True, "online retrieval returned no candidate docs", "fix query/index/filter/topK so relevant docs enter recall", True, ev),
            )
        )
    else:
        verdicts.append(_verdict(stage="retrieval", status=missing_status("retrieval"), evidence_ids=ev, counterfactual=_counterfactual(False, "retrieval hit evidence missing")))

    # rerank
    block = _first_upstream_block(verdicts, "rerank")
    ev = builder.ensure_stage_evidence("rerank", {"rerank": rerank, "rerank_tunable": probe_state.get("rerank_tunable"), "oracle_status": probe_state.get("oracle_status")})
    if block:
        verdicts.append(_verdict(stage="rerank", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, f"blocked by upstream stage {block}"), upstream_blocked_by=block))
    elif assertion_insufficient and not expected_ids and not inferred_expected_ids:
        verdicts.append(_verdict(stage="rerank", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "expected assertions missing; rerank cannot be judged against a target"), block_downstream=False))
    elif probe_state.get("rerank_tunable"):
        verdicts.append(_verdict(stage="rerank", status="fail", candidate_cause="rerank_tunable", confidence=0.78, evidence_ids=ev, counterfactual=_counterfactual(True, "rerank parameter change can recover target docs", "tune threshold/features and verify on regression set", True, ev)))
    elif _string_list(rerank.get("missing_expected_points_from_rerank")):
        detail = join_short(_string_list(rerank.get("missing_expected_points_from_rerank")))
        verdicts.append(_verdict(stage="rerank", status="fail", candidate_cause="rerank_drop", confidence=_oracle_adjusted_confidence(0.86, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, f"required assertions were covered by origin recall but dropped by rerank: {detail}", f"fix rerank scoring/dedup/topK for: {detail}", True, ev)))
    elif required_assertions_all_covered:
        verdicts.append(_verdict(stage="rerank", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "required assertions survived into prompt; doc-id-only rerank drop ignored")))
    elif rerank.get("oracle_missing_from_rerank_ids"):
        missing_ids = _string_list(rerank.get("oracle_missing_from_rerank_ids"))
        verdicts.append(_verdict(stage="rerank", status="fail", candidate_cause="rerank_drop", confidence=_oracle_adjusted_confidence(0.86, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, f"self-oracle expected docs were recalled but dropped by rerank: {', '.join(missing_ids[:10])}", "fix rerank scoring/dedup/topK so inferred expected docs survive", True, ev)))
    elif rerank.get("expected_doc_survived_rerank") is False:
        verdicts.append(_verdict(stage="rerank", status="fail", candidate_cause="rerank_drop", confidence=0.86, evidence_ids=ev, counterfactual=_counterfactual(True, "expected doc would enter context if rerank did not drop it", "fix rerank scoring/dedup/topK so expected doc survives", True, ev)))
    elif rerank.get("expected_doc_survived_rerank") is True:
        verdicts.append(_verdict(stage="rerank", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "expected doc survived rerank")))
    elif not expected_ids and counts["rerank_docs"] > 0:
        verdicts.append(
            _verdict(
                stage="rerank",
                status="pass",
                evidence_ids=ev,
                counterfactual=_counterfactual(False, "rerank kept candidate docs without expected knowledge labels"),
            )
        )
    elif not expected_ids and recall_count > 0 and completeness.get("rerank") == "complete" and counts["rerank_docs"] == 0:
        verdicts.append(
            _verdict(
                stage="rerank",
                status="fail",
                candidate_cause="rerank_drop",
                confidence=0.68,
                evidence_ids=ev,
                counterfactual=_counterfactual(True, "rerank dropped all recalled candidates", "check rerank scoring/dedup/topK so candidates can survive", True, ev),
            )
        )
    else:
        verdicts.append(_verdict(stage="rerank", status=missing_status("rerank"), evidence_ids=ev, counterfactual=_counterfactual(False, "rerank survival evidence missing")))

    # context
    block = _first_upstream_block(verdicts, "context")
    ev = builder.ensure_stage_evidence("context", {"rerank": rerank, "oracle_status": probe_state.get("oracle_status")})
    if block:
        verdicts.append(_verdict(stage="context", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, f"blocked by upstream stage {block}"), upstream_blocked_by=block))
    elif assertion_insufficient and not expected_ids and not inferred_expected_ids:
        verdicts.append(_verdict(stage="context", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "expected assertions missing; prompt assembly cannot be judged against a target"), block_downstream=False))
    elif _string_list(rerank.get("missing_expected_points_from_prompt")):
        detail = join_short(_string_list(rerank.get("missing_expected_points_from_prompt")))
        verdicts.append(_verdict(stage="context", status="fail", candidate_cause="context_assembly_error", confidence=_oracle_adjusted_confidence(0.82, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, f"required assertions survived rerank but did not enter prompt: {detail}", f"fix prompt_docs assembly/truncation/noise budget for: {detail}", True, ev)))
    elif required_assertions_all_covered:
        verdicts.append(_verdict(stage="context", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "required assertions reached prompt; doc-id-only prompt mismatch ignored")))
    elif rerank.get("oracle_missing_from_prompt_ids"):
        missing_ids = _string_list(rerank.get("oracle_missing_from_prompt_ids"))
        verdicts.append(_verdict(stage="context", status="fail", candidate_cause="context_assembly_error", confidence=_oracle_adjusted_confidence(0.82, probe_state), evidence_ids=ev, counterfactual=_counterfactual(True, f"self-oracle expected docs survived upstream but did not enter prompt: {', '.join(missing_ids[:10])}", "fix prompt_docs assembly, truncation, and noise budget", True, ev)))
    elif rerank.get("expected_doc_in_prompt") is False or rerank.get("context_assembly_error") or rerank.get("prompt_truncation") or rerank.get("noise_overload"):
        verdicts.append(_verdict(stage="context", status="fail", candidate_cause="context_assembly_error", confidence=0.82, evidence_ids=ev, counterfactual=_counterfactual(True, "prompt would contain expected evidence after context assembly fix", "fix prompt_docs assembly, truncation, and noise budget", True, ev)))
    elif rerank.get("expected_doc_in_prompt") is True or _counts_from_request(request_dict)["prompt_docs"] > 0:
        verdicts.append(_verdict(stage="context", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "prompt_docs evidence present")))
    elif not expected_ids and counts["rerank_docs"] > 0 and completeness.get("context") == "complete" and counts["prompt_docs"] == 0:
        verdicts.append(
            _verdict(
                stage="context",
                status="fail",
                candidate_cause="context_assembly_error",
                confidence=0.7,
                evidence_ids=ev,
                counterfactual=_counterfactual(True, "reranked candidates did not enter prompt_docs", "fix prompt_docs assembly, truncation, and noise budget", True, ev),
            )
        )
    else:
        verdicts.append(_verdict(stage="context", status=missing_status("context"), evidence_ids=ev, counterfactual=_counterfactual(False, "prompt_docs/context evidence missing")))

    # answer
    block = _first_upstream_block(verdicts, "answer")
    ev = builder.ensure_stage_evidence("answer", {"qa": qa})
    answer_ready = qa.get("prompt_supports_answer") is True and qa.get("answer_satisfies_expected") is False
    has_unsupported_answer_claim = _has_answer_claim_role(request_dict, "unsupported_claim")
    if block and not answer_ready:
        verdicts.append(_verdict(stage="answer", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, f"blocked by upstream stage {block}"), upstream_blocked_by=block))
    elif answer_ready:
        if qa.get("wrong_citation"):
            cause = "wrong_citation"
        elif qa.get("scope_violation"):
            cause = "answer_scope_violation"
        elif qa.get("branching_unclear"):
            cause = "answer_branching_unclear"
        elif qa.get("partial_answer"):
            cause = "partial_answer"
        else:
            cause = "unsupported_claim"
        verdicts.append(_verdict(stage="answer", status="fail", candidate_cause=cause, confidence=0.86, evidence_ids=ev, counterfactual=_counterfactual(True, "answer would change if generation/citation behavior were fixed", "regenerate with faithful use of prompt_docs and citation mapping", True, ev)))
    elif qa.get("answer_satisfies_expected") is True:
        verdicts.append(_verdict(stage="answer", status="pass", evidence_ids=ev, counterfactual=_counterfactual(False, "answer satisfies expected output")))
    elif has_unsupported_answer_claim or qa.get("wrong_citation") or qa.get("partial_answer") or qa.get("scope_violation") or qa.get("branching_unclear"):
        verdicts.append(_verdict(stage="answer", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "answer flags exist but prompt_supports_answer/answer_satisfies_expected precondition is not proven")))
    else:
        verdicts.append(_verdict(stage="answer", status=missing_status("answer"), evidence_ids=ev, counterfactual=_counterfactual(False, "answer alignment evidence missing")))

    # evaluation
    ev = builder.ensure_stage_evidence("evaluation", {"judgement_evidence": request_dict.get("judgement_evidence", {})})
    verdicts.append(_verdict(stage="evaluation", status="indeterminate", evidence_ids=ev, counterfactual=_counterfactual(False, "evaluation is observation-only in v3")))
    return verdicts


def _validate_evidence_bindings(verdicts: list[dict[str, Any]], evidence_bundle: list[dict[str, Any]]) -> None:
    valid_ids = {item.get("evidence_id") for item in evidence_bundle}
    for verdict in verdicts:
        if "counterfactual" not in verdict:
            raise V3Error("E_COUNTERFACTUAL_MISSING", f"{verdict.get('stage')} verdict lacks counterfactual.")
        cause = verdict.get("candidate_cause")
        if cause:
            if cause not in CAUSE_ENUM:
                raise V3Error("E_CAUSE_NOT_IN_ENUM", f"candidate_cause={cause} is not in v3 cause enum.")
            if not verdict.get("evidence_ids"):
                raise V3Error("E_EVIDENCE_NOT_BOUND", f"{verdict.get('stage')} candidate cause has no evidence_id.")
        for evidence_id in verdict.get("evidence_ids") or []:
            if evidence_id not in valid_ids:
                raise V3Error("E_EVIDENCE_ID_INVALID", f"{verdict.get('stage')} references unknown evidence_id={evidence_id}.")
        for evidence_id in (verdict.get("counterfactual") or {}).get("evidence_ids") or []:
            if evidence_id not in valid_ids:
                raise V3Error("E_EVIDENCE_ID_INVALID", f"{verdict.get('stage')} counterfactual references unknown evidence_id={evidence_id}.")


def _select_primary(verdicts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    for verdict in verdicts:
        if verdict.get("upstream_blocked_by") is not None:
            continue
        if verdict.get("status") != "fail":
            continue
        cf = verdict.get("counterfactual") or {}
        if cf.get("available") is not True or cf.get("downstream_would_change") is not True:
            continue
        earlier = [item for item in verdicts if STAGE_ORDER.index(item["stage"]) < STAGE_ORDER.index(verdict["stage"])]
        unresolved = [
            item["stage"]
            for item in earlier
            if item.get("status") not in {"pass"} and item.get("block_downstream", True)
        ]
        if unresolved:
            return None, f"{verdict['stage']} 之前存在未解决的上游阶段：{', '.join(unresolved)}"
        cause = verdict["candidate_cause"]
        evidence_ids = verdict.get("evidence_ids") or []
        rationale = (
            f"主因选择在阶段 [{verdict['stage']}]："
            f"该阶段 counterfactual.downstream_would_change=true（证据：{', '.join(evidence_ids)}）；"
            "更上游阶段均已通过，修复该阶段后下游结果预期会变化。"
        )
        return {
            "stage": verdict["stage"],
            "cause_code": cause,
            "confidence": verdict.get("confidence", 0.0),
            "owner": CAUSE_OWNER[cause],
            "selection_rationale": rationale,
        }, rationale
    return None, "上游反事实证据均不可判定，无法确定主因"


def _failure_patterns(verdicts: list[dict[str, Any]], ingest: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for verdict in verdicts:
        cause = verdict.get("candidate_cause")
        if cause and CAUSE_PATTERN.get(cause):
            patterns.append(CAUSE_PATTERN[cause])
    completeness = (ingest.get("ingest_summary") or {}).get("trace_completeness") or {}
    nonblocking_stages = {
        str(verdict.get("stage"))
        for verdict in verdicts
        if verdict.get("status") == "indeterminate" and verdict.get("block_downstream") is False
    }
    if any(
        stage != "evaluation"
        and stage not in nonblocking_stages
        and (str(value).startswith("trace_missing") or str(value) in {"missing_node", "missing_evidence"})
        for stage, value in completeness.items()
    ):
        patterns.append("trace_incomplete_blocking_attribution")
    return list(dict.fromkeys(patterns))


def _case_assessment(
    *,
    primary: dict[str, Any] | None,
    verdicts: list[dict[str, Any]],
    request_dict: dict[str, Any],
    primary_reason: str,
) -> dict[str, Any]:
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    judgement = request_dict.get("judgement_evidence") if isinstance(request_dict.get("judgement_evidence"), dict) else {}
    signals = judgement.get("signals") if isinstance(judgement.get("signals"), list) else []
    has_unsupported_answer_claim = _has_answer_claim_role(request_dict, "unsupported_claim")
    answer_verdict = next((item for item in verdicts if item.get("stage") == "answer"), {})
    if qa.get("answer_satisfies_expected") is True:
        return {
            "status": "not_badcase",
            "reason": "答案满足期望，当前不应认定为 badcase；无需按 RAG 链路修复。",
            "evidence_ids": answer_verdict.get("evidence_ids") or [],
        }
    if primary:
        return {
            "status": "confirmed_badcase",
            "reason": f"已定位到主因：{primary.get('cause_code')}，可按归因链路修复。",
            "evidence_ids": answer_verdict.get("evidence_ids") or [],
        }
    if qa.get("answer_satisfies_expected") is False or signals or has_unsupported_answer_claim or qa.get("wrong_citation") or qa.get("partial_answer"):
        return {
            "status": "badcase_needs_review",
            "reason": f"存在评估失败或答案问题信号，但当前证据不足以选择唯一主因：{primary_reason}",
            "evidence_ids": answer_verdict.get("evidence_ids") or [],
        }
    if qa.get("answer"):
        return {
            "status": "not_badcase_candidate",
            "reason": "trace 中有最终答案，且未收到明确评估失败信号；建议人工确认后从 badcase 集合剔除或标记为评估误判。",
            "evidence_ids": answer_verdict.get("evidence_ids") or [],
        }
    return {
        "status": "indeterminate",
        "reason": f"缺少答案质量或评估信号，无法判断是否为 badcase：{primary_reason}",
        "evidence_ids": answer_verdict.get("evidence_ids") or [],
    }


def _orchestrate_oracle_status(request_dict: dict[str, Any], probe_state: dict[str, Any]) -> dict[str, Any]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    provided_ids = _string_list(case_input.get("expected_knowledge_ids"))
    inferred_ids = _string_list(case_input.get("inferred_expected_knowledge_ids"))
    raw_oracle = request_dict.get("raw_oracle_fields") if isinstance(request_dict.get("raw_oracle_fields"), dict) else {}
    raw_points = _canonicalize_assertion_records(raw_oracle.get("expected_knowledge_points"))
    raw_coverage = _canonicalize_assertion_records(raw_oracle.get("point_coverage"))
    expected_points = raw_points or _expected_knowledge_points(request_dict)
    required_points = _required_assertion_points(expected_points)
    status = probe_state.get("oracle_status")
    if isinstance(status, dict):
        if isinstance(status.get("expected_knowledge_points"), list):
            status = dict(status)
            status["expected_knowledge_points"] = _canonicalize_assertion_records(status.get("expected_knowledge_points"))
        if isinstance(status.get("point_coverage"), list):
            status = dict(status)
            status["point_coverage"] = _canonicalize_assertion_records(status.get("point_coverage"))
        return status
    if provided_ids:
        return {
            "source": "provided",
            "signals_used": [],
            "signals_attempted": [],
            "signals_failed": {},
            "inferred_doc_count": 0,
            "inferred_doc_ids": [],
            "provided_doc_ids": provided_ids,
            "confidence": 1.0,
            "conflict_detected": False,
            "provided_conflict_detected": False,
            "expected_knowledge_points": expected_points,
            "point_coverage": raw_coverage,
        }
    if inferred_ids:
        return {
            "source": "self_inferred",
            "signals_used": ["unknown"],
            "signals_attempted": ["unknown"],
            "signals_failed": {},
            "inferred_doc_count": len(inferred_ids),
            "inferred_doc_ids": inferred_ids,
            "provided_doc_ids": [],
            "confidence": probe_state.get("oracle_confidence", 0.0),
            "conflict_detected": False,
            "provided_conflict_detected": False,
            "expected_knowledge_points": expected_points,
            "point_coverage": raw_coverage,
        }
    if not required_points:
        return {
            "source": "insufficient_assertions",
            "signals_used": [],
            "signals_attempted": [],
            "signals_failed": {"assertions": "no expected_required or missing_expected assertions provided"},
            "inferred_doc_count": 0,
            "inferred_doc_ids": [],
            "provided_doc_ids": [],
            "confidence": 0.0,
            "conflict_detected": False,
            "provided_conflict_detected": False,
            "expected_knowledge_points": expected_points,
            "point_coverage": [],
            "assertion_status": "insufficient_required_assertions",
        }
    if raw_coverage:
        return {
            "source": "host_assertions",
            "signals_used": ["host_agent.answer_claim"],
            "signals_attempted": [],
            "signals_failed": {},
            "inferred_doc_count": 0,
            "inferred_doc_ids": [],
            "provided_doc_ids": provided_ids,
            "confidence": max([float(point.get("confidence") or 0.0) for point in required_points] or [0.0]),
            "conflict_detected": False,
            "provided_conflict_detected": False,
            "expected_knowledge_points": expected_points,
            "point_coverage": raw_coverage,
            "assertion_status": "coverage_computed_from_host_assertions",
        }
    return {
        "source": "insufficient",
        "signals_used": [],
        "signals_attempted": [],
        "signals_failed": {"probe-self-oracle": "not run or produced no oracle status"},
        "inferred_doc_count": 0,
        "inferred_doc_ids": [],
        "provided_doc_ids": [],
        "confidence": 0.0,
        "conflict_detected": False,
        "provided_conflict_detected": False,
        "expected_knowledge_points": expected_points,
        "point_coverage": raw_coverage,
        "assertion_status": "required_assertions_without_oracle_docs" if required_points else "no_required_assertions",
    }


def orchestrate_v3(
    *,
    ingest: dict[str, Any],
    probes: list[dict[str, Any]],
    mode: str = "final",
    only_stages: list[str] | None = None,
) -> dict[str, Any]:
    if ingest.get("schema_version") != SCHEMA_VERSION:
        raise V3Error("E_SCHEMA_VERSION_MISMATCH", "ingest file is not schema_version v3.", status_code=2)
    request_dict = (ingest.get("raw_artifacts") or {}).get("attribution_request")
    if not isinstance(request_dict, dict):
        raise V3Error("E_INGEST_INVALID", "ingest raw_artifacts.attribution_request is missing.", status_code=2)
    request_dict = _normalize_assertion_inputs(request_dict)
    validate_judgement_signals(request_dict)
    request_dict, probe_state = _merge_probe_signals(request_dict, probes)
    builder = EvidenceBuilder()
    _base_evidence(request_dict, ingest, builder)
    for probe in probes:
        builder.add_probe_items(probe)
    verdicts = _infer_verdicts(
        request_dict,
        ingest,
        probe_state,
        builder,
        mode=mode,
        only_stages=set(only_stages) if only_stages else None,
    )
    _validate_evidence_bindings(verdicts, builder.items)
    primary, primary_reason = _select_primary(verdicts)
    oracle_status = _orchestrate_oracle_status(request_dict, probe_state)
    case_assessment = _case_assessment(
        primary=primary,
        verdicts=verdicts,
        request_dict=request_dict,
        primary_reason=primary_reason,
    )
    patterns = _failure_patterns(verdicts, ingest)
    human_review_reasons: list[str] = []
    if primary is None and case_assessment.get("status") not in {"not_badcase"}:
        human_review_reasons.append(primary_reason)
    has_blocking_knowledge_review = any(
        v["stage"] == "knowledge" and v["status"] == "indeterminate" and v.get("block_downstream", True)
        for v in verdicts
    )
    if has_blocking_knowledge_review or (
        probe_state.get("knowledge_exists_state") == "unknown"
        and not (primary and primary.get("stage") == "answer")
    ):
        human_review_reasons.append("知识是否存在仍无法确认")
    if primary and float(primary.get("confidence") or 0.0) < 0.6:
        human_review_reasons.append(f"主因置信度较低：{primary.get('confidence')}")
    if "trace_incomplete_blocking_attribution" in patterns:
        human_review_reasons.append("trace 不完整阻塞归因")
    retrieval_state = request_dict.get("retrieval") if isinstance(request_dict.get("retrieval"), dict) else {}
    if retrieval_state.get("upper_bound_unavailable_points"):
        human_review_reasons.append(
            "理论召回上界未配置或失败，无法判断这些必要断言是知识缺失还是线上召回缺失"
        )
    if case_assessment.get("status") != "not_badcase":
        if oracle_status.get("source") == "insufficient_assertions":
            has_any_assertion = bool(oracle_status.get("expected_knowledge_points"))
            if primary is None or not has_any_assertion:
                human_review_reasons.append("缺少 expected_required/missing_expected 断言；宿主 Agent 需从 judgement/rubric/answer claims 补充确定断言")
        if oracle_status.get("source") == "insufficient":
            if _required_assertion_points(_expected_knowledge_points(request_dict)):
                human_review_reasons.append("oracle insufficient: required assertions exist but self-oracle/wide recall could not infer supporting documents or recall template")
            else:
                human_review_reasons.append("oracle insufficient: no required assertions or recall evidence available")
        if oracle_status.get("conflict_detected"):
            human_review_reasons.append(f"oracle signals contradictory: jaccard={oracle_status.get('jaccard')}")
        if oracle_status.get("provided_conflict_detected"):
            human_review_reasons.append("provided expected knowledge contradicts self-oracle inference")
    if mode == "preliminary":
        human_review_reasons.append("当前为 preliminary 模式，仍需补充探针证据")
    human_review_reasons = list(dict.fromkeys(human_review_reasons))
    next_actions = _next_actions(primary, verdicts, case_assessment)
    telemetry = {
        "probes_invoked": [probe.get("probe_name") for probe in probes if probe.get("probe_name")],
        "probe_latencies_ms": {
            probe.get("probe_name"): (probe.get("telemetry") or {}).get("latency_ms")
            for probe in probes
            if probe.get("probe_name")
        },
        "cache_hits": [probe.get("probe_name") for probe in probes if (probe.get("telemetry") or {}).get("cache_hit")],
        "orchestrate_mode": mode,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "log_id": ingest.get("log_id"),
        "workspace_id": ingest.get("workspace_id"),
        "app_id": ingest.get("app_id") or (request_dict.get("case_input") or {}).get("app_id"),
        "oracle_status": oracle_status,
        "case_assessment": case_assessment,
        "primary_cause": primary,
        "failure_patterns": patterns,
        "needs_human_review": bool(human_review_reasons),
        "human_review_reasons": human_review_reasons,
        "evidence_bundle": builder.items,
        "evidence_chain": verdicts,
        "next_actions": next_actions,
        "telemetry": telemetry,
        "deprecations": [],
        "raw_artifacts": {
            "trace_summary": (ingest.get("raw_artifacts") or {}).get("trace_summary", {}),
            "workflow_span_ios": (ingest.get("raw_artifacts") or {}).get("workflow_span_ios", []),
            "probe_outputs": {probe.get("probe_name"): probe for probe in probes if probe.get("probe_name")},
            "oracle_status": oracle_status,
            "selection_rationale": primary_reason,
        },
    }
    payload["human_report_markdown"] = render_case_report_markdown(payload, ingest, request_dict)
    return payload


def _report_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)


STAGE_DISPLAY = {
    "preprocess": "输入改写",
    "knowledge": "知识是否存在",
    "retrieval": "初召回",
    "rerank": "重排",
    "context": "Prompt 组装",
    "answer": "答案生成",
    "evaluation": "评估器信号",
}

STATUS_DISPLAY = {
    "pass": "通过",
    "fail": "失败",
    "indeterminate": "无法判定",
    "blocked": "被阻塞",
    "not_probed": "未探测",
}

CAUSE_DISPLAY = {
    "non_rag_route_boundary": "非 RAG 路由边界",
    "query_rewrite_drift": "Query 改写偏移",
    "keyword_loss": "关键词丢失",
    "suspected_knowledge_missing": "疑似知识缺失",
    "knowledge_topic_mismatch": "知识主题不匹配",
    "retrieval_miss": "召回缺失",
    "permission_miss": "权限缺失",
    "rerank_drop": "重排误杀",
    "rerank_tunable": "重排参数可调",
    "context_assembly_error": "上下文组装错误",
    "unsupported_claim": "答案存在未支撑断言",
    "wrong_citation": "引用错误",
    "partial_answer": "回答不完整",
}

OWNER_DISPLAY = {
    "manual_review": "人工复核",
    "preprocess_owner": "输入改写负责人",
    "knowledge_owner": "知识库负责人",
    "retrieval_owner": "召回负责人",
    "permission_owner": "权限负责人",
    "rerank_strategy_owner": "重排策略负责人",
    "context_owner": "Prompt 组装负责人",
    "answer_owner": "答案生成负责人",
    "citation_owner": "引用负责人",
}

ASSESSMENT_DISPLAY = {
    "confirmed_badcase": "确认是 badcase",
    "badcase_needs_review": "疑似 badcase，仍需复核主因",
    "not_badcase": "不应认定为 badcase",
    "not_badcase_candidate": "可能不是 badcase",
    "indeterminate": "无法判断是否为 badcase",
}

REASON_DISPLAY = {
    "stage healthy": "该阶段有可用证据，暂未发现异常。",
    "knowledge exists": "已确认相关知识存在。",
    "knowledge existence unknown after retry": "没有期望知识 ID 或知识探针仍无法确认知识是否存在，因此不能判断是知识缺失还是后续链路问题。",
    "retrieval hit expected evidence": "初召回命中了期望知识。",
    "retrieval hit evidence missing": "缺少“是否命中期望知识”的证据。",
    "rerank survival evidence missing": "缺少“期望知识是否通过重排”的证据。",
    "prompt_docs evidence present": "Prompt 中已有可用文档。",
    "prompt_docs/context evidence missing": "缺少 Prompt 组装后的文档证据。",
    "answer alignment evidence missing": "缺少答案与 Prompt 文档的支撑关系判断。",
    "answer satisfies expected output": "答案满足期望。",
    "evaluation is observation-only in v3": "评估器结果只作为问题线索，不单独决定主因。",
    "rewrite_query/keywords evidence missing": "缺少改写 query 或关键词证据。",
    "stage excluded by --only-stages": "该阶段本次未纳入探测。",
    "knowledge unlabeled; continue with downstream trace evidence": "未提供期望知识 ID，因此不把知识阶段作为硬阻塞，继续查看下游 trace 证据。",
    "retrieval returned candidates without expected knowledge labels": "初召回有候选文档；由于未提供期望知识 ID，只能说明召回链路有结果，不能证明命中了正确知识。",
    "online retrieval returned no candidate docs": "初召回没有返回任何候选文档。",
    "rerank kept candidate docs without expected knowledge labels": "重排后仍有候选文档；由于未提供期望知识 ID，不能判断是否误杀了正确知识。",
    "rerank dropped all recalled candidates": "重排把初召回候选全部过滤掉了。",
    "reranked candidates did not enter prompt_docs": "重排后有候选文档，但最终没有进入 Prompt 文档。",
    "self-oracle expected docs missed online retrieval": "self-oracle 推断的期望知识没有进入线上初召回。",
    "self-oracle expected docs were recalled but dropped by rerank": "self-oracle 推断的期望知识已被召回，但重排后丢失。",
    "self-oracle expected docs survived upstream but did not enter prompt": "self-oracle 推断的期望知识通过了上游，但没有进入最终 Prompt。",
    "adding the missing knowledge would change downstream recall": "补齐缺失知识后，下游召回预期会变化。",
}


def _display_stage(stage: Any) -> str:
    value = str(stage or "")
    label = STAGE_DISPLAY.get(value, value or "unknown")
    return f"{label}（{value}）" if value and label != value else label


def _display_status(status: Any) -> str:
    value = str(status or "")
    return STATUS_DISPLAY.get(value, value or "unknown")


def _display_owner(owner: Any) -> str:
    value = str(owner or "")
    label = OWNER_DISPLAY.get(value, value or "unknown")
    return f"{label}（{value}）" if value and label != value else label


def _display_assessment(status: Any) -> str:
    value = str(status or "")
    label = ASSESSMENT_DISPLAY.get(value, value or "unknown")
    return f"{label}（`{value}`）" if value and label != value else label


def _display_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if text in REASON_DISPLAY:
        return REASON_DISPLAY[text]
    prefix = "blocked by upstream stage "
    if text.startswith(prefix):
        stage = text[len(prefix) :]
        return f"上游阶段 {_display_stage(stage)} 还无法判定，所以本阶段暂不继续下钻。"
    prefix = "recall stage has no supporting docs for required assertions: "
    if text.startswith(prefix):
        return f"理论召回上界也没有找到可承载这些必要断言的文档：{text[len(prefix):]}"
    prefix = "theoretical recall can support required assertions but online origin recall missed them: "
    if text.startswith(prefix):
        return f"理论召回上界可以支持这些必要断言，但线上初召回没有命中：{text[len(prefix):]}"
    prefix = "theoretical recall upper bound is unavailable for required assertions: "
    if text.startswith(prefix):
        return f"这些必要断言缺少理论召回上界结果，暂时不能判断是知识缺失还是线上召回缺失：{text[len(prefix):]}"
    prefix = "required assertions were covered by origin recall but dropped by rerank: "
    if text.startswith(prefix):
        return f"这些必要断言已被线上初召回覆盖，但重排后丢失：{text[len(prefix):]}"
    prefix = "required assertions survived rerank but did not enter prompt: "
    if text.startswith(prefix):
        return f"这些必要断言通过了重排，但没有进入最终 Prompt：{text[len(prefix):]}"
    prefix = "self-oracle expected docs missed online retrieval: "
    if text.startswith(prefix):
        return f"self-oracle 推断的期望知识 ID 未进入线上初召回：{text[len(prefix):]}"
    prefix = "self-oracle expected docs were recalled but dropped by rerank: "
    if text.startswith(prefix):
        return f"self-oracle 推断的期望知识已被初召回，但重排后丢失：{text[len(prefix):]}"
    prefix = "self-oracle expected docs survived upstream but did not enter prompt: "
    if text.startswith(prefix):
        return f"self-oracle 推断的期望知识通过上游，但没有进入最终 Prompt：{text[len(prefix):]}"
    return text


def _selected_workflow_io(ingest: dict[str, Any]) -> dict[str, Any]:
    items = (ingest.get("raw_artifacts") or {}).get("workflow_span_ios") or []
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, dict) and item.get("selected"):
            return item
    return next((item for item in items if isinstance(item, dict)), {})


def _stage_key_basis(verdict: dict[str, Any]) -> str:
    cause = verdict.get("candidate_cause") or ""
    blocked = verdict.get("upstream_blocked_by")
    cf = verdict.get("counterfactual") if isinstance(verdict.get("counterfactual"), dict) else {}
    if cause:
        cause_label = CAUSE_DISPLAY.get(str(cause), str(cause))
        reason = _display_reason(cf.get("reason") or cf.get("if_fixed") or "")
        return f"候选原因：{cause_label}（`{cause}`）；反事实判断：{reason}"
    if blocked:
        return f"被上游阶段 {_display_stage(blocked)} 阻塞；{_display_reason(cf.get('reason') or '')}"
    return _display_reason(cf.get("reason") or "")


def _doc_presence_summary(request_dict: dict[str, Any]) -> dict[str, Any]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    raw_oracle = request_dict.get("raw_oracle_fields") if isinstance(request_dict.get("raw_oracle_fields"), dict) else {}
    expected = set(_string_list(case_input.get("expected_knowledge_ids")))
    origin = _docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list")
    rerank = _docs_from_request(request_dict, "rerank", "rerank_docs")
    prompt = _docs_from_request(request_dict, "rerank", "prompt_docs")
    doc_map: dict[str, dict[str, Any]] = {}
    for doc in [*origin, *rerank, *prompt]:
        doc_id = _doc_id(doc)
        if doc_id and doc_id not in doc_map:
            doc_map[doc_id] = _doc_brief(doc)
    for doc in raw_oracle.get("inferred_expected_docs") or []:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("doc_id") or doc.get("id") or "").strip()
        if doc_id and doc_id not in doc_map:
            doc_map[doc_id] = {
                "id": doc_id,
                "title": str(doc.get("title") or ""),
                "rank": doc.get("rank"),
                "source": "self-oracle",
            }
    origin_ids = _doc_ids(origin)
    rerank_ids = _doc_ids(rerank)
    prompt_ids = _doc_ids(prompt)

    def docs_for(ids: set[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for doc_id in sorted(ids):
            result.append(doc_map.get(doc_id, {"id": doc_id, "title": "", "rank": None, "source": "unknown"}))
        return result

    return {
        "expected": sorted(expected),
        "expected_in_origin": sorted(expected & origin_ids),
        "expected_in_rerank": sorted(expected & rerank_ids),
        "expected_in_prompt": sorted(expected & prompt_ids),
        "missing_in_origin": docs_for(expected - origin_ids),
        "missing_in_rerank": docs_for((expected & origin_ids) - rerank_ids),
        "missing_in_prompt": docs_for((expected & (origin_ids | rerank_ids)) - prompt_ids),
        "point_coverage": raw_oracle.get("point_coverage") if isinstance(raw_oracle.get("point_coverage"), list) else [],
        "origin_hits": [
            _doc_brief(doc)
            for doc in origin
            if _doc_id(doc) in expected
        ][:5],
    }


def _format_doc_gap_items(items: list[dict[str, Any]], empty: str, *, limit: int = 10) -> str:
    if not items:
        return f"- {empty}"
    lines: list[str] = []
    for item in items[:limit]:
        doc_id = str(item.get("id") or "")
        title = str(item.get("title") or "无标题")
        rank = item.get("rank")
        rank_text = f"，rank={rank}" if rank not in (None, "") else ""
        lines.append(f"- `{doc_id}` {title}{rank_text}")
    if len(items) > limit:
        lines.append(f"- 另有 {len(items) - limit} 条未展开，详见 `final/attribution_record.json`。")
    return "\n".join(lines)


def _point_stage_text(row: dict[str, Any], key: str) -> str:
    docs = row.get(key) if isinstance(row.get(key), list) else []
    if not docs:
        return "未找到"
    first = docs[0]
    status = str(first.get("support_status") or "").strip()
    status_text = f"（{status}）" if status else ""
    return f"命中 `{first.get('id')}` {first.get('title') or ''}{status_text}".strip()


def _format_upper_bound_support_doc(doc: dict[str, Any]) -> str:
    doc_id = str(doc.get("id") or "unknown")
    title = str(doc.get("title") or "").strip()
    parts = [f"`{doc_id}`"]
    if title:
        parts.append(title)
    details: list[str] = []
    matched_terms = [str(term) for term in doc.get("matched_terms") or [] if term]
    if matched_terms:
        details.append(f"匹配词：{'、'.join(matched_terms[:5])}")
    support_status = str(doc.get("support_status") or "").strip()
    if support_status:
        details.append(f"支撑={support_status}")
    if doc.get("support_score") not in (None, ""):
        details.append(f"support_score={doc.get('support_score')}")
    if doc.get("score") not in (None, ""):
        details.append(f"score={doc.get('score')}")
    suffix = f"（{'；'.join(details)}）" if details else ""
    support_spans = [re.sub(r"\s+", " ", str(span or "")).strip() for span in doc.get("support_spans") or []]
    span_text = ""
    if support_spans:
        span = support_spans[0]
        span_text = f"；片段：{span[:120]}{'...' if len(span) > 120 else ''}"
    return " ".join(parts) + suffix + span_text


def _format_upper_bound_assertion_relations(rows: list[dict[str, Any]], *, limit: int = 8, doc_limit: int = 3) -> list[str]:
    if not rows:
        return ["- 无"]
    lines: list[str] = []
    for row in rows[:limit]:
        point = re.sub(r"\s+", " ", str(row.get("text") or "未命名断言")).strip()
        docs = row.get("upper_bound_docs") if isinstance(row.get("upper_bound_docs"), list) else []
        status = str(row.get("upper_bound_status") or "unknown")
        if docs:
            support_text = "；".join(_format_upper_bound_support_doc(doc) for doc in docs[:doc_limit] if isinstance(doc, dict))
            if len(docs) > doc_limit:
                support_text += f"；另有 {len(docs) - doc_limit} 条未展开"
            lines.append(f"- {point}：支持该断言：{support_text}")
        elif status == "ok":
            lines.append(f"- {point}：未找到可承载该断言的上界文档")
        else:
            lines.append(f"- {point}：理论召回上界不可用（status={status}）")
    if len(rows) > limit:
        lines.append(f"- 另有 {len(rows) - limit} 条断言未展开，完整关系见 `oracle_status.point_coverage[].upper_bound_docs`。")
    return lines


ASSERTION_ROLE_DISPLAY = {
    "expected_required": "应覆盖事实",
    "missing_expected": "遗漏事实",
    "answer_claim": "答案 claim",
    "unsupported_claim": "未支持 claim",
}


def _format_point_coverage_table(rows: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    if not rows:
        return [
            "- 未提供 `host_agent.answer_claim` 中的 `expected_required/missing_expected` 断言，无法生成链路归因用的断言覆盖矩阵。",
            "- 先用 probe-v1 提示词生成探针计划，将必要断言同步写入 `host_agent.answer_claim`，再执行 `run-probe-plan` 后重新 `orchestrate`。",
        ]
    lines = [
        "| 断言 | 角色 | 来源 | 线上初召回 | 重排 | Prompt | 阶段判断 |",
        "|---|---|---|---|---|---|---|",
    ]
    stage_text = {
        "knowledge": "知识部分缺失",
        "retrieval": "线上召回缺失",
        "rerank": "重排丢失",
        "context": "上下文未组装",
        "covered": "已进入 Prompt",
        "upper_bound_unavailable": "缺少理论召回上界",
    }
    for row in rows[:limit]:
        point = str(row.get("text") or "").replace("|", "\\|")
        role = ASSERTION_ROLE_DISPLAY.get(str(row.get("role") or "expected_required"), str(row.get("role") or "expected_required"))
        source = _canonical_assertion_source(row.get("source")).replace("|", "\\|")
        lines.append(
            "| "
            + " | ".join(
                [
                    point,
                    role,
                    source,
                    _point_stage_text(row, "origin_docs").replace("|", "\\|"),
                    _point_stage_text(row, "rerank_docs").replace("|", "\\|"),
                    _point_stage_text(row, "prompt_docs").replace("|", "\\|"),
                    stage_text.get(str(row.get("missing_stage") or ""), str(row.get("missing_stage") or "unknown")),
                ]
            )
            + " |"
        )
    if len(rows) > limit:
        lines.append(f"\n仅展示前 {limit} 条，完整矩阵见 `oracle_status.point_coverage`。")
    return lines


def render_case_report_markdown(result: dict[str, Any], ingest: dict[str, Any], request_dict: dict[str, Any]) -> str:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    primary = result.get("primary_cause") if isinstance(result.get("primary_cause"), dict) else None
    trace_summary = (ingest.get("raw_artifacts") or {}).get("trace_summary") if isinstance((ingest.get("raw_artifacts") or {}).get("trace_summary"), dict) else {}
    counts = trace_summary.get("counts") if isinstance(trace_summary.get("counts"), dict) else _counts_from_request(request_dict)
    workflow_io = _selected_workflow_io(ingest)
    doc_presence = _doc_presence_summary(request_dict)
    point_coverage = doc_presence.get("point_coverage") if isinstance(doc_presence.get("point_coverage"), list) else []
    point_rows = _format_point_coverage_table(point_coverage)
    upper_bound_relation_rows = _format_upper_bound_assertion_relations(point_coverage)
    expected_assertions = (request_dict.get("raw_oracle_fields") or {}).get("expected_knowledge_points") if isinstance(request_dict.get("raw_oracle_fields"), dict) else []
    answer_assertions = [
        item for item in expected_assertions
        if isinstance(item, dict) and str(item.get("role") or "") in {"answer_claim", "unsupported_claim"}
    ] if isinstance(expected_assertions, list) else []
    missing_point_rows = [row for row in point_coverage if isinstance(row, dict) and row.get("missing_stage") == "knowledge"]
    retrieval_point_rows = [row for row in point_coverage if isinstance(row, dict) and row.get("missing_stage") == "retrieval"]
    rerank_point_rows = [row for row in point_coverage if isinstance(row, dict) and row.get("missing_stage") == "rerank"]
    context_point_rows = [row for row in point_coverage if isinstance(row, dict) and row.get("missing_stage") == "context"]
    unavailable_point_rows = [row for row in point_coverage if isinstance(row, dict) and row.get("missing_stage") == "upper_bound_unavailable"]
    stage_rows = []
    for verdict in result.get("evidence_chain") or []:
        if not isinstance(verdict, dict):
            continue
        stage_rows.append(
            f"| {_display_stage(verdict.get('stage'))} | {_display_status(verdict.get('status'))} | {_stage_key_basis(verdict)} |"
        )
    next_actions = result.get("next_actions") if isinstance(result.get("next_actions"), list) else []
    actions = "\n".join(
        f"- `{item.get('priority', '')}` {_display_owner(item.get('owner'))}：{item.get('action', '')}"
        for item in next_actions
        if isinstance(item, dict)
    ) or "- 暂无自动建议。"
    action_extras: list[str] = []
    if missing_point_rows:
        points_text = "；".join(str(item.get("text") or "") for item in missing_point_rows[:5])
        action_extras.append(f"- `P0` 知识库负责人：补充或改写这些必要断言对应的知识：{points_text}")
    if retrieval_point_rows:
        points_text = "；".join(str(item.get("text") or "") for item in retrieval_point_rows[:5])
        action_extras.append(f"- `P0` 召回负责人：理论召回上界可覆盖但线上初召回未命中，检查 query/index/filter/topK：{points_text}")
    if rerank_point_rows:
        points_text = "；".join(str(item.get("text") or "") for item in rerank_point_rows[:5])
        action_extras.append(f"- `P0` 重排策略负责人：初召回已覆盖这些必要断言但重排未保留，检查排序、去重和 topK：{points_text}")
    if context_point_rows:
        points_text = "；".join(str(item.get("text") or "") for item in context_point_rows[:5])
        action_extras.append(f"- `P1` Prompt 组装负责人：这些必要断言已通过重排但未进入 Prompt，检查截断、拼接顺序和 token 预算：{points_text}")
    if doc_presence.get("missing_in_rerank"):
        docs_text = "，".join(
            f"{item.get('id')}（{item.get('title') or '无标题'}）"
            for item in doc_presence.get("missing_in_rerank", [])[:5]
        )
        action_extras.append(f"- `P0` 重排策略负责人：保障这些初召回已命中文档通过重排：{docs_text}")
    if doc_presence.get("missing_in_prompt"):
        docs_text = "，".join(
            f"{item.get('id')}（{item.get('title') or '无标题'}）"
            for item in doc_presence.get("missing_in_prompt", [])[:5]
        )
        action_extras.append(f"- `P1` Prompt 组装负责人：检查这些已通过上游的文档为什么没有进入 Prompt：{docs_text}")
    if action_extras:
        actions = actions + "\n" + "\n".join(action_extras)
    primary_lines = (
        [
            f"- 主因阶段：`{primary.get('stage')}`",
            f"- 主因枚举：`{primary.get('cause_code')}`",
            f"- 置信度：`{primary.get('confidence')}`",
            f"- 建议负责人：`{primary.get('owner')}`",
        ]
        if primary
        else ["- 主因枚举：`null`", "- 建议负责人：人工复核（`manual_review`）"]
    )
    rationale = primary.get("selection_rationale", "") if primary else "; ".join(result.get("human_review_reasons") or [])
    human_review = "是" if result.get("needs_human_review") else "否"
    case_assessment = result.get("case_assessment") if isinstance(result.get("case_assessment"), dict) else {}
    assessment_status = case_assessment.get("status", "indeterminate")
    assessment_reason = case_assessment.get("reason", "")
    oracle_status = result.get("oracle_status") if isinstance(result.get("oracle_status"), dict) else {}
    inferred_doc_ids = _string_list(oracle_status.get("inferred_doc_ids"))
    provided_doc_ids = _string_list(oracle_status.get("provided_doc_ids"))
    expected_ids = doc_presence["expected"]
    if inferred_doc_ids and oracle_status.get("source") in {"self_inferred", "mixed"}:
        expected_text = f"self-oracle 推断：{'，'.join(inferred_doc_ids)}"
    elif provided_doc_ids:
        expected_text = f"宿主提供：{'，'.join(provided_doc_ids)}"
    elif expected_ids:
        expected_text = "，".join(expected_ids)
    else:
        expected_text = "未提供，且 self-oracle 未能构造"
    expected_in_origin = list(doc_presence["expected_in_origin"])
    expected_in_rerank = list(doc_presence["expected_in_rerank"])
    expected_in_prompt = list(doc_presence["expected_in_prompt"])
    origin_set = set(expected_in_origin)
    rerank_set = set(expected_in_rerank)
    prompt_set = set(expected_in_prompt)
    lost_in_recall = [doc_id for doc_id in expected_ids if doc_id not in origin_set]
    lost_in_rerank = [doc_id for doc_id in expected_in_origin if doc_id not in rerank_set]
    lost_in_prompt = [doc_id for doc_id in expected_in_rerank if doc_id not in prompt_set]
    funnel_text = (
        f"初召回 {len(expected_in_origin)}/{len(expected_ids)}"
        f" → 重排 {len(expected_in_rerank)}/{len(expected_in_origin)}"
        f" → Prompt {len(expected_in_prompt)}/{len(expected_in_rerank)}"
    )
    retrieval_state = request_dict.get("retrieval") if isinstance(request_dict.get("retrieval"), dict) else {}
    recall_counts = retrieval_state.get("theoretical_recall_counts") if isinstance(retrieval_state.get("theoretical_recall_counts"), dict) else {}
    recall_counts_text = (
        "，".join(f"{key}={value}" for key, value in recall_counts.items())
        if recall_counts
        else "未配置"
    )
    origin_hits_intro = (
        "初召回中命中的期望文档（前 5 条）："
        if expected_ids
        else "本 case 未提供期望知识 ID，所以无法列出“命中的期望文档”："
    )
    report = [
        "# FindReason 单 Case 归因摘要",
        "",
        "## 1. 结论",
        "",
        *primary_lines,
        f"- Case 判定：{_display_assessment(assessment_status)}",
        f"- 判定原因：{assessment_reason or '未提供'}",
        f"- 是否需要人工复核：`{human_review}`",
        f"- 失败模式：`{', '.join(result.get('failure_patterns') or [])}`",
        "",
        rationale or "证据不足，需要补充探针或人工复核。",
        "",
        "## 2. Case 信息",
        "",
        f"- log_id：`{result.get('log_id') or ingest.get('log_id') or ''}`",
        f"- workspace_id：`{result.get('workspace_id') or ingest.get('workspace_id') or ''}`",
        f"- app_id：`{result.get('app_id') or ingest.get('app_id') or case_input.get('app_id') or ''}`",
        f"- 用户问题：{case_input.get('query') or ''}",
        f"- 评估信号：{case_input.get('judgement') or ''}",
        f"- 证据来源：{'fornax_trace' if trace_summary.get('has_middle_node_trace') else 'trace_or_replay_incomplete'}",
        "",
        "## 3. 原始 Workflow 输入输出",
        "",
        f"- workflow_span_id：`{workflow_io.get('span_id') or ''}`",
        f"- workflow_node_id：`{workflow_io.get('node_id') or ''}`",
        "",
        "### 输入",
        "",
        "```json",
        _report_json(workflow_io.get("input")),
        "```",
        "",
        "### 输出",
        "",
        "```json",
        _report_json(workflow_io.get("output")),
        "```",
        "",
        "## 4. 证据概览",
        "",
        f"- Oracle 来源：`{oracle_status.get('source', 'unknown')}`",
        f"- Oracle 使用信号：`{', '.join(_string_list(oracle_status.get('signals_used'))) or '无'}`",
        f"- Oracle 推断文档数：`{oracle_status.get('inferred_doc_count', 0)}`",
        f"- Oracle 置信度：`{oracle_status.get('confidence', 0)}`",
        f"- Oracle 冲突：`{oracle_status.get('conflict_detected', False)}`",
        f"- 理论召回上界状态：`{retrieval_state.get('theoretical_recall_status', 'not_configured')}`",
        f"- 理论召回范围：`{retrieval_state.get('upper_bound_scope', '未配置')}`",
        f"- 理论召回 topK：`{retrieval_state.get('theoretical_recall_topk', '未配置')}`",
        f"- 理论召回 query：`{', '.join(_string_list(retrieval_state.get('theoretical_query_variants'))) or '未配置'}`",
        f"- 理论召回数量：`{recall_counts_text}`",
        f"- 初召回文档数：`{counts.get('origin_doc_list', 0)}`（trace 里 recall 节点返回的普通知识文档数量）",
        f"- 初召回 FAQ 数：`{counts.get('origin_faq_list', 0)}`（trace 里 recall 节点返回的 FAQ 数量）",
        f"- 重排候选文档数：`{counts.get('rerank_docs', 0)}`（进入 rerank 节点参与排序的文档数量）",
        f"- 进入 Prompt 的文档数：`{counts.get('prompt_docs', 0)}`（最终塞进大模型上下文的文档数量）",
        f"- 期望知识 ID：`{expected_text}`",
        f"- 期望知识阶段漏斗：`{funnel_text}`",
        f"- 初召回丢失的期望知识 ID：`{'，'.join(lost_in_recall) if lost_in_recall else '无'}`",
        f"- 重排丢失的期望知识 ID：`{'，'.join(lost_in_rerank) if lost_in_rerank else '无'}`",
        f"- Prompt 丢失的期望知识 ID：`{'，'.join(lost_in_prompt) if lost_in_prompt else '无'}`",
        "",
        "### 断言覆盖矩阵",
        "",
        *point_rows,
        "",
        "### 理论召回上界与断言关系",
        "",
        *upper_bound_relation_rows,
        "",
        "### 答案断言观察",
        "",
        *(
            [
            f"- {ASSERTION_ROLE_DISPLAY.get(str(row.get('role') or ''), str(row.get('role') or ''))}（{_canonical_assertion_source(row.get('source'))}）：{row.get('text')}"
                for row in answer_assertions[:8]
            ]
            if answer_assertions
            else ["- 无"]
        ),
        "",
        "### 阶段丢失明细",
        "",
        "理论召回上界也未找到承载文档的必要断言（判为知识部分缺失时看这里）：",
        "",
        *(
            [f"- {row.get('text')}" for row in missing_point_rows[:8]]
            if missing_point_rows
            else ["- 无"]
        ),
        "",
        "理论召回上界可覆盖、但线上初召回未命中的必要断言：",
        "",
        *(
            [f"- {row.get('text')}" for row in retrieval_point_rows[:8]]
            if retrieval_point_rows
            else ["- 无"]
        ),
        "",
        "因缺少理论召回上界而不能定性的必要断言：",
        "",
        *(
            [f"- {row.get('text')}" for row in unavailable_point_rows[:8]]
            if unavailable_point_rows
            else ["- 无"]
        ),
        "",
        "初召回已命中、但重排丢失的期望知识 ID / 文档：",
        "",
        _format_doc_gap_items(doc_presence.get("missing_in_rerank", []), "无"),
        "",
        "通过上游、但未进入 Prompt 的期望知识 ID / 文档：",
        "",
        _format_doc_gap_items(doc_presence.get("missing_in_prompt", []), "无"),
        "",
        "必要断言已召回、但重排未承接：",
        "",
        *(
            [f"- {row.get('text')}" for row in rerank_point_rows[:8]]
            if rerank_point_rows
            else ["- 无"]
        ),
        "",
        "必要断言已过重排、但 Prompt 未承接：",
        "",
        *(
            [f"- {row.get('text')}" for row in context_point_rows[:8]]
            if context_point_rows
            else ["- 无"]
        ),
        "",
        origin_hits_intro,
        "",
        "```json",
        _report_json(doc_presence["origin_hits"]),
        "```",
        "",
        "## 5. 归因链路",
        "",
        "| 阶段 | 结论 | 关键依据 |",
        "|---|---|---|",
        *(stage_rows or ["| unknown | indeterminate | no evidence_chain |"]),
        "",
        "## 6. 主因解释",
        "",
        rationale or "当前无法确定主因。",
        "",
        "## 7. 修改建议",
        "",
        actions,
        "",
        "## 8. 附件",
        "",
        "- `ingest.json`",
        "- `probes/*.json`",
        "- `final/attribution_record.json`",
        "- `final/short_summary.json`",
        "- `final/case_report.md`",
    ]
    return "\n".join(report).rstrip() + "\n"


def _next_actions(
    primary: dict[str, Any] | None,
    verdicts: list[dict[str, Any]],
    case_assessment: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    assessment_status = (case_assessment or {}).get("status")
    if assessment_status == "not_badcase":
        return [{"owner": "manual_review", "action": "标记为非 badcase，不进入 RAG 修复队列。", "priority": "P1"}]
    if assessment_status == "not_badcase_candidate":
        return [{"owner": "manual_review", "action": "人工确认评估标签；若确认答案可接受，则从 badcase 集合剔除。", "priority": "P0"}]
    if not primary:
        return [{"owner": "manual_review", "action": "补齐上游 evidence/counterfactual 后重跑 orchestrate", "priority": "P0"}]
    cause = primary["cause_code"]
    actions = {
        "suspected_knowledge_missing": "补入或改写缺失知识，并用相同 log/case 回归验证。",
        "knowledge_topic_mismatch": "补录正主题知识或调整标题/索引描述，避免相邻主题覆盖。",
        "retrieval_miss": "检查 query 构造、索引新鲜度、filter、召回通道与 topK。",
        "permission_miss": "审计 workspace/app/user ACL、namespace、知识状态和检索过滤。",
        "rerank_drop": "检查 rerank 模型、阈值、去重、topK 和多路优先级。",
        "rerank_tunable": "复核可恢复目标文档的参数组合，并做小流量或离线回归。",
        "context_assembly_error": "检查 prompt_docs 构造、截断、token budget 和拼接顺序。",
        "unsupported_claim": "修正答案生成约束，要求只基于 prompt_docs 作答。",
        "wrong_citation": "修正引用映射和 citation 选择策略。",
        "partial_answer": "补齐答案覆盖策略，避免部分证据被当成完整回答。",
        "query_rewrite_drift": "优化 rewrite prompt 或对该类 query 关闭/绕过 rewrite。",
        "keyword_loss": "调优关键词抽取并强制保留关键实体。",
        "non_rag_route_boundary": "将 case 分流到正确的非 RAG 或工具规划路径。",
    }
    return [{"owner": primary["owner"], "action": actions.get(cause, "按 evidence_chain 修复对应阶段。"), "priority": "P0"}]


def _probe_evidence(probe_name: str, stage: str, content: Any, confidence: float = 0.7) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": f"{probe_name}:ev_001",
            "evidence_type": "probe_output",
            "source_stage": stage,
            "source": {"probe_name": probe_name},
            "content": content,
            "quality": {"confidence": confidence},
        }
    ]


def _request_from_ingest(ingest: dict[str, Any]) -> dict[str, Any]:
    request_dict = (ingest.get("raw_artifacts") or {}).get("attribution_request")
    if not isinstance(request_dict, dict):
        raise V3Error("E_INGEST_INVALID", "ingest raw_artifacts.attribution_request is missing.", status_code=2)
    return _normalize_assertion_inputs(request_dict)


def build_probe_result(
    probe_name: str,
    *,
    ingest: dict[str, Any],
    params: dict[str, Any] | None = None,
    no_cache: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    params = params or {}
    workspace_id = str(ingest.get("workspace_id") or params.get("workspace_id") or "")
    log_id = str(ingest.get("log_id") or params.get("log_id") or "")
    cache_key = _stable_hash({"probe_name": probe_name, "params": params})
    cache_file = _cache_path(workspace_id, log_id, f"{probe_name}.{cache_key}.json")
    if cache_file.exists() and not no_cache:
        cached = read_json(cache_file)
        cached.setdefault("telemetry", {})["cache_hit"] = True
        return cached
    request_dict = _request_from_ingest(ingest)
    result = _compute_probe(probe_name, request_dict, ingest, params)
    result["schema_version"] = SCHEMA_VERSION
    result["log_id"] = log_id
    result["workspace_id"] = workspace_id
    result["probe_name"] = probe_name
    result.setdefault("telemetry", {})["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result.setdefault("telemetry", {})["cache_hit"] = False
    result["telemetry"]["cache_key"] = cache_key
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(cache_file, result)
    return result


def _answer_text_from_request(request_dict: dict[str, Any]) -> str:
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    return str(qa.get("answer") or "")


def _probe_target_docs(request_dict: dict[str, Any], target_artifact: str, wide_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if target_artifact == "kb_wide_recall":
        return wide_docs
    if target_artifact == "online_origin_recall":
        return _docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list")
    if target_artifact == "rerank_output":
        return _docs_from_request(request_dict, "rerank", "rerank_docs")
    if target_artifact == "prompt_context":
        return _docs_from_request(request_dict, "rerank", "prompt_docs")
    return []


def _answer_span_hit(query: str, expected_pattern: str, answer_text: str) -> tuple[bool, list[str]]:
    if not answer_text:
        return False, []
    terms = _point_required_terms(expected_pattern) or _point_required_terms(query)
    if not terms:
        terms = _probe_literal_terms(expected_pattern) or _probe_literal_terms(query)
    if not terms:
        return False, []
    matched = [term for term in terms if term and term in answer_text]
    return bool(matched), matched


def _probe_literal_terms(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = re.sub(r"^[：:，,、。；;\"'“”‘’「」【】《》\\s]+|[：:，,、。；;\"'“”‘’「」【】《》\\s]+$", "", term.strip())
        if 2 <= len(cleaned) <= 24 and cleaned not in terms:
            terms.append(cleaned)

    for match in re.findall(r"(?:包含|含有|含|出现|命中|关键词|应包含|应含)([\u4e00-\u9fffA-Za-z0-9_\-]{2,24})", raw):
        add(match)
    for match in re.findall(r"[\"'“”‘’「」【】《》]([^\"'“”‘’「」【】《》]{2,24})[\"'“”‘’「」【】《》]", raw):
        add(match)
    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_\-]{2,24}", raw):
        add(raw)
    return terms[:6]


def run_probe_plan(
    *,
    ingest: dict[str, Any],
    plan: dict[str, Any],
    no_cache: bool = False,
) -> dict[str, Any]:
    """Execute a host-Agent probe-v1 plan as a deterministic CLI probe.

    The host Agent reverse-constructs probe queries (relevance_gap / coverage_gap /
    scope_violation / citation_missing / internal_contradiction). This executor only
    runs each query against the requested evidence face, records deterministic hit/miss
    facts, and aggregates them into stage_signals consumed by orchestrate. It never
    decides the primary cause.
    """
    started = time.perf_counter()
    if not isinstance(plan, dict):
        raise V3Error("E_PROBE_PLAN_INVALID", "probe plan must be a JSON object.", status_code=2)
    if str(plan.get("schema_version") or "") != "probe-v1":
        raise V3Error("E_PROBE_PLAN_SCHEMA", "probe plan schema_version must be 'probe-v1'.", status_code=2)
    raw_probes = plan.get("probes")
    if not isinstance(raw_probes, list):
        raise V3Error("E_PROBE_PLAN_INVALID", "probe plan 'probes' must be a list.", status_code=2)
    for index, probe in enumerate(raw_probes):
        if not isinstance(probe, dict):
            raise V3Error("E_PROBE_PLAN_INVALID", f"probe at index {index} must be a JSON object.", status_code=2)
        probe_id = str(probe.get("probe_id") or f"P-{index + 1}")
        direction = str(probe.get("direction") or "")
        if not direction:
            raise V3Error("E_PROBE_PLAN_INVALID", f"probe {probe_id} must include direction.", status_code=2)
        if direction not in PROBE_PLAN_DIRECTIONS:
            raise V3Error("E_PROBE_DIRECTION_INVALID", f"probe {probe_id} direction={direction} is not supported.", status_code=2)
        target_artifact = str(probe.get("target_artifact") or "")
        if not target_artifact:
            raise V3Error("E_PROBE_PLAN_INVALID", f"probe {probe_id} must include target_artifact.", status_code=2)
        if target_artifact not in PROBE_TARGET_ARTIFACTS:
            raise V3Error("E_PROBE_TARGET_INVALID", f"probe {probe_id} target_artifact={target_artifact} is not supported.", status_code=2)

    workspace_id = str(ingest.get("workspace_id") or "")
    log_id = str(ingest.get("log_id") or "")
    request_dict = _request_from_ingest(ingest)
    answer_text = _answer_text_from_request(request_dict)

    # kb_wide_recall is shared across all probes: run the open-label upper bound once.
    needs_wide_recall = any(
        isinstance(probe, dict) and str(probe.get("target_artifact") or "") == "kb_wide_recall"
        for probe in raw_probes
    )
    wide_docs: list[dict[str, Any]] = []
    wide_recall_result: dict[str, Any] = {}
    theoretical_status = "not_configured"
    if needs_wide_recall:
        topk = max(int((plan.get("probe_execution_hint") or {}).get("topk_recommendation") or 50), 50)
        wide_recall_result = build_probe_result(
            "probe-wide-recall",
            ingest=ingest,
            params={"topk": topk},
            no_cache=no_cache,
        )
        retrieval_signal = (wide_recall_result.get("stage_signals") or {}).get("retrieval") or {}
        wide_docs = [item for item in retrieval_signal.get("wide_recall_docs", []) if isinstance(item, dict)]
        wide_docs += [item for item in retrieval_signal.get("wide_recall_faqs", []) if isinstance(item, dict)]
        theoretical_status = str(retrieval_signal.get("theoretical_recall_status") or "indeterminate")

    executed_probes: list[dict[str, Any]] = []
    for index, probe in enumerate(raw_probes):
        probe_id = str(probe.get("probe_id") or f"P-{index + 1}")
        direction = str(probe.get("direction") or "")
        role = _normalize_assertion_role(probe.get("role"), "expected_required")
        target_artifact = str(probe.get("target_artifact") or "")
        query = str(probe.get("query") or "")
        expected_pattern = str(probe.get("expected_hit_pattern") or "")
        if target_artifact == "answer_span":
            hit, matched_terms = _answer_span_hit(query, expected_pattern, answer_text)
            matched_docs: list[dict[str, Any]] = []
            executed = True
            skip_reason = ""
        elif target_artifact == "kb_wide_recall" and theoretical_status != "ok":
            hit = None
            matched_terms = []
            matched_docs = []
            executed = False
            skip_reason = "kb_wide_recall_unavailable"
        else:
            target_docs = _probe_target_docs(request_dict, target_artifact, wide_docs)
            matched_docs = _point_doc_matches(query or expected_pattern, target_docs)
            hit = bool(matched_docs)
            matched_terms = []
            executed = True
            skip_reason = ""
        converged = "" if hit is None else str(probe.get("if_hit") if hit else probe.get("if_miss") or "")
        executed_probes.append(
            {
                "probe_id": probe_id,
                "direction": direction,
                "role": role,
                "query": query,
                "target_artifact": target_artifact,
                "expected_hit_pattern": expected_pattern,
                "executed": executed,
                "hit": hit,
                "matched_docs": matched_docs,
                "matched_terms": matched_terms,
                "converged_direction": converged,
                "evidence_id": f"run-probe-plan:{probe_id}",
                **({"skip_reason": skip_reason} if skip_reason else {}),
            }
        )

    stage_signals = _probe_plan_stage_signals(executed_probes, wide_recall_result)
    content = {
        "schema_version_plan": "probe-v1",
        "extracted": plan.get("extracted") if isinstance(plan.get("extracted"), dict) else {},
        "probe_results": executed_probes,
        "theoretical_recall_status": theoretical_status,
    }
    evidence_bundle = [
        {
            "evidence_id": probe["evidence_id"],
            "evidence_type": "probe_output",
            "source_stage": "plan",
            "source": {"probe_name": "run-probe-plan", "probe_id": probe["probe_id"], "direction": probe["direction"]},
            "content": {
                "query": probe["query"],
                "target_artifact": probe["target_artifact"],
                "hit": probe["hit"],
                "matched_docs": probe["matched_docs"],
                "converged_direction": probe["converged_direction"],
            },
            "quality": {"confidence": 0.78},
        }
        for probe in executed_probes
    ]
    if wide_recall_result.get("evidence_bundle"):
        evidence_bundle.extend(wide_recall_result["evidence_bundle"])
    result = {
        "schema_version": SCHEMA_VERSION,
        "log_id": log_id,
        "workspace_id": workspace_id,
        "probe_name": "run-probe-plan",
        "status": "ok",
        "stage_signals": stage_signals,
        "raw_artifacts": {
            "probe_plan": plan,
            "wide_recall": wide_recall_result.get("raw_artifacts", {}),
        },
        "evidence_bundle": evidence_bundle,
    }
    result.setdefault("telemetry", {})["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["telemetry"]["probe_count"] = len(executed_probes)
    if "content" not in result:
        result["content"] = content
    return result


def _probe_plan_stage_signals(
    executed_probes: list[dict[str, Any]],
    wide_recall_result: dict[str, Any],
) -> dict[str, Any]:
    """Fold probe-plan execution results into the existing stage_signals contract.

    relevance_gap / coverage_gap probes feed the deterministic point_coverage chain
    (knowledge / retrieval / rerank / context). scope_violation / citation_missing /
    internal_contradiction probes set new answer / knowledge flags consumed by
    _infer_verdicts. converged_direction is recorded as a hypothesis only.
    """
    signals: dict[str, Any] = {}

    # Reuse the wide-recall retrieval signal verbatim so the theoretical upper bound,
    # point_coverage missing_stage logic, and report fields all keep working.
    wide_retrieval = (wide_recall_result.get("stage_signals") or {}).get("retrieval")
    if isinstance(wide_retrieval, dict):
        signals["retrieval"] = json.loads(json.dumps(wide_retrieval, ensure_ascii=False, default=_json_default))

    answer_signal: dict[str, Any] = {}
    knowledge_signal: dict[str, Any] = {}
    for probe in executed_probes:
        direction = probe["direction"]
        hit = probe["hit"]
        if hit is None:
            continue
        if direction == "scope_violation" and not hit:
            # The scoped fact is unsupported in KB/prompt or absent from the answer text -> answer overreached or shifted objects.
            answer_signal["scope_violation"] = True
        elif direction == "citation_missing":
            if hit:
                # Authoritative source exists in KB but answer/prompt did not cite it.
                answer_signal["wrong_citation"] = True
            else:
                knowledge_signal["lacks_authoritative_source"] = True
        elif direction == "internal_contradiction":
            if hit:
                # KB clarifies the branch premises but the answer did not distinguish them.
                answer_signal["branching_unclear"] = True
            else:
                knowledge_signal["internal_inconsistency"] = True
    if answer_signal:
        signals["answer"] = answer_signal
    if knowledge_signal:
        signals["knowledge"] = knowledge_signal
    return signals


def _compute_probe(probe_name: str, request_dict: dict[str, Any], ingest: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if probe_name == "probe-self-oracle":
        return _probe_self_oracle(probe_name, request_dict, params)
    if probe_name == "probe-knowledge-detail":
        return _probe_knowledge_detail(probe_name, request_dict, params)
    if probe_name == "probe-permission-check":
        return _probe_permission_check(probe_name, request_dict, params)
    if probe_name == "probe-wide-recall":
        return _probe_wide_recall(probe_name, request_dict, ingest, params)
    if probe_name == "probe-rerank-bypass":
        return _probe_rerank_bypass(probe_name, request_dict, params)
    if probe_name == "probe-rerank-tune":
        return _probe_rerank_tune(probe_name, request_dict, params)
    if probe_name == "probe-context-assembly":
        return _probe_context_assembly(probe_name, request_dict, params)
    if probe_name == "probe-by-judgement":
        return _probe_by_judgement(probe_name, request_dict, params)
    if probe_name == "probe-by-claim":
        return _probe_by_claim(probe_name, request_dict, params)
    if probe_name == "probe-by-doc-title":
        return _probe_by_doc_title(probe_name, request_dict, params)
    raise V3Error("E_UNKNOWN_PROBE", f"Unknown probe command: {probe_name}", status_code=2)


def _expected_ids(request_dict: dict[str, Any], params: dict[str, Any]) -> list[str]:
    ids = _string_list(params.get("doc_ids"))
    if ids:
        return ids
    return _string_list((request_dict.get("case_input") or {}).get("expected_knowledge_ids"))


def _request_for_legacy_model(request_dict: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(request_dict, ensure_ascii=False, default=_json_default))
    judgement = payload.get("judgement_evidence") if isinstance(payload.get("judgement_evidence"), dict) else {}
    signals = judgement.get("signals") if isinstance(judgement.get("signals"), list) else []
    normalized: list[dict[str, Any]] = []
    changed = False
    for index, signal in enumerate(signals):
        if not isinstance(signal, dict):
            normalized.append({"key": f"signal_{index}", "value": signal, "source": "judgement", "evidence_text": str(signal)})
            changed = True
            continue
        item = dict(signal)
        if not item.get("key"):
            item["key"] = str(item.get("dimension") or item.get("label") or f"signal_{index}")
            item["value"] = item.get("value", item.get("result"))
            item["evidence_text"] = str(item.get("reason") or item.get("evidence_text") or "")
            item["source"] = str(item.get("source") or "judgement")
            changed = True
        normalized.append(item)
    if changed:
        payload.setdefault("judgement_evidence", {})["signals"] = normalized
    return payload


def _all_docs(request_dict: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *_docs_from_request(request_dict, "retrieval", "origin_doc_list"),
        *_docs_from_request(request_dict, "retrieval", "origin_faq_list"),
        *_docs_from_request(request_dict, "retrieval", "wide_recall_docs"),
        *_docs_from_request(request_dict, "retrieval", "wide_recall_faqs"),
        *_docs_from_request(request_dict, "rerank", "rerank_docs"),
        *_docs_from_request(request_dict, "rerank", "prompt_docs"),
        *(_docs_from_request(request_dict, "reference", "support_docs") if isinstance(request_dict.get("reference"), dict) else []),
    ]


ORACLE_SIGNAL_SOURCES = ("judgement_back_recall", "claim_back_recall", "query_wide_recall")


def _upper_bound_query_variants(request_dict: dict[str, Any]) -> list[str]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    preprocess = request_dict.get("preprocess") if isinstance(request_dict.get("preprocess"), dict) else {}
    candidates = [case_input.get("query"), preprocess.get("rewrite_query")]
    variants: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        if not normalized or normalized in seen:
            continue
        variants.append(normalized[:200])
        seen.add(normalized)
    return variants


def _doc_id(doc: dict[str, Any]) -> str:
    return str(doc.get("id") or doc.get("doc_id") or doc.get("docId") or "").strip()


def _doc_title(doc: dict[str, Any]) -> str:
    return str(doc.get("title") or doc.get("name") or doc.get("doc_title") or "").strip()


def _doc_text(doc: dict[str, Any]) -> str:
    fields = [
        doc.get("title"),
        doc.get("name"),
        doc.get("content"),
        doc.get("text"),
        doc.get("summary"),
        doc.get("chunk"),
        doc.get("snippet"),
        doc.get("answer"),
        doc.get("source"),
    ]
    return " ".join(str(item) for item in fields if item not in (None, ""))


def _doc_body_text(doc: dict[str, Any]) -> str:
    fields = [
        doc.get("content"),
        doc.get("text"),
        doc.get("summary"),
        doc.get("chunk"),
        doc.get("snippet"),
        doc.get("answer"),
        doc.get("source"),
    ]
    return " ".join(str(item) for item in fields if item not in (None, ""))


def _unique_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in docs:
        doc_id = _doc_id(doc)
        key = doc_id or _stable_hash(doc)
        if key in seen:
            continue
        seen.add(key)
        result.append(doc)
    return result


def _oracle_tokens(text: Any) -> set[str]:
    raw = str(text or "").lower()
    latin = {item for item in re.findall(r"[a-z0-9_]{2,}", raw) if len(item) >= 2}
    cjk = set(re.findall(r"[\u4e00-\u9fff]", raw))
    # Keep short Chinese bigrams for better matching on product/function names.
    compact_cjk = "".join(re.findall(r"[\u4e00-\u9fff]", raw))
    bigrams = {compact_cjk[i : i + 2] for i in range(max(0, len(compact_cjk) - 1))}
    return latin | cjk | bigrams


def _oracle_score(signal_text: str, doc: dict[str, Any]) -> float:
    signal_tokens = _oracle_tokens(signal_text)
    doc_tokens = _oracle_tokens(_doc_text(doc))
    if not signal_tokens or not doc_tokens:
        return 0.0
    overlap = signal_tokens & doc_tokens
    score = len(overlap) / max(1, len(signal_tokens))
    title_tokens = _oracle_tokens(_doc_title(doc))
    if title_tokens:
        score += min(0.2, len(signal_tokens & title_tokens) / max(1, len(title_tokens)) * 0.2)
    return round(min(1.0, score), 4)


def _claim_texts(request_dict: dict[str, Any]) -> list[str]:
    claims: list[str] = []
    for item in _host_answer_claim_items(request_dict):
        if isinstance(item, dict):
            value = item.get("text") or item.get("claim") or item.get("content") or item.get("value")
            if value:
                claims.append(str(value))
        elif item not in (None, ""):
            claims.append(str(item))
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    if not claims and qa.get("answer"):
        claims.append(str(qa.get("answer")))
    return claims


def _oracle_signal_text(request_dict: dict[str, Any], source: str) -> str:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    judgement = request_dict.get("judgement_evidence") if isinstance(request_dict.get("judgement_evidence"), dict) else {}
    preprocess = request_dict.get("preprocess") if isinstance(request_dict.get("preprocess"), dict) else {}
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    if source == "judgement_back_recall":
        parts = [case_input.get("judgement"), judgement.get("raw_text")]
        for signal in judgement.get("signals") or []:
            if isinstance(signal, dict):
                parts.extend([signal.get("label"), signal.get("result"), signal.get("reason"), signal.get("value")])
            else:
                parts.append(signal)
        return " ".join(str(item) for item in parts if item not in (None, ""))
    if source == "claim_back_recall":
        return " ".join(_claim_texts(request_dict))
    if source == "query_wide_recall":
        parts = [case_input.get("query"), preprocess.get("rewrite_query")]
        keywords = preprocess.get("keywords") or preprocess.get("keyword")
        parts.extend(_as_list(keywords))
        return " ".join(str(item) for item in parts if item not in (None, ""))
    return ""


POINT_KEYWORDS = (
    "应",
    "应该",
    "正确",
    "核心问题",
    "用户问",
    "遗漏",
    "未提供",
    "未覆盖",
    "不一致",
    "矛盾",
    "入口",
    "路径",
    "设置",
    "查看",
    "周期",
    "多久",
    "时间",
    "预警",
    "短视频",
    "全域",
    "铺底",
    "家居",
    "素材",
)

EVALUATION_LABEL_NAMES = {
    "无矛盾",
    "是否回答",
    "问题无遗漏",
    "相关性",
    "范围最小",
    "有参考链接",
    "事实正确性",
    "answer_presence",
    "coverage",
    "relevance",
    "minimal_scope",
    "factuality",
    "citation",
    "contradiction_free",
}

ASSERTION_ROLES = {
    "expected_required",
    "missing_expected",
    "answer_claim",
    "unsupported_claim",
    "constraint_check",
    "citation_check",
    "consistency_check",
}
REQUIRED_ASSERTION_ROLES = {"expected_required", "missing_expected"}
# Probe-plan directions accepted from the host Agent's probe-v1 plan.
PROBE_PLAN_DIRECTIONS = {
    "relevance_gap",
    "coverage_gap",
    "scope_violation",
    "citation_missing",
    "internal_contradiction",
}
# Evidence faces a probe query may target inside the trace/recall pipeline.
PROBE_TARGET_ARTIFACTS = {
    "kb_wide_recall",
    "online_origin_recall",
    "rerank_output",
    "prompt_context",
    "answer_span",
}
CANONICAL_ASSERTION_SOURCE = "host_agent.answer_claim"
UNSUPPORTED_ASSERTION_STATUSES = {
    "unsupported",
    "not_supported",
    "no_support",
    "unsupported_by_prompt",
    "contradicted",
    "contradict",
    "conflict",
    "conflicted",
    "partially_supported",
    "partially-supported",
}


def _looks_like_evaluation_label(text: Any) -> bool:
    value = re.sub(r"\s+", "", str(text or "")).strip("；;。")
    if not value:
        return False
    if value.startswith("评估器失败项"):
        return True
    if len(value) > 80:
        return False
    match = re.match(r"^([^=：:]{1,24})[=：:](.+)$", value)
    if match:
        left = match.group(1).strip()
        right = match.group(2).strip()
        if left in EVALUATION_LABEL_NAMES:
            return True
        if right in {"是", "否", "未知", "矛盾", "一致", "未覆盖/无法判断", "不相关", "通过", "不通过"}:
            return True
    return value in EVALUATION_LABEL_NAMES


def _looks_like_diagnostic_text(text: Any) -> bool:
    value = re.sub(r"\s+", "", str(text or "")).strip()
    if not value:
        return True
    diagnostic_markers = (
        "Agent_Reply为空",
        "未提供任何事实性信息",
        "显然未回答用户问题",
        "未对该问题点做任何回应",
        "存在遗漏",
        "判定为不通过",
        "评分为0",
        "符合该规则",
        "不符合该规则",
    )
    return any(marker in value for marker in diagnostic_markers)


def _normalize_assertion_role(value: Any, default_role: str) -> str:
    role = str(value or default_role or "").strip()
    aliases = {
        "required": "expected_required",
        "expected": "expected_required",
        "expected_point": "expected_required",
        "expected_required": "expected_required",
        "missing": "missing_expected",
        "missing_point": "missing_expected",
        "missing_expected": "missing_expected",
        "claim": "answer_claim",
        "answer_claim": "answer_claim",
        "unsupported": "unsupported_claim",
        "unsupported_claim": "unsupported_claim",
        "contradicted": "unsupported_claim",
        "constraint": "constraint_check",
        "constraint_check": "constraint_check",
        "scope": "constraint_check",
        "citation": "citation_check",
        "citation_check": "citation_check",
        "consistency": "consistency_check",
        "consistency_check": "consistency_check",
        "contradiction": "consistency_check",
    }
    return aliases.get(role, role if role in ASSERTION_ROLES else default_role)


def _canonical_assertion_source(_: Any = None) -> str:
    return CANONICAL_ASSERTION_SOURCE


def _host_answer_claim_items(request_dict: dict[str, Any]) -> list[Any]:
    host_agent = request_dict.get("host_agent") if isinstance(request_dict.get("host_agent"), dict) else {}
    return _as_list(host_agent.get("answer_claim"))


def _assertion_item_text(item: Any) -> str:
    if isinstance(item, dict):
        value = item.get("text") or item.get("point") or item.get("content") or item.get("value") or item.get("claim") or item.get("assertion") or item.get("fact")
        return _clean_point_text(value)
    return _clean_point_text(item)


def _legacy_assertion_fields(request_dict: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    judgement = request_dict.get("judgement_evidence") if isinstance(request_dict.get("judgement_evidence"), dict) else {}
    if any(_assertion_item_text(item) for item in _as_list(case_input.get("expected_knowledge_points"))):
        fields.append("case_input.expected_knowledge_points")
    for key in ("answer_claims", "missing_expected_points", "unsupported_claims", "claim_alignments"):
        if any(_assertion_item_text(item) for item in _as_list(qa.get(key))):
            fields.append(f"qa.{key}")
    for key in ("answer_claims", "missing_expected_points", "unsupported_claims", "claim_alignments", "expected_knowledge_points"):
        if any(_assertion_item_text(item) for item in _as_list(request_dict.get(key))):
            fields.append(key)
    for index, signal in enumerate(judgement.get("signals") or []):
        if not isinstance(signal, dict):
            continue
        for key in ("assertions", "fact_points", "facts", "missing_expected_points"):
            if any(_assertion_item_text(item) for item in _as_list(signal.get(key))):
                fields.append(f"judgement_evidence.signals[{index}].{key}")
    return fields


def _raise_if_legacy_assertion_inputs(request_dict: dict[str, Any]) -> None:
    fields = _legacy_assertion_fields(request_dict)
    if not fields:
        return
    raise V3Error(
        "E_LEGACY_ASSERTION_INPUT",
        "Legacy assertion inputs are not accepted. The host Agent must consolidate assertions into host_agent.answer_claim.",
        status_code=2,
        details={"fields": fields, "required_field": "host_agent.answer_claim"},
    )


def _drop_legacy_assertion_fields(request_dict: dict[str, Any]) -> dict[str, Any]:
    case_input = request_dict.get("case_input") if isinstance(request_dict.get("case_input"), dict) else {}
    case_input.pop("expected_knowledge_points", None)
    qa = request_dict.get("qa") if isinstance(request_dict.get("qa"), dict) else {}
    for key in ("answer_claims", "missing_expected_points", "unsupported_claims", "claim_alignments"):
        qa.pop(key, None)
    for key in ("answer_claims", "missing_expected_points", "unsupported_claims", "claim_alignments", "expected_knowledge_points"):
        request_dict.pop(key, None)
    judgement = request_dict.get("judgement_evidence") if isinstance(request_dict.get("judgement_evidence"), dict) else {}
    for signal in judgement.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        for key in ("assertions", "fact_points", "facts", "missing_expected_points"):
            signal.pop(key, None)
    return request_dict


def _clean_point_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^依据[^，。；:：]{0,80}[，:：]\s*", "", text)
    text = re.sub(r"^根据[^，。；:：]{0,80}[，:：]\s*", "", text)
    text = re.sub(r"^规则[^，。；:：]{0,80}[，:：]\s*", "", text)
    text = text.strip(" ；;。")
    return text[:180].strip()


def _split_point_candidates(value: Any) -> list[str]:
    text = str(value or "")
    parts = re.split(r"[。；;\n\r]+", text)
    result: list[str] = []
    for part in parts:
        cleaned = _clean_point_text(part)
        if len(cleaned) < 6:
            continue
        if _looks_like_evaluation_label(cleaned):
            continue
        if any(keyword in cleaned for keyword in POINT_KEYWORDS):
            result.append(cleaned)
    return result


def _expected_knowledge_points(request_dict: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(text: Any, source: str, confidence: float, role: str) -> None:
        cleaned = _clean_point_text(text)
        if len(cleaned) < 3:
            return
        if _looks_like_evaluation_label(cleaned):
            return
        if _looks_like_diagnostic_text(cleaned):
            return
        normalized_role = _normalize_assertion_role(role, "expected_required")
        normalized_text_key = re.sub(r"\W+", "", cleaned.lower())
        if not normalized_text_key:
            return
        key = f"{normalized_role}:{normalized_text_key}"
        if key in seen:
            return
        seen.add(key)
        points.append(
            {
                "point_id": f"kp_{len(points) + 1:03d}",
                "text": cleaned,
                "source": _canonical_assertion_source(source),
                "role": normalized_role,
                "confidence": round(float(confidence), 4),
            }
        )

    def add_point_item(item: Any, source: str, default_confidence: float, default_role: str) -> None:
        if isinstance(item, dict):
            value = item.get("text") or item.get("point") or item.get("content") or item.get("value") or item.get("claim") or item.get("assertion") or item.get("fact")
            confidence = item.get("confidence", default_confidence)
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError):
                confidence_value = default_confidence
            role = _normalize_assertion_role(item.get("role") or item.get("assertion_role"), default_role)
            source_value = _canonical_assertion_source(item.get("source") or source)
            add(value, source_value, confidence_value, role)
        else:
            add(item, source, default_confidence, default_role)

    for item in _host_answer_claim_items(request_dict):
        add_point_item(item, CANONICAL_ASSERTION_SOURCE, 0.55, "answer_claim")
    return points[:20]


def _normalize_assertion_inputs(request_dict: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(request_dict, ensure_ascii=False, default=_json_default))
    _raise_if_legacy_assertion_inputs(normalized)
    host_agent = normalized.setdefault("host_agent", {})
    if not isinstance(host_agent, dict):
        normalized["host_agent"] = {}
        host_agent = normalized["host_agent"]
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Any, default_role: str, default_confidence: float) -> None:
        if isinstance(item, dict):
            text = item.get("text") or item.get("point") or item.get("content") or item.get("value") or item.get("claim") or item.get("assertion") or item.get("fact")
            role = _normalize_assertion_role(item.get("role") or item.get("assertion_role"), default_role)
            confidence_raw = item.get("confidence", default_confidence)
        else:
            text = item
            role = default_role
            confidence_raw = default_confidence
        cleaned = _clean_point_text(text)
        if not cleaned:
            return
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = default_confidence
        normalized_text_key = re.sub(r"\W+", "", cleaned.lower())
        key = f"{role}:{normalized_text_key}"
        if key in seen:
            return
        seen.add(key)
        claims.append(
            {
                "text": cleaned,
                "role": role,
                "source": CANONICAL_ASSERTION_SOURCE,
                "confidence": round(confidence, 4),
            }
        )

    for item in _as_list(host_agent.get("answer_claim")):
        add(item, "answer_claim", 0.55)
    host_agent["answer_claim"] = claims
    return normalized


def _required_assertion_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point for point in points if str(point.get("role") or "expected_required") in REQUIRED_ASSERTION_ROLES]


def _has_answer_claim_role(request_dict: dict[str, Any], role: str) -> bool:
    return any(str(point.get("role") or "") == role for point in _expected_knowledge_points(request_dict))


def _point_required_terms(text: str) -> list[str]:
    raw = str(text or "")
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = re.sub(r"^[：:，,、的地得在是为与和或及]+|[：:，,、的地得在是为与和或及]+$", "", term.strip())
        if 2 <= len(cleaned) <= 24 and cleaned not in terms:
            terms.append(cleaned)

    for match in re.findall(r"[“\"'‘’「【《]([^”\"'‘’」】》]{2,24})[”\"'‘’」】》]", raw):
        add(match)
    trigger_pattern = (
        r"(?:如何|怎么|怎样|查看|设置|补充|说明|提供|覆盖|回答|"
        r"正确入口为|正确路径为|入口为|路径为|核心问题是|问的是|限定在|应为|应该)"
        r"([\u4e00-\u9fffA-Za-z0-9_\-「」【】《》]{2,24})"
    )
    for match in re.findall(trigger_pattern, raw):
        add(match)
    normalized = re.sub(r"(用户期望答案覆盖|Agent|回复|验证方|工具B|规则|依据|根据|判定|核心问题|正确答案)", " ", raw)
    for chunk in re.split(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", normalized):
        chunk = chunk.strip()
        if 2 <= len(chunk) <= 16 and any(keyword in chunk for keyword in POINT_KEYWORDS):
            add(chunk)
    return terms[:6]


def _doc_brief(
    doc: dict[str, Any],
    *,
    score: float | None = None,
    matched_terms: list[str] | None = None,
    support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": _doc_id(doc),
        "title": _doc_title(doc),
        "rank": doc.get("rank"),
        "source": doc.get("source"),
    }
    if score is not None:
        item["score"] = round(float(score), 4)
    if matched_terms:
        item["matched_terms"] = matched_terms[:5]
    if support:
        for key in ("support_status", "support_score", "support_spans", "missing_constraints"):
            value = support.get(key)
            if value not in (None, "", []):
                item[key] = value
    return item


SUPPORT_FULL_THRESHOLD = 0.42
SUPPORT_PARTIAL_THRESHOLD = 0.3
SUPPORT_ACCEPTED_STATUSES = {"full_support", "partial_support"}


def _support_span_candidates(text: Any, *, limit: int = 80) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(span: str) -> None:
        cleaned = re.sub(r"\s+", " ", span).strip(" ，,、:：；;。")
        if len(cleaned) < 4 or cleaned in seen:
            return
        seen.add(cleaned)
        candidates.append(cleaned)

    if len(normalized) <= 240:
        add(normalized)
    parts = [
        part.strip(" ，,、:：；;。")
        for part in re.split(r"[。！？!?；;\n\r]+", normalized)
        if part.strip(" ，,、:：；;。")
    ]
    for part in parts:
        if len(part) <= 240:
            add(part)
            continue
        for start in range(0, len(part), 160):
            add(part[start : start + 240])
            if len(candidates) >= limit:
                return candidates[:limit]
    for index in range(len(parts) - 1):
        combined = f"{parts[index]}。{parts[index + 1]}".strip(" ，,、:：；;。")
        if len(combined) <= 240:
            add(combined)
        if len(candidates) >= limit:
            break
    if not candidates:
        add(normalized[:240])
    return candidates[:limit]


def _doc_support_evidence(point_text: str, doc: dict[str, Any], *, terms: list[str]) -> dict[str, Any]:
    body_text = _doc_body_text(doc)
    doc_text = _doc_text(doc)
    lexical_terms = [term for term in terms if term and term in doc_text]
    doc_score = _oracle_score(point_text, doc)
    scored_spans: list[tuple[float, int, str, list[str]]] = []
    for span in _support_span_candidates(body_text):
        span_score = _oracle_score(point_text, {"content": span})
        span_terms = [term for term in terms if term and term in span]
        if span_score <= 0:
            continue
        scored_spans.append((span_score, len(span_terms), span, span_terms))
    scored_spans.sort(key=lambda item: (item[0], item[1], len(item[2])), reverse=True)
    best_score = scored_spans[0][0] if scored_spans else 0.0
    best_terms = scored_spans[0][3] if scored_spans else []
    support_spans = [span for score, _, span, _ in scored_spans[:2] if score >= SUPPORT_PARTIAL_THRESHOLD]
    missing_constraints = [term for term in terms if term and term not in (support_spans[0] if support_spans else body_text)]

    if best_score >= SUPPORT_FULL_THRESHOLD:
        status = "full_support"
    elif best_score >= SUPPORT_PARTIAL_THRESHOLD:
        status = "partial_support"
    elif (lexical_terms and doc_score >= 0.035) or doc_score >= 0.48:
        status = "lexical_match_only"
    else:
        status = "no_support"
    return {
        "support_status": status,
        "support_score": round(best_score, 4),
        "support_spans": support_spans[:2],
        "matched_terms": (best_terms or lexical_terms)[:5],
        "missing_constraints": missing_constraints[:5],
        "score": doc_score,
    }


def _point_doc_matches(point_text: str, docs: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    terms = _point_required_terms(point_text) or _probe_literal_terms(point_text)
    for doc in _unique_docs(docs):
        support = _doc_support_evidence(point_text, doc, terms=terms)
        if support["support_status"] in SUPPORT_ACCEPTED_STATUSES:
            matches.append(_doc_brief(doc, score=support["score"], matched_terms=support["matched_terms"], support=support))
    status_rank = {"full_support": 2, "partial_support": 1}
    matches.sort(
        key=lambda item: (
            status_rank.get(str(item.get("support_status") or ""), 0),
            float(item.get("support_score") or 0.0),
            float(item.get("score") or 0.0),
            len(item.get("matched_terms") or []),
        ),
        reverse=True,
    )
    return matches[:limit]


def _knowledge_point_coverage(request_dict: dict[str, Any], points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retrieval = request_dict.get("retrieval") if isinstance(request_dict.get("retrieval"), dict) else {}
    upper_bound_docs = _docs_from_request(request_dict, "retrieval", "wide_recall_docs") + _docs_from_request(request_dict, "retrieval", "wide_recall_faqs")
    upper_bound_status = str(retrieval.get("theoretical_recall_status") or ("ok" if upper_bound_docs else "not_configured"))
    upper_bound_available = upper_bound_status == "ok"
    origin_docs = _docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list")
    rerank_docs = _docs_from_request(request_dict, "rerank", "rerank_docs")
    prompt_docs = _docs_from_request(request_dict, "rerank", "prompt_docs")
    coverage: list[dict[str, Any]] = []
    for point in _required_assertion_points(points):
        text = str(point.get("text") or "")
        upper_bound_matches = _point_doc_matches(text, upper_bound_docs)
        origin_matches = _point_doc_matches(text, origin_docs)
        rerank_matches = _point_doc_matches(text, rerank_docs)
        prompt_matches = _point_doc_matches(text, prompt_docs)
        if origin_matches:
            # 线上初召回已命中 → 知识确实存在，只看下游链路，绝不回判为知识缺失
            if not rerank_matches:
                missing_stage = "rerank"
            elif not prompt_matches:
                missing_stage = "context"
            else:
                missing_stage = "covered"
        elif upper_bound_available and upper_bound_matches:
            # 上界能覆盖但线上没召回 → 线上召回漏召
            missing_stage = "retrieval"
        elif upper_bound_available and not upper_bound_matches:
            # 上界也找不到承载文档 → 知识缺失
            missing_stage = "knowledge"
        else:
            missing_stage = "upper_bound_unavailable"
        coverage.append(
            {
                **point,
                "required_terms": _point_required_terms(text),
                "missing_stage": missing_stage,
                "upper_bound_status": upper_bound_status,
                "upper_bound_docs": upper_bound_matches,
                "origin_docs": origin_matches,
                "rerank_docs": rerank_matches,
                "prompt_docs": prompt_matches,
            }
        )
    return coverage


def _oracle_source_confidence(source: str, score: float) -> float:
    if source == "judgement_back_recall":
        return round(min(0.95, 0.8 + score * 0.15), 4)
    if source == "claim_back_recall":
        return round(min(0.8, 0.6 + score * 0.2), 4)
    return round(min(0.6, 0.4 + score * 0.2), 4)


def _combine_confidences(values: list[float]) -> float:
    miss_probability = 1.0
    for value in values:
        miss_probability *= max(0.0, 1.0 - float(value or 0.0))
    return round(min(0.98, 1.0 - miss_probability), 4)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _oracle_doc_sets(inferred_docs: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for doc in inferred_docs:
        doc_id = str(doc.get("doc_id") or "")
        if not doc_id:
            continue
        for source in doc.get("oracle_sources") or []:
            result.setdefault(str(source), set()).add(doc_id)
    return result


def _oracle_conflict(inferred_docs: list[dict[str, Any]]) -> tuple[bool, float | None]:
    source_sets = [value for value in _oracle_doc_sets(inferred_docs).values() if value]
    if len(source_sets) < 2:
        return False, None
    scores = [_jaccard(source_sets[i], source_sets[j]) for i in range(len(source_sets)) for j in range(i + 1, len(source_sets))]
    min_score = min(scores) if scores else 1.0
    return min_score < 0.1, round(min_score, 4)


def _oracle_compare_to_flow(request_dict: dict[str, Any], inferred_ids: list[str]) -> dict[str, Any]:
    ids = set(_string_list(inferred_ids))
    origin_ids = _doc_ids(_docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list"))
    rerank_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "rerank_docs"))
    prompt_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "prompt_docs"))
    origin_hit = ids & origin_ids
    rerank_hit = ids & rerank_ids
    prompt_hit = ids & prompt_ids
    return {
        "origin_hit_ids": sorted(origin_hit),
        "rerank_hit_ids": sorted(rerank_hit),
        "prompt_hit_ids": sorted(prompt_hit),
        "missing_from_origin_ids": sorted(ids - origin_hit),
        "missing_from_rerank_ids": sorted((ids & origin_ids) - rerank_hit),
        "missing_from_prompt_ids": sorted((ids & (origin_ids | rerank_ids)) - prompt_hit),
        "partial_retrieval_miss": bool(origin_hit and ids - origin_hit),
        "partial_rerank_drop": bool(rerank_hit and (ids & origin_ids) - rerank_hit),
        "partial_context_miss": bool(prompt_hit and (ids & (origin_ids | rerank_ids)) - prompt_hit),
    }


def _probe_self_oracle(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    requested_sources = _string_list(params.get("signals")) or list(ORACLE_SIGNAL_SOURCES)
    sources = [source for source in requested_sources if source in ORACLE_SIGNAL_SOURCES] or list(ORACLE_SIGNAL_SOURCES)
    topk = int(params.get("topk") or 50)
    docs = _unique_docs(_all_docs(request_dict))
    provided_ids = _string_list((request_dict.get("case_input") or {}).get("expected_knowledge_ids"))
    expected_points = _expected_knowledge_points(request_dict)
    required_points = _required_assertion_points(expected_points)
    if not required_points and not provided_ids:
        oracle_status = {
            "source": "insufficient_assertions",
            "signals_used": [],
            "signals_attempted": list(sources),
            "signals_failed": {"expected_assertions": "no expected_required or missing_expected assertions were supplied"},
            "inferred_doc_count": 0,
            "inferred_doc_ids": [],
            "provided_doc_ids": [],
            "confidence": 0.0,
            "conflict_detected": False,
            "provided_conflict_detected": False,
            "no_docs_found": False,
            "assertion_status": "insufficient_required_assertions",
            "expected_knowledge_points": expected_points,
            "point_coverage": [],
            "missing_expected_points_from_theoretical_recall": [],
            "missing_expected_points_from_origin": [],
            "missing_expected_points_from_rerank": [],
            "missing_expected_points_from_prompt": [],
        }
        content = {
            "oracle_status": oracle_status,
            "inferred_expected_docs": [],
            "expected_knowledge_points": expected_points,
            "point_coverage": [],
        }
        return {
            "status": "indeterminate",
            "oracle_status": oracle_status,
            "stage_signals": {
                "oracle_status": oracle_status,
                "knowledge": {
                    "knowledge_exists": "unknown",
                    "inferred_expected_docs": [],
                    "inferred_expected_ids": [],
                    "oracle_confidence": 0.0,
                    "expected_knowledge_points": expected_points,
                    "point_coverage": [],
                    "partial_knowledge_missing": False,
                    "missing_expected_points_from_theoretical_recall": [],
                },
                "retrieval": {
                    "inferred_expected_ids": [],
                    "oracle_origin_hit_ids": [],
                    "oracle_missing_from_origin_ids": [],
                    "partial_retrieval_miss": False,
                    "knowledge_gap_points": [],
                    "point_retrieval_gap_points": [],
                    "expected_knowledge_hit": None,
                    "online_retrieval_hit": None,
                },
                "rerank": {
                    "inferred_expected_ids": [],
                    "oracle_rerank_hit_ids": [],
                    "oracle_missing_from_rerank_ids": [],
                    "partial_rerank_drop": False,
                    "missing_expected_points_from_rerank": [],
                    "expected_doc_survived_rerank": None,
                },
                "context": {
                    "inferred_expected_ids": [],
                    "oracle_prompt_hit_ids": [],
                    "oracle_missing_from_prompt_ids": [],
                    "partial_context_miss": False,
                    "missing_expected_points_from_prompt": [],
                    "expected_doc_in_prompt": None,
                },
            },
            "evidence_bundle": _probe_evidence(probe_name, "knowledge", content, 0.35),
            "raw_artifacts": {
                "inferred_expected_docs": [],
                "expected_knowledge_points": expected_points,
                "point_coverage": [],
                "per_source_hits": {},
                "note": "未提供 expected_required/missing_expected 断言，self-oracle 不从 query 或 judgement 文本推断期望文档。",
            },
        }
    signals_attempted: list[str] = []
    signals_used: list[str] = []
    signals_failed: dict[str, str] = {}
    per_source_hits: dict[str, list[dict[str, Any]]] = {}
    aggregated: dict[str, dict[str, Any]] = {}

    for source in sources:
        text = _oracle_signal_text(request_dict, source)
        if len(text.strip()) < 10:
            signals_failed[source] = "signal text is empty or shorter than 10 characters"
            continue
        signals_attempted.append(source)
        if not docs:
            signals_failed[source] = "no trace docs available for oracle matching"
            continue
        threshold = 0.08 if source in {"judgement_back_recall", "claim_back_recall"} else 0.04
        scored = sorted(
            [
                {"doc": doc, "score": _oracle_score(text, doc)}
                for doc in docs
            ],
            key=lambda item: item["score"],
            reverse=True,
        )
        hits = [item for item in scored if item["score"] >= threshold][0:topk]
        if not hits:
            continue
        signals_used.append(source)
        source_hits: list[dict[str, Any]] = []
        for item in hits:
            doc = item["doc"]
            doc_id = _doc_id(doc)
            if not doc_id:
                continue
            confidence = _oracle_source_confidence(source, float(item["score"]))
            basis = {
                "oracle_source": source,
                "score": item["score"],
                "signal_excerpt": text[:300],
                "doc_title": _doc_title(doc),
            }
            existing = aggregated.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "title": _doc_title(doc),
                    "confidence_parts": [],
                    "oracle_sources": [],
                    "inference_basis": [],
                },
            )
            existing["confidence_parts"].append(confidence)
            if source not in existing["oracle_sources"]:
                existing["oracle_sources"].append(source)
            existing["inference_basis"].append(basis)
            source_hits.append({"doc_id": doc_id, "title": _doc_title(doc), "score": item["score"], "confidence": confidence})
        per_source_hits[source] = source_hits

    inferred_docs: list[dict[str, Any]] = []
    for item in aggregated.values():
        confidence_parts = [float(value) for value in item.pop("confidence_parts", [])]
        item["confidence"] = _combine_confidences(confidence_parts)
        inferred_docs.append(item)
    inferred_docs.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
    inferred_ids = [str(item["doc_id"]) for item in inferred_docs]
    conflict_detected, jaccard = _oracle_conflict(inferred_docs)
    if conflict_detected:
        for item in inferred_docs:
            item["confidence"] = round(min(0.5, float(item.get("confidence") or 0.0)), 4)
    confidence = round(sum(float(item.get("confidence") or 0.0) for item in inferred_docs) / len(inferred_docs), 4) if inferred_docs else 0.0
    no_docs_found = bool(signals_attempted) and bool(docs) and not inferred_docs and not signals_used
    if no_docs_found:
        confidence = 0.85
    if provided_ids and inferred_ids:
        source = "mixed"
    elif provided_ids:
        source = "provided"
    elif inferred_docs or no_docs_found:
        source = "self_inferred"
    else:
        source = "insufficient"
    provided_set = set(provided_ids)
    inferred_set = set(inferred_ids)
    provided_conflict = bool(provided_set and inferred_set and not (provided_set & inferred_set))
    compare = _oracle_compare_to_flow(request_dict, inferred_ids)
    point_coverage = _knowledge_point_coverage(request_dict, expected_points)
    missing_points_from_theoretical = [
        item for item in point_coverage if item.get("missing_stage") == "knowledge"
    ]
    missing_points_from_origin = [
        item for item in point_coverage if item.get("missing_stage") == "retrieval"
    ]
    missing_points_from_rerank = [
        item for item in point_coverage if item.get("missing_stage") == "rerank"
    ]
    missing_points_from_prompt = [
        item for item in point_coverage if item.get("missing_stage") == "context"
    ]
    oracle_status = {
        "source": source,
        "signals_used": signals_used,
        "signals_attempted": list(sources),
        "signals_failed": signals_failed,
        "inferred_doc_count": len(inferred_docs),
        "inferred_doc_ids": inferred_ids,
        "provided_doc_ids": provided_ids,
        "confidence": confidence,
        "conflict_detected": conflict_detected,
        "jaccard": jaccard,
        "provided_conflict_detected": provided_conflict,
        "no_docs_found": no_docs_found,
        "expected_knowledge_points": expected_points,
        "point_coverage": point_coverage,
        "missing_expected_points_from_theoretical_recall": [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "required_terms": item.get("required_terms", [])}
            for item in missing_points_from_theoretical
        ],
        "missing_expected_points_from_origin": [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "upper_bound_docs": item.get("upper_bound_docs", [])}
            for item in missing_points_from_origin
        ],
        "missing_expected_points_from_rerank": [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "origin_docs": item.get("origin_docs", [])}
            for item in missing_points_from_rerank
        ],
        "missing_expected_points_from_prompt": [
            {"point_id": item.get("point_id"), "text": item.get("text"), "role": item.get("role"), "source": item.get("source"), "rerank_docs": item.get("rerank_docs", [])}
            for item in missing_points_from_prompt
        ],
    }
    if source == "insufficient":
        status = "indeterminate"
    elif no_docs_found:
        status = "empty"
    else:
        status = "ok"

    evidence_bundle: list[dict[str, Any]] = []
    for index, (source_name, hits) in enumerate(per_source_hits.items(), start=1):
        evidence_bundle.append(
            {
                "evidence_id": f"probe-self-oracle:ev_{index:03d}",
                "evidence_type": "inferred_oracle",
                "source_stage": "knowledge",
                "source": {"probe_name": probe_name, "oracle_signal": source_name},
                "content": {
                    "normalized_summary": f"{source_name} inferred {len(hits)} expected docs from trace candidates",
                    "inferred_doc_ids": [item["doc_id"] for item in hits],
                    "hits": hits[:10],
                    "expected_knowledge_points": expected_points[:10],
                    "point_coverage": point_coverage[:10],
                },
                "relation_to_query": {
                    "semantic_relevance": "high" if hits else "unknown",
                    "topic_match": bool(hits) or None,
                    "constraint_match": None,
                    "coverage": "partial" if hits else "unknown",
                },
                "quality": {
                    "freshness": "valid",
                    "permission_available": True,
                    "confidence": max([float(item.get("confidence") or 0.0) for item in hits], default=0.0),
                    "oracle_source": source_name,
                },
            }
        )
    if not evidence_bundle:
        evidence_bundle = _probe_evidence(
            probe_name,
            "knowledge",
            {
                "oracle_status": oracle_status,
                "inferred_expected_docs": inferred_docs,
                "expected_knowledge_points": expected_points,
                "point_coverage": point_coverage,
            },
            confidence if confidence else 0.35,
        )

    return {
        "status": status,
        "oracle_status": oracle_status,
        "stage_signals": {
            "oracle_status": oracle_status,
            "knowledge": {
                "knowledge_exists": "no" if no_docs_found else "yes" if inferred_docs or provided_ids else "unknown",
                "inferred_expected_docs": inferred_docs,
                "inferred_expected_ids": inferred_ids,
                "oracle_confidence": confidence,
                "expected_knowledge_points": expected_points,
                "point_coverage": point_coverage,
                "partial_knowledge_missing": bool(missing_points_from_theoretical),
                "missing_expected_points_from_theoretical_recall": [
                    item.get("text") for item in missing_points_from_theoretical if item.get("text")
                ],
            },
            "retrieval": {
                "inferred_expected_ids": inferred_ids,
                "oracle_origin_hit_ids": compare["origin_hit_ids"],
                "oracle_missing_from_origin_ids": compare["missing_from_origin_ids"],
                "partial_retrieval_miss": compare["partial_retrieval_miss"],
                "knowledge_gap_points": [
                    item.get("text") for item in missing_points_from_theoretical if item.get("text")
                ],
                "point_retrieval_gap_points": [
                    item.get("text") for item in missing_points_from_origin if item.get("text")
                ],
                "expected_knowledge_hit": (bool(compare["origin_hit_ids"]) if inferred_ids else None),
                "online_retrieval_hit": (bool(compare["origin_hit_ids"]) if inferred_ids else None),
            },
            "rerank": {
                "inferred_expected_ids": inferred_ids,
                "oracle_rerank_hit_ids": compare["rerank_hit_ids"],
                "oracle_missing_from_rerank_ids": compare["missing_from_rerank_ids"],
                "partial_rerank_drop": compare["partial_rerank_drop"],
                "missing_expected_points_from_rerank": [
                    item.get("text") for item in missing_points_from_rerank if item.get("text")
                ],
                "expected_doc_survived_rerank": (bool(compare["rerank_hit_ids"]) if inferred_ids and compare["origin_hit_ids"] else None),
            },
            "context": {
                "inferred_expected_ids": inferred_ids,
                "oracle_prompt_hit_ids": compare["prompt_hit_ids"],
                "oracle_missing_from_prompt_ids": compare["missing_from_prompt_ids"],
                "partial_context_miss": compare["partial_context_miss"],
                "missing_expected_points_from_prompt": [
                    item.get("text") for item in missing_points_from_prompt if item.get("text")
                ],
                "expected_doc_in_prompt": (bool(compare["prompt_hit_ids"]) if inferred_ids and (compare["origin_hit_ids"] or compare["rerank_hit_ids"]) else None),
            },
        },
        "evidence_bundle": evidence_bundle,
        "raw_artifacts": {
            "inferred_expected_docs": inferred_docs,
            "expected_knowledge_points": expected_points,
            "point_coverage": point_coverage,
            "per_source_hits": per_source_hits,
            "note": "P0 self-oracle uses trace-local candidate docs; it can be backed by a live KB recall service later.",
        },
    }


def _probe_knowledge_detail(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    ids = _expected_ids(request_dict, params)
    known_ids = _doc_ids(_all_docs(request_dict))
    retrieval = request_dict.get("retrieval") or {}
    if retrieval.get("knowledge_exists") is True or any(doc_id in known_ids for doc_id in ids):
        state = "yes"
        status = "ok"
        confidence = 0.86
    elif retrieval.get("knowledge_exists") is False:
        state = "no"
        status = "ok"
        confidence = 0.82
    else:
        state = "unknown"
        status = "indeterminate"
        confidence = 0.45
    content = {"doc_ids": ids, "known_doc_ids": sorted(known_ids), "knowledge_exists": state, "retry_count": 1}
    return {
        "status": status,
        "stage_signals": {"knowledge": content},
        "evidence_bundle": _probe_evidence(probe_name, "knowledge", content, confidence),
        "raw_artifacts": {"note": "knowledge detail uses trace/provided ids unless live KB detail is configured by host"},
    }


def _probe_permission_check(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    retrieval = request_dict.get("retrieval") or {}
    doc_ids = _expected_ids(request_dict, params)
    permission_miss = bool(retrieval.get("permission_miss"))
    content = {"doc_ids": doc_ids, "permission_miss": permission_miss, "permission_available": False if permission_miss else None}
    return {
        "status": "fail" if permission_miss else "indeterminate",
        "stage_signals": {"retrieval": content},
        "evidence_bundle": _probe_evidence(probe_name, "retrieval", content, 0.82 if permission_miss else 0.45),
        "raw_artifacts": {},
    }


def _probe_wide_recall(probe_name: str, request_dict: dict[str, Any], ingest: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    expected = set(_expected_ids(request_dict, params))
    online_ids = _doc_ids(_docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list"))
    topk = max(int(params.get("topk") or os.getenv("WIDE_RECALL_TOPK") or 50), 50)
    query_variants = _upper_bound_query_variants(request_dict)
    wide_docs: list[dict[str, Any]] = []
    wide_faqs: list[dict[str, Any]] = []
    theoretical_status = "not_configured"
    theoretical_error = ""
    theoretical_notes = ""
    theoretical_request_payload: dict[str, Any] = {}
    theoretical_response_payload: Any = None
    upper_bound_scope = "open_label"
    auth_token_source = ""
    theoretical_counts: dict[str, Any] = {}
    source_template: dict[str, Any] = {}
    try:
        recall_result = _run_sirius_open_label_wide_recall(ingest=ingest, request_dict=request_dict, topk=topk)
        wide_docs = [item for item in recall_result.get("wide_recall_docs", []) if isinstance(item, dict)]
        wide_faqs = [item for item in recall_result.get("wide_recall_faqs", []) if isinstance(item, dict)]
        theoretical_status = str(recall_result.get("status") or "indeterminate")
        theoretical_error = str(recall_result.get("error") or "")
        theoretical_notes = str(recall_result.get("notes") or "")
        theoretical_request_payload = recall_result.get("request_payload") if isinstance(recall_result.get("request_payload"), dict) else {}
        theoretical_response_payload = recall_result.get("response_payload")
        query_variants = _string_list(recall_result.get("query_variants")) or query_variants
        upper_bound_scope = str(recall_result.get("upper_bound_scope") or upper_bound_scope)
        auth_token_source = str(recall_result.get("auth_token_source") or "")
        theoretical_counts = recall_result.get("counts") if isinstance(recall_result.get("counts"), dict) else {}
        source_template = recall_result.get("source_template") if isinstance(recall_result.get("source_template"), dict) else {}
    except Exception as exc:
        theoretical_status = "error"
        theoretical_error = str(exc)[:500]
        theoretical_notes = "Sirius open-label 理论召回上界调用失败。"
    wide_ids = _doc_ids(wide_docs + wide_faqs)
    matched = sorted(expected & (wide_ids or online_ids))
    gap = bool(expected and matched and not (expected & online_ids))
    points = _expected_knowledge_points(request_dict)
    coverage_request = json.loads(json.dumps(request_dict, ensure_ascii=False, default=_json_default))
    coverage_request.setdefault("retrieval", {})["wide_recall_docs"] = wide_docs
    coverage_request.setdefault("retrieval", {})["wide_recall_faqs"] = wide_faqs
    coverage_request["retrieval"]["theoretical_recall_status"] = theoretical_status
    point_coverage = _knowledge_point_coverage(coverage_request, points)
    content = {
        "expected_doc_ids": sorted(expected),
        "online_doc_ids": sorted(online_ids),
        "wide_recall_doc_ids": sorted(wide_ids),
        "wide_recall_docs": wide_docs,
        "wide_recall_faqs": wide_faqs,
        "matched_expected_ids": matched,
        "retrieval_gap_detected": gap,
        "online_retrieval_hit": bool(expected & online_ids) if expected else None,
        "theoretical_recall_status": theoretical_status,
        "theoretical_recall_topk": topk,
        "theoretical_query_variants": query_variants,
        "upper_bound_scope": upper_bound_scope,
        "theoretical_recall_counts": theoretical_counts,
        "theoretical_error": theoretical_error,
        "theoretical_notes": theoretical_notes,
        "auth_token_source": auth_token_source,
        "expected_knowledge_points": points,
        "point_coverage": point_coverage,
        "point_retrieval_gap_points": [
            item.get("text") for item in point_coverage if item.get("missing_stage") == "retrieval" and item.get("text")
        ],
    }
    return {
        "status": "ok" if theoretical_status == "ok" else theoretical_status if theoretical_status else "indeterminate",
        "stage_signals": {"retrieval": content},
        "evidence_bundle": _probe_evidence(probe_name, "retrieval", content, 0.78),
        "raw_artifacts": {
            "request_payload": theoretical_request_payload,
            "response_payload": theoretical_response_payload,
            "wide_recall_docs": wide_docs,
            "wide_recall_faqs": wide_faqs,
            "upper_bound_scope": upper_bound_scope,
            "source_template": source_template,
            "auth_token_source": auth_token_source,
        },
    }


def _probe_rerank_bypass(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    expected = set(_expected_ids(request_dict, params))
    recall_ids = _doc_ids(_docs_from_request(request_dict, "retrieval", "origin_doc_list") + _docs_from_request(request_dict, "retrieval", "origin_faq_list"))
    rerank_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "rerank_docs"))
    bypass_would_restore = bool(expected and expected & recall_ids and not expected & rerank_ids)
    content = {
        "expected_doc_ids": sorted(expected),
        "recall_doc_ids": sorted(recall_ids),
        "rerank_doc_ids": sorted(rerank_ids),
        "bypass_would_restore": bypass_would_restore,
        "expected_doc_survived_rerank": False if bypass_would_restore else None,
    }
    return {
        "status": "fail" if bypass_would_restore else "not_applicable",
        "stage_signals": {"rerank": content},
        "evidence_bundle": _probe_evidence(probe_name, "rerank", content, 0.84 if bypass_would_restore else 0.5),
        "raw_artifacts": {},
    }


def _probe_rerank_tune(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    rerank = request_dict.get("rerank") or {}
    experiment = rerank.get("parameter_experiment") if isinstance(rerank.get("parameter_experiment"), dict) else {}
    tunable = bool(params.get("rerank_tunable") or experiment.get("parameter_issue_supported"))
    content = {
        "rerank_tunable": tunable,
        "tunable_param": "threshold" if tunable else None,
        "parameter_experiment_status": experiment.get("status") or ("host_supplied" if params.get("rerank_tunable") else "not_run"),
    }
    return {
        "status": "fail" if tunable else "not_applicable",
        "stage_signals": {"rerank": content},
        "evidence_bundle": _probe_evidence(probe_name, "rerank", content, 0.78 if tunable else 0.5),
        "raw_artifacts": {},
    }


def _probe_context_assembly(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    expected = set(_expected_ids(request_dict, params))
    rerank_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "rerank_docs"))
    prompt_ids = _doc_ids(_docs_from_request(request_dict, "rerank", "prompt_docs"))
    missing_from_prompt = bool(expected and expected & rerank_ids and not expected & prompt_ids)
    content = {
        "expected_doc_ids": sorted(expected),
        "rerank_doc_ids": sorted(rerank_ids),
        "prompt_doc_ids": sorted(prompt_ids),
        "expected_doc_in_prompt": False if missing_from_prompt else (True if expected & prompt_ids else None),
        "context_assembly_error": missing_from_prompt,
    }
    return {
        "status": "fail" if missing_from_prompt else "ok" if prompt_ids else "indeterminate",
        "stage_signals": {"context": content},
        "evidence_bundle": _probe_evidence(probe_name, "context", content, 0.84 if missing_from_prompt else 0.65),
        "raw_artifacts": {},
    }


def _probe_by_judgement(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    judgement = read_text_arg(params.get("judgement")) or (request_dict.get("case_input") or {}).get("judgement") or ""
    signals = (request_dict.get("judgement_evidence") or {}).get("signals") if isinstance(request_dict.get("judgement_evidence"), dict) else []
    content = {"judgement_present": bool(judgement), "signal_count": len(signals or []), "judgement": judgement[:500]}
    return {
        "status": "ok" if judgement or signals else "empty",
        "stage_signals": {"retrieval": {"judgement_probe_available": bool(judgement or signals)}},
        "evidence_bundle": _probe_evidence(probe_name, "retrieval", content, 0.62),
        "raw_artifacts": {},
    }


def _probe_by_claim(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    qa = request_dict.get("qa") or {}
    claims = _string_list(params.get("claims")) or _claim_texts(request_dict)
    unsupported_claims = [
        str(point.get("text") or "")
        for point in _expected_knowledge_points(request_dict)
        if str(point.get("role") or "") == "unsupported_claim" and point.get("text")
    ]
    content = {
        "claims": claims,
        "unsupported_claims": unsupported_claims,
        "prompt_supports_answer": qa.get("prompt_supports_answer"),
        "answer_satisfies_expected": qa.get("answer_satisfies_expected"),
        "wrong_citation": qa.get("wrong_citation"),
        "partial_answer": qa.get("partial_answer"),
    }
    return {
        "status": "ok" if claims else "empty",
        "stage_signals": {"answer": content},
        "evidence_bundle": _probe_evidence(probe_name, "answer", content, 0.72 if claims else 0.4),
        "raw_artifacts": {},
    }


def _probe_by_doc_title(probe_name: str, request_dict: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    titles = _string_list(params.get("titles"))
    docs = _all_docs(request_dict)
    matches = []
    for doc in docs:
        title = str(doc.get("title") or "")
        doc_id = str(doc.get("id") or "")
        if (titles and any(item in title for item in titles)) or (not titles and doc_id in _expected_ids(request_dict, params)):
            matches.append({"id": doc_id, "title": title, "source": doc.get("source", "")})
    content = {"titles": titles, "matches": matches, "online_retrieval_hit": bool(matches) if titles else None}
    return {
        "status": "ok" if matches else "empty",
        "stage_signals": {"retrieval": content},
        "evidence_bundle": _probe_evidence(probe_name, "retrieval", content, 0.76 if matches else 0.4),
        "raw_artifacts": {},
    }


def fetch_workflow_nodes_v3(*, workspace_id: str, app_id: str, output_dir: str | None = None) -> dict[str, Any]:
    request = AttributionRequest.model_validate(
        {
            "case_input": {
                "query": "fetch workflow nodes",
                "workspace_id": str(workspace_id),
                "app_id": str(app_id),
            }
        }
    )
    try:
        resolved = resolve_workflow(request)
        status = "ok"
        error = None
    except Exception as exc:
        resolved = {}
        status = "error"
        error = str(exc)[:500]
    workflow_config = resolved.get("workflow_config") if isinstance(resolved.get("workflow_config"), dict) else {}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "command": "fetch-workflow-nodes",
        "workspace_id": str(workspace_id),
        "app_id": str(app_id),
        "status": status,
        "error": error,
        "workflow": {
            "source": resolved.get("source"),
            "database": resolved.get("database"),
            "wip_id": resolved.get("wip_id"),
            "version_id": resolved.get("version_id"),
            "status": resolved.get("status"),
            "nodes": workflow_config.get("nodes", []),
            "edges": workflow_config.get("edges", []),
            "global_config": workflow_config.get("global_config", {}),
            "input_schema": resolved.get("input_schema", []),
        },
    }
    if output_dir:
        write_json(Path(output_dir) / "workflow_nodes.json", payload)
    return payload


async def replay_workflow_v3(*, ingest: dict[str, Any], overrides: dict[str, Any] | None = None, output_dir: str | None = None) -> dict[str, Any]:
    request_dict = _request_from_ingest(ingest)
    if overrides:
        request_dict.setdefault("workflow_overrides", {}).update(overrides)
    try:
        request = AttributionRequest.model_validate(request_dict)
        replayed = await replay_workflow(request)
        status = replayed.workflow_replay.status
        raw = replayed.workflow_replay.model_dump(mode="json")
        error = replayed.workflow_replay.error
    except Exception as exc:
        status = "error"
        raw = {}
        error = str(exc)[:500]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "log_id": ingest.get("log_id"),
        "workspace_id": ingest.get("workspace_id"),
        "probe_name": "replay-workflow",
        "status": status,
        "stage_signals": {"context": {"replay_status": status, "replay_diverged": status not in {"ok", "not_configured"}}},
        "evidence_bundle": _probe_evidence("replay-workflow", "context", raw or {"error": error}, 0.55 if error else 0.74),
        "raw_artifacts": raw,
        "telemetry": {"exclusive": True, "error": error},
    }
    if output_dir:
        write_json(Path(output_dir) / "replay-workflow.json", payload)
    return payload


def load_probe_dir(probe_dir: str | None) -> list[dict[str, Any]]:
    if not probe_dir:
        return []
    root = Path(probe_dir)
    if not root.exists():
        return []
    probes: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".stdout.json"):
            continue
        data = read_json(path)
        if isinstance(data, dict) and data.get("probe_name"):
            probes.append(data)
    return probes
