# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.job import CrawlJob
from creative import free_phase4_worker
from creative.free_mode_service import FreeCreativeModeService
from creative.repositories.base import CreativeRepositoryBase


def _free_project(repository: CreativeRepositoryBase) -> dict[str, Any]:
    project = repository.create_original_project(
        seed_text="一个能听见谎言的普通女孩",
        name="谎言回声",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="free-generation-project",
        workflow_mode="free",
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def test_outline_export_includes_recorded_generation_model() -> None:
    from creative.api import build_outline_markdown

    markdown = build_outline_markdown(
        {"name": "模型对比项目"},
        [
            {
                "episode_number": 1,
                "content": {"core_event": "主角作出选择"},
                "generation_meta": {
                    "llm_provider_name": "OpenAI 主账号",
                    "llm_model_display_name": "GPT Screenwriter",
                    "job_id": "job-model-test",
                },
            }
        ],
    )

    assert "生成模型**：OpenAI 主账号 / GPT Screenwriter" in markdown
    assert "生成任务 ID**：job-model-test" in markdown


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
    postgres_creative,
    monkeypatch,
    action: str,
    stage: str,
    episode_number: int | None,
) -> None:
    repository = postgres_creative
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
async def test_free_generation_uses_selected_model_and_persists_attribution(
    postgres_creative, monkeypatch
) -> None:
    repository = postgres_creative
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    context = service.create_generation_context(
        context_id="selected-model-context",
        project_id=project["id"],
        stage="brief",
        episode_number=None,
        instruction="",
        context_refs={},
        actor_user_id="owner-1",
    )
    selected_calls: list[str] = []
    provider = SimpleNamespace(
        provider_id="provider-1",
        provider_name="openai",
        provider_display_name="OpenAI 主账号",
        model_record_id="model-1",
        model="gpt-screenwriter",
        model_display_name="GPT Screenwriter",
    )

    def create_provider(_config, *, model_record_id=None):
        selected_calls.append(model_record_id)
        return provider

    async def generate_brief(*_args, **_kwargs):
        return [{"content": {"title": "模型方向"}}], {"provider": "openai"}

    monkeypatch.setattr(
        free_phase4_worker, "create_llm_provider_from_config", create_provider
    )
    monkeypatch.setattr(
        free_phase4_worker, "generate_original_directions", generate_brief
    )
    job = CrawlJob(
        job_id="free-selected-model",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints",
            "normalized_stage": "brief",
            "workflow_mode": "free",
            "project_id": project["id"],
            "created_by_user_id": "owner-1",
            "operation_id": "operation-selected-model",
            "free_context_id": context["id"],
            "expected_head_revision": 0,
            "llm_model_record_id": "model-1",
        },
    )

    await free_phase4_worker.run_free_generation(
        job, repository, lambda *_args, **_kwargs: None
    )

    resource = repository.get_project(project["id"])["blueprints"][0]
    assert selected_calls == ["model-1"]
    expected_meta = {
        "llm_provider_id": "provider-1",
        "llm_provider_name": "OpenAI 主账号",
        "llm_model_record_id": "model-1",
        "llm_model_id": "gpt-screenwriter",
        "llm_model_display_name": "GPT Screenwriter",
    }
    assert {
        key: resource["generation_meta"].get(key) for key in expected_meta
    } == expected_meta


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3, 5])
async def test_free_brief_generation_preserves_requested_direction_count(postgres_creative, monkeypatch, count):
    repository = postgres_creative
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    context = service.create_generation_context(
        context_id="context-three-briefs",
        project_id=project["id"],
        stage="brief",
        episode_number=None,
        instruction="生成三个补足方向",
        context_refs={},
        actor_user_id="owner-1",
    )

    async def generate_brief(_provider, _project, _seed, _sources, _existing, count):
        return [
            {"content": {"title": f"方向 {number}", "logline": f"故事 {number}"}}
            for number in range(1, count + 1)
        ], {"model": "fake"}

    monkeypatch.setattr(free_phase4_worker, "create_llm_provider_from_config", lambda _config: object())
    monkeypatch.setattr(free_phase4_worker, "generate_original_directions", generate_brief)
    job = CrawlJob(
        job_id="free-three-briefs",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints",
            "normalized_stage": "brief",
            "workflow_mode": "free",
            "project_id": project["id"],
            "created_by_user_id": "owner-1",
            "free_context_id": context["id"],
            "expected_head_revision": 0,
            "count": count,
        },
    )
    events = []
    await free_phase4_worker.run_free_generation(
        job, repository, lambda *_args, **kwargs: events.append(kwargs.get("payload")),
    )

    refreshed = repository.get_project(project["id"])
    assert len(refreshed["blueprints"]) == count
    assert {item["content"]["title"] for item in refreshed["blueprints"]} == {
        f"方向 {number}" for number in range(1, count + 1)
    }
    assert len({item["candidate_group_id"] for item in refreshed["blueprints"]}) == count
    assert len(events[-1]["blueprint_ids"]) == count
    assert service.get_head(project["id"], "brief")["revision"] == 1


def test_free_brief_batch_preserves_existing_direction_and_rolls_back_on_conflict(postgres_creative):
    repository = postgres_creative
    project = _free_project(repository)
    service = FreeCreativeModeService(repository)
    original = service.create_brief(
        project["id"], {"title": "已有方向"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    original_resource = repository.get_blueprint(original["resource"]["id"])
    contents = [{"title": f"新方向 {number}"} for number in range(3)]
    with pytest.raises(ValueError, match="head changed"):
        service.create_briefs(
            project["id"], contents,
            expected_head_revision=0, actor_user_id="owner-1",
        )
    assert len(repository.get_project(project["id"])["blueprints"]) == 1
    result = service.create_briefs(
        project["id"], contents,
        expected_head_revision=1, actor_user_id="owner-1",
    )
    assert len(repository.get_project(project["id"])["blueprints"]) == 4
    assert repository.get_blueprint(original["resource"]["id"]) == original_resource
    assert result["head"]["revision"] == 2
    assert result["head"]["resource_ref"] == str(result["resources"][0]["id"])


@pytest.mark.asyncio
async def test_free_brief_api_forwards_direction_count(tmp_path, postgres_creative):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from creative.api import build_creative_router
    from creative.schema import FreeCreativeGenerateRequest

    repository = postgres_creative
    project = _free_project(repository)
    requests = []

    def create_job(payload, **_kwargs):
        requests.append(payload)
        return SimpleNamespace(to_dict=lambda: {"job_id": "brief-api-test"})

    router = build_creative_router(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        repository=repository,
        workflow_repository=Mock(),
        application_service=Mock(assert_stage_write_access=Mock()),
        job_manager=SimpleNamespace(create_job=create_job),
    )
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/free/{stage}/generate"
    )
    for count in (3, 5):
        await endpoint(
            project_id=project["id"], stage="briefs", episode_number=None,
            payload=FreeCreativeGenerateRequest(operation_id="brief-api-count", count=count),
            principal=SimpleNamespace(user_id="owner-1"),
        )
        assert requests[-1]["extra"]["count"] == count
    assert requests[0]["extra"]["generation_request_hash"] != requests[1]["extra"]["generation_request_hash"]


@pytest.mark.asyncio
async def test_free_generation_api_validates_and_hashes_selected_model(tmp_path, postgres_creative):
    from unittest.mock import Mock
    from creative.api import build_creative_router
    from creative.schema import FreeCreativeGenerateRequest

    repository = postgres_creative
    project = _free_project(repository)
    jobs = []
    resolved = []

    def create_job(payload, **_kwargs):
        jobs.append(payload)
        return SimpleNamespace(to_dict=lambda: {"job_id": "model-api-test"})

    def resolve_model(model_record_id):
        resolved.append(model_record_id)
        return {"model_record_id": model_record_id}

    router = build_creative_router(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        repository=repository,
        workflow_repository=Mock(),
        application_service=Mock(assert_stage_write_access=Mock()),
        job_manager=SimpleNamespace(create_job=create_job),
        llm_model_resolver=resolve_model,
    )
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/free/{stage}/generate"
    )
    for model_record_id in ("model-a", "model-b"):
        await endpoint(
            project_id=project["id"],
            stage="briefs",
            episode_number=None,
            payload=FreeCreativeGenerateRequest(
                operation_id="model-api-operation",
                llm_model_record_id=model_record_id,
            ),
            principal=SimpleNamespace(user_id="owner-1"),
        )

    assert resolved == ["model-a", "model-b"]
    assert [job["extra"]["llm_model_record_id"] for job in jobs] == [
        "model-a", "model-b"
    ]
    assert jobs[0]["extra"]["generation_request_hash"] != (
        jobs[1]["extra"]["generation_request_hash"]
    )


@pytest.mark.asyncio
async def test_free_series_api_forwards_one_selected_model_to_all_episodes(tmp_path, postgres_creative):
    from unittest.mock import Mock
    from creative.api import build_creative_router
    from creative.schema import FreeCreativeSeriesGenerateRequest

    repository = postgres_creative
    project = _free_project(repository)
    jobs = []

    def create_job(payload, **_kwargs):
        jobs.append(payload)
        return SimpleNamespace(to_dict=lambda: {"job_id": "series-model-test"})

    router = build_creative_router(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        repository=repository,
        workflow_repository=Mock(),
        application_service=Mock(assert_stage_write_access=Mock()),
        job_manager=SimpleNamespace(create_job=create_job),
        llm_model_resolver=lambda model_record_id: {
            "model_record_id": model_record_id
        },
    )
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/free/beats/generate-series"
    )

    await endpoint(
        project_id=project["id"],
        payload=FreeCreativeSeriesGenerateRequest(
            operation_id="series-model-operation",
            expected_head_revisions={1: 0, 2: 0, 3: 0},
            llm_model_record_id="model-series",
        ),
        principal=SimpleNamespace(user_id="owner-1"),
    )

    assert jobs[0]["extra"]["llm_model_record_id"] == "model-series"
    assert len(jobs[0]["extra"]["free_context_ids"]) == 3


@pytest.mark.asyncio
async def test_free_beats_series_generates_every_episode_and_preserves_locked_versions(
    postgres_creative,
    monkeypatch,
) -> None:
    repository = postgres_creative
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
    postgres_creative,
    monkeypatch,
) -> None:
    repository = postgres_creative
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
    postgres_creative,
    monkeypatch,
) -> None:
    repository = postgres_creative
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
