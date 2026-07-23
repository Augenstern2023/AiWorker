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
import sqlite3

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
def repository(tmp_path: Path) -> IdentityRepository:
    result = IdentityRepository(tmp_path / "system.db")
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


def test_project_owner_replacement_requires_disabled_current_owner(
    repository: IdentityRepository,
):
    first = repository.create_user("first-owner", "第一负责人", "hash")
    second = repository.create_user("second-owner", "第二负责人", "hash")
    repository.assign_project_owner(first["id"])

    with pytest.raises(ValueError, match="先停用当前项目负责人"):
        repository.assign_project_owner(second["id"])

    repository.update_user(first["id"], status="disabled")
    repository.assign_project_owner(second["id"])

    assert "project_owner" not in repository.get_user(first["id"])["role_codes"]
    assert repository.get_user(second["id"])["role_codes"] == ["project_owner"]


def test_database_rejects_a_second_project_owner_binding(
    repository: IdentityRepository,
):
    first = repository.create_user("owner-one", "负责人一", "hash")
    second = repository.create_user("owner-two", "负责人二", "hash")
    role = repository.get_role_by_code("project_owner")
    assert role is not None
    repository.replace_user_roles(first["id"], [role["id"]])

    with pytest.raises(ValueError, match="项目负责人只能有一名"):
        repository.replace_user_roles(second["id"], [role["id"]])


def test_migration_normalizes_duplicate_project_owners_idempotently(
    tmp_path: Path,
):
    db_path = tmp_path / "legacy-system.db"
    legacy = IdentityRepository(db_path)
    legacy.seed_defaults()
    first = legacy.create_user("legacy-owner-1", "旧负责人一", "hash")
    second = legacy.create_user("legacy-owner-2", "旧负责人二", "hash")
    role = legacy.get_role_by_code("project_owner")
    assert role is not None
    with legacy.connect() as connection:
        connection.execute("DROP TRIGGER trg_single_project_owner_insert")
        connection.execute("DROP TRIGGER trg_single_project_owner_update")
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (first["id"], role["id"]),
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (second["id"], role["id"]),
        )

    migrated = IdentityRepository(db_path)
    IdentityRepository(db_path)
    with migrated.connect() as connection:
        bindings = connection.execute(
            "SELECT user_id FROM user_roles WHERE role_id = ?", (role["id"],)
        ).fetchall()
        events = connection.execute(
            "SELECT action FROM audit_logs "
            "WHERE action = 'role.project_owner.uniqueness_migration'"
        ).fetchall()

    assert [row["user_id"] for row in bindings] == [min(first["id"], second["id"])]
    assert len(events) == 1


def test_database_trigger_blocks_direct_duplicate_owner_binding(
    repository: IdentityRepository,
):
    first = repository.create_user("direct-owner-1", "负责人一", "hash")
    second = repository.create_user("direct-owner-2", "负责人二", "hash")
    role = repository.get_role_by_code("project_owner")
    assert role is not None
    repository.replace_user_roles(first["id"], [role["id"]])

    with pytest.raises(sqlite3.IntegrityError, match="项目负责人只能有一名"):
        with repository.connect() as connection:
            connection.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (second["id"], role["id"]),
            )


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
        connection.execute(
            "INSERT INTO workflow_instances "
            "(id, workflow_type, resource_type, resource_id, created_by) "
            "VALUES ('workflow-delete', 'creative', 'project', 'project-delete', ?)",
            (creator["id"],),
        )
        connection.execute(
            "INSERT INTO workflow_tasks "
            "(id, workflow_id, task_type, role_code, assignee_id, status) "
            "VALUES ('task-delete', 'workflow-delete', 'brief', "
            "'creative_planner', ?, 'in_progress')",
            (user["id"],),
        )
        connection.execute(
            "INSERT INTO project_members "
            "(id, project_id, user_id, project_role, status, added_by) "
            "VALUES ('member-delete', 'project-delete', ?, 'creative_planner', "
            "'active', ?)",
            (user["id"], creator["id"]),
        )

    repository.soft_delete_user(user["id"], deleted_by_user_id=creator["id"])

    assert repository.get_user(user["id"]) is None
    with repository.connect() as connection:
        stored = connection.execute(
            "SELECT status, is_deleted FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        task = connection.execute(
            "SELECT status, assignee_id FROM workflow_tasks WHERE id = 'task-delete'"
        ).fetchone()
        member = connection.execute(
            "SELECT status FROM project_members WHERE id = 'member-delete'"
        ).fetchone()
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


def test_admin_must_disable_current_owner_before_assigning_successor(
    client: TestClient, repository: IdentityRepository
):
    _, admin_csrf = setup_admin(client)
    owner = repository.create_user("current-owner", "当前负责人", "hash")
    repository.assign_project_owner(owner["id"])
    successor = repository.create_user("successor", "接任负责人", "hash")

    blocked = client.post(
        "/api/admin/project-owner",
        headers={"X-CSRF-Token": admin_csrf},
        json={"user_id": successor["id"]},
    )
    repository.update_user(owner["id"], status="disabled")
    assigned = client.post(
        "/api/admin/project-owner",
        headers={"X-CSRF-Token": admin_csrf},
        json={"user_id": successor["id"]},
    )

    assert blocked.status_code == 409
    assert assigned.status_code == 200
    assert repository.get_user(owner["id"])["role_codes"] == []
    assert repository.get_user(successor["id"])["role_codes"] == ["project_owner"]


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


def test_generic_admin_role_assignment_cannot_bypass_owner_handover(
    client: TestClient, repository: IdentityRepository
):
    _, csrf = setup_admin(client)
    candidate = repository.create_user("candidate", "候选负责人", "hash")
    owner_role = repository.get_role_by_code("project_owner")
    assert owner_role is not None

    created = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "bypass-owner",
            "display_name": "绕过负责人",
            "password": "Bypass-Strong-123!",
            "role_ids": [owner_role["id"]],
        },
    )
    replaced = client.put(
        f"/api/admin/users/{candidate['id']}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_ids": [owner_role["id"]]},
    )

    assert created.status_code == 422
    assert replaced.status_code == 422


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
