# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from typing import Any


PROMPT_TEMPLATE_VERSION = "phase4-v1"
BREAKDOWN_PROMPT_TEMPLATE_VERSION = "transcript-breakdown-v1"


def _messages(system: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system + " 只输出合法 JSON，不要输出 Markdown。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def blueprint_messages(payload: dict[str, Any], count: int) -> list[dict[str, str]]:
    return _messages(
        "你是短剧创意策划。借鉴结构与情绪价值，但不得复制角色名、具体台词、完整剧情节点或独特表达。"
        f"生成 {count} 个明显不同的方向，返回 blueprints 数组。每项包含 title、borrowed_elements、"
        "different_elements、protagonist、core_conflict、main_goal、background、worldview、first_three_hooks、"
        "highlights、audience、cost_level、risk_score、risk_reasons、revision_suggestions、priority。",
        payload,
    )


def character_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _messages(
        "根据已锁定 Brief 生成人物卡和人物关系。返回 characters 和 relationships 数组。"
        "人物包含 name、age_range、identity、external_tags、desire、secret、growth_arc、"
        "dialogue_style、appearance_prompt、voice_prompt；关系包含 from、to、relation、conflict、change_arc。",
        payload,
    )


def series_arc_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _messages(
        "根据锁定 Brief 与人物关系规划全剧阶段弧线。返回 series_arc 字符串和 stages 数组，"
        "只规划结构，不写完整正文或台词。",
        payload,
    )


def outline_messages(payload: dict[str, Any], start: int, end: int) -> list[dict[str, str]]:
    return _messages(
        f"生成第 {start}-{end} 集分集大纲，返回 episodes 数组。每集必须包含 episode、core_event、"
        "conflict、character_progress、highlight、reversal、cliffhanger、estimated_seconds。"
        "不得写完整正文或完整台词。",
        payload,
    )


def beats_messages(payload: dict[str, Any], episode_number: int) -> list[dict[str, str]]:
    return _messages(
        f"为第 {episode_number} 集生成 Beats，返回 beats 数组。每项包含 order、scene、characters、"
        "goal、obstacle、emotion_change、turn、hook。不得生成完整正文或完整台词。",
        payload,
    )


def breakdown_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _messages(
        "你是严谨的短剧拆解分析师。只能依据输入的剧目元数据和逐集转写归纳，不得套用通用案例、"
        "补写未出现的剧情或伪造评论反馈。返回 content、field_sources、risks 三个字段。content 必须包含"
        " title、one_line_selling_point、category、target_audience、protagonist_identity、resistance_source、"
        "core_conflict、hook_points、selling_points、emotional_curve、reversal_nodes、comment_resonance、"
        "imitable_elements、production_difficulty、risk_notes。field_sources 按 content 字段名组织为证据数组，"
        "每条证据必须包含 episode_number、source_path、excerpt，source_path 必须原样使用输入转写的路径，"
        "excerpt 必须是该集转写中的原文短句。只有部分集数时，要在 risk_notes 和 risks 中明确覆盖缺口；"
        "没有评论正文时，comment_resonance 必须明确写出当前未提供评论正文，不能声称观众观点。"
        "数组字段必须返回 JSON 数组，文本字段必须返回字符串。",
        payload,
    )


def repair_messages(messages: list[dict[str, str]], error: str) -> list[dict[str, str]]:
    return messages + [{
        "role": "user",
        "content": f"上一次响应无法通过结构校验：{error}。请保持原任务，仅重新输出符合字段要求的 JSON。",
    }]
