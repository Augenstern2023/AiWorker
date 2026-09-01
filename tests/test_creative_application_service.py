# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.job import CrawlJob
from creative.application_service import (
    MAX_INLINE_SOURCE_SNAPSHOT_BYTES,
    CreativeApplicationService,
)
from creative.repository import CreativeRepository, canonical_request_hash
from identity.schema import Principal


@pytest.fixture
def creative_context(tmp_path: Path) -> SimpleNamespace:
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.ensure_project_task.return_value = {
        "id": "brief-task",
        "task_type": "brief",
        "status": "in_progress",
        "project_id": "1",
        "assignee_user_id": "writer-1",
    }
    transitions = MagicMock()
    jobs = MagicMock()
    workflow_adapter = MagicMock(unsafe=True)
    permission_service = MagicMock()
    permission_service.can_read_project.return_value = True
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    service = CreativeApplicationService(
        repository,
        workflow,
        transitions,
        jobs,
        tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permission_service,
    )

    def create_original_project() -> dict:
        project, _ = repository.ensure_original_project(
            seed_text="未来公司按照员工情绪发工资",
            name="情绪公司",
            target_episode_count=1,
            created_by=actor.user_id,
            creation_request_id="creative-service-test",
        )
        workflow.ensure_project_task.return_value["project_id"] = str(project["id"])
        return project

    return SimpleNamespace(
        application_service=service,
        repository=repository,
        workflow=workflow,
        transitions=transitions,
        jobs=jobs,
        workflow_adapter=workflow_adapter,
        permission_service=permission_service,
        actor=actor,
        create_original_project=create_original_project,
    )


def valid_original_brief(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择",
        "protagonist": "林岚",
        "protagonist_desire": "获得自主权",
        "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作",
        "main_opposition": "僵化制度",
        "core_conflict": "情绪考核",
        "main_goal": "改变制度",
        "background": "未来公司",
        "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核",
        "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"],
        "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁",
        "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low",
        "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"],
        "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }


def test_application_service_creates_first_original_brief_idempotently(
    creative_context,
) -> None:
    service = creative_context.application_service
    project = creative_context.create_original_project()
    task = service.ensure_stage_task(project["id"], "brief", creative_context.actor)
    brief = valid_original_brief(title="情绪公司")

    first = service.persist_draft(
        project_id=project["id"],
        stage="brief",
        payload=brief,
        workflow_task_id=task["id"],
        operation_id="act-1:brief",
        actor=creative_context.actor,
    )
    second = service.persist_draft(
        project_id=project["id"],
        stage="brief",
        payload=brief,
        workflow_task_id=task["id"],
        operation_id="act-1:brief",
        actor=creative_context.actor,
    )

    assert second == first
    assert first["resource_type"] == "brief"
    assert first["status"] == "draft"
    creative_context.workflow_adapter.assert_task_for_creation.assert_called_with(
        task["id"], "brief", creative_context.actor, project_id=project["id"]
    )
    creative_context.permission_service.can_create_project_content.assert_called()


def test_application_service_generation_job_contains_context(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="job-7", status="pending"
    )

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="outline",
        actor=creative_context.actor,
        workflow_task_id="outline-task",
        operation_id="act-7:outline",
        generation_instruction="加强第3集反转",
        source_snapshot_id="snapshot-7",
        source_snapshot={"project": {"id": project["id"], "revision": 7}},
    )

    payload = creative_context.jobs.get_or_create_job.call_args.args[1]
    job = SimpleNamespace(job=CrawlJob.from_dict({"job_id": "job-7", **payload}))
    assert job.job.extra["generation_instruction"] == "加强第3集反转"
    assert job.job.extra["source_snapshot_id"] == "snapshot-7"
    assert job.job.extra["operation_id"] == "act-7:outline"


def test_application_service_forwards_original_brief_context_to_job_and_hash(
    creative_context,
) -> None:
    """Would catch dropping frozen original inputs on the typed service boundary."""
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="original-brief-job", status="pending"
    )
    common = {
        "project_id": project["id"],
        "stage": "brief",
        "actor": creative_context.actor,
        "workflow_task_id": "brief-task",
        "operation_id": "original-brief-operation",
        "generation_instruction": "从冻结种子生成一个原创 Brief",
        "source_snapshot_id": "original-brief-snapshot",
        "source_snapshot": {"snapshot_id": "original-brief-snapshot"},
        "count": 1,
        "market_context": [{"resource_type": "topic", "resource_id": 9}],
        "blocked_terms": ["市场角色名"],
        "reference_snapshot": [{
            "resource_type": "topic", "resource_id": 9, "captured_at": 1.0,
        }],
    }

    creative_context.application_service.start_generation(seed_id=71, **common)
    first = creative_context.jobs.get_or_create_job.call_args.args[1]["extra"]
    creative_context.application_service.start_generation(seed_id=72, **common)
    second = creative_context.jobs.get_or_create_job.call_args.args[1]["extra"]

    assert first["seed_id"] == 71
    assert first["count"] == 1
    assert first["market_context"] == common["market_context"]
    assert first["blocked_terms"] == ["市场角色名"]
    assert first["reference_snapshot"] == common["reference_snapshot"]
    assert first["generation_request_hash"] != second["generation_request_hash"]


def test_application_service_reserves_generation_job_by_operation_id(
    creative_context,
) -> None:
    """Calling create_job directly would reopen the crash-window duplicate launch."""
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="reserved-job", status="pending"
    )

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="brief",
        actor=creative_context.actor,
        workflow_task_id="brief-task",
        operation_id="reserved-operation",
        generation_instruction="生成 Brief",
        source_snapshot_id="snapshot-reserved",
        source_snapshot={"snapshot_id": "snapshot-reserved"},
    )

    call = creative_context.jobs.get_or_create_job.call_args
    assert call.args[0] == "reserved-operation"
    assert call.args[1]["extra"]["operation_id"] == "reserved-operation"
    assert call.kwargs == {
        "created_by_user_id": creative_context.actor.user_id,
        "workflow_task_id": "brief-task",
        "project_id": project["id"],
    }
    creative_context.jobs.create_job.assert_not_called()


def test_application_service_marks_failed_operation_retry_without_changing_identity(
    creative_context,
) -> None:
    """A retry action must tell JobManager to relaunch the failed reservation."""
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="retry-job", status="pending"
    )

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="script",
        actor=creative_context.actor,
        workflow_task_id="script-task",
        operation_id="retry-operation",
        generation_instruction="生成第一集",
        source_snapshot_id="snapshot-retry",
        source_snapshot={"snapshot_id": "snapshot-retry"},
        episode_number=1,
        retry_failed_operation=True,
    )

    extra = creative_context.jobs.get_or_create_job.call_args.args[1]["extra"]
    assert extra["operation_id"] == "retry-operation"
    assert extra["retry_failed_operation"] is True


def test_application_service_spills_large_snapshot_to_relative_chunks(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="job-large", status="pending"
    )
    snapshot = {
        "snapshot_id": "snapshot-large",
        "material": "字" * MAX_INLINE_SOURCE_SNAPSHOT_BYTES,
    }

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="brief",
        actor=creative_context.actor,
        workflow_task_id="brief-task",
        operation_id="act-large:brief",
        generation_instruction="保留材料中的因果关系",
        source_snapshot_id="snapshot-large",
        source_snapshot=snapshot,
    )

    extra = creative_context.jobs.get_or_create_job.call_args.args[1]["extra"]
    assert "source_snapshot" not in extra
    reference = extra["source_snapshot_ref"]
    assert reference["read_only"] is True
    assert len(reference["paths"]) > 1
    assert all(not Path(path).is_absolute() for path in reference["paths"])
    assert all(
        (creative_context.application_service.runtime_dir / path).is_file()
        for path in reference["paths"]
    )


def test_first_brief_rejects_second_root_and_imitation_project(
    creative_context,
) -> None:
    service = creative_context.application_service
    project = creative_context.create_original_project()
    first = valid_original_brief("首个根 Brief")
    service.persist_draft(
        project_id=project["id"],
        stage="brief",
        payload=first,
        workflow_task_id="brief-task",
        operation_id="first-root",
        actor=creative_context.actor,
    )

    with pytest.raises(ValueError, match="revision"):
        service.persist_draft(
            project_id=project["id"],
            stage="brief",
            payload=valid_original_brief("第二个根 Brief"),
            workflow_task_id="brief-task",
            operation_id="second-root",
            actor=creative_context.actor,
        )

    imitation, _ = creative_context.repository.ensure_original_project(
        seed_text="仿写种子",
        name="仿写项目",
        target_episode_count=1,
        created_by=creative_context.actor.user_id,
        creation_request_id="imitation-project",
    )
    with creative_context.repository._connect() as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE creative_projects SET creation_mode = 'imitation' WHERE id = ?",
            (imitation["id"],),
        )
    with pytest.raises(ValueError, match="original"):
        service.persist_draft(
            project_id=imitation["id"],
            stage="brief",
            payload=valid_original_brief("错误原创根"),
            workflow_task_id="brief-task",
            operation_id="imitation-root",
            actor=creative_context.actor,
        )


def test_lock_replay_and_changed_key_are_checked_before_transition(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    result = {
        "resource": {"id": "brief-1", "status": "brief_locked"},
        "task": {"id": "brief-task", "status": "completed"},
    }
    creative_context.transitions.complete_creative_without_review.return_value = result
    service = creative_context.application_service

    first = service.lock_resource(
        project_id=project["id"],
        stage="brief",
        resource_id="brief-1",
        workflow_task_id="brief-task",
        operation_id="lock-op",
        actor=creative_context.actor,
    )
    replay = service.lock_resource(
        project_id=project["id"],
        stage="brief",
        resource_id="brief-1",
        workflow_task_id="brief-task",
        operation_id="lock-op",
        actor=creative_context.actor,
    )

    assert replay == first == result
    assert creative_context.transitions.complete_creative_without_review.call_count == 1
    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_REUSED"):
        service.lock_resource(
            project_id=project["id"],
            stage="brief",
            resource_id="brief-2",
            workflow_task_id="brief-task",
            operation_id="lock-op",
            actor=creative_context.actor,
        )
    assert creative_context.transitions.complete_creative_without_review.call_count == 1


def test_expired_lock_reservation_reconciles_after_side_effect_process_crash(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    result = {
        "resource": {"id": "brief-1", "status": "brief_locked"},
        "task": {"id": "brief-task", "status": "completed"},
    }
    request_hash = canonical_request_hash(
        {
            "workflow_task_id": "brief-task",
            "resource_id": "brief-1",
            "difference_note": "",
        }
    )
    creative_context.repository.reserve_side_effect_operation(
        operation_id="crashed-lock-op",
        project_id=project["id"],
        stage="brief.lock",
        request_hash=request_hash,
        owner_id="dead-process",
    )
    creative_context.transitions.complete_creative_without_review(
        "brief-task", "brief", "brief-1", creative_context.actor,
        difference_note="",
    )
    with creative_context.repository._connect() as connection:
        row = connection.execute(
            "SELECT resource_ref_json FROM creative_operation_results "
            "WHERE operation_id = ?",
            ("crashed-lock-op",),
        ).fetchone()
        pending = json.loads(row["resource_ref_json"])
        pending["lease_expires_at"] = 0
        connection.execute(
            "UPDATE creative_operation_results SET resource_ref_json = ? "
            "WHERE operation_id = ?",
            (json.dumps(pending), "crashed-lock-op"),
        )
    creative_context.transitions.reconcile_creative_without_review.return_value = result

    replay = creative_context.application_service.lock_resource(
        project_id=project["id"],
        stage="brief",
        resource_id="brief-1",
        workflow_task_id="brief-task",
        operation_id="crashed-lock-op",
        actor=creative_context.actor,
    )

    assert replay == result
    assert creative_context.transitions.complete_creative_without_review.call_count == 1
    creative_context.transitions.reconcile_creative_without_review.assert_called_once_with(
        "brief-task", "brief", "brief-1"
    )


def test_live_lock_reservation_rejects_duplicate_and_changed_request(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.repository.reserve_side_effect_operation(
        operation_id="live-lock-op",
        project_id=project["id"],
        stage="brief.lock",
        request_hash=canonical_request_hash(
            {
                "workflow_task_id": "brief-task",
                "resource_id": "brief-1",
                "difference_note": "",
            }
        ),
        owner_id="live-process",
    )

    with pytest.raises(RuntimeError, match="OPERATION_IN_PROGRESS"):
        creative_context.application_service.lock_resource(
            project_id=project["id"],
            stage="brief",
            resource_id="brief-1",
            workflow_task_id="brief-task",
            operation_id="live-lock-op",
            actor=creative_context.actor,
        )
    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_REUSED"):
        creative_context.application_service.lock_resource(
            project_id=project["id"],
            stage="brief",
            resource_id="brief-2",
            workflow_task_id="brief-task",
            operation_id="live-lock-op",
            actor=creative_context.actor,
        )
    creative_context.transitions.complete_creative_without_review.assert_not_called()


def test_lock_heartbeat_keeps_an_expired_live_owner_exclusive(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    request_hash = canonical_request_hash(
        {
            "workflow_task_id": "brief-task",
            "resource_id": "brief-1",
            "difference_note": "",
        }
    )
    creative_context.repository.reserve_side_effect_operation(
        operation_id="heartbeat-lock-op",
        project_id=project["id"],
        stage="brief.lock",
        request_hash=request_hash,
        owner_id="live-process",
    )
    with creative_context.repository._connect() as connection:
        row = connection.execute(
            "SELECT resource_ref_json FROM creative_operation_results "
            "WHERE operation_id = ?",
            ("heartbeat-lock-op",),
        ).fetchone()
        pending = json.loads(row["resource_ref_json"])
        pending["lease_expires_at"] = 0
        connection.execute(
            "UPDATE creative_operation_results SET resource_ref_json = ? "
            "WHERE operation_id = ?",
            (json.dumps(pending), "heartbeat-lock-op"),
        )

    assert creative_context.repository.renew_side_effect_operation(
        operation_id="heartbeat-lock-op", owner_id="live-process"
    )
    with pytest.raises(RuntimeError, match="OPERATION_IN_PROGRESS"):
        creative_context.repository.reserve_side_effect_operation(
            operation_id="heartbeat-lock-op",
            project_id=project["id"],
            stage="brief.lock",
            request_hash=request_hash,
            owner_id="duplicate-process",
        )


def test_locked_non_brief_legacy_saves_require_explicit_revision_authority(
    creative_context,
) -> None:
    service = creative_context.application_service
    repository = creative_context.repository
    project = creative_context.create_original_project()
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("锁定 Brief")}],
        direction_type="original",
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林岚"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "开端"}], "主线"
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"beat": "开场"}]}
    )
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(
        project["id"], 1, {"scenes": []}
    )
    repository.lock_episode_script(script["id"])

    cases = (
        ("characters", {"characters": [{"name": "林岚"}], "relationships": []}, None),
        ("outline", {"episodes": [{"episode": 1, "summary": "重做"}], "series_arc": "主线"}, None),
        ("beats", {"beats": [{"beat": "重做"}]}, 1),
        ("script", {"scenes": []}, 1),
    )
    for stage, payload, episode_number in cases:
        with pytest.raises(ValueError, match="replaces_resource_id.*expected_project_revision"):
            service.persist_legacy_draft(
                project_id=project["id"],
                stage=stage,
                payload=payload,
                workflow_task_id=f"{stage}-task",
                operation_id=f"locked-{stage}-save",
                actor=creative_context.actor,
                episode_number=episode_number,
            )


def test_locked_legacy_save_is_rechecked_inside_the_repository_transaction(
    creative_context, monkeypatch,
) -> None:
    service = creative_context.application_service
    repository = creative_context.repository
    project = creative_context.create_original_project()
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("锁定 Brief")}],
        direction_type="original",
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林岚"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    monkeypatch.setattr(repository, "has_locked_stage_resource", lambda *args, **kwargs: False)

    with pytest.raises(ValueError, match="revision"):
        service.persist_legacy_draft(
            project_id=project["id"],
            stage="characters",
            payload={"characters": [{"name": "绕过版本"}], "relationships": []},
            workflow_task_id="characters-task",
            operation_id="atomic-legacy-guard",
            actor=creative_context.actor,
        )
def test_generation_always_adds_server_authority_to_material_snapshot(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.jobs.get_or_create_job.return_value = SimpleNamespace(
        job_id="authority-job", status="pending"
    )

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="brief",
        actor=creative_context.actor,
        workflow_task_id="brief-task",
        operation_id="authority-op",
        generation_instruction="只使用冻结材料",
        source_snapshot_id="material-only",
        source_snapshot={"snapshot_id": "material-only", "materials": ["素材"]},
    )

    extra = creative_context.jobs.get_or_create_job.call_args.args[1]["extra"]
    assert extra["source_authority"]["project_id"] == project["id"]
    assert extra["source_authority"]["revision"] == project["revision"]
    assert extra["source_authority_sha256"]
    assert extra["generation_request_hash"]


def test_application_service_hydrates_legacy_resource_shapes(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    brief = creative_context.repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("可水合 Brief")}],
        direction_type="original",
    )[0]
    creative_context.repository.lock_brief(brief["id"])
    characters = creative_context.repository.create_character_batch(
        project["id"], [{"name": "林岚"}], []
    )

    hydrated = creative_context.application_service.hydrate_resource_ref(
        creative_context.application_service.resource_ref("characters", characters)
    )

    assert hydrated["batch_id"] == characters["batch_id"]
    assert hydrated["characters"][0]["id"] == characters["characters"][0]["id"]
