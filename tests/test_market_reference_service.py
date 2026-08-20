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

from creative.repository import CreativeRepository


def seed_reference_material(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "market-ref", "source_path": "rank.json",
        "captured_at": "2026-08-20T08:00:00", "title": "顾家逆袭录",
        "description": "顾清歌被逐出顾家后复仇", "category": "都市",
        "rank_type": "热播榜", "score": 91, "confidence": "high",
        "score_details": {}, "recommendation": "建议关注主角反击",
        "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {
        "category": "都市", "target_audience": "25-45 岁女性",
        "emotional_curve": ["受压", "反击", "扬眉吐气"],
        "production_difficulty": "中",
        "protagonist_identity": "顾清歌", "core_conflict": "顾清歌争夺顾家继承权",
        "hook_points": ["你不配进顾家"], "reversals": ["养女才是真继承人"],
        "dialogue": "你不配进顾家",
    }, {}, [])
    breakdown = repository.lock_breakdown(breakdown["id"])
    assert breakdown is not None
    return repository, repository.get_topic(topic["id"]), breakdown


def test_market_context_excludes_story_specific_content(tmp_path):
    from creative.market_reference_service import MarketReferenceService

    repository, _, breakdown = seed_reference_material(tmp_path)
    bundle = MarketReferenceService(repository).resolve(
        [{"resource_type": "breakdown", "resource_id": breakdown["id"]}],
        can_read=lambda _kind, _identifier: True,
    )

    serialized = json.dumps(bundle.context, ensure_ascii=False)
    assert "都市" in serialized
    assert "25-45 岁女性" in serialized
    assert "顾清歌" not in serialized
    assert "你不配进顾家" not in serialized
    assert "顾清歌" in bundle.blocked_terms
    assert bundle.source_refs == [{"resource_type": "breakdown", "resource_id": breakdown["id"]}]


def test_market_context_rejects_unreadable_reference(tmp_path):
    from creative.market_reference_service import MarketReferenceService

    repository, _, breakdown = seed_reference_material(tmp_path)
    with pytest.raises(PermissionError, match=f"{breakdown['id']}"):
        MarketReferenceService(repository).resolve(
            [{"resource_type": "breakdown", "resource_id": breakdown["id"]}],
            can_read=lambda _kind, _identifier: False,
        )


def test_market_context_deduplicates_and_rejects_missing_or_too_many(tmp_path):
    from creative.market_reference_service import MarketReferenceService

    repository, topic, _ = seed_reference_material(tmp_path)
    service = MarketReferenceService(repository)
    repeated = service.resolve([
        {"resource_type": "topic", "resource_id": topic["id"]},
        {"resource_type": "topic", "resource_id": topic["id"]},
    ], can_read=lambda _kind, _identifier: True)
    assert len(repeated.context) == 1
    assert repeated.context[0]["category"] == "都市"

    with pytest.raises(ValueError, match="999999"):
        service.resolve(
            [{"resource_type": "topic", "resource_id": 999999}],
            can_read=lambda _kind, _identifier: True,
        )
    with pytest.raises(ValueError, match="最多 10"):
        service.resolve(
            [{"resource_type": "topic", "resource_id": index} for index in range(1, 12)],
            can_read=lambda _kind, _identifier: True,
        )
