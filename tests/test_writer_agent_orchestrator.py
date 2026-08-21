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

from identity.schema import Principal
from writer_agent.action_service import persist_proposed_actions
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
    session = orchestrator_context.store.get(action["session_id"], "writer-1")
    assert session["project_id"] == first["project_id"]


def test_link_action_rechecks_project_read_access(orchestrator_context) -> None:
    action = orchestrator_context.create_action(
        "project.link", payload={"project_id": 404}
    )
    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_project_action(
            action["id"], orchestrator_context.actor
        )
    assert exc.value.code == "PROJECT_ACCESS_DENIED"


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
    orchestrator_context.creative_service.ensure_stage_task.side_effect = PermissionError(
        "该创作阶段已由其他人员处理"
    )

    with pytest.raises(WriterActionError) as exc:
        orchestrator_context.orchestrator.execute_step(
            plan=plan, step=plan.steps[0], actor=orchestrator_context.actor,
            source_snapshot={}, payload={"title": "情绪公司"},
        )

    assert exc.value.code == "TASK_ASSIGNED_TO_OTHER"


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
