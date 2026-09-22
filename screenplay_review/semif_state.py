# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Build deterministic, traceable SemIf states without dropping script text."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .strategy import estimate_tokens


STATE_PACKAGE_VERSION = "screenplay-semif-state-v2"
DEFAULT_STATE_TARGET_TOKENS = 3_000


@dataclass(frozen=True)
class SemIfStateWindow:
    id: str
    episode_number: int
    state: dict[str, Any]
    state_refs: tuple[dict[str, Any], ...]
    estimated_tokens: int

    def to_manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "episode_number": self.episode_number,
            "state_refs": [dict(item) for item in self.state_refs],
            "estimated_tokens": self.estimated_tokens,
            "state_sha256": _digest(self.state),
        }


def build_semif_state_windows(
    source: dict[str, Any],
    *,
    target_tokens: int = DEFAULT_STATE_TARGET_TOKENS,
) -> list[SemIfStateWindow]:
    """Split scripts into complete state windows under a conservative budget."""
    budget = int(target_tokens)
    if budget < 256:
        raise ValueError("本地初审状态包预算不能低于 256")
    episodes = [item for item in source.get("episodes") or [] if isinstance(item, dict)]
    if not episodes:
        raise ValueError("审核来源没有可供本地初审的剧本")

    result: list[SemIfStateWindow] = []
    for episode in episodes:
        number = int(episode.get("episode_number") or 0)
        if number < 1:
            raise ValueError("本地初审的剧本分集缺少有效集号")
        context = _episode_context(source, episode)
        empty_state = _state(context, [])
        if estimate_tokens(empty_state) >= budget:
            context = _compact_episode_context(context, budget)
            empty_state = _state(context, [])
            if estimate_tokens(empty_state) >= budget:
                raise ValueError(f"第 {number} 集基础信息超过本地初审输入预算")
        segments = _episode_segments(episode)
        expanded = [
            piece
            for segment in segments
            for piece in _split_segment(context, segment, budget)
        ]
        batches: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
        current: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for segment, reference in expanded:
            candidate = [*current, (segment, reference)]
            if current and estimate_tokens(_state(context, candidate)) > budget:
                batches.append(current)
                current = [(segment, reference)]
            else:
                current = candidate
        if current:
            batches.append(current)

        for index, batch in enumerate(batches, start=1):
            state = _state(context, batch)
            estimated = estimate_tokens(state)
            if estimated > budget:
                raise ValueError(
                    f"第 {number} 集状态包 {index} 超过本地初审输入预算，无法提交"
                )
            state_hash = _digest(state)
            result.append(
                SemIfStateWindow(
                    id=f"episode-{number}-state-{index}-{state_hash[:12]}",
                    episode_number=number,
                    state=state,
                    state_refs=tuple(reference for _, reference in batch),
                    estimated_tokens=estimated,
                )
            )
    return result


def _episode_context(source: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    prompt = source.get("prompt_snapshot") or {}
    return {
        "package_version": STATE_PACKAGE_VERSION,
        "project": {
            "id": source.get("project_id"),
            "name": str(source.get("project_name") or ""),
            "formal": bool(source.get("formal")),
        },
        "review_focus": str(prompt.get("effective_instruction") or "").strip(),
        "brief": _content(source.get("brief")),
        "characters": [_content(item) for item in source.get("characters") or []],
        "relationships": [
            _content(item) for item in source.get("relationships") or []
        ],
        "episode": {
            "number": int(episode.get("episode_number") or 0),
            "outline": _content(episode.get("outline")),
            "beats": _content(episode.get("beats")),
            "script_resource": _resource_identity(episode.get("script") or episode),
        },
    }


def _episode_segments(
    episode: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    script = episode.get("script") if isinstance(episode.get("script"), dict) else {}
    content = script.get("content") if isinstance(script.get("content"), dict) else {}
    resource = _resource_identity(script or episode)
    segments: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for scene_index, scene in enumerate(content.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        scene_no = scene.get("scene_no") or scene_index + 1
        for block_index, block in enumerate(scene.get("blocks") or []):
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "")
            if not text:
                continue
            segment = {
                "scene_no": scene_no,
                "block_index": block_index,
                "type": str(block.get("type") or ""),
                "speaker": str(block.get("speaker") or ""),
                "text": text,
            }
            reference = {
                **resource,
                "episode_number": int(episode.get("episode_number") or 0),
                "scene_index": scene_index,
                "scene_no": scene_no,
                "block_index": block_index,
                "path": f"script.content.scenes[{scene_index}].blocks[{block_index}].text",
                "start": 0,
                "end": len(text),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            segments.append((segment, reference))
    if segments:
        return segments

    text = str(episode.get("text") or "")
    if text:
        return [
            (
                {"type": "raw_text", "text": text},
                {
                    **resource,
                    "episode_number": int(episode.get("episode_number") or 0),
                    "path": "episodes[].text",
                    "start": 0,
                    "end": len(text),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                },
            )
        ]
    return [
        (
            {"type": "empty_script", "text": "（剧本正文为空）"},
            {
                **resource,
                "episode_number": int(episode.get("episode_number") or 0),
                "path": "script.content.scenes",
                "start": 0,
                "end": 0,
                "empty": True,
            },
        )
    ]


def _compact_episode_context(
    context: dict[str, Any], budget: int
) -> dict[str, Any]:
    """Keep high-signal background fields while reserving space for script text."""
    episode = context.get("episode") or {}
    compact = {
        "package_version": STATE_PACKAGE_VERSION,
        "project": context.get("project") or {},
        "episode": {
            "number": episode.get("number"),
            "script_resource": episode.get("script_resource") or {},
        },
        "context_compacted": True,
        "context_segments": [],
    }
    target = max(1, int(budget * 0.4))
    candidates = _context_summary_candidates(context)
    selected: list[dict[str, Any]] = compact["context_segments"]
    for candidate in candidates:
        trial = [*selected, candidate]
        if estimate_tokens(_state({**compact, "context_segments": trial}, [])) <= target:
            selected.append(candidate)
    return compact


def _context_summary_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def append(path: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        text = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        candidates.append({"path": path, "text": text})

    def append_fields(path: str, value: Any, fields: tuple[str, ...]) -> None:
        if not isinstance(value, dict):
            return
        for field in fields:
            append(f"{path}.{field}", value.get(field))

    append("review_focus", context.get("review_focus"))
    episode = context.get("episode") or {}
    append_fields(
        "episode.outline",
        episode.get("outline"),
        (
            "core_event",
            "conflict",
            "reversal",
            "cliffhanger",
            "highlight",
            "character_progress",
        ),
    )
    beats = episode.get("beats") or {}
    for index, beat in enumerate(beats.get("beats") or []):
        append_fields(
            f"episode.beats[{index}]",
            beat,
            (
                "scene",
                "goal",
                "obstacle",
                "turn",
                "hook",
                "state_change",
                "characters",
            ),
        )
    for index, character in enumerate(context.get("characters") or []):
        append_fields(
            f"characters[{index}]",
            character,
            ("name", "identity", "desire", "action_goal", "dialogue_style"),
        )
    for index, relationship in enumerate(context.get("relationships") or []):
        append_fields(
            f"relationships[{index}]",
            relationship,
            ("from", "to", "relation", "conflict", "change_arc"),
        )
    append_fields(
        "brief",
        context.get("brief"),
        (
            "theme",
            "logline",
            "main_goal",
            "core_conflict",
            "protagonist",
            "protagonist_desire",
            "main_opposition",
            "story_engine",
        ),
    )
    return candidates


def _split_segment(
    context: dict[str, Any],
    item: tuple[dict[str, Any], dict[str, Any]],
    budget: int,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    segment, reference = item
    if estimate_tokens(_state(context, [item])) <= budget:
        return [item]
    text = str(segment.get("text") or "")
    if not text:
        raise ValueError("本地初审的非文本状态片段超过输入预算")

    pieces: list[tuple[dict[str, Any], dict[str, Any]]] = []
    offset = 0
    while offset < len(text):
        low, high, accepted = 1, len(text) - offset, 0
        while low <= high:
            middle = (low + high) // 2
            candidate_segment = {
                **segment,
                "text": text[offset : offset + middle],
                "chunk_start": offset,
                "chunk_end": offset + middle,
            }
            if estimate_tokens(_state(context, [(candidate_segment, reference)])) <= budget:
                accepted = middle
                low = middle + 1
            else:
                high = middle - 1
        if accepted < 1:
            raise ValueError("本地初审的基础信息过大，无法切分正文")
        end = offset + accepted
        piece_text = text[offset:end]
        piece_segment = {
            **segment,
            "text": piece_text,
            "chunk_start": offset,
            "chunk_end": end,
        }
        piece_reference = {
            **reference,
            "start": offset,
            "end": end,
            "text_sha256": hashlib.sha256(piece_text.encode("utf-8")).hexdigest(),
        }
        pieces.append((piece_segment, piece_reference))
        offset = end
    return pieces


def _state(
    context: dict[str, Any],
    segments: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    return {
        **context,
        "script_segments": [segment for segment, _ in segments],
    }


def _content(value: Any) -> Any:
    if not isinstance(value, dict):
        return value if value is not None else {}
    content = value.get("content")
    return content if isinstance(content, (dict, list, str)) else value


def _resource_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in ("id", "resource_id", "version", "content_fingerprint")
        if value.get(key) not in (None, "")
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
