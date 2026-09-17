# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Explicitly phased migration of the legacy creative SQLite store.

``--check``  reads the legacy file only and never touches PostgreSQL.
``--apply``  imports every table in one transaction, or none at all.
``--verify`` re-reads the source and requires a byte-identical digest per table.

The migration is deliberately loud rather than clever: it refuses to guess a
back end, refuses a target that is not at the Alembic head, refuses a target
that already holds creative data, and refuses a test-named database unless the
operator says so explicitly. It never refuses a production target that was
explicitly named, because that is exactly what a formal cutover is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

# Permit ``python scripts/migrate_creative_sqlite_to_postgres.py`` from the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creative.sqlite_import import (  # noqa: E402
    CreativeImportError,
    import_sqlite_creative,
    inspect_sqlite_creative,
    inspect_target_creative,
    verify_creative_import,
)
from system_database import SystemDatabase, SystemDatabaseSettings  # noqa: E402


def _database_name(database: SystemDatabase) -> str:
    return database.url.database or ""


def _looks_like_a_test_database(database: SystemDatabase) -> bool:
    """Protect the disposable test databases from an accidental real import."""

    name = _database_name(database).lower()
    return name.endswith("_test") or name.endswith("_tests") or "test" == name


def main(
    source: Path = typer.Option(..., exists=True, readable=True),
    check: bool = typer.Option(False, help="Validate the SQLite source without PostgreSQL writes."),
    apply: bool = typer.Option(False, help="Copy validated data into PostgreSQL."),
    verify: bool = typer.Option(False, help="Compare the SQLite source with PostgreSQL."),
    allow_test_target: bool = typer.Option(
        False,
        "--allow-test-target",
        help="Permit --apply or --verify against a test-named database, as in a rehearsal.",
    ),
) -> None:
    selected = [
        name
        for name, enabled in (("check", check), ("apply", apply), ("verify", verify))
        if enabled
    ]
    if len(selected) != 1:
        raise typer.BadParameter("select exactly one of --check, --apply, or --verify")
    mode = selected[0]

    if mode == "check":
        report = inspect_sqlite_creative(source, strict=False)
        payload = report.to_dict()
        payload["mode"] = "check"
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        if not report.ok:
            raise typer.Exit(code=1)
        return

    try:
        settings = SystemDatabaseSettings.from_environment()
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    database = SystemDatabase(settings)
    try:
        health = database.health()
        if not health.reachable:
            raise typer.BadParameter(
                f"PostgreSQL target is not reachable: {health.database_url_redacted}"
            )
        if not health.schema_current:
            raise typer.BadParameter(
                "PostgreSQL target is not at the Alembic head "
                f"(current={health.current_revision!r}, head={health.head_revision!r})"
            )
        if _looks_like_a_test_database(database) and not allow_test_target:
            raise typer.BadParameter(
                f"refusing to migrate into the test-named database "
                f"{_database_name(database)!r}; pass --allow-test-target for a rehearsal"
            )

        if mode == "apply":
            report = import_sqlite_creative(source, database)
        else:
            target = inspect_target_creative(database)
            if target["empty"]:
                raise typer.BadParameter(
                    "refusing to verify an empty creative target; run --apply first"
                )
            report = verify_creative_import(source, database)
    finally:
        database.dispose()

    typer.echo(
        json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    )
    if not report.verified:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
