# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .permission_catalog import DEFAULT_ROLES, PERMISSIONS, PERMISSION_DEPENDENCIES


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    normalized_username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS permissions (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role_id)
);
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE RESTRICT,
    data_scope TEXT NOT NULL CHECK (data_scope IN ('task', 'project', 'managed_projects', 'all')),
    PRIMARY KEY (role_id, permission_code)
);
CREATE TABLE IF NOT EXISTS permission_dependencies (
    permission_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    depends_on_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE RESTRICT,
    PRIMARY KEY (permission_code, depends_on_code),
    CHECK (permission_code <> depends_on_code)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT,
    user_agent TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS project_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    project_role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    added_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, user_id, project_role)
);
CREATE TABLE IF NOT EXISTS workflow_instances (
    id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflow_tasks (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflow_instances(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    role_code TEXT NOT NULL,
    topic_id INTEGER,
    project_id TEXT,
    assignee_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    assigned_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    due_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflow_task_jobs (
    task_id TEXT NOT NULL REFERENCES workflow_tasks(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, job_id)
);
CREATE TABLE IF NOT EXISTS workflow_task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES workflow_tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    from_status TEXT,
    to_status TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS approval_records (
    id TEXT PRIMARY KEY,
    workflow_id TEXT REFERENCES workflow_instances(id) ON DELETE SET NULL,
    task_id TEXT REFERENCES workflow_tasks(id) ON DELETE SET NULL,
    version_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    project_id TEXT,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_project_members_project_id ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pool ON workflow_tasks(role_code, status);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
"""


class IdentityRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def seed_defaults(self) -> None:
        with self.connect() as connection:
            for code, label in PERMISSIONS.items():
                connection.execute(
                    """
                    INSERT INTO permissions (code, label) VALUES (?, ?)
                    ON CONFLICT(code) DO UPDATE SET
                        label = excluded.label,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (code, label),
                )

            # Dependency edges belong to the system catalog, so reseeding makes
            # this table an exact projection rather than retaining retired edges.
            connection.execute("DELETE FROM permission_dependencies")
            for permission_code, dependencies in PERMISSION_DEPENDENCIES.items():
                for depends_on_code in dependencies:
                    connection.execute(
                        """
                        INSERT INTO permission_dependencies (permission_code, depends_on_code)
                        VALUES (?, ?)
                        ON CONFLICT(permission_code, depends_on_code) DO NOTHING
                        """,
                        (permission_code, depends_on_code),
                    )

            for role in DEFAULT_ROLES:
                role_id = f"role:{role.code}"
                connection.execute(
                    """
                    INSERT INTO roles (id, code, name, description, is_system)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(code) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        is_system = excluded.is_system,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (role_id, role.code, role.name, role.description, int(role.is_system)),
                )
                stored_role_id = connection.execute(
                    "SELECT id FROM roles WHERE code = ?", (role.code,)
                ).fetchone()["id"]
                # Default-role grants are system-owned. Custom-role grants and
                # every user-role binding remain untouched.
                connection.execute(
                    "DELETE FROM role_permissions WHERE role_id = ?",
                    (stored_role_id,),
                )
                for permission_code, data_scope in self._expanded_permissions(role.permissions):
                    connection.execute(
                        """
                        INSERT INTO role_permissions (role_id, permission_code, data_scope)
                        VALUES (?, ?, ?)
                        ON CONFLICT(role_id, permission_code) DO UPDATE SET
                            data_scope = excluded.data_scope
                        """,
                        (stored_role_id, permission_code, data_scope),
                    )

    @staticmethod
    def _expanded_permissions(
        permissions: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        from .schema import DATA_SCOPE_ORDER

        expanded: dict[str, str] = {}
        pending = sorted(permissions, reverse=True)
        while pending:
            permission_code, data_scope = pending.pop()
            current_scope = expanded.get(permission_code)
            if current_scope is not None and (
                DATA_SCOPE_ORDER[current_scope] >= DATA_SCOPE_ORDER[data_scope]
            ):
                continue
            expanded[permission_code] = data_scope
            for dependency in sorted(PERMISSION_DEPENDENCIES.get(permission_code, ())):
                pending.append((dependency, data_scope))
        if "*" in expanded:
            return (("*", "all"),)
        return tuple(sorted(expanded.items()))

    def list_roles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM roles ORDER BY code").fetchall()
        return [self._role_dict(row) for row in rows]

    def get_role_by_code(self, code: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM roles WHERE code = ? COLLATE NOCASE", (code,)
            ).fetchone()
        return self._role_dict(row) if row else None

    def get_role(self, role_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM roles WHERE id = ?", (role_id,)
            ).fetchone()
        return self._role_dict(row) if row else None

    def create_role(
        self, code: str, name: str, description: str, *, is_system: bool = False
    ) -> dict[str, Any]:
        role_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO roles (id, code, name, description, is_system)
                VALUES (?, ?, ?, ?, ?)
                """,
                (role_id, code, name, description, int(is_system)),
            )
        role = self.get_role(role_id)
        assert role is not None
        return role

    def update_role(
        self,
        role_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            role = connection.execute(
                "SELECT * FROM roles WHERE id = ?", (role_id,)
            ).fetchone()
            if role is None:
                raise KeyError(role_id)
            if (
                role["code"].casefold() == "system_admin"
                and role["is_enabled"]
                and is_enabled is False
            ):
                active_admins = connection.execute(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users AS u
                    JOIN user_roles AS ur ON ur.user_id = u.id
                    WHERE ur.role_id = ? AND u.status = 'active' AND u.is_deleted = 0
                    """,
                    (role_id,),
                ).fetchone()[0]
                if active_admins:
                    raise ValueError("系统必须至少保留一个启用的系统管理员")
            connection.execute(
                """
                UPDATE roles
                SET name = COALESCE(?, name),
                    description = COALESCE(?, description),
                    is_enabled = COALESCE(?, is_enabled),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    name,
                    description,
                    int(is_enabled) if is_enabled is not None else None,
                    role_id,
                ),
            )
        updated = self.get_role(role_id)
        assert updated is not None
        return updated

    def list_role_permissions(self, role_id: str) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT permission_code, data_scope
                FROM role_permissions
                WHERE role_id = ?
                ORDER BY permission_code
                """,
                (role_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_role_permissions(
        self, role_id: str, permissions: list[tuple[str, str]]
    ) -> list[dict[str, str]]:
        expanded = self._expanded_permissions(tuple(permissions))
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM roles WHERE id = ?", (role_id,)
            ).fetchone() is None:
                raise KeyError(role_id)
            known_permissions = {
                row["code"]
                for row in connection.execute(
                    "SELECT code FROM permissions"
                ).fetchall()
            }
            unknown = {code for code, _ in expanded} - known_permissions
            if unknown:
                raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
            connection.execute(
                "DELETE FROM role_permissions WHERE role_id = ?", (role_id,)
            )
            connection.executemany(
                """
                INSERT INTO role_permissions (role_id, permission_code, data_scope)
                VALUES (?, ?, ?)
                """,
                [(role_id, code, scope) for code, scope in expanded],
            )
        return self.list_role_permissions(role_id)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users WHERE is_deleted = 0 ORDER BY normalized_username"
            ).fetchall()
        return [self._user_dict(row) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
            ).fetchone()
        return self._user_dict(row) if row else None

    def create_user(
        self, username: str, display_name: str, password_hash: str
    ) -> dict[str, Any]:
        user_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users
                    (id, username, normalized_username, display_name, password_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, username, username.casefold(), display_name, password_hash),
            )
        user = self.get_user(user_id)
        assert user is not None
        return user

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        role_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        unique_role_ids = (
            list(dict.fromkeys(role_ids)) if role_ids is not None else None
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute(
                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["is_deleted"]:
                raise KeyError(user_id)

            roles: list[sqlite3.Row] | None = None
            if unique_role_ids is not None:
                role_placeholders = ",".join("?" for _ in unique_role_ids)
                roles = connection.execute(
                    f"SELECT id, code, is_enabled FROM roles "
                    f"WHERE id IN ({role_placeholders})"
                    if unique_role_ids
                    else "SELECT id, code, is_enabled FROM roles WHERE 0",
                    unique_role_ids,
                ).fetchall()
                if len(roles) != len(unique_role_ids):
                    raise ValueError("角色不存在")

            currently_admin = connection.execute(
                """
                SELECT 1 FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id
                WHERE ur.user_id = ? AND r.code = 'system_admin' AND r.is_enabled = 1
                """,
                (user_id,),
            ).fetchone() is not None
            remains_admin = currently_admin
            if roles is not None:
                remains_admin = any(
                    role["code"].casefold() == "system_admin"
                    and role["is_enabled"]
                    for role in roles
                )
            resulting_status = status or user["status"]
            if (
                currently_admin
                and user["status"] == "active"
                and (resulting_status != "active" or not remains_admin)
            ):
                other_admins = connection.execute(
                    """
                    SELECT COUNT(DISTINCT u.id)
                    FROM users AS u
                    JOIN user_roles AS ur ON ur.user_id = u.id
                    JOIN roles AS r ON r.id = ur.role_id
                    WHERE u.status = 'active' AND u.is_deleted = 0
                      AND r.code = 'system_admin' AND r.is_enabled = 1
                      AND u.id <> ?
                    """,
                    (user_id,),
                ).fetchone()[0]
                if other_admins < 1:
                    raise ValueError("系统必须至少保留一个启用的系统管理员")

            connection.execute(
                """
                UPDATE users
                SET display_name = COALESCE(?, display_name),
                    status = COALESCE(?, status),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (display_name, status, user_id),
            )
            if unique_role_ids is not None:
                connection.execute(
                    "DELETE FROM user_roles WHERE user_id = ?", (user_id,)
                )
                connection.executemany(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    [(user_id, role_id) for role_id in unique_role_ids],
                )
            if resulting_status == "disabled":
                connection.execute(
                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (user_id,),
                )
        user = self.get_user(user_id)
        assert user is not None
        return user

    def replace_user_roles(
        self, user_id: str, role_ids: list[str]
    ) -> list[str]:
        user = self.update_user(user_id, role_ids=role_ids)
        return user["role_codes"]

    def user_role_codes(self, user_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.code FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id
                WHERE ur.user_id = ? ORDER BY r.code
                """,
                (user_id,),
            ).fetchall()
        return [row["code"] for row in rows]

    def count_active_system_admins(
        self,
        excluding_user_id: str | None = None,
        removing_role_id: str | None = None,
    ) -> int:
        conditions = [
            "u.status = 'active'",
            "u.is_deleted = 0",
            "r.code = 'system_admin'",
            "r.is_enabled = 1",
        ]
        parameters: list[str] = []
        if excluding_user_id is not None and removing_role_id is None:
            conditions.append("u.id <> ?")
            parameters.append(excluding_user_id)
        elif excluding_user_id is not None and removing_role_id is not None:
            conditions.append("NOT (u.id = ? AND r.id = ?)")
            parameters.extend((excluding_user_id, removing_role_id))

        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(DISTINCT u.id)
                FROM users AS u
                JOIN user_roles AS ur ON ur.user_id = u.id
                JOIN roles AS r ON r.id = ur.role_id
                WHERE {' AND '.join(conditions)}
                """,
                parameters,
            ).fetchone()
        return int(row[0])

    def assert_admin_survives(
        self, user_id: str, removing_role_id: str | None = None
    ) -> None:
        if (
            self.count_active_system_admins(
                excluding_user_id=user_id, removing_role_id=removing_role_id
            )
            < 1
        ):
            raise ValueError("系统必须至少保留一个启用的系统管理员")

    def disable_user(self, user_id: str) -> None:
        self.update_user(user_id, status="disabled")

    @staticmethod
    def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
        role = dict(row)
        role["is_system"] = bool(role["is_system"])
        role["is_enabled"] = bool(role["is_enabled"])
        return role

    def _user_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        user = dict(row)
        user["role_codes"] = self.user_role_codes(user["id"])
        user.pop("password_hash", None)
        user.pop("normalized_username", None)
        user.pop("is_deleted", None)
        user.pop("updated_at", None)
        return user
