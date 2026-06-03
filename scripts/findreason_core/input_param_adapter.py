from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .models import AdaptedInputFields, CaseInput, InputAdaptRequest, InputAdaptResponse, InputParamItem, WorkflowOverrides

QUERY_KEYS = {"query", "rankquery", "question", "input", "userquery", "oriquery", "originalquery"}
RETRIEVE_QUERY_KEYS = {"retrievequerylist", "rewritequerylist", "querylist", "queries", "additionalqueries"}
TOPK_KEYS = {"topk", "topn", "limit", "maxcount", "maxdocs", "count"}


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text or text in {"无", "未标注"}:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return _string_list(parsed)
    return [part.strip() for part in re.split(r"[,，;；\n]+", text) if part.strip()]


def _int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _decode_json_prefix(text: str, start: int) -> Any:
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[start:].lstrip())
    return value


def _field_value(block: str, field: str) -> Any:
    match = re.search(rf'"{re.escape(field)}"\s*:', block)
    if not match:
        return None
    try:
        return _decode_json_prefix(block, match.end())
    except (json.JSONDecodeError, ValueError):
        return None


def _object_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start : index + 1])
                start = None
    return blocks


def _param_from_mapping(value: dict[str, Any]) -> InputParamItem | None:
    key = value.get("key") or value.get("name") or value.get("field") or value.get("param_name") or value.get("paramName")
    if not key:
        return None
    param_type = value.get("type") or value.get("value_type") or value.get("valueType") or "String"
    return InputParamItem(key=str(key).strip(), type=str(param_type or "String").strip() or "String", value=value.get("value"))


def _strict_json_params(raw_input: str) -> list[InputParamItem]:
    try:
        parsed = json.loads(raw_input)
    except json.JSONDecodeError:
        return []
    candidate = parsed.get("input_params") if isinstance(parsed, dict) else parsed
    if isinstance(candidate, dict):
        candidate = [candidate]
    if not isinstance(candidate, list):
        return []
    params = [_param_from_mapping(item) for item in candidate if isinstance(item, dict)]
    return [param for param in params if param is not None]


def parse_input_params(raw_input: str) -> list[InputParamItem]:
    strict_params = _strict_json_params(raw_input.strip())
    if strict_params:
        return strict_params

    params: list[InputParamItem] = []
    for block in _object_blocks(raw_input):
        key = _field_value(block, "key") or _field_value(block, "name") or _field_value(block, "field")
        if not key:
            continue
        param_type = _field_value(block, "type") or "String"
        value = _field_value(block, "value")
        params.append(InputParamItem(key=str(key).strip(), type=str(param_type or "String").strip() or "String", value=value))
    return params


def _adapted_fields_from_params(
    request: InputAdaptRequest,
    params: list[InputParamItem],
    raw_case_input: dict[str, Any] | None = None,
) -> AdaptedInputFields:
    raw_case_input = raw_case_input or {}
    query = str(raw_case_input.get("query") or "").strip()
    retrieve_query_list = _string_list(raw_case_input.get("retrieve_query_list"))

    for param in params:
        canonical = _canonical_key(param.key)
        if not query and canonical in QUERY_KEYS:
            if isinstance(param.value, list):
                values = _string_list(param.value)
                query = values[0] if values else ""
            else:
                query = str(param.value or "").strip()
        if canonical in RETRIEVE_QUERY_KEYS:
            retrieve_query_list.extend(item for item in _string_list(param.value) if item not in retrieve_query_list)

    return AdaptedInputFields(
        query=query,
        judgement=request.judgement.strip(),
        workspace_id=request.workspace_id.strip(),
        app_id=request.app_id.strip(),
        retrieve_query_list=retrieve_query_list,
        case_id=request.case_id.strip() if request.case_id else None,
        source_row=request.source_row.strip() if request.source_row else None,
    )


def _case_input_from_fields(fields: AdaptedInputFields) -> CaseInput | None:
    if not fields.query or not fields.workspace_id or not fields.app_id:
        return None

    try:
        return CaseInput.model_validate(fields.model_dump(mode="json"))
    except ValidationError:
        return None


def _overrides_from_params(params: list[InputParamItem], raw_overrides: dict[str, Any] | None = None) -> WorkflowOverrides:
    topk = _int_value((raw_overrides or {}).get("topk"))
    for param in params:
        if topk is not None:
            break
        if _canonical_key(param.key) in TOPK_KEYS:
            topk = _int_value(param.value)
    return WorkflowOverrides(topk=topk)


def _response_from_params(
    request: InputAdaptRequest,
    *,
    params: list[InputParamItem],
    source: str,
    status: str,
    raw_case_input: dict[str, Any] | None = None,
    raw_overrides: dict[str, Any] | None = None,
    error: str | None = None,
    notes: str = "",
) -> InputAdaptResponse:
    adapted_fields = _adapted_fields_from_params(request, params, raw_case_input)
    case_input = _case_input_from_fields(adapted_fields)
    workflow_overrides = _overrides_from_params(params, raw_overrides)
    if not adapted_fields.query:
        status = "error"
        error = error or "无法从输入中解析出 query。"
    elif case_input is None:
        notes = notes or "已解析输入；补充 workspace_id 和 app_id 后可运行归因。"
    return InputAdaptResponse(
        status=status,
        source=source,
        case_input=case_input,
        adapted_fields=adapted_fields,
        input_params=params,
        workflow_overrides=workflow_overrides,
        error=error,
        notes=notes,
    )


def deterministic_adapt_input(request: InputAdaptRequest, *, error: str | None = None) -> InputAdaptResponse:
    params = parse_input_params(request.input)
    return _response_from_params(
        request,
        params=params,
        source="deterministic",
        status="fallback",
        error=error,
        notes="已使用本地 key/type/value 解析；宿主 Agent 可在调用前补齐更复杂的原始输入适配。",
    )


async def adapt_input(request: InputAdaptRequest) -> InputAdaptResponse:
    return deterministic_adapt_input(request)
