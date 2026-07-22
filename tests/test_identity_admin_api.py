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

from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.repository import IdentityRepository
from identity.session_service import SessionService


@pytest.fixture
def repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


@pytest.fixture
def app(repository: IdentityRepository) -> FastAPI:
    app = FastAPI()
    app.include_router(build_identity_router(repository, SessionService(repository)))
    return app


def setup_admin(client: TestClient, username: str = "admin") -> str:
    response = client.post(
        "/api/auth/setup",
        json={
            "username": username,
            "display_name": "管理员",
            "password": "Admin-Strong-123!",
        },
    )
    assert response.status_code == 201
    return response.json()["csrf_token"]


def login_writer(client: TestClient, repository: IdentityRepository) -> str:
    writer = repository.create_user(
        "writer", "编剧", PasswordService().hash("Writer-Strong-123!")
    )
    role = repository.get_role_by_code("screenwriter")
    assert role is not None
    repository.replace_user_roles(writer["id"], [role["id"]])
    response = client.post(
        "/api/auth/login",
        json={"username": "writer", "password": "Writer-Strong-123!"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/api/admin/users", None),
        (
            "post",
            "/api/admin/users",
            {
                "username": "new-user",
                "display_name": "新用户",
                "password": "New-User-123!",
                "role_ids": [],
            },
        ),
        (
            "post",
            "/api/admin/roles/role:screenwriter/clone",
            {"code": "writer_copy", "name": "编剧副本"},
        ),
        (
            "put",
            "/api/admin/roles/role:screenwriter/permissions",
            {"permissions": [{"permission_code": "rank.read", "data_scope": "task"}]},
        ),
    ],
)
def test_non_admin_cannot_manage_users_or_roles(
    app: FastAPI,
    repository: IdentityRepository,
    method: str,
    path: str,
    json: dict | None,
):
    client = TestClient(app)
    csrf_token = login_writer(client, repository)

    response = client.request(
        method, path, json=json, headers={"X-CSRF-Token": csrf_token}
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "没有执行此操作的权限"}


def test_admin_can_create_and_list_users_and_roles(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    screenwriter = repository.get_role_by_code("screenwriter")
    assert screenwriter is not None

    created_user = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "username": "new-writer",
            "display_name": "新编剧",
            "password": "New-Writer-123!",
            "role_ids": [screenwriter["id"]],
        },
    )
    created_role = client.post(
        "/api/admin/roles",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "code": "custom_writer",
            "name": "定制编剧",
            "claimable_task_types": ["beats", "outline"],
            "permissions": [
                {"permission_code": "beats.edit", "data_scope": "project"}
            ],
        },
    )

    assert created_user.status_code == 201
    assert created_user.json()["role_codes"] == ["screenwriter"]
    assert any(
        user["username"] == "new-writer"
        for user in client.get("/api/admin/users").json()
    )
    assert created_role.status_code == 201
    grants = {
        grant["permission_code"] for grant in created_role.json()["permissions"]
    }
    assert {"beats.edit", "beats.read", "outline.read.approved", "page.writing"} <= grants
    assert created_role.json()["claimable_task_types"] == ["beats", "outline"]
    assert any(
        role["code"] == "custom_writer"
        for role in client.get("/api/admin/roles").json()
    )


def test_admin_can_clone_role_and_replace_permissions(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    source = repository.get_role_by_code("screenwriter")
    assert source is not None

    cloned = client.post(
        f"/api/admin/roles/{source['id']}/clone",
        headers={"X-CSRF-Token": csrf_token},
        json={"code": "writer_copy", "name": "编剧副本"},
    )
    assert cloned.status_code == 201
    assert cloned.json()["permissions"] == [
        {
            "permission_code": item["permission_code"],
            "data_scope": item["data_scope"],
        }
        for item in repository.list_role_permissions(source["id"])
    ]

    replaced = client.put(
        f"/api/admin/roles/{cloned.json()['id']}/permissions",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "permissions": [
                {"permission_code": "breakdown.edit", "data_scope": "task"}
            ]
        },
    )
    assert replaced.status_code == 200
    assert {
        grant["permission_code"] for grant in replaced.json()["permissions"]
    } >= {"breakdown.edit", "breakdown.read", "topic.read", "page.selection"}


def test_required_dependency_is_restored_while_parent_remains(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    role = repository.create_role("dependency_role", "依赖角色", "")
    repository.replace_role_permissions(role["id"], [("beats.edit", "project")])

    response = client.put(
        f"/api/admin/roles/{role['id']}/permissions",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "permissions": [
                {"permission_code": "beats.edit", "data_scope": "project"},
                {
                    "permission_code": "outline.read.approved",
                    "data_scope": "project",
                },
                {"permission_code": "page.writing", "data_scope": "project"},
            ]
        },
    )

    assert response.status_code == 200
    assert "beats.read" in {
        grant["permission_code"] for grant in response.json()["permissions"]
    }


def test_replacing_role_permissions_is_idempotent(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    role = repository.create_role("idempotent_role", "幂等角色", "")
    payload = {
        "permissions": [
            {"permission_code": "beats.edit", "data_scope": "project"}
        ]
    }

    first = client.put(
        f"/api/admin/roles/{role['id']}/permissions",
        headers={"X-CSRF-Token": csrf_token},
        json=payload,
    )
    second = client.put(
        f"/api/admin/roles/{role['id']}/permissions",
        headers={"X-CSRF-Token": csrf_token},
        json=payload,
    )

    assert first.status_code == second.status_code == 200
    assert second.json()["permissions"] == first.json()["permissions"]
    assert second.json()["permissions"] == [
        {"permission_code": "beats.edit", "data_scope": "project"},
        {"permission_code": "beats.read", "data_scope": "project"},
        {"permission_code": "outline.read.approved", "data_scope": "project"},
        {"permission_code": "page.writing", "data_scope": "project"},
    ]
    assert second.json()["permissions"] == repository.list_role_permissions(role["id"])


def test_last_admin_cannot_be_disabled_or_stripped_of_admin_role(app: FastAPI):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    admin = client.get("/api/auth/me").json()["user"]

    disabled = client.patch(
        f"/api/admin/users/{admin['id']}",
        headers={"X-CSRF-Token": csrf_token},
        json={"status": "disabled"},
    )
    stripped = client.put(
        f"/api/admin/users/{admin['id']}/roles",
        headers={"X-CSRF-Token": csrf_token},
        json={"role_ids": []},
    )

    assert disabled.status_code == 409
    assert stripped.status_code == 409
    assert client.get("/api/auth/me").status_code == 200


def test_rejected_admin_patch_leaves_profile_roles_and_sessions_unchanged(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    admin = client.get("/api/auth/me").json()["user"]
    system_admin = repository.get_role_by_code("system_admin")
    screenwriter = repository.get_role_by_code("screenwriter")
    assert system_admin is not None
    assert screenwriter is not None
    before = repository.get_user(admin["id"])
    with repository.connect() as connection:
        session_ids_before = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM sessions WHERE user_id = ? ORDER BY id",
                (admin["id"],),
            )
        ]

    response = client.patch(
        f"/api/admin/users/{admin['id']}",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "display_name": "不应保存",
            "status": "disabled",
            "role_ids": [system_admin["id"], screenwriter["id"]],
        },
    )

    assert response.status_code == 409
    assert repository.get_user(admin["id"]) == before
    with repository.connect() as connection:
        sessions_after = [
            dict(row)
            for row in connection.execute(
                "SELECT id, revoked_at FROM sessions WHERE user_id = ? ORDER BY id",
                (admin["id"],),
            )
        ]
    assert [session["id"] for session in sessions_after] == session_ids_before
    assert all(session["revoked_at"] is None for session in sessions_after)


def test_admin_can_update_user_roles_and_reset_password(
    app: FastAPI, repository: IdentityRepository
):
    client = TestClient(app)
    csrf_token = setup_admin(client)
    user = repository.create_user(
        "reset-user", "重置用户", PasswordService().hash("Original-123!")
    )
    role = repository.get_role_by_code("screenwriter")
    assert role is not None

    roles = client.put(
        f"/api/admin/users/{user['id']}/roles",
        headers={"X-CSRF-Token": csrf_token},
        json={"role_ids": [role["id"]]},
    )
    reset = client.post(
        f"/api/admin/users/{user['id']}/reset-password",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "Replacement-456!"},
    )

    assert roles.status_code == 200
    assert roles.json()["role_codes"] == ["screenwriter"]
    assert reset.status_code == 204
    assert client.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "Replacement-456!"},
    ).status_code == 200


def test_permissions_catalog_is_available_to_admin(app: FastAPI):
    client = TestClient(app)
    setup_admin(client)

    response = client.get("/api/admin/permissions")

    assert response.status_code == 200
    beats_edit = next(item for item in response.json() if item["code"] == "beats.edit")
    assert beats_edit["dependencies"] == [
        "beats.read",
        "outline.read.approved",
        "page.writing",
    ]
