# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json

import pytest

from creative.breakdown_service import generate_breakdown
from creative.hit_breakdown import personalize_breakdown_field_guide


class SequentialProvider:
    provider_name = "test-llm"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


def transcript_bundle() -> dict:
    return {
        "title": "穿成炮灰后：不是反派吗？怎么要抱抱",
        "platform": "hg",
        "episodes": [{
            "episode_number": 1,
            "text": "第一集真实剧情：苏软穿书后认错了反派顾宴，并被迫与他同行。",
            "transcript_source": "script",
            "script_path": "data/hg/jobs/job-1/dramas/目标剧/episode_01/script.md",
            "transcript_path": "",
            "metadata_path": "data/hg/jobs/job-1/dramas/目标剧/episode_01/source.json",
        }],
        "searched_paths": [],
    }


def personalized_breakdown_payload(*, source_path: str | None = None) -> dict:
    path = source_path or transcript_bundle()["episodes"][0]["script_path"]
    content = {
        "title": "穿成炮灰后：不是反派吗？怎么要抱抱",
        "one_line_selling_point": "穿书炮灰苏软认错反派后，在危险同行中改写必死结局。",
        "category": "穿书 / 甜宠 / 反转",
        "target_audience": "偏好穿书设定、身份错认和反差关系的女性观众（基于剧情推断）。",
        "protagonist_identity": "苏软，穿进书中且面临死亡结局的炮灰女配。",
        "resistance_source": "苏软对书中人物的错误认知，以及与顾宴同行带来的持续危险。",
        "core_conflict": "苏软要避开原定死亡结局，却因认错顾宴身份不断卷入危险。",
        "hook_points": ["第1集：苏软认错反派顾宴并被迫同行；她能否及时发现身份错误？"],
        "selling_points": ["苏软以为掌握剧情，却因认错关键人物形成连续认知反差。"],
        "emotional_curve": ["穿书惊慌 -> 认错后的短暂笃定 -> 被迫同行带来的紧张"],
        "reversal_nodes": ["第1集：苏软以为顾宴是反派，同行后发现自己的书中认知并不可靠。"],
        "comment_resonance": "未知：当前仅有剧目转写，未提供评论正文，无法验证评论共鸣。",
        "imitable_elements": ["可借鉴认知错位推动关系的机制，但需替换人物、世界观和具体事件。"],
        "production_difficulty": "低：当前证据只涉及少量人物与同行场景，完整成本待更多集转写复核。",
        "risk_notes": ["当前仅有第1集转写，后续主线、反转和制作规模存在证据缺口。"],
    }
    evidence = {
        "episode_number": 1,
        "source_path": path,
        "excerpt": "苏软穿书后认错了反派顾宴，并被迫与他同行",
    }
    return {
        "content": content,
        "field_sources": {key: [dict(evidence)] for key in content},
        "risks": ["仅覆盖第1集，未覆盖集数不得推断为已发生事实。"],
        "provider": "test-llm",
    }


@pytest.mark.asyncio
async def test_generation_uses_transcripts_and_returns_field_evidence():
    provider = SequentialProvider([personalized_breakdown_payload()])

    content, sources, risks, meta = await generate_breakdown(
        provider,
        {"title": transcript_bundle()["title"], "category": "甜宠", "description": ""},
        transcript_bundle(),
    )

    assert "苏软" in content["protagonist_identity"]
    assert sources["protagonist_identity"][0]["episode_number"] == 1
    assert risks == ["仅覆盖第1集，未覆盖集数不得推断为已发生事实。"]
    assert meta["coverage"] == [1]
    prompt_payload = json.loads(provider.calls[0][1]["content"])
    assert "第一集真实剧情" in prompt_payload["transcripts"][0]["text"]


@pytest.mark.asyncio
async def test_generation_repairs_evidence_that_is_not_in_the_bundle():
    invalid = personalized_breakdown_payload(source_path="data/other/script.md")
    provider = SequentialProvider([invalid, personalized_breakdown_payload()])

    _, sources, _, meta = await generate_breakdown(
        provider,
        {"title": transcript_bundle()["title"]},
        transcript_bundle(),
    )

    assert len(provider.calls) == 2
    assert meta["repair_attempted"] is True
    assert sources["core_conflict"][0]["source_path"].endswith("script.md")


@pytest.mark.asyncio
async def test_generation_rejects_comment_claims_without_comment_evidence():
    invalid = personalized_breakdown_payload()
    invalid["content"]["comment_resonance"] = "观众都喜欢苏软与顾宴的互动。"
    provider = SequentialProvider([invalid, personalized_breakdown_payload()])

    content, _, _, meta = await generate_breakdown(
        provider,
        {"title": transcript_bundle()["title"]},
        transcript_bundle(),
    )

    assert meta["repair_attempted"] is True
    assert "未提供评论正文" in content["comment_resonance"]


@pytest.mark.asyncio
async def test_generation_does_not_require_transcript_excerpt_for_missing_comments():
    payload = personalized_breakdown_payload()
    payload["field_sources"]["comment_resonance"] = [{
        "episode_number": 1,
        "source_path": transcript_bundle()["episodes"][0]["script_path"],
        "excerpt": "当前未提供评论正文",
    }]
    provider = SequentialProvider([payload])

    _, sources, _, meta = await generate_breakdown(
        provider,
        {"title": transcript_bundle()["title"]},
        transcript_bundle(),
    )

    assert sources["comment_resonance"] == []
    assert meta["repair_attempted"] is False


@pytest.mark.asyncio
async def test_generation_allows_evidence_gap_risk_notes_without_fake_excerpt():
    payload = personalized_breakdown_payload()
    payload["field_sources"]["risk_notes"] = []
    provider = SequentialProvider([payload])

    content, sources, _, meta = await generate_breakdown(
        provider,
        {"title": transcript_bundle()["title"]},
        transcript_bundle(),
    )

    assert content["risk_notes"]
    assert sources["risk_notes"] == []
    assert meta["repair_attempted"] is False


def test_field_examples_come_from_current_breakdown():
    guide = personalize_breakdown_field_guide({
        "protagonist_identity": "苏软，穿书女配",
        "hook_points": ["第1集：认错反派"],
    })

    assert next(item for item in guide if item["key"] == "protagonist_identity")["example"] == "苏软，穿书女配"
    assert next(item for item in guide if item["key"] == "hook_points")["example"] == "第1集：认错反派"
    assert next(item for item in guide if item["key"] == "core_conflict")["example"] == "当前转写证据不足，暂未生成示例"
    assert "被婆家轻视" not in str(guide)
