# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import Connection, and_, select, text, update

from system_database.schema import roles as roles_table
from system_database.schema import role_permissions as role_permissions_table
from system_database.schema import sessions as sessions_table
from system_database.schema import user_roles as user_roles_table
from system_database.schema import users as users_table

from .repository import IdentityRepository
from .schema import DATA_SCOPE_ORDER, Principal


_CSRF_DERIVATION_KEY = b"aiworker-session-csrf-v1"


@dataclass(frozen=True)
class SessionContext:
    principal: Principal
    csrf_token: str


class SessionService:
    cookie_name = "aiworker_session"

    def __init__(
        self,
        repository: IdentityRepository,
        *,
        session_ttl: timedelta = timedelta(hours=12),
        secure_cookie: bool = False,
        csrf_secret: bytes | None = None,
    ) -> None:
        self.repository = repository
        self.session_ttl = session_ttl
        self.secure_cookie = secure_cookie
        self._csrf_secret = csrf_secret or _CSRF_DERIVATION_KEY

    def create_session(
        self,
        user_id: str,
        request: Request,
        *,
        connection: Connection | None = None,
    ) -> tuple[str, str]:
        with self.repository.connection_scope(connection) as active_connection:
            user = active_connection.execute(
                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["status"] != "active" or user["is_deleted"]:
                raise ValueError("用户不可登录")

            raw_token = secrets.token_urlsafe(32)
            csrf_token = self._csrf_for_token(raw_token)
            now = datetime.now(timezone.utc)
            active_connection.execute(
                """
                INSERT INTO sessions
                    (id, user_id, token_hash, csrf_token_hash, expires_at,
                     last_seen_at, user_agent, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    user_id,
                    self._hash_token(raw_token),
                    self._hash_token(csrf_token),
                    (now + self.session_ttl).isoformat(),
                    now.isoformat(),
                    request.headers.get("user-agent"),
                    request.client.host if request.client else None,
                ),
            )
        return raw_token, csrf_token

    def resolve_session(
        self,
        raw_token: str | None,
        *,
        connection: Connection | None = None,
    ) -> SessionContext | None:
        if not raw_token:
            return None
        token_hash = self._hash_token(raw_token)
        with self.repository.connection_scope(connection) as active_connection:
            row = active_connection.execute(
                """
                SELECT s.id AS session_id, s.user_id, s.csrf_token_hash,
                       s.expires_at, s.revoked_at, u.username, u.display_name,
                       u.status, u.is_deleted
                FROM sessions AS s
                JOIN users AS u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                return None

            if row["status"] != "active" or row["is_deleted"]:
                active_connection.execute(
                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (row["user_id"],),
                )
                return None

            if self._parse_datetime(row["expires_at"]) <= datetime.now(timezone.utc):
                active_connection.execute(
                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["session_id"],),
                )
                return None

            role_rows = active_connection.execute(
                """
                SELECT r.code, rp.permission_code, rp.data_scope
                FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
                WHERE ur.user_id = ?
                ORDER BY r.code, rp.permission_code
                """,
                (row["user_id"],),
            ).fetchall()
            active_connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["session_id"]),
            )

        role_codes = tuple(sorted({role["code"] for role in role_rows}))
        permissions = frozenset(
            role["permission_code"]
            for role in role_rows
            if role["permission_code"] is not None
        )
        scopes = [
            role["data_scope"]
            for role in role_rows
            if role["data_scope"] is not None
        ]
        data_scope = max(scopes, key=DATA_SCOPE_ORDER.get) if scopes else "task"
        csrf_token = self._csrf_for_token(raw_token)
        if not hmac.compare_digest(
            row["csrf_token_hash"], self._hash_token(csrf_token)
        ):
            return None
        return SessionContext(
            principal=Principal(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                role_codes=role_codes,
                permissions=permissions,
                data_scope=data_scope,
                session_id=row["session_id"],
            ),
            csrf_token=csrf_token,
        )

    def verify_csrf(self, raw_token: str, supplied_token: str | None) -> bool:
        if not supplied_token:
            return False
        expected = self._csrf_for_token(raw_token)
        return hmac.compare_digest(expected, supplied_token)

    def revoke_session(
        self,
        raw_token: str,
        *,
        connection: Connection | None = None,
    ) -> None:
        with self.repository.connection_scope(connection) as active_connection:
            active_connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE token_hash = ? AND revoked_at IS NULL",
                (self._hash_token(raw_token),),
            )

    def revoke_user_sessions(
        self,
        user_id: str,
        *,
        connection: Connection | None = None,
    ) -> None:
        with self.repository.connection_scope(connection) as active_connection:
            active_connection.execute(
                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _csrf_for_token(self, raw_token: str) -> str:
        digest = hmac.new(
            self._csrf_secret, raw_token.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
