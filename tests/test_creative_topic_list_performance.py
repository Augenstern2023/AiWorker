# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis.today_rank_service import TodayRankService
from creative.api import creative_api_principal, register_creative_routes
from creative.legacy_sqlite_repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal


def test_lightweight_topic_list_does_not_scan_lifecycle_sources(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-lightweight",
                "source_path": "data/hg/lightweight.json",
                "captured_at": "2026-09-21",
                "title": "轻量选题列表",
                "description": "列表初始化不需要生命周期",
                "category": "都市",
                "rank_type": "热播榜",
                "rank_source": "全部热播榜",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]
    manager = MagicMock()
    manager.list_jobs.side_effect = AssertionError(
        "lightweight topic lists must not scan jobs"
    )
    workflow_repository = MagicMock()
    workflow_repository.identity_repository = None
    app = FastAPI()
    principal = Principal(
        user_id="performance-admin",
        username="performance-admin",
        display_name="Performance Admin",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="performance-session",
    )
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    with (
        patch("creative.api.ProjectPromptService") as prompt_service_class,
        patch("creative.api.PromptMarketplaceService"),
        patch("creative.api.PromptObservabilityService"),
    ):
        prompt_service_class.return_value.store = MagicMock()
        prompt_service_class.return_value.public_projection.side_effect = (
            lambda payload, *_args: payload
        )
        register_creative_routes(
            app,
            root=tmp_path,
            runtime_dir=tmp_path / ".runtime",
            job_manager=manager,
            workflow_repository=workflow_repository,
            repository=repository,
            rank_service=TodayRankService.from_files(root=tmp_path),
        )

    response = TestClient(app).get(
        "/api/creative/topics",
        params={"limit": 50, "include_lifecycle": "false"},
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [topic["id"]]
    assert "lifecycle_status" not in response.json()["items"][0]
    manager.list_jobs.assert_not_called()

    repository.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    manager.list_jobs.side_effect = None
    manager.list_jobs.return_value = []
    filtered = TestClient(app).get(
        "/api/creative/topics",
        params={
            "lifecycle_status": "pending_review",
            "include_lifecycle": "false",
        },
    )

    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["items"][0]["lifecycle_status"] == "pending_review"
    manager.list_jobs.assert_called_once_with()
