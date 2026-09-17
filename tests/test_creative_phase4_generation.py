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
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from analysis.llm import LLMOutputTruncatedError
from core.job import CrawlJob
from creative import api as creative_api
from creative import phase4_worker
from creative import script_service
from creative.api import build_beats_markdown
from creative.imitation_service import generate_blueprints
from creative.imitation_service import complete_with_one_repair
from creative.outline_fields import OUTLINE_FIELD_LABELS, OUTLINE_TEXT_FIELDS, outline_field_source_fingerprint
from creative.prompt_templates import blueprint_messages, character_messages, outline_messages, beats_messages, script_messages
from creative.repository import CreativeRepository
from creative.script_service import (
    build_outline_history_context,
    build_continuity_ledger,
    generate_character_bundle,
    generate_episode_beats_chunk,
    generate_episode_beats,
    generate_episode_script_chunk,
    generate_outline_bundle,
    validate_beats,
)


def test_phase4_worker_creates_provider_with_selected_model(monkeypatch) -> None:
    selected = []
    provider = SimpleNamespace(
        provider_id="provider-1",
        provider_display_name="主提供商",
        model_record_id="model-record-2",
        model="model-api-2",
        model_display_name="模型二",
    )

    def create_provider(model_record_id=None):
        selected.append(model_record_id)
        return provider

    monkeypatch.setattr(phase4_worker, "create_provider", create_provider)

    resolved = phase4_worker._create_generation_provider(
        {"llm_model_record_id": "model-record-2"}
    )
    meta = phase4_worker._provider_generation_meta(resolved)

    assert selected == ["model-record-2"]
    assert meta["llm_provider_name"] == "主提供商"
    assert meta["llm_model_display_name"] == "模型二"


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


def authority_extra(
    repository: CreativeRepository, project_id: int
) -> dict[str, Any]:
    authority = repository.generation_authority_envelope(project_id)
    return {
        "source_authority": authority,
        "source_authority_sha256": phase4_worker.canonical_request_hash(authority),
    }


def generation_contract(
    repository: CreativeRepository,
    project_id: int,
    operation_id: str = "worker-generation-operation",
) -> dict[str, Any]:
    return {
        **authority_extra(repository, project_id),
        "operation_id": operation_id,
        "generation_request_hash": phase4_worker.canonical_request_hash(
            {"project_id": project_id, "operation_id": operation_id}
        ),
    }

def blueprint_payload(count: int = 3):
    return {
        "blueprints": [
            {
                "title": f"方向 {index}",
                "imitation_reason": "目标受众偏好高压逆袭与身份反差，适合采用这一结构方向。",
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


def test_worker_loads_and_validates_inline_source_snapshot(tmp_path: Path) -> None:
    snapshot = {"snapshot_id": "snapshot-7", "materials": ["第一集"]}
    fingerprint = phase4_worker.canonical_request_hash(snapshot)

    loaded = phase4_worker._load_source_snapshot(
        {
            "runtime_dir": str(tmp_path),
            "source_snapshot_id": "snapshot-7",
            "source_snapshot_sha256": fingerprint,
            "source_snapshot": snapshot,
        }
    )

    assert loaded == snapshot


def test_worker_rejects_snapshot_chunk_path_outside_runtime(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="runtime root"):
        phase4_worker._load_source_snapshot(
            {
                "runtime_dir": str(tmp_path),
                "source_snapshot_id": "snapshot-7",
                "source_snapshot_sha256": "0" * 64,
                "source_snapshot_ref": {
                    "paths": ["../outside.jsonpart"],
                    "sha256": "0" * 64,
                    "size": 2,
                    "read_only": True,
                },
            }
        )


def test_worker_rejects_oversized_inline_snapshot(tmp_path: Path) -> None:
    snapshot = {
        "snapshot_id": "oversized",
        "material": "字" * (2 * 1024 * 1024),
    }
    with pytest.raises(ValueError, match="2 MiB"):
        phase4_worker._load_source_snapshot(
            {
                "runtime_dir": str(tmp_path),
                "source_snapshot_id": "oversized",
                "source_snapshot_sha256": phase4_worker.canonical_request_hash(
                    snapshot
                ),
                "source_snapshot": snapshot,
            }
        )


def test_worker_requires_read_only_chunk_reference(tmp_path: Path) -> None:
    snapshot = {"snapshot_id": "chunked", "material": "素材"}
    content = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    chunk = tmp_path / "creative" / "source_snapshots" / "chunk.jsonpart"
    chunk.parent.mkdir(parents=True)
    chunk.write_bytes(content)
    fingerprint = phase4_worker.canonical_request_hash(snapshot)

    with pytest.raises(ValueError, match="read_only"):
        phase4_worker._load_source_snapshot(
            {
                "runtime_dir": str(tmp_path),
                "source_snapshot_id": "chunked",
                "source_snapshot_sha256": fingerprint,
                "source_snapshot_ref": {
                    "paths": [chunk.relative_to(tmp_path).as_posix()],
                    "sha256": fingerprint,
                    "size": len(content),
                },
            }
        )


def test_worker_requires_and_validates_server_authority(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=1)
    authority = repository.generation_authority_envelope(project["id"])

    assert phase4_worker._validate_source_authority(
        {
            "source_authority": authority,
            "source_authority_sha256": phase4_worker.canonical_request_hash(
                authority
            ),
        },
        repository,
        project["id"],
    ) == authority
    with pytest.raises(ValueError, match="authority"):
        phase4_worker._validate_source_authority({}, repository, project["id"])


def test_worker_rehydrates_authority_only_for_pre_envelope_jobs(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=1)

    legacy = phase4_worker._validate_source_authority(
        {"action": "blueprints", "legacy_authority_compat": True},
        repository,
        project["id"],
    )

    assert legacy == repository.generation_authority_envelope(project["id"])
    with pytest.raises(ValueError, match="authority"):
        phase4_worker._validate_source_authority(
            {
                "action": "blueprints",
                "authority_schema_version": 1,
                "operation_id": "new-writer-operation",
            },
            repository,
            project["id"],
        )
    with pytest.raises(ValueError, match="authority"):
        phase4_worker._validate_source_authority(
            {"action": "blueprints"}, repository, project["id"]
        )


def test_blueprint_prompt_requires_an_imitation_reason_for_each_direction() -> None:
    messages = blueprint_messages({}, 3)

    assert "imitation_reason" in messages[0]["content"]


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
async def test_blueprint_generation_normalizes_a_structured_protagonist_to_text():
    payload = blueprint_payload()
    payload["blueprints"][0]["protagonist"] = {
        "name": "林向暖", "identity": "社区药店店长"
    }

    blueprints, _ = await generate_blueprints(
        SequentialProvider([payload]),
        {"name": "项目"},
        {"title": "来源"},
        {"content": {"core_conflict": "来源冲突"}},
        3,
    )

    assert blueprints[0]["content"]["protagonist"] == "林向暖 社区药店店长"


@pytest.mark.asyncio
async def test_blueprint_generation_accepts_a_chinese_risk_level_instead_of_a_score():
    payload = blueprint_payload()
    payload["blueprints"][0]["risk_score"] = "低"

    blueprints, _ = await generate_blueprints(
        SequentialProvider([payload]),
        {"name": "项目"},
        {"title": "来源"},
        {"content": {"core_conflict": "来源冲突"}},
        3,
    )

    assert blueprints[0]["risk_level"] == "low"


@pytest.mark.asyncio
async def test_structured_content_repair_does_not_repeat_exhausted_gateway_errors():
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(502, request=request)
    gateway_error = httpx.HTTPStatusError("Bad Gateway", request=request, response=response)
    provider = SequentialProvider([gateway_error])

    with pytest.raises(httpx.HTTPStatusError):
        await complete_with_one_repair(provider, [], lambda payload: payload)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_character_generation_requires_structured_people_and_relationships():
    provider = SequentialProvider([{
        "characters": [{
            "name": "周晴", "age_range": "25-30", "identity": "调解员",
            "external_tags": ["冷静"], "desire": "守住社区", "secret": "曾经逃避责任",
                "growth_arc": "从独自承担到信任伙伴", "dialogue_style": "简短直接",
                "appearance_prompt": "职业女性", "voice_prompt": "清晰坚定",
                "action_goal": "当众说服居民接受调解", "fear_of_loss": "失去社区信任",
                "behavioral_contradiction": "越害怕失败越表现得冷静", "pressure_response": "会先替人承担后果",
                "bottom_line": "不拿无辜者交换结果",
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


@pytest.mark.asyncio
async def test_character_generation_requires_actionable_character_fields():
    invalid_character = {
        "name": "周晴", "age_range": "25-30", "identity": "调解员",
        "external_tags": ["冷静"], "desire": "守住社区", "secret": "曾经逃避责任",
        "growth_arc": "从独自承担到信任伙伴", "dialogue_style": "简短直接",
        "appearance_prompt": "职业女性", "voice_prompt": "清晰坚定",
    }
    valid_character = {
        **invalid_character,
        "action_goal": "当众说服居民接受调解", "fear_of_loss": "失去社区信任",
        "behavioral_contradiction": "越害怕失败越表现得冷静", "pressure_response": "会先替人承担后果",
        "bottom_line": "不拿无辜者交换结果",
    }
    provider = SequentialProvider([
        {"characters": [invalid_character], "relationships": []},
        {"characters": [valid_character], "relationships": []},
    ])

    bundle, _ = await generate_character_bundle(provider, {"name": "项目"}, {"title": "Brief"})

    assert bundle["characters"][0]["action_goal"] == "当众说服居民接受调解"
    assert len(provider.calls) == 2
    assert "反派每两集至少暴露一次软肋" in provider.calls[0][0]["content"]


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


def series_overview() -> dict[str, str]:
    return {
        "core_event": "主角追查真相并守住团队",
        "character_progress": "主角从逃避责任成长为主动担当",
        "conflict": "内部背叛与外部围堵同步升级",
        "reversal": "最可信任的盟友才是幕后推手",
        "highlight": "主角在直播现场完成证据反击",
        "cliffhanger": "终局订单指向尚未揭开的旧案",
    }


def series_arc_payload() -> dict:
    return {
        "series_arc": "相识、对抗、合作",
        "stages": ["相识", "对抗", "合作"],
        "series_overview": series_overview(),
    }


@pytest.mark.parametrize(
    ("completed_through", "checkpoint_episodes"),
    [(35, [10, 20, 25]), (90, list(range(10, 81, 10))), (100, list(range(10, 91, 10)))],
)
def test_outline_history_context_bounds_recent_episodes_and_keeps_prior_checkpoints(
    completed_through: int, checkpoint_episodes: list[int],
) -> None:
    context = build_outline_history_context(
        [outline_episode(number) for number in range(1, completed_through + 1)]
    )

    assert context["completed_through"] == completed_through
    assert [item["episode"] for item in context["recent_episodes"]] == list(
        range(completed_through - 9, completed_through + 1)
    )
    assert [item["episode"] for item in context["prior_chunk_checkpoints"]] == checkpoint_episodes
    assert all(
        set(item) == {"episode", "core_event", "character_progress", "reversal", "cliffhanger"}
        for item in context["prior_chunk_checkpoints"]
    )


def test_outline_history_context_truncates_checkpoint_text_without_mutating_input() -> None:
    episodes = [outline_episode(number) for number in range(1, 21)]
    original = "字" * 501
    for field in ("core_event", "character_progress", "reversal", "cliffhanger"):
        episodes[9][field] = original

    context = build_outline_history_context(episodes)
    checkpoint = context["prior_chunk_checkpoints"][0]

    assert all(checkpoint[field] == "字" * 499 + "…" for field in (
        "core_event", "character_progress", "reversal", "cliffhanger",
    ))
    assert all(len(checkpoint[field]) == 500 for field in (
        "core_event", "character_progress", "reversal", "cliffhanger",
    ))
    assert all(episodes[9][field] == original for field in (
        "core_event", "character_progress", "reversal", "cliffhanger",
    ))


def test_bounded_outline_history_reduces_late_batch_prompt_by_at_least_half() -> None:
    episodes = []
    for number in range(1, 81):
        episode = outline_episode(number)
        for field in (
            "core_event", "conflict", "character_progress", "highlight",
            "reversal", "cliffhanger",
        ):
            episode[field] = f"{field}-{number}-" + "剧情状态" * 80
        episodes.append(episode)
    base = {
        "project": {"name": "长篇项目", "target_episode_count": 90},
        "locked_brief": {"title": "锁定 Brief", "story_engine": "成长与冲突" * 100},
        "characters": [{"name": "主角", "growth_arc": "持续成长" * 100}],
        "relationships": [],
        "series_arc": {"series_arc": "九阶段推进", "stages": list(range(1, 10))},
    }

    legacy_messages = outline_messages(
        {**base, "completed_episodes": episodes}, 81, 90
    )
    bounded_messages = outline_messages(
        {**base, "history_context": build_outline_history_context(episodes)}, 81, 90
    )
    legacy_chars = sum(len(message["content"]) for message in legacy_messages)
    bounded_chars = sum(len(message["content"]) for message in bounded_messages)

    assert bounded_chars <= legacy_chars * 0.5


@pytest.mark.asyncio
@pytest.mark.parametrize("completed", [10, 20])
async def test_resumed_outline_generation_rebuilds_bounded_history_context(completed: int) -> None:
    provider = SequentialProvider([{
        "episodes": [outline_episode(number) for number in range(completed + 1, completed + 11)],
    }])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": completed + 10},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        resume_episodes=[outline_episode(number) for number in range(1, completed + 1)],
        resume_series_arc="相识、对抗、合作",
        resume_stages=["相识", "对抗", "合作"],
        resume_series_overview=series_overview(),
    )

    request = json.loads(provider.calls[0][1]["content"])
    history = request["history_context"]
    assert "completed_episodes" not in request
    assert history["completed_through"] == completed
    assert [item["episode"] for item in history["recent_episodes"]] == list(
        range(completed - 9, completed + 1)
    )
    assert [item["episode"] for item in history["prior_chunk_checkpoints"]] == list(
        range(10, completed - 9, 10)
    )
    assert [item["episode"] for item in result["episodes"]] == list(
        range(1, completed + 11)
    )


@pytest.mark.asyncio
async def test_outline_generation_emits_request_progress_for_series_arc_and_chunk() -> None:
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(1)]},
    ])
    events = []

    await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 1},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        on_request_progress=events.append,
    )

    assert [(event["phase"], event["request_kind"]) for event in events] == [
        ("started", "series_arc"),
        ("finished", "series_arc"),
        ("started", "outline_chunk"),
        ("finished", "outline_chunk"),
    ]
    assert all(set(event) == {
        "phase", "request_kind", "batch_start", "batch_end", "batch_started_at",
        "reasoning_effort", "prompt_chars", "request_elapsed_seconds", "batch_in_flight",
        "llm_provider", "llm_model", "wire_api", "max_output_tokens",
    } for event in events)
    assert events[0]["batch_start"] is events[0]["batch_end"] is None
    assert (events[2]["batch_start"], events[2]["batch_end"]) == (1, 1)
    assert events[0]["batch_in_flight"] is events[2]["batch_in_flight"] is True
    assert events[1]["batch_in_flight"] is events[3]["batch_in_flight"] is False
    assert all(event["reasoning_effort"] == "high" for event in events)


@pytest.mark.asyncio
async def test_outline_generation_emits_failed_request_progress_and_reraises_error() -> None:
    request = httpx.Request("POST", "https://example.test/v1/responses")
    error = httpx.HTTPStatusError("Bad Gateway", request=request, response=httpx.Response(502, request=request))
    provider = SequentialProvider([error])
    events = []

    with pytest.raises(httpx.HTTPStatusError, match="Bad Gateway"):
        await generate_outline_bundle(
            provider,
            {"name": "项目", "target_episode_count": 1},
            {"title": "Brief"},
            [{"content": {"name": "周晴"}}],
            [],
            on_request_progress=events.append,
        )

    assert [(event["phase"], event["request_kind"]) for event in events] == [
        ("started", "series_arc"), ("failed", "series_arc"),
    ]
    assert events[-1]["batch_in_flight"] is False


@pytest.mark.asyncio
async def test_outline_request_progress_callback_failure_does_not_discard_model_result() -> None:
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(1)]},
    ])

    def failing_callback(_event: dict) -> None:
        raise RuntimeError("telemetry sink unavailable")

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 1},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        on_request_progress=failing_callback,
    )

    assert [item["episode"] for item in result["episodes"]] == [1]


def valid_beat_content() -> dict:
    return {"beats": [{
        "order": 1, "scene": "大厅", "characters": ["主角"], "goal": "守住证据",
        "obstacle": "反派阻拦", "emotion_change": "犹豫到坚定", "turn": "证据公开",
        "hook": "新威胁出现", "state_change": "局势逆转", "cost": "主角失去退路",
        "opposition_tactic": "公开施压", "continuity_updates": [],
    }]}


def valid_script_content(episode_number: int = 1) -> dict:
    return {
        "episode_title": f"第 {episode_number} 集",
        "scenes": [{
            "sequence_no": 1,
            "scene_no": 1,
            "time_of_day": "日",
            "interior_exterior": "内",
            "location": "办公室",
            "props": [],
            "characters": ["主角"],
            "blocks": [{"type": "action", "text": "主角守住证据"}],
        }],
    }


@pytest.mark.asyncio
async def test_script_chunk_repairs_only_missing_episodes():
    provider = SequentialProvider([
        {"episodes": [{"episode": 1, **valid_script_content(1)}]},
        {"episodes": [{"episode": 2, **valid_script_content(2)}]},
    ])

    generated, meta = await generate_episode_script_chunk(
        provider,
        {"name": "项目", "target_episode_count": 2},
        {"content": {"title": "锁定 Brief"}},
        [
            {"episode_number": number, "content": outline_episode(number)}
            for number in range(1, 3)
        ],
        [{"content": {"name": "主角"}}],
        [],
        {number: valid_beat_content() for number in range(1, 3)},
        continuity_ledger=[],
    )

    assert [episode_number for episode_number, _ in generated] == [1, 2]
    assert len(provider.calls) == 2
    repair_payload = json.loads(provider.calls[1][1]["content"])
    assert [item["episode_number"] for item in repair_payload["locked_outlines"]] == [2]
    assert [item["episode_number"] for item in repair_payload["locked_beats"]] == [2]
    assert meta["repair_attempted"] is True


@pytest.mark.asyncio
async def test_script_chunk_reports_actual_and_missing_episodes_after_repair():
    provider = SequentialProvider([
        {"episodes": [{"episode": 1, **valid_script_content(1)}]},
        {"episodes": [{"episode": 1, **valid_script_content(1)}]},
    ])

    with pytest.raises(
        script_service.LLMError,
        match="实际集数：1.*缺失集数：2.*最终缺失集数：2",
    ):
        await generate_episode_script_chunk(
            provider,
            {"name": "项目", "target_episode_count": 2},
            {"content": {"title": "锁定 Brief"}},
            [
                {"episode_number": number, "content": outline_episode(number)}
                for number in range(1, 3)
            ],
            [{"content": {"name": "主角"}}],
            [],
            {number: valid_beat_content() for number in range(1, 3)},
            continuity_ledger=[],
        )


@pytest.mark.asyncio
async def test_beats_series_requests_one_model_response_per_ten_episode_chunk():
    provider = SequentialProvider([{
        "episodes": [
            {"episode": number, **valid_beat_content()}
            for number in range(1, 11)
        ]
    }])

    generated, _ = await generate_episode_beats_chunk(
        provider,
        {"name": "项目", "target_episode_count": 10},
        {"content": {"title": "锁定 Brief"}},
        [{"episode_number": number, "content": outline_episode(number)} for number in range(1, 11)],
        [{"content": {"name": "主角"}}],
        [],
        continuity_ledger=[],
    )

    assert [episode_number for episode_number, _ in generated] == list(range(1, 11))
    assert len(provider.calls) == 1
    prompt_payload = provider.calls[0][1]["content"]
    assert "locked_brief" in prompt_payload
    assert "locked_outlines" in prompt_payload


@pytest.mark.asyncio
async def test_outline_generation_chunks_every_ten_episodes():
    provider = SequentialProvider([
        {
            "series_arc": "相识、对抗、合作",
            "stages": ["相识", "对抗", "合作"],
            "series_overview": series_overview(),
        },
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
    assert result["series_overview"] == series_overview()
    assert progress == [(10, 12), (12, 12)]
    assert meta["preferred_chunk_size"] == 10


@pytest.mark.asyncio
async def test_outline_generation_splits_a_truncated_chunk_and_checkpoints_each_child():
    provider = SequentialProvider([
        series_arc_payload(),
        LLMOutputTruncatedError(
            "模型输出被输出上限截断（finish_reason=length，已返回 5602 字符）",
            finish_reason="length",
            returned_chars=5602,
        ),
        {"episodes": [outline_episode(number) for number in range(1, 6)]},
        {"episodes": [outline_episode(number) for number in range(6, 11)]},
    ])
    checkpoints = []
    progress = []
    request_progress = []

    result, meta = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 10},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        on_checkpoint=lambda _arc, episodes, snapshot: checkpoints.append(
            (len(episodes), dict(snapshot))
        ),
        on_progress=lambda done, total: progress.append((done, total)),
        on_request_progress=request_progress.append,
    )

    assert [item["episode"] for item in result["episodes"]] == list(range(1, 11))
    assert [count for count, _snapshot in checkpoints] == [5, 10]
    assert progress == [(5, 10), (10, 10)]
    assert meta["preferred_chunk_size"] == 10
    assert meta["split_count"] == 1
    assert meta["actual_batches"] == [
        {"start": 1, "end": 10, "status": "truncated"},
        {"start": 1, "end": 5, "status": "completed"},
        {"start": 6, "end": 10, "status": "completed"},
    ]
    right_payload = json.loads(provider.calls[3][1]["content"])
    assert [
        item["episode"] for item in right_payload["history_context"]["recent_episodes"]
    ] == list(range(1, 6))
    assert [
        event["phase"]
        for event in request_progress
        if event.get("phase") in {"truncated", "split"}
    ] == ["truncated", "split"]
    assert next(
        event for event in request_progress if event.get("phase") == "truncated"
    )["returned_chars"] == 5602


@pytest.mark.asyncio
async def test_outline_generation_recursively_splits_a_truncated_child_range():
    provider = SequentialProvider([
        series_arc_payload(),
        LLMOutputTruncatedError(
            "1-10 truncated", finish_reason="length", returned_chars=5000
        ),
        LLMOutputTruncatedError(
            "1-5 truncated", finish_reason="max_tokens", returned_chars=4000
        ),
        {"episodes": [outline_episode(number) for number in range(1, 3)]},
        {"episodes": [outline_episode(number) for number in range(3, 6)]},
        {"episodes": [outline_episode(number) for number in range(6, 11)]},
    ])

    result, meta = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 10},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
    )

    assert [item["episode"] for item in result["episodes"]] == list(range(1, 11))
    assert meta["split_count"] == 2
    assert meta["actual_batches"] == [
        {"start": 1, "end": 10, "status": "truncated"},
        {"start": 1, "end": 5, "status": "truncated"},
        {"start": 1, "end": 2, "status": "completed"},
        {"start": 3, "end": 5, "status": "completed"},
        {"start": 6, "end": 10, "status": "completed"},
    ]


@pytest.mark.asyncio
async def test_outline_generation_stops_when_one_episode_is_still_truncated():
    provider = SequentialProvider([
        series_arc_payload(),
        LLMOutputTruncatedError(
            "single truncated", finish_reason="max_output_tokens", returned_chars=3000
        ),
    ])

    with pytest.raises(
        LLMOutputTruncatedError,
        match="单集生成仍达到模型输出上限",
    ):
        await generate_outline_bundle(
            provider,
            {"name": "项目", "target_episode_count": 1},
            {"title": "Brief"},
            [{"content": {"name": "周晴"}}],
            [],
        )

    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_outline_split_keeps_left_checkpoint_when_right_episode_fails():
    provider = SequentialProvider([
        series_arc_payload(),
        LLMOutputTruncatedError(
            "1-2 truncated", finish_reason="length", returned_chars=4000
        ),
        {"episodes": [outline_episode(1)]},
        LLMOutputTruncatedError(
            "2 truncated", finish_reason="length", returned_chars=3500
        ),
    ])
    checkpoints = []

    with pytest.raises(
        LLMOutputTruncatedError,
        match="第 2 集单集生成仍达到模型输出上限",
    ):
        await generate_outline_bundle(
            provider,
            {"name": "项目", "target_episode_count": 2},
            {"title": "Brief"},
            [{"content": {"name": "周晴"}}],
            [],
            on_checkpoint=lambda _arc, episodes, _meta: checkpoints.append(
                [item["episode"] for item in episodes]
            ),
        )

    assert checkpoints == [[1]]


@pytest.mark.asyncio
async def test_outline_resume_after_split_checkpoint_starts_at_next_episode():
    provider = SequentialProvider([{
        "episodes": [outline_episode(number) for number in range(6, 11)],
    }])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 10},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        resume_episodes=[outline_episode(number) for number in range(1, 6)],
        resume_series_arc="相识、对抗、合作",
        resume_stages=["相识", "对抗", "合作"],
        resume_series_overview=series_overview(),
    )

    assert len(provider.calls) == 1
    payload = json.loads(provider.calls[0][1]["content"])
    assert [
        item["episode"] for item in payload["history_context"]["recent_episodes"]
    ] == [1, 2, 3, 4, 5]
    assert [item["episode"] for item in result["episodes"]] == list(range(1, 11))


@pytest.mark.asyncio
async def test_outline_generation_accepts_a_bare_episode_array_from_the_llm():
    provider = SequentialProvider([
        series_arc_payload(),
        [outline_episode(number) for number in range(1, 3)],
    ])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 2},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
    )

    assert [item["episode"] for item in result["episodes"]] == [1, 2]


@pytest.mark.asyncio
async def test_outline_generation_fills_a_missing_duration_from_the_project_setting():
    episodes = [outline_episode(number) for number in range(1, 3)]
    for episode in episodes:
        episode.pop("estimated_seconds")
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": episodes},
    ])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 2, "episode_duration_seconds": 180},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
    )

    assert [item["estimated_seconds"] for item in result["episodes"]] == [180, 180]


@pytest.mark.asyncio
async def test_outline_generation_continues_from_a_saved_contiguous_prefix():
    provider = SequentialProvider([
        {"episodes": [outline_episode(number) for number in range(11, 13)]},
    ])

    result, meta = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 12},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        resume_episodes=[outline_episode(number) for number in range(1, 11)],
        resume_series_arc="相识、对抗、合作",
        resume_stages=["相识", "对抗", "合作"],
        resume_series_overview=series_overview(),
    )

    assert len(provider.calls) == 1
    assert [item["episode"] for item in result["episodes"]] == list(range(1, 13))
    assert meta["resumed_from_episode"] == 10


@pytest.mark.asyncio
async def test_legacy_outline_checkpoint_generates_a_missing_overview_after_resume():
    provider = SequentialProvider([
        {"episodes": [outline_episode(number) for number in range(11, 13)]},
        {"series_overview": series_overview()},
    ])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "旧项目", "target_episode_count": 12},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
        resume_episodes=[outline_episode(number) for number in range(1, 11)],
        resume_series_arc="相识、对抗、合作",
        resume_stages=["相识", "对抗", "合作"],
    )

    assert len(provider.calls) == 2
    assert result["series_overview"] == series_overview()
    assert [item["episode"] for item in result["episodes"]] == list(range(1, 13))


@pytest.mark.asyncio
async def test_outline_generation_retries_a_malformed_chunk_before_failing_the_batch():
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": "格式错误"},
        {"episodes": "仍然错误"},
        {"episodes": [outline_episode(number) for number in range(1, 3)]},
    ])

    result, _ = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 2},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
    )

    assert len(provider.calls) == 4
    assert [item["episode"] for item in result["episodes"]] == [1, 2]


@pytest.mark.asyncio
async def test_outline_generation_repairs_missing_text_fields_after_all_chunks():
    incomplete = outline_episode(1)
    incomplete.pop("conflict")
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [incomplete, outline_episode(2)]},
        {"fields": {"conflict": "主角与反派围绕证据归属正面冲突"}},
    ])

    result, meta = await generate_outline_bundle(
        provider,
        {"name": "项目", "target_episode_count": 2},
        {"title": "Brief"},
        [{"content": {"name": "周晴"}}],
        [],
    )

    assert len(provider.calls) == 3
    assert result["episodes"][0]["conflict"] == "主角与反派围绕证据归属正面冲突"
    assert meta["field_repair_count"] == 1


@pytest.mark.asyncio
async def test_beats_generation_rejects_wrong_episode_shape():
    provider = SequentialProvider([
        {"beats": [{
                "order": 1, "scene": "大厅", "characters": ["周晴"], "goal": "说服居民",
                "obstacle": "无人信任", "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
                "state_change": "居民公开站到周晴一边", "cost": "周晴失去调解资格",
                "opposition_tactic": "对手挑动居民质疑她的资格", "continuity_updates": [],
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


@pytest.mark.asyncio
async def test_beats_generation_normalizes_a_bare_beats_array_from_the_llm():
    beat = {
        "order": 1, "scene": "大厅", "characters": ["周晴"], "goal": "说服居民",
        "obstacle": "无人信任", "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
        "state_change": "居民公开站到周晴一边", "cost": "周晴失去调解资格",
        "opposition_tactic": "对手挑动居民质疑她的资格", "continuity_updates": [],
    }
    provider = SequentialProvider([[beat]])

    result, meta = await generate_episode_beats(
        provider,
        {"name": "项目"},
        {"content": {"episode": 1, "core_event": "首次交锋"}},
        [],
        1,
    )

    assert result == {"beats": [beat]}
    assert meta["provider"] == "test-llm"


@pytest.mark.asyncio
async def test_beats_generation_requires_change_cost_tactic_and_continuity_updates():
    legacy_beat = {
        "order": 1, "scene": "大厅", "characters": ["周晴"], "goal": "说服居民",
        "obstacle": "无人信任", "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
    }
    valid_beat = {
        **legacy_beat,
        "state_change": "居民公开站到周晴一边", "cost": "周晴失去调解资格",
        "opposition_tactic": "对手挑动居民质疑她的资格",
        "continuity_updates": [{
            "kind": "prop", "name": "旧布袋", "event": "introduce", "holder": "周晴",
            "location": "大厅", "known_by": ["周晴"], "status": "active",
        }],
    }
    provider = SequentialProvider([{"beats": [legacy_beat]}, {"beats": [valid_beat]}])

    result, _ = await generate_episode_beats(
        provider,
        {"name": "项目"},
        {"content": {"episode": 1, "core_event": "首次交锋"}},
        [],
        1,
        continuity_ledger=[],
    )

    assert result["beats"][0]["cost"] == "周晴失去调解资格"
    assert len(provider.calls) == 2
    assert provider.calls[0][1]["content"].count("continuity_ledger") == 1


@pytest.mark.asyncio
async def test_beats_generation_limits_an_episode_to_six_beats():
    beat = {
        "scene": "大厅", "characters": ["周晴"], "goal": "说服居民", "obstacle": "无人信任",
        "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
        "state_change": "居民改变态度", "cost": "周晴失去资格", "opposition_tactic": "公开质疑",
        "continuity_updates": [],
    }
    invalid_payload = {"beats": [{**beat, "order": number} for number in range(1, 8)]}

    with pytest.raises(ValueError, match="1-6"):
        validate_beats(invalid_payload)


def test_continuity_ledger_carries_the_latest_prop_state_across_episodes():
    ledger = build_continuity_ledger([
        {"episode_number": 1, "content": {"beats": [{"order": 1, "continuity_updates": [{
            "kind": "prop", "name": "旧布袋", "event": "introduce", "holder": "林晚",
            "location": "洗衣店", "known_by": ["林晚"], "status": "active",
        }]}]}},
        {"episode_number": 2, "content": {"beats": [{"order": 1, "continuity_updates": [{
            "kind": "prop", "name": "旧布袋", "event": "transfer", "holder": "小禾",
            "location": "贴纸本夹层", "known_by": ["林晚", "小禾"], "status": "active",
        }]}]}},
    ])

    assert ledger == [{
        "kind": "prop", "name": "旧布袋", "event": "transfer", "holder": "小禾",
        "location": "贴纸本夹层", "known_by": ["林晚", "小禾"], "status": "active",
    }]


def test_worker_only_injects_locked_prior_beats_into_the_next_episode_ledger():
    update = {
        "kind": "information", "name": "债务来源", "event": "reveal", "holder": "周启明",
        "location": "短信", "known_by": ["周启明"], "status": "hidden",
    }
    project = {"episodes": [
        {"id": 1, "stage": "beats", "episode_number": 1, "version": 1, "status": "locked",
         "content": {"beats": [{"order": 1, "continuity_updates": [update]}]}},
        {"id": 2, "stage": "beats", "episode_number": 2, "version": 1, "status": "draft",
         "content": {"beats": [{"order": 1, "continuity_updates": []}]}},
    ]}

    assert phase4_worker._continuity_ledger_before(project, 2) == [update]
    assert phase4_worker._continuity_ledger_before(project, 1) == []


def test_outline_batch_persists_the_same_series_overview_on_every_episode(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])

    batch = repository.create_outline_batch(
        project["id"],
        [outline_episode(1), outline_episode(2)],
        "全剧弧线",
        series_overview=series_overview(),
    )

    assert batch["series_overview"] == series_overview()
    assert all(
        item["content"]["series_overview"] == series_overview()
        for item in batch["episodes"]
    )


@pytest.mark.asyncio
async def test_existing_outline_batch_can_generate_a_series_overview_without_rewriting_episodes():
    generate = getattr(script_service, "generate_series_overview", None)
    assert callable(generate), "generate_series_overview must exist"
    episodes = [outline_episode(1), outline_episode(2)]
    provider = SequentialProvider([{"series_overview": series_overview()}])

    generated, _ = await generate(
        provider,
        {"name": "旧大纲项目", "target_episode_count": 2},
        episodes,
        "全剧弧线",
    )

    assert generated == series_overview()
    assert episodes == [outline_episode(1), outline_episode(2)]
    prompt = provider.calls[0][1]["content"]
    assert '"episode": 1' in prompt
    assert '"episode": 2' in prompt


def test_outline_overview_update_is_atomic_and_rejects_a_locked_batch(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    update = getattr(repository, "update_outline_overview", None)
    assert callable(update), "update_outline_overview must exist"

    updated = update(project["id"], batch["batch_id"], series_overview())

    assert updated["series_overview"] == series_overview()
    assert all(
        item["content"]["series_overview"] == series_overview()
        for item in updated["episodes"]
    )
    repository.lock_outline_batch(project["id"], batch["batch_id"])
    with pytest.raises(ValueError, match="已完成"):
        update(project["id"], batch["batch_id"], series_overview())


def test_outline_overview_generation_requires_the_current_complete_draft(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    partial = repository.save_outline_batch_checkpoint(
        project["id"], [outline_episode(1)], "未完成弧线"
    )

    with pytest.raises(ValueError, match="完整覆盖"):
        repository.assert_outline_overview_generation_ready(
            project["id"], partial["batch_id"]
        )

    first = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "第一版弧线"
    )
    latest = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "第二版弧线"
    )
    with pytest.raises(ValueError, match="当前大纲批次"):
        repository.assert_outline_overview_generation_ready(
            project["id"], first["batch_id"]
        )

    ready = repository.assert_outline_overview_generation_ready(
        project["id"], latest["batch_id"]
    )
    assert ready["batch_id"] == latest["batch_id"]


def test_outline_overview_generation_can_append_once_to_current_locked_batch(tmp_path):
    """Removing locked-batch support must break the one-time legacy backfill."""

    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    first = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "第一版弧线"
    )
    repository.lock_outline_batch(project["id"], first["batch_id"])
    current = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "当前弧线"
    )
    repository.lock_outline_batch(project["id"], current["batch_id"])

    with pytest.raises(ValueError, match="当前.*大纲批次"):
        repository.assert_outline_overview_generation_ready(
            project["id"], first["batch_id"]
        )

    ready = repository.assert_outline_overview_generation_ready(
        project["id"], current["batch_id"]
    )
    assert ready["batch_id"] == current["batch_id"]

    saved = repository.update_outline_overview(
        project["id"],
        current["batch_id"],
        series_overview(),
        only_if_missing=True,
    )
    assert saved["series_overview"] == series_overview()
    assert {item["status"] for item in saved["episodes"]} == {"locked"}

    with pytest.raises(ValueError, match="已存在"):
        repository.update_outline_overview(
            project["id"],
            current["batch_id"],
            {**series_overview(), "core_event": "不得覆盖"},
            only_if_missing=True,
        )
    with pytest.raises(ValueError, match="已完成"):
        repository.update_outline_overview(
            project["id"], current["batch_id"], series_overview()
        )


def test_locked_overview_backfill_does_not_invalidate_existing_beats(tmp_path):
    """Including batch metadata in outline fingerprints must stale old Beats."""

    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=1)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outline = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outline["batch_id"])
    beats = repository.create_episode_beats(
        project["id"], 1, valid_beat_content()
    )

    repository.update_outline_overview(
        project["id"],
        outline["batch_id"],
        series_overview(),
        only_if_missing=True,
    )

    assert [item["id"] for item in repository.batch_lockable_episode_beats(project["id"])] == [beats["id"]]
    repository.lock_current_episode_beats_batch(project["id"])
    assert repository.missing_current_locked_beats(project["id"]) == []
    script = repository.create_episode_script(
        project["id"], 1, valid_script_content()
    )
    assert script["upstream_refs"]["beats_id"] == beats["id"]

    # Releases before this fix stored the full outline content hash when the
    # overview already existed. That historical form must remain readable.
    current_outline = repository.get_outline_batch(
        project["id"], outline["batch_id"]
    )["episodes"][0]
    legacy_refs = dict(repository.get_episode(beats["id"])["upstream_refs"])
    legacy_refs["outline_content_fingerprint"] = repository._content_fingerprint(
        current_outline["content"]
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET upstream_refs_json = ? WHERE id = ?",
            (json.dumps(legacy_refs, ensure_ascii=False), beats["id"]),
        )
    assert repository.missing_current_locked_beats(project["id"]) == []


def test_single_episode_update_cannot_replace_the_batch_series_overview(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线",
        series_overview=series_overview(),
    )
    episode = batch["episodes"][0]
    stale_content = dict(episode["content"])
    stale_content["core_event"] = "人工更新后的单集事件"
    stale_content["series_overview"] = {
        **series_overview(),
        "core_event": "旧标签页里的过期概览",
    }

    updated = repository.update_episode(episode["id"], stale_content)

    assert updated["content"]["core_event"] == "人工更新后的单集事件"
    assert updated["content"]["series_overview"] == series_overview()


def test_single_episode_update_preserves_an_overview_saved_concurrently(
    tmp_path, monkeypatch
):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线",
        series_overview=series_overview(),
    )
    episode = batch["episodes"][0]
    stale_content = dict(episode["content"])
    stale_content["core_event"] = "并发中的单集修改"
    concurrent = {**series_overview(), "core_event": "并发保存的新概览"}
    original_update = repository._update_creative_item

    def save_overview_before_episode_write(*args, **kwargs):
        repository.update_outline_overview(
            project["id"], batch["batch_id"], concurrent
        )
        return original_update(*args, **kwargs)

    monkeypatch.setattr(
        repository, "_update_creative_item", save_overview_before_episode_write
    )

    updated = repository.update_episode(episode["id"], stale_content)

    assert updated["content"]["core_event"] == "并发中的单集修改"
    assert updated["content"]["series_overview"] == concurrent


def test_episode_update_keeps_overview_read_and_write_in_one_transaction(
    tmp_path, monkeypatch
):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线",
        series_overview=series_overview(),
    )
    episode = batch["episodes"][0]
    stale_content = dict(episode["content"])
    stale_content["core_event"] = "并发事务中的单集修改"
    concurrent = {**series_overview(), "core_event": "事务期间保存的新概览"}
    episode_paused = threading.Event()
    release_episode = threading.Event()
    original_connect = repository._connect

    class PausingConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, sql, parameters=()):
            if sql.startswith("UPDATE creative_episodes SET content_json"):
                episode_paused.set()
                assert release_episode.wait(3)
            return self.connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def controlled_connect():
        connection = original_connect()
        if threading.current_thread().name == "outline-episode-save":
            return PausingConnection(connection)
        return connection

    monkeypatch.setattr(repository, "_connect", controlled_connect)
    episode_result = {}
    writer_result = {}

    def save_episode():
        episode_result["value"] = repository.update_episode(
            episode["id"], stale_content
        )

    def save_overview():
        writer_result["value"] = repository.update_outline_overview(
            project["id"], batch["batch_id"], concurrent
        )

    episode_thread = threading.Thread(
        target=save_episode, name="outline-episode-save"
    )
    episode_thread.start()
    assert episode_paused.wait(3)
    writer_thread = threading.Thread(target=save_overview, name="overview-save")
    writer_thread.start()
    writer_thread.join(timeout=0.15)
    release_episode.set()
    episode_thread.join(timeout=3)
    writer_thread.join(timeout=3)

    assert not episode_thread.is_alive()
    assert not writer_thread.is_alive()
    assert episode_result["value"]["content"]["core_event"] == "并发事务中的单集修改"
    assert writer_result["value"]["series_overview"] == concurrent
    assert repository.get_episode(episode["id"])["content"]["series_overview"] == concurrent


def test_ai_overview_compare_and_set_does_not_overwrite_manual_content(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线"
    )
    manual = {**series_overview(), "core_event": "人工保存的概览"}
    repository.update_outline_overview(project["id"], batch["batch_id"], manual)

    with pytest.raises(ValueError, match="已存在"):
        repository.update_outline_overview(
            project["id"],
            batch["batch_id"],
            series_overview(),
            only_if_missing=True,
        )

    assert repository.get_outline_batch(
        project["id"], batch["batch_id"]
    )["series_overview"] == manual


def test_series_beats_checkpoint_uses_one_batch_and_is_resumable(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])

    saved = repository.save_episode_generation_checkpoint(
        project["id"], "beats", [(1, valid_beat_content())], "batch-job-1",
        {"series_generation": True}, batch_id="series-beats-1",
    )

    assert [item["episode_number"] for item in saved] == [1]
    assert saved[0]["batch_id"] == "series-beats-1"
    resumed = repository.find_resumable_episode_series(project["id"], "beats")
    assert resumed["batch_id"] == "series-beats-1"
    assert resumed["completed_episode_count"] == 1
    repository.save_episode_generation_checkpoint(
        project["id"], "beats", [(2, valid_beat_content())], "batch-job-2",
        {"series_generation": True}, batch_id="series-beats-1",
    )
    completed = repository.find_resumable_episode_series(project["id"], "beats")
    assert completed["completed_episode_count"] == 2


def test_script_series_checkpoint_rejects_beats_from_an_old_outline_batch(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    first_outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "第一版全剧弧线"
    )
    repository.lock_outline_batch(project["id"], first_outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, valid_beat_content())
    repository.lock_episode_beats(beats["id"])

    new_outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "第二版全剧弧线"
    )
    repository.lock_outline_batch(project["id"], new_outlines["batch_id"])

    with pytest.raises(ValueError, match="必须先锁定当前集 Beats"):
        repository.save_episode_generation_checkpoint(
            project["id"], "script", [(1, valid_script_content())], "script-series-job"
        )


def test_series_checkpoint_freezes_brief_and_character_content_fingerprints(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 1})
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [outline_episode(1)], "全剧弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    saved = repository.save_episode_generation_checkpoint(
        project["id"], "beats", [(1, valid_beat_content())], "beats-series-job"
    )

    assert len(saved[0]["upstream_refs"]["brief_content_fingerprint"]) == 64
    assert len(saved[0]["upstream_refs"]["character_content_fingerprint"]) == 64


def test_beats_markdown_exports_story_changes_and_continuity_updates():
    markdown = build_beats_markdown(
        {"name": "项目"},
        [{
            "episode_number": 1,
            "version": 1,
            "status": "draft",
            "content": {"beats": [{
                "order": 1, "scene": "大厅", "characters": ["周晴"], "goal": "说服居民",
                "obstacle": "无人信任", "emotion_change": "克制到坚定", "turn": "证据出现", "hook": "对手到场",
                "state_change": "居民公开站到周晴一边", "cost": "周晴失去调解资格",
                "opposition_tactic": "公开质疑", "continuity_updates": [{
                    "kind": "prop", "name": "旧布袋", "event": "introduce", "holder": "周晴",
                    "location": "大厅", "known_by": ["周晴"], "status": "active",
                }],
            }]},
        }],
    )

    for label in ("局面变化", "代价", "对抗策略", "连续性变更"):
        assert label in markdown


def test_outline_markdown_exports_only_the_readable_episode_outline_fields():
    markdown = creative_api.build_outline_markdown(
        {"name": "大纲导出项目"},
        [{
            "episode_number": 1,
            "batch_id": "outline-batch",
            "version": 2,
            "content": {
                "series_overview": series_overview(),
                "core_event": "主角意外拿到关键证据",
                "conflict": "对手抢先封锁证人",
                "character_progress": "主角从犹豫变为主动反击",
                "highlight": "证据在直播中公开",
                "reversal": "证人其实是对手的人",
                "cliffhanger": "主角收到匿名威胁",
                "estimated_seconds": 120,
            },
            "generation_meta": {"job_id": "should-not-export"},
            "upstream_refs": {"brief_id": 99},
        }],
    )

    assert markdown.startswith("# 大纲导出项目\n")
    assert "- 创作阶段：分集大纲" in markdown
    assert "## 第 1 集" in markdown
    assert "## 全集剧情概览" in markdown
    assert markdown.index("## 全集剧情概览") < markdown.index("## 第 1 集")
    assert markdown.count("## 全集剧情概览") == 1
    assert "**主要冲突**：内部背叛与外部围堵同步升级" in markdown
    assert "**核心事件**：主角意外拿到关键证据" in markdown
    assert "**冲突**：对手抢先封锁证人" in markdown
    assert "**人物推进**：主角从犹豫变为主动反击" in markdown
    assert "**爽点**：证据在直播中公开" in markdown
    assert "**反转**：证人其实是对手的人" in markdown
    assert "**结尾悬念**：主角收到匿名威胁" in markdown
    assert "**预计时长**：120 秒" in markdown
    assert "should-not-export" not in markdown
    assert "brief_id" not in markdown


def test_story_constraints_are_present_in_all_generation_prompts():
    payload = {"project": {"name": "项目"}}
    prompts = (
        character_messages(payload),
        outline_messages(payload, 1, 1),
        beats_messages(payload, 1),
        script_messages(payload, 1),
    )

    for messages in prompts:
        assert "每场冲突不得以重复的证据留存或口头反击收束" in messages[0]["content"]
        assert "反派每两集至少暴露一次软肋" in messages[0]["content"]


def seed_worker_project(repository: CreativeRepository, episode_count: int = 20) -> dict:
    topic = repository.upsert_topics([{
        "platform": "hg", "job_id": "rank", "source_path": "rank.json",
        "captured_at": "2026-07-17", "title": "来源剧", "description": "逆袭",
        "category": "都市", "rank_type": "热播榜", "score": 90, "confidence": "high",
        "score_details": {}, "recommendation": "推荐", "evidence": [],
    }])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"core_conflict": "来源冲突"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    return repository.create_project(topic["id"], breakdown["id"], "Worker 项目", episode_count)


def test_batch_lock_beats_locks_all_current_drafts_and_skips_existing_locked_items(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=2)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    first = repository.create_episode_beats(project["id"], 1, valid_beat_content())
    second = repository.create_episode_beats(project["id"], 2, valid_beat_content())
    repository.lock_episode_beats(first["id"])

    locked = repository.lock_current_episode_beats_batch(project["id"])

    assert [item["episode_number"] for item in locked] == [2]
    assert repository.get_episode(second["id"])["status"] == "locked"


def test_batch_lock_scripts_locks_all_current_drafts_and_skips_locked_items(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    for episode_number in (1, 2):
        beats = repository.create_episode_beats(
            project["id"], episode_number, valid_beat_content()
        )
        repository.lock_episode_beats(beats["id"])
    first = repository.create_episode_script(
        project["id"], 1, valid_script_content(1)
    )
    second = repository.create_episode_script(
        project["id"], 2, valid_script_content(2)
    )
    repository.lock_episode_script(first["id"])

    locked = repository.lock_current_episode_scripts_batch(project["id"])

    assert [item["episode_number"] for item in locked] == [2]
    assert repository.get_episode(second["id"])["status"] == "locked"


def test_batch_lock_scripts_preflights_all_episodes_before_writing(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_worker_project(repository, episode_count=2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    for episode_number in (1, 2):
        beats = repository.create_episode_beats(
            project["id"], episode_number, valid_beat_content()
        )
        repository.lock_episode_beats(beats["id"])
    first = repository.create_episode_script(
        project["id"], 1, valid_script_content(1)
    )

    with pytest.raises(ValueError, match="第.*2.*集数缺少|以下集数缺少"):
        repository.lock_current_episode_scripts_batch(project["id"])

    assert repository.get_episode(first["id"])["status"] == "draft"


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
            **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    detail = repository.get_project(project["id"])
    report = tmp_path / "data" / "creative" / "jobs" / job.job_id / "generation.json"
    assert len(detail["blueprints"]) == 3
    assert detail["status"] == "direction_review"
    assert report.exists()
    assert "blueprints" in report.read_text(encoding="utf-8")


def test_phase4_worker_rejects_authority_change_at_persistence_boundary(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)

    class AuthorityChangingProvider(SequentialProvider):
        async def complete_json(self, messages):
            repository.update_project(project["id"], {"name": "权威已变化"})
            return await super().complete_json(messages)

    provider = AuthorityChangingProvider([blueprint_payload()])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="creative-worker-authority-race",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(repository, project["id"]),
        },
    )

    with pytest.raises(ValueError, match="authority changed"):
        asyncio.run(phase4_worker.run(job))

    assert repository.get_project(project["id"])["blueprints"] == []


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
            **authority_extra(repository, project["id"]),
        },
    )

    with pytest.raises(Exception, match="结构校验"):
        asyncio.run(phase4_worker.run(job))

    assert repository.get_project(project["id"])["blueprints"] == []


def test_phase4_worker_generates_overview_for_an_existing_draft_without_rewriting_episodes(
    tmp_path, monkeypatch
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1), outline_episode(2)], "全剧弧线"
    )
    before = [dict(item["content"]) for item in batch["episodes"]]
    provider = SequentialProvider([{"series_overview": series_overview()}])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="creative-worker-outline-overview",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outline_overview",
            "project_id": project["id"],
                "batch_id": batch["batch_id"],
                "db_path": str(db_path),
                "runtime_dir": str(tmp_path / ".runtime"),
                **authority_extra(repository, project["id"]),
            },
    )

    asyncio.run(phase4_worker.run(job))

    saved = repository.get_outline_batch(project["id"], batch["batch_id"])
    assert saved["series_overview"] == series_overview()
    for previous, current in zip(before, saved["episodes"], strict=True):
        current_without_overview = dict(current["content"])
        current_without_overview.pop("series_overview")
        assert current_without_overview == previous


def test_phase4_worker_rejects_an_incomplete_outline_before_calling_the_model(
    tmp_path, monkeypatch
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository, episode_count=2)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    partial = repository.save_outline_batch_checkpoint(
        project["id"], [outline_episode(1)], "未完成弧线"
    )
    provider = SequentialProvider([])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    job = CrawlJob(
        job_id="creative-worker-incomplete-overview",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outline_overview",
            "project_id": project["id"],
                "batch_id": partial["batch_id"],
                "db_path": str(db_path),
                "runtime_dir": str(tmp_path / ".runtime"),
                **authority_extra(repository, project["id"]),
            },
    )

    with pytest.raises(ValueError, match="完整覆盖"):
        asyncio.run(phase4_worker.run(job))

    assert provider.calls == []


def test_phase4_worker_does_not_overwrite_an_overview_saved_during_generation(
    tmp_path, monkeypatch
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository, episode_count=1)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    batch = repository.create_outline_batch(
        project["id"], [outline_episode(1)], "全剧弧线"
    )
    manual = {**series_overview(), "core_event": "生成期间人工保存的概览"}

    class ConcurrentSaveProvider(SequentialProvider):
        async def complete_json(self, messages):
            repository.update_outline_overview(
                project["id"], batch["batch_id"], manual
            )
            return await super().complete_json(messages)

    provider = ConcurrentSaveProvider([{"series_overview": series_overview()}])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    job = CrawlJob(
        job_id="creative-worker-overview-race",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outline_overview",
            "project_id": project["id"],
                "batch_id": batch["batch_id"],
                "db_path": str(db_path),
                "runtime_dir": str(tmp_path / ".runtime"),
                **authority_extra(repository, project["id"]),
            },
    )

    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(phase4_worker.run(job))

    saved = repository.get_outline_batch(project["id"], batch["batch_id"])
    assert saved["series_overview"] == manual


def test_phase4_worker_preserves_completed_outline_chunks_after_a_later_failure(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(number) for number in range(1, 11)]},
        {"episodes": []},
        {"episodes": []},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    job = CrawlJob(
        job_id="creative-worker-outline-checkpoint",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(repository, project["id"]),
        },
    )

    with pytest.raises(Exception, match="结构校验"):
        asyncio.run(phase4_worker.run(job))

    partial_outlines = [
        item for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "outline"
    ]
    assert [item["episode_number"] for item in partial_outlines] == list(range(1, 11))
    assert len({item["batch_id"] for item in partial_outlines}) == 1


def test_phase4_worker_continues_a_saved_outline_batch_after_a_failed_job(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    failed_provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(number) for number in range(1, 11)]},
        {"episodes": []},
        {"episodes": []},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: failed_provider)
    failed_job = CrawlJob(
        job_id="creative-worker-outline-failed",
        platform="creative", crawler_type="phase4_generate",
        extra={"action": "outlines", "project_id": project["id"], "db_path": str(db_path),
               **generation_contract(repository, project["id"])},
    )
    with pytest.raises(Exception, match="结构校验"):
        asyncio.run(phase4_worker.run(failed_job))

    class ResumedProviderWithRetryNotice(SequentialProvider):
        retry_callback = None

        async def complete_json(self, messages):
            if self.retry_callback:
                self.retry_callback(1, 4, "upstream read timeout")
            return await super().complete_json(messages)

    resumed_provider = ResumedProviderWithRetryNotice([
        {"episodes": [outline_episode(number) for number in range(11, 21)]},
    ])
    resumed_events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: resumed_provider)
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: resumed_events.append(
            {"event": event, "message": message, "level": level, "payload": payload or {}},
        ),
    )
    resumed_job = CrawlJob(
        job_id="creative-worker-outline-resumed",
        platform="creative", crawler_type="phase4_generate",
        extra={"action": "outlines", "project_id": project["id"], "db_path": str(db_path),
               "retry_of_job_id": "creative-worker-outline-failed",
               **generation_contract(repository, project["id"])},
    )

    asyncio.run(phase4_worker.run(resumed_job))

    outlines = [
        item for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "outline"
    ]
    assert len(resumed_provider.calls) == 1
    assert [item["episode_number"] for item in outlines] == list(range(1, 21))
    assert len({item["batch_id"] for item in outlines}) == 1
    retry_progress = next(
        event["payload"] for event in resumed_events
        if event["payload"].get("retry_attempt")
    )
    assert (retry_progress["completed"], retry_progress["total"]) == (10, 20)
    assert (retry_progress["batch_start"], retry_progress["batch_end"]) == (11, 20)


def test_phase4_worker_retry_takes_over_failure_before_first_checkpoint(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository, episode_count=1)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    operation = generation_contract(
        repository, project["id"], operation_id="pre-checkpoint-retry"
    )

    class FailingProvider:
        provider_name = "failing-test-llm"

        async def complete_json(self, messages):
            raise RuntimeError("provider failed before checkpoint")

    monkeypatch.setattr(phase4_worker, "create_provider", FailingProvider)
    failed_job = CrawlJob(
        job_id="outline-before-checkpoint-failed",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **operation,
        },
    )
    with pytest.raises(RuntimeError, match="before checkpoint"):
        asyncio.run(phase4_worker.run(failed_job))

    retry_provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(1)]},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: retry_provider)
    retry_job = CrawlJob(
        job_id="outline-before-checkpoint-retry",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            "retry_of_job_id": failed_job.job_id,
            **operation,
        },
    )

    asyncio.run(phase4_worker.run(retry_job))

    outlines = [
        item for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "outline"
    ]
    assert [item["episode_number"] for item in outlines] == [1]


def test_phase4_worker_retry_takes_over_expired_orphan_before_first_checkpoint(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository, episode_count=1)
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    operation = generation_contract(
        repository, project["id"], operation_id="orphan-before-checkpoint"
    )
    repository.reserve_generation_operation(
        operation_id="orphan-before-checkpoint",
        project_id=project["id"],
        stage="outline",
        request_hash=operation["generation_request_hash"],
        owner_id="dead-outline-worker",
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE creative_generation_operations SET updated_at = 0 "
            "WHERE operation_id = ?",
            ("orphan-before-checkpoint",),
        )
    provider = SequentialProvider([
        series_arc_payload(),
        {"episodes": [outline_episode(1)]},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    retry_job = CrawlJob(
        job_id="orphan-outline-retry",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            "retry_of_job_id": "dead-outline-worker",
            **operation,
        },
    )

    asyncio.run(phase4_worker.run(retry_job))

    outlines = [
        item for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "outline"
    ]
    assert [item["episode_number"] for item in outlines] == [1]


def test_phase4_worker_saves_series_beats_in_ten_episode_checkpoints(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 12})
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(number) for number in range(1, 13)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    provider = SequentialProvider([
        {"episodes": [{"episode": number, **valid_beat_content()} for number in range(1, 11)]},
        {"episodes": [{"episode": number, **valid_beat_content()} for number in range(11, 13)]},
    ])
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker, "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "payload": payload or {}},
        ),
    )
    job = CrawlJob(
        job_id="creative-worker-series-beats", platform="creative", crawler_type="phase4_generate",
        extra={
            "action": "beats", "series": True, "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    beats = [item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "beats"]
    assert [item["episode_number"] for item in beats] == list(range(1, 13))
    assert len({item["batch_id"] for item in beats}) == 1
    assert len(provider.calls) == 2
    assert any(item["payload"].get("completed") == 10 for item in events)
    assert any(item["payload"].get("completed") == 12 for item in events)


def test_phase4_worker_splits_truncated_series_beats_and_checkpoints_children(
    tmp_path, monkeypatch
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 10})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"],
        [outline_episode(number) for number in range(1, 11)],
        "全剧弧线",
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    first_half_content = valid_beat_content()
    first_half_content["beats"][0]["continuity_updates"] = [{
        "kind": "information",
        "name": "五集检查点线索",
        "event": "reveal",
        "holder": "主角",
        "location": "大厅",
        "known_by": ["主角"],
        "status": "active",
    }]
    provider = SequentialProvider([
        LLMOutputTruncatedError(
            "1-10 truncated", finish_reason="length", returned_chars=120_000
        ),
        {
            "episodes": [
                {"episode": number, **first_half_content}
                for number in range(1, 6)
            ]
        },
        {
            "episodes": [
                {"episode": number, **valid_beat_content()}
                for number in range(6, 11)
            ]
        },
    ])
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {
                "event": event,
                "message": message,
                "level": level,
                "payload": payload or {},
            }
        ),
    )
    job = CrawlJob(
        job_id="creative-worker-series-beats-split",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "series": True,
            "project_id": project["id"],
            "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(
                repository, project["id"], "worker-generation-operation-split"
            ),
        },
    )

    asyncio.run(phase4_worker.run(job))

    beats = [
        item
        for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "beats"
    ]
    assert [item["episode_number"] for item in beats] == list(range(1, 11))
    assert len({item["batch_id"] for item in beats}) == 1
    assert len(provider.calls) == 3
    assert "五集检查点线索" in provider.calls[2][1]["content"]
    assert [
        item["payload"].get("completed")
        for item in events
        if item["event"] == "progress" and "chunk_end" in item["payload"]
    ] == [5, 10]
    assert any(
        item["level"] == "warning"
        and item["payload"].get("split_start") == 1
        and item["payload"].get("split_end") == 10
        and item["payload"].get("left_end") == 5
        and item["payload"].get("right_start") == 6
        for item in events
    )


def test_phase4_worker_keeps_left_beats_checkpoint_when_single_episode_truncates(
    tmp_path, monkeypatch
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 2})
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"],
        [outline_episode(1), outline_episode(2)],
        "全剧弧线",
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    provider = SequentialProvider([
        LLMOutputTruncatedError(
            "1-2 truncated", finish_reason="length", returned_chars=50_000
        ),
        {"episodes": [{"episode": 1, **valid_beat_content()}]},
        LLMOutputTruncatedError(
            "2 truncated", finish_reason="max_tokens", returned_chars=30_000
        ),
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(phase4_worker, "emit", lambda *_args, **_kwargs: None)
    job = CrawlJob(
        job_id="creative-worker-series-beats-single-truncated",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "beats",
            "series": True,
            "project_id": project["id"],
            "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(
                repository,
                project["id"],
                "worker-generation-operation-single-truncated",
            ),
        },
    )

    with pytest.raises(
        LLMOutputTruncatedError,
        match="第 2 集 Beats 生成仍达到模型输出上限",
    ):
        asyncio.run(phase4_worker.run(job))

    beats = [
        item
        for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "beats"
    ]
    assert [item["episode_number"] for item in beats] == [1]
    assert len(provider.calls) == 3


def test_phase4_worker_saves_series_scripts_in_two_episode_chunks(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    repository.update_project(project["id"], {"target_episode_count": 5})
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [outline_episode(number) for number in range(1, 6)], "全剧弧线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    for episode_number in range(1, 6):
        beats = repository.create_episode_beats(project["id"], episode_number, valid_beat_content())
        repository.lock_episode_beats(beats["id"])
    provider = SequentialProvider([
        {"episodes": [{"episode": number, **valid_script_content(number)} for number in range(1, 3)]},
        {"episodes": [{"episode": number, **valid_script_content(number)} for number in range(3, 5)]},
        {"episodes": [{"episode": 5, **valid_script_content(5)}]},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    job = CrawlJob(
        job_id="creative-worker-series-scripts", platform="creative", crawler_type="phase4_generate",
        extra={
            "action": "script", "series": True, "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    scripts = [item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "script"]
    assert [item["episode_number"] for item in scripts] == list(range(1, 6))
    assert {item["status"] for item in scripts} == {"draft"}
    assert len({item["batch_id"] for item in scripts}) == 1
    assert len(provider.calls) == 3
    assert [
        [item["episode_number"] for item in json.loads(call[1]["content"])["locked_outlines"]]
        for call in provider.calls
    ] == [[1, 2], [3, 4], [5]]
    assert "locked_beats" in provider.calls[0][1]["content"]


def test_phase4_worker_surfaces_provider_retry_progress(tmp_path, monkeypatch):
    class ProviderWithRetryNotice(SequentialProvider):
        retry_callback = None

        async def complete_json(self, messages):
            if self.retry_callback:
                self.retry_callback(1, 2, "502 Bad Gateway")
            return await super().complete_json(messages)

    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    provider = ProviderWithRetryNotice([blueprint_payload()])
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "level": level, "payload": payload or {}},
        ),
    )
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="creative-worker-retry-progress",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **authority_extra(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    retry_event = next(item for item in events if item["payload"].get("retry_attempt") == 1)
    assert retry_event["level"] == "warning"
    assert "第 1/2 次自动重试" in retry_event["message"]


def _prepare_outline_worker_project(repository: CreativeRepository, episode_count: int = 1) -> dict:
    project = seed_worker_project(repository, episode_count=episode_count)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    return project


def test_outline_worker_sets_reasoning_effort_and_maps_request_progress_events(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = _prepare_outline_worker_project(repository)
    provider = SequentialProvider([series_arc_payload(), {"episodes": [outline_episode(1)]}])
    provider.reasoning_effort = "low"
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(phase4_worker.config, "CREATIVE_OUTLINE_REASONING_EFFORT", "high", raising=False)
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "level": level, "payload": payload or {}},
        ),
    )
    job = CrawlJob(
        job_id="creative-worker-outline-request-progress",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"], "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"), **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    assert provider.reasoning_effort == "high"
    request_events = [event for event in events if event["payload"].get("request_kind")]
    assert [(event["event"], event["payload"]["phase"]) for event in request_events] == [
        ("progress", "started"), ("progress", "finished"),
        ("progress", "started"), ("progress", "finished"),
    ]
    assert request_events[0]["message"] == "正在规划全剧阶段弧线"
    assert request_events[2]["message"] == "正在生成第 1-1 集大纲"
    assert all(event["payload"]["stage"] == "outlines" for event in request_events)
    assert all(event["payload"]["completed"] == 0 for event in request_events)
    assert all(event["payload"]["total"] == 1 for event in request_events)
    assert request_events[0]["payload"]["batch_in_flight"] is True
    assert request_events[1]["payload"]["batch_in_flight"] is False


def test_outline_worker_retry_progress_preserves_active_batch_telemetry(tmp_path, monkeypatch):
    class ProviderWithRetryNotice(SequentialProvider):
        retry_callback = None

        async def complete_json(self, messages):
            if self.retry_callback and len(self.calls) == 2:
                self.retry_callback(1, 4, "upstream read timeout")
            return await super().complete_json(messages)

    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = _prepare_outline_worker_project(repository, episode_count=12)
    provider = ProviderWithRetryNotice([
        series_arc_payload(),
        {"episodes": [outline_episode(number) for number in range(1, 11)]},
        {"episodes": [outline_episode(number) for number in range(11, 13)]},
    ])
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "level": level, "payload": payload or {}},
        ),
    )
    job = CrawlJob(
        job_id="creative-worker-outline-retry-telemetry",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines",
            "project_id": project["id"],
            "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"),
            **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    retries = [event["payload"] for event in events if event["payload"].get("retry_attempt")]
    assert [payload["request_kind"] for payload in retries] == ["outline_chunk"]
    assert [(payload["batch_start"], payload["batch_end"]) for payload in retries] == [(11, 12)]
    assert [(payload["completed"], payload["total"]) for payload in retries] == [(10, 12)]
    assert all(payload["batch_in_flight"] is True for payload in retries)
    assert all(payload["batch_started_at"] > 0 for payload in retries)
    assert all(payload["reasoning_effort"] == "high" for payload in retries)
    assert all(payload["prompt_chars"] > 0 for payload in retries)
    assert all(payload["request_elapsed_seconds"] >= 0 for payload in retries)


@pytest.mark.parametrize("configured_effort", ["xhigh", "max", "turbo"])
def test_outline_worker_falls_back_to_high_reasoning_and_warns_for_invalid_setting(
    tmp_path, monkeypatch, configured_effort
):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = _prepare_outline_worker_project(repository)
    provider = SequentialProvider([series_arc_payload(), {"episodes": [outline_episode(1)]}])
    provider.reasoning_effort = "none"
    events = []
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker.config,
        "CREATIVE_OUTLINE_REASONING_EFFORT",
        configured_effort,
        raising=False,
    )
    monkeypatch.setattr(
        phase4_worker,
        "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "level": level, "payload": payload or {}},
        ),
    )
    job = CrawlJob(
        job_id="creative-worker-outline-invalid-reasoning",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "outlines", "project_id": project["id"], "db_path": str(db_path),
            "runtime_dir": str(tmp_path / ".runtime"), **generation_contract(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    assert provider.reasoning_effort == "high"
    warning = next(event for event in events if event["level"] == "warning")
    assert "CREATIVE_OUTLINE_REASONING_EFFORT" in warning["message"]


def test_non_outline_worker_action_keeps_provider_reasoning_effort(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository, episode_count=1)
    provider = SequentialProvider([blueprint_payload()])
    provider.reasoning_effort = "none"
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(phase4_worker.config, "CREATIVE_OUTLINE_REASONING_EFFORT", "xhigh", raising=False)
    job = CrawlJob(
        job_id="creative-worker-blueprint-reasoning",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
            **authority_extra(repository, project["id"]),
        },
    )

    asyncio.run(phase4_worker.run(job))

    assert provider.reasoning_effort == "none"


def test_rewrite_outline_field_calls_llm_once_and_returns_only_target_value():
    provider = SequentialProvider([{"value": "主角在旧剧院找到被烧毁账本的末页。"}])
    prompt_snapshot = {
        "template_id": "system:outline-test",
        "revision": 2,
        "content_hash": "outline-rewrite-hash",
        "effective_instruction": "保持悬疑信息留白，通过既有道具导出反转。",
    }
    value, meta = asyncio.run(script_service.rewrite_outline_field(
        provider,
        project={"id": 8, "name": "旧剧院", "target_episode_count": 3},
        brief={"content": {"title": "旧剧院迷局"}},
        characters=[{"content": {"name": "林舟"}}],
        relationships=[],
        episode={"episode": 2, "core_event": "找到线索", "conflict": "是否赴约"},
        previous_episode={"episode": 1, "cliffhanger": "收到匿名票根"},
        next_episode={"episode": 3, "core_event": "旧剧院交锋"},
        field="core_event",
        instruction="强化危险感",
        prompt_snapshot=prompt_snapshot,
    ))

    assert value == "主角在旧剧院找到被烧毁账本的末页。"
    assert len(provider.calls) == 1
    assert "当前项目创作风格要求" in provider.calls[0][0]["content"]
    assert "既有道具导出反转" in provider.calls[0][0]["content"]
    payload = json.loads(provider.calls[0][1]["content"])
    assert payload["target_field"] == "core_event"
    assert payload["instruction"] == "强化危险感"
    assert payload["current_episode"]["conflict"] == "是否赴约"
    assert payload["previous_episode"]["episode"] == 1
    assert payload["next_episode"]["episode"] == 3
    assert meta["prompt_template_version"] == "outline-field-rewrite-v1"
    assert meta["prompt_template_id"] == "system:outline-test"
    assert meta["prompt_template_revision"] == 2


@pytest.mark.parametrize("field", OUTLINE_TEXT_FIELDS)
def test_rewrite_outline_field_accepts_each_outline_text_field(field: str):
    provider = SequentialProvider([{"value": f"改写后的{OUTLINE_FIELD_LABELS[field]}"}])

    value, _ = asyncio.run(script_service.rewrite_outline_field(
        provider,
        project={"id": 8, "name": "项目"}, brief={}, characters=[], relationships=[],
        episode={"episode": 2, field: "原始内容"}, previous_episode=None, next_episode=None,
        field=field,
    ))

    assert value == f"改写后的{OUTLINE_FIELD_LABELS[field]}"
    assert len(provider.calls) == 1


def test_rewrite_outline_field_rejects_unsupported_field_without_calling_llm():
    provider = SequentialProvider([])

    with pytest.raises(ValueError, match="不支持的大纲字段"):
        asyncio.run(script_service.rewrite_outline_field(
            provider, {"id": 8}, {}, [], [], {"episode": 2}, None, None, "title",
        ))

    assert provider.calls == []


@pytest.mark.parametrize("response", [
    {"value": ""},
    {"value": "  "},
    {"value": "有效文本", "episode": 2},
    {"value": '{"episode": 2, "core_event": "旧事件", "conflict": "旧冲突"}'},
    {"value": '{"core_event": "旧事件", "conflict": "旧冲突", "reversal": "旧反转"}'},
    {"value": '{"episode_number": 2, "core_event": "旧事件", "conflict": "旧冲突"}'},
    {"value": '```json\n{"episode": 2, "core_event": "旧事件", "conflict": "旧冲突"}\n```'},
    {"value": "## 第 2 集\n\n- 核心事件：旧事件\n- 冲突：旧冲突\n- 人物推进：主角作出选择"},
    {"value": "| 字段 | 内容 |\n| --- | --- |\n| 核心事件 | 旧事件 |\n| 冲突 | 旧冲突 |\n| 反转 | 旧反转 |"},
])
def test_rewrite_outline_field_rejects_invalid_single_field_response_after_one_call(response: dict[str, str]):
    provider = SequentialProvider([response])

    with pytest.raises(ValueError):
        asyncio.run(script_service.rewrite_outline_field(
            provider, {"id": 8}, {}, [], [], {"episode": 2}, None, None, "core_event",
        ))

    assert len(provider.calls) == 1


def test_rewrite_outline_field_accepts_prose_that_mentions_other_field_labels():
    provider = SequentialProvider([{
        "value": "新稿让冲突升级，但核心事件仍是主角找到账本。"
    }])

    value, _ = asyncio.run(script_service.rewrite_outline_field(
        provider, {"id": 8}, {}, [], [], {"episode": 2}, None, None, "core_event",
    ))

    assert value == "新稿让冲突升级，但核心事件仍是主角找到账本。"
    assert len(provider.calls) == 1


def test_rewrite_outline_field_propagates_provider_error_without_a_second_call():
    provider = SequentialProvider([RuntimeError("provider unavailable")])

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(script_service.rewrite_outline_field(
            provider, {"id": 8}, {}, [], [], {"episode": 2}, None, None, "core_event",
        ))

    assert len(provider.calls) == 1


def test_outline_field_source_fingerprint_tracks_source_identity_and_current_value_only():
    episode = {
        "project_id": 8, "batch_id": 13, "id": 21, "episode_number": 2,
        "core_event": "找到线索", "candidate": "候选文本 A",
    }

    source_a = outline_field_source_fingerprint(episode, "core_event")
    candidate_changed = {**episode, "candidate": "候选文本 B"}
    source_changed = {**episode, "core_event": "新的线索"}

    assert source_a == outline_field_source_fingerprint(candidate_changed, "core_event")
    assert source_a != outline_field_source_fingerprint(source_changed, "core_event")
