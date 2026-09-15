# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any


REVIEW_MODES = frozenset({"single", "multiple", "collection"})
SOURCE_TYPES = frozenset({"project", "text"})
VERDICTS = frozenset({"通过", "建议修改"})
SEVERITIES = frozenset({"P0", "P1", "P2"})
RULE_PACKAGE = "short-drama-review-v1"
RULES = (
    "首集吸引力、前三集信息、逻辑一致性、人物执行、台词/OS、"
    "爽点、节奏与跨集连续性"
)
APPROVED_STATUSES = frozenset({"locked", "approved"})
FINDING_ROUTES = frozenset(
    {"script_only", "beats_then_script", "outline_revision_required"}
)


def validate_review_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the small public request contract before source lookup."""
    source_type = str(payload.get("source_type") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    if source_type not in SOURCE_TYPES:
        raise ValueError("source_type 必须是 project 或 text")
    if mode not in REVIEW_MODES:
        raise ValueError("mode 必须是 single、multiple 或 collection")

    raw_numbers = payload.get("episode_numbers") or []
    if not isinstance(raw_numbers, list):
        raise ValueError("episode_numbers 必须是数组")
    episode_numbers = sorted({int(number) for number in raw_numbers if int(number) > 0})

    if source_type == "text":
        text = str(payload.get("text") or "").strip()
        if mode != "single":
            raise ValueError("粘贴文本仅支持单集审核")
        if not text:
            raise ValueError("请粘贴剧本文本")
        return {"source_type": source_type, "mode": mode, "text": text, "episode_numbers": [1]}

    project_id = int(payload.get("project_id") or 0)
    if project_id < 1:
        raise ValueError("请选择创作项目")
    if mode == "single" and len(episode_numbers) != 1:
        raise ValueError("单集审核请选择 1 集")
    if mode == "multiple" and len(episode_numbers) < 2:
        raise ValueError("多集审核至少选择 2 集")
    return {
        "source_type": source_type,
        "mode": mode,
        "project_id": project_id,
        "episode_numbers": episode_numbers,
    }


def build_project_source(
    project: dict[str, Any], mode: str, episode_numbers: list[int]
) -> dict[str, Any]:
    """Freeze one formally valid creative context for each reviewed episode."""
    latest: dict[int, dict[str, Any]] = {}
    for item in project.get("episodes") or []:
        if item.get("stage") != "script":
            continue
        number = int(item.get("episode_number") or 0)
        if number < 1:
            continue
        current = latest.get(number)
        if current is None or (int(item.get("version") or 0), int(item.get("id") or 0)) > (
            int(current.get("version") or 0),
            int(current.get("id") or 0),
        ):
            latest[number] = item
    selected = sorted(latest) if mode == "collection" else list(episode_numbers)
    missing = [number for number in selected if number not in latest]
    if not selected:
        raise ValueError("项目尚无可审核的场次剧本")
    if missing:
        raise ValueError(f"第 {', '.join(str(number) for number in missing)} 集尚无场次剧本")

    brief, characters, relationships = _project_story_context(project)
    outline_batch_id = str(project.get("outline_batch_id") or "")
    episodes = [
        _source_episode_with_context(
            latest[number],
            _current_outline(project, number, outline_batch_id),
            _current_beats(project, number, outline_batch_id),
            project,
        )
        for number in selected
    ]
    source = {
        "project_id": int(project["id"]),
        "project_name": str(project.get("name") or ""),
        "mode": mode,
        "formal": True,
        "episode_numbers": selected,
        "brief": brief,
        "characters": characters,
        "relationships": relationships,
        "episodes": episodes,
    }
    source["fingerprint"] = source_fingerprint(source)
    return source


def build_text_source(text: str, *, source_filename: str = "") -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("请粘贴剧本文本")
    source = {
        "project_id": None,
        "project_name": "",
        "source_filename": str(source_filename or "").strip(),
        "mode": "single",
        "formal": False,
        "episode_numbers": [1],
        "episodes": [{"episode_number": 1, "resource_id": None, "version": None, "text": normalized}],
    }
    source["fingerprint"] = source_fingerprint(source)
    return source


def source_fingerprint(source: dict[str, Any]) -> str:
    value = json.dumps(
        {
            key: value
            for key, value in source.items()
            if key not in {"fingerprint", "prompt_snapshot"}
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_source_label(review: dict[str, Any]) -> str:
    payload = review.get("source_payload") or {}
    if review.get("source_type") == "text":
        return f"剧本文件：{payload.get('source_filename') or '未命名文件'}（非正式）"
    return str(payload.get("project_name") or f"项目 {review.get('project_id') or '-'}")


def _outline_anchor_text(anchor: Any) -> str:
    if not isinstance(anchor, dict) or not anchor:
        return ""
    labels = {
        "core_event": "核心事件", "conflict": "冲突", "character_progress": "人物推进",
        "highlight": "爽点", "reversal": "反转", "cliffhanger": "结尾悬念",
        "estimated_seconds": "预计时长",
    }
    value_text = lambda value: "、".join(map(str, value)) if isinstance(value, list) else str(value or "").strip()
    if anchor.get("field"):
        value = value_text(anchor.get("value"))
        label = labels.get(str(anchor["field"]), "大纲要点")
        return f"{label}：{value}" if value else label
    return "；".join(
        f"{labels.get(str(key), '大纲要点')}：{value_text(value)}"
        for key, value in anchor.items() if value_text(value)
    )


def _finding_markdown(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "审核问题待补充").strip()
    parts = [f"- [{str(item.get('severity') or 'P2').upper()}] {title}"]
    anchor = _outline_anchor_text(item.get("outline_anchor"))
    if anchor:
        parts.append(f"对应大纲：{anchor}")
    beats = [str(number) for number in item.get("beat_orders") or [] if str(number).strip()]
    if beats:
        parts.append(f"关联节拍：第 {'、第 '.join(beats)} 节")
    if item.get("evidence"):
        parts.append(f"证据：{str(item['evidence']).strip()}")
    if item.get("suggestion"):
        parts.append(f"建议：{str(item['suggestion']).strip()}")
    route = {
        "script_only": "场次细节优化", "beats_then_script": "先优化 Beats",
        "outline_revision_required": "需人工修订大纲",
    }.get(str(item.get("change_route") or "script_only"), "场次细节优化")
    parts.append(f"处理方式：{route}")
    return "；".join(parts)


def build_review_markdown(review: dict[str, Any]) -> str:
    """Render an audit report as a portable, writer-facing Markdown file."""
    report = review.get("report") or {}
    payload = review.get("source_payload") or {}
    mode = {"single": "单集", "multiple": "多集", "collection": "合集"}.get(
        str(review.get("mode") or ""), "审核"
    )
    episodes = "、".join(f"第 {number} 集" for number in review.get("episode_numbers") or []) or "-"
    lines = [
        f"# 剧本审核报告 #{review.get('id') or '-'}",
        "",
        f"- 来源：{_review_source_label(review)}",
        f"- 审核范围：{mode} · {episodes}",
        f"- 审核状态：{review.get('status') or '-'}",
        "",
        "## 审核结论",
        "",
        f"AI 建议：{report.get('ai_verdict') or review.get('ai_verdict') or '-'}",
    ]
    if report.get("summary"):
        lines.extend(["", str(report["summary"]).strip()])
    if not payload.get("formal"):
        lines.extend(["", "> 本报告为非正式快速诊断，不可人工确认或生成优化草稿。"])
    else:
        lines.extend(["", "## 评审依据", ""])
        lines.append(
            f"Brief v{(payload.get('brief') or {}).get('version') or '-'} · 人物关系 {len(payload.get('relationships') or [])} 条"
        )
        for episode in payload.get("episodes") or []:
            lines.append(
                f"- 第 {episode.get('episode_number') or '-'} 集：大纲 v{(episode.get('outline') or {}).get('version') or '-'} · "
                f"Beats v{(episode.get('beats') or {}).get('version') or '-'} · 场次剧本 v{(episode.get('script') or {}).get('version') or episode.get('version') or '-'}"
            )

    sections = [
        ("核心问题", report.get("core_findings") or []),
        ("跨集问题", report.get("cross_episode_findings") or []),
    ]
    for heading, findings in sections:
        if findings:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(_finding_markdown(item) for item in findings if isinstance(item, dict))
    for episode_review in report.get("episode_reviews") or []:
        findings = episode_review.get("findings") or [] if isinstance(episode_review, dict) else []
        if findings:
            lines.extend(["", f"## 第 {episode_review.get('episode_number') or '-'} 集问题", ""])
            lines.extend(_finding_markdown(item) for item in findings if isinstance(item, dict))
    return "\n".join(lines).rstrip() + "\n"


def _project_story_context(
    project: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    brief_id = int(project.get("locked_brief_id") or 0)
    character_batch_id = str(project.get("character_batch_id") or "")
    if brief_id < 1:
        raise ValueError("项目尚未锁定 Brief，无法发起正式审核")
    if not character_batch_id:
        raise ValueError("项目尚未锁定人物关系，无法发起正式审核")
    brief = next(
        (item for item in project.get("blueprints") or [] if int(item.get("id") or 0) == brief_id),
        None,
    )
    if not brief or str(brief.get("status") or "") not in {"brief_locked", "approved"}:
        raise ValueError("当前锁定 Brief 不可用，无法发起正式审核")
    characters = [
        item for item in project.get("characters") or []
        if str(item.get("batch_id") or "") == character_batch_id
    ]
    relationships = [
        item for item in project.get("relationships") or []
        if str(item.get("batch_id") or "") == character_batch_id
    ]
    if not characters or any(str(item.get("status") or "") not in APPROVED_STATUSES for item in [*characters, *relationships]):
        raise ValueError("当前人物关系版本未锁定，无法发起正式审核")
    return (
        _resource_snapshot(brief),
        [_resource_snapshot(item) for item in characters],
        [_resource_snapshot(item) for item in relationships],
    )


def _current_outline(project: dict[str, Any], episode_number: int, batch_id: str) -> dict[str, Any]:
    if not batch_id:
        raise ValueError("项目尚未锁定分集大纲，无法发起正式审核")
    candidates = [
        item for item in project.get("episodes") or []
        if item.get("stage") == "outline"
        and str(item.get("batch_id") or "") == batch_id
        and int(item.get("episode_number") or 0) == episode_number
        and str(item.get("status") or "") in APPROVED_STATUSES
    ]
    if not candidates:
        raise ValueError(f"第 {episode_number} 集分集大纲未锁定，无法发起正式审核")
    return max(candidates, key=_version_key)


def _current_beats(project: dict[str, Any], episode_number: int, outline_batch_id: str) -> dict[str, Any]:
    candidates = [
        item for item in project.get("episodes") or []
        if item.get("stage") == "beats"
        and int(item.get("episode_number") or 0) == episode_number
        and str(item.get("status") or "") in APPROVED_STATUSES
    ]
    if not candidates:
        raise ValueError(f"第 {episode_number} 集 Beats 未锁定，无法发起正式审核")
    beats = max(candidates, key=_version_key)
    _assert_upstream_refs(beats, project, f"第 {episode_number} 集 Beats")
    _assert_outline_ref(beats, _current_outline(project, episode_number, outline_batch_id), f"第 {episode_number} 集 Beats")
    return beats


def _source_episode_with_context(
    script: dict[str, Any], outline: dict[str, Any], beats: dict[str, Any], project: dict[str, Any]
) -> dict[str, Any]:
    episode_number = int(script["episode_number"])
    _assert_upstream_refs(script, project, f"第 {episode_number} 集场次剧本")
    _assert_outline_ref(script, outline, f"第 {episode_number} 集场次剧本")
    refs = script.get("upstream_refs") or {}
    if (int(refs.get("beats_id") or 0), int(refs.get("beats_version") or 0)) != (
        int(beats.get("id") or 0), int(beats.get("version") or 0)
    ):
        raise ValueError(f"第 {episode_number} 集场次剧本引用的 Beats 已过期，请重新生成场次剧本")
    expected_beats_fingerprint = _content_fingerprint(beats.get("content") or {})
    actual_beats_fingerprint = str(refs.get("beats_content_fingerprint") or "")
    if actual_beats_fingerprint and actual_beats_fingerprint != expected_beats_fingerprint:
        raise ValueError(f"第 {episode_number} 集场次剧本引用的 Beats 内容已变化，请重新生成场次剧本")
    return {
        **_source_episode(script),
        "script": _resource_snapshot(script),
        "outline": _resource_snapshot(outline),
        "beats": _resource_snapshot(beats),
    }


def _assert_upstream_refs(item: dict[str, Any], project: dict[str, Any], label: str) -> None:
    refs = item.get("upstream_refs") or {}
    expected = {
        "brief_id": int(project.get("locked_brief_id") or 0),
        "character_batch_id": str(project.get("character_batch_id") or ""),
        "outline_batch_id": str(project.get("outline_batch_id") or ""),
    }
    if any(str(refs.get(key) or "") != str(value) for key, value in expected.items()):
        raise ValueError(f"{label}引用的 Brief、人物关系或分集大纲已过期，请重新生成")


def _assert_outline_ref(item: dict[str, Any], outline: dict[str, Any], label: str) -> None:
    refs = item.get("upstream_refs") or {}
    expected_identity = (
        int(outline.get("id") or 0),
        int(outline.get("version") or 0),
    )
    actual_identity = (
        int(refs.get("outline_id") or 0),
        int(refs.get("outline_version") or 0),
    )
    content = outline.get("content") or {}
    authority_content = dict(content)
    authority_content.pop("series_overview", None)
    saved_fingerprint = str(refs.get("outline_content_fingerprint") or "")
    valid_fingerprints = {
        _content_fingerprint(authority_content),
        _content_fingerprint(content),
    }
    if actual_identity != expected_identity or saved_fingerprint not in valid_fingerprints:
        raise ValueError(f"{label}引用的分集大纲版本已过期，请重新生成")


def _version_key(item: dict[str, Any]) -> tuple[int, int]:
    return int(item.get("version") or 0), int(item.get("id") or 0)


def _content_fingerprint(content: dict[str, Any]) -> str:
    value = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resource_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content") or {}
    return {
        "id": int(item.get("id") or 0),
        "version": int(item.get("version") or 1),
        "status": str(item.get("status") or ""),
        "content": content,
        "upstream_refs": item.get("upstream_refs") or {},
        "content_fingerprint": _content_fingerprint(content),
    }


def normalize_review_report(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = str(payload.get("ai_verdict") or "").strip()
    if verdict not in VERDICTS:
        raise ValueError("AI 审核结论必须是通过或建议修改")
    findings = [_normalize_finding(item) for item in payload.get("core_findings") or [] if isinstance(item, dict)]
    episodes = [
        _normalize_episode_review(item)
        for item in payload.get("episode_reviews") or []
        if isinstance(item, dict)
    ]
    cross = [_normalize_finding(item) for item in payload.get("cross_episode_findings") or [] if isinstance(item, dict)]
    return {
        "ai_verdict": verdict,
        "summary": str(payload.get("summary") or "").strip(),
        "core_findings": findings[:5],
        "episode_reviews": episodes,
        "cross_episode_findings": cross,
    }


async def review_source(
    provider: Any,
    source: dict[str, Any],
    *,
    on_progress: Any = None,
    on_checkpoint: Any = None,
    initial_episode_reviews: list[dict[str, Any]] | None = None,
    prompt_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the configured LLM for a bounded, evidence-based review report."""
    episodes = list(source.get("episodes") or [])
    if not episodes:
        raise ValueError("审核来源没有可用剧本")
    if len(episodes) == 1:
        report = normalize_review_report(
            await provider.complete_json(_review_messages(source, prompt_snapshot))
        )
        await _report_progress(on_progress, 1, 1)
        return report

    completed_by_episode = {
        int(item.get("episode_number") or 0): item
        for item in (initial_episode_reviews or [])
        if isinstance(item, dict) and int(item.get("episode_number") or 0) > 0
    }
    episode_reports = [
        completed_by_episode[number]
        for number in sorted(completed_by_episode)
        if any(int(episode.get("episode_number") or 0) == number for episode in episodes)
    ]
    total = len(episodes) + 1
    for index, episode in enumerate(episodes, start=1):
        episode_number = int(episode["episode_number"])
        if episode_number in completed_by_episode:
            await _report_progress(on_progress, index, total)
            continue
        episode_source = {
            **source,
            "mode": "single",
            "episode_numbers": [episode_number],
            "episodes": [episode],
        }
        episode_report = normalize_review_report(
            await provider.complete_json(_review_messages(episode_source, prompt_snapshot))
        )
        completed = {
            "episode_number": episode_number,
            "summary": episode_report["summary"],
            "findings": episode_report["core_findings"],
        }
        completed_by_episode[episode_number] = completed
        episode_reports.append(completed)
        await _report_checkpoint(
            on_checkpoint,
            build_partial_review_report(episode_reports, len(episodes)),
        )
        await _report_progress(on_progress, index, total)

    combined = {
        **source,
        "episode_reports": [
            {
                "episode_number": item["episode_number"],
                "summary": item.get("summary") or "",
                "core_findings": item.get("findings") or [],
            }
            for item in episode_reports
        ],
    }
    report = normalize_review_report(
        await provider.complete_json(_summary_messages(combined, prompt_snapshot))
    )
    await _report_progress(on_progress, total, total)
    return report


def build_partial_review_report(
    episode_reviews: list[dict[str, Any]], total_episode_count: int
) -> dict[str, Any]:
    """Make completed per-episode results readable before the final aggregation."""
    completed = len(episode_reviews)
    return {
        "partial": True,
        "completed_episode_count": completed,
        "total_episode_count": total_episode_count,
        "summary": (
            f"已完成 {completed}/{total_episode_count} 集逐集审核，"
            "跨集汇总尚未完成。"
        ),
        "core_findings": [],
        "episode_reviews": episode_reviews,
        "cross_episode_findings": [],
    }


def _with_review_style(
    system: str, prompt_snapshot: dict[str, Any] | None
) -> str:
    if not prompt_snapshot:
        return system
    instruction = str(prompt_snapshot.get("effective_instruction") or "").strip()
    if not instruction:
        return system
    return (
        f"{system}\n\n当前项目审核侧重点（不得覆盖前述 JSON 契约、固定规则包、"
        f"证据要求、修改路径或人工确认机制）：\n{instruction}"
    )


def _review_messages(
    source: dict[str, Any], prompt_snapshot: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    episode_numbers = {int(number) for number in source.get("episode_numbers") or []}
    checks = [
        "检查剧情逻辑、人物执行、台词与 OS、爽点、节奏和结尾钩子。",
    ]
    if source.get("formal"):
        checks.extend([
            "正式项目审核必须逐项核对 Beats 是否落实既定大纲：场景目标、阻力、人物行动、转折和连续性安排能否支撑场次剧本。",
            "Beats 本身存在缺失、矛盾、顺序失当或不足以支撑既定大纲时，必须标记 beats_then_script；"
            "仅台词、镜头、动作、场次细节的问题才标记 script_only。",
        ])
    else:
        checks.append("只基于提供的剧本给结论；无法确认的项目写证据不足。")
    if 1 in episode_numbers:
        checks.append("第 1 集必须执行首集生死线：故事、主角、冲突、调性、情绪点和结尾钩子。")
    if {1, 2, 3}.issubset(episode_numbers):
        checks.append("前三集检查核心设定、人物关系和敌我阵营是否前置交代。")
    return [
        {
            "role": "system",
            "content": _with_review_style((
                "你是短剧编剧审稿助手。AI 建议结论只能是“通过”或“建议修改”，"
                "不得代写剧本。输出 JSON：ai_verdict、summary、core_findings、"
                "episode_reviews、cross_episode_findings。episode_reviews 每项必须有 "
                "episode_number、summary 和 findings。core_findings 最多 5 条；"
                "每条问题都要给 title、rule_id、category、severity(P0/P1/P2)、episode_number、"
                "outline_anchor（对应的大纲字段和值）、beat_orders、evidence、suggestion、"
                "change_route（script_only、beats_then_script、outline_revision_required）。"
                "所有建议必须遵守冻结的分集大纲；需要改变核心事件、人物进展、反转或钩子时，"
                "必须标记 outline_revision_required。\n"
                f"固定规则包：{RULE_PACKAGE}，覆盖{RULES}。\n"
                + "\n".join(checks)
            ), prompt_snapshot),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


def _summary_messages(
    source: dict[str, Any], prompt_snapshot: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _with_review_style((
                "你是短剧编剧审稿助手。根据逐集审核和剧本，汇总多集或合集审核报告。"
                "结论只能是“通过”或“建议修改”。重点检查跨集剧情承接、人物状态、"
                "设定一致性、前三集信息前置和重复拖沓；无法确认的内容标记证据不足。"
                "输出 JSON：ai_verdict、summary、core_findings、"
                "episode_reviews、cross_episode_findings；episode_reviews 每项必须有 episode_number、summary 和 findings；"
                "每条问题都要给 title、category、outline_anchor、beat_orders、evidence、suggestion、"
                "change_route；不得建议自动改写分集大纲。"
                "汇总时保留逐集对 Beats 的归因：Beats 本身需要调整才标记 beats_then_script，"
                "仅场次细节问题标记 script_only。"
                f"core_findings 最多 5 条。固定规则包：{RULE_PACKAGE}，覆盖{RULES}。"
            ), prompt_snapshot),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


async def _report_progress(callback: Any, completed: int, total: int) -> None:
    if callback is None:
        return
    result = callback(completed, total)
    if inspect.isawaitable(result):
        await result


async def _report_checkpoint(callback: Any, report: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(report)
    if inspect.isawaitable(result):
        await result


def _source_episode(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content") or {}
    lines = [str(content.get("episode_title") or "").strip()]
    for scene in content.get("scenes") or []:
        scene_no = scene.get("scene_no") or ""
        for block in scene.get("blocks") or []:
            text = str(block.get("text") or "").strip()
            if text:
                speaker = str(block.get("speaker") or "").strip()
                prefix = f"第 {scene_no} 场 {speaker}".strip()
                lines.append(f"{prefix}：{text}" if prefix else text)
    return {
        "episode_number": int(item["episode_number"]),
        "resource_id": int(item["id"]),
        "version": int(item.get("version") or 1),
        "text": "\n".join(line for line in lines if line).strip(),
    }


def _normalize_finding(item: dict[str, Any]) -> dict[str, Any]:
    severity = str(item.get("severity") or "P2").upper()
    rule_id = str(item.get("rule_id") or "").strip()
    route = str(item.get("change_route") or "script_only").strip()
    beat_orders = sorted(
        {int(order) for order in item.get("beat_orders") or [] if str(order).isdigit() and int(order) > 0}
    )
    outline_anchor = item.get("outline_anchor") if isinstance(item.get("outline_anchor"), dict) else {}
    return {
        "title": str(item.get("title") or _fallback_finding_title(rule_id)).strip(),
        "severity": severity if severity in SEVERITIES else "P2",
        "rule_id": rule_id,
        "category": str(item.get("category") or "script_detail").strip() or "script_detail",
        "episode_number": item.get("episode_number"),
        "outline_anchor": outline_anchor,
        "beat_orders": beat_orders,
        "change_route": route if route in FINDING_ROUTES else "script_only",
        "evidence": str(item.get("evidence") or "证据不足").strip() or "证据不足",
        "suggestion": str(item.get("suggestion") or "").strip(),
    }


def _fallback_finding_title(rule_id: str) -> str:
    prefix = rule_id.split("-", 1)[0].lower()
    return {
        "logic": "剧情逻辑待补强",
        "character": "人物动机待补强",
        "detail": "细节表达不明确",
        "plot": "冲突与爽点待加强",
        "dialogue": "台词与 OS 待优化",
        "pacing": "剧情节奏待调整",
        "continuity": "跨集连续性待补强",
    }.get(prefix, "审核问题待补充")


def _normalize_episode_review(item: dict[str, Any]) -> dict[str, Any]:
    try:
        episode_number = int(item.get("episode_number") or 0)
    except (TypeError, ValueError):
        episode_number = 0
    return {
        "episode_number": episode_number or None,
        "summary": str(item.get("summary") or "").strip(),
        "findings": [
            _normalize_finding(finding)
            for finding in item.get("findings") or []
            if isinstance(finding, dict)
        ],
    }
