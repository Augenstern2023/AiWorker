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
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, Text, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url

from system_database.baseline_schema import BASELINE_METADATA
from identity.repository import IdentityRepository
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import SYSTEM_METADATA


PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def postgres_url() -> str:
    value = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not value:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(value).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    return value


def run_alembic_upgrade(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


def test_system_schema_declares_all_shared_tables():
    expected_tables = {
        "approval_records",
        "audit_logs",
        "permission_dependencies",
        "permissions",
        "project_members",
        "role_claimable_task_types",
        "role_permissions",
        "roles",
        "sessions",
        "user_roles",
        "users",
        "workflow_instances",
        "workflow_project_member_events",
        "workflow_submissions",
        "workflow_task_events",
        "workflow_task_jobs",
        "workflow_tasks",
    }

    assert expected_tables <= set(SYSTEM_METADATA.tables)


def test_system_schema_keeps_existing_user_ids_as_text():
    assert isinstance(SYSTEM_METADATA.tables["users"].c.id.type, Text)


def test_runtime_schema_uses_timestamptz_and_jsonb_for_system_events():
    sessions = SYSTEM_METADATA.tables["sessions"]
    audit_logs = SYSTEM_METADATA.tables["audit_logs"]
    task_events = SYSTEM_METADATA.tables["workflow_task_events"]
    submissions = SYSTEM_METADATA.tables["workflow_submissions"]

    assert isinstance(sessions.c.expires_at.type, DateTime)
    assert sessions.c.expires_at.type.timezone is True
    assert isinstance(audit_logs.c.created_at.type, DateTime)
    assert audit_logs.c.created_at.type.timezone is True
    assert isinstance(audit_logs.c.details_json.type, JSONB)
    assert isinstance(task_events.c.details_json.type, JSONB)
    assert isinstance(submissions.c.content_snapshot_json.type, JSONB)


def test_baseline_table_names_are_immutable():
    assert frozenset(BASELINE_METADATA.tables) == frozenset(
        {
            "approval_records",
            "audit_logs",
            "permission_dependencies",
            "permissions",
            "project_members",
            "role_claimable_task_types",
            "role_permissions",
            "roles",
            "sessions",
            "user_roles",
            "users",
            "workflow_instances",
            "workflow_project_member_events",
            "workflow_submissions",
            "workflow_task_events",
            "workflow_task_jobs",
            "workflow_tasks",
        }
    )


def test_upgrade_head_creates_system_tables(postgres_url, monkeypatch):
    run_alembic_upgrade(postgres_url, monkeypatch)
    engine = create_engine(postgres_url)
    try:
        inspector = inspect(engine)
        assert inspector.has_table("users")
        assert inspector.has_table("workflow_tasks")
        assert inspector.has_table("audit_logs")
    finally:
        engine.dispose()


def test_baseline_keeps_text_primary_keys(postgres_url, monkeypatch):
    run_alembic_upgrade(postgres_url, monkeypatch)
    engine = create_engine(postgres_url)
    try:
        columns = inspect(engine).get_columns("users")
        identifier = next(column for column in columns if column["name"] == "id")
        assert identifier["type"].__class__.__name__ == "TEXT"
    finally:
        engine.dispose()


def test_role_page_access_migration_round_trips_existing_account_grants(
    postgres_url, monkeypatch
):
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    engine = create_engine(postgres_url)
    command.downgrade(config, "0006_user_page_access")
    try:
        database = SystemDatabase(SystemDatabaseSettings(postgres_url))
        repository = IdentityRepository(database)
        repository.seed_defaults()
        user = repository.create_user(
            f"migration-page-access-{uuid.uuid4()}",
            "Migration Page Access User",
            "hash",
        )
        with database.transaction() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_page_access (user_id, route) "
                    "VALUES (:user_id, '/selection/ranks') "
                    "ON CONFLICT (user_id, route) DO NOTHING"
                ),
                {"user_id": user["id"]},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT is_allowed FROM user_page_access "
                    "WHERE user_id = :user_id AND route = '/selection/ranks'"
                ),
                {"user_id": user["id"]},
            ).scalar_one() is True
            defaults = set(
                connection.execute(
                    text(
                        "SELECT p.route FROM role_page_access p "
                        "JOIN roles r ON r.id = p.role_id "
                        "WHERE r.code = 'selection_operator'"
                    )
                ).scalars()
            )
        assert defaults == {
            "/selection/dashboard",
            "/selection/ranks",
            "/selection/hits",
            "/selection/topics",
            "/selection/dramas",
        }

        command.downgrade(config, "0006_user_page_access")
        inspector = inspect(engine)
        assert not inspector.has_table("role_page_access")
        assert "is_allowed" not in {
            column["name"] for column in inspector.get_columns("user_page_access")
        }
    finally:
        command.upgrade(config, "head")
        engine.dispose()
