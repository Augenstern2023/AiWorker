# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio

import pytest

from core.job import CrawlJob
from creative import phase4_worker
from creative.imitation_service import generate_blueprints
from creative.repository import CreativeRepository
from creative.script_service import (
    generate_character_bundle,
    generate_episode_beats,
    generate_outline_bundle,
)


class SequentialProvider:
    provider_name = "test-llm"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_json(self, messages):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def blueprint_payload(count: int = 3):
    return {
        "blueprints": [
            {
                "title": f"方向 {index}",
                "borrowed_elements": ["逆袭节奏"],
                "different_elements": ["职业和背景全部替换"],
                "protagonist": f"主角 {index}",
                "core_conflict": f"全新冲突 {index}",
                "main_goal": "主角在限定时间内修复社区信任",
                "background": "社区",
                "worldview": "现代都市",
                "first_three_hooks": ["误会", "反转", "结盟"],
                "highlights": ["身份反差"],
                "audience": "女性用户",
                "cost_level": "低",
                "risk_score": 20,
                "risk_reasons": [],
                "revision_suggestions": ["继续拉开差异"],
                "priority": index,
            }
            for index in range(1, count + 1)
        ],
        "provider": "test-llm",
    }


@pytest.mark.asyncio
async def test_blueprint_generation_repairs_invalid_response_once():
    provider = SequentialProvider([
        {"blueprints": []},
        blueprint_payload(),
    ])

    blueprints, meta = await generate_blueprints(
        provider,
        {"name": "项目"},
        {"title": "来源"},
        {"content": {"core_conflict": "来源冲突"}},
        3,
    )

    assert len(blueprints) == 3
    assert len(provider.calls) == 2
    assert meta["repair_attempted"] is True


@pytest.mark.asyncio
async def test_character_generation_requires_structured_people_and_relationships():
    provider = SequentialProvider([{
        "characters": [{
            "name": "周晴", "age_range": "25-30", "identity": "调解员",
            "external_tags": ["冷静"], "desire": "守住社区", "secret": "曾经逃避责任",
            "growth_arc": "从独自承担到信任伙伴", "dialogue_style": "简短直接",
            "appearance_prompt": "职业女性", "voice_prompt": "清晰坚定",
        }],
        "relationships": [{
            "from": "周晴", "to": "顾川", "relation": "对手",
            "conflict": "处理方式不同", "change_arc": "对抗转合作",
        }],
    }])

    bundle, meta = await generate_character_bundle(provider, {"name": "项目"}, {"title": "Brief"})

    assert bundle["characters"][0]["name"] == "周晴"
    assert bundle["relationships"][0]["relation"] == "对手"
    assert meta["provider"] == "test-llm"


def outline_episode(number: int) -> dict:
    return {
        "episode": number,
        "core_event": f"事件 {number}",
        "conflict": "立场冲突",
        "character_progress": "关系推进",
        "highlight": "反差爽点",
        "reversal": "误会解除",
        "cliffhanger": "新问题出现",
        "estimated_seconds": 120,
    }


@pytest.mark.asyncio
async def test_outline_generation_chunks_every_ten_episodes():
    provider = SequentialProvider([
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
        {"episodes": [outline_episode(number) for number in range(1, 11)]},
        {"episodes": [outline_episode(number) for number in range(11, 13)]},
    ])
    progress = []

    result, meta = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 12},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert len(provider.calls) == 3
    assert len(result["episodes"]) == 12
    assert progress == [(10, 12), (12, 12)]
    assert meta["chunk_size"] == 10


@pytest.mark.asyncio
async def test_beats_generation_rejects_wrong_episode_shape():
    provider = SequentialProvider([
        {"beats": [{
            "order": 1, "scene": "大厅", "characters": ["周晴"], "goal": "说服居民",
            "obstacle": "无人信任", "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
        }]},
    ])

    result, _ = await generate_episode_beats(
        provider,
        {"name": "项目"},
        {"content": {"episode": 1, "core_event": "首次交锋"}},
        [],
        1,
    )

    assert result["beats"][0]["scene"] == "大厅"


def seed_worker_project(repository: CreativeRepository) -> dict:
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "rank", "source_path": "rank.json",
        "captured_at": "2026-07-17", "title": "来源剧", "description": "逆袭",
        "category": "都市", "rank_type": "热播榜", "score": 90, "confidence": "high",
        "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"core_conflict": "来源冲突"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    return repository.create_project(topic["id"], breakdown["id"], "Worker 项目", 20)


def test_phase4_worker_persists_generation_and_report(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    provider = SequentialProvider([blueprint_payload()])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="creative-worker-1",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
        },
    )

    asyncio.run(phase4_worker.run(job))

    detail = repository.get_project(project["id"])
    report = tmp_path / "data" / "creative" / "jobs" / job.job_id / "generation.json"
    assert len(detail["blueprints"]) == 3
    assert detail["status"] == "direction_review"
    assert report.exists()
    assert "blueprints" in report.read_text(encoding="utf-8")


def test_phase4_worker_does_not_persist_partial_blueprints_after_llm_failure(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    provider = SequentialProvider([{"blueprints": []}, {"blueprints": []}])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="creative-worker-failed",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
        },
    )

    with pytest.raises(Exception, match="结构校验"):
        asyncio.run(phase4_worker.run(job))

    assert repository.get_project(project["id"])["blueprints"] == []
