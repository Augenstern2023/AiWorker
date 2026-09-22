# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Convert frozen screenplay sources into a stable, evidence-addressable package."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schema import (
    EpisodePackage,
    EvidenceRef,
    InsufficientEvidence,
    ProjectContext,
    ScenePackage,
    ScriptPackage,
)


MAX_EVIDENCE_CHARS = 240


def normalize_script_package(source: dict[str, Any]) -> ScriptPackage:
    """Normalize one already-frozen source without mutating or reloading it."""

    fingerprint = str(source.get("fingerprint") or _fingerprint(source))
    project_id = str(source.get("project_id") or f"text:{fingerprint[:16]}")
    project = ProjectContext(
        project_id=project_id,
        title=str(source.get("project_name") or source.get("source_filename") or "上传文本"),
    )
    episodes: list[EpisodePackage] = []
    insufficient: list[InsufficientEvidence] = []
    for source_episode in source.get("episodes") or []:
        if not isinstance(source_episode, dict):
            continue
        episode = _normalize_episode(source_episode, project_id=project_id)
        episodes.append(episode)
        if not episode.scenes or not any(scene.text.strip() for scene in episode.scenes):
            insufficient.append(
                InsufficientEvidence(
                    scope="episode",
                    scope_ref=episode.episode_id,
                    reason="episode_has_no_scene_or_text",
                )
            )
        insufficient.extend(
            InsufficientEvidence(
                scope="scene",
                scope_ref=scene.scene_id,
                reason="scene_has_no_text",
            )
            for scene in episode.scenes
            if not scene.text.strip()
        )
    if not episodes:
        raise ValueError("审核来源没有可标准化的剧本集")
    return ScriptPackage(
        source_fingerprint=fingerprint,
        project=project,
        brief=_mapping(source.get("brief")),
        characters=_mappings(source.get("characters")),
        relationships=_mappings(source.get("relationships")),
        episodes=episodes,
        insufficient_evidence=insufficient,
    )


def _normalize_episode(source: dict[str, Any], *, project_id: str) -> EpisodePackage:
    number = int(source.get("episode_number") or 0)
    if number < 1:
        raise ValueError("剧本集号必须是正整数")
    episode_id = f"ep-{number:04d}"
    script = source.get("script") if isinstance(source.get("script"), dict) else {}
    resource_id = str(script.get("id") or source.get("resource_id") or f"{project_id}:{episode_id}")
    version = int(script.get("version") or source.get("version") or 1)
    content = script.get("content") if isinstance(script.get("content"), dict) else {}
    content_fingerprint = str(
        script.get("content_fingerprint")
        or source.get("content_fingerprint")
        or _fingerprint(content or {"text": source.get("text") or ""})
    )
    scenes = _structured_scenes(
        content.get("scenes"),
        episode_id=episode_id,
        resource_id=resource_id,
        version=version,
        content_fingerprint=content_fingerprint,
        source_url=(
            f"/writing/projects/{project_id}/episodes/{number}/scripts"
            if not project_id.startswith("text:")
            else ""
        ),
    )
    if not scenes and str(source.get("text") or "").strip():
        scenes = [
            _text_scene(
                str(source["text"]).strip(),
                episode_id=episode_id,
                resource_id=resource_id,
                version=version,
                content_fingerprint=content_fingerprint,
            )
        ]
    return EpisodePackage(
        episode_id=episode_id,
        episode_number=number,
        resource_id=resource_id,
        version=version,
        content_fingerprint=content_fingerprint,
        title=str(content.get("episode_title") or ""),
        outline=_mapping(source.get("outline")),
        beats=_mapping(source.get("beats")),
        scenes=scenes,
    )


def _structured_scenes(
    raw_scenes: Any,
    *,
    episode_id: str,
    resource_id: str,
    version: int,
    content_fingerprint: str,
    source_url: str,
) -> list[ScenePackage]:
    scenes: list[ScenePackage] = []
    explicit_ids: set[str] = set()
    for index, raw in enumerate(raw_scenes or [], start=1):
        if not isinstance(raw, dict):
            continue
        sequence = int(raw.get("sequence_no") or index)
        scene_number = str(raw.get("scene_no") or sequence)
        explicit_id = str(raw.get("scene_id") or "").strip()
        if explicit_id:
            if explicit_id in explicit_ids:
                raise ValueError(f"重复 scene_id: {explicit_id}")
            explicit_ids.add(explicit_id)
        scene_id = explicit_id or f"{resource_id}:{version}:{sequence}"
        text, evidence = _scene_text_and_evidence(
            raw.get("blocks"),
            episode_id=episode_id,
            scene_id=scene_id,
            resource_id=resource_id,
            version=version,
            source_url=source_url,
        )
        scenes.append(
            ScenePackage(
                scene_id=scene_id,
                episode_id=episode_id,
                scene_number=scene_number,
                sequence_number=sequence,
                resource_id=resource_id,
                version=version,
                content_fingerprint=content_fingerprint,
                text=text,
                evidence=evidence,
            )
        )
    return scenes


def _scene_text_and_evidence(
    raw_blocks: Any,
    *,
    episode_id: str,
    scene_id: str,
    resource_id: str,
    version: int,
    source_url: str,
) -> tuple[str, list[EvidenceRef]]:
    lines: list[str] = []
    evidence_specs: list[tuple[int, str]] = []
    for index, block in enumerate(raw_blocks or []):
        if not isinstance(block, dict):
            continue
        body = str(block.get("text") or "").strip()
        if not body:
            continue
        speaker = str(block.get("speaker") or "").strip()
        line = f"{speaker}：{body}" if speaker else body
        evidence_specs.append((index, line[:MAX_EVIDENCE_CHARS]))
        lines.append(line)
    text = "\n".join(lines)
    evidence: list[EvidenceRef] = []
    cursor = 0
    for ordinal, (block_index, quote) in enumerate(evidence_specs):
        start = cursor
        end = start + len(quote)
        evidence.append(
            EvidenceRef(
                source_ref=f"{scene_id}:{block_index}",
                episode_id=episode_id,
                scene_id=scene_id,
                resource_id=resource_id,
                version=version,
                source_url=source_url,
                block_index=block_index,
                start=start,
                end=end,
                text=quote,
                text_hash=_text_hash(quote),
            )
        )
        cursor += len(lines[ordinal]) + 1
    return text, evidence


def _text_scene(
    text: str,
    *,
    episode_id: str,
    resource_id: str,
    version: int,
    content_fingerprint: str,
) -> ScenePackage:
    scene_id = f"{resource_id}:{version}:1"
    quote = text[:MAX_EVIDENCE_CHARS]
    return ScenePackage(
        scene_id=scene_id,
        episode_id=episode_id,
        scene_number="1",
        sequence_number=1,
        resource_id=resource_id,
        version=version,
        content_fingerprint=content_fingerprint,
        text=text,
        evidence=[
            EvidenceRef(
                source_ref=f"{scene_id}:0",
                episode_id=episode_id,
                scene_id=scene_id,
                resource_id=resource_id,
                version=version,
                block_index=0,
                start=0,
                end=len(quote),
                text=quote,
                text_hash=_text_hash(quote),
            )
        ],
    )


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]
