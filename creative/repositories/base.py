# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Shared connection, transaction and outbound-conversion behaviour.

The SQLite store handed callers raw ``sqlite3.Row`` objects whose values were
epoch floats and JSON text. Callers were later taught to expect parsed
dictionaries, but never ``datetime`` objects and never SQLAlchemy ``Row``
objects. Everything in this module exists to keep that contract: a repository
method returns plain ``dict`` values with epoch-second timestamps and native
JSON, regardless of how PostgreSQL represents them internally.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import RowMapping, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from creative.database_schema import creative_store_metadata
from system_database import SystemDatabase


STORE_ID_KEY = "store_id"

_IDENTITY_TABLES = {
    "creative_blueprints",
    "creative_characters",
    "creative_episodes",
    "creative_projects",
    "creative_relationships",
    "creative_seeds",
}


def _legacy_value(value: Any) -> Any:
    """Expose PostgreSQL values through the old row-oriented service contract."""

    if isinstance(value, datetime):
        return epoch_seconds(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class _LegacyResult:
    """Minimal sqlite cursor-shaped wrapper used by the free-mode service.

    Free mode predates the repository split and contains a sizeable set of
    transaction-local SQL queries. Keeping those queries on the repository's
    PostgreSQL transaction boundary avoids a second persistence path while the
    service is decomposed into repository methods.
    """

    def __init__(self, result: Any, *, returns_identity: bool = False) -> None:
        self.rowcount = int(result.rowcount or 0)
        self.lastrowid: int | None = None
        self._rows: list[dict[str, Any]] | None = None
        self._index = 0
        if result.returns_rows:
            rows = [
                {key: _legacy_value(value) for key, value in dict(row).items()}
                for row in result.mappings().all()
            ]
            if returns_identity and rows:
                self.lastrowid = int(rows[0]["id"])
                rows = []
            self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        if not self._rows or self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[dict[str, Any]]:
        if not self._rows:
            return []
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _LegacyConnection:
    """Translate qmark SQL onto one PostgreSQL SQLAlchemy transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    @staticmethod
    def _statement(statement: str, values: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
        stripped = statement.strip()
        if stripped.upper() == "BEGIN IMMEDIATE":
            return "", {}
        # The only SQLite boolean literal left in the free-mode query set.
        statement = re.sub(r"\bdeleted\s*=\s*0\b", "deleted = FALSE", statement)
        parts = statement.split("?")
        if len(parts) - 1 != len(values):
            raise ValueError("creative query parameter mismatch")
        bindings: dict[str, Any] = {}
        translated = parts[0]
        for index, part in enumerate(parts[1:]):
            key = f"p{index}"
            translated += f":{key}{part}"
            value = values[index]
            # Legacy services use epoch floats. PostgreSQL stores TIMESTAMPTZ.
            if isinstance(value, float) and value > 1_000_000_000:
                value = from_epoch_seconds(value)
            bindings[key] = value
        return translated, bindings

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> _LegacyResult:
        sql, bindings = self._statement(statement, tuple(parameters))
        if not sql:
            # SQLite's BEGIN IMMEDIATE serialized every writer before it read
            # MAX(version) or a resource head. One transaction-scoped advisory
            # lock preserves that guarantee for this compatibility query set.
            return _LegacyResult(
                self._connection.execute(
                    text("SELECT pg_advisory_xact_lock(593245723510639777)")
                )
            )
        match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)", sql, re.IGNORECASE)
        returns_identity = bool(match and match.group(1).lower() in _IDENTITY_TABLES)
        if returns_identity and not re.search(r"\bRETURNING\b", sql, re.IGNORECASE):
            sql = f"{sql.rstrip().rstrip(';')} RETURNING id"
        return _LegacyResult(
            self._connection.execute(text(sql), bindings),
            returns_identity=returns_identity,
        )


def utc_now() -> datetime:
    """Timezone-aware now, so writes never depend on the server timezone."""

    return datetime.now(timezone.utc)


def epoch_seconds(value: datetime | None) -> float | None:
    """Convert a TIMESTAMPTZ back to the epoch seconds the legacy store used."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def from_epoch_seconds(value: Any) -> datetime | None:
    """Convert a legacy epoch float into a timezone-aware datetime."""

    if value is None or value == "":
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def iso8601(value: datetime | None) -> str | None:
    """Render a timestamp the way legacy ISO columns and fingerprints expect."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def jsonb_copy(value: Any) -> Any:
    """Deep copy a JSONB value so callers cannot mutate stored state."""

    return copy.deepcopy(value)


class CreativeRepositoryBase:
    """Connection boundary shared by every creative domain repository."""

    def __init__(self, database: SystemDatabase) -> None:
        if database.url.get_backend_name() != "postgresql":
            raise ValueError("creative runtime database must be PostgreSQL")
        self.database = database
        self.store_id = self._load_or_create_store_id()

    # -- connection boundaries ---------------------------------------------

    @contextmanager
    def connection_scope(
        self, connection: Connection | None = None
    ) -> Iterator[Connection]:
        """Reuse a caller's connection, or borrow a short-lived one.

        An externally supplied connection is never committed, rolled back or
        closed here; the caller owns its transaction.
        """

        if connection is not None:
            yield connection
            return
        with self.database.connection() as owned:
            yield owned

    @contextmanager
    def transaction_scope(
        self, connection: Connection | None = None
    ) -> Iterator[Connection]:
        """Join the caller's transaction, or own a committed one."""

        if connection is not None:
            yield connection
            return
        with self.database.transaction() as owned:
            yield owned

    @contextmanager
    def _connect(self) -> Iterator[_LegacyConnection]:
        """Provide the legacy cursor shape over a PostgreSQL transaction only.

        This private compatibility boundary exists for ``free_mode_service``;
        it neither opens nor knows about a SQLite database.
        """

        with self.transaction_scope() as connection:
            yield _LegacyConnection(connection)

    @contextmanager
    def read_snapshot_scope(self) -> Iterator[Connection]:
        """Open one repeatable-read transaction for a multi-query snapshot."""

        with self.database.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                yield connection

    # -- outbound conversions ----------------------------------------------

    @classmethod
    def row_dict(cls, row: RowMapping | dict[str, Any]) -> dict[str, Any]:
        """Flatten a result row into a caller-safe dictionary.

        No ``Row``, ``RowMapping`` or ``datetime`` may escape to legacy
        callers, so timestamps become epoch seconds and JSON is deep copied.
        """

        converted: dict[str, Any] = {}
        for key, value in dict(row).items():
            if isinstance(value, datetime):
                converted[key] = epoch_seconds(value)
            elif isinstance(value, uuid.UUID):
                converted[key] = str(value)
            elif isinstance(value, (dict, list)):
                converted[key] = jsonb_copy(value)
            else:
                converted[key] = value
        return converted

    @classmethod
    def rows_dict(cls, rows: Sequence[RowMapping]) -> list[dict[str, Any]]:
        return [cls.row_dict(row) for row in rows]

    # -- workflow identity --------------------------------------------------

    def workflow_project_id(self, project_id: int | str) -> str:
        """Build the globally scoped workflow identity for a local project."""

        return f"creative:{self.store_id}:project:{int(project_id)}"

    def workflow_topic_id(self, topic_id: int | str) -> str:
        """Build the globally scoped workflow identity for a local topic."""

        return f"creative:{self.store_id}:topic:{int(topic_id)}"

    def local_project_id_from_workflow(self, project_id: Any) -> int | None:
        """Decode only project identities that belong to this creative store."""

        prefix = f"creative:{self.store_id}:project:"
        value = str(project_id or "")
        if not value.startswith(prefix):
            return None
        local_id = value.removeprefix(prefix)
        return int(local_id) if local_id.isdigit() and int(local_id) > 0 else None

    def local_topic_id_from_workflow(self, topic_id: Any) -> int | None:
        """Decode only topic identities that belong to this creative store."""

        prefix = f"creative:{self.store_id}:topic:"
        value = str(topic_id or "")
        if not value.startswith(prefix):
            return None
        local_id = value.removeprefix(prefix)
        return int(local_id) if local_id.isdigit() and int(local_id) > 0 else None

    # -- identity persistence ----------------------------------------------

    def _load_or_create_store_id(self) -> str:
        """Return the durable identity of this creative store.

        The store ID scopes every workflow identity, so the importer copies
        ``creative_store_metadata`` verbatim: a migrated store keeps the ID its
        workflow history already refers to. A store that has never held data
        mints one, matching the legacy SQLite behaviour.
        """

        with self.transaction_scope() as connection:
            connection.execute(
                pg_insert(creative_store_metadata)
                .values(key=STORE_ID_KEY, value=str(uuid.uuid4()))
                .on_conflict_do_nothing(index_elements=["key"])
            )
            value = connection.execute(
                select(creative_store_metadata.c.value).where(
                    creative_store_metadata.c.key == STORE_ID_KEY
                )
            ).scalar_one()
        try:
            return str(uuid.UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("creative store identity is invalid") from exc

    # -- health --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Report PostgreSQL reachability without exposing credentials."""

        database_health = self.database.health()
        store_readable = False
        try:
            with self.connection_scope() as connection:
                connection.execute(
                    select(creative_store_metadata.c.key).limit(1)
                ).all()
            store_readable = True
        except SQLAlchemyError:
            store_readable = False
        return {
            "backend": self.database.url.get_backend_name(),
            "database": database_health.database_url_redacted,
            "reachable": database_health.reachable,
            "schema_current": database_health.schema_current,
            "current_revision": database_health.current_revision,
            "head_revision": database_health.head_revision,
            "store_readable": store_readable,
            "store_id": self.store_id,
            "healthy": bool(
                database_health.reachable
                and database_health.schema_current
                and store_readable
            ),
        }


__all__ = [
    "CreativeRepositoryBase",
    "STORE_ID_KEY",
    "epoch_seconds",
    "from_epoch_seconds",
    "iso8601",
    "jsonb_copy",
    "utc_now",
]
