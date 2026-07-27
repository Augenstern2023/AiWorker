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
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from organization.service import DepartmentService
from system_database import SystemDatabase, SystemDatabaseSettings


PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def postgres_url() -> str:
    value = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not value:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(value).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    return value


@pytest.fixture
def database(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> SystemDatabase:
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return SystemDatabase(SystemDatabaseSettings(postgres_url))


@pytest.fixture
def service(database: SystemDatabase) -> DepartmentService:
    with database.transaction() as connection:
        connection.execute(
            text(
                "DELETE FROM project_members "
                "WHERE project_id = 'project:organization'"
            )
        )
        connection.execute(text("DELETE FROM user_departments"))
        connection.execute(text("DELETE FROM departments"))
    return DepartmentService(database)


def create_user(database: SystemDatabase) -> str:
    user_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, username, normalized_username, display_name, password_hash)
                VALUES (:id, :username, :normalized_username, :display_name, :password_hash)
                """
            ),
            {
                "id": user_id,
                "username": f"organization-{user_id}",
                "normalized_username": f"organization-{user_id}",
                "display_name": "Organization User",
                "password_hash": "not-used-by-this-test",
            },
        )
    return user_id


def test_organization_schema_keeps_text_ids_and_primary_department_constraint(
    database: SystemDatabase,
):
    inspector = inspect(database.engine)
    assert inspector.has_table("departments")
    assert inspector.has_table("user_departments")
    assert next(
        column
        for column in inspector.get_columns("departments")
        if column["name"] == "id"
    )["type"].__class__.__name__ == "TEXT"

    with database.connection() as connection:
        index_definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = 'uq_user_departments_primary'"
            )
        ).scalar_one()

    assert "UNIQUE INDEX" in index_definition
    assert "is_primary IS TRUE" in index_definition


def test_department_tree_rejects_a_move_to_a_descendant(service: DepartmentService):
    root = service.create_department("Root")
    child = service.create_department("Child", parent_id=root["id"])

    with pytest.raises(ValueError, match="后代部门"):
        service.move_department(root["id"], child["id"])

    assert service.list_tree() == [
        {
            **root,
            "children": [{**child, "children": []}],
        }
    ]


def test_new_primary_assignment_replaces_the_users_existing_primary(
    service: DepartmentService, database: SystemDatabase
):
    user_id = create_user(database)
    product = service.create_department("Product")
    research = service.create_department("Research")

    service.assign_user_department(user_id, product["id"], is_primary=True)
    service.assign_user_department(user_id, research["id"], is_primary=True)

    assignments = service.list_user_departments(user_id)
    assert [(item["department_id"], item["is_primary"]) for item in assignments] == [
        (product["id"], False),
        (research["id"], True),
    ]


def test_disabled_or_nonempty_departments_reject_new_assignment_and_disable(
    service: DepartmentService, database: SystemDatabase
):
    user_id = create_user(database)
    parent = service.create_department("Parent")
    service.create_department("Child", parent_id=parent["id"])

    with pytest.raises(ValueError, match="非空部门"):
        service.disable_department(parent["id"])

    empty = service.create_department("Empty")
    service.disable_department(empty["id"])
    with pytest.raises(ValueError, match="已停用"):
        service.assign_user_department(user_id, empty["id"], is_primary=True)


def test_department_assignments_do_not_change_project_member_roles(
    service: DepartmentService, database: SystemDatabase
):
    user_id = create_user(database)
    department = service.create_department("Writing")
    membership_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            text(
                """
                INSERT INTO project_members
                    (id, project_id, user_id, project_role, status, added_by)
                VALUES (:id, 'project:organization', :user_id, 'screenwriter', 'active', :user_id)
                """
            ),
            {"id": membership_id, "user_id": user_id},
        )

    service.assign_user_department(user_id, department["id"], is_primary=True)

    assert service.list_project_member_roles("project:organization") == [
        {
            "id": membership_id,
            "user_id": user_id,
            "project_role": "screenwriter",
            "status": "active",
        }
    ]
