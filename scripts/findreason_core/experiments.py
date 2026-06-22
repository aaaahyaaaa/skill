from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import httpx

from .artifacts import decode_jsonish, normalize_docs, normalize_rag_artifacts, to_jsonable
from .evidence_kernel import SCHEMA_VERSION, json_dumps, read_json_file, write_json
from .models import AttributionRequest, CaseInput
from .workflow_replay import replay_workflow, resolve_workflow_auth_token


def _case_from_facts(facts: dict[str, Any]) -> dict[str, Any]:
    case = facts.get("case") if isinstance(facts.get("case"), dict) else {}
    return {
        "query": case.get("query") or case.get("query_hint") or "unknown query",
        "workspace_id": facts.get("workspace_id") or case.get("workspace_id") or "",
        "app_id": facts.get("app_id") or case.get("app_id") or "",
        "version_id": (
            facts.get("version_id")
            or facts.get("versionId")
            or facts.get("app_version")
            or facts.get("appVersion")
            or case.get("version_id")
            or case.get("versionId")
            or case.get("app_version")
            or case.get("appVersion")
            or ""
        ),
    }


def _base_envelope(experiment_type: str, facts: dict[str, Any]) -> dict[str, Any]:
    case = _case_from_facts(facts)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "experiment_result",
        "experiment_type": experiment_type,
        "log_id": facts.get("log_id", ""),
        "workspace_id": facts.get("workspace_id", ""),
        "app_id": facts.get("app_id", ""),
        "version_id": case.get("version_id", ""),
        "code_role": "run experiment and return observations; do not select root cause",
    }


def plan_recall_experiment(facts: dict[str, Any]) -> dict[str, Any]:
    envelope = _base_envelope("recall", facts)
    case = _case_from_facts(facts)
    experiment_inputs = facts.get("experiment_inputs") if isinstance(facts.get("experiment_inputs"), dict) else {}
    recall_templates = experiment_inputs.get("recall_templates") if isinstance(experiment_inputs.get("recall_templates"), list) else []
    envelope.update(
        {
            "status": "planned",
            "available_recall_templates": len(recall_templates),
            "query_variants_to_try": [
                {"variant": "workflow_query", "query": case["query"]},
                {"variant": "agent_original_or_context_query", "query": "<agent-fill-from-original-user-context>"},
                {"variant": "decomposed_sub_intent", "query": "<agent-fill-for-missing-aspect>"},
            ],
            "checks": [
                "Does open-label recall retrieve exact-topic support?",
                "Does recall miss support that exists in wider retrieval?",
                "Are origin_doc_list and origin_faq_list differently useful for the same assertion?",
            ],
            "notes": "This thin v4 planner intentionally leaves semantic query variants to the Agent; code will execute concrete variants once supplied.",
        }
    )
    return envelope


def _doc_id(value: Any) -> str:
    return str(value or "").strip()


def _artifact_docs(facts: dict[str, Any], key: str) -> list[dict[str, Any]]:
    artifacts = facts.get("artifacts") if isinstance(facts.get("artifacts"), dict) else {}
    return normalize_docs(artifacts.get(key, []), source=key)


def _index_docs(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for doc in docs:
        doc_id = _doc_id(doc.get("id"))
        if doc_id and doc_id not in indexed:
            indexed[doc_id] = doc
    return indexed


def _doc_summary(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not doc:
        return {}
    return {
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "content_preview": str(doc.get("content") or "")[:300],
    }


def run_rerank_experiment(facts: dict[str, Any], *, target_doc_ids: list[str] | None = None) -> dict[str, Any]:
    envelope = _base_envelope("rerank", facts)
    origin_docs = _artifact_docs(facts, "origin_doc_list")
    origin_faqs = _artifact_docs(facts, "origin_faq_list")
    rerank_docs = _artifact_docs(facts, "rerank_docs")
    prompt_docs = _artifact_docs(facts, "prompt_docs")
    recall_docs = origin_docs + origin_faqs
    origin_doc_index = _index_docs(origin_docs)
    origin_faq_index = _index_docs(origin_faqs)
    rerank_index = _index_docs(rerank_docs)
    prompt_index = _index_docs(prompt_docs)
    target_ids = [_doc_id(item) for item in (target_doc_ids or []) if _doc_id(item)]
    if not target_ids:
        target_ids = [_doc_id(doc.get("id")) for doc in recall_docs if _doc_id(doc.get("id"))]
    if not target_ids:
        envelope.update(
            {
                "status": "no_artifacts",
                "counts": {
                    "origin_doc_list": len(origin_docs),
                    "origin_faq_list": len(origin_faqs),
                    "recall": len(recall_docs),
                    "rerank_docs": len(rerank_docs),
                    "prompt_docs": len(prompt_docs),
                },
                "notes": "No doc ids were available for recall-to-rerank survival observation.",
            }
        )
        return envelope

    survival: list[dict[str, Any]] = []
    missing_from_rerank: list[str] = []
    missing_from_prompt: list[str] = []
    for doc_id in target_ids:
        origin_doc = origin_doc_index.get(doc_id)
        origin_faq = origin_faq_index.get(doc_id)
        rerank_doc = rerank_index.get(doc_id)
        prompt_doc = prompt_index.get(doc_id)
        in_recall = bool(origin_doc or origin_faq)
        in_rerank = bool(rerank_doc)
        in_prompt = bool(prompt_doc)
        if in_recall and not in_rerank:
            missing_from_rerank.append(doc_id)
        if in_rerank and not in_prompt:
            missing_from_prompt.append(doc_id)
        representative = origin_doc or origin_faq or rerank_doc or prompt_doc or {}
        survival.append(
            {
                "doc_id": doc_id,
                "title": representative.get("title", ""),
                "recall_sources": [
                    source
                    for source, present in (
                        ("origin_doc_list", bool(origin_doc)),
                        ("origin_faq_list", bool(origin_faq)),
                    )
                    if present
                ],
                "in_recall": in_recall,
                "in_rerank": in_rerank,
                "in_prompt": in_prompt,
                "origin_doc": _doc_summary(origin_doc),
                "origin_faq": _doc_summary(origin_faq),
                "rerank_doc": _doc_summary(rerank_doc),
                "prompt_doc": _doc_summary(prompt_doc),
            }
        )
    envelope.update(
        {
            "status": "observed",
            "counts": {
                "origin_doc_list": len(origin_docs),
                "origin_faq_list": len(origin_faqs),
                "recall": len(recall_docs),
                "rerank_docs": len(rerank_docs),
                "prompt_docs": len(prompt_docs),
            },
            "target_doc_ids": target_ids,
            "survival": survival,
            "missing_from_rerank": missing_from_rerank,
            "missing_from_prompt": missing_from_prompt,
            "checks": [
                "Which support docs appear in recall but disappear from rerank_docs?",
                "Are exact scenario docs ranked below generic high-frequency docs?",
                "Would a bypass/topK/diversity variant recover the missing support?",
            ],
            "notes": "This is doc-id survival observation only. Agent must still inspect whether the same required assertion is supported before judging a root cause.",
        }
    )
    return envelope


def _recall_templates(facts: dict[str, Any]) -> list[dict[str, Any]]:
    experiment_inputs = facts.get("experiment_inputs") if isinstance(facts.get("experiment_inputs"), dict) else {}
    templates = experiment_inputs.get("recall_templates")
    return [item for item in templates if isinstance(item, dict)] if isinstance(templates, list) else []


def _build_recall_request_body(template: dict[str, Any], *, query: str, workspace_id: str) -> dict[str, Any]:
    body = copy.deepcopy(template.get("request_body") if isinstance(template.get("request_body"), dict) else {})
    body["oriQuery"] = query
    if isinstance(body.get("query"), list):
        body["query"] = [query]
    elif "query" in body:
        body["query"] = query
    params = body.get("params")
    if not isinstance(params, dict):
        params = {}
    params["workspaceId"] = workspace_id
    body["params"] = params
    return to_jsonable(body)


def _collect_docs_from_response(value: Any) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        decoded = decode_jsonish(node)
        if isinstance(decoded, list):
            normalized = normalize_docs(decoded, source="recall_experiment")
            if normalized:
                docs.extend(normalized)
            for child in decoded:
                if isinstance(child, (dict, list, str)):
                    visit(child)
        elif isinstance(decoded, dict):
            for child in decoded.values():
                if isinstance(child, (dict, list, str)):
                    visit(child)

    visit(value)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for doc in docs:
        key = (_doc_id(doc.get("id")), str(doc.get("title") or ""), str(doc.get("content") or "")[:200])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(doc)
    return deduped


def _redacted_headers(headers: dict[str, Any]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if str(key).lower() == "authorization" and value:
            redacted[str(key)] = "Bearer <redacted>"
        else:
            redacted[str(key)] = str(value)
    return redacted


def run_recall_experiment(
    facts: dict[str, Any], *, query: str | None = None, timeout_seconds: int = 90
) -> dict[str, Any]:
    if not query:
        return plan_recall_experiment(facts)
    envelope = _base_envelope("recall", facts)
    templates = _recall_templates(facts)
    if not templates:
        planned = plan_recall_experiment(facts)
        planned.update(
            {
                "status": "not_configured",
                "reason": "No recall/searchDoc request template was found in case_facts.experiment_inputs.recall_templates.",
                "query": query,
            }
        )
        return planned
    template = templates[0]
    endpoint = str(template.get("endpoint") or "")
    if not endpoint:
        envelope.update({"status": "not_configured", "reason": "Recall template has no endpoint.", "query": query})
        return envelope
    request_body = _build_recall_request_body(template, query=query, workspace_id=str(facts.get("workspace_id") or ""))
    try:
        token, token_source = asyncio.run(resolve_workflow_auth_token(str(facts.get("workspace_id") or "")))
    except Exception as exc:
        envelope.update(
            {
                "status": "auth_error",
                "query": query,
                "endpoint": endpoint,
                "request_payload": request_body,
                "error": str(exc)[:1000],
                "notes": "Recall experiment needs a workspace API key resolved from workspace info before it can call the recall endpoint.",
            }
        )
        return envelope
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "workspaceId": str(facts.get("workspace_id") or ""),
    }
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    try:
        with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
            response = client.post(endpoint, headers=headers, json=request_body)
    except Exception as exc:
        envelope.update(
            {
                "status": "error",
                "query": query,
                "endpoint": endpoint,
                "request_payload": request_body,
                "headers": _redacted_headers(headers),
                "auth_token_source": token_source,
                "error": str(exc)[:1000],
            }
        )
        return envelope
    try:
        response_payload = response.json()
    except Exception:
        response_payload = {"raw_text": response.text[:2000]}
    docs = _collect_docs_from_response(response_payload)
    envelope.update(
        {
            "status": "ok" if response.status_code < 400 else "error",
            "query": query,
            "mode": "trace_template_query_override",
            "template_kind": template.get("kind", ""),
            "endpoint": endpoint,
            "request_payload": request_body,
            "headers": _redacted_headers(headers),
            "auth_token_source": token_source,
            "http_status_code": response.status_code,
            "response_code": response_payload.get("code") if isinstance(response_payload, dict) else None,
            "response_message": str(response_payload.get("msg") or response_payload.get("message") or "")[:1000]
            if isinstance(response_payload, dict)
            else "",
            "counts": {"recall_docs": len(docs)},
            "artifacts": {"recall_docs": docs},
            "notes": "Recall experiment reuses a trace request shape with query override. It returns observations only, not a root-cause verdict.",
        }
    )
    return envelope


def run_replay_experiment(
    facts: dict[str, Any],
    *,
    query: str | None = None,
    app_id: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    envelope = _base_envelope("replay", facts)
    trace = facts.get("trace") if isinstance(facts.get("trace"), dict) else {}
    if trace.get("has_middle_node_trace"):
        envelope.update(
            {
                "status": "ok",
                "mode": "skipped_authoritative_trace",
                "counts": facts.get("counts") if isinstance(facts.get("counts"), dict) else {},
                "artifacts": {},
                "answer": "",
                "reasoning": "",
                "trace_completeness": {"replay_skipped": True, "historical_middle_node_trace": True},
                "node_traces": [],
                "notes": "检测到原始 trace 中间节点证据，未执行 workflow replay；归因以历史现场证据为权威，replay 文件仅记录跳过状态。",
            }
        )
        return envelope
    case = _case_from_facts(facts)
    replay_query = query or case["query"]
    replay_app_id = app_id or case["app_id"]
    replay_version_id = version_id or case.get("version_id") or None
    if not replay_query or replay_query == "unknown query" or not replay_app_id:
        envelope.update(
            {
                "status": "blocked",
                "reason": "replay requires concrete query and app_id",
                "required_inputs": ["query", "app_id"],
            }
        )
        return envelope
    request = AttributionRequest(
        case_input=CaseInput(
            query=replay_query,
            workspace_id=str(case["workspace_id"]),
            app_id=str(replay_app_id),
            version_id=str(replay_version_id) if replay_version_id else None,
        )
    )
    if replay_version_id:
        envelope["version_id"] = str(replay_version_id)
    enriched = asyncio.run(replay_workflow(request))
    replay = enriched.workflow_replay
    extracted = replay.extracted_evidence if isinstance(replay.extracted_evidence, dict) else {}
    normalized = normalize_rag_artifacts(
        origin_doc_list=extracted.get("origin_doc_list", []),
        origin_faq_list=extracted.get("origin_faq_list", []),
        rerank_docs=extracted.get("rerank_docs", []),
        prompt_docs=extracted.get("prompt_docs", []),
    )
    envelope.update(
        {
            "status": replay.status,
            "endpoint": replay.endpoint,
            "request_payload": replay.request_payload,
            "auth_token_source": replay.auth_token_source,
            "error": replay.error,
            "notes": replay.notes,
            "resolved_app": replay.resolved_app,
            "counts": normalized["counts"],
            "artifacts": normalized["artifacts"],
            "answer": extracted.get("answer", ""),
            "reasoning": extracted.get("reasoning", ""),
            "trace_completeness": extracted.get("trace_completeness", {}),
            "node_traces": extracted.get("node_traces", []),
        }
    )
    return envelope


def run_experiment(
    *,
    experiment_type: str,
    facts_file: str,
    output_dir: str | None = None,
    query: str | None = None,
    app_id: str | None = None,
    version_id: str | None = None,
    target_doc_ids: list[str] | None = None,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    facts = read_json_file(facts_file)
    if experiment_type == "recall":
        result = run_recall_experiment(facts, query=query, timeout_seconds=timeout_seconds)
    elif experiment_type == "rerank":
        result = run_rerank_experiment(facts, target_doc_ids=target_doc_ids)
    elif experiment_type == "replay":
        result = run_replay_experiment(facts, query=query, app_id=app_id, version_id=version_id)
    else:
        result = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "experiment_result",
            "status": "error",
            "error_code": "E_EXPERIMENT_TYPE",
            "message": f"Unsupported experiment_type={experiment_type}",
            "supported": ["recall", "rerank", "replay"],
        }
    if output_dir:
        target = Path(output_dir)
        write_json(target / f"{experiment_type}_experiment.json", result)
    return result


def print_experiment_result(value: dict[str, Any]) -> None:
    print(json_dumps(value))
