# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from typing import Any, Callable

from analysis.llm import (
    LLMError,
    LLMOutputTruncatedError,
    is_retryable_llm_exception,
)
from creative.imitation_service import complete_with_one_repair
from creative.outline_fields import OUTLINE_FIELD_LABELS, OUTLINE_TEXT_FIELDS
from creative.outline_overview import normalize_series_overview
from creative.prompt_templates import (
    OUTLINE_FIELD_REWRITE_PROMPT_VERSION,
    PROMPT_TEMPLATE_VERSION,
    beats_chunk_messages,
    beats_messages,
    character_messages,
    outline_field_repair_messages,
    outline_field_rewrite_messages,
    outline_messages,
    series_arc_messages,
    series_overview_messages,
    script_messages,
    script_chunk_messages,
)


logger = logging.getLogger(__name__)


def _project_context(project: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "genre", "target_platform", "target_audience",
        "target_episode_count", "episode_duration_seconds", "locked_brief_id",
        "character_batch_id", "outline_batch_id",
    )
    return {key: project.get(key) for key in keys}


def _require_fields(item: dict[str, Any], required: set[str] | frozenset[str], label: str) -> dict[str, Any]:
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


CHARACTER_FIELDS = frozenset({
    "name", "age_range", "identity", "external_tags", "desire", "secret",
    "growth_arc", "dialogue_style", "appearance_prompt", "voice_prompt", "action_goal",
    "fear_of_loss", "behavioral_contradiction", "pressure_response", "bottom_line",
})
RELATIONSHIP_FIELDS = frozenset({"from", "to", "relation", "conflict", "change_arc"})

# 模型偶发会把键名写成近义词或中文，这里按别名兜住，避免整套人物卡被丢弃。
CHARACTER_KEY_ALIASES = ("characters", "character_cards", "roles", "人物卡", "人物")
RELATIONSHIP_KEY_ALIASES = (
    "relationships", "relations", "character_relationships", "relationship", "人物关系",
)

CHARACTER_BUNDLE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["characters", "relationships"],
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(CHARACTER_FIELDS),
                "properties": {
                    field: (
                        {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": r"\S"}}
                        if field == "external_tags"
                        else {"type": "string", "minLength": 1, "pattern": r"\S"}
                    )
                    for field in sorted(CHARACTER_FIELDS)
                },
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(RELATIONSHIP_FIELDS),
                "properties": {
                    field: {"type": "string", "minLength": 1, "pattern": r"\S"}
                    for field in sorted(RELATIONSHIP_FIELDS)
                },
            },
        },
    },
}


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _normalize_object_list(
    value: Any, *, item_fields: frozenset[str]
) -> list[Any] | None:
    """把模型常见的偏离形态归一化成人/关系数组；无法识别为数组时返回 None。

    接受的形态：数组原样返回；对象按「单条记录」或「键值映射」两种语义展开；
    null 与空串视为空数组；字符串若是 JSON 数组则解析。字段是否齐全仍由
    `_require_fields` 严格校验，这里只放宽形状。
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        if not value:
            return []
        if item_fields & set(value):
            return [dict(value)]
        return list(value.values())
    if isinstance(value, list):
        return value
    return None


def _require_object_items(items: list[Any], label: str) -> list[dict[str, Any]]:
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{label}每一项必须是对象")
    return items


def _validate_character_fields(item: dict[str, Any], fields: frozenset[str], label: str) -> dict[str, Any]:
    result = _require_fields(item, fields, label)
    for field in sorted(fields):
        value = result[field]
        if field == "external_tags":
            if not isinstance(value, list) or any(
                not isinstance(tag, str) or not tag.strip() for tag in value
            ):
                raise ValueError("人物卡 external_tags 必须是非空字符串组成的数组")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} {field} 必须是非空字符串")
    return result


def _validate_character_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("人物与人物关系必须返回 JSON 对象")
    characters = _normalize_object_list(
        _first_present(payload, CHARACTER_KEY_ALIASES), item_fields=CHARACTER_FIELDS
    )
    relationships = _normalize_object_list(
        _first_present(payload, RELATIONSHIP_KEY_ALIASES), item_fields=RELATIONSHIP_FIELDS
    )
    if characters is None:
        raise ValueError("characters 必须是数组")
    if not characters:
        raise ValueError("characters 至少包含一个人物")
    if relationships is None:
        raise ValueError("relationships 必须是数组")
    characters = [
        _validate_character_fields(item, CHARACTER_FIELDS, "人物卡")
        for item in _require_object_items(characters, "人物卡")
    ]
    relationships = [
        _validate_character_fields(item, RELATIONSHIP_FIELDS, "人物关系")
        for item in _require_object_items(relationships, "人物关系")
    ]
    # 空关系本身合法（例如全剧只有一个人物），但多人物却没有关系通常是模型漏输出，
    # 记一条告警便于事后定位，不因此判定任务失败。
    if not relationships and len(characters) > 1:
        logger.warning(
            "人物关系为空：%s 张人物卡未产出任何关系，可能为模型漏输出", len(characters)
        )
    return {"characters": characters, "relationships": relationships}


def _validate_series_arc(payload: dict[str, Any]) -> dict[str, Any]:
    arc = str(payload.get("series_arc") or "").strip()
    stages = payload.get("stages")
    if not arc or not isinstance(stages, list) or not stages:
        raise ValueError("全剧阶段弧线缺少 series_arc 或 stages")
    return {
        "series_arc": arc,
        "stages": stages,
        "series_overview": normalize_series_overview(payload.get("series_overview")),
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
            field for field in OUTLINE_TEXT_FIELDS
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
        for field in OUTLINE_TEXT_FIELDS:
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
    prompt_snapshot: dict[str, Any] | None = None,
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
                outline_field_repair_messages(payload, prompt_snapshot),
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
    prompt_snapshot: dict[str, Any] | None = None,
    *,
    on_attempt: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle, meta = await complete_with_one_repair(
        provider,
        character_messages(
            {"project": _project_context(project), "locked_brief": brief},
            prompt_snapshot,
        ),
        _validate_character_bundle,
        on_attempt=on_attempt,
        json_schema=CHARACTER_BUNDLE_JSON_SCHEMA,
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return bundle, meta


_OUTLINE_CHECKPOINT_FIELDS = (
    "core_event", "character_progress", "reversal", "cliffhanger",
)

def _truncate_outline_checkpoint_text(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 500 else text[:499] + "…"


def build_outline_history_context(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a bounded, read-only continuity summary for the next outline chunk."""
    ordered = sorted(
        (dict(item) for item in episodes if isinstance(item, dict)),
        key=lambda item: int(item.get("episode") or 0),
    )
    completed_through = max(
        (int(item.get("episode") or 0) for item in ordered), default=0
    )
    recent_episodes = ordered[-10:]
    prior_episodes = ordered[:-10]
    checkpoints = []
    for chunk_start in range(0, len(prior_episodes), 10):
        episode = prior_episodes[min(chunk_start + 9, len(prior_episodes) - 1)]
        checkpoints.append(
            {
                "episode": int(episode.get("episode") or 0),
                **{
                    field: _truncate_outline_checkpoint_text(episode.get(field))
                    for field in _OUTLINE_CHECKPOINT_FIELDS
                },
            }
        )
    return {
        "completed_through": completed_through,
        "recent_episodes": recent_episodes,
        "prior_chunk_checkpoints": checkpoints,
    }


async def _notify_outline_request_progress(
    callback: Callable[[dict[str, Any]], Any] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        result = callback(payload)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.warning("分集大纲请求进度回调失败，已忽略", exc_info=True)


async def _complete_outline_request(
    provider: Any,
    messages: list[dict[str, str]],
    validator: Callable[[Any], Any],
    *,
    request_kind: str,
    batch_start: int | None,
    batch_end: int | None,
    reasoning_effort: str,
    on_request_progress: Callable[[dict[str, Any]], Any] | None,
    max_attempts: int = 2,
) -> tuple[Any, dict[str, Any]]:
    batch_started_at = time.time()
    request_started_at = time.monotonic()
    shared = {
        "request_kind": request_kind,
        "batch_start": batch_start,
        "batch_end": batch_end,
        "batch_started_at": batch_started_at,
        "reasoning_effort": reasoning_effort,
        "prompt_chars": sum(len(str(message.get("content") or "")) for message in messages),
        "llm_provider": str(getattr(provider, "provider_display_name", "") or getattr(provider, "provider_name", "")),
        "llm_model": str(getattr(provider, "model_display_name", "") or getattr(provider, "model", "")),
        "wire_api": str(getattr(provider, "wire_api", "")),
        "max_output_tokens": getattr(provider, "max_output_tokens", None),
    }
    await _notify_outline_request_progress(
        on_request_progress,
        {**shared, "phase": "started", "request_elapsed_seconds": 0.0, "batch_in_flight": True},
    )
    try:
        result = await complete_with_one_repair(
            provider, messages, validator, max_attempts=max_attempts
        )
    except LLMOutputTruncatedError as exc:
        await _notify_outline_request_progress(
            on_request_progress,
            {
                **shared,
                "phase": "truncated",
                "request_elapsed_seconds": time.monotonic() - request_started_at,
                "batch_in_flight": False,
                "finish_reason": exc.finish_reason,
                "returned_chars": exc.returned_chars,
            },
        )
        raise
    except Exception:
        await _notify_outline_request_progress(
            on_request_progress,
            {
                **shared,
                "phase": "failed",
                "request_elapsed_seconds": time.monotonic() - request_started_at,
                "batch_in_flight": False,
            },
        )
        raise
    await _notify_outline_request_progress(
        on_request_progress,
        {
            **shared,
            "phase": "finished",
            "request_elapsed_seconds": time.monotonic() - request_started_at,
            "batch_in_flight": False,
        },
    )
    return result


def _looks_like_whole_episode_rewrite(value: str) -> bool:
    json_candidate = value
    fenced_json = re.fullmatch(
        r"\s*```(?:json)?\s*(.*?)\s*```\s*",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_json:
        json_candidate = fenced_json.group(1)
    try:
        embedded = json.loads(json_candidate)
    except (TypeError, ValueError):
        embedded = None
    if isinstance(embedded, dict):
        if sum(field in embedded for field in OUTLINE_TEXT_FIELDS) >= 2:
            return True

    labels = "|".join(
        re.escape(label)
        for label in (*OUTLINE_TEXT_FIELDS, *OUTLINE_FIELD_LABELS.values())
    )
    structured_labels = set(re.findall(
        rf"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+)?(?:\*\*)?"
        rf"({labels})(?:\*\*)?(?:\s*[:：]|\s*$)",
        value,
    ))
    table_labels = set(re.findall(
        rf"(?m)^\s*\|\s*(?:\*\*)?({labels})(?:\*\*)?\s*\|",
        value,
    ))
    return len(structured_labels | table_labels) >= 2


def _validate_outline_field_rewrite(payload: Any) -> str:
    if not isinstance(payload, dict) or set(payload) - {"value", "provider"}:
        raise ValueError("字段重写只能返回 value")
    value = payload.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("字段重写的 value 必须是非空字符串")
    normalized = value.strip()
    if _looks_like_whole_episode_rewrite(normalized):
        raise ValueError("字段重写不能包含整集大纲")
    return normalized


async def rewrite_outline_field(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    episode: dict[str, Any],
    previous_episode: dict[str, Any] | None,
    next_episode: dict[str, Any] | None,
    field: str,
    instruction: str = "",
    prompt_snapshot: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    if field not in OUTLINE_TEXT_FIELDS:
        raise ValueError("不支持的大纲字段")
    messages = outline_field_rewrite_messages(
        {
            "project": _project_context(project),
            "locked_brief": brief,
            "characters": characters,
            "relationships": relationships,
            "current_episode": episode,
            "previous_episode": previous_episode,
            "next_episode": next_episode,
            "target_field": field,
            "instruction": str(instruction or "").strip(),
        },
        prompt_snapshot,
    )
    payload = await provider.complete_json(messages)
    value = _validate_outline_field_rewrite(payload)
    meta = {
        "provider": str(payload.get("provider") or getattr(provider, "provider_name", "")),
        "prompt_template_version": OUTLINE_FIELD_REWRITE_PROMPT_VERSION,
    }
    if isinstance(prompt_snapshot, dict):
        meta.update(
            {
                "prompt_template_id": str(prompt_snapshot.get("template_id") or ""),
                "prompt_template_revision": int(prompt_snapshot.get("revision") or 0),
                "prompt_content_hash": str(prompt_snapshot.get("content_hash") or ""),
            }
        )
    return value, meta


async def generate_outline_bundle(
    provider: Any,
    project: dict[str, Any],
    brief: dict[str, Any],
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    on_progress: Callable[[int, int], Any] | None = None,
    on_request_progress: Callable[[dict[str, Any]], Any] | None = None,
    on_checkpoint: Callable[[dict[str, Any], list[dict[str, Any]], dict[str, Any]], Any] | None = None,
    checkpoint: Callable[[], Any] | None = None,
    resume_episodes: list[dict[str, Any]] | None = None,
    resume_series_arc: str | None = None,
    resume_stages: list[Any] | None = None,
    resume_series_overview: dict[str, Any] | None = None,
    reasoning_effort: str = "high",
    prompt_snapshot: dict[str, Any] | None = None,
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
        arc = {
            "series_arc": series_arc,
            "stages": list(resume_stages),
            "series_overview": (
                normalize_series_overview(resume_series_overview)
                if resume_series_overview is not None
                else None
            ),
        }
        meta = {"resumed_from_episode": len(episodes)}
    else:
        await _checkpoint(checkpoint)
        arc, meta = await _complete_outline_request(
            provider,
            series_arc_messages(base, prompt_snapshot),
            _validate_series_arc,
            request_kind="series_arc",
            batch_start=None,
            batch_end=None,
            reasoning_effort=reasoning_effort,
            on_request_progress=on_request_progress,
        )
    preferred_chunk_size = 10
    actual_batches: list[dict[str, Any]] = []
    split_count = 0
    meta.update(
        {
            "preferred_chunk_size": preferred_chunk_size,
            "actual_batches": actual_batches,
            "split_count": split_count,
        }
    )
    missing_fields: dict[int, list[str]] = {}

    async def generate_range(start: int, end: int) -> None:
        nonlocal split_count
        await _checkpoint(checkpoint)
        try:
            chunk_result, chunk_meta = await _complete_outline_request(
                provider,
                outline_messages(
                    {
                        **base,
                        "series_arc": arc,
                        "history_context": build_outline_history_context(episodes),
                    },
                    start,
                    end,
                    prompt_snapshot,
                ),
                lambda payload, chunk_start=start, chunk_end=end: _normalize_outline_chunk(
                    payload,
                    chunk_start,
                    chunk_end,
                    default_estimated_seconds=default_estimated_seconds,
                ),
                request_kind="outline_chunk",
                batch_start=start,
                batch_end=end,
                reasoning_effort=reasoning_effort,
                on_request_progress=on_request_progress,
                max_attempts=4,
            )
        except LLMOutputTruncatedError as exc:
            actual_batches.append(
                {"start": start, "end": end, "status": "truncated"}
            )
            if start == end:
                raise LLMOutputTruncatedError(
                    f"第 {start} 集单集生成仍达到模型输出上限；"
                    "请提高模型输出上限或减少单集字段内容",
                    finish_reason=exc.finish_reason,
                    returned_chars=exc.returned_chars,
                    partial_text=exc.partial_text,
                ) from exc
            midpoint = (start + end - 1) // 2
            split_count += 1
            meta["split_count"] = split_count
            await _notify_outline_request_progress(
                on_request_progress,
                {
                    "phase": "split",
                    "request_kind": "outline_chunk",
                    "batch_start": start,
                    "batch_end": end,
                    "left_start": start,
                    "left_end": midpoint,
                    "right_start": midpoint + 1,
                    "right_end": end,
                    "finish_reason": exc.finish_reason,
                    "returned_chars": exc.returned_chars,
                    "reasoning_effort": reasoning_effort,
                    "batch_in_flight": False,
                },
            )
            await generate_range(start, midpoint)
            await generate_range(midpoint + 1, end)
            return

        chunk = chunk_result["episodes"]
        episodes.extend(chunk)
        missing_fields.update(chunk_result["missing_fields"])
        meta.update({key: value for key, value in chunk_meta.items() if value})
        actual_batches.append(
            {"start": start, "end": end, "status": "completed"}
        )
        if on_checkpoint:
            result = on_checkpoint(arc, episodes, meta)
            if inspect.isawaitable(result):
                await result
        if on_progress:
            result = on_progress(len(episodes), total)
            if inspect.isawaitable(result):
                await result

    for start in range(len(episodes) + 1, total + 1, preferred_chunk_size):
        await generate_range(start, min(total, start + preferred_chunk_size - 1))
    fallback_fields = await _repair_outline_fields(
        provider, base, arc, episodes, missing_fields, prompt_snapshot
    )
    if missing_fields:
        meta["field_repair_count"] = sum(len(fields) for fields in missing_fields.values())
        meta["backend_fallback_fields"] = fallback_fields
        if on_checkpoint:
            result = on_checkpoint(arc, episodes, meta)
            if inspect.isawaitable(result):
                await result
    if arc.get("series_overview") is None:
        await _checkpoint(checkpoint)
        overview, overview_meta = await generate_series_overview(
            provider,
            project,
            episodes,
            arc["series_arc"],
            prompt_snapshot=prompt_snapshot,
        )
        arc["series_overview"] = overview
        meta.update({key: value for key, value in overview_meta.items() if value})
        if on_checkpoint:
            result = on_checkpoint(arc, episodes, meta)
            if inspect.isawaitable(result):
                await result
    meta.update(
        {
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "preferred_chunk_size": preferred_chunk_size,
            "actual_batches": actual_batches,
            "split_count": split_count,
        }
    )
    return {
        "series_arc": arc["series_arc"],
        "stages": arc["stages"],
        "series_overview": arc["series_overview"],
        "episodes": episodes,
    }, meta


async def generate_series_overview(
    provider: Any,
    project: dict[str, Any],
    episodes: list[dict[str, Any]],
    series_arc: str,
    prompt_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Summarize an existing draft outline without changing episode content."""

    ordered_episodes = sorted(
        (dict(item) for item in episodes),
        key=lambda item: int(item.get("episode") or item.get("episode_number") or 0),
    )
    overview, meta = await complete_with_one_repair(
        provider,
        series_overview_messages(
            {
                "project": _project_context(project),
                "series_arc": str(series_arc or "").strip(),
                "episodes": ordered_episodes,
            },
            prompt_snapshot,
        ),
        lambda payload: normalize_series_overview(payload.get("series_overview")),
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return overview, meta


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
    prompt_snapshot: dict[str, Any] | None = None,
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
        beats_messages(payload, int(episode_number), prompt_snapshot),
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
    numbers: list[int] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            numbers.append(int(item.get("episode") or 0))
        except (TypeError, ValueError):
            numbers.append(0)
    if len(rows) != len(episode_numbers) or sorted(numbers) != episode_numbers:
        expected = set(episode_numbers)
        actual = [number for number in numbers if number > 0]
        missing = [number for number in episode_numbers if number not in actual]
        duplicates = sorted(
            number for number in set(actual) if actual.count(number) > 1
        )
        unexpected = sorted(number for number in set(actual) if number not in expected)
        details = [
            f"实际集数：{'、'.join(map(str, actual)) or '无'}",
            f"缺失集数：{'、'.join(map(str, missing)) or '无'}",
        ]
        if duplicates:
            details.append(f"重复集数：{'、'.join(map(str, duplicates))}")
        if unexpected:
            details.append(f"非预期集数：{'、'.join(map(str, unexpected))}")
        raise ValueError(
            f"{label}批量结果必须完整覆盖第 {episode_numbers[0]}-{episode_numbers[-1]} 集；"
            + "；".join(details)
        )
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
    prompt_snapshot: dict[str, Any] | None = None,
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
        beats_chunk_messages(
            payload, episode_numbers[0], episode_numbers[-1], prompt_snapshot
        ),
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
    prompt_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "project": _project_context(project), "locked_brief": brief or {}, "locked_outline": outline,
        "characters": characters, "locked_beats": beats,
        "relationships": relationships or [],
        "episode_number": int(episode_number), "continuity_ledger": continuity_ledger or [],
    }
    script, meta = await complete_with_one_repair(
        provider,
        script_messages(payload, int(episode_number), prompt_snapshot),
        validate_script,
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
    prompt_snapshot: dict[str, Any] | None = None,
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
    accepted: dict[int, dict[str, Any]] = {}
    last_error: Exception | None = None
    provider_name = str(getattr(provider, "provider_name", "") or "")

    def collect(result: Any, requested: list[int]) -> None:
        nonlocal last_error, provider_name
        if isinstance(result, dict):
            provider_name = str(result.get("provider") or provider_name)
        try:
            rows = result if isinstance(result, list) else result.get("episodes")
            if not isinstance(rows, list):
                raise ValueError("场次剧本批量结果必须包含 episodes 数组")
            counts: dict[int, int] = {}
            for item in rows:
                if not isinstance(item, dict):
                    continue
                try:
                    number = int(item.get("episode") or 0)
                except (TypeError, ValueError):
                    continue
                counts[number] = counts.get(number, 0) + 1
            for item in rows:
                if not isinstance(item, dict):
                    continue
                try:
                    number = int(item.get("episode") or 0)
                except (TypeError, ValueError):
                    continue
                if number not in requested or counts.get(number) != 1:
                    continue
                content = {key: value for key, value in item.items() if key != "episode"}
                try:
                    accepted[number] = validate_script(content)
                except (TypeError, ValueError) as exc:
                    last_error = exc
            missing = [number for number in requested if number not in accepted]
            if missing:
                _normalize_episode_generation_chunk(
                    result, requested, validate_script, "场次剧本"
                )
        except Exception as exc:
            last_error = exc

    messages = script_chunk_messages(
        payload, episode_numbers[0], episode_numbers[-1], prompt_snapshot
    )
    try:
        collect(await provider.complete_json(messages), episode_numbers)
    except Exception as exc:
        if is_retryable_llm_exception(exc):
            raise
        last_error = exc

    missing = [number for number in episode_numbers if number not in accepted]
    repair_attempted = bool(missing)
    if missing:
        repair_payload = {
            **payload,
            "locked_outlines": [
                item
                for item in payload["locked_outlines"]
                if int(item["episode_number"]) in missing
            ],
            "locked_beats": [
                item
                for item in payload["locked_beats"]
                if int(item["episode_number"]) in missing
            ],
        }
        repair_request = script_chunk_messages(
            repair_payload,
            missing[0],
            missing[-1],
            prompt_snapshot,
            requested_episode_numbers=missing,
        )
        repair_request.append(
            {
                "role": "user",
                "content": (
                    "上一批响应不完整。只生成本次输入中列出的缺失集，"
                    "不要重复已经成功的集数。"
                ),
            }
        )
        try:
            collect(await provider.complete_json(repair_request), missing)
        except Exception as exc:
            if is_retryable_llm_exception(exc):
                raise
            last_error = exc

    missing = [number for number in episode_numbers if number not in accepted]
    if missing:
        detail = str(last_error or "模型未返回有效场次剧本")
        raise LLMError(
            "LLM 返回内容无法通过结构校验："
            f"{detail}；最终缺失集数：{'、'.join(map(str, missing))}"
        ) from last_error

    generated = sorted(accepted.items())
    meta = {
        "provider": provider_name,
        "repair_attempted": repair_attempted,
    }
    meta.update({"prompt_template_version": PROMPT_TEMPLATE_VERSION, "chunk_size": len(episode_numbers)})
    return generated, meta
