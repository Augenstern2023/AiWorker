# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import inspect
from typing import Any, Callable

from creative.imitation_service import complete_with_one_repair
from creative.prompt_templates import (
    PROMPT_TEMPLATE_VERSION,
    beats_messages,
    character_messages,
    outline_messages,
    series_arc_messages,
)


def _project_context(project: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "genre", "target_platform", "target_audience",
        "target_episode_count", "episode_duration_seconds", "locked_brief_id",
        "character_batch_id", "outline_batch_id",
    )
    return {key: project.get(key) for key in keys}


def _require_fields(item: dict[str, Any], required: set[str], label: str) -> dict[str, Any]:
    missing = required - set(item)
    if missing:
        raise ValueError(f"{label}缺少字段：{sorted(missing)}")
    return dict(item)


def _validate_character_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    characters = payload.get("characters")
    relationships = payload.get("relationships")
    if not isinstance(characters, list) or not characters:
        raise ValueError("characters 至少包含一个人物")
    if not isinstance(relationships, list):
        raise ValueError("relationships 必须是数组")
    character_fields = {
        "name", "age_range", "identity", "external_tags", "desire", "secret",
        "growth_arc", "dialogue_style", "appearance_prompt", "voice_prompt",
    }
    relationship_fields = {"from", "to", "relation", "conflict", "change_arc"}
    return {
        "characters": [_require_fields(item, character_fields, "人物卡") for item in characters],
        "relationships": [_require_fields(item, relationship_fields, "人物关系") for item in relationships],
    }


def _validate_series_arc(payload: dict[str, Any]) -> dict[str, Any]:
    arc = str(payload.get("series_arc") or "").strip()
    stages = payload.get("stages")
    if not arc or not isinstance(stages, list) or not stages:
        raise ValueError("全剧阶段弧线缺少 series_arc 或 stages")
    return {"series_arc": arc, "stages": stages}


def _validate_outline_chunk(payload: dict[str, Any], start: int, end: int) -> list[dict[str, Any]]:
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("episodes 必须是数组")
    fields = {
        "episode", "core_event", "conflict", "character_progress", "highlight",
        "reversal", "cliffhanger", "estimated_seconds",
    }
    normalized = [_require_fields(item, fields, "分集大纲") for item in episodes]
    actual = sorted(int(item["episode"]) for item in normalized)
    expected = list(range(start, end + 1))
    if actual != expected:
        raise ValueError(f"分集大纲必须覆盖 {start}-{end} 集")
    return sorted(normalized, key=lambda item: int(item["episode"]))


def _validate_beats(payload: dict[str, Any]) -> dict[str, Any]:
    beats = payload.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats 至少包含一个节拍")
    fields = {"order", "scene", "characters", "goal", "obstacle", "emotion_change", "turn", "hook"}
    normalized = [_require_fields(item, fields, "Beat") for item in beats]
    normalized.sort(key=lambda item: int(item["order"]))
    return {"beats": normalized}


async def _checkpoint(callback: Callable[[], Any] | None) -> None:
    if callback is None:
        return
    result = callback()
    if inspect.isawaitable(result):
        await result


async def generate_character_bundle(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle, meta = await complete_with_one_repair(
        provider,
        character_messages({"project": _project_context(project), "locked_brief": brief}),
        _validate_character_bundle,
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return bundle, meta


async def generate_outline_bundle(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    on_progress: Callable[[int, int], Any] | None = None,
    checkpoint: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    total = int(project.get("target_episode_count") or 0)
    if not 1 <= total <= 100:
        raise ValueError("目标集数必须在 1-100 之间")
    base = {
        "project": _project_context(project),
        "locked_brief": brief,
        "characters": characters,
        "relationships": relationships,
    }
    await _checkpoint(checkpoint)
    arc, meta = await complete_with_one_repair(provider, series_arc_messages(base), _validate_series_arc)
    episodes: list[dict[str, Any]] = []
    for start in range(1, total + 1, 10):
        end = min(total, start + 9)
        await _checkpoint(checkpoint)
        chunk, chunk_meta = await complete_with_one_repair(
            provider,
            outline_messages({**base, "series_arc": arc, "completed_episodes": episodes}, start, end),
            lambda payload, chunk_start=start, chunk_end=end: _validate_outline_chunk(payload, chunk_start, chunk_end),
        )
        episodes.extend(chunk)
        meta.update({key: value for key, value in chunk_meta.items() if value})
        if on_progress:
            result = on_progress(len(episodes), total)
            if inspect.isawaitable(result):
                await result
    meta.update({"prompt_template_version": PROMPT_TEMPLATE_VERSION, "chunk_size": 10})
    return {"series_arc": arc["series_arc"], "stages": arc["stages"], "episodes": episodes}, meta


async def generate_episode_beats(
    provider: Any,
    project: dict[str, Any],
    outline: dict[str, Any],
    characters: list[dict[str, Any]],
    episode_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project),
        "locked_outline": outline,
        "characters": characters,
        "episode_number": int(episode_number),
    }
    beats, meta = await complete_with_one_repair(
        provider,
        beats_messages(payload, int(episode_number)),
        _validate_beats,
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return beats, meta
