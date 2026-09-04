# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from audit.api import build_audit_router
from audit.service import AuditService
from identity.api import build_identity_router
from identity.dependencies import current_principal
from identity.password_service import PasswordService
from identity.permission_catalog import PERMISSIONS
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository


@pytest.fixture
def repository(isolated_system_database) -> IdentityRepository:
    repository = IdentityRepository(isolated_system_database)
    repository.seed_defaults()
    return repository


@pytest.fixture
def admin(repository: IdentityRepository) -> Principal:
    user = repository.create_user("admin", "Admin", "unused-password-hash")
    role = repository.get_role_by_code("system_admin")
    assert role is not None
    repository.replace_user_roles(user["id"], [role["id"]])
    return Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-1",
    )


@pytest.fixture
def audit_service(repository: IdentityRepository) -> AuditService:
    return AuditService(repository)


def _request(request_id: str = "request-123") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [(b"user-agent", b"audit-test")],
            "client": ("127.0.0.1", 4321),
        }
    )
    request.state.request_id = request_id
    return request


def test_system_admin_override_audit_allows_empty_reason(
    audit_service: AuditService, admin: Principal
):
    event_id = audit_service.record(
        actor=admin,
        action="version.force_unlock",
        target_type="creative_stage_version",
        target_id="42",
        before={"status": "submitted"},
        after={"status": "draft"},
        reason=None,
        result="success",
    )

    event = audit_service.get(event_id)
    assert event is not None
    assert event["reason"] is None
    assert event["actor_user_id"] == admin.user_id
    assert event["actor_role_snapshot"] == ["system_admin"]
    assert event["before_summary"] == {"status": "submitted"}
    assert event["after_summary"] == {"status": "draft"}


def test_audit_redacts_secrets_and_records_request_metadata(
    audit_service: AuditService, admin: Principal
):
    event_id = audit_service.record(
        actor=admin,
        action="config.update",
        target_type="settings",
        target_id="llm",
        before={"api_key": "old", "nested": {"access_token": "old-token"}},
        after={
            "password": "new-password",
            "activation_code": "code",
            "items": [{"session_cookie": "cookie"}, {"value": "kept"}],
        },
        reason="rotation",
        result="success",
        request=_request(),
    )

    event = audit_service.get(event_id)
    assert event is not None
    assert event["before_summary"] == {
        "api_key": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]"},
    }
    assert event["after_summary"] == {
        "password": "[REDACTED]",
        "activation_code": "[REDACTED]",
        "items": [{"session_cookie": "[REDACTED]"}, {"value": "kept"}],
    }
    assert event["request_id"] == "request-123"
    assert event["ip_address"] == "127.0.0.1"
    assert event["user_agent"] == "audit-test"


def test_audit_redacts_sensitive_key_value_text_inside_error_summaries(
    audit_service: AuditService, admin: Principal
):
    event_id = audit_service.record(
        actor=admin,
        action="schedule.run.failed",
        target_type="schedule",
        target_id="run:1",
        before=None,
        after={"error_summary": "request failed: token=top-secret password: keep-out"},
        reason=None,
        result="failure",
    )

    event = audit_service.get(event_id)

    assert event is not None
    assert "top-secret" not in event["after_summary"]["error_summary"]
    assert "keep-out" not in event["after_summary"]["error_summary"]
    assert "[REDACTED]" in event["after_summary"]["error_summary"]


def test_audit_summaries_are_bounded_and_queries_are_filterable(
    audit_service: AuditService, admin: Principal
):
    matching_id = audit_service.record(
        actor=admin,
        action="user.update",
        target_type="user",
        target_id="user-2",
        before=None,
        after={"notes": "x" * 20_000},
        reason=None,
        result="success",
    )
    audit_service.record(
        actor=None,
        action="auth.login",
        target_type="user",
        target_id="unknown",
        before=None,
        after=None,
        reason=None,
        result="failure",
    )

    event = audit_service.get(matching_id)
    assert event is not None
    assert event["after_summary"]["_truncated"] is True
    assert len(json.dumps(event["after_summary"]).encode("utf-8")) <= 8192
    assert [item["id"] for item in audit_service.query(
        action="user.update", target_type="user", result="success"
    )] == [matching_id]


def test_identity_mutations_and_login_results_are_audited(
    repository: IdentityRepository, audit_service: AuditService
):
    session_service = SessionService(repository, secure_cookie=False)
    app = FastAPI()
    app.include_router(build_identity_router(repository, session_service, audit_service))
    client = TestClient(app)
    setup = client.post(
        "/api/auth/setup",
        headers={"X-Request-ID": "setup-request"},
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )
    assert setup.status_code == 201
    csrf_token = setup.json()["csrf_token"]
    role = repository.get_role_by_code("screenwriter")
    assert role is not None
    created = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "username": "writer",
            "display_name": "Writer",
            "password": "Writer-Strong-123!",
            "role_ids": [],
        },
    )
    assert created.status_code == 201

    failed_login = TestClient(app).post(
        "/api/auth/login",
        headers={"X-Request-ID": "failed-login-request"},
        json={"username": "writer", "password": "wrong-password"},
    )
    successful_login = TestClient(app).post(
        "/api/auth/login",
        headers={"X-Request-ID": "successful-login-request"},
        json={"username": "writer", "password": "Writer-Strong-123!"},
    )
    roles = client.put(
        f"/api/admin/users/{created.json()['id']}/roles",
        headers={"X-CSRF-Token": csrf_token},
        json={"role_ids": [role["id"]]},
    )
    disabled = client.patch(
        f"/api/admin/users/{created.json()['id']}",
        headers={"X-CSRF-Token": csrf_token},
        json={"status": "disabled"},
    )
    permissions = client.put(
        f"/api/admin/roles/{role['id']}/permissions",
        headers={"X-CSRF-Token": csrf_token},
        json={"permissions": [{"permission_code": "rank.read", "data_scope": "task"}]},
    )

    assert failed_login.status_code == 401
    assert successful_login.status_code == 200
    assert roles.status_code == 200
    assert disabled.status_code == 200
    assert permissions.status_code == 200

    login_events = audit_service.query(action="auth.login")
    assert {event["result"] for event in login_events} == {"success", "failure"}
    failed_event = next(event for event in login_events if event["result"] == "failure")
    successful_event = next(event for event in login_events if event["result"] == "success")
    assert failed_event["actor_user_id"] is None
    assert failed_event["request_id"] == "failed-login-request"
    assert successful_event["actor_user_id"] == created.json()["id"]

    user_event = audit_service.query(action="user.update")[0]
    role_event = audit_service.query(action="user.roles.replace")[0]
    grant_event = audit_service.query(action="role.permissions.replace")[0]
    assert user_event["before_summary"]["status"] == "active"
    assert user_event["after_summary"]["status"] == "disabled"
    assert role_event["before_summary"]["role_codes"] == []
    assert role_event["after_summary"]["role_codes"] == ["screenwriter"]
    assert grant_event["before_summary"]["permissions"]
    assert grant_event["after_summary"]["permissions"] == [
        {"permission_code": "rank.read", "data_scope": "task"}
    ]


def test_admin_audit_api_requires_permission_and_applies_filters(
    repository: IdentityRepository, audit_service: AuditService, admin: Principal
):
    session_service = SessionService(repository, secure_cookie=False)
    permission_service = PermissionService(repository, session_service)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.include_router(build_identity_router(repository, session_service, audit_service))
    app.include_router(build_audit_router(audit_service))
    audit_service.record(
        actor=admin,
        action="user.update",
        target_type="user",
        target_id="user-2",
        before={"status": "active"},
        after={"status": "disabled"},
        reason=None,
        result="success",
    )
    client = TestClient(app)

    assert client.get("/api/admin/audit").status_code == 401
    # The fixture user has a placeholder hash, so set a real password before login.
    with repository.connect() as connection:
        connection.exec_driver_sql(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (PasswordService().hash("Admin-Strong-123!"), admin.user_id),
        )
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin-Strong-123!"},
    )
    assert login.status_code == 200

    response = client.get(
        "/api/admin/audit",
        params={"action": "user.update", "target_id": "user-2", "result": "success"},
    )
    assert response.status_code == 200
    assert [event["action"] for event in response.json()] == ["user.update"]
    assert response.headers["X-Total-Count"] == "1"


def test_logout_and_password_changes_are_audited(repository: IdentityRepository):
    audit_service = AuditService(repository)
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository, secure_cookie=False),
            audit_service,
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
    csrf_token = setup.json()["csrf_token"]

    changed = client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "current_password": "Admin-Strong-123!",
            "new_password": "Admin-New-Strong-456!",
        },
    )
    assert changed.status_code == 204
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin-New-Strong-456!"},
    )
    logged_out = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )

    assert logged_out.status_code == 204
    assert len(audit_service.query(action="auth.password.change")) == 1
    assert len(audit_service.query(action="auth.logout")) == 1


def test_audit_read_permission_is_seeded_and_assignable(
    repository: IdentityRepository,
):
    role = repository.create_role("auditor", "Auditor", "")

    grants = repository.replace_role_permissions(
        role["id"], [("audit.read", "all")]
    )

    assert "audit.read" in PERMISSIONS
    assert grants == [{"permission_code": "audit.read", "data_scope": "all"}]


def test_user_mutation_rolls_back_when_atomic_audit_insert_fails(
    repository: IdentityRepository, monkeypatch: pytest.MonkeyPatch
):
    audit_service = AuditService(repository)
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository, secure_cookie=False),
            audit_service,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    setup = client.post(
        "/api/auth/setup",
        json={
            "username": "admin",
            "display_name": "Admin",
            "password": "Admin-Strong-123!",
        },
    )
    csrf_token = setup.json()["csrf_token"]
    user = repository.create_user(
        "rollback-user",
        "Rollback User",
        PasswordService().hash("Rollback-Strong-123!"),
    )

    def fail_insert(event, *, connection=None):
        del event
        assert connection is not None
        raise RuntimeError("forced audit insert failure")

    monkeypatch.setattr(audit_service.repository, "insert", fail_insert)
    response = client.patch(
        f"/api/admin/users/{user['id']}",
        headers={"X-CSRF-Token": csrf_token},
        json={"status": "disabled"},
    )

    assert response.status_code == 500
    assert repository.get_user(user["id"])["status"] == "active"
    assert audit_service.query(action="user.update") == []


def test_project_audit_reader_is_limited_to_managed_project(
    repository: IdentityRepository, audit_service: AuditService
):
    role = repository.get_role_by_code("project_owner")
    assert role is not None
    repository.replace_role_permissions(
        role["id"], [("audit.read.project", "managed_projects")]
    )
    user = repository.create_user("audit-owner", "Audit Owner", "hash")
    repository.replace_user_roles(user["id"], [role["id"]])
    owner = PermissionService(repository, SessionService(repository)).principal_for_user(
        user["id"], "audit-owner-session"
    )
    assert owner is not None
    WorkflowRepository(repository).add_project_member(
        "managed-project", owner.user_id, "project_owner", owner
    )
    audit_service.record(
        actor=owner,
        action="workflow.claim",
        target_type="task",
        target_id="task-1",
        project_id="managed-project",
        before=None,
        after={"status": "assigned"},
        reason=None,
        result="success",
    )
    app = FastAPI()
    app.state.identity_permission_service = PermissionService(
        repository, SessionService(repository)
    )
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_audit_router(audit_service))
    client = TestClient(app)

    assert client.get("/api/admin/audit").status_code == 403
    assert client.get(
        "/api/admin/audit", params={"project_id": "other-project"}
    ).status_code == 403
    managed = client.get(
        "/api/admin/audit", params={"project_id": "managed-project"}
    )
    assert managed.status_code == 200
    assert [event["target_id"] for event in managed.json()] == ["task-1"]
