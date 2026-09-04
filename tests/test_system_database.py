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
from sqlalchemy import text

from system_database.config import SystemDatabaseSettings
from system_database.database import SystemDatabase


@pytest.fixture
def database():
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")

    system_database = SystemDatabase(SystemDatabaseSettings(url=postgres_url))
    try:
        yield system_database
    finally:
        system_database.dispose()


def test_settings_require_a_postgresql_sqlalchemy_url(monkeypatch):
    monkeypatch.delenv("AIWORKER_SYSTEM_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="AIWORKER_SYSTEM_DATABASE_URL"):
        SystemDatabaseSettings.from_environment()


def test_settings_redact_database_password(monkeypatch):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://app:secret@127.0.0.1:5432/aiworker_system",
    )

    settings = SystemDatabaseSettings.from_environment()

    assert settings.redacted_url == (
        "postgresql+psycopg://app:***@127.0.0.1:5432/aiworker_system"
    )


def test_transaction_rolls_back_when_block_raises(database):
    with pytest.raises(RuntimeError, match="rollback"):
        with database.transaction() as connection:
            connection.execute(text("CREATE TEMP TABLE rollback_probe (id integer)"))
            connection.execute(text("INSERT INTO rollback_probe VALUES (1)"))
            raise RuntimeError("rollback")

    with database.connection() as connection:
        assert (
            connection.execute(
                text("SELECT to_regclass('pg_temp.rollback_probe')")
            ).scalar_one()
            is None
        )


def test_health_never_exposes_password(database):
    health = database.health()

    assert health.reachable is True
    assert "secret" not in health.database_url_redacted


def test_health_reports_schema_current_after_upgrade(database, monkeypatch):
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", database.settings.url)
    config = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    command.upgrade(config, "head")

    health = database.health()

    assert health.reachable is True
    assert health.schema_current is True
    assert health.current_revision == health.head_revision
