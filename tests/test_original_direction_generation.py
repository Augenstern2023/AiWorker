# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from analysis.llm import LLMError
from core.job import CrawlJob
from creative import phase4_worker
from creative.original_direction_service import (
    derive_original_direction,
    generate_original_directions,
    validate_original_direction_content,
)
from creative.repository import CreativeRepository


ORIGINAL_REQUIRED_FIELDS = {
    "title", "logline", "theme", "protagonist", "protagonist_desire",
    "protagonist_flaw", "pressure_response", "main_opposition",
    "core_conflict", "main_goal", "background", "worldview", "story_engine",
    "first_three_hooks", "highlights", "differentiators", "audience",
    "market_fit", "cost_level", "originality_statement", "risks",
    "assumptions", "open_questions",
}


class FakeProvider:
    provider_name = "fake"

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.call_count = 0

    async def complete_json(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = self.payloads[self.call_count]
        self.call_count += 1
        return payload


def direction(
    title: str, protagonist: str, background: str, conflict: str
) -> dict[str, Any]:
    return {
        "title": title, "logline": f"{protagonist}在{background}解决{conflict}",
        "theme": "人在制度中保持选择", "protagonist": protagonist,
        "protagonist_desire": "获得自主权", "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作", "main_opposition": "僵化制度",
        "core_conflict": conflict, "main_goal": "改变制度", "background": background,
        "worldview": "情绪可以被量化但不能被定义", "story_engine": f"围绕{conflict}推进",
        "first_three_hooks": ["工资因愤怒清零", "同事出售快乐", "AI 拒绝考核"],
        "highlights": ["规则反杀", "身份反差"], "differentiators": ["情绪劳动具象化"],
        "audience": "18-35 岁职场观众", "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low", "originality_statement": "人物和冲突均由种子独立发展",
        "risks": ["设定解释成本"], "assumptions": ["观众理解情绪积分"],
        "open_questions": ["公司为何采用该制度"],
    }


def valid_directions() -> list[dict[str, Any]]:
    return [
        direction("情绪公司", "林岚", "未来公司", "情绪考核"),
        direction("潮汐公约", "周屿", "海上社区", "资源配给"),
        direction("满分之外", "许真", "虚拟学校", "人格评分"),
    ]


PROJECT = {"id": 9, "name": "情绪公司", "creation_mode": "original"}
SEED = {
    "id": 7, "version": 1, "seed_text": "未来公司按情绪发工资",
    "constraints": {}, "reference_refs": [],
}
PARENT_DIRECTION = {
    "id": 11,
    "content": direction("情绪公司", "林岚", "未来公司", "情绪考核"),
}


@pytest.mark.asyncio
async def test_original_generation_returns_diverse_directions() -> None:
    provider = FakeProvider([{"directions": valid_directions()}])

    directions, meta = await generate_original_directions(
        provider, PROJECT, SEED, [], [], count=3,
    )

    assert len(directions) == 3
    assert all(ORIGINAL_REQUIRED_FIELDS <= set(item["content"]) for item in directions)
    assert meta["prompt_template_version"] == "original-direction-v1"
    assert meta["missing_count"] == 0


@pytest.mark.asyncio
async def test_invalid_candidates_are_repaired_without_dropping_valid_ones() -> None:
    first = direction("情绪公司", "林岚", "未来公司", "情绪考核")
    duplicate_a = direction("情绪公司甲", "林岚", "未来公司", "情绪考核")
    duplicate_b = direction("情绪公司乙", "林岚", "未来公司", "情绪考核")
    replacements = valid_directions()[1:]
    provider = FakeProvider([
        {"directions": [first, duplicate_a, duplicate_b]},
        {"directions": replacements},
    ])

    directions, meta = await generate_original_directions(
        provider, PROJECT, SEED, [], ["顾清歌"], count=3,
    )

    assert [item["content"]["title"] for item in directions] == [
        "情绪公司", "潮汐公约", "满分之外",
    ]
    assert provider.call_count == 2
    assert meta["repair_attempted"] is True


@pytest.mark.asyncio
async def test_original_generation_rejects_partial_repair_result() -> None:
    first = direction("情绪公司", "林岚", "未来公司", "情绪考核")
    duplicate = direction("情绪公司甲", "林岚", "未来公司", "情绪考核")
    provider = FakeProvider([
        {"directions": [first]},
        {"directions": [duplicate]},
    ])

    with pytest.raises(LLMError, match="未达到请求数"):
        await generate_original_directions(
            provider, PROJECT, SEED, [], [], count=3,
        )

    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_original_direction_rejects_blocked_source_terms() -> None:
    blocked = valid_directions()
    blocked[0]["protagonist"] = "顾清歌"
    blocked[1]["protagonist"] = "顾清歌"
    blocked[2]["protagonist"] = "顾清歌"

    with pytest.raises(LLMError, match="来源专有表达"):
        await generate_original_directions(
            FakeProvider([{"directions": blocked}]),
            PROJECT, SEED, [], ["顾清歌"], count=3,
        )


@pytest.mark.asyncio
async def test_market_context_adds_reference_ids() -> None:
    directions, _ = await generate_original_directions(
        FakeProvider([{"directions": valid_directions()}]),
        PROJECT, SEED,
        [{"resource_type": "topic", "resource_id": 23, "category": "都市"}],
        [], count=3,
    )

    assert all(item["content"]["reference_ids"] == ["topic:23"] for item in directions)


def test_original_direction_validation_rejects_missing_fields() -> None:
    invalid = direction("情绪公司", "林岚", "未来公司", "情绪考核")
    invalid.pop("story_engine")

    with pytest.raises(ValueError, match="缺少字段"):
        validate_original_direction_content(invalid, market_context=[], blocked_terms=[])


@pytest.mark.asyncio
async def test_derived_direction_points_to_parent() -> None:
    variant = direction("情绪迷局", "林岚", "未来社区", "工资失踪案")
    result, meta = await derive_original_direction(
        FakeProvider([{"direction": variant}]), PROJECT, SEED, PARENT_DIRECTION,
        instruction="保留情绪工资设定，改成悬疑类型",
        market_context=[], blocked_terms=[],
    )

    assert result["source_refs"]["parent_blueprint_id"] == PARENT_DIRECTION["id"]
    assert meta["prompt_template_version"] == "original-direction-variant-v1"


def _ready_original_repository(tmp_path) -> tuple[CreativeRepository, dict[str, Any], dict[str, Any]]:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text=SEED["seed_text"], name=PROJECT["name"], target_episode_count=20,
        created_by="writer-1", creation_request_id="worker-original",
    )
    repository.mark_project_bootstrap_status(project["id"], "ready")
    seed = repository.freeze_seed(repository.get_current_seed(project["id"])["id"])
    return repository, repository.get_project(project["id"]), seed


def test_worker_uses_frozen_seed_snapshot_instead_of_current_seed(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project, frozen_seed = _ready_original_repository(tmp_path)
    repository.create_seed_version(
        project["id"], seed_text="排队后新建的种子", constraints={},
        reference_refs=[], created_by="writer-1",
    )
    provider = FakeProvider([{"directions": valid_directions()}])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(phase4_worker, "emit", lambda *_args, **_kwargs: None)
    job = CrawlJob(
        job_id="original-worker", platform="creative", crawler_type="phase4_generate",
        extra={
            "action": "blueprints", "project_id": project["id"], "count": 3,
                "db_path": str(repository.db_path), "seed_id": frozen_seed["id"],
                "market_context": [], "blocked_terms": [], "reference_snapshot": [],
                "legacy_authority_compat": True,
        },
    )

    asyncio.run(phase4_worker.run(job))

    blueprints = repository.get_project(project["id"])["blueprints"]
    assert len(blueprints) == 3
    assert all(item["direction_type"] == "original" for item in blueprints)
    assert all(item["source_refs"]["seed_id"] == frozen_seed["id"] for item in blueprints)


def test_worker_creates_one_original_variant_with_parent_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project, frozen_seed = _ready_original_repository(tmp_path)
    parent = repository.create_blueprints(
        project["id"], [{"content": PARENT_DIRECTION["content"]}],
        direction_type="original",
        source_refs={"seed_id": frozen_seed["id"], "seed_version": 1},
    )[0]
    provider = FakeProvider([{
        "direction": direction("情绪迷局", "林岚", "未来社区", "工资失踪案")
    }])
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(phase4_worker, "emit", lambda *_args, **_kwargs: None)
    job = CrawlJob(
        job_id="variant-worker", platform="creative", crawler_type="phase4_generate",
        extra={
            "action": "blueprint_variant", "project_id": project["id"],
            "db_path": str(repository.db_path), "seed_id": frozen_seed["id"],
                "parent_blueprint_id": parent["id"], "instruction": "改成悬疑类型",
                "market_context": [], "blocked_terms": [],
                "legacy_authority_compat": True,
        },
    )

    asyncio.run(phase4_worker.run(job))

    variants = repository.get_project(project["id"])["blueprints"]
    child = max(variants, key=lambda item: item["id"])
    assert child["source_refs"]["parent_blueprint_id"] == parent["id"]


def test_original_high_risk_direction_does_not_require_imitation_difference_note(
    tmp_path,
) -> None:
    repository, project, frozen_seed = _ready_original_repository(tmp_path)
    blueprint = repository.create_blueprints(
        project["id"], [{"content": valid_directions()[0], "risk_score": 90, "risk_level": "high"}],
        direction_type="original",
        source_refs={"seed_id": frozen_seed["id"], "seed_version": 1},
    )[0]

    locked = repository.lock_brief(blueprint["id"])

    assert locked["status"] == "brief_locked"


def test_original_blueprint_version_keeps_direction_type_and_seed_source(
    tmp_path,
) -> None:
    repository, project, frozen_seed = _ready_original_repository(tmp_path)
    blueprint = repository.create_blueprints(
        project["id"], [{"content": valid_directions()[0]}],
        direction_type="original",
        source_refs={"seed_id": frozen_seed["id"], "seed_version": 1},
    )[0]

    version = repository.create_blueprint_version(blueprint["id"])

    assert version["direction_type"] == "original"
    assert version["source_refs"]["seed_id"] == frozen_seed["id"]
