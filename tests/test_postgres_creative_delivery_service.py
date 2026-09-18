# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""The delivery service against the PostgreSQL creative repository.

``DeliveryService`` used to reach into a private ``_connect()`` on the creative
repository, which the PostgreSQL repository does not have, so every formal
delivery raised ``AttributeError``. This suite drives the service end to end
against ``postgres_creative`` and therefore exercises every replaced site:

* construction (the removed ``_init_schema`` DDL),
* ``create`` (record + replay + orphan artifact handling),
* ``list`` / ``get`` / ``_find_existing`` reads,
* ``_mark_reconciled`` / ``_get_unreconciled`` reconciliation writes,
* the formal path (stage export plus workflow completion).

The workflow repository and the workflow adapter belong to other ports, so the
tests stub exactly the calls ``delivery.py`` makes and assert on what it asked
for. Every creative read and write still goes to real PostgreSQL tables.
"""

from __future__ import annotations

import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from creative import delivery as delivery_module
from creative.delivery import DeliveryService
from creative.postgres_repository import PostgresCreativeRepository
from identity.schema import Principal

PROJECT_NAME = "交付服务项目"


def _principal() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin",
        display_name="Admin",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="delivery-session",
    )


def _count(database, table: str) -> int:
    with database.connection() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


@pytest.fixture
def project_id(isolated_system_database) -> int:
    """A real ``creative_projects`` row so the delivery foreign key is honest."""

    with isolated_system_database.transaction() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, created_at, updated_at) "
                    "VALUES (:name, 'original', now(), now()) RETURNING id"
                ),
                {"name": PROJECT_NAME},
            ).scalar_one()
        )


class RecordingAuditService:
    """Stands in for ``AuditService``; the audit domain is a separate port."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []
        self.records: list[dict[str, Any]] = []

    def query(self, *, action: str, target_id: str):
        self.queries.append((action, target_id))
        return []

    def record(self, **kwargs: Any) -> dict[str, Any]:
        self.records.append(kwargs)
        return {"id": "audit-1"}


class StubWorkflowRepository:
    """Only the workflow surface ``DeliveryService`` calls.

    With ``database=None`` it answers the audit-path actor lookup without SQL.
    With a database it also serves ``_resolve_delivery_task``'s ``SELECT`` through
    a real connection, and records the terminal transition instead of running the
    workflow state machine, which another task ports.
    """

    def __init__(
        self,
        database: Any = None,
        task: dict[str, Any] | None = None,
    ) -> None:
        self.database = database
        self.task = dict(task or {})
        self.transitions: list[dict[str, Any]] = []
        self.identity_repository = self
        self.live_actor = {
            "user_id": "admin-1",
            "role_codes": ["system_admin"],
            "permissions": ["*"],
            "is_system_admin": True,
        }

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.database is None:
            yield None
            return
        with self.database.connection() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        if self.database is None:
            yield None
            return
        with self.database.transaction() as connection:
            yield connection

    @staticmethod
    def _task_dict(row: Any) -> dict[str, Any]:
        return dict(row._mapping)

    def _require_task(self, connection: Any, task_id: str) -> dict[str, Any]:
        return dict(self.task)

    def _live_actor(self, connection: Any, user_id: str) -> dict[str, Any]:
        return dict(self.live_actor, user_id=user_id)

    def transition_task_in_transaction(
        self,
        task_id: str,
        action: str,
        actor: dict[str, Any],
        *,
        connection: Any,
        event_type: str,
        details: dict[str, Any],
        authorization_context: Any = None,
    ) -> None:
        self.transitions.append(
            {
                "task_id": task_id,
                "action": action,
                "event_type": event_type,
                "details": dict(details),
                "authorization_context": authorization_context,
            }
        )


def _install_snapshot_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str,
    snapshot: dict[str, Any],
) -> None:
    """``create`` builds its adapter internally; supply the snapshot it reads."""

    class StubWorkflowAdapter:
        def __init__(self, creative_repository: Any, workflow_repository: Any) -> None:
            self.creative_repository = creative_repository
            self.workflow_repository = workflow_repository

        def domain_status(self, resource_type: str, resource_id: str) -> str:
            return status

        def snapshot(
            self, resource_type: str, resource_id: str, reconcile: bool = False
        ) -> dict[str, Any]:
            return copy.deepcopy(snapshot)

    monkeypatch.setattr(
        delivery_module, "CreativeWorkflowAdapter", StubWorkflowAdapter
    )


@pytest.fixture
def draft_snapshot(monkeypatch: pytest.MonkeyPatch, project_id: int) -> dict[str, Any]:
    snapshot = {"project_id": project_id, "version": 3, "title": "方向"}
    _install_snapshot_adapter(monkeypatch, status="draft", snapshot=snapshot)
    return snapshot


@pytest.fixture
def audit_service() -> RecordingAuditService:
    return RecordingAuditService()


@pytest.fixture
def workflow_repository() -> StubWorkflowRepository:
    return StubWorkflowRepository()


@pytest.fixture
def service(
    tmp_path: Path,
    postgres_creative: PostgresCreativeRepository,
    workflow_repository: StubWorkflowRepository,
    audit_service: RecordingAuditService,
    draft_snapshot: dict[str, Any],
) -> DeliveryService:
    return DeliveryService(
        root=tmp_path,
        creative_repository=postgres_creative,
        workflow_repository=workflow_repository,  # type: ignore[arg-type]
        audit_service=audit_service,  # type: ignore[arg-type]
    )


def _create_draft(
    service: DeliveryService,
    project_id: int,
    *,
    resource_id: str = "10",
    format: str = "md",
) -> dict[str, Any]:
    return service.create(
        project_id=project_id,
        stage="brief",
        resource_id=resource_id,
        format=format,
        actor=_principal(),
    )


# --------------------------------------------------------------- construction


def test_service_constructs_without_runtime_ddl(
    service: DeliveryService,
    postgres_creative: PostgresCreativeRepository,
) -> None:
    """Construction must not ask the PostgreSQL repository for a private connect."""

    assert service.creative_repository is postgres_creative
    assert isinstance(service.creative_repository, PostgresCreativeRepository)
    assert not hasattr(DeliveryService, "_init_schema")
    assert service.delivery_dir.is_dir()


def test_module_carries_no_private_connection_or_runtime_ddl() -> None:
    """Guard against the private-handle and DDL patterns the port removed.

    The behavioural tests below are the real evidence; this only pins the shape
    of the source so the SQLite-only constructs cannot quietly return.
    """

    source = (
        Path(__file__).resolve().parent.parent / "creative" / "delivery.py"
    ).read_text(encoding="utf-8")
    assert "_connect(" not in source
    assert "sqlite3" not in source
    for pattern in ("CREATE TABLE", "ALTER TABLE", "PRAGMA ", "DROP INDEX"):
        assert pattern not in source


# --------------------------------------------------------------------- create


def test_create_persists_a_draft_delivery_through_the_repository(
    service: DeliveryService,
    audit_service: RecordingAuditService,
    project_id: int,
    isolated_system_database,
) -> None:
    created = _create_draft(service, project_id)

    assert created["project_id"] == str(project_id)
    assert created["project_name"] == PROJECT_NAME
    assert created["stage"] == "brief"
    assert created["resource_type"] == "brief"
    assert created["resource_version"] == 3
    assert created["submission_id"] == "direct:brief:10"
    assert created["is_formal"] is False
    assert created["resource_status"] == "draft"
    assert created["workflow_task_id"] is None
    assert created["actor_user_id"] == "admin-1"
    assert created["stage_export_recorded_at"] is None
    assert created["workflow_completed_at"] is None
    # ``_mark_reconciled`` wrote this and ``_get_unreconciled`` read it back.
    assert created["audit_recorded_at"] is not None

    artifact = Path(created["path"])
    assert artifact.parent == service.delivery_dir
    assert artifact.is_file()
    assert created["fingerprint"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert _count(isolated_system_database, "creative_deliveries") == 1
    assert audit_service.queries == [("delivery.create", created["id"])]
    assert [entry["target_id"] for entry in audit_service.records] == [created["id"]]
    assert audit_service.records[0]["action"] == "delivery.create"


def test_create_replay_returns_the_stored_row_and_drops_the_orphan(
    service: DeliveryService,
    project_id: int,
    isolated_system_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _create_draft(service, project_id)
    # Force the concurrent-writer branch: the pre-read misses, the named unique
    # key then refuses the second row and the stored winner is returned.
    monkeypatch.setattr(service, "_find_existing", lambda *args, **kwargs: None)

    replay = _create_draft(service, project_id)

    assert replay["id"] == first["id"]
    assert replay["path"] == first["path"]
    assert Path(first["path"]).is_file()
    assert sorted(path.name for path in service.delivery_dir.iterdir()) == [
        Path(first["path"]).name
    ]
    assert _count(isolated_system_database, "creative_deliveries") == 1


def test_failed_insert_removes_the_orphan_artifact(
    service: DeliveryService,
    project_id: int,
    isolated_system_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_record_delivery(values, connection=None):
        raise IntegrityError("INSERT", None, Exception("delivery constraint"))

    monkeypatch.setattr(
        service.creative_repository, "record_delivery", failing_record_delivery
    )

    with pytest.raises(IntegrityError):
        _create_draft(service, project_id)

    assert list(service.delivery_dir.iterdir()) == []
    assert _count(isolated_system_database, "creative_deliveries") == 0


# ---------------------------------------------------------------- list and get


def test_list_and_get_read_deliveries_through_the_repository(
    service: DeliveryService,
    project_id: int,
) -> None:
    assert service.list() == []

    first = _create_draft(service, project_id, resource_id="10")
    second = _create_draft(service, project_id, resource_id="11", format="json")

    records = service.list()
    assert {record["id"] for record in records} == {first["id"], second["id"]}
    assert all(record["project_name"] == PROJECT_NAME for record in records)
    # The repository already projects the public shape the service used to build.
    assert all("path" in record and "project_name" in record for record in records)

    loaded = service.get(first["id"])
    assert loaded is not None and loaded["id"] == first["id"]
    assert loaded["project_id"] == str(project_id)
    assert loaded["fingerprint"] == first["fingerprint"]
    assert service.get("missing-delivery") is None


def test_find_existing_matches_the_named_unique_key(
    service: DeliveryService,
    project_id: int,
) -> None:
    created = _create_draft(service, project_id)

    assert (
        service._find_existing(
            project_id, "brief", "direct:brief:10", "md", is_formal=False
        )["id"]
        == created["id"]
    )
    assert (
        service._find_existing(
            project_id, "brief", "direct:brief:10", "md", is_formal=True
        )
        is None
    )
    assert (
        service._find_existing(
            project_id, "brief", "direct:brief:99", "md", is_formal=False
        )
        is None
    )


def test_project_authorizer_scopes_list_get_and_download(
    service: DeliveryService,
    project_id: int,
) -> None:
    created = _create_draft(service, project_id)

    allowed = lambda project: project == str(project_id)  # noqa: E731
    forbidden = lambda project: False  # noqa: E731

    assert [record["id"] for record in service.list(project_authorizer=allowed)] == [
        created["id"]
    ]
    assert service.list(project_authorizer=forbidden) == []
    assert service.download_path(created["id"], project_authorizer=allowed) == Path(
        created["path"]
    )
    with pytest.raises(PermissionError, match="outside the actor scope"):
        service.get(created["id"], project_authorizer=forbidden)
    with pytest.raises(PermissionError, match="outside the actor scope"):
        service.download_path(created["id"], project_authorizer=forbidden)


# ------------------------------------------------------------- reconciliation


def test_mark_reconciled_and_unreconciled_round_trip(
    service: DeliveryService,
    project_id: int,
) -> None:
    created = _create_draft(service, project_id)
    before = service._get_unreconciled(created["id"])
    assert before is not None and before["workflow_task_id"] is None

    service._mark_reconciled(created["id"], "workflow_task_id", "task-9")
    service._mark_reconciled(
        created["id"], "stage_export_recorded_at", 1_700_000_000.0
    )
    service._mark_reconciled(created["id"], "stage_content_fingerprint", "recomputed")

    refreshed = service._get_unreconciled(created["id"])
    assert refreshed is not None
    assert refreshed["workflow_task_id"] == "task-9"
    assert refreshed["stage_export_recorded_at"] == 1_700_000_000.0
    assert refreshed["stage_content_fingerprint"] == "recomputed"

    with pytest.raises(ValueError, match="invalid delivery reconciliation column"):
        service._mark_reconciled(created["id"], "project_id", 5)


# ---------------------------------------------------------------- formal path


def test_formal_delivery_records_the_stage_export_and_completes_the_task(
    tmp_path: Path,
    isolated_system_database,
    postgres_creative: PostgresCreativeRepository,
    audit_service: RecordingAuditService,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_project_id = postgres_creative.workflow_project_id(project_id)
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO workflow_instances "
                "(id, workflow_type, resource_type, resource_id, project_id) "
                "VALUES ('wf-1', 'creative', 'project', :scoped, :scoped)"
            ),
            {"scoped": scoped_project_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_tasks "
                "(id, workflow_id, task_type, role_code, project_id, status) "
                "VALUES ('task-1', 'wf-1', 'delivery', 'system_admin', :scoped, 'pool')"
            ),
            {"scoped": scoped_project_id},
        )
    with isolated_system_database.connection() as connection:
        task_row = dict(
            connection.execute(
                text("SELECT * FROM workflow_tasks WHERE id = 'task-1'")
            )
            .mappings()
            .one()
        )

    snapshot = {
        "id": 77,
        "project_id": project_id,
        "stage": "beats",
        "episode_number": 1,
        "batch_id": "batch-1",
        "version": 2,
        "parent_version_id": None,
        "status": "locked",
        "content": {"beats": [{"order": 1, "scene": "柴房", "goal": "拿到丹药"}]},
        "upstream_refs": {},
        "generation_meta": {},
    }
    _install_snapshot_adapter(monkeypatch, status="locked", snapshot=snapshot)
    workflow = StubWorkflowRepository(isolated_system_database, task_row)
    service = DeliveryService(
        root=tmp_path,
        creative_repository=postgres_creative,
        workflow_repository=workflow,  # type: ignore[arg-type]
        audit_service=audit_service,  # type: ignore[arg-type]
    )

    created = service.create(
        project_id=project_id,
        stage="beats",
        resource_id="77",
        format="md",
        actor=_principal(),
        workflow_task_id="task-1",
    )

    assert created["is_formal"] is True
    assert created["resource_status"] == "locked"
    assert created["workflow_task_id"] == "task-1"
    assert created["stage_export_recorded_at"] is not None
    assert created["workflow_completed_at"] is not None
    assert created["audit_recorded_at"] is not None

    assert _count(isolated_system_database, "creative_stage_exports") == 1
    assert (
        postgres_creative.has_stage_export(
            project_id, "beats", created["stage_content_fingerprint"]
        )
        is True
    )
    assert [entry["event_type"] for entry in workflow.transitions] == [
        "delivery.complete"
    ]
    assert workflow.transitions[0]["action"] == "deliver"
    assert workflow.transitions[0]["task_id"] == "task-1"
    assert workflow.transitions[0]["details"]["delivery_id"] == created["id"]
    assert workflow.transitions[0]["details"]["stage"] == "beats"

    # A second delivery of the same locked resource replays instead of exporting
    # or completing the workflow twice.
    replayed = service.create(
        project_id=project_id,
        stage="beats",
        resource_id="77",
        format="md",
        actor=_principal(),
        workflow_task_id="task-1",
    )
    assert replayed["id"] == created["id"]
    assert _count(isolated_system_database, "creative_deliveries") == 1
    assert _count(isolated_system_database, "creative_stage_exports") == 1
    assert len(workflow.transitions) == 1
    assert len(audit_service.records) == 1
