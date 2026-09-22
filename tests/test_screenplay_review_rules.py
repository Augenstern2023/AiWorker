# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from screenplay_review.rules import (
    ALL_DIMENSIONS,
    HARD_GATES,
    RULE_PACKAGE,
    SCORING_DIMENSIONS,
    decision_set_for,
)


def test_review_rules_have_four_gates_and_weighted_dimensions() -> None:
    assert RULE_PACKAGE == "short-drama-semif-v1"
    assert {item.id for item in HARD_GATES} == {
        "script_completeness",
        "mainline_clarity",
        "core_logic",
        "content_compliance",
    }
    assert sum(item.weight for item in SCORING_DIMENSIONS) == 100
    assert all("insufficient_evidence" in item.option_ids for item in ALL_DIMENSIONS)
    assert all(item.option_scores.get("excellent") == 3 for item in SCORING_DIMENSIONS)


def test_decision_sets_are_partitioned_by_scope() -> None:
    project_ids = {item.id for item in decision_set_for("project")}
    episode_ids = {item.id for item in decision_set_for("episode")}
    scene_ids = {item.id for item in decision_set_for("scene")}

    assert {item.id for item in HARD_GATES} <= project_ids
    assert "episode_hook" in episode_ids
    assert "dialogue_effectiveness" in scene_ids
    assert not (episode_ids & scene_ids)
