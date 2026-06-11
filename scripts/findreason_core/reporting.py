from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .evidence_kernel import SCHEMA_VERSION, json_dumps, read_json_file, write_json


def _short(value: Any, limit: int = 700) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _doc_id(doc: dict[str, Any]) -> str:
    return str(doc.get("id") or doc.get("doc_id") or "").strip()


def _doc_title(doc: dict[str, Any]) -> str:
    return str(doc.get("title") or doc.get("doc_title") or doc.get("name") or "").strip()


def _doc_url(doc: dict[str, Any]) -> str:
    return str(doc.get("url") or doc.get("link") or doc.get("doc_url") or "").strip()


def _doc_content(doc: dict[str, Any]) -> str:
    return str(doc.get("content") or doc.get("content_preview") or doc.get("text") or "").strip()


def _artifact_docs(facts: dict[str, Any], key: str) -> list[dict[str, Any]]:
    artifacts = facts.get("artifacts") if isinstance(facts.get("artifacts"), dict) else {}
    docs = artifacts.get(key)
    return [item for item in docs if isinstance(item, dict)] if isinstance(docs, list) else []


def _experiment_artifacts(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
    docs = artifacts.get(key)
    return [item for item in docs if isinstance(item, dict)] if isinstance(docs, list) else []


def _selected_workflow_io(facts: dict[str, Any]) -> tuple[Any, Any]:
    trace = facts.get("trace") if isinstance(facts.get("trace"), dict) else {}
    ios = trace.get("workflow_span_ios")
    if not isinstance(ios, list):
        return "", ""
    selected = next((item for item in ios if isinstance(item, dict) and item.get("selected")), None)
    item = selected or next((item for item in ios if isinstance(item, dict)), {})
    if not isinstance(item, dict):
        return "", ""
    return item.get("input", ""), item.get("output", "")


def _code_block_json(value: Any, limit: int = 1800) -> list[str]:
    if value in ("", None, [], {}):
        return ["未采集到。"]
    text = json_dumps(value)
    if len(text) > limit:
        text = text[: limit - 20].rstrip() + "\n... <truncated>"
    return ["```json", text, "```"]


def _evaluator_summary(text: str) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    for match in re.finditer(r"([a-zA-Z_]+)=score:(\d+); reason:([^\n]+)", text or ""):
        score = match.group(2)
        signals.append(
            {
                "signal": match.group(1),
                "status": "pass" if score == "1" else "fail",
                "reason": _short(match.group(3), 240),
            }
        )
    if signals:
        return signals
    if text:
        return [{"signal": "raw_evaluator_note", "status": "observed", "reason": _short(text, 500)}]
    return []


def _format_doc(doc: dict[str, Any], stage: str) -> list[str]:
    doc_id = _doc_id(doc)
    title = _doc_title(doc) or "(untitled)"
    url = _doc_url(doc)
    snippet = _short(_doc_content(doc), 420)
    rank = doc.get("rank", "")
    heading = f"- {doc_id} {title}" if doc_id else f"- {title}"
    details = [heading]
    if url:
        details.append(f"  - 文档链接: [打开文档]({url})")
    else:
        details.append("  - 文档链接: 未提供")
    if rank not in ("", None):
        details.append(f"  - {stage} rank: `{rank}`")
    details.append(f"  - 援引片段: {snippet or '未提供'}")
    return details


def _doc_index_entries(source: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for doc in docs:
        entries.append(
            {
                "stage": source,
                "id": _doc_id(doc),
                "title": _doc_title(doc),
                "url": _doc_url(doc),
                "snippet": _short(_doc_content(doc), 700),
                "rank": doc.get("rank"),
                "score": doc.get("score"),
                "source": doc.get("source"),
            }
        )
    return entries


def _load_experiment(path: Path) -> dict[str, Any]:
    return read_json_file(str(path)) if path.exists() else {}


def _find_replay_log_ids(replay: dict[str, Any], historical_log_id: str) -> list[str]:
    found: list[str] = []
    keys = {"log_id", "logId", "logid", "trace_id", "traceId"}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, (str, int)):
                    candidate = str(value)
                    if candidate and candidate != historical_log_id and len(candidate) >= 12 and candidate not in found:
                        found.append(candidate)
                elif isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(replay)
    return found[:5]


def _top_docs_section(title: str, docs: list[dict[str, Any]], *, stage: str, limit: int = 6) -> list[str]:
    lines = [f"### {title}", ""]
    if not docs:
        lines.extend(["未采集到。", ""])
        return lines
    for doc in docs[:limit]:
        lines.extend(_format_doc(doc, stage))
    lines.append("")
    return lines


def _trace_summary(facts: dict[str, Any]) -> dict[str, Any]:
    trace = facts.get("trace") if isinstance(facts.get("trace"), dict) else {}
    summary = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}
    return summary


def _fact_app_id(facts: dict[str, Any]) -> str:
    direct = str(facts.get("app_id") or "").strip()
    if direct:
        return direct
    return str(_trace_summary(facts).get("app_id") or "").strip()


def _workflow_query(workflow_input: Any) -> str:
    if not isinstance(workflow_input, dict):
        return ""
    sys_input = workflow_input.get("sys") if isinstance(workflow_input.get("sys"), dict) else {}
    query = sys_input.get("query") or workflow_input.get("query")
    return str(query or "").strip()


def _case_query(facts: dict[str, Any], workflow_input: Any) -> str:
    case = facts.get("case") if isinstance(facts.get("case"), dict) else {}
    for candidate in (case.get("query"), case.get("query_hint"), _workflow_query(workflow_input), _trace_summary(facts).get("query")):
        text = str(candidate or "").strip()
        if text and text.lower() not in {"unknown query", "unknown"}:
            return text
    preprocess = facts.get("preprocess") if isinstance(facts.get("preprocess"), dict) else {}
    return str(preprocess.get("rewrite_query") or "").strip()


def _compact_json(value: Any, limit: int = 500) -> str:
    if value in ("", None, [], {}):
        return "未采集到。"
    return _short(json_dumps(value), limit)


def _dedupe_docs(docs: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        key = (_doc_id(doc), _doc_title(doc))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(doc)
        if len(chosen) >= limit:
            break
    return chosen


def _concise_doc_line(doc: dict[str, Any], stage: str) -> list[str]:
    doc_id = _doc_id(doc)
    title = _doc_title(doc) or "(untitled)"
    url = _doc_url(doc)
    snippet = _short(_doc_content(doc), 240)
    rank = doc.get("rank")
    rank_text = f"，{stage} rank={rank}" if rank not in ("", None) else ""
    heading = f"- {doc_id} {title}{rank_text}" if doc_id else f"- {title}{rank_text}"
    lines = [heading]
    if url:
        lines.append(f"  - 文档链接: [打开文档]({url})")
    elif snippet:
        lines.append("  - 文档链接: 未提供")
    if snippet:
        lines.append(f"  - 命中片段: {snippet}")
    else:
        lines.append("  - 命中片段: 未提供")
    return lines


def _experiment_status_line(name: str, result: dict[str, Any]) -> str:
    status = result.get("status") or "not_run"
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    if counts:
        return f"- {name}: `{status}`，counts=`{_compact_json(counts, 220)}`"
    return f"- {name}: `{status}`"


def build_evidence_index(
    facts: dict[str, Any],
    *,
    recall: dict[str, Any] | None = None,
    rerank: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recall = recall or {}
    rerank = rerank or {}
    replay = replay or {}
    historical_log_id = str(facts.get("log_id") or "")
    replay_log_ids = _find_replay_log_ids(replay, historical_log_id)
    docs: list[dict[str, Any]] = []
    for stage in ("origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"):
        docs.extend(_doc_index_entries(stage, _artifact_docs(facts, stage)))
    docs.extend(_doc_index_entries("recall_experiment.recall_docs", _experiment_artifacts(recall, "recall_docs")))
    for stage in ("origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"):
        docs.extend(_doc_index_entries(f"replay.{stage}", _experiment_artifacts(replay, stage)))
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "evidence_index",
        "log_id": historical_log_id,
        "workspace_id": facts.get("workspace_id", ""),
        "app_id": facts.get("app_id", ""),
        "case_id": (facts.get("case") or {}).get("case_id") if isinstance(facts.get("case"), dict) else "",
        "replay_log_ids": replay_log_ids,
        "indexing_recommendation": "Use local JSON artifacts as the authoritative searchable evidence bundle; use Fornax by historical log_id for raw trace audit. Publish Markdown/Feishu docs for human review, not as the only evidence store.",
        "sufficiency_review_contract": {
            "purpose": "Agent reviews whether evidence is enough to produce a rigorous business answer, not just whether it explains the attribution.",
            "support_levels": [
                "direct_support",
                "partial_support",
                "adjacent_support",
                "insufficient",
                "contradictory",
            ],
            "agent_fields": [
                "required_assertions",
                "supports_assertions",
                "support_level",
                "missing_authoritative_evidence",
            ],
        },
        "docs": docs,
        "report_rule": "Human reports must cite title/link/snippet. Doc ids may be shown for audit, but never as the only evidence.",
    }


def synthesize_report_markdown(
    facts: dict[str, Any],
    *,
    recall: dict[str, Any] | None = None,
    rerank: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
    evidence_index_path: str = "evidence_index.json",
) -> str:
    recall = recall or {}
    rerank = rerank or {}
    replay = replay or {}
    case = facts.get("case") if isinstance(facts.get("case"), dict) else {}
    counts = facts.get("counts") if isinstance(facts.get("counts"), dict) else {}
    preprocess = facts.get("preprocess") if isinstance(facts.get("preprocess"), dict) else {}
    citation = facts.get("citation_observations") if isinstance(facts.get("citation_observations"), dict) else {}
    workflow_input, workflow_output = _selected_workflow_io(facts)
    evaluator_signals = _evaluator_summary(str(case.get("judgement") or ""))
    historical_log_id = str(facts.get("log_id") or "")
    replay_log_ids = _find_replay_log_ids(replay, historical_log_id)
    replay_log_line = ", ".join(f"`{item}`" for item in replay_log_ids) if replay_log_ids else "未在 replay 响应中返回新的 log_id"
    app_id = _fact_app_id(facts)
    query = _case_query(facts, workflow_input)
    keywords = ", ".join(map(str, preprocess.get("keywords") or [])) or "未采集到"
    missing_rerank = rerank.get("missing_from_rerank") if isinstance(rerank.get("missing_from_rerank"), list) else []
    missing_prompt = rerank.get("missing_from_prompt") if isinstance(rerank.get("missing_from_prompt"), list) else []
    prompt_docs = _artifact_docs(facts, "prompt_docs")
    recall_docs = _artifact_docs(facts, "origin_doc_list") + _artifact_docs(facts, "origin_faq_list")
    replay_prompt_docs = _experiment_artifacts(replay, "prompt_docs")
    key_docs = _dedupe_docs(prompt_docs + replay_prompt_docs + recall_docs, limit=5)

    lines: list[str] = [
        "# FindReason Judgement Summary",
        "",
        "## 当前结论",
        "",
        "- candidate_cause: 待 Agent 判断",
        "- 置信度: 待 Agent 判断",
        "",
        "## Case 摘要",
        "",
        f"- log_id: `{historical_log_id}`",
        f"- workspace_id: `{facts.get('workspace_id', '')}`",
        f"- app_id: `{app_id}`",
        f"- replay_log_id: {replay_log_line}",
        f"- 用户问题: {_short(query, 500) or '未采集到'}",
        f"- workflow rewrite: {_short(preprocess.get('rewrite_query'), 700)}",
        f"- workflow keywords: {keywords}",
        f"- 包装后的输出 / answer_hint: {_short(case.get('answer_hint'), 500) or '未提供'}",
        "",
        "workflow 输入:",
        "",
        f"`{_compact_json(workflow_input, 600)}`",
        "",
        "workflow 输出:",
        "",
        f"`{_compact_json(workflow_output, 700)}`",
        "",
        "## 评估器信号",
        "",
    ]
    if evaluator_signals:
        for item in evaluator_signals:
            lines.append(f"- `{item['signal']}`: {item['status']}，{item['reason']}")
    else:
        lines.append("- 未提供评估器信号；最终判断需要 Agent 基于答案与证据对齐。")
    lines.extend(
        [
            "",
            "## 答案症状入口",
            "",
            f"- trace answer: {_short(facts.get('answer'), 700) or '未采集到'}",
            f"- wrong_citation_observed: `{citation.get('wrong_citation', False)}`",
            "- answer_issue_types: 待基于 trace answer、评估器信号和 prompt evidence 对齐后填写。",
            "",
            "## 上游证据链",
            "",
            f"- preprocess: rewrite=`{_short(preprocess.get('rewrite_query'), 180)}`；keywords={keywords}",
            f"- recall: `{counts.get('recall', 0)}`，origin_doc_list=`{counts.get('origin_doc_list', 0)}`，origin_faq_list=`{counts.get('origin_faq_list', 0)}`",
            f"- rerank_docs: `{counts.get('rerank_docs', 0)}`；prompt_docs: `{counts.get('prompt_docs', 0)}`",
            f"- rerank survival: missing_from_rerank={', '.join(map(str, missing_rerank)) or '无'}；missing_from_prompt={', '.join(map(str, missing_prompt)) or '无'}",
            _experiment_status_line("recall experiment", recall),
            _experiment_status_line("rerank experiment", rerank),
            _experiment_status_line("replay experiment", replay),
            f"- replay answer: {_short(replay.get('answer'), 500) or '未运行或未采集到'}",
            "",
            "## 关键证据",
            "",
        ]
    )
    if key_docs:
        for doc in key_docs:
            lines.extend(_concise_doc_line(doc, "prompt/recall"))
    else:
        lines.append("- 未采集到可展示证据。")
    lines.extend(
        [
            "",
            "## 证据充分性判断",
            "",
            "- required_assertions: 待 Agent 将用户问题拆成正确答案必须覆盖的断言。",
            "- support_level: 待对关键证据标注 `direct_support` / `partial_support` / `adjacent_support` / `insufficient` / `contradictory`。",
            "- missing_authoritative_evidence: 待说明当前证据组合仍缺哪条权威文档或业务规则。",
            "- 判断边界: replay 证据变好只能说明当前版本证据链改善；是否足以产出严谨业务答案，需要逐条断言检查。",
            "",
            "## 本地证据包",
            "",
            f"- `case_facts.json`: 历史 trace 事实和归一化 artifacts。",
            f"- `recall_experiment.json` / `rerank_experiment.json` / `replay_experiment.json`: 本次实验结果。",
            f"- `{evidence_index_path}`: 可索引证据包；给机器和复盘用，不要求人工逐行阅读。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def synthesize_brief(
    *,
    facts_file: str,
    output_dir: str | None = None,
    experiment_dir: str | None = None,
) -> dict[str, Any]:
    facts_path = Path(facts_file)
    target_dir = Path(output_dir) if output_dir else facts_path.parent
    source_dir = Path(experiment_dir) if experiment_dir else facts_path.parent
    facts = read_json_file(str(facts_path))
    recall = _load_experiment(source_dir / "recall_experiment.json")
    rerank = _load_experiment(source_dir / "rerank_experiment.json")
    replay = _load_experiment(source_dir / "replay_experiment.json")
    target_dir.mkdir(parents=True, exist_ok=True)
    evidence_index = build_evidence_index(facts, recall=recall, rerank=rerank, replay=replay)
    index_path = target_dir / "evidence_index.json"
    report_path = target_dir / "agent_judgement.md"
    write_json(index_path, evidence_index)
    report = synthesize_report_markdown(
        facts,
        recall=recall,
        rerank=rerank,
        replay=replay,
        evidence_index_path=index_path.name,
    )
    report_path.write_text(report, encoding="utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "synthesized_brief",
        "status": "ok",
        "facts_file": str(facts_path),
        "experiment_dir": str(source_dir),
        "outputs": {
            "agent_judgement": str(report_path),
            "evidence_index": str(index_path),
        },
        "replay_log_ids": evidence_index.get("replay_log_ids", []),
        "notes": "agent_judgement.md is a concise human summary draft; JSON artifacts remain the searchable evidence bundle for audit and reproduction.",
    }
