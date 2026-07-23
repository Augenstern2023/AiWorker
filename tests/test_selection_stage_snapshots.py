# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.service import AuditService
from creative.api import build_creative_router, creative_api_principal
from creative.repository import CreativeRepository
from creative.selection_stage_service import SelectionStageService
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from identity.schema import Principal
from core.job import CrawlJob
from runtime.job_state import JobState
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository
from workflow.transition_service import WORKFLOW_NODES
from scripts.migrate_role_workflow import migrate_selection_stages

import WebGUI


class _CreativeJobManager:
    def __init__(self) -> None:
        self.states: list[JobState] = []

    def create_job(self, data: dict) -> JobState:
        payload = dict(data)
        payload.setdefault("job_id", f"creative-{len(self.states) + 1}")
        state = JobState(job=CrawlJob.from_dict(payload), status="running")
        self.states.append(state)
        return state

    def list_jobs(self) -> list[dict]:
        return [state.to_dict() for state in self.states]


def test_production_workflow_starts_at_brief() -> None:
    assert list(WORKFLOW_NODES) == [
        "brief",
        "characters",
        "outline",
        "beats",
        "delivery",
    ]


def _topic(repository: CreativeRepository, title: str = "免审测试短剧") -> dict:
    return repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-job",
                "source_path": "data/hg/rank.json",
                "captured_at": "2026-07-23",
                "title": title,
                "description": "测试简介",
                "category": "都市",
                "rank_type": "热播榜",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]


def test_selection_stage_snapshots_are_versioned_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = _topic(repository)

    first = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        actor_user_id="selector-1",
        source_job_id="job-1",
        deduplicate=True,
    )
    repeated = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        actor_user_id="selector-1",
        source_job_id="job-1",
        deduplicate=True,
    )
    changed = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1, 2]},
        actor_user_id="selector-1",
        source_job_id="job-2",
        deduplicate=True,
    )

    assert repeated["id"] == first["id"]
    assert changed["version"] == 2
    assert changed["snapshot"]["valid_transcript_episodes"] == [1, 2]
    with repository._connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE selection_stage_snapshots SET outcome = 'changed' WHERE id = ?",
            (first["id"],),
        )
    with repository._connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "DELETE FROM selection_stage_snapshots WHERE id = ?", (first["id"],)
        )


def test_topic_decisions_keep_each_append_even_when_content_matches(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = _topic(repository)

    first = repository.append_selection_stage_snapshot(
        "topic_decision",
        topic["id"],
        "adopted",
        {"decision_note": "继续推进"},
        actor_user_id="selector-1",
    )
    second = repository.append_selection_stage_snapshot(
        "topic_decision",
        topic["id"],
        "adopted",
        {"decision_note": "继续推进"},
        actor_user_id="selector-1",
    )

    assert first["content_fingerprint"] == second["content_fingerprint"]
    assert second["version"] == 2


def test_source_package_becomes_ready_with_one_valid_transcript_and_records_skips(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / ".runtime/creative/creative.db")
    topic = _topic(repository, "部分成功短剧")
    episode_dir = (
        tmp_path
        / "data/hg/jobs/job-1/dramas/部分成功短剧/episode_01"
    )
    episode_dir.mkdir(parents=True)
    (episode_dir / "source.json").write_text(
        json.dumps({"title": topic["title"], "episode": {"episode": 1}}),
        encoding="utf-8",
    )
    (episode_dir / "transcript.json").write_text(
        json.dumps({"source": "asr", "text": "第一集有效转写"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (episode_dir / "script.md").write_text("第一集有效转写", encoding="utf-8")
    manifest = episode_dir.parent / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "items": [
                    {"episode": 1, "status": "succeeded"},
                    {"episode": 2, "status": "skipped_paid", "reason": "付费"},
                    {"episode": 3, "status": "failed", "reason": "接口超时"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = SelectionStageService(repository, tmp_path).record_source_readiness(
        topic["id"], source_job_id="job-1", actor_user_id="selector-1"
    )

    assert result is not None
    assert result["outcome"] == "ready"
    assert result["snapshot"]["valid_transcript_episodes"] == [1]
    assert result["snapshot"]["skipped_paid"][0]["episode"] == 2
    assert result["snapshot"]["failed"][0]["episode"] == 3


def test_source_package_stays_blocked_without_nonempty_transcript(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / ".runtime/creative/creative.db")
    topic = _topic(repository, "空转写短剧")
    episode_dir = tmp_path / "data/hg/jobs/job-2/dramas/空转写短剧/episode_01"
    episode_dir.mkdir(parents=True)
    (episode_dir / "source.json").write_text(
        json.dumps({"title": topic["title"], "episode": {"episode": 1}}),
        encoding="utf-8",
    )
    (episode_dir / "transcript.json").write_text(
        json.dumps({"source": "asr", "text": ""}), encoding="utf-8"
    )

    result = SelectionStageService(repository, tmp_path).record_source_readiness(
        topic["id"], source_job_id="job-2", actor_user_id="selector-1"
    )

    assert result is None
    assert repository.get_latest_selection_stage_snapshot(
        topic["id"], "source_package"
    ) is None


def test_selection_operator_adopts_topic_directly_with_snapshot_and_audit(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    role = identity.get_role_by_code("selection_operator")
    assert role is not None
    user = identity.create_user("selector", "选品运营", "hash")
    identity.replace_user_roles(user["id"], [role["id"]])
    permission_service = PermissionService(identity, SessionService(identity))
    principal = permission_service.principal_for_user(user["id"], "session")
    assert principal is not None
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository)
    audit = AuditService(identity)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.audit_service = audit
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))

    response = TestClient(app).patch(
        f"/api/creative/topics/{topic['id']}",
        json={"status": "adopted", "decision_note": "进入素材准备"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "adopted"
    snapshot = repository.get_latest_selection_stage_snapshot(
        topic["id"], "topic_decision"
    )
    assert snapshot is not None
    assert snapshot["outcome"] == "adopted"
    assert snapshot["actor_user_id"] == principal.user_id
    events = audit.query(action="selection.topic_decision")
    assert len(events) == 1
    assert events[0]["actor_user_id"] == principal.user_id


def test_adopted_topic_download_context_does_not_require_source_package_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = _topic(repository, "直接下载短剧")
    repository.decide_topic(topic["id"], "adopted", "", "selector-1")
    principal = Principal(
        user_id="selector-1",
        username="selector",
        display_name="选品运营",
        role_codes=("selection_operator",),
        permissions=frozenset(
            {"drama.download", "job.create.selection", "topic.read"}
        ),
        data_scope="own",
        session_id="session",
    )
    monkeypatch.setattr(WebGUI, "creative_repository", repository)

    task_id, project_id = WebGUI._job_task_context(
        None,
        principal,
        expected_task_type="source_package",
        topic_id=topic["id"],
        topic_title=topic["title"],
    )

    assert task_id is None
    assert project_id is None


def test_breakdown_generation_uses_material_snapshot_without_personnel_task(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository, "免任务拆解短剧")
    repository.decide_topic(topic["id"], "adopted", "", "selector-1")
    episode_dir = tmp_path / "data/hg/jobs/job-3/dramas/免任务拆解短剧/episode_01"
    episode_dir.mkdir(parents=True)
    (episode_dir / "source.json").write_text(
        json.dumps({"title": topic["title"], "episode": {"episode": 1}}),
        encoding="utf-8",
    )
    (episode_dir / "script.md").write_text("第一集剧情正文", encoding="utf-8")
    material = SelectionStageService(repository, tmp_path).record_source_readiness(
        topic["id"], source_job_id="job-3", actor_user_id="selector-1"
    )
    assert material is not None
    manager = _CreativeJobManager()
    principal = Principal(
        user_id="selector-1",
        username="selector",
        display_name="选品运营",
        role_codes=("selection_operator",),
        permissions=frozenset(
            {"breakdown.generate", "job.create.selection", "topic.read"}
        ),
        data_scope="own",
        session_id="session",
    )
    app = FastAPI()
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    permission_service = PermissionService(identity, SessionService(identity))
    permission_service.principal_for_session = lambda _token: principal  # type: ignore[method-assign]
    app.state.identity_permission_service = permission_service
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(
        build_creative_router(root=tmp_path, runtime_dir=runtime, job_manager=manager)
    )

    response = TestClient(app).post(
        "/api/creative/breakdowns", json={"topic_id": topic["id"]}
    )

    assert response.status_code == 200, response.text
    assert len(manager.states) == 1
    extra = manager.states[0].job.extra
    assert extra["material_snapshot_id"] == material["id"]
    assert extra.get("workflow_task_id") in {None, ""}


def test_breakdown_records_the_material_snapshot_it_was_generated_from(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = _topic(repository)
    material = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        deduplicate=True,
    )

    breakdown = repository.create_breakdown(
        topic["id"],
        {"title": topic["title"]},
        {},
        [],
        source_snapshot_id=material["id"],
    )

    assert breakdown["source_snapshot_id"] == material["id"]


def test_selection_operator_completes_breakdown_without_approval(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    role = identity.get_role_by_code("selection_operator")
    assert role is not None
    user = identity.create_user("breakdown-selector", "拆解运营", "hash")
    identity.replace_user_roles(user["id"], [role["id"]])
    permission_service = PermissionService(identity, SessionService(identity))
    principal = permission_service.principal_for_user(user["id"], "session")
    assert principal is not None
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository)
    repository.decide_topic(topic["id"], "adopted", "", principal.user_id)
    material = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        deduplicate=True,
    )
    breakdown = repository.create_breakdown(
        topic["id"],
        {"title": topic["title"], "core_conflict": "冲突"},
        {},
        [],
        source_snapshot_id=material["id"],
    )
    audit = AuditService(identity)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.audit_service = audit
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))

    response = TestClient(app).post(
        f"/api/creative/breakdowns/{breakdown['id']}/lock"
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "locked"
    snapshot = repository.get_latest_selection_stage_snapshot(
        topic["id"], "breakdown"
    )
    assert snapshot is not None
    assert snapshot["resource_id"] == str(breakdown["id"])
    assert audit.query(action="selection.breakdown_complete")
    assert TestClient(app).patch(
        f"/api/creative/breakdowns/{breakdown['id']}",
        json={"content": {"title": "不可修改"}},
    ).status_code == 409


def test_project_creation_is_idempotent_and_bootstraps_one_brief_pool_task(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    permission_service = PermissionService(identity, SessionService(identity))
    principal = Principal(
        user_id="workflow:system",
        username="admin",
        display_name="系统管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session",
    )
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository, "项目幂等短剧")
    repository.decide_topic(topic["id"], "adopted", "", principal.user_id)
    material = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        deduplicate=True,
    )
    breakdown = repository.create_breakdown(
        topic["id"],
        {"title": topic["title"]},
        {},
        [],
        source_snapshot_id=material["id"],
    )
    repository.complete_breakdown(breakdown["id"], principal.user_id)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    client = TestClient(app)
    payload = {
        "topic_id": topic["id"],
        "breakdown_id": breakdown["id"],
        "name": "项目 A",
        "creation_request_id": "create-request-a",
    }

    first = client.post("/api/creative/projects", json=payload)
    repeated = client.post("/api/creative/projects", json=payload)

    assert first.status_code == 200, first.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    with identity.connect() as connection:
        tasks = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM workflow_tasks ORDER BY created_at, id"
            ).fetchall()
        ]
    brief_tasks = [task for task in tasks if task["task_type"] == "brief"]
    assert len(brief_tasks) == 1
    assert brief_tasks[0]["status"] == "pool"
    assert brief_tasks[0]["role_code"] == "creative_planner"
    assert brief_tasks[0]["project_id"] == str(first.json()["id"])

    second = client.post(
        "/api/creative/projects",
        json={**payload, "name": "项目 B", "creation_request_id": "create-request-b"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] != first.json()["id"]


def test_project_owner_assigns_locked_topic_to_planner_and_manages_project(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    planner_role = identity.get_role_by_code("creative_planner")
    assert owner_role is not None and planner_role is not None
    owner_user = identity.create_user("owner-assign-topic", "项目负责人", "hash")
    planner_user = identity.create_user("planner-assignee", "创意策划甲", "hash")
    identity.replace_user_roles(owner_user["id"], [owner_role["id"]])
    identity.replace_user_roles(planner_user["id"], [planner_role["id"]])
    permission_service = PermissionService(identity, SessionService(identity))
    owner = permission_service.principal_for_user(owner_user["id"], "owner-session")
    assert owner is not None
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository, "负责人分配短剧")
    repository.decide_topic(topic["id"], "adopted", "", owner.user_id)
    material = repository.append_selection_stage_snapshot(
        "source_package",
        topic["id"],
        "ready",
        {"valid_transcript_episodes": [1]},
        deduplicate=True,
    )
    breakdown = repository.create_breakdown(
        topic["id"],
        {"title": topic["title"]},
        {},
        [],
        source_snapshot_id=material["id"],
    )
    repository.complete_breakdown(breakdown["id"], owner.user_id)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.audit_service = AuditService(identity)
    app.dependency_overrides[creative_api_principal] = lambda: owner
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    client = TestClient(app)
    payload = {
        "assignee_user_id": planner_user["id"],
        "name": "负责人创建的策划项目",
        "target_platform": "hg",
        "target_episode_count": 20,
        "creation_request_id": "owner-assignment-1",
    }

    assigned = client.post(
        f"/api/creative/topics/{topic['id']}/planning-assignment",
        json=payload,
    )
    repeated = client.post(
        f"/api/creative/topics/{topic['id']}/planning-assignment",
        json=payload,
    )

    assert assigned.status_code == 200, assigned.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["project"]["id"] == assigned.json()["project"]["id"]
    assert assigned.json()["task"]["status"] == "assigned"
    assert assigned.json()["task"]["assignee_user_id"] == planner_user["id"]
    project_id = str(assigned.json()["project"]["id"])
    workflow = WorkflowRepository(identity)
    members = workflow.list_project_members(project_id)
    assert {
        (item["user_id"], item["project_role"], item["status"])
        for item in members
    } == {
        (owner.user_id, "project_owner", "active"),
        (planner_user["id"], "creative_planner", "active"),
    }
    visible_projects = client.get("/api/creative/projects")
    assert visible_projects.status_code == 200
    assert [item["id"] for item in visible_projects.json()["items"]] == [
        int(project_id)
    ]


def test_selection_stage_migration_cancels_open_legacy_tasks_and_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    admin = identity.create_user("migration-admin", "迁移管理员", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])
    repository = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(repository, "迁移短剧")
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(
        topic["id"], {"title": topic["title"]}, {}, []
    )
    repository.lock_breakdown(breakdown["id"])
    workflow = WorkflowRepository(identity)
    legacy = workflow.create_task(
        f"topic:{topic['id']}",
        "source_package",
        "selection_operator",
        topic_id=topic["id"],
        actor={"user_id": admin["id"]},
    )

    dry_run = migrate_selection_stages(
        tmp_path, admin["id"], dry_run=True
    )
    assert dry_run.cancelled_tasks == 1
    assert workflow.get_task(legacy["id"])["status"] == "pool"

    applied = migrate_selection_stages(tmp_path, admin["id"])
    repeated = migrate_selection_stages(tmp_path, admin["id"])

    assert applied.cancelled_tasks == 1
    assert len(applied.backups) == 2
    assert workflow.get_task(legacy["id"])["status"] == "cancelled"
    assert repeated.already_applied is True
    refreshed = CreativeRepository(runtime / "creative/creative.db")
    assert refreshed.get_latest_selection_stage_snapshot(
        topic["id"], "topic_decision"
    ) is not None
    assert refreshed.get_latest_selection_stage_snapshot(
        topic["id"], "breakdown"
    ) is not None


def test_project_owner_can_review_waiting_project_task_without_workload_assignment(
    tmp_path: Path,
) -> None:
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    assert owner_role is not None
    owner_user = identity.create_user("review-owner", "项目负责人", "hash")
    identity.replace_user_roles(owner_user["id"], [owner_role["id"]])
    owner = PermissionService(identity, SessionService(identity)).principal_for_user(
        owner_user["id"], "owner-session"
    )
    assert owner is not None
    workflow = WorkflowRepository(identity)
    task = workflow.ensure_project_task(
        "project-1", "brief", "creative_planner", actor=SYSTEM_ACTOR
    )
    with identity.transaction() as connection:
        connection.execute(
            "UPDATE workflow_tasks SET status = 'waiting_review' WHERE id = ?",
            (task["id"],),
        )

    visible = workflow.list_tasks(owner)

    assert [item["id"] for item in visible] == [task["id"]]
