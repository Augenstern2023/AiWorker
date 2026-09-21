# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Lossless PostgreSQL -> SQLite rollback export of the creative store.

The cutover to PostgreSQL keeps a rollback window of
:data:`ROLLBACK_WINDOW_DAYS` days. During that window an operator must be able
to put the frozen legacy runtime back in front of the data it understands, so
this module writes a SQLite file that

* carries every row the PostgreSQL store holds, with its original identifiers,
* is created by the *frozen* legacy DDL wherever the legacy repository owns the
  table, so :class:`~creative.legacy_sqlite_repository.LegacySQLiteCreativeRepository`
  can genuinely read it, and
* canonicalises back to the same bytes as PostgreSQL, table by table, so the
  export is provably lossless rather than merely plausible.

Two DDL sources are reconciled, in this order:

1. ``LegacySQLiteCreativeRepository._init_schema()`` runs first against the
   temporary file. It is the frozen authority for the 20 tables the legacy
   runtime creates, and it is the only thing allowed to create them, so their
   SQL text cannot drift from what the legacy runtime expects.
2. Every table the legacy DDL does *not* create is derived from
   :data:`creative.database_schema.CREATIVE_METADATA` compiled to SQLite
   affinity: epoch ``REAL`` timestamps, ``TEXT`` JSON, ``INTEGER`` flags,
   ``INTEGER``/``TEXT`` primary keys. That mirrors how the real legacy store
   declares the same tables (verified against ``.runtime/creative/creative.db``).
3. A legacy-owned table is checked against the PostgreSQL column list, and any
   PostgreSQL column it lacks is added with ``ALTER TABLE ... ADD COLUMN``. A
   legacy-only column that is ``NOT NULL`` without a default is reported as a
   problem instead of being silently unable to receive a row.

Conversion is driven entirely by the per-table
:class:`~creative.sqlite_import.CreativeTableSpec`: ``timestamptz`` becomes the
Unix-seconds ``REAL`` float the legacy store held, ``JSONB`` becomes stable JSON
text, a boolean becomes 0/1, and ``None`` stays ``None``.

The export is published atomically: rows are written to a temporary file next
to the destination, verified there, and only then renamed onto the destination
with :func:`os.replace`. A failed export therefore never leaves a destination
path behind, and a partially written file is never readable at it.
"""

from __future__ import annotations

import gc
import json
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Float, Integer, select
from sqlalchemy.engine import Connection

from creative.database_schema import CREATIVE_METADATA
from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository
from creative.sqlite_import import (
    CREATIVE_TABLE_SPECS,
    VERSIONED_TABLES,
    CreativeImportError,
    CreativeTableSpec,
    # ``_declared_kind`` and ``_parent_cannot_precede_child`` are the importer's
    # single source of truth for a column's kind and for version ordering.
    # Reusing them keeps the reverse direction from drifting from the forward one.
    _declared_kind,
    _parent_cannot_precede_child,
    canonical_json_default,
    coerce_boolean,
    coerce_timestamp,
    redact_secrets,
    sha256_file,
    table_digest,
)
from system_database import SystemDatabase

#: How long the pre-cutover SQLite store may be restored after the cutover.
ROLLBACK_WINDOW_DAYS = 7

#: Suffix of the temporary file an export is verified in before it is published.
TEMPORARY_SUFFIX = ".rollback.tmp"


class CreativeRollbackError(ValueError):
    """Raised when a rollback export cannot be attempted at all."""


# ---------------------------------------------------------------------------
# report objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportVerification:
    """What the written SQLite file proved about itself."""

    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    row_counts: Mapping[str, int] = field(default_factory=dict)
    digests: Mapping[str, str] = field(default_factory=dict)
    integrity_ok: bool = False

    @property
    def ok(self) -> bool:
        return self.integrity_ok and not self.problems


@dataclass(frozen=True)
class RollbackReport:
    """Outcome of one rollback export.

    ``verified`` is the load-bearing field: it is true only when every check
    passed on the temporary file *and* the file was published to
    ``destination``. A report with ``verified`` false always carries the problem
    text that stopped the export.
    """

    destination: str
    sha256: str = ""
    row_counts: Mapping[str, int] = field(default_factory=dict)
    digests: Mapping[str, str] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    verified: bool = False
    published: bool = False
    rollback_window_days: int = ROLLBACK_WINDOW_DAYS
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.published and self.verified and not self.problems

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())

    @property
    def expires_at(self) -> datetime | None:
        if self.started_at is None:
            return None
        return self.started_at + timedelta(days=self.rollback_window_days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "sha256": self.sha256,
            "ok": self.ok,
            "verified": self.verified,
            "published": self.published,
            "total_rows": self.total_rows,
            "row_counts": dict(self.row_counts),
            "digests": dict(self.digests),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "rollback_window_days": self.rollback_window_days,
            "expires_at": (
                None
                if self.expires_at is None
                else canonical_json_default(self.expires_at)
            ),
            "started_at": (
                None
                if self.started_at is None
                else canonical_json_default(self.started_at)
            ),
            "finished_at": (
                None
                if self.finished_at is None
                else canonical_json_default(self.finished_at)
            ),
        }


# ---------------------------------------------------------------------------
# PostgreSQL side
# ---------------------------------------------------------------------------


def _read_postgres_state(
    database: SystemDatabase,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, str]]:
    """Read every creative table once, with the digest the export must reproduce.

    The digest is the importer's own :func:`~creative.sqlite_import.table_digest`
    over the PostgreSQL rows, so the comparison after the export is
    byte-for-byte the same test the forward migration ran.
    """

    if database.url.get_backend_name() != "postgresql":
        raise CreativeRollbackError(
            "the creative source of a rollback export must be PostgreSQL"
        )

    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    with database.connection() as connection:
        for spec in CREATIVE_TABLE_SPECS:
            rows = _select_table(connection, spec)
            rows_by_table[spec.name] = rows
            counts[spec.name] = len(rows)
            digests[spec.name] = table_digest(rows, spec)
    return rows_by_table, counts, digests


def _select_table(
    connection: Connection, spec: CreativeTableSpec
) -> list[dict[str, Any]]:
    table = CREATIVE_METADATA.tables[spec.name]
    return [
        dict(row) for row in connection.execute(select(table)).mappings().all()
    ]


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------


def _sqlite_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return tuple(row[0] for row in rows)


def _sqlite_column_type(column: Any) -> str:
    """The legacy storage affinity for a PostgreSQL-declared column.

    The affinity is the whole point of this function: the legacy store held
    timestamps as ``REAL`` epoch floats, JSON as ``TEXT`` and flags as ``INTEGER``,
    so the ``TIMESTAMPTZ``/``JSONB``/``BOOLEAN`` declarations must not leak into
    the SQLite file.
    """

    kind = _declared_kind(column.type)
    if kind == "json":
        return "TEXT"
    if kind == "boolean":
        return "INTEGER"
    if kind == "timestamp":
        return "REAL"
    declared = column.type
    if isinstance(declared, Integer):
        return "INTEGER"
    if isinstance(declared, Float):
        return "REAL"
    return "TEXT"


def _create_table_sql(spec: CreativeTableSpec, table: Any) -> str:
    """Derive SQLite DDL for a table the frozen legacy DDL does not own."""

    primary_key = [column.name for column in table.primary_key.columns]
    lines: list[str] = []
    for column in table.columns:
        if primary_key == [column.name]:
            lines.append(f'"{column.name}" {_sqlite_column_type(column)} PRIMARY KEY')
            continue
        piece = f'"{column.name}" {_sqlite_column_type(column)}'
        if not column.nullable:
            piece += " NOT NULL"
        lines.append(piece)
    if len(primary_key) > 1:
        columns = ", ".join(f'"{name}"' for name in primary_key)
        lines.append(f"PRIMARY KEY ({columns})")
    for column, parent_table, parent_column in spec.internal_references:
        lines.append(
            f'FOREIGN KEY ("{column}") REFERENCES "{parent_table}"("{parent_column}")'
        )
    body = ",\n    ".join(lines)
    return f'CREATE TABLE "{spec.name}" (\n    {body}\n)'


def _add_missing_columns(
    connection: sqlite3.Connection, spec: CreativeTableSpec, table: Any
) -> tuple[list[str], list[str]]:
    """Add every PostgreSQL column a legacy-owned table is missing.

    The frozen DDL can lag the PostgreSQL schema (``creative_breakdowns`` never
    learned ``source_snapshot_id``), so the legacy table is widened instead of
    being trusted to be complete. Each widening is reported as a warning: it is
    the visible trace of reconciling the two DDL sources. The reverse direction
    -- a legacy column that PostgreSQL does not have -- is only a problem when it
    cannot be left to its own default.
    """

    problems: list[str] = []
    warnings: list[str] = []
    declared = {
        str(row[1]): row
        for row in connection.execute(f'PRAGMA table_info("{spec.name}")').fetchall()
    }
    for column in table.columns:
        if column.name in declared:
            continue
        connection.execute(
            f'ALTER TABLE "{spec.name}" ADD COLUMN '
            f'"{column.name}" {_sqlite_column_type(column)}'
        )
        warnings.append(
            f"{spec.name}.{column.name}: the frozen legacy DDL does not declare "
            "this column, so it was added to the rollback table"
        )
    postgres_columns = {column.name for column in table.columns}
    for name, row in declared.items():
        if name in postgres_columns:
            continue
        # ``notnull`` and ``dflt_value`` are positions 3 and 4 of table_info.
        if row[3] and row[4] is None and not row[5]:
            problems.append(
                f"{spec.name}.{name}: the legacy table declares this column "
                "NOT NULL without a default and PostgreSQL has no value for it"
            )
    return problems, warnings


def _release_sqlite_handles() -> None:
    """Close SQLite handles that only the cyclic collector can reclaim.

    ``LegacySQLiteCreativeRepository`` opens its connections with ``with``
    blocks, which commit but never close, so the objects live until the cyclic
    garbage collector runs. On Windows an open handle makes the ``os.replace``
    that publishes the file fail, so the collector is run explicitly instead of
    waiting for it.
    """

    gc.collect()


def _create_sqlite_schema(path: Path) -> tuple[list[str], list[str]]:
    """Create the rollback schema, then reconcile it with PostgreSQL.

    The legacy repository is constructed against the temporary path because
    that is what makes its own DDL string constants run verbatim. It also seeds
    a fresh ``store_id``, so every legacy-owned table is emptied again before the
    PostgreSQL rows are written: the export must carry the *existing* identity,
    not a new one.
    """

    problems: list[str] = []
    warnings: list[str] = []
    LegacySQLiteCreativeRepository(path)
    _release_sqlite_handles()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in _sqlite_tables(connection):
            connection.execute(f'DELETE FROM "{table}"')
        connection.commit()

        existing = set(_sqlite_tables(connection))
        for spec in CREATIVE_TABLE_SPECS:
            table = CREATIVE_METADATA.tables[spec.name]
            if spec.name in existing:
                column_problems, column_warnings = _add_missing_columns(
                    connection, spec, table
                )
                problems.extend(column_problems)
                warnings.extend(column_warnings)
            else:
                connection.execute(_create_table_sql(spec, table))
        connection.commit()
    finally:
        connection.close()
    return problems, warnings


# ---------------------------------------------------------------------------
# conversion and writing
# ---------------------------------------------------------------------------


def sqlite_value(column: str, value: Any, spec: CreativeTableSpec) -> Any:
    """Convert one PostgreSQL value into the value the legacy store held."""

    if value is None:
        return None
    if column in spec.json_columns:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=canonical_json_default,
        )
    if column in spec.timestamp_columns:
        moment = coerce_timestamp(value)
        return None if moment is None else moment.timestamp()
    if column in spec.boolean_columns:
        flag = coerce_boolean(value)
        return None if flag is None else int(flag)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _insert_rows(
    connection: sqlite3.Connection,
    spec: CreativeTableSpec,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """Insert one table's rows with every original identifier preserved."""

    if not rows:
        return 0
    table = CREATIVE_METADATA.tables[spec.name]
    columns = [column.name for column in table.columns]
    names = ", ".join(f'"{name}"' for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    payload = [
        tuple(sqlite_value(name, row.get(name), spec) for name in columns)
        for row in rows
    ]
    connection.executemany(
        f'INSERT INTO "{spec.name}" ({names}) VALUES ({placeholders})', payload
    )
    return len(payload)


def _write_sqlite_rows(
    path: Path, rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, int]:
    """Write every table in dependency order.

    Foreign keys stay disabled while loading: the export must not depend on the
    insertion order being perfect, because the final ``PRAGMA foreign_key_check``
    is what proves the result instead.
    """

    counts: dict[str, int] = {}
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for spec in CREATIVE_TABLE_SPECS:
            counts[spec.name] = _insert_rows(
                connection, spec, rows_by_table.get(spec.name, ())
            )
        connection.commit()
    finally:
        connection.close()
    return counts


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def _digest_problems(
    expected: Mapping[str, str], actual: Mapping[str, str]
) -> list[str]:
    problems: list[str] = []
    for name in sorted(expected):
        if expected[name] != actual.get(name):
            problems.append(
                f"{name}: PostgreSQL digest {expected[name]} != "
                f"exported digest {actual.get(name)}"
            )
    return problems


def _count_problems(
    expected: Mapping[str, int], actual: Mapping[str, int]
) -> list[str]:
    problems: list[str] = []
    for name in sorted(expected):
        if expected[name] != actual.get(name):
            problems.append(
                f"{name}: PostgreSQL rows {expected[name]} != "
                f"exported rows {actual.get(name)}"
            )
    return problems


def _identity_problems(connection: sqlite3.Connection) -> list[str]:
    """The store identity must survive: a new one would orphan workflow history."""

    row = connection.execute(
        "SELECT value FROM creative_store_metadata WHERE key = 'store_id'"
    ).fetchone()
    if row is None:
        return ["creative_store_metadata: store_id is missing from the exported store"]
    value = str(row[0]).strip()
    try:
        uuid.UUID(value)
    except ValueError:
        return [f"creative_store_metadata: store_id {value!r} is not a UUID"]
    return []


def _version_chain_problems(connection: sqlite3.Connection) -> list[str]:
    """Every version row must point at a parent that exists and precedes it."""

    problems: list[str] = []
    present = set(_sqlite_tables(connection))
    for name, parent_column in VERSIONED_TABLES:
        if name not in present:
            continue
        rows = connection.execute(
            f'SELECT id, "{parent_column}" FROM "{name}"'
        ).fetchall()
        existing = {row[0] for row in rows}
        for row in rows:
            parent = row[1]
            if parent is None:
                continue
            if parent not in existing:
                problems.append(
                    f"{name}: id={row[0]} has a broken parent version {parent}"
                )
            elif _parent_cannot_precede_child(parent, row[0]):
                problems.append(
                    f"{name}: id={row[0]} points forward to parent version {parent}"
                )
    return problems


def _head_reference_problems(connection: sqlite3.Connection) -> list[str]:
    """Every resource head must point at an episode row that exists."""

    present = set(_sqlite_tables(connection))
    if "creative_resource_heads" not in present or "creative_episodes" not in present:
        return []
    head_rows = connection.execute(
        "SELECT project_id, stage, resource_ref FROM creative_resource_heads"
    ).fetchall()
    if not head_rows:
        return []
    known = {
        (row[0], row[1])
        for row in connection.execute(
            "SELECT project_id, id FROM creative_episodes"
        ).fetchall()
    }
    problems: list[str] = []
    for project_id, stage, resource_ref in head_rows:
        if resource_ref is None:
            continue
        try:
            reference = int(resource_ref)
        except (TypeError, ValueError):
            continue
        if (project_id, reference) not in known:
            problems.append(
                f"creative_resource_heads: project {project_id} stage {stage} "
                f"points at missing episode {resource_ref}"
            )
    return problems


def _verify_export(
    path: Path,
    specs: Sequence[CreativeTableSpec],
    postgres_counts: Mapping[str, int],
    postgres_digests: Mapping[str, str],
) -> ExportVerification:
    """Prove the written file is sound *before* it becomes the destination.

    Every check runs and every problem is collected, so one broken table does
    not hide the next one.
    """

    problems: list[str] = []
    warnings: list[str] = []
    row_counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    integrity_ok = False

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        # Foreign keys are enabled for the checks on purpose: the file is then
        # inspected exactly as the legacy runtime will open it.
        connection.execute("PRAGMA foreign_keys = ON")

        integrity = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        integrity_ok = integrity == ["ok"]
        if not integrity_ok:
            problems.append(
                "PRAGMA integrity_check failed: " + "; ".join(integrity)
            )

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            rendered = "; ".join(
                f"{row[0]}: rowid={row[1]} references {row[2]}" for row in violations[:10]
            )
            problems.append(
                f"PRAGMA foreign_key_check reported {len(violations)} violation(s): "
                + rendered
            )

        present = set(_sqlite_tables(connection))
        for spec in specs:
            table = CREATIVE_METADATA.tables[spec.name]
            columns = {column.name for column in table.columns}
            if spec.name not in present:
                problems.append(f"{spec.name}: the exported file has no such table")
                row_counts[spec.name] = 0
                digests[spec.name] = ""
                continue
            declared = {
                str(row[1])
                for row in connection.execute(
                    f'PRAGMA table_info("{spec.name}")'
                ).fetchall()
            }
            missing = sorted(columns - declared)
            if missing:
                problems.append(
                    f"{spec.name}: the exported table is missing PostgreSQL "
                    f"columns {', '.join(missing)}"
                )
            rows = [
                {
                    key: value
                    for key, value in dict(row).items()
                    if key in columns
                }
                for row in connection.execute(
                    f'SELECT * FROM "{spec.name}"'
                ).fetchall()
            ]
            row_counts[spec.name] = len(rows)
            try:
                digests[spec.name] = table_digest(rows, spec)
            except (CreativeImportError, TypeError, ValueError) as exc:
                digests[spec.name] = ""
                problems.append(f"{spec.name}: exported rows are not canonical: {exc}")

        problems.extend(_digest_problems(postgres_digests, digests))
        problems.extend(_count_problems(postgres_counts, row_counts))
        problems.extend(_identity_problems(connection))
        problems.extend(_version_chain_problems(connection))
        problems.extend(_head_reference_problems(connection))
    finally:
        connection.close()

    return ExportVerification(
        problems=tuple(problems),
        warnings=tuple(warnings),
        row_counts=row_counts,
        digests=digests,
        integrity_ok=integrity_ok,
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=TEMPORARY_SUFFIX,
        dir=destination.parent,
    )
    os.close(handle)
    return Path(name)


def export_sqlite_rollback(
    database: SystemDatabase,
    destination: str | Path,
) -> RollbackReport:
    """Export PostgreSQL back to a legacy-readable SQLite file.

    The destination must not already exist: a rollback export never overwrites a
    file, because the file it would overwrite may be the only remaining copy of
    something. ``FileExistsError`` is raised for that case; every other failure
    is reported in the returned :class:`RollbackReport` with ``verified`` false
    and the destination path left absent.
    """

    started_at = datetime.now(timezone.utc)
    target = Path(destination)
    if target.exists():
        raise FileExistsError(f"rollback destination already exists: {target}")

    problems: list[str] = []
    warnings: list[str] = []
    row_counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    temporary: Path | None = None
    published = False

    try:
        rows_by_table, postgres_counts, postgres_digests = _read_postgres_state(
            database
        )
        row_counts = dict(postgres_counts)
        digests = dict(postgres_digests)

        temporary = _temporary_path(target)
        schema_problems, schema_warnings = _create_sqlite_schema(temporary)
        problems.extend(schema_problems)
        warnings.extend(schema_warnings)

        if not problems:
            _write_sqlite_rows(temporary, rows_by_table)
            verification = _verify_export(
                temporary, CREATIVE_TABLE_SPECS, postgres_counts, postgres_digests
            )
            problems.extend(verification.problems)
            warnings.extend(verification.warnings)
            row_counts = dict(verification.row_counts)
            digests = dict(verification.digests)

        if not problems:
            # The destination must still be absent at the last moment: a
            # rollback export never overwrites a file, not even one that
            # appeared while this export was running.
            if target.exists():
                raise FileExistsError(
                    f"rollback destination already exists: {target}"
                )
            # Same directory, same volume: the rename is atomic, so the
            # destination only ever appears fully written and verified.
            try:
                os.replace(temporary, target)
            except OSError:
                # A Windows handle still held by a collected-late SQLite
                # connection is the one failure worth retrying once.
                _release_sqlite_handles()
                os.replace(temporary, target)
            published = True
    except FileExistsError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure must be reported
        problems.append(f"{type(exc).__name__}: {redact_secrets(str(exc))}")
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:  # pragma: no cover - defensive
                pass

    verified = published and not problems
    return RollbackReport(
        destination=str(target),
        sha256=sha256_file(target) if published else "",
        row_counts=row_counts,
        digests=digests,
        problems=tuple(problems),
        warnings=tuple(warnings),
        verified=verified,
        published=published,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )


__all__ = [
    "CreativeRollbackError",
    "ExportVerification",
    "ROLLBACK_WINDOW_DAYS",
    "RollbackReport",
    "TEMPORARY_SUFFIX",
    "export_sqlite_rollback",
    "sqlite_value",
]
