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
    workflow = WorkflowRepository(identity)
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    clients = {
        name: _client(app, sessions, user["id"])
        for name, user in users.items()
    }

    created = clients["author"].post("/api/creative/projects", json={
        "topic_id": topic["id"], "breakdown_id": breakdown["id"],
        "name": "Owned draft", "target_episode_count": 2,
    })
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["created_by"] == users["author"]["id"]
    owner_principal = permissions.principal_for_user(users["owner"]["id"], "owner")
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
    drafts = creative.create_blueprints(project["id"], [
        {"content": {"name": "approved"}},
        {"content": {"name": "private draft"}},
    ])
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_blueprints SET status = 'approved' WHERE id = ?",
            (drafts[0]["id"],),
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

    assert clients["other"].patch(
        f"/api/creative/projects/{project['id']}", json={"name": "stolen"}
    ).status_code == 403
    owner_view = clients["owner"].get(f"/api/creative/projects/{project['id']}")
    writer_view = clients["writer"].get(f"/api/creative/projects/{project['id']}")
    author_view = clients["author"].get(f"/api/creative/projects/{project['id']}")
    assert owner_view.status_code == 200
    assert [item["content"]["name"] for item in writer_view.json()["blueprints"]] == [
        "approved", "assigned draft"
    ]
    assert len(author_view.json()["blueprints"]) == 3
