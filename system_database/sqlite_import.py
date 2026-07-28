# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Validated one-way import helpers for the legacy SQLite system database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, MetaData, Table, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, insert as postgresql_insert

from system_database.database import SystemDatabase
from system_database.schema import SYSTEM_METADATA


_JSON_COLUMNS = frozenset(
    {
        "actor_role_snapshot_json",
        "after_summary_json",
        "before_summary_json",
        "content_snapshot_json",
        "details_json",
        "payload_json",
        "trigger_config_json",
    }
)


@dataclass(frozen=True)
class SQLiteInspection:
    source_counts: dict[str, int]
    skipped_session_count: int
    primary_key_samples: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImportReport:
    source_counts: dict[str, int]
    imported_counts: dict[str, int]
    skipped_session_count: int
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_sqlite_system(source_path: str | Path) -> SQLiteInspection:
    """Read the source database without opening a write-capable SQLite connection."""

    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite system database does not exist: {source}")

    uri = source.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        source_tables = _sqlite_table_names(connection)
        counts: dict[str, int] = {}
        samples: dict[str, tuple[str, ...]] = {}
        skipped_session_count = 0
        for table_name in sorted(source_tables & set(SYSTEM_METADATA.tables)):
            count = int(
                connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            )
            if table_name == "sessions":
                skipped_session_count = count
                continue
            _validate_json_columns(connection, table_name)
            counts[table_name] = count
            samples[table_name] = _primary_key_sample(connection, table_name)

    return SQLiteInspection(
        source_counts=counts,
        skipped_session_count=skipped_session_count,
        primary_key_samples=samples,
    )


def import_sqlite_system(
    source_path: str | Path, database: SystemDatabase
) -> ImportReport:
    """Copy validated legacy rows into an already migrated PostgreSQL system database."""

    inspection = inspect_sqlite_system(source_path)
    source = Path(source_path).resolve()
    imported_counts: dict[str, int] = {}
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as sqlite_connection:
        sqlite_connection.row_factory = sqlite3.Row
        with database.transaction() as target_connection:
            for table_name in _import_order(inspection.source_counts):
                rows = _source_rows(sqlite_connection, table_name)
                if not rows:
                    imported_counts[table_name] = 0
                    continue
                table = Table(table_name, MetaData(), autoload_with=target_connection)
                values = [_target_values(dict(row), table) for row in rows]
                result = target_connection.execute(
                    postgresql_insert(table)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(literal(1))
                )
                imported_counts[table_name] = len(result.fetchall())

    verified = verify_sqlite_import(source_path, database)
    return ImportReport(
        source_counts=inspection.source_counts,
        imported_counts=imported_counts,
        skipped_session_count=inspection.skipped_session_count,
        verified=verified,
    )


def verify_sqlite_import(source_path: str | Path, database: SystemDatabase) -> bool:
    """Verify that each copied source table has at least its source row count in PostgreSQL."""

    inspection = inspect_sqlite_system(source_path)
    with database.connection() as connection:
        for table_name, source_count in inspection.source_counts.items():
            table = SYSTEM_METADATA.tables[table_name]
            target_count = int(connection.execute(select(func.count()).select_from(table)).scalar_one())
            if target_count < source_count:
                return False
    return True


def _sqlite_table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _validate_json_columns(connection: sqlite3.Connection, table_name: str) -> None:
    columns = {
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    }
    for column_name in sorted(columns & _JSON_COLUMNS):
        for row in connection.execute(
            f'SELECT rowid, "{column_name}" FROM "{table_name}" '
            f'WHERE "{column_name}" IS NOT NULL'
        ):
            try:
                json.loads(str(row[1]))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {table_name}.{column_name} at SQLite row {row[0]}"
                ) from exc


def _primary_key_sample(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    columns = list(connection.execute(f'PRAGMA table_info("{table_name}")'))
    primary_keys = [str(row[1]) for row in columns if int(row[5]) > 0]
    if len(primary_keys) != 1:
        return ()
    identifier = primary_keys[0]
    rows = connection.execute(
        f'SELECT "{identifier}" FROM "{table_name}" ORDER BY "{identifier}" LIMIT 10'
    )
    return tuple(str(row[0]) for row in rows)


def _source_rows(connection: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    return list(connection.execute(f'SELECT * FROM "{table_name}"'))


def _target_values(row: dict[str, Any], table: Any) -> dict[str, Any]:
    """Convert SQLite JSON text and legacy timestamps for PostgreSQL Core types."""

    values: dict[str, Any] = {}
    for key, value in row.items():
        if key not in table.c:
            continue
        column_type = table.c[key].type
        if value is not None and isinstance(column_type, JSONB) and isinstance(value, str):
            value = json.loads(value)
        if value is not None and isinstance(column_type, DateTime):
            value = _timestamp_value(value)
        values[key] = value
    return values


def _timestamp_value(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _import_order(source_counts: dict[str, int]) -> tuple[str, ...]:
    preferred_order = (
        "permissions",
        "users",
        "roles",
        "permission_dependencies",
        "role_permissions",
        "role_claimable_task_types",
        "user_roles",
        "departments",
        "user_departments",
        "navigation_items",
        "project_members",
        "workflow_project_member_events",
        "workflow_instances",
        "workflow_tasks",
        "workflow_task_jobs",
        "workflow_task_events",
        "workflow_submissions",
        "approval_records",
        "audit_logs",
        "scheduling_definitions",
        "scheduling_runs",
        "scheduling_alerts",
    )
    return tuple(table for table in preferred_order if table in source_counts)
