# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""One-way, explicitly phased import of the legacy SQLite system database."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from system_database import SystemDatabase, SystemDatabaseSettings
from system_database.sqlite_import_cli import execute_import_command


def main(
    source: Path = typer.Option(..., exists=True, readable=True),
    check: bool = typer.Option(False, help="Validate the SQLite source without PostgreSQL writes."),
    apply: bool = typer.Option(False, help="Copy validated data into PostgreSQL."),
    verify: bool = typer.Option(False, help="Compare the SQLite source with PostgreSQL."),
) -> None:
    selected = [name for name, enabled in (("check", check), ("apply", apply), ("verify", verify)) if enabled]
    if len(selected) != 1:
        raise typer.BadParameter("select exactly one of --check, --apply, or --verify")

    mode = selected[0]
    database = None
    if mode != "check":
        database = SystemDatabase(SystemDatabaseSettings.from_environment())
        health = database.health()
        if not health.schema_current:
            raise typer.BadParameter("PostgreSQL system database is not reachable at Alembic head")
    try:
        report = execute_import_command(mode, source, database)
    finally:
        if database is not None:
            database.dispose()
    typer.echo(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":
    typer.run(main)
