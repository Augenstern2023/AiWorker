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
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from audit.service import AuditService
from creative.api import build_creative_router
from creative.delivery import DeliveryService, build_delivery_router
from creative.lifecycle_service import TopicLifecycleService
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


def _login_client(
    app: FastAPI,
    sessions: SessionService,
    user_id: str,
) -> TestClient:
    session_app = FastAPI()

    @session_app.get("/session")
    async def create_session(request: Request):
        return sessions.create_session(user_id, request)

    token, csrf = TestClient(session_app).get("/session").json()
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, token)
    client.headers["X-CSRF-Token"] = csrf
    return client


def _seed_environment(tmp_path: Path):
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
    for username, role_code in (
        ("administrator", "system_admin"),
        ("planner", "creative_planner"),
        ("writer", "screenwriter"),
    ):
        role = identity.get_role_by_code(role_code)
        assert role is not None
        user = identity.create_user(username, username.title(), "hash")
        identity.replace_user_roles(user["id"], [role["id"]])
        users[username] = user
    admin = permissions.principal_for_user(users["administrator"]["id"], "admin")
    planner = permissions.principal_for_user(users["planner"]["id"], "planner")
    assert admin is not None and planner is not None

    topic = creative.upsert_topics(
        [
            {
                "platform": "dy",
                "job_id": "delivery-rank",
                "source_path": "delivery.json",
                "captured_at": "2026-07-22",
                "title": "Delivery drama",
                "description": "",
                "category": "urban",
                "rank_type": "hot",
                "score": 90,
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
    project = creative.create_project(topic["id"], breakdown["id"], "Delivery", 1)
    brief = creative.create_blueprints(
        project["id"], [{"content": {"premise": "approved"}, "risk_level": "low"}]
    )[0]
    workflow.add_project_member(str(project["id"]), planner.user_id, "creative_planner", admin)
    task = workflow.create_task(
        "workflow-delivery",
        "brief",
        "creative_planner",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(task["id"], planner.user_id, admin)
    workflow.start_task(task["id"], planner)
    approved_result = transitions.approve(
        adapter.submit(task["id"], "brief", str(brief["id"]), planner)["id"],
        admin,
    )
    approved = approved_result["submission"]
    characters_task = approved_result["next_task"]
    characters = creative.create_character_batch(
        project["id"], [{"name": "Lead"}], []
    )
    workflow.assign_task(characters_task["id"], planner.user_id, admin)
    workflow.start_task(characters_task["id"], planner)
    approved_characters = transitions.approve(
        adapter.submit(
            characters_task["id"],
            "characters",
            f"{project['id']}:{characters['batch_id']}",
            planner,
        )["id"],
        admin,
    )["submission"]

    draft_task = workflow.create_task(
        "workflow-delivery-draft",
        "brief",
        "creative_planner",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(draft_task["id"], planner.user_id, admin)
    workflow.start_task(draft_task["id"], planner)
    draft_brief = creative.create_blueprint_version(brief["id"], draft_task["id"])
    draft = adapter.create_draft(
        draft_task["id"], "brief", str(draft_brief["id"]), planner
    )
    delivery_task = workflow.create_task(
        "workflow-delivery-terminal",
        "delivery",
        "system_admin",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = audit
    delivery_service = DeliveryService(
        root=tmp_path,
        creative_repository=creative,
        workflow_repository=workflow,
        audit_service=audit,
    )
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    app.include_router(build_delivery_router(delivery_service))
    clients = {
        name: _login_client(app, sessions, user["id"])
        for name, user in users.items()
    }
    return (
        runtime,
        identity,
        sessions,
        audit,
        workflow,
        project,
        approved,
        approved_characters,
        draft,
        delivery_task,
        delivery_service,
        clients,
        admin,
    )


def test_only_system_admin_can_create_list_and_download_approved_delivery(
    tmp_path: Path,
):
    (
        runtime,
        identity,
        _,
        audit,
        workflow,
        project,
        approved,
        approved_characters,
        _,
        delivery_task,
        _,
        clients,
        _,
    ) = _seed_environment(tmp_path)

    for name in ("planner", "writer"):
        assert clients[name].get("/api/admin/deliveries").status_code == 403
        assert clients[name].post(
            "/api/admin/deliveries",
            json={
                "project_id": project["id"],
                "stage": "brief",
                "submission_id": approved["id"],
                "format": "md",
            },
        ).status_code == 403
        assert clients[name].get(
            f"/api/creative/projects/{project['id']}/export",
            params={"stage": "brief", "format": "md", "submission_id": approved["id"]},
        ).status_code == 403

    created = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "md",
        },
    )

    assert created.status_code == 201, created.text
    delivery = created.json()["delivery"]
    assert delivery["project_id"] == str(project["id"])
    assert delivery["stage"] == "brief"
    assert delivery["submission_id"] == approved["id"]
    assert delivery["submission_version"] == approved["version"]
    assert delivery["workflow_task_id"] == delivery_task["id"]
    assert delivery["actor_user_id"]
    assert delivery["format"] == "md"
    assert Path(delivery["path"]).is_file()
    downloaded = clients["administrator"].get(
        f"/api/admin/deliveries/{delivery['id']}/download"
    )
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == delivery["fingerprint"]
    listed = clients["administrator"].get("/api/admin/deliveries").json()["deliveries"]
    assert [item["id"] for item in listed] == [delivery["id"]]
    event = audit.query(action="delivery.create", target_id=delivery["id"])[0]
    assert event["project_id"] == str(project["id"])
    assert event["after_summary"]["submission_version"] == approved["version"]
    assert event["after_summary"]["fingerprint"] == delivery["fingerprint"]
    assert str(runtime / "deliveries") in delivery["path"]
    assert workflow.get_task(delivery_task["id"])["status"] == "completed"
    with identity.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_task_events "
            "WHERE task_id = ? AND event_type = 'delivery.complete'",
            (delivery_task["id"],),
        ).fetchone()[0] == 1
    json_delivery = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "json",
        },
    )
    characters_delivery = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "characters",
            "submission_id": approved_characters["id"],
            "format": "json",
        },
    )
    assert json_delivery.status_code == 201
    assert characters_delivery.status_code == 201
    assert {
        delivery["id"],
        json_delivery.json()["delivery"]["id"],
        characters_delivery.json()["delivery"]["id"],
    } == {
        item["id"]
        for item in clients["administrator"].get(
            "/api/admin/deliveries"
        ).json()["deliveries"]
    }
    with identity.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_task_events "
            "WHERE task_id = ? AND event_type = 'delivery.complete'",
            (delivery_task["id"],),
        ).fetchone()[0] == 1
    assert len(audit.query(action="delivery.create")) == 3

    repeated = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "md",
        },
    )
    assert repeated.status_code == 201
    assert repeated.json()["delivery"]["id"] == delivery["id"]
    assert len(audit.query(action="delivery.create")) == 3
    with identity.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_task_events "
            "WHERE task_id = ? AND event_type = 'delivery.complete'",
            (delivery_task["id"],),
        ).fetchone()[0] == 1


def test_delivery_rejects_draft_mismatch_and_unsupported_scope(tmp_path: Path):
    (
        runtime,
        identity,
        _,
        _,
        _,
        project,
        approved,
        _,
        draft,
        _,
        _,
        clients,
        _,
    ) = _seed_environment(tmp_path)
    admin = clients["administrator"]

    assert admin.post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": draft["id"],
            "format": "json",
        },
    ).status_code == 409
    assert admin.post(
        "/api/admin/deliveries",
        json={
            "submission_id": approved["id"],
            "project_id": int(project["id"]) + 1,
            "stage": "brief",
            "format": "json",
        },
    ).status_code == 409
    assert admin.post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "zip",
        },
    ).status_code == 422
    with identity.transaction() as connection:
        connection.execute(
            "UPDATE workflow_submissions SET content_snapshot_json = ? WHERE id = ?",
            ('{"project_id":1,"content":{}}', approved["id"]),
        )
    assert admin.post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "json",
        },
    ).status_code == 409
    assert list((runtime / "deliveries").glob("*")) == []


def test_delivery_registry_and_download_persist_across_app_restart(tmp_path: Path):
    (
        runtime,
        identity,
        sessions,
        _,
        _,
        _,
        approved,
        _,
        _,
        _,
        _,
        clients,
        _,
    ) = _seed_environment(tmp_path)
    response = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": 1,
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "json",
        },
    )
    assert response.status_code == 201
    delivery_id = response.json()["delivery"]["id"]

    restarted = FastAPI()
    restarted.state.identity_permission_service = PermissionService(identity, sessions)
    restarted_audit = AuditService(identity)
    restarted.state.audit_service = restarted_audit
    restarted.include_router(
        build_delivery_router(
            DeliveryService(
                root=tmp_path,
                creative_repository=CreativeRepository(runtime / "creative/creative.db"),
                workflow_repository=WorkflowRepository(identity),
                audit_service=restarted_audit,
            )
        )
    )
    admin_id = identity.list_users()[0]["id"]
    client = _login_client(restarted, sessions, admin_id)

    assert client.get("/api/admin/deliveries").json()["deliveries"][0]["id"] == delivery_id
    assert client.get(
        f"/api/admin/deliveries/{delivery_id}/download"
    ).status_code == 200


def test_delivery_registry_reconciles_task_after_cross_database_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (
        _,
        _,
        _,
        _,
        workflow,
        project,
        approved,
        _,
        _,
        delivery_task,
        service,
        _,
        admin,
    ) = _seed_environment(tmp_path)
    original = service._complete_workflow_task
    attempts = 0

    def fail_once(record):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("system database unavailable")
        return original(record)

    monkeypatch.setattr(service, "_complete_workflow_task", fail_once)
    with pytest.raises(RuntimeError, match="system database unavailable"):
        service.create(
            project_id=project["id"],
            stage="brief",
            submission_id=approved["id"],
            format="json",
            actor=admin,
        )

    assert len(service.list()) == 1
    assert workflow.get_task(delivery_task["id"])["status"] == "completed"


def test_delivery_rejects_inactive_or_mismatched_terminal_task(tmp_path: Path):
    (
        _,
        _,
        _,
        _,
        workflow,
        project,
        approved,
        _,
        _,
        delivery_task,
        service,
        clients,
        admin,
    ) = _seed_environment(tmp_path)
    workflow.cancel_task(delivery_task["id"], admin)

    response = clients["administrator"].post(
        "/api/admin/deliveries",
        json={
            "project_id": project["id"],
            "stage": "brief",
            "submission_id": approved["id"],
            "format": "json",
            "workflow_task_id": delivery_task["id"],
        },
    )

    assert response.status_code == 409
    assert service.list() == []


def test_beats_lifecycle_requires_each_current_episode_delivery(tmp_path: Path):
    (
        _,
        identity,
        _,
        _,
        workflow,
        project,
        _,
        _,
        _,
        _,
        service,
        _,
        admin,
    ) = _seed_environment(tmp_path)
    creative = service.creative_repository
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET target_episode_count = 2 WHERE id = ?",
            (project["id"],),
        )
    outline = creative.create_outline_batch(
        project["id"],
        [{"episode": 1}, {"episode": 2}],
        "two episode arc",
    )
    creative.lock_outline_batch(project["id"], outline["batch_id"])
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET status = 'approved' "
            "WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
            (project["id"], outline["batch_id"]),
        )
    beats = [
        creative.create_episode_beats(
            project["id"], episode, {"beats": [{"order": 1, "scene": scene}]}
        )
        for episode, scene in ((1, "office"), (2, "street"))
    ]
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET status = 'approved' "
            "WHERE id IN (?, ?)",
            (beats[0]["id"], beats[1]["id"]),
        )
    adapter = CreativeWorkflowAdapter(creative, workflow)
    submission_ids = []
    now = time.time()
    with identity.transaction() as connection:
        for index, item in enumerate(beats, start=1):
            snapshot = adapter.snapshot("beats", str(item["id"]), reconcile=False)
            serialized = json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            submission_id = f"approved-beats-{index}"
            submission_ids.append(submission_id)
            connection.execute(
                """
                INSERT INTO workflow_submissions (
                    id, task_id, resource_type, resource_id, version, status,
                    content_snapshot_json, content_fingerprint, created_by,
                    submitted_by, submitted_at, reviewed_by, reviewed_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'beats', ?, 1, 'approved', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    f"source-beats-{index}",
                    str(item["id"]),
                    serialized,
                    hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                    admin.user_id,
                    admin.user_id,
                    now,
                    admin.user_id,
                    now,
                    now,
                    now,
                ),
            )
    lifecycle = TopicLifecycleService(creative, tmp_path)
    topic_id = creative.get_project(project["id"])["topic_id"]
    assert lifecycle.enrich_topic(topic_id)["lifecycle_status"] == "beats_generated"

    service.create(
        project_id=project["id"],
        stage="beats",
        submission_id=submission_ids[0],
        format="json",
        actor=admin,
    )
    assert lifecycle.enrich_topic(topic_id)["lifecycle_status"] == "beats_generated"

    service.create(
        project_id=project["id"],
        stage="beats",
        submission_id=submission_ids[1],
        format="json",
        actor=admin,
    )
    assert lifecycle.enrich_topic(topic_id)["lifecycle_status"] == "beats_exported"
    with creative._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM creative_stage_exports "
            "WHERE project_id = ? AND stage = 'beats'",
            (project["id"],),
        ).fetchone()[0] == 2
