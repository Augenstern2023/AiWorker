# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Rollback-export tests for the creative migration.

The migration is only reversible if the reverse direction is *provable*. These
tests build the same frozen legacy SQLite store the import tests use, import it
into an isolated PostgreSQL schema, export it back and then compare the two
SQLite files table by table: row counts, identifiers, store identity and the
canonical digest that the forward migration already trusts.

The failure tests matter as much as the round trip: a rollback file must never
appear at its destination unless it verified, because an operator reaching for
it is already having a bad day.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository
from creative.sqlite_import import (
    SPEC_BY_NAME,
    import_sqlite_creative,
    inspect_sqlite_creative,
    sha256_file,
    table_digest,
)
from creative.sqlite_rollback import (
    ROLLBACK_WINDOW_DAYS,
    TEMPORARY_SUFFIX,
    export_sqlite_rollback,
)
from system_database import SystemDatabase


# ---------------------------------------------------------------------------
# frozen legacy DDL, copied from tests/test_creative_sqlite_import.py
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

#: The tables the fixture store actually holds; the round trip compares these.
FIXTURE_TABLES: tuple[str, ...] = tuple(LEGACY_DDL)


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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_only(path: Path) -> sqlite3.Connection:
    """Open a sqlite file for reading, exactly as an operator inspecting it would."""

    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _sqlite_rows(path: Path, table: str) -> list[dict[str, Any]]:
    connection = _read_only(path)
    try:
        return [
            dict(row)
            for row in connection.execute(f'SELECT * FROM "{table}"').fetchall()
        ]
    finally:
        connection.close()


def _sqlite_table_names(path: Path) -> tuple[str, ...]:
    connection = _read_only(path)
    try:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
    finally:
        connection.close()


def _identities(path: Path, table: str) -> list[tuple[Any, ...]]:
    spec = SPEC_BY_NAME[table]
    rows = _sqlite_rows(path, table)
    return sorted(
        tuple(row.get(column) for column in spec.primary_key) for row in rows
    )


def _store_id(path: Path) -> str:
    rows = _sqlite_rows(path, "creative_store_metadata")
    return str(next(row["value"] for row in rows if row["key"] == "store_id"))


def _export(tmp_path: Path, database: SystemDatabase) -> tuple[Path, Any]:
    destination = tmp_path / "rollback" / "creative.db"
    report = export_sqlite_rollback(database, destination)
    assert report.verified is True, report.problems
    assert report.published is True
    return destination, report


# ---------------------------------------------------------------------------
# the round trip
# ---------------------------------------------------------------------------


def test_rollback_round_trip_is_lossless_per_table(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    """SQLite A -> PostgreSQL -> SQLite B: every fixture table must match A."""

    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination, report = _export(tmp_path, isolated_system_database)

    assert destination.is_file()
    assert report.sha256 == sha256_file(destination)
    assert len(report.sha256) == 64
    assert report.row_counts["creative_topics"] == 1
    assert report.row_counts["creative_episodes"] == 0

    source_inspection = inspect_sqlite_creative(legacy_creative_db)
    for table in FIXTURE_TABLES:
        spec = SPEC_BY_NAME[table]
        source_rows = _sqlite_rows(legacy_creative_db, table)
        exported_rows = _sqlite_rows(destination, table)

        assert len(exported_rows) == len(source_rows), table
        assert _identities(destination, table) == _identities(
            legacy_creative_db, table
        ), table
        source_columns = set().union(*(row.keys() for row in source_rows))
        legacy_exported_rows = [
            {key: value for key, value in row.items() if key in source_columns}
            for row in exported_rows
        ]
        assert table_digest(legacy_exported_rows, spec) == table_digest(
            source_rows, spec
        ), table
        # ...and the digest is the one the forward importer computed.
        assert (
            table_digest(legacy_exported_rows, spec)
            == source_inspection.digests[table]
        )

    exported_project = _sqlite_rows(destination, "creative_projects")[0]
    uuid.UUID(exported_project["project_uid"])

    assert _store_id(destination) == _store_id(legacy_creative_db) == STORE_ID
    # The frozen legacy DDL still lacks the breakdown snapshot reference; the
    # export widens that table and says so rather than dropping the column.
    joined_warnings = "\n".join(report.warnings)
    assert "creative_breakdowns.source_snapshot_id" in joined_warnings
    assert "script_optimizations.prompt_snapshot_json" not in joined_warnings
    # The export is genuinely complete: every declared table is present.
    exported_tables = set(_sqlite_table_names(destination))
    for table in SPEC_BY_NAME:
        assert table in exported_tables, table
    # The empty tables round trip as empty tables, not as missing tables.
    assert _sqlite_rows(destination, "creative_episodes") == []


def test_rollback_writes_legacy_storage_shapes(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    """Timestamps are epoch REAL, JSON is TEXT and flags are INTEGER."""

    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination, _ = _export(tmp_path, isolated_system_database)

    connection = sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro", uri=True)
    try:
        declared = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(creative_topics)").fetchall()
        }
        assert declared["created_at"] == "REAL"
        assert declared["evidence_json"] == "TEXT"
        assert declared["deleted"] == "INTEGER"
        stored = connection.execute(
            "SELECT typeof(created_at), typeof(evidence_json), typeof(deleted), "
            "created_at, evidence_json FROM creative_topics WHERE id = 1"
        ).fetchone()
    finally:
        connection.close()

    assert stored[0] == "real"
    assert stored[1] == "text"
    assert stored[2] == "integer"
    assert stored[3] == pytest.approx(EPOCH)
    assert json.loads(stored[4]) == []


def test_rollback_carries_post_cutover_rows(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    """Rows written after the cutover are part of the rollback, not lost by it."""

    from creative.postgres_repository import PostgresCreativeRepository

    imported = import_sqlite_creative(legacy_creative_db, isolated_system_database)
    repository = PostgresCreativeRepository(isolated_system_database)
    project = repository.create_original_project(
        name="迁移后项目",
        seed_text="新种子",
        target_episode_count=20,
        created_by="user-1",
        creation_request_id="after-import",
    )
    new_project_id = int(project["id"])
    new_seed = repository.create_seed_version(new_project_id, seed_text="新种子第二版")
    new_seed_id = int(new_seed["id"])

    destination, report = _export(tmp_path, isolated_system_database)

    project_ids = {row["id"] for row in _sqlite_rows(destination, "creative_projects")}
    seed_ids = {row["id"] for row in _sqlite_rows(destination, "creative_seeds")}
    assert new_project_id in project_ids
    assert new_seed_id in seed_ids
    # The post-cutover project really is newer than everything A held.
    assert new_project_id > imported.id_ranges["creative_projects"][1]
    assert report.row_counts["creative_projects"] == imported.row_counts["creative_projects"] + 1
    assert report.row_counts["creative_seeds"] > imported.row_counts["creative_seeds"]


# ---------------------------------------------------------------------------
# refusals and failure containment
# ---------------------------------------------------------------------------


def test_export_refuses_an_existing_destination(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination = tmp_path / "rollback" / "creative.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"not a database")

    with pytest.raises(FileExistsError, match="rollback destination already exists"):
        export_sqlite_rollback(isolated_system_database, destination)

    assert destination.read_bytes() == b"not a database"


def test_a_lossy_export_is_caught_and_never_published(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verification is not decorative: real data loss must stop the publish."""

    from creative import sqlite_rollback

    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    real_write = sqlite_rollback._write_sqlite_rows

    def lossy_write(path, rows_by_table):
        counts = real_write(path, rows_by_table)
        connection = sqlite3.connect(path)
        try:
            connection.execute("DELETE FROM creative_topics")
            connection.commit()
        finally:
            connection.close()
        return counts

    monkeypatch.setattr(sqlite_rollback, "_write_sqlite_rows", lossy_write)

    destination = tmp_path / "rollback" / "creative.db"
    report = export_sqlite_rollback(isolated_system_database, destination)

    assert report.verified is False
    assert report.published is False
    assert report.sha256 == ""
    assert not destination.exists()
    joined = "\n".join(report.problems)
    assert "creative_topics: PostgreSQL rows 1 != exported rows 0" in joined
    assert "creative_topics: PostgreSQL digest" in joined


def test_broken_version_chain_and_head_references_are_reported(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The structural checks are not vacuous: both must fire on real damage."""

    from creative import sqlite_rollback

    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    real_write = sqlite_rollback._write_sqlite_rows

    def damaging_write(path, rows_by_table):
        counts = real_write(path, rows_by_table)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                INSERT INTO creative_breakdowns (
                    id, topic_id, version, parent_version_id, status, content_json,
                    field_sources_json, risks_json, created_at, updated_at
                ) VALUES (99, 1, 99, 100, 'draft', '{}', '{}', '[]', ?, ?)
                """,
                (EPOCH, EPOCH),
            )
            connection.execute(
                """
                INSERT INTO creative_resource_heads (
                    project_id, resource_scope, stage, episode_number,
                    resource_ref, resource_version, revision, updated_at, updated_by
                ) VALUES (1, 'free', 'script', 0, '999999', 1, 0, ?, '')
                """,
                (EPOCH,),
            )
            connection.commit()
        finally:
            connection.close()
        return counts

    monkeypatch.setattr(sqlite_rollback, "_write_sqlite_rows", damaging_write)

    destination = tmp_path / "rollback" / "creative.db"
    report = export_sqlite_rollback(isolated_system_database, destination)

    assert report.verified is False
    assert not destination.exists()
    joined = "\n".join(report.problems)
    assert "creative_breakdowns: id=99 has a broken parent version 100" in joined
    assert (
        "creative_resource_heads: project 1 stage script points at missing episode 999999"
        in joined
    )


def test_injected_verification_failure_is_never_published(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from creative import sqlite_rollback

    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    def failing_verify(path, specs, postgres_counts, postgres_digests):
        return sqlite_rollback.ExportVerification(
            problems=("injected verification failure",), integrity_ok=True
        )

    monkeypatch.setattr(sqlite_rollback, "_verify_export", failing_verify)

    destination = tmp_path / "rollback" / "creative.db"
    report = export_sqlite_rollback(isolated_system_database, destination)

    assert report.verified is False
    assert report.published is False
    assert not destination.exists()
    assert "injected verification failure" in report.problems
    # The verified-then-published step must not leave its temporary file behind.
    assert list(destination.parent.glob(f"*{TEMPORARY_SUFFIX}")) == []


def test_a_broken_postgres_source_is_reported_not_raised(tmp_path: Path):
    """A failure that happens before the write still leaves no destination path."""

    destination = tmp_path / "rollback" / "creative.db"
    report = export_sqlite_rollback(
        _UnreachableDatabase(), destination  # type: ignore[arg-type]
    )

    assert report.verified is False
    assert report.published is False
    assert not destination.exists()
    assert any("unreachable" in problem for problem in report.problems)


class _UnreachableDatabase:
    """A SystemDatabase stand-in whose connections always fail."""

    class _Url:
        @staticmethod
        def get_backend_name() -> str:
            return "postgresql"

    url = _Url()

    def connection(self):
        raise RuntimeError("source database is unreachable")


# ---------------------------------------------------------------------------
# the published file
# ---------------------------------------------------------------------------


def test_exported_file_passes_integrity_and_foreign_key_checks(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination, _ = _export(tmp_path, isolated_system_database)

    connection = sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        assert [
            row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()
        ] == ["ok"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_exported_file_is_readable_by_the_legacy_repository(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    """The whole point of the rollback: the frozen runtime can read the file."""

    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination, _ = _export(tmp_path, isolated_system_database)

    repository = LegacySQLiteCreativeRepository(destination)
    assert repository.store_id == STORE_ID

    topics = repository.list_topics()
    assert [topic["title"] for topic in topics] == ["测试选题"]
    assert topics[0]["score"] == pytest.approx(80.0)
    assert topics[0]["source_platforms"] == ["hg"]

    project = repository.get_project(1)
    assert project is not None
    assert project["name"] == "测试项目"
    assert project["creation_mode"] == "imitation"
    assert project["topic_title"] == "测试选题"

    seed = repository.get_seed(1)
    assert seed is not None
    assert seed["seed_text"] == "种子"


def test_report_serialises_to_json_safe_values(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    _, report = _export(tmp_path, isolated_system_database)

    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["published"] is True
    assert payload["sha256"] == report.sha256
    assert payload["rollback_window_days"] == ROLLBACK_WINDOW_DAYS == 7
    assert payload["expires_at"] is not None
    assert payload["row_counts"]["creative_topics"] == 1
    assert payload["problems"] == []


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
        / "rollback_creative_postgres_to_sqlite.py"
    )
    spec = importlib.util.spec_from_file_location("creative_rollback_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cli_app(module=None):
    import typer

    app = typer.Typer(add_completion=False)
    app.command()((module or _cli_module()).main)
    return app


def _plain_output(result) -> str:
    """Strip typer's rich error panel and unwrap its lines.

    A refusal message is rendered inside a box that wraps at the terminal width,
    so the assertion has to look at the text rather than at the exact layout.
    """

    text = re.sub(r"[─│┌┐└┘╭╮╰╯]", " ", result.output)
    return " ".join(text.split())


def test_cli_rejects_a_destination_outside_the_rollback_directory(tmp_path: Path):
    runner = _cli_runner()
    app = _cli_app()
    rollback_dir = tmp_path / "rollback"

    for destination in (
        tmp_path / "elsewhere" / "creative.db",
        # A ``..`` detour is resolved before the containment check.
        rollback_dir / ".." / "elsewhere" / "creative.db",
    ):
        result = runner.invoke(
            app,
            [
                "--destination",
                str(destination),
                "--rollback-dir",
                str(rollback_dir),
            ],
        )
        assert result.exit_code != 0
        assert "not inside the rollback directory" in _plain_output(result)

    assert not (tmp_path / "elsewhere").exists()


def test_cli_rejects_the_sealed_legacy_store(tmp_path: Path):
    module = _cli_module()
    original_refusal_reason = module._refusal_reason
    module._refusal_reason = lambda destination, rollback_dir: original_refusal_reason(
        destination, rollback_dir, project_root=tmp_path
    )
    app = _cli_app(module)
    sealed_store = tmp_path / ".runtime" / "creative" / "creative.db"
    sealed_store.parent.mkdir(parents=True)
    sealed_store.write_bytes(b"sealed legacy store")
    before = sha256_file(sealed_store)

    result = _cli_runner().invoke(
        app,
        [
            "--destination",
            str(sealed_store),
            "--rollback-dir",
            str(tmp_path / ".runtime"),
        ],
    )

    assert result.exit_code != 0
    assert "refusing to write" in _plain_output(result)
    assert sha256_file(sealed_store) == before
    assert not list(
        (tmp_path / ".runtime" / "creative").glob(f"*{TEMPORARY_SUFFIX}")
    )


def test_cli_refuses_every_path_inside_the_creative_runtime_directory(tmp_path: Path):
    module = _cli_module()
    app = _cli_app()
    runtime = module.PROJECT_ROOT / ".runtime" / "creative"

    result = _cli_runner().invoke(
        app,
        [
            "--destination",
            str(runtime / "rollback-copy.db"),
            "--rollback-dir",
            str(runtime),
        ],
    )

    assert result.exit_code != 0
    assert "runtime directory" in _plain_output(result)
    assert not (runtime / "rollback-copy.db").exists()


def test_cli_publishes_a_verified_rollback_and_prints_the_hash(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
):
    import_sqlite_creative(legacy_creative_db, isolated_system_database)
    destination = tmp_path / "rollback" / "creative.db"

    result = _cli_runner().invoke(
        _cli_app(),
        [
            "--destination",
            str(destination),
            "--rollback-dir",
            str(tmp_path / "rollback"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verified"] is True
    assert payload["published"] is True
    assert payload["ok"] is True
    assert payload["sha256"] == sha256_file(destination)
    assert len(payload["sha256"]) == 64
    assert payload["source_backend"] == "postgresql"
    assert payload["row_counts"]["creative_topics"] == 1
    assert destination.is_file()


def test_cli_exits_non_zero_when_verification_fails(
    legacy_creative_db: Path,
    isolated_system_database: SystemDatabase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from creative import sqlite_rollback

    import_sqlite_creative(legacy_creative_db, isolated_system_database)

    def failing_verify(path, specs, postgres_counts, postgres_digests):
        return sqlite_rollback.ExportVerification(
            problems=("injected verification failure",), integrity_ok=True
        )

    monkeypatch.setattr(sqlite_rollback, "_verify_export", failing_verify)
    destination = tmp_path / "rollback" / "creative.db"

    result = _cli_runner().invoke(
        _cli_app(),
        [
            "--destination",
            str(destination),
            "--rollback-dir",
            str(tmp_path / "rollback"),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["verified"] is False
    assert payload["problems"] == ["injected verification failure"]
    assert not destination.exists()


def test_cli_requires_postgresql_configuration(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AIWORKER_SYSTEM_DATABASE_URL", raising=False)

    result = _cli_runner().invoke(
        _cli_app(),
        [
            "--destination",
            str(tmp_path / "rollback" / "creative.db"),
            "--rollback-dir",
            str(tmp_path / "rollback"),
        ],
    )

    assert result.exit_code != 0
    assert "AIWORKER_SYSTEM_DATABASE_URL" in _plain_output(result)


def test_cli_requires_both_paths(tmp_path: Path):
    runner = _cli_runner()
    app = _cli_app()

    for args in (
        ["--rollback-dir", str(tmp_path)],
        ["--destination", str(tmp_path / "creative.db")],
        [],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code != 0
