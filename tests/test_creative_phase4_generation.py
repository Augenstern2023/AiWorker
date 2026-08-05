# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio

import httpx
import pytest

from core.job import CrawlJob
from creative import api as creative_api
from creative import phase4_worker
from creative.api import build_beats_markdown
from creative.imitation_service import generate_blueprints
from creative.imitation_service import complete_with_one_repair
from creative.prompt_templates import character_messages, outline_messages, beats_messages, script_messages
from creative.repository import CreativeRepository
from creative.script_service import (
    build_continuity_ledger,
    generate_character_bundle,
    generate_episode_beats_chunk,
    generate_episode_beats,
    generate_outline_bundle,
    validate_beats,
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
async def test_outline_generation_accepts_a_bare_episode_array_from_the_llm():
    provider = SequentialProvider([
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
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
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
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
    )

    assert len(provider.calls) == 1
    assert [item["episode"] for item in result["episodes"]] == list(range(1, 13))
    assert meta["resumed_from_episode"] == 10


@pytest.mark.asyncio
async def test_outline_generation_retries_a_malformed_chunk_before_failing_the_batch():
    provider = SequentialProvider([
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
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
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
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


def test_phase4_worker_preserves_completed_outline_chunks_after_a_later_failure(tmp_path, monkeypatch):
    db_path = tmp_path / ".runtime" / "creative" / "creative.db"
    repository = CreativeRepository(db_path)
    project = seed_worker_project(repository)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    provider = SequentialProvider([
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
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
        {"series_arc": "相识、对抗、合作", "stages": ["相识", "对抗", "合作"]},
        {"episodes": [outline_episode(number) for number in range(1, 11)]},
        {"episodes": []},
        {"episodes": []},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: failed_provider)
    failed_job = CrawlJob(
        job_id="creative-worker-outline-failed",
        platform="creative", crawler_type="phase4_generate",
        extra={"action": "outlines", "project_id": project["id"], "db_path": str(db_path)},
    )
    with pytest.raises(Exception, match="结构校验"):
        asyncio.run(phase4_worker.run(failed_job))

    resumed_provider = SequentialProvider([
        {"episodes": [outline_episode(number) for number in range(11, 21)]},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: resumed_provider)
    resumed_job = CrawlJob(
        job_id="creative-worker-outline-resumed",
        platform="creative", crawler_type="phase4_generate",
        extra={"action": "outlines", "project_id": project["id"], "db_path": str(db_path)},
    )

    asyncio.run(phase4_worker.run(resumed_job))

    outlines = [
        item for item in repository.get_project(project["id"])["episodes"]
        if item["stage"] == "outline"
    ]
    assert len(resumed_provider.calls) == 1
    assert [item["episode_number"] for item in outlines] == list(range(1, 21))
    assert len({item["batch_id"] for item in outlines}) == 1


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
        },
    )

    asyncio.run(phase4_worker.run(job))

    beats = [item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "beats"]
    assert [item["episode_number"] for item in beats] == list(range(1, 13))
    assert len({item["batch_id"] for item in beats}) == 1
    assert len(provider.calls) == 2
    assert any(item["payload"].get("completed") == 10 for item in events)
    assert any(item["payload"].get("completed") == 12 for item in events)


def test_phase4_worker_saves_series_scripts_after_all_current_beats_are_locked(tmp_path, monkeypatch):
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
    for episode_number in range(1, 13):
        beats = repository.create_episode_beats(project["id"], episode_number, valid_beat_content())
        repository.lock_episode_beats(beats["id"])
    provider = SequentialProvider([
        {"episodes": [{"episode": number, **valid_script_content(number)} for number in range(1, 11)]},
        {"episodes": [{"episode": number, **valid_script_content(number)} for number in range(11, 13)]},
    ])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    job = CrawlJob(
        job_id="creative-worker-series-scripts", platform="creative", crawler_type="phase4_generate",
        extra={
            "action": "script", "series": True, "project_id": project["id"],
            "db_path": str(db_path), "runtime_dir": str(tmp_path / ".runtime"),
        },
    )

    asyncio.run(phase4_worker.run(job))

    scripts = [item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "script"]
    assert [item["episode_number"] for item in scripts] == list(range(1, 13))
    assert {item["status"] for item in scripts} == {"draft"}
    assert len({item["batch_id"] for item in scripts}) == 1
    assert len(provider.calls) == 2
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
        },
    )

    asyncio.run(phase4_worker.run(job))

    retry_event = next(item for item in events if item["payload"].get("retry_attempt") == 1)
    assert retry_event["level"] == "warning"
    assert "第 1/2 次自动重试" in retry_event["message"]
