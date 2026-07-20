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

from core.job import CrawlJob
from creative import breakdown_worker
from creative.repository import CreativeRepository


class Provider:
    provider_name = "test-llm"

    def __init__(self, response: dict):
        self.response = response

    async def complete_json(self, messages):
        return self.response


def seed_topic(repository: CreativeRepository) -> tuple[dict, dict]:
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


def test_breakdown_worker_persists_personalized_draft_and_completion_event(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime/creative/creative.db"
    repository = CreativeRepository(db_path)
    topic, locked = seed_topic(repository)
    script_path = write_script(tmp_path)
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: Provider(generated_payload(script_path)))
    events = []
    monkeypatch.setattr(breakdown_worker, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    job = CrawlJob(
        job_id="breakdown-1", platform="creative", crawler_type="breakdown_generate",
        extra={
            "topic_id": topic["id"], "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"), "workspace_root": str(tmp_path),
        },
    )

    asyncio.run(breakdown_worker.run(job))

    with repository._connect() as conn:
        row = conn.execute(
            "SELECT id FROM creative_breakdowns WHERE topic_id = ? ORDER BY version DESC LIMIT 1",
            (topic["id"],),
        ).fetchone()
    draft = repository.get_breakdown(row["id"])
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


def test_breakdown_worker_does_not_create_partial_draft_on_generation_failure(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime/creative/creative.db"
    repository = CreativeRepository(db_path)
    topic, _ = seed_topic(repository)
    script_path = write_script(tmp_path)
    invalid = generated_payload(script_path)
    invalid["content"].pop("core_conflict")
    monkeypatch.setattr(breakdown_worker, "create_provider", lambda: Provider(invalid))
    job = CrawlJob(
        job_id="breakdown-failed", platform="creative", crawler_type="breakdown_generate",
        extra={"topic_id": topic["id"], "db_path": str(db_path), "workspace_root": str(tmp_path)},
    )

    try:
        asyncio.run(breakdown_worker.run(job))
    except Exception:
        pass

    with repository._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM creative_breakdowns WHERE topic_id = ?", (topic["id"],)).fetchone()[0]
    assert count == 1
