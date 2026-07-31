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
    script_messages,
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
        "growth_arc", "dialogue_style", "appearance_prompt", "voice_prompt", "action_goal",
        "fear_of_loss", "behavioral_contradiction", "pressure_response", "bottom_line",
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


_CONTINUITY_UPDATE_FIELDS = {
    "kind", "name", "event", "holder", "location", "known_by", "status",
}


def _validate_continuity_updates(updates: Any) -> list[dict[str, Any]]:
    if not isinstance(updates, list):
        raise ValueError("连续性变更必须是数组")
    normalized = []
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("连续性变更必须是对象")
        item = _require_fields(update, _CONTINUITY_UPDATE_FIELDS, "连续性变更")
        if not all(str(item[key]).strip() for key in ("kind", "name", "event", "status")):
            raise ValueError("连续性变更的类型、名称、事件和状态不能为空")
        if not isinstance(item["known_by"], list):
            raise ValueError("连续性变更的知情人必须是数组")
        normalized.append(item)
    return normalized


def validate_beats(payload: dict[str, Any] | list[Any], *, allow_legacy: bool = False) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = {"beats": payload}
    beats = payload.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats 至少包含一个节拍")
    enhanced_fields = {"state_change", "cost", "opposition_tactic", "continuity_updates"}
    if allow_legacy and all(
        isinstance(beat, dict) and not (enhanced_fields & set(beat))
        for beat in beats
    ):
        return {"beats": [dict(beat) for beat in beats]}
    if len(beats) > 6:
        raise ValueError("每集 Beats 数量必须为 1-6 个")
    fields = {
        "order", "scene", "characters", "goal", "obstacle", "emotion_change", "turn", "hook",
        "state_change", "cost", "opposition_tactic", "continuity_updates",
    }
    normalized = []
    for beat in beats:
        item = _require_fields(beat, fields, "Beat")
        if not all(str(item[key]).strip() for key in ("scene", "goal", "obstacle", "state_change", "cost", "opposition_tactic")):
            raise ValueError("Beat 的场景、目标、阻力、局面变化、代价和对抗策略不能为空")
        if not isinstance(item["characters"], list):
            raise ValueError("Beat 的出场人物必须是数组")
        item["continuity_updates"] = _validate_continuity_updates(item["continuity_updates"])
        normalized.append(item)
    normalized.sort(key=lambda item: int(item["order"]))
    if [int(item["order"]) for item in normalized] != list(range(1, len(normalized) + 1)):
        raise ValueError("Beat 序号必须从 1 连续编号")
    return {"beats": normalized}


def build_continuity_ledger(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce ordered Beat updates into the latest state for tracked story entities."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for episode in sorted(episodes, key=lambda item: int(item.get("episode_number") or 0)):
        beats = (episode.get("content") or {}).get("beats") or []
        for beat in sorted(beats, key=lambda item: int(item.get("order") or 0)):
            for update in beat.get("continuity_updates") or []:
                if not isinstance(update, dict):
                    continue
                kind = str(update.get("kind") or "").strip()
                name = str(update.get("name") or "").strip()
                if kind and name:
                    latest[(kind, name)] = dict(update)
    return list(latest.values())


def validate_script(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("episode_title") or "").strip()
    scenes = payload.get("scenes")
    if not title or not isinstance(scenes, list) or not scenes:
        raise ValueError("场次剧本缺少 episode_title 或 scenes")
    required = {"sequence_no", "scene_no", "time_of_day", "interior_exterior", "location", "props", "characters", "blocks"}
    seen: set[tuple[int, int]] = set()
    normalized = []
    for scene in scenes:
        item = _require_fields(scene, required, "场次")
        key = (int(item["sequence_no"]), int(item["scene_no"]))
        if key in seen:
            raise ValueError("场次编号不能重复")
        seen.add(key)
        if not all(str(item[field]).strip() for field in ("time_of_day", "interior_exterior", "location")):
            raise ValueError("场次时间、内外、场景不能为空")
        if not isinstance(item["props"], list) or not isinstance(item["characters"], list):
            raise ValueError("道具和出场人物必须是数组")
        blocks = item["blocks"]
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("每场至少包含一个内容块")
        for block in blocks:
            kind = str(block.get("type") or "")
            if kind not in {"action", "dialogue", "voiceover", "inner_voice", "subtitle", "cut"}:
                raise ValueError("内容块类型无效")
            if kind != "cut" and not str(block.get("text") or "").strip():
                raise ValueError("内容块缺少文本")
            if kind == "dialogue" and not str(block.get("speaker") or "").strip():
                raise ValueError("台词缺少人物")
        normalized.append(item)
    return {"episode_title": title, "scenes": sorted(normalized, key=lambda item: (int(item["sequence_no"]), int(item["scene_no"]))) }


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
    continuity_ledger: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project),
        "locked_outline": outline,
        "characters": characters,
        "episode_number": int(episode_number),
        "continuity_ledger": continuity_ledger or [],
    }
    beats, meta = await complete_with_one_repair(
        provider,
        beats_messages(payload, int(episode_number)),
        validate_beats,
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return beats, meta


async def generate_episode_script(
    provider: Any,
    project: dict[str, Any],
    outline: dict[str, Any],
    characters: list[dict[str, Any]],
    beats: dict[str, Any],
    episode_number: int,
    continuity_ledger: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project), "locked_outline": outline,
        "characters": characters, "locked_beats": beats,
        "episode_number": int(episode_number), "continuity_ledger": continuity_ledger or [],
    }
    script, meta = await complete_with_one_repair(
        provider, script_messages(payload, int(episode_number)), validate_script
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return script, meta
