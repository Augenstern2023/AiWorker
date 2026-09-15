# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/WebGUI.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from identity.api import build_identity_router
from identity.presence_api import build_presence_router
from identity.presence_service import PresenceService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from identity.password_service import PasswordService
from identity.permission_service import PermissionService
from WebGUI import (
    app as production_app,
    configure_cors,
    install_security_middleware,
    llm_settings_repository,
    resolve_backup_path,
    system_database,
    _api_permission,
)
from audit.service import AuditService
from analysis.llm_credentials import KeyringLLMCredentialStore
from analysis.llm_settings_api import LLMSettingsService, build_llm_settings_router
from tests.analysis.test_llm_settings import _repository


def _secured_app(repository: IdentityRepository) -> tuple[FastAPI, SessionService]:
    session_service = SessionService(repository, secure_cookie=False)
    app = FastAPI()
    seen_request_ids: list[str | None] = []

    @app.middleware("http")
    async def observe_request_id(request: Request, call_next):
        seen_request_ids.append(getattr(request.state, "request_id", None))
        return await call_next(request)

    @app.post("/api/protected")
    def protected() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/public-write")
    def public_write() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(build_identity_router(repository, session_service))
    install_security_middleware(app, session_service)
    app.state.seen_request_ids = seen_request_ids
    return app, session_service


@pytest.fixture
def secured_app(
    isolated_system_database,
) -> tuple[FastAPI, SessionService]:
    return _secured_app(IdentityRepository(isolated_system_database))


def test_request_id_is_available_before_logging_and_returned(secured_app):
    app, _ = secured_app

    response = TestClient(app).post(
        "/api/public-write", headers={"X-Request-ID": "client-request-123"}
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "client-request-123"
    assert app.state.seen_request_ids == []


def test_authenticated_write_requires_matching_csrf_token(secured_app):
    app, _ = secured_app
    client = TestClient(app)
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )
    assert setup.status_code == 201
    csrf_token = setup.json()["csrf_token"]

    missing = client.post("/api/protected")
    incorrect = client.post(
        "/api/protected", headers={"X-CSRF-Token": "incorrect"}
    )
    accepted = client.post(
        "/api/protected", headers={"X-CSRF-Token": csrf_token}
    )

    assert missing.status_code == 403
    assert missing.json() == {"detail": "CSRF 校验失败"}
    assert incorrect.status_code == 403
    assert accepted.status_code == 200


def test_credential_rotation_route_keeps_authenticated_csrf_protection(secured_app):
    """Would catch the secret-rotation route bypassing the global CSRF middleware."""

    app, _ = secured_app

    @app.middleware("http")
    async def represent_loopback_client(request: Request, call_next):
        request.scope["client"] = ("127.0.0.1", 50000)
        return await call_next(request)

    app.include_router(
        build_llm_settings_router(
            LLMSettingsService(_repository()),
            audit_service=object(),
        )
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )

    response = client.put(
        "/api/llm/providers/not-a-provider/credential",
        json={"api_key": "csrf-protected-secret"},
    )
    accepted = client.put(
        "/api/llm/providers/not-a-provider/credential",
        headers={"X-CSRF-Token": setup.json()["csrf_token"]},
        json={"api_key": "csrf-protected-secret"},
    )

    assert setup.status_code == 201
    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF 校验失败"}
    assert "csrf-protected-secret" not in response.text
    assert accepted.status_code == 400
    assert accepted.json() == {"detail": "提供商不存在"}
    assert "csrf-protected-secret" not in accepted.text


def test_authenticated_non_admin_presence_heartbeat_requires_csrf_not_wildcard(
    secured_app,
):
    app, session_service = secured_app
    repository = session_service.repository
    app.include_router(
        build_presence_router(
            PresenceService(repository),
            session_service,
            permission_service=app.state.identity_permission_service,
        )
    )
    client = TestClient(app)
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )
    assert setup.status_code == 201
    writer = repository.create_user(
        "heartbeat-writer",
        "Heartbeat Writer",
        PasswordService().hash("Writer-Strong-123!"),
    )
    login = client.post(
        "/api/auth/login",
        json={
            "username": "heartbeat-writer",
            "password": "Writer-Strong-123!",
        },
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == writer["id"]
    payload = {
        "tab_id": "writer-tab",
        "path": "/writing/projects",
        "is_visible": True,
        "idle_seconds": 0,
    }

    missing_csrf = client.post("/api/presence/heartbeat", json=payload)
    accepted = client.post(
        "/api/presence/heartbeat",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json=payload,
    )

    assert missing_csrf.status_code == 403
    assert missing_csrf.json() == {"detail": "CSRF 校验失败"}
    assert accepted.status_code == 204


def test_login_and_first_run_setup_are_csrf_exempt(secured_app):
    app, _ = secured_app
    client = TestClient(app)

    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )
    client.cookies.clear()
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin-Strong-123!"},
    )

    assert setup.status_code == 201
    assert login.status_code == 200


def test_protected_api_rejects_anonymous_write_before_csrf(secured_app):
    app, _ = secured_app

    response = TestClient(app).post("/api/public-write")

    assert response.status_code == 401


def test_cors_defaults_to_same_origin_and_uses_explicit_origin_list():
    same_origin_app = FastAPI()

    @same_origin_app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    configure_cors(same_origin_app, "")
    cross_origin = TestClient(same_origin_app).get(
        "/ping", headers={"Origin": "https://untrusted.example"}
    )
    assert "access-control-allow-origin" not in cross_origin.headers

    configured_app = FastAPI()

    @configured_app.get("/ping")
    def configured_ping() -> dict[str, bool]:
        return {"ok": True}

    configure_cors(
        configured_app,
        "https://admin.example, https://review.example,https://admin.example",
    )
    allowed = TestClient(configured_app).get(
        "/ping", headers={"Origin": "https://review.example"}
    )
    rejected = TestClient(configured_app).get(
        "/ping", headers={"Origin": "https://untrusted.example"}
    )
    assert allowed.headers["access-control-allow-origin"] == "https://review.example"
    assert "access-control-allow-origin" not in rejected.headers


def test_production_webgui_wires_shared_identity_security_and_routes():
    repository = production_app.state.identity_repository

    assert repository.database.url.get_backend_name() == "postgresql"
    assert isinstance(production_app.state.identity_password_service, PasswordService)
    assert isinstance(production_app.state.identity_session_service, SessionService)
    assert isinstance(production_app.state.identity_permission_service, PermissionService)
    assert isinstance(production_app.state.audit_service, AuditService)
    assert production_app.state.identity_session_service.repository is repository
    assert production_app.state.identity_permission_service.repository is repository

    routes = {
        (route.path, method)
        for route in production_app.routes
        for method in (getattr(route, "methods", None) or ())
    }
    assert ("/api/auth/login", "POST") in routes
    assert ("/api/admin/audit", "GET") in routes
    assert sum(
        route.path == "/api/admin/deliveries"
        for route in production_app.routes
    ) == 2
    assert sum(
        route.path == "/api/admin/deliveries/options"
        for route in production_app.routes
    ) == 1

    client = TestClient(production_app)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/audit").status_code == 401


def test_production_webgui_uses_system_database_for_llm_settings():
    assert llm_settings_repository.database is system_database
    assert not hasattr(llm_settings_repository, "cipher")


def test_production_webgui_uses_live_keyring_for_llm_settings():
    """Would catch WebGUI bypassing its live credential-store factory."""

    assert isinstance(
        llm_settings_repository.credential_store, KeyringLLMCredentialStore
    )


def test_backup_path_rejects_traversal_and_non_backup_names(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    assert resolve_backup_path(
        "data_backup_20260722_120000.json", backup_dir
    ) == (backup_dir / "data_backup_20260722_120000.json").resolve()
    for filename in ("../data.json", "nested/data_backup_x.json", "other.json"):
        try:
            resolve_backup_path(filename, backup_dir)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe backup filename accepted: {filename}")


def test_legacy_permissions_are_method_specific_for_destructive_routes():
    assert _api_permission("GET", "/api/files") == "data.media.preview"
    assert _api_permission("GET", "/api/file/preview") == "data.media.preview"
    assert _api_permission("DELETE", "/api/files") == "data.media.delete"
    assert _api_permission("GET", "/api/analytics/overview") == "analytics.read"
    assert _api_permission("POST", "/api/analytics/sync") == "*"


def test_production_app_has_no_team_management_api_routes():
    routes = {route.path for route in production_app.routes}

    assert all(not route.startswith("/api/team/") for route in routes)


def test_current_profile_api_relies_on_authenticated_session_not_page_permissions():
    assert _api_permission("GET", "/api/profile") is None
    assert _api_permission("PATCH", "/api/profile") is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/config"),
        ("get", "/api/config/backups"),
        ("post", "/api/config/backup/restore"),
        ("get", "/api/files"),
        ("get", "/api/media/list"),
        ("get", "/api/creative/topics"),
        ("post", "/api/creative/topics/generate"),
        ("get", "/api/workflow/tasks"),
        ("post", "/api/jobs"),
    ],
)
def test_production_api_surface_fails_closed_for_anonymous(method: str, path: str):
    response = TestClient(production_app).request(method, path)
    assert response.status_code == 401
