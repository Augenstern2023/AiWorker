# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from creative.imitation_service import complete_with_one_repair
from creative.prompt_templates import (
    PROMPT_TEMPLATE_VERSION,
    beats_chunk_messages,
    beats_messages,
    character_messages,
    outline_field_repair_messages,
    outline_messages,
    series_arc_messages,
    script_messages,
    script_chunk_messages,
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


def _normalize_scene_number(value: Any, field_label: str) -> int:
    """Accept common episode-scene labels while persisting a stable numeric field."""
    if isinstance(value, bool):
        raise ValueError(f"场次的{field_label}必须是正整数")
    if isinstance(value, int):
        number = value
    else:
        text = str(value or "").strip()
        if text.isdigit():
            number = int(text)
        else:
            composite = re.fullmatch(r"(?:第\s*)?\d+\s*[-_/]\s*(\d+)\s*(?:段|场)?", text)
            if not composite:
                raise ValueError(f"场次的{field_label}必须是正整数，不能使用“{text}”")
            number = int(composite.group(1))
    if number < 1:
        raise ValueError(f"场次的{field_label}必须是大于 0 的整数")
    return number


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


_OUTLINE_TEXT_FIELDS = {
    "core_event", "conflict", "character_progress", "highlight", "reversal", "cliffhanger",
}


def _normalize_outline_chunk(
    payload: dict[str, Any] | list[Any],
    start: int,
    end: int,
    *,
    default_estimated_seconds: int | None = None,
) -> dict[str, Any]:
    if isinstance(payload, list):
        payload = {"episodes": payload}
    if not isinstance(payload, dict):
        raise ValueError("分集大纲必须返回对象或 episodes 数组")
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("episodes 必须是数组")
    fields = {
        "episode", "core_event", "conflict", "character_progress", "highlight",
        "reversal", "cliffhanger", "estimated_seconds",
    }
    normalized = []
    missing_fields = {
        int(episode["episode"]): sorted(
            field for field in _OUTLINE_TEXT_FIELDS
            if not str(episode.get(field) or "").strip()
        )
        for episode in episodes
    }
    missing_fields = {
        episode_number: fields
        for episode_number, fields in missing_fields.items()
        if fields
    }
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("分集大纲数组项必须是对象")
        item = dict(episode)
        if "episode" not in item:
            raise ValueError("分集大纲缺少字段：['episode']")
        episode_number = int(item["episode"])
        missing = []
        for field in _OUTLINE_TEXT_FIELDS:
            if not str(item.get(field) or "").strip():
                item[field] = ""
                missing.append(field)
        if "estimated_seconds" not in item and default_estimated_seconds is not None:
            item["estimated_seconds"] = default_estimated_seconds
        normalized.append(_require_fields(item, fields, "分集大纲"))
        if missing:
            missing_fields[episode_number] = sorted(missing)
    actual = sorted(int(item["episode"]) for item in normalized)
    expected = list(range(start, end + 1))
    if actual != expected:
        raise ValueError(f"分集大纲必须覆盖 {start}-{end} 集")
    return {
        "episodes": sorted(normalized, key=lambda item: int(item["episode"])),
        "missing_fields": missing_fields,
    }


def _validate_outline_field_repair(payload: Any, fields: list[str]) -> dict[str, str]:
    values = payload.get("fields", payload) if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise ValueError("字段修复必须返回 fields 对象")
    result = {}
    for field in fields:
        value = str(values.get(field) or "").strip()
        if not value:
            raise ValueError(f"字段修复缺少 {field}")
        result[field] = value
    return result


def _outline_field_fallback(episode: dict[str, Any], field: str) -> str:
    number = int(episode.get("episode") or 0)
    core_event = str(episode.get("core_event") or f"第 {number} 集主线事件")
    values = {
        "core_event": f"第 {number} 集围绕主线危机继续推进。",
        "conflict": f"围绕{core_event}，主角与阻力方发生正面冲突。",
        "character_progress": "主角在压力下作出选择，人物关系随之发生变化。",
        "highlight": "局势突然升级，形成当集爽点。",
        "reversal": "关键判断被新信息推翻，局面出现反转。",
        "cliffhanger": "新的风险在结尾出现，推动下一集。",
    }
    return values[field]


async def _repair_outline_fields(
    provider: Any,
    base: dict[str, Any],
    arc: dict[str, Any],
    episodes: list[dict[str, Any]],
    missing_fields: dict[int, list[str]],
) -> list[dict[str, Any]]:
    fallback_fields = []
    for index, episode in enumerate(episodes):
        fields = missing_fields.get(int(episode["episode"]))
        if not fields:
            continue
        payload = {
            **base,
            "series_arc": arc,
            "episode": episode,
            "repair_fields": fields,
            "previous_episode": episodes[index - 1] if index else None,
            "next_episode": episodes[index + 1] if index + 1 < len(episodes) else None,
        }
        try:
            repaired, _ = await complete_with_one_repair(
                provider,
                outline_field_repair_messages(payload),
                lambda result, required=fields: _validate_outline_field_repair(result, required),
                max_attempts=3,
            )
        except Exception:
            repaired = {field: _outline_field_fallback(episode, field) for field in fields}
            fallback_fields.append({"episode": int(episode["episode"]), "fields": fields})
        episode.update(repaired)
    return fallback_fields


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
        item["sequence_no"] = _normalize_scene_number(item["sequence_no"], "段号")
        item["scene_no"] = _normalize_scene_number(item["scene_no"], "场号")
        key = (item["sequence_no"], item["scene_no"])
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
            if kind not in {"action", "dialogue", "voiceover", "inner_voice", "cut"}:
                raise ValueError("内容块类型无效")
            if kind != "cut" and not str(block.get("text") or "").strip():
                raise ValueError("内容块缺少文本")
            if kind == "dialogue" and not str(block.get("speaker") or "").strip():
                raise ValueError("台词缺少人物")
        normalized.append(item)
    return {"episode_title": title, "scenes": sorted(normalized, key=lambda item: (int(item["sequence_no"]), int(item["scene_no"]))) }


def normalize_script_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Hide obsolete subtitle blocks from persisted scene scripts without rewriting history."""
    content = dict(payload) if isinstance(payload, dict) else {}
    scenes = content.get("scenes")
    if not isinstance(scenes, list):
        return content

    normalized_scenes = []
    for scene in scenes:
        if not isinstance(scene, dict):
            normalized_scenes.append(scene)
            continue
        normalized_scene = dict(scene)
        blocks = scene.get("blocks")
        if isinstance(blocks, list):
            normalized_scene["blocks"] = [
                dict(block) if isinstance(block, dict) else block
                for block in blocks
                if not (
                    isinstance(block, dict)
                    and str(block.get("type") or "").strip().lower() == "subtitle"
                )
            ]
        normalized_scenes.append(normalized_scene)
    content["scenes"] = normalized_scenes
    return content


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
    on_checkpoint: Callable[[dict[str, Any], list[dict[str, Any]], dict[str, Any]], Any] | None = None,
    checkpoint: Callable[[], Any] | None = None,
    resume_episodes: list[dict[str, Any]] | None = None,
    resume_series_arc: str | None = None,
    resume_stages: list[Any] | None = None,
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
    default_estimated_seconds = max(
        1, int(project.get("episode_duration_seconds") or 120)
    )
    episodes = [dict(item) for item in (resume_episodes or [])]
    if episodes:
        actual = sorted(int(item.get("episode") or 0) for item in episodes)
        expected = list(range(1, len(episodes) + 1))
        if actual != expected or len(episodes) >= total:
            raise ValueError("可继续生成的大纲必须是未完成的连续分集前缀")
        series_arc = str(resume_series_arc or "").strip()
        if not series_arc or not isinstance(resume_stages, list) or not resume_stages:
            raise ValueError("可继续生成的大纲缺少全剧阶段弧线")
        arc = {"series_arc": series_arc, "stages": list(resume_stages)}
        meta = {"resumed_from_episode": len(episodes)}
    else:
        await _checkpoint(checkpoint)
        arc, meta = await complete_with_one_repair(
            provider, series_arc_messages(base), _validate_series_arc
        )
    missing_fields: dict[int, list[str]] = {}
    for start in range(len(episodes) + 1, total + 1, 10):
        end = min(total, start + 9)
        await _checkpoint(checkpoint)
        chunk_result, chunk_meta = await complete_with_one_repair(
            provider,
            outline_messages({**base, "series_arc": arc, "completed_episodes": episodes}, start, end),
            lambda payload, chunk_start=start, chunk_end=end: _normalize_outline_chunk(
                payload,
                chunk_start,
                chunk_end,
                default_estimated_seconds=default_estimated_seconds,
            ),
            max_attempts=4,
        )
        chunk = chunk_result["episodes"]
        episodes.extend(chunk)
        missing_fields.update(chunk_result["missing_fields"])
        meta.update({key: value for key, value in chunk_meta.items() if value})
        if on_checkpoint:
            result = on_checkpoint(arc, episodes, meta)
            if inspect.isawaitable(result):
                await result
        if on_progress:
            result = on_progress(len(episodes), total)
            if inspect.isawaitable(result):
                await result
    fallback_fields = await _repair_outline_fields(
        provider, base, arc, episodes, missing_fields
    )
    if missing_fields:
        meta["field_repair_count"] = sum(len(fields) for fields in missing_fields.values())
        meta["backend_fallback_fields"] = fallback_fields
        if on_checkpoint:
            result = on_checkpoint(arc, episodes, meta)
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
    *,
    brief: dict[str, Any] | None = None,
    relationships: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project),
        "locked_brief": brief or {},
        "locked_outline": outline,
        "characters": characters,
        "relationships": relationships or [],
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


def _normalize_episode_generation_chunk(
    payload: dict[str, Any] | list[Any],
    episode_numbers: list[int],
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    label: str,
) -> list[tuple[int, dict[str, Any]]]:
    if isinstance(payload, list):
        payload = {"episodes": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("episodes"), list):
        raise ValueError(f"{label}批量结果必须包含 episodes 数组")
    rows = payload["episodes"]
    numbers = [int(item.get("episode") or 0) for item in rows if isinstance(item, dict)]
    if len(rows) != len(episode_numbers) or sorted(numbers) != episode_numbers:
        raise ValueError(f"{label}批量结果必须完整覆盖第 {episode_numbers[0]}-{episode_numbers[-1]} 集")
    normalized: list[tuple[int, dict[str, Any]]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError(f"{label}批量结果的每集必须是对象")
        episode_number = int(item["episode"])
        content = {key: value for key, value in item.items() if key != "episode"}
        normalized.append((episode_number, validator(content)))
    return sorted(normalized, key=lambda item: item[0])


def _episode_chunk_numbers(outlines: list[dict[str, Any]]) -> list[int]:
    numbers = sorted(int(item.get("episode_number") or 0) for item in outlines)
    if not numbers or len(numbers) > 10 or numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise ValueError("批量生成必须使用不超过 10 集的连续分集大纲")
    return numbers


async def generate_episode_beats_chunk(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    outlines: list[dict[str, Any]],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    continuity_ledger: list[dict[str, Any]] | None = None,
) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    episode_numbers = _episode_chunk_numbers(outlines)
    payload = {
        "project": _project_context(project),
        "locked_brief": brief,
        "locked_outlines": [
            {"episode_number": int(item["episode_number"]), "content": item.get("content") or {}}
            for item in sorted(outlines, key=lambda item: int(item["episode_number"]))
        ],
        "characters": characters,
        "relationships": relationships,
        "continuity_ledger": continuity_ledger or [],
    }
    generated, meta = await complete_with_one_repair(
        provider,
        beats_chunk_messages(payload, episode_numbers[0], episode_numbers[-1]),
        lambda result: _normalize_episode_generation_chunk(
            result, episode_numbers, validate_beats, "Beats"
        ),
    )
    meta.update({"prompt_template_version": PROMPT_TEMPLATE_VERSION, "chunk_size": len(episode_numbers)})
    return generated, meta


async def generate_episode_script(
    provider: Any,
    project: dict[str, Any],
    outline: dict[str, Any],
    characters: list[dict[str, Any]],
    beats: dict[str, Any],
    episode_number: int,
    continuity_ledger: list[dict[str, Any]] | None = None,
    *,
    brief: dict[str, Any] | None = None,
    relationships: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project), "locked_brief": brief or {}, "locked_outline": outline,
        "characters": characters, "locked_beats": beats,
        "relationships": relationships or [],
        "episode_number": int(episode_number), "continuity_ledger": continuity_ledger or [],
    }
    script, meta = await complete_with_one_repair(
        provider, script_messages(payload, int(episode_number)), validate_script
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return script, meta


async def generate_episode_script_chunk(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    outlines: list[dict[str, Any]],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    beats_by_episode: dict[int, dict[str, Any]],
    *,
    continuity_ledger: list[dict[str, Any]] | None = None,
) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    episode_numbers = _episode_chunk_numbers(outlines)
    if sorted(beats_by_episode) != episode_numbers:
        raise ValueError("场次剧本批量生成缺少当前集已锁定 Beats")
    payload = {
        "project": _project_context(project),
        "locked_brief": brief,
        "locked_outlines": [
            {"episode_number": int(item["episode_number"]), "content": item.get("content") or {}}
            for item in sorted(outlines, key=lambda item: int(item["episode_number"]))
        ],
        "characters": characters,
        "relationships": relationships,
        "locked_beats": [
            {"episode_number": episode_number, "content": beats_by_episode[episode_number]}
            for episode_number in episode_numbers
        ],
        "continuity_ledger": continuity_ledger or [],
    }
    generated, meta = await complete_with_one_repair(
        provider,
        script_chunk_messages(payload, episode_numbers[0], episode_numbers[-1]),
        lambda result: _normalize_episode_generation_chunk(
            result, episode_numbers, validate_script, "场次剧本"
        ),
    )
    meta.update({"prompt_template_version": PROMPT_TEMPLATE_VERSION, "chunk_size": len(episode_numbers)})
    return generated, meta
