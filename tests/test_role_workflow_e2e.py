# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from audit.service import AuditService
from creative.api import build_creative_router
from creative.delivery import DeliveryService, build_delivery_router
from creative.lifecycle_service import TopicLifecycleService
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from web.navigation_service import NavigationService
from web.page_router import build_page_router
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


ROOT = Path(__file__).resolve().parents[1]


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


def _claim_start(client: TestClient, task_id: str) -> None:
    assert client.post(f"/api/workflow/tasks/{task_id}/claim").status_code == 200
    assert client.post(f"/api/workflow/tasks/{task_id}/start").status_code == 200


def _submit(
    client: TestClient, task_id: str, resource_type: str, resource_id: str
) -> dict:
    response = client.post(
        "/api/creative/workflow/submit",
        json={
            "workflow_task_id": task_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _complete(
    client: TestClient, task_id: str, resource_type: str, resource_id: str
) -> dict:
    if resource_type == "brief":
        url = f"/api/creative/blueprints/{resource_id}/lock-brief"
    elif resource_type == "characters":
        project_id, batch_id = resource_id.split(":", 1)
        url = f"/api/creative/projects/{project_id}/characters/{batch_id}/lock"
    elif resource_type == "outline":
        project_id, batch_id = resource_id.split(":", 1)
        url = f"/api/creative/projects/{project_id}/outlines/{batch_id}/lock"
    else:
        url = f"/api/creative/episodes/{resource_id}/lock"
    response = client.post(url, json={"workflow_task_id": task_id})
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, submission_id: str) -> dict:
    response = client.post(
        f"/api/workflow/submissions/{submission_id}/approve"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_five_role_workflow_isolated_audited_and_persistent(tmp_path: Path):
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    audit = AuditService(identity)
    workflow = WorkflowRepository(identity)
    creative = CreativeRepository(runtime / "creative/creative.db")
    adapter = CreativeWorkflowAdapter(creative, workflow)
    transitions = TransitionService(workflow, creative_adapter=adapter)

    users = {}
    for role_code in (
        "selection_operator",
        "creative_planner",
        "screenwriter",
        "project_owner",
        "system_admin",
    ):
        role = identity.get_role_by_code(role_code)
        assert role is not None
        user = identity.create_user(role_code, role_code, "hash")
        identity.replace_user_roles(user["id"], [role["id"]])
        users[role_code] = user
    principals = {
        role: permissions.principal_for_user(user["id"], role)
        for role, user in users.items()
    }
    assert all(principals.values())
    admin = principals["system_admin"]
    assert admin is not None

    topic = creative.upsert_topics(
        [
            {
                "platform": "dy",
                "job_id": "e2e-rank",
                "source_path": "e2e.json",
                "captured_at": "2026-07-22",
                "title": "Five role drama",
                "description": "",
                "category": "urban",
                "rank_type": "hot",
                "score": 95,
                "confidence": "high",
                "score_details": {},
                "recommendation": "adopt",
                "evidence": [],
            }
        ]
    )[0]
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"hook": "strong"}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    project = creative.create_project(topic["id"], breakdown["id"], "Five Roles", 2)
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_breakdowns SET status = 'draft', locked_at = NULL WHERE id = ?",
            (breakdown["id"],),
        )
    project_id = str(project["id"])
    for role_code, user in users.items():
        project_role = "project_owner" if role_code == "project_owner" else role_code
        workflow.add_project_member(
            project_id, user["id"], project_role, admin
        )

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = audit
    app.include_router(build_workflow_router(workflow, transitions))
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    app.include_router(
        build_delivery_router(
            DeliveryService(
                root=tmp_path,
                creative_repository=creative,
                workflow_repository=workflow,
                audit_service=audit,
            )
        )
    )
    app.include_router(
        build_page_router(
            Jinja2Templates(directory=str(ROOT / "templates")), NavigationService()
        )
    )
    clients = {
        role: _client(app, sessions, user["id"])
        for role, user in users.items()
    }
    bootstrapped = clients["project_owner"].post(
        f"/api/creative/topics/{topic['id']}/workflow",
        json={"project_id": project_id},
    )
    assert bootstrapped.status_code == 200, bootstrapped.text
    topic_decision_task = bootstrapped.json()["task"]

    def approve_generic(task: dict, resource_type: str) -> dict:
        _claim_start(clients["selection_operator"], task["id"])
        draft = clients["selection_operator"].post(
            f"/api/workflow/tasks/{task['id']}/submissions",
            json={
                "resource_type": resource_type,
                "resource_id": str(topic["id"]),
                "content": {"topic_id": topic["id"]},
            },
        )
        assert draft.status_code == 201, draft.text
        submitted = clients["selection_operator"].post(
            f"/api/workflow/submissions/{draft.json()['id']}/submit"
        )
        assert submitted.status_code == 200, submitted.text
        return _approve(clients["project_owner"], submitted.json()["id"])["next_task"]

    source_package_task = approve_generic(topic_decision_task, "topic_decision")
    first_task = approve_generic(source_package_task, "source_package")
    assert first_task["task_type"] == "breakdown"

    assert clients["selection_operator"].get("/selection/breakdowns").status_code == 404
    assert "创建工作流" not in clients["selection_operator"].get(
        "/selection/topics"
    ).text
    assert clients["selection_operator"].get("/writing/projects").status_code == 403
    assert clients["creative_planner"].get("/planning/projects").status_code == 200
    assert clients["creative_planner"].get("/admin/deliveries").status_code == 403
    assert clients["screenwriter"].get("/writing/projects").status_code == 200
    assert clients["project_owner"].get("/review/approvals").status_code == 404
    assert "managed-project-list" in clients["project_owner"].get(
        "/review/projects"
    ).text
    assert clients["system_admin"].get("/admin/deliveries").status_code == 200
    assert clients["screenwriter"].post(
        f"/api/workflow/tasks/{first_task['id']}/claim"
    ).status_code == 403

    _claim_start(clients["selection_operator"], first_task["id"])
    breakdown_submission = _submit(
        clients["selection_operator"],
        first_task["id"],
        "breakdown",
        str(breakdown["id"]),
    )
    frozen = clients["selection_operator"].patch(
        f"/api/creative/breakdowns/{breakdown['id']}",
        json={"content": {"hook": "changed"}},
    )
    assert frozen.status_code == 409
    assert clients["creative_planner"].post(
        f"/api/workflow/submissions/{breakdown_submission['id']}/approve"
    ).status_code == 404
    brief_task = _approve(
        clients["project_owner"], breakdown_submission["id"]
    )["next_task"]
    assert (brief_task["task_type"], brief_task["role_code"], brief_task["status"]) == (
        "brief",
        "creative_planner",
        "pool",
    )

    brief = creative.create_blueprints(
        project["id"], [{"content": {"premise": "approved"}, "risk_level": "low"}]
    )[0]
    _claim_start(clients["creative_planner"], brief_task["id"])
    brief_result = _complete(
        clients["creative_planner"], brief_task["id"], "brief", str(brief["id"])
    )
    characters_task = brief_result["next_task"]
    assert characters_task["role_code"] == "creative_planner"

    _claim_start(clients["creative_planner"], characters_task["id"])
    planner_role = identity.get_role_by_code("creative_planner")
    assert planner_role is not None
    other_planner = identity.create_user("other-planner", "Other Planner", "hash")
    identity.replace_user_roles(other_planner["id"], [planner_role["id"]])
    workflow.add_project_member(
        project_id, other_planner["id"], "creative_planner", admin
    )
    other_planner_client = _client(app, sessions, other_planner["id"])
    unauthorized_characters = other_planner_client.post(
        f"/api/creative/projects/{project_id}/characters",
        json={
            "characters": [{"name": "Not mine"}],
            "relationships": [],
            "workflow_task_id": characters_task["id"],
        },
    )
    assert unauthorized_characters.status_code == 403

    created_characters = clients["creative_planner"].post(
        f"/api/creative/projects/{project_id}/characters",
        json={
            "characters": [{"name": "Lead"}],
            "relationships": [
                {"from": "Lead", "to": "Rival", "relation": "conflict"}
            ],
            "workflow_task_id": characters_task["id"],
        },
    )
    assert created_characters.status_code == 200, created_characters.text
    characters = created_characters.json()
    assert all(
        item["workflow_task_id"] == characters_task["id"]
        for item in characters["characters"] + characters["relationships"]
    )
    visible_project = clients["creative_planner"].get(
        f"/api/creative/projects/{project_id}"
    )
    assert visible_project.status_code == 200, visible_project.text
    assert any(
        item["batch_id"] == characters["batch_id"]
        for item in visible_project.json()["characters"]
    )
    character = characters["characters"][0]
    edited = clients["creative_planner"].patch(
        f"/api/creative/characters/{character['id']}",
        json={"content": {"name": "Lead", "identity": "Detective"}},
    )
    assert edited.status_code == 200, edited.text

    resolved_characters = clients["creative_planner"].post(
        f"/api/creative/projects/{project_id}/characters",
        json={"characters": [{"name": "Supporting"}], "relationships": []},
    )
    assert resolved_characters.status_code == 200, resolved_characters.text
    assert (
        resolved_characters.json()["characters"][0]["workflow_task_id"]
        == characters_task["id"]
    )

    character_resource_id = f"{project_id}:{characters['batch_id']}"
    character_result = _complete(
        clients["creative_planner"],
        characters_task["id"],
        "characters",
        character_resource_id,
    )
    outline_task = character_result["next_task"]
    assert outline_task["role_code"] == "screenwriter"
    no_character_task = clients["creative_planner"].post(
        f"/api/creative/projects/{project_id}/characters",
        json={"characters": [{"name": "Too late"}], "relationships": []},
    )
    assert no_character_task.status_code == 409

    outline = creative.create_outline_batch(
        project["id"],
        [
            {"episode": 1, "hook": "cliffhanger"},
            {"episode": 2, "hook": "reversal"},
        ],
        "series arc",
    )
    outline_resource_id = f"{project_id}:{outline['batch_id']}"
    _claim_start(clients["screenwriter"], outline_task["id"])
    outline_result = _complete(
        clients["screenwriter"],
        outline_task["id"],
        "outline",
        outline_resource_id,
    )
    beats_task = outline_result["next_task"]
    assert beats_task["role_code"] == "screenwriter"

    _claim_start(clients["screenwriter"], beats_task["id"])
    beats_response = clients["screenwriter"].post(
        f"/api/creative/projects/{project_id}/episodes/1/beats",
        json={
            "workflow_task_id": beats_task["id"],
            "content": {
            "beats": [
                {
                    "order": 1,
                    "scene": "office",
                    "characters": ["Lead"],
                    "goal": "win",
                    "obstacle": "Rival",
                    "emotion_change": "fear to resolve",
                    "turn": "new evidence",
                    "hook": "door opens",
                }
            ]
            },
        },
    )
    assert beats_response.status_code == 200, beats_response.text
    beats = beats_response.json()
    first_beats_result = _complete(
        clients["screenwriter"], beats_task["id"], "beats", str(beats["id"])
    )
    assert first_beats_result["next_task"] is None
    assert first_beats_result["task"]["status"] == "in_progress"

    beats_two_response = clients["screenwriter"].post(
        f"/api/creative/projects/{project_id}/episodes/2/beats",
        json={
            "workflow_task_id": beats_task["id"],
            "content": {
            "beats": [
                {
                    "order": 1,
                    "scene": "station",
                    "characters": ["Lead"],
                    "goal": "escape",
                    "obstacle": "Rival",
                    "emotion_change": "hope to fear",
                    "turn": "train stops",
                    "hook": "lights fail",
                }
            ]
            },
        },
    )
    assert beats_two_response.status_code == 200, beats_two_response.text
    beats_two = beats_two_response.json()
    second_beats_result = _complete(
        clients["screenwriter"], beats_task["id"], "beats", str(beats_two["id"])
    )
    delivery_task = second_beats_result["next_task"]
    assert (delivery_task["task_type"], delivery_task["role_code"]) == (
        "delivery",
        "system_admin",
    )

    assert TopicLifecycleService(creative, tmp_path).enrich_topic(topic["id"])[
        "lifecycle_status"
    ] == "beats_generated"

    with identity.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM approval_records WHERE decision = 'approved'"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_task_events WHERE event_type = 'approve'"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_task_events "
            "WHERE event_type = 'creative.lock'"
        ).fetchone()[0] == 5

    restarted_identity = IdentityRepository(runtime / "system/system.db")
    restarted_workflow = WorkflowRepository(restarted_identity)
    assert restarted_workflow.get_task(delivery_task["id"])["status"] == "pool"
