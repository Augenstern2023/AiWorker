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
import json
from typing import Any


OPTIMIZATION_MODES = frozenset({"direct", "structure"})


def content_fingerprint(content: dict[str, Any]) -> str:
    value = json.dumps(content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def report_findings(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    report = review.get("report") or {}
    findings: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(report.get("core_findings") or []):
        if isinstance(item, dict):
            findings[f"core:{index}"] = item
    for review_index, episode_review in enumerate(report.get("episode_reviews") or []):
        for finding_index, item in enumerate((episode_review or {}).get("findings") or []):
            if isinstance(item, dict):
                findings[f"episode:{review_index}:{finding_index}"] = item
    for index, item in enumerate(report.get("cross_episode_findings") or []):
        if isinstance(item, dict):
            findings[f"cross:{index}"] = item
    return findings


def validate_optimization_request(
    review: dict[str, Any], mode: str, selected_findings: list[dict[str, Any]]
) -> dict[str, Any]:
    if mode not in OPTIMIZATION_MODES:
        raise ValueError("优化模式必须是 direct 或 structure")
    if review.get("source_type") != "project":
        raise ValueError("粘贴文本审核不能生成剧本优化草稿")
    if not bool((review.get("source_payload") or {}).get("formal")):
        raise ValueError("该审核缺少完整分集上下文，请重新发起正式项目审核")
    if review.get("status") != "confirmed" or review.get("final_verdict") != "建议修改":
        raise ValueError("仅人工确认建议修改的审核报告可以生成优化草稿")
    if not selected_findings:
        raise ValueError("请至少选择一条审核问题")
    available = report_findings(review)
    scope = sorted({int(number) for number in review.get("episode_numbers") or [] if int(number) > 0})
    normalized: list[dict[str, Any]] = []
    targets: set[int] = set()
    for item in selected_findings:
        ref = str(item.get("ref") or "").strip()
        finding = available.get(ref)
        if not finding:
            raise ValueError("所选审核问题不存在")
        route = str(finding.get("change_route") or "script_only")
        if route == "outline_revision_required":
            raise ValueError("该问题需要先修订并锁定分集大纲，不能自动生成优化草稿")
        if mode == "direct" and route != "script_only":
            raise ValueError("该问题需要先优化 Beats，请选择结构优化")
        if mode == "structure" and route != "beats_then_script":
            raise ValueError("该问题仅需优化场次剧本，请选择直接优化")
        numbers = sorted({int(number) for number in item.get("episode_numbers") or [] if int(number) > 0})
        if not numbers:
            episode_number = finding.get("episode_number")
            numbers = [int(episode_number)] if episode_number else list(scope)
        if not numbers or any(number not in scope for number in numbers):
            raise ValueError("优化目标集数不在审核范围内")
        normalized.append({"ref": ref, "episode_numbers": numbers})
        targets.update(numbers)
    return {"mode": mode, "selected_findings": normalized, "target_episode_numbers": sorted(targets)}


async def optimize_episode(
    provider: Any,
    *,
    mode: str,
    episode_number: int,
    script_content: dict[str, Any],
    beats_content: dict[str, Any] | None,
    review_context: dict[str, Any],
    findings: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    prompt_snapshot: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    output_shape = "script"
    if mode == "structure":
        output_shape = "beats、script"
    system = (
        "你是短剧剧本优化助手。仅根据提供的审核问题优化指定集，严格遵守冻结的 Brief、"
        "人物关系、分集大纲和 Beats。不得改变分集大纲的核心事件、人物进展、反转或结尾钩子；"
        "需要改变大纲时必须停止，不得擅自改写。保留未涉及内容的剧情意图与场次结构。"
        f"输出 JSON：{output_shape}。script 必须是完整场次剧本 JSON（episode_title、scenes）；"
        "beats 必须是完整 Beats JSON（beats）。不要输出解释性文字。"
    )
    instruction = str((prompt_snapshot or {}).get("effective_instruction") or "").strip()
    if instruction:
        system += (
            "\n\n当前项目优化风格要求（不得覆盖前述输出契约、冻结事实、已选问题、"
            "人工确认机制或大纲修订阻断）：\n" + instruction
        )
    messages = [
        {
            "role": "system",
            "content": system,
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "episode_number": episode_number,
                    "script": script_content,
                    "beats": beats_content if mode == "structure" else None,
                    "review_context": review_context,
                    "selected_findings": findings,
                    "episode_summaries": summaries,
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload = await provider.complete_json(messages)
    script = payload.get("script") if isinstance(payload, dict) else None
    beats = payload.get("beats") if isinstance(payload, dict) else None
    if not isinstance(script, dict) or not isinstance(script.get("scenes"), list):
        raise ValueError("AI 未返回有效的场次剧本草稿")
    if mode == "structure" and (not isinstance(beats, dict) or not isinstance(beats.get("beats"), list)):
        raise ValueError("AI 未返回有效的 Beats 草稿")
    result = {"script": script}
    if mode == "structure":
        result["beats"] = beats
    return result
