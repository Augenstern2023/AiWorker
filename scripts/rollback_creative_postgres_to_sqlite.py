# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Export PostgreSQL back to a legacy-readable SQLite store during the rollback window.

The rollback file is the last line of defence of the cutover, so this CLI is
deliberately paranoid about where it may write:

* ``--rollback-dir`` is the operator-designated directory that holds rollback
  artifacts, and ``--destination`` must resolve to a path inside it,
* nothing may be written inside ``.runtime/creative/``, which still owns the
  live legacy store and its sealed pre-migration backups, and
* an existing destination is refused outright.

On success the SHA-256 of the published file and the full verification report
are printed as one JSON object. A verification failure exits non-zero with the
report's ``problems`` in the output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

# Permit ``python scripts/rollback_creative_postgres_to_sqlite.py`` from the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creative.sqlite_rollback import export_sqlite_rollback  # noqa: E402
from system_database import SystemDatabase, SystemDatabaseSettings  # noqa: E402

#: The legacy runtime directory: the live store and its sealed backups live here.
RUNTIME_CREATIVE_DIRECTORY = Path(".runtime") / "creative"

#: The live legacy store, which must never be the target of an export.
LEGACY_STORE_NAME = "creative.db"

#: Sealed pre-cutover backups of the legacy store: the only copy of the
#: pre-migration data, so an export may never be written to one either.
SEALED_BACKUP_PREFIX = "creative.db.pre-postgres-migration."


def _resolved(path: Path) -> Path:
    """Resolve a user-supplied path, collapsing ``..`` and symlinks."""

    return Path(path).expanduser().resolve()


def _sealed_paths(project_root: Path) -> tuple[Path, ...]:
    """The named paths inside the runtime directory that are never a destination."""

    runtime = _resolved(project_root / RUNTIME_CREATIVE_DIRECTORY)
    sealed = [runtime / LEGACY_STORE_NAME]
    if runtime.is_dir():
        sealed.extend(sorted(runtime.glob(f"{SEALED_BACKUP_PREFIX}*.bak")))
    return tuple(sealed)


def _refusal_reason(
    destination: Path,
    rollback_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> str | None:
    """Why an export to ``destination`` must be refused, or ``None``.

    Both paths are compared *after* resolution, so ``..`` and symlinks cannot
    sneak a destination into a forbidden place.
    """

    resolved_destination = _resolved(destination)
    resolved_rollback = _resolved(rollback_dir)
    runtime = _resolved(project_root / RUNTIME_CREATIVE_DIRECTORY)

    if resolved_destination.is_relative_to(runtime):
        if resolved_destination in _sealed_paths(project_root):
            return (
                f"refusing to write to the sealed legacy creative store "
                f"{resolved_destination}"
            )
        return (
            f"refusing to write inside the live creative runtime directory "
            f"{runtime}"
        )
    if not resolved_destination.is_relative_to(resolved_rollback):
        return (
            f"destination {resolved_destination} is not inside the rollback "
            f"directory {resolved_rollback}"
        )
    return None


def main(
    destination: Path = typer.Option(
        ...,
        "--destination",
        help="SQLite file to publish; must resolve inside --rollback-dir.",
    ),
    rollback_dir: Path = typer.Option(
        ...,
        "--rollback-dir",
        help="Directory that holds the rollback artifacts.",
    ),
) -> None:
    reason = _refusal_reason(destination, rollback_dir)
    if reason is not None:
        raise typer.BadParameter(reason)

    try:
        settings = SystemDatabaseSettings.from_environment()
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error

    database = SystemDatabase(settings)
    try:
        health = database.health()
        if not health.reachable:
            raise typer.BadParameter(
                f"PostgreSQL source is not reachable: {health.database_url_redacted}"
            )
        try:
            report = export_sqlite_rollback(database, destination)
        except FileExistsError as error:
            raise typer.BadParameter(str(error)) from error
    finally:
        database.dispose()

    payload = report.to_dict()
    payload["source_backend"] = "postgresql"
    payload["source_database"] = health.database_url_redacted
    payload["schema_current"] = bool(health.schema_current)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    if not report.verified:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
