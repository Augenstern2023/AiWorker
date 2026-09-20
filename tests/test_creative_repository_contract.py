# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Contract tests for the creative repository boundary.

These fix the behaviour every back end must share: connection/transaction
ownership, workflow identity encoding, and the exact value types that may cross
the boundary. Domain scenarios live in the per-domain test modules and are
added as each repository lands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from creative.postgres_repository import PostgresCreativeRepository
from creative.repositories.base import (
    epoch_seconds,
    from_epoch_seconds,
    iso8601,
    jsonb_copy,
)
from creative.repository_factory import build_creative_repository
from system_database import SystemDatabase
from system_database.config import SystemDatabaseSettings


# `postgres_creative` comes from tests/conftest.py so every domain suite shares
# one construction path.


# ---------------------------------------------------------------------------
# construction and backend boundary
# ---------------------------------------------------------------------------


def test_postgres_creative_repository_requires_postgresql():
    sqlite_database = SystemDatabase(SystemDatabaseSettings(url="sqlite://"))

    with pytest.raises(ValueError, match="must be PostgreSQL"):
        PostgresCreativeRepository(sqlite_database)


def test_postgres_creative_repository_exposes_database_and_store_id(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    assert postgres_creative.database is isolated_system_database
    assert postgres_creative.store_id
    assert postgres_creative.workflow_project_id(12).endswith(":project:12")


def test_store_id_is_persisted_and_stable(isolated_system_database: SystemDatabase):
    first = PostgresCreativeRepository(isolated_system_database)
    second = PostgresCreativeRepository(isolated_system_database)
    assert first.store_id == second.store_id

    with isolated_system_database.connection() as connection:
        rows = connection.execute(
            text("SELECT key, value FROM creative_store_metadata")
        ).fetchall()
    assert rows == [("store_id", first.store_id)]


def test_store_id_is_preserved_across_repository_instances(
    isolated_system_database: SystemDatabase,
):
    """A migrated store keeps the identity its workflow history refers to."""

    repository = PostgresCreativeRepository(isolated_system_database)
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_store_metadata SET value = :value WHERE key = 'store_id'"
            ),
            {"value": "11111111-2222-3333-4444-555555555555"},
        )

    reloaded = PostgresCreativeRepository(isolated_system_database)
    assert reloaded.store_id == "11111111-2222-3333-4444-555555555555"
    assert repository.store_id != reloaded.store_id


def test_invalid_store_id_is_rejected(postgres_creative: PostgresCreativeRepository,
                                     isolated_system_database: SystemDatabase):
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text("UPDATE creative_store_metadata SET value = 'not-a-uuid' WHERE key = 'store_id'")
        )

    with pytest.raises(ValueError, match="creative store identity is invalid"):
        PostgresCreativeRepository(isolated_system_database)


# ---------------------------------------------------------------------------
# workflow identity
# ---------------------------------------------------------------------------


def test_workflow_identity_round_trips(postgres_creative: PostgresCreativeRepository):
    scoped = postgres_creative.workflow_project_id(12)
    assert scoped == f"creative:{postgres_creative.store_id}:project:12"
    assert postgres_creative.local_project_id_from_workflow(scoped) == 12

    topic_scoped = postgres_creative.workflow_topic_id(7)
    assert topic_scoped == f"creative:{postgres_creative.store_id}:topic:7"
    assert postgres_creative.local_topic_id_from_workflow(topic_scoped) == 7


def test_workflow_identity_rejects_foreign_and_malformed_values(
    postgres_creative: PostgresCreativeRepository,
):
    assert postgres_creative.local_project_id_from_workflow(None) is None
    assert postgres_creative.local_project_id_from_workflow("") is None
    assert (
        postgres_creative.local_project_id_from_workflow("creative:other:project:12")
        is None
    )
    assert (
        postgres_creative.local_project_id_from_workflow(
            f"creative:{postgres_creative.store_id}:project:not-a-number"
        )
        is None
    )
    assert postgres_creative.local_topic_id_from_workflow(
        postgres_creative.workflow_project_id(3)
    ) is None


# ---------------------------------------------------------------------------
# connection and transaction ownership
# ---------------------------------------------------------------------------


def test_transaction_scope_commits_when_it_owns_the_transaction(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    with postgres_creative.transaction_scope() as connection:
        connection.execute(
            text(
                "INSERT INTO creative_store_metadata (key, value) "
                "VALUES ('committed', 'yes')"
            )
        )

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT value FROM creative_store_metadata WHERE key = 'committed'")
        ).scalar_one() == "yes"


def test_connection_scope_without_connection_does_not_commit(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    with postgres_creative.connection_scope() as connection:
        connection.execute(
            text(
                "INSERT INTO creative_store_metadata (key, value) "
                "VALUES ('rolled-back', 'no')"
            )
        )

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM creative_store_metadata WHERE key = 'rolled-back'")
        ).scalar_one() == 0


def test_connection_scope_never_commits_a_caller_transaction(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    with pytest.raises(RuntimeError, match="injected failure"):
        with isolated_system_database.transaction() as connection:
            with postgres_creative.connection_scope(connection) as borrowed:
                assert borrowed is connection
                borrowed.execute(
                    text(
                        "INSERT INTO creative_store_metadata (key, value) "
                        "VALUES ('borrowed', 'no')"
                    )
                )
            raise RuntimeError("injected failure")

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM creative_store_metadata WHERE key = 'borrowed'")
        ).scalar_one() == 0


def test_transaction_scope_defers_to_a_caller_connection(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    """An externally owned connection stays open and uncommitted by the repository."""

    with isolated_system_database.transaction() as connection:
        with postgres_creative.transaction_scope(connection) as borrowed:
            assert borrowed is connection
            borrowed.execute(
                text(
                    "INSERT INTO creative_store_metadata (key, value) "
                    "VALUES ('shared', 'yes')"
                )
            )
        # still usable: the repository did not close it
        assert connection.execute(
            text("SELECT value FROM creative_store_metadata WHERE key = 'shared'")
        ).scalar_one() == "yes"

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT value FROM creative_store_metadata WHERE key = 'shared'")
        ).scalar_one() == "yes"


def test_transaction_scope_rolls_back_on_error(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    with pytest.raises(RuntimeError, match="boom"):
        with postgres_creative.transaction_scope() as connection:
            connection.execute(
                text(
                    "INSERT INTO creative_store_metadata (key, value) "
                    "VALUES ('doomed', 'yes')"
                )
            )
            raise RuntimeError("boom")

    with isolated_system_database.connection() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM creative_store_metadata WHERE key = 'doomed'")
        ).scalar_one() == 0


# ---------------------------------------------------------------------------
# outbound value contract
# ---------------------------------------------------------------------------


def test_row_dict_converts_timestamps_to_epoch_seconds(
    postgres_creative: PostgresCreativeRepository,
):
    moment = datetime(2026, 9, 15, 12, 30, 45, 123456, tzinfo=timezone.utc)
    converted = PostgresCreativeRepository.row_dict(
        {"created_at": moment, "title": "x"}
    )
    assert isinstance(converted["created_at"], float)
    assert converted["created_at"] == moment.timestamp()
    assert not isinstance(converted["created_at"], datetime)
    assert converted["title"] == "x"


def test_row_dict_deep_copies_json_values():
    original = {"evidence": [{"rank": 1}], "score_details": {"a": 1}}
    converted = PostgresCreativeRepository.row_dict(original)
    # `evidence` is a list, so row_dict copies it; the dict itself is rebuilt
    converted["score_details"]["a"] = 99
    assert original["score_details"]["a"] == 1
    assert converted["score_details"] is not original["score_details"]


def test_jsonb_copy_isolates_nested_structures():
    original = {"a": [{"b": 1}]}
    copied = jsonb_copy(original)
    copied["a"][0]["b"] = 2
    assert original["a"][0]["b"] == 1


def test_epoch_second_helpers_round_trip():
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    seconds = epoch_seconds(moment)
    assert isinstance(seconds, float)
    assert from_epoch_seconds(seconds) == moment
    assert from_epoch_seconds(None) is None
    assert from_epoch_seconds("") is None
    assert epoch_seconds(None) is None


def test_naive_timestamps_are_treated_as_utc():
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert epoch_seconds(naive) == epoch_seconds(
        naive.replace(tzinfo=timezone.utc)
    )
    assert iso8601(naive) == "2026-01-02T03:04:05Z"


def test_iso8601_renders_utc_with_z_suffix():
    shifted = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    assert iso8601(shifted) == "2026-01-01T19:04:05Z"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_reports_backend_without_credentials(
    postgres_creative: PostgresCreativeRepository,
):
    report = postgres_creative.health()
    assert report["backend"] == "postgresql"
    assert report["reachable"] is True
    assert report["schema_current"] is True
    assert report["store_readable"] is True
    assert report["healthy"] is True
    assert report["store_id"] == postgres_creative.store_id
    assert "LocalPass" not in str(report)


def test_describe_is_credential_free(postgres_creative: PostgresCreativeRepository):
    described = postgres_creative.describe()
    assert described["backend"] == "postgresql"
    assert described["store_id"] == postgres_creative.store_id
    assert "LocalPass" not in str(described)


# ---------------------------------------------------------------------------
# transitional factory
# ---------------------------------------------------------------------------


def test_factory_selects_postgresql(isolated_system_database: SystemDatabase):
    repository = build_creative_repository(isolated_system_database, backend="postgresql")
    assert isinstance(repository, PostgresCreativeRepository)


def test_factory_selects_sqlite_with_an_explicit_path(
    tmp_path: Path, isolated_system_database: SystemDatabase
):
    """The system database is only consulted for the PostgreSQL branch."""

    path = tmp_path / "creative.db"
    repository = build_creative_repository(
        isolated_system_database,
        backend="sqlite",
        legacy_sqlite_path=path,
    )
    assert path.is_file()
    assert repository.store_id


def test_factory_rejects_unknown_backend(isolated_system_database: SystemDatabase):
    with pytest.raises(ValueError, match="must be sqlite or postgresql"):
        build_creative_repository(isolated_system_database, backend="mysql")


def test_sqlite_selection_is_never_implicit(isolated_system_database: SystemDatabase):
    """Choosing PostgreSQL can never fall back to SQLite."""

    repository = build_creative_repository(isolated_system_database, backend="postgresql")
    assert repository.database is isolated_system_database
    assert not hasattr(repository, "db_path")


# ---------------------------------------------------------------------------
# the runtime storage switch
# ---------------------------------------------------------------------------


def test_runtime_selection_never_falls_back_to_sqlite(monkeypatch):
    """An unusable PostgreSQL target must fail loudly, not read a frozen store."""

    from types import SimpleNamespace

    from creative.repository_factory import build_runtime_creative_repository

    class UnavailableDatabase:
        def health(self):
            return SimpleNamespace(
                reachable=False, schema_current=False, current_revision=None
            )

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "postgresql")
    with pytest.raises(RuntimeError, match="creative PostgreSQL is unavailable"):
        build_runtime_creative_repository(UnavailableDatabase())


def test_runtime_selection_refuses_a_schema_behind_head(
    monkeypatch, isolated_system_database: SystemDatabase
):
    """Reachable is not enough: a stale schema is still a hard failure."""

    from types import SimpleNamespace

    from creative.repository_factory import build_runtime_creative_repository

    class StaleDatabase:
        def health(self):
            return SimpleNamespace(
                reachable=True,
                schema_current=False,
                current_revision="0024_llm_model_max_output_tokens",
            )

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "postgresql")
    with pytest.raises(RuntimeError, match="creative PostgreSQL is unavailable"):
        build_runtime_creative_repository(StaleDatabase())


def test_runtime_selection_builds_postgresql_when_healthy(
    monkeypatch, isolated_system_database: SystemDatabase
):
    from creative.repository_factory import build_runtime_creative_repository

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "postgresql")
    repository = build_runtime_creative_repository(isolated_system_database)
    assert isinstance(repository, PostgresCreativeRepository)
    assert repository.database is isolated_system_database


def test_runtime_selection_ignores_legacy_sqlite_backend_setting(
    monkeypatch, isolated_system_database: SystemDatabase
):
    """The production runtime is PostgreSQL-only after the cutover."""

    from creative.repository_factory import build_runtime_creative_repository

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "sqlite")
    repository = build_runtime_creative_repository(isolated_system_database)
    assert isinstance(repository, PostgresCreativeRepository)
    assert repository.database is isolated_system_database


def test_runtime_selection_ignores_legacy_sqlite_path(
    monkeypatch, tmp_path: Path, isolated_system_database: SystemDatabase
):
    from creative.repository_factory import build_runtime_creative_repository

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "sqlite")
    path = tmp_path / "creative.db"
    repository = build_runtime_creative_repository(
        isolated_system_database, legacy_sqlite_path=path
    )
    assert isinstance(repository, PostgresCreativeRepository)
    assert repository.database is isolated_system_database
    assert not path.exists()


def test_runtime_selection_ignores_unknown_legacy_backend_setting(
    monkeypatch, isolated_system_database: SystemDatabase
):
    from creative.repository_factory import build_runtime_creative_repository

    monkeypatch.setenv("AIWORKER_CREATIVE_STORAGE", "mysql")
    repository = build_runtime_creative_repository(isolated_system_database)
    assert isinstance(repository, PostgresCreativeRepository)
    assert repository.database is isolated_system_database


# ---------------------------------------------------------------------------
# surface parity groundwork
# ---------------------------------------------------------------------------


def test_postgres_surface_is_callable_and_reports_names(
    postgres_creative: PostgresCreativeRepository,
):
    surface = PostgresCreativeRepository.public_surface()
    assert {"workflow_project_id", "workflow_topic_id", "health", "describe"} <= surface
    assert all(not name.startswith("_") for name in surface)


def test_repository_does_not_leak_sqlalchemy_types(
    postgres_creative: PostgresCreativeRepository,
    isolated_system_database: SystemDatabase,
):
    with postgres_creative.connection_scope() as connection:
        rows = connection.execute(
            text("SELECT key, value FROM creative_store_metadata")
        ).mappings().all()
    for row in rows:
        converted = PostgresCreativeRepository.row_dict(row)
        assert type(converted) is dict
        for value in converted.values():
            assert not isinstance(value, datetime)


# ---------------------------------------------------------------------------
# full surface parity against the frozen SQLite implementation
# ---------------------------------------------------------------------------

# Every public method the PostgreSQL repository is allowed to add on top of the
# frozen surface. Anything outside this set is an unintended API change.
_INTENTIONAL_ADDITIONS = frozenset(
    {
        # shared repository infrastructure
        "connection_scope",
        "describe",
        "health",
        "local_topic_id_from_workflow",
        "public_surface",
        "row_dict",
        "rows_dict",
        "transaction_scope",
        # delivery and production-export capabilities added by the migration
        "batch_lockable_episode_scripts",
        "complete_video_parameter_recommendation",
        "confirm_video_parameter_recommendation",
        "delivery_public_record",
        "fail_video_parameter_recommendation",
        "find_delivery",
        "find_production_export",
        "finish_script_optimization",
        "get_delivery",
        "get_delivery_projection_source",
        "get_production_export",
        "get_production_status",
        "get_video_parameter_recommendation",
        "list_deliveries",
        "list_production_exports",
        "lock_current_episode_scripts_batch",
        "mark_delivery_reconciled",
        "mark_production_export_audited",
        "prepare_script_optimization_resume",
        "prepare_script_review_resume",
        "production_export_public_record",
        "queue_video_parameter_recommendation",
        "read_snapshot_scope",
        "record_delivery",
        "record_production_export",
        "record_production_status",
        "reserve_project_identity",
        "restore_video_parameter_recommendation",
        "save_script_optimization_episode",
        "set_video_parameter_recommendation_job",
        "start_video_parameter_recommendation",
    }
)

# Methods that may accept an extra optional connection so a caller can join an
# existing transaction. Additive and keyword-defaulted, so every legacy call
# still binds.
_CONNECTION_AWARE = frozenset(
    {
        "create_original_project",
        "ensure_original_project",
        "get_blueprint",
        "get_character_batch",
        "get_episode",
        "get_outline_batch",
        "get_project",
        "has_stage_export",
        "lock_brief",
        "lock_character_batch",
        "lock_current_episode_beats_batch",
        "lock_episode_beats",
        "lock_episode_script",
        "lock_outline_batch",
        "mark_project_bootstrap_status",
        "reconcile_project_workflow_task",
        "record_stage_export",
    }
)


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name, None))
    }


def test_postgres_covers_every_legacy_public_method():
    """The cutover is only safe if no caller can hit a missing method."""

    from creative.legacy_sqlite_repository import CreativeRepository

    legacy = _public_methods(CreativeRepository)
    postgres = _public_methods(PostgresCreativeRepository)

    assert legacy - postgres == set(), "legacy methods missing from PostgreSQL"
    # The frozen SQLite implementation is the contract; it must stay complete.
    assert len(legacy) >= 136


def test_postgres_adds_only_the_expected_public_methods():
    from creative.legacy_sqlite_repository import CreativeRepository

    added = _public_methods(PostgresCreativeRepository) - _public_methods(
        CreativeRepository
    )
    assert added == set(_INTENTIONAL_ADDITIONS), sorted(
        added.symmetric_difference(_INTENTIONAL_ADDITIONS)
    )


def test_shared_method_signatures_match_except_for_optional_connections():
    """A caller written against SQLite must keep binding against PostgreSQL."""

    import inspect

    from creative.legacy_sqlite_repository import CreativeRepository

    def parameters(func: object) -> list[inspect.Parameter]:
        values = list(inspect.signature(func).parameters.values())
        if values and values[0].name in {"self", "cls"}:
            values = values[1:]
        return values

    drifted = []
    for name in sorted(_public_methods(CreativeRepository)):
        legacy = parameters(getattr(CreativeRepository, name))
        postgres = parameters(getattr(PostgresCreativeRepository, name))

        legacy_only = [p.replace(default=inspect.Parameter.empty) for p in legacy]
        postgres_only = [p.replace(default=inspect.Parameter.empty) for p in postgres]

        if legacy_only == postgres_only:
            continue
        # The only accepted difference is a trailing optional connection.
        if (
            name in _CONNECTION_AWARE
            and len(postgres) == len(legacy) + 1
            and postgres[-1].name == "connection"
            and postgres[-1].default is None
            and legacy_only == postgres_only[: len(legacy)]
        ):
            continue
        drifted.append(name)

    assert drifted == [], f"signature drift: {drifted}"
