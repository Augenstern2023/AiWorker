# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from system_database.schema import sessions, user_presence, users

from .repository import IdentityRepository


class PresenceService:
    """Store current browser-tab presence independently from authentication."""

    def __init__(
        self,
        repository: IdentityRepository,
        *,
        online_timeout: timedelta = timedelta(seconds=90),
        idle_timeout: timedelta = timedelta(minutes=5),
    ) -> None:
        self.repository = repository
        self.online_timeout = online_timeout
        self.idle_timeout = idle_timeout

    def record_heartbeat(
        self,
        *,
        user_id: str,
        session_id: str,
        tab_id: str,
        path: str,
        is_visible: bool,
        idle_seconds: float,
        connection=None,
        now: datetime | None = None,
    ) -> None:
        current = self._utc(now)
        activity_at = current - timedelta(seconds=max(0.0, idle_seconds))
        page_label = self.page_label_for_path(path)
        insert_statement = pg_insert(user_presence).values(
            session_id=session_id,
            tab_id=tab_id,
            user_id=user_id,
            page_label=page_label,
            is_visible=is_visible,
            last_activity_at=activity_at,
            last_heartbeat_at=current,
        )
        heartbeat_upsert = insert_statement.on_conflict_do_update(
            index_elements=[user_presence.c.session_id, user_presence.c.tab_id],
            set_={
                "user_id": user_id,
                "page_label": page_label,
                "is_visible": is_visible,
                "last_activity_at": activity_at,
                "last_heartbeat_at": current,
            },
        )
        with self.repository.connection_scope(connection) as active_connection:
            active_connection.execute(heartbeat_upsert)
            active_connection.execute(
                update(users)
                .where(users.c.id == user_id, users.c.is_deleted == 0)
                .values(last_online_at=current)
            )

    def disconnect_session(
        self,
        *,
        user_id: str,
        session_id: str,
        connection=None,
        now: datetime | None = None,
    ) -> None:
        current = self._utc(now)
        with self.repository.connection_scope(connection) as active_connection:
            active_connection.execute(
                update(users)
                .where(users.c.id == user_id, users.c.is_deleted == 0)
                .values(last_online_at=current)
            )
            active_connection.execute(
                delete(user_presence).where(user_presence.c.session_id == session_id)
            )

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = self._utc(now)
        online_cutoff = current - self.online_timeout
        idle_cutoff = current - self.idle_timeout
        with self.repository.connect() as connection:
            user_rows = connection.execute(
                select(users.c.id, users.c.last_online_at)
                .where(users.c.is_deleted == 0)
                .order_by(users.c.normalized_username)
            ).mappings().all()
            presence_rows = connection.execute(
                select(
                    user_presence.c.user_id,
                    user_presence.c.session_id,
                    user_presence.c.page_label,
                    user_presence.c.is_visible,
                    user_presence.c.last_activity_at,
                    user_presence.c.last_heartbeat_at,
                )
                .select_from(
                    user_presence.join(
                        sessions, sessions.c.id == user_presence.c.session_id
                    ).join(users, users.c.id == user_presence.c.user_id)
                )
                .where(
                    user_presence.c.last_heartbeat_at >= online_cutoff,
                    sessions.c.revoked_at.is_(None),
                    sessions.c.expires_at > current,
                    users.c.status == "active",
                    users.c.is_deleted == 0,
                )
            ).mappings().all()

        rows_by_user: dict[str, list[Any]] = defaultdict(list)
        for row in presence_rows:
            rows_by_user[row["user_id"]].append(row)

        items: dict[str, dict[str, Any]] = {}
        for user in user_rows:
            active_rows = rows_by_user.get(user["id"], [])
            if not active_rows:
                items[user["id"]] = {
                    "status": "offline",
                    "page": None,
                    "device_count": 0,
                    "last_online_at": self._iso(user["last_online_at"]),
                }
                continue

            is_active = any(
                bool(row["is_visible"])
                and self._utc(row["last_activity_at"]) >= idle_cutoff
                for row in active_rows
            )
            page_row = max(
                active_rows,
                key=lambda row: (
                    bool(row["is_visible"]),
                    self._utc(row["last_activity_at"]),
                    self._utc(row["last_heartbeat_at"]),
                ),
            )
            items[user["id"]] = {
                "status": "active" if is_active else "idle",
                "page": page_row["page_label"],
                "device_count": len(
                    {row["session_id"] for row in active_rows}
                ),
                "last_online_at": self._iso(user["last_online_at"]),
            }

        return {"refreshed_at": self._iso(current), "users": items}

    @staticmethod
    def page_label_for_path(path: str) -> str:
        normalized = (path or "/").split("?", 1)[0]
        if normalized.startswith("/selection"):
            return "选品"
        if normalized.startswith("/planning"):
            return "内容策划"
        if normalized.startswith("/writing"):
            return "内容创作"
        if normalized.startswith("/review"):
            return "审核管理"
        if normalized.startswith("/admin"):
            return "系统管理"
        if normalized == "/profile":
            return "个人中心"
        if normalized == "/tasks":
            return "任务中心"
        return "工作台"

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _iso(cls, value: datetime | None) -> str | None:
        return cls._utc(value).isoformat() if value is not None else None
