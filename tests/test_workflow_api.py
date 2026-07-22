# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.service import AuditService
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository


@pytest.fixture
def identity_repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


@pytest.fixture
def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
    return WorkflowRepository(identity_repository)


def _principal(
    identity_repository: IdentityRepository, username: str, role_code: str
) -> Principal:
    role = identity_repository.get_role_by_code(role_code)
    assert role is not None
    user = identity_repository.create_user(username, username.title(), "hash")
    identity_repository.replace_user_roles(user["id"], [role["id"]])
    service = PermissionService(identity_repository, SessionService(identity_repository))
    principal = service.principal_for_user(user["id"], f"session:{username}")
    assert principal is not None
    return principal


def _client(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
    principal: Principal,
) -> TestClient:
    app = FastAPI()
    app.state.identity_permission_service = PermissionService(
        identity_repository, SessionService(identity_repository)
    )
    app.state.audit_service = AuditService(identity_repository)
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_workflow_router(repository))
    return TestClient(app)


def test_task_api_lists_claimable_owned_and_managed_tasks_only(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    selector = _principal(identity_repository, "selector_api", "selection_operator")
    owner = _principal(identity_repository, "owner_api", "project_owner")
    repository.add_project_member("project-1", owner.user_id, "owner", owner)
    selection_pool = repository.create_task(
        "wf-selection",
        "breakdown.create",
        "selection_operator",
        topic_id=1,
        actor=SYSTEM_ACTOR,
    )
    repository.create_task(
        "wf-writing",
        "outline.create",
        "screenwriter",
        topic_id=1,
        actor=SYSTEM_ACTOR,
    )
    managed = repository.create_task(
        "wf-managed",
        "outline.review",
        "project_owner",
        project_id="project-1",
        actor=SYSTEM_ACTOR,
    )
    repository.create_task(
        "wf-other",
        "outline.review",
        "project_owner",
        project_id="project-2",
        actor=SYSTEM_ACTOR,
    )

    selector_tasks = _client(identity_repository, repository, selector).get(
        "/api/workflow/tasks"
    )
    owner_tasks = _client(identity_repository, repository, owner).get(
        "/api/workflow/tasks"
    )

    assert selector_tasks.status_code == 200
    assert {task["id"] for task in selector_tasks.json()} == {selection_pool["id"]}
    assert owner_tasks.status_code == 200
    assert {task["id"] for task in owner_tasks.json()} == {managed["id"]}


def test_claim_start_cancel_api_and_conflict_statuses(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    selector = _principal(identity_repository, "selector_mutation", "selection_operator")
    admin = _principal(identity_repository, "admin_mutation", "system_admin")
    task = repository.create_task(
        "wf-api",
        "breakdown.create",
        "selection_operator",
        topic_id=1,
        actor=SYSTEM_ACTOR,
    )
    selector_client = _client(identity_repository, repository, selector)

    claimed = selector_client.post(f"/api/workflow/tasks/{task['id']}/claim")
    duplicate = selector_client.post(f"/api/workflow/tasks/{task['id']}/claim")
    started = selector_client.post(f"/api/workflow/tasks/{task['id']}/start")
    cancelled = _client(identity_repository, repository, admin).post(
        f"/api/workflow/tasks/{task['id']}/cancel"
    )

    assert claimed.status_code == 200
    assert claimed.json()["status"] == "assigned"
    assert duplicate.status_code == 409
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    app_audit = selector_client.app.state.audit_service
    claim_events = app_audit.query(action="workflow.claim", target_id=task["id"])
    assert {event["result"] for event in claim_events} == {"success", "failure"}


def test_assign_and_reassign_api_require_manager_scope(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    owner = _principal(identity_repository, "owner_assign", "project_owner")
    first = _principal(identity_repository, "selector_first", "selection_operator")
    second = _principal(identity_repository, "selector_second", "selection_operator")
    repository.add_project_member("project-1", owner.user_id, "owner", owner)
    repository.add_project_member(
        "project-1", first.user_id, "selection_operator", owner
    )
    repository.add_project_member(
        "project-1", second.user_id, "selection_operator", owner
    )
    task = repository.create_task(
        "wf-assign",
        "breakdown.create",
        "selection_operator",
        project_id="project-1",
        actor=SYSTEM_ACTOR,
    )
    client = _client(identity_repository, repository, owner)

    assigned = client.post(
        f"/api/workflow/tasks/{task['id']}/assign",
        json={"assignee_user_id": first.user_id},
    )
    reassigned = client.post(
        f"/api/workflow/tasks/{task['id']}/reassign",
        json={"assignee_user_id": second.user_id},
    )

    assert assigned.status_code == 200
    assert assigned.json()["assignee_user_id"] == first.user_id
    assert reassigned.status_code == 200
    assert reassigned.json()["assignee_user_id"] == second.user_id


def test_project_member_api_adds_lists_and_protects_last_owner(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    admin = _principal(identity_repository, "admin_members", "system_admin")
    owner = _principal(identity_repository, "member_owner", "project_owner")
    client = _client(identity_repository, repository, admin)

    created = client.post(
        "/api/workflow/projects/project-1/members",
        headers={"X-Request-ID": "member-add-request"},
        json={
            "user_id": owner.user_id,
            "project_role": "project_owner",
            "status": "active",
        },
    )
    updated = client.post(
        "/api/workflow/projects/project-1/members",
        json={
            "user_id": owner.user_id,
            "project_role": "project_owner",
            "status": "active",
        },
    )
    additional_role = client.post(
        "/api/workflow/projects/project-1/members",
        json={
            "user_id": owner.user_id,
            "project_role": "manager",
            "status": "active",
        },
    )
    listed = client.get("/api/workflow/projects/project-1/members")
    removed = client.delete(
        f"/api/workflow/projects/project-1/members/{owner.user_id}",
        headers={"X-Request-ID": "member-remove-failure"},
    )
    replacement = client.post(
        "/api/workflow/projects/project-1/members",
        json={
            "user_id": admin.user_id,
            "project_role": "project_owner",
            "status": "active",
        },
    )
    removed_with_replacement = client.delete(
        f"/api/workflow/projects/project-1/members/{owner.user_id}"
    )

    assert created.status_code == 201
    assert updated.status_code == 201
    assert additional_role.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["user_id"] == owner.user_id
    assert removed.status_code == 409
    assert replacement.status_code == 201
    assert removed_with_replacement.status_code == 204
    assert {
        (member["user_id"], member["project_role"])
        for member in client.get(
            "/api/workflow/projects/project-1/members"
        ).json()
    } == {(admin.user_id, "project_owner")}
    events = repository.list_project_member_events("project-1")
    assert [event["event_type"] for event in events] == [
        "project_member.add",
        "project_member.update",
        "project_member.add",
        "project_member.add",
        "project_member.remove",
        "project_member.remove",
    ]
    removed_events = [
        event for event in events if event["event_type"] == "project_member.remove"
    ]
    assert {event["before"]["project_role"] for event in removed_events} == {
        "project_owner",
        "manager",
    }
    assert all(event["after"] is None for event in removed_events)
    assert all(event["actor_user_id"] == admin.user_id for event in events)
    audit = client.app.state.audit_service
    added_audit = audit.query(
        action="project.member.add", project_id="project-1", result="success"
    )
    removed_audit = audit.query(
        action="project.member.remove", project_id="project-1", result="failure"
    )
    assert any(event["request_id"] == "member-add-request" for event in added_audit)
    assert any(event["request_id"] == "member-remove-failure" for event in removed_audit)


def test_member_api_rejects_non_manager(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    selector = _principal(identity_repository, "selector_members", "selection_operator")
    client = _client(identity_repository, repository, selector)

    response = client.get("/api/workflow/projects/project-1/members")

    assert response.status_code == 403


def test_submission_create_success_and_failure_are_audited(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    selector = _principal(identity_repository, "selector_submission", "selection_operator")
    admin = _principal(identity_repository, "admin_submission", "system_admin")
    task = repository.create_task(
        "submission-audit", "topic_decision", "selection_operator", actor=SYSTEM_ACTOR
    )
    repository.claim_task(task["id"], selector)
    repository.start_task(task["id"], selector)
    selector_client = _client(identity_repository, repository, selector)
    created = selector_client.post(
        f"/api/workflow/tasks/{task['id']}/submissions",
        headers={"X-Request-ID": "submission-create-request"},
        json={
            "resource_type": "topic_decision",
            "resource_id": "topic-1",
            "content": {"decision": "adopt"},
        },
    )
    denied = _client(identity_repository, repository, admin).post(
        f"/api/workflow/tasks/{task['id']}/submissions",
        json={
            "resource_type": "topic_decision",
            "resource_id": "topic-2",
            "content": {"decision": "adopt"},
        },
    )

    assert created.status_code == 201
    assert denied.status_code == 403
    audit = selector_client.app.state.audit_service
    successes = audit.query(action="workflow.submission.create", result="success")
    failures = audit.query(action="workflow.submission.create", result="failure")
    assert successes[0]["request_id"] == "submission-create-request"
    assert failures
