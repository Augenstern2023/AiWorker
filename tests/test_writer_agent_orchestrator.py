# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from creative.application_service import CreativeContextConflict, CreativeTaskOccupied
from identity.schema import Principal
from writer_agent.action_service import parse_proposed_actions, persist_proposed_actions
from writer_agent.orchestrator import WriterProjectOrchestrator
from writer_agent.schema import ProposedAction, WriterActionError
from writer_agent.store import WriterAgentStore


@pytest.fixture
def orchestrator_context(tmp_path: Path) -> SimpleNamespace:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-1", username="writer", display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create", "script.review.generate"}),
        data_scope="project", session_id="writer-session",
    )
    session = store.create(actor.user_id, "原创会话", "一句话灵感")
    creative_service = MagicMock()
    creative_service.create_original_project.return_value = {
        "id": 17, "name": "情绪公司", "creation_mode": "original",
    }
    creative_service.get_project.side_effect = lambda project_id: {
        "id": project_id, "name": f"项目 {project_id}", "revision": 0,
    }
    creative_service.repository.workflow_project_id.side_effect = (
        lambda project_id: str(project_id)
    )
    permission_service = MagicMock()
    permission_service.can_read_project.side_effect = (
        lambda _actor, project_id: project_id != "404"
    )
    orchestrator = WriterProjectOrchestrator(
        store, creative_service, permission_service, MagicMock(),
        can_read_reference=lambda _actor, _resource_type, resource_id: resource_id != 404,
    )

    def create_action(action_type: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return store.create_action(
            session_id=session["id"], actor_user_id=actor.user_id,
            action_type=action_type, project_id=payload.get("project_id"),
            idempotency_key=f"{action_type}:{hashlib.sha256(normalized.encode()).hexdigest()}",
            request_hash=hashlib.sha256(normalized.encode()).hexdigest(),
            payload=payload,
            expected_context={"session_revision": session["context_revision"]},
            source_snapshot_id=None,
        )

    return SimpleNamespace(
        store=store, actor=actor, session=session, orchestrator=orchestrator,
        creative_service=creative_service, permission_service=permission_service,
        create_action=create_action,
    )


def test_create_action_binds_idempotent_original_project(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
            "assumptions": [],
        },
    )

    first = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )
    second = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )

    assert second["project_id"] == first["project_id"]
    assert set(first) == {"project_id", "name", "creation_mode", "open_url"}
    session = orchestrator_context.store.get(action["session_id"], "writer-1")
    assert session["project_id"] == first["project_id"]


def test_create_action_persists_project_id_before_session_bind(
    orchestrator_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would catch a created project becoming unreachable when session binding fails."""

    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": [], "reference_refs": [], "assumptions": [],
        },
    )

    def fail_bind(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise WriterActionError(
            "SESSION_MUTATION_LEASE_LOST", "会话写入租约已失效", retryable=True
        )

    monkeypatch.setattr(orchestrator_context.store, "bind_project", fail_bind)
    observer = MagicMock()
    orchestrator_context.orchestrator.new_project_observer = observer

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )

    assert exc.value.code == "SESSION_MUTATION_LEASE_LOST"
    persisted = orchestrator_context.store.require_action(action["id"], "writer-1")
    assert persisted["project_id"] == 17
    assert persisted["result"] is None
    observer.assert_called_once()
    assert observer.call_args.args[0]["project_id"] == 17


def test_create_action_retry_reuses_project_after_session_bind_failure(
    orchestrator_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying the bind must not create an orphan second project."""

    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": [], "reference_refs": [], "assumptions": [],
        },
    )
    orchestrator_context.store.claim_pending_action(
        action["id"], expected_revision=1
    )
    orchestrator_context.store.start_action(action["id"])
    original_bind = orchestrator_context.store.bind_project
    bind_attempts = 0

    def fail_first_bind(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal bind_attempts
        bind_attempts += 1
        if bind_attempts == 1:
            raise WriterActionError(
                "SESSION_MUTATION_LEASE_LOST",
                "会话写入租约已失效",
                retryable=True,
            )
        return original_bind(*args, **kwargs)

    monkeypatch.setattr(orchestrator_context.store, "bind_project", fail_first_bind)
    orchestrator_context.creative_service.create_original_project.side_effect = [
        {"id": 17, "name": "情绪公司", "creation_mode": "original"},
        {"id": 18, "name": "重复项目", "creation_mode": "original"},
    ]

    with pytest.raises(WriterActionError):
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    failed = orchestrator_context.store.fail_action(
        action["id"],
        code="SESSION_MUTATION_LEASE_LOST",
        message="会话写入租约已失效",
        retryable=True,
    )
    retry = orchestrator_context.store.create_retry_action(
        failed,
        orchestrator_context.actor.user_id,
        retry_request_id="retry-bind-only",
    )
    orchestrator_context.store.start_action(retry["id"])

    result = orchestrator_context.orchestrator.execute_project_action(
        retry["id"], orchestrator_context.actor
    )

    assert result["project_id"] == 17
    assert orchestrator_context.store.get(
        action["session_id"], orchestrator_context.actor.user_id
    )["project_id"] == 17
    assert orchestrator_context.creative_service.create_original_project.call_count == 1


def test_link_action_rechecks_project_read_access(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.link", payload={"project_id": 404}
    )
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    assert exc.value.code == "PROJECT_ACCESS_DENIED"


def test_standalone_lock_materializes_and_executes_a_real_lock_step(
    orchestrator_context,
) -> None:
    snapshot = orchestrator_context.store.save_source_snapshot({
        "snapshot_id": "standalone-lock-snapshot",
        "materials": [],
    })
    action = orchestrator_context.store.create_action(
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        action_type="creative.lock",
        project_id=17,
        idempotency_key="standalone-lock",
        request_hash="standalone-lock",
        payload={
            "target_stage": "brief",
            "resource_id": "101",
            "assumptions": [],
        },
        expected_context={"project_revision": 0},
        source_snapshot_id=snapshot["id"],
    )
    orchestrator_context.creative_service.ensure_stage_task.return_value = {
        "id": "brief-task",
        "status": "in_progress",
    }
    orchestrator_context.creative_service.lock_resource.return_value = {
        "resource_type": "brief",
        "resource_id": "101",
        "status": "locked",
    }

    steps = orchestrator_context.orchestrator.prepare_action_plan(
        action["id"], orchestrator_context.actor
    )
    result = orchestrator_context.orchestrator.execute_action_step(
        action,
        steps[0],
        orchestrator_context.actor,
        operation_id=steps[0]["operation_id"],
    )

    assert [(step["step_key"], step["status"]) for step in steps] == [
        ("brief.lock", "pending")
    ]
    assert result["resource_refs"][0]["resource_id"] == "101"
    orchestrator_context.creative_service.lock_resource.assert_called_once()
    assert orchestrator_context.creative_service.ensure_stage_task.call_args.kwargs[
        "operation_id"
    ] == steps[0]["operation_id"]


def test_lock_step_tags_direct_task_start_with_writer_operation_id(
    orchestrator_context,
) -> None:
    """The direct assigned-to-start transition must be attributable to Writer."""

    snapshot = orchestrator_context.store.save_source_snapshot(
        {"snapshot_id": "snapshot-start-tag", "materials": []}
    )
    action = orchestrator_context.store.create_action(
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        action_type="creative.lock",
        project_id=17,
        idempotency_key="start-tag-lock",
        request_hash="start-tag-lock",
        payload={
            "target_stage": "brief",
            "resource_id": "brief-1",
            "assumptions": [],
        },
        expected_context={"project_revision": 0},
        source_snapshot_id=snapshot["id"],
    )
    orchestrator_context.creative_service.ensure_stage_task.return_value = {
        "id": "brief-task",
        "status": "assigned",
    }
    orchestrator_context.creative_service.lock_resource.return_value = {
        "resource_type": "brief",
        "resource_id": "brief-1",
        "status": "locked",
    }
    steps = orchestrator_context.orchestrator.prepare_action_plan(
        action["id"], orchestrator_context.actor
    )

    orchestrator_context.orchestrator.execute_action_step(
        action,
        steps[0],
        orchestrator_context.actor,
        operation_id=steps[0]["operation_id"],
    )

    orchestrator_context.creative_service.workflow_repository.start_task.assert_called_once_with(
        "brief-task",
        orchestrator_context.actor,
        writer_operation_id=steps[0]["operation_id"],
    )


@pytest.mark.parametrize("target_stage", ["beats", "script"])
@pytest.mark.parametrize(
    ("episode_start", "episode_end"),
    [(None, None), (2, None), (2, 3)],
)
def test_model_episode_lock_requires_exactly_one_episode(
    target_stage: str,
    episode_start: int | None,
    episode_end: int | None,
) -> None:
    """Accepting a missing or multi-episode lock scope would create an ambiguous step."""

    actions = parse_proposed_actions({
        "proposed_actions": [
            {
                "action_type": "creative.lock",
                "title": f"锁定第 2 集 {target_stage}",
                "project_id": 17,
                "target_stage": target_stage,
                "episode_start": episode_start,
                "episode_end": episode_end,
                "payload": {"resource_id": f"{target_stage}-2"},
            }
        ]
    })

    assert actions == []


@pytest.mark.parametrize("target_stage", ["beats", "script"])
def test_model_episode_lock_persists_and_executes_from_top_level_scope(
    orchestrator_context,
    target_stage: str,
) -> None:
    """Dropping the persisted top-level range would strand a valid standalone lock."""

    proposed = parse_proposed_actions({
        "proposed_actions": [
            {
                "action_type": "creative.lock",
                "title": f"锁定第 2 集 {target_stage}",
                "project_id": 17,
                "target_stage": target_stage,
                "episode_start": 2,
                "episode_end": 2,
                "payload": {"resource_id": f"{target_stage}-2"},
            }
        ]
    })
    assert len(proposed) == 1
    action = persist_proposed_actions(
        orchestrator_context.store,
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        proposed_actions=proposed,
        materials=[],
        selected_messages=[],
        project_context={"project_id": 17, "project_revision": 0},
    )[0]
    assert action["payload"]["episode_start"] == 2
    assert action["payload"]["episode_end"] == 2
    assert "episode_number" not in action["payload"]
    orchestrator_context.creative_service.ensure_stage_task.return_value = {
        "id": f"{target_stage}-task-2",
        "status": "in_progress",
    }
    orchestrator_context.creative_service.lock_resource.return_value = {
        "resource_type": target_stage,
        "resource_id": f"{target_stage}-2",
        "status": "locked",
    }

    steps = orchestrator_context.orchestrator.prepare_action_plan(
        action["id"], orchestrator_context.actor
    )
    result = orchestrator_context.orchestrator.execute_action_step(
        action,
        steps[0],
        orchestrator_context.actor,
        operation_id=steps[0]["operation_id"],
    )

    assert [
        (step["step_key"], step["episode_start"], step["episode_end"])
        for step in steps
    ] == [(f"{target_stage}.lock:2", 2, 2)]
    assert result["resource_refs"][0]["resource_id"] == f"{target_stage}-2"
    assert (
        orchestrator_context.creative_service.lock_resource.call_args.kwargs[
            "episode_number"
        ]
        == 2
    )


def test_link_action_persisted_from_proposal_uses_top_level_project_id(
    orchestrator_context,
) -> None:
    action = persist_proposed_actions(
        orchestrator_context.store,
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        proposed_actions=[ProposedAction.model_validate({
            "action_type": "project.link",
            "title": "关联已有项目",
            "project_id": 18,
        })],
        materials=[],
        selected_messages=[],
        project_context={
            "session_revision": orchestrator_context.session["context_revision"]
        },
    )[0]

    assert action["project_id"] == 18
    assert "project_id" not in action["payload"]

    result = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )

    assert result["project_id"] == 18
    assert orchestrator_context.store.get(action["session_id"], "writer-1")["project_id"] == 18


@pytest.mark.parametrize(
    "project_id",
    [None, "invalid-project-id", "1.5", 0, -1, 1.5, True, 1.0, "1", "1.0"],
)
def test_link_action_keeps_pending_when_top_level_project_id_is_invalid(
    orchestrator_context, project_id: float | int | str | None,
) -> None:
    action = orchestrator_context.store.create_action(
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        action_type="project.link",
        project_id=project_id,
        idempotency_key=f"invalid-link-project:{project_id}",
        request_hash=f"invalid-link-project:{project_id}",
        payload={},
        expected_context={
            "session_revision": orchestrator_context.session["context_revision"]
        },
        source_snapshot_id=None,
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )

    assert exc.value.code == "RESULT_VALIDATION_FAILED"
    assert orchestrator_context.store.require_action(action["id"], "writer-1")["status"] == "pending"


def test_legacy_pending_link_without_project_id_json_fails_closed(
    orchestrator_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = orchestrator_context.store.create_action(
        session_id=orchestrator_context.session["id"],
        actor_user_id=orchestrator_context.actor.user_id,
        action_type="project.link",
        project_id=1,
        idempotency_key="legacy-link-project:1",
        request_hash="legacy-link-project:1",
        payload={},
        expected_context={
            "session_revision": orchestrator_context.session["context_revision"]
        },
        source_snapshot_id=None,
    )
    with sqlite3.connect(orchestrator_context.store.db_path) as connection:
        connection.execute(
            "UPDATE writer_agent_actions SET project_id_json=NULL WHERE id=?",
            (action["id"],),
        )
        legacy_row = connection.execute(
            "SELECT project_id, project_id_json FROM writer_agent_actions WHERE id=?",
            (action["id"],),
        ).fetchone()
    assert legacy_row == (1, None)
    bind_project = MagicMock(wraps=orchestrator_context.store.bind_project)
    monkeypatch.setattr(orchestrator_context.store, "bind_project", bind_project)

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )

    assert exc.value.code == "RESULT_VALIDATION_FAILED"
    persisted = orchestrator_context.store.require_action(action["id"], "writer-1")
    assert persisted["status"] == "pending"
    assert orchestrator_context.store.get(action["session_id"], "writer-1")["project_id"] is None
    orchestrator_context.creative_service.get_project.assert_not_called()
    bind_project.assert_not_called()


def test_create_action_rejects_reference_revoked_after_card_creation(
    orchestrator_context,
) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "情绪工资", "name": "情绪公司", "genre": "都市奇幻",
            "target_platform": "hg", "target_audience": "18-35岁",
            "target_episode_count": 20, "episode_duration_seconds": 120,
            "cost_level": "low", "content_boundaries": [],
            "reference_refs": [{"resource_type": "topic", "resource_id": 404}],
            "assumptions": [],
        },
    )
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    assert exc.value.code == "REFERENCE_UNAVAILABLE"


def test_create_action_keeps_pending_when_required_project_field_is_missing(
    orchestrator_context,
) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
            "assumptions": [],
        },
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )

    assert exc.value.code == "RESULT_VALIDATION_FAILED"
    assert orchestrator_context.store.require_action(action["id"], "writer-1")["status"] == "pending"
    orchestrator_context.creative_service.create_original_project.assert_not_called()


def test_create_action_keeps_pending_when_assumptions_are_missing(
    orchestrator_context,
) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
        },
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )

    assert exc.value.code == "RESULT_VALIDATION_FAILED"
    assert orchestrator_context.store.require_action(action["id"], "writer-1")["status"] == "pending"
    orchestrator_context.creative_service.create_original_project.assert_not_called()


def test_create_action_requires_project_create_permission(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
            "assumptions": [],
        },
    )
    unprivileged_actor = replace(orchestrator_context.actor, permissions=frozenset())

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], unprivileged_actor
        )

    assert exc.value.code == "PROJECT_CREATE_DENIED"
    orchestrator_context.creative_service.create_original_project.assert_not_called()


def test_system_admin_can_create_own_original_project(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": [], "reference_refs": [], "assumptions": [],
        },
    )
    admin = replace(
        orchestrator_context.actor,
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
    )

    result = orchestrator_context.orchestrator.execute_project_action(
        action["id"], admin
    )

    assert result["project_id"] == 17
    orchestrator_context.creative_service.create_original_project.assert_called_once()


def test_replay_after_binding_records_result_without_rebinding(
    orchestrator_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = orchestrator_context.create_action(
        "project.create_original",
        payload={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg",
            "target_audience": "18-35岁", "target_episode_count": 20,
            "episode_duration_seconds": 120, "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
            "assumptions": [],
        },
    )
    bind_calls: list[tuple[int, int, int]] = []
    original_bind = orchestrator_context.store.bind_project
    original_record = orchestrator_context.store.record_action_result

    def record_result_once(action_id: str, result: dict[str, Any]) -> dict[str, Any]:
        monkeypatch.setattr(
            orchestrator_context.store, "record_action_result", original_record
        )
        raise RuntimeError("simulated exit after bind")

    def record_bind(session_id: int, project_id: int, expected_context_revision: int):
        bind_calls.append((session_id, project_id, expected_context_revision))
        return original_bind(session_id, project_id, expected_context_revision)

    monkeypatch.setattr(orchestrator_context.store, "bind_project", record_bind)
    monkeypatch.setattr(
        orchestrator_context.store, "record_action_result", record_result_once
    )

    with pytest.raises(RuntimeError, match="simulated exit after bind"):
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    replayed = orchestrator_context.orchestrator.execute_project_action(
        action["id"], orchestrator_context.actor
    )

    assert bind_calls == [(action["session_id"], 17, 0)]
    assert replayed["project_id"] == 17
    assert orchestrator_context.store.require_action(action["id"], "writer-1")["result"] == replayed


def empty_original_project() -> dict[str, Any]:
    return {
        "id": 17, "revision": 0, "target_episode_count": 2,
        "locked_brief_id": None, "character_batch_id": None,
        "outline_batch_id": None, "blueprints": [], "characters": [],
        "relationships": [], "episodes": [],
    }


def project_with_locked_outline() -> dict[str, Any]:
    project = empty_original_project()
    project.update({
        "revision": 3, "locked_brief_id": 101,
        "character_batch_id": "characters-v1",
        "outline_batch_id": "outlines-v1",
        "blueprints": [{"id": 101, "version": 1, "status": "brief_locked"}],
        "characters": [{"id": 201, "batch_id": "characters-v1", "version": 1,
                        "status": "locked"}],
        "episodes": [
            {"id": 301, "stage": "outline", "episode_number": 1,
             "batch_id": "outlines-v1", "version": 1, "status": "locked"},
            {"id": 302, "stage": "outline", "episode_number": 2,
             "batch_id": "outlines-v1", "version": 1, "status": "locked"},
        ],
    })
    return project


def project_with_locked_episode_chain() -> dict[str, Any]:
    """Build one fully compatible locked episode authority chain."""
    project = project_with_locked_outline()
    project["target_episode_count"] = 1
    project["episodes"] = project["episodes"][:1]
    outline = project["episodes"][0]
    outline["content"] = {"episode": 1, "summary": "开端"}
    outline_fingerprint = hashlib.sha256(
        json.dumps(
            outline["content"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    beats_content = {"beats": [{"beat": "开场"}]}
    beats_fingerprint = hashlib.sha256(
        json.dumps(
            beats_content, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    brief_fingerprint = hashlib.sha256(b"{}").hexdigest()
    character_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "characters": [
                    {"id": 201, "version": 1, "content": {}}
                ],
                "relationships": [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    project["episodes"].extend([
        {
            "id": 401, "stage": "beats", "episode_number": 1,
            "version": 1, "status": "locked", "content": beats_content,
            "upstream_refs": {
                "brief_id": 101,
                "brief_version": 1,
                "brief_content_fingerprint": brief_fingerprint,
                "character_batch_id": "characters-v1",
                "character_content_fingerprint": character_fingerprint,
                "outline_batch_id": "outlines-v1",
                "outline_id": 301,
                "outline_version": 1,
                "outline_content_fingerprint": outline_fingerprint,
            },
        },
        {
            "id": 501, "stage": "script", "episode_number": 1,
            "version": 1, "status": "locked", "content": {"scenes": []},
            "upstream_refs": {
                "brief_id": 101,
                "brief_version": 1,
                "brief_content_fingerprint": brief_fingerprint,
                "character_batch_id": "characters-v1",
                "character_content_fingerprint": character_fingerprint,
                "outline_batch_id": "outlines-v1",
                "outline_id": 301,
                "outline_version": 1,
                "outline_content_fingerprint": outline_fingerprint,
                "beats_id": 401,
                "beats_version": 1,
                "beats_content_fingerprint": beats_fingerprint,
            },
        },
    ])
    return project


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("brief", ["brief.persist"]),
        ("characters", ["brief.persist", "brief.lock", "characters.generate"]),
        ("outline", [
            "brief.persist", "brief.lock", "characters.generate",
            "characters.lock", "outline.generate",
        ]),
        ("script", [
            "brief.persist", "brief.lock", "characters.generate",
            "characters.lock", "outline.generate", "outline.lock",
            "beats.generate:1", "beats.lock:1", "script.generate:1",
        ]),
    ],
)
def test_plan_fills_only_missing_dependencies(target, expected, orchestrator_context) -> None:
    """Would catch a planner that skips an upstream locked baseline step."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(), target_stage=target,
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-1",
        confirmed_brief_payload={"title": "情绪公司", "logline": "情绪决定工资"},
    )
    assert [step.step_key for step in plan.steps] == expected


def test_plan_reuses_locked_authority(orchestrator_context) -> None:
    """Would catch a planner that regenerates compatible locked authority."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=project_with_locked_outline(), target_stage="script",
        episode_start=1, episode_end=2, source_snapshot_id="snapshot-2",
    )
    assert [step.step_key for step in plan.steps] == [
        "beats.generate:1", "beats.lock:1", "script.generate:1",
        "beats.generate:2", "beats.lock:2", "script.generate:2",
    ]
    assert {item["resource_type"] for item in plan.reused_resources} == {
        "brief", "characters", "outline",
    }


def test_plan_reuses_all_five_compatible_locked_resources_as_empty_chain(
    orchestrator_context,
) -> None:
    """Would catch a planner that regenerates an already compatible locked Script."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=project_with_locked_episode_chain(), target_stage="script",
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-all-locked",
    )

    assert plan.steps == []
    assert [item["resource_type"] for item in plan.reused_resources] == [
        "brief", "characters", "outline", "beats", "script",
    ]


def test_plan_rejects_stale_locked_beats_and_rebuilds_its_script_chain(
    orchestrator_context,
) -> None:
    """Would catch reuse based only on the locked status of a historical Beats."""
    project = project_with_locked_episode_chain()
    beats = next(item for item in project["episodes"] if item["stage"] == "beats")
    beats["upstream_refs"]["outline_batch_id"] = "outlines-old"

    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage="script", episode_start=1, episode_end=1,
        source_snapshot_id="snapshot-stale-beats",
    )

    assert [step.step_key for step in plan.steps] == [
        "beats.generate:1", "beats.lock:1", "script.generate:1",
    ]
    assert {item["resource_type"] for item in plan.reused_resources} == {
        "brief", "characters", "outline",
    }


def test_script_generation_binds_reused_beats_authority(orchestrator_context) -> None:
    """Would catch a Script step detached from the locked Beats it consumes."""
    project = project_with_locked_episode_chain()
    project["episodes"] = [
        item for item in project["episodes"] if item["stage"] != "script"
    ]

    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage="script", episode_start=1, episode_end=1,
        source_snapshot_id="snapshot-bind-beats",
    )

    assert [step.step_key for step in plan.steps] == ["script.generate:1"]
    assert plan.steps[0].expected_context["beats"] == {
        "resource_id": "401",
        "version": 1,
        "fingerprint": hashlib.sha256(
            json.dumps(
                {"beats": [{"beat": "开场"}]}, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def test_plan_selects_highest_compatible_locked_beats_and_script(
    orchestrator_context,
) -> None:
    """Would catch first-record or highest-but-stale locked resource selection."""
    project = project_with_locked_episode_chain()
    beats_v1 = next(item for item in project["episodes"] if item["stage"] == "beats")
    script_v1 = next(item for item in project["episodes"] if item["stage"] == "script")
    beats_v3 = {
        **beats_v1, "id": 403, "version": 3,
        "content": {"beats": [{"beat": "兼容新版"}]},
    }
    beats_v4_stale = {
        **beats_v1, "id": 404, "version": 4,
        "upstream_refs": {**beats_v1["upstream_refs"], "outline_id": 999},
    }
    beats_v3_fingerprint = hashlib.sha256(
        json.dumps(
            beats_v3["content"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    script_v2 = {
        **script_v1, "id": 502, "version": 2,
        "upstream_refs": {
            **script_v1["upstream_refs"],
            "beats_id": 403,
            "beats_version": 3,
            "beats_content_fingerprint": beats_v3_fingerprint,
        },
    }
    script_v3_stale = {
        **script_v1, "id": 503, "version": 3,
        "upstream_refs": {**script_v1["upstream_refs"], "beats_id": 999},
    }
    project["episodes"].extend([
        beats_v3, beats_v4_stale, script_v2, script_v3_stale,
    ])

    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage="script", episode_start=1, episode_end=1,
        source_snapshot_id="snapshot-highest-compatible",
    )

    assert plan.steps == []
    assert [
        (item["resource_type"], item["resource_id"])
        for item in plan.reused_resources[-2:]
    ] == [("beats", "403"), ("script", "502")]


def test_plan_rejects_locked_beats_with_stale_extended_upstream_fingerprints(
    orchestrator_context,
) -> None:
    """Would catch reuse that checks pointers but ignores supplied authority hashes."""
    project = project_with_locked_episode_chain()
    beats_v1 = next(item for item in project["episodes"] if item["stage"] == "beats")
    project["episodes"].append({
        **beats_v1,
        "id": 402,
        "version": 2,
        "upstream_refs": {
            **beats_v1["upstream_refs"],
            "brief_version": 999,
            "brief_content_fingerprint": "stale-brief",
            "character_content_fingerprint": "stale-characters",
        },
    })

    plan = orchestrator_context.orchestrator.build_plan(
        project=project,
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-stale-extended-refs",
    )

    assert plan.steps == []
    assert [
        (item["resource_type"], item["resource_id"])
        for item in plan.reused_resources[-2:]
    ] == [("beats", "401"), ("script", "501")]


@pytest.mark.parametrize(
    "missing_ref",
    [
        "brief_version",
        "brief_content_fingerprint",
        "character_content_fingerprint",
    ],
)
def test_plan_strictly_rejects_legacy_episode_authority_without_fingerprints(
    missing_ref, orchestrator_context,
) -> None:
    """Historical rows with incomplete authority are never implicitly trusted."""
    project = project_with_locked_episode_chain()
    for item in project["episodes"]:
        if item["stage"] in {"beats", "script"}:
            item["upstream_refs"].pop(missing_ref, None)

    plan = orchestrator_context.orchestrator.build_plan(
        project=project,
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id=f"snapshot-legacy-missing-{missing_ref}",
    )

    assert [step.step_key for step in plan.steps] == [
        "beats.generate:1",
        "beats.lock:1",
        "script.generate:1",
    ]


@pytest.mark.parametrize("rework_stage", ["beats", "script"])
def test_reworking_locked_episode_resource_creates_real_per_episode_revision_step(
    rework_stage, orchestrator_context,
) -> None:
    """Would catch a rework lookup that drops the required episode number."""
    project = project_with_locked_episode_chain()
    expected_resource_id = "401" if rework_stage == "beats" else "501"

    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage=rework_stage, episode_start=1, episode_end=1,
        source_snapshot_id=f"snapshot-rework-{rework_stage}",
        rework_stage=rework_stage,
    )

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.step_key == f"{rework_stage}.persist:1"
    assert step.episode_number == 1
    assert step.replaces_resource_id == expected_resource_id
    assert step.invalidates_downstream is True


@pytest.mark.parametrize(
    ("episode_start", "episode_end"),
    [(0, 1), (2, 1), (1, 3)],
)
def test_plan_rejects_episode_range_outside_project_bounds(
    episode_start, episode_end, orchestrator_context,
) -> None:
    """Would catch a planner that emits impossible per-episode work."""
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.build_plan(
            project=empty_original_project(), target_stage="script",
            episode_start=episode_start, episode_end=episode_end,
            source_snapshot_id="snapshot-range",
            confirmed_brief_payload={"title": "情绪公司"},
        )

    assert exc.value.code == "RESULT_VALIDATION_FAILED"


def test_plan_pauses_for_selection_after_multi_direction_generation(
    orchestrator_context,
) -> None:
    """Would catch a planner that locks an unselected generated direction."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(), target_stage="outline",
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-3",
        confirmed_brief_payload=None,
    )
    assert [step.step_key for step in plan.steps] == ["brief.generate"]
    assert plan.requires_selection is True
    assert plan.selection_stage == "brief"


def test_execute_step_rejects_task_assigned_to_another_user(orchestrator_context) -> None:
    """Would catch an executor that leaks the workflow assignment exception."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(), target_stage="brief",
        episode_start=1, episode_end=1, source_snapshot_id="snapshot-4",
        confirmed_brief_payload={"title": "情绪公司"},
    )
    orchestrator_context.creative_service.ensure_stage_task.side_effect = CreativeTaskOccupied(
        "该创作阶段已由其他人员处理"
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_step(
            plan=plan, step=plan.steps[0], actor=orchestrator_context.actor,
            source_snapshot={}, payload={"title": "情绪公司"},
        )

    assert exc.value.code == "TASK_ASSIGNED_TO_OTHER"


def test_execute_step_sanitizes_native_permission_error(orchestrator_context) -> None:
    """Only the typed task-occupation exception may expose assignment semantics."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(),
        target_stage="brief",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-native-permission",
        confirmed_brief_payload={"title": "情绪公司"},
    )
    orchestrator_context.creative_service.ensure_stage_task.side_effect = PermissionError(
        r"D:\private\workflow.db: authorization backend failed"
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=orchestrator_context.actor,
            source_snapshot={},
            payload={"title": "情绪公司"},
        )

    assert exc.value.code == "CREATIVE_OPERATION_FAILED"
    assert "private" not in str(exc.value)


def test_reworking_locked_outline_calls_create_revision_and_marks_downstream_historical(
    orchestrator_context,
) -> None:
    """Would catch an executor that writes a locked outline as an ordinary draft."""
    project = project_with_locked_outline()
    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage="outline", episode_start=1, episode_end=1,
        source_snapshot_id="snapshot-5", rework_stage="outline",
    )
    step = plan.steps[0]
    orchestrator_context.creative_service.ensure_stage_task.return_value = {"id": "task-1"}
    orchestrator_context.creative_service.create_revision.return_value = {
        "resource_type": "outline", "resource_id": "17:outlines-v2",
    }

    result = orchestrator_context.orchestrator.execute_step(
        plan=plan, step=step, actor=orchestrator_context.actor,
        source_snapshot={}, payload={"episodes": []},
    )

    assert step.replaces_resource_id == "17:outlines-v1"
    assert step.invalidates_downstream is True
    assert result["resource_refs"] == [
        {"resource_type": "outline", "resource_id": "17:outlines-v2"}
    ]
    orchestrator_context.creative_service.create_revision.assert_called_once()
    orchestrator_context.creative_service.persist_draft.assert_not_called()


def test_locked_resource_is_never_patched_in_place(orchestrator_context) -> None:
    """Would catch an execution path that uses the repository PATCH-style draft API."""
    project = project_with_locked_outline()
    plan = orchestrator_context.orchestrator.build_plan(
        project=project, target_stage="characters", episode_start=1, episode_end=1,
        source_snapshot_id="snapshot-6", rework_stage="characters",
    )
    step = plan.steps[0]
    orchestrator_context.creative_service.ensure_stage_task.return_value = {"id": "task-2"}
    orchestrator_context.creative_service.create_revision.return_value = {
        "resource_type": "characters", "resource_id": "17:characters-v2",
    }

    orchestrator_context.orchestrator.execute_step(
        plan=plan, step=step, actor=orchestrator_context.actor,
        source_snapshot={}, payload={"characters": [{"name": "林夏"}]},
    )

    assert step.replaces_resource_id == "17:characters-v1"
    orchestrator_context.creative_service.persist_draft.assert_not_called()


def test_failed_authority_advance_does_not_partially_mutate_downstream_steps(
    orchestrator_context,
) -> None:
    """Would catch partial trust propagation when a later context check conflicts."""
    plan = orchestrator_context.orchestrator.build_plan(
        project=empty_original_project(),
        target_stage="script",
        episode_start=1,
        episode_end=2,
        source_snapshot_id="snapshot-atomic-advance",
        confirmed_brief_payload={"title": "情绪公司"},
    )
    lock_step = next(step for step in plan.steps if step.step_key == "outline.lock")
    first_dependent = next(
        step for step in plan.steps if step.step_key == "beats.generate:1"
    )
    original_context = dict(first_dependent.expected_context)
    orchestrator_context.creative_service.ensure_stage_task.return_value = {
        "id": "outline-task"
    }
    orchestrator_context.creative_service.lock_resource.return_value = {
        "resource_type": "outline",
        "resource_id": "17:outlines-v1",
    }
    orchestrator_context.creative_service.advance_plan_context.side_effect = [
        {**original_context, "revision": 1},
        CreativeContextConflict("concurrent authority change"),
    ]

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_step(
            plan=plan,
            step=lock_step,
            actor=orchestrator_context.actor,
            source_snapshot={"snapshot_id": "snapshot-atomic-advance"},
            payload={"resource_id": "17:outlines-v1"},
        )

    assert exc.value.code == "STALE_PROJECT_CONTEXT"
    assert first_dependent.expected_context == original_context
