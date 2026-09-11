# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.api import build_creative_router, creative_api_principal
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


@pytest.fixture
def environment(tmp_path, isolated_system_database):
    identity = IdentityRepository(isolated_system_database)
    identity.seed_defaults()
    role = identity.get_role_by_code("project_owner")
    user = identity.create_user("reconciliation-owner", "项目负责人", "hash")
    identity.replace_user_roles(user["id"], [role["id"]])
    permissions = PermissionService(identity, SessionService(identity))
    principal = permissions.principal_for_user(user["id"], "reconciliation")
    workflow = WorkflowRepository(identity)
    repository = CreativeRepository(tmp_path / "creative.db")
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=tmp_path / "runtime",
            repository=repository,
            workflow_repository=workflow,
        )
    )
    return SimpleNamespace(
        app=app,
        client=TestClient(app),
        identity=identity,
        permissions=permissions,
        repository=repository,
        workflow=workflow,
        principal=principal,
    )


def project(environment, name, task_id):
    created = environment.repository.create_original_project(
        seed_text="测试创意种子",
        name=name,
        target_episode_count=1,
        created_by=environment.principal.user_id,
        creation_request_id=name,
    )
    environment.repository.mark_project_bootstrap_status(created["id"], "ready")
    with environment.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET creation_workflow_task_id=?, "
            "workflow_task_id=? WHERE id=?",
            (task_id, task_id, created["id"]),
        )
    return created["id"]


def test_missing_creation_task_does_not_hide_projects_or_erase_evidence(
    environment, caplog
):
    orphan_id = project(environment, "缺失创建任务项目", "missing-creation-task")
    healthy_id = project(environment, "正常项目", None)

    with caplog.at_level(logging.WARNING, logger="aiworker.creative"):
        response = environment.client.get("/api/creative/projects")

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {
        orphan_id,
        healthy_id,
    }
    stored = environment.repository.get_project(orphan_id)
    assert stored["creation_workflow_task_id"] == "missing-creation-task"
    assert stored["workflow_task_id"] == "missing-creation-task"
    assert "missing-creation-task" in caplog.text
    assert f"project_id={orphan_id}" in caplog.text


def test_missing_creation_task_preserves_later_workflow_task(environment):
    project_id = project(environment, "已进入下一阶段", "missing-brief-task")
    current_task = environment.workflow.create_task(
        "current-stage-workflow",
        "script",
        "screenwriter",
        project_id=environment.repository.workflow_project_id(project_id),
        actor=SYSTEM_ACTOR,
    )
    environment.repository.link_project_workflow_task(project_id, current_task["id"])

    response = environment.client.get("/api/creative/projects")

    assert response.status_code == 200, response.text
    stored = environment.repository.get_project(project_id)
    assert stored["workflow_task_id"] == current_task["id"]
    assert stored["creation_workflow_task_id"] == "missing-brief-task"


def test_scoped_creation_task_project_id_does_not_break_project_list(environment):
    created = environment.repository.create_original_project(
        seed_text="测试创意种子",
        name="作用域项目标识",
        target_episode_count=1,
        created_by=environment.principal.user_id,
        creation_request_id="scoped-project-id",
    )
    environment.repository.mark_project_bootstrap_status(created["id"], "ready")
    scoped_project_id = environment.repository.workflow_project_id(created["id"])
    task = environment.workflow.create_task(
        "scoped-brief-workflow",
        "brief",
        "project_owner",
        project_id=scoped_project_id,
        actor=SYSTEM_ACTOR,
    )
    with environment.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET creation_workflow_task_id=?, "
            "workflow_task_id=? WHERE id=?",
            (task["id"], task["id"], created["id"]),
        )

    response = environment.client.get("/api/creative/projects")

    assert response.status_code == 200, response.text
    assert any(item["id"] == created["id"] for item in response.json()["items"])
    assert environment.workflow.get_task(task["id"])["project_id"] == scoped_project_id


def test_participating_filter_uses_scoped_project_membership(environment):
    scoped_project_id = environment.repository.workflow_project_id(1)
    task = environment.workflow.create_task(
        "scoped-participating-workflow",
        "brief",
        "project_owner",
        project_id=scoped_project_id,
        actor=SYSTEM_ACTOR,
    )
    scoped_local_id = project(environment, "作用域成员项目", task["id"])
    assert scoped_local_id == 1
    project(environment, "未参与项目", None)
    environment.workflow.add_project_member(
        scoped_project_id,
        environment.principal.user_id,
        "project_owner",
        environment.principal,
    )
    screenwriter_role = environment.identity.get_role_by_code("screenwriter")
    screenwriter = environment.identity.create_user(
        "scoped-screenwriter", "作用域编剧", "hash"
    )
    environment.identity.replace_user_roles(
        screenwriter["id"], [screenwriter_role["id"]]
    )
    environment.workflow.add_project_member(
        scoped_project_id,
        screenwriter["id"],
        "screenwriter",
        environment.principal,
    )
    screenwriter_principal = environment.permissions.principal_for_user(
        screenwriter["id"], "scoped-participating"
    )
    environment.app.dependency_overrides[current_principal] = (
        lambda: screenwriter_principal
    )
    environment.app.dependency_overrides[creative_api_principal] = (
        lambda: screenwriter_principal
    )

    response = environment.client.get(
        "/api/creative/projects", params={"participating_only": "true"}
    )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [scoped_local_id]


def test_workflow_database_errors_return_stable_503_with_diagnostic_context(
    environment, monkeypatch, caplog
):
    project_id = project(environment, "数据库异常项目", "unavailable-task")

    def unavailable(task_id):
        raise RuntimeError("workflow storage unavailable")

    monkeypatch.setattr(environment.workflow, "get_task", unavailable)
    with caplog.at_level(logging.ERROR, logger="aiworker.creative"):
        response = environment.client.get("/api/creative/projects")

    assert response.status_code == 503
    assert response.json()["detail"] == "项目工作流状态暂不可用，请稍后重试"
    assert "workflow storage unavailable" not in response.text
    assert f"project_id={project_id}" in caplog.text
    assert "unavailable-task" in caplog.text
    assert any(record.exc_info for record in caplog.records)
