# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Read-only, resumable complete-project review orchestration."""

from __future__ import annotations

from copy import deepcopy
import inspect
import json
from typing import Any, Awaitable, Callable, Mapping


JsonCallback = Callable[[dict[str, Any]], Any | Awaitable[Any]]
_VERDICTS = frozenset({"通过", "建议修改"})
_SEVERITIES = frozenset({"P0", "P1", "P2"})


def unit_key(stage_kind: str, episode_number: int) -> str:
    return f"{stage_kind}:{int(episode_number)}"


def normalize_unit_result(payload: dict, *, stage_kind: str, episode_number: int) -> dict:
    allowed = {"P0", "P1", "P2"}
    findings = []
    for item in payload.get("findings") or []:
        if not isinstance(item, dict):
            item = {}
        severity = str(item.get("severity") or "P2").upper()
        findings.append({
            "severity": severity if severity in allowed else "P2",
            "category": str(item.get("category") or "continuity"),
            "resource_id": item.get("resource_id"),
            "version": item.get("version"),
            "field": str(item.get("field") or "content"),
            "evidence": str(item.get("evidence") or "证据不足"),
            "suggestion": str(item.get("suggestion") or ""),
        })
    return {
        "stage_kind": stage_kind,
        "episode_number": int(episode_number),
        "summary": str(payload.get("summary") or ""),
        "findings": findings,
        "continuity_state": payload.get("continuity_state") or {},
    }


def build_unit_messages(
    source: dict[str, Any],
    *,
    stage_kind: str,
    episode_number: int,
    checkpoint: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build one bounded, read-only unit-review request from the frozen source."""
    unit = _unit_for(source, stage_kind, episode_number)
    state = _mapping((checkpoint or {}).get("continuity_state"))
    payload = {
        "request_kind": "unit_review",
        "stage_kind": stage_kind,
        "current_unit": deepcopy(unit),
        "loaded_resource_refs": _loaded_resource_refs(source, unit),
        "checkpoint_context": deepcopy(state),
    }
    contract = {
        "summary": "本集审核摘要",
        "findings": [{
            "severity": "P0/P1/P2",
            "category": "continuity/character/pacing/logic",
            "resource_id": "来源资源 ID",
            "version": "来源资源版本",
            "field": "内容字段",
            "evidence": "仅引用输入中的证据",
            "suggestion": "可执行建议",
        }],
        "continuity_state": {"unresolved_conflicts": ["待跟踪问题"]},
    }
    return [
        {
            "role": "system",
            "content": (
                "你是完整项目的只读编剧审核员。只能基于当前工作单元和有界连续性上下文分析，"
                "不得编造未提供的内容，不得改写来源内容。每条 finding 必须输出 resource_id、"
                "version、field、evidence 和 suggestion。只返回 JSON："
                + json.dumps(contract, ensure_ascii=False)
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def build_batch_summary_messages(
    source: dict[str, Any],
    *,
    stage_kind: str,
    batch_index: int,
    episode_numbers: list[int],
    unit_results: list[dict[str, Any]],
    checkpoint: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build a compact summary request for one already-reviewed source batch."""
    payload = {
        "request_kind": "batch_summary",
        "stage_kind": stage_kind,
        "batch_index": int(batch_index),
        "episode_numbers": [int(number) for number in episode_numbers],
        "unit_results": deepcopy(unit_results),
        "loaded_resource_refs": _stage_refs(source, stage_kind, episode_numbers),
        "checkpoint_context": deepcopy(_mapping((checkpoint or {}).get("continuity_state"))),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是只读项目审核员。根据本批次已审核结果提炼跨集问题；不得改写来源内容。"
                "只返回 JSON："
                + json.dumps({
                    "summary": "批次摘要",
                    "findings": [{"severity": "P0/P1/P2", "evidence": "批次内证据", "suggestion": "建议"}],
                    "continuity_state": {"unresolved_conflicts": ["待跟踪问题"]},
                }, ensure_ascii=False)
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def build_final_summary_messages(
    source: dict[str, Any], *, batch_summaries: list[dict[str, Any]], checkpoint: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    """Build a final request from compact batch summaries, never source bodies."""
    payload = {
        "request_kind": "final_summary",
        "audit_scope": str(source.get("audit_scope") or ""),
        "source_manifest": deepcopy(_mapping(source.get("manifest"))),
        "batch_summaries": deepcopy(batch_summaries),
        "upstream_mismatches": deepcopy(_mappings(source.get("upstream_mismatches"))),
        "checkpoint_context": deepcopy(_mapping((checkpoint or {}).get("continuity_state"))),
    }
    return [
        {
            "role": "system",
            "content": (
                "你是只读项目审核员。基于批次摘要和来源清单输出最终结论，"
                "不得改写来源内容。只返回 JSON："
                + json.dumps({
                    "ai_verdict": "通过/建议修改",
                    "summary": "项目总评",
                    "core_findings": [{"severity": "P0/P1/P2", "evidence": "跨集证据", "suggestion": "建议"}],
                    "continuity_state": {"unresolved_conflicts": ["待跟踪问题"]},
                }, ensure_ascii=False)
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def review_project_source(
    provider: Any,
    source: dict[str, Any],
    *,
    on_checkpoint: JsonCallback | None = None,
    on_request: JsonCallback | None = None,
    initial_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review frozen work units serially and checkpoint each durable result."""
    report = _resume_report(source, initial_report)
    for stage_kind, stage in _stages(source):
        for batch_index, batch in enumerate(_batches(stage), start=1):
            batch_results: list[dict[str, Any]] = []
            for episode_number in batch:
                key = unit_key(stage_kind, episode_number)
                saved = _saved_unit(report, key)
                if saved is not None:
                    batch_results.append(saved)
                    continue
                messages = build_unit_messages(
                    source,
                    stage_kind=stage_kind,
                    episode_number=episode_number,
                    checkpoint=report,
                )
                await _call(on_request, _request_event("unit", stage_kind, episode_number, batch_index))
                response = await provider.complete_json(messages)
                result = normalize_unit_result(
                    response if isinstance(response, dict) else {},
                    stage_kind=stage_kind,
                    episode_number=episode_number,
                )
                report["stage_results"][stage_kind].append(result)
                report["all_unit_results"].append(result)
                batch_results.append(result)
                _advance_continuity(report, result)
                _set_cursor(report, source)
                report["checkpoint_key"] = f"unit:{key}"
                await _call(on_checkpoint, deepcopy(report))

            summary_key = _batch_key(stage_kind, batch_index)
            if not _has_batch_summary(report, summary_key):
                messages = build_batch_summary_messages(
                    source,
                    stage_kind=stage_kind,
                    batch_index=batch_index,
                    episode_numbers=batch,
                    unit_results=batch_results,
                    checkpoint=report,
                )
                await _call(on_request, _request_event("batch_summary", stage_kind, 0, batch_index))
                response = await provider.complete_json(messages)
                summary = _normalize_batch_summary(
                    response if isinstance(response, dict) else {}, stage_kind, batch_index, batch
                )
                report["batch_summaries"].append(summary)
                _advance_continuity(report, summary)
                _set_cursor(report, source)
                report["checkpoint_key"] = f"batch_summary:{summary_key}"
                await _call(on_checkpoint, deepcopy(report))

    if not _has_final_summary(report):
        messages = build_final_summary_messages(source, batch_summaries=report["batch_summaries"], checkpoint=report)
        await _call(on_request, _request_event("final_summary", "", 0, 0))
        response = await provider.complete_json(messages)
        final = _normalize_final_summary(response if isinstance(response, dict) else {})
        report.update(final)
        _advance_continuity(report, final)
        report["partial"] = False
        report["cursor"] = {"stage_kind": "", "episode_number": 0, "batch_index": 0}
        report["checkpoint_key"] = "final_summary"
        await _call(on_checkpoint, deepcopy(report))

    return normalize_project_review_report(report, source)


def normalize_project_review_report(report: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Return the stable export contract without modifying source snapshot content."""
    normalized = _resume_report(source, report)
    if isinstance(report, dict) and _has_final_summary(report):
        normalized.update(_normalize_final_summary(report))
    normalized["partial"] = bool(report.get("partial")) if isinstance(report, dict) else True
    normalized["source_manifest"] = deepcopy(_mapping(source.get("manifest")))
    normalized["upstream_mismatches"] = deepcopy(_mappings(source.get("upstream_mismatches")))
    normalized["source_fingerprint"] = str(source.get("fingerprint") or "")
    for stage_kind in ("outline", "script"):
        normalized["stage_results"][stage_kind].sort(key=lambda item: int(item["episode_number"]))
    normalized["all_unit_results"] = [
        item
        for stage_kind in ("outline", "script")
        for item in normalized["stage_results"][stage_kind]
    ]
    return normalized


def build_project_review_markdown(report: dict[str, Any]) -> str:
    """Render the normalized report with source traceability and evidence."""
    manifest = _mapping(report.get("source_manifest"))
    project = _mapping(manifest.get("project"))
    lines = [
        "# 完整项目审核报告",
        "",
        f"**项目：** {project.get('name') or manifest.get('project_name') or '未命名项目'}",
        f"**范围：** {report.get('audit_scope') or manifest.get('audit_scope') or ''}",
        f"**快照指纹：** {report.get('source_fingerprint') or ''}",
        f"**AI 结论：** {report.get('ai_verdict') or '建议修改'}",
        "",
        "## 总评",
        "",
        str(report.get("summary") or ""),
        "",
        "## 核心问题",
        "",
    ]
    for finding in _mappings(report.get("core_findings")):
        lines.append(_finding_line(finding))
    for stage_kind in ("outline", "script"):
        lines.extend(["", f"## {stage_kind} 逐集结果", ""])
        for item in _mappings(_mapping(report.get("stage_results")).get(stage_kind)):
            lines.extend([f"### 第{item.get('episode_number')}集", "", str(item.get("summary") or "")])
            for finding in _mappings(item.get("findings")):
                lines.append(_finding_line(finding))
            lines.append("")
    lines.extend(["## 跨集问题", ""])
    for summary in _mappings(report.get("batch_summaries")):
        lines.append(f"- {summary.get('stage_kind')} 批次 {summary.get('batch_index')}：{summary.get('summary') or ''}")
        for finding in _mappings(summary.get("findings")):
            lines.append(_finding_line(finding))
    lines.extend(["", "## 资源版本", ""])
    for stage in _mapping(manifest.get("stages")).values():
        for unit in _mappings(_mapping(stage).get("units")):
            resource = _mapping(unit.get("resource"))
            lines.append(
                f"- {stage.get('stage_kind')} 第{unit.get('episode_number')}集："
                f"资源 {resource.get('id')}，版本 {resource.get('version')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _resume_report(source: dict[str, Any], initial: dict[str, Any] | None) -> dict[str, Any]:
    raw = initial if isinstance(initial, dict) else {}
    stages = _mapping(raw.get("stage_results"))
    stage_results: dict[str, list[dict[str, Any]]] = {"outline": [], "script": []}
    saved: set[str] = set()
    for item in [*_mappings(raw.get("all_unit_results")), *(_mappings(stages.get("outline"))), *(_mappings(stages.get("script")))]:
        stage_kind = str(item.get("stage_kind") or "")
        number = _positive_int(item.get("episode_number"))
        if stage_kind not in stage_results or not number or unit_key(stage_kind, number) in saved:
            continue
        stage_results[stage_kind].append(normalize_unit_result(item, stage_kind=stage_kind, episode_number=number))
        saved.add(unit_key(stage_kind, number))
    summaries: list[dict[str, Any]] = []
    summary_keys: set[str] = set()
    for item in _mappings(raw.get("batch_summaries")):
        stage_kind = str(item.get("stage_kind") or "")
        batch_index = _positive_int(item.get("batch_index"))
        key = _batch_key(stage_kind, batch_index)
        if not stage_kind or not batch_index or key in summary_keys:
            continue
        summaries.append(_normalize_batch_summary(item, stage_kind, batch_index, _int_list(item.get("episode_numbers"))))
        summary_keys.add(key)
    result = {
        "partial": bool(raw.get("partial", True)),
        "audit_scope": str(source.get("audit_scope") or ""),
        "stage_results": stage_results,
        "all_unit_results": [*stage_results["outline"], *stage_results["script"]],
        "batch_summaries": summaries,
        "continuity_state": deepcopy(_mapping(raw.get("continuity_state"))),
        "cursor": deepcopy(_mapping(raw.get("cursor"))),
        "source_manifest": deepcopy(_mapping(source.get("manifest"))),
        "upstream_mismatches": deepcopy(_mappings(source.get("upstream_mismatches"))),
        "source_fingerprint": str(source.get("fingerprint") or ""),
        "checkpoint_key": str(raw.get("checkpoint_key") or ""),
    }
    if _has_final_summary(raw):
        result.update(_normalize_final_summary(raw))
    return result


def _normalize_batch_summary(payload: dict[str, Any], stage_kind: str, batch_index: int, episode_numbers: list[int]) -> dict[str, Any]:
    result = normalize_unit_result(payload, stage_kind=stage_kind, episode_number=episode_numbers[0] if episode_numbers else 0)
    result.pop("episode_number", None)
    result.update({"batch_index": int(batch_index), "episode_numbers": [int(number) for number in episode_numbers]})
    return result


def _normalize_final_summary(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = str(payload.get("ai_verdict") or "建议修改")
    findings = []
    for item in payload.get("core_findings") or []:
        item = item if isinstance(item, dict) else {}
        severity = str(item.get("severity") or "P2").upper()
        findings.append({
            "severity": severity if severity in _SEVERITIES else "P2",
            "category": str(item.get("category") or "continuity"),
            "resource_id": item.get("resource_id"),
            "version": item.get("version"),
            "field": str(item.get("field") or "content"),
            "evidence": str(item.get("evidence") or "证据不足"),
            "suggestion": str(item.get("suggestion") or ""),
        })
    return {
        "ai_verdict": verdict if verdict in _VERDICTS else "建议修改",
        "summary": str(payload.get("summary") or ""),
        "core_findings": findings,
        "final_summary": True,
        "continuity_state": _mapping(payload.get("continuity_state")),
    }


def _unit_for(source: dict[str, Any], stage_kind: str, episode_number: int) -> dict[str, Any]:
    for unit in _mappings(_mapping(_mapping(source.get("stages")).get(stage_kind)).get("units")):
        if _positive_int(unit.get("episode_number")) == int(episode_number):
            return deepcopy(unit)
    raise ValueError(f"未找到 {stage_kind} 第{episode_number}集审核工作单元")


def _loaded_resource_refs(source: dict[str, Any], unit: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = _mapping(source.get("manifest"))
    refs = [
        {"kind": "project", "id": manifest.get("project_id"), "revision": _mapping(manifest.get("project")).get("revision")},
        {"kind": "brief", **_mapping(manifest.get("brief"))} if manifest.get("brief") else {"kind": "brief"},
    ]
    for name in ("resource", "outline", "beats"):
        resource = _mapping(unit.get(name))
        if resource:
            refs.append({
                "kind": name,
                "id": resource.get("id"),
                "version": resource.get("version"),
                "stage_kind": resource.get("stage_kind"),
                "episode_number": resource.get("episode_number"),
                "content_fingerprint": resource.get("content_fingerprint"),
            })
    return refs


def _stage_refs(source: dict[str, Any], stage_kind: str, episode_numbers: list[int]) -> list[dict[str, Any]]:
    return [
        {
            "stage_kind": stage_kind,
            "episode_numbers": [int(number) for number in episode_numbers],
            "source_fingerprint": str(source.get("fingerprint") or ""),
        }
    ]


def _stages(source: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    stages = _mapping(source.get("stages"))
    return [(kind, _mapping(stages.get(kind))) for kind in ("outline", "script") if kind in stages]


def _batches(stage: dict[str, Any]) -> list[list[int]]:
    return [_int_list(batch) for batch in stage.get("batches") or [] if _int_list(batch)]


def _saved_unit(report: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((item for item in report["all_unit_results"] if unit_key(item["stage_kind"], item["episode_number"]) == key), None)


def _batch_key(stage_kind: str, batch_index: int) -> str:
    return f"{stage_kind}:{int(batch_index)}"


def _has_batch_summary(report: dict[str, Any], key: str) -> bool:
    return any(_batch_key(str(item.get("stage_kind") or ""), _positive_int(item.get("batch_index"))) == key for item in report["batch_summaries"])


def _has_final_summary(report: dict[str, Any]) -> bool:
    return bool(report.get("final_summary") or (report.get("ai_verdict") and report.get("summary")))


def _advance_continuity(report: dict[str, Any], result: dict[str, Any]) -> None:
    state = _mapping(result.get("continuity_state"))
    if state:
        report["continuity_state"] = deepcopy(state)


def _set_cursor(report: dict[str, Any], source: dict[str, Any]) -> None:
    saved = {unit_key(item["stage_kind"], item["episode_number"]) for item in report["all_unit_results"]}
    for stage_kind, stage in _stages(source):
        for batch_index, batch in enumerate(_batches(stage), start=1):
            for episode_number in batch:
                if unit_key(stage_kind, episode_number) not in saved:
                    report["cursor"] = {"stage_kind": stage_kind, "episode_number": episode_number, "batch_index": batch_index}
                    return
    report["cursor"] = {"stage_kind": "", "episode_number": 0, "batch_index": 0}


def _request_event(kind: str, stage_kind: str, episode_number: int, batch_index: int) -> dict[str, Any]:
    return {"kind": kind, "stage_kind": stage_kind, "episode_number": episode_number, "batch_index": batch_index}


async def _call(callback: JsonCallback | None, value: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mappings(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _int_list(value: object) -> list[int]:
    return [_positive_int(item) for item in value if _positive_int(item)] if isinstance(value, list) else []


def _positive_int(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _finding_line(finding: Mapping[str, Any]) -> str:
    return (
        f"- [{finding.get('severity') or 'P2'}] 资源 {finding.get('resource_id') or '-'} "
        f"v{finding.get('version') or '-'}，字段 {finding.get('field') or 'content'}："
        f"证据：{finding.get('evidence') or '证据不足'}；建议：{finding.get('suggestion') or ''}"
    )
