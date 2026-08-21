# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from creative.api import build_creative_router
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository
from workflow import SYSTEM_ACTOR


def _client(app: FastAPI, sessions: SessionService, user_id: str) -> TestClient:
    session_app = FastAPI()

    @session_app.get("/session")
    def session(request: Request):
        return sessions.create_session(user_id, request)

    token, _ = TestClient(session_app).get("/session").json()
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, token)
    return client


def test_global_project_owner_can_access_projects_without_membership(tmp_path: Path):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    assert owner_role is not None
    owner = identity.create_user("global-owner", "Global Owner", "hash")
    identity.replace_user_roles(owner["id"], [owner_role["id"]])
    permissions = PermissionService(identity, SessionService(identity, secure_cookie=False))
    principal = permissions.principal_for_user(owner["id"], "global-owner-session")
    assert principal is not None

    assert permissions.can_access_assigned_project(principal, "unassigned-project")
    assert permissions.can_access_managed_project(principal, "unassigned-project")


def test_global_project_owner_locks_breakdown_without_approval(tmp_path: Path):
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    assert owner_role is not None
    owner = identity.create_user("breakdown-owner", "Breakdown Owner", "hash")
    identity.replace_user_roles(owner["id"], [owner_role["id"]])
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = creative.upsert_topics([{
        "platform": "hg", "job_id": "owner-lock", "source_path": "rank.json",
        "captured_at": "2026-07-23", "title": "负责人直接锁定",
        "score": 90, "confidence": "high", "score_details": {},
        "recommendation": "adopt", "evidence": [],
    }])[0]
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    client = _client(app, sessions, owner["id"])

    response = client.post(f"/api/creative/breakdowns/{breakdown['id']}/lock")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "locked"


def test_project_author_owns_drafts_and_managed_reader_sees_approved_only(
    tmp_path: Path,
):
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    users = {}
    for username, role_code in (
        ("author", "creative_planner"),
        ("other", "creative_planner"),
        ("writer", "screenwriter"),
        ("owner", "project_owner"),
        ("selector", "selection_operator"),
        ("admin", "system_admin"),
    ):
        role = identity.get_role_by_code(role_code)
        assert role is not None
        user = identity.create_user(username, username, "hash")
        identity.replace_user_roles(user["id"], [role["id"]])
        users[username] = user
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = creative.upsert_topics([{
        "platform": "dy", "job_id": "project-visible", "source_path": "rank.json",
        "captured_at": "2026-07-22", "title": "Visible project", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "adopt",
        "evidence": [],
    }])[0]
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"hook": "approved"}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_breakdowns SET status = 'approved' WHERE id = ?",
            (breakdown["id"],),
        )
    workflow = WorkflowRepository(identity)
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    clients = {
        name: _client(app, sessions, user["id"])
        for name, user in users.items()
    }
    pending = next(item for item in creative.upsert_topics([{
        "platform": "dy", "job_id": "pending-candidate", "source_path": "pending.json",
        "captured_at": "2026-07-23", "title": "Pending candidate", "score": 88,
        "confidence": "high", "score_details": {"secret": 1},
        "recommendation": "candidate", "evidence": [{"private": "evidence"}],
    }]) if item["title"] == "Pending candidate")
    direct_adopt = clients["selector"].patch(
        f"/api/creative/topics/{pending['id']}", json={"status": "adopted"}
    )
    assert direct_adopt.status_code == 200, direct_adopt.text
    assert creative.get_topic(pending["id"])["status"] == "adopted"
    candidates = clients["owner"].get("/api/creative/topics/workflow-candidates")
    assert candidates.status_code == 200
    assert pending["id"] not in {item["id"] for item in candidates.json()["items"]}

    author_principal = permissions.principal_for_user(users["author"]["id"], "author")
    assert author_principal is not None
    brief_task = workflow.create_task(
        "visible-project-brief",
        "brief",
        "creative_planner",
        topic_id=topic["id"],
        actor=SYSTEM_ACTOR,
    )
    workflow.claim_task(brief_task["id"], author_principal)

    project_payload = {
        "topic_id": topic["id"], "breakdown_id": breakdown["id"],
        "name": "Owned draft", "target_episode_count": 2,
        "workflow_task_id": brief_task["id"],
    }
    created = clients["author"].post(
        "/api/creative/projects", json=project_payload
    )
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["created_by"] == users["author"]["id"]
    owner_principal = permissions.principal_for_user(users["admin"]["id"], "admin")
    assert owner_principal is not None
    for name in ("owner", "author", "other", "writer"):
        workflow.add_project_member(
            str(project["id"]), users[name]["id"],
            "project_owner" if name == "owner" else name,
            owner_principal,
        )
    assert clients["writer"].get(
        "/api/creative/topics", params={"status": "adopted"}
    ).status_code == 200
    assert clients["author"].get(
        f"/api/creative/topics/{topic['id']}/breakdowns", params={"status": "locked"}
    ).status_code == 200
    transcript = clients["writer"].get(
        f"/api/creative/topics/{topic['id']}/transcripts"
    )
    assert transcript.status_code == 403
    approved_draft = creative.create_blueprints(
        project["id"], [{"content": {"name": "approved"}}]
    )[0]
    other_task = workflow.create_task(
        "other-private-draft",
        "brief",
        "creative_planner",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(other_task["id"], users["other"]["id"], owner_principal)
    creative.create_blueprints(
        project["id"],
        [{"content": {"name": "private draft"}}],
        workflow_task_id=other_task["id"],
    )
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_blueprints SET status = 'approved' WHERE id = ?",
            (approved_draft["id"],),
        )
    assigned_task = workflow.create_task(
        "assigned-draft",
        "brief",
        "screenwriter",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(assigned_task["id"], users["writer"]["id"], owner_principal)
    creative.create_blueprints(
        project["id"],
        [{"content": {"name": "assigned draft"}}],
        workflow_task_id=assigned_task["id"],
    )
    author_task = workflow.create_task(
        "author-draft",
        "brief",
        "creative_planner",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(author_task["id"], users["author"]["id"], owner_principal)
    creative.create_blueprints(
        project["id"],
        [{"content": {"name": "author draft"}}],
        workflow_task_id=author_task["id"],
    )

    member_update = clients["other"].patch(
        f"/api/creative/projects/{project['id']}", json={"name": "member edit"}
    )
    assert member_update.status_code == 200
    assert member_update.json()["name"] == "member edit"
    owner_view = clients["owner"].get(f"/api/creative/projects/{project['id']}")
    writer_view = clients["writer"].get(f"/api/creative/projects/{project['id']}")
    author_view = clients["author"].get(f"/api/creative/projects/{project['id']}")
    assert owner_view.status_code == 200
    owner_preview = clients["owner"].get(
        f"/api/creative/projects/{project['id']}/generated-content",
        params={"stage": "brief"},
    )
    writer_preview = clients["writer"].get(
        f"/api/creative/projects/{project['id']}/generated-content",
        params={"stage": "brief"},
    )
    assert owner_preview.status_code == 200
    assert owner_preview.json()["snapshot"]["content"]["name"] == "author draft"
    assert writer_preview.status_code == 403
    assert [item["content"]["name"] for item in writer_view.json()["blueprints"]] == [
        "approved"
    ]
    assert owner_view.json()["blueprints"] == []
    assert [item["content"]["name"] for item in author_view.json()["blueprints"]] == [
        "approved", "author draft"
    ]
    member_retry = clients["author"].post(
        "/api/creative/projects", json=project_payload
    )
    assert member_retry.status_code == 200, member_retry.text
    assert member_retry.json()["id"] == project["id"]

    project_owner_principal = permissions.principal_for_user(
        users["owner"]["id"], "owner-remove"
    )
    assert project_owner_principal is not None
    workflow.remove_project_member(
        str(project["id"]), users["author"]["id"], project_owner_principal
    )
    assert all(
        member["user_id"] != users["author"]["id"]
        for member in workflow.list_project_members(str(project["id"]))
    )
    creative.create_blueprints(
        project["id"],
        [{"content": {"name": "v11-sensitive-blueprint"}}],
        workflow_task_id=author_task["id"],
    )
    assert clients["author"].get(
        f"/api/creative/projects/{project['id']}"
    ).status_code == 403

    retried = clients["author"].post(
        "/api/creative/projects", json=project_payload
    )

    assert retried.status_code == 403, retried.text
    assert "v11-sensitive-blueprint" not in retried.text
    assert all(
        member["user_id"] != users["author"]["id"]
        for member in workflow.list_project_members(str(project["id"]))
    )

    listed = clients["author"].get("/api/creative/projects")

    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert all(
        member["user_id"] != users["author"]["id"]
        for member in workflow.list_project_members(str(project["id"]))
    )
