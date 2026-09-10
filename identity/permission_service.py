# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from sqlalchemy import Connection, and_, select

from system_database.schema import (
    project_members,
    role_permissions,
    roles,
    user_roles,
    users,
    workflow_tasks,
)

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
        connection: Connection | None = None,
    ) -> Principal | None:
        with self.repository.connection_scope(connection) as active_connection:
            user = active_connection.execute(
                select(
                    users.c.id,
                    users.c.username,
                    users.c.display_name,
                    users.c.status,
                    users.c.is_deleted,
                ).where(users.c.id == user_id)
            ).mappings().first()
            if user is None or user["status"] != "active" or user["is_deleted"]:
                return None
            rows = active_connection.execute(
                select(
                    roles.c.code,
                    role_permissions.c.permission_code,
                    role_permissions.c.data_scope,
                )
                .select_from(
                    user_roles.join(
                        roles,
                        and_(
                            roles.c.id == user_roles.c.role_id,
                            roles.c.is_enabled == 1,
                        ),
                    ).outerjoin(
                        role_permissions,
                        role_permissions.c.role_id == roles.c.id,
                    )
                )
                .where(user_roles.c.user_id == user_id)
                .order_by(roles.c.code, role_permissions.c.permission_code)
            ).mappings().all()

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
                select(workflow_tasks.c.assignee_id, workflow_tasks.c.project_id).where(
                    workflow_tasks.c.id == task_id
                )
            ).mappings().first()
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
                select(project_members.c.id).where(
                    project_members.c.project_id == project_id,
                    project_members.c.user_id == principal.user_id,
                    project_members.c.status == "active",
                )
            ).mappings().first()
        return row is not None

    def can_read_project(self, principal: Principal, project_id: str) -> bool:
        """Allow portfolio read access without granting assigned-project mutations."""
        if principal.data_scope == "all" or "project_owner" in principal.role_codes:
            return True
        if "project.read.established" in principal.permissions:
            return True
        return self.can_access_assigned_project(
            principal, project_id
        ) or self.can_access_managed_project(principal, project_id)

    def can_access_managed_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        if principal.data_scope == "all" or "project_owner" in principal.role_codes:
            return True
        if DATA_SCOPE_ORDER[principal.data_scope] < DATA_SCOPE_ORDER["managed_projects"]:
            return False
        with self.repository.connect() as connection:
            rows = connection.execute(
                select(project_members.c.project_role).where(
                    project_members.c.project_id == project_id,
                    project_members.c.user_id == principal.user_id,
                    project_members.c.status == "active",
                )
            ).mappings().all()
        return any(row["project_role"] in _MANAGED_PROJECT_ROLES for row in rows)

    def can_participate_in_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        return self.can_access_assigned_project(
            principal, project_id
        ) or self.can_access_managed_project(principal, project_id)

    def can_create_project_content(
        self, principal: Principal, project_id: str, *, active_member: bool
    ) -> bool:
        return active_member or self.can_access_managed_project(principal, project_id)

    def can_govern_project(self, principal: Principal, project_id: str) -> bool:
        return principal.is_system_admin or (
            "project.member.manage" in principal.permissions
            and self.can_access_managed_project(principal, project_id)
        )

    def can_delete_project(self, principal: Principal, project_id: str) -> bool:
        return principal.is_system_admin or (
            "project.delete" in principal.permissions
            and self.can_access_managed_project(principal, project_id)
        )

    @staticmethod
    def can_access_all(principal: Principal) -> bool:
        return principal.data_scope == "all"
