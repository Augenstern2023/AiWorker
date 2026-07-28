# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, and_, func, or_, select
from sqlalchemy.sql.elements import ColumnClause

from identity.repository import IdentityRepository
from system_database.schema import audit_logs, users


class AuditRepository:
    """Persists normalized audit events in the shared system database."""

    def __init__(self, identity_repository: IdentityRepository) -> None:
        self.identity_repository = identity_repository

    def insert(
        self,
        event: dict[str, Any],
        *,
        connection: Connection | None = None,
    ) -> str:
        if connection is not None:
            self._insert(connection, event)
        else:
            with self.identity_repository.connect() as conn:
                self._insert(conn, event)
        return str(event["id"])

    @staticmethod
    def _insert(conn: Connection, event: dict[str, Any]) -> None:
        conn.execute(
            audit_logs.insert().values(
                id=event["id"],
                actor_id=event["actor_user_id"],
                actor_user_id=event["actor_user_id"],
                actor_display_name_snapshot=event["actor_display_name_snapshot"],
                actor_role_snapshot_json=event["actor_role_snapshot_json"],
                action=event["action"],
                resource_type=event["target_type"],
                target_type=event["target_type"],
                resource_id=event["target_id"],
                target_id=event["target_id"],
                project_id=event["project_id"],
                reason=event["reason"],
                details_json="{}",
                before_summary_json=event["before_summary_json"],
                after_summary_json=event["after_summary_json"],
                request_id=event["request_id"],
                ip_address=event["ip_address"],
                user_agent=event["user_agent"],
                result=event["result"],
            )
        )

    def get(self, event_id: str) -> dict[str, Any] | None:
        display_name = func.coalesce(
            func.nullif(audit_logs.c.actor_display_name_snapshot, ""),
            users.c.display_name,
            audit_logs.c.actor_user_id,
        ).label("actor_display_name")
        stmt = (
            select(
                audit_logs,
                display_name,
            )
            .select_from(
                audit_logs.outerjoin(
                    users, users.c.id == audit_logs.c.actor_user_id
                )
            )
            .where(audit_logs.c.id == event_id)
        )
        with self.identity_repository.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return self._event_dict(row) if row is not None else None

    def query(
        self,
        *,
        actor_user_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        project_id: str | None = None,
        result: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        display_name = func.coalesce(
            func.nullif(audit_logs.c.actor_display_name_snapshot, ""),
            users.c.display_name,
            audit_logs.c.actor_user_id,
        ).label("actor_display_name")
        stmt = (
            select(
                audit_logs,
                display_name,
            )
            .select_from(
                audit_logs.outerjoin(
                    users, users.c.id == audit_logs.c.actor_user_id
                )
            )
            .where(self._build_filters(
                actor_user_id=actor_user_id,
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
                result=result,
                created_from=created_from,
                created_to=created_to,
            ))
            .order_by(audit_logs.c.created_at.desc(), audit_logs.c.id.desc())
            .limit(max(1, min(limit, 500)))
            .offset(max(0, offset))
        )
        with self.identity_repository.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._event_dict(r) for r in rows]

    def count(
        self,
        *,
        actor_user_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        project_id: str | None = None,
        result: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(
                audit_logs.outerjoin(
                    users, users.c.id == audit_logs.c.actor_user_id
                )
            )
            .where(self._build_filters(
                actor_user_id=actor_user_id,
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
                result=result,
                created_from=created_from,
                created_to=created_to,
            ))
        )
        with self.identity_repository.connect() as conn:
            return conn.execute(stmt).scalar_one()

    @staticmethod
    def _build_filters(
        *,
        actor_user_id: str | None,
        actor: str | None,
        action: str | None,
        target_type: str | None,
        target_id: str | None,
        project_id: str | None,
        result: str | None,
        created_from: str | None,
        created_to: str | None,
    ) -> ColumnClause:
        conditions = []
        for column_name, value in (
            ("actor_user_id", actor_user_id),
            ("action", action),
            ("target_type", target_type),
            ("target_id", target_id),
            ("project_id", project_id),
            ("result", result),
        ):
            if value is not None:
                conditions.append(
                    getattr(audit_logs.c, column_name) == value
                )
        if actor is not None:
            pattern = f"%{actor}%"
            conditions.append(
                or_(
                    audit_logs.c.actor_user_id == actor,
                    audit_logs.c.actor_display_name_snapshot.ilike(pattern),
                    users.c.username.ilike(pattern),
                    users.c.display_name.ilike(pattern),
                )
            )
        if created_from is not None:
            conditions.append(audit_logs.c.created_at >= created_from)
        if created_to is not None:
            conditions.append(audit_logs.c.created_at < created_to)
        return and_(*conditions) if conditions else True

    @staticmethod
    def _event_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "actor_user_id": row["actor_user_id"],
            "actor_display_name": row["actor_display_name"],
            "actor_role_snapshot": _json_value(row["actor_role_snapshot_json"], []),
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "project_id": row["project_id"],
            "before_summary": (
                _json_value(row["before_summary_json"], None)
                if row["before_summary_json"] is not None
                else None
            ),
            "after_summary": (
                _json_value(row["after_summary_json"], None)
                if row["after_summary_json"] is not None
                else None
            ),
            "request_id": row["request_id"],
            "ip_address": row["ip_address"],
            "user_agent": row["user_agent"],
            "reason": row["reason"],
            "result": row["result"],
            "created_at": row["created_at"],
        }


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)
