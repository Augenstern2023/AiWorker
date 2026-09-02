# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from analysis.rank_models import RankDatabaseUnavailable, RankSnapshot
from analysis.rank_repository import RankRepository
from analysis.today_rank_api import build_today_rank_router
from analysis.today_rank_service import TodayRankService
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import rank_entries, rank_snapshots


PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch):
    value = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not value:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(value).database in {"aiworker", "aiworker_system"}:
        raise ValueError("rank API tests require a dedicated test database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", value)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    system_database = SystemDatabase(SystemDatabaseSettings(value))
    with system_database.transaction() as connection:
        connection.execute(rank_entries.delete())
        connection.execute(rank_snapshots.delete())
    try:
        yield system_database
    finally:
        with system_database.transaction() as connection:
            connection.execute(rank_entries.delete())
            connection.execute(rank_snapshots.delete())
        system_database.dispose()


def test_today_rank_api_reads_postgres_when_local_rank_directory_is_empty(
    tmp_path: Path,
    database: SystemDatabase,
):
    snapshot = RankSnapshot(
        platform="dy",
        job_id="central-job",
        source_path=Path("data/dy/jobs/central-job/rank/rank.json"),
        captured_at=datetime(2026, 9, 2, 10, 0, 0),
        records=[
            {
                "id": "central-1",
                "record_id": "central-1",
                "series_id": "central-1",
                "title": "数据库榜单剧",
                "rank": 1,
                "tab": "榜单",
                "category": "短剧",
                "category_label": "短剧",
                "heat": 99.5,
                "like_count": 100,
                "validation_warnings": [],
            }
        ],
        validation={"status": "passed", "has_warnings": False},
    )
    RankRepository(database).store_snapshot(
        snapshot,
        source_kind="douyin_json",
        source_ref="data/dy/jobs/central-job/rank/rank.json",
        source_checksum="e" * 64,
        parser_version=1,
    )
    app = FastAPI()
    app.include_router(build_today_rank_router(root=tmp_path))

    response = TestClient(app).get("/api/rank/today?platform=dy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-09-02"
    assert payload["current_entries"][0]["title"] == "数据库榜单剧"
    assert payload["selected_snapshot"]["source_path"] == (
        "data/dy/jobs/central-job/rank/rank.json"
    )


def test_today_rank_api_returns_503_when_postgres_source_is_unavailable(tmp_path: Path):
    class UnavailableSource:
        def revision(self, platform: str) -> str:
            raise RankDatabaseUnavailable("榜单数据库暂不可用")

        def list_snapshots(self, platform: str):
            raise RankDatabaseUnavailable("榜单数据库暂不可用")

    service = TodayRankService(
        root=tmp_path,
        snapshot_source=UnavailableSource(),
    )
    app = FastAPI()
    app.include_router(build_today_rank_router(root=tmp_path, service=service))

    response = TestClient(app).get("/api/rank/today?platform=dy")

    assert response.status_code == 503
    assert response.json() == {"detail": "榜单数据库暂不可用"}
