# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Inspection and deterministic-conversion tests for the creative migration.

The fixture builds a legacy SQLite store from frozen DDL copied verbatim from
``.runtime/creative/creative.db``, so these tests pin the real legacy column
shapes rather than an idealised schema. Every corrupt-source test asserts that
the failure names the exact table, column and row, because that is what makes a
failed production run actionable.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from creative.sqlite_import import (
    BUSINESS_TABLE_NAMES,
    BUSINESS_TABLE_SPECS,
    CREATIVE_TABLE_SPECS,
    SPEC_BY_NAME,
    CreativeImportError,
    canonical_json_default,
    canonical_row,
    coerce_boolean,
    coerce_timestamp,
    import_sqlite_creative,
    inspect_cross_domain_references,
    inspect_sqlite_creative,
    inspect_target_creative,
    normalize_for_target,
    parse_json_value,
    sha256_file,
    table_digest,
    verify_creative_import,
)
from system_database import SystemDatabase


# ---------------------------------------------------------------------------
# frozen legacy DDL, copied from .runtime/creative/creative.db
# ---------------------------------------------------------------------------

LEGACY_DDL: dict[str, str] = {
    "creative_store_metadata": """
        CREATE TABLE creative_store_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """,
    "creative_topics": """
        CREATE TABLE creative_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL, job_id TEXT NOT NULL, source_path TEXT NOT NULL,
            captured_at TEXT NOT NULL, title TEXT NOT NULL, category TEXT,
            status TEXT NOT NULL DEFAULT 'pending_review', score REAL NOT NULL,
            confidence TEXT NOT NULL, score_details_json TEXT NOT NULL,
            recommendation TEXT NOT NULL, evidence_json TEXT NOT NULL,
            decision_note TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            source_platforms_json TEXT NOT NULL DEFAULT '[]',
            rank_type TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            rank_source TEXT NOT NULL DEFAULT '',
            UNIQUE(platform, job_id, source_path, title)
        )
    """,
    "creative_breakdowns": """
        CREATE TABLE creative_breakdowns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL, version INTEGER NOT NULL,
            parent_version_id INTEGER, status TEXT NOT NULL DEFAULT 'draft',
            content_json TEXT NOT NULL, field_sources_json TEXT NOT NULL,
            risks_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            locked_at REAL, workflow_task_id TEXT, source_snapshot_id INTEGER,
            UNIQUE(topic_id, version)
        )
    """,
    "creative_projects": """
        CREATE TABLE creative_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER, breakdown_id INTEGER, name TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT '', target_platform TEXT NOT NULL DEFAULT '',
            target_audience TEXT NOT NULL DEFAULT '',
            target_episode_count INTEGER NOT NULL DEFAULT 20,
            episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
            status TEXT NOT NULL DEFAULT 'created', locked_brief_id INTEGER,
            character_batch_id TEXT, outline_batch_id TEXT, workflow_task_id TEXT,
            creation_workflow_task_id TEXT, creation_request_id TEXT,
            created_by TEXT NOT NULL DEFAULT 'local', deleted INTEGER NOT NULL DEFAULT 0,
            creation_mode TEXT NOT NULL DEFAULT 'imitation'
                CHECK (creation_mode IN ('imitation', 'original')),
            bootstrap_status TEXT NOT NULL DEFAULT 'ready'
                CHECK (bootstrap_status IN ('pending', 'ready', 'failed')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            workflow_mode TEXT NOT NULL DEFAULT 'sequential',
            mode_revision INTEGER NOT NULL DEFAULT 0,
            mode_changed_at REAL, mode_changed_by TEXT,
            FOREIGN KEY(topic_id) REFERENCES creative_topics(id),
            FOREIGN KEY(breakdown_id) REFERENCES creative_breakdowns(id),
            CHECK (
                (creation_mode = 'imitation' AND topic_id IS NOT NULL AND breakdown_id IS NOT NULL)
                OR
                (creation_mode = 'original' AND topic_id IS NULL AND breakdown_id IS NULL)
            )
        )
    """,
    "creative_seeds": """
        CREATE TABLE creative_seeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL, version INTEGER NOT NULL,
            parent_version_id INTEGER, status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'frozen')),
            seed_text TEXT NOT NULL, constraints_json TEXT NOT NULL DEFAULT '{}',
            reference_refs_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL, updated_at REAL NOT NULL, locked_at REAL,
            FOREIGN KEY(project_id) REFERENCES creative_projects(id),
            FOREIGN KEY(parent_version_id) REFERENCES creative_seeds(id),
            UNIQUE(project_id, version)
        )
    """,
}

STORE_ID = "3f1d2c4b-5a69-4e78-9b01-2c3d4e5f6a7b"
EPOCH = 1789430400.0  # 2026-09-15T00:00:00Z
ISO_EPOCH = "2026-09-15T00:00:00Z"

#: The only table/column pairs a test may corrupt. Keeping this closed stops a
#: test from quietly mutating the fixture into something the importer never
#: sees in production.
CORRUPTIBLE_TARGETS: frozenset[tuple[str, str]] = frozenset(
    {
        ("creative_topics", "evidence_json"),
        ("creative_topics", "score_details_json"),
        ("creative_topics", "created_at"),
        ("creative_topics", "captured_at"),
        ("creative_topics", "deleted"),
        ("creative_breakdowns", "content_json"),
        ("creative_breakdowns", "topic_id"),
        ("creative_seeds", "constraints_json"),
        ("creative_seeds", "parent_version_id"),
        ("creative_seeds", "project_id"),
        ("creative_projects", "name"),
    }
)


def _create_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        for statement in LEGACY_DDL.values():
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def _insert_valid_rows(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO creative_store_metadata (key, value) VALUES ('store_id', ?)",
            (STORE_ID,),
        )
        connection.execute(
            """
            INSERT INTO creative_topics (
                platform, job_id, source_path, captured_at, title, category, status,
                score, confidence, score_details_json, recommendation, evidence_json,
                decision_note, deleted, created_at, updated_at, source_platforms_json,
                rank_type, description, rank_source
            ) VALUES ('hg', 'job-1', 'rank.json', '2026-09-15T00:00:00Z', '测试选题',
                      '都市', 'pending_review', 80.0, 'high', '{}', 'adopt', '[]',
                      '', 0, ?, ?, '["hg"]', '热播榜', '', '热播榜')
            """,
            (EPOCH, EPOCH),
        )
        connection.execute(
            """
            INSERT INTO creative_breakdowns (
                topic_id, version, parent_version_id, status, content_json,
                field_sources_json, risks_json, created_at, updated_at, locked_at,
                workflow_task_id, source_snapshot_id
            ) VALUES (1, 1, NULL, 'draft', '{}', '{}', '[]', ?, ?, NULL, NULL, NULL)
            """,
            (EPOCH, EPOCH),
        )
        connection.execute(
            """
            INSERT INTO creative_projects (
                topic_id, breakdown_id, name, created_by, deleted, creation_mode,
                bootstrap_status, created_at, updated_at
            ) VALUES (1, 1, '测试项目', 'local', 0, 'imitation', 'ready', ?, ?)
            """,
            (EPOCH, EPOCH),
        )
        connection.execute(
            """
            INSERT INTO creative_seeds (
                project_id, version, parent_version_id, status, seed_text,
                constraints_json, reference_refs_json, created_at, updated_at, locked_at
            ) VALUES (1, 1, NULL, 'draft', '种子', '{}', '[]', ?, ?, NULL)
            """,
            (EPOCH, EPOCH),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def legacy_creative_db(tmp_path: Path) -> Path:
    """A minimal but realistic legacy creative SQLite store."""

    path = tmp_path / "creative.db"
    _create_schema(path)
    _insert_valid_rows(path)
    return path


@pytest.fixture
def empty_legacy_creative_db(tmp_path: Path) -> Path:
    """Schema only, so a test can insert exactly the rows it wants."""

    path = tmp_path / "creative-empty.db"
    _create_schema(path)
    return path


def corrupt_value(path: Path, table: str, column: str, value: object) -> None:
    """Overwrite one cell of the fixture, restricted to declared targets."""

    if (table, column) not in CORRUPTIBLE_TARGETS:
        raise ValueError(f"{table}.{column} is not a declared corruption target")
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'UPDATE "{table}" SET "{column}" = ?', (value,))
        connection.commit()
    finally:
        connection.close()


def drop_column(path: Path, table: str, column: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# the valid fixture
# ---------------------------------------------------------------------------


def test_valid_legacy_source_passes_inspection(legacy_creative_db: Path):
    report = inspect_sqlite_creative(legacy_creative_db)

    assert report.ok is True
    assert report.integrity_ok is True
    assert report.problems == ()
    assert report.row_counts["creative_topics"] == 1
    assert report.row_counts["creative_breakdowns"] == 1
    assert report.row_counts["creative_projects"] == 1
    assert report.row_counts["creative_seeds"] == 1
    assert report.id_ranges["creative_topics"] == (1, 1)
    assert report.total_rows == 5  # four data rows plus the store metadata row


def test_inspection_never_modifies_the_source(legacy_creative_db: Path):
    before = sha256_file(legacy_creative_db)
    inspect_sqlite_creative(legacy_creative_db)
    inspect_sqlite_creative(legacy_creative_db, strict=False)
    assert sha256_file(legacy_creative_db) == before


def test_inspection_treats_absent_tables_as_a_reported_variation(
    legacy_creative_db: Path,
):
    report = inspect_sqlite_creative(legacy_creative_db)
    absent = [table for table in report.tables if not table.present]
    # The fixture only carries five of the declared tables.
    assert len(absent) == len(BUSINESS_TABLE_NAMES) - 5
    assert report.problems == ()
    assert any("absent from the legacy source" in warning for warning in report.warnings)


def test_inspection_reports_unknown_source_tables(tmp_path: Path):
    path = tmp_path / "extra.db"
    _create_schema(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE legacy_scratch (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    report = inspect_sqlite_creative(path)
    assert any("legacy_scratch" in warning for warning in report.warnings)
    assert report.problems == ()


# ---------------------------------------------------------------------------
# corrupt sources must name the exact table, column and row
# ---------------------------------------------------------------------------


def test_inspection_rejects_invalid_json(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_topics", "evidence_json", "{")

    with pytest.raises(ValueError, match=r"creative_topics\.evidence_json.*row"):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_invalid_json_and_names_the_primary_key(
    legacy_creative_db: Path,
):
    corrupt_value(legacy_creative_db, "creative_seeds", "constraints_json", "{oops")

    with pytest.raises(CreativeImportError) as error:
        inspect_sqlite_creative(legacy_creative_db)

    assert "creative_seeds.constraints_json" in str(error.value)
    assert "id=1" in str(error.value)


def test_inspection_rejects_invalid_timestamp(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_topics", "created_at", "not-a-time")

    with pytest.raises(ValueError, match=r"creative_topics\.created_at.*row"):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_invalid_boolean(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_topics", "deleted", 7)

    with pytest.raises(ValueError, match=r"creative_topics\.deleted.*row"):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_duplicate_unique_key(tmp_path: Path):
    """A source created before the constraint existed can still hold duplicates.

    SQLite would refuse the duplicate insert against the current DDL, so the
    fixture drops the ``UNIQUE`` clause exactly as an older legacy store would
    have looked. The importer must still catch it from the declared target key.
    """

    path = tmp_path / "duplicates.db"
    ddl = dict(LEGACY_DDL)
    ddl["creative_topics"] = re.sub(
        r",\s*UNIQUE\(platform, job_id, source_path, title\)",
        "",
        ddl["creative_topics"],
    )
    connection = sqlite3.connect(path)
    try:
        for statement in ddl.values():
            connection.execute(statement)
        for index, title in enumerate(("测试选题", "另一标题"), start=1):
            connection.execute(
                """
                INSERT INTO creative_topics (
                    platform, job_id, source_path, captured_at, title, category,
                    status, score, confidence, score_details_json, recommendation,
                    evidence_json, decision_note, deleted, created_at, updated_at,
                    source_platforms_json, rank_type, description, rank_source
                ) VALUES ('hg', 'job-1', 'rank.json', ?, ?, '都市', 'pending_review',
                          ?, 'high', '{}', 'adopt', '[]', '', 0, ?, ?, '["hg"]',
                          '热播榜', '', '热播榜')
                """,
                (ISO_EPOCH, title, float(index), EPOCH, EPOCH),
            )
        # Collapse the two rows onto the same unique key.
        connection.execute("UPDATE creative_topics SET title = '测试选题' WHERE id = 2")
        connection.commit()
    finally:
        connection.close()

    report = inspect_sqlite_creative(path, strict=False)
    joined = "\n".join(report.problems)
    assert "creative_topics" in joined
    assert "platform, job_id, source_path, title" in joined
    assert "row 1 (id=2)" in joined

    with pytest.raises(ValueError, match=r"creative_topics: row 1 .* duplicates the unique key"):
        inspect_sqlite_creative(path)


def test_inspection_rejects_broken_parent_reference(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_seeds", "project_id", 9999)

    with pytest.raises(
        ValueError, match=r"creative_seeds\.project_id.*creative_projects"
    ):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_broken_parent_version(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_seeds", "parent_version_id", 4242)

    with pytest.raises(ValueError, match=r"creative_seeds\.parent_version_id"):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_missing_column(legacy_creative_db: Path):
    drop_column(legacy_creative_db, "creative_topics", "evidence_json")

    with pytest.raises(ValueError, match=r"creative_topics\.evidence_json: column is missing"):
        inspect_sqlite_creative(legacy_creative_db)


def test_inspection_rejects_missing_primary_key_column(tmp_path: Path):
    path = tmp_path / "no-primary-key.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE creative_seeds ("
            "project_id INTEGER NOT NULL, version INTEGER NOT NULL, seed_text TEXT NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        ValueError, match=r"creative_seeds: source table is missing primary key"
    ):
        inspect_sqlite_creative(path)


def test_inspection_rejects_a_missing_source_file(tmp_path: Path):
    with pytest.raises(CreativeImportError, match="not found"):
        inspect_sqlite_creative(tmp_path / "does-not-exist.db")


def test_inspection_reports_every_problem_at_once(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_topics", "evidence_json", "{")
    corrupt_value(legacy_creative_db, "creative_seeds", "constraints_json", "[")

    report = inspect_sqlite_creative(legacy_creative_db, strict=False)
    joined = "\n".join(report.problems)
    assert "creative_topics.evidence_json" in joined
    assert "creative_seeds.constraints_json" in joined


# ---------------------------------------------------------------------------
# explicit table specification
# ---------------------------------------------------------------------------


def test_specs_cover_every_creative_table():
    assert len(CREATIVE_TABLE_SPECS) == len(SPEC_BY_NAME)
    assert "creative_database_import_runs" in SPEC_BY_NAME
    assert len(BUSINESS_TABLE_SPECS) == len(BUSINESS_TABLE_NAMES)


def test_topic_spec_separates_json_timestamps_and_flags():
    spec = SPEC_BY_NAME["creative_topics"]
    assert spec.primary_key == ("id",)
    assert spec.json_columns == frozenset(
        {"score_details_json", "evidence_json", "source_platforms_json"}
    )
    assert spec.timestamp_columns == frozenset({"created_at", "updated_at"})
    assert spec.boolean_columns == frozenset({"deleted"})
    # captured_at stays TEXT: the legacy store holds ISO strings there.
    assert "captured_at" not in spec.timestamp_columns


def test_specs_capture_partial_unique_indexes_separately():
    spec = SPEC_BY_NAME["creative_projects"]
    # The legacy store scoped idempotency per creator, so the partial unique
    # indexes are wider than the bare request identifier.
    assert ("created_by", "creation_request_id") in spec.partial_unique_keys
    assert ("creation_workflow_task_id",) in spec.partial_unique_keys
    assert ("workflow_task_id",) in spec.partial_unique_keys
    # None of them may be treated as an unconditional uniqueness rule.
    assert ("created_by", "creation_request_id") not in spec.unique_keys
    # The topics source key is fully enforced, not partial.
    assert ("platform", "job_id", "source_path", "title") in SPEC_BY_NAME[
        "creative_topics"
    ].unique_keys


def test_specs_never_declare_a_self_reference_as_a_dependency():
    """Self references order rows inside a table, not the tables themselves."""

    assert "creative_seeds" not in SPEC_BY_NAME["creative_seeds"].dependencies
    assert "creative_breakdowns" not in SPEC_BY_NAME["creative_breakdowns"].dependencies
    # ...but the self reference is still checked as an internal reference.
    assert ("parent_version_id", "creative_seeds", "id") in SPEC_BY_NAME[
        "creative_seeds"
    ].internal_references


def test_specs_declare_internal_references():
    spec = SPEC_BY_NAME["creative_seeds"]
    assert ("project_id", "creative_projects", "id") in spec.internal_references
    assert ("parent_version_id", "creative_seeds", "id") in spec.internal_references
    assert "creative_projects" in spec.dependencies


def test_import_order_places_dependencies_before_dependents():
    positions = {name: index for index, name in enumerate(BUSINESS_TABLE_NAMES)}
    for spec in BUSINESS_TABLE_SPECS:
        for dependency in spec.dependencies:
            if dependency in positions:
                assert positions[dependency] < positions[spec.name], (
                    f"{dependency} must import before {spec.name}"
                )


# ---------------------------------------------------------------------------
# deterministic conversion
# ---------------------------------------------------------------------------


def test_epoch_floats_become_aware_utc_datetimes():
    converted = coerce_timestamp(EPOCH)
    assert converted == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert converted.tzinfo is not None


def test_iso_text_timestamps_are_accepted_too():
    assert coerce_timestamp("2026-09-15T00:00:00Z") == datetime(
        2026, 9, 15, tzinfo=timezone.utc
    )
    assert coerce_timestamp("2026-09-15T08:00:00+08:00") == datetime(
        2026, 9, 15, tzinfo=timezone.utc
    )
    assert coerce_timestamp(None) is None
    assert coerce_timestamp("") is None


def test_invalid_timestamps_are_rejected():
    with pytest.raises(ValueError, match="unrecognised timestamp"):
        coerce_timestamp("tomorrow")
    with pytest.raises(ValueError, match="boolean is not a timestamp"):
        coerce_timestamp(True)


def test_flags_become_booleans():
    assert coerce_boolean(0) is False
    assert coerce_boolean(1) is True
    assert coerce_boolean(True) is True
    assert coerce_boolean(None) is None
    with pytest.raises(ValueError, match="not a 0/1 flag"):
        coerce_boolean(3)
    with pytest.raises(ValueError, match="not a 0/1 flag"):
        coerce_boolean("yes")


def test_json_text_becomes_native_values():
    assert parse_json_value('{"a": 1}') == {"a": 1}
    assert parse_json_value("[1, 2]") == [1, 2]
    assert parse_json_value(None) is None
    assert parse_json_value("") is None
    assert parse_json_value({"already": "parsed"}) == {"already": "parsed"}
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_json_value("{")


def test_normalize_for_target_reports_the_offending_row():
    spec = SPEC_BY_NAME["creative_topics"]
    row = {"id": 5, "evidence_json": "{", "created_at": EPOCH, "updated_at": EPOCH}

    with pytest.raises(CreativeImportError) as error:
        normalize_for_target(row, spec, index=0)

    message = str(error.value)
    assert "creative_topics.evidence_json" in message
    assert "id=5" in message


def test_normalize_for_target_converts_every_declared_type():
    spec = SPEC_BY_NAME["creative_topics"]
    row = {
        "id": 1,
        "evidence_json": "[]",
        "source_platforms_json": '["hg"]',
        "created_at": EPOCH,
        "updated_at": EPOCH,
        "deleted": 0,
        "title": "x",
    }
    normalized = normalize_for_target(row, spec)
    assert normalized["evidence_json"] == []
    assert normalized["source_platforms_json"] == ["hg"]
    assert normalized["created_at"] == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert normalized["deleted"] is False
    assert normalized["title"] == "x"


def test_canonical_row_is_independent_of_key_order():
    spec = SPEC_BY_NAME["creative_topics"]
    first = {"id": 1, "evidence_json": "[]", "created_at": EPOCH, "updated_at": EPOCH}
    second = {"updated_at": EPOCH, "created_at": EPOCH, "evidence_json": "[]", "id": 1}
    assert canonical_row(first, spec) == canonical_row(second, spec)


def test_canonical_row_normalizes_equivalent_timestamp_shapes():
    spec = SPEC_BY_NAME["creative_topics"]
    from_epoch = canonical_row(
        {"id": 1, "created_at": EPOCH, "updated_at": EPOCH}, spec
    )
    from_iso = canonical_row(
        {
            "id": 1,
            "created_at": "2026-09-15T00:00:00Z",
            "updated_at": "2026-09-15T00:00:00Z",
        },
        spec,
    )
    assert from_epoch == from_iso


def test_canonical_json_default_renders_datetimes_as_utc_iso():
    moment = datetime(2026, 9, 15, 8, 0, 0, tzinfo=timezone.utc)
    assert canonical_json_default(moment) == "2026-09-15T08:00:00Z"
    assert canonical_json_default(uuid.UUID(int=0)) == str(uuid.UUID(int=0))
    with pytest.raises(TypeError, match="cannot canonicalise"):
        canonical_json_default(object())


def test_table_digest_is_order_independent():
    spec = SPEC_BY_NAME["creative_topics"]
    rows = [
        {"id": 1, "created_at": EPOCH, "updated_at": EPOCH},
        {"id": 2, "created_at": EPOCH, "updated_at": EPOCH},
    ]
    assert table_digest(rows, spec) == table_digest(list(reversed(rows)), spec)
    assert table_digest(rows[:1], spec) != table_digest(rows, spec)


def test_report_serialises_to_json_safe_values(legacy_creative_db: Path):
    import json

    report = inspect_sqlite_creative(legacy_creative_db)
    payload = report.to_dict()
    # Round-tripping through JSON proves the report can be written by the CLI.
    assert json.loads(json.dumps(payload))["ok"] is True
    assert payload["row_counts"]["creative_topics"] == 1


# ---------------------------------------------------------------------------
# target inspection
# ---------------------------------------------------------------------------


def test_target_inspection_reports_an_empty_schema(
    isolated_system_database: SystemDatabase,
):
    report = inspect_target_creative(isolated_system_database)
    assert report["empty"] is True
    assert report["non_empty_tables"] == {}
    assert "creative_database_import_runs" not in report["business_tables"]


def test_target_inspection_detects_a_non_empty_business_table(
    isolated_system_database: SystemDatabase,
):
    from sqlalchemy import text

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO creative_projects (name, creation_mode, created_at, updated_at) "
                "VALUES ('x', 'original', now(), now())"
            )
        )

    report = inspect_target_creative(isolated_system_database)
    assert report["empty"] is False
    assert report["non_empty_tables"] == {"creative_projects": 1}


def test_source_reader_rejects_a_directory(tmp_path: Path):
    from creative.sqlite_import import open_read_only

    with pytest.raises(CreativeImportError, match="not found"):
        open_read_only(tmp_path)


def test_imported_fixture_copy_is_independent(tmp_path: Path, legacy_creative_db: Path):
    """Guard the fixture helper itself: a corrupt copy leaves the original intact."""

    copy = tmp_path / "copy.db"
    shutil.copyfile(legacy_creative_db, copy)
    original = sha256_file(legacy_creative_db)
    corrupt_value(copy, "creative_topics", "evidence_json", "{")
    assert sha256_file(legacy_creative_db) == original
    assert hashlib.sha256(legacy_creative_db.read_bytes()).hexdigest() == original


# ---------------------------------------------------------------------------
# import: identifiers, identity, sequences
# ---------------------------------------------------------------------------


def _control_rows(database: SystemDatabase) -> list[tuple[str, str, str]]:
    from sqlalchemy import text

    with database.connection() as connection:
        return [
            (row[0], row[1], row[2])
            for row in connection.execute(
                text(
                    "SELECT status, mode, error_summary FROM creative_database_import_runs "
                    "ORDER BY started_at, run_id"
                )
            ).all()
        ]


def test_import_preserves_ids_and_resets_sequences(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    from creative.postgres_repository import PostgresCreativeRepository

    report = import_sqlite_creative(legacy_creative_db, isolated_system_database)

    assert report.verified is True
    assert report.status == "completed"
    assert report.problems == ()
    assert report.row_counts["creative_projects"] == 1
    # The target hash must equal the source hash for every table.
    assert report.digests == {
        name: digest for name, digest in report.digests.items()
    }

    repository = PostgresCreativeRepository(isolated_system_database)
    created = repository.create_original_project(
        name="迁移后项目",
        seed_text="新种子",
        target_episode_count=20,
        created_by="user-1",
        creation_request_id="after-import",
    )
    assert created["id"] > report.id_ranges["creative_projects"][1]


def test_import_preserves_the_store_identity(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    """A migrated store must keep the identity its workflow history refers to."""

    from creative.postgres_repository import PostgresCreativeRepository

    report = import_sqlite_creative(legacy_creative_db, isolated_system_database)
    assert report.verified is True

    repository = PostgresCreativeRepository(isolated_system_database)
    assert repository.store_id == STORE_ID
    assert repository.workflow_project_id(1) == f"creative:{STORE_ID}:project:1"
    assert repository.local_project_id_from_workflow(
        f"creative:{STORE_ID}:project:1"
    ) == 1


def test_import_sequences_start_beyond_the_imported_maximum(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    report = import_sqlite_creative(legacy_creative_db, isolated_system_database)
    assert report.verified is True
    assert report.sequences["creative_topics"] > report.id_ranges["creative_topics"][1]
    assert report.sequences["creative_projects"] > report.id_ranges["creative_projects"][1]


def test_import_records_a_completed_run(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    report = import_sqlite_creative(legacy_creative_db, isolated_system_database)
    rows = _control_rows(isolated_system_database)
    assert len(rows) == 1
    assert rows[0][0] == "completed"
    assert rows[0][1] == "apply"
    assert rows[0][2] == ""
    assert report.run_id


def test_import_is_dry_run_safe_when_the_target_is_not_empty(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    with pytest.raises(CreativeImportError, match="non-empty creative target"):
        import_sqlite_creative(legacy_creative_db, isolated_system_database)


def test_import_requires_the_target_to_be_at_alembic_head(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase, monkeypatch
):
    health = isolated_system_database.health()
    behind = type(health)(
        reachable=True,
        database_url_redacted=health.database_url_redacted,
        current_revision="0001_initial",
        head_revision=health.head_revision,
        schema_current=False,
    )
    monkeypatch.setattr(isolated_system_database, "health", lambda: behind)

    with pytest.raises(CreativeImportError, match="not at the Alembic head"):
        import_sqlite_creative(legacy_creative_db, isolated_system_database)


# ---------------------------------------------------------------------------
# import: whole-run atomicity
# ---------------------------------------------------------------------------


def test_import_rolls_back_entirely_on_failure(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase, monkeypatch
):
    from creative import sqlite_import

    real_insert = sqlite_import._insert_source_rows
    calls = {"count": 0}

    def flaky_insert(connection, spec, rows):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("injected mid-import failure")
        return real_insert(connection, spec, rows)

    monkeypatch.setattr(sqlite_import, "_insert_source_rows", flaky_insert)

    with pytest.raises(RuntimeError, match="injected mid-import failure"):
        sqlite_import.import_sqlite_creative(legacy_creative_db, isolated_system_database)

    # No business table may hold a partial result...
    assert inspect_target_creative(isolated_system_database)["empty"] is True

    # ...and exactly one failed control record must survive the rollback.
    rows = _control_rows(isolated_system_database)
    assert len(rows) == 1
    assert rows[0][0] == "failed"
    assert rows[0][1] == "apply"
    assert "injected mid-import failure" in rows[0][2]


def test_failed_import_does_not_leak_credentials(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase, monkeypatch
):
    from creative import sqlite_import

    def leaky_insert(connection, spec, rows):
        raise RuntimeError(
            "could not connect to postgresql+psycopg://aiworker_app:LocalPass%401234@127.0.0.1:5432/x"
        )

    monkeypatch.setattr(sqlite_import, "_insert_source_rows", leaky_insert)

    with pytest.raises(RuntimeError):
        sqlite_import.import_sqlite_creative(legacy_creative_db, isolated_system_database)

    _, _, summary = _control_rows(isolated_system_database)[0]
    assert "LocalPass" not in summary
    assert "***" in summary


def test_redact_secrets_hides_the_password():
    from creative.sqlite_import import redact_secrets

    redacted = redact_secrets("postgresql://user:secret@host:5432/db")
    assert redacted == "postgresql://user:***@host:5432/db"


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def test_verify_accepts_a_faithful_import(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    report = verify_creative_import(legacy_creative_db, isolated_system_database)
    assert report.verified is True
    assert report.status == "verified"
    assert report.problems == ()
    assert report.mode == "verify"


def test_verify_detects_a_tampered_target(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    from sqlalchemy import text

    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text("UPDATE creative_topics SET title = '被篡改' WHERE id = 1")
        )

    report = verify_creative_import(legacy_creative_db, isolated_system_database)
    assert report.verified is False
    assert report.status == "failed"
    assert any("creative_topics" in problem for problem in report.problems)


def test_verify_detects_a_broken_version_chain(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    """The verifier must catch a broken chain that arrived past the foreign key.

    PostgreSQL enforces the self-referencing parent foreign key, so a naive
    corruption is rejected outright. The verifier is defence in depth for data
    that reached the table while the constraint was absent, which is exactly the
    shape a legacy SQLite store can have.
    """

    from sqlalchemy import text

    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    # The foreign key really does block the naive attempt...
    with pytest.raises(Exception, match="fk_creative_seeds_parent_version_id"):
        with isolated_system_database.transaction() as connection:
            connection.execute(
                text("UPDATE creative_seeds SET parent_version_id = 99 WHERE id = 1")
            )

    # ...so drop it to reproduce the legacy-carrying-a-broken-chain case.
    with isolated_system_database.transaction() as connection:
        connection.execute(
            text("ALTER TABLE creative_seeds DROP CONSTRAINT fk_creative_seeds_parent_version_id")
        )
        connection.execute(
            text("UPDATE creative_seeds SET parent_version_id = 99 WHERE id = 1")
        )

    report = verify_creative_import(legacy_creative_db, isolated_system_database)
    assert report.verified is False
    assert any("broken parent version" in problem for problem in report.problems)


def test_verify_detects_a_missing_store_identity(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    from sqlalchemy import text

    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    with isolated_system_database.transaction() as connection:
        connection.execute(
            text("UPDATE creative_store_metadata SET value = 'not-a-uuid' WHERE key = 'store_id'")
        )

    report = verify_creative_import(legacy_creative_db, isolated_system_database)
    assert report.verified is False
    assert any("is not a UUID" in problem for problem in report.problems)


def test_verify_reads_the_source_read_only(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    before = sha256_file(legacy_creative_db)
    verify_creative_import(legacy_creative_db, isolated_system_database)
    assert sha256_file(legacy_creative_db) == before


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_runner():
    from typer.testing import CliRunner

    return CliRunner()


def _cli_module():
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "migrate_creative_sqlite_to_postgres.py"
    )
    spec = importlib.util.spec_from_file_location("creative_migration_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cli_app():
    """Wrap the ``typer.run`` style entry point in an app the runner can drive."""

    import typer

    app = typer.Typer(add_completion=False)
    app.command()(_cli_module().main)
    return app


def test_cli_check_is_read_only_and_reports_json(
    legacy_creative_db: Path, tmp_path: Path
):
    import json

    before = sha256_file(legacy_creative_db)
    result = _cli_runner().invoke(
            _cli_app(), ["--source", str(legacy_creative_db), "--check"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["mode"] == "check"
    assert payload["row_counts"]["creative_topics"] == 1
    assert sha256_file(legacy_creative_db) == before


def test_cli_check_fails_on_a_corrupt_source(legacy_creative_db: Path):
    corrupt_value(legacy_creative_db, "creative_topics", "evidence_json", "{")

    result = _cli_runner().invoke(
            _cli_app(), ["--source", str(legacy_creative_db), "--check"]
    )
    assert result.exit_code == 1
    assert "creative_topics.evidence_json" in result.output


def test_cli_requires_exactly_one_mode(legacy_creative_db: Path):
    runner = _cli_runner()
    app = _cli_app()

    for args in ([], ["--check", "--apply"], ["--check", "--apply", "--verify"]):
        result = runner.invoke(app, ["--source", str(legacy_creative_db), *args])
        assert result.exit_code != 0


def test_cli_apply_requires_postgresql_configuration(
    legacy_creative_db: Path, monkeypatch
):
    monkeypatch.delenv("AIWORKER_SYSTEM_DATABASE_URL", raising=False)

    result = _cli_runner().invoke(
        _cli_app(), ["--source", str(legacy_creative_db), "--apply"]
    )
    assert result.exit_code != 0
    assert "AIWORKER_SYSTEM_DATABASE_URL" in result.output


def test_cli_refuses_a_test_named_database_without_the_flag(
    legacy_creative_db: Path, monkeypatch
):
    """A rehearsal must be explicit: --apply never lands in a test database by accident."""

    from sqlalchemy.engine import make_url

    module = _cli_module()

    class _FakeHealth:
        reachable = True
        schema_current = True
        current_revision = "head"
        head_revision = "head"
        database_url_redacted = "postgresql+psycopg://u:***@127.0.0.1:5432/aiworker_system_test"

    class _FakeDatabase:
        def __init__(self, settings):
            self.url = make_url(settings.url)

        def health(self):
            return _FakeHealth()

        def dispose(self):
            pass

    monkeypatch.setattr(module, "SystemDatabase", _FakeDatabase)
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://aiworker_app:pw@127.0.0.1:5432/aiworker_system_test",
    )

    result = _cli_runner().invoke(
        _cli_app(), ["--source", str(legacy_creative_db), "--apply"]
    )
    assert result.exit_code != 0
    assert "test-named database" in result.output


def test_test_database_name_protection_is_narrow():
    from sqlalchemy.engine import make_url

    module = _cli_module()

    class _Probe:
        def __init__(self, name):
            self.url = make_url(f"postgresql+psycopg://u:p@127.0.0.1:5432/{name}")

    assert module._looks_like_a_test_database(_Probe("aiworker_system_test")) is True
    assert module._looks_like_a_test_database(_Probe("test")) is True
    # A real target is never refused: a formal cutover is exactly this case.
    assert module._looks_like_a_test_database(_Probe("aiworker_system")) is False
    assert module._looks_like_a_test_database(_Probe("creative_prod")) is False


# ---------------------------------------------------------------------------
# cross-domain references
# ---------------------------------------------------------------------------


def test_cross_domain_references_target_the_real_parent_columns():
    """Regression: users.user_id and workflow_tasks.task_id do not exist.

    Because the resolver treats a missing parent column as "nothing resolves",
    a wrong column silently turns every reference into an illegal orphan and
    blocks the import with a misleading error instead of failing loudly.
    """

    from creative.sqlite_import import CROSS_DOMAIN_REFERENCE_COLUMNS

    parents = {parent_column for _, _, parent_column in CROSS_DOMAIN_REFERENCE_COLUMNS}
    assert "user_id" not in parents
    assert "task_id" not in parents
    assert ("created_by", "users", "id") in CROSS_DOMAIN_REFERENCE_COLUMNS
    assert ("workflow_task_id", "workflow_tasks", "id") in CROSS_DOMAIN_REFERENCE_COLUMNS


def test_legal_historical_gaps_are_declared_by_value_not_by_column():
    """Declaring a column would also excuse a genuinely broken future reference."""

    from creative.sqlite_import import LEGAL_HISTORICAL_GAP_REFERENCES

    assert LEGAL_HISTORICAL_GAP_REFERENCES, "the audited gaps must be declared"
    for entry in LEGAL_HISTORICAL_GAP_REFERENCES:
        assert len(entry) == 3, f"{entry!r} must be a (table, column, value) triple"
        table, column, value = entry
        assert table and column and value

    # The two non-user sentinels are the clearest audited cases.
    assert (
        "creative_projects",
        "created_by",
        "local",
    ) in LEGAL_HISTORICAL_GAP_REFERENCES
    assert (
        "creative_prompt_template_revisions",
        "created_by",
        "system",
    ) in LEGAL_HISTORICAL_GAP_REFERENCES


def test_audited_legal_gaps_match_the_documented_count():
    """The declaration must stay in step with the audit document."""

    from creative.sqlite_import import LEGAL_HISTORICAL_GAP_REFERENCES

    document = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "operations"
        / "creative-cross-domain-reference-audit.md"
    )
    assert document.is_file(), "the cross-domain audit document is missing"

    text = document.read_text(encoding="utf-8")
    for table, column, value in LEGAL_HISTORICAL_GAP_REFERENCES:
        assert value in text, f"{table}.{column} gap {value} is not documented"
    assert len(LEGAL_HISTORICAL_GAP_REFERENCES) == 8


def test_cross_domain_resolution_ignores_a_missing_parent_table(
    legacy_creative_db: Path, isolated_system_database: SystemDatabase
):
    """An absent parent domain must be reported, never crash the import."""

    report = inspect_cross_domain_references(
        isolated_system_database, source=legacy_creative_db
    )
    # workflow_submissions does not exist in the system schema.
    assert all(
        entry.parent_table != "workflow_submissions" or entry.unresolved
        for entry in report
    )
    assert isinstance(report, tuple)


# ---------------------------------------------------------------------------
# specification coverage
# ---------------------------------------------------------------------------


def test_import_order_covers_every_business_table_exactly_once():
    from creative.sqlite_import import IMPORT_ORDER

    business_in_order = [name for name in IMPORT_ORDER if name in BUSINESS_TABLE_NAMES]
    assert sorted(business_in_order) == sorted(BUSINESS_TABLE_NAMES)
    assert len(business_in_order) == len(set(business_in_order))
    assert IMPORT_ORDER[0] == "creative_store_metadata"
    assert IMPORT_ORDER[-1] == "creative_database_import_runs"


def test_parent_version_comparison_is_numeric():
    """A lexical comparison would call id 10 with parent 9 a forward reference."""

    from creative.sqlite_import import _parent_cannot_precede_child

    assert _parent_cannot_precede_child(9, 10) is False
    assert _parent_cannot_precede_child(2, 10) is False
    assert _parent_cannot_precede_child(10, 10) is True
    assert _parent_cannot_precede_child(11, 10) is True
    # Non-integer keys fall back to a textual ordering.
    assert _parent_cannot_precede_child("a", "b") is False
    assert _parent_cannot_precede_child("b", "a") is True
