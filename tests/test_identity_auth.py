# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
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
def client(repository: IdentityRepository) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_identity_router(repository, SessionService(repository, secure_cookie=False))
    )
    return TestClient(app)


def create_user(
    repository: IdentityRepository,
    username: str = "writer",
    password: str = "Writer-123!",
    role_code: str = "screenwriter",
) -> dict[str, str]:
    password_hash = PasswordService().hash(password)
    role = repository.get_role_by_code(role_code)
    assert role is not None
    user_id = f"user:{username}"
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, normalized_username, display_name, password_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, username, username.casefold(), username.title(), password_hash),
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role["id"]),
        )
    return {"id": user_id, "username": username}


def test_password_service_uses_argon2id_and_rejects_wrong_password():
    service = PasswordService()

    password_hash = service.hash("Strong-Pass-123!")

    assert password_hash.startswith("$argon2id$")
    assert service.verify(password_hash, "Strong-Pass-123!") is True
    assert service.verify(password_hash, "Wrong-Pass-123!") is False
    assert service.verify("not-an-argon-hash", "Strong-Pass-123!") is False


def test_password_service_accepts_unicode_letters():
    service = PasswordService()

    password_hash = service.hash("强密码安全-123!")

    assert service.verify(password_hash, "强密码安全-123!") is True


@pytest.mark.parametrize(
    "password",
    [
        "Short-1!",
        "a" * 129 + "1!",
        "NoDigitsHere!",
        "NoSymbols123",
        "123456789!",
    ],
)
def test_password_service_rejects_passwords_outside_policy(password: str):
    with pytest.raises(ValueError, match="密码"):
        PasswordService().hash(password)


def test_session_stores_only_hashes_and_request_metadata(
    repository: IdentityRepository,
):
    user = create_user(repository)
    app = FastAPI()
    service = SessionService(repository)

    @app.get("/create")
    async def create(request: Request):
        return service.create_session(user["id"], request)

    raw_token, csrf_token = TestClient(app).get(
        "/create",
        headers={"user-agent": "identity-test"},
    ).json()

    with repository.connect() as connection:
        session = dict(connection.execute("SELECT * FROM sessions").fetchone())
    assert session["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
    assert session["csrf_token_hash"] == hashlib.sha256(csrf_token.encode()).hexdigest()
    assert raw_token not in session.values()
    assert csrf_token not in session.values()
    assert session["user_agent"] == "identity-test"
    assert session["ip_address"] == "testclient"


def test_persisted_session_survives_service_recreation(
    repository: IdentityRepository,
):
    create_user(repository)
    first_service = SessionService(repository)
    app = FastAPI()
    app.include_router(build_identity_router(repository, first_service))
    client = TestClient(app)
    login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    raw_token = client.cookies.get("aiworker_session")
    assert login.status_code == 200
    assert raw_token is not None

    restored = SessionService(repository).resolve_session(raw_token)

    assert restored is not None
    assert restored.principal.username == "writer"
    assert restored.csrf_token == login.json()["csrf_token"]


def test_first_setup_creates_system_admin_and_disables_setup(client: TestClient):
    response = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "管理员",
            "password": "Admin-Strong-123!",
        },
    )

    assert response.status_code == 201
    assert response.json()["user"]["role_codes"] == ["system_admin"]
    assert response.json()["landing_route"] == "/tasks"
    assert response.json()["csrf_token"]
    assert "aiworker_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert client.post(
        "/api/auth/setup",
        json={
            "username": "other",
            "display_name": "其他",
            "password": "Admin-Strong-456!",
        },
    ).status_code == 409
    assert client.get("/setup").status_code == 404


def test_session_cookie_sets_secure_when_configured(repository: IdentityRepository):
    app = FastAPI()
    app.include_router(
        build_identity_router(repository, SessionService(repository, secure_cookie=True))
    )

    response = TestClient(app, base_url="https://testserver").post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "管理员",
            "password": "Admin-Strong-123!",
        },
    )

    assert response.status_code == 201
    assert "Secure" in response.headers["set-cookie"]


def test_login_has_uniform_errors_and_returns_permission_landing_route(
    client: TestClient, repository: IdentityRepository
):
    create_user(repository)

    missing = client.post(
        "/api/auth/login", json={"username": "missing", "password": "Writer-123!"}
    )
    invalid = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Wrong-123!"}
    )
    login = client.post(
        "/api/auth/login", json={"username": "WRITER", "password": "Writer-123!"}
    )

    assert missing.status_code == invalid.status_code == 401
    assert missing.json() == invalid.json() == {"detail": "用户名或密码错误"}
    assert login.status_code == 200
    assert login.json()["landing_route"] == "/writing/projects"
    assert login.json()["user"]["role_codes"] == ["screenwriter"]
    assert login.json()["csrf_token"]


def test_me_returns_csrf_and_logout_requires_it(
    client: TestClient, repository: IdentityRepository
):
    create_user(repository)
    login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    csrf_token = login.json()["csrf_token"]

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["csrf_token"] == csrf_token
    assert client.post("/api/auth/logout").status_code == 403
    logout = client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
    )
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_disabled_user_sessions_are_revoked(
    client: TestClient, repository: IdentityRepository
):
    create_user(repository)
    login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    assert login.status_code == 200

    repository.disable_user(login.json()["user"]["id"])

    assert client.get("/api/auth/me").status_code == 401
    disabled_login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    assert disabled_login.status_code == 401
    assert disabled_login.json() == {"detail": "用户名或密码错误"}
    with repository.connect() as connection:
        revoked_at = connection.execute(
            "SELECT revoked_at FROM sessions WHERE user_id = ?",
            (login.json()["user"]["id"],),
        ).fetchone()["revoked_at"]
    assert revoked_at is not None


def test_expired_session_is_rejected(client: TestClient, repository: IdentityRepository):
    create_user(repository)
    login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    assert login.status_code == 200
    with repository.connect() as connection:
        connection.execute(
            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
        )

    assert client.get("/api/auth/me").status_code == 401


def test_change_password_requires_csrf_and_revokes_existing_sessions(
    client: TestClient, repository: IdentityRepository
):
    create_user(repository)
    login = client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    )
    csrf_token = login.json()["csrf_token"]
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": "Writer-123!", "new_password": "Writer-New-456!"},
    ).status_code == 403

    changed = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf_token},
        json={"current_password": "Writer-123!", "new_password": "Writer-New-456!"},
    )

    assert changed.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "writer", "password": "Writer-New-456!"}
    ).status_code == 200


def test_last_active_system_admin_cannot_be_disabled(
    client: TestClient, repository: IdentityRepository
):
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "管理员",
            "password": "Admin-Strong-123!",
        },
    )
    assert setup.status_code == 201

    with pytest.raises(ValueError, match="系统必须至少保留一个启用的系统管理员"):
        repository.disable_user(setup.json()["user"]["id"])


def test_login_and_initial_setup_pages_are_served(client: TestClient):
    assert "登录" in client.get("/login").text
    assert "初始化" in client.get("/setup").text
