# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from audit.service import AuditService
from creative.api import register_creative_routes
from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.schema import project_members
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


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
