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
from sqlalchemy import text
from sqlalchemy.engine import make_url

from identity.dependencies import current_principal
from identity.schema import Principal
from organization.api import build_organization_router
from organization.service import DepartmentService
from system_database import SystemDatabase, SystemDatabaseSettings


class RecordingAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def record(self, **event):
        self.actions.append(event["action"])


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> SystemDatabase:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    return SystemDatabase(SystemDatabaseSettings(postgres_url))


def _admin() -> Principal:
    return Principal(
        user_id="admin:organization-api",
        username="admin",
        display_name="Administrator",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="organization-api",
    )


def test_system_administrator_can_manage_departments_and_audit_changes(
    database: SystemDatabase,
):
    audit = RecordingAudit()
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin
    app.include_router(build_organization_router(DepartmentService(database), audit))
    client = TestClient(app)

    created = client.post("/api/admin/departments", json={"name": "Production"})

    assert created.status_code == 201
    department_id = created.json()["id"]
    renamed = client.patch(
        f"/api/admin/departments/{department_id}", json={"name": "Content"}
    )
    assert renamed.json()["name"] == "Content"
    assert client.delete(f"/api/admin/departments/{department_id}").status_code == 204
    assert audit.actions == [
        "organization.department.create",
        "organization.department.rename",
        "organization.department.delete",
    ]


def test_administrator_can_remove_a_user_department_assignment(
    database: SystemDatabase,
):
    audit = RecordingAudit()
    service = DepartmentService(database)
    user_id = f"user:organization-api:{uuid.uuid4()}"
    username = f"organization-api-{uuid.uuid4()}"
    with database.transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, username, normalized_username, display_name,
                    password_hash, status
                ) VALUES (
                    :id, :username, :username,
                    'Organization API User', 'unused', 'active'
                )
                """
            ),
            {"id": user_id, "username": username},
        )
    department = service.create_department(f"Production {uuid.uuid4()}")
    service.assign_user_department(
        user_id, department["id"], is_primary=True
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin
    app.include_router(build_organization_router(service, audit))
    client = TestClient(app)

    response = client.delete(
        f"/api/admin/users/{user_id}/departments/" + department["id"]
    )

    assert response.status_code == 204
    assert service.list_user_departments(user_id) == []
    assert audit.actions == ["organization.user_department.remove"]
