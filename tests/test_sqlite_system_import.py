# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import sqlite3
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import users, workflow_instances
from system_database.sqlite_import import (
    _import_order,
    import_sqlite_system,
    inspect_sqlite_system,
)
from system_database.sqlite_import_cli import execute_import_command


def _source_database(path: Path, *, details_json: str = "{}") -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, details_json TEXT);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT);
            INSERT INTO users (id, username, details_json)
            VALUES ('role:system', 'admin', :details_json);
            INSERT INTO sessions (id, user_id) VALUES ('session-1', 'role:system');
            """.replace(":details_json", f"'{details_json}'")
        )
    return path


def test_inspection_keeps_text_identifiers_and_excludes_legacy_sessions(tmp_path: Path):
    report = inspect_sqlite_system(_source_database(tmp_path / "system.db"))

    assert report.source_counts == {"users": 1}
    assert report.skipped_session_count == 1
    assert report.primary_key_samples["users"] == ("role:system",)


def test_inspection_rejects_invalid_json_before_an_import(tmp_path: Path):
    source = _source_database(tmp_path / "invalid-system.db", details_json="not-json")

    with pytest.raises(ValueError, match="users.details_json"):
        inspect_sqlite_system(source)


def test_check_command_is_read_only_and_returns_machine_readable_report(tmp_path: Path):
    report = execute_import_command("check", _source_database(tmp_path / "check.db"))

    assert report["mode"] == "check"
    assert report["source_counts"] == {"users": 1}
    assert report["skipped_session_count"] == 1


def test_apply_import_preserves_text_ids_and_does_not_copy_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identifier = f"role:import-{uuid.uuid4()}"
    username = f"import-{uuid.uuid4()}"
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                normalized_username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            """
        )
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, 'Import User', 'hash')",
            (identifier, username, username),
        )
        connection.execute(
            "INSERT INTO sessions VALUES ('legacy-session', ?)", (identifier,)
        )

    report = import_sqlite_system(source, database)

    with database.connection() as connection:
        assert connection.execute(select(users.c.id).where(users.c.id == identifier)).scalar_one() == identifier
    assert report.verified is True
    assert report.skipped_session_count == 1


def test_apply_import_converts_workflow_instance_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identifier = f"workflow:import-{uuid.uuid4()}"
    source = tmp_path / "legacy-workflow.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE workflow_instances (
                id TEXT PRIMARY KEY,
                workflow_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO workflow_instances VALUES (?, 'brief', 'project', '1', "
            "'active', '2026-07-23 05:07:20', '2026-07-23 05:07:20')",
            (identifier,),
        )

    report = import_sqlite_system(source, database)

    with database.connection() as connection:
        created_at = connection.execute(
            select(workflow_instances.c.created_at).where(
                workflow_instances.c.id == identifier
            )
        ).scalar_one()
    assert created_at.astimezone(UTC) == datetime(2026, 7, 23, 5, 7, 20, tzinfo=UTC)
    assert report.verified is True


def test_import_order_includes_project_member_events():
    assert _import_order({"workflow_project_member_events": 1}) == (
        "workflow_project_member_events",
    )
