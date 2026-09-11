# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

import WebGUI
from identity.password_service import PasswordService
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository
from workflow.transition_service import TransitionService


REQUIRED_ROUTES = {
    ("/api/workflow/tasks", "GET"),
    ("/api/workflow/tasks/{task_id}", "GET"),
    ("/api/workflow/tasks/{task_id}/claim", "POST"),
    ("/api/workflow/tasks/{task_id}/assign", "POST"),
    ("/api/workflow/tasks/{task_id}/reassign", "POST"),
    ("/api/workflow/tasks/{task_id}/start", "POST"),
    ("/api/workflow/tasks/{task_id}/cancel", "POST"),
    ("/api/workflow/projects/{project_id}/members", "GET"),
    ("/api/workflow/projects/{project_id}/members", "POST"),
    ("/api/workflow/projects/{project_id}/members/{user_id}", "DELETE"),
}


def test_production_mounts_workflow_with_shared_identity_services() -> None:
    routes = {
        (route.path, method)
        for route in WebGUI.app.routes
        for method in (getattr(route, "methods", None) or ())
    }

    assert REQUIRED_ROUTES <= routes
    assert isinstance(WebGUI.app.state.workflow_repository, WorkflowRepository)
    assert isinstance(WebGUI.app.state.workflow_transition_service, TransitionService)
    assert (
        WebGUI.app.state.workflow_repository.identity_repository
        is WebGUI.app.state.identity_repository
    )
    assert (
        WebGUI.app.state.workflow_transition_service.repository
        is WebGUI.app.state.workflow_repository
    )


def test_production_workflow_uses_real_auth_csrf_and_role_filtered_listing() -> None:
    repository = WebGUI.app.state.identity_repository
    workflow_repository = WebGUI.app.state.workflow_repository
    suffix = uuid.uuid4().hex
    username = f"workflow_prod_{suffix}"
    password = "Workflow-Prod-123!"
    role = repository.get_role_by_code("selection_operator")
    assert role is not None
    user = repository.create_user(
        username, "Workflow Production Test", PasswordService().hash(password)
    )
    repository.replace_user_roles(user["id"], [role["id"]])
    selection_workflow = f"production-selection-{suffix}"
    writing_workflow = f"production-writing-{suffix}"

    try:
        visible = workflow_repository.create_task(
            selection_workflow,
            "breakdown",
            "selection_operator",
            actor=SYSTEM_ACTOR,
        )
        workflow_repository.create_task(
            writing_workflow,
            "outline",
            "screenwriter",
            actor=SYSTEM_ACTOR,
        )
        anonymous = TestClient(WebGUI.app)
        assert anonymous.get("/api/workflow/tasks").status_code == 401
        assert (
            anonymous.post(f"/api/workflow/tasks/{visible['id']}/claim").status_code
            == 401
        )

        client = TestClient(WebGUI.app)
        login = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert login.status_code == 200
        bad_csrf = client.post(
            f"/api/workflow/tasks/{visible['id']}/claim",
            headers={"X-CSRF-Token": "invalid"},
        )
        assert bad_csrf.status_code == 403
        csrf_token = login.json()["csrf_token"]
        listed = client.get("/api/workflow/tasks")
        listed_ids = {task["id"] for task in listed.json()}
        assert visible["id"] in listed_ids
        assert all(
            task["role_code"] == "selection_operator"
            for task in listed.json()
            if task["workflow_id"] in {selection_workflow, writing_workflow}
        )
        claimed = client.post(
            f"/api/workflow/tasks/{visible['id']}/claim",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert claimed.status_code == 200
        assert client.post(
            f"/api/workflow/tasks/{visible['id']}/start",
            headers={"X-CSRF-Token": csrf_token},
        ).status_code == 200
    finally:
        with repository.connect() as connection:
            connection.execute(
                text(
                    "DELETE FROM workflow_instances "
                    "WHERE id IN (:selection_workflow, :writing_workflow)"
                ),
                {
                    "selection_workflow": selection_workflow,
                    "writing_workflow": writing_workflow,
                },
            )
            connection.execute(
                text("DELETE FROM sessions WHERE user_id = :user_id"),
                {"user_id": user["id"]},
            )
            connection.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": user["id"]},
            )
