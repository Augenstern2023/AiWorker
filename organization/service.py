# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, exists, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection

from system_database import SystemDatabase
from system_database.schema import departments, project_members, user_departments, users


class DepartmentService:
    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def create_department(
        self, name: str, *, parent_id: str | None = None, sort_order: int = 0
    ) -> dict[str, Any]:
        normalized_name = self._normalize_name(name)
        with self.database.transaction() as connection:
            if parent_id is not None:
                self._require_department(connection, parent_id)
            department_id = str(uuid.uuid4())
            connection.execute(
                insert(departments).values(
                    id=department_id,
                    name=normalized_name,
                    parent_id=parent_id,
                    sort_order=sort_order,
                )
            )
            return self._department_dict(
                self._require_department(connection, department_id)
            )

    def rename_department(self, department_id: str, name: str) -> dict[str, Any]:
        normalized_name = self._normalize_name(name)
        with self.database.transaction() as connection:
            self._require_department(connection, department_id)
            connection.execute(
                update(departments)
                .where(departments.c.id == department_id)
                .values(name=normalized_name)
            )
            return self._department_dict(
                self._require_department(connection, department_id)
            )

    def move_department(
        self, department_id: str, parent_id: str | None
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            self._require_department(connection, department_id)
            if parent_id == department_id:
                raise ValueError("不能将部门移动到自身")
            if parent_id is not None:
                self._assert_not_descendant(connection, department_id, parent_id)
            connection.execute(
                update(departments)
                .where(departments.c.id == department_id)
                .values(parent_id=parent_id)
            )
            return self._department_dict(
                self._require_department(connection, department_id)
            )

    def disable_department(self, department_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            self._require_department(connection, department_id)
            self._require_empty_department(connection, department_id, "停用")
            connection.execute(
                update(departments)
                .where(departments.c.id == department_id)
                .values(status="disabled")
            )
            return self._department_dict(
                self._require_department(connection, department_id)
            )

    def delete_department(self, department_id: str) -> None:
        with self.database.transaction() as connection:
            self._require_department(connection, department_id)
            self._require_empty_department(connection, department_id, "删除")
            connection.execute(delete(departments).where(departments.c.id == department_id))

    def list_tree(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(departments).order_by(
                    departments.c.sort_order,
                    departments.c.created_at,
                    departments.c.id,
                )
            ).mappings()
            nodes = {
                row["id"]: {**self._department_dict(row), "children": []}
                for row in rows
            }
        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent_id = node["parent_id"]
            if parent_id is None:
                roots.append(node)
            else:
                nodes[parent_id]["children"].append(node)
        return roots

    def get_department(self, department_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                select(departments).where(departments.c.id == department_id)
            ).mappings().one_or_none()
        return self._department_dict(row) if row is not None else None

    def assign_user_department(
        self, user_id: str, department_id: str, *, is_primary: bool = False
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            is_active_user = connection.execute(
                select(
                    exists().where(
                        users.c.id == user_id,
                        users.c.status == "active",
                        users.c.is_deleted == 0,
                    )
                )
            ).scalar_one()
            if not is_active_user:
                raise ValueError("用户不存在或已停用")
            department = self._require_department(connection, department_id)
            if department["status"] != "active":
                raise ValueError("已停用部门不能新增归属")
            if is_primary:
                connection.execute(
                    update(user_departments)
                    .where(user_departments.c.user_id == user_id)
                    .values(is_primary=False)
                )
            connection.execute(
                postgresql_insert(user_departments)
                .values(
                    user_id=user_id,
                    department_id=department_id,
                    is_primary=is_primary,
                )
                .on_conflict_do_update(
                    index_elements=[
                        user_departments.c.user_id,
                        user_departments.c.department_id,
                    ],
                    set_={"is_primary": is_primary},
                )
            )
            row = connection.execute(
                select(user_departments).where(
                    user_departments.c.user_id == user_id,
                    user_departments.c.department_id == department_id,
                )
            ).mappings().one()
            return dict(row)

    def list_user_departments(self, user_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(
                    user_departments.c.user_id,
                    user_departments.c.department_id,
                    user_departments.c.is_primary,
                    user_departments.c.created_at,
                    departments.c.name.label("department_name"),
                    departments.c.status.label("department_status"),
                    departments.c.sort_order,
                )
                .join(departments, departments.c.id == user_departments.c.department_id)
                .where(user_departments.c.user_id == user_id)
                .order_by(
                    departments.c.sort_order,
                    departments.c.created_at,
                    departments.c.id,
                )
            ).mappings()
        return [dict(row) for row in rows]

    def remove_user_department(
        self, user_id: str, department_id: str
    ) -> dict[str, Any]:
        """Remove one membership and keep a remaining membership primary."""

        with self.database.transaction() as connection:
            assignment = connection.execute(
                select(user_departments).where(
                    user_departments.c.user_id == user_id,
                    user_departments.c.department_id == department_id,
                )
            ).mappings().one_or_none()
            if assignment is None:
                raise KeyError(f"{user_id}:{department_id}")
            connection.execute(
                delete(user_departments).where(
                    user_departments.c.user_id == user_id,
                    user_departments.c.department_id == department_id,
                )
            )
            if assignment["is_primary"]:
                replacement = connection.execute(
                    select(user_departments.c.department_id)
                    .where(user_departments.c.user_id == user_id)
                    .order_by(
                        user_departments.c.created_at,
                        user_departments.c.department_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if replacement is not None:
                    connection.execute(
                        update(user_departments)
                        .where(
                            user_departments.c.user_id == user_id,
                            user_departments.c.department_id == replacement,
                        )
                        .values(is_primary=True)
                    )
        return dict(assignment)

    def list_project_member_roles(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(
                    project_members.c.id,
                    project_members.c.user_id,
                    project_members.c.project_role,
                    project_members.c.status,
                )
                .where(project_members.c.project_id == project_id)
                .order_by(project_members.c.created_at, project_members.c.id)
            ).mappings()
            return [dict(row) for row in rows]

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ValueError("部门名称不能为空")
        return normalized

    @staticmethod
    def _department_dict(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _require_department(connection: Connection, department_id: str) -> Any:
        row = connection.execute(
            select(departments).where(departments.c.id == department_id)
        ).mappings().one_or_none()
        if row is None:
            raise ValueError("部门不存在")
        return row

    @staticmethod
    def _require_empty_department(
        connection: Connection, department_id: str, operation: str
    ) -> None:
        has_children = connection.execute(
            select(exists().where(departments.c.parent_id == department_id))
        ).scalar_one()
        has_assignments = connection.execute(
            select(exists().where(user_departments.c.department_id == department_id))
        ).scalar_one()
        if has_children or has_assignments:
            raise ValueError(f"非空部门不能{operation}")

    def _assert_not_descendant(
        self, connection: Connection, department_id: str, parent_id: str
    ) -> None:
        current_id: str | None = parent_id
        while current_id is not None:
            if current_id == department_id:
                raise ValueError("不能将部门移动到后代部门")
            current = self._require_department(connection, current_id)
            current_id = current["parent_id"]
