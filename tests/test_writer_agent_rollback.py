# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from writer_agent.api import build_writer_agent_router
from writer_agent.rollback_service import RollbackService
from writer_agent.schema import WriterActionError
from writer_agent.store import WriterAgentStore


class StableWorkflowRepository:
    def __init__(self) -> None:
        self.states: dict[str, dict] = {}
        self.deleting_projects: set[str] = set()
        self.fail_restore_once = False
        self.rollback_operation_ids: list[str | None] = []
        self.rollback_changes_deletion_fingerprint = False
        self.deletion_fingerprint_suffixes: dict[str, str] = {}
        self.delete_expected_fingerprints: list[str | None] = []
        self.deletion_markers: dict[str, dict] = {}

    def export_writer_rollback_state(self, project_id: str) -> dict:
        return self.states.setdefault(
            str(project_id),
            {"project_id": str(project_id), "tables": {}, "fingerprint": "workflow-0"},
        )

    def writer_rollback_fingerprint(self, project_id: str) -> str:
        return self.export_writer_rollback_state(project_id)["fingerprint"]

    def writer_project_deletion_fingerprint(self, project_id: str) -> str:
        state = self.states.get(str(project_id))
        if state is None:
            return "workflow-empty"
        return (
            str(state["fingerprint"])
            + self.deletion_fingerprint_suffixes.get(str(project_id), "")
        )

    def writer_project_external_use_fingerprint(
        self, _project_id: str, **_kwargs,
    ) -> str:
        return "workflow-members-initial"

    def writer_project_data_exists(self, project_id: str) -> bool:
        return str(project_id) in self.states

    def restore_writer_rollback_state(
        self,
        project_id: str,
        preimage: dict,
        *,
        expected_fingerprint: str,
        actor,
        writer_operation_id: str | None = None,
    ) -> dict:
        self.rollback_operation_ids.append(writer_operation_id)
        if self.fail_restore_once:
            self.fail_restore_once = False
            raise RuntimeError("workflow store unavailable")
        current_fingerprint = self.writer_rollback_fingerprint(project_id)
        if current_fingerprint == preimage.get("fingerprint"):
            return dict(preimage)
        if current_fingerprint != expected_fingerprint:
            raise ValueError("workflow rollback state changed")
        self.states[str(project_id)] = dict(preimage)
        if self.rollback_changes_deletion_fingerprint:
            self.deletion_fingerprint_suffixes[str(project_id)] = (
                f":rollback-event:{writer_operation_id}"
            )
        return dict(preimage)

    def delete_project_data(
        self,
        project_id: str,
        *,
        expected_writer_fingerprint: str | None = None,
        expected_writer_external_fingerprint: str | None = None,
        writer_external_baseline_state: dict | None = None,
        writer_ownership_manifest: dict | None = None,
    ) -> dict[str, int]:
        del writer_external_baseline_state, writer_ownership_manifest
        self.delete_expected_fingerprints.append(expected_writer_fingerprint)
        if (
            expected_writer_external_fingerprint is not None
            and expected_writer_external_fingerprint
            != self.writer_project_external_use_fingerprint(project_id)
        ):
            raise ValueError("workflow project has external use")
        if (
            expected_writer_fingerprint is not None
            and self.writer_project_deletion_fingerprint(project_id)
            != expected_writer_fingerprint
        ):
            raise ValueError("workflow deletion state changed")
        self.states.pop(str(project_id), None)
        self.deleting_projects.add(str(project_id))
        return {"workflow_count": 0, "task_count": 0}

    def begin_project_deletion(
        self,
        project_id: str,
        *,
        owner_token: str = "",
        requested_by: str = "",
        project_snapshot: dict | None = None,
    ) -> dict:
        marker = self.deletion_markers.setdefault(
            str(project_id),
            {
                "project_id": str(project_id),
                "owner_token": owner_token,
                "requested_by": requested_by,
                "project_snapshot": dict(project_snapshot or {}),
            },
        )
        self.deleting_projects.add(str(project_id))
        return dict(marker)

    def abort_project_deletion(self, project_id: str, *, owner_token: str) -> bool:
        marker = self.deletion_markers.get(str(project_id))
        if marker is None or marker["owner_token"] != owner_token:
            return False
        self.deletion_markers.pop(str(project_id), None)
        self.deleting_projects.discard(str(project_id))
        return True

    def complete_project_deletion(
        self, project_id: str, *, owner_token: str | None = None
    ) -> None:
        marker = self.deletion_markers.get(str(project_id))
        if (
            marker is not None
            and owner_token is not None
            and marker["owner_token"] != owner_token
        ):
            return
        self.deletion_markers.pop(str(project_id), None)
        self.deleting_projects.discard(str(project_id))

    def is_active_project_member(self, _project_id: str, _actor: object) -> bool:
        return True


def test_service_owned_rollback_renews_its_session_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would catch a long direct rollback losing its own mutation lease."""

    store = WriterAgentStore(tmp_path / "rollback-heartbeat.db")
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "初始材料", "回档租约")
    checkpoint = store.begin_turn(
        session["id"], actor.user_id, "heartbeat-turn",
        original_message={"message": "第一轮"}, content_hash="heartbeat-turn",
    )
    saved = store.save_turn(
        session["id"], actor.user_id,
        [
            *session["messages"],
            {"role": "user", "content": "第一轮", "message_id": "heartbeat-turn"},
            {"role": "assistant", "content": "已处理"},
        ],
        None,
        client_message_id="heartbeat-turn",
        turn_checkpoint_id=checkpoint["id"],
    )
    store.complete_turn(checkpoint["id"])
    service = RollbackService(store, None, None)
    service._mutation_lease_seconds = 0.12
    entered = threading.Event()
    release = threading.Event()
    original_begin = store.begin_rollback
    original_acquire = store.acquire_session_mutation

    def short_service_lease(*args, **kwargs):
        if str(kwargs.get("owner_token") or "").startswith("rollback-"):
            kwargs["lease_seconds"] = service._mutation_lease_seconds
        return original_acquire(*args, **kwargs)

    def slow_begin(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original_begin(*args, **kwargs)

    monkeypatch.setattr(store, "begin_rollback", slow_begin)
    monkeypatch.setattr(store, "acquire_session_mutation", short_service_lease)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.rollback,
            session["id"],
            "heartbeat-turn",
            actor=actor,
            mode="rollback",
            client_request_id="heartbeat-rollback",
            expected_history_revision=saved["history_revision"],
        )
        assert entered.wait(timeout=5)
        time.sleep(0.2)
        competing_error = None
        try:
            store.acquire_session_mutation(
                session["id"], actor.user_id,
                owner_token="competing-writer",
                mutation_kind="message",
                lease_seconds=0.12,
            )
        except WriterActionError as exc:
            competing_error = exc
        finally:
            if competing_error is None:
                store.release_session_mutation(
                    session["id"], owner_token="competing-writer"
                )
            release.set()
        first_error = None
        try:
            restored = future.result(timeout=5)
        except WriterActionError as exc:
            first_error = exc
            restored = None

    assert competing_error is not None
    assert competing_error.code == "SESSION_MUTATION_BUSY"
    assert first_error is None
    assert restored is not None


class ProjectWritePermissions:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def can_create_project_content(
        self, _actor: object, _project_id: str, *, active_member: bool
    ) -> bool:
        return self.allowed and active_member


class FixedWorkflowIdentityResolver:
    def __init__(self, workflow_project_id: str) -> None:
        self.workflow_project_id = workflow_project_id

    def project_id(self, _local_project_id: int) -> str:
        return self.workflow_project_id


def test_workflow_ownership_manifest_includes_every_writer_step_operation(
    tmp_path: Path,
) -> None:
    """Unbound Workflow events must be owned by exact Writer operation IDs."""

    store = WriterAgentStore(tmp_path / "writer-manifest.db")
    workflow = StableWorkflowRepository()
    creative = MagicMock()
    creative.writer_action_workflow_task_ids.return_value = set()
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(),
    )
    session = store.create("writer-1", "创建项目", "归属清单")
    action = store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="creative.generate",
        project_id=17,
        idempotency_key="manifest-action",
        request_hash="manifest-action",
        payload={},
        expected_context={"project_revision": 0},
        source_snapshot_id=None,
    )
    store.upsert_step(
        action_id=action["id"],
        step_key="brief.persist",
        stage="brief",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id="writer-action:brief.persist",
        depends_on=[],
        attempt=1,
        step_order=0,
    )

    manifest = service._workflow_ownership_manifest(
        {
            "action_id": action["id"],
            "project_id": 17,
            "session_id": session["id"],
        }
    )

    assert manifest["operation_ids"] == ["writer-action:brief.persist"]


def test_workflow_ownership_manifest_includes_all_actions_in_rollback_batch(
    tmp_path: Path,
) -> None:
    """Would catch ownership being limited to the create action family."""

    store = WriterAgentStore(tmp_path / "writer-multi-action-manifest.db")
    workflow = StableWorkflowRepository()
    creative = MagicMock()
    creative.writer_action_workflow_task_ids.side_effect = (
        lambda _project_id, action_id: {f"task:{action_id}"}
    )
    service = RollbackService(store, creative, workflow)
    session = store.create("writer-1", "创建并续写", "跨动作归属清单")
    actions = []
    for index, action_type in enumerate(
        ("project.create_original", "creative.run_plan"), start=1
    ):
        action = store.create_action(
            session_id=session["id"],
            actor_user_id="writer-1",
            action_type=action_type,
            project_id=17,
            idempotency_key=f"manifest-action-{index}",
            request_hash=f"manifest-action-{index}",
            payload={},
            expected_context={"project_revision": 0},
            source_snapshot_id=None,
        )
        store.upsert_step(
            action_id=action["id"],
            step_key="brief.persist",
            stage="brief",
            episode_start=None,
            episode_end=None,
            status="pending",
            operation_id=f"writer-action-{index}:brief.persist",
            depends_on=[],
            attempt=1,
            step_order=0,
        )
        actions.append(action)

    manifest = service._workflow_ownership_manifest(
        {
            "action_id": actions[0]["id"],
            "project_id": 17,
            "session_id": session["id"],
        },
        actions=actions,
        rollback_operation_ids=["writer-rollback:rollback-1:action-2"],
    )

    assert manifest == {
        "task_ids": sorted(f"task:{action['id']}" for action in actions),
        "job_ids": [],
        "operation_ids": [
            "writer-action-1:brief.persist",
            "writer-action-2:brief.persist",
            "writer-rollback:rollback-1:action-2",
        ],
    }


def ready_project(repository: CreativeRepository) -> dict:
    project = repository.create_original_project(
        seed_text="一个需要回档的故事",
        name="回档前项目",
        target_episode_count=12,
        target_platform="抖音",
        genre="悬疑",
        target_audience="年轻人",
        constraints={},
        reference_refs=[],
        created_by="writer-1",
        creation_request_id="rollback-project",
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def completed_new_project_action(
    tmp_path: Path,
) -> tuple[
    RollbackService,
    WriterAgentStore,
    CreativeRepository,
    StableWorkflowRepository,
    object,
    dict,
    dict,
    dict,
]:
    store = WriterAgentStore(tmp_path / "writer-new-project.db")
    creative = CreativeRepository(tmp_path / "creative-new-project.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(
        store, creative, workflow, permission_service=ProjectWritePermissions()
    )
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "创建项目", "新项目异常回档")
    checkpoint = store.begin_turn(
        session["id"], actor.user_id, "create-message",
        original_message={"message": "创建原创项目"},
        content_hash="create-message",
    )
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="create-action", request_hash="create-action",
        payload={}, expected_context={"session_revision": 0},
        source_snapshot_id=None, turn_checkpoint_id=checkpoint["id"],
    )
    service.prepare_action_undo(action, actor)
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    project = ready_project(creative)
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {"workflow_tasks": [{"id": "created-project-task"}]},
        "fingerprint": "workflow-created-project",
    }
    action = store.attach_project_to_action(
        action["id"], project["id"], project_context={"project_revision": 0}
    )
    service.capture_new_project_ownership(action, actor)
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    store.record_action_result(action["id"], {"project_id": project["id"]})
    terminal = store.finish_action(action["id"], status="succeeded")
    service.finalize_action_undo(terminal, actor)
    current = store.get(session["id"], actor.user_id)
    saved = store.save_turn(
        session["id"], actor.user_id,
        [
            *current["messages"],
            {"role": "user", "content": "创建原创项目", "message_id": "create-message"},
            {"role": "assistant", "content": "已创建"},
        ],
        None,
        client_message_id="create-message",
        turn_checkpoint_id=checkpoint["id"],
    )
    store.complete_turn(checkpoint["id"])
    return service, store, creative, workflow, actor, session, action, saved


def completed_project_edit(
    tmp_path: Path,
    *,
    workflow_project_id: str | None = None,
) -> tuple[RollbackService, WriterAgentStore, CreativeRepository, dict, dict, object]:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(),
        workflow_identity_resolver=(
            FixedWorkflowIdentityResolver(workflow_project_id)
            if workflow_project_id is not None
            else None
        ),
    )
    actor = SimpleNamespace(user_id="writer-1")
    project = ready_project(creative)
    session = store.create("writer-1", "原始材料", "回档会话")
    session = store.bind_project(
        session["id"], project["id"], expected_context_revision=0
    )
    checkpoint = store.begin_turn(
        session["id"],
        "writer-1",
        "message-1",
        original_message={"message": "修改项目", "display_user_message": True},
        content_hash="message-hash",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="edit-project",
        request_hash="edit-project",
        payload={"target_stage": "brief"},
        expected_context={"session_revision": session["context_revision"]},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    service.prepare_action_undo(action, actor)
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    creative.update_project(
        project["id"], {"name": "动作完成后的项目"}, expected_revision=0
    )
    resolved_workflow_project_id = workflow_project_id or str(project["id"])
    workflow.states[resolved_workflow_project_id] = {
        "project_id": resolved_workflow_project_id,
        "tables": {"workflow_tasks": [{"id": "task-after-action"}]},
        "fingerprint": "workflow-1",
    }
    store.finish_action(action["id"], status="succeeded")
    service.finalize_action_undo(
        store.require_action(action["id"], "writer-1"), actor
    )
    saved = store.save_turn(
        session["id"],
        "writer-1",
        [
            *session["messages"],
            {"role": "user", "content": "修改项目", "message_id": "message-1"},
            {"role": "assistant", "content": "已修改"},
        ],
        None,
        client_message_id="message-1",
    )
    store.complete_turn(checkpoint["id"])
    return service, store, creative, saved, checkpoint, actor


def test_rollback_persists_and_reuses_one_resolved_workflow_project_id(
    tmp_path: Path,
) -> None:
    workflow_project_id = "creative:test-store:project:1"
    service, store, _creative, session, checkpoint, actor = completed_project_edit(
        tmp_path,
        workflow_project_id=workflow_project_id,
    )
    action = store.list_actions_for_turns([checkpoint["id"]])[0]
    undo = store.require_action_undo(action["id"])

    assert undo["preimage"]["workflow_project_id"] == workflow_project_id
    assert undo["postcondition"]["workflow_project_id"] == workflow_project_id

    restored = service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="scoped-workflow-rollback",
        expected_history_revision=session["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert workflow_project_id in service.workflow_repository.states
    assert str(session["project_id"]) not in service.workflow_repository.states


def test_rollback_service_restores_project_and_conversation(tmp_path: Path) -> None:
    """Would catch successful rollback restoring only the visible conversation."""

    service, store, creative, session, checkpoint, actor = completed_project_edit(
        tmp_path
    )

    preview = service.preview(session["id"], "message-1", actor=actor)
    restored = service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-1",
        expected_history_revision=preview["history_revision"],
    )

    assert preview["messages_to_remove"] == 2
    assert preview["actions_to_rollback"] == 1
    assert creative.get_project(session["project_id"])["name"] == "回档前项目"
    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert store.require_turn(
        session["id"], checkpoint["user_message_id"], "writer-1"
    )["status"] == "rolled_back"


def test_existing_project_rollback_event_uses_stable_operation_id(
    tmp_path: Path,
) -> None:
    """Would catch retry-created Workflow rollback events lacking ownership."""

    service, store, _creative, session, checkpoint, actor = completed_project_edit(
        tmp_path
    )
    action = store.list_actions_for_turns([checkpoint["id"]])[0]

    service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-operation-id",
        expected_history_revision=session["history_revision"],
    )

    rollback = store.require_rollback_by_request(
        actor.user_id, "rollback-operation-id"
    )
    assert service.workflow_repository.rollback_operation_ids == [
        f"writer-rollback:{rollback['id']}:{action['id']}"
    ]


def test_rollback_service_rejects_external_project_changes(tmp_path: Path) -> None:
    """Would catch rollback overwriting a newer edit from another project entry."""

    service, store, creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    creative.update_project(
        session["project_id"], {"name": "其他页面的新修改"}, expected_revision=1
    )
    preview = service.preview(session["id"], "message-1", actor=actor)

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"],
            "message-1",
            actor=actor,
            mode="rollback",
            client_request_id="rollback-conflict",
            expected_history_revision=session["history_revision"],
        )

    assert preview["can_rollback"] is False
    assert "项目或任务状态已从其他入口更新" in preview["blockers"]
    assert exc.value.code == "ROLLBACK_CONFLICT"
    assert creative.get_project(session["project_id"])["name"] == "其他页面的新修改"
    audit = store.require_rollback_by_request("writer-1", "rollback-conflict")
    assert audit["error_code"] == "ROLLBACK_CONFLICT"
    assert audit["error_message"]


def test_rollback_service_rechecks_current_project_write_permission(
    tmp_path: Path,
) -> None:
    """Would catch rollback overwriting project state after the actor lost write access."""

    service, store, creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    service.permission_service = ProjectWritePermissions(allowed=False)

    preview = service.preview(session["id"], "message-1", actor=actor)
    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"],
            "message-1",
            actor=actor,
            mode="rollback",
            client_request_id="rollback-after-revocation",
            expected_history_revision=session["history_revision"],
        )

    assert preview["can_rollback"] is False
    assert "当前账号已无权修改受影响项目" in preview["blockers"]
    assert exc.value.code == "ROLLBACK_PERMISSION_DENIED"
    assert creative.get_project(session["project_id"])["name"] == "动作完成后的项目"
    assert store.require_turn(session["id"], "message-1", actor.user_id)["status"] == "completed"


def test_rollback_api_returns_403_after_project_write_permission_is_revoked(
    tmp_path: Path,
) -> None:
    """Would catch the HTTP endpoint bypassing the service's live permission check."""

    _service, store, creative, session, _checkpoint, _actor = completed_project_edit(
        tmp_path
    )
    actor = Principal(
        user_id="writer-1",
        username="writer-1",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"script.review.generate"}),
        data_scope="project",
        session_id="rollback-permission-session",
    )
    workflow = StableWorkflowRepository()
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: actor
    app.include_router(
        build_writer_agent_router(
            tmp_path,
            store=store,
            creative_repository=creative,
            workflow_repository=workflow,
            permission_service=ProjectWritePermissions(allowed=False),
        )
    )

    response = TestClient(app).post(
        f"/api/writer-agent/sessions/{session['id']}/messages/message-1/rollback",
        json={
            "client_request_id": "rollback-permission-api",
            "expected_history_revision": session["history_revision"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ROLLBACK_PERMISSION_DENIED"


def test_rollback_service_resumes_after_creative_restore_and_workflow_failure(
    tmp_path: Path,
) -> None:
    """Would catch a partial cross-store rollback becoming impossible to retry."""

    service, store, creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    service.workflow_repository.fail_restore_once = True

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"],
            "message-1",
            actor=actor,
            mode="rollback",
            client_request_id="rollback-partial-retry",
            expected_history_revision=session["history_revision"],
        )

    action = store.list_session_actions(session["id"])[0]
    assert exc.value.code == "ROLLBACK_PARTIAL_FAILURE"
    assert creative.get_project(session["project_id"])["name"] == "回档前项目"
    assert store.require_action_undo(action["id"])["status"] == "creative_restored"
    assert len(store.get(session["id"], actor.user_id)["messages"]) == 3
    retry_preview = service.preview(session["id"], "message-1", actor=actor)
    assert retry_preview["can_rollback"] is True

    restored = service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-partial-retry",
        expected_history_revision=session["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert store.require_action_undo(action["id"])["status"] == "compensated"


def test_rollback_recovers_when_creative_restore_committed_before_marker(
    tmp_path: Path,
) -> None:
    """Would catch a crash after Creative commit but before the Writer marker."""

    service, store, creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    action = store.list_session_actions(session["id"])[0]
    undo = store.require_action_undo(action["id"])
    creative.restore_writer_rollback_state(
        session["project_id"],
        undo["preimage"]["creative"],
        expected_fingerprint=undo["postcondition"]["creative_fingerprint"],
    )

    restored = service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-after-creative-commit",
        expected_history_revision=session["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert store.require_action_undo(action["id"])["status"] == "compensated"


def test_rollback_recovers_when_workflow_restore_committed_before_marker(
    tmp_path: Path,
) -> None:
    """Would catch a crash after Workflow commit but before compensation marking."""

    service, store, _creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    action = store.list_session_actions(session["id"])[0]
    undo = store.require_action_undo(action["id"])
    service.workflow_repository.restore_writer_rollback_state(
        str(session["project_id"]),
        undo["workflow_preimage"],
        expected_fingerprint=undo["postcondition"]["workflow_fingerprint"],
        actor=actor,
    )

    restored = service.rollback(
        session["id"],
        "message-1",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-after-workflow-commit",
        expected_history_revision=session["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert store.require_action_undo(action["id"])["status"] == "compensated"


def test_rollback_service_requests_active_action_cancellation_before_restore(
    tmp_path: Path,
) -> None:
    """Would catch rollback reporting busy without first requesting cancellation."""

    store = WriterAgentStore(tmp_path / "writer-agent-active.db")
    creative = CreativeRepository(tmp_path / "creative-active.db")
    workflow = StableWorkflowRepository()
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "原始材料", "停止任务并回档")
    checkpoint = store.begin_turn(
        session["id"],
        actor.user_id,
        "message-active",
        original_message={"message": "关联项目"},
        content_hash="message-active",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="project.link",
        project_id=17,
        idempotency_key="active-link",
        request_hash="active-link",
        payload={},
        expected_context={"session_revision": 0},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    store.claim_pending_action(action["id"], expected_revision=1)
    saved = store.save_turn(
        session["id"],
        actor.user_id,
        [
            *session["messages"],
            {"role": "user", "content": "关联项目", "message_id": "message-active"},
            {"role": "assistant", "content": "正在关联"},
        ],
        None,
        client_message_id="message-active",
        turn_checkpoint_id=checkpoint["id"],
    )
    action_runner = MagicMock()
    action_runner.cancel.side_effect = lambda action_id, _actor: store.finish_action(
        action_id, status="cancelled"
    )
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(),
        action_runner=action_runner,
    )

    restored = service.rollback(
        session["id"],
        "message-active",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-active",
        expected_history_revision=saved["history_revision"],
    )

    action_runner.cancel.assert_called_once_with(action["id"], actor)
    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert store.require_action(action["id"], actor.user_id)["status"] == "rolled_back"


def test_rollback_rechecks_active_action_permission_before_cancelling(
    tmp_path: Path,
) -> None:
    """Would catch a revoked user stopping project work before permission denial."""

    store = WriterAgentStore(tmp_path / "writer-agent-revoked-active.db")
    creative = CreativeRepository(tmp_path / "creative-revoked-active.db")
    workflow = StableWorkflowRepository()
    actor = SimpleNamespace(user_id="writer-1", permissions=frozenset())
    project = ready_project(creative)
    session = store.create(actor.user_id, "原始材料", "撤权后停止任务")
    checkpoint = store.begin_turn(
        session["id"],
        actor.user_id,
        "message-active-revoked",
        original_message={"message": "修改项目"},
        content_hash="message-active-revoked",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="active-revoked",
        request_hash="active-revoked",
        payload={"target_stage": "brief"},
        expected_context={"session_revision": 0},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    store.claim_pending_action(action["id"], expected_revision=1)
    saved = store.save_turn(
        session["id"],
        actor.user_id,
        [
            *session["messages"],
            {
                "role": "user",
                "content": "修改项目",
                "message_id": "message-active-revoked",
            },
            {"role": "assistant", "content": "正在修改"},
        ],
        None,
        client_message_id="message-active-revoked",
        turn_checkpoint_id=checkpoint["id"],
    )
    action_runner = MagicMock()
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(allowed=False),
        action_runner=action_runner,
    )

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"],
            "message-active-revoked",
            actor=actor,
            mode="rollback",
            client_request_id="rollback-active-revoked",
            expected_history_revision=saved["history_revision"],
        )

    assert exc.value.code == "ROLLBACK_PERMISSION_DENIED"
    action_runner.cancel.assert_not_called()
    assert store.require_action(action["id"], actor.user_id)["status"] == "queued"


def test_rollback_skips_cancelled_action_that_never_started(
    tmp_path: Path,
) -> None:
    """Would catch a no-op cancelled action permanently blocking its turn rollback."""

    store = WriterAgentStore(tmp_path / "writer-agent-unstarted-cancel.db")
    creative = CreativeRepository(tmp_path / "creative-unstarted-cancel.db")
    workflow = StableWorkflowRepository()
    actor = SimpleNamespace(user_id="writer-1")
    project = ready_project(creative)
    session = store.create(actor.user_id, "原始材料", "未启动取消")
    checkpoint = store.begin_turn(
        session["id"],
        actor.user_id,
        "message-unstarted-cancel",
        original_message={"message": "生成后取消"},
        content_hash="message-unstarted-cancel",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="unstarted-cancel",
        request_hash="unstarted-cancel",
        payload={"target_stage": "brief"},
        expected_context={"session_revision": 0},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    cancelled, requested = store.request_action_cancellation_once(
        action["id"],
        expected_revision=1,
        client_request_id="cancel-before-start",
    )
    assert requested is True
    assert cancelled["status"] == "cancelled"
    saved = store.save_turn(
        session["id"],
        actor.user_id,
        [
            *session["messages"],
            {
                "role": "user",
                "content": "生成后取消",
                "message_id": "message-unstarted-cancel",
            },
            {"role": "assistant", "content": "已取消"},
        ],
        None,
        client_message_id="message-unstarted-cancel",
        turn_checkpoint_id=checkpoint["id"],
    )
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(),
    )

    restored = service.rollback(
        session["id"],
        "message-unstarted-cancel",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-unstarted-cancel",
        expected_history_revision=saved["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]
    assert creative.get_project(project["id"])["name"] == "回档前项目"


def test_rollback_skips_action_failed_before_any_side_effect(
    tmp_path: Path,
) -> None:
    """Would catch undo-preparation failure making a conversation irreversible."""

    store = WriterAgentStore(tmp_path / "writer-agent-prepare-failed.db")
    creative = CreativeRepository(tmp_path / "creative-prepare-failed.db")
    workflow = StableWorkflowRepository()
    actor = SimpleNamespace(user_id="writer-1")
    project = ready_project(creative)
    session = store.create(actor.user_id, "原始材料", "撤销点建立失败")
    checkpoint = store.begin_turn(
        session["id"],
        actor.user_id,
        "message-prepare-failed",
        original_message={"message": "修改项目"},
        content_hash="message-prepare-failed",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="prepare-failed",
        request_hash="prepare-failed",
        payload={"target_stage": "brief"},
        expected_context={"session_revision": 0},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    store.fail_action(
        action["id"],
        code="ROLLBACK_PREPARE_FAILED",
        message="无法建立动作撤销点",
        retryable=True,
    )
    saved = store.save_turn(
        session["id"],
        actor.user_id,
        [
            *session["messages"],
            {
                "role": "user",
                "content": "修改项目",
                "message_id": "message-prepare-failed",
            },
            {"role": "assistant", "content": "执行失败"},
        ],
        None,
        client_message_id="message-prepare-failed",
        turn_checkpoint_id=checkpoint["id"],
    )
    service = RollbackService(
        store,
        creative,
        workflow,
        permission_service=ProjectWritePermissions(),
    )

    restored = service.rollback(
        session["id"],
        "message-prepare-failed",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-prepare-failed",
        expected_history_revision=saved["history_revision"],
    )

    assert [message["kind"] for message in restored["messages"]] == ["material"]


@pytest.mark.parametrize("workflow_delete_committed", [False, True])
def test_rollback_service_removes_a_project_created_by_the_turn(
    tmp_path: Path, workflow_delete_committed: bool,
) -> None:
    """Would catch rollback leaving a newly created project in project lists."""

    store = WriterAgentStore(tmp_path / "writer-agent.db")
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(
        store, creative, workflow, permission_service=ProjectWritePermissions()
    )
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create("writer-1", "创建项目", "新项目会话")
    checkpoint = store.begin_turn(
        session["id"],
        "writer-1",
        "create-message",
        original_message={"message": "创建原创项目"},
        content_hash="create-message",
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="project.create_original",
        project_id=None,
        idempotency_key="create-action",
        request_hash="create-action",
        payload={},
        expected_context={"session_revision": 0},
        source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    service.prepare_action_undo(action, actor)
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    project = ready_project(creative)
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {"workflow_tasks": [{"id": "created-project-task"}]},
        "fingerprint": "workflow-created-project",
    }
    action = store.attach_project_to_action(
        action["id"], project["id"], project_context={"project_revision": 0}
    )
    service.capture_new_project_ownership(action, actor)
    creative.create_blueprints(
        project["id"],
        [{"content": {"title": "动作生成的 Brief"}}],
        direction_type="original",
        operation_id=f"{action['id']}:brief.persist",
        first_root_only=True,
    )
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    store.record_action_result(action["id"], {"project_id": project["id"]})
    terminal = store.finish_action(action["id"], status="succeeded")
    service.finalize_action_undo(terminal, actor)
    current = store.get(session["id"], "writer-1")
    saved = store.save_turn(
        session["id"],
        "writer-1",
        [
            *current["messages"],
            {"role": "user", "content": "创建原创项目", "message_id": "create-message"},
            {"role": "assistant", "content": "已创建"},
        ],
        None,
        client_message_id="create-message",
    )
    store.complete_turn(checkpoint["id"])

    if workflow_delete_committed:
        undo = store.require_action_undo(action["id"])
        creative.delete_project(
            project["id"],
            requested_by=actor.user_id,
            expected_writer_fingerprint=undo["postcondition"][
                "creative_fingerprint"
            ],
        )
        store.mark_action_undo_creative_restored(action["id"])
        workflow.delete_project_data(
            str(project["id"]),
            expected_writer_fingerprint=undo["postcondition"][
                "workflow_fingerprint"
            ],
        )
    else:
        delete_creative = creative.delete_project

        def delete_creative_after_workflow_fence(*args, **kwargs):
            assert str(project["id"]) in workflow.deleting_projects
            return delete_creative(*args, **kwargs)

        creative.delete_project = delete_creative_after_workflow_fence

    restored = service.rollback(
        session["id"],
        "create-message",
        actor=actor,
        mode="rollback",
        client_request_id="rollback-create",
        expected_history_revision=saved["history_revision"],
    )

    assert creative.get_project(project["id"]) is None
    assert restored["project_id"] is None
    assert str(project["id"]) not in workflow.deleting_projects


def test_new_project_rollback_releases_its_marker_when_lease_is_lost_before_creative_delete(
    tmp_path: Path,
) -> None:
    """A lease fence failure before Creative starts must not freeze the project."""

    service, store, creative, workflow, actor, session, _action, saved = (
        completed_new_project_action(tmp_path)
    )
    project_id = int(saved["project_id"])
    real_begin = workflow.begin_project_deletion
    captured_snapshot: dict = {}

    def begin_then_lose_lease(project_id: str, **kwargs) -> dict:
        captured_snapshot.update(kwargs["project_snapshot"])
        marker = real_begin(project_id, **kwargs)
        with store._connect() as connection:
            connection.execute(
                "DELETE FROM writer_agent_session_mutations WHERE session_id=?",
                (session["id"],),
            )
        return marker

    workflow.begin_project_deletion = begin_then_lose_lease

    with pytest.raises(WriterActionError):
        service.rollback(
            session["id"], "create-message", actor=actor, mode="rollback",
            client_request_id="rollback-lease-loss",
            expected_history_revision=saved["history_revision"],
        )

    assert creative.get_project(project_id) is not None
    assert creative.get_pending_project_deletion(project_id) is None
    assert str(project_id) not in workflow.deleting_projects
    assert captured_snapshot == {"id": project_id, "name": "回档前项目"}


def test_new_project_rollback_reports_conflict_after_predelete_marker_is_aborted(
    tmp_path: Path,
) -> None:
    """A rejected Creative delete is not a partial mutation once its fence is gone."""

    service, _store, creative, workflow, actor, session, _action, saved = (
        completed_new_project_action(tmp_path)
    )
    project_id = int(saved["project_id"])

    def reject_delete(*_args, **_kwargs):
        raise ValueError("project deletion state changed")

    creative.delete_project = reject_delete

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"], "create-message", actor=actor, mode="rollback",
            client_request_id="rollback-delete-conflict",
            expected_history_revision=saved["history_revision"],
        )

    assert exc.value.code == "ROLLBACK_CONFLICT"
    assert exc.value.retryable is False
    assert creative.get_project(project_id) is not None
    assert creative.get_pending_project_deletion(project_id) is None
    assert str(project_id) not in workflow.deleting_projects


def test_new_project_rollback_keeps_marker_after_creative_delete_and_resumes(
    tmp_path: Path,
) -> None:
    """A Creative tombstone means the workflow fence must survive for retry."""

    service, _store, creative, workflow, actor, session, _action, saved = (
        completed_new_project_action(tmp_path)
    )
    project_id = int(saved["project_id"])
    real_delete = workflow.delete_project_data
    failed = False

    def fail_workflow_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("workflow unavailable")
        return real_delete(*args, **kwargs)

    workflow.delete_project_data = fail_workflow_once

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"], "create-message", actor=actor, mode="rollback",
            client_request_id="rollback-after-creative-delete",
            expected_history_revision=saved["history_revision"],
        )

    assert exc.value.code == "ROLLBACK_PARTIAL_FAILURE"
    assert creative.get_project(project_id) is None
    assert creative.get_pending_project_deletion(project_id) is not None
    assert str(project_id) in workflow.deleting_projects

    restored = service.rollback(
        session["id"], "create-message", actor=actor, mode="rollback",
        client_request_id="rollback-after-creative-delete",
        expected_history_revision=saved["history_revision"],
    )

    assert restored["project_id"] is None
    assert creative.get_pending_project_deletion(project_id) is None
    assert str(project_id) not in workflow.deleting_projects


def test_create_turn_rollback_uses_fresh_workflow_deletion_fingerprint(
    tmp_path: Path,
) -> None:
    """Would catch deleting with action A's fingerprint after undoing action B."""

    store = WriterAgentStore(tmp_path / "writer-multi-action-delete.db")
    creative = CreativeRepository(tmp_path / "creative-multi-action-delete.db")
    workflow = StableWorkflowRepository()
    workflow.rollback_changes_deletion_fingerprint = True
    service = RollbackService(
        store, creative, workflow, permission_service=ProjectWritePermissions()
    )
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "创建再修改", "跨动作回档")

    checkpoint_a = store.begin_turn(
        session["id"], actor.user_id, "create-message-a",
        original_message={"message": "创建原创项目"},
        content_hash="create-message-a",
    )
    action_a = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="create-action-a", request_hash="create-action-a",
        payload={}, expected_context={"session_revision": 0},
        source_snapshot_id=None, turn_checkpoint_id=checkpoint_a["id"],
    )
    service.prepare_action_undo(action_a, actor)
    store.claim_pending_action(action_a["id"], expected_revision=1)
    store.start_action(action_a["id"])
    project = ready_project(creative)
    real_delete_project = creative.delete_project
    real_deletion_fingerprint = creative.writer_project_deletion_fingerprint
    creative.writer_project_deletion_fingerprint = creative.writer_rollback_fingerprint

    def delete_with_real_creative_fingerprint(project_id, **kwargs):
        kwargs["expected_writer_fingerprint"] = real_deletion_fingerprint(project_id)
        return real_delete_project(project_id, **kwargs)

    creative.delete_project = delete_with_real_creative_fingerprint
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {},
        "fingerprint": "workflow-after-a",
    }
    action_a = store.attach_project_to_action(
        action_a["id"], project["id"], project_context={"project_revision": 0}
    )
    service.capture_new_project_ownership(action_a, actor)
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    store.record_action_result(action_a["id"], {"project_id": project["id"]})
    action_a = store.finish_action(action_a["id"], status="succeeded")
    service.finalize_action_undo(action_a, actor)
    current = store.get(session["id"], actor.user_id)
    saved_a = store.save_turn(
        session["id"], actor.user_id,
        [
            *current["messages"],
            {"role": "user", "content": "创建原创项目", "message_id": "create-message-a"},
            {"role": "assistant", "content": "已创建"},
        ],
        None,
        client_message_id="create-message-a",
        turn_checkpoint_id=checkpoint_a["id"],
    )
    store.complete_turn(checkpoint_a["id"])

    checkpoint_b = store.begin_turn(
        session["id"], actor.user_id, "edit-message-b",
        original_message={"message": "继续修改"},
        content_hash="edit-message-b",
    )
    action_b = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="creative.run_plan", project_id=project["id"],
        idempotency_key="edit-action-b", request_hash="edit-action-b",
        payload={"target_stage": "brief"},
        expected_context={"session_revision": saved_a["context_revision"]},
        source_snapshot_id=None, turn_checkpoint_id=checkpoint_b["id"],
    )
    service.prepare_action_undo(action_b, actor)
    store.claim_pending_action(action_b["id"], expected_revision=1)
    store.start_action(action_b["id"])
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {},
        "fingerprint": "workflow-after-b",
    }
    action_b = store.finish_action(action_b["id"], status="succeeded")
    service.finalize_action_undo(action_b, actor)
    current = store.get(session["id"], actor.user_id)
    saved_b = store.save_turn(
        session["id"], actor.user_id,
        [
            *current["messages"],
            {"role": "user", "content": "继续修改", "message_id": "edit-message-b"},
            {"role": "assistant", "content": "已修改"},
        ],
        None,
        client_message_id="edit-message-b",
        turn_checkpoint_id=checkpoint_b["id"],
    )
    store.complete_turn(checkpoint_b["id"])

    restored = service.rollback(
        session["id"], "create-message-a", actor=actor, mode="rollback",
        client_request_id="rollback-a-and-b",
        expected_history_revision=saved_b["history_revision"],
    )

    rollback = store.require_rollback_by_request(actor.user_id, "rollback-a-and-b")
    rollback_operation_id = f"writer-rollback:{rollback['id']}:{action_b['id']}"
    assert workflow.rollback_operation_ids == [rollback_operation_id]
    assert workflow.delete_expected_fingerprints == [
        f"workflow-after-a:rollback-event:{rollback_operation_id}"
    ]
    assert restored["project_id"] is None


def test_create_retry_after_bind_failure_remains_fully_rollbackable(
    tmp_path: Path,
) -> None:
    """A bind-only retry must preserve the root create action's project deletion."""

    store = WriterAgentStore(tmp_path / "writer-create-retry.db")
    creative = CreativeRepository(tmp_path / "creative-create-retry.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(
        store, creative, workflow, permission_service=ProjectWritePermissions()
    )
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "创建项目", "创建重试回档")
    checkpoint = store.begin_turn(
        session["id"], actor.user_id, "create-retry-message",
        original_message={"message": "创建原创项目"},
        content_hash="create-retry-message",
    )
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="create-retry-action", request_hash="create-retry-action",
        payload={}, expected_context={"session_revision": 0},
        source_snapshot_id=None, turn_checkpoint_id=checkpoint["id"],
    )
    service.prepare_action_undo(action, actor)
    store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    project = ready_project(creative)
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {},
        "fingerprint": "workflow-created-project",
    }
    action = store.attach_project_to_action(
        action["id"], project["id"], project_context={"project_revision": 0}
    )
    service.capture_new_project_ownership(action, actor)
    failed = store.fail_action(
        action["id"], code="SESSION_MUTATION_LEASE_LOST",
        message="会话写入租约已失效", retryable=True,
    )
    service.finalize_action_undo(failed, actor)

    retry = store.create_retry_action(
        failed, actor.user_id, retry_request_id="retry-create-bind"
    )
    service.prepare_action_undo(retry, actor)
    store.start_action(retry["id"])
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    store.record_action_result(retry["id"], {"project_id": project["id"]})
    retry = store.finish_action(retry["id"], status="succeeded")
    service.finalize_action_undo(retry, actor)
    current = store.get(session["id"], actor.user_id)
    saved = store.save_turn(
        session["id"], actor.user_id,
        [
            *current["messages"],
            {"role": "user", "content": "创建原创项目", "message_id": "create-retry-message"},
            {"role": "assistant", "content": "已创建"},
        ],
        None,
        client_message_id="create-retry-message",
        turn_checkpoint_id=checkpoint["id"],
    )
    store.complete_turn(checkpoint["id"])

    restored = service.rollback(
        session["id"], "create-retry-message", actor=actor,
        mode="rollback", client_request_id="rollback-create-retry",
        expected_history_revision=saved["history_revision"],
    )

    assert creative.get_project(project["id"]) is None
    assert restored["project_id"] is None


def test_new_project_rollback_rejects_external_review_created_during_action(
    tmp_path: Path,
) -> None:
    """Would catch finalization blessing an external review for project deletion."""

    store = WriterAgentStore(tmp_path / "writer-external-use.db")
    creative = CreativeRepository(tmp_path / "creative-external-use.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(
        store, creative, workflow, permission_service=ProjectWritePermissions()
    )
    actor = SimpleNamespace(user_id="writer-1")
    session = store.create(actor.user_id, "创建项目", "外部使用冲突")
    checkpoint = store.begin_turn(
        session["id"], actor.user_id, "create-external-use",
        original_message={"message": "创建原创项目"},
        content_hash="create-external-use",
    )
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="create-external-use-action",
        request_hash="create-external-use-action", payload={},
        expected_context={"session_revision": 0}, source_snapshot_id=None,
        turn_checkpoint_id=checkpoint["id"],
    )
    service.prepare_action_undo(action, actor)
    store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    project = ready_project(creative)
    workflow.states[str(project["id"])] = {
        "project_id": str(project["id"]),
        "tables": {},
        "fingerprint": "workflow-created-project",
    }
    action = store.attach_project_to_action(
        action["id"], project["id"], project_context={"project_revision": 0}
    )
    service.capture_new_project_ownership(action, actor)
    creative.create_project_review_once(
        project_id=project["id"], episode_numbers=[],
        source_fingerprint="external-review-source",
        source_payload={"audit_scope": "project"},
        created_by="another-writer", request_key="external-review",
    )
    terminal = store.finish_action(action["id"], status="succeeded")
    service.finalize_action_undo(terminal, actor)
    current = store.get(session["id"], actor.user_id)
    saved = store.save_turn(
        session["id"], actor.user_id,
        [
            *current["messages"],
            {"role": "user", "content": "创建原创项目", "message_id": "create-external-use"},
            {"role": "assistant", "content": "已创建"},
        ],
        None,
        client_message_id="create-external-use",
        turn_checkpoint_id=checkpoint["id"],
    )
    store.complete_turn(checkpoint["id"])

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"], "create-external-use", actor=actor,
            mode="rollback", client_request_id="rollback-external-use",
            expected_history_revision=saved["history_revision"],
        )

    assert exc.value.code == "ROLLBACK_CONFLICT"
    assert creative.get_project(project["id"]) is not None
    assert any(
        int(review["project_id"]) == int(project["id"])
        for review in creative.list_script_reviews()
    )
