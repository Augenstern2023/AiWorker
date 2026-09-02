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
from sqlalchemy import BigInteger, DateTime, Numeric, Text, create_engine, inspect, text
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
        "llm_models",
        "llm_providers",
        "llm_settings",
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


def test_system_schema_declares_central_rank_tables():
    snapshots = SYSTEM_METADATA.tables["rank_snapshots"]
    entries = SYSTEM_METADATA.tables["rank_entries"]

    assert snapshots.c.source_checksum.type.length == 64
    assert isinstance(snapshots.c.validation_json.type, JSONB)
    assert isinstance(entries.c.source_record_json.type, JSONB)
    assert isinstance(entries.c.like_count.type, BigInteger)
    assert isinstance(entries.c.heat.type, Numeric)
    foreign_key = next(iter(entries.c.snapshot_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"


def test_upgrade_head_creates_central_rank_constraints(postgres_url, monkeypatch):
    run_alembic_upgrade(postgres_url, monkeypatch)
    engine = create_engine(postgres_url)
    try:
        inspector = inspect(engine)
        assert inspector.has_table("rank_snapshots")
        assert inspector.has_table("rank_entries")
        assert {
            item["name"]
            for item in inspector.get_unique_constraints("rank_snapshots")
        } >= {
            "uq_rank_snapshots_source_ref",
            "uq_rank_snapshots_checksum",
        }
        assert {
            item["name"] for item in inspector.get_foreign_keys("rank_entries")
        } == {"fk_rank_entries_snapshot_id"}
    finally:
        engine.dispose()


def test_baseline_users_structure_excludes_contact_profile():
    users = BASELINE_METADATA.tables["users"]

    baseline_column_names = {column.name for column in users.c}
    assert not {"email", "phone", "gender"} & baseline_column_names
    assert "ck_users_gender" not in {constraint.name for constraint in users.constraints}
    baseline_indexes = {
        index.name
        for table in BASELINE_METADATA.tables.values()
        for index in table.indexes
    }
    assert not {"uq_users_active_email", "uq_users_active_phone"} & baseline_indexes


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
        assert inspector.has_table("llm_providers")
        assert inspector.has_table("llm_models")
        assert inspector.has_table("llm_settings")
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


def test_project_creator_permission_backfill_includes_disabled_builtin_roles(
    postgres_url, monkeypatch
):
    """Re-enabling a built-in creator role must not lose project.create."""
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    try:
        with engine.begin() as connection:
            role_id = connection.execute(
                text("SELECT id FROM roles WHERE lower(code) = 'screenwriter'")
            ).scalar_one()
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE role_id = :role_id "
                    "AND permission_code = 'project.create'"
                ),
                {"role_id": role_id},
            )
            connection.execute(
                text("UPDATE roles SET is_enabled = 0 WHERE id = :role_id"),
                {"role_id": role_id},
            )

        command.downgrade(config, "0009_project_creator_permission")
        command.upgrade(config, "head")

        with engine.connect() as connection:
            scope = connection.execute(
                text(
                    "SELECT data_scope FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_code = 'project.create'"
                ),
                {"role_id": role_id},
            ).scalar_one_or_none()
        assert scope == "project"
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE roles SET is_enabled = 1 WHERE lower(code) = 'screenwriter'")
            )
        command.upgrade(config, "head")
        engine.dispose()


def test_writer_stage_permission_migration_preserves_admin_owned_grants(
    postgres_url, monkeypatch
):
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    custom_permission = f"custom.writer-stage-{uuid.uuid4()}"
    try:
        with engine.begin() as connection:
            role_id = connection.execute(
                text("SELECT id FROM roles WHERE lower(code) = 'screenwriter'")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO permissions (code, label) VALUES (:code, '后台自定义权限')"
                ),
                {"code": custom_permission},
            )
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_code, data_scope) "
                    "VALUES (:role_id, :code, 'project')"
                ),
                {"role_id": role_id, "code": custom_permission},
            )

        command.downgrade(config, "0011_writer_established_read")

        with engine.connect() as connection:
            grants = dict(
                connection.execute(
                    text(
                        "SELECT permission_code, data_scope FROM role_permissions "
                        "WHERE role_id = :role_id AND permission_code IN "
                        "('project.write.established.writing', :custom_permission)"
                    ),
                    {
                        "role_id": role_id,
                        "custom_permission": custom_permission,
                    },
                ).all()
            )
        assert grants == {
            "project.write.established.writing": "project",
            custom_permission: "project",
        }
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE permission_code = :code"
                ),
                {"code": custom_permission},
            )
            connection.execute(
                text("DELETE FROM permissions WHERE code = :code"),
                {"code": custom_permission},
            )
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
