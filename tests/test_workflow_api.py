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

from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
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
        "wf-selection", "breakdown.create", "selection_operator", topic_id=1
    )
    repository.create_task("wf-writing", "outline.create", "screenwriter", topic_id=1)
    managed = repository.create_task(
        "wf-managed", "outline.review", "project_owner", project_id="project-1"
    )
    repository.create_task(
        "wf-other", "outline.review", "project_owner", project_id="project-2"
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
        "wf-api", "breakdown.create", "selection_operator", topic_id=1
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
        "wf-assign", "breakdown.create", "selection_operator", project_id="project-1"
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
        json={
            "user_id": owner.user_id,
            "project_role": "project_owner",
            "status": "active",
        },
    )
    listed = client.get("/api/workflow/projects/project-1/members")
    removed = client.delete(
        f"/api/workflow/projects/project-1/members/{created.json()['id']}"
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["user_id"] == owner.user_id
    assert removed.status_code == 409


def test_member_api_rejects_non_manager(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
):
    selector = _principal(identity_repository, "selector_members", "selection_operator")
    client = _client(identity_repository, repository, selector)

    response = client.get("/api/workflow/projects/project-1/members")

    assert response.status_code == 403
