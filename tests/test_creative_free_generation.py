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

from core.job import CrawlJob
from creative import free_phase4_worker
from creative.free_mode_service import FreeCreativeModeService
from creative.repository import CreativeRepository


def _free_project(repository: CreativeRepository) -> dict[str, Any]:
    project = repository.create_original_project(
        seed_text="一个能听见谎言的普通女孩",
        name="谎言回声",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="free-generation-project",
        workflow_mode="free",
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "stage", "episode_number"),
    [
        ("blueprints", "brief", None),
        ("characters", "characters", None),
        ("outlines", "outline", None),
        ("beats", "beats", 1),
        ("script", "script", 1),
    ],
)
async def test_free_generation_works_without_upstream_resources_and_updates_head(
    tmp_path,
    monkeypatch,
    action: str,
    stage: str,
    episode_number: int | None,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    context = service.create_generation_context(
        context_id=f"context-{stage}",
        project_id=project["id"],
        stage=stage,
        episode_number=episode_number,
        instruction="突出主角主动选择",
        context_refs={},
        actor_user_id="owner-1",
    )

    async def generate_brief(*_args, **_kwargs):
        return [{"content": {"title": "谎言回声", "logline": "女孩追查谎言"}}], {"model": "fake"}

    async def generate_characters(*_args, **_kwargs):
        return {
            "characters": [{"name": "林真", "role": "主角"}],
            "relationships": [],
        }, {"model": "fake"}

    async def generate_outline(*_args, **kwargs):
        progress = kwargs.get("on_progress")
        if progress:
            progress(3, 3)
        return {
            "series_arc": "女孩从逃避到直面真相",
            "series_overview": {"premise": "谎言会发出回声"},
            "episodes": [
                {"episode": number, "core_event": f"事件 {number}"}
                for number in range(1, 4)
            ],
            "stages": [],
        }, {"model": "fake"}

    async def generate_beats(*_args, **_kwargs):
        return {"beats": [{"order": 1, "event": "听见第一句谎言"}]}, {"model": "fake"}

    async def generate_script(*_args, **_kwargs):
        return {"episode_title": "回声", "scenes": []}, {"model": "fake"}

    monkeypatch.setattr(free_phase4_worker, "create_llm_provider_from_config", lambda _config: object())
    monkeypatch.setattr(free_phase4_worker, "generate_original_directions", generate_brief)
    monkeypatch.setattr(free_phase4_worker, "generate_character_bundle", generate_characters)
    monkeypatch.setattr(free_phase4_worker, "generate_outline_bundle", generate_outline)
    monkeypatch.setattr(free_phase4_worker, "generate_episode_beats", generate_beats)
    monkeypatch.setattr(free_phase4_worker, "generate_episode_script", generate_script)

    job = CrawlJob(
        job_id=f"free-{stage}",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": action,
            "normalized_stage": stage,
            "workflow_mode": "free",
            "project_id": project["id"],
            "episode_number": episode_number,
            "created_by_user_id": "owner-1",
            "operation_id": f"operation-{stage}",
            "free_context_id": context["id"],
            "expected_head_revision": 0,
        },
    )
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(_job, event_type: str, _message: str, **kwargs) -> None:
        events.append((event_type, dict(kwargs.get("payload") or {})))

    await free_phase4_worker.run_free_generation(job, repository, emit)

    refreshed = repository.get_project(project["id"])
    assert refreshed is not None
    head = next(
        item
        for item in refreshed["resource_heads"]
        if item["stage"] == stage
        and int(item["episode_number"]) == int(episode_number or 0)
    )
    assert head["revision"] == 1
    assert head["resource_version"] == 1
    if stage == "brief":
        resources = refreshed["blueprints"]
    elif stage == "characters":
        resources = refreshed["characters"]
    else:
        resources = [
            item for item in refreshed["episodes"] if item["stage"] == stage
        ]
    assert resources
    assert all(item["resource_scope"] == "free" for item in resources)
    assert all(
        item["generation_meta"]["generation_context_id"] == context["id"]
        for item in resources
    )
    assert refreshed["locked_brief_id"] is None
    assert refreshed["character_batch_id"] is None
    assert refreshed["outline_batch_id"] is None
    assert events[-1][0] == "job_finished"


@pytest.mark.asyncio
async def test_free_beats_series_generates_every_episode_and_preserves_locked_versions(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    original = service.create_episode_resource(
        project["id"],
        "beats",
        2,
        {"beats": [{"order": 1, "event": "原第二集"}]},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"],
        stage="beats",
        resource_ref=str(original["resource"]["id"]),
        expected_head_revision=1,
        actor_user_id="owner-1",
        episode_number=2,
    )
    contexts = {
        episode_number: service.create_generation_context(
            context_id=f"series-context-{episode_number}",
            project_id=project["id"],
            stage="beats",
            episode_number=episode_number,
            instruction="每集都强化悬念",
            context_refs={},
            actor_user_id="owner-1",
        )
        for episode_number in range(1, 4)
    }
    generated_episodes: list[int] = []

    async def generate_beats(_provider, _project, _outline, _characters, episode_number, **_kwargs):
        generated_episodes.append(episode_number)
        return {
            "beats": [{"order": 1, "event": f"新第 {episode_number} 集"}],
        }, {"model": "fake"}

    monkeypatch.setattr(free_phase4_worker, "create_llm_provider_from_config", lambda _config: object())
    monkeypatch.setattr(free_phase4_worker, "generate_episode_beats", generate_beats)
    job = CrawlJob(
        job_id="free-beats-series",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "normalized_stage": "beats",
            "workflow_mode": "free",
            "project_id": project["id"],
            "episode_number": None,
            "created_by_user_id": "owner-1",
            "operation_id": "operation-beats-series",
            "series": True,
            "episode_start": 1,
            "episode_end": 3,
            "free_context_ids": {
                str(number): context["id"] for number, context in contexts.items()
            },
            "expected_head_revisions": {"1": 0, "2": 2, "3": 0},
        },
    )
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(_job, event_type: str, _message: str, **kwargs) -> None:
        events.append((event_type, dict(kwargs.get("payload") or {})))

    await free_phase4_worker.run_free_generation(job, repository, emit)

    assert generated_episodes == [1, 2, 3]
    refreshed = repository.get_project(project["id"])
    assert refreshed is not None
    heads = {
        int(item["episode_number"]): item
        for item in refreshed["resource_heads"]
        if item["stage"] == "beats"
    }
    assert {number: head["revision"] for number, head in heads.items()} == {
        1: 1,
        2: 3,
        3: 1,
    }
    old_resource = repository.get_episode(original["resource"]["id"])
    assert old_resource is not None
    assert old_resource["status"] == "locked"
    assert heads[2]["resource_ref"] != str(old_resource["id"])
    generated = [
        item
        for item in refreshed["episodes"]
        if item["stage"] == "beats"
        and item.get("generation_meta", {}).get("job_id") == job.job_id
    ]
    assert len(generated) == 3
    assert all(item["generation_meta"]["series_generation"] is True for item in generated)
    assert {item["generation_meta"]["generation_context_id"] for item in generated} == {
        context["id"] for context in contexts.values()
    }
    progress = [payload for event_type, payload in events if event_type == "progress"]
    assert progress[0]["completed"] == 0
    assert progress[0]["total"] == 3
    assert progress[-1]["completed"] == 3
    assert progress[-1]["total"] == 3
    assert events[-1][0] == "job_finished"
    assert events[-1][1]["series_generation"] is True
    assert events[-1][1]["episode_count"] == 3


@pytest.mark.asyncio
async def test_free_beats_series_rejects_stale_episode_head_before_generation(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    contexts = {
        episode_number: service.create_generation_context(
            context_id=f"stale-series-context-{episode_number}",
            project_id=project["id"],
            stage="beats",
            episode_number=episode_number,
            instruction="",
            context_refs={},
            actor_user_id="owner-1",
        )
        for episode_number in range(1, 4)
    }
    changed = service.create_episode_resource(
        project["id"],
        "beats",
        2,
        {"beats": [{"order": 1, "event": "并发生成的第二集"}]},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    calls = 0

    async def generate_beats(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"beats": []}, {"model": "fake"}

    monkeypatch.setattr(free_phase4_worker, "create_llm_provider_from_config", lambda _config: object())
    monkeypatch.setattr(free_phase4_worker, "generate_episode_beats", generate_beats)
    job = CrawlJob(
        job_id="free-beats-series-stale",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "normalized_stage": "beats",
            "workflow_mode": "free",
            "project_id": project["id"],
            "episode_number": None,
            "created_by_user_id": "owner-1",
            "operation_id": "operation-beats-series-stale",
            "series": True,
            "episode_start": 1,
            "episode_end": 3,
            "free_context_ids": {
                str(number): context["id"] for number, context in contexts.items()
            },
            "expected_head_revisions": {"1": 0, "2": 0, "3": 0},
        },
    )

    with pytest.raises(ValueError, match="第 2 集 Beats 已发生变化"):
        await free_phase4_worker.run_free_generation(job, repository, lambda *_args, **_kwargs: None)

    assert calls == 0
    head = service.get_head(project["id"], "beats", 2)
    assert head is not None
    assert head["revision"] == 1
    assert head["resource_ref"] == str(changed["resource"]["id"])


@pytest.mark.asyncio
async def test_free_script_series_generates_every_episode_and_updates_each_head(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    contexts = {
        episode_number: service.create_generation_context(
            context_id=f"script-series-context-{episode_number}",
            project_id=project["id"],
            stage="script",
            episode_number=episode_number,
            instruction="每集结尾保留悬念",
            context_refs={},
            actor_user_id="owner-1",
        )
        for episode_number in range(1, 4)
    }
    generated_episodes: list[int] = []

    async def generate_script(
        _provider,
        _project,
        _outline,
        _characters,
        _beats,
        episode_number,
        **_kwargs,
    ):
        generated_episodes.append(episode_number)
        return {
            "episode_title": f"第 {episode_number} 集",
            "scenes": [],
        }, {"model": "fake"}

    monkeypatch.setattr(
        free_phase4_worker,
        "create_llm_provider_from_config",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        free_phase4_worker, "generate_episode_script", generate_script
    )
    job = CrawlJob(
        job_id="free-script-series",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "script",
            "normalized_stage": "script",
            "workflow_mode": "free",
            "project_id": project["id"],
            "episode_number": None,
            "created_by_user_id": "owner-1",
            "operation_id": "operation-script-series",
            "series": True,
            "episode_start": 1,
            "episode_end": 3,
            "free_context_ids": {
                str(number): context["id"] for number, context in contexts.items()
            },
            "expected_head_revisions": {"1": 0, "2": 0, "3": 0},
        },
    )
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(_job, event_type: str, _message: str, **kwargs) -> None:
        events.append((event_type, dict(kwargs.get("payload") or {})))

    await free_phase4_worker.run_free_generation(job, repository, emit)

    assert generated_episodes == [1, 2, 3]
    refreshed = repository.get_project(project["id"])
    assert refreshed is not None
    generated = [
        item
        for item in refreshed["episodes"]
        if item["stage"] == "script"
        and item.get("generation_meta", {}).get("job_id") == job.job_id
    ]
    assert len(generated) == 3
    assert all(item["generation_meta"]["series_generation"] is True for item in generated)
    heads = {
        int(item["episode_number"]): item
        for item in refreshed["resource_heads"]
        if item["stage"] == "script"
    }
    assert set(heads) == {1, 2, 3}
    assert all(head["revision"] == 1 for head in heads.values())
    progress = [payload for event_type, payload in events if event_type == "progress"]
    assert progress[0] == {"stage": "script", "completed": 0, "total": 3}
    assert progress[-1]["completed"] == 3
    assert events[-1][0] == "job_finished"
    assert events[-1][1]["action"] == "script"
    assert events[-1][1]["episode_count"] == 3
