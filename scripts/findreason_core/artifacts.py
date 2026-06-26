from __future__ import annotations

import json
import re
from typing import Any, Iterable


DOC_ID_KEYS = ("id", "doc_id", "docId", "record_id", "recordId", "identifier", "knowledge_id", "knowledgeId")
DOC_TITLE_KEYS = ("title", "doc_title", "docTitle", "name")
DOC_CONTENT_KEYS = ("content", "text", "chunk", "chunk_text", "chunkText", "summary", "description")
DOC_URL_KEYS = ("url", "link", "doc_url", "docUrl", "source_url", "sourceUrl")
DOC_SCORE_KEYS = (
    "score",
    "rerank_score",
    "rerankScore",
    "similarity",
    "rank_score",
    "rankScore",
    "fineScore",
    "fine_score",
    "recallScore",
    "recall_score",
)


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): to_jsonable(child) for key, child in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def decode_jsonish(value: Any) -> Any:
    current = value
    for _ in range(5):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text:
            return current
        if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
            return current
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            return current
    return current


def first_present(mapping: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _doc_aliases(item: dict[str, Any], primary_id: str) -> list[str]:
    aliases: list[str] = []

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        text = str(value).strip()
        if text and text not in aliases:
            aliases.append(text)

    add(primary_id)
    for key in DOC_ID_KEYS:
        add(item.get(key))
    raw_aliases = item.get("doc_id_aliases") or item.get("docIdAliases") or item.get("aliases")
    if isinstance(raw_aliases, list):
        for value in raw_aliases:
            add(value)
    source_text = str(item.get("source") or "")
    for match in re.finditer(r"(?:identifier|id|doc_id|record_id|knowledge_id)=([^|,\s]+)", source_text):
        add(match.group(1))
    return aliases


def normalize_doc(value: Any, *, source: str = "", rank: int | None = None) -> dict[str, Any] | None:
    item = to_jsonable(decode_jsonish(value))
    if not isinstance(item, dict):
        return None
    doc_id = str(first_present(item, DOC_ID_KEYS) or "").strip()
    title = str(first_present(item, DOC_TITLE_KEYS) or "").strip()
    content = str(first_present(item, DOC_CONTENT_KEYS) or "").strip()
    url = str(first_present(item, DOC_URL_KEYS) or "").strip()
    score = _float_or_none(first_present(item, DOC_SCORE_KEYS))
    resolved_source = str(source or item.get("source") or item.get("source_type") or "").strip()
    if not any((doc_id, title, content, url)):
        return None
    raw_rank = _float_or_none(item.get("rank"))
    return {
        "id": doc_id,
        "doc_id_aliases": _doc_aliases(item, doc_id),
        "title": title,
        "content": content,
        "url": url,
        "rank": raw_rank if raw_rank is not None else rank,
        "score": score,
        "source": resolved_source,
        "raw_keys": sorted(str(key) for key in item.keys())[:40],
    }


def normalize_docs(values: Any, *, source: str = "") -> list[dict[str, Any]]:
    decoded = to_jsonable(decode_jsonish(values))
    if not isinstance(decoded, list):
        decoded = [decoded] if isinstance(decoded, dict) else []
    docs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, value in enumerate(decoded, start=1):
        doc = normalize_doc(value, source=source, rank=index)
        if not doc:
            continue
        key = (str(doc.get("id") or ""), str(doc.get("title") or ""), str(doc.get("content") or "")[:200])
        if key in seen:
            continue
        seen.add(key)
        docs.append(doc)
    return docs


def artifact_counts(artifacts: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    origin_doc_count = len(artifacts.get("origin_doc_list") or [])
    origin_faq_count = len(artifacts.get("origin_faq_list") or [])
    return {
        "origin_doc_list": origin_doc_count,
        "origin_faq_list": origin_faq_count,
        "recall": origin_doc_count + origin_faq_count,
        "rerank_docs": len(artifacts.get("rerank_docs") or []),
        "prompt_docs": len(artifacts.get("prompt_docs") or []),
    }


def normalize_rag_artifacts(
    *,
    origin_doc_list: Any = None,
    origin_faq_list: Any = None,
    rerank_docs: Any = None,
    prompt_docs: Any = None,
) -> dict[str, Any]:
    artifacts = {
        "origin_doc_list": normalize_docs(origin_doc_list or [], source="origin_doc_list"),
        "origin_faq_list": normalize_docs(origin_faq_list or [], source="origin_faq_list"),
        "rerank_docs": normalize_docs(rerank_docs or [], source="rerank_docs"),
        "prompt_docs": normalize_docs(prompt_docs or [], source="prompt_docs"),
    }
    return {
        "artifacts": artifacts,
        "counts": artifact_counts(artifacts),
        "recall_field_note": "recall is a human-facing alias for origin_doc_list + origin_faq_list; raw trace field names are preserved for audit.",
    }
