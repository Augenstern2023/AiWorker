# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Domain tests for the PostgreSQL idempotency, lease and rollback repository.

Every scenario is driven through the shared ``postgres_creative`` fixture so the
suite exercises the composed repository exactly as production composes it.
Prerequisite rows are written with raw SQL through
``isolated_system_database.transaction()`` so this module never depends on
another domain's repository.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
from sqlalchemy import text

from creative.database_schema import creative_blueprints
from creative.legacy_sqlite_repository import (
    GENERATION_AUTHORITY_SCHEMA_VERSION,
    GENERATION_OPERATION_LEASE_SECONDS,
    SIDE_EFFECT_OPERATION_LEASE_SECONDS,
    CreativeRepository,
    canonical_request_hash,
)
from creative.repositories import operations as operations_module
from system_database import SystemDatabase


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

_ROLLBACK_TABLE_ORDER = [
    "creative_projects",
    "creative_seeds",
    "creative_blueprints",
    "creative_characters",
    "creative_relationships",
    "creative_episodes",
    "creative_operation_results",
    "creative_generation_operations",
]

_DELETED_TABLES = (
    "creative_seeds",
    "creative_blueprints",
    "creative_characters",
    "creative_relationships",
    "creative_episodes",
    "creative_operation_results",
    "creative_generation_operations",
    "creative_stage_exports",
    "script_reviews",
    "script_optimizations",
    "script_optimization_drafts",
    "creative_deliveries",
    "creative_production_exports",
    "creative_generation_contexts",
    "creative_resource_heads",
    "creative_mode_migrations",
    "creative_project_deletions",
)


# ---------------------------------------------------------------------------
# fixtures and raw-SQL helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def postgres_factory(isolated_system_database: SystemDatabase):
    """A factory for independent repository instances on one isolated schema."""

    from creative.postgres_repository import PostgresCreativeRepository

    return lambda: PostgresCreativeRepository(isolated_system_database)


def insert_row(
    database: SystemDatabase,
    table: str,
    *,
    returning: str | None = "id",
    **values: Any,
) -> Any:
    """Insert one prerequisite row with raw SQL.

    ``table`` and the column names come from this test module only; the values
    are always bound parameters. ``dict``/``list`` values are rendered as JSON
    text so PostgreSQL can parse them into the ``JSONB`` column.
    """

    columns = list(values)
    binds = {
        f"v{index}": (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else value
        )
        for index, value in enumerate(values.values())
    }
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':v{index}' for index in range(len(columns)))})"
    )
    if returning is not None:
        statement += f" RETURNING {returning}"
    with database.transaction() as connection:
        result = connection.execute(text(statement), binds)
        return result.scalar_one() if returning is not None else None


def count_rows(database: SystemDatabase, table: str, project_id: int) -> int:
    with database.connection() as connection:
        return connection.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE project_id = :project_id"),
            {"project_id": project_id},
        ).scalar_one()


def create_project(
    database: SystemDatabase,
    *,
    name: str = "测试项目",
    workflow_task_id: str | None = None,
    creation_workflow_task_id: str | None = None,
    **overrides: Any,
) -> int:
    values: dict[str, Any] = {
        "name": name,
        "genre": "都市",
        "target_platform": "hg",
        "target_audience": "女性",
        "target_episode_count": 20,
        "episode_duration_seconds": 120,
        "status": "created",
        "created_by": "user-1",
        "creation_mode": "original",
        "bootstrap_status": "ready",
        "workflow_mode": "sequential",
        "workflow_task_id": workflow_task_id,
        "creation_workflow_task_id": creation_workflow_task_id,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return int(insert_row(database, "creative_projects", **values))


def create_blueprint(
    database: SystemDatabase,
    project_id: int,
    *,
    content: dict[str, Any] | None = None,
    status: str = "draft",
    workflow_task_id: str | None = None,
    candidate_group_id: str = "group-1",
    version: int = 1,
) -> int:
    return int(
        insert_row(
            database,
            "creative_blueprints",
            project_id=project_id,
            candidate_group_id=candidate_group_id,
            version=version,
            status=status,
            content_json=content if content is not None else {"title": "方向一"},
            risk_score=0.5,
            risk_level="low",
            difference_note="",
            source_refs_json={},
            generation_meta_json={},
            workflow_task_id=workflow_task_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def create_episode(
    database: SystemDatabase,
    project_id: int,
    *,
    stage: str = "beats",
    episode_number: int = 1,
    batch_id: str = "batch-1",
    version: int = 1,
    status: str = "draft",
    content: dict[str, Any] | None = None,
    workflow_task_id: str | None = None,
) -> int:
    return int(
        insert_row(
            database,
            "creative_episodes",
            project_id=project_id,
            stage=stage,
            episode_number=episode_number,
            batch_id=batch_id,
            version=version,
            status=status,
            content_json=content if content is not None else {"scenes": []},
            upstream_refs_json={},
            generation_meta_json={},
            workflow_task_id=workflow_task_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def create_character(
    database: SystemDatabase,
    project_id: int,
    *,
    batch_id: str = "chars-1",
    character_key: str = "lead",
    workflow_task_id: str | None = None,
    content: dict[str, Any] | None = None,
) -> int:
    return int(
        insert_row(
            database,
            "creative_characters",
            project_id=project_id,
            batch_id=batch_id,
            version=1,
            character_key=character_key,
            status="draft",
            content_json=content if content is not None else {"name": "主角"},
            source_refs_json={},
            generation_meta_json={},
            workflow_task_id=workflow_task_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def create_operation_result(
    database: SystemDatabase,
    project_id: int,
    *,
    operation_id: str,
    stage: str = "brief",
    request_hash: str = "hash",
    resource_ref: dict[str, Any] | None = None,
) -> None:
    insert_row(
        database,
        "creative_operation_results",
        returning=None,
        operation_id=operation_id,
        project_id=project_id,
        stage=stage,
        request_hash=request_hash,
        resource_ref_json=resource_ref if resource_ref is not None else {},
        created_at=NOW,
    )


def run_parallel(tasks: list[Callable[[], Any]]) -> dict[int, tuple[str, Any]]:
    """Run every task on its own connection after one shared barrier."""

    outcomes: dict[int, tuple[str, Any]] = {}
    barrier = threading.Barrier(len(tasks))

    def runner(index: int) -> None:
        barrier.wait()
        try:
            outcomes[index] = ("ok", tasks[index]())
        except BaseException as error:  # noqa: BLE001 - the test asserts on it
            outcomes[index] = ("error", error)

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        list(pool.map(runner, range(len(tasks))))
    return outcomes


def expire_side_effect_lease(database: SystemDatabase, operation_id: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_operation_results SET resource_ref_json = "
                "jsonb_set(resource_ref_json, '{lease_expires_at}', "
                "to_jsonb(extract(epoch from now()) - 60)) "
                "WHERE operation_id = :operation_id"
            ),
            {"operation_id": operation_id},
        )


def expire_generation_lease(database: SystemDatabase, operation_id: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_generation_operations "
                "SET updated_at = now() - interval '1 hour' "
                "WHERE operation_id = :operation_id"
            ),
            {"operation_id": operation_id},
        )


def read_generation_row(database: SystemDatabase, operation_id: str) -> dict[str, Any]:
    with database.connection() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT status, owner_id, batch_id, result_json "
                    "FROM creative_generation_operations WHERE operation_id = :id"
                ),
                {"id": operation_id},
            )
            .mappings()
            .one()
        )


# ---------------------------------------------------------------------------
# constants and request hashing
# ---------------------------------------------------------------------------


def test_operation_lease_constants_keep_the_legacy_values():
    assert SIDE_EFFECT_OPERATION_LEASE_SECONDS == 30.0
    assert GENERATION_OPERATION_LEASE_SECONDS == 30.0
    assert (
        operations_module.SIDE_EFFECT_OPERATION_LEASE_SECONDS
        is SIDE_EFFECT_OPERATION_LEASE_SECONDS
    )
    assert (
        operations_module.GENERATION_OPERATION_LEASE_SECONDS
        is GENERATION_OPERATION_LEASE_SECONDS
    )


def test_operation_request_hash_is_stable_across_key_order():
    payload_a = {
        "project_id": 7,
        "stage": "brief",
        "scope": {"b": 2, "a": [1, {"z": 1, "y": 2}]},
        "payload": {"items": [{"b": 1, "a": 2}], "job_id": "job-1"},
    }
    payload_b = {
        "payload": {"job_id": "job-1", "items": [{"a": 2, "b": 1}]},
        "scope": {"a": [1, {"y": 2, "z": 1}], "b": 2},
        "stage": "brief",
        "project_id": 7,
    }

    first = operations_module.OperationsRepository._operation_request_hash(**payload_a)
    second = operations_module.OperationsRepository._operation_request_hash(**payload_b)

    assert first == second
    assert first == CreativeRepository._operation_request_hash(**payload_a)
    assert first == canonical_request_hash(
        {
            "project_id": 7,
            "stage": "brief",
            "scope": {"a": [1, {"y": 2, "z": 1}], "b": 2},
            "payload": {"items": [{"a": 2, "b": 1}], "job_id": "job-1"},
        }
    )


# ---------------------------------------------------------------------------
# idempotent results
# ---------------------------------------------------------------------------


def test_record_operation_result_replays_the_stored_result(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    reference = {"resource_type": "brief", "resource_ids": [11, 12]}

    first = postgres_creative.record_operation_result(
        operation_id="op-1",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        resource_ref=reference,
    )
    second = postgres_creative.record_operation_result(
        operation_id="op-1",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        resource_ref={"resource_type": "other"},
    )

    assert first == reference
    assert second == reference
    assert count_rows(isolated_system_database, "creative_operation_results", project_id) == 1


def test_record_operation_result_rejects_a_reused_key(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    postgres_creative.record_operation_result(
        operation_id="op-1",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        resource_ref={"resource_type": "brief"},
    )

    with pytest.raises(ValueError) as error:
        postgres_creative.record_operation_result(
            operation_id="op-1",
            project_id=project_id,
            stage="brief",
            request_hash="hash-2",
            resource_ref={"resource_type": "brief"},
        )
    assert str(error.value) == "IDEMPOTENCY_KEY_REUSED"


def test_hydrate_operation_result_reads_jsonb_and_missing_keys(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    blueprint_id = create_blueprint(
        isolated_system_database, project_id, content={"title": "方向 A"}
    )
    postgres_creative.record_operation_result(
        operation_id="op-brief",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        resource_ref={"resource_type": "brief", "resource_ids": [blueprint_id]},
    )

    hydrated = postgres_creative.hydrate_operation_result("op-brief")

    assert isinstance(hydrated, list) and len(hydrated) == 1
    assert hydrated[0]["id"] == blueprint_id
    assert hydrated[0]["content"] == {"title": "方向 A"}
    assert "content_json" not in hydrated[0]
    assert isinstance(hydrated[0]["created_at"], float)
    assert hydrated[0]["source_refs"] == {}
    assert postgres_creative.hydrate_operation_result("missing-operation") is None


def test_hydrate_operation_result_restores_the_operation_revision(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    blueprint_id = create_blueprint(isolated_system_database, project_id)
    postgres_creative.record_operation_result(
        operation_id="op-revision",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        resource_ref={
            "resource_type": "brief",
            "resource_id": blueprint_id,
            "revision": 4,
        },
    )

    hydrated = postgres_creative.hydrate_operation_result("op-revision")

    assert hydrated["id"] == blueprint_id
    assert hydrated["revision"] == 4


# ---------------------------------------------------------------------------
# side-effect reservations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nested", [[1, 2], [{"created_at": NOW}]])
def test_side_effect_reservation_finalize_and_replay(
    postgres_creative, isolated_system_database, nested
):
    project_id = create_project(isolated_system_database)

    reserved = postgres_creative.reserve_side_effect_operation(
        operation_id="op-se",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        owner_id="owner-a",
    )
    assert reserved == {"state": "reserved", "owner_id": "owner-a"}

    finalized = postgres_creative.finalize_side_effect_operation(
        operation_id="op-se", owner_id="owner-a", result={"value": 1, "nested": nested}
    )
    assert finalized == {"value": 1, "nested": nested}

    replay = postgres_creative.reserve_side_effect_operation(
        operation_id="op-se",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        owner_id="owner-b",
    )
    assert replay == {
        "state": "complete",
        "result": {"value": 1, "nested": json.loads(json.dumps(nested, default=str))},
    }

    with pytest.raises(ValueError) as error:
        postgres_creative.reserve_side_effect_operation(
            operation_id="op-se",
            project_id=project_id,
            stage="brief",
            request_hash="hash-2",
            owner_id="owner-b",
        )
    assert str(error.value) == "IDEMPOTENCY_KEY_REUSED"


def test_side_effect_reservation_requires_the_owner(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    postgres_creative.reserve_side_effect_operation(
        operation_id="op-se",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        owner_id="owner-a",
    )

    with pytest.raises(RuntimeError) as other_owner:
        postgres_creative.finalize_side_effect_operation(
            operation_id="op-se", owner_id="owner-b", result={"value": 1}
        )
    assert str(other_owner.value) == "OPERATION_IN_PROGRESS"

    with pytest.raises(RuntimeError) as missing:
        postgres_creative.finalize_side_effect_operation(
            operation_id="missing", owner_id="owner-a", result={"value": 1}
        )
    assert str(missing.value) == "operation reservation is missing"

    # A finalized operation replays its own result instead of failing.
    finalized = postgres_creative.finalize_side_effect_operation(
        operation_id="op-se", owner_id="owner-a", result={"value": 1}
    )
    assert finalized == {"value": 1}
    again = postgres_creative.finalize_side_effect_operation(
        operation_id="op-se", owner_id="owner-b", result={"value": 2}
    )
    assert again == {"value": 1}


def test_side_effect_reservation_lease_renewal_expiry_and_abandon(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    postgres_creative.reserve_side_effect_operation(
        operation_id="op-se",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        owner_id="owner-a",
    )

    assert postgres_creative.renew_side_effect_operation(
        operation_id="op-se", owner_id="owner-b"
    ) is False
    assert postgres_creative.renew_side_effect_operation(
        operation_id="op-se", owner_id="owner-a"
    ) is True

    # A live lease keeps the reservation: a second owner cannot steal it.
    with pytest.raises(RuntimeError) as in_progress:
        postgres_creative.reserve_side_effect_operation(
            operation_id="op-se",
            project_id=project_id,
            stage="brief",
            request_hash="hash-1",
            owner_id="owner-b",
        )
    assert str(in_progress.value) == "OPERATION_IN_PROGRESS"

    # An expired lease is reclaimable and reports the previous owner.
    expire_side_effect_lease(isolated_system_database, "op-se")
    recovered = postgres_creative.reserve_side_effect_operation(
        operation_id="op-se",
        project_id=project_id,
        stage="brief",
        request_hash="hash-1",
        owner_id="owner-b",
    )
    assert recovered == {"state": "reserved", "owner_id": "owner-b", "recovered": True}

    stored = postgres_creative.hydrate_operation_result("op-se")
    assert stored["owner_id"] == "owner-b"
    assert stored["recovered_from_owner_id"] == "owner-a"
    assert stored["_operation_state"] == "pending"

    # Abandon removes only the matching pending reservation.
    postgres_creative.abandon_side_effect_operation(
        operation_id="op-se", owner_id="owner-a"
    )
    assert postgres_creative.hydrate_operation_result("op-se") is not None
    postgres_creative.abandon_side_effect_operation(
        operation_id="op-se", owner_id="owner-b"
    )
    assert postgres_creative.hydrate_operation_result("op-se") is None
    postgres_creative.abandon_side_effect_operation(
        operation_id="op-se", owner_id="owner-b"
    )


def test_side_effect_reservation_lease_falls_back_to_created_at(
    postgres_creative, isolated_system_database
):
    """A pending row without ``lease_expires_at`` uses ``created_at`` + lease."""

    project_id = create_project(isolated_system_database)
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="op-fallback",
        resource_ref={"_operation_state": "pending", "owner_id": "owner-a"},
    )
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_operation_results SET created_at = now() "
                "WHERE operation_id = 'op-fallback'"
            )
        )

    with pytest.raises(RuntimeError) as live:
        postgres_creative.reserve_side_effect_operation(
            operation_id="op-fallback",
            project_id=project_id,
            stage="brief",
            request_hash="hash",
            owner_id="owner-b",
        )
    assert str(live.value) == "OPERATION_IN_PROGRESS"

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_operation_results SET created_at = "
                "now() - interval '1 hour' WHERE operation_id = 'op-fallback'"
            )
        )
    recovered = postgres_creative.reserve_side_effect_operation(
        operation_id="op-fallback",
        project_id=project_id,
        stage="brief",
        request_hash="hash",
        owner_id="owner-b",
    )
    assert recovered == {"state": "reserved", "owner_id": "owner-b", "recovered": True}
    assert (
        postgres_creative.hydrate_operation_result("op-fallback")[
            "recovered_from_owner_id"
        ]
        == "owner-a"
    )


def test_concurrent_side_effect_reservation_has_one_winner(
    postgres_creative, postgres_factory, isolated_system_database
):
    project_id = create_project(isolated_system_database)

    def reserve(owner_id: str):
        repository = postgres_factory()
        return repository.reserve_side_effect_operation(
            operation_id="op-race",
            project_id=project_id,
            stage="brief",
            request_hash="hash",
            owner_id=owner_id,
        )

    outcomes = run_parallel([lambda: reserve("owner-a"), lambda: reserve("owner-b")])

    winners = [outcome[1] for outcome in outcomes.values() if outcome[0] == "ok"]
    errors = [str(outcome[1]) for outcome in outcomes.values() if outcome[0] == "error"]
    assert len(winners) == 1 and winners[0]["state"] == "reserved"
    assert errors == ["OPERATION_IN_PROGRESS"]
    stored = postgres_creative.hydrate_operation_result("op-race")
    assert stored["owner_id"] == winners[0]["owner_id"]
    assert (
        count_rows(
            isolated_system_database, "creative_operation_results", project_id
        )
        == 1
    )


def test_concurrent_idempotent_operation_executes_create_once(
    postgres_factory, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    create_calls: list[int] = []
    lock = threading.Lock()

    def run() -> Any:
        repository = postgres_factory()

        def create(connection):
            with lock:
                create_calls.append(1)
            blueprint_id = int(
                connection.execute(
                    creative_blueprints.insert()
                    .values(
                        project_id=project_id,
                        candidate_group_id="group-race",
                        content_json={"title": "won"},
                        source_refs_json={},
                        generation_meta_json={},
                        created_at=NOW,
                        updated_at=NOW,
                    )
                    .returning(creative_blueprints.c.id)
                ).scalar_one()
            )
            return {"resource_type": "brief", "resource_ids": [blueprint_id]}

        with repository.transaction_scope() as connection:
            return repository._run_idempotent_operation(
                connection,
                operation_id="op-create-once",
                project_id=project_id,
                stage="brief",
                request_hash="hash",
                create=create,
            )

    outcomes = run_parallel([run, run])

    assert all(outcome[0] == "ok" for outcome in outcomes.values())
    assert create_calls == [1]
    created_ids = {
        outcome[1][0]["id"] for outcome in outcomes.values() if outcome[0] == "ok"
    }
    assert len(created_ids) == 1
    assert count_rows(isolated_system_database, "creative_blueprints", project_id) == 1
    assert (
        count_rows(
            isolated_system_database, "creative_operation_results", project_id
        )
        == 1
    )


# ---------------------------------------------------------------------------
# generation reservations
# ---------------------------------------------------------------------------


def test_reserve_generation_operation_creates_and_replays(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)

    first = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )
    assert first["status"] == "active"
    assert first["result"] == {}
    assert len(first["batch_id"]) == 32

    replay = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )
    assert replay == first

    with pytest.raises(ValueError) as error:
        postgres_creative.reserve_generation_operation(
            operation_id="gen-1",
            project_id=project_id,
            stage="outline",
            request_hash="other",
            owner_id="owner-a",
        )
    assert str(error.value) == "IDEMPOTENCY_KEY_REUSED"


def test_generation_reservation_lease_expiry_and_predecessor_takeover(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    reserved = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )

    with pytest.raises(RuntimeError) as live:
        postgres_creative.reserve_generation_operation(
            operation_id="gen-1",
            project_id=project_id,
            stage="outline",
            request_hash="hash",
            owner_id="owner-b",
            predecessor_owner_id="owner-a",
        )
    assert str(live.value) == "OPERATION_IN_PROGRESS"

    expire_generation_lease(isolated_system_database, "gen-1")

    with pytest.raises(RuntimeError) as no_predecessor:
        postgres_creative.reserve_generation_operation(
            operation_id="gen-1",
            project_id=project_id,
            stage="outline",
            request_hash="hash",
            owner_id="owner-b",
        )
    assert str(no_predecessor.value) == "OPERATION_IN_PROGRESS"

    taken_over = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-b",
        predecessor_owner_id="owner-a",
    )
    assert taken_over == {
        "batch_id": reserved["batch_id"],
        "status": "active",
        "result": {},
    }
    stored = read_generation_row(isolated_system_database, "gen-1")
    assert stored["owner_id"] == "owner-b"
    assert stored["status"] == "active"


def test_generation_operation_renew_fail_and_reactivate(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )

    assert postgres_creative.renew_generation_operation(
        operation_id="gen-1", request_hash="hash", owner_id="owner-b"
    ) is False
    assert postgres_creative.renew_generation_operation(
        operation_id="gen-1", request_hash="other", owner_id="owner-a"
    ) is False
    assert postgres_creative.renew_generation_operation(
        operation_id="gen-1", request_hash="hash", owner_id="owner-a"
    ) is True

    assert postgres_creative.fail_generation_operation(
        operation_id="gen-1", request_hash="hash", owner_id="owner-a"
    ) is True
    assert postgres_creative.fail_generation_operation(
        operation_id="gen-1", request_hash="hash", owner_id="owner-a"
    ) is False
    assert postgres_creative.renew_generation_operation(
        operation_id="gen-1", request_hash="hash", owner_id="owner-a"
    ) is False

    with pytest.raises(RuntimeError) as foreign_owner:
        postgres_creative.reserve_generation_operation(
            operation_id="gen-1",
            project_id=project_id,
            stage="outline",
            request_hash="hash",
            owner_id="owner-b",
        )
    assert str(foreign_owner.value) == "OPERATION_IN_PROGRESS"

    reactivated = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )
    assert reactivated["status"] == "active"
    assert reactivated["batch_id"] == read_generation_row(
        isolated_system_database, "gen-1"
    )["batch_id"]


def test_generation_reservation_validation_failure_paths(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    reserved = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )

    postgres_creative.validate_generation_reservation(
        operation_id="gen-1",
        request_hash="hash",
        batch_id=reserved["batch_id"],
        owner_id="owner-a",
    )

    for operation_id, kwargs in (
        (
            "gen-1",
            {
                "request_hash": "other",
                "batch_id": reserved["batch_id"],
                "owner_id": "owner-a",
            },
        ),
        (
            "gen-1",
            {"request_hash": "hash", "batch_id": "other", "owner_id": "owner-a"},
        ),
        (
            "gen-1",
            {
                "request_hash": "hash",
                "batch_id": reserved["batch_id"],
                "owner_id": "owner-b",
            },
        ),
        (
            "missing",
            {
                "request_hash": "hash",
                "batch_id": reserved["batch_id"],
                "owner_id": "owner-a",
            },
        ),
    ):
        with pytest.raises(ValueError) as error:
            postgres_creative.validate_generation_reservation(
                operation_id=operation_id, **kwargs
            )
        assert str(error.value) == "generation operation reservation changed"

    with postgres_creative.transaction_scope() as connection:
        postgres_creative._validate_generation_reservation_conn(
            connection,
            operation_id="gen-1",
            request_hash="hash",
            batch_id=reserved["batch_id"],
            owner_id="owner-a",
        )
        postgres_creative.fail_generation_operation(
            operation_id="gen-1", request_hash="hash", owner_id="owner-a"
        )
        with pytest.raises(ValueError) as inactive:
            postgres_creative._validate_generation_reservation_conn(
                connection,
                operation_id="gen-1",
                request_hash="hash",
                batch_id=reserved["batch_id"],
                owner_id="owner-a",
            )
        assert str(inactive.value) == "generation operation reservation changed"


def test_complete_generation_operation_validates_the_reservation(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    reserved = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )

    with pytest.raises(ValueError) as wrong_batch:
        postgres_creative.complete_generation_operation(
            operation_id="gen-1",
            request_hash="hash",
            batch_id="other",
            result={"episodes": [1]},
        )
    assert str(wrong_batch.value) == "generation operation reservation changed"

    with pytest.raises(ValueError) as wrong_owner:
        postgres_creative.complete_generation_operation(
            operation_id="gen-1",
            request_hash="hash",
            batch_id=reserved["batch_id"],
            result={"episodes": [1]},
            owner_id="owner-b",
        )
    assert str(wrong_owner.value) == "generation operation reservation changed"

    with pytest.raises(ValueError) as missing:
        postgres_creative.complete_generation_operation(
            operation_id="missing",
            request_hash="hash",
            batch_id="other",
            result={},
        )
    assert str(missing.value) == "generation operation reservation changed"

    postgres_creative.complete_generation_operation(
        operation_id="gen-1",
        request_hash="hash",
        batch_id=reserved["batch_id"],
        result={"episodes": [1, 2], "series_overview": {"title": "全剧"}},
    )
    stored = read_generation_row(isolated_system_database, "gen-1")
    assert stored["status"] == "complete"
    assert stored["result_json"] == {
        "episodes": [1, 2],
        "series_overview": {"title": "全剧"},
    }

    replay = postgres_creative.reserve_generation_operation(
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner-a",
    )
    assert replay["status"] == "complete"
    assert replay["result"] == {"episodes": [1, 2], "series_overview": {"title": "全剧"}}

    # The public validator only compares the reservation triple (legacy parity);
    # the connection-scoped variant also requires an active status.
    postgres_creative.validate_generation_reservation(
        operation_id="gen-1",
        request_hash="hash",
        batch_id=reserved["batch_id"],
        owner_id="owner-a",
    )
    with postgres_creative.transaction_scope() as connection:
        with pytest.raises(ValueError) as inactive:
            postgres_creative._validate_generation_reservation_conn(
                connection,
                operation_id="gen-1",
                request_hash="hash",
                batch_id=reserved["batch_id"],
                owner_id="owner-a",
            )
    assert str(inactive.value) == "generation operation reservation changed"


def test_concurrent_generation_reservation_has_one_owner(
    postgres_factory, isolated_system_database
):
    project_id = create_project(isolated_system_database)

    def reserve(owner_id: str):
        return postgres_factory().reserve_generation_operation(
            operation_id="gen-race",
            project_id=project_id,
            stage="outline",
            request_hash="hash",
            owner_id=owner_id,
        )

    outcomes = run_parallel([lambda: reserve("owner-a"), lambda: reserve("owner-b")])

    winners = [outcome[1] for outcome in outcomes.values() if outcome[0] == "ok"]
    errors = [outcome[1] for outcome in outcomes.values() if outcome[0] == "error"]
    assert len(winners) == 1 and winners[0]["status"] == "active"
    assert len(errors) == 1 and str(errors[0]) == "OPERATION_IN_PROGRESS"
    with isolated_system_database.connection() as connection:
        rows = connection.execute(
            text(
                "SELECT owner_id, status FROM creative_generation_operations "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project_id},
        ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["owner_id"] in {"owner-a", "owner-b"}


# ---------------------------------------------------------------------------
# generation authority envelopes
# ---------------------------------------------------------------------------


def test_generation_authority_envelope_matches_and_detects_changes(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)

    envelope = postgres_creative.generation_authority_envelope(project_id)

    assert envelope == {
        "project_id": project_id,
        "revision": 0,
        "brief": {"id": None, "fingerprint": None},
        "characters": {"batch_id": None, "fingerprint": None},
        "outline": {"batch_id": None, "fingerprint": None},
        "locked_beats": [],
        "authority_schema_version": GENERATION_AUTHORITY_SCHEMA_VERSION,
    }
    assert "episode_number" not in envelope
    assert "stage" not in envelope
    assert postgres_creative.generation_authority_envelope(project_id) == envelope

    # Matching path: the stored receipt still describes the project, and an
    # absent receipt is always accepted.
    postgres_creative.assert_generation_authority(project_id, envelope)
    with postgres_creative.transaction_scope() as connection:
        postgres_creative._assert_generation_authority_conn(connection, project_id, None)
        postgres_creative._assert_generation_authority_conn(
            connection, project_id, envelope
        )

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET revision = revision + 1 "
                "WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )
    with pytest.raises(ValueError) as changed:
        postgres_creative.assert_generation_authority(project_id, envelope)
    assert str(changed.value) == "server source authority changed"

    with postgres_creative.transaction_scope() as connection:
        with pytest.raises(ValueError) as changed_conn:
            postgres_creative._assert_generation_authority_conn(
                connection, project_id, envelope
            )
    assert str(changed_conn.value) == "server source authority changed"


def test_generation_authority_envelope_tracks_the_locked_brief_and_scope(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    blueprint_id = create_blueprint(
        isolated_system_database, project_id, content={"title": "锁定方向"}
    )
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET locked_brief_id = :blueprint_id "
                "WHERE id = :project_id"
            ),
            {"blueprint_id": blueprint_id, "project_id": project_id},
        )

    envelope = postgres_creative.generation_authority_envelope(project_id)
    assert envelope["brief"] == {
        "id": blueprint_id,
        "fingerprint": canonical_request_hash({"title": "锁定方向"}),
    }

    scoped = postgres_creative.generation_authority_envelope(
        project_id, 1, stage="script"
    )
    assert scoped["episode_number"] == 1
    assert scoped["stage"] == "script"
    assert scoped["brief"] == envelope["brief"]
    assert scoped["locked_beats"] == []
    postgres_creative.assert_generation_authority(project_id, scoped)

    # Reworking the locked brief invalidates every receipt that captured it.
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_blueprints SET content_json = "
                "'{\"title\": \"改了\"}'::jsonb WHERE id = :blueprint_id"
            ),
            {"blueprint_id": blueprint_id},
        )
    for stale in (envelope, scoped):
        with pytest.raises(ValueError) as changed:
            postgres_creative.assert_generation_authority(project_id, stale)
        assert str(changed.value) == "server source authority changed"


def test_generation_authority_envelope_rejects_invalid_scope(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)

    with pytest.raises(ValueError) as invalid_stage:
        postgres_creative.generation_authority_envelope(
            project_id, 1, stage="unknown"
        )
    assert str(invalid_stage.value) == "invalid generation authority stage"

    with pytest.raises(ValueError) as missing:
        postgres_creative.generation_authority_envelope(project_id + 999)
    assert str(missing.value) == "创作项目不存在"

    envelope = postgres_creative.generation_authority_envelope(project_id)
    # An episode-scoped receipt without a stage used to imply a Beats scope that
    # was unsafe, so it is rejected instead of hashed.
    stage_less = dict(envelope, episode_number=1)
    with pytest.raises(ValueError) as no_stage:
        postgres_creative.assert_generation_authority(project_id, stage_less)
    assert str(no_stage.value) == "server source authority stage is required"


def test_generation_authority_accepts_the_pre_upgrade_outline_fingerprint(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    outline_content = {
        "series_overview": {"title": "总览"},
        "episodes": [{"episode_number": 1, "summary": "开篇"}],
    }
    create_episode(
        isolated_system_database,
        project_id,
        stage="outline",
        batch_id="outline-1",
        episode_number=1,
        content=outline_content,
    )
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET outline_batch_id = 'outline-1' "
                "WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )

    current = postgres_creative.generation_authority_envelope(project_id)
    # The canonical envelope drops overview-only metadata from the outline.
    assert current["outline"]["fingerprint"] == canonical_request_hash(
        [{"episodes": [{"episode_number": 1, "summary": "开篇"}]}]
    )

    legacy = json.loads(json.dumps(current))
    legacy.pop("authority_schema_version")
    legacy["outline"]["fingerprint"] = canonical_request_hash([outline_content])

    # A pre-upgrade envelope is accepted only with a known historical outline
    # representation of the same content.
    postgres_creative.assert_generation_authority(project_id, legacy)

    legacy["outline"]["fingerprint"] = canonical_request_hash(
        [{"episodes": [{"episode_number": 2, "summary": "别的"}]}]
    )
    with pytest.raises(ValueError) as changed:
        postgres_creative.assert_generation_authority(project_id, legacy)
    assert str(changed.value) == "server source authority changed"


# ---------------------------------------------------------------------------
# stage locks
# ---------------------------------------------------------------------------


def test_has_locked_stage_resource(postgres_creative, isolated_system_database):
    project_id = create_project(isolated_system_database)

    def locked(stage: str, episode_number: int | None = None) -> bool:
        return postgres_creative.has_locked_stage_resource(
            project_id, stage, episode_number=episode_number
        )

    assert locked("characters") is False
    assert locked("outline") is False
    assert locked("beats", 1) is False
    assert locked("beats") is False
    assert locked("unknown") is False
    assert locked("unknown", 1) is False

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET character_batch_id = 'chars-1', "
                "outline_batch_id = 'outline-1' WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )
    assert locked("characters") is True
    assert locked("outline") is True

    create_episode(
        isolated_system_database,
        project_id,
        stage="beats",
        episode_number=1,
        status="locked",
    )
    create_episode(
        isolated_system_database,
        project_id,
        stage="script",
        episode_number=1,
        status="draft",
    )
    assert locked("beats", 1) is True
    assert locked("script", 1) is False
    assert locked("beats", 2) is False

    create_episode(
        isolated_system_database,
        project_id,
        stage="script",
        episode_number=1,
        version=2,
        status="approved",
    )
    assert locked("script", 1) is True

    # An unknown project is not a locked resource, exactly like the legacy
    # ``bool(row and row["resource_id"])`` check.
    assert postgres_creative.has_locked_stage_resource(project_id + 999, "characters") is False


# ---------------------------------------------------------------------------
# Writer rollback export
# ---------------------------------------------------------------------------


def test_writer_rollback_export_is_ordered_and_stable(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    create_blueprint(isolated_system_database, project_id)
    create_blueprint(
        isolated_system_database, project_id, status="superseded", version=2
    )
    create_episode(isolated_system_database, project_id, episode_number=2)
    create_episode(isolated_system_database, project_id, episode_number=1)
    create_operation_result(isolated_system_database, project_id, operation_id="op-1")

    state = postgres_creative.export_writer_rollback_state(project_id)

    assert list(state["tables"]) == _ROLLBACK_TABLE_ORDER
    assert state["project_id"] == project_id
    # Rows are ordered by ``id``: the legacy ``ORDER BY rowid``.
    blueprint_ids = [row["id"] for row in state["tables"]["creative_blueprints"]]
    assert blueprint_ids == sorted(blueprint_ids)
    assert len(blueprint_ids) == 1
    assert state["tables"]["creative_blueprints"][0]["status"] == "draft"
    # Legacy ``ORDER BY rowid`` is insertion order, so episode 2 (inserted
    # first) precedes episode 1.
    episode_numbers = [
        row["episode_number"] for row in state["tables"]["creative_episodes"]
    ]
    assert episode_numbers == [2, 1]
    assert state["tables"]["creative_projects"][0]["name"] == "测试项目"
    assert isinstance(state["tables"]["creative_projects"][0]["created_at"], float)
    assert state["tables"]["creative_operation_results"][0]["operation_id"] == "op-1"

    # The digest is a pure function of the exported payload.
    assert state["fingerprint"] == canonical_request_hash(
        {"project_id": state["project_id"], "tables": state["tables"]}
    )
    assert (
        postgres_creative.writer_rollback_fingerprint(project_id)
        == state["fingerprint"]
    )
    assert (
        postgres_creative.writer_rollback_fingerprint(project_id)
        == postgres_creative.writer_rollback_fingerprint(project_id)
    )


def test_writer_rollback_fingerprint_tracks_row_changes(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    create_blueprint(isolated_system_database, project_id)
    before = postgres_creative.writer_rollback_fingerprint(project_id)

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET name = '改名', revision = revision + 1, "
                "updated_at = created_at + interval '1 hour' WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )

    assert postgres_creative.writer_rollback_fingerprint(project_id) != before


def test_writer_project_external_use_state_splits_owned_rows(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    owned_blueprint = create_blueprint(
        isolated_system_database, project_id, candidate_group_id="owned"
    )
    unowned_blueprint = create_blueprint(
        isolated_system_database, project_id, candidate_group_id="unowned"
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:1",
        resource_ref={"resource_type": "brief", "resource_ids": [owned_blueprint]},
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-2:1",
        resource_ref={"resource_type": "brief", "resource_ids": [unowned_blueprint]},
    )

    fingerprint = postgres_creative.writer_project_external_use_fingerprint(
        project_id, "action-1"
    )
    assert fingerprint == postgres_creative.writer_project_external_use_fingerprint(
        project_id, "action-1"
    )

    with postgres_creative.transaction_scope() as connection:
        state = postgres_creative._writer_project_external_use_state_conn(
            connection, project_id, "action-1"
        )
    assert [
        row["id"] for row in state["unowned_resources"]["creative_blueprints"]
    ] == [unowned_blueprint]
    assert [
        row["operation_id"]
        for row in state["unowned_operations"]["creative_operation_results"]
    ] == ["action-2:1"]
    assert state["project_metadata"]["name"] == "测试项目"
    assert state["fingerprint"] == canonical_request_hash(
        {
            "project_id": state["project_id"],
            "project_metadata": state["project_metadata"],
            "unowned_resources": state["unowned_resources"],
            "unowned_operations": state["unowned_operations"],
            "external_tables": state["external_tables"],
        }
    )

    # An action-owned reference claims a previously unowned row, so the
    # external-use fingerprint changes: the row is no longer external.
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:2",
        resource_ref={"resource_type": "brief", "resource_ids": [unowned_blueprint]},
    )
    claimed = postgres_creative.writer_project_external_use_fingerprint(
        project_id, "action-1"
    )
    assert claimed != fingerprint
    with postgres_creative.transaction_scope() as connection:
        claimed_state = postgres_creative._writer_project_external_use_state_conn(
            connection, project_id, "action-1"
        )
    assert claimed_state["unowned_resources"]["creative_blueprints"] == []

    # A row that belongs to another action stays external and changes it again.
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-9:1",
        resource_ref={"resource_type": "episode", "resource_ids": []},
    )
    assert (
        postgres_creative.writer_project_external_use_fingerprint(
            project_id, "action-1"
        )
        != claimed
    )


def test_writer_project_deletion_fingerprint_tracks_dependent_rows(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    create_blueprint(isolated_system_database, project_id)
    before = postgres_creative.writer_project_deletion_fingerprint(project_id)
    assert before == postgres_creative.writer_project_deletion_fingerprint(project_id)

    postgres_creative.queue_video_parameter_recommendation(
        project_id, created_by="writer-1"
    )
    with_video_parameters = postgres_creative.writer_project_deletion_fingerprint(
        project_id
    )
    assert with_video_parameters != before

    create_episode(isolated_system_database, project_id)
    assert (
        postgres_creative.writer_project_deletion_fingerprint(project_id)
        != with_video_parameters
    )


def test_writer_action_workflow_task_ids(
    postgres_creative, isolated_system_database
):
    project_id = create_project(
        isolated_system_database,
        workflow_task_id="wt-project",
        creation_workflow_task_id="wt-creation",
    )
    blueprint_id = create_blueprint(
        isolated_system_database, project_id, workflow_task_id="wt-brief"
    )
    create_character(
        isolated_system_database,
        project_id,
        batch_id="chars-1",
        workflow_task_id="wt-characters",
    )
    episode_id = create_episode(
        isolated_system_database,
        project_id,
        stage="outline",
        batch_id="outline-1",
        episode_number=1,
        workflow_task_id="wt-outline",
    )
    beats_id = create_episode(
        isolated_system_database,
        project_id,
        stage="beats",
        episode_number=1,
        workflow_task_id="wt-beats",
    )

    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:1",
        resource_ref={"resource_type": "brief", "resource_ids": [blueprint_id]},
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:2",
        resource_ref={"resource_type": "characters", "resource_id": f"{project_id}:chars-1"},
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:3",
        resource_ref={"resource_type": "outline", "resource_ids": [f"{project_id}:outline-1"]},
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:4",
        resource_ref={"resource_type": "beats", "resource_ids": [beats_id]},
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-9:1",
        resource_ref={"resource_type": "episode", "resource_ids": [episode_id]},
    )

    assert postgres_creative.writer_action_workflow_task_ids(
        project_id, "action-1"
    ) == {"wt-project", "wt-creation", "wt-brief", "wt-characters", "wt-outline", "wt-beats"}
    assert postgres_creative.writer_action_workflow_task_ids(
        project_id, "action-9"
    ) == {"wt-project", "wt-creation", "wt-outline"}

    with pytest.raises(ValueError) as error:
        postgres_creative.writer_action_workflow_task_ids(project_id + 999, "action-1")
    assert str(error.value) == "创作项目不存在"


# ---------------------------------------------------------------------------
# Writer rollback restore
# ---------------------------------------------------------------------------


def test_restore_writer_rollback_state_round_trip(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    blueprint_id = create_blueprint(
        isolated_system_database, project_id, content={"title": "原方向"}
    )
    episode_id = create_episode(isolated_system_database, project_id)
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:1",
        resource_ref={"resource_type": "brief", "resource_ids": [blueprint_id]},
    )

    preimage = postgres_creative.export_writer_rollback_state(project_id)

    # Mutate every group a Writer action may touch.
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET name = '被改写', revision = 9, "
                "updated_at = now() WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )
        connection.execute(
            text(
                "UPDATE creative_blueprints SET content_json = '{\"title\": \"新方向\"}'::jsonb, "
                "status = 'locked' WHERE id = :blueprint_id"
            ),
            {"blueprint_id": blueprint_id},
        )
        connection.execute(
            text(
                "UPDATE creative_episodes SET status = 'locked' WHERE id = :episode_id"
            ),
            {"episode_id": episode_id},
        )
    extra_episode = create_episode(
        isolated_system_database, project_id, episode_number=2, batch_id="batch-2"
    )
    create_operation_result(
        isolated_system_database,
        project_id,
        operation_id="action-1:2",
        resource_ref={"resource_type": "episode", "resource_ids": [extra_episode]},
    )

    post_state = postgres_creative.writer_rollback_fingerprint(project_id)
    assert post_state != preimage["fingerprint"]

    restored = postgres_creative.restore_writer_rollback_state(
        project_id, preimage, expected_fingerprint=post_state
    )

    assert postgres_creative.writer_rollback_fingerprint(project_id) == preimage["fingerprint"]
    assert restored["name"] == "测试项目"
    assert restored["revision"] == 0
    assert restored["blueprints"][0]["content"] == {"title": "原方向"}
    assert restored["blueprints"][0]["status"] == "draft"
    assert restored["episodes"][0]["status"] == "draft"

    # Rows the action created stay in the table but are superseded, exactly like
    # the legacy rollback that never deletes version rows.
    with isolated_system_database.connection() as connection:
        statuses = dict(
            connection.execute(
                text(
                    "SELECT id, status FROM creative_episodes WHERE project_id = :project_id"
                ),
                {"project_id": project_id},
            ).all()
        )
        operation_ids = [
            row[0]
            for row in connection.execute(
                text(
                    "SELECT operation_id FROM creative_operation_results "
                    "WHERE project_id = :project_id ORDER BY operation_id"
                ),
                {"project_id": project_id},
            ).all()
        ]
    assert statuses == {episode_id: "draft", extra_episode: "superseded"}
    assert operation_ids == ["action-1:1"]


def test_restore_writer_rollback_state_returns_when_already_at_preimage(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    create_blueprint(isolated_system_database, project_id)
    preimage = postgres_creative.export_writer_rollback_state(project_id)

    restored = postgres_creative.restore_writer_rollback_state(
        project_id, preimage, expected_fingerprint="not-the-current-state"
    )

    assert restored["id"] == project_id
    assert postgres_creative.writer_rollback_fingerprint(project_id) == preimage["fingerprint"]


def test_restore_writer_rollback_state_rejects_invalid_snapshots(
    postgres_creative, isolated_system_database
):
    project_id = create_project(isolated_system_database)
    other_project = create_project(isolated_system_database, name="另一个项目")
    create_blueprint(isolated_system_database, project_id)
    preimage = postgres_creative.export_writer_rollback_state(project_id)

    with pytest.raises(ValueError) as foreign:
        postgres_creative.restore_writer_rollback_state(
            other_project, preimage, expected_fingerprint=preimage["fingerprint"]
        )
    assert str(foreign.value) == "rollback snapshot belongs to another project"

    with pytest.raises(ValueError) as invalid:
        postgres_creative.restore_writer_rollback_state(
            project_id,
            {"project_id": project_id, "tables": []},
            expected_fingerprint=preimage["fingerprint"],
        )
    assert str(invalid.value) == "rollback snapshot is invalid"

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE creative_projects SET name = '改动' WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )
    with pytest.raises(ValueError) as changed:
        postgres_creative.restore_writer_rollback_state(
            project_id, preimage, expected_fingerprint="mismatch"
        )
    assert str(changed.value) == "project rollback state changed"

    # With a matching post-state fingerprint the snapshot itself is validated,
    # so a snapshot without exactly one project row is rejected after the
    # resource group has been restored (inside the rolled-back transaction).
    current = postgres_creative.writer_rollback_fingerprint(project_id)
    broken = dict(preimage)
    broken["tables"] = dict(preimage["tables"], creative_projects=[])
    with pytest.raises(ValueError) as invalid_project_rows:
        postgres_creative.restore_writer_rollback_state(
            project_id, broken, expected_fingerprint=current
        )
    assert str(invalid_project_rows.value) == "rollback project snapshot is invalid"

    with pytest.raises(ValueError) as missing_project:
        postgres_creative.export_writer_rollback_state(project_id + 999)
    assert str(missing_project.value) == "创作项目不存在"


# ---------------------------------------------------------------------------
# project deletion
# ---------------------------------------------------------------------------


def seed_full_project(database: SystemDatabase) -> int:
    project_id = create_project(database, name="待删除项目")
    insert_row(
        database,
        "creative_seeds",
        returning=None,
        project_id=project_id,
        version=1,
        status="draft",
        seed_text="种子",
        constraints_json={},
        reference_refs_json=[],
        created_at=NOW,
        updated_at=NOW,
    )
    blueprint_id = create_blueprint(database, project_id)
    create_character(database, project_id)
    insert_row(
        database,
        "creative_relationships",
        returning=None,
        project_id=project_id,
        batch_id="chars-1",
        version=1,
        relationship_key="pair",
        status="draft",
        content_json={},
        source_refs_json={},
        generation_meta_json={},
        created_at=NOW,
        updated_at=NOW,
    )
    create_episode(database, project_id)
    create_operation_result(database, project_id, operation_id="op-1")
    insert_row(
        database,
        "creative_generation_operations",
        returning=None,
        operation_id="gen-1",
        project_id=project_id,
        stage="outline",
        request_hash="hash",
        owner_id="owner",
        batch_id="batch-1",
        status="active",
        result_json={},
        created_at=NOW,
        updated_at=NOW,
    )
    insert_row(
        database,
        "creative_stage_exports",
        returning=None,
        project_id=project_id,
        stage="brief",
        format="json",
        content_fingerprint="fp",
        exported_at=NOW,
    )
    review_id = insert_row(
        database,
        "script_reviews",
        project_id=project_id,
        source_type="text",
        mode="single",
        episode_numbers_json=[1],
        source_fingerprint="fp",
        source_payload_json={"text": "内容"},
        created_by="user-1",
        created_at=NOW,
        updated_at=NOW,
    )
    optimization_id = insert_row(
        database,
        "script_optimizations",
        review_id=review_id,
        project_id=project_id,
        mode="single",
        source_fingerprint="fp",
        selected_findings_json=[],
        target_episode_numbers_json=[1],
        created_by="user-1",
        created_at=NOW,
        updated_at=NOW,
    )
    insert_row(
        database,
        "script_optimization_drafts",
        returning=None,
        optimization_id=optimization_id,
        project_id=project_id,
        episode_number=1,
        kind="script",
        source_fingerprint="fp",
        content_json={},
        created_at=NOW,
        updated_at=NOW,
    )
    insert_row(
        database,
        "creative_deliveries",
        returning=None,
        id="delivery-1",
        project_id=project_id,
        stage="brief",
        resource_type="brief",
        resource_id=str(blueprint_id),
        resource_version=1,
        submission_id="submission-1",
        submission_version=1,
        format="json",
        content_fingerprint="fp",
        actor_id="user-1",
        path="delivery-1.json",
        created_at=NOW,
    )
    insert_row(
        database,
        "creative_production_exports",
        returning=None,
        id="export-1",
        project_id=project_id,
        fingerprint="fp",
        contract_version="v1",
        created_by="user-1",
        created_at=NOW,
        episode_count=1,
        path="export-1.zip",
        size_bytes=10,
        file_sha256="sha",
    )
    insert_row(
        database,
        "creative_generation_contexts",
        returning=None,
        id="context-1",
        project_id=project_id,
        target_stage="outline",
        context_hash="hash",
        created_at=NOW,
    )
    insert_row(
        database,
        "creative_resource_heads",
        returning=None,
        project_id=project_id,
        resource_scope="sequential",
        stage="brief",
        episode_number=0,
        resource_ref=str(blueprint_id),
        updated_at=NOW,
    )
    insert_row(
        database,
        "creative_mode_migrations",
        returning=None,
        operation_id="migration-1",
        project_id=project_id,
        from_mode="sequential",
        to_mode="free",
        source_mode_revision=0,
        status="complete",
        created_at=NOW,
        updated_at=NOW,
    )
    return project_id


def test_delete_project_removes_every_dependent_row_and_leaves_a_tombstone(
    postgres_creative, isolated_system_database
):
    project_id = seed_full_project(isolated_system_database)

    result = postgres_creative.delete_project(
        project_id, requested_by="user-1"
    )

    assert set(result) == {"id", "name", "topic_id", "delivery_paths"}
    assert result["id"] == project_id
    assert result["name"] == "待删除项目"
    assert result["topic_id"] is None
    assert result["delivery_paths"] == ["delivery-1.json"]

    for table in _DELETED_TABLES:
        if table == "creative_project_deletions":
            continue
        assert count_rows(isolated_system_database, table, project_id) == 0, table
    with isolated_system_database.connection() as connection:
        remaining = connection.execute(
            text("SELECT COUNT(*) FROM creative_projects WHERE id = :project_id"),
            {"project_id": project_id},
        ).scalar_one()
    assert remaining == 0

    tombstone = postgres_creative.get_pending_project_deletion(project_id)
    assert tombstone is not None
    assert tombstone["project"] == {
        "id": project_id,
        "name": "待删除项目",
        "topic_id": None,
    }
    assert tombstone["delivery_paths"] == ["delivery-1.json"]
    assert tombstone["requested_by"] == "user-1"
    assert tombstone["status"] == "creative_deleted"
    assert isinstance(tombstone["created_at"], float)
    assert isinstance(tombstone["updated_at"], float)

    with pytest.raises(ValueError) as again:
        postgres_creative.delete_project(project_id)
    assert str(again.value) == "创作项目不存在"

    assert postgres_creative.get_pending_project_deletion(project_id + 1) is None
    postgres_creative.complete_project_deletion(project_id)
    assert postgres_creative.get_pending_project_deletion(project_id) is None


def test_delete_project_guards_writer_fingerprints(
    postgres_creative, isolated_system_database
):
    project_id = seed_full_project(isolated_system_database)

    with pytest.raises(ValueError) as state_changed:
        postgres_creative.delete_project(
            project_id, expected_writer_fingerprint="wrong"
        )
    assert str(state_changed.value) == "project deletion state changed"

    with pytest.raises(ValueError) as action_required:
        postgres_creative.delete_project(
            project_id, expected_writer_external_fingerprint="wrong"
        )
    assert str(action_required.value) == "writer action id is required"

    with pytest.raises(ValueError) as external_use:
        postgres_creative.delete_project(
            project_id,
            expected_writer_external_fingerprint="wrong",
            writer_action_id="action-1",
        )
    assert str(external_use.value) == "project has external use"

    deletion_fingerprint = postgres_creative.writer_project_deletion_fingerprint(
        project_id
    )
    external_fingerprint = postgres_creative.writer_project_external_use_fingerprint(
        project_id, "action-1"
    )
    deleted = postgres_creative.delete_project(
        project_id,
        expected_writer_fingerprint=deletion_fingerprint,
        expected_writer_external_fingerprint=external_fingerprint,
        writer_action_id="action-1",
    )
    assert deleted["id"] == project_id


def test_delete_project_upserts_the_tombstone(
    postgres_creative, isolated_system_database
):
    """A stale tombstone is replaced with the new deletion payload."""

    project_id = seed_full_project(isolated_system_database)
    insert_row(
        isolated_system_database,
        "creative_project_deletions",
        returning=None,
        project_id=project_id,
        project_json={},
        delivery_paths_json=[],
        requested_by="stale",
        status="creative_deleted",
        created_at=NOW,
        updated_at=NOW,
    )

    deleted = postgres_creative.delete_project(project_id, requested_by="second")

    tombstone = postgres_creative.get_pending_project_deletion(project_id)
    assert tombstone is not None
    assert tombstone["requested_by"] == "second"
    assert tombstone["project"]["name"] == "待删除项目"
    assert tombstone["delivery_paths"] == deleted["delivery_paths"] == ["delivery-1.json"]
    assert count_rows(
        isolated_system_database, "creative_project_deletions", project_id
    ) == 1


def test_delete_project_resolves_export_paths_before_the_transaction(
    postgres_creative, isolated_system_database, tmp_path: Path
):
    export_directory = (tmp_path / "exports").resolve()
    export_directory.mkdir()
    inside = export_directory / "package.zip"
    inside.write_bytes(b"data")
    outside = (tmp_path / "outside.zip").resolve()
    outside.write_bytes(b"data")

    project_id = create_project(isolated_system_database)
    insert_row(
        isolated_system_database,
        "creative_deliveries",
        returning=None,
        id="delivery-1",
        project_id=project_id,
        stage="brief",
        resource_type="brief",
        resource_id="1",
        resource_version=1,
        submission_id="submission-1",
        submission_version=1,
        format="json",
        content_fingerprint="fp",
        actor_id="user-1",
        path="delivery-1.json",
        created_at=NOW,
    )
    for index, path in enumerate((str(inside), str(outside))):
        insert_row(
            isolated_system_database,
            "creative_production_exports",
            returning=None,
            id=f"export-{index}",
            project_id=project_id,
            fingerprint=f"fp-{index}",
            contract_version="v1",
            created_by="user-1",
            created_at=NOW,
            episode_count=1,
            path=path,
            size_bytes=4,
            file_sha256="sha",
        )

    postgres_creative.production_export_directory = export_directory
    result = postgres_creative.delete_project(project_id)

    assert result["delivery_paths"] == ["delivery-1.json", str(inside)]
    # Deletion only hands the paths to the workflow side; it never unlinks.
    assert inside.exists()
    assert outside.exists()
