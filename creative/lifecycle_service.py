# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from creative.repository import CreativeRepository
from creative.transcript_assets import index_transcript_bundles, normalize_title


LIFECYCLE_LABELS = {
    "pending_review": "待评估",
    "adopted": "已采纳",
    "pending_download": "待下载",
    "downloaded": "已下载",
    "pending_breakdown": "待拆解",
    "breakdown_completed": "已拆解",
    "pending_imitation": "待仿制",
    "imitation_generated": "已仿制",
    "pending_character_relationships": "人物关系待生成",
    "character_relationships_generated": "人物关系已生成",
    "pending_episode_outlines": "分集大纲待生成",
    "episode_outlines_generated": "分集大纲已生成",
    "pending_beats": "Beats脚本待生成",
    "beats_generated": "Beats脚本已生成",
    "beats_exported": "Beats脚本已导出",
    "rejected": "不跟进",
    "archived": "已归档",
}

PROJECT_STATUS_RANK = {
    "pending_imitation": 1,
    "imitation_generated": 2,
    "pending_character_relationships": 3,
    "character_relationships_generated": 4,
    "pending_episode_outlines": 5,
    "episode_outlines_generated": 6,
    "pending_beats": 7,
    "beats_generated": 8,
    "beats_exported": 9,
}


def current_beats_content(project: dict[str, Any]) -> list[dict[str, Any]]:
    outline_batch_id = project.get("outline_batch_id")
    if not outline_batch_id:
        return []
    candidates = [
        item for item in project.get("episodes") or []
        if item.get("stage") == "beats"
        and (item.get("upstream_refs") or {}).get("outline_batch_id") == outline_batch_id
    ]
    current: dict[int, dict[str, Any]] = {}
    for item in candidates:
        episode_number = int(item.get("episode_number") or 0)
        previous = current.get(episode_number)
        if previous is None or (
            int(item.get("version") or 0), int(item.get("id") or 0)
        ) > (
            int(previous.get("version") or 0), int(previous.get("id") or 0)
        ):
            current[episode_number] = item
    return [current[key] for key in sorted(current)]


def content_fingerprint(content: Any) -> str:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def beats_resource_fingerprint(item: dict[str, Any]) -> str:
    keys = (
        "id",
        "project_id",
        "stage",
        "episode_number",
        "batch_id",
        "version",
        "parent_version_id",
        "content",
        "upstream_refs",
        "generation_meta",
    )
    return content_fingerprint({key: item.get(key) for key in keys})


class TopicLifecycleService:
    def __init__(
        self,
        repository: CreativeRepository,
        workspace_root: str | Path,
        job_manager: Any | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_root = Path(workspace_root)
        self.job_manager = job_manager

    def enrich_topics(self, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transcript_index = index_transcript_bundles(self.workspace_root)
        jobs = self._jobs_by_title()
        projects = self._projects_by_title()
        enriched = []
        for topic in topics:
            title_key = normalize_title(topic.get("title"))
            episode_count = len((transcript_index.get(title_key) or {}).get("episodes") or [])
            status, project_id = self._resolve_topic(
                topic,
                episode_count,
                jobs.get(title_key) or set(),
                projects.get(title_key) or [],
            )
            item = dict(topic)
            item["lifecycle_status"] = status
            item["lifecycle_label"] = (
                f"已下载 {episode_count} 集" if status == "downloaded"
                else LIFECYCLE_LABELS[status]
            )
            item["downloaded_episode_count"] = episode_count
            item["lifecycle_project_id"] = project_id
            enriched.append(item)
        return enriched

    def enrich_topic(self, topic_id: int) -> dict[str, Any] | None:
        topic = self.repository.get_topic(topic_id)
        if not topic:
            return None
        title_key = normalize_title(topic.get("title"))
        candidates = self.repository.list_topics(limit=500)
        merged = next(
            (item for item in candidates if normalize_title(item.get("title")) == title_key),
            topic,
        )
        lifecycle = self.enrich_topics([merged])[0]
        result = dict(topic)
        for key in (
            "lifecycle_status",
            "lifecycle_label",
            "downloaded_episode_count",
            "lifecycle_project_id",
        ):
            result[key] = lifecycle[key]
        return result

    def _jobs_by_title(self) -> dict[str, set[str]]:
        if self.job_manager is None or not hasattr(self.job_manager, "list_jobs"):
            return {}
        result: dict[str, set[str]] = {}
        for state in self.job_manager.list_jobs() or []:
            job = state.get("job") or state
            extra = job.get("extra") or {}
            crawler_type = str(state.get("crawler_type") or job.get("crawler_type") or "")
            title_key = normalize_title(extra.get("title"))
            if crawler_type in {"drama_download", "douyin_fallback_analysis"} and title_key:
                result.setdefault(title_key, set()).add("download")
            if crawler_type == "breakdown_generate":
                try:
                    topic = self.repository.get_topic(int(extra.get("topic_id") or 0))
                except (TypeError, ValueError):
                    topic = None
                breakdown_key = normalize_title((topic or {}).get("title"))
                if breakdown_key:
                    result.setdefault(breakdown_key, set()).add("breakdown")
        return result

    def _projects_by_title(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in self.repository.list_projects():
            if item.get("status") == "archived":
                continue
            project = self.repository.get_project(int(item["id"]))
            if not project:
                continue
            title_key = normalize_title(project.get("topic_title"))
            grouped.setdefault(title_key, []).append(project)
        return grouped

    def _resolve_topic(
        self,
        topic: dict[str, Any],
        episode_count: int,
        jobs: set[str],
        projects: list[dict[str, Any]],
    ) -> tuple[str, int | None]:
        decision = str(topic.get("status") or "pending_review")
        if decision in {"rejected", "archived"}:
            return decision, None
        if decision != "adopted":
            return "pending_review", None
        if projects:
            ranked = sorted(
                ((self._project_status(project), project) for project in projects),
                key=lambda pair: (PROJECT_STATUS_RANK[pair[0]], int(pair[1]["id"])),
                reverse=True,
            )
            return ranked[0][0], int(ranked[0][1]["id"])
        if topic.get("latest_breakdown_id"):
            return (
                "breakdown_completed" if topic.get("latest_breakdown_status") == "locked"
                else "pending_breakdown"
            ), None
        if "breakdown" in jobs:
            return "pending_breakdown", None
        if episode_count:
            return "downloaded", None
        if "download" in jobs:
            return "pending_download", None
        return "adopted", None

    def _project_status(self, project: dict[str, Any]) -> str:
        beats = current_beats_content(project)
        if beats:
            if all(
                item.get("status") == "approved"
                and self.repository.has_stage_export(
                    int(project["id"]),
                    "beats",
                    beats_resource_fingerprint(item),
                )
                for item in beats
            ):
                return "beats_exported"
            return "beats_generated"
        if project.get("outline_batch_id"):
            return "pending_beats"
        current_characters = project.get("character_batch_id")
        outline_groups: dict[str, list[dict[str, Any]]] = {}
        for episode in project.get("episodes") or []:
            if episode.get("stage") != "outline":
                continue
            if (episode.get("upstream_refs") or {}).get("character_batch_id") != current_characters:
                continue
            outline_groups.setdefault(str(episode.get("batch_id") or ""), []).append(episode)
        if current_characters and any(
            len(items) == int(project["target_episode_count"])
            for items in outline_groups.values()
        ):
            return "episode_outlines_generated"
        if current_characters:
            return "pending_episode_outlines"
        locked_brief_id = project.get("locked_brief_id")
        if locked_brief_id and any(
            (item.get("source_refs") or {}).get("brief_id") == locked_brief_id
            for item in project.get("characters") or []
        ):
            return "character_relationships_generated"
        if locked_brief_id:
            return "pending_character_relationships"
        if project.get("blueprints"):
            return "imitation_generated"
        return "pending_imitation"
