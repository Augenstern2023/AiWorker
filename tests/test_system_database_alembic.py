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
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Text, create_engine, inspect
from sqlalchemy.engine import make_url

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
