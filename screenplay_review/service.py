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
    """Freeze the latest generated script for each selected episode."""
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

    episodes = [_source_episode(latest[number]) for number in selected]
    source = {
        "project_id": int(project["id"]),
        "project_name": str(project.get("name") or ""),
        "mode": mode,
        "episode_numbers": selected,
        "episodes": episodes,
    }
    source["fingerprint"] = source_fingerprint(source)
    return source


def build_text_source(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("请粘贴剧本文本")
    source = {
        "project_id": None,
        "project_name": "",
        "mode": "single",
        "episode_numbers": [1],
        "episodes": [{"episode_number": 1, "resource_id": None, "version": None, "text": normalized}],
    }
    source["fingerprint"] = source_fingerprint(source)
    return source


def source_fingerprint(source: dict[str, Any]) -> str:
    value = json.dumps(source.get("episodes") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
) -> dict[str, Any]:
    """Ask the configured LLM for a bounded, evidence-based review report."""
    episodes = list(source.get("episodes") or [])
    if not episodes:
        raise ValueError("审核来源没有可用剧本")
    if len(episodes) == 1:
        report = normalize_review_report(
            await provider.complete_json(_review_messages(source))
        )
        await _report_progress(on_progress, 1, 1)
        return report

    episode_reports: list[dict[str, Any]] = []
    total = len(episodes) + 1
    for index, episode in enumerate(episodes, start=1):
        episode_source = {
            **source,
            "mode": "single",
            "episode_numbers": [int(episode["episode_number"])],
            "episodes": [episode],
        }
        episode_reports.append(
            normalize_review_report(
                await provider.complete_json(_review_messages(episode_source))
            )
        )
        await _report_progress(on_progress, index, total)

    combined = {
        **source,
        "episode_reports": episode_reports,
    }
    report = normalize_review_report(
        await provider.complete_json(_summary_messages(combined))
    )
    await _report_progress(on_progress, total, total)
    return report


def _review_messages(source: dict[str, Any]) -> list[dict[str, str]]:
    episode_numbers = {int(number) for number in source.get("episode_numbers") or []}
    checks = [
        "检查剧情逻辑、人物执行、台词与 OS、爽点、节奏和结尾钩子。",
        "只基于提供的剧本给结论；无法确认的项目写证据不足。",
    ]
    if 1 in episode_numbers:
        checks.append("第 1 集必须执行首集生死线：故事、主角、冲突、调性、情绪点和结尾钩子。")
    if {1, 2, 3}.issubset(episode_numbers):
        checks.append("前三集检查核心设定、人物关系和敌我阵营是否前置交代。")
    return [
        {
            "role": "system",
            "content": (
                "你是短剧编剧审稿助手。AI 建议结论只能是“通过”或“建议修改”，"
                "不得代写剧本。输出 JSON：ai_verdict、summary、core_findings、"
                "episode_reviews、cross_episode_findings。episode_reviews 每项必须有 "
                "episode_number、summary 和 findings。core_findings 最多 5 条；"
                "每条问题都要给 title、rule_id、severity(P0/P1/P2)、episode_number、evidence、suggestion。\n"
                f"固定规则包：{RULE_PACKAGE}，覆盖{RULES}。\n"
                + "\n".join(checks)
            ),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


def _summary_messages(source: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是短剧编剧审稿助手。根据逐集审核和剧本，汇总多集或合集审核报告。"
                "结论只能是“通过”或“建议修改”。重点检查跨集剧情承接、人物状态、"
                "设定一致性、前三集信息前置和重复拖沓；无法确认的内容标记证据不足。"
                "输出 JSON：ai_verdict、summary、core_findings、"
                "episode_reviews、cross_episode_findings；episode_reviews 每项必须有 episode_number、summary 和 findings；"
                f"core_findings 最多 5 条，每条必须给出 title 和证据。固定规则包：{RULE_PACKAGE}，覆盖{RULES}。"
            ),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


async def _report_progress(callback: Any, completed: int, total: int) -> None:
    if callback is None:
        return
    result = callback(completed, total)
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
    return {
        "title": str(item.get("title") or _fallback_finding_title(rule_id)).strip(),
        "severity": severity if severity in SEVERITIES else "P2",
        "rule_id": rule_id,
        "episode_number": item.get("episode_number"),
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
