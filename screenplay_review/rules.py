# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Versioned deterministic decision rules for SemIf screenplay reviews."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .schema import Scope


RULE_PACKAGE = "short-drama-semif-v1"
GATE_OPTIONS = ("passed", "needs_completion", "severe_missing", "insufficient_evidence")
SCORE_OPTIONS = ("excellent", "good", "needs_revision", "failed", "insufficient_evidence")
SCORE_OPTION_VALUES = {"excellent": 3, "good": 2, "needs_revision": 1, "failed": 0}


@dataclass(frozen=True)
class DecisionRule:
    id: str
    label: str
    scope: Scope
    kind: Literal["hard_gate", "score"]
    weight: int = 0
    option_ids: tuple[str, ...] = GATE_OPTIONS
    option_scores: dict[str, int] = field(default_factory=dict)

    def to_contract(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "scope": self.scope,
            "kind": self.kind,
            "weight": self.weight,
            "option_ids": list(self.option_ids),
        }


HARD_GATES = (
    DecisionRule("script_completeness", "剧本完整性", "project", "hard_gate"),
    DecisionRule("mainline_clarity", "主线清晰度", "project", "hard_gate"),
    DecisionRule("core_logic", "核心逻辑", "project", "hard_gate"),
    DecisionRule("content_compliance", "内容合规", "project", "hard_gate"),
)


def _score_rule(rule_id: str, label: str, scope: Scope, weight: int) -> DecisionRule:
    return DecisionRule(
        rule_id,
        label,
        scope,
        "score",
        weight,
        SCORE_OPTIONS,
        dict(SCORE_OPTION_VALUES),
    )


SCORING_DIMENSIONS = (
    _score_rule("core_logic_score", "核心逻辑质量", "project", 15),
    _score_rule("mainline_clarity_score", "主线清晰度", "project", 10),
    _score_rule("character_motivation", "人物动机", "project", 10),
    _score_rule("conflict_strength", "冲突强度", "episode", 12),
    _score_rule("pacing_density", "节奏密度", "episode", 10),
    _score_rule("episode_hook", "集尾钩子", "episode", 12),
    _score_rule("opening_hook", "开篇钩子", "episode", 10),
    _score_rule("character_arc", "人物弧光", "project", 8),
    _score_rule("dialogue_effectiveness", "台词有效性", "scene", 5),
    _score_rule("visual_feasibility", "视觉可执行性", "scene", 5),
    _score_rule("commercial_fit", "商业适配度", "project", 3),
)

ALL_DIMENSIONS = (*HARD_GATES, *SCORING_DIMENSIONS)
RULES_BY_ID = {item.id: item for item in ALL_DIMENSIONS}


def decision_set_for(scope: Scope) -> tuple[DecisionRule, ...]:
    """Return only the rules evaluated at one hierarchy level."""

    return tuple(item for item in ALL_DIMENSIONS if item.scope == scope)
