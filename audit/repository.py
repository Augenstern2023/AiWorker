# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import sqlite3
from typing import Any

from identity.repository import IdentityRepository


_AUDIT_COLUMNS = {
    "actor_user_id": "TEXT REFERENCES users(id) ON DELETE SET NULL",
    "actor_display_name_snapshot": "TEXT",
    "actor_role_snapshot_json": "TEXT NOT NULL DEFAULT '[]'",
    "target_type": "TEXT",
    "target_id": "TEXT",
    "before_summary_json": "TEXT",
    "after_summary_json": "TEXT",
    "request_id": "TEXT",
    "user_agent": "TEXT",
    "result": "TEXT NOT NULL DEFAULT 'success'",
}


class AuditRepository:
    """Persists normalized audit events in the shared system database."""

    def __init__(self, identity_repository: IdentityRepository) -> None:
        self.identity_repository = identity_repository
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        with self.identity_repository.connect() as connection:
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(audit_logs)")
            }
            for column, definition in _AUDIT_COLUMNS.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE audit_logs ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "UPDATE audit_logs SET actor_user_id = actor_id "
                "WHERE actor_user_id IS NULL AND actor_id IS NOT NULL"
            )
            connection.execute(
                """
                UPDATE audit_logs
                SET actor_display_name_snapshot = COALESCE(
                    (SELECT display_name FROM users WHERE users.id = audit_logs.actor_user_id),
                    actor_user_id
                )
                WHERE actor_display_name_snapshot IS NULL
                  AND actor_user_id IS NOT NULL
                """
            )
            connection.execute(
                "UPDATE audit_logs SET target_type = resource_type "
                "WHERE target_type IS NULL"
            )
            connection.execute(
                "UPDATE audit_logs SET target_id = resource_id "
                "WHERE target_id IS NULL AND resource_id IS NOT NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_action "
                "ON audit_logs(action)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id "
                "ON audit_logs(actor_user_id)"
            )

    def insert(
        self,
        event: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        if connection is not None:
            self._insert(connection, event)
        else:
            with self.identity_repository.connect() as owned_connection:
                self._insert(owned_connection, event)
        return str(event["id"])

    @staticmethod
    def _insert(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO audit_logs (
                id, actor_id, actor_user_id, actor_display_name_snapshot,
                actor_role_snapshot_json,
                action, resource_type, target_type, resource_id, target_id,
                project_id, reason, details_json, before_summary_json,
                after_summary_json, request_id, ip_address, user_agent,
                result, created_at
            ) VALUES (
                :id, :actor_user_id, :actor_user_id, :actor_display_name_snapshot,
                :actor_role_snapshot_json, :action, :target_type,
                :target_type, :target_id, :target_id, :project_id, :reason,
                '{}', :before_summary_json, :after_summary_json, :request_id,
                :ip_address, :user_agent, :result, CURRENT_TIMESTAMP
            )
            """,
            event,
        )

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self.identity_repository.connect() as connection:
            row = connection.execute(
                """
                SELECT audit_logs.*,
                       COALESCE(
                           NULLIF(audit_logs.actor_display_name_snapshot, ''),
                           users.display_name,
                           audit_logs.actor_user_id
                       ) AS actor_display_name
                FROM audit_logs
                LEFT JOIN users ON users.id = audit_logs.actor_user_id
                WHERE audit_logs.id = ?
                """,
                (event_id,),
            ).fetchone()
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
        where, parameters = self._where_clause(
            actor_user_id=actor_user_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
            result=result,
            created_from=created_from,
            created_to=created_to,
        )
        parameters.extend((max(1, min(limit, 500)), max(0, offset)))
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT audit_logs.*,
                       COALESCE(
                           NULLIF(audit_logs.actor_display_name_snapshot, ''),
                           users.display_name,
                           audit_logs.actor_user_id
                       ) AS actor_display_name
                FROM audit_logs
                LEFT JOIN users ON users.id = audit_logs.actor_user_id
                {where}
                ORDER BY audit_logs.created_at DESC, audit_logs.id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [self._event_dict(row) for row in rows]

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
        where, parameters = self._where_clause(
            actor_user_id=actor_user_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
            result=result,
            created_from=created_from,
            created_to=created_to,
        )
        with self.identity_repository.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM audit_logs
                LEFT JOIN users ON users.id = audit_logs.actor_user_id
                {where}
                """,
                parameters,
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _where_clause(
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
    ) -> tuple[str, list[Any]]:
        filters = {
            "actor_user_id": actor_user_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "project_id": project_id,
            "result": result,
        }
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"audit_logs.{column} = ?")
                parameters.append(value)
        if actor is not None:
            pattern = f"%{actor}%"
            clauses.append(
                """
                (
                    audit_logs.actor_user_id = ?
                    OR audit_logs.actor_display_name_snapshot LIKE ? COLLATE NOCASE
                    OR users.username LIKE ? COLLATE NOCASE
                    OR users.display_name LIKE ? COLLATE NOCASE
                )
                """
            )
            parameters.extend((actor, pattern, pattern, pattern))
        if created_from is not None:
            clauses.append("audit_logs.created_at >= ?")
            parameters.append(created_from)
        if created_to is not None:
            clauses.append("audit_logs.created_at < ?")
            parameters.append(created_to)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, parameters

    @staticmethod
    def _event_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "actor_user_id": row["actor_user_id"],
            "actor_display_name": row["actor_display_name"],
            "actor_role_snapshot": json.loads(
                row["actor_role_snapshot_json"] or "[]"
            ),
            "action": row["action"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "project_id": row["project_id"],
            "before_summary": (
                json.loads(row["before_summary_json"])
                if row["before_summary_json"] is not None
                else None
            ),
            "after_summary": (
                json.loads(row["after_summary_json"])
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
