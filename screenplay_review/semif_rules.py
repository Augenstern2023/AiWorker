# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Versioned Chinese screenplay criteria passed to SemIf at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .semif_contract import SemIfCriterion, SemIfOption


RuleScope = Literal["project", "episode", "scene"]
RuleKind = Literal["hard_gate", "quality"]


@dataclass(frozen=True)
class SemIfRule:
    id: str
    label: str
    scope: RuleScope
    kind: RuleKind
    question: str

    def to_criterion(self) -> SemIfCriterion:
        if self.kind == "hard_gate":
            options = HARD_GATE_OPTIONS
        else:
            options = QUALITY_OPTIONS
        return SemIfCriterion(
            id=self.id,
            question=self.question,
            options=[SemIfOption(id=item[0], description=item[1]) for item in options],
        )


HARD_GATE_OPTIONS = (
    ("passed", "所给材料明确满足这项要求，没有发现会阻断审核的缺失或矛盾。"),
    ("needs_revision", "材料基本涉及这项要求，但存在需要人工修改的重要缺口。"),
    ("failed", "材料明确不满足这项要求，或存在足以阻断通过的严重问题。"),
    ("insufficient_evidence", "当前材料不足以判断这项要求是否满足。"),
)

QUALITY_OPTIONS = (
    ("excellent", "材料在这一维度表现突出、清晰且有效，未见实质问题。"),
    ("good", "材料在这一维度总体有效，仅有轻微问题，不影响主要效果。"),
    ("needs_revision", "材料在这一维度有可识别基础，但存在影响效果的重要问题。"),
    ("failed", "材料在这一维度明显无效、缺失或自相矛盾。"),
    ("insufficient_evidence", "当前材料不足以评价这一维度。"),
)


RULES = (
    SemIfRule(
        "script_completeness",
        "剧本完整性",
        "project",
        "hard_gate",
        "所给剧本材料是否具备可审核的完整故事信息，而不是明显缺页、空场或只有提纲？",
    ),
    SemIfRule(
        "mainline_clarity",
        "主线清晰度",
        "project",
        "hard_gate",
        "所给材料是否能清楚识别主角目标、核心阻力和主要剧情推进方向？",
    ),
    SemIfRule(
        "core_logic",
        "核心逻辑",
        "project",
        "hard_gate",
        "所给材料的关键因果、人物行动和情节转折是否基本自洽，没有阻断理解的矛盾？",
    ),
    SemIfRule(
        "content_compliance",
        "内容合规初筛",
        "project",
        "hard_gate",
        "仅根据所给材料，是否未发现明显需要升级人工合规审查的高风险内容？",
    ),
    SemIfRule(
        "core_logic_quality",
        "核心逻辑质量",
        "project",
        "quality",
        "关键事件之间的因果连接是否自然、充分，并能支撑故事继续发展？",
    ),
    SemIfRule(
        "mainline_focus",
        "主线聚焦度",
        "project",
        "quality",
        "当前材料是否围绕主要目标推进，避免无关支线冲淡核心冲突？",
    ),
    SemIfRule(
        "character_motivation",
        "人物动机",
        "project",
        "quality",
        "人物的关键选择是否有材料内可见的欲望、利益、关系或压力作为动机？",
    ),
    SemIfRule(
        "conflict_strength",
        "冲突强度",
        "episode",
        "quality",
        "本段冲突是否具体、有对抗性，并会给人物带来明确后果或选择压力？",
    ),
    SemIfRule(
        "pacing_density",
        "节奏密度",
        "episode",
        "quality",
        "本段是否持续产生有效信息、行动或局势变化，避免明显拖沓和重复？",
    ),
    SemIfRule(
        "episode_hook",
        "集尾钩子",
        "episode",
        "quality",
        "若材料包含本集结尾，结尾是否形成明确悬念、威胁、承诺或下一集观看动力？",
    ),
    SemIfRule(
        "opening_hook",
        "开篇钩子",
        "episode",
        "quality",
        "若材料包含本集开篇，开篇是否迅速建立异常、冲突、目标或强烈信息差？",
    ),
    SemIfRule(
        "character_progression",
        "人物推进",
        "project",
        "quality",
        "人物关系、认知、处境或行动策略是否因当前事件产生可识别的推进？",
    ),
    SemIfRule(
        "dialogue_effectiveness",
        "台词有效性",
        "scene",
        "quality",
        "台词是否符合说话者身份并承担推进、冲突或信息揭示功能，而非重复说明？",
    ),
    SemIfRule(
        "visual_feasibility",
        "视觉可执行性",
        "scene",
        "quality",
        "动作、场面和信息表达是否能通过可拍摄的画面或声音清楚呈现？",
    ),
    SemIfRule(
        "short_drama_payoff",
        "短剧爽点兑现",
        "episode",
        "quality",
        "材料是否对前置压迫、期待或信息差给出清晰的反击、反转或情绪回报？",
    ),
)

RULES_BY_ID = {rule.id: rule for rule in RULES}
OPTION_LABELS = {
    "passed": "满足",
    "excellent": "突出",
    "good": "良好",
    "needs_revision": "需要修改",
    "failed": "不满足",
    "insufficient_evidence": "材料不足",
}


def criteria() -> list[SemIfCriterion]:
    return [rule.to_criterion() for rule in RULES]
