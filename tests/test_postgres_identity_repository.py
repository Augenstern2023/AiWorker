# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from datetime import datetime, timezone
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import make_url

from audit.api import build_audit_router
from audit.service import AuditService
from audit.api import build_audit_router
from creative.api import register_creative_routes
from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from identity.team_api import build_team_management_router
from organization.service import DepartmentService
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import project_members, roles, user_roles, users
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService
from web.navigation_service import NavigationService
from web.page_router import build_page_router


def test_identity_repository_uses_postgresql_system_database(
    monkeypatch: pytest.MonkeyPatch,
):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)

    repository.seed_defaults()
    username = f"postgres-user-{uuid.uuid4()}"
    user = repository.create_user(username, "PostgreSQL User", "hash")

    assert repository.get_user(user["id"])["username"] == username
    assert repository.get_role_by_code("SYSTEM_ADMIN") is not None

    workflow_repository = WorkflowRepository(repository)
    TransitionService(workflow_repository)
    task = workflow_repository.create_task(
        f"workflow:{uuid.uuid4()}",
        "selection",
        "selection_operator",
        actor=Principal(
            user_id=user["id"],
            username=username,
            display_name="PostgreSQL User",
            role_codes=(),
            permissions=frozenset(),
            data_scope="task",
            session_id="postgres-test",
        ),
    )
    assert task["status"] == "pool"

    audit = AuditService(repository)
    audit.record(
        actor=Principal(
            user_id=user["id"],
            username=username,
            display_name="PostgreSQL User",
            role_codes=(),
            permissions=frozenset(),
            data_scope="task",
            session_id="postgres-test",
        ),
        action="postgres.verify",
        target_type="workflow_task",
        target_id=task["id"],
        before=None,
        after={"status": "pool"},
        reason=None,
        result="success",
    )
    assert audit.query(action="postgres.verify")[0]["actor_display_name"] == "PostgreSQL User"


def test_workflow_event_serializes_datetime_details_on_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    identity_repository = IdentityRepository(
        SystemDatabase(SystemDatabaseSettings(postgres_url))
    )
    identity_repository.seed_defaults()
    username = f"event-user-{uuid.uuid4()}"
    user = identity_repository.create_user(username, "Event User", "hash")
    actor = Principal(
        user_id=user["id"],
        username=username,
        display_name="Event User",
        role_codes=(),
        permissions=frozenset(),
        data_scope="task",
        session_id="postgres-test",
    )
    workflow_repository = WorkflowRepository(identity_repository)
    task = workflow_repository.create_task(
        f"workflow:{uuid.uuid4()}",
        "selection",
        "selection_operator",
        actor=actor,
    )

    recorded_at = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)
    with identity_repository.transaction() as connection:
        workflow_repository.record_task_event_in_transaction(
            task["id"],
            "creative.lock",
            actor,
            connection=connection,
            details={"recorded_at": recorded_at},
        )

    with identity_repository.connect() as connection:
        event_details = connection.exec_driver_sql(
            "SELECT details_json FROM workflow_task_events "
            "WHERE task_id = %s AND event_type = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (task["id"], "creative.lock"),
        ).scalar_one()
    assert event_details["recorded_at"] == recorded_at.isoformat()


def test_locked_beats_retry_completes_its_open_workflow_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    identity_repository = IdentityRepository(
        SystemDatabase(SystemDatabaseSettings(postgres_url))
    )
    identity_repository.seed_defaults()
    username = f"beats-user-{uuid.uuid4()}"
    user = identity_repository.create_user(username, "Beats User", "hash")
    screenwriter = identity_repository.get_role_by_code("screenwriter")
    assert screenwriter is not None
    identity_repository.replace_user_roles(user["id"], [screenwriter["id"]])
    actor = Principal(
        user_id=user["id"],
        username=username,
        display_name="Beats User",
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="task",
        session_id="postgres-test",
    )
    workflow_repository = WorkflowRepository(identity_repository)
    task = workflow_repository.create_task(
        f"workflow:{uuid.uuid4()}", "beats", "screenwriter", actor=actor
    )
    workflow_repository.claim_task(task["id"], actor)
    workflow_repository.start_task(task["id"], actor)

    class LockedBeatsAdapter:
        def lock_without_review(self, *args, **kwargs):
            raise ValueError("creative resource is immutable after completion")

        def domain_status(self, resource_type, resource_id):
            assert (resource_type, resource_id) == ("beats", "44")
            return "locked"

        def beats_episode_progress(self, resource_id):
            assert resource_id == "44"
            return {
                "required_episodes": [2],
                "approved_episodes": [2],
                "remaining_episodes": [],
                "complete": True,
            }

    result = TransitionService(
        workflow_repository, creative_adapter=LockedBeatsAdapter()
    ).complete_creative_without_review(task["id"], "beats", "44", actor)

    assert result["task"]["status"] == "completed"
    assert result["next_task"]["task_type"] == "script"


def test_user_contact_profile_migration_round_trips_on_the_isolated_postgres_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0004_runtime_timestamptz_jsonb")
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            ).scalars()
        )
        gender_default = connection.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'gender'"
            )
        ).scalar_one()
    assert {"email", "phone", "gender"} <= columns
    assert "unspecified" in gender_default

    command.downgrade(config, "0004_runtime_timestamptz_jsonb")
    with engine.connect() as connection:
        columns_after_downgrade = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            ).scalars()
        )
    assert not {"email", "phone", "gender"} & columns_after_downgrade
    command.upgrade(config, "head")


def test_postgres_initial_setup_lands_on_the_profile_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None
    with database.connection() as connection:
        active_admin_ids = list(
            connection.execute(
                select(users.c.id)
                .select_from(
                    users.join(user_roles, user_roles.c.user_id == users.c.id)
                )
                .where(
                    users.c.status == "active",
                    users.c.is_deleted == 0,
                    user_roles.c.role_id == admin_role["id"],
                )
            ).scalars()
        )
        role_enabled = connection.execute(
            select(roles.c.is_enabled).where(roles.c.id == admin_role["id"])
        ).scalar_one()
    with database.transaction() as connection:
        if active_admin_ids:
            connection.execute(
                update(users)
                .where(users.c.id.in_(active_admin_ids))
                .values(status="disabled")
            )
        connection.execute(
            update(roles)
            .where(roles.c.id == admin_role["id"])
            .values(is_enabled=0)
        )

    app = FastAPI()
    app.include_router(
        build_identity_router(repository, SessionService(repository), AuditService(repository))
    )
    created_user_id: str | None = None
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/auth/setup",
            json={
                "username": f"setup-profile-{uuid.uuid4()}",
                "display_name": "Setup Profile",
                "password": "Setup-Profile-123!",
            },
        )
        assert response.status_code == 201
        assert response.json()["landing_route"] == "/profile"
        created_user_id = response.json()["user"]["id"]
    finally:
        with database.transaction() as connection:
            if created_user_id is not None:
                connection.execute(delete(users).where(users.c.id == created_user_id))
            if active_admin_ids:
                connection.execute(
                    update(users)
                    .where(users.c.id.in_(active_admin_ids))
                    .values(status="active")
                )
            connection.execute(
                update(roles)
                .where(roles.c.id == admin_role["id"])
                .values(is_enabled=role_enabled)
            )


def test_project_owner_can_read_filter_and_export_all_audit_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    owner_role = repository.get_role_by_code("project_owner")
    assert owner_role is not None
    owner_user = repository.create_user(
        f"audit-owner-{uuid.uuid4()}", "Audit Owner", "hash"
    )
    repository.replace_user_roles(owner_user["id"], [owner_role["id"]])
    permission_service = PermissionService(repository, SessionService(repository))
    owner = permission_service.principal_for_user(owner_user["id"], "owner-session")
    assert owner is not None
    assert "audit.read" in owner.permissions
    assert {"permission_code": "audit.read", "data_scope": "all"} in repository.list_role_permissions(
        owner_role["id"]
    )

    managed_project_id = f"project:managed-audit-{uuid.uuid4()}"
    other_project_id = f"project:other-audit-{uuid.uuid4()}"
    workflow_repository = WorkflowRepository(repository)
    workflow_repository.add_project_member(
        managed_project_id, owner.user_id, "project_owner", owner
    )
    audit_service = AuditService(repository)
    audit_service.record(
        actor=owner,
        action="workflow.claim",
        target_type="task",
        target_id="task:managed-audit",
        project_id=managed_project_id,
        before=None,
        after={"status": "assigned"},
        reason=None,
        result="success",
    )
    other_event_id = audit_service.record(
        actor=owner,
        action="workflow.claim",
        target_type="task",
        target_id="task:other-audit",
        project_id=other_project_id,
        before=None,
        after={"status": "assigned"},
        reason=None,
        result="success",
    )
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_audit_router(audit_service))
    client = TestClient(app)

    options = client.get("/api/admin/audit/filter-options")
    assert options.status_code == 200
    assert options.json()["project_scope_required"] is False
    assert {item["value"] for item in options.json()["projects"]} >= {
        managed_project_id,
        other_project_id,
    }
    unfiltered = client.get("/api/admin/audit", params={"action": "workflow.claim"})
    assert unfiltered.status_code == 200
    assert {event["target_id"] for event in unfiltered.json()} >= {
        "task:managed-audit",
        "task:other-audit",
    }
    filtered = client.get(
        "/api/admin/audit",
        params={"project_id": managed_project_id, "action": "workflow.claim"},
    )
    assert filtered.status_code == 200
    assert [event["target_id"] for event in filtered.json()] == ["task:managed-audit"]
    exported = client.get(
        "/api/admin/audit/export",
        params={"format": "csv"},
    )
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert b"task:managed-audit" in exported.content
    assert b"task:other-audit" in exported.content
    assert client.get(f"/api/admin/audit/{other_event_id}").status_code == 200

    navigation = NavigationService().items_for(owner)
    assert any(
        item["route"] == "/admin/audit"
        for group in navigation
        for item in group["items"]
    )
    page_app = FastAPI()
    page_app.state.identity_permission_service = permission_service
    page_app.dependency_overrides[current_principal] = lambda: owner
    page_app.include_router(
        build_page_router(
            Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates")),
            NavigationService(),
        )
    )
    assert TestClient(page_app).get("/admin/audit").status_code == 200


def test_creative_routes_accept_the_postgres_workflow_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity_repository = IdentityRepository(database)
    identity_repository.seed_defaults()

    app = FastAPI()
    register_creative_routes(
        app,
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        workflow_repository=WorkflowRepository(identity_repository),
    )

    assert any(route.path.startswith("/api/creative") for route in app.routes)


def test_postgres_login_creates_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    password_service = PasswordService()
    username = f"login-user-{uuid.uuid4()}"
    repository.create_user(
        username,
        "PostgreSQL Login User",
        password_service.hash("StrongPassword-123!"),
    )
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository),
            AuditService(repository),
            password_service=password_service,
        )
    )

    response = TestClient(app).post(
        "/api/auth/login",
        json={"username": username, "password": "StrongPassword-123!"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["username"] == username


def test_postgres_registration_creates_a_disabled_user_pending_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository),
            AuditService(repository),
        )
    )
    client = TestClient(app)
    username = f"registration-user-{uuid.uuid4()}"
    payload = {
        "username": username,
        "display_name": "注册运营",
        "password": "Registration-Strong-123!",
        "role_codes": ["selection_operator", "screenwriter"],
        "email": "registration@example.com",
        "phone": "13800138000",
        "gender": "female",
    }

    page = client.get("/register")
    response = client.post("/api/auth/register", json=payload)

    assert page.status_code == 200
    assert 'id="register-form"' in page.text
    assert response.status_code == 201
    assert response.json() == {"detail": "注册申请已提交，请等待管理员激活"}
    users = repository.list_users(username=username)
    assert len(users) == 1
    assert users[0]["username"] == username
    assert users[0]["status"] == "disabled"
    assert users[0]["role_codes"] == ["screenwriter", "selection_operator"]
    assert users[0]["email"] == "registration@example.com"
    assert users[0]["phone"] == "13800138000"
    assert users[0]["gender"] == "female"
    assert client.post(
        "/api/auth/login",
        json={"username": username, "password": payload["password"]},
    ).status_code == 401
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_postgres_registration_rejects_privileged_role_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    app = FastAPI()
    app.include_router(build_identity_router(repository, SessionService(repository)))
    response = TestClient(app).post(
        "/api/auth/register",
        json={
            "username": f"registration-admin-{uuid.uuid4()}",
            "display_name": "越权注册",
            "password": "Registration-Strong-123!",
            "role_codes": ["system_admin"],
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "所选角色不可注册"}


def test_postgres_admin_user_list_marks_other_users_as_deletable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    password_service = PasswordService()
    admin_username = f"delete-admin-{uuid.uuid4()}"
    target_username = f"delete-target-{uuid.uuid4()}"
    admin = repository.create_user(
        admin_username,
        "Delete Administrator",
        password_service.hash("Delete-Admin-123!"),
    )
    target = repository.create_user(
        target_username,
        "Delete Target",
        password_service.hash("Delete-Target-123!"),
    )
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None
    repository.replace_user_roles(admin["id"], [admin_role["id"]])
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository),
            password_service=password_service,
        )
    )
    client = TestClient(app)

    assert client.post(
        "/api/auth/login",
        json={"username": admin_username, "password": "Delete-Admin-123!"},
    ).status_code == 200
    users = client.get("/api/admin/users").json()
    can_delete = {user["id"]: user["can_delete"] for user in users}

    assert can_delete[admin["id"]] is False
    assert can_delete[target["id"]] is True


def test_postgres_role_management_endpoints_return_catalog_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin role page must use SQLAlchemy statements, not SQLite strings."""

    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    password_service = PasswordService()
    username = f"role-admin-{uuid.uuid4()}"
    user = repository.create_user(
        username,
        "Role Administrator",
        password_service.hash("StrongPassword-123!"),
    )
    system_admin = repository.get_role_by_code("system_admin")
    assert system_admin is not None
    repository.replace_user_roles(user["id"], [system_admin["id"]])
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository),
            AuditService(repository),
            password_service=password_service,
        )
    )
    client = TestClient(app)

    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": "StrongPassword-123!"},
    )

    assert login.status_code == 200
    assert client.get("/api/admin/roles").status_code == 200
    permissions = client.get("/api/admin/permissions")
    assert permissions.status_code == 200
    assert any(item["code"] == "page.admin.delivery" for item in permissions.json())


def test_postgres_role_page_access_endpoint_updates_the_effective_role_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    password_service = PasswordService()
    user = repository.create_user(
        f"role-pages-admin-{uuid.uuid4()}",
        "Role Pages Administrator",
        password_service.hash("StrongPassword-123!"),
    )
    system_admin = repository.get_role_by_code("system_admin")
    screenwriter = repository.get_role_by_code("screenwriter")
    assert system_admin is not None and screenwriter is not None
    repository.replace_user_roles(user["id"], [system_admin["id"]])
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            SessionService(repository),
            AuditService(repository),
            password_service=password_service,
        )
    )
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": user["username"], "password": "StrongPassword-123!"},
    )
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]

    catalog = client.get("/api/admin/roles/page-catalog")
    assert catalog.status_code == 200
    assert {item["route"] for item in catalog.json()} >= {
        "/selection/ranks", "/admin/data"
    }
    updated = client.put(
        f"/api/admin/roles/{screenwriter['id']}/pages",
        headers={"X-CSRF-Token": csrf_token},
        json={"page_routes": ["/selection/ranks", "/admin/data"]},
    )
    assert updated.status_code == 200
    assert updated.json()["page_routes"] == ["/admin/data", "/selection/ranks"]
    assert repository.list_role_page_access(screenwriter["id"]) == [
        "/admin/data", "/selection/ranks"
    ]


def test_postgres_project_member_listing_uses_text_primary_key_tiebreaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity_repository = IdentityRepository(database)
    identity_repository.seed_defaults()
    user = identity_repository.create_user(
        f"project-member-{uuid.uuid4()}", "Project Member", "unused-password-hash"
    )
    project_id = f"project:{uuid.uuid4()}"
    with database.transaction() as connection:
        connection.execute(
            project_members.insert().values(
                id=str(uuid.uuid4()),
                project_id=project_id,
                user_id=user["id"],
                project_role="member",
                status="active",
            )
        )

    members = WorkflowRepository(identity_repository).list_project_members(project_id)

    assert [member["user_id"] for member in members] == [user["id"]]


def test_postgres_team_user_list_resolves_creator_with_core_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    password_service = PasswordService()
    username = f"team-admin-{uuid.uuid4()}"
    user = repository.create_user(
        username,
        "Team Administrator",
        password_service.hash("StrongPassword-123!"),
    )
    system_admin = repository.get_role_by_code("system_admin")
    assert system_admin is not None
    repository.replace_user_roles(user["id"], [system_admin["id"]])
    session_service = SessionService(repository)
    app = FastAPI()
    app.include_router(
        build_identity_router(
            repository,
            session_service,
            AuditService(repository),
            password_service=password_service,
        )
    )
    app.include_router(
        build_team_management_router(
            repository,
            session_service,
            AuditService(repository),
            permission_service=PermissionService(repository, session_service),
        )
    )
    client = TestClient(app)

    assert client.post(
        "/api/auth/login",
        json={"username": username, "password": "StrongPassword-123!"},
    ).status_code == 200
    response = client.get("/api/team/users")

    assert response.status_code == 200
    assert any(item["id"] == user["id"] for item in response.json())
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": "StrongPassword-123!"},
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.post(
        f"/api/team/users/{user['id']}/reset-password",
        json={"password": "Replacement-Strong-123!"},
    ).status_code == 204


def test_postgres_audit_filter_options_list_distinct_select_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    user = repository.create_user(
        f"audit-filter-{uuid.uuid4()}", "Audit Filter User", "unused-password-hash"
    )
    principal = Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="audit-filter-options",
    )
    audit_service = AuditService(repository)
    project_id = f"project:{uuid.uuid4()}"
    target_id = f"department:{uuid.uuid4()}"
    audit_service.record(
        actor=principal,
        action="organization.department.create",
        target_type="department",
        target_id=target_id,
        project_id=project_id,
        before=None,
        after={"name": "Production"},
        reason=None,
        result="success",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_audit_router(audit_service))

    response = TestClient(app).get(
        "/api/admin/audit/filter-options",
        params={"project_id": project_id, "target_type": "department"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert {item["value"] for item in payload["actors"]} >= {user["id"]}
    assert {item["value"] for item in payload["projects"]} >= {project_id}
    assert {item["value"] for item in payload["actions"]} >= {
        "organization.department.create"
    }
    assert {item["value"] for item in payload["target_ids"]} >= {target_id}

    unscoped_response = TestClient(app).get("/api/admin/audit/filter-options")

    assert unscoped_response.status_code == 200
    assert {item["value"] for item in unscoped_response.json()["target_ids"]} >= {
        target_id
    }


def test_workflow_submission_dict_accepts_postgresql_jsonb_values() -> None:
    submission = WorkflowRepository._submission_dict(
        {"content_snapshot_json": {"source": "postgresql"}}
    )

    assert submission["content_snapshot"] == {"source": "postgresql"}


def test_postgres_permission_service_checks_assigned_project_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    user = repository.create_user(
        f"member-{uuid.uuid4()}", "Project Member", "unused-password-hash"
    )
    with database.transaction() as connection:
        connection.execute(
            project_members.insert().values(
                id=str(uuid.uuid4()),
                project_id="creative-project-test",
                user_id=user["id"],
                project_role="member",
                status="active",
            )
        )
    principal = Principal(
        user_id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        role_codes=(),
        permissions=frozenset(),
        data_scope="project",
        session_id="project-membership-test",
    )

    assert PermissionService(repository, SessionService(repository)).can_access_assigned_project(
        principal, "creative-project-test"
    )


def test_postgres_user_directory_filters_and_current_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    audit_service = AuditService(repository)
    department_service = DepartmentService(database)
    phone = f"139{uuid.uuid4().int:032d}"[:11]
    app = FastAPI()
    app.include_router(
        build_identity_router(repository, SessionService(repository), audit_service)
    )
    admin_client = TestClient(app, raise_server_exceptions=False)
    repository.seed_defaults()
    admin_username = f"profile-admin-{uuid.uuid4()}"
    admin = repository.create_user(
        admin_username, "Profile Admin", PasswordService().hash("Admin-Strong-123!")
    )
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None
    repository.replace_user_roles(admin["id"], [admin_role["id"]])
    login_admin = admin_client.post(
        "/api/auth/login",
        json={"username": admin_username, "password": "Admin-Strong-123!"},
    )
    assert login_admin.status_code == 200

    parent = department_service.create_department(f"Parent-{uuid.uuid4()}")
    child = department_service.create_department(
        f"Child-{uuid.uuid4()}", parent_id=parent["id"]
    )
    created = admin_client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": login_admin.json()["csrf_token"]},
        json={
            "username": f"profile-user-{uuid.uuid4()}",
            "display_name": "Profile User",
            "password": "Profile-Strong-123!",
            "role_ids": [],
            "email": f"PROFILE-{uuid.uuid4()}@EXAMPLE.COM ",
            "phone": phone,
            "gender": "female",
        },
    )

    assert created.status_code == 201
    assert created.json()["email"] == created.json()["email"].lower()
    assert created.json()["phone"] == phone
    assert created.json()["gender"] == "female"
    department_service.assign_user_department(
        created.json()["id"], child["id"], is_primary=True
    )
    directory = admin_client.get(
        "/api/admin/users",
        params={"department_id": parent["id"], "gender": "female"},
    )
    matching = next(
        user for user in directory.json() if user["id"] == created.json()["id"]
    )
    assert matching["primary_department"]["id"] == child["id"]
    export_csv = admin_client.get(
        "/api/admin/users/export",
        params={
            "department_id": parent["id"],
            "gender": "female",
            "format": "csv",
        },
    )
    assert export_csv.status_code == 200
    assert export_csv.content.startswith(b"\xef\xbb\xbf")
    assert created.json()["username"].encode() in export_csv.content
    export_xlsx = admin_client.get(
        "/api/admin/users/export",
        params={"username": created.json()["username"], "format": "xlsx"},
    )
    assert export_xlsx.status_code == 200
    assert export_xlsx.content.startswith(b"PK")

    user_client = TestClient(app, raise_server_exceptions=False)
    login = user_client.post(
        "/api/auth/login",
        json={"username": created.json()["username"], "password": "Profile-Strong-123!"},
    )
    assert login.json()["landing_route"] == "/profile"
    profile = user_client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.json()["departments"][0]["id"] == child["id"]
    changed_email = f"changed-{uuid.uuid4()}@example.com"
    updated = user_client.patch(
        "/api/profile",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"email": changed_email, "phone": None, "gender": "male"},
    )
    assert updated.status_code == 200
    assert updated.json()["email"].startswith("changed-")
    assert updated.json()["phone"] is None
    assert updated.json()["gender"] == "male"
    profile_audit = audit_service.query(action="user.profile.update")[0]
    audit_text = str(profile_audit)
    assert changed_email not in audit_text
    assert phone not in audit_text
    assert "***" in audit_text

    csrf_rejected = user_client.patch(
        "/api/profile", json={"gender": "female"}
    )
    assert csrf_rejected.status_code == 403
    invalid_phone = user_client.patch(
        "/api/profile",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"phone": "12345"},
    )
    assert invalid_phone.status_code == 422
    assert "中国大陆" in invalid_phone.json()["detail"]
    assert user_client.get("/api/admin/users").status_code == 403
    assert user_client.get("/api/admin/users/export").status_code == 403

    changed_password = "Profile-Changed-123!"
    password_change = user_client.post(
        "/api/auth/change-password",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={
            "current_password": "Profile-Strong-123!",
            "new_password": changed_password,
        },
    )
    assert password_change.status_code == 204
    assert TestClient(app).post(
        "/api/auth/login",
        json={"username": created.json()["username"], "password": changed_password},
    ).status_code == 200

    duplicate = admin_client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": login_admin.json()["csrf_token"]},
        json={
            "username": f"duplicate-contact-{uuid.uuid4()}",
            "display_name": "Duplicate Contact",
            "password": "Duplicate-Strong-123!",
            "role_ids": [],
            "email": updated.json()["email"].upper(),
            "phone": phone,
            "gender": "unspecified",
        },
    )
    assert duplicate.status_code == 409

    reset_target = repository.create_user(
        f"reset-target-{uuid.uuid4()}",
        "Reset Target",
        PasswordService().hash("Reset-Old-123!"),
    )
    reset = admin_client.post(
        f"/api/admin/users/{reset_target['id']}/reset-password",
        headers={"X-CSRF-Token": login_admin.json()["csrf_token"]},
        json={"password": "Reset-New-123!"},
    )
    assert reset.status_code == 204
    assert TestClient(app).post(
        "/api/auth/login",
        json={"username": reset_target["username"], "password": "Reset-New-123!"},
    ).status_code == 200


def test_postgres_role_directory_filters_and_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    audit_service = AuditService(repository)
    app = FastAPI()
    app.include_router(
        build_identity_router(repository, SessionService(repository), audit_service)
    )
    repository.seed_defaults()

    admin_username = f"role-export-admin-{uuid.uuid4()}"
    admin = repository.create_user(
        admin_username, "Role Export Admin", PasswordService().hash("Admin-Strong-123!")
    )
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None
    repository.replace_user_roles(admin["id"], [admin_role["id"]])
    admin_client = TestClient(app, raise_server_exceptions=False)
    assert admin_client.post(
        "/api/auth/login",
        json={"username": admin_username, "password": "Admin-Strong-123!"},
    ).status_code == 200

    marker = uuid.uuid4().hex
    enabled = repository.create_role(
        f"role-search-{marker}", "可检索角色", f"角色检索说明 {marker}"
    )
    disabled = repository.create_role(
        f"role-disabled-{marker}", "已停用角色", f"角色检索说明 {marker}"
    )
    repository.update_role(disabled["id"], is_enabled=False)

    unfiltered_result = admin_client.get(
        "/api/admin/roles", params={"keyword": "", "status": ""}
    )
    assert unfiltered_result.status_code == 200
    unfiltered_export = admin_client.get(
        "/api/admin/roles/export",
        params={"keyword": "", "status": "", "format": "csv"},
    )
    assert unfiltered_export.status_code == 200

    enabled_result = admin_client.get(
        "/api/admin/roles", params={"keyword": marker.upper(), "status": "enabled"}
    )
    assert enabled_result.status_code == 200
    assert [role["id"] for role in enabled_result.json()] == [enabled["id"]]

    disabled_result = admin_client.get(
        "/api/admin/roles", params={"keyword": marker, "status": "disabled"}
    )
    assert disabled_result.status_code == 200
    assert [role["id"] for role in disabled_result.json()] == [disabled["id"]]

    exported_csv = admin_client.get(
        "/api/admin/roles/export",
        params={"keyword": marker, "status": "disabled", "format": "csv"},
    )
    assert exported_csv.status_code == 200
    assert exported_csv.content.startswith(b"\xef\xbb\xbf")
    assert disabled["code"].encode() in exported_csv.content
    assert enabled["code"].encode() not in exported_csv.content

    exported_xlsx = admin_client.get(
        "/api/admin/roles/export",
        params={"keyword": marker, "status": "enabled", "format": "xlsx"},
    )
    assert exported_xlsx.status_code == 200
    assert exported_xlsx.content.startswith(b"PK")
    assert any(event["action"] == "role.export" for event in audit_service.query())

    member_username = f"role-export-member-{uuid.uuid4()}"
    repository.create_user(
        member_username, "Role Export Member", PasswordService().hash("Member-Strong-123!")
    )
    member_client = TestClient(app, raise_server_exceptions=False)
    assert member_client.post(
        "/api/auth/login",
        json={"username": member_username, "password": "Member-Strong-123!"},
    ).status_code == 200
    assert member_client.get("/api/admin/roles/export").status_code == 403
