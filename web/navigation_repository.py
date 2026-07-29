# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL persistence for the permission-backed navigation catalog."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from identity.permission_catalog import NAVIGATION_CATALOG
from system_database import SystemDatabase
from system_database.schema import (
    navigation_items,
    role_page_access,
    roles,
    user_page_access,
    user_roles,
    users,
)
from web.navigation_service import PAGE_ROUTES


PAGE_ACCESS_ROUTES = frozenset(
    route for route, _, _ in PAGE_ROUTES if "{" not in route
)


class NavigationRepository:
    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def seed_catalog(self) -> None:
        with self.database.transaction() as connection:
            for item in NAVIGATION_CATALOG:
                statement = postgresql_insert(navigation_items).values(
                        id=item["id"],
                        parent_id=item.get("parent_id"),
                        label=item["label"],
                        icon=item.get("icon", ""),
                        route=item["route"],
                        required_permission=item["required_permission"],
                        sort_order=item.get("sort_order", 0),
                        is_enabled=item.get("is_enabled", True),
                        is_system=True,
                        hide_for_system_admin=item.get("hide_for_system_admin", False),
                    )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[navigation_items.c.id],
                        set_={
                            "parent_id": statement.excluded.parent_id,
                            "route": statement.excluded.route,
                            "required_permission": statement.excluded.required_permission,
                            "is_system": True,
                            "hide_for_system_admin": statement.excluded.hide_for_system_admin,
                        },
                    )
                )

    def list_items(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                select(navigation_items).order_by(
                    navigation_items.c.parent_id,
                    navigation_items.c.sort_order,
                    navigation_items.c.id,
                )
            ).mappings()
            return [self._item_dict(row) for row in rows]

    def get_item(
        self, item_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        with self._connection_scope(connection) as conn:
            row = conn.execute(
                select(navigation_items).where(navigation_items.c.id == item_id)
            ).mappings().one_or_none()
        return self._item_dict(row) if row is not None else None

    def list_page_access_user_ids(
        self, route: str, *, connection: Connection | None = None
    ) -> set[str]:
        self._validate_page_access_route(route)
        with self._connection_scope(connection) as conn:
            return set(
                conn.execute(
                    select(user_page_access.c.user_id).where(
                        user_page_access.c.route == route,
                        user_page_access.c.is_allowed.is_(True),
                    )
                ).scalars()
            )

    def get_page_access_override(
        self, user_id: str, route: str, *, connection: Connection | None = None
    ) -> bool | None:
        self._validate_page_access_route(route)
        with self._connection_scope(connection) as conn:
            return conn.execute(
                select(user_page_access.c.is_allowed).where(
                    user_page_access.c.user_id == user_id,
                    user_page_access.c.route == route,
                )
            ).scalar_one_or_none()

    def list_page_access_overrides(
        self, route: str, *, connection: Connection | None = None
    ) -> dict[str, bool]:
        self._validate_page_access_route(route)
        with self._connection_scope(connection) as conn:
            rows = conn.execute(
                select(user_page_access.c.user_id, user_page_access.c.is_allowed).where(
                    user_page_access.c.route == route
                )
            ).mappings().all()
        return {str(row["user_id"]): bool(row["is_allowed"]) for row in rows}

    def list_role_page_access_routes(self, role_codes: tuple[str, ...]) -> set[str]:
        if not role_codes:
            return set()
        normalized_codes = {code.casefold() for code in role_codes}
        with self.database.connection() as connection:
            return set(
                connection.execute(
                    select(role_page_access.c.route)
                    .select_from(role_page_access.join(roles, role_page_access.c.role_id == roles.c.id))
                    .where(
                        func.lower(roles.c.code).in_(normalized_codes),
                        roles.c.is_enabled == 1,
                        roles.c.is_deleted == 0,
                    )
                ).scalars()
            )

    def replace_page_access(
        self,
        route: str,
        user_ids: set[str],
        *,
        connection: Connection | None = None,
    ) -> set[str]:
        return set(
            self.replace_page_access_overrides(
                route, {user_id: True for user_id in user_ids}, connection=connection
            )
        )

    def replace_page_access_overrides(
        self,
        route: str,
        overrides: dict[str, bool],
        *,
        connection: Connection | None = None,
    ) -> dict[str, bool]:
        self._validate_page_access_route(route)
        normalized = {str(user_id): bool(is_allowed) for user_id, is_allowed in overrides.items()}
        if any(not isinstance(user_id, str) or not user_id for user_id in overrides):
            raise ValueError("page access user ids must be non-empty strings")

        with self._transaction_scope(connection) as conn:
            system_admin_role = exists(
                select(1)
                .select_from(user_roles.join(roles, user_roles.c.role_id == roles.c.id))
                .where(
                    user_roles.c.user_id == users.c.id,
                    func.lower(roles.c.code) == "system_admin",
                )
            )
            valid_user_ids = set(
                conn.execute(
                    select(users.c.id).where(
                        users.c.id.in_(normalized),
                        users.c.status == "active",
                        users.c.is_deleted == 0,
                        ~system_admin_role,
                    )
                ).scalars()
            )
            if valid_user_ids != set(normalized):
                raise ValueError("page access contains an unknown or ineligible account")

            conn.execute(
                delete(user_page_access).where(user_page_access.c.route == route)
            )
            if normalized:
                conn.execute(
                    insert(user_page_access),
                    [
                        {"user_id": user_id, "route": route, "is_allowed": is_allowed}
                        for user_id, is_allowed in normalized.items()
                    ],
                )
        return normalized

    @contextmanager
    def _connection_scope(
        self, connection: Connection | None
    ) -> Iterator[Connection]:
        if connection is not None:
            yield connection
            return
        with self.database.connection() as owned_connection:
            yield owned_connection

    @contextmanager
    def _transaction_scope(
        self, connection: Connection | None
    ) -> Iterator[Connection]:
        if connection is not None:
            yield connection
            return
        with self.database.transaction() as owned_connection:
            yield owned_connection

    def update_presentation(
        self,
        item_id: str,
        *,
        label: str,
        icon: str,
        sort_order: int,
        is_enabled: bool,
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                select(navigation_items).where(navigation_items.c.id == item_id)
            ).mappings().one_or_none()
            if row is None:
                raise KeyError(item_id)
            if not row["is_system"]:
                raise ValueError("only built-in navigation items can be updated")
            connection.execute(
                update(navigation_items)
                .where(navigation_items.c.id == item_id)
                .values(
                    label=label.strip(),
                    icon=icon.strip(),
                    sort_order=sort_order,
                    is_enabled=is_enabled,
                )
            )
            updated = connection.execute(
                select(navigation_items).where(navigation_items.c.id == item_id)
            ).mappings().one()
        return self._item_dict(updated)

    @staticmethod
    def _item_dict(row: Any) -> dict[str, Any]:
        item = dict(row)
        for key in ("is_enabled", "is_system", "hide_for_system_admin"):
            item[key] = bool(item[key])
        return item

    @staticmethod
    def _validate_page_access_route(route: str) -> None:
        if route not in PAGE_ACCESS_ROUTES:
            raise ValueError("page access route must be a static registered page route")
