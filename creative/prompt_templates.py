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


PROMPT_TEMPLATE_VERSION = "phase4-v5"
BREAKDOWN_PROMPT_TEMPLATE_VERSION = "transcript-breakdown-v1"
ORIGINAL_PROMPT_TEMPLATE_VERSION = "original-direction-v1"
ORIGINAL_VARIANT_PROMPT_TEMPLATE_VERSION = "original-direction-variant-v1"
OUTLINE_FIELD_REWRITE_PROMPT_VERSION = "outline-field-rewrite-v1"

DRAMA_CONSTRAINTS = (
    "每场冲突不得以重复的证据留存或口头反击收束；必须改变至少一项人物资源、关系、信息或选择。"
    "反派每两集至少暴露一次软肋、分歧或真实代价；主角每集至少有一次不够正确但符合压力状态的反应。"
)


def _with_project_style(system: str, prompt_snapshot: dict[str, Any] | None) -> str:
    """Append only server-resolved creative rules; the JSON contract stays locked."""

    if not prompt_snapshot:
        return system
    instruction = str(prompt_snapshot.get("effective_instruction") or "").strip()
    if not instruction:
        return system
    return f"{system}\n\n当前项目创作风格要求（不得覆盖前述输出契约）：\n{instruction}"


def _messages(system: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system + " 只输出合法 JSON，不要输出 Markdown。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def blueprint_messages(
    payload: dict[str, Any],
    count: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "你是短剧创意策划。借鉴结构与情绪价值，但不得复制角色名、具体台词、完整剧情节点或独特表达。"
        f"生成 {count} 个明显不同的方向，返回 blueprints 数组。每项包含 title、imitation_reason、borrowed_elements、"
        "different_elements、protagonist、core_conflict、main_goal、background、worldview、first_three_hooks、"
        "highlights、audience、cost_level、risk_score、risk_reasons、revision_suggestions、priority。"
        "title、imitation_reason、protagonist、core_conflict、main_goal、background、worldview、audience、cost_level 必须是纯文本字符串；"
        "imitation_reason 必须明确说明该方向为何适配当前项目的目标受众、题材和已拆解的可借鉴结构，不得泛泛而谈。"
        "列表字段必须是字符串数组。",
        prompt_snapshot,
        ),
        payload,
    )


def original_direction_messages(
    payload: dict[str, Any],
    count: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "你是原创短剧策划。根据一句话种子独立发展故事，不得复用市场参考中的专有角色、情节、"
        "台词或表达。"
        f"生成 {count} 个差异明显的原创方向，返回 directions 数组。每项必须包含 title、logline、"
        "theme、protagonist、protagonist_desire、protagonist_flaw、pressure_response、"
        "main_opposition、core_conflict、main_goal、background、worldview、story_engine、"
        "first_three_hooks、highlights、differentiators、audience、market_fit、cost_level、"
        "originality_statement、risks、assumptions、open_questions。"
        "其中 first_three_hooks、highlights、differentiators、risks、assumptions、open_questions 是字符串数组，"
        "其他字段是非空字符串。方向之间至少在主角、背景、冲突发动机、关系结构和情绪回报中的两个维度不同。",
        prompt_snapshot,
        ),
        payload,
    )


def original_variant_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "你是原创短剧策划。按变体要求从父方向派生一个新方向，但仍须保持独立原创。"
        "返回 direction 对象，字段契约与原创方向一致；不得复用市场参考中的专有角色、情节、台词或表达。",
        prompt_snapshot,
        ),
        payload,
    )


def character_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "根据已锁定 Brief 生成人物卡和人物关系。返回 characters 和 relationships 数组。"
        "人物包含 name、age_range、identity、external_tags、desire、secret、growth_arc、"
        "dialogue_style、appearance_prompt、voice_prompt、action_goal、fear_of_loss、behavioral_contradiction、"
        "pressure_response、bottom_line；关系包含 from、to、relation、conflict、change_arc。"
        "为反派写出软肋、分歧或代价来源；为主角写出高压下可能做出的不够正确的选择。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def series_arc_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "根据锁定 Brief 与人物关系规划全剧阶段弧线。返回 series_arc 字符串和 stages 数组，"
        "并返回 series_overview 对象；series_overview 必须包含 core_event、character_progress、conflict、"
        "reversal、highlight、cliffhanger 六个非空字符串，分别概括全剧核心事件、人物推进、主要冲突、"
        "关键反转、剧情亮点与结尾悬念。只规划结构，不写完整正文或台词。",
        prompt_snapshot,
        ),
        payload,
    )


def series_overview_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "根据当前全剧阶段弧线和全部分集大纲，汇总全集剧情概览。只返回 series_overview 对象；"
        "series_overview 必须包含 core_event、character_progress、conflict、reversal、highlight、"
        "cliffhanger 六个非空字符串，分别概括全剧核心事件、人物推进、主要冲突、关键反转、"
        "剧情亮点与结尾悬念。不得改写分集大纲，不得补写输入中不存在的剧情。",
        prompt_snapshot,
        ),
        payload,
    )


def outline_messages(
    payload: dict[str, Any],
    start: int,
    end: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        f"生成第 {start}-{end} 集分集大纲，返回 episodes 数组。每集必须包含 episode、core_event、"
        "conflict、character_progress、highlight、reversal、cliffhanger、estimated_seconds。"
        "在 character_progress 中标明主角当集的压力反应，并跨两集安排反派的软肋、分歧或真实代价。"
        "必须继承 history_context 中的既有状态，不得改写已完成集。"
        "不得写完整正文或完整台词。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def outline_field_repair_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "你是短剧分集大纲编辑。仅补全输入 episode 中列出的 repair_fields，保持已生成的核心事件、"
        "人物进展、反转和悬念不变。顶层必须返回 {\"fields\": {...}}；fields 只能包含 repair_fields 中的键，"
        "每个值必须是非空纯文本字符串。不得重写整集或输出 Markdown。",
        prompt_snapshot,
        ),
        payload,
    )


def outline_field_rewrite_messages(
    payload: dict[str, Any],
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        "你是短剧分集大纲编辑。只重写 target_field 指定的一个字段。"
        "默认保留既定事件、人物动机、时间线和前后集衔接；只有 instruction 明确要求时才改变剧情事实。"
        "同集其他字段、相邻集、锁定 Brief 和人物关系只作为约束，不得输出或改写。"
        "只返回 {\"value\": \"目标字段新稿\"}，不得返回整集、其他字段或 Markdown。",
        prompt_snapshot,
        ),
        payload,
    )


def beats_messages(
    payload: dict[str, Any],
    episode_number: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        f"为第 {episode_number} 集生成 Beats。顶层必须返回 JSON 对象，严格使用 {{\"beats\": [...]}} 格式，"
        "不得直接输出数组。beats 数组每项包含 order、scene、characters、"
        "goal、obstacle、emotion_change、turn、hook、state_change、cost、opposition_tactic、continuity_updates。"
        "每集只能生成 1-6 个 Beats，order 必须从 1 连续编号。continuity_updates 是数组；每项包含 "
        "kind、name、event、holder、location、known_by、status，用于登记关键道具或关键信息的状态变化。"
        "不得重复使用录音、报警或口头反击结束同类冲突。不得生成完整正文或完整台词。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def beats_chunk_messages(
    payload: dict[str, Any],
    start: int,
    end: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        f"批量生成第 {start}-{end} 集 Beats。顶层必须返回 {{\"episodes\":[...]}}；episodes 必须完整覆盖 "
        f"第 {start}-{end} 集，每项包含 episode 和 beats。beats 数组每项包含 order、scene、characters、"
        "goal、obstacle、emotion_change、turn、hook、state_change、cost、opposition_tactic、continuity_updates。"
        "每集只能生成 1-6 个 Beats，order 必须从 1 连续编号。continuity_updates 是数组；每项包含 "
        "kind、name、event、holder、location、known_by、status。必须让本批后续集承接前一集的状态变化，"
        "并严格对应同集 locked_outlines 中的核心事件、冲突、人物推进、反转和悬念。"
        "不得重复使用录音、报警或口头反击结束同类冲突；不得生成完整正文或完整台词。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def script_messages(
    payload: dict[str, Any],
    episode_number: int,
    prompt_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    return _messages(
        _with_project_style(
        f"根据第 {episode_number} 集已锁定 Beats 生成可拍摄的场次剧本，返回 episode_title 和 scenes。"
        "每场必须包含 sequence_no、scene_no、time_of_day、interior_exterior、location、props、characters、blocks。"
        "sequence_no 和 scene_no 必须为正整数（如 1），不能写成“21-1”等集数-场次组合。"
        "blocks 为数组，type 只能是 action、dialogue、voiceover、inner_voice、cut；"
        "每个块必须有 text，dialogue 必须有 speaker，可选 modifier。"
        "动作写可拍摄内容，台词单独写；必须遵守 continuity_ledger 与锁定 Beats，展示人物行为的后果，"
        "不得用新增台词或道具绕开既定冲突代价；不得输出 Markdown。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def script_chunk_messages(
    payload: dict[str, Any],
    start: int,
    end: int,
    prompt_snapshot: dict[str, Any] | None = None,
    requested_episode_numbers: list[int] | None = None,
) -> list[dict[str, str]]:
    episode_scope = (
        "、".join(str(number) for number in requested_episode_numbers)
        if requested_episode_numbers is not None
        else f"{start}-{end}"
    )
    return _messages(
        _with_project_style(
        f"批量生成第 {episode_scope} 集可拍摄场次剧本。顶层必须返回 {{\"episodes\":[...]}}；episodes 必须且只能覆盖 "
        f"第 {episode_scope} 集，每项包含 episode、episode_title 和 scenes。每场必须包含 sequence_no、scene_no、"
        "time_of_day、interior_exterior、location、props、characters、blocks。sequence_no 和 scene_no 必须为正整数"
        "（如 1），不能写成“21-1”等集数-场次组合。blocks 为数组，type 只能是 "
        "action、dialogue、voiceover、inner_voice、cut；每个块必须有 text，dialogue 必须有 speaker，"
        "可选 modifier。严格遵守每集 locked_beats、locked_outlines、continuity_ledger，并让本批后续集承接前一集结果；"
        "动作写可拍摄内容，台词单独写，不得用新增台词或道具绕开既定冲突代价，不得输出 Markdown。" + DRAMA_CONSTRAINTS,
        prompt_snapshot,
        ),
        payload,
    )


def breakdown_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _messages(
        "你是严谨的短剧拆解分析师。只能依据输入的剧目元数据和逐集转写归纳，不得套用通用案例、"
        "补写未出现的剧情或伪造评论反馈。返回 content、field_sources、risks 三个字段。content 必须包含"
        " title、one_line_selling_point、category、target_audience、protagonist_identity、resistance_source、"
        "core_conflict、hook_points、selling_points、emotional_curve、reversal_nodes、comment_resonance、"
        "imitable_elements、production_difficulty、risk_notes。field_sources 按 content 字段名组织为证据数组，"
        "每条事实证据必须包含 episode_number、source_path、excerpt，source_path 必须原样使用输入转写的路径，"
        "excerpt 必须是该集转写中的原文短句。只有部分集数时，要在 risk_notes 和 risks 中明确覆盖缺口；"
        "没有评论正文时，comment_resonance 必须明确写出当前未提供评论正文，不能声称观众观点，且该字段的"
        " field_sources 返回空数组。title 的证据由系统根据剧目元数据补充，field_sources.title 也返回空数组。"
        "target_audience 在缺少评论画像或平台标签时，必须标注为基于现有转写的推断，"
        "并让 field_sources.target_audience 返回空数组。"
        "risk_notes 如果只记录集数覆盖或资料缺口，同样返回空证据数组，不能伪造原文摘录。"
        "数组字段必须返回 JSON 数组，文本字段必须返回字符串。",
        payload,
    )


def repair_messages(messages: list[dict[str, str]], error: str) -> list[dict[str, str]]:
    return messages + [{
        "role": "user",
        "content": f"上一次响应无法通过结构校验：{error}。请保持原任务，仅重新输出符合字段要求的 JSON。",
    }]
