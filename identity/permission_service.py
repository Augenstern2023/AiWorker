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

from .permission_catalog import PERMISSION_DEPENDENCIES
from .repository import IdentityRepository
from .schema import DATA_SCOPE_ORDER, Principal
from .session_service import SessionService


_MANAGED_PROJECT_ROLES = frozenset(
    {"owner", "project_owner", "manager", "project_manager"}
)


class PermissionService:
    def __init__(
        self, repository: IdentityRepository, session_service: SessionService
    ) -> None:
        self.repository = repository
        self.session_service = session_service

    def principal_for_session(self, raw_token: str | None) -> Principal | None:
        context = self.session_service.resolve_session(raw_token)
        if context is None:
            return None
        return self.principal_for_user(
            context.principal.user_id, context.principal.session_id
        )

    def principal_for_user(
        self,
        user_id: str,
        session_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Principal | None:
        with self.repository.connection_scope(connection) as active_connection:
            user = active_connection.execute(
                """
                SELECT id, username, display_name, status, is_deleted
                FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if user is None or user["status"] != "active" or user["is_deleted"]:
                return None
            rows = active_connection.execute(
                """
                SELECT r.code, rp.permission_code, rp.data_scope
                FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
                WHERE ur.user_id = ?
                ORDER BY r.code, rp.permission_code
                """,
                (user_id,),
            ).fetchall()

        role_codes = tuple(sorted({row["code"] for row in rows}))
        grants = [
            (row["permission_code"], row["data_scope"])
            for row in rows
            if row["permission_code"] is not None
        ]
        expanded = self.expand_permissions(grants)
        if "*" in expanded:
            expanded = {"*": "all"}
        data_scope = (
            max(expanded.values(), key=DATA_SCOPE_ORDER.get) if expanded else "task"
        )
        return Principal(
            user_id=user["id"],
            username=user["username"],
            display_name=user["display_name"],
            role_codes=role_codes,
            permissions=frozenset(expanded),
            data_scope=data_scope,
            session_id=session_id,
        )

    @staticmethod
    def expand_permissions(
        grants: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    ) -> dict[str, str]:
        expanded: dict[str, str] = {}
        pending = sorted(grants, reverse=True)
        while pending:
            code, scope = pending.pop()
            current = expanded.get(code)
            if current is not None and DATA_SCOPE_ORDER[current] >= DATA_SCOPE_ORDER[scope]:
                continue
            expanded[code] = scope
            for dependency in sorted(PERMISSION_DEPENDENCIES.get(code, ())):
                pending.append((dependency, scope))
        return dict(sorted(expanded.items()))

    def can_access_task(self, principal: Principal, task_id: str) -> bool:
        with self.repository.connect() as connection:
            task = connection.execute(
                "SELECT assignee_id, project_id FROM workflow_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if task is None:
            return False
        if principal.data_scope == "all":
            return True
        if principal.data_scope == "task":
            return task["assignee_id"] == principal.user_id
        if not task["project_id"]:
            return task["assignee_id"] == principal.user_id
        if principal.data_scope == "project":
            return self.can_access_assigned_project(principal, task["project_id"])
        return self.can_access_managed_project(principal, task["project_id"])

    def can_access_assigned_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        # The single global project-owner role is responsible for the complete
        # portfolio. Membership records still describe local collaborators,
        # but must not hide a project from that global owner.
        if principal.data_scope == "all" or "project_owner" in principal.role_codes:
            return True
        if DATA_SCOPE_ORDER[principal.data_scope] < DATA_SCOPE_ORDER["project"]:
            return False
        with self.repository.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM project_members
                WHERE project_id = ? AND user_id = ? AND status = 'active'
                """,
                (project_id, principal.user_id),
            ).fetchone()
        return row is not None

    def can_access_managed_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        if principal.data_scope == "all" or "project_owner" in principal.role_codes:
            return True
        if DATA_SCOPE_ORDER[principal.data_scope] < DATA_SCOPE_ORDER["managed_projects"]:
            return False
        with self.repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT project_role FROM project_members
                WHERE project_id = ? AND user_id = ? AND status = 'active'
                """,
                (project_id, principal.user_id),
            ).fetchall()
        return any(row["project_role"] in _MANAGED_PROJECT_ROLES for row in rows)

    @staticmethod
    def can_access_all(principal: Principal) -> bool:
        return principal.data_scope == "all"
