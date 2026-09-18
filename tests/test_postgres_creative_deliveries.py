# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL coverage for stage exports, deliveries, production packages and
production status (``creative/repositories/deliveries.py``).

Prerequisite rows are inserted with raw SQL through
``isolated_system_database.transaction()`` so the suite never depends on another
domain repository.

The ``deliveries`` fixture returns the shared ``postgres_creative`` repository.
Until the coordinating agent wires :class:`DeliveriesRepository` into
``PostgresCreativeRepository`` the fixture composes the mixin locally, the same
way the scratch self-verification did; once the wired repository exposes these
methods, every test runs against ``postgres_creative`` itself. The fallback class
is built lazily so this module still imports after the mixin is wired (an eager
``class Composed(DeliveriesRepository, PostgresCreativeRepository)`` would then
be an inconsistent MRO).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from creative.postgres_repository import PostgresCreativeRepository
from creative.repositories.deliveries import DeliveriesRepository

#: Every method this suite exercises; used to detect that the mixin is wired.
_REQUIRED_METHODS: tuple[str, ...] = (
    "record_stage_export",
    "has_stage_export",
    "record_delivery",
    "find_delivery",
    "get_delivery",
    "list_deliveries",
    "mark_delivery_reconciled",
    "record_production_export",
    "find_production_export",
    "get_production_export",
    "list_production_exports",
    "mark_production_export_audited",
    "record_production_status",
    "get_production_status",
)


def _compose_unwired(database):
    """Compose the mixin onto the repository class until it is wired in."""

    composed = type(
        "UnwiredDeliveriesRepository",
        (DeliveriesRepository, PostgresCreativeRepository),
        {},
    )
    return composed(database)


@pytest.fixture
def existing_project_id(isolated_system_database) -> int:
    """A real ``creative_projects`` row so the creative foreign keys are honest."""

    with isolated_system_database.transaction() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, created_at, updated_at) "
                    "VALUES ('交付测试项目', 'original', now(), now()) RETURNING id"
                )
            ).scalar_one()
        )


@pytest.fixture
def deliveries(postgres_creative):
    """The repository under test; ``postgres_creative`` once the mixin is wired."""

    if all(hasattr(postgres_creative, name) for name in _REQUIRED_METHODS):
        return postgres_creative
    return _compose_unwired(postgres_creative.database)


def _count(isolated_system_database, table: str) -> int:
    with isolated_system_database.connection() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def delivery_values(project_id: int, delivery_id: str, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": delivery_id,
        "project_id": project_id,
        "stage": "beats",
        "resource_type": "beats",
        "resource_id": "77",
        "resource_version": 2,
        "submission_id": "direct:beats:77",
        "submission_version": 2,
        "format": "md",
        "content_fingerprint": "content-fp",
        "actor_id": "user-1",
        "path": "deliveries/beats.md",
        "is_formal": True,
        "created_at": 1_700_000_000.0,
        "stage_content_fingerprint": "stage-fp",
        "resource_status": "locked",
        "workflow_task_id": "task-1",
    }
    values.update(overrides)
    return values


def production_export_values(
    project_id: int, export_id: str, fingerprint: str, **overrides: Any
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": export_id,
        "project_id": project_id,
        "fingerprint": fingerprint,
        "contract_version": "1.0",
        "created_by": "user-1",
        "created_by_name": "User One",
        "created_at": 1_700_000_100.0,
        "episode_count": 2,
        "episode_numbers_json": [1, 2],
        "export_scope": "full",
        "project_total_episodes": 2,
        "workflow_mode": "sequential",
        "version_strategy": "current",
        "version_manifest_json": {"1": 3, "2": 4},
        "path": "production-exports/export.json",
        "size_bytes": 1234,
        "file_sha256": "sha-value",
    }
    values.update(overrides)
    return values


# --------------------------------------------------------------- stage exports


def test_record_stage_export_is_idempotent(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    assert deliveries.has_stage_export(existing_project_id, "beats", "stage-fp") is False

    deliveries.record_stage_export(existing_project_id, "beats", "md", "stage-fp")
    deliveries.record_stage_export(existing_project_id, "beats", "md", "stage-fp")

    assert _count(isolated_system_database, "creative_stage_exports") == 1
    assert deliveries.has_stage_export(existing_project_id, "beats", "stage-fp") is True


def test_has_stage_export_matches_the_whole_identity(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    deliveries.record_stage_export(existing_project_id, "beats", "md", "stage-fp")

    assert deliveries.has_stage_export(existing_project_id, "beats", "other-fp") is False
    assert deliveries.has_stage_export(existing_project_id, "script", "stage-fp") is False
    with isolated_system_database.transaction() as connection:
        other_project_id = int(
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, created_at, updated_at) "
                    "VALUES ('另一个项目', 'original', now(), now()) RETURNING id"
                )
            ).scalar_one()
        )
    assert deliveries.has_stage_export(other_project_id, "beats", "stage-fp") is False

    deliveries.record_stage_export(existing_project_id, "beats", "md", "other-fp")
    assert _count(isolated_system_database, "creative_stage_exports") == 2
    assert deliveries.has_stage_export(existing_project_id, "beats", "other-fp") is True


# ----------------------------------------------------------------- deliveries


def test_delivery_create_read_and_ordering(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    first = deliveries.record_delivery(
        delivery_values(existing_project_id, "delivery-1")
    )
    assert first["id"] == "delivery-1"
    assert first["project_id"] == str(existing_project_id)
    assert first["fingerprint"] == "content-fp"
    assert first["actor_user_id"] == "user-1"
    assert first["is_formal"] is True
    assert first["project_name"] == "交付测试项目"
    assert first["created_at"] == 1_700_000_000.0
    assert first["workflow_task_id"] == "task-1"
    assert first["stage_content_fingerprint"] == "stage-fp"
    assert first["stage_export_recorded_at"] is None
    assert "path" in first and "content_fingerprint" not in first

    second = deliveries.record_delivery(
        delivery_values(
            existing_project_id,
            "delivery-2",
            format="json",
            submission_id="direct:beats:77:json",
            content_fingerprint="other-fp",
            is_formal=False,
            created_at=1_700_000_100.0,
        )
    )
    assert second["is_formal"] is False

    assert _count(isolated_system_database, "creative_deliveries") == 2

    found = deliveries.find_delivery(
        existing_project_id, "beats", "direct:beats:77", "md", is_formal=True
    )
    assert found is not None and found["id"] == "delivery-1"
    assert (
        deliveries.find_delivery(
            existing_project_id, "beats", "direct:beats:77", "md", is_formal=False
        )
        is None
    )

    assert deliveries.get_delivery("delivery-1")["fingerprint"] == "content-fp"
    assert deliveries.get_delivery("missing") is None

    assert [item["id"] for item in deliveries.list_deliveries()] == [
        "delivery-2",
        "delivery-1",
    ]


def test_delivery_replay_returns_the_stored_row(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    values = delivery_values(existing_project_id, "delivery-1")
    first = deliveries.record_delivery(values)
    replay = deliveries.record_delivery(values)

    assert replay["id"] == first["id"]
    assert replay["created_at"] == first["created_at"]
    assert _count(isolated_system_database, "creative_deliveries") == 1


def test_delivery_other_integrity_errors_still_raise(
    deliveries, existing_project_id
) -> None:
    deliveries.record_delivery(delivery_values(existing_project_id, "delivery-1"))

    with pytest.raises(IntegrityError):
        deliveries.record_delivery(
            delivery_values(
                existing_project_id,
                "delivery-1",
                submission_id="direct:beats:other",
                content_fingerprint="other-fp",
            )
        )


def test_delivery_reconciliation_columns(
    deliveries, existing_project_id
) -> None:
    deliveries.record_delivery(delivery_values(existing_project_id, "delivery-1"))

    deliveries.mark_delivery_reconciled("delivery-1", "workflow_task_id", "task-2")
    deliveries.mark_delivery_reconciled(
        "delivery-1", "stage_export_recorded_at", 1_700_000_300.0
    )
    deliveries.mark_delivery_reconciled(
        "delivery-1", "stage_content_fingerprint", "recomputed-fp"
    )

    refreshed = deliveries.get_delivery("delivery-1")
    assert refreshed["workflow_task_id"] == "task-2"
    assert refreshed["stage_export_recorded_at"] == 1_700_000_300.0
    assert refreshed["stage_content_fingerprint"] == "recomputed-fp"

    with pytest.raises(ValueError) as excinfo:
        deliveries.mark_delivery_reconciled("delivery-1", "id", "hacked")
    assert str(excinfo.value) == "invalid delivery reconciliation column"


# ---------------------------------------------------------- production exports


def test_production_export_create_read_and_history(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    first = deliveries.record_production_export(
        production_export_values(existing_project_id, "export-1", "pkg-fp")
    )
    assert first["id"] == "export-1"
    assert first["path"] == "production-exports/export.json"
    assert first["size_bytes"] == 1234
    assert first["file_sha256"] == "sha-value"
    assert first["created_at"] == 1_700_000_100.0
    assert first["episode_numbers_json"] == [1, 2]
    assert first["version_manifest_json"] == {"1": 3, "2": 4}
    assert first["audit_recorded_at"] is None

    replay = deliveries.record_production_export(
        production_export_values(existing_project_id, "export-999", "pkg-fp")
    )
    assert replay["id"] == "export-1"
    assert _count(isolated_system_database, "creative_production_exports") == 1

    assert deliveries.find_production_export(existing_project_id, "pkg-fp")["id"] == (
        "export-1"
    )
    assert deliveries.find_production_export(existing_project_id, "missing-fp") is None
    assert deliveries.get_production_export("export-1")["file_sha256"] == "sha-value"
    assert deliveries.get_production_export("missing") is None

    deliveries.record_production_export(
        production_export_values(
            existing_project_id,
            "export-2",
            "pkg-fp-2",
            created_at=1_700_000_400.0,
            export_scope="partial",
            episode_numbers_json=[2],
            project_total_episodes=3,
        )
    )

    history = deliveries.list_production_exports(existing_project_id)
    assert history["total"] == 2
    assert history["page"] == 1
    assert history["page_size"] == 20
    assert [item["id"] for item in history["items"]] == ["export-2", "export-1"]

    newest, oldest = history["items"]
    assert newest["episode_numbers"] == [2]
    assert newest["export_scope"] == "partial"
    assert oldest["episode_numbers"] == [1, 2]
    assert oldest["export_scope"] == "full"
    assert oldest["version_manifest"] == {"1": 3, "2": 4}
    assert oldest["project_id"] == str(existing_project_id)
    assert oldest["download_url"].endswith("/api/creative/production-exports/export-1/download")
    assert oldest["audit_status"] == "pending"
    assert "path" not in oldest

    paged = deliveries.list_production_exports(existing_project_id, page=2, page_size=1)
    assert [item["id"] for item in paged["items"]] == ["export-1"]
    assert paged["total"] == 2

    deliveries.mark_production_export_audited("export-1")
    audited = deliveries.production_export_public_record(
        deliveries.get_production_export("export-1")
    )
    assert audited["audit_status"] == "recorded"


# ----------------------------------------------------------- production status


def test_production_status_upsert_per_fingerprint(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    assert deliveries.get_production_status(existing_project_id, "fp-1") is None

    stored = deliveries.record_production_status(
        existing_project_id, "fp-1", {"state": "incomplete", "issues": ["missing"]}
    )
    assert stored == {"state": "incomplete", "issues": ["missing"]}

    rewritable = deliveries.record_production_status(
        existing_project_id, "fp-1", {"state": "completed", "issues": []}
    )
    assert rewritable == {"state": "completed", "issues": []}
    assert _count(isolated_system_database, "production_status") == 1
    assert deliveries.get_production_status(existing_project_id, "fp-1") == {
        "state": "completed",
        "issues": [],
    }

    deliveries.record_production_status(existing_project_id, "fp-2", {"state": "partial"})
    assert _count(isolated_system_database, "production_status") == 2
    assert deliveries.get_production_status(existing_project_id, "fp-2") == {
        "state": "partial"
    }
    assert deliveries.get_production_status(existing_project_id, "fp-3") is None


def test_production_status_payload_is_jsonb_object(
    deliveries, existing_project_id, isolated_system_database
) -> None:
    deliveries.record_production_status(
        existing_project_id,
        "fp-json",
        {
            "schemaVersion": "1.0",
            "episodes": [{"episodeId": "ep-1", "exists": True}],
            "issues": [],
        },
    )

    with isolated_system_database.connection() as connection:
        data_type = connection.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'production_status' AND column_name = 'payload'"
            )
        ).scalar_one()
        stored = connection.execute(
            text(
                "SELECT payload FROM production_status "
                "WHERE project_id = :project_id AND fingerprint = :fingerprint"
            ),
            {"project_id": existing_project_id, "fingerprint": "fp-json"},
        ).scalar_one()
    assert data_type == "jsonb"
    assert stored["episodes"] == [{"episodeId": "ep-1", "exists": True}]
    assert deliveries.get_production_status(existing_project_id, "fp-json")["issues"] == []
