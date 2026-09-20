# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Derive compact project-list navigation metadata."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any


_FREE_STAGE_PRIORITY = {
    "brief": 1,
    "characters": 2,
    "outline": 3,
    "beats": 4,
    "script": 5,
}


def _timestamp_key(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def attach_latest_free_stage(
    projects: list[dict[str, Any]], heads: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach the most recently touched free-mode stage to project summaries."""

    latest_by_project: dict[int, dict[str, Any]] = {}
    for head in heads:
        stage = str(head.get("stage") or "")
        if stage not in _FREE_STAGE_PRIORITY:
            continue
        project_id = int(head.get("project_id") or 0)
        if project_id <= 0:
            continue
        candidate_key = (
            _timestamp_key(head.get("updated_at")),
            _FREE_STAGE_PRIORITY[stage],
            int(head.get("episode_number") or 0),
        )
        current = latest_by_project.get(project_id)
        if current is None or candidate_key > current["sort_key"]:
            latest_by_project[project_id] = {
                "sort_key": candidate_key,
                "stage": stage,
                "episode_number": int(head.get("episode_number") or 0),
            }

    for project in projects:
        if project.get("workflow_mode") != "free":
            continue
        latest = latest_by_project.get(int(project.get("id") or 0))
        project["latest_stage"] = latest["stage"] if latest else "brief"
        project["latest_episode_number"] = latest["episode_number"] if latest else 0
    return projects
