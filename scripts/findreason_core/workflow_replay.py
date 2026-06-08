from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import httpx

from .env import load_runtime_env
from .models import AttributionRequest, EvidenceDoc, WorkflowReplayEvidence

load_runtime_env()

DEFAULT_WORKFLOW_REPLAY_URL = ""
DEFAULT_WORKFLOW_OPEN_EXEC_BASE_URL = "https://zhishang.bytedance.net"
DEFAULT_WORKFLOW_RDS_DATABASE = "zs_open"
DEFAULT_OPEN_PLAT_WORKSPACE_INFO_URL = "https://zhishang.bytedance.net/open-plat/api/workspace/get-workspace-info"
MAX_REPLAY_VALUE_CHARS = 12000
FAQ_RECALL_SOURCES = {"featured_search"}
DOC_RECALL_SOURCES = {"doc_search", "self_dataset_search", "suite_dataset"}
ORIGIN_DOC_KEYS = {"origin_doc_list", "doc_list", "docs", "documents"}
ORIGIN_FAQ_KEYS = {"origin_faq_list", "faq_list", "faqs"}
RERANK_DOC_KEYS = {"rerank_docs", "reranked_docs", "ranked_docs"}
PROMPT_DOC_KEYS = {"prompt_docs", "promptDocs", "context_docs", "context"}
ANSWER_KEYS = {"answer", "final_answer", "actual_output"}
REASONING_KEYS = {"reasoning", "reasoning_content", "thought", "trace", "debug_info"}
LEGACY_REPLAY_INPUT_SCHEMA = [
    {"key": "RankQuery", "type": "String", "required": True},
    {"key": "RetrieveQueryList", "type": "Array<String>", "required": False},
    {"key": "topk", "type": "Number", "required": False},
]


class WorkflowResolverError(RuntimeError):
    pass


def _truncate(value: Any, max_chars: int = MAX_REPLAY_VALUE_CHARS) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return value
    return {"truncated": True, "preview": text[:max_chars]}


def _safe_error(exc: Exception) -> str:
    text = repr(exc) if not str(exc) else str(exc)
    token = os.getenv("WORKFLOW_AUTH_TOKEN", "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text[:500]


def _numeric_id(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise WorkflowResolverError(f"{field_name} must be a numeric id for workflow resolver, got {text!r}")
    return text


def _run_bytedcli_query(database: str, sql: str) -> list[dict[str, Any]]:
    env = os.environ.copy()
    env.setdefault("NPM_CONFIG_REGISTRY", "http://bnpm.byted.org")
    timeout = float(os.getenv("WORKFLOW_RDS_TIMEOUT_SECONDS", "30"))
    bytedcli_bin = os.getenv("BYTEDCLI_BIN", "bytedcli")
    commands = [[bytedcli_bin, "--json", "rds", "db", "query", database, sql]]
    if "BYTEDCLI_BIN" not in os.environ:
        commands.append(
            [
                "npx",
                "-y",
                "@bytedance-dev/bytedcli@latest",
                "--json",
                "rds",
                "db",
                "query",
                database,
                sql,
            ]
        )
    last_error = ""
    completed = None
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except FileNotFoundError as exc:
            last_error = str(exc)
            continue
        if completed.returncode == 0:
            break
        last_error = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
    if completed is None:
        raise WorkflowResolverError(f"bytedcli not found: {last_error[:500]}")
    if completed.returncode != 0:
        raise WorkflowResolverError(f"bytedcli RDS query failed: {last_error[:500]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowResolverError(f"bytedcli RDS query returned non-json output: {completed.stdout[:300]}") from exc
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [item for item in data["data"] if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    raise WorkflowResolverError("bytedcli RDS query returned an unsupported payload shape")


def _find_list_key(value: Any, keys: set[str]) -> list[Any] | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, list):
                return item
        for item in value.values():
            found = _find_list_key(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_list_key(item, keys)
            if found is not None:
                return found
    return None


def _schema_param_key(param: dict[str, Any]) -> str:
    value = param.get("key") or param.get("name") or param.get("field") or param.get("param_name") or param.get("paramName")
    return str(value or "").strip()


def _schema_param_type(param: dict[str, Any]) -> str:
    value = param.get("type") or param.get("value_type") or param.get("valueType") or param.get("param_type") or param.get("paramType")
    return str(value or "String").strip() or "String"


def _schema_param_required(param: dict[str, Any]) -> bool:
    for key in ("required", "is_required", "isRequired", "require"):
        if key in param:
            value = param.get(key)
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes"}
            return bool(value)
    return False


def _schema_param_default(param: dict[str, Any]) -> Any:
    for key in ("default_value", "defaultValue", "default"):
        if key in param:
            return param.get(key)
    return None


def _normalize_schema_param(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    key = _schema_param_key(raw)
    if not key:
        return None
    normalized = dict(raw)
    normalized["key"] = key
    normalized["type"] = _schema_param_type(raw)
    normalized["required"] = _schema_param_required(raw)
    if _schema_param_default(raw) is not None:
        normalized["default_value"] = _schema_param_default(raw)
    return normalized


def _node_identity(node: dict[str, Any]) -> str:
    values = [
        node.get("type"),
        node.get("node_type"),
        node.get("nodeType"),
        node.get("name"),
        node.get("id"),
        node.get("node_id"),
    ]
    return " ".join(str(value) for value in values if value is not None).lower()


def _extract_start_schema(workflow_config: Any) -> list[dict[str, Any]]:
    for node in _walk(workflow_config):
        if not isinstance(node, dict):
            continue
        identity = _node_identity(node)
        if "start" not in identity:
            continue
        output_params = _find_list_key(node, {"output_params", "outputParams", "outputs"})
        if not output_params:
            continue
        return [param for raw in output_params for param in [_normalize_schema_param(raw)] if param]
    return []


def _extract_workflow_parts(workflow_config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    candidates = [workflow_config]
    for key in ("workflow", "graph", "flow", "dsl", "config"):
        value = workflow_config.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        nodes = candidate.get("nodes")
        edges = candidate.get("edges")
        if isinstance(nodes, list):
            global_config = (
                candidate.get("global_config")
                or candidate.get("globalConfig")
                or workflow_config.get("global_config")
                or workflow_config.get("globalConfig")
                or {}
            )
            return (
                [node for node in nodes if isinstance(node, dict)],
                [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else [],
                global_config if isinstance(global_config, dict) else {},
            )
    nodes = _find_list_key(workflow_config, {"nodes"}) or []
    edges = _find_list_key(workflow_config, {"edges"}) or []
    global_config = workflow_config.get("global_config") or workflow_config.get("globalConfig") or {}
    return (
        [node for node in nodes if isinstance(node, dict)],
        [edge for edge in edges if isinstance(edge, dict)],
        global_config if isinstance(global_config, dict) else {},
    )


def _node_id(node: dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    value = (
        node.get("id")
        or node.get("node_id")
        or node.get("nodeId")
        or data.get("id")
        or data.get("node_id")
        or data.get("nodeId")
    )
    return str(value or "").strip()


def _node_type(node: dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    value = (
        node.get("type")
        or node.get("node_type")
        or node.get("nodeType")
        or data.get("type")
        or data.get("node_type")
        or data.get("nodeType")
    )
    return str(value or "").strip()


def _node_name(node: dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    value = node.get("name") or node.get("title") or data.get("name") or data.get("title") or data.get("label")
    return str(value or "").strip()


def _field_keys(node: dict[str, Any], *keys: str) -> list[str]:
    values: list[Any] = []
    for key in keys:
        found = _find_list_key(node, {key})
        if found:
            values.extend(found)
    results: list[str] = []
    for value in values:
        if isinstance(value, dict):
            key = _schema_param_key(value)
            if key and key not in results:
                results.append(key)
        elif isinstance(value, str) and value.strip() and value.strip() not in results:
            results.append(value.strip())
    return results


def _edge_endpoint(edge: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = edge.get(key)
        if value not in (None, ""):
            return str(value)
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _node_order(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    ids = [_node_id(node) for node in nodes if _node_id(node)]
    known = set(ids)
    adjacency = {node_id: [] for node_id in ids}
    indegree = {node_id: 0 for node_id in ids}
    for edge in edges:
        source = _edge_endpoint(edge, "source", "source_node_id", "sourceNodeId", "from", "fromNodeId")
        target = _edge_endpoint(edge, "target", "target_node_id", "targetNodeId", "to", "toNodeId")
        if source in known and target in known:
            adjacency[source].append(target)
            indegree[target] += 1
    queue = [node_id for node_id in ids if indegree.get(node_id, 0) == 0]
    ordered: list[str] = []
    while queue:
        node_id = queue.pop(0)
        if node_id in ordered:
            continue
        ordered.append(node_id)
        for target in adjacency.get(node_id, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    for node_id in ids:
        if node_id not in ordered:
            ordered.append(node_id)
    return ordered


def _workflow_config_summary(workflow_config: dict[str, Any]) -> dict[str, Any]:
    nodes, edges, global_config = _extract_workflow_parts(workflow_config)
    order = _node_order(nodes, edges)
    order_index = {node_id: index for index, node_id in enumerate(order)}
    summarized_nodes = []
    for node in nodes:
        node_id = _node_id(node)
        summarized_nodes.append(
            {
                "id": node_id,
                "type": _node_type(node),
                "name": _node_name(node),
                "order": order_index.get(node_id),
                "input_keys": _field_keys(node, "input_params", "inputParams", "inputs"),
                "output_keys": _field_keys(node, "output_params", "outputParams", "outputs"),
            }
        )
    summarized_edges = [
        {
            "source": _edge_endpoint(edge, "source", "source_node_id", "sourceNodeId", "from", "fromNodeId"),
            "target": _edge_endpoint(edge, "target", "target_node_id", "targetNodeId", "to", "toNodeId"),
            "type": str(edge.get("type") or edge.get("edge_type") or edge.get("edgeType") or ""),
        }
        for edge in edges
    ]
    return {
        "node_count": len(summarized_nodes),
        "edge_count": len(summarized_edges),
        "nodes": summarized_nodes,
        "edges": summarized_edges,
        "node_order": order,
        "global_config": _truncate(global_config, 4000),
    }


def _parse_workflow_config(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkflowResolverError("applications_wip.workflow_config_v2 is not valid JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise WorkflowResolverError("applications_wip.workflow_config_v2 is empty or unsupported")


def resolve_workflow(request: AttributionRequest, version_id: str | None = None) -> dict[str, Any]:
    case_input = request.case_input
    workspace_id = _numeric_id(case_input.workspace_id, "workspace_id")
    app_id = _numeric_id(case_input.app_id, "app_id")
    database = os.getenv("WORKFLOW_RDS_DATABASE", DEFAULT_WORKFLOW_RDS_DATABASE)
    app_rows = _run_bytedcli_query(
        database,
        (
            "select id, workspace_id, name, status, version_id "
            f"from applications where workspace_id = {workspace_id} and id = {app_id} limit 1"
        ),
    )
    if not app_rows:
        raise WorkflowResolverError(f"applications not found for workspace_id={workspace_id}, app_id={app_id} in {database}")
    version = version_id or os.getenv("WORKFLOW_VERSION_ID")
    if version:
        version = _numeric_id(version, "version_id")
        wip_where = f"workspace_id = {workspace_id} and app_id = {app_id} and version_id = {version}"
    else:
        published_status = _numeric_id(os.getenv("WORKFLOW_PUBLISHED_STATUS", "1"), "WORKFLOW_PUBLISHED_STATUS")
        wip_where = f"workspace_id = {workspace_id} and app_id = {app_id} and status = {published_status}"
    wip_rows = _run_bytedcli_query(
        database,
        (
            "select id, app_id, workspace_id, name, status, version_id, workflow_config_v2 "
            f"from applications_wip where {wip_where} order by id desc limit 1"
        ),
    )
    if not wip_rows:
        raise WorkflowResolverError(f"applications_wip not found for workspace_id={workspace_id}, app_id={app_id}, version={version or 'published'} in {database}")
    app_row = app_rows[0]
    wip_row = wip_rows[0]
    workflow_config = _parse_workflow_config(wip_row.get("workflow_config_v2"))
    input_schema = _extract_start_schema(workflow_config)
    workflow_config_summary = _workflow_config_summary(workflow_config)
    return {
        "source": "rds",
        "database": database,
        "workspace_id": str(app_row.get("workspace_id") or workspace_id),
        "app_id": str(app_row.get("id") or app_id),
        "app_name": str(app_row.get("name") or wip_row.get("name") or ""),
        "version_id": str(wip_row.get("version_id") or app_row.get("version_id") or ""),
        "wip_id": str(wip_row.get("id") or ""),
        "status": wip_row.get("status"),
        "input_schema": input_schema,
        "workflow_config": workflow_config_summary,
    }


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _clean_string_list(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(item).strip() for item in values if str(item).strip()]
    if isinstance(values, str):
        parsed = _parse_json_string(values)
        if isinstance(parsed, list):
            return _clean_string_list(parsed)
        return [item.strip() for item in values.split(",") if item.strip()]
    return []


def _coerce_param_value(value: Any, param_type: str) -> Any:
    lowered = param_type.lower()
    if "array" in lowered:
        if isinstance(value, list):
            return value
        return _clean_string_list(value)
    if "number" in lowered or "integer" in lowered or lowered in {"int", "long", "double", "float"}:
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        return float(text) if "." in text else int(text)
    if "bool" in lowered:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes"}
    if "object" in lowered or "json" in lowered:
        parsed = _parse_json_string(value)
        return parsed if parsed is not None else value
    return "" if value is None else str(value)


def _mapped_input_value(request: AttributionRequest, key: str, query_variants: list[str] | None, topk_env: str, default_topk: int) -> tuple[bool, Any]:
    case_input = request.case_input
    canonical = _canonical_key(key)
    if canonical in {"query", "rankquery", "question", "input", "userquery", "oriquery", "originalquery"}:
        return True, case_input.query
    if canonical in {"retrievequerylist", "rewritequerylist", "querylist", "queries", "additionalqueries"}:
        values = query_variants if query_variants is not None else _clean_string_list(case_input.retrieve_query_list)
        return True, values
    if canonical in {"topk", "topn", "limit", "maxcount", "maxdocs", "count"}:
        if topk_env == "WORKFLOW_TOPK" and request.workflow_overrides.topk is not None:
            return True, request.workflow_overrides.topk
        return True, int(os.getenv(topk_env, str(default_topk)))
    if canonical in {"workspaceid", "workspace"}:
        return True, case_input.workspace_id
    if canonical in {"appid", "app"}:
        return True, case_input.app_id
    if canonical in {"expectedknowledgeids", "knowledgeids", "expectedids"}:
        return True, case_input.expected_knowledge_ids
    return False, None


def build_workflow_payload(
    request: AttributionRequest,
    input_schema: list[dict[str, Any]],
    *,
    query_variants: list[str] | None = None,
    topk_env: str = "WORKFLOW_TOPK",
    default_topk: int = 15,
) -> tuple[dict[str, Any], list[str]]:
    input_params: list[dict[str, Any]] = []
    missing_fields: list[str] = []
    for raw_param in input_schema:
        param = _normalize_schema_param(raw_param) if isinstance(raw_param, dict) else None
        if not param:
            continue
        key = param["key"]
        param_type = _schema_param_type(param)
        mapped, value = _mapped_input_value(request, key, query_variants, topk_env, default_topk)
        if not mapped:
            value = _schema_param_default(param)
        if value is None:
            if _schema_param_required(param):
                missing_fields.append(key)
            continue
        try:
            value = _coerce_param_value(value, param_type)
        except (TypeError, ValueError):
            if _schema_param_required(param):
                missing_fields.append(key)
            continue
        input_params.append({"key": key, "type": param_type, "value": value})
    return {"stream": False, "node_result_filter": {"detail": True}, "input_params": input_params}, missing_fields


def _build_replay_payload(request: AttributionRequest, input_schema: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload, _ = build_workflow_payload(request, input_schema or LEGACY_REPLAY_INPUT_SCHEMA)
    return payload


def _workflow_endpoint(resolved_app: dict[str, Any]) -> str:
    base_url = os.getenv("WORKFLOW_OPEN_EXEC_BASE_URL", DEFAULT_WORKFLOW_OPEN_EXEC_BASE_URL).rstrip("/")
    app_id = resolved_app.get("app_id") or resolved_app.get("id")
    return f"{base_url}/open-exec/api/v1/workflow/{app_id}/completions"


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _as_doc(value: Any, source: str) -> EvidenceDoc | None:
    if not isinstance(value, dict):
        return None
    doc_id = value.get("id") or value.get("doc_id") or value.get("knowledge_id") or value.get("document_id")
    title = value.get("title") or value.get("name") or value.get("document_name") or ""
    content = value.get("content") or value.get("text") or value.get("chunk") or value.get("summary") or ""
    rank = value.get("rank")
    score = value.get("score") or value.get("recallScore") or value.get("fineScore")
    if doc_id is None and not title and not content:
        return None
    return EvidenceDoc(
        id=str(doc_id) if doc_id is not None else None,
        title=str(title),
        content=str(content)[:1500],
        rank=rank if isinstance(rank, int) else None,
        score=float(score) if isinstance(score, (int, float)) else None,
        source=source,
    )


def _dedupe_docs(docs: list[EvidenceDoc]) -> list[EvidenceDoc]:
    seen: set[tuple[str | None, str, str]] = set()
    unique_docs: list[EvidenceDoc] = []
    for doc in docs:
        marker = (doc.id, doc.title, doc.content[:80])
        if marker in seen:
            continue
        seen.add(marker)
        if doc.rank is None:
            doc.rank = len(unique_docs) + 1
        unique_docs.append(doc)
    return unique_docs[:20]


def _collect_docs(response_payload: Any, candidate_keys: set[str], source: str) -> list[EvidenceDoc]:
    docs: list[EvidenceDoc] = []
    for node in _walk(response_payload):
        for key, value in node.items():
            if key not in candidate_keys:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                doc = _as_doc(item, source)
                if doc:
                    docs.append(doc)
    return _dedupe_docs(docs)


def _find_first_text(response_payload: Any, candidate_keys: set[str]) -> str:
    for node in _walk(response_payload):
        for key, value in node.items():
            if key in candidate_keys and isinstance(value, str):
                return value[:4000]
    return ""


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _parse_maybe_json(value: Any) -> Any:
    parsed = _parse_json_string(value)
    return parsed if parsed is not None else value


def _payload_variants(value: Any) -> list[Any]:
    parsed = _parse_maybe_json(value)
    variants = [parsed] if parsed is not None else []
    if isinstance(parsed, dict):
        preview = _parse_json_string(parsed.get("preview"))
        if preview is not None:
            variants.append(preview)
    return variants


def _expanded_payloads(response_payload: Any) -> list[Any]:
    payloads = [response_payload]
    if isinstance(response_payload, dict):
        data = response_payload.get("data")
        message = data.get("message") if isinstance(data, dict) else response_payload.get("message")
        if isinstance(message, dict):
            parsed_content = _parse_json_string(message.get("content"))
            if parsed_content is not None:
                payloads.append(parsed_content)
        parsed_content = _parse_json_string(response_payload.get("content"))
        if parsed_content is not None:
            payloads.append(parsed_content)
    return payloads


def _first_present(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value:
            return value.get(key)
    return None


def _extract_node_result_items(response_payload: Any) -> list[dict[str, Any]]:
    for payload in _expanded_payloads(response_payload):
        for node in _walk(payload):
            if not isinstance(node, dict):
                continue
            for key in ("node_results", "nodeResults", "node_result", "nodeResultsMap"):
                value = node.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    return [item for item in value.values() if isinstance(item, dict)]
    return []


def _normalize_node_trace(node_result: dict[str, Any]) -> dict[str, Any]:
    node_id = _first_present(node_result, ("node_id", "nodeId", "id"))
    node_type = _first_present(node_result, ("node_type", "nodeType", "type"))
    node_name = _first_present(node_result, ("node_name", "nodeName", "name"))
    input_value = _first_present(node_result, ("input", "inputs", "input_data", "inputData"))
    output_value = _first_present(node_result, ("output", "outputs", "output_data", "outputData", "result"))
    error = _first_present(node_result, ("error", "error_msg", "errorMsg", "exception"))
    return {
        "node_id": str(node_id or ""),
        "name": str(node_name or ""),
        "type": str(node_type or ""),
        "status": str(_first_present(node_result, ("status", "state")) or ""),
        "input": _truncate(_parse_maybe_json(input_value)),
        "output": _truncate(_parse_maybe_json(output_value)),
        "error": _truncate(_parse_maybe_json(error)) if error is not None else None,
    }


def _extract_node_traces(response_payload: Any) -> list[dict[str, Any]]:
    traces = [_normalize_node_trace(item) for item in _extract_node_result_items(response_payload)]
    return [trace for trace in traces if trace["node_id"] or trace["name"] or trace["type"]]


def _node_result_payloads(node_results: list[dict[str, Any]]) -> list[Any]:
    payloads: list[Any] = []
    for node_result in node_results:
        input_value = _first_present(node_result, ("input", "inputs", "input_data", "inputData"))
        output_value = _first_present(node_result, ("output", "outputs", "output_data", "outputData", "result"))
        payloads.extend(_payload_variants(input_value))
        payloads.extend(_payload_variants(output_value))
    return [payload for payload in payloads if payload is not None]


def _collect_docs_from_values(payloads: list[Any], candidate_keys: set[str], source: str) -> list[EvidenceDoc]:
    docs: list[EvidenceDoc] = []
    for payload in payloads:
        docs.extend(_collect_docs(payload, candidate_keys, source))
    return _dedupe_docs(docs)


def _find_first_text_from_values(payloads: list[Any], candidate_keys: set[str]) -> str:
    for payload in payloads:
        text = _find_first_text(payload, candidate_keys)
        if text:
            return text
    return ""


def _has_key_from_values(payloads: list[Any], candidate_keys: set[str]) -> bool:
    for payload in payloads:
        for node in _walk(payload):
            if isinstance(node, dict) and any(key in node for key in candidate_keys):
                return True
    return False


def _collect_docs_from_payloads(response_payload: Any, candidate_keys: set[str], source: str) -> list[EvidenceDoc]:
    docs: list[EvidenceDoc] = []
    for payload in _expanded_payloads(response_payload):
        docs.extend(_collect_docs(payload, candidate_keys, source))
    return _dedupe_docs(docs)


def _recall_sources(value: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    recall_source = value.get("recallSource")
    if isinstance(recall_source, str) and recall_source:
        sources.add(recall_source)
    all_recall_source = value.get("allRecallSource")
    if isinstance(all_recall_source, dict):
        sources.update(str(key) for key in all_recall_source.keys() if key)
    return sources


def _primary_recall_source(value: dict[str, Any]) -> str:
    recall_source = value.get("recallSource")
    if isinstance(recall_source, str) and recall_source:
        return recall_source
    sources = _recall_sources(value)
    for source in sorted(sources):
        if source:
            return source
    return "output"


def _is_faq_output_item(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    sources = _recall_sources(value)
    return bool(sources & FAQ_RECALL_SOURCES) or value.get("type") == 4


def _is_doc_output_item(value: Any) -> bool:
    if not isinstance(value, dict) or _is_faq_output_item(value):
        return False
    sources = _recall_sources(value)
    doc_type = value.get("type")
    if sources:
        return bool(sources & DOC_RECALL_SOURCES) or doc_type == 2
    if doc_type is not None:
        return doc_type == 2
    return True


def _collect_output_docs_from_payloads(response_payload: Any) -> tuple[list[EvidenceDoc], list[EvidenceDoc]]:
    doc_items: list[EvidenceDoc] = []
    faq_items: list[EvidenceDoc] = []
    for payload in _expanded_payloads(response_payload):
        for node in _walk(payload):
            output = node.get("output")
            if not isinstance(output, list):
                continue
            for item in output:
                if _is_faq_output_item(item):
                    doc = _as_doc(item, "workflow_replay:featured_search")
                    if doc:
                        faq_items.append(doc)
                elif _is_doc_output_item(item):
                    source = _primary_recall_source(item) if isinstance(item, dict) else "output"
                    doc = _as_doc(item, f"workflow_replay:{source}")
                    if doc:
                        doc_items.append(doc)
    return _dedupe_docs(doc_items), _dedupe_docs(faq_items)


def _find_first_text_from_payloads(response_payload: Any, candidate_keys: set[str]) -> str:
    for payload in _expanded_payloads(response_payload):
        text = _find_first_text(payload, candidate_keys)
        if text:
            return text
    return ""


def _has_key_from_payloads(response_payload: Any, candidate_keys: set[str]) -> bool:
    for payload in _expanded_payloads(response_payload):
        for node in _walk(payload):
            if any(key in node for key in candidate_keys):
                return True
    return False


def _extract_evidence(response_payload: Any, *, include_output_as_origin: bool = False) -> dict[str, Any]:
    node_result_items = _extract_node_result_items(response_payload)
    node_traces = [_normalize_node_trace(item) for item in node_result_items]
    node_traces = [trace for trace in node_traces if trace["node_id"] or trace["name"] or trace["type"]]
    output_doc_list, output_faq_list = _collect_output_docs_from_payloads(response_payload)
    if node_traces:
        trace_payloads = _node_result_payloads(node_result_items)
        origin_doc_list = _collect_docs_from_values(trace_payloads, ORIGIN_DOC_KEYS, "workflow_replay")
        origin_faq_list = _collect_docs_from_values(trace_payloads, ORIGIN_FAQ_KEYS, "workflow_replay")
        rerank_docs = _collect_docs_from_values(trace_payloads, RERANK_DOC_KEYS, "workflow_replay")
        prompt_docs = _collect_docs_from_values(trace_payloads, PROMPT_DOC_KEYS, "workflow_replay")
        answer = _find_first_text_from_values(trace_payloads, ANSWER_KEYS)
        reasoning = _find_first_text_from_values(trace_payloads, REASONING_KEYS)
        has_origin_trace = _has_key_from_values(trace_payloads, ORIGIN_DOC_KEYS | ORIGIN_FAQ_KEYS)
        has_rerank_trace = _has_key_from_values(trace_payloads, RERANK_DOC_KEYS)
        has_prompt_trace = _has_key_from_values(trace_payloads, PROMPT_DOC_KEYS)
        has_answer_trace = _has_key_from_values(trace_payloads, ANSWER_KEYS)
    else:
        origin_doc_list = _collect_docs_from_payloads(response_payload, ORIGIN_DOC_KEYS, "workflow_replay")
        origin_faq_list = _collect_docs_from_payloads(response_payload, ORIGIN_FAQ_KEYS, "workflow_replay")
        if include_output_as_origin:
            origin_doc_list = _dedupe_docs(origin_doc_list + output_doc_list)
            origin_faq_list = _dedupe_docs(origin_faq_list + output_faq_list)
        rerank_docs = _collect_docs_from_payloads(response_payload, RERANK_DOC_KEYS, "workflow_replay")
        prompt_docs = _collect_docs_from_payloads(response_payload, PROMPT_DOC_KEYS, "workflow_replay")
        answer = _find_first_text_from_payloads(response_payload, ANSWER_KEYS)
        reasoning = _find_first_text_from_payloads(response_payload, REASONING_KEYS)
        has_origin_trace = include_output_as_origin or _has_key_from_payloads(response_payload, ORIGIN_DOC_KEYS | ORIGIN_FAQ_KEYS)
        has_rerank_trace = _has_key_from_payloads(response_payload, RERANK_DOC_KEYS)
        has_prompt_trace = _has_key_from_payloads(response_payload, PROMPT_DOC_KEYS)
        has_answer_trace = _has_key_from_payloads(response_payload, ANSWER_KEYS)
    return {
        "origin_doc_list": [doc.model_dump(mode="json") for doc in origin_doc_list],
        "origin_faq_list": [doc.model_dump(mode="json") for doc in origin_faq_list],
        "workflow_output_doc_list": [doc.model_dump(mode="json") for doc in output_doc_list],
        "workflow_output_faq_list": [doc.model_dump(mode="json") for doc in output_faq_list],
        "rerank_docs": [doc.model_dump(mode="json") for doc in rerank_docs],
        "prompt_docs": [doc.model_dump(mode="json") for doc in prompt_docs],
        "answer": answer,
        "reasoning": reasoning,
        "trace_completeness": {
            "has_origin_trace": has_origin_trace,
            "has_rerank_trace": has_rerank_trace,
            "has_prompt_trace": has_prompt_trace,
            "has_answer_trace": has_answer_trace,
            "output_only": bool(output_doc_list or output_faq_list) and not has_origin_trace and not has_rerank_trace and not has_prompt_trace,
        },
        "node_traces": node_traces,
    }


def _replay_status_and_notes(extracted: dict[str, Any]) -> tuple[str, str]:
    trace = extracted.get("trace_completeness")
    if not isinstance(trace, dict):
        return "ok", "已通过 PipelineReplayTool 重跑 workflow 并构建运行级证据。"
    has_chain_trace = bool(trace.get("has_origin_trace") or trace.get("has_rerank_trace") or trace.get("has_prompt_trace"))
    if has_chain_trace:
        return "ok", "已通过 PipelineReplayTool 重跑 workflow 并构建运行级证据。"
    if trace.get("output_only"):
        return "partial", "Workflow 重跑成功，但仅返回 output 列表，未返回 origin/rerank/prompt 链路 trace；output 只作为工作流产物样本，不作为线上召回或重排证据。"
    return "partial", "Workflow 重跑成功，但未返回可用于归因的 origin/rerank/prompt 链路 trace。"


def _business_error(response_payload: Any) -> str | None:
    if not isinstance(response_payload, dict):
        return "Workflow replay returned a non-object response"
    data = response_payload.get("data")
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status and status != "completed":
        request_id = data.get("request_id") or response_payload.get("request_id")
        error = data.get("error") or response_payload.get("message") or "empty workflow content"
        return f"Workflow replay status={status}; request_id={request_id}; error={error}"
    return None


def _authoritative_fornax_trace(request: AttributionRequest) -> dict[str, Any] | None:
    evidence = request.workflow_replay.extracted_evidence
    if not isinstance(evidence, dict):
        return None
    if evidence.get("trace_source") not in {"fornax-cli trace get", "openplat_trace_detail"}:
        return None
    if not evidence.get("has_middle_node_trace"):
        return None
    return {
        "trace_source": evidence.get("trace_source"),
        "fornax_evidence_status": evidence.get("fornax_evidence_status", "authoritative"),
        "log_id": evidence.get("log_id"),
        "fornax_space_id": evidence.get("fornax_space_id"),
        "middle_node_types": evidence.get("middle_node_types", []),
        "rag_node_types": evidence.get("rag_node_types", []),
        "counts": evidence.get("counts", {}),
        "mapping_status": evidence.get("mapping_status"),
    }


def _workflow_headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def _openplat_bootstrap_token() -> tuple[str, str]:
    value = os.getenv("OPEN_PLAT_ZS_OPEN_TOKEN", "").strip()
    if value:
        return value, "OPEN_PLAT_ZS_OPEN_TOKEN"
    return "", "not_configured"


async def resolve_workflow_auth_token(workspace_id: str) -> tuple[str | None, str]:
    if os.getenv("FINDREASON_LIVE", "true").lower() in {"0", "false", "no"}:
        return None, "live_disabled"
    bootstrap_token, bootstrap_source = _openplat_bootstrap_token()
    env_token = os.getenv("WORKFLOW_AUTH_TOKEN", "").strip()
    if bootstrap_token:
        workspace_info_url = os.getenv("OPEN_PLAT_WORKSPACE_INFO_URL", DEFAULT_OPEN_PLAT_WORKSPACE_INFO_URL)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                workspace_info_url,
                params={"workspaceId": workspace_id},
                headers={
                    "x-zs-plt-open": "zs_open",
                    "Authorization": f"Bearer {bootstrap_token}",
                },
            )
        if response.status_code >= 400:
            if env_token:
                return env_token, "workflow_auth_token_env_fallback"
            raise WorkflowResolverError(f"workspace info HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        auth_info = data.get("authInfo") if isinstance(data, dict) else None
        api_key = auth_info.get("apiKey") if isinstance(auth_info, dict) else None
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip(), f"workspace_info_api:{bootstrap_source}"
        if env_token:
            return env_token, "workflow_auth_token_env_fallback"
        code = payload.get("code") if isinstance(payload, dict) else None
        raise WorkflowResolverError(f"workspace info returned no authInfo.apiKey for workspace_id={workspace_id}; code={code}")
    if env_token:
        return env_token, "workflow_auth_token_env"
    return None, "not_configured"


def _merge_replay_evidence(request: AttributionRequest, replay: WorkflowReplayEvidence) -> AttributionRequest:
    enriched = request.model_copy(deep=True)
    authoritative_trace = _authoritative_fornax_trace(request)
    if authoritative_trace:
        replay.extracted_evidence["replay_role"] = "supplemental_current_version"
        replay.extracted_evidence["preserved_authoritative_fornax_trace"] = authoritative_trace
        replay.notes = (
            (replay.notes + " " if replay.notes else "")
            + "已保留 Fornax 原始中间节点 trace 作为权威 badcase 证据；本次 workflow replay 仅作为当前版本对照，不覆盖原始 origin/rerank/prompt/answer。"
        )
    enriched.workflow_replay = replay
    extracted = replay.extracted_evidence
    if replay.status not in {"ok", "partial"}:
        return enriched

    origin_doc_list = [EvidenceDoc.model_validate(doc) for doc in extracted.get("origin_doc_list", []) if isinstance(doc, dict)]
    origin_faq_list = [EvidenceDoc.model_validate(doc) for doc in extracted.get("origin_faq_list", []) if isinstance(doc, dict)]
    rerank_docs = [EvidenceDoc.model_validate(doc) for doc in extracted.get("rerank_docs", []) if isinstance(doc, dict)]
    prompt_docs = [EvidenceDoc.model_validate(doc) for doc in extracted.get("prompt_docs", []) if isinstance(doc, dict)]
    trace = extracted.get("trace_completeness") if isinstance(extracted.get("trace_completeness"), dict) else {}

    if authoritative_trace:
        return enriched

    if origin_doc_list:
        enriched.retrieval.origin_doc_list = origin_doc_list
    if origin_faq_list:
        enriched.retrieval.origin_faq_list = origin_faq_list
    if rerank_docs:
        enriched.rerank.rerank_docs = rerank_docs
    if prompt_docs:
        enriched.rerank.prompt_docs = prompt_docs
    if extracted.get("answer"):
        enriched.qa.answer = str(extracted["answer"])

    if enriched.case_input.expected_knowledge_ids and trace.get("has_origin_trace"):
        ids = {doc.id for doc in origin_doc_list + origin_faq_list if doc.id}
        enriched.retrieval.online_retrieval_hit = any(expected_id in ids for expected_id in enriched.case_input.expected_knowledge_ids)
        enriched.retrieval.expected_knowledge_hit = enriched.retrieval.online_retrieval_hit
    if enriched.case_input.expected_knowledge_ids and trace.get("has_rerank_trace"):
        rerank_ids = {doc.id for doc in rerank_docs if doc.id}
        enriched.rerank.expected_doc_survived_rerank = any(expected_id in rerank_ids for expected_id in enriched.case_input.expected_knowledge_ids)
    if enriched.case_input.expected_knowledge_ids and trace.get("has_prompt_trace"):
        prompt_ids = {doc.id for doc in prompt_docs if doc.id}
        enriched.rerank.expected_doc_in_prompt = any(expected_id in prompt_ids for expected_id in enriched.case_input.expected_knowledge_ids)

    return enriched


async def replay_workflow(request: AttributionRequest) -> AttributionRequest:
    endpoint = DEFAULT_WORKFLOW_REPLAY_URL
    token = None
    auth_token_source = "not_configured"
    request_payload = _build_replay_payload(request)
    authoritative_trace = _authoritative_fornax_trace(request)
    if authoritative_trace:
        enriched = request.model_copy(deep=True)
        replay = enriched.workflow_replay
        replay.enabled = False
        replay.status = "ok"
        replay.request_payload = request_payload
        replay.auth_token_source = "not_needed_original_trace"
        replay.extracted_evidence["replay_role"] = "not_run_original_trace_authoritative"
        replay.extracted_evidence["replay_skipped"] = True
        replay.extracted_evidence["preserved_authoritative_fornax_trace"] = authoritative_trace
        replay.notes = "检测到原始 trace 中间节点证据，未执行 workflow replay；后续归因只使用历史现场证据。"
        return enriched

    try:
        token, auth_token_source = await resolve_workflow_auth_token(request.case_input.workspace_id)
    except Exception as exc:
        replay = WorkflowReplayEvidence(
            enabled=True,
            status="error",
            endpoint=endpoint,
            request_payload=request_payload,
            auth_token_source=auth_token_source,
            error=_safe_error(exc),
            notes="获取 workspace 级 workflow apiKey 失败，未调用 workflow。",
        )
        return _merge_replay_evidence(request, replay)

    if not token:
        replay = WorkflowReplayEvidence(
            enabled=False,
            status="not_configured",
            endpoint=endpoint,
            request_payload=request_payload,
            auth_token_source=auth_token_source,
            notes="未配置 OPEN_PLAT_ZS_OPEN_TOKEN 或 WORKFLOW_AUTH_TOKEN，跳过流水线重跑。",
        )
        return _merge_replay_evidence(request, replay)

    resolved_app: dict[str, Any] = {}
    input_schema: list[dict[str, Any]] = []
    try:
        resolved_app = resolve_workflow(request)
        input_schema = [item for item in resolved_app.get("input_schema", []) if isinstance(item, dict)]
        endpoint = _workflow_endpoint(resolved_app)
        request_payload, missing_fields = build_workflow_payload(request, input_schema)
        if missing_fields:
            replay = WorkflowReplayEvidence(
                enabled=True,
                status="error",
                endpoint=endpoint,
                request_payload=request_payload,
                resolved_app=resolved_app,
                input_schema=input_schema,
                auth_token_source=auth_token_source,
                error=f"Workflow Start schema contains unmapped required fields: {', '.join(missing_fields)}",
                notes="Workflow schema 输入缺少可映射字段，已停止重跑，避免伪造输入。",
            )
            return _merge_replay_evidence(request, replay)
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                endpoint,
                headers=_workflow_headers(token),
                json=request_payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Workflow replay HTTP {response.status_code}: {response.text[:500]}")
            response_payload = response.json()
            business_error = _business_error(response_payload)
            if business_error:
                replay = WorkflowReplayEvidence(
                    enabled=True,
                    status="error",
                    endpoint=endpoint,
                    request_payload=request_payload,
                    response_payload=_truncate(response_payload),
                    resolved_app=resolved_app,
                    input_schema=input_schema,
                    node_traces=_extract_node_traces(response_payload),
                    auth_token_source=auth_token_source,
                    error=business_error,
                    notes="Workflow 重跑返回业务失败，未构建出运行级证据。",
                )
                return _merge_replay_evidence(request, replay)
        extracted = _extract_evidence(response_payload)
        replay_status, replay_notes = _replay_status_and_notes(extracted)
        replay = WorkflowReplayEvidence(
            enabled=True,
            status=replay_status,
            endpoint=endpoint,
            request_payload=request_payload,
            response_payload=_truncate(response_payload),
            extracted_evidence=extracted,
            resolved_app=resolved_app,
            input_schema=input_schema,
            node_traces=extracted.get("node_traces", []) if isinstance(extracted.get("node_traces"), list) else [],
            auth_token_source=auth_token_source,
            notes=replay_notes,
        )
    except Exception as exc:
        replay = WorkflowReplayEvidence(
            enabled=True,
            status="error",
            endpoint=endpoint,
            request_payload=request_payload,
            resolved_app=resolved_app,
            input_schema=input_schema,
            auth_token_source=auth_token_source,
            error=_safe_error(exc),
            notes="Workflow 重跑失败，继续使用人工输入和已有证据归因。",
        )
    return _merge_replay_evidence(request, replay)
