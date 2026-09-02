# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import importlib
import importlib.util
import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from analysis.rank_models import RankSnapshot
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import rank_entries, rank_snapshots


PROJECT_ROOT = Path(__file__).parent.parent


def _rank_repository_type():
    spec = importlib.util.find_spec("analysis.rank_repository")
    assert spec is not None, "analysis.rank_repository must provide PostgreSQL persistence"
    return importlib.import_module("analysis.rank_repository").RankRepository


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch):
    value = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not value:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    database_name = make_url(value).database
    if database_name in {"aiworker", "aiworker_system"}:
        raise ValueError("rank repository tests require a dedicated test database")
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


def _snapshot(*records: dict, captured_at: str = "2026-09-01 10:00:00"):
    return RankSnapshot(
        platform="dy",
        job_id="job-1",
        source_path=Path("data/dy/jobs/job-1/rank/rank.json"),
        captured_at=datetime.strptime(captured_at, "%Y-%m-%d %H:%M:%S"),
        records=list(records),
        validation={"status": "passed", "records": len(records)},
    )


def _record(rank: int, title: str, series_id: str, heat: float) -> dict:
    return {
        "id": series_id,
        "record_id": series_id,
        "series_id": series_id,
        "title": title,
        "rank": rank,
        "tab": "榜单",
        "category": "短剧",
        "category_label": "短剧",
        "status": "连载中",
        "subject": "逆袭",
        "description": f"{title} 简介",
        "heat": heat,
        "like_count": 12000,
        "validation_warnings": [],
    }


def _counts(database: SystemDatabase) -> tuple[int, int]:
    with database.connection() as connection:
        return (
            connection.execute(select(func.count()).select_from(rank_snapshots)).scalar_one(),
            connection.execute(select(func.count()).select_from(rank_entries)).scalar_one(),
        )


def test_rank_repository_round_trips_snapshot_and_skips_identical_source(database):
    repository = _rank_repository_type()(database)
    snapshot = _snapshot(
        _record(1, "第一名", "dy-1", 88.5),
        _record(2, "第二名", "dy-2", 70),
    )
    source_ref = "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"

    imported = repository.store_snapshot(
        snapshot,
        source_kind="douyin_json",
        source_ref=source_ref,
        source_checksum="a" * 64,
        parser_version=1,
    )
    repeated = repository.store_snapshot(
        snapshot,
        source_kind="douyin_json",
        source_ref=source_ref,
        source_checksum="a" * 64,
        parser_version=1,
    )

    assert imported.action == "imported"
    assert imported.record_count == 2
    assert repeated.action == "skipped"
    assert repeated.snapshot_id == imported.snapshot_id
    assert _counts(database) == (1, 2)
    restored = repository.list_snapshots("dy")
    assert len(restored) == 1
    assert restored[0].source_path.as_posix() == source_ref
    assert restored[0].records == snapshot.records
    assert restored[0].validation == snapshot.validation


def test_rank_repository_replaces_changed_source_and_changes_revision(database):
    repository = _rank_repository_type()(database)
    source_ref = "data/dy/jobs/job-1/rank/douyin_series_rank_20260901_100000.json"
    repository.store_snapshot(
        _snapshot(_record(1, "旧榜单", "dy-1", 70)),
        source_kind="douyin_json",
        source_ref=source_ref,
        source_checksum="b" * 64,
        parser_version=1,
    )
    first_revision = repository.revision("dy")

    updated = repository.store_snapshot(
        _snapshot(
            _record(1, "新榜单", "dy-2", 99.5),
            captured_at="2026-09-01 11:00:00",
        ),
        source_kind="douyin_json",
        source_ref=source_ref,
        source_checksum="c" * 64,
        parser_version=2,
    )

    assert updated.action == "updated"
    assert _counts(database) == (1, 1)
    assert repository.list_snapshots("dy")[0].records[0]["title"] == "新榜单"
    assert repository.revision("dy") != first_revision


def test_rank_repository_rolls_back_snapshot_when_entries_violate_board_rank(database):
    repository = _rank_repository_type()(database)
    duplicate_board_rank = _snapshot(
        _record(1, "冲突一", "dy-1", 70),
        _record(1, "冲突二", "dy-2", 60),
    )

    with pytest.raises(IntegrityError):
        repository.store_snapshot(
            duplicate_board_rank,
            source_kind="douyin_json",
            source_ref="data/dy/jobs/job-1/rank/conflict.json",
            source_checksum="d" * 64,
            parser_version=1,
        )

    assert _counts(database) == (0, 0)
