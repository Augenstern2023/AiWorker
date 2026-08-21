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
    filter_project_authority_output,
    match_natural_language_decision,
    parse_creative_cards,
    validate_project_authority,
    validate_cards,
)
from writer_agent.schema import (
    BriefCard,
    DirectionCard,
    ProposedAction,
    ResultCard,
    WriterActionError,
)


def test_locked_brief_allows_only_explicit_current_brief_rework() -> None:
    card = BriefCard.model_validate({
        "card_id": "brief-rework",
        "content": {"title": "新版 Brief"},
    })
    action = ProposedAction.model_validate({
        "action_type": "creative.run_plan",
        "title": "重做 Brief",
        "source_card_id": card.card_id,
        "target_stage": "brief",
        "payload": {
            "rework_stage": "brief",
            "replaces_resource_id": "9",
        },
    })
    authority = {
        "project_id": 17,
        "locked_brief": {"id": 9, "version": 2, "content": {}},
    }

    validate_project_authority([card], [action], authority)

    stale = action.model_copy(
        update={
            "payload": {
                "rework_stage": "brief",
                "replaces_resource_id": "8",
            }
        }
    )
    with pytest.raises(WriterActionError, match="LOCKED_RESULT_CONFLICT"):
        validate_project_authority([card], [stale], authority)

    stale_nested = action.model_copy(
        update={
            "payload": {
                "rework_stage": "brief",
                "replaces_resource_id": "9",
                "step_payloads": {
                    "brief": {"replaces_resource_id": "8"},
                },
            }
        }
    )
    with pytest.raises(WriterActionError, match="LOCKED_RESULT_CONFLICT"):
        validate_project_authority([card], [stale_nested], authority)


def test_authority_filter_drops_ambiguous_duplicate_card_ids_only() -> None:
    cards = parse_creative_cards({
        "cards": [
            {
                "card_id": "duplicate",
                "card_type": "brief",
                "content": {"title": "不得借重复 ID 绕过"},
            },
            {
                "card_id": "duplicate",
                "card_type": "plan",
                "title": "伪装计划",
                "summary": "重复 ID",
                "steps": [{"stage": "outline"}],
            },
            {
                "card_id": "valid-plan",
                "card_type": "plan",
                "title": "合法计划",
                "summary": "继续生成大纲",
                "steps": [{"stage": "outline"}],
            },
        ]
    })
    actions = [
        ProposedAction.model_validate({
            "action_type": "creative.run_plan",
            "title": "重复来源动作",
            "source_card_id": "duplicate",
            "target_stage": "outline",
            "payload": {},
        }),
        ProposedAction.model_validate({
            "action_type": "creative.run_plan",
            "title": "合法大纲动作",
            "source_card_id": "valid-plan",
            "target_stage": "outline",
            "payload": {},
        }),
    ]

    allowed_cards, allowed_actions = filter_project_authority_output(
        cards,
        actions,
        {
            "project_id": 17,
            "locked_brief": {
                "id": 101,
                "version": 1,
                "content": {"title": "已锁定 Brief"},
            },
        },
    )

    assert [card.card_id for card in allowed_cards] == ["valid-plan"]
    assert [action.source_card_id for action in allowed_actions] == ["valid-plan"]


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


@pytest.mark.parametrize(
    "open_url",
    [
        "javascript:alert(document.domain)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//attacker.example/project/7",
        "https://attacker.example/project/7",
    ],
)
def test_result_card_rejects_non_local_project_urls(open_url: str) -> None:
    """A model-controlled result link must never escape the application origin."""

    with pytest.raises(ValidationError):
        ResultCard.model_validate({
            "card_id": "result-xss",
            "project_id": 7,
            "resource_refs": [],
            "open_url": open_url,
        })


@pytest.mark.parametrize("card_type", ["progress", "result"])
def test_model_cannot_supply_server_owned_action_cards(card_type: str) -> None:
    card = (
        {
            "card_id": "model-progress",
            "card_type": "progress",
            "action_id": "forged-action",
            "status": "succeeded",
            "completed_steps": 99,
            "total_steps": 99,
        }
        if card_type == "progress"
        else {
            "card_id": "model-result",
            "card_type": "result",
            "project_id": 999,
            "resource_refs": [{"resource_type": "script", "resource_id": "forged"}],
            "open_url": "/writing/projects/999/outlines",
        }
    )

    with pytest.raises(WriterActionError, match="RESULT_VALIDATION_FAILED"):
        parse_creative_cards({"cards": [card]})


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
