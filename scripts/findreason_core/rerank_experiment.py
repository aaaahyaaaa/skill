from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, Field

from .fornax_trace import (
    FornaxTraceIngestRequest,
    _decode_jsonish,
    _doc_id,
    _doc_score,
    _evidence_docs,
    _first_nonempty,
    _span_output,
    _trace_spans,
    ingest_fornax_trace,
    load_trace_file,
)
from .models import (
    AttributionRequest,
    RerankExperimentVariant,
    RerankParameterExperimentEvidence,
    RerankTargetObservation,
)


DEFAULT_RERANK_ENDPOINT_URL = "https://ad-sirius.bytedance.net/api/sirius_plugin/v1/rerank"
ALLOWED_TOP_LEVEL_PARAMS = {
    "qa_same_intent_min_score",
    "qa_diff_intent_min_score",
    "qa_business_intent_conveyed_business_doc",
    "qa_business_intent_conveyed_field_doc",
    "feature_complement_min_score",
    "feature_max_size",
    "feature_complement_max_business_size",
}
SECRET_HEADER_NAMES = {"authorization", "cookie", "x-jwt-token", "x-use-ppe", "x-tt-token"}


class RerankExperimentRequest(BaseModel):
    trace_file: str
    output_dir: str = ""
    target_doc_ids: list[str] = Field(default_factory=list)
    workspace_id: str = ""
    app_id: str = ""
    query: str = ""
    judgement: str = ""
    case_id: Optional[str] = None
    source_row: Optional[str] = None
    fornax_space_id: str = ""
    fornax_space_name: str = ""
    max_variants: int = 5
    endpoint: str = ""


class RerankExperimentResponse(BaseModel):
    attribution_request: AttributionRequest
    experiment: RerankParameterExperimentEvidence
    experiment_report_markdown: str = ""
    output_paths: dict[str, str] = Field(default_factory=dict)


def _safe_json_loads(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        escaped = re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", value)
        return json.loads(escaped)


def _decode_http_body(value: Any) -> Any:
    decoded = _decode_jsonish(value)
    if isinstance(decoded, str):
        stripped = decoded.strip()
        if stripped and stripped[0] in "[{":
            try:
                return _safe_json_loads(stripped)
            except Exception:
                return decoded
    return decoded


def _span_input(span: dict[str, Any] | None) -> Any:
    return _decode_jsonish((span or {}).get("input"))


def _find_rerank_http_span(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    for span in spans:
        if str(span.get("span_type") or span.get("type") or "") != "http_client":
            continue
        input_payload = _span_input(span)
        if not isinstance(input_payload, dict):
            continue
        url = str(input_payload.get("url") or "")
        if "/rerank" in url:
            return span
    return None


def _extract_original_rerank_request(spans: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    span = _find_rerank_http_span(spans)
    if not span:
        return None, {}
    input_payload = _span_input(span)
    if not isinstance(input_payload, dict):
        return None, {}
    body = _decode_http_body(input_payload.get("body"))
    if not isinstance(body, dict):
        return None, {}
    request_body = {
        key: deepcopy(body[key])
        for key in ("oriQuery", "recallDocs", "businessPostRequests", "params", "ruleRerankMode")
        if key in body
    }
    headers: dict[str, str] = {}
    for item in input_payload.get("headers") or []:
        if isinstance(item, dict):
            for key, value in item.items():
                if str(key).lower() in {"content-type", "x-tt-logid"} and value not in (None, ""):
                    headers[str(key)] = str(value)
    return request_body, headers


def _extract_baseline_docs(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    span = _find_rerank_http_span(spans)
    if span:
        output_payload = _span_output(span)
        if isinstance(output_payload, dict):
            response = _decode_http_body(output_payload.get("response"))
            if isinstance(response, dict) and isinstance(response.get("docs"), list):
                return [item for item in response["docs"] if isinstance(item, dict)]
    for candidate in spans:
        if str(candidate.get("span_type") or candidate.get("type") or "") == "ZhiShangRAGRerank":
            output = _span_output(candidate)
            if isinstance(output, dict) and isinstance(output.get("rerank_docs"), list):
                return [item for item in output["rerank_docs"] if isinstance(item, dict)]
    return []


def _baseline_parameters(request_body: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(request_body, dict):
        return {}
    params = request_body.get("params") if isinstance(request_body.get("params"), dict) else {}
    business_requests = request_body.get("businessPostRequests")
    post_params: list[dict[str, Any]] = []
    if isinstance(business_requests, list):
        for index, item in enumerate(business_requests):
            if not isinstance(item, dict):
                continue
            item_params = item.get("params") if isinstance(item.get("params"), dict) else {}
            if "min_score" in item_params:
                post_params.append(
                    {
                        "index": index,
                        "name": item.get("name") or item.get("recallPostStrategy") or "",
                        "min_score": item_params.get("min_score"),
                    }
                )
    return {
        "params": {key: value for key, value in params.items() if key in ALLOWED_TOP_LEVEL_PARAMS},
        "businessPostRequests": post_params,
        "ruleRerankMode": request_body.get("ruleRerankMode"),
    }


def _doc_keys(doc: dict[str, Any]) -> set[str]:
    values = [
        doc.get("id"),
        doc.get("identifier"),
        doc.get("doc_id"),
        doc.get("docId"),
        doc.get("knowledge_id"),
        doc.get("knowledgeId"),
        doc.get("chunkId"),
    ]
    return {str(value) for value in values if value not in (None, "")}


def _target_observations(docs: list[dict[str, Any]], target_doc_ids: list[str]) -> list[RerankTargetObservation]:
    observations: list[RerankTargetObservation] = []
    for target_id in target_doc_ids:
        target = str(target_id)
        matches: list[tuple[int, dict[str, Any]]] = []
        for index, doc in enumerate(docs, 1):
            if target in _doc_keys(doc):
                matches.append((index, doc))
        if not matches:
            observations.append(RerankTargetObservation(target_doc_id=target, survived=False))
            continue
        rank, doc = min(matches, key=lambda item: item[0])
        observations.append(
            RerankTargetObservation(
                target_doc_id=target,
                survived=True,
                best_rank=rank,
                best_score=_doc_score(doc),
                title=str(doc.get("title") or doc.get("doc_title") or ""),
            )
        )
    return observations


def _best_rank(observations: list[RerankTargetObservation]) -> int | None:
    ranks = [item.best_rank for item in observations if item.best_rank is not None]
    return min(ranks) if ranks else None


def _best_score(observations: list[RerankTargetObservation]) -> float | None:
    scores = [item.best_score for item in observations if item.best_score is not None]
    return max(scores) if scores else None


def _variant_from_docs(
    variant_id: str,
    description: str,
    parameter_diff: dict[str, Any],
    parameters: dict[str, Any],
    docs: list[dict[str, Any]],
    target_doc_ids: list[str],
    baseline_rank: int | None = None,
    baseline_score: float | None = None,
    status: str = "ok",
    error: str | None = None,
) -> RerankExperimentVariant:
    observations = _target_observations(docs, target_doc_ids)
    best_rank = _best_rank(observations)
    best_score = _best_score(observations)
    rank_lift = baseline_rank - best_rank if baseline_rank is not None and best_rank is not None else None
    score_lift = best_score - baseline_score if baseline_score is not None and best_score is not None else None
    return RerankExperimentVariant(
        variant_id=variant_id,
        description=description,
        parameter_diff=parameter_diff,
        parameters=parameters,
        status=status,
        target_observations=observations,
        survived_target_count=sum(1 for item in observations if item.survived),
        best_rank=best_rank,
        best_score=best_score,
        rank_lift=rank_lift,
        score_lift=score_lift,
        top_doc_ids=[str(_doc_id(doc) or "") for doc in docs[:10] if _doc_id(doc)],
        error=error,
    )


def _set_param(body: dict[str, Any], key: str, value: str) -> bool:
    params = body.setdefault("params", {})
    if not isinstance(params, dict):
        body["params"] = {}
        params = body["params"]
    if params.get(key) == value:
        return False
    params[key] = value
    return True


def _set_post_min_score(body: dict[str, Any], value: str) -> bool:
    changed = False
    requests = body.get("businessPostRequests")
    if not isinstance(requests, list):
        return False
    for item in requests:
        if not isinstance(item, dict):
            continue
        params = item.get("params")
        if not isinstance(params, dict) or "min_score" not in params:
            continue
        if params.get("min_score") != value:
            params["min_score"] = value
            changed = True
    return changed


def _make_variants(request_body: dict[str, Any], max_variants: int) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    if max_variants <= 0:
        return []
    definitions = [
        (
            "lower_feature_complement_min_score",
            "Lower feature complement minimum score.",
            lambda body: _set_param(body, "feature_complement_min_score", "0.0"),
            {"params.feature_complement_min_score": "0.0"},
        ),
        (
            "lower_qa_intent_thresholds",
            "Lower QA same/different intent minimum scores.",
            lambda body: any(
                [
                    _set_param(body, "qa_same_intent_min_score", "0.0"),
                    _set_param(body, "qa_diff_intent_min_score", "0.0"),
                ]
            ),
            {"params.qa_same_intent_min_score": "0.0", "params.qa_diff_intent_min_score": "0.0"},
        ),
        (
            "increase_feature_doc_limits",
            "Increase feature document limits.",
            lambda body: any(
                [
                    _set_param(body, "feature_max_size", "10"),
                    _set_param(body, "feature_complement_max_business_size", "15"),
                ]
            ),
            {"params.feature_max_size": "10", "params.feature_complement_max_business_size": "15"},
        ),
        (
            "increase_business_conveyed_docs",
            "Increase business and field document guarantees.",
            lambda body: any(
                [
                    _set_param(body, "qa_business_intent_conveyed_business_doc", "3"),
                    _set_param(body, "qa_business_intent_conveyed_field_doc", "3"),
                ]
            ),
            {"params.qa_business_intent_conveyed_business_doc": "3", "params.qa_business_intent_conveyed_field_doc": "3"},
        ),
        (
            "relax_post_rerank_min_score",
            "Relax post-rerank minimum score.",
            lambda body: _set_post_min_score(body, "0.3"),
            {"businessPostRequests[].params.min_score": "0.3"},
        ),
    ]
    variants: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for variant_id, description, apply_patch, diff in definitions:
        body = deepcopy(request_body)
        if apply_patch(body):
            variants.append((variant_id, description, diff, body))
        if len(variants) >= max_variants:
            break
    return variants


def _extract_response_docs(payload: Any) -> list[dict[str, Any]]:
    decoded = _decode_http_body(payload)
    if isinstance(decoded, dict):
        if isinstance(decoded.get("docs"), list):
            return [item for item in decoded["docs"] if isinstance(item, dict)]
        response = _decode_http_body(decoded.get("response"))
        if isinstance(response, dict) and isinstance(response.get("docs"), list):
            return [item for item in response["docs"] if isinstance(item, dict)]
    return []


def _headers_from_env(original_headers: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for key, value in original_headers.items():
        if str(key).lower() == "x-tt-logid" and value:
            headers["x-tt-logid"] = str(value)
    raw = os.getenv("RERANK_ENDPOINT_HEADERS_JSON", "").strip()
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if value not in (None, ""):
                    headers[str(key)] = str(value)
    return headers


def _redaction_values(headers: dict[str, str]) -> list[str]:
    values = []
    for key, value in headers.items():
        if str(key).lower() in SECRET_HEADER_NAMES and value:
            values.append(str(value))
    raw = os.getenv("RERANK_ENDPOINT_HEADERS_JSON", "")
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            values.extend(str(value) for value in parsed.values() if value)
    return values


def _redact_text(text: str, values: list[str]) -> str:
    result = text
    for value in values:
        if value:
            result = result.replace(value, "[REDACTED]")
    return result


def _safe_error(exc: Exception, values: list[str]) -> str:
    return _redact_text(str(exc) or repr(exc), values)[:500]


def _run_variant_http(endpoint: str, headers: dict[str, str], body: dict[str, Any]) -> list[dict[str, Any]]:
    with httpx.Client(timeout=30) as client:
        response = client.post(endpoint, headers=headers, json=body)
    if response.status_code >= 400:
        raise RuntimeError(f"rerank HTTP {response.status_code}: {response.text[:300]}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"rerank returned non-json response: {response.text[:300]}") from exc
    return _extract_response_docs(payload)


def _choose_target_doc_ids(
    request: AttributionRequest,
    explicit_target_doc_ids: list[str],
) -> list[str]:
    expected = [str(item) for item in request.case_input.expected_knowledge_ids if str(item).strip()]
    if expected:
        return expected
    selected = request.workflow_replay.extracted_evidence.get("selected_workflow_segment")
    if isinstance(selected, dict):
        dropped = selected.get("dropped_relevant_docs")
        if isinstance(dropped, list):
            ids = [str(item.get("id")) for item in dropped if isinstance(item, dict) and item.get("id")]
            if ids:
                return ids
    return [str(item) for item in explicit_target_doc_ids if str(item).strip()]


def run_rerank_experiment(
    request: RerankExperimentRequest,
    *,
    post_variant: Callable[[str, dict[str, str], dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> RerankExperimentResponse:
    payload = load_trace_file(request.trace_file)
    ingest_request = FornaxTraceIngestRequest(
        trace_file=request.trace_file,
        workspace_id=request.workspace_id,
        app_id=request.app_id,
        query=request.query,
        judgement=request.judgement,
        case_id=request.case_id,
        source_row=request.source_row,
        fornax_space_id=request.fornax_space_id,
        fornax_space_name=request.fornax_space_name,
    )
    ingest_response = ingest_fornax_trace(payload, ingest_request)
    attribution_request = ingest_response.attribution_request
    spans = _trace_spans(payload)
    target_doc_ids = _choose_target_doc_ids(attribution_request, request.target_doc_ids)
    endpoint = request.endpoint or os.getenv("RERANK_ENDPOINT_URL", DEFAULT_RERANK_ENDPOINT_URL)
    original_request, original_headers = _extract_original_rerank_request(spans)
    baseline_docs = _extract_baseline_docs(spans)
    baseline_parameters = _baseline_parameters(original_request)
    baseline = _variant_from_docs(
        "baseline",
        "Original rerank result from trace.",
        {},
        baseline_parameters,
        baseline_docs,
        target_doc_ids,
        status="ok" if baseline_docs else "missing",
    )
    experiment = RerankParameterExperimentEvidence(
        enabled=bool(original_request),
        status="not_run",
        endpoint=endpoint,
        baseline_parameters=baseline_parameters,
        baseline=baseline,
        target_doc_ids=target_doc_ids,
        notes="",
    )

    if not original_request:
        experiment.status = "missing_rerank_request"
        experiment.parameter_issue_supported = False
        experiment.notes = "Trace does not contain a /rerank http_client span with a JSON request body."
        attribution_request.rerank.parameter_experiment = experiment
        return _response(attribution_request, experiment, request.output_dir)
    if not target_doc_ids:
        experiment.status = "missing_target_doc"
        experiment.parameter_issue_supported = False
        experiment.notes = "No target doc id found from expected_knowledge_ids, dropped relevant docs, or --target-doc-id."
        attribution_request.rerank.parameter_experiment = experiment
        return _response(attribution_request, experiment, request.output_dir)

    headers = _headers_from_env(original_headers)
    redactions = _redaction_values(headers)
    post = post_variant or _run_variant_http
    variants: list[RerankExperimentVariant] = []
    for variant_id, description, diff, body in _make_variants(original_request, request.max_variants):
        try:
            docs = post(endpoint, headers, body)
            variants.append(
                _variant_from_docs(
                    variant_id,
                    description,
                    diff,
                    _baseline_parameters(body),
                    docs,
                    target_doc_ids,
                    baseline_rank=baseline.best_rank,
                    baseline_score=baseline.best_score,
                )
            )
        except Exception as exc:
            variants.append(
                RerankExperimentVariant(
                    variant_id=variant_id,
                    description=description,
                    parameter_diff=diff,
                    parameters=_baseline_parameters(body),
                    status="error",
                    target_observations=[RerankTargetObservation(target_doc_id=target_id) for target_id in target_doc_ids],
                    error=_safe_error(exc, redactions),
                )
            )

    experiment.variants = variants
    successful = [variant for variant in variants if variant.status == "ok"]
    baseline_survived = baseline.survived_target_count > 0
    lifted = [
        variant
        for variant in successful
        if variant.survived_target_count > baseline.survived_target_count
        or (variant.rank_lift is not None and variant.rank_lift > 0)
        or (variant.score_lift is not None and variant.score_lift > 0)
    ]
    experiment.best_variant = max(
        lifted,
        key=lambda item: (
            item.survived_target_count,
            item.rank_lift if item.rank_lift is not None else -9999,
            item.score_lift if item.score_lift is not None else -9999.0,
        ),
        default=None,
    )
    experiment.parameter_issue_supported = bool(not baseline_survived and experiment.best_variant and experiment.best_variant.survived_target_count > 0)
    if experiment.best_variant:
        experiment.status = "supported" if experiment.parameter_issue_supported else "lift_observed"
        experiment.notes = f"Best variant {experiment.best_variant.variant_id} improved target document survival/ranking."
    elif any(variant.status == "error" for variant in variants) and not successful:
        experiment.status = "error"
        experiment.parameter_issue_supported = False
        experiment.notes = "All rerank parameter variants failed; experiment could not validate parameter impact."
    else:
        experiment.status = "no_lift"
        experiment.parameter_issue_supported = False
        experiment.notes = "Rerank parameter variants did not improve target document survival or ranking."
    attribution_request.rerank.parameter_experiment = experiment
    return _response(attribution_request, experiment, request.output_dir)


def _response(
    attribution_request: AttributionRequest,
    experiment: RerankParameterExperimentEvidence,
    output_dir: str = "",
) -> RerankExperimentResponse:
    report = render_rerank_experiment_report(experiment)
    paths: dict[str, str] = {}
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        experiment_path = out / "rerank_experiment.json"
        case_path = out / "case_with_rerank_experiment.json"
        report_path = out / "rerank_experiment_report.md"
        experiment_path.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
        case_path.write_text(attribution_request.model_dump_json(indent=2), encoding="utf-8")
        report_path.write_text(report, encoding="utf-8")
        paths = {
            "rerank_experiment_path": str(experiment_path),
            "case_with_rerank_experiment_path": str(case_path),
            "rerank_experiment_report_path": str(report_path),
        }
    return RerankExperimentResponse(
        attribution_request=attribution_request,
        experiment=experiment,
        experiment_report_markdown=report,
        output_paths=paths,
    )


def render_rerank_experiment_report(experiment: RerankParameterExperimentEvidence) -> str:
    lines = [
        "# Rerank Parameter Experiment",
        "",
        f"- status: `{experiment.status}`",
        f"- endpoint: `{experiment.endpoint}`",
        f"- target_doc_ids: `{', '.join(experiment.target_doc_ids)}`",
        f"- parameter_issue_supported: `{experiment.parameter_issue_supported}`",
        "",
        "## Baseline",
        "",
        f"- parameters: `{json.dumps(experiment.baseline_parameters, ensure_ascii=False)}`",
    ]
    if experiment.baseline:
        lines.append(f"- survived_target_count: `{experiment.baseline.survived_target_count}`")
        lines.append(f"- best_rank: `{experiment.baseline.best_rank}`")
        lines.append(f"- best_score: `{experiment.baseline.best_score}`")
    lines.extend(["", "## Variants", "", "| variant | status | survived | rank_lift | score_lift | diff |", "|---|---|---:|---:|---:|---|"])
    for variant in experiment.variants:
        lines.append(
            f"| `{variant.variant_id}` | `{variant.status}` | {variant.survived_target_count} | "
            f"{variant.rank_lift if variant.rank_lift is not None else ''} | "
            f"{variant.score_lift if variant.score_lift is not None else ''} | "
            f"`{json.dumps(variant.parameter_diff, ensure_ascii=False)}` |"
        )
    if experiment.best_variant:
        lines.extend(["", "## Best Variant", "", f"- `{experiment.best_variant.variant_id}`: {experiment.best_variant.description}"])
    if experiment.notes:
        lines.extend(["", "## Notes", "", experiment.notes])
    return "\n".join(lines).rstrip() + "\n"
