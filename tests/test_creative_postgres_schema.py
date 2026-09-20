# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, DateTime, Identity, BigInteger, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError

from creative.database_schema import (
    CREATIVE_BUSINESS_TABLES,
    CREATIVE_CONTROL_TABLES,
    CREATIVE_METADATA,
    EXPECTED_CREATIVE_TABLES,
)
from system_database import SystemDatabase


PROJECT_ROOT = Path(__file__).parent.parent

# Frozen inventory of the 36 tables that physically exist in the legacy
# .runtime/creative/creative.db. The PostgreSQL schema must give every one of
# them a home; a table that disappears here means data would be dropped.
LEGACY_SQLITE_TABLES = frozenset(
    {
        "creative_blueprints",
        "creative_breakdowns",
        "creative_characters",
        "creative_deliveries",
        "creative_episodes",
        "creative_generation_contexts",
        "creative_generation_operations",
        "creative_mode_migration_compensations",
        "creative_mode_migrations",
        "creative_operation_results",
        "creative_production_exports",
        "creative_project_deletions",
        "creative_project_prompt_bindings",
        "creative_project_prompt_settings",
        "creative_projects",
        "creative_prompt_ab_arms",
        "creative_prompt_ab_experiments",
        "creative_prompt_governance_events",
        "creative_prompt_iteration_drafts",
        "creative_prompt_market_events",
        "creative_prompt_market_installations",
        "creative_prompt_market_ratings",
        "creative_prompt_market_submissions",
        "creative_prompt_template_revisions",
        "creative_prompt_templates",
        "creative_relationships",
        "creative_resource_heads",
        "creative_seeds",
        "creative_stage_exports",
        "creative_store_metadata",
        "creative_topics",
        "role_workflow_migrations",
        "script_optimization_drafts",
        "script_optimizations",
        "script_reviews",
        "selection_stage_snapshots",
    }
)


# ---------------------------------------------------------------------------
# metadata level assertions
# ---------------------------------------------------------------------------


def test_creative_metadata_declares_complete_schema():
    assert set(CREATIVE_METADATA.tables) == set(EXPECTED_CREATIVE_TABLES)


def test_creative_metadata_covers_every_legacy_sqlite_table():
    assert LEGACY_SQLITE_TABLES <= set(CREATIVE_METADATA.tables)


def test_creative_table_inventory_is_partitioned():
    assert CREATIVE_BUSINESS_TABLES & CREATIVE_CONTROL_TABLES == frozenset()
    assert len(CREATIVE_BUSINESS_TABLES) == 38
    assert CREATIVE_CONTROL_TABLES == frozenset({"creative_database_import_runs"})


def test_creative_tables_declared_here_are_not_system_tables():
    from system_database.schema import SYSTEM_METADATA

    assert set(CREATIVE_METADATA.tables) & set(SYSTEM_METADATA.tables) == set()


def test_creative_json_columns_use_jsonb():
    topics = CREATIVE_METADATA.tables["creative_topics"]
    assert isinstance(topics.c.evidence_json.type, JSONB)
    assert isinstance(topics.c.score_details_json.type, JSONB)
    assert isinstance(
        CREATIVE_METADATA.tables["creative_deliveries"].c.projection_source_json.type,
        JSONB,
    )
    assert isinstance(
        CREATIVE_METADATA.tables["creative_database_import_runs"].c.table_hashes_json.type,
        JSONB,
    )


def test_creative_timestamps_are_timezone_aware():
    columns = CREATIVE_METADATA.tables["creative_projects"].c
    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone is True
    assert isinstance(columns.mode_changed_at.type, DateTime)
    assert columns.mode_changed_at.type.timezone is True


def test_creative_flags_are_boolean():
    assert isinstance(CREATIVE_METADATA.tables["creative_projects"].c.deleted.type, Boolean)
    assert isinstance(CREATIVE_METADATA.tables["creative_topics"].c.deleted.type, Boolean)
    assert isinstance(CREATIVE_METADATA.tables["creative_deliveries"].c.is_formal.type, Boolean)


def test_creative_integer_primary_keys_are_identity():
    projects = CREATIVE_METADATA.tables["creative_projects"]
    assert projects.c.id.identity is not None
    assert isinstance(projects.c.id.identity, Identity)
    assert isinstance(projects.c.id.type, BigInteger)
    # text identifiers stay text
    assert CREATIVE_METADATA.tables["creative_deliveries"].c.id.primary_key is True
    assert not isinstance(
        CREATIVE_METADATA.tables["creative_deliveries"].c.id.type, BigInteger
    )


def test_legacy_float_measurements_stay_double_precision():
    """NUMERIC would round and break the SQLite -> PostgreSQL -> SQLite round trip."""
    from sqlalchemy.dialects import postgresql

    for table, column in (
        ("creative_topics", "score"),
        ("creative_blueprints", "risk_score"),
        ("creative_prompt_ab_experiments", "budget_amount"),
    ):
        declared = CREATIVE_METADATA.tables[table].c[column]
        rendered = declared.type.compile(dialect=postgresql.dialect())
        assert rendered == "DOUBLE PRECISION", (table, column, rendered)


def test_named_constraints_used_by_repositories_exist():
    topics = {c.name for c in CREATIVE_METADATA.tables["creative_topics"].constraints}
    assert "uq_creative_topics_source" in topics

    deliveries = {
        i.name for i in CREATIVE_METADATA.tables["creative_deliveries"].indexes
    }
    assert "uq_creative_delivery_request" in deliveries

    projects = {i.name for i in CREATIVE_METADATA.tables["creative_projects"].indexes}
    assert {
        "uq_creative_projects_creation_request",
        "uq_creative_projects_creation_workflow_task",
        "uq_creative_projects_workflow_task",
    } <= projects


# ---------------------------------------------------------------------------
# migration graph
# ---------------------------------------------------------------------------


def test_alembic_head_includes_video_parameters_and_zip_exports():
    revisions = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))

    assert revisions.get_current_head() == "0026_video_params_zip_exports"
    assert (
        revisions.get_revision("0026_video_params_zip_exports").down_revision
        == "0025_creative_schema_baseline"
    )
    assert (
        revisions.get_revision("0025_creative_schema_baseline").down_revision
        == "0024_llm_model_max_output_tokens"
    )


# ---------------------------------------------------------------------------
# live schema assertions
# ---------------------------------------------------------------------------


def test_alembic_head_creates_every_creative_table(isolated_system_database: SystemDatabase):
    inspector = inspect(isolated_system_database.engine)
    present = set(inspector.get_table_names())
    assert set(EXPECTED_CREATIVE_TABLES) <= present


def test_creative_integer_columns_are_identity_in_postgres(
    isolated_system_database: SystemDatabase,
):
    with isolated_system_database.connection() as connection:
        identity = connection.execute(
            text(
                "SELECT is_identity FROM information_schema.columns "
                "WHERE table_name = 'creative_projects' AND column_name = 'id' "
                "AND table_schema = current_schema()"
            )
        ).scalar_one()
    assert identity == "YES"


def test_creative_partial_unique_indexes_exist(isolated_system_database: SystemDatabase):
    with isolated_system_database.connection() as connection:
        rows = connection.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'creative_projects' "
                "AND schemaname = current_schema()"
            )
        ).fetchall()
    definitions = {name: definition for name, definition in rows}
    creation_request = definitions["uq_creative_projects_creation_request"]
    assert "UNIQUE" in creation_request
    assert "WHERE" in creation_request
    assert "creation_request_id" in creation_request


def test_creative_domain_foreign_keys_are_declared(
    isolated_system_database: SystemDatabase,
):
    inspector = inspect(isolated_system_database.engine)
    projects = inspector.get_foreign_keys("creative_projects")
    referred = {
        (fk["constrained_columns"][0], fk["referred_table"])
        for fk in projects
    }
    assert ("topic_id", "creative_topics") in referred
    assert ("breakdown_id", "creative_breakdowns") in referred

    episodes = inspector.get_foreign_keys("creative_episodes")
    assert any(fk["referred_table"] == "creative_projects" for fk in episodes)


def test_cross_domain_foreign_keys_are_not_declared(
    isolated_system_database: SystemDatabase,
):
    """users/workflow_tasks/workflow_submissions stay application-enforced."""
    inspector = inspect(isolated_system_database.engine)
    forbidden = {"users", "workflow_tasks", "workflow_submissions", "audit_logs"}
    for table in EXPECTED_CREATIVE_TABLES:
        for fk in inspector.get_foreign_keys(table):
            assert fk["referred_table"] not in forbidden, (table, fk)


def test_creative_tables_start_empty(isolated_system_database: SystemDatabase):
    with isolated_system_database.connection() as connection:
        for table in sorted(EXPECTED_CREATIVE_TABLES):
            count = connection.execute(
                text(f'SELECT COUNT(*) FROM "{table}"')
            ).scalar_one()
            assert count == 0, table


def test_creative_baseline_downgrade_removes_only_creative_tables(
    isolated_system_database: SystemDatabase,
):
    """A failed cutover must be reversible without touching the system schema."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, "0024_llm_model_max_output_tokens")

    present = set(inspect(isolated_system_database.engine).get_table_names())
    assert set(EXPECTED_CREATIVE_TABLES) & present == set()
    assert {"llm_models", "llm_providers", "workflow_tasks", "users"} <= present

    with isolated_system_database.connection() as connection:
        leftover = connection.execute(
            text(
                "SELECT COUNT(*) FROM pg_proc "
                "WHERE proname = 'creative_selection_snapshot_immutable' "
                "AND pronamespace = current_schema()::regnamespace"
            )
        ).scalar_one()
    assert leftover == 0

    command.upgrade(config, "head")
    present = set(inspect(isolated_system_database.engine).get_table_names())
    assert set(EXPECTED_CREATIVE_TABLES) <= present


def test_selection_stage_snapshots_reject_update_and_delete(
    isolated_system_database: SystemDatabase,
):
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO selection_stage_snapshots "
                "(id, stage, topic_id, version, outcome, snapshot_json, "
                " content_fingerprint, created_at) "
                "VALUES ('snap-1', 'rank', 1, 1, 'adopted', '{}'::jsonb, 'fp', now())"
            )
        )

    with pytest.raises(DBAPIError, match="selection snapshots are immutable"):
        with isolated_system_database.transaction() as connection:
            connection.execute(
                text("UPDATE selection_stage_snapshots SET outcome = 'x' WHERE id = 'snap-1'")
            )

    with pytest.raises(DBAPIError, match="selection snapshots are immutable"):
        with isolated_system_database.transaction() as connection:
            connection.execute(
                text("DELETE FROM selection_stage_snapshots WHERE id = 'snap-1'")
            )

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT outcome FROM selection_stage_snapshots WHERE id = 'snap-1'")
        ).scalar_one() == "adopted"


def test_creative_check_constraints_reject_invalid_rows(
    isolated_system_database: SystemDatabase,
):
    with pytest.raises(DBAPIError):
        with isolated_system_database.transaction() as connection:
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, created_at, updated_at) "
                    "VALUES ('bad', 'nonsense', now(), now())"
                )
            )

    with pytest.raises(DBAPIError):
        with isolated_system_database.transaction() as connection:
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, topic_id, breakdown_id, created_at, updated_at) "
                    "VALUES ('bad', 'original', 1, 1, now(), now())"
                )
            )
