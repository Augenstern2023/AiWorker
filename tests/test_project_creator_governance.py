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

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from creative.api import build_creative_router, creative_api_principal
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.api import build_workflow_router
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


def _role_user(
    identity: IdentityRepository, role_code: str, label: str
) -> dict:
    role = identity.get_role_by_code(role_code)
    assert role is not None
    user = identity.create_user(
        f"governance-{role_code}-{uuid.uuid4().hex}", label, "hash"
    )
    identity.replace_user_roles(user["id"], [role["id"]])
    return user


def _set_actor(app: FastAPI, principal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[creative_api_principal] = lambda: principal


def _open_stage_task(
    workflow: WorkflowRepository,
    client: TestClient,
    project_id: str,
    task_type: str,
) -> dict:
    role_code = "creative_planner" if task_type in {"brief", "characters"} else "screenwriter"
    task = workflow.create_task(
        f"governance:{project_id}:{task_type}:{uuid.uuid4().hex}",
        task_type,
        role_code,
        project_id=project_id,
        actor=SYSTEM_ACTOR,
    )
    claimed = client.post(f"/api/workflow/tasks/{task['id']}/claim")
    assert claimed.status_code == 200, claimed.text
    started = client.post(f"/api/workflow/tasks/{task['id']}/start")
    assert started.status_code == 200, started.text
    return started.json()


def _exercise_five_stage_creation_edit_and_lock(
    client: TestClient,
    workflow: WorkflowRepository,
    creative: CreativeRepository,
    project_id: str,
) -> None:
    brief_task = _open_stage_task(workflow, client, project_id, "brief")
    initial_brief = creative.create_blueprints(
        int(project_id),
        [{"content": {"project_name": "初稿", "target_episode_count": 1}}],
        workflow_task_id=brief_task["id"],
    )[0]
    created_brief = client.post(
        f"/api/creative/blueprints/{initial_brief['id']}/versions",
        json={"workflow_task_id": brief_task["id"]},
    )
    assert created_brief.status_code == 200, created_brief.text
    brief_id = created_brief.json()["id"]
    edited_brief = client.patch(
        f"/api/creative/blueprints/{brief_id}",
        json={
            "content": {
                "project_name": "确认稿",
                "target_episode_count": 1,
                "episode_duration_seconds": 120,
            }
        },
    )
    assert edited_brief.status_code == 200, edited_brief.text
    locked_brief = client.post(
        f"/api/creative/blueprints/{brief_id}/lock-brief",
        json={"workflow_task_id": brief_task["id"]},
    )
    assert locked_brief.status_code == 200, locked_brief.text
    assert locked_brief.json()["resource"]["status"] == "brief_locked"

    character_task = _open_stage_task(workflow, client, project_id, "characters")
    created_characters = client.post(
        f"/api/creative/projects/{project_id}/characters",
        json={
            "characters": [{"name": "主角"}],
            "relationships": [],
            "workflow_task_id": character_task["id"],
        },
    )
    assert created_characters.status_code == 200, created_characters.text
    character_batch = created_characters.json()
    character_id = character_batch["characters"][0]["id"]
    edited_character = client.patch(
        f"/api/creative/characters/{character_id}",
        json={"content": {"name": "主角", "identity": "创作者"}},
    )
    assert edited_character.status_code == 200, edited_character.text
    locked_characters = client.post(
        f"/api/creative/projects/{project_id}/characters/"
        f"{character_batch['batch_id']}/lock",
        json={"workflow_task_id": character_task["id"]},
    )
    assert locked_characters.status_code == 200, locked_characters.text
    assert locked_characters.json()["resource"]["characters"][0]["status"] == "locked"

    outline_task = _open_stage_task(workflow, client, project_id, "outline")
    created_outline = client.post(
        f"/api/creative/projects/{project_id}/outlines",
        json={
            "series_arc": "成长",
            "episodes": [{"episode": 1, "summary": "开始"}],
            "workflow_task_id": outline_task["id"],
        },
    )
    assert created_outline.status_code == 200, created_outline.text
    outline_batch = created_outline.json()
    outline_id = outline_batch["episodes"][0]["id"]
    edited_outline = client.patch(
        f"/api/creative/episodes/{outline_id}",
        json={"content": {"episode": 1, "summary": "推进"}},
    )
    assert edited_outline.status_code == 200, edited_outline.text
    locked_outline = client.post(
        f"/api/creative/projects/{project_id}/outlines/"
        f"{outline_batch['batch_id']}/lock",
        json={"workflow_task_id": outline_task["id"]},
    )
    assert locked_outline.status_code == 200, locked_outline.text
    assert locked_outline.json()["resource"]["episodes"][0]["status"] == "locked"

    beats_task = _open_stage_task(workflow, client, project_id, "beats")
    beats_content = {
        "beats": [
            {
                "order": 1,
                "scene": "办公室",
                "characters": ["主角"],
                "goal": "完成任务",
                "obstacle": "时间不足",
                "emotion_change": "焦虑到坚定",
                "turn": "找到线索",
                "hook": "门被推开",
            }
        ]
    }
    created_beats = client.post(
        f"/api/creative/projects/{project_id}/episodes/1/beats",
        json={"content": beats_content, "workflow_task_id": beats_task["id"]},
    )
    assert created_beats.status_code == 200, created_beats.text
    beats_id = created_beats.json()["id"]
    beats_content["beats"][0]["hook"] = "电话响起"
    edited_beats = client.patch(
        f"/api/creative/episodes/{beats_id}", json={"content": beats_content}
    )
    assert edited_beats.status_code == 200, edited_beats.text
    locked_beats = client.post(
        f"/api/creative/episodes/{beats_id}/lock",
        json={"workflow_task_id": beats_task["id"]},
    )
    assert locked_beats.status_code == 200, locked_beats.text
    assert locked_beats.json()["resource"]["status"] == "locked"

    script_task = _open_stage_task(workflow, client, project_id, "script")
    script_content = {
        "episode_title": "第一集",
        "scenes": [
            {
                "sequence_no": 1,
                "scene_no": 1,
                "time_of_day": "日",
                "interior_exterior": "内",
                "location": "办公室",
                "props": [],
                "characters": ["主角"],
                "blocks": [{"type": "action", "text": "主角推门而入。"}],
            }
        ],
    }
    created_script = client.post(
        f"/api/creative/projects/{project_id}/episodes/1/scripts",
        json={"content": script_content, "workflow_task_id": script_task["id"]},
    )
    assert created_script.status_code == 200, created_script.text
    script_id = created_script.json()["id"]
    edited_script = client.patch(
        f"/api/creative/episodes/{script_id}",
        json={
            "content": {
                **script_content,
                "episode_title": "第一集确认稿",
            }
        },
    )
    assert edited_script.status_code == 200, edited_script.text
    locked_script = client.post(
        f"/api/creative/scripts/{edited_script.json()['id']}/lock",
        json={"workflow_task_id": script_task["id"]},
    )
    assert locked_script.status_code == 200, locked_script.text
    assert locked_script.json()["resource"]["status"] == "locked"


def test_project_creator_can_contribute_but_cannot_govern_or_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    owner_role = identity.get_role_by_code("project_owner")
    writer_role = identity.get_role_by_code("screenwriter")
    assert owner_role is not None and writer_role is not None
    owner_user = identity.create_user(f"owner-{uuid.uuid4().hex}", "负责人", "hash")
    creator_user = identity.create_user(f"creator-{uuid.uuid4().hex}", "创建人", "hash")
    identity.replace_user_roles(owner_user["id"], [owner_role["id"]])
    identity.replace_user_roles(creator_user["id"], [writer_role["id"]])
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "owner-session")
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert owner is not None and creator is not None
    workflow = WorkflowRepository(identity)
    project_id = f"governance-{uuid.uuid4().hex}"
    try:
        workflow.add_project_member(project_id, creator.user_id, "project_creator", creator, allow_creator_bootstrap=True)

        assert permissions.can_create_project_content(creator, project_id, active_member=True)
        assert not permissions.can_govern_project(creator, project_id)
        assert not permissions.can_delete_project(creator, project_id)
        assert permissions.can_govern_project(owner, project_id)
        assert permissions.can_delete_project(owner, project_id)

        creator_member = workflow.list_project_members(project_id)[0]
        workflow.remove_project_member(project_id, creator_member["id"], owner)
        assert not workflow.is_active_project_member(project_id, creator)
        assert not permissions.can_create_project_content(creator, project_id, active_member=False)
        assert workflow.list_project_members(project_id)[0]["status"] == "inactive"
    finally:
        workflow.delete_project_data(project_id)
        identity.soft_delete_user(owner_user["id"])
        identity.soft_delete_user(creator_user["id"])
        database.dispose()


def test_project_governance_http_matrix_and_five_stage_collaboration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
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
    workflow = WorkflowRepository(identity)
    permissions = PermissionService(identity, SessionService(identity))
    creator_user = _role_user(identity, "screenwriter", "项目创建负责人")
    member_user = _role_user(identity, "selection_operator", "普通项目成员")
    owner_user = _role_user(identity, "project_owner", "平台项目负责人")
    admin_user = _role_user(identity, "system_admin", "系统管理员")
    candidate_user = _role_user(identity, "screenwriter", "待分配成员")
    users = (creator_user, member_user, owner_user, admin_user, candidate_user)
    principals = {
        "creator": permissions.principal_for_user(
            creator_user["id"], "creator-session"
        ),
        "member": permissions.principal_for_user(
            member_user["id"], "member-session"
        ),
        "owner": permissions.principal_for_user(owner_user["id"], "owner-session"),
        "admin": permissions.principal_for_user(admin_user["id"], "admin-session"),
    }
    assert all(principals.values())
    creator = principals["creator"]
    member = principals["member"]
    owner = principals["owner"]
    admin = principals["admin"]
    assert creator is not None and member is not None
    assert owner is not None and admin is not None

    runtime = tmp_path / ".runtime"
    creative = CreativeRepository(runtime / "creative/creative.db")
    with creative._connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) "
            "VALUES ('creative_projects', ?)",
            (1_200_000_000 + uuid.uuid4().int % 100_000_000,),
        )
    topic = creative.upsert_topics(
        [
            {
                "platform": "dy",
                "job_id": f"governance-{uuid.uuid4().hex}",
                "source_path": "governance.json",
                "captured_at": "2026-08-21",
                "title": "治理矩阵项目",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "adopt",
                "evidence": [],
            }
        ]
    )[0]
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(
        topic["id"], {"title": topic["title"]}, {}, []
    )
    creative.lock_breakdown(breakdown["id"])

    created_projects: list[str] = []

    def create_project(name: str) -> dict:
        project = creative.create_project(
            topic["id"],
            breakdown["id"],
            name,
            1,
            creation_request_id=uuid.uuid4().hex,
            created_by=creator.user_id,
        )
        created_projects.append(str(project["id"]))
        return project

    creator_project = create_project("创建负责人五阶段")
    creator_project_id = str(creator_project["id"])
    member_project = create_project("普通成员五阶段")
    member_project_id = str(member_project["id"])
    workflow.add_project_member(
        creator_project_id,
        creator.user_id,
        "project_creator",
        creator,
        allow_creator_bootstrap=True,
    )
    workflow.add_project_member(
        creator_project_id, creator.user_id, "screenwriter", admin
    )
    workflow.add_project_member(
        creator_project_id, member.user_id, "creative_planner", admin
    )
    workflow.add_project_member(
        member_project_id, member.user_id, "screenwriter", admin
    )

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_workflow_router(workflow))
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=runtime,
            workflow_repository=workflow,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    def cleanup() -> None:
        for project_id in created_projects:
            workflow.delete_project_data(project_id)
        for user in users:
            if identity.get_user(user["id"]) is not None:
                identity.soft_delete_user(user["id"])
        database.dispose()

    request.addfinalizer(cleanup)

    _set_actor(app, creator)
    _exercise_five_stage_creation_edit_and_lock(
        client, workflow, creative, creator_project_id
    )
    _set_actor(app, member)
    _exercise_five_stage_creation_edit_and_lock(
        client, workflow, creative, member_project_id
    )

    assert permissions.can_create_project_content(
        creator, creator_project_id, active_member=True
    )
    assert permissions.can_create_project_content(
        member, creator_project_id, active_member=True
    )
    for principal in (creator, member):
        assert not permissions.can_govern_project(principal, creator_project_id)
        assert not permissions.can_delete_project(principal, creator_project_id)
        _set_actor(app, principal)
        assert client.patch(
            f"/api/creative/projects/{creator_project_id}",
            json={"status": "archived"},
        ).status_code == 403
        assert client.delete(
            f"/api/creative/projects/{creator_project_id}"
        ).status_code == 403
        assert client.get(
            f"/api/workflow/projects/{creator_project_id}/members"
        ).status_code == 403
        assert client.post(
            f"/api/workflow/projects/{creator_project_id}/members",
            json={
                "user_id": candidate_user["id"],
                "project_role": "screenwriter",
                "status": "active",
            },
        ).status_code == 403

    assignment_task = workflow.create_task(
        f"governance-assignment-{uuid.uuid4().hex}",
        "outline",
        "screenwriter",
        project_id=creator_project_id,
        actor=SYSTEM_ACTOR,
    )
    for principal in (creator, member):
        _set_actor(app, principal)
        assert client.post(
            f"/api/workflow/tasks/{assignment_task['id']}/assign",
            json={"assignee_user_id": candidate_user["id"]},
        ).status_code == 403
        assert client.post(
            f"/api/workflow/tasks/{assignment_task['id']}/reassign",
            json={"assignee_user_id": candidate_user["id"]},
        ).status_code == 403

    assert permissions.can_govern_project(owner, creator_project_id)
    assert permissions.can_delete_project(owner, creator_project_id)
    assert permissions.can_govern_project(admin, creator_project_id)
    assert permissions.can_delete_project(admin, creator_project_id)
    _set_actor(app, owner)
    assert client.get(
        f"/api/workflow/projects/{creator_project_id}/members"
    ).status_code == 200
    owner_added = client.post(
        f"/api/workflow/projects/{creator_project_id}/members",
        json={
            "user_id": candidate_user["id"],
            "project_role": "screenwriter",
            "status": "active",
        },
    )
    assert owner_added.status_code == 201, owner_added.text
    assigned = client.post(
        f"/api/workflow/tasks/{assignment_task['id']}/assign",
        json={"assignee_user_id": candidate_user["id"]},
    )
    assert assigned.status_code == 200, assigned.text
    assert client.patch(
        f"/api/creative/projects/{creator_project_id}",
        json={"status": "archived"},
    ).status_code == 200
    _set_actor(app, admin)
    assert client.get(
        f"/api/workflow/projects/{creator_project_id}/members"
    ).status_code == 200
    admin_added = client.post(
        f"/api/workflow/projects/{creator_project_id}/members",
        json={
            "user_id": candidate_user["id"],
            "project_role": "creative_planner",
            "status": "active",
        },
    )
    assert admin_added.status_code == 201, admin_added.text
    reassigned = client.post(
        f"/api/workflow/tasks/{assignment_task['id']}/reassign",
        json={"assignee_user_id": creator.user_id},
    )
    assert reassigned.status_code == 200, reassigned.text
    assert client.patch(
        f"/api/creative/projects/{creator_project_id}",
        json={"status": "archived"},
    ).status_code == 200

    owner_delete_project = create_project("平台负责人删除")
    admin_delete_project = create_project("系统管理员删除")
    _set_actor(app, owner)
    assert client.delete(
        f"/api/creative/projects/{owner_delete_project['id']}"
    ).status_code == 200
    _set_actor(app, admin)
    assert client.delete(
        f"/api/creative/projects/{admin_delete_project['id']}"
    ).status_code == 200

    original_created_by = creative.get_project(
        int(creator_project_id)
    )["created_by"]
    creator_membership = next(
        item
        for item in workflow.list_project_members(creator_project_id)
        if item["user_id"] == creator.user_id
        and item["project_role"] == "project_creator"
    )
    _set_actor(app, owner)
    deactivated = client.delete(
        f"/api/workflow/projects/{creator_project_id}/members/"
        f"{creator_membership['id']}"
    )
    assert deactivated.status_code == 204, deactivated.text
    members = workflow.list_project_members(creator_project_id)
    assert all(
        item["status"] == "inactive"
        for item in members
        if item["user_id"] == creator.user_id
    )
    assert any(
        item["user_id"] == member.user_id and item["status"] == "active"
        for item in members
    )
    assert creative.get_project(int(creator_project_id))["created_by"] == original_created_by
    assert not permissions.can_create_project_content(
        creator, creator_project_id, active_member=False
    )
    _set_actor(app, creator)
    assert client.patch(
        f"/api/creative/projects/{creator_project_id}",
        json={"name": "停用后不可创作"},
    ).status_code == 403
    assert client.patch(
        f"/api/creative/projects/{creator_project_id}",
        json={"status": "archived"},
    ).status_code == 403
    assert client.delete(
        f"/api/creative/projects/{creator_project_id}"
    ).status_code == 403
    assert creative.get_project(int(creator_project_id))["created_by"] == original_created_by
