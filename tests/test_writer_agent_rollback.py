# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from creative.repository import CreativeRepository
from writer_agent.rollback_service import RollbackService
from writer_agent.schema import WriterActionError
from writer_agent.store import WriterAgentStore


class StableWorkflowRepository:
    def __init__(self) -> None:
        self.states: dict[str, dict] = {}

    def export_writer_rollback_state(self, project_id: str) -> dict:
        return self.states.setdefault(
            str(project_id),
            {"project_id": str(project_id), "tables": {}, "fingerprint": "workflow-0"},
        )

    def writer_rollback_fingerprint(self, project_id: str) -> str:
        return self.export_writer_rollback_state(project_id)["fingerprint"]

    def restore_writer_rollback_state(
        self, project_id: str, preimage: dict, *, expected_fingerprint: str, actor
    ) -> dict:
        if self.writer_rollback_fingerprint(project_id) != expected_fingerprint:
            raise ValueError("workflow rollback state changed")
        self.states[str(project_id)] = dict(preimage)
        return dict(preimage)

    def delete_project_data(self, project_id: str) -> dict[str, int]:
        self.states.pop(str(project_id), None)
        return {"workflow_count": 0, "task_count": 0}


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


def completed_project_edit(
    tmp_path: Path,
) -> tuple[RollbackService, WriterAgentStore, CreativeRepository, dict, dict, object]:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    creative = CreativeRepository(tmp_path / "creative.db")
    workflow = StableWorkflowRepository()
    service = RollbackService(store, creative, workflow)
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


def test_rollback_service_restores_project_and_conversation(tmp_path: Path) -> None:
    """Would catch successful rollback restoring only the visible conversation."""

    service, store, creative, session, checkpoint, actor = completed_project_edit(
        tmp_path
    )

    preview = service.preview(
        session["id"], "message-1", actor_user_id="writer-1"
    )
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


def test_rollback_service_rejects_external_project_changes(tmp_path: Path) -> None:
    """Would catch rollback overwriting a newer edit from another project entry."""

    service, _store, creative, session, _checkpoint, actor = completed_project_edit(
        tmp_path
    )
    creative.update_project(
        session["project_id"], {"name": "其他页面的新修改"}, expected_revision=1
    )

    with pytest.raises(WriterActionError) as exc:
        service.rollback(
            session["id"],
            "message-1",
            actor=actor,
            mode="rollback",
            client_request_id="rollback-conflict",
            expected_history_revision=session["history_revision"],
        )

    assert exc.value.code == "ROLLBACK_CONFLICT"
    assert creative.get_project(session["project_id"])["name"] == "其他页面的新修改"
