# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import make_url

from core.job import CrawlJob
from creative.api import build_creative_router
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from runtime.job_state import JobState
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.api import build_workflow_router
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


def _postgres_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SystemDatabase, IdentityRepository]:
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
    return database, identity


def _role_user(identity: IdentityRepository, role_code: str, label: str) -> dict:
    role = identity.get_role_by_code(role_code)
    assert role is not None
    user = identity.create_user(
        f"stage-member-{role_code}-{uuid.uuid4().hex}", label, "hash"
    )
    identity.replace_user_roles(user["id"], [role["id"]])
    return user


def _client(app: FastAPI, sessions: SessionService, user_id: str) -> TestClient:
    session_app = FastAPI()

    @session_app.get("/session")
    def create_session(request: Request):
        return sessions.create_session(user_id, request)

    token, csrf = TestClient(session_app).get("/session").json()
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, token)
    client.headers["X-CSRF-Token"] = csrf
    return client


class _CreativeJobManager:
    def __init__(self) -> None:
        self.states: list[JobState] = []

    def create_job(self, data: dict) -> JobState:
        payload = dict(data)
        payload.setdefault("job_id", f"member-stage-{len(self.states) + 1}")
        state = JobState(job=CrawlJob.from_dict(payload), status="running")
        self.states.append(state)
        return state


def test_active_project_members_can_collaborate_on_any_stage_task(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    project_id = f"stage-member-project-{uuid.uuid4().hex}"
    administrator_user = _role_user(identity, "system_admin", "阶段成员管理员")
    first_member_user = _role_user(identity, "selection_operator", "跨阶段成员甲")
    collaborator_user = _role_user(identity, "selection_operator", "跨阶段成员乙")
    inactive_user = _role_user(identity, "selection_operator", "失效项目成员")
    outsider_user = _role_user(identity, "selection_operator", "项目外账号")
    users = (
        administrator_user,
        first_member_user,
        collaborator_user,
        inactive_user,
        outsider_user,
    )

    def cleanup() -> None:
        workflow.delete_project_data(project_id)
        for user in users:
            if identity.get_user(user["id"]) is not None:
                identity.soft_delete_user(user["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    permissions = PermissionService(identity, SessionService(identity))
    administrator = permissions.principal_for_user(
        administrator_user["id"], "stage-admin"
    )
    first_member = permissions.principal_for_user(
        first_member_user["id"], "stage-first"
    )
    collaborator = permissions.principal_for_user(
        collaborator_user["id"], "stage-collaborator"
    )
    inactive_member = permissions.principal_for_user(
        inactive_user["id"], "stage-inactive"
    )
    outsider = permissions.principal_for_user(outsider_user["id"], "stage-outsider")
    assert all(
        principal is not None
        for principal in (
            administrator,
            first_member,
            collaborator,
            inactive_member,
            outsider,
        )
    )
    assert administrator is not None
    assert first_member is not None
    assert collaborator is not None
    assert inactive_member is not None
    assert outsider is not None

    workflow.add_project_member(
        project_id, first_member.user_id, "screenwriter", administrator
    )
    workflow.add_project_member(
        project_id, collaborator.user_id, "project_owner", administrator
    )
    workflow.add_project_member(
        project_id,
        inactive_member.user_id,
        "creative_planner",
        administrator,
        status="inactive",
    )
    tasks = [
        workflow.create_task(
            f"project:{project_id}:{task_type}",
            task_type,
            role_code,
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )
        for task_type, role_code in (
            ("brief", "creative_planner"),
            ("characters", "creative_planner"),
            ("outline", "screenwriter"),
            ("beats", "screenwriter"),
            ("script", "screenwriter"),
        )
    ]

    visible_ids = {item["id"] for item in workflow.list_tasks(first_member)}
    assert {task["id"] for task in tasks} <= visible_ids
    for task in tasks:
        assert workflow.can_claim_task(task["id"], first_member) is True
        assert workflow.can_claim_task(task["id"], inactive_member) is False
        assert workflow.can_claim_task(task["id"], outsider) is False
        claimed = workflow.claim_task(task["id"], first_member)
        assert claimed["assignee_user_id"] == first_member.user_id
        with pytest.raises(PermissionError):
            workflow.start_task(task["id"], outsider)
        started = workflow.start_task(task["id"], collaborator)
        assert started["status"] == "in_progress"
        with identity.transaction() as connection:
            completed = workflow.transition_task_in_transaction(
                task["id"], "complete_work", collaborator, connection=connection
            )
        assert completed["status"] == "completed"


def test_project_member_without_stage_permissions_can_generate_edit_and_complete(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    administrator_user = _role_user(identity, "system_admin", "全阶段管理员")
    assignee_user = _role_user(identity, "creative_planner", "原任务负责人")
    collaborator_role = identity.create_role(
        f"stage_collaborator_{uuid.uuid4().hex}", "无全局创作权限成员", ""
    )
    collaborator_user = identity.create_user(
        f"stage-collaborator-{uuid.uuid4().hex}", "跨阶段协作者", "hash"
    )
    identity.replace_user_roles(
        collaborator_user["id"], [collaborator_role["id"]]
    )
    outsider_user = _role_user(identity, "selection_operator", "项目外账号")
    users = (
        administrator_user,
        assignee_user,
        collaborator_user,
        outsider_user,
    )
    administrator = permissions.principal_for_user(
        administrator_user["id"], "member-stage-admin"
    )
    assignee = permissions.principal_for_user(
        assignee_user["id"], "member-stage-assignee"
    )
    assert administrator is not None
    assert assignee is not None

    runtime = tmp_path / ".runtime"
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = creative.upsert_topics(
        [
            {
                "platform": "dy",
                "job_id": "member-stage-topic",
                "source_path": "member-stage.json",
                "captured_at": "2026-08-18",
                "title": "项目成员全阶段协作",
                "score": 91,
                "confidence": "high",
                "score_details": {},
                "recommendation": "adopt",
                "evidence": [],
            }
        ]
    )[0]
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"hook": "source"}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    project = creative.create_project(
        topic["id"], breakdown["id"], "成员协作项目", 1
    )
    project_id = str(project["id"])

    def cleanup() -> None:
        workflow.delete_project_data(project_id)
        for user in users:
            if identity.get_user(user["id"]) is not None:
                identity.soft_delete_user(user["id"])
        identity.soft_delete_role(collaborator_role["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    workflow.add_project_member(
        project_id, assignee.user_id, "creative_planner", administrator
    )
    workflow.add_project_member(
        project_id,
        collaborator_user["id"],
        "screenwriter",
        administrator,
    )
    task = workflow.create_task(
        f"project:{project_id}",
        "brief",
        "creative_planner",
        project_id=project_id,
        actor=SYSTEM_ACTOR,
    )
    workflow.claim_task(task["id"], assignee)
    blueprint = creative.create_blueprints(
        project["id"],
        [{"content": {"title": "初始 Brief", "hook": "source"}}],
        workflow_task_id=task["id"],
    )[0]

    manager = _CreativeJobManager()
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_workflow_router(workflow))
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=runtime,
            job_manager=manager,
            workflow_repository=workflow,
        )
    )
    collaborator_client = _client(app, sessions, collaborator_user["id"])
    outsider_client = _client(app, sessions, outsider_user["id"])

    listed = collaborator_client.get("/api/creative/projects")
    assert listed.status_code == 200, listed.text
    assert project_id in {str(item["id"]) for item in listed.json()["items"]}
    viewed = collaborator_client.get(f"/api/creative/projects/{project_id}")
    assert viewed.status_code == 200, viewed.text
    assert blueprint["id"] in {
        item["id"] for item in viewed.json()["blueprints"]
    }

    started = collaborator_client.post(
        f"/api/workflow/tasks/{task['id']}/start"
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"

    settings = collaborator_client.patch(
        f"/api/creative/projects/{project_id}",
        json={"name": "项目成员更新的项目名称", "target_episode_count": 2},
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["name"] == "项目成员更新的项目名称"
    assert settings.json()["target_episode_count"] == 2

    forbidden_status = collaborator_client.patch(
        f"/api/creative/projects/{project_id}",
        json={"status": "archived"},
    )
    assert forbidden_status.status_code == 403

    generated = collaborator_client.post(
        f"/api/creative/projects/{project_id}/blueprints/generate",
        json={"count": 3, "workflow_task_id": task["id"]},
    )
    assert generated.status_code == 200, generated.text
    assert manager.states[-1].job.extra["created_by_user_id"] == collaborator_user["id"]

    versioned = collaborator_client.post(
        f"/api/creative/blueprints/{blueprint['id']}/versions",
        json={
            "workflow_task_id": task["id"],
            "expected_project_revision": creative.get_project(project["id"])["revision"],
        },
    )
    assert versioned.status_code == 200, versioned.text
    assert versioned.json()["workflow_task_id"] == task["id"]

    edited = collaborator_client.patch(
        f"/api/creative/blueprints/{blueprint['id']}",
        json={
            "content": {"title": "协作者修改后的 Brief", "hook": "new"},
            "difference_note": "由项目成员协作修改",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["content"]["title"] == "协作者修改后的 Brief"

    completed = collaborator_client.post(
        f"/api/creative/blueprints/{blueprint['id']}/lock-brief",
        json={
            "workflow_task_id": task["id"],
            "difference_note": "由项目成员协作完成",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["task"]["status"] == "completed"
    assert workflow.list_task_events(task["id"])[-1]["actor_user_id"] == collaborator_user["id"]

    denied = outsider_client.post(
        f"/api/creative/projects/{project_id}/blueprints/generate",
        json={"count": 3, "workflow_task_id": task["id"]},
    )
    assert denied.status_code == 403

    workflow.remove_project_member(
        project_id, collaborator_user["id"], administrator
    )
    revoked = collaborator_client.post(
        f"/api/creative/blueprints/{blueprint['id']}/versions",
        json={"workflow_task_id": task["id"]},
    )
    assert revoked.status_code == 403
