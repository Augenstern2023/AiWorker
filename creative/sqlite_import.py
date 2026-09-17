# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Read-only inspection, deterministic conversion and import of the creative store.

The legacy store is a SQLite file whose values are shaped differently from the
PostgreSQL target:

* epoch ``REAL`` timestamps became ``TIMESTAMPTZ``
* ``*_json`` ``TEXT`` columns became ``JSONB``
* 0/1 ``INTEGER`` flags became ``BOOLEAN``

Nothing here guesses. :data:`CREATIVE_TABLE_SPECS` is an immutable, explicit
per-table description derived from the *declared* column types in
:mod:`creative.database_schema`, so the conversion rules and the schema cannot
drift apart. The source is only ever opened read-only, so a failed run leaves
the legacy file byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    UniqueConstraint,
    column as sql_column,
    func,
    select,
    table as sql_table,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from creative.database_schema import (
    CREATIVE_CONTROL_TABLES,
    CREATIVE_METADATA,
    EXPECTED_CREATIVE_TABLES,
)
from system_database import SystemDatabase


IMPORT_RUNS_TABLE = "creative_database_import_runs"

#: Tables the target declares that the legacy store may legitimately lack.
#: A missing table is a known historical variation and is imported as empty;
#: a table that exists but has drifted columns is a hard failure.
EMPTY_WHEN_ABSENT_TABLES: frozenset[str] = frozenset({"production_status"})

#: Columns whose values point at rows owned by other domains (users, workflow
#: tasks, submissions). They are deliberately not foreign keys, so the importer
#: has to classify them instead of relying on the database.
CROSS_DOMAIN_REFERENCE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("created_by", "users", "user_id"),
    ("actor_id", "users", "user_id"),
    ("actor_user_id", "users", "user_id"),
    ("workflow_task_id", "workflow_tasks", "task_id"),
    ("submission_id", "workflow_submissions", "submission_id"),
)

#: ``(table, column)`` pairs whose unresolved references are legitimate
#: historical gaps rather than orphans. Task 15's cross-domain audit document
#: is what fills this in; empty means every unresolved reference is fatal.
LEGAL_HISTORICAL_GAP_REFERENCES: frozenset[tuple[str, str]] = frozenset()


class CreativeImportError(ValueError):
    """Raised when the legacy source or the target cannot be migrated safely."""


# ---------------------------------------------------------------------------
# explicit per-table specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreativeTableSpec:
    """How one legacy table converts into its PostgreSQL counterpart."""

    name: str
    primary_key: tuple[str, ...]
    json_columns: frozenset[str]
    timestamp_columns: frozenset[str]
    boolean_columns: frozenset[str]
    dependencies: tuple[str, ...]
    sort_columns: tuple[str, ...]
    unique_keys: tuple[tuple[str, ...], ...]
    partial_unique_keys: tuple[tuple[str, ...], ...]
    internal_references: tuple[tuple[str, str, str], ...]
    non_nullable_columns: frozenset[str]

    @property
    def is_control_table(self) -> bool:
        return self.name in CREATIVE_CONTROL_TABLES


def _declared_kind(column_type: Any) -> str:
    if isinstance(column_type, JSONB):
        return "json"
    if isinstance(column_type, Boolean):
        return "boolean"
    if isinstance(column_type, DateTime):
        return "timestamp"
    return "scalar"


def _unique_column_sets(table: Any) -> tuple[tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    """Split declared unique keys into fully-enforced and partial ones.

    A partial unique index only constrains the rows matching its predicate, so
    the importer must not apply it to every row. PostgreSQL enforces it on
    insert, which is the correct place for that check.
    """

    primary_key = tuple(column.name for column in table.primary_key.columns)
    complete: set[tuple[str, ...]] = set()
    partial: set[tuple[str, ...]] = set()

    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            columns = tuple(column.name for column in constraint.columns)
            if columns and columns != primary_key:
                complete.add(columns)

    for index in table.indexes:
        if not index.unique:
            continue
        columns = tuple(column.name for column in index.columns)
        if not columns or columns == primary_key:
            continue
        predicate = None
        try:
            predicate = index.dialect_options["postgresql"].get("where")
        except (KeyError, AttributeError):  # pragma: no cover - defensive
            predicate = None
        (partial if predicate is not None else complete).add(columns)

    return (
        tuple(sorted(complete)),
        tuple(sorted(partial)),
    )


def _build_table_spec(table: Any, creative_table_names: frozenset[str]) -> CreativeTableSpec:
    primary_key = tuple(column.name for column in table.primary_key.columns)
    json_columns: set[str] = set()
    timestamp_columns: set[str] = set()
    boolean_columns: set[str] = set()

    for column in table.columns:
        kind = _declared_kind(column.type)
        if kind == "json":
            json_columns.add(column.name)
        elif kind == "timestamp":
            timestamp_columns.add(column.name)
        elif kind == "boolean":
            boolean_columns.add(column.name)

    dependencies = tuple(
        sorted(
            {
                foreign_key.column.table.name
                for foreign_key in table.foreign_keys
                if foreign_key.column.table.name in creative_table_names
                # A self-reference orders rows inside a table, not the tables
                # themselves, so it must not appear as an import dependency.
                and foreign_key.column.table.name != table.name
            }
        )
    )
    internal_references = tuple(
        sorted(
            (
                foreign_key.parent.name,
                foreign_key.column.table.name,
                foreign_key.column.name,
            )
            for foreign_key in table.foreign_keys
            if foreign_key.column.table.name in creative_table_names
        )
    )
    complete_unique, partial_unique = _unique_column_sets(table)

    return CreativeTableSpec(
        name=table.name,
        primary_key=primary_key,
        json_columns=frozenset(json_columns),
        timestamp_columns=frozenset(timestamp_columns),
        boolean_columns=frozenset(boolean_columns),
        dependencies=dependencies,
        # The primary key is a total order on the rows of every legacy table,
        # so dumping in primary-key order is reproducible without a second
        # per-table ordering decision.
        sort_columns=primary_key,
        unique_keys=complete_unique,
        partial_unique_keys=partial_unique,
        internal_references=internal_references,
        non_nullable_columns=frozenset(
            column.name for column in table.columns if not column.nullable
        ),
    )


def _build_table_specs() -> tuple[CreativeTableSpec, ...]:
    """Build the specification tuple in dependency order.

    ``MetaData.sorted_tables`` already returns tables in foreign-key dependency
    order, which is exactly the import order the plan fixes as
    metadata -> topics -> breakdowns -> projects -> seeds/versions ->
    heads/contexts -> operations -> reviews/optimizations -> deliveries/exports.
    """

    creative_table_names = frozenset(CREATIVE_METADATA.tables)
    specs = [
        _build_table_spec(table, creative_table_names)
        for table in CREATIVE_METADATA.sorted_tables
    ]
    root = [spec for spec in specs if spec.is_control_table]
    business = [spec for spec in specs if not spec.is_control_table]
    return tuple(business + root)


CREATIVE_TABLE_SPECS: tuple[CreativeTableSpec, ...] = _build_table_specs()
SPEC_BY_NAME: Mapping[str, CreativeTableSpec] = {
    spec.name: spec for spec in CREATIVE_TABLE_SPECS
}
BUSINESS_TABLE_SPECS: tuple[CreativeTableSpec, ...] = tuple(
    spec for spec in CREATIVE_TABLE_SPECS if not spec.is_control_table
)
BUSINESS_TABLE_NAMES: tuple[str, ...] = tuple(
    spec.name for spec in BUSINESS_TABLE_SPECS
)
IMPORT_ORDER: tuple[str, ...] = tuple(spec.name for spec in CREATIVE_TABLE_SPECS)


# ---------------------------------------------------------------------------
# canonical conversion
# ---------------------------------------------------------------------------


def canonical_json_default(value: Any) -> Any:
    """Serialise the value types the canonical hash needs but JSON lacks."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def coerce_timestamp(value: Any) -> datetime | None:
    """Convert a legacy timestamp into an aware UTC datetime.

    Legacy stores are inconsistent: most tables used epoch ``REAL`` seconds,
    while a few wrote ISO 8601 text into a column that later became
    ``TIMESTAMPTZ``. Both are accepted and normalised to the same instant.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool):
        raise ValueError(f"boolean is not a timestamp: {value!r}")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text_value = str(value).strip()
    try:
        return datetime.fromtimestamp(float(text_value), tz=timezone.utc)
    except ValueError:
        pass
    normalized = text_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"unrecognised timestamp {value!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def coerce_boolean(value: Any) -> bool | None:
    """Convert a legacy 0/1 flag into a real boolean."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(int(value))
    raise ValueError(f"not a 0/1 flag: {value!r}")


def parse_json_value(value: Any) -> Any:
    """Parse a legacy JSON text column into native Python."""

    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def row_label(row: Mapping[str, Any], spec: CreativeTableSpec, index: int | None = None) -> str:
    """Identify a source row precisely enough to act on the report."""

    keys = spec.primary_key or tuple(sorted(row))[:1]
    rendered = ", ".join(f"{name}={row.get(name)!r}" for name in keys)
    return f"row {index} ({rendered})" if index is not None else f"row ({rendered})"


def normalize_for_target(
    row: Mapping[str, Any],
    spec: CreativeTableSpec,
    *,
    index: int | None = None,
) -> dict[str, Any]:
    """Convert one legacy row into target-ready Python values.

    Raises :class:`CreativeImportError` naming the offending table, column and
    row so a failed migration is actionable.
    """

    normalized: dict[str, Any] = dict(row)
    for column in spec.json_columns:
        if column not in normalized:
            continue
        try:
            normalized[column] = parse_json_value(normalized[column])
        except ValueError as exc:
            raise CreativeImportError(
                f"{spec.name}.{column}: {row_label(normalized, spec, index)} {exc}"
            ) from exc
    for column in spec.timestamp_columns:
        if column not in normalized:
            continue
        try:
            normalized[column] = coerce_timestamp(normalized[column])
        except ValueError as exc:
            raise CreativeImportError(
                f"{spec.name}.{column}: {row_label(normalized, spec, index)} {exc}"
            ) from exc
    for column in spec.boolean_columns:
        if column not in normalized:
            continue
        try:
            normalized[column] = coerce_boolean(normalized[column])
        except ValueError as exc:
            raise CreativeImportError(
                f"{spec.name}.{column}: {row_label(normalized, spec, index)} {exc}"
            ) from exc

    for column in spec.non_nullable_columns:
        if column in normalized and normalized[column] is None:
            raise CreativeImportError(
                f"{spec.name}.{column}: {row_label(normalized, spec, index)} "
                "is NULL in the source but NOT NULL in PostgreSQL"
            )
    return normalized


def canonical_row(
    row: Mapping[str, Any],
    spec: CreativeTableSpec,
    *,
    index: int | None = None,
) -> bytes:
    """Return the canonical byte form of a row, used for migration hashes.

    Float epoch timestamps are normalised to UTC ISO 8601 first, so a hash
    comparison never depends on float formatting. Business content fingerprint
    columns are carried through as the original strings.
    """

    normalized = normalize_for_target(row, spec, index=index)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=canonical_json_default,
    ).encode("utf-8")


def table_digest(rows: Iterable[Mapping[str, Any]], spec: CreativeTableSpec) -> str:
    """Digest a whole table in primary-key order."""

    ordered = sorted(rows, key=lambda row: _sort_key(row, spec))
    hasher = hashlib.sha256()
    for index, row in enumerate(ordered):
        hasher.update(canonical_row(row, spec, index=index))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _sort_key(row: Mapping[str, Any], spec: CreativeTableSpec) -> tuple[Any, ...]:
    def component(value: Any) -> tuple[int, Any]:
        # None sorts last and mixed types never compare against each other.
        if value is None:
            return (1, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, float(value))
        return (0, str(value))

    return tuple(component(row.get(name)) for name in spec.sort_columns)


# ---------------------------------------------------------------------------
# source inspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableInspection:
    name: str
    present: bool
    row_count: int
    digest: str
    primary_key_range: tuple[Any, Any] | None
    missing_columns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreativeInspectionReport:
    source_path: str
    source_sha256: str
    source_size: int
    integrity_ok: bool
    sqlite_tables: tuple[str, ...]
    tables: tuple[TableInspection, ...]
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.integrity_ok and not self.problems

    @property
    def row_counts(self) -> dict[str, int]:
        return {table.name: table.row_count for table in self.tables}

    @property
    def id_ranges(self) -> dict[str, tuple[Any, Any] | None]:
        return {table.name: table.primary_key_range for table in self.tables}

    @property
    def digests(self) -> dict[str, str]:
        return {table.name: table.digest for table in self.tables}

    @property
    def total_rows(self) -> int:
        return sum(table.row_count for table in self.tables)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "integrity_ok": self.integrity_ok,
            "ok": self.ok,
            "total_rows": self.total_rows,
            "row_counts": self.row_counts,
            "id_ranges": {
                name: (None if limits is None else list(limits))
                for name, limits in self.id_ranges.items()
            },
            "digests": self.digests,
            "warnings": list(self.warnings),
            "problems": list(self.problems),
        }


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def open_read_only(source: str | Path) -> sqlite3.Connection:
    """Open the legacy store without any possibility of writing to it."""

    path = Path(source).resolve()
    if not path.is_file():
        raise CreativeImportError(f"legacy creative database not found: {path}")
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def _source_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return tuple(row[0] for row in rows)


def _source_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return tuple(row[1] for row in rows)


def _fetch_rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return list(connection.execute(f'SELECT * FROM "{table}"').fetchall())


def _inspect_table(
    connection: sqlite3.Connection,
    spec: CreativeTableSpec,
) -> tuple[TableInspection, list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    if spec.name not in _source_tables(connection):
        if spec.name in EMPTY_WHEN_ABSENT_TABLES:
            warnings.append(
                f"{spec.name}: absent from the legacy source; nothing to import"
            )
        else:
            warnings.append(
                f"{spec.name}: absent from the legacy source; imported as empty "
                "under the known historical schema variation"
            )
        return (
            TableInspection(
                name=spec.name,
                present=False,
                row_count=0,
                digest=hashlib.sha256(b"").hexdigest(),
                primary_key_range=None,
                notes=("absent",),
            ),
            problems,
            warnings,
        )

    columns = _source_columns(connection, spec.name)
    missing_columns = tuple(
        column for column in spec.primary_key if column not in columns
    )
    if missing_columns:
        problems.append(
            f"{spec.name}: source table is missing primary key columns "
            f"{', '.join(missing_columns)}"
        )
        return (
            TableInspection(
                name=spec.name,
                present=True,
                row_count=0,
                digest=hashlib.sha256(b"").hexdigest(),
                primary_key_range=None,
                missing_columns=missing_columns,
            ),
            problems,
            warnings,
        )

    checked_columns = sorted(
        spec.json_columns | spec.timestamp_columns | spec.boolean_columns
    )
    unknown_columns = tuple(
        column
        for column in checked_columns
        if column not in columns
    )
    if unknown_columns:
        for column in unknown_columns:
            problems.append(
                f"{spec.name}.{column}: column is missing from the legacy source table"
            )

    rows = [
        dict(row)
        for row in sorted(
            (_fetch_rows(connection, spec.name)),
            key=lambda row: _sort_key(dict(row), spec),
        )
    ]

    # Canonicalise once, in primary-key order. Both the migration digest and
    # the conversion errors come from this single pass, so a corrupt source can
    # never make the digest computation raise before the problem is reported.
    canonical_forms: list[bytes] = []
    conversion_failed = False
    for index, mapping in enumerate(rows):
        try:
            canonical_forms.append(canonical_row(mapping, spec, index=index))
        except CreativeImportError as exc:
            conversion_failed = True
            problems.append(str(exc))

    if conversion_failed:
        digest = ""
        notes = ("digest not computed: the source has conversion errors",)
    else:
        hasher = hashlib.sha256()
        for form in canonical_forms:
            hasher.update(form)
            hasher.update(b"\n")
        digest = hasher.hexdigest()
        notes = ()

    if not conversion_failed:
        problems.extend(_unique_key_problems(rows, spec))
        problems.extend(_reference_problems(connection, rows, spec))

    return (
        TableInspection(
            name=spec.name,
            present=True,
            row_count=len(rows),
            digest=digest,
            primary_key_range=_primary_key_range(rows, spec),
            notes=notes,
        ),
        problems,
        warnings,
    )


def _unique_key_problems(
    rows: Sequence[Mapping[str, Any]], spec: CreativeTableSpec
) -> list[str]:
    problems: list[str] = []
    for key_columns in spec.unique_keys:
        seen: dict[tuple[Any, ...], int] = {}
        for index, mapping in enumerate(rows):
            key = tuple(mapping.get(column) for column in key_columns)
            if any(value is None for value in key):
                continue
            if key in seen:
                problems.append(
                    f"{spec.name}: {row_label(mapping, spec, index)} duplicates the "
                    f"unique key ({', '.join(key_columns)}) already used by "
                    f"{row_label(mapping, spec, seen[key])}"
                )
            else:
                seen[key] = index
    return problems


def _reference_problems(
    connection: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    spec: CreativeTableSpec,
) -> list[str]:
    """Check the internal references that the target declares as foreign keys."""

    problems: list[str] = []
    source_tables = _source_tables(connection)
    for column, parent_table, parent_column in spec.internal_references:
        if parent_table not in source_tables:
            continue
        if parent_table == spec.name:
            valid = {mapping.get(parent_column) for mapping in rows}
        else:
            valid = {
                dict(row).get(parent_column)
                for row in _fetch_rows(connection, parent_table)
            }
        for index, mapping in enumerate(rows):
            value = mapping.get(column)
            if value is None or value in valid:
                continue
            problems.append(
                f"{spec.name}.{column}: {row_label(mapping, spec, index)} references "
                f"missing {parent_table}.{parent_column}={value!r}"
            )
    return problems


def _primary_key_range(
    rows: Sequence[Mapping[str, Any]], spec: CreativeTableSpec
) -> tuple[Any, Any] | None:
    if not rows or not spec.primary_key:
        return None
    if len(spec.primary_key) != 1:
        return None
    column = spec.primary_key[0]
    values = [mapping.get(column) for mapping in rows]
    numeric = [
        value
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if len(numeric) == len(values) and numeric:
        return (min(numeric), max(numeric))
    rendered = sorted(str(value) for value in values if value is not None)
    if not rendered:
        return None
    return (rendered[0], rendered[-1])


def inspect_sqlite_creative(
    source: str | Path,
    *,
    strict: bool = True,
    expected_tables: Sequence[str] | None = None,
) -> CreativeInspectionReport:
    """Read-only inspection of the legacy creative store.

    Never writes to ``source``. With ``strict`` (the default) any fatal problem
    raises :class:`CreativeImportError`; pass ``strict=False`` to receive the
    report with its ``problems`` populated instead.
    """

    path = Path(source).resolve()
    if not path.is_file():
        raise CreativeImportError(f"legacy creative database not found: {path}")
    source_hash = sha256_file(path)
    problems: list[str] = []
    warnings: list[str] = []

    with open_read_only(path) as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity_ok = [row[0] for row in integrity_rows] == ["ok"]
        if not integrity_ok:
            problems.append(
                "PRAGMA integrity_check failed: "
                + "; ".join(str(row[0]) for row in integrity_rows)
            )

        sqlite_tables = _source_tables(connection)
        wanted = tuple(expected_tables) if expected_tables is not None else BUSINESS_TABLE_NAMES
        specs = [SPEC_BY_NAME[name] for name in wanted if name in SPEC_BY_NAME]

        unknown = sorted(set(sqlite_tables) - set(EXPECTED_CREATIVE_TABLES))
        if unknown:
            warnings.append(
                "legacy source contains tables with no PostgreSQL counterpart: "
                + ", ".join(unknown)
            )

        inspections: list[TableInspection] = []
        for spec in specs:
            inspection, table_problems, table_warnings = _inspect_table(connection, spec)
            inspections.append(inspection)
            problems.extend(table_problems)
            warnings.extend(table_warnings)

    report = CreativeInspectionReport(
        source_path=str(path),
        source_sha256=source_hash,
        source_size=path.stat().st_size,
        integrity_ok=integrity_ok,
        sqlite_tables=sqlite_tables,
        tables=tuple(inspections),
        problems=tuple(problems),
        warnings=tuple(warnings),
    )
    if strict and problems:
        raise CreativeImportError(
            f"legacy creative source failed inspection ({len(problems)} problem(s)):\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )
    return report


# ---------------------------------------------------------------------------
# target inspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossDomainReferenceReport:
    table: str
    column: str
    parent_table: str
    parent_column: str
    distinct_values: int
    resolved: int
    unresolved: tuple[str, ...]
    legal_gaps: tuple[str, ...]

    @property
    def illegal_orphans(self) -> tuple[str, ...]:
        return tuple(
            value for value in self.unresolved if value not in set(self.legal_gaps)
        )


def inspect_target_creative(database: SystemDatabase) -> dict[str, Any]:
    """Check the PostgreSQL target is empty and classify cross-domain references.

    Only the business tables are considered when deciding whether the target is
    empty: the import control table is expected to hold history.
    """

    from creative.database_schema import CREATIVE_METADATA as metadata

    non_empty: dict[str, int] = {}
    with database.connection() as connection:
        for name in BUSINESS_TABLE_NAMES:
            table = metadata.tables[name]
            count = connection.execute(select(func.count()).select_from(table)).scalar_one()
            if count:
                non_empty[name] = int(count)

    return {
        "empty": not non_empty,
        "non_empty_tables": non_empty,
        "business_tables": list(BUSINESS_TABLE_NAMES),
    }


def inspect_cross_domain_references(
    database: SystemDatabase,
    *,
    source: str | Path,
) -> tuple[CrossDomainReferenceReport, ...]:
    """Classify creative references into other domains.

    A reference is complete when it resolves, a legal historical gap when the
    plan's audit document has declared it acceptable, and an illegal orphan
    otherwise. The caller decides what to do with illegal orphans; the importer
    treats them as fatal.
    """

    from creative.database_schema import CREATIVE_METADATA as metadata

    reports: list[CrossDomainReferenceReport] = []
    with open_read_only(source) as source_connection:
        source_tables = set(_source_tables(source_connection))
        with database.connection() as connection:
            for spec in BUSINESS_TABLE_SPECS:
                if spec.name not in source_tables:
                    continue
                source_columns = set(_source_columns(source_connection, spec.name))
                for column, parent_table, parent_column in CROSS_DOMAIN_REFERENCE_COLUMNS:
                    if column not in source_columns:
                        continue
                    if column not in metadata.tables[spec.name].columns:
                        continue
                    rows = _fetch_rows(source_connection, spec.name)
                    values = {
                        str(dict(row).get(column))
                        for row in rows
                        if dict(row).get(column) not in (None, "")
                    }
                    if not values:
                        continue
                    resolved = _resolved_reference_values(
                        connection, parent_table, parent_column, values
                    )
                    unresolved = tuple(sorted(values - resolved))
                    legal = tuple(
                        value
                        for value in unresolved
                        if (spec.name, column) in LEGAL_HISTORICAL_GAP_REFERENCES
                    )
                    reports.append(
                        CrossDomainReferenceReport(
                            table=spec.name,
                            column=column,
                            parent_table=parent_table,
                            parent_column=parent_column,
                            distinct_values=len(values),
                            resolved=len(resolved),
                            unresolved=unresolved,
                            legal_gaps=legal,
                        )
                    )
    return tuple(reports)


def _resolved_reference_values(
    connection: Connection,
    parent_table: str,
    parent_column: str,
    values: set[str],
) -> set[str]:
    """Return the subset of ``values`` that exists in the parent table.

    The parent tables belong to other domains, so they may be absent in a test
    or partially-migrated target. An absent parent means nothing resolves, and
    the caller reports every value as unresolved rather than crashing.
    """

    if not values:
        return set()
    if not _table_exists(connection, parent_table):
        return set()
    if not _column_exists(connection, parent_table, parent_column):
        return set()

    parent = sql_table(parent_table, sql_column(parent_column))
    resolved: set[str] = set()
    for chunk in _chunks(sorted(values), 500):
        rows = connection.execute(
            select(parent.c[parent_column]).where(parent.c[parent_column].in_(chunk))
        ).all()
        resolved.update(str(row[0]) for row in rows)
    return resolved


def _table_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.execute(
            sql_text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :name"
            ),
            {"name": name},
        ).first()
    )


def _column_exists(connection: Connection, table: str, column: str) -> bool:
    return bool(
        connection.execute(
            sql_text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).first()
    )


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


__all__ = [
    "BUSINESS_TABLE_NAMES",
    "BUSINESS_TABLE_SPECS",
    "CREATIVE_TABLE_SPECS",
    "CROSS_DOMAIN_REFERENCE_COLUMNS",
    "CreativeImportError",
    "CreativeInspectionReport",
    "CreativeTableSpec",
    "CrossDomainReferenceReport",
    "IMPORT_ORDER",
    "IMPORT_RUNS_TABLE",
    "SPEC_BY_NAME",
    "TableInspection",
    "canonical_json_default",
    "canonical_row",
    "coerce_boolean",
    "coerce_timestamp",
    "inspect_cross_domain_references",
    "inspect_sqlite_creative",
    "inspect_target_creative",
    "normalize_for_target",
    "open_read_only",
    "parse_json_value",
    "sha256_file",
    "table_digest",
]
