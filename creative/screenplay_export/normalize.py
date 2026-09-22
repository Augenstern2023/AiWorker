# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Normalize project resources into the screenplay export AST."""

from __future__ import annotations

from typing import Any

from creative.outline_overview import SERIES_OVERVIEW_FIELD_LABELS, read_series_overview
from creative.screenplay_export.model import (
    CharacterExport,
    EpisodeExport,
    ExportMetadata,
    SceneExport,
    ScriptBlockExport,
    ScriptExportDocument,
)
from creative.script_service import normalize_script_content


_SUPPORTED_BLOCK_TYPES = {"action", "dialogue", "inner_voice", "voiceover", "cut"}
_CHARACTER_FIELD_LABELS = (
    ("role", "角色"),
    ("identity", "身份"),
    ("age", "年龄"),
    ("personality", "性格"),
    ("background", "背景"),
    ("backstory", "经历"),
    ("goal", "目标"),
    ("desire", "欲望"),
    ("motivation", "动机"),
    ("character_arc", "人物弧光"),
    ("arc", "人物弧光"),
    ("description", "小传"),
    ("bio", "小传"),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _text(item)))


def _synopsis(project: dict[str, Any]) -> str:
    direct = _text(
        project.get("topic_description")
        or project.get("description")
        or project.get("synopsis")
    )
    if direct:
        return direct
    locked_id = project.get("locked_brief_id")
    if not locked_id:
        brief_head = next(
            (
                item
                for item in project.get("resource_heads") or []
                if item.get("stage") == "brief"
                and _number(item.get("episode_number")) == 0
            ),
            None,
        )
        locked_id = (brief_head or {}).get("resource_ref")
    blueprints = project.get("blueprints") or []
    locked = next(
        (item for item in blueprints if str(item.get("id")) == str(locked_id)),
        None,
    )
    content = (locked or {}).get("content") or {}
    return _text(content.get("logline") or content.get("synopsis") or content.get("summary"))


def _character_description(content: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key, label in _CHARACTER_FIELD_LABELS:
        value = content.get(key)
        if isinstance(value, (list, tuple)):
            rendered = "、".join(_text(item) for item in value if _text(item))
        elif isinstance(value, dict):
            rendered = "；".join(
                f"{_text(item_key)}：{_text(item_value)}"
                for item_key, item_value in value.items()
                if _text(item_value)
            )
        else:
            rendered = _text(value)
        if rendered and rendered not in seen:
            parts.append(f"{label}：{rendered}")
            seen.add(rendered)
    return "；".join(parts)


def _character_bios(project: dict[str, Any]) -> tuple[CharacterExport, ...]:
    batch_id = str(project.get("character_batch_id") or "")
    candidates = [
        item
        for item in project.get("characters") or []
        if not batch_id or str(item.get("batch_id") or "") == batch_id
    ]
    if not batch_id and candidates:
        latest_version = max(_number(item.get("version")) for item in candidates)
        candidates = [item for item in candidates if _number(item.get("version")) == latest_version]
    result = []
    for item in sorted(candidates, key=lambda value: (_number(value.get("version")), _number(value.get("id")))):
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        name = _text(content.get("name"))
        if not name:
            continue
        result.append(CharacterExport(name=name, description=_character_description(content)))
    return tuple(result)


def _series_overview(items: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    for item in items:
        content = item.get("content") or {}
        overview = read_series_overview(content.get("series_overview"))
        if overview:
            return tuple(
                (label, _text(overview.get(key)))
                for key, label in SERIES_OVERVIEW_FIELD_LABELS
                if _text(overview.get(key))
            )
    return ()


def build_export_document(
    project: dict[str, Any],
    *,
    stage: str,
    episode_numbers: list[int] | None,
    video_parameter_record: dict[str, Any] | None = None,
) -> ScriptExportDocument:
    """Build one immutable export AST from current downloadable resources."""

    if stage != "script":
        raise ValueError("模板化剧本导出仅支持 script 阶段")

    # Imported lazily to reuse the workspace's single source of truth without
    # making creative.api and this domain package import each other at startup.
    from creative.api import (  # pylint: disable=import-outside-toplevel
        build_video_parameter_recommendation_text,
        current_episode_download_items,
    )

    available_items = current_episode_download_items(project, stage)
    available = {
        _number(item.get("episode_number") or (item.get("content") or {}).get("episode")): item
        for item in available_items
        if _number(item.get("episode_number") or (item.get("content") or {}).get("episode")) > 0
    }
    requested = sorted(available) if episode_numbers is None else sorted(set(episode_numbers))
    missing = [number for number in requested if number not in available]
    if missing:
        raise ValueError(f"以下集数没有当前可下载内容：{'、'.join(map(str, missing))}")
    if not requested:
        raise ValueError("尚无可下载的场次剧本")

    episodes: list[EpisodeExport] = []
    selected_items = [available[number] for number in requested]
    for number, item in zip(requested, selected_items, strict=True):
        content = normalize_script_content(item.get("content") or {})
        scenes: list[SceneExport] = []
        raw_scenes = content.get("scenes") if isinstance(content.get("scenes"), list) else []
        for raw_scene in raw_scenes:
            if not isinstance(raw_scene, dict):
                continue
            blocks: list[ScriptBlockExport] = []
            raw_blocks = raw_scene.get("blocks") if isinstance(raw_scene.get("blocks"), list) else []
            for raw_block in raw_blocks:
                if not isinstance(raw_block, dict):
                    continue
                block_type = _text(raw_block.get("type")).lower()
                if block_type not in _SUPPORTED_BLOCK_TYPES:
                    continue
                blocks.append(
                    ScriptBlockExport(
                        type=block_type,
                        text=_text(raw_block.get("text")),
                        speaker=_text(raw_block.get("speaker")),
                        modifier=_text(raw_block.get("modifier")),
                    )
                )
            scenes.append(
                SceneExport(
                    sequence_no=_number(raw_scene.get("sequence_no")),
                    scene_no=_number(raw_scene.get("scene_no")),
                    location=_text(raw_scene.get("location")),
                    time_of_day=_text(raw_scene.get("time_of_day")),
                    interior_exterior=_text(raw_scene.get("interior_exterior")),
                    characters=_string_items(raw_scene.get("characters")),
                    props=_string_items(raw_scene.get("props")),
                    blocks=tuple(blocks),
                )
            )
        episodes.append(
            EpisodeExport(
                episode_number=number,
                title=_text(content.get("episode_title")) or f"第 {number} 集",
                version=max(1, _number(item.get("version"))),
                scenes=tuple(scenes),
            )
        )

    appendix = build_video_parameter_recommendation_text(video_parameter_record)
    metadata = ExportMetadata(
        project_id=project.get("id") or "",
        stage=stage,
        episode_numbers=tuple(requested),
        versions=tuple(episode.version for episode in episodes),
        video_parameter_appendix=appendix,
    )
    drama_name = _text(project.get("topic_title") or project.get("name")) or "剧本"
    return ScriptExportDocument(
        drama_name=drama_name,
        synopsis=_synopsis(project),
        character_bios=_character_bios(project),
        series_overview=_series_overview(
            selected_items + current_episode_download_items(project, "outline")
        ),
        episodes=tuple(episodes),
        metadata=metadata,
    )
