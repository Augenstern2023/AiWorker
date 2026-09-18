# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from core.job import CrawlJob
from creative import breakdown_worker
from creative.postgres_repository import PostgresCreativeRepository


class Provider:
    provider_name = "test-llm"

    def __init__(self, response: dict):
        self.response = response

    async def complete_json(self, messages):
        return self.response


class SlowProvider(Provider):
    async def complete_json(self, messages):
        await asyncio.sleep(0.04)
        return self.response


def seed_topic(repository) -> tuple[dict, dict]:
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "rank", "source_path": "rank.json",
        "captured_at": "2026-07-20", "title": "目标剧", "description": "",
        "category": "穿书", "rank_type": "热播榜", "score": 90, "confidence": "high",
        "score_details": {}, "recommendation": "值得拆解", "evidence": [],
    }])[0]
    locked = repository.create_breakdown(topic["id"], {"core_conflict": "旧内容"}, {}, [])
    return topic, repository.lock_breakdown(locked["id"])


def write_script(root: Path) -> Path:
    episode = root / "data/hg/jobs/job-1/dramas/目标剧/episode_01"
    episode.mkdir(parents=True)
    (episode / "source.json").write_text(
        json.dumps({"title": "目标剧", "episode": {"episode": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    path = episode / "script.md"
    path.write_text("## 转写内容\n\n苏软穿书后认错顾宴，并被迫与他同行。", encoding="utf-8")
    return path


def write_video(root: Path) -> tuple[Path, Path]:
    episode = root / "data/hg/jobs/job-video/dramas/目标剧/episode_01"
    episode.mkdir(parents=True)
    source = episode / "source.json"
    source.write_text(
        json.dumps({"title": "目标剧", "episode": {"episode": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    video = episode / "video.mp4"
    video.write_bytes(b"video")
    return video, source


def generated_payload(script_path: Path) -> dict:
    content = {
        "title": "目标剧", "one_line_selling_point": "苏软认错顾宴后改写命运。",
        "category": "穿书 / 反转", "target_audience": "偏好穿书反差的观众（剧情推断）。",
        "protagonist_identity": "苏软，穿书炮灰。", "resistance_source": "错误认知与顾宴。",
        "core_conflict": "苏软要改写命运，却因认错顾宴陷入危险。",
        "hook_points": ["第1集：苏软认错顾宴。"], "selling_points": ["认知错位形成反差。"],
        "emotional_curve": ["惊慌 -> 笃定 -> 紧张"], "reversal_nodes": ["第1集：顾宴身份与预期不同。"],
        "comment_resonance": "未知：当前未提供评论正文，无法验证评论共鸣。",
        "imitable_elements": ["借鉴认知错位机制，替换人物和事件。"],
        "production_difficulty": "低：当前仅涉及两名主要人物。",
        "risk_notes": ["仅有第1集转写，后续剧情证据不足。"],
    }
    evidence = {
        "episode_number": 1,
        "source_path": str(script_path),
        "excerpt": "苏软穿书后认错顾宴，并被迫与他同行",
    }
    return {
        "content": content,
        "field_sources": {key: [dict(evidence)] for key in content},
        "risks": ["仅覆盖第1集。"],
    }


def test_breakdown_worker_persists_personalized_draft_and_completion_event(
    tmp_path, monkeypatch, worker_system_database
):
    repository = PostgresCreativeRepository(worker_system_database)
    topic, locked = seed_topic(repository)
    script_path = write_script(tmp_path)
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: Provider(generated_payload(script_path)))
    events = []
    monkeypatch.setattr(breakdown_worker, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    job = CrawlJob(
        job_id="breakdown-1", platform="creative", crawler_type="breakdown_generate",
        extra={
            "topic_id": topic["id"],
            "runtime_dir": str(tmp_path / ".runtime"), "workspace_root": str(tmp_path),
        },
    )

    asyncio.run(breakdown_worker.run(job))

    draft = repository.get_latest_breakdown(topic["id"])
    assert draft["status"] == "draft"
    assert draft["parent_version_id"] == locked["id"]
    assert draft["content"]["protagonist_identity"] == "苏软，穿书炮灰。"
    assert draft["content"] != locked["content"]
    report = tmp_path / "data/creative/jobs/breakdown-1/generation.json"
    assert report.exists()
    finished = next(args for args, _ in events if args[0] == "job_finished")
    assert finished[6] == {
        "action": "breakdown", "breakdown_id": draft["id"], "topic_id": topic["id"], "coverage": [1],
    }


def test_breakdown_worker_does_not_create_partial_draft_on_generation_failure(
    tmp_path, monkeypatch, worker_system_database
):
    repository = PostgresCreativeRepository(worker_system_database)
    topic, _ = seed_topic(repository)
    script_path = write_script(tmp_path)
    invalid = generated_payload(script_path)
    invalid["content"].pop("core_conflict")
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: Provider(invalid))
    job = CrawlJob(
        job_id="breakdown-failed", platform="creative", crawler_type="breakdown_generate",
        extra={"topic_id": topic["id"], "workspace_root": str(tmp_path)},
    )

    try:
        asyncio.run(breakdown_worker.run(job))
    except Exception:
        pass

    with worker_system_database.connection() as connection:
        count = connection.execute(
            text(
                "SELECT COUNT(*) FROM creative_breakdowns WHERE topic_id = :topic_id"
            ),
            {"topic_id": topic["id"]},
        ).scalar_one()
    assert count == 1


def test_breakdown_worker_emits_model_analysis_heartbeats(
    tmp_path, monkeypatch, worker_system_database
):
    repository = PostgresCreativeRepository(worker_system_database)
    topic, _ = seed_topic(repository)
    script_path = write_script(tmp_path)
    monkeypatch.setattr(breakdown_worker, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: SlowProvider(generated_payload(script_path)))
    events = []
    monkeypatch.setattr(breakdown_worker, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    job = CrawlJob(
        job_id="breakdown-heartbeat", platform="creative", crawler_type="breakdown_generate",
        extra={"topic_id": topic["id"], "workspace_root": str(tmp_path)},
    )

    asyncio.run(breakdown_worker.run(job))

    heartbeats = [args for args, _ in events if args[0] == "progress" and args[6].get("indeterminate")]
    assert heartbeats
    assert heartbeats[-1][6]["stage"] == "model_analysis"
    assert heartbeats[-1][6]["elapsed_seconds"] >= 0


def test_breakdown_worker_materializes_downloaded_video_before_generation(
    tmp_path, monkeypatch, worker_system_database
):
    repository = PostgresCreativeRepository(worker_system_database)
    topic, locked = seed_topic(repository)
    video, source = write_video(tmp_path)
    script_path = video.with_name("script.md")
    events = []

    async def fake_materialize(assets, title, **kwargs):
        assert assets[0]["video_path"] == str(video)
        script_path.write_text("## 转写内容\n\n苏软穿书后认错顾宴，并被迫与他同行。", encoding="utf-8")
        return {"created": [{"episode_number": 1, "script_path": str(script_path)}], "failed": []}

    monkeypatch.setattr(breakdown_worker, "materialize_video_transcripts", fake_materialize)
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: Provider(generated_payload(script_path)))
    monkeypatch.setattr(breakdown_worker, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    job = CrawlJob(
        job_id="breakdown-video", platform="creative", crawler_type="breakdown_generate",
        extra={
            "topic_id": topic["id"],
            "runtime_dir": str(tmp_path / ".runtime"), "workspace_root": str(tmp_path),
        },
    )

    asyncio.run(breakdown_worker.run(job))

    latest = repository.get_latest_breakdown(topic["id"])
    assert latest["id"] != locked["id"]
    assert latest["content"]["protagonist_identity"] == "苏软，穿书炮灰。"
    progress = [args for args, _ in events if args[0] == "progress"]
    assert any(args[6].get("stage") == "transcript_materialization" for args in progress)
