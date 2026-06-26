from __future__ import annotations

import asyncio
import copy
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .artifacts import decode_jsonish, normalize_docs, normalize_rag_artifacts, to_jsonable
from .evidence_kernel import SCHEMA_VERSION, json_dumps, read_json_file, write_json
from .models import AttributionRequest, CaseInput
from .workflow_replay import replay_workflow, resolve_workflow_auth_token


STATUS_SIGNAL_TERMS = (
    "停止更新",
    "历史版本",
    "临时",
    "下线",
    "废弃",
    "已升级",
    "过期",
    "活动时效",
    "不再维护",
    "停止维护",
    "旧版",
)
KNOWLEDGE_DETAIL_ENDPOINT_TEMPLATE = (
    os.getenv("FINDREASON_KNOWLEDGE_DETAIL_ENDPOINT")
    or "https://ad-sirius.bytedance.net/api/sirius_knowledge/v1/search/docDetail/{doc_id}"
)


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


def _doc_aliases(doc: dict[str, Any] | None) -> list[str]:
    if not isinstance(doc, dict):
        return []
    aliases: list[str] = []

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        text = str(value).strip()
        if text and text not in aliases:
            aliases.append(text)

    for key in ("id", "doc_id", "docId", "record_id", "recordId", "identifier", "knowledge_id", "knowledgeId"):
        add(doc.get(key))
    raw_aliases = doc.get("doc_id_aliases") or doc.get("docIdAliases") or doc.get("aliases")
    if isinstance(raw_aliases, list):
        for value in raw_aliases:
            add(value)
    source_text = str(doc.get("source") or "")
    for match in re.finditer(r"(?:identifier|id|doc_id|record_id|knowledge_id)=([^|,\s]+)", source_text):
        add(match.group(1))
    return aliases


def _doc_source_for_detail(doc: dict[str, Any] | None) -> str:
    if not isinstance(doc, dict):
        return ""
    for key in ("knowledge_source", "knowledgeSource", "doc_source", "docSource", "source_type", "sourceType"):
        value = str(doc.get(key) or "").strip()
        if value:
            return value
    source = str(doc.get("source") or "").strip()
    if source.isupper() and "|" not in source:
        return source
    for match in re.finditer(r"(?:knowledge_source|doc_source|source)=([^|,\s]+)", source):
        candidate = match.group(1)
        if candidate and candidate not in {"origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"}:
            return candidate
    return ""


def _artifact_docs(facts: dict[str, Any], key: str) -> list[dict[str, Any]]:
    artifacts = facts.get("artifacts") if isinstance(facts.get("artifacts"), dict) else {}
    return normalize_docs(artifacts.get(key, []), source=key)


def _index_docs(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for doc in docs:
        for doc_id in _doc_aliases(doc):
            if doc_id and doc_id not in indexed:
                indexed[doc_id] = doc
    return indexed


def _doc_summary(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not doc:
        return {}
    return {
        "id": doc.get("id", ""),
        "doc_id_aliases": _doc_aliases(doc),
        "title": doc.get("title", ""),
        "url": doc.get("url", ""),
        "content_preview": str(doc.get("content") or "")[:300],
        "rank": doc.get("rank"),
        "score": doc.get("score"),
    }


def _rank_value(doc: dict[str, Any] | None) -> int | float | None:
    if not doc:
        return None
    value = doc.get("rank")
    return value if isinstance(value, (int, float)) else None


def _score_value(doc: dict[str, Any] | None) -> float | None:
    if not doc:
        return None
    value = doc.get("score")
    return float(value) if isinstance(value, (int, float)) else None


def _numeric_delta(end: int | float | None, start: int | float | None) -> int | float | None:
    if end is None or start is None:
        return None
    return end - start


def _case_core_doc_specs(facts: dict[str, Any], target_doc_ids: list[str] | None = None) -> list[dict[str, str]]:
    case = facts.get("case") if isinstance(facts.get("case"), dict) else {}
    specs: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(doc_id: Any, *, supported_assertion: Any = "", title_hint: Any = "", source: str = "") -> None:
        text_id = _doc_id(doc_id)
        if not text_id or text_id in seen:
            return
        seen.add(text_id)
        specs.append(
            {
                "doc_id": text_id,
                "supported_assertion": str(supported_assertion or ""),
                "title_hint": str(title_hint or ""),
                "source": source,
            }
        )

    core_documents = case.get("core_documents")
    if isinstance(core_documents, list):
        for item in core_documents:
            if not isinstance(item, dict):
                continue
            add(
                item.get("doc_id")
                or item.get("id")
                or item.get("identifier")
                or item.get("knowledge_id")
                or item.get("knowledgeId"),
                supported_assertion=item.get("supported_assertion") or item.get("assertion"),
                title_hint=item.get("title_hint") or item.get("title"),
                source="case.core_documents",
            )
    expected_ids = case.get("expected_knowledge_ids")
    if isinstance(expected_ids, list):
        for item in expected_ids:
            add(item, source="case.expected_knowledge_ids")
    for item in target_doc_ids or []:
        add(item, source="cli.target_doc_id")
    return specs


def _doc_matches_spec(doc: dict[str, Any], spec: dict[str, str]) -> bool:
    doc_id = spec.get("doc_id") or ""
    if doc_id and doc_id in set(_doc_aliases(doc)):
        return True
    title_hint = (spec.get("title_hint") or "").strip()
    if title_hint:
        title = str(doc.get("title") or "")
        return bool(title and (title_hint in title or title in title_hint))
    return False


def _find_doc_for_spec(docs: list[dict[str, Any]], spec: dict[str, str]) -> dict[str, Any] | None:
    return next((doc for doc in docs if _doc_matches_spec(doc, spec)), None)


def _matched_core_docs(docs: list[dict[str, Any]], specs: list[dict[str, str]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for spec in specs:
        doc = _find_doc_for_spec(docs, spec)
        matches.append(
            {
                "doc_id": spec.get("doc_id", ""),
                "supported_assertion": spec.get("supported_assertion", ""),
                "title_hint": spec.get("title_hint", ""),
                "matched": bool(doc),
                "matched_id": doc.get("id", "") if doc else "",
                "matched_aliases": _doc_aliases(doc) if doc else [],
                "title": doc.get("title", "") if doc else "",
                "rank": doc.get("rank") if doc else None,
                "score": doc.get("score") if doc else None,
            }
        )
    return matches


def _context_boundary_observation(facts: dict[str, Any]) -> dict[str, Any]:
    trace = facts.get("trace") if isinstance(facts.get("trace"), dict) else {}
    prompt_observation = trace.get("prompt_observation") if isinstance(trace.get("prompt_observation"), dict) else {}
    locations = prompt_observation.get("locations") if isinstance(prompt_observation.get("locations"), list) else []
    nonempty = [item for item in locations if isinstance(item, dict) and int(item.get("count") or 0) > 0]
    if nonempty:
        first = nonempty[0]
        return {
            "status": prompt_observation.get("status") or "observed",
            "node_id": first.get("node_id") or "",
            "node_type": first.get("node_type") or "",
            "node_name": first.get("node_name") or "",
            "span_id": first.get("span_id") or "",
            "path": first.get("path") or "",
            "truncation_status": "prompt_boundary_observed_doc_missing_requires_span_inspection",
            "note": "Observed a prompt-doc boundary, but absence of a target doc there does not by itself prove Script/prompt truncation.",
        }
    node_map = trace.get("node_evidence_map") if isinstance(trace.get("node_evidence_map"), list) else []
    prompt_like_nodes: list[dict[str, Any]] = []
    for item in node_map:
        if not isinstance(item, dict):
            continue
        role = str(item.get("inferred_role") or "")
        node = item.get("node") if isinstance(item.get("node"), dict) else {}
        identity = " ".join(str(value or "") for value in (role, node.get("type"), node.get("name"), node.get("id"))).lower()
        if any(term in identity for term in ("qa", "问答", "model", "llm", "大模型", "script", "脚本")):
            prompt_like_nodes.append(
                {
                    "node_id": node.get("id") or item.get("node_id") or "",
                    "node_type": node.get("type") or "",
                    "node_name": node.get("name") or "",
                    "inferred_role": role,
                }
            )
    return {
        "status": prompt_observation.get("status") or "not_observed",
        "node_id": "",
        "node_type": "",
        "node_name": "",
        "span_id": "",
        "path": "",
        "candidate_prompt_nodes": prompt_like_nodes[:6],
        "truncation_status": "not_observed",
        "note": "No concrete prompt-doc boundary was observed; do not claim a specific script or prompt truncation without reading the relevant span input/output.",
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
    core_specs = _case_core_doc_specs(facts, target_doc_ids=target_doc_ids)
    if not core_specs:
        core_specs = [
            {"doc_id": _doc_id(doc.get("id")), "supported_assertion": "", "title_hint": doc.get("title", ""), "source": "recall_artifact"}
            for doc in recall_docs
            if _doc_id(doc.get("id"))
        ]
    target_ids = [item["doc_id"] for item in core_specs if item.get("doc_id")]
    if not core_specs:
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

    context_boundary = _context_boundary_observation(facts)
    survival: list[dict[str, Any]] = []
    missing_from_rerank: list[str] = []
    missing_from_prompt: list[str] = []
    rank_shift_observations: list[dict[str, Any]] = []
    for spec in core_specs:
        doc_id = spec["doc_id"]
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
        recall_doc = origin_doc or origin_faq
        recall_rank = _rank_value(recall_doc)
        rerank_rank = _rank_value(rerank_doc)
        prompt_rank = _rank_value(prompt_doc)
        recall_score = _score_value(recall_doc)
        rerank_score = _score_value(rerank_doc)
        prompt_score = _score_value(prompt_doc)
        if not in_recall:
            missing_reason = "missing_from_recall"
        elif not in_rerank:
            missing_reason = "missing_from_rerank"
        elif not in_prompt:
            missing_reason = "missing_from_prompt"
        else:
            missing_reason = "present_in_prompt"
        survival.append(
            {
                "doc_id": doc_id,
                "title": representative.get("title", ""),
                "doc_id_aliases": sorted(set(_doc_aliases(representative))),
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
        rank_shift_observations.append(
            {
                "core_doc": {
                    "doc_id": doc_id,
                    "supported_assertion": spec.get("supported_assertion", ""),
                    "title_hint": spec.get("title_hint", ""),
                    "source": spec.get("source", ""),
                },
                "title": representative.get("title", ""),
                "matched_aliases": sorted(set(_doc_aliases(representative))),
                "recall": {
                    "in_stage": in_recall,
                    "source": "origin_doc_list" if origin_doc else "origin_faq_list" if origin_faq else "",
                    "rank": recall_rank,
                    "score": recall_score,
                    "doc": _doc_summary(recall_doc),
                },
                "rerank": {
                    "in_stage": in_rerank,
                    "rank": rerank_rank,
                    "score": rerank_score,
                    "doc": _doc_summary(rerank_doc),
                },
                "prompt": {
                    "in_stage": in_prompt,
                    "rank": prompt_rank,
                    "score": prompt_score,
                    "doc": _doc_summary(prompt_doc),
                },
                "rank_delta": {
                    "recall_to_rerank": _numeric_delta(rerank_rank, recall_rank),
                    "rerank_to_prompt": _numeric_delta(prompt_rank, rerank_rank),
                    "recall_to_prompt": _numeric_delta(prompt_rank, recall_rank),
                },
                "score_delta": {
                    "recall_to_rerank": _numeric_delta(rerank_score, recall_score),
                    "rerank_to_prompt": _numeric_delta(prompt_score, rerank_score),
                    "recall_to_prompt": _numeric_delta(prompt_score, recall_score),
                },
                "in_prompt": in_prompt,
                "missing_reason": missing_reason,
                "context_boundary": context_boundary,
                "truncation_status": "not_observed" if in_prompt else context_boundary.get("truncation_status", "not_observed"),
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
            "rank_shift_observations": rank_shift_observations,
            "context_boundary": context_boundary,
            "missing_from_rerank": missing_from_rerank,
            "missing_from_prompt": missing_from_prompt,
            "checks": [
                "Which support docs appear in recall but disappear from rerank_docs?",
                "For each core doc, what assertion does it support and how did rank/score move?",
                "Did the doc enter the observed prompt boundary, and is that boundary actually known for this workflow?",
                "Are exact scenario docs ranked below generic high-frequency docs?",
                "Would a bypass/topK/diversity variant recover the missing support?",
            ],
            "notes": "This is rank-shift and doc survival observation only. Agent must still inspect whether the same required assertion is supported before judging a root cause.",
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


def _walk_mappings(value: Any, path: str = "$") -> list[tuple[dict[str, Any], str]]:
    found: list[tuple[dict[str, Any], str]] = []
    if isinstance(value, dict):
        found.append((value, path))
        for key, child in value.items():
            found.extend(_walk_mappings(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_mappings(child, f"{path}[{index}]"))
    return found


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recall_request_summary(body: dict[str, Any], *, endpoint: str = "") -> dict[str, Any]:
    top_fields: list[dict[str, Any]] = []
    label_fields: list[dict[str, Any]] = []
    threshold_fields: list[dict[str, Any]] = []
    for mapping, path in _walk_mappings(body):
        for key, value in mapping.items():
            lowered = str(key).lower()
            field = {"path": f"{path}.{key}", "value": value}
            if lowered in {"topk", "top_k", "maxcount", "max_count", "topn", "limit"}:
                top_fields.append(field)
            elif lowered in {"label", "labels", "labelids", "label_ids", "tagids", "tag_ids", "tags", "doclabels"}:
                label_fields.append(field)
            elif lowered in {
                "threshold",
                "scorethreshold",
                "score_threshold",
                "minscore",
                "min_score",
                "recallthreshold",
                "similaritythreshold",
            }:
                threshold_fields.append(field)
    return {
        "endpoint": endpoint,
        "top_level_keys": sorted(str(key) for key in body.keys())[:30],
        "oriQuery": body.get("oriQuery", ""),
        "query": body.get("query", ""),
        "top_fields": top_fields[:20],
        "label_fields": label_fields[:20],
        "threshold_fields": threshold_fields[:20],
    }


def _ensure_topk_at_least(body: dict[str, Any], minimum: int = 50) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    top_keys = {"topk", "top_k", "maxcount", "max_count", "topn", "limit"}
    for mapping, path in _walk_mappings(body):
        for key in list(mapping.keys()):
            if str(key).lower() not in top_keys:
                continue
            before = mapping.get(key)
            current = _int_or_none(before)
            if current is None or current < minimum:
                mapping[key] = minimum
                changes.append({"path": f"{path}.{key}", "before": before, "after": minimum})
        recall_requests = mapping.get("recallRequests")
        if isinstance(recall_requests, list):
            for index, item in enumerate(recall_requests):
                if not isinstance(item, dict):
                    continue
                before = item.get("maxCount")
                current = _int_or_none(before)
                if current is None or current < minimum:
                    item["maxCount"] = minimum
                    changes.append({"path": f"{path}.recallRequests[{index}].maxCount", "before": before, "after": minimum})
    return changes


def _relax_label_fields(body: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    label_keys = {"label", "labels", "labelids", "label_ids", "tagids", "tag_ids", "tags", "doclabels"}
    for mapping, path in _walk_mappings(body):
        for key in list(mapping.keys()):
            if str(key).lower() not in label_keys:
                continue
            before = mapping.get(key)
            if before in (None, "", [], {}):
                continue
            if isinstance(before, list):
                after: Any = []
            elif isinstance(before, dict):
                after = {}
            elif isinstance(before, str):
                after = ""
            else:
                after = None
            mapping[key] = after
            changes.append({"path": f"{path}.{key}", "before": before, "after": after})
    return changes


def _relax_threshold_fields(body: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    threshold_keys = {
        "threshold",
        "scorethreshold",
        "score_threshold",
        "minscore",
        "min_score",
        "recallthreshold",
        "similaritythreshold",
    }
    for mapping, path in _walk_mappings(body):
        for key in list(mapping.keys()):
            if str(key).lower() not in threshold_keys:
                continue
            before = mapping.get(key)
            if before in (None, ""):
                continue
            mapping[key] = 0
            changes.append({"path": f"{path}.{key}", "before": before, "after": 0})
    return changes


def _baseline_recall_variant(facts: dict[str, Any], core_specs: list[dict[str, str]]) -> dict[str, Any]:
    origin_docs = _artifact_docs(facts, "origin_doc_list")
    origin_faqs = _artifact_docs(facts, "origin_faq_list")
    docs = origin_docs + origin_faqs
    return {
        "variant_id": "baseline_trace_recall",
        "variant_type": "baseline_trace_recall",
        "status": "observed",
        "mode": "historical_trace_only",
        "query": _case_from_facts(facts).get("query", ""),
        "request_summary": {"source": "case_facts.artifacts", "http_executed": False},
        "counts": {
            "origin_doc_list": len(origin_docs),
            "origin_faq_list": len(origin_faqs),
            "recall_docs": len(docs),
        },
        "docs": docs,
        "matched_core_docs": _matched_core_docs(docs, core_specs),
        "failure_reason": "",
        "notes": "Historical origin_doc_list + origin_faq_list only; never overwritten by replay or live recall variants.",
    }


def _unsupported_recall_variant(variant_id: str, variant_type: str, query: str, reason: str) -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "variant_type": variant_type,
        "status": "unsupported",
        "query": query,
        "request_summary": {},
        "counts": {"recall_docs": 0},
        "docs": [],
        "matched_core_docs": [],
        "failure_reason": reason,
    }


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


def _execute_recall_variant(
    *,
    facts: dict[str, Any],
    template: dict[str, Any],
    endpoint: str,
    query: str,
    variant_id: str,
    variant_type: str,
    headers: dict[str, str],
    token_source: str,
    core_specs: list[dict[str, str]],
    timeout_seconds: int,
    mutator: Any = None,
    observation_note: str = "",
) -> dict[str, Any]:
    request_body = _build_recall_request_body(template, query=query, workspace_id=str(facts.get("workspace_id") or ""))
    parameter_changes: list[dict[str, Any]] = []
    if mutator:
        parameter_changes = mutator(request_body)
        if not parameter_changes:
            return _unsupported_recall_variant(
                variant_id,
                variant_type,
                query,
                "The trace template did not expose a safely identifiable field for this relaxed variant.",
            )
    try:
        with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
            response = client.post(endpoint, headers=headers, json=request_body)
    except Exception as exc:
        return {
            "variant_id": variant_id,
            "variant_type": variant_type,
            "status": "error",
            "query": query,
            "endpoint": endpoint,
            "request_payload": request_body,
            "request_summary": _recall_request_summary(request_body, endpoint=endpoint),
            "headers": _redacted_headers(headers),
            "auth_token_source": token_source,
            "parameter_changes": parameter_changes,
            "counts": {"recall_docs": 0},
            "docs": [],
            "matched_core_docs": [],
            "failure_reason": str(exc)[:1000],
        }
    try:
        response_payload = response.json()
    except Exception:
        response_payload = {"raw_text": response.text[:2000]}
    docs = _collect_docs_from_response(response_payload)
    status = "ok" if response.status_code < 400 else "error"
    message = ""
    if isinstance(response_payload, dict):
        message = str(response_payload.get("msg") or response_payload.get("message") or "")[:1000]
    return {
        "variant_id": variant_id,
        "variant_type": variant_type,
        "status": status,
        "query": query,
        "endpoint": endpoint,
        "request_payload": request_body,
        "request_summary": _recall_request_summary(request_body, endpoint=endpoint),
        "headers": _redacted_headers(headers),
        "auth_token_source": token_source,
        "template_kind": template.get("kind", ""),
        "http_status_code": response.status_code,
        "response_code": response_payload.get("code") if isinstance(response_payload, dict) else None,
        "response_message": message,
        "parameter_changes": parameter_changes,
        "counts": {"recall_docs": len(docs)},
        "docs": docs,
        "matched_core_docs": _matched_core_docs(docs, core_specs),
        "failure_reason": "" if status == "ok" else message or response.text[:500],
        "notes": observation_note,
    }


def run_recall_experiment(
    facts: dict[str, Any], *, query: str | None = None, context_queries: list[str] | None = None, timeout_seconds: int = 90
) -> dict[str, Any]:
    envelope = _base_envelope("recall", facts)
    core_specs = _case_core_doc_specs(facts)
    matrix: list[dict[str, Any]] = [_baseline_recall_variant(facts, core_specs)]
    context_queries = [item for item in (context_queries or []) if str(item or "").strip()]
    if not query and not context_queries:
        planned = plan_recall_experiment(facts)
        planned.update(
            {
                "status": "planned",
                "recall_variant_matrix": matrix,
                "baseline_request_summary": matrix[0]["request_summary"],
                "matched_core_docs": [
                    {"variant_id": matrix[0]["variant_id"], **item} for item in matrix[0].get("matched_core_docs", [])
                ],
                "notes": "No live recall query was supplied. Baseline reflects historical trace recall only; live variants require --query or --context-query.",
            }
        )
        return planned
    templates = _recall_templates(facts)
    if not templates:
        planned = plan_recall_experiment(facts)
        planned.update(
            {
                "status": "not_configured",
                "reason": "No recall/searchDoc request template was found in case_facts.experiment_inputs.recall_templates.",
                "query": query,
                "context_queries": context_queries,
                "recall_variant_matrix": matrix,
                "baseline_request_summary": matrix[0]["request_summary"],
                "matched_core_docs": [
                    {"variant_id": matrix[0]["variant_id"], **item} for item in matrix[0].get("matched_core_docs", [])
                ],
            }
        )
        return planned
    template = templates[0]
    endpoint = str(template.get("endpoint") or "")
    if not endpoint:
        envelope.update(
            {
                "status": "not_configured",
                "reason": "Recall template has no endpoint.",
                "query": query,
                "context_queries": context_queries,
                "recall_variant_matrix": matrix,
                "baseline_request_summary": matrix[0]["request_summary"],
            }
        )
        return envelope
    try:
        token, token_source = asyncio.run(resolve_workflow_auth_token(str(facts.get("workspace_id") or "")))
    except Exception as exc:
        envelope.update(
            {
                "status": "auth_error",
                "query": query,
                "context_queries": context_queries,
                "endpoint": endpoint,
                "error": str(exc)[:1000],
                "recall_variant_matrix": matrix,
                "baseline_request_summary": matrix[0]["request_summary"],
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

    executable_variants: list[dict[str, Any]] = []
    if query:
        executable_variants.extend(
            [
                {
                    "variant_id": "workflow_query_override",
                    "variant_type": "workflow_query_override",
                    "query": query,
                    "mutator": None,
                    "note": "Trace template query override; this is the compatibility path for --query.",
                },
                {
                    "variant_id": "topk_relaxed",
                    "variant_type": "topK/maxCount_relaxed",
                    "query": query,
                    "mutator": lambda body: _ensure_topk_at_least(body, 50),
                    "note": "Raises safely identifiable topK/maxCount style fields to at least 50.",
                },
                {
                    "variant_id": "label_relaxed",
                    "variant_type": "label_relaxed",
                    "query": query,
                    "mutator": _relax_label_fields,
                    "note": "Clears only clearly identifiable label/tag fields; unsupported if absent.",
                },
                {
                    "variant_id": "threshold_relaxed",
                    "variant_type": "threshold_relaxed",
                    "query": query,
                    "mutator": _relax_threshold_fields,
                    "note": "Lowers only clearly identifiable score/threshold fields; unsupported if absent.",
                },
            ]
        )
    for index, context_query in enumerate(context_queries, start=1):
        executable_variants.append(
            {
                "variant_id": f"context_query_{index}",
                "variant_type": "context_query",
                "query": context_query,
                "mutator": None,
                "note": "Context-rich query observation only; do not auto-promote workflow_input_loss from this hit without counterfactual outcome improvement.",
            }
        )
    for variant in executable_variants:
        matrix.append(
            _execute_recall_variant(
                facts=facts,
                template=template,
                endpoint=endpoint,
                query=variant["query"],
                variant_id=variant["variant_id"],
                variant_type=variant["variant_type"],
                headers=headers,
                token_source=token_source,
                core_specs=core_specs,
                timeout_seconds=timeout_seconds,
                mutator=variant.get("mutator"),
                observation_note=variant.get("note", ""),
            )
        )

    workflow_variant = next((item for item in matrix if item.get("variant_id") == "workflow_query_override"), None)
    first_ok = next((item for item in matrix if item.get("status") == "ok"), None)
    representative = workflow_variant if workflow_variant and workflow_variant.get("status") == "ok" else first_ok or matrix[0]
    docs = representative.get("docs") if isinstance(representative.get("docs"), list) else []
    matched_core_docs = [
        {"variant_id": variant.get("variant_id", ""), **match}
        for variant in matrix
        for match in (variant.get("matched_core_docs") if isinstance(variant.get("matched_core_docs"), list) else [])
    ]
    ok_count = sum(1 for item in matrix if item.get("status") == "ok")
    unsupported_count = sum(1 for item in matrix if item.get("status") == "unsupported")
    error_count = sum(1 for item in matrix if item.get("status") == "error")
    envelope.update(
        {
            "status": "ok" if ok_count else "error" if error_count else "observed",
            "query": query,
            "context_queries": context_queries,
            "mode": "recall_variant_matrix",
            "template_kind": template.get("kind", ""),
            "endpoint": endpoint,
            "request_payload": representative.get("request_payload", {}),
            "request_summary": representative.get("request_summary", {}),
            "baseline_request_summary": _recall_request_summary(
                _build_recall_request_body(template, query=query or context_queries[0], workspace_id=str(facts.get("workspace_id") or "")),
                endpoint=endpoint,
            ),
            "headers": _redacted_headers(headers),
            "auth_token_source": token_source,
            "http_status_code": representative.get("http_status_code"),
            "response_code": representative.get("response_code"),
            "response_message": representative.get("response_message", ""),
            "counts": {"recall_docs": len(docs)},
            "artifacts": {"recall_docs": docs},
            "recall_variant_matrix": matrix,
            "matched_core_docs": matched_core_docs,
            "variant_summary": {
                "total": len(matrix),
                "ok": ok_count,
                "unsupported": unsupported_count,
                "error": error_count,
            },
            "notes": "Recall experiment returns a variant matrix of observations only. Context-query hits do not automatically promote workflow_input_loss.",
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


def _key_docs_for_knowledge_detail(facts: dict[str, Any], target_doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    all_docs = (
        _artifact_docs(facts, "prompt_docs")
        + _artifact_docs(facts, "rerank_docs")
        + _artifact_docs(facts, "origin_doc_list")
        + _artifact_docs(facts, "origin_faq_list")
    )
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_doc(doc: dict[str, Any], reason: str) -> None:
        aliases = _doc_aliases(doc)
        key = next((item for item in aliases if item), _doc_id(doc.get("id")))
        if not key or key in seen:
            return
        seen.add(key)
        copied = dict(doc)
        copied["selection_reason"] = reason
        copied["doc_id_aliases"] = aliases
        chosen.append(copied)

    core_specs = _case_core_doc_specs(facts, target_doc_ids=target_doc_ids)
    for spec in core_specs:
        doc = _find_doc_for_spec(all_docs, spec)
        if doc:
            add_doc(doc, spec.get("source") or "core_document")
        else:
            add_doc(
                {
                    "id": spec.get("doc_id", ""),
                    "doc_id_aliases": [spec.get("doc_id", "")],
                    "title": spec.get("title_hint", ""),
                    "content": "",
                    "source": spec.get("source", ""),
                },
                spec.get("source") or "core_document",
            )
    for doc in _artifact_docs(facts, "prompt_docs")[:5]:
        add_doc(doc, "prompt_key_evidence")

    case = facts.get("case") if isinstance(facts.get("case"), dict) else {}
    evaluator_text = " ".join(str(case.get(key) or "") for key in ("judgement", "answer_hint", "expected_answer"))
    for match in re.finditer(r"(?:doc_id|文档|知识)[^\d]{0,12}(\d{4,})", evaluator_text):
        add_doc({"id": match.group(1), "doc_id_aliases": [match.group(1)], "title": "", "content": "", "source": "case_text_reference"}, "case_text_reference")
    return chosen[:20]


def _first_recursive_value(value: Any, keys: set[str]) -> Any:
    decoded = decode_jsonish(value)
    if isinstance(decoded, dict):
        for key, child in decoded.items():
            if str(key) in keys and child not in (None, ""):
                return child
        for child in decoded.values():
            found = _first_recursive_value(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(decoded, list):
        for child in decoded:
            found = _first_recursive_value(child, keys)
            if found not in (None, ""):
                return found
    return ""


def _status_fields(value: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    wanted = {
        "status",
        "state",
        "enable",
        "enabled",
        "deleted",
        "isDeleted",
        "is_deleted",
        "expireTime",
        "expire_time",
        "endTime",
        "end_time",
        "offline",
        "version",
    }

    def visit(node: Any, path: str = "$") -> None:
        decoded = decode_jsonish(node)
        if isinstance(decoded, dict):
            for key, child in decoded.items():
                child_path = f"{path}.{key}"
                if str(key) in wanted and child not in (None, "") and child_path not in found:
                    found[child_path] = child
                visit(child, child_path)
        elif isinstance(decoded, list):
            for index, child in enumerate(decoded[:50]):
                visit(child, f"{path}[{index}]")

    visit(value)
    return found


def _extract_doc_status(payload: Any, fallback_doc: dict[str, Any]) -> dict[str, Any]:
    text = json_dumps(payload) if payload not in (None, "", {}, []) else ""
    if not text:
        text = " ".join(str(fallback_doc.get(key) or "") for key in ("title", "content"))
    signals = [term for term in STATUS_SIGNAL_TERMS if term in text]
    last_modified = _first_recursive_value(
        payload,
        {
            "last_modified",
            "lastModified",
            "modify_time",
            "modifyTime",
            "modified_at",
            "modifiedAt",
            "update_time",
            "updateTime",
            "updated_at",
            "updatedAt",
        },
    )
    fields = _status_fields(payload)
    status_confirmed = bool(signals or fields or last_modified)
    if signals:
        reason = "status_signals_found"
    elif fields:
        reason = "detail_status_fields_found"
    elif last_modified:
        reason = "last_modified_found"
    else:
        reason = "status_unconfirmed"
    return {
        "status_signals": signals,
        "status_confirmed": status_confirmed,
        "last_modified": str(last_modified or ""),
        "status_reason": reason,
        "status_fields": fields,
    }


def _knowledge_detail_endpoint(doc: dict[str, Any]) -> str:
    aliases = _doc_aliases(doc)
    doc_id = next((item for item in aliases if item), _doc_id(doc.get("id")))
    endpoint = KNOWLEDGE_DETAIL_ENDPOINT_TEMPLATE.format(doc_id=quote(doc_id), identifier=quote(doc_id))
    source = _doc_source_for_detail(doc)
    if source and "source=" not in endpoint:
        separator = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{separator}source={quote(source)}"
    return endpoint


def _unconfirmed_detail(doc: dict[str, Any], reason: str, *, endpoint: str = "", error: str = "") -> dict[str, Any]:
    return {
        "doc_id": _doc_id(doc.get("id")),
        "doc_id_aliases": _doc_aliases(doc),
        "title": doc.get("title", ""),
        "selection_reason": doc.get("selection_reason", ""),
        "endpoint": endpoint,
        "request_payload": {},
        "http_status_code": None,
        "status_signals": [],
        "status_confirmed": False,
        "last_modified": "",
        "status_reason": reason,
        "status_fields": {},
        "error": error,
    }


def run_knowledge_detail_experiment(
    facts: dict[str, Any], *, target_doc_ids: list[str] | None = None, timeout_seconds: int = 90
) -> dict[str, Any]:
    envelope = _base_envelope("knowledge-detail", facts)
    key_docs = _key_docs_for_knowledge_detail(facts, target_doc_ids=target_doc_ids)
    if not key_docs:
        envelope.update(
            {
                "status": "no_key_docs",
                "counts": {"key_docs": 0, "confirmed": 0, "unconfirmed": 0},
                "knowledge_details": [],
                "notes": "No core_documents, target ids, or prompt key evidence were available for doc state enrichment.",
            }
        )
        return envelope
    try:
        token, token_source = asyncio.run(resolve_workflow_auth_token(str(facts.get("workspace_id") or "")))
    except Exception as exc:
        details = [_unconfirmed_detail(doc, "status_unconfirmed", error=f"auth_error: {str(exc)[:500]}") for doc in key_docs]
        envelope.update(
            {
                "status": "status_unconfirmed",
                "auth_token_source": "auth_error",
                "counts": {"key_docs": len(key_docs), "confirmed": 0, "unconfirmed": len(details)},
                "knowledge_details": details,
                "notes": "Knowledge detail auth failed; doc states are explicitly unconfirmed.",
            }
        )
        return envelope
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    details: list[dict[str, Any]] = []
    with httpx.Client(timeout=max(int(timeout_seconds), 1)) as client:
        for doc in key_docs:
            endpoint = _knowledge_detail_endpoint(doc)
            request_payload: dict[str, Any] = {}
            try:
                response = client.post(endpoint, headers=headers, json=request_payload)
                try:
                    payload = response.json()
                except Exception:
                    payload = {"raw_text": response.text[:2000]}
                if response.status_code >= 400:
                    details.append(
                        _unconfirmed_detail(
                            doc,
                            "status_unconfirmed",
                            endpoint=endpoint,
                            error=f"HTTP {response.status_code}: {response.text[:500]}",
                        )
                    )
                    continue
                extracted = _extract_doc_status(payload, doc)
                details.append(
                    {
                        "doc_id": _doc_id(doc.get("id")),
                        "doc_id_aliases": _doc_aliases(doc),
                        "title": doc.get("title", ""),
                        "selection_reason": doc.get("selection_reason", ""),
                        "endpoint": endpoint,
                        "request_payload": request_payload,
                        "headers": _redacted_headers(headers),
                        "auth_token_source": token_source,
                        "http_status_code": response.status_code,
                        "response_code": payload.get("code") if isinstance(payload, dict) else None,
                        "status_signals": extracted["status_signals"],
                        "status_confirmed": extracted["status_confirmed"],
                        "last_modified": extracted["last_modified"],
                        "status_reason": extracted["status_reason"],
                        "status_fields": extracted["status_fields"],
                        "error": "" if extracted["status_confirmed"] else "detail fields insufficient for state confirmation",
                        "response_preview": json_dumps(payload)[:1200],
                    }
                )
            except Exception as exc:
                details.append(_unconfirmed_detail(doc, "status_unconfirmed", endpoint=endpoint, error=str(exc)[:500]))
    confirmed = sum(1 for item in details if item.get("status_confirmed"))
    unconfirmed = len(details) - confirmed
    envelope.update(
        {
            "status": "ok" if confirmed else "status_unconfirmed",
            "endpoint_template": KNOWLEDGE_DETAIL_ENDPOINT_TEMPLATE,
            "auth_token_source": token_source,
            "counts": {"key_docs": len(key_docs), "confirmed": confirmed, "unconfirmed": unconfirmed},
            "knowledge_details": details,
            "artifacts": {"knowledge_detail_docs": details},
            "notes": "Doc state enrichment only covers key docs. status_confirmed=false/status_reason=status_unconfirmed means the state was not verified.",
        }
    )
    return envelope


def run_experiment(
    *,
    experiment_type: str,
    facts_file: str,
    output_dir: str | None = None,
    query: str | None = None,
    context_queries: list[str] | None = None,
    app_id: str | None = None,
    version_id: str | None = None,
    target_doc_ids: list[str] | None = None,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    facts = read_json_file(facts_file)
    if experiment_type == "recall":
        result = run_recall_experiment(facts, query=query, context_queries=context_queries, timeout_seconds=timeout_seconds)
    elif experiment_type == "rerank":
        result = run_rerank_experiment(facts, target_doc_ids=target_doc_ids)
    elif experiment_type == "knowledge-detail":
        result = run_knowledge_detail_experiment(facts, target_doc_ids=target_doc_ids, timeout_seconds=timeout_seconds)
    elif experiment_type == "replay":
        result = run_replay_experiment(facts, query=query, app_id=app_id, version_id=version_id)
    else:
        result = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "experiment_result",
            "status": "error",
            "error_code": "E_EXPERIMENT_TYPE",
            "message": f"Unsupported experiment_type={experiment_type}",
            "supported": ["recall", "rerank", "replay", "knowledge-detail"],
        }
    if output_dir:
        target = Path(output_dir)
        artifact_name = experiment_type.replace("-", "_")
        write_json(target / f"{artifact_name}_experiment.json", result)
    return result


def print_experiment_result(value: dict[str, Any]) -> None:
    print(json_dumps(value))
