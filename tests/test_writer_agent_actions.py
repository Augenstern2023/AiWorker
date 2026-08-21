# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from writer_agent.action_service import (
    build_source_snapshot,
    match_natural_language_decision,
    validate_cards,
)
from writer_agent.schema import DirectionCard, ProposedAction, WriterActionError


def pending_action(
    action_id: str, action_type: str, *, target_stage: str | None = None
) -> dict[str, Any]:
    return {
        "id": action_id,
        "action_type": action_type,
        "status": "pending",
        "revision": 1,
        "target_stage": target_stage,
    }


def direction(
    title: str, protagonist: str, background: str, conflict: str
) -> dict[str, Any]:
    return {
        "title": title,
        "protagonist": protagonist,
        "background": background,
        "core_conflict": conflict,
        "relationship_engine": f"{protagonist}与同盟互相制衡",
        "emotional_payoff": f"{protagonist}夺回选择权",
        "difference_dimensions": ["protagonist", "background", "conflict"],
    }


def test_model_cannot_propose_an_unknown_or_trusted_write_action() -> None:
    with pytest.raises(ValidationError):
        ProposedAction.model_validate({
            "action_type": "project.delete",
            "title": "删除项目",
            "payload": {"project_id": 7},
        })
    with pytest.raises(ValidationError):
        ProposedAction.model_validate({
            "action_type": "creative.run_plan",
            "title": "生成大纲",
            "target_stage": "outline",
            "target_key": "model-controlled",
        })


def test_model_cannot_nest_server_trusted_fields_in_action_payload() -> None:
    with pytest.raises(ValidationError):
        ProposedAction.model_validate({
            "action_type": "creative.run_plan",
            "title": "生成大纲",
            "target_stage": "outline",
            "payload": {
                "plan": {
                    "steps": [{"actor": "model-controlled"}],
                    "permissions": ["*"],
                },
            },
        })


def test_confirmation_requires_one_matching_pending_action() -> None:
    actions = [
        pending_action("a", "creative.run_plan", target_stage="outline"),
        pending_action("b", "creative.lock", target_stage="brief"),
    ]

    assert match_natural_language_decision("确认生成大纲", actions).action_id == "a"
    with pytest.raises(WriterActionError, match="ACTION_AMBIGUOUS"):
        match_natural_language_decision("继续", actions)


def test_confirmation_with_new_constraint_requires_revision() -> None:
    decision = match_natural_language_decision(
        "确认生成大纲，但人物关系改成师徒对立",
        [pending_action("a", "creative.run_plan", target_stage="outline")],
    )

    assert decision.action_id == "a"
    assert decision.revision_required is True


def test_confirmation_with_a_negative_constraint_is_not_a_cancellation() -> None:
    decision = match_natural_language_decision(
        "确认生成大纲，但不要虐主",
        [pending_action("a", "creative.run_plan", target_stage="outline")],
    )

    assert decision.action_id == "a"
    assert decision.decision == "confirm"
    assert decision.revision_required is True


def test_direction_card_rejects_declared_but_not_actual_differences() -> None:
    card = DirectionCard.model_validate({
        "card_id": "card-directions-1",
        "items": [
            direction("情绪公司", "林岚", "未来公司", "情绪考核"),
            direction("潮汐公约", "林岚", "未来公司", "情绪考核"),
            direction("满分之外", "许真", "虚拟学校", "人格评分"),
        ],
    })

    with pytest.raises(WriterActionError, match="RESULT_VALIDATION_FAILED"):
        validate_cards([card])


def test_direction_card_rejects_a_candidate_with_untrue_difference_declaration() -> None:
    first = direction("情绪公司", "林岚", "未来公司", "情绪考核")
    second = direction("潮汐公约", "周屿", "海上社区", "情绪考核")
    second["difference_dimensions"] = ["conflict", "relationship"]
    third = direction("满分之外", "许真", "虚拟学校", "人格评分")
    card = DirectionCard.model_validate({
        "card_id": "card-directions-2", "items": [first, second, third]
    })

    with pytest.raises(WriterActionError, match="RESULT_VALIDATION_FAILED"):
        validate_cards([card])


def test_source_snapshot_normalizes_content_and_records_reference_permission() -> None:
    common = dict(
        selected_messages=[],
        project_context={"locked_brief": {"id": 9, "version": 2}},
        reference_refs=[{
            "resource_type": "topic", "resource_id": 7, "name": "职场榜单",
            "version": 3, "readable": True,
        }],
        assumptions=[], model_version="writer-v1", prompt_version="creative-v1",
    )
    windows = build_source_snapshot(materials=[{"name": "旧剧本.md", "content": "第一版\r\n第二行"}], **common)
    unix = build_source_snapshot(materials=[{"name": "旧剧本.md", "content": "第一版\n第二行"}], **common)

    assert windows["content_fingerprint"] == unix["content_fingerprint"]
    assert windows["references"] == [{
        "resource_type": "topic", "resource_id": 7, "name": "职场榜单",
        "version": 3, "readable": True,
    }]
