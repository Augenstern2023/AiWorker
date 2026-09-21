# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from creative.hit_breakdown import breakdown_field_guide, build_breakdown
from creative.legacy_sqlite_repository import CreativeRepository
from creative.topic_service import TopicService


def test_topic_repository_persists_decision_and_breakdown_versions(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = {
        "platform": "dy", "job_id": "job-1", "source_path": "data/dy/rank.json", "captured_at": "2026-07-16",
        "title": "测试短剧", "category": "逆袭", "score": 82.5, "confidence": "high",
        "score_details": {"heat": 90}, "recommendation": "排名靠前", "evidence": [{"source_type": "snapshot"}],
    }
    saved = repository.upsert_topics([topic])[0]
    updated = repository.update_topic(saved["id"], "adopted", "运营确认")
    content, sources, risks = build_breakdown(updated)
    breakdown = repository.create_breakdown(saved["id"], content, sources, risks)
    locked = repository.lock_breakdown(breakdown["id"])

    assert updated["status"] == "adopted"
    assert locked["status"] == "locked"
    assert locked["content"]["title"] == "测试短剧"
    assert repository.list_topics(status="adopted", has_locked_breakdown=True)[0]["locked_breakdown_id"] == locked["id"]
    assert repository.list_topics(status="adopted", has_locked_breakdown=False) == []


def test_breakdown_template_explains_every_editable_field():
    content, _, _ = build_breakdown({
        "title": "测试短剧", "category": "家庭", "recommendation": "排名靠前", "evidence": [],
    })
    guide = breakdown_field_guide()

    assert set(content) == {item["key"] for item in guide}
    assert {"resistance_source", "emotional_curve", "reversal_nodes"} <= set(content)
    assert all(item["label"] and item["description"] and item["writing_prompt"] and item["example"] for item in guide)
    assert next(item for item in guide if item["key"] == "core_conflict")["evidence_rule"]


def test_topic_service_reweights_when_growth_is_unavailable(tmp_path):
    class FakeRankService:
        def get_today_rank(self, platform, date, limit):
            return {
                "date": "2026-07-16", "selected_snapshot": {"job_id": "job-1", "source_path": "data/dy/rank.json"},
                "previous_snapshot": None,
                "current_entries": [{"title": "测试短剧", "rank": 1, "category": "逆袭"}],
            }

    service = TopicService(str(tmp_path), rank_service=FakeRankService())
    topic = service.generate("dy", limit=1)[0]

    assert topic["confidence"] == "medium"
    assert topic["score_details"]["growth"] is None
    assert topic["score_details"]["available_weight"] == 40
    assert topic["category"] == "待补充"


def test_topic_service_uses_subject_as_genre_not_rank_board():
    class FakeRankService:
        def get_today_rank(self, platform, date, limit):
            return {
                "date": "2026-07-16", "selected_snapshot": {"job_id": "job-1", "source_path": "data/dy/rank.json"},
                "previous_snapshot": None,
                "current_entries": [{"title": "测试短剧", "rank": 1, "category_label": "短剧", "tab": "热榜", "subject": "复仇 / 逆袭"}],
            }

    service = TopicService(".", rank_service=FakeRankService())

    topic = service.generate("dy", limit=1)[0]
    assert topic["category"] == "复仇 / 逆袭"
    assert topic["rank_type"] == "热榜"
    assert "description" in topic


def test_topic_repository_deduplicates_same_title_and_marks_platforms(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    base = {
        "job_id": "job-dy", "source_path": "data/dy/rank.json", "captured_at": "2026-07-16",
        "title": "同名短剧", "category": "逆袭", "score": 80, "confidence": "medium",
        "score_details": {"heat": 80}, "recommendation": "抖音排名靠前",
        "evidence": [{"source_type": "snapshot", "platform": "dy", "excerpt": "抖音排名 1"}],
    }
    repository.upsert_topics([{**base, "platform": "dy"}])
    repository.upsert_topics([{**base, "platform": "hg", "job_id": "job-hg", "source_path": "data/hg/rank.json", "score": 85, "recommendation": "红果排名靠前", "evidence": [{"source_type": "snapshot", "platform": "hg", "excerpt": "红果排名 2"}]}])

    items = repository.list_topics()

    assert len(items) == 1
    assert items[0]["source_platforms"] == ["dy", "hg"]
    assert {item["platform"] for item in items[0]["evidence"]} == {"dy", "hg"}
