# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.engine import make_url

from identity.repository import IdentityRepository
from identity.schema import Principal
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository
from workflow.transition_service import TransitionService


@pytest.fixture
def workflow_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> WorkflowRepository:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the production system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity = IdentityRepository(database)
    identity.seed_defaults()
    return WorkflowRepository(identity)


def _writer_actor(workflow_repository: WorkflowRepository) -> Principal:
    identity = workflow_repository.identity_repository
    username = f"writer-workflow-{uuid.uuid4().hex}"
    user = identity.create_user(username, "Writer Workflow", "hash")
    role = identity.get_role_by_code("system_admin")
    assert role is not None
    identity.replace_user_roles(user["id"], [role["id"]])
    return Principal(
        user_id=user["id"],
        username=username,
        display_name="Writer Workflow",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id=f"session-{uuid.uuid4().hex}",
    )


def test_original_project_tasks_use_project_as_the_only_resource(
    workflow_repository: WorkflowRepository,
) -> None:
    project_id = f"project-original-{uuid.uuid4().hex}"
    try:
        first = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        repeated = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )

        assert repeated["id"] == first["id"]
        assert first["topic_id"] is None
        assert first["project_id"] == project_id
        with workflow_repository.identity_repository.connect() as connection:
            workflow = connection.exec_driver_sql(
                "SELECT resource_type, resource_id FROM workflow_instances WHERE id = %s",
                (first["workflow_id"],),
            ).mappings().one()
        assert workflow["resource_type"] == "project"
        assert workflow["resource_id"] == project_id
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_external_use_detects_new_task_and_job(
    workflow_repository: WorkflowRepository,
) -> None:
    """A task/job created outside Writer must block deleting its project."""

    project_id = f"project-writer-external-{uuid.uuid4().hex}"
    try:
        workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        baseline = workflow_repository.writer_project_external_use_fingerprint(
            project_id
        )
        external = workflow_repository.create_task(
            workflow_id=f"external:{project_id}",
            task_type="script", role_code="screenwriter",
            project_id=project_id, actor=SYSTEM_ACTOR,
        )
        workflow_repository.link_task_job(external["id"], "external-job")

        assert workflow_repository.writer_project_external_use_fingerprint(
            project_id
        ) != baseline
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_external_use_excludes_declared_writer_task_and_job(
    workflow_repository: WorkflowRepository,
) -> None:
    """The ownership manifest must not classify Writer's own rows as external."""

    project_id = f"project-writer-owned-{uuid.uuid4().hex}"
    try:
        brief = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        baseline_state = workflow_repository.export_writer_project_external_use_state(
            project_id
        )
        owned = workflow_repository.create_next_task(
            brief["id"], "characters", "creative_planner", actor=SYSTEM_ACTOR,
            writer_operation_id="writer-action:characters.lock",
        )
        workflow_repository.link_task_job(owned["id"], "writer-job")
        manifest = {
            "operation_ids": ["writer-action:characters.lock"],
            "job_ids": ["writer-job"],
        }

        expected = workflow_repository.writer_project_external_use_baseline_fingerprint(
            baseline_state, manifest=manifest
        )
        current = workflow_repository.writer_project_external_use_fingerprint(
            project_id, baseline_state=baseline_state, manifest=manifest
        )

        assert current == expected
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_external_use_keeps_same_actor_untagged_event_external(
    workflow_repository: WorkflowRepository,
) -> None:
    """User identity alone must never make an unrelated event Writer-owned."""

    project_id = f"project-writer-event-{uuid.uuid4().hex}"
    try:
        brief = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        baseline_state = workflow_repository.export_writer_project_external_use_state(
            project_id
        )
        owned = workflow_repository.create_next_task(
            brief["id"], "characters", "creative_planner", actor=SYSTEM_ACTOR,
            writer_operation_id="writer-action:characters.lock",
        )
        manifest = {"operation_ids": ["writer-action:characters.lock"]}
        expected = workflow_repository.writer_project_external_use_baseline_fingerprint(
            baseline_state, manifest=manifest
        )
        with workflow_repository.identity_repository.transaction() as connection:
            workflow_repository._record_event(
                connection, owned["id"], "external.edit", SYSTEM_ACTOR.user_id,
                "pool", "pool", {"source": "another-page"},
            )

        assert workflow_repository.writer_project_external_use_fingerprint(
            project_id, baseline_state=baseline_state, manifest=manifest
        ) != expected
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_external_use_excludes_tagged_task_lifecycle(
    workflow_repository: WorkflowRepository,
) -> None:
    """Every Writer-owned task event must be identified by operation ID."""

    project_id = f"project-writer-lifecycle-{uuid.uuid4().hex}"
    operation_id = "writer-action:brief.persist"
    actor = _writer_actor(workflow_repository)
    try:
        baseline_state = workflow_repository.export_writer_project_external_use_state(
            project_id
        )
        task = workflow_repository.ensure_project_task(
            project_id,
            None,
            task_type="brief",
            role_code="creative_planner",
            actor=actor,
            writer_operation_id=operation_id,
        )
        workflow_repository.claim_task(
            task["id"], actor, writer_operation_id=operation_id
        )
        workflow_repository.start_task(
            task["id"], actor, writer_operation_id=operation_id
        )
        manifest = {"operation_ids": [operation_id]}

        expected = workflow_repository.writer_project_external_use_baseline_fingerprint(
            baseline_state, manifest=manifest
        )
        current = workflow_repository.writer_project_external_use_fingerprint(
            project_id, baseline_state=baseline_state, manifest=manifest
        )

        assert current == expected
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_external_use_excludes_tagged_auto_membership(
    workflow_repository: WorkflowRepository,
) -> None:
    """A Writer-owned claim may add membership without becoming external use."""

    project_id = f"project-writer-membership-{uuid.uuid4().hex}"
    operation_id = "writer-action:outline.claim"
    owner = _writer_actor(workflow_repository)
    identity = workflow_repository.identity_repository
    writer_user = identity.create_user(
        f"screenwriter-{uuid.uuid4().hex}", "Ordinary Screenwriter", "hash"
    )
    writer_role = identity.get_role_by_code("screenwriter")
    assert writer_role is not None
    identity.replace_user_roles(writer_user["id"], [writer_role["id"]])
    writer = Principal(
        user_id=writer_user["id"],
        username=str(writer_user["username"]),
        display_name=str(writer_user["display_name"]),
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="assigned",
        session_id=f"session-{uuid.uuid4().hex}",
    )
    try:
        workflow_repository.add_project_member(
            project_id, owner.user_id, "project_owner", owner
        )
        task = workflow_repository.create_task(
            f"project:{project_id}",
            "outline",
            "screenwriter",
            project_id=project_id,
            actor=owner,
        )
        baseline_state = (
            workflow_repository.export_writer_project_external_use_state(
                project_id
            )
        )

        workflow_repository.claim_task(
            task["id"], writer, writer_operation_id=operation_id
        )
        manifest = {
            "operation_ids": [operation_id],
            "task_ids": [str(task["id"])],
        }

        expected = (
            workflow_repository.writer_project_external_use_baseline_fingerprint(
                baseline_state, manifest=manifest
            )
        )
        current = workflow_repository.writer_project_external_use_fingerprint(
            project_id, baseline_state=baseline_state, manifest=manifest
        )

        assert current == expected
    finally:
        workflow_repository.delete_project_data(project_id)
        for user_id in (writer.user_id, owner.user_id):
            if identity.get_user(user_id) is not None:
                identity.soft_delete_user(user_id)


def test_writer_project_external_use_excludes_tagged_lock_transition(
    workflow_repository: WorkflowRepository,
) -> None:
    """Writer-owned lock and successor events must not look like external use."""

    class CreativeAdapter:
        @staticmethod
        def lock_without_review(*args, **kwargs):
            return {"resource": {"id": "brief-1", "status": "locked"}}

    project_id = f"project-writer-transition-{uuid.uuid4().hex}"
    operation_id = "writer-action:brief.lock"
    actor = _writer_actor(workflow_repository)
    try:
        baseline_state = workflow_repository.export_writer_project_external_use_state(
            project_id
        )
        task = workflow_repository.ensure_project_task(
            project_id,
            None,
            task_type="brief",
            role_code="creative_planner",
            actor=actor,
            writer_operation_id=operation_id,
        )
        workflow_repository.claim_task(
            task["id"], actor, writer_operation_id=operation_id
        )
        workflow_repository.start_task(
            task["id"], actor, writer_operation_id=operation_id
        )
        TransitionService(
            workflow_repository, creative_adapter=CreativeAdapter()
        ).complete_creative_without_review(
            task["id"],
            "brief",
            "brief-1",
            actor,
            writer_operation_id=operation_id,
        )
        manifest = {"operation_ids": [operation_id]}

        expected = workflow_repository.writer_project_external_use_baseline_fingerprint(
            baseline_state, manifest=manifest
        )
        current = workflow_repository.writer_project_external_use_fingerprint(
            project_id, baseline_state=baseline_state, manifest=manifest
        )

        assert current == expected
    finally:
        workflow_repository.delete_project_data(project_id)


def test_next_original_task_preserves_null_topic(
    workflow_repository: WorkflowRepository,
) -> None:
    project_id = f"project-original-{uuid.uuid4().hex}"
    try:
        brief = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        characters = workflow_repository.create_next_task(
            brief["id"], "characters", "creative_planner", actor=SYSTEM_ACTOR
        )

        assert characters["topic_id"] is None
        assert characters["project_id"] == brief["project_id"]
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_rollback_snapshot_restores_tasks_and_cancels_new_work(
    workflow_repository: WorkflowRepository,
) -> None:
    """Would catch restored creative content leaving workflow tasks advanced."""

    project_id = f"project-writer-rollback-{uuid.uuid4().hex}"
    writer_operation_id = "writer-rollback:rollback-test:action-test"
    try:
        original = workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="brief",
            role_code="creative_planner",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )
        before = workflow_repository.export_writer_rollback_state(project_id)
        with workflow_repository.identity_repository.transaction() as connection:
            connection.exec_driver_sql(
                "UPDATE workflow_tasks SET status = %s WHERE id = %s",
                ("in_progress", original["id"]),
            )
        created = workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="script",
            role_code="screenwriter",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )
        after_fingerprint = workflow_repository.writer_rollback_fingerprint(
            project_id
        )

        workflow_repository.restore_writer_rollback_state(
            project_id,
            before,
            expected_fingerprint=after_fingerprint,
            actor=SYSTEM_ACTOR,
            writer_operation_id=writer_operation_id,
        )

        with workflow_repository.identity_repository.connect() as connection:
            rows = {
                row["id"]: row["status"]
                for row in connection.exec_driver_sql(
                    "SELECT id, status FROM workflow_tasks WHERE project_id = %s",
                    (project_id,),
                ).mappings()
            }
            rollback_events = connection.exec_driver_sql(
                "SELECT details_json FROM workflow_task_events "
                "WHERE task_id = %s AND event_type = %s",
                (original["id"], "writer.rollback"),
            ).mappings().all()
        assert rows[original["id"]] == "pool"
        assert rows[created["id"]] == "cancelled"
        assert len(rollback_events) == 1
        assert rollback_events[0]["details_json"]["writer_operation_id"] == (
            writer_operation_id
        )
        assert (
            workflow_repository.writer_rollback_fingerprint(project_id)
            == before["fingerprint"]
        )
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_project_delete_rejects_workflow_rows_added_after_action(
    workflow_repository: WorkflowRepository,
) -> None:
    """Would catch rollback deleting workflow events absent from its postcondition."""

    project_id = f"project-writer-delete-{uuid.uuid4().hex}"
    try:
        brief = workflow_repository.ensure_project_task(
            project_id,
            None,
            task_type="brief",
            role_code="creative_planner",
            actor=SYSTEM_ACTOR,
        )
        expected = workflow_repository.writer_project_deletion_fingerprint(
            project_id
        )
        characters = workflow_repository.create_next_task(
            brief["id"], "characters", "creative_planner", actor=SYSTEM_ACTOR
        )

        with pytest.raises(ValueError, match="workflow deletion state changed"):
            workflow_repository.delete_project_data(
                project_id, expected_writer_fingerprint=expected
            )

        assert workflow_repository.get_task(brief["id"]) is not None
        assert workflow_repository.get_task(characters["id"]) is not None
    finally:
        workflow_repository.delete_project_data(project_id)


def test_project_deletion_marker_blocks_new_workflow_rows_until_completed(
    workflow_repository: WorkflowRepository,
) -> None:
    """A durable deletion marker must reject recreating deleted project rows."""

    project_id = f"project-deletion-marker-{uuid.uuid4().hex}"
    workflow_repository.ensure_project_task(
        project_id,
        None,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )

    workflow_repository.begin_project_deletion(project_id)
    workflow_repository.delete_project_data(project_id)

    assert workflow_repository.is_project_deleting(project_id) is True
    with pytest.raises(ValueError, match="workflow project is being deleted"):
        workflow_repository.ensure_project_task(
            project_id,
            None,
            task_type="brief",
            role_code="creative_planner",
            actor=SYSTEM_ACTOR,
        )
    with pytest.raises(ValueError, match="workflow project is being deleted"):
        workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="brief",
            role_code="creative_planner",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )

    workflow_repository.complete_project_deletion(project_id)

    assert workflow_repository.is_project_deleting(project_id) is False
    recreated = workflow_repository.ensure_project_task(
        project_id,
        None,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )
    assert recreated["project_id"] == project_id
    workflow_repository.delete_project_data(project_id)
    workflow_repository.complete_project_deletion(project_id)


def test_project_deletion_abort_only_releases_the_owning_marker(
    workflow_repository: WorkflowRepository,
) -> None:
    """A failed rollback must not remove another deletion operation's fence."""

    project_id = f"project-deletion-owner-{uuid.uuid4().hex}"
    first = workflow_repository.begin_project_deletion(
        project_id,
        owner_token="rollback-owner-a",
        requested_by=SYSTEM_ACTOR.user_id,
        project_snapshot={"id": project_id, "name": "待删除项目"},
    )
    second = workflow_repository.begin_project_deletion(
        project_id,
        owner_token="rollback-owner-b",
        requested_by="another-user",
        project_snapshot={"id": project_id, "name": "并发快照"},
    )

    assert first["owner_token"] == "rollback-owner-a"
    assert second["owner_token"] == "rollback-owner-a"
    assert second["requested_by"] == SYSTEM_ACTOR.user_id
    assert second["project_snapshot"]["name"] == "待删除项目"
    assert workflow_repository.abort_project_deletion(
        project_id, owner_token="rollback-owner-b"
    ) is False
    assert workflow_repository.is_project_deleting(project_id) is True
    assert workflow_repository.abort_project_deletion(
        project_id, owner_token="rollback-owner-a"
    ) is True
    assert workflow_repository.is_project_deleting(project_id) is False


def test_project_deletion_marker_fences_existing_task_writes_before_delete(
    workflow_repository: WorkflowRepository,
) -> None:
    """The marker phase must block child task rows before cleanup starts."""

    project_id = f"project-deletion-fence-{uuid.uuid4().hex}"
    task = workflow_repository.ensure_project_task(
        project_id,
        None,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )

    workflow_repository.begin_project_deletion(project_id)

    with pytest.raises(ValueError, match="workflow project is being deleted"):
        workflow_repository.create_next_task(
            task["id"],
            "characters",
            "creative_planner",
            actor=SYSTEM_ACTOR,
        )
    with pytest.raises(ValueError, match="workflow project is being deleted"):
        workflow_repository.link_task_job(task["id"], "late-writer-job")

    workflow_repository.delete_project_data(project_id)
    workflow_repository.complete_project_deletion(project_id)

    assert workflow_repository.is_project_deleting(project_id) is False


def test_project_deletion_waits_for_inflight_workflow_creation(
    workflow_repository: WorkflowRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deletion must serialize with a task transaction and remove its committed rows."""

    project_id = f"project-deletion-race-{uuid.uuid4().hex}"
    creation_entered = threading.Event()
    release_creation = threading.Event()
    deletion_finished = threading.Event()
    original_ensure_workflow = workflow_repository._ensure_workflow

    def pause_after_project_guard(*args, **kwargs):
        creation_entered.set()
        assert release_creation.wait(timeout=5)
        return original_ensure_workflow(*args, **kwargs)

    monkeypatch.setattr(
        workflow_repository, "_ensure_workflow", pause_after_project_guard
    )

    def create_task() -> None:
        workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="brief",
            role_code="creative_planner",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )

    def delete_project() -> None:
        workflow_repository.delete_project_data(project_id)
        deletion_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        create_future = pool.submit(create_task)
        assert creation_entered.wait(timeout=5)
        delete_future = pool.submit(delete_project)
        assert deletion_finished.wait(timeout=0.2) is False
        release_creation.set()
        create_future.result(timeout=5)
        delete_future.result(timeout=5)

    assert workflow_repository.writer_project_data_exists(project_id) is False
    assert workflow_repository.is_project_deleting(project_id) is False
