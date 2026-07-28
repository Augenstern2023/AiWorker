# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Connection, and_, delete, exists, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from system_database import SystemDatabase
from system_database.schema import (
    permission_dependencies,
    permissions,
    role_claimable_task_types,
    role_permissions,
    roles,
    sessions,
    departments,
    user_departments,
    user_roles,
    users,
)

from .permission_catalog import DEFAULT_ROLES, PERMISSIONS, PERMISSION_DEPENDENCIES


class IdentityRepository:
    def __init__(self, system_database: SystemDatabase) -> None:
        self._system_database = system_database

    @property
    def database(self) -> SystemDatabase:
        return self._system_database

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self._system_database.transaction() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._system_database.transaction() as connection:
            yield connection

    @contextmanager
    def connection_scope(
        self, connection: Connection | None
    ) -> Iterator[Connection]:
        if connection is not None:
            yield connection
            return
        with self.connect() as owned_connection:
            yield owned_connection

    # ------------------------------------------------------------------ seed

    def seed_defaults(self) -> None:
        with self.transaction() as connection:
            retired_permissions = ("version.approve", "version.return")
            connection.execute(
                delete(role_permissions).where(
                    role_permissions.c.permission_code.in_(retired_permissions)
                )
            )
            connection.execute(
                delete(permission_dependencies).where(
                    or_(
                        permission_dependencies.c.permission_code.in_(retired_permissions),
                        permission_dependencies.c.depends_on_code.in_(retired_permissions),
                    )
                )
            )
            connection.execute(
                delete(permissions).where(
                    permissions.c.code.in_(retired_permissions)
                )
            )
            for code, label in PERMISSIONS.items():
                connection.execute(
                    pg_insert(permissions)
                    .values(code=code, label=label)
                    .on_conflict_do_update(
                        index_elements=[permissions.c.code],
                        set_={"label": permissions.c.code, "updated_at": text("CURRENT_TIMESTAMP")},
                    )
                )

            # Dependency edges belong to the system catalog; reseeding makes
            # this table an exact projection.
            connection.execute(delete(permission_dependencies))
            for permission_code, dependencies in PERMISSION_DEPENDENCIES.items():
                for depends_on_code in dependencies:
                    connection.execute(
                        pg_insert(permission_dependencies)
                        .values(permission_code=permission_code, depends_on_code=depends_on_code)
                        .on_conflict_do_nothing()
                    )

            for role in DEFAULT_ROLES:
                role_id = f"role:{role.code}"
                connection.execute(
                    pg_insert(roles)
                    .values(
                        id=role_id,
                        code=role.code,
                        name=role.name,
                        description=role.description,
                        is_system=int(role.is_system),
                    )
                    .on_conflict_do_update(
                        index_elements=[func.lower(roles.c.code)],
                        set_={
                            "name": role.name,
                            "description": role.description,
                            "is_system": int(role.is_system),
                            "updated_at": text("CURRENT_TIMESTAMP"),
                        },
                    )
                )
                stored_role_id = connection.execute(
                    select(roles.c.id).where(
                        func.lower(roles.c.code) == role.code.lower()
                    )
                ).scalar_one()
                # Default-role grants are system-owned.
                connection.execute(
                    delete(role_permissions).where(
                        role_permissions.c.role_id == stored_role_id
                    )
                )
                for permission_code, data_scope in self._expanded_permissions(role.permissions):
                    connection.execute(
                        pg_insert(role_permissions)
                        .values(role_id=stored_role_id, permission_code=permission_code, data_scope=data_scope)
                        .on_conflict_do_update(
                            index_elements=["role_id", "permission_code"],
                            set_={"data_scope": data_scope},
                        )
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

    # ------------------------------------------------------------------ roles

    def list_roles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                select(roles).where(roles.c.is_deleted == 0).order_by(roles.c.code)
            ).mappings().all()
        return [self._role_dict(row) for row in rows]

    def get_role_by_code(self, code: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                select(roles).where(
                    and_(
                        func.lower(roles.c.code) == code.lower(),
                        roles.c.is_deleted == 0,
                    )
                )
            ).mappings().first()
        return self._role_dict(row) if row else None

    def get_role(
        self, role_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        with self.connection_scope(connection) as conn:
            row = conn.execute(
                select(roles).where(
                    and_(roles.c.id == role_id, roles.c.is_deleted == 0)
                )
            ).mappings().first()
        return self._role_dict(row) if row else None

    def create_role(
        self,
        code: str,
        name: str,
        description: str,
        *,
        is_system: bool = False,
        created_by_user_id: str | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        role_id = str(uuid.uuid4())
        with self.connection_scope(connection) as conn:
            conn.execute(
                insert(roles).values(
                    id=role_id,
                    code=code,
                    name=name,
                    description=description,
                    is_system=int(is_system),
                    created_by_user_id=created_by_user_id,
                )
            )
        result = self.get_role(role_id, connection=connection)
        assert result is not None
        return result

    def update_role(
        self,
        role_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        with self.connection_scope(connection) as conn:
            role = conn.execute(
                select(roles).where(roles.c.id == role_id)
            ).mappings().first()
            if role is None:
                raise KeyError(role_id)
            if (
                role["code"].casefold() == "system_admin"
                and role["is_enabled"]
                and is_enabled is False
            ):
                admin_count = conn.execute(
                    select(func.count(func.distinct(users.c.id)))
                    .select_from(
                        users.join(user_roles, user_roles.c.user_id == users.c.id)
                    )
                    .where(
                        and_(
                            user_roles.c.role_id == role_id,
                            users.c.status == "active",
                            users.c.is_deleted == 0,
                        )
                    )
                ).scalar_one()
                if admin_count:
                    raise ValueError("系统必须至少保留一个启用的系统管理员")

            set_values = {"updated_at": text("CURRENT_TIMESTAMP")}
            if name is not None:
                set_values["name"] = name
            if description is not None:
                set_values["description"] = description
            if is_enabled is not None:
                set_values["is_enabled"] = int(is_enabled)
            conn.execute(
                update(roles).where(roles.c.id == role_id).values(**set_values)
            )
        updated = self.get_role(role_id, connection=connection)
        assert updated is not None
        return updated
    def list_role_permissions(
        self, role_id: str, *, connection=None
    ):
        with self.connection_scope(connection) as conn:
            rows = conn.execute(
                select(
                    role_permissions.c.permission_code,
                    role_permissions.c.data_scope,
                )
                .where(role_permissions.c.role_id == role_id)
                .order_by(role_permissions.c.permission_code)
            ).mappings().all()
        return [dict(r) for r in rows]

    def list_role_claimable_task_types(self, role_id: str, *, connection=None):
        from system_database.schema import role_claimable_task_types as t
        with self.connection_scope(connection) as conn:
            rows = conn.execute(
                __import__('sqlalchemy').select(t.c.task_type)
                .where(t.c.role_id == role_id)
                .order_by(t.c.task_type)
            ).mappings().all()
        return [str(r['task_type']) for r in rows]

    def replace_role_claimable_task_types(
        self, role_id: str, task_types, *, connection=None
    ):
        from system_database.schema import role_claimable_task_types as t
        S = __import__('sqlalchemy')
        normalized = sorted({str(item).strip() for item in task_types if str(item).strip()})
        with self.connection_scope(connection) as conn:
            if self.get_role(role_id, connection=conn) is None:
                raise KeyError(role_id)
            conn.execute(S.delete(t).where(t.c.role_id == role_id))
            for tt in normalized:
                conn.execute(S.insert(t).values(role_id=role_id, task_type=tt))

    def replace_role_permissions(self, role_id: str, permissions, *, connection=None):
        from system_database.schema import role_permissions as rp, permissions as p, roles as r
        S = __import__('sqlalchemy')
        expanded = self._expanded_permissions(tuple(permissions))
        with self.connection_scope(connection) as conn:
            if conn.execute(S.select(r.c.id).where(r.c.id == role_id)).scalar_one_or_none() is None:
                raise KeyError(role_id)
            known = {row['code'] for row in conn.execute(S.select(p.c.code)).mappings().all()}
            unknown = {code for code, _ in expanded} - known
            if unknown:
                raise ValueError(f'未知权限: {chr(44).join(sorted(unknown))}')
            conn.execute(S.delete(rp).where(rp.c.role_id == role_id))
            for code, scope in expanded:
                conn.execute(S.insert(rp).values(role_id=role_id, permission_code=code, data_scope=scope))
        return self.list_role_permissions(role_id, connection=connection)

    _UNSET = object()

    def list_users(
        self,
        *,
        username: str | None = None,
        role_code: str | None = None,
        department_id: str | None = None,
        gender: str | None = None,
        status: str | None = None,
    ):
        S = __import__('sqlalchemy')
        users_t = __import__('system_database.schema', fromlist=['users']).users
        filters = [users_t.c.is_deleted == 0]
        if username and username.strip():
            filters.append(users_t.c.normalized_username.like(f"%{username.strip().casefold()}%"))
        if status:
            filters.append(users_t.c.status == status)
        if gender:
            filters.append(users_t.c.gender == gender)
        if role_code:
            filters.append(
                exists(
                    select(user_roles.c.user_id)
                    .select_from(user_roles.join(roles, roles.c.id == user_roles.c.role_id))
                    .where(user_roles.c.user_id == users_t.c.id, roles.c.code == role_code)
                )
            )
        if department_id:
            scope = select(departments.c.id).where(departments.c.id == department_id).cte(
                "department_scope", recursive=True
            )
            scope = scope.union_all(
                select(departments.c.id).where(departments.c.parent_id == scope.c.id)
            )
            filters.append(
                exists(
                    select(user_departments.c.user_id).where(
                        user_departments.c.user_id == users_t.c.id,
                        user_departments.c.department_id.in_(select(scope.c.id)),
                    )
                )
            )
        with self.connect() as conn:
            rows = conn.execute(
                S.select(users_t).where(*filters).order_by(users_t.c.normalized_username)
            ).mappings().all()
            return [self._user_dict(row, connection=conn) for row in rows]

    def get_user(self, user_id: str, *, connection=None):
        S = __import__('sqlalchemy')
        users_t = __import__('system_database.schema', fromlist=['users']).users
        with self.connection_scope(connection) as conn:
            row = conn.execute(S.select(users_t).where(S.and_(users_t.c.id == user_id, users_t.c.is_deleted == 0))).mappings().first()
        return self._user_dict(row, connection=connection) if row else None

    def create_user(self, username: str, display_name: str, password_hash: str, *, email=None, phone=None, gender="unspecified", created_by_user_id: str = None, connection=None):
        import uuid as _uuid
        S = __import__('sqlalchemy')
        users_t = __import__('system_database.schema', fromlist=['users']).users
        user_id = str(_uuid.uuid4())
        with self.connection_scope(connection) as conn:
            conn.execute(S.insert(users_t).values(id=user_id, username=username, normalized_username=username.casefold(), display_name=display_name, password_hash=password_hash, email=self._normalize_email(email), phone=self._normalize_phone(phone), gender=self._normalize_gender(gender), created_by_user_id=created_by_user_id))
        user = self.get_user(user_id, connection=connection)
        assert user is not None
        return user


    def update_user(
        self, user_id, *, display_name=None, status=None, role_ids=None, email=_UNSET, phone=_UNSET, gender=_UNSET, connection=None
    ):
        S = __import__('sqlalchemy')
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        users_t = __import__('system_database.schema', fromlist=['users']).users
        roles_t = __import__('system_database.schema', fromlist=['roles']).roles
        unique_role_ids = list(dict.fromkeys(role_ids)) if role_ids is not None else None
        with self.connection_scope(connection) as conn:
            u = conn.execute(S.select(users_t.c.status, users_t.c.is_deleted).where(users_t.c.id == user_id)).mappings().first()
            if u is None or u['is_deleted']:
                raise KeyError(user_id)
            role_rows = None
            if unique_role_ids is not None:
                role_rows = conn.execute(S.select(roles_t.c.id, roles_t.c.code, roles_t.c.is_enabled).where(roles_t.c.id.in_(unique_role_ids))).mappings().all()
                if len(role_rows) != len(unique_role_ids):
                    raise ValueError('角色不存在')
            currently_admin = conn.execute(
                S.select(S.func.count()).select_from(
                    user_roles_t.join(roles_t, S.and_(roles_t.c.id == user_roles_t.c.role_id, roles_t.c.code == 'system_admin', roles_t.c.is_enabled == 1))
                ).where(user_roles_t.c.user_id == user_id)
            ).scalar_one() > 0
            remains_admin = currently_admin
            if role_rows is not None:
                remains_admin = any(r['code'].casefold() == 'system_admin' and r['is_enabled'] for r in role_rows)
            resulting_status = status or u['status']
            if currently_admin and u['status'] == 'active' and (resulting_status != 'active' or not remains_admin):
                count = conn.execute(
                    S.select(S.func.count(S.func.distinct(users_t.c.id))).select_from(
                        users_t.join(user_roles_t, user_roles_t.c.user_id == users_t.c.id).join(roles_t, S.and_(roles_t.c.id == user_roles_t.c.role_id, roles_t.c.code == 'system_admin', roles_t.c.is_enabled == 1))
                    ).where(S.and_(users_t.c.status == 'active', users_t.c.is_deleted == 0, users_t.c.id != user_id))
                ).scalar_one()
                if count < 1:
                    raise ValueError('系统必须至少保留一个启用的系统管理员')
            vals = {'updated_at': S.text('CURRENT_TIMESTAMP')}
            if display_name is not None:
                vals['display_name'] = display_name
            if status is not None:
                vals['status'] = status
            if email is not self._UNSET:
                vals['email'] = self._normalize_email(email)
            if phone is not self._UNSET:
                vals['phone'] = self._normalize_phone(phone)
            if gender is not self._UNSET:
                vals['gender'] = self._normalize_gender(gender)
            conn.execute(S.update(users_t).where(users_t.c.id == user_id).values(**vals))
            if unique_role_ids is not None:
                conn.execute(S.delete(user_roles_t).where(user_roles_t.c.user_id == user_id))
                for rid in unique_role_ids:
                    conn.execute(S.insert(user_roles_t).values(user_id=user_id, role_id=rid))
            if resulting_status == 'disabled':
                sessions_t = __import__('system_database.schema', fromlist=['sessions']).sessions
                conn.execute(S.update(sessions_t).where(S.and_(sessions_t.c.user_id == user_id, sessions_t.c.revoked_at == None)).values(revoked_at=S.text('CURRENT_TIMESTAMP')))
        u = self.get_user(user_id, connection=connection)
        assert u is not None
        return u


    def replace_user_roles(self, user_id: str, role_ids: list, *, connection=None):
        S = __import__('sqlalchemy')
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        try:
            user = self.update_user(user_id, role_ids=role_ids, connection=connection)
        except S.exc.IntegrityError as exc:
            if '项目负责人只能有一名' in str(exc):
                raise ValueError('项目负责人只能有一名') from exc
            raise
        return user['role_codes']

    def assign_project_owner(self, user_id: str, *, connection=None):
        S = __import__('sqlalchemy')
        roles_t = __import__('system_database.schema', fromlist=['roles']).roles
        users_t = __import__('system_database.schema', fromlist=['users']).users
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        with self.connection_scope(connection) as conn:
            target = conn.execute(S.select(users_t.c.status, users_t.c.is_deleted).where(users_t.c.id == user_id)).mappings().first()
            if target is None or target['is_deleted']:
                raise KeyError(user_id)
            if target['status'] != 'active':
                raise ValueError('只能指定启用中的账号为项目负责人')
            role = conn.execute(
                S.select(roles_t.c.id).where(
                    S.and_(
                        S.func.lower(roles_t.c.code) == 'project_owner',
                        roles_t.c.is_enabled == 1,
                        roles_t.c.is_deleted == 0,
                    )
                )
            ).mappings().first()
            if role is None:
                raise ValueError('项目负责人角色不可用')
            current = conn.execute(
                S.select(users_t.c.id, users_t.c.status, users_t.c.is_deleted).select_from(
                    user_roles_t.join(users_t, users_t.c.id == user_roles_t.c.user_id)
                ).where(user_roles_t.c.role_id == role['id'])
            ).mappings().first()
            if current is not None and current['id'] == user_id:
                result_data = self.get_user(user_id, connection=conn)
                assert result_data is not None
                return result_data
            if current is not None and current['status'] == 'active' and not current['is_deleted']:
                raise ValueError('请先停用当前项目负责人')
            conn.execute(S.delete(user_roles_t).where(user_roles_t.c.role_id == role['id']))
            conn.execute(S.insert(user_roles_t).values(user_id=user_id, role_id=role['id']))
        result_data = self.get_user(user_id, connection=connection)
        assert result_data is not None
        return result_data

    def soft_delete_user(self, user_id: str, *, deleted_by_user_id: str = None, connection=None):
        S = __import__('sqlalchemy')
        users_t = __import__('system_database.schema', fromlist=['users']).users
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        sessions_t = __import__('system_database.schema', fromlist=['sessions']).sessions
        project_members_t = __import__('system_database.schema', fromlist=['project_members']).project_members
        workflow_tasks_t = __import__('system_database.schema', fromlist=['workflow_tasks']).workflow_tasks
        with self.connection_scope(connection) as conn:
            u = conn.execute(S.select(users_t.c.status, users_t.c.is_deleted).where(users_t.c.id == user_id)).mappings().first()
            if u is None or u['is_deleted']:
                raise KeyError(user_id)
            self.update_user(user_id, status='disabled', connection=conn)
            conn.execute(S.update(users_t).where(users_t.c.id == user_id).values(
                is_deleted=1, deleted_at=S.text('CURRENT_TIMESTAMP'),
                deleted_by_user_id=deleted_by_user_id, updated_at=S.text('CURRENT_TIMESTAMP')
            ))
            conn.execute(S.delete(user_roles_t).where(user_roles_t.c.user_id == user_id))
            conn.execute(S.update(sessions_t).where(S.and_(sessions_t.c.user_id == user_id, sessions_t.c.revoked_at == None)).values(revoked_at=S.text('CURRENT_TIMESTAMP')))
            conn.execute(S.update(project_members_t).where(S.and_(project_members_t.c.user_id == user_id, project_members_t.c.status == 'active')).values(status='inactive', updated_at=S.text('CURRENT_TIMESTAMP')))
            conn.execute(S.update(workflow_tasks_t).where(S.and_(workflow_tasks_t.c.assignee_id == user_id, workflow_tasks_t.c.status.in_(['assigned', 'in_progress', 'returned']))).values(assignee_id=None, assigned_by=None, status='pool', updated_at=S.text('CURRENT_TIMESTAMP')))


    def soft_delete_role(self, role_id: str, *, deleted_by_user_id: str = None, connection=None):
        S = __import__('sqlalchemy')
        roles_t = __import__('system_database.schema', fromlist=['roles']).roles
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        with self.connection_scope(connection) as conn:
            role = conn.execute(S.select(roles_t.c.is_system, roles_t.c.is_deleted).where(roles_t.c.id == role_id)).mappings().first()
            if role is None or role['is_deleted']:
                raise KeyError(role_id)
            if role['is_system']:
                raise ValueError('系统角色不能删除')
            conn.execute(S.delete(user_roles_t).where(user_roles_t.c.role_id == role_id))
            conn.execute(S.update(roles_t).where(roles_t.c.id == role_id).values(
                is_enabled=0, is_deleted=1,
                deleted_at=S.text('CURRENT_TIMESTAMP'),
                deleted_by_user_id=deleted_by_user_id,
                updated_at=S.text('CURRENT_TIMESTAMP')
            ))

    def user_role_codes(self, user_id: str, *, connection=None):
        S = __import__('sqlalchemy')
        roles_t = __import__('system_database.schema', fromlist=['roles']).roles
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        with self.connection_scope(connection) as conn:
            rows = conn.execute(
                S.select(roles_t.c.code).select_from(
                    user_roles_t.join(roles_t, roles_t.c.id == user_roles_t.c.role_id)
                ).where(user_roles_t.c.user_id == user_id).order_by(roles_t.c.code)
            ).mappings().all()
        return [r['code'] for r in rows]

    def count_active_system_admins(self, excluding_user_id: str = None, removing_role_id: str = None):
        S = __import__('sqlalchemy')
        users_t = __import__('system_database.schema', fromlist=['users']).users
        user_roles_t = __import__('system_database.schema', fromlist=['user_roles']).user_roles
        roles_t = __import__('system_database.schema', fromlist=['roles']).roles
        conds = [users_t.c.status == 'active', users_t.c.is_deleted == 0, roles_t.c.code == 'system_admin', roles_t.c.is_enabled == 1]
        if excluding_user_id is not None and removing_role_id is None:
            conds.append(users_t.c.id != excluding_user_id)
        elif excluding_user_id is not None and removing_role_id is not None:
            conds.append(S.not_(S.and_(users_t.c.id == excluding_user_id, roles_t.c.id == removing_role_id)))
        with self.connect() as conn:
            return conn.execute(
                S.select(S.func.count(S.func.distinct(users_t.c.id))).select_from(
                    users_t.join(user_roles_t, user_roles_t.c.user_id == users_t.c.id).join(roles_t, roles_t.c.id == user_roles_t.c.role_id)
                ).where(S.and_(*conds))
            ).scalar_one()

    def assert_admin_survives(self, user_id: str, removing_role_id: str = None):
        if self.count_active_system_admins(excluding_user_id=user_id, removing_role_id=removing_role_id) < 1:
            raise ValueError('系统必须至少保留一个启用的系统管理员')

    def disable_user(self, user_id: str):
        self.update_user(user_id, status='disabled')

    @staticmethod
    def _role_dict(row):
        d = dict(row)
        d['is_system'] = bool(d['is_system'])
        d['is_enabled'] = bool(d['is_enabled'])
        d['is_deleted'] = bool(d.get('is_deleted', 0))
        return d

    def _user_dict(self, row, *, connection=None):
        d = dict(row)
        d['role_codes'] = self.user_role_codes(d['id'], connection=connection)
        d['departments'] = self.list_user_departments(d['id'], connection=connection)
        d['primary_department'] = next(
            (department for department in d['departments'] if department['is_primary']), None
        )
        d.pop('password_hash', None)
        d.pop('normalized_username', None)
        d.pop('is_deleted', None)
        d.pop('updated_at', None)
        return d

    def list_user_departments(self, user_id: str, *, connection=None) -> list[dict[str, Any]]:
        with self.connection_scope(connection) as conn:
            rows = conn.execute(
                select(
                    user_departments.c.department_id.label('id'),
                    departments.c.name,
                    user_departments.c.is_primary,
                )
                .select_from(user_departments.join(departments, departments.c.id == user_departments.c.department_id))
                .where(user_departments.c.user_id == user_id)
                .order_by(user_departments.c.is_primary.desc(), departments.c.sort_order, departments.c.name)
            ).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _normalize_email(value: str | None) -> str | None:
        normalized = (value or '').strip().lower()
        if not normalized:
            return None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError('邮箱格式无效')
        return normalized

    @staticmethod
    def _normalize_phone(value: str | None) -> str | None:
        normalized = (value or '').strip()
        if not normalized:
            return None
        if not re.fullmatch(r"1[3-9]\d{9}", normalized):
            raise ValueError('手机号必须为中国大陆 11 位手机号')
        return normalized

    @staticmethod
    def _normalize_gender(value: str | None) -> str:
        normalized = value or 'unspecified'
        if normalized not in {'unspecified', 'male', 'female'}:
            raise ValueError('性别值无效')
        return normalized
