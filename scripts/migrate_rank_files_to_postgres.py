# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Import local rank snapshots into the centralized PostgreSQL database."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import typer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.rank_importer import rank_importer_from_environment


app = typer.Typer(add_completion=False)


@app.command()
def main(
    project_root: Path = typer.Option(PROJECT_ROOT, exists=True, file_okay=False),
    hongguo_exports_root: Path = typer.Option(Path(r"E:\hg\exports")),
    platform: str = typer.Option("all", help="all, dy, or hg"),
    start_date: str = typer.Option("", help="Inclusive YYYY-MM-DD date"),
    end_date: str = typer.Option("", help="Inclusive YYYY-MM-DD date"),
) -> None:
    if platform not in {"all", "dy", "hg"}:
        raise typer.BadParameter("platform must be all, dy, or hg")
    parsed_start = _parse_date(start_date, "start-date")
    parsed_end = _parse_date(end_date, "end-date")
    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise typer.BadParameter("start-date must not be after end-date")

    importer, database = rank_importer_from_environment(
        project_root,
        hongguo_exports_root,
    )
    try:
        health = database.health()
        if not health.reachable or not health.schema_current:
            raise typer.BadParameter(
                "PostgreSQL system database is not reachable at Alembic head"
            )
        report = importer.import_all(
            platform=platform,
            start_date=parsed_start,
            end_date=parsed_end,
        )
    finally:
        database.dispose()
    typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, default=str))
    if report.failed:
        raise typer.Exit(code=1)


def _parse_date(value: str, option_name: str) -> date | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter(
            f"{option_name} must use YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != value:
        raise typer.BadParameter(f"{option_name} must use YYYY-MM-DD")
    return parsed


if __name__ == "__main__":
    app()
