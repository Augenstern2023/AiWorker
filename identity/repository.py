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
import json
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
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
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
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
    created_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
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
CREATE TABLE IF NOT EXISTS role_claimable_task_types (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    PRIMARY KEY (role_id, task_type)
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
    predecessor_task_id TEXT REFERENCES workflow_tasks(id) ON DELETE SET NULL,
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
CREATE TABLE IF NOT EXISTS workflow_submissions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_submission_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'submitted', 'approved', 'returned', 'superseded')
    ),
    content_snapshot_json TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    created_by TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at REAL,
    reviewed_by TEXT,
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(resource_type, resource_id, version)
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
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    actor_display_name_snapshot TEXT,
    actor_role_snapshot_json TEXT NOT NULL DEFAULT '[]',
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    target_type TEXT,
    resource_id TEXT,
    target_id TEXT,
    project_id TEXT,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    before_summary_json TEXT,
    after_summary_json TEXT,
    request_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
    result TEXT NOT NULL DEFAULT 'success',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_project_members_project_id ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pool ON workflow_tasks(role_code, status);
CREATE INDEX IF NOT EXISTS idx_workflow_submissions_task
ON workflow_submissions(task_id, status);
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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a serialized transaction shared by identity and audit writes."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    @contextmanager
    def connection_scope(
        self, connection: sqlite3.Connection | None
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with self.connect() as owned_connection:
            yield owned_connection

    def _init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._ensure_team_management_schema(connection)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(workflow_tasks)")
            }
            if "predecessor_task_id" not in columns:
                connection.execute(
                    "ALTER TABLE workflow_tasks ADD COLUMN predecessor_task_id TEXT"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_task_transition
                ON workflow_tasks(workflow_id, task_type, predecessor_task_id)
                WHERE predecessor_task_id IS NOT NULL
                """
            )

    @staticmethod
    def _ensure_team_management_schema(connection: sqlite3.Connection) -> None:
        migrations = {
            "users": (
                ("created_by_user_id", "TEXT REFERENCES users(id) ON DELETE SET NULL"),
                ("deleted_at", "TEXT"),
                ("deleted_by_user_id", "TEXT REFERENCES users(id) ON DELETE SET NULL"),
            ),
            "roles": (
                ("is_deleted", "INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1))"),
                ("created_by_user_id", "TEXT REFERENCES users(id) ON DELETE SET NULL"),
                ("deleted_at", "TEXT"),
                ("deleted_by_user_id", "TEXT REFERENCES users(id) ON DELETE SET NULL"),
            ),
        }
        for table, definitions in migrations.items():
            existing = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            for column, definition in definitions:
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )

        owner_role = connection.execute(
            "SELECT id FROM roles WHERE code = 'project_owner' COLLATE NOCASE"
        ).fetchone()
        if owner_role is not None:
            bindings = connection.execute(
                """
                SELECT ur.user_id
                FROM user_roles AS ur
                JOIN users AS u ON u.id = ur.user_id
                WHERE ur.role_id = ?
                ORDER BY u.created_at, u.id
                """,
                (owner_role["id"],),
            ).fetchall()
            if len(bindings) > 1:
                retained = bindings[0]["user_id"]
                removed = [row["user_id"] for row in bindings[1:]]
                connection.executemany(
                    "DELETE FROM user_roles WHERE user_id = ? AND role_id = ?",
                    [(user_id, owner_role["id"]) for user_id in removed],
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO audit_logs
                        (id, action, resource_type, resource_id, details_json)
                    VALUES (?, 'role.project_owner.uniqueness_migration',
                            'role', ?, ?)
                    """,
                    (
                        "migration:project_owner_uniqueness",
                        owner_role["id"],
                        json.dumps(
                            {"retained_user_id": retained, "removed_user_ids": removed},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                )

        connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_single_project_owner_insert
            BEFORE INSERT ON user_roles
            WHEN (SELECT code FROM roles WHERE id = NEW.role_id) = 'project_owner'
              AND EXISTS (
                  SELECT 1 FROM user_roles AS ur
                  JOIN roles AS r ON r.id = ur.role_id
                  WHERE r.code = 'project_owner'
              )
            BEGIN
                SELECT RAISE(ABORT, '项目负责人只能有一名');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_single_project_owner_update
            BEFORE UPDATE OF role_id ON user_roles
            WHEN (SELECT code FROM roles WHERE id = NEW.role_id) = 'project_owner'
              AND EXISTS (
                  SELECT 1 FROM user_roles AS ur
                  JOIN roles AS r ON r.id = ur.role_id
                  WHERE r.code = 'project_owner'
                    AND NOT (ur.user_id = OLD.user_id AND ur.role_id = OLD.role_id)
              )
            BEGIN
                SELECT RAISE(ABORT, '项目负责人只能有一名');
            END;
            """
        )

    def seed_defaults(self) -> None:
        with self.connect() as connection:
            retired_permissions = ("version." + "approve", "version." + "return")
            connection.executemany(
                "DELETE FROM role_permissions WHERE permission_code = ?",
                [(code,) for code in retired_permissions],
            )
            connection.executemany(
                "DELETE FROM permission_dependencies WHERE permission_code = ? OR depends_on_code = ?",
                [(code, code) for code in retired_permissions],
            )
            connection.executemany(
                "DELETE FROM permissions WHERE code = ?",
                [(code,) for code in retired_permissions],
            )
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
            rows = connection.execute(
                "SELECT * FROM roles WHERE is_deleted = 0 ORDER BY code"
            ).fetchall()
        return [self._role_dict(row) for row in rows]

    def get_role_by_code(self, code: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM roles WHERE code = ? COLLATE NOCASE AND is_deleted = 0",
                (code,),
            ).fetchone()
        return self._role_dict(row) if row else None

    def get_role(
        self, role_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        with self.connection_scope(connection) as active_connection:
            row = active_connection.execute(
                "SELECT * FROM roles WHERE id = ? AND is_deleted = 0", (role_id,)
            ).fetchone()
        return self._role_dict(row) if row else None

    def create_role(
        self,
        code: str,
        name: str,
        description: str,
        *,
        is_system: bool = False,
        created_by_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        role_id = str(uuid.uuid4())
        with self.connection_scope(connection) as active_connection:
            active_connection.execute(
                """
                INSERT INTO roles
                    (id, code, name, description, is_system, created_by_user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    role_id,
                    code,
                    name,
                    description,
                    int(is_system),
                    created_by_user_id,
                ),
            )
        role = self.get_role(role_id, connection=connection)
        assert role is not None
        return role

    def update_role(
        self,
        role_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            role = active_connection.execute(
                "SELECT * FROM roles WHERE id = ?", (role_id,)
            ).fetchone()
            if role is None:
                raise KeyError(role_id)
            if (
                role["code"].casefold() == "system_admin"
                and role["is_enabled"]
                and is_enabled is False
            ):
                active_admins = active_connection.execute(
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
            active_connection.execute(
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
        updated = self.get_role(role_id, connection=connection)
        assert updated is not None
        return updated

    def list_role_permissions(
        self, role_id: str, *, connection: sqlite3.Connection | None = None
    ) -> list[dict[str, str]]:
        with self.connection_scope(connection) as active_connection:
            rows = active_connection.execute(
                """
                SELECT permission_code, data_scope
                FROM role_permissions
                WHERE role_id = ?
                ORDER BY permission_code
                """,
                (role_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_role_claimable_task_types(
        self, role_id: str, *, connection: sqlite3.Connection | None = None
    ) -> list[str]:
        with self.connection_scope(connection) as active_connection:
            rows = active_connection.execute(
                "SELECT task_type FROM role_claimable_task_types "
                "WHERE role_id = ? ORDER BY task_type",
                (role_id,),
            ).fetchall()
        return [str(row["task_type"]) for row in rows]

    def replace_role_claimable_task_types(
        self,
        role_id: str,
        task_types: list[str] | tuple[str, ...],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        normalized = sorted({str(item).strip() for item in task_types if str(item).strip()})
        with self.connection_scope(connection) as active_connection:
            if self.get_role(role_id, connection=active_connection) is None:
                raise KeyError(role_id)
            active_connection.execute(
                "DELETE FROM role_claimable_task_types WHERE role_id = ?", (role_id,)
            )
            active_connection.executemany(
                "INSERT INTO role_claimable_task_types (role_id, task_type) VALUES (?, ?)",
                [(role_id, task_type) for task_type in normalized],
            )

    def replace_role_permissions(
        self,
        role_id: str,
        permissions: list[tuple[str, str]],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, str]]:
        expanded = self._expanded_permissions(tuple(permissions))
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            if active_connection.execute(
                "SELECT 1 FROM roles WHERE id = ?", (role_id,)
            ).fetchone() is None:
                raise KeyError(role_id)
            known_permissions = {
                row["code"]
                for row in active_connection.execute(
                    "SELECT code FROM permissions"
                ).fetchall()
            }
            unknown = {code for code, _ in expanded} - known_permissions
            if unknown:
                raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
            active_connection.execute(
                "DELETE FROM role_permissions WHERE role_id = ?", (role_id,)
            )
            active_connection.executemany(
                """
                INSERT INTO role_permissions (role_id, permission_code, data_scope)
                VALUES (?, ?, ?)
                """,
                [(role_id, code, scope) for code, scope in expanded],
            )
        return self.list_role_permissions(role_id, connection=connection)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users WHERE is_deleted = 0 ORDER BY normalized_username"
            ).fetchall()
        return [self._user_dict(row) for row in rows]

    def get_user(
        self, user_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        with self.connection_scope(connection) as active_connection:
            row = active_connection.execute(
                "SELECT * FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
            ).fetchone()
        return self._user_dict(row, connection=connection) if row else None

    def create_user(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        *,
        created_by_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        user_id = str(uuid.uuid4())
        with self.connection_scope(connection) as active_connection:
            active_connection.execute(
                """
                INSERT INTO users
                    (id, username, normalized_username, display_name, password_hash,
                     created_by_user_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    username.casefold(),
                    display_name,
                    password_hash,
                    created_by_user_id,
                ),
            )
        user = self.get_user(user_id, connection=connection)
        assert user is not None
        return user

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        role_ids: list[str] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        unique_role_ids = (
            list(dict.fromkeys(role_ids)) if role_ids is not None else None
        )
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            user = active_connection.execute(
                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["is_deleted"]:
                raise KeyError(user_id)

            roles: list[sqlite3.Row] | None = None
            if unique_role_ids is not None:
                role_placeholders = ",".join("?" for _ in unique_role_ids)
                roles = active_connection.execute(
                    f"SELECT id, code, is_enabled FROM roles "
                    f"WHERE id IN ({role_placeholders})"
                    if unique_role_ids
                    else "SELECT id, code, is_enabled FROM roles WHERE 0",
                    unique_role_ids,
                ).fetchall()
                if len(roles) != len(unique_role_ids):
                    raise ValueError("角色不存在")

            currently_admin = active_connection.execute(
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
                other_admins = active_connection.execute(
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

            active_connection.execute(
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
                active_connection.execute(
                    "DELETE FROM user_roles WHERE user_id = ?", (user_id,)
                )
                active_connection.executemany(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                    [(user_id, role_id) for role_id in unique_role_ids],
                )
            if resulting_status == "disabled":
                active_connection.execute(
                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (user_id,),
                )
        user = self.get_user(user_id, connection=connection)
        assert user is not None
        return user

    def replace_user_roles(
        self,
        user_id: str,
        role_ids: list[str],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[str]:
        try:
            user = self.update_user(
                user_id, role_ids=role_ids, connection=connection
            )
        except sqlite3.IntegrityError as exc:
            if "项目负责人只能有一名" in str(exc):
                raise ValueError("项目负责人只能有一名") from exc
            raise
        return user["role_codes"]

    def assign_project_owner(
        self,
        user_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            target = active_connection.execute(
                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if target is None or target["is_deleted"]:
                raise KeyError(user_id)
            if target["status"] != "active":
                raise ValueError("只能指定启用中的账号为项目负责人")
            role = active_connection.execute(
                "SELECT id FROM roles WHERE code = 'project_owner' COLLATE NOCASE "
                "AND is_enabled = 1 AND is_deleted = 0"
            ).fetchone()
            if role is None:
                raise ValueError("项目负责人角色不可用")
            current = active_connection.execute(
                """
                SELECT u.id, u.status, u.is_deleted
                FROM user_roles AS ur
                JOIN users AS u ON u.id = ur.user_id
                WHERE ur.role_id = ?
                """,
                (role["id"],),
            ).fetchone()
            if current is not None and current["id"] == user_id:
                result = self.get_user(user_id, connection=active_connection)
                assert result is not None
                return result
            if (
                current is not None
                and current["status"] == "active"
                and not current["is_deleted"]
            ):
                raise ValueError("请先停用当前项目负责人")
            active_connection.execute(
                "DELETE FROM user_roles WHERE role_id = ?", (role["id"],)
            )
            active_connection.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role["id"]),
            )
        result = self.get_user(user_id, connection=connection)
        assert result is not None
        return result

    def soft_delete_user(
        self,
        user_id: str,
        *,
        deleted_by_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            user = active_connection.execute(
                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["is_deleted"]:
                raise KeyError(user_id)
            self.update_user(user_id, status="disabled", connection=active_connection)
            active_connection.execute(
                """
                UPDATE users
                SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP,
                    deleted_by_user_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (deleted_by_user_id, user_id),
            )
            active_connection.execute(
                "DELETE FROM user_roles WHERE user_id = ?", (user_id,)
            )
            active_connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
            active_connection.execute(
                "UPDATE project_members SET status = 'inactive', "
                "updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            active_connection.execute(
                """
                UPDATE workflow_tasks
                SET assignee_id = NULL, assigned_by = NULL, status = 'pool',
                    updated_at = CURRENT_TIMESTAMP
                WHERE assignee_id = ?
                  AND status IN ('assigned', 'in_progress', 'returned')
                """,
                (user_id,),
            )

    def soft_delete_role(
        self,
        role_id: str,
        *,
        deleted_by_user_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        with self.connection_scope(connection) as active_connection:
            if connection is None:
                active_connection.execute("BEGIN IMMEDIATE")
            role = active_connection.execute(
                "SELECT is_system, is_deleted FROM roles WHERE id = ?", (role_id,)
            ).fetchone()
            if role is None or role["is_deleted"]:
                raise KeyError(role_id)
            if role["is_system"]:
                raise ValueError("系统角色不能删除")
            active_connection.execute(
                "DELETE FROM user_roles WHERE role_id = ?", (role_id,)
            )
            active_connection.execute(
                """
                UPDATE roles
                SET is_enabled = 0, is_deleted = 1,
                    deleted_at = CURRENT_TIMESTAMP, deleted_by_user_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (deleted_by_user_id, role_id),
            )

    def user_role_codes(
        self, user_id: str, *, connection: sqlite3.Connection | None = None
    ) -> list[str]:
        with self.connection_scope(connection) as active_connection:
            rows = active_connection.execute(
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
        role["is_deleted"] = bool(role.get("is_deleted", 0))
        return role

    def _user_dict(
        self, row: sqlite3.Row, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any]:
        user = dict(row)
        user["role_codes"] = self.user_role_codes(
            user["id"], connection=connection
        )
        user.pop("password_hash", None)
        user.pop("normalized_username", None)
        user.pop("is_deleted", None)
        user.pop("updated_at", None)
        return user
