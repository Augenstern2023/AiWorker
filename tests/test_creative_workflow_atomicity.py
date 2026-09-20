# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Cross-domain write atomicity for the creative/workflow boundary.

Creative tables and the workflow/identity tables live in one PostgreSQL
database, so a single failing write must not leave one domain's rows committed
while the other domain's rows are missing. Every test here injects a failure
into the *second* domain and then asserts that neither domain kept a partial
result.

The four flows Task 13 names are covered:

* original project creation (``OriginalProjectService.create``),
* stage locking (``TransitionService.complete_creative_without_review``),
* formal delivery (``DeliveryService.create``),
* stage approval / supersede (``CreativeWorkflowAdapter.on_approved``),
* writer project deletion (documented resumable saga, see
  ``test_writer_deletion_failure_keeps_both_domains_recoverable``).

Contract for project creation, decided explicitly: the joinable PostgreSQL
store commits the creative project row, its seed, the creator membership, the
five stage tasks, the brief link and the ``ready`` status in **one**
transaction. A failed bootstrap therefore leaves nothing in either domain, and
a retry carrying the same ``creation_request_id`` is re-runnable -- it creates
the project fresh instead of resuming a surviving ``failed`` row. Idempotency
comes from ``ensure_original_project``'s find-or-insert keyed on
(creator, request id) under an advisory lock, not from a partially committed
row. The frozen SQLite store cannot join another store's transaction
(``transaction_scope`` is a PostgreSQL-repository capability), so it keeps the
compensating bootstrap: the project row commits first, a failure stamps it
``failed`` and the retry resumes it. That older behaviour is what
``tests/test_original_project_api.py``
``::test_original_create_api_returns_503_then_retries_the_same_failed_project``
pins by name, on the SQLite store, and it is deliberately unchanged.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from creative import delivery as delivery_module
from creative.delivery import DeliveryService
from creative.market_reference_service import MarketReferenceService
from creative.original_project_service import OriginalProjectService
from creative.postgres_repository import PostgresCreativeRepository
from creative.schema import OriginalProjectCreateRequest
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.repository import IdentityRepository
from identity.schema import Principal
from system_database import SystemDatabase
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository
from workflow.transition_service import TransitionService


def _count(database: SystemDatabase, table: str) -> int:
    with database.connection() as connection:
        return int(
            connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        )


@pytest.fixture
def identity_repository(isolated_system_database: SystemDatabase) -> IdentityRepository:
    identity = IdentityRepository(isolated_system_database)
    identity.seed_defaults()
    return identity


@pytest.fixture
def workflow_repository(identity_repository: IdentityRepository) -> WorkflowRepository:
    return WorkflowRepository(identity_repository)


@pytest.fixture
def system_actor_repository(workflow_repository: WorkflowRepository) -> WorkflowRepository:
    """The real workflow repository with its system actor row present."""

    with workflow_repository.identity_repository.transaction() as connection:
        workflow_repository._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
    return workflow_repository


@pytest.fixture
def owner_actor(identity_repository: IdentityRepository) -> Principal:
    """A real user holding an enabled project-owner role."""

    role = identity_repository.get_role_by_code("project_owner")
    assert role is not None
    user = identity_repository.create_user(
        f"atomicity-owner-{uuid.uuid4().hex}", "原子性创建者", "hash"
    )
    identity_repository.replace_user_roles(user["id"], [role["id"]])
    return Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=("project_owner",),
        permissions=frozenset({"project.create"}),
        data_scope="all",
        session_id="atomicity-owner",
    )


@pytest.fixture
def admin_actor(identity_repository: IdentityRepository) -> Principal:
    """A real, active system administrator (the seeded system actor is not live)."""

    role = identity_repository.get_role_by_code("system_admin")
    assert role is not None
    user = identity_repository.create_user(
        f"atomicity-admin-{uuid.uuid4().hex}", "原子性管理员", "hash"
    )
    identity_repository.replace_user_roles(user["id"], [role["id"]])
    return Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="atomicity-admin",
    )


def _original_payload(*, creation_request_id: str) -> OriginalProjectCreateRequest:
    return OriginalProjectCreateRequest(
        seed_text="未来公司按照员工情绪发工资",
        name="原子性项目",
        genre="都市奇幻",
        target_platform="hg",
        target_audience="18-35 岁职场观众",
        target_episode_count=1,
        episode_duration_seconds=120,
        cost_level="low",
        content_boundaries=[],
        reference_refs=[],
        creation_request_id=creation_request_id,
    )


def _scope_project(
    repository: PostgresCreativeRepository, *, created_by: str
) -> dict[str, Any]:
    project, _ = repository.ensure_original_project(
        seed_text="未来公司按照员工情绪发工资",
        name="原子性交付项目",
        target_episode_count=1,
        created_by=created_by,
        creation_request_id=f"atomicity-{uuid.uuid4().hex}",
    )
    assert project["workflow_mode"] == "sequential"
    return project


def _outline_content(episode_number: int) -> dict[str, Any]:
    return {
        "episode": episode_number,
        "core_event": f"主角发现线索 {episode_number}",
        "conflict": f"冲突 {episode_number}",
        "character_progress": f"推进 {episode_number}",
        "highlight": f"亮点 {episode_number}",
        "reversal": f"反转 {episode_number}",
        "cliffhanger": f"悬念 {episode_number}",
    }


def _legacy_beats(order: int = 1) -> dict[str, Any]:
    return {"beats": [{"order": order}]}


def _script_content(title: str = "第一集") -> dict[str, Any]:
    return {
        "episode_title": title,
        "scenes": [
            {
                "sequence_no": 1,
                "scene_no": 1,
                "time_of_day": "夜",
                "interior_exterior": "内",
                "location": "旧仓库",
                "props": ["手电"],
                "characters": ["林昭"],
                "blocks": [{"type": "action", "text": "林昭推开门，灰尘落下。"}],
            }
        ],
    }


# ------------------------------------------------------- 1. project creation


def test_project_creation_rolls_back_both_domains_and_the_retry_is_re_runnable(
    isolated_system_database: SystemDatabase,
    postgres_creative: PostgresCreativeRepository,
    workflow_repository: WorkflowRepository,
    owner_actor: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing stage task must leave neither domain half-bootstrapped.

    ``OriginalProjectService`` runs the whole creation on one connection, so a
    failure on the third stage task rolls back the creative project row, its
    seed, the creator membership and the first two tasks together. Before the
    fix each write committed on its own connection: the membership, a workflow
    instance and two tasks survived, and the creative project stayed behind as
    a ``failed`` row.
    """

    service = OriginalProjectService(
        postgres_creative,
        workflow_repository,
        MarketReferenceService(postgres_creative),
    )
    real_ensure_task = workflow_repository.ensure_project_task
    calls = {"count": 0}

    def flaky_ensure_project_task(*args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("injected bootstrap task failure")
        return real_ensure_task(*args, **kwargs)

    monkeypatch.setattr(
        workflow_repository, "ensure_project_task", flaky_ensure_project_task
    )

    creation_request_id = f"atomicity-bootstrap-{uuid.uuid4().hex}"
    with pytest.raises(RuntimeError, match="injected bootstrap task failure"):
        service.create(
            _original_payload(creation_request_id=creation_request_id),
            owner_actor,
            can_read_reference=lambda _kind, _id: True,
        )

    assert calls["count"] == 3
    # Workflow domain: nothing at all from the failed bootstrap may survive.
    assert _count(isolated_system_database, "workflow_instances") == 0
    assert _count(isolated_system_database, "workflow_tasks") == 0
    assert _count(isolated_system_database, "project_members") == 0
    assert _count(isolated_system_database, "workflow_project_member_events") == 0
    # Creative domain: the project row and its seed joined the same transaction,
    # so there is no row for a repair pass to resume -- not even a 'failed' one.
    assert _count(isolated_system_database, "creative_projects") == 0
    assert _count(isolated_system_database, "creative_seeds") == 0
    assert (
        postgres_creative.get_project_by_creation_request_id(
            owner_actor.user_id, creation_request_id
        )
        is None
    )

    # Re-runnable retry: the same creation request id creates the project
    # fresh and bootstraps it completely, exactly once.
    monkeypatch.undo()
    ready, created = service.create(
        _original_payload(creation_request_id=creation_request_id),
        owner_actor,
        can_read_reference=lambda _kind, _id: True,
    )

    assert created is True
    assert ready["bootstrap_status"] == "ready"
    # The link and the status are read back on the caller's own connection, so
    # the returned row already shows both uncommitted writes.
    assert ready["creation_workflow_task_id"] is not None
    assert ready["workflow_task_id"] == ready["creation_workflow_task_id"]
    assert _count(isolated_system_database, "creative_projects") == 1
    assert _count(isolated_system_database, "creative_seeds") == 1
    assert _count(isolated_system_database, "workflow_tasks") == 5
    assert _count(isolated_system_database, "project_members") == 1


# ---------------------------------------------------------- 2. stage locking


def test_stage_lock_rolls_back_the_locked_resource_when_the_next_task_fails(
    isolated_system_database: SystemDatabase,
    postgres_creative: PostgresCreativeRepository,
    system_actor_repository: WorkflowRepository,
    admin_actor: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed successor task must not leave the creative Brief locked.

    ``TransitionService.complete_creative_without_review`` locks the creative
    version and then completes the workflow task. Both now share one
    transaction, so the failure of the successor task created for the workflow's
    next node rolls the lock, the project revision and the brief link back.
    Before the fix the lock committed first and only the workflow write rolled
    back, leaving a ``brief_locked`` direction with an ``in_progress`` task.
    """

    project = _scope_project(postgres_creative, created_by=admin_actor.user_id)
    project_id = int(project["id"])
    scoped_project_id = postgres_creative.workflow_project_id(project_id)
    blueprint = postgres_creative.create_blueprints(
        project_id, [{"content": {"title": "方向", "audience": "职场人"}}]
    )[0]
    blueprint_id = str(blueprint["id"])
    revision_before = int(postgres_creative.get_project(project_id)["revision"])

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO workflow_instances "
                "(id, workflow_type, resource_type, resource_id, project_id) "
                "VALUES ('wf-lock', 'creative', 'project', :scoped, :scoped)"
            ),
            {"scoped": scoped_project_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_tasks "
                "(id, workflow_id, task_type, role_code, project_id, status) "
                "VALUES ('task-lock', 'wf-lock', 'brief', 'creative_planner', "
                ":scoped, 'in_progress')"
            ),
            {"scoped": scoped_project_id},
        )

    adapter = CreativeWorkflowAdapter(postgres_creative, system_actor_repository)
    transitions = TransitionService(
        system_actor_repository, creative_adapter=adapter
    )

    def fail_next_task(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected successor task failure")

    monkeypatch.setattr(system_actor_repository, "create_next_task", fail_next_task)

    with pytest.raises(RuntimeError, match="injected successor task failure"):
        transitions.complete_creative_without_review(
            "task-lock", "brief", blueprint_id, admin_actor
        )

    # Creative domain: the lock, the revision bump and the brief link rolled back.
    assert postgres_creative.get_blueprint(int(blueprint_id))["status"] == "draft"
    rolled_back = postgres_creative.get_project(project_id)
    assert int(rolled_back["revision"]) == revision_before
    assert rolled_back["locked_brief_id"] is None
    # Workflow domain: the task is still open and recorded no lock event.
    with isolated_system_database.connection() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM workflow_tasks WHERE id = 'task-lock'")
            ).scalar_one()
            == "in_progress"
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM workflow_task_events "
                    "WHERE event_type = 'creative.lock'"
                )
            ).scalar_one()
            == 0
        )


def test_repository_writes_still_own_their_transaction_without_a_caller(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    """Every method that gained ``connection`` must work when nobody passes one.

    This is the path where the repository owns -- and therefore closes -- the
    connection, so any read-back that reused the scoped name after its ``with``
    block raised ``ResourceClosedError``. The five stage locks, the batch lock
    and the whole project-creation family are called here with no connection.
    """

    request_id = f"no-caller-{uuid.uuid4().hex}"
    project, created = postgres_creative.ensure_original_project(
        seed_text="未来公司按照员工情绪发工资",
        name="无调用方连接项目",
        target_episode_count=2,
        created_by="no-caller-owner",
        creation_request_id=request_id,
    )
    assert created is True
    assert project["bootstrap_status"] == "pending"
    project_id = int(project["id"])

    same, created_again = postgres_creative.ensure_original_project(
        seed_text="未来公司按照员工情绪发工资",
        name="无调用方连接项目",
        target_episode_count=2,
        created_by="no-caller-owner",
        creation_request_id=request_id,
    )
    assert created_again is False
    assert int(same["id"]) == project_id
    assert (
        int(
            postgres_creative.create_original_project(
                seed_text="未来公司按照员工情绪发工资",
                name="无调用方连接项目",
                target_episode_count=2,
                created_by="no-caller-owner",
                creation_request_id=request_id,
            )["id"]
        )
        == project_id
    )

    assert (
        postgres_creative.mark_project_bootstrap_status(project_id, "ready")[
            "bootstrap_status"
        ]
        == "ready"
    )
    linked = postgres_creative.reconcile_project_workflow_task(
        "wf-no-caller-task", project_id
    )
    assert linked["creation_workflow_task_id"] == "wf-no-caller-task"

    # brief -> characters -> outline -> beats -> script, each without a caller.
    blueprint = postgres_creative.create_blueprints(
        project_id, [{"content": {"title": "方向"}}]
    )[0]
    before = int(postgres_creative.get_project(project_id)["revision"])
    locked_brief = postgres_creative.lock_brief(int(blueprint["id"]))
    assert locked_brief["status"] == "brief_locked"
    assert int(locked_brief["revision"]) == before + 1

    batch = postgres_creative.create_character_batch(
        project_id, [{"name": "林昭"}], []
    )
    before = int(postgres_creative.get_project(project_id)["revision"])
    locked_characters = postgres_creative.lock_character_batch(
        project_id, batch["batch_id"]
    )
    assert [item["status"] for item in locked_characters["characters"]] == ["locked"]
    assert int(locked_characters["revision"]) == before + 1

    outline = postgres_creative.create_outline_batch(
        project_id,
        [_outline_content(number) for number in (1, 2)],
        "调查、受阻、反击",
    )
    before = int(postgres_creative.get_project(project_id)["revision"])
    locked_outline = postgres_creative.lock_outline_batch(
        project_id, outline["batch_id"]
    )
    assert [item["status"] for item in locked_outline["episodes"]] == [
        "locked",
        "locked",
    ]
    assert int(locked_outline["revision"]) == before + 1

    beats = postgres_creative.create_episode_beats(project_id, 1, _legacy_beats())
    assert postgres_creative.lock_episode_beats(int(beats["id"]))["status"] == "locked"

    script = postgres_creative.create_episode_script(project_id, 1, _script_content())
    assert (
        postgres_creative.lock_episode_script(int(script["id"]))["status"] == "locked"
    )

    postgres_creative.create_episode_beats(project_id, 1, _legacy_beats())
    postgres_creative.create_episode_beats(project_id, 2, _legacy_beats())
    locked_batch = postgres_creative.lock_current_episode_beats_batch(project_id)
    assert [item["status"] for item in locked_batch] == ["locked", "locked"]

    # The read-only helpers gained the parameter too and keep working alone.
    assert postgres_creative.get_project(project_id) is not None
    assert postgres_creative.get_blueprint(int(blueprint["id"])) is not None
    assert postgres_creative.get_episode(int(script["id"])) is not None
    assert postgres_creative.get_character_batch(project_id, batch["batch_id"])[
        "characters"
    ]
    assert postgres_creative.get_outline_batch(project_id, outline["batch_id"])[
        "episodes"
    ]


# -------------------------------------------------------- 2. formal delivery


@pytest.fixture
def formal_delivery_context(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
    postgres_creative: PostgresCreativeRepository,
    system_actor_repository: WorkflowRepository,
    admin_actor: Principal,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """A locked beats resource plus a real terminal delivery workflow task."""

    project = _scope_project(
        postgres_creative, created_by=admin_actor.user_id
    )
    project_id = int(project["id"])
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

    class StubWorkflowAdapter:
        def __init__(self, creative_repository: Any, workflow_repository: Any) -> None:
            self.creative_repository = creative_repository
            self.workflow_repository = workflow_repository

        def domain_status(self, resource_type: str, resource_id: str) -> str:
            return "locked"

        def snapshot(
            self, resource_type: str, resource_id: str, reconcile: bool = False
        ) -> dict[str, Any]:
            return copy.deepcopy(snapshot)

    monkeypatch.setattr(
        delivery_module, "CreativeWorkflowAdapter", StubWorkflowAdapter
    )

    class RecordingAuditService:
        def query(self, *, action: str, target_id: str) -> list[Any]:
            return []

        def record(self, **kwargs: Any) -> dict[str, Any]:
            return {"id": "audit-1"}

    service = DeliveryService(
        root=tmp_path,
        creative_repository=postgres_creative,
        workflow_repository=system_actor_repository,  # type: ignore[arg-type]
        audit_service=RecordingAuditService(),  # type: ignore[arg-type]
    )
    return service, project_id, admin_actor


def test_formal_delivery_rolls_back_both_domains_and_the_artifact(
    isolated_system_database: SystemDatabase,
    formal_delivery_context: Any,
    system_actor_repository: WorkflowRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workflow failure must drop the delivery row, the export and the file.

    The delivery row, its reconciliation stamps, the stage export and the
    workflow completion belong to one transaction. Before the fix each write
    committed separately, so a failing terminal transition left a delivered
    artifact plus a stage export with the workflow task still open.
    """

    service, project_id, admin_actor = formal_delivery_context
    project = service.creative_repository.get_project(project_id)
    revision_before = int(project["revision"])

    def fail_transition(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected workflow completion failure")

    monkeypatch.setattr(
        system_actor_repository, "transition_task_in_transaction", fail_transition
    )

    with pytest.raises(RuntimeError, match="injected workflow completion failure"):
        service.create(
            project_id=project_id,
            stage="beats",
            resource_id="77",
            format="md",
            actor=admin_actor,
            workflow_task_id="task-1",
        )

    # Creative domain: neither the delivery row nor its export survived.
    assert _count(isolated_system_database, "creative_deliveries") == 0
    assert _count(isolated_system_database, "creative_stage_exports") == 0
    assert int(service.creative_repository.get_project(project_id)["revision"]) == (
        revision_before
    )
    # Workflow domain: the terminal task is untouched and recorded no delivery.
    with isolated_system_database.connection() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM workflow_tasks WHERE id = 'task-1'")
            ).scalar_one()
            == "pool"
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM workflow_task_events "
                    "WHERE event_type = 'delivery.complete'"
                )
            ).scalar_one()
            == 0
        )
    # Filesystem: a rolled back row never leaves an orphan artifact.
    assert list(service.delivery_dir.iterdir()) == []


# ------------------------------------------------- 3. stage approval supersede


def test_stage_approval_supersede_rolls_back_both_domains(
    isolated_system_database: SystemDatabase,
    postgres_creative: PostgresCreativeRepository,
    system_actor_repository: WorkflowRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a Brief must not commit Creative while the registry rolls back.

    ``on_approved`` supersedes the previously approved workflow submissions.
    Before the fix those workflow writes ran after the creative transaction had
    already committed, so a workflow failure left Creative approving the new
    Brief (and bumping the project revision) while the registry still approved
    the old submission.
    """

    project = _scope_project(postgres_creative, created_by=SYSTEM_ACTOR.user_id)
    project_id = int(project["id"])
    scoped_project_id = postgres_creative.workflow_project_id(project_id)
    blueprint = postgres_creative.create_blueprints(
        project_id, [{"content": {"title": "方向", "audience": "职场人"}}]
    )[0]
    blueprint_id = str(blueprint["id"])
    revision_before = int(postgres_creative.get_project(project_id)["revision"])

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
                "VALUES ('task-1', 'wf-1', 'brief', 'creative_planner', "
                ":scoped, 'in_progress')"
            ),
            {"scoped": scoped_project_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_submissions "
                "(id, task_id, resource_type, resource_id, version, status, "
                " content_snapshot_json, content_fingerprint, created_by, "
                " reviewed_by, reviewed_at, created_at, updated_at) "
                "VALUES ('sub-approved', 'task-1', 'brief', :resource_id, 1, "
                " 'approved', '{}'::jsonb, 'fp-1', :actor, :actor, now(), "
                " now(), now())"
            ),
            {"resource_id": blueprint_id, "actor": SYSTEM_ACTOR.user_id},
        )

    adapter = CreativeWorkflowAdapter(postgres_creative, system_actor_repository)
    requested = {
        "id": "sub-requested",
        "task_id": "task-1",
        "resource_type": "brief",
        "resource_id": blueprint_id,
        "version": 2,
        "status": "approved",
        "content_snapshot": {"content": {"title": "方向", "audience": "职场人"}},
        "content_fingerprint": "fp-2",
    }
    authoritative = {**requested, "id": "sub-approved", "version": 1}
    monkeypatch.setattr(
        system_actor_repository,
        "list_approved_submissions",
        lambda resource_type: [authoritative],
    )
    real_get_submission = system_actor_repository.get_submission

    def flaky_get_submission(submission_id: str, *, connection: Any = None) -> Any:
        if connection is not None:
            raise RuntimeError("injected workflow supersede failure")
        return real_get_submission(submission_id, connection=connection)

    monkeypatch.setattr(
        system_actor_repository, "get_submission", flaky_get_submission
    )

    with pytest.raises(RuntimeError, match="injected workflow supersede failure"):
        adapter.on_approved(requested, SYSTEM_ACTOR)

    # Creative domain: the approval and the project revision were rolled back.
    assert postgres_creative.get_blueprint(int(blueprint_id))["status"] == "draft"
    rolled_back = postgres_creative.get_project(project_id)
    assert int(rolled_back["revision"]) == revision_before
    assert rolled_back["locked_brief_id"] is None
    # Workflow domain: the previously approved submission is still approved.
    with isolated_system_database.connection() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT status FROM workflow_submissions "
                    "WHERE id = 'sub-approved'"
                )
            ).scalar_one()
            == "approved"
        )
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM workflow_task_events "
                    "WHERE event_type = 'submission.supersede'"
                )
            ).scalar_one()
            == 0
        )


# --------------------------------------------------- 4. writer project deletion


def test_writer_deletion_failure_keeps_both_domains_recoverable(
    isolated_system_database: SystemDatabase,
    postgres_creative: PostgresCreativeRepository,
    system_actor_repository: WorkflowRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer deletion is a resumable saga: a failure leaves no half-deletion.

    ``RollbackService`` fences the creative deletion with a workflow deletion
    marker and a creative tombstone, then deletes the workflow rows and finally
    completes both sides. The creative ``delete_project`` and the workflow
    ``delete_project_data`` are deliberately separate commits (the marker must be
    visible to the retry), so the guarantee under test is that a failing
    workflow deletion leaves the workflow domain *completely* intact and the
    deletion marker in place, and that the retry converges to a full deletion on
    both sides -- never a half-deleted workflow project.
    """

    project = _scope_project(postgres_creative, created_by=SYSTEM_ACTOR.user_id)
    project_id = int(project["id"])
    scoped_project_id = postgres_creative.workflow_project_id(project_id)
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO workflow_instances "
                "(id, workflow_type, resource_type, resource_id, project_id) "
                "VALUES ('wf-del', 'creative', 'project', :scoped, :scoped)"
            ),
            {"scoped": scoped_project_id},
        )
        connection.execute(
            text(
                "INSERT INTO workflow_tasks "
                "(id, workflow_id, task_type, role_code, project_id, status) "
                "VALUES ('task-del', 'wf-del', 'brief', 'creative_planner', "
                ":scoped, 'pool')"
            ),
            {"scoped": scoped_project_id},
        )

    owner_token = f"writer-rollback:{uuid.uuid4().hex}"
    marker = system_actor_repository.begin_project_deletion(
        scoped_project_id,
        owner_token=owner_token,
        requested_by=SYSTEM_ACTOR.user_id,
        project_snapshot={"id": project_id, "workflow_project_id": scoped_project_id,
                          "name": project["name"]},
    )
    assert marker["owner_token"] == owner_token

    def fail_delete(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected workflow deletion failure")

    monkeypatch.setattr(system_actor_repository, "delete_project_data", fail_delete)
    with pytest.raises(RuntimeError, match="injected workflow deletion failure"):
        system_actor_repository.delete_project_data(scoped_project_id)
    monkeypatch.undo()

    # Neither domain has a partial deletion: the workflow project is untouched
    # and its deletion marker still guards the retry.
    assert _count(isolated_system_database, "workflow_tasks") == 1
    assert _count(isolated_system_database, "workflow_instances") == 1
    assert (
        system_actor_repository.get_pending_project_deletion(scoped_project_id)
        is not None
    )
    assert postgres_creative.get_project(project_id) is not None

    # The retry converges to a full deletion in both domains.
    system_actor_repository.delete_project_data(scoped_project_id)
    postgres_creative.delete_project(
        project_id,
        requested_by=SYSTEM_ACTOR.user_id,
    )
    postgres_creative.complete_project_deletion(project_id)
    system_actor_repository.complete_project_deletion(scoped_project_id)

    assert _count(isolated_system_database, "workflow_tasks") == 0
    assert _count(isolated_system_database, "workflow_instances") == 0
    assert postgres_creative.get_project(project_id) is None
