# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import sqlite3
from pathlib import Path

import pytest

from identity.permission_catalog import PERMISSION_DEPENDENCIES
from identity.repository import IdentityRepository


SYSTEM_TABLES = {
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "permission_dependencies",
    "sessions",
    "project_members",
    "workflow_instances",
    "workflow_tasks",
    "workflow_task_jobs",
    "workflow_task_events",
    "approval_records",
    "audit_logs",
}


def test_identity_repository_creates_all_system_tables(tmp_path: Path):
    db_path = tmp_path / "system.db"
    IdentityRepository(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert SYSTEM_TABLES <= tables


def test_identity_repository_seeds_exact_default_roles(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()

    assert {role["code"] for role in repository.list_roles()} == {
        "selection_operator",
        "creative_planner",
        "screenwriter",
        "project_owner",
        "system_admin",
    }
    admin = repository.get_role_by_code("system_admin")
    assert admin is not None
    assert admin["is_system"] is True
    assert repository.list_role_permissions(admin["id"]) == [
        {"permission_code": "*", "data_scope": "all"},
    ]


def test_seed_defaults_is_idempotent_and_preserves_custom_bindings(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()

    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO roles (id, code, name, description, is_system) "
            "VALUES ('custom-role', 'custom', 'Custom', 'Keep me', 0)"
        )
        connection.execute(
            "INSERT INTO users "
            "(id, username, normalized_username, display_name, password_hash) "
            "VALUES ('user-1', 'Alice', 'alice', 'Alice', 'hash')"
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ('user-1', 'custom-role')"
        )
        connection.execute(
            "UPDATE roles SET description = 'stale' WHERE code = 'selection_operator'"
        )

    repository.seed_defaults()

    assert repository.get_role_by_code("custom") is not None
    with repository.connect() as connection:
        binding_count = connection.execute(
            "SELECT COUNT(*) FROM user_roles WHERE user_id = 'user-1'"
        ).fetchone()[0]
    assert binding_count == 1
    assert repository.get_role_by_code("selection_operator")["description"] != "stale"


def test_seed_defaults_removes_stale_system_grants_and_dependencies(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None

    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO permissions (code, label) VALUES ('stale.permission', 'Stale')"
        )
        connection.execute(
            "INSERT INTO role_permissions (role_id, permission_code, data_scope) "
            "VALUES (?, 'stale.permission', 'all')",
            (admin_role["id"],),
        )
        connection.execute(
            "INSERT INTO permission_dependencies (permission_code, depends_on_code) "
            "VALUES ('breakdown.edit', 'stale.permission')"
        )

    repository.seed_defaults()

    assert repository.list_role_permissions(admin_role["id"]) == [
        {"permission_code": "*", "data_scope": "all"},
    ]
    with repository.connect() as connection:
        stale_dependency_count = connection.execute(
            """
            SELECT COUNT(*) FROM permission_dependencies
            WHERE permission_code = 'breakdown.edit'
              AND depends_on_code = 'stale.permission'
            """
        ).fetchone()[0]
    assert stale_dependency_count == 0


def test_default_roles_materialize_permission_dependency_closure(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    planner = repository.get_role_by_code("creative_planner")
    writer = repository.get_role_by_code("screenwriter")
    assert planner is not None
    assert writer is not None

    planner_permissions = {
        permission["permission_code"]
        for permission in repository.list_role_permissions(planner["id"])
    }
    writer_permissions = {
        permission["permission_code"]
        for permission in repository.list_role_permissions(writer["id"])
    }

    assert {"brief.read.approved", "page.planning"} <= planner_permissions
    assert {"outline.read.approved", "page.writing"} <= writer_permissions


def test_permission_dependencies_are_seeded(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()

    with repository.connect() as connection:
        actual = {
            (row["permission_code"], row["depends_on_code"])
            for row in connection.execute(
                "SELECT permission_code, depends_on_code FROM permission_dependencies"
            )
        }

    expected = {
        (permission, dependency)
        for permission, dependencies in PERMISSION_DEPENDENCIES.items()
        for dependency in dependencies
    }
    assert actual == expected


def test_usernames_are_unique_after_normalization(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, username, normalized_username, display_name, password_hash) "
            "VALUES ('user-1', 'Alice', 'alice', 'Alice', 'hash')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users "
                "(id, username, normalized_username, display_name, password_hash) "
                "VALUES ('user-2', 'ALICE', 'alice', 'Other Alice', 'hash')"
            )


def test_last_active_system_admin_cannot_be_removed(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    admin_role = repository.get_role_by_code("system_admin")
    assert admin_role is not None

    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, username, normalized_username, display_name, password_hash) "
            "VALUES ('admin-1', 'admin', 'admin', 'Admin', 'hash')"
        )
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            ("admin-1", admin_role["id"]),
        )

    assert repository.count_active_system_admins() == 1
    with pytest.raises(ValueError, match="系统必须至少保留一个启用的系统管理员"):
        repository.assert_admin_survives("admin-1", admin_role["id"])
