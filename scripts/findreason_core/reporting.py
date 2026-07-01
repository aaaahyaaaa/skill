from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .evidence_kernel import SCHEMA_VERSION, SKILL_RELEASE_MARKER, SKILL_RELEASE_POLICY, json_dumps, read_json_file, write_json


def _short(value: Any, limit: int = 700) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _doc_id(doc: dict[str, Any]) -> str:
    return str(doc.get("id") or doc.get("doc_id") or "").strip()


def _doc_aliases(doc: dict[str, Any]) -> list[str]:
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


def _knowledge_detail_entries(knowledge_detail: dict[str, Any]) -> list[dict[str, Any]]:
    entries = knowledge_detail.get("knowledge_details")
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, dict)]
    artifacts = knowledge_detail.get("artifacts") if isinstance(knowledge_detail.get("artifacts"), dict) else {}
    entries = artifacts.get("knowledge_detail_docs")
    return [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []


def _knowledge_detail_index(knowledge_detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in _knowledge_detail_entries(knowledge_detail):
        for alias in _doc_aliases(item):
            indexed.setdefault(alias, item)
    return indexed


def _doc_status_payload(doc: dict[str, Any], detail_index: dict[str, dict[str, Any]], detail_loaded: bool) -> dict[str, Any]:
    detail = next((detail_index.get(alias) for alias in _doc_aliases(doc) if detail_index.get(alias)), None)
    if not detail:
        return {
            "status_signals": [],
            "status_confirmed": False,
            "last_modified": "",
            "status_reason": "not_requested" if detail_loaded else "knowledge_detail_not_run",
        }
    return {
        "status_signals": detail.get("status_signals") if isinstance(detail.get("status_signals"), list) else [],
        "status_confirmed": bool(detail.get("status_confirmed")),
        "last_modified": str(detail.get("last_modified") or ""),
        "status_reason": str(detail.get("status_reason") or "status_unconfirmed"),
    }


def _doc_index_entries(
    source: str,
    docs: list[dict[str, Any]],
    *,
    detail_index: dict[str, dict[str, Any]] | None = None,
    detail_loaded: bool = False,
) -> list[dict[str, Any]]:
    detail_index = detail_index or {}
    entries: list[dict[str, Any]] = []
    for doc in docs:
        status_payload = _doc_status_payload(doc, detail_index, detail_loaded)
        entries.append(
            {
                "stage": source,
                "id": _doc_id(doc),
                "doc_id_aliases": _doc_aliases(doc),
                "title": _doc_title(doc),
                "url": _doc_url(doc),
                "snippet": _short(_doc_content(doc), 700),
                "rank": doc.get("rank"),
                "score": doc.get("score"),
                "source": doc.get("source"),
                **status_payload,
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
    mode = result.get("mode")
    status_text = f"{status}/{mode}" if mode else str(status)
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    if counts:
        return f"- {name}: `{status_text}`，counts=`{_compact_json(counts, 220)}`"
    return f"- {name}: `{status_text}`"


def _historical_trace_replay_skip(facts: dict[str, Any]) -> dict[str, Any]:
    trace = facts.get("trace") if isinstance(facts.get("trace"), dict) else {}
    if not trace.get("has_middle_node_trace"):
        return {}
    return {
        "status": "ok",
        "mode": "skipped_authoritative_trace",
        "counts": facts.get("counts") if isinstance(facts.get("counts"), dict) else {},
        "artifacts": {},
        "answer": "",
        "reasoning": "",
        "trace_completeness": {"replay_skipped": True, "historical_middle_node_trace": True},
        "notes": "Historical trace already has middle-node evidence; no empty replay_experiment.json is required.",
    }


def _effective_replay_for_summary(facts: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    return replay if replay else _historical_trace_replay_skip(facts)


def build_evidence_index(
    facts: dict[str, Any],
    *,
    recall: dict[str, Any] | None = None,
    rerank: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
    knowledge_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recall = recall or {}
    rerank = rerank or {}
    replay = _effective_replay_for_summary(facts, replay or {})
    knowledge_detail = knowledge_detail or {}
    detail_index = _knowledge_detail_index(knowledge_detail)
    detail_loaded = bool(knowledge_detail)
    historical_log_id = str(facts.get("log_id") or "")
    replay_log_ids = _find_replay_log_ids(replay, historical_log_id)
    docs: list[dict[str, Any]] = []
    for stage in ("origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"):
        docs.extend(_doc_index_entries(stage, _artifact_docs(facts, stage), detail_index=detail_index, detail_loaded=detail_loaded))
    docs.extend(
        _doc_index_entries(
            "recall_experiment.recall_docs",
            _experiment_artifacts(recall, "recall_docs"),
            detail_index=detail_index,
            detail_loaded=detail_loaded,
        )
    )
    for stage in ("origin_doc_list", "origin_faq_list", "rerank_docs", "prompt_docs"):
        docs.extend(
            _doc_index_entries(
                f"replay.{stage}",
                _experiment_artifacts(replay, stage),
                detail_index=detail_index,
                detail_loaded=detail_loaded,
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_release_marker": SKILL_RELEASE_MARKER,
        "skill_release_policy": SKILL_RELEASE_POLICY,
        "artifact_type": "evidence_index",
        "log_id": historical_log_id,
        "workspace_id": facts.get("workspace_id", ""),
        "app_id": facts.get("app_id", ""),
        "case_id": (facts.get("case") or {}).get("case_id") if isinstance(facts.get("case"), dict) else "",
        "replay_log_ids": replay_log_ids,
        "replay_status": replay.get("mode") or replay.get("status") or "not_run",
        "knowledge_detail_status": knowledge_detail.get("status") or "not_run",
        "knowledge_detail_counts": knowledge_detail.get("counts") if isinstance(knowledge_detail.get("counts"), dict) else {},
        "indexing_recommendation": "Use local JSON artifacts as the authoritative searchable evidence bundle; use Fornax by historical log_id for raw trace audit. Publish Markdown/Feishu docs for human review, not as the only evidence store.",
        "docs": docs,
        "report_rule": "Human reports must cite title/link/snippet. Doc ids may be shown for audit, but never as the only evidence.",
    }


def synthesize_report_markdown(
    facts: dict[str, Any],
    *,
    recall: dict[str, Any] | None = None,
    rerank: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
    knowledge_detail: dict[str, Any] | None = None,
    evidence_index_path: str = "evidence_index.json",
) -> str:
    recall = recall or {}
    rerank = rerank or {}
    replay = _effective_replay_for_summary(facts, replay or {})
    knowledge_detail = knowledge_detail or {}
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
    evaluator_text = "；".join(
        f"{item['signal']}={item['status']}：{item['reason']}" for item in evaluator_signals
    )
    if not evaluator_text:
        evaluator_text = "未提供评估器信号，当前只能基于 trace answer 和证据链做低/中置信判断。"
    answer_text = _short(facts.get("answer"), 700) or "未采集到历史答案。"
    recall_status = _experiment_status_line("recall experiment", recall).removeprefix("- ")
    rerank_status = _experiment_status_line("rerank experiment", rerank).removeprefix("- ")
    replay_status = _experiment_status_line("replay experiment", replay).removeprefix("- ")
    knowledge_status = _experiment_status_line("knowledge-detail experiment", knowledge_detail).removeprefix("- ")
    detail_entries = _knowledge_detail_entries(knowledge_detail)
    status_signal_docs = [
        item for item in detail_entries if isinstance(item.get("status_signals"), list) and item.get("status_signals")
    ]

    lines: list[str] = [
        "# FindReason Judgement",
        "",
        "## 当前判断",
        "",
        "这份是自动合成的短版结论草稿。CLI 只整理现场证据，不硬选 cause；Agent 需要基于下面的答案症状、上游链路和实验结果，把第一段改成最终 judgement。",
        "",
        f"这条 case 的问题是：{_short(query, 500) or '未采集到'}",
        "",
        f"历史答案是：{answer_text}",
        "",
        f"评估器线索：{evaluator_text}",
        "",
        "## 审计锚点",
        "",
        f"- log_id=`{historical_log_id}`，workspace_id=`{facts.get('workspace_id', '')}`，app_id=`{app_id}`，replay_log_id={replay_log_line}",
        f"- skill_release_marker=`{facts.get('skill_release_marker') or SKILL_RELEASE_MARKER}`",
        f"- Workflow 摘要：输入 `{_compact_json(workflow_input, 360)}`；输出 `{_compact_json(workflow_output, 360)}`",
        f"- 上游摘要：rewrite「{_short(preprocess.get('rewrite_query'), 200) or '未采集到'}」；keywords {keywords}",
        f"- 证据生存：召回 `{counts.get('recall', 0)}` 条，重排 `{counts.get('rerank_docs', 0)}` 条，进入 prompt `{counts.get('prompt_docs', 0)}` 条；重排缺失 {', '.join(map(str, missing_rerank)) or '无'}，prompt 缺失 {', '.join(map(str, missing_prompt)) or '无'}",
        "",
        "## 被评估目标",
        "",
        f"- 原始 query：{_short(query, 500) or '未采集到'}",
        f"- 被评估答案 / answer_hint：{_short(case.get('answer_hint'), 700) or answer_text}",
        f"- trace answer：{answer_text}",
        f"- replay answer：{_short(replay.get('answer'), 500) if replay.get('answer') else '未返回'}",
        f"- 实验状态：{recall_status}；{rerank_status}；{replay_status}；{knowledge_status}",
    ]
    lines.extend(["", "## 关键证据", ""])
    if key_docs:
        for doc in key_docs:
            lines.extend(_concise_doc_line(doc, "prompt/recall"))
    else:
        lines.append("- 未采集到可展示证据。")
    lines.extend(["", "## 知识状态信号", ""])
    if status_signal_docs:
        for item in status_signal_docs[:4]:
            lines.append(
                f"- {item.get('doc_id') or ''} {item.get('title') or '(untitled)'}：signals={', '.join(map(str, item.get('status_signals') or []))}；last_modified={item.get('last_modified') or '未提供'}"
            )
    elif knowledge_detail:
        lines.append("- 已尝试关键文档状态查询，但未确认停止更新/历史版本/过期等状态信号；未确认项在 evidence_index.json 中标记为 status_unconfirmed 或 not_requested。")
    else:
        lines.append("- 未运行 knowledge-detail；evidence_index.json 中的文档状态默认标记为 knowledge_detail_not_run。")
    lines.extend(
        [
            "",
            "## 评估器与复核",
            "",
            "最终报告以中文 cause 为主，旧 slug 只作为兼容别名：`输入侧问题` / `workflow_input_loss`、`知识缺失或证据不足` / `suspected_knowledge_missing`、`召回遗漏` / `retrieval_miss`、`重排丢失` / `rerank_drop`、`答案生成错误` / `answer_failure`、`无明显错误/评估器不准，需人工进一步核实` / `evaluator_disputed_no_obvious_error`。",
            "",
            "`输入侧问题` 只有在根据验证点改写后的 query 带来召回改善、排序改善，或 replay / 最终结果改善时，才能上调为主因；如果只是看起来 Workflow input、rewrite、keywords 少了信息，只能写成低置信候选或待验证点。",
            "",
            "`无明显错误/评估器不准，需人工进一步核实` 不能作为“看不出来”的兜底。若归到第 6 类，必须显式写出人工复核点；不要求固定范式，但要讲清楚为什么怀疑评估器不准、人工需要复核哪里、复核后可能如何改变结论。`评估器输出暂无` 本身不是第 6 类证据。",
            "",
            "`badcase_review_status` 独立于 cause；`not_badcase_evaluator_error` 只在人工确认或明确人工标注后使用。",
            "",
            "## 当前还不能直接裁决的地方",
            "",
            "自动合成只整理到证据层，还没有完成答案症状抽取和 required assertions 对齐，所以这里不直接给 `candidate_cause`。Prompt sufficiency gate 至少要分四档：`相关词命中`、`部分支撑`、`直接核心证据`、`冲突证据`。只有关键 required assertions 已在 prompt 中有 direct support 且 Workflow 输出仍漏答、错引、越界或编造，才适合把主因落到 `答案生成错误`；如果 prompt 只有相关词、泛化文档或冲突/过期风险，就应该回溯知识、recall、rerank 或人工复核评估器。",
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
    knowledge_detail = _load_experiment(source_dir / "knowledge_detail_experiment.json")
    target_dir.mkdir(parents=True, exist_ok=True)
    evidence_index = build_evidence_index(
        facts,
        recall=recall,
        rerank=rerank,
        replay=replay,
        knowledge_detail=knowledge_detail,
    )
    index_path = target_dir / "evidence_index.json"
    report_path = target_dir / "agent_judgement.md"
    write_json(index_path, evidence_index)
    report = synthesize_report_markdown(
        facts,
        recall=recall,
        rerank=rerank,
        replay=replay,
        knowledge_detail=knowledge_detail,
        evidence_index_path=index_path.name,
    )
    report_path.write_text(report, encoding="utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_release_marker": SKILL_RELEASE_MARKER,
        "skill_release_policy": SKILL_RELEASE_POLICY,
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
