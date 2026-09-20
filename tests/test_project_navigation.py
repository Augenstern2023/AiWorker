# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from datetime import datetime, timezone

from creative.project_navigation import attach_latest_free_stage


def test_latest_free_stage_accepts_postgres_datetimes_and_has_stable_ties() -> None:
    projects = [
        {"id": 1, "workflow_mode": "free"},
        {"id": 2, "workflow_mode": "sequential"},
    ]
    same_time = datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc)

    result = attach_latest_free_stage(
        projects,
        [
            {
                "project_id": 1,
                "stage": "beats",
                "episode_number": 8,
                "updated_at": same_time,
            },
            {
                "project_id": 1,
                "stage": "script",
                "episode_number": 3,
                "updated_at": same_time,
            },
        ],
    )

    assert result[0]["latest_stage"] == "script"
    assert result[0]["latest_episode_number"] == 3
    assert "latest_stage" not in result[1]
