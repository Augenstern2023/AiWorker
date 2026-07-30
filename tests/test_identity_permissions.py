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
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from identity.api import build_identity_router
from identity.permission_catalog import PERMISSIONS
from identity.dependencies import assigned_project_scope, require_permission
from identity.password_service import PasswordService
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService


HASH = PasswordService().hash("Writer-Strong-123!")


@pytest.fixture
def repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


@pytest.fixture
def session_service(repository: IdentityRepository) -> SessionService:
    return SessionService(repository)


@pytest.fixture
def permission_service(
    repository: IdentityRepository, session_service: SessionService
) -> PermissionService:
    return PermissionService(repository, session_service)


def test_role_permission_dependencies_are_transitive(
    repository: IdentityRepository, permission_service: PermissionService
):
    role = repository.create_role("custom_writer", "定制编剧", "")
    repository.replace_role_permissions(role["id"], [("beats.edit", "project")])
    user = repository.create_user("writer", "编剧", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])

    principal = permission_service.principal_for_user(user["id"], "session-1")

    assert principal is not None
    assert {
        "beats.edit",
        "beats.read",
        "outline.read.approved",
        "page.writing",
    } <= principal.permissions
    assert principal.data_scope == "project"


def test_permission_catalog_provides_chinese_labels():
    assert PERMISSIONS["beats.edit"] == "编辑单集节拍"
    assert PERMISSIONS["analytics.read"] == "查看数据分析"


def test_multiple_roles_union_permissions_and_choose_maximum_scope(
    repository: IdentityRepository, permission_service: PermissionService
):
    task_role = repository.create_role("task_reader", "任务读取", "")
    project_role = repository.create_role("project_editor", "项目编辑", "")
    repository.replace_role_permissions(task_role["id"], [("rank.read", "task")])
    repository.replace_role_permissions(
        project_role["id"], [("topic.edit", "managed_projects")]
    )
    user = repository.create_user("multi", "多角色", HASH)
    repository.replace_user_roles(user["id"], [task_role["id"], project_role["id"]])

    principal = permission_service.principal_for_user(user["id"], "session-1")

    assert principal is not None
    assert {"rank.read", "topic.edit"} <= principal.permissions
    assert principal.data_scope == "managed_projects"


def test_wildcard_always_has_all_scope(
    repository: IdentityRepository, permission_service: PermissionService
):
    role = repository.create_role("operator_admin", "运维管理员", "")
    repository.replace_role_permissions(role["id"], [("*", "task")])
    user = repository.create_user("operator", "运维", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])

    principal = permission_service.principal_for_user(user["id"], "session-1")

    assert principal is not None
    assert principal.permissions == frozenset({"*"})
    assert principal.data_scope == "all"
    assert principal.is_system_admin is True


def test_role_changes_take_effect_for_an_existing_session(
    repository: IdentityRepository, session_service: SessionService
):
    role = repository.create_role("live_role", "即时角色", "")
    repository.replace_role_permissions(role["id"], [("rank.read", "task")])
    user = repository.create_user("live", "即时用户", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])
    app = FastAPI()
    app.include_router(build_identity_router(repository, session_service))
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "live", "password": "Writer-Strong-123!"},
    )
    raw_token = client.cookies.get(session_service.cookie_name)
    assert login.status_code == 200
    assert raw_token is not None
    service = PermissionService(repository, session_service)
    assert "topic.read" not in service.principal_for_session(raw_token).permissions

    repository.replace_role_permissions(role["id"], [("topic.read", "project")])

    principal = service.principal_for_session(raw_token)
    assert principal is not None
    assert "topic.read" in principal.permissions
    assert "rank.read" not in principal.permissions
    assert principal.data_scope == "project"


def test_assigned_project_scope_queries_current_membership(
    repository: IdentityRepository, session_service: SessionService
):
    role = repository.create_role("project_reader", "项目读取", "")
    repository.replace_role_permissions(
        role["id"], [("project.read.assigned", "project")]
    )
    user = repository.create_user("member", "项目成员", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])
    app = FastAPI()
    service = PermissionService(repository, session_service)
    app.state.identity_permission_service = service
    app.include_router(build_identity_router(repository, session_service))

    @app.get("/projects/{project_id}")
    async def project(
        _=Depends(
            require_permission(
                "project.read.assigned", assigned_project_scope("project_id")
            )
        ),
    ):
        return {"ok": True}

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "member", "password": "Writer-Strong-123!"},
    )
    assert login.status_code == 200
    assert client.get("/projects/client-claimed-project").status_code == 403

    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO project_members
                (id, project_id, user_id, project_role, status)
            VALUES ('membership-1', 'assigned-project', ?, 'writer', 'active')
            """,
            (user["id"],),
        )

    assert client.get("/projects/assigned-project").status_code == 200
