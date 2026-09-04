# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.service import AuditService
from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from identity.team_api import build_team_management_router


@pytest.fixture
def repository(isolated_system_database) -> IdentityRepository:
    result = IdentityRepository(isolated_system_database)
    result.seed_defaults()
    return result


@pytest.fixture
def client(repository: IdentityRepository) -> TestClient:
    sessions = SessionService(repository)
    permissions = PermissionService(repository, sessions)
    audit = AuditService(repository)
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(
        build_identity_router(
            repository,
            sessions,
            audit,
            permission_service=permissions,
        )
    )
    app.include_router(
        build_team_management_router(
            repository,
            sessions,
            audit,
            permission_service=permissions,
        )
    )
    return TestClient(app)


def setup_admin(client: TestClient) -> tuple[dict, str]:
    response = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "管理员",
            "password": "Admin-Strong-123!",
        },
    )
    assert response.status_code == 201
    return response.json()["user"], response.json()["csrf_token"]


def login_owner(
    client: TestClient, repository: IdentityRepository, username: str = "owner"
) -> tuple[dict, str]:
    password = "Owner-Strong-123!"
    user = repository.create_user(
        username, username, PasswordService().hash(password)
    )
    repository.assign_project_owner(user["id"])
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return user, response.json()["csrf_token"]


def test_repository_records_user_and_role_creator(repository: IdentityRepository):
    creator = repository.create_user("owner", "负责人", "hash")

    user = repository.create_user(
        "planner",
        "策划",
        "hash",
        created_by_user_id=creator["id"],
    )
    role = repository.create_role(
        "custom_planner",
        "自定义策划",
        "",
        created_by_user_id=creator["id"],
    )

    assert user["created_by_user_id"] == creator["id"]
    assert role["created_by_user_id"] == creator["id"]


def test_soft_delete_user_revokes_access_and_requeues_owned_work(
    repository: IdentityRepository,
):
    creator = repository.create_user("owner", "负责人", "hash")
    user = repository.create_user(
        "worker", "执行人", "hash", created_by_user_id=creator["id"]
    )
    role = repository.get_role_by_code("creative_planner")
    assert role is not None
    repository.replace_user_roles(user["id"], [role["id"]])
    with repository.connect() as connection:
        connection.exec_driver_sql(
            "INSERT INTO workflow_instances "
            "(id, workflow_type, resource_type, resource_id, created_by) "
            "VALUES ('workflow-delete', 'creative', 'project', 'project-delete', %s)",
            (creator["id"],),
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_tasks "
            "(id, workflow_id, task_type, role_code, assignee_id, status) "
            "VALUES ('task-delete', 'workflow-delete', 'brief', "
            "'creative_planner', %s, 'in_progress')",
            (user["id"],),
        )
        connection.exec_driver_sql(
            "INSERT INTO project_members "
            "(id, project_id, user_id, project_role, status, added_by) "
            "VALUES ('member-delete', 'project-delete', %s, 'creative_planner', "
            "'active', %s)",
            (user["id"], creator["id"]),
        )

    repository.soft_delete_user(user["id"], deleted_by_user_id=creator["id"])

    assert repository.get_user(user["id"]) is None
    with repository.connect() as connection:
        stored = connection.exec_driver_sql(
            "SELECT status, is_deleted FROM users WHERE id = %s", (user["id"],)
        ).fetchone()
        task = connection.exec_driver_sql(
            "SELECT status, assignee_id FROM workflow_tasks WHERE id = 'task-delete'"
        ).fetchone()
        member = connection.exec_driver_sql(
            "SELECT status FROM project_members WHERE id = 'member-delete'"
        ).mappings().fetchone()
    assert tuple(stored) == ("disabled", 1)
    assert tuple(task) == ("pool", None)
    assert member["status"] == "inactive"


def test_soft_delete_custom_role_removes_live_bindings(
    repository: IdentityRepository,
):
    creator = repository.create_user("owner", "负责人", "hash")
    user = repository.create_user("worker", "执行人", "hash")
    role = repository.create_role(
        "custom_worker",
        "自定义执行人",
        "",
        created_by_user_id=creator["id"],
    )
    repository.replace_user_roles(user["id"], [role["id"]])

    repository.soft_delete_role(role["id"], deleted_by_user_id=creator["id"])

    assert repository.get_role(role["id"]) is None
    assert repository.get_user(user["id"])["role_codes"] == []


def test_project_owner_can_create_and_manage_only_owned_users(
    client: TestClient, repository: IdentityRepository
):
    admin, _ = setup_admin(client)
    owner, csrf = login_owner(client, repository)
    planner = repository.get_role_by_code("creative_planner")
    assert planner is not None

    created = client.post(
        "/api/team/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "owned-planner",
            "display_name": "负责人创建的策划",
            "password": "Planner-Strong-123!",
            "role_ids": [planner["id"]],
        },
    )
    listed = client.get("/api/team/users")
    admin_row = next(item for item in listed.json() if item["id"] == admin["id"])
    owner_row = next(item for item in listed.json() if item["id"] == owner["id"])

    assert created.status_code == 201
    assert created.json()["created_by"]["id"] == owner["id"]
    assert created.json()["can_edit"] is True
    assert admin_row["is_system_admin"] is True
    assert admin_row["can_edit"] is False
    assert owner_row["is_project_owner"] is True
    assert owner_row["can_edit"] is False
    assert client.patch(
        f"/api/team/users/{admin['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "禁止修改"},
    ).status_code == 403


def test_project_owner_cannot_assign_governance_roles(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    _, csrf = login_owner(client, repository)
    project_owner = repository.get_role_by_code("project_owner")
    system_admin = repository.get_role_by_code("system_admin")
    assert project_owner is not None and system_admin is not None

    for role in (project_owner, system_admin):
        response = client.post(
            "/api/team/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "username": f"blocked-{role['code']}",
                "display_name": "禁止授权",
                "password": "Blocked-Strong-123!",
                "role_ids": [role["id"]],
            },
        )
        assert response.status_code == 422


def test_custom_role_permission_ceiling_is_enforced(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    _, csrf = login_owner(client, repository)

    allowed = client.post(
        "/api/team/roles",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "dialogue_writer",
            "name": "对白编剧",
            "permissions": [
                {"permission_code": "beats.edit", "data_scope": "project"}
            ],
            "claimable_task_types": ["beats"],
        },
    )
    forbidden = client.post(
        "/api/team/roles",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "fake_owner",
            "name": "伪负责人",
            "permissions": [
                {
                    "permission_code": "version.approve",
                    "data_scope": "managed_projects",
                }
            ],
        },
    )
    all_scope = client.post(
        "/api/team/roles",
        headers={"X-CSRF-Token": csrf},
        json={
            "code": "all_reader",
            "name": "全局读取",
            "permissions": [
                {"permission_code": "topic.read", "data_scope": "all"}
            ],
        },
    )

    assert allowed.status_code == 201
    assert "beats.edit" in {
        item["permission_code"] for item in allowed.json()["permissions"]
    }
    assert forbidden.status_code == 422
    assert all_scope.status_code == 422


def test_owner_can_soft_delete_owned_user_and_reset_password(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    owner, csrf = login_owner(client, repository)
    worker = repository.create_user(
        "owned-worker",
        "执行人",
        PasswordService().hash("Original-Strong-123!"),
        created_by_user_id=owner["id"],
    )

    reset = client.post(
        f"/api/team/users/{worker['id']}/reset-password",
        headers={"X-CSRF-Token": csrf},
        json={"password": "Replacement-Strong-456!"},
    )
    deleted = client.delete(
        f"/api/team/users/{worker['id']}",
        headers={"X-CSRF-Token": csrf},
    )

    assert reset.status_code == 204
    assert deleted.status_code == 204
    assert repository.get_user(worker["id"]) is None
    actions = {
        item["action"]
        for item in AuditService(repository).query(actor_user_id=owner["id"])
    }
    assert {"team.user.password.reset", "team.user.delete"} <= actions


def test_admin_can_soft_delete_user_and_custom_role(
    client: TestClient, repository: IdentityRepository
):
    admin, csrf = setup_admin(client)
    user = repository.create_user(
        "admin-created-user",
        "管理员创建用户",
        "hash",
        created_by_user_id=admin["id"],
    )
    role = repository.create_role(
        "admin_custom",
        "管理员自定义角色",
        "",
        created_by_user_id=admin["id"],
    )

    deleted_user = client.delete(
        f"/api/admin/users/{user['id']}", headers={"X-CSRF-Token": csrf}
    )
    deleted_role = client.delete(
        f"/api/admin/roles/{role['id']}", headers={"X-CSRF-Token": csrf}
    )

    assert deleted_user.status_code == 204
    assert deleted_role.status_code == 204
    assert repository.get_user(user["id"]) is None
    assert repository.get_role(role["id"]) is None


def test_custom_role_is_global_but_only_its_creator_can_edit(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    owner, owner_csrf = login_owner(client, repository)
    created = client.post(
        "/api/team/roles",
        headers={"X-CSRF-Token": owner_csrf},
        json={
            "code": "global_writer",
            "name": "全局编剧",
            "permissions": [
                {"permission_code": "outline.edit", "data_scope": "project"}
            ],
            "claimable_task_types": ["outline"],
        },
    )
    assert created.status_code == 201

    admin_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin-Strong-123!"},
    )
    admin_csrf = admin_login.json()["csrf_token"]
    assert client.patch(
        f"/api/admin/users/{owner['id']}",
        headers={"X-CSRF-Token": admin_csrf},
        json={"status": "disabled"},
    ).status_code == 200
    successor_password = "Successor-Strong-123!"
    successor = repository.create_user(
        "successor-owner",
        "接任负责人",
        PasswordService().hash(successor_password),
    )
    assert client.post(
        "/api/admin/project-owner",
        headers={"X-CSRF-Token": admin_csrf},
        json={"user_id": successor["id"]},
    ).status_code == 200

    successor_login = client.post(
        "/api/auth/login",
        json={"username": "successor-owner", "password": successor_password},
    )
    successor_csrf = successor_login.json()["csrf_token"]
    role = next(
        item
        for item in client.get("/api/team/roles").json()
        if item["code"] == "global_writer"
    )
    edited = client.patch(
        f"/api/team/roles/{role['id']}",
        headers={"X-CSRF-Token": successor_csrf},
        json={"name": "禁止修改"},
    )

    assert role["can_assign"] is True
    assert role["can_edit"] is False
    assert edited.status_code == 403


def test_project_owner_can_clone_an_assignable_role(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    owner, csrf = login_owner(client, repository)
    source = repository.get_role_by_code("screenwriter")
    assert source is not None

    cloned = client.post(
        f"/api/team/roles/{source['id']}/clone",
        headers={"X-CSRF-Token": csrf},
        json={"code": "screenwriter_copy", "name": "编剧副本"},
    )

    assert cloned.status_code == 201
    assert cloned.json()["created_by"]["id"] == owner["id"]
    assert cloned.json()["can_edit"] is True
    assert cloned.json()["permissions"]


def test_business_roles_cannot_access_team_management_api(
    client: TestClient, repository: IdentityRepository
):
    setup_admin(client)
    password = "Writer-Strong-123!"
    writer = repository.create_user(
        "team-blocked-writer",
        "编剧",
        PasswordService().hash(password),
    )
    role = repository.get_role_by_code("screenwriter")
    assert role is not None
    repository.replace_user_roles(writer["id"], [role["id"]])
    login = client.post(
        "/api/auth/login",
        json={"username": "team-blocked-writer", "password": password},
    )

    assert login.status_code == 200
    assert client.get("/api/team/users").status_code == 403
    assert client.get("/api/team/roles").status_code == 403
