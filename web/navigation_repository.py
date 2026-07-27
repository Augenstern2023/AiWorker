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

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from identity.permission_catalog import NAVIGATION_CATALOG
from system_database import SystemDatabase
from system_database.schema import navigation_items


class NavigationRepository:
    def __init__(self, database: SystemDatabase) -> None:
        self.database = database

    def seed_catalog(self) -> None:
        with self.database.transaction() as connection:
            for item in NAVIGATION_CATALOG:
                connection.execute(
                    postgresql_insert(navigation_items)
                    .values(
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
                    .on_conflict_do_nothing(index_elements=[navigation_items.c.id])
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
