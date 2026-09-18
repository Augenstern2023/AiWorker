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
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import RowMapping, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from creative.database_schema import creative_store_metadata
from system_database import SystemDatabase


STORE_ID_KEY = "store_id"


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
