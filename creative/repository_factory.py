# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Explicit back-end selection during the creative store migration.

Nothing here guesses. A caller states which back end it wants, so a missing or
misconfigured PostgreSQL install can never be silently served by SQLite: the
SQLite branch exists only for the frozen legacy implementation, contract
comparison and the rollback tests, and it is removed once the seven day
observation window closes.
"""

from __future__ import annotations

import os
from pathlib import Path

from creative.postgres_repository import PostgresCreativeRepository
from system_database import SystemDatabase


SQLITE_BACKEND = "sqlite"
POSTGRESQL_BACKEND = "postgresql"
STORAGE_ENVIRONMENT_VARIABLE = "AIWORKER_CREATIVE_STORAGE"
DEFAULT_LEGACY_SQLITE_PATH = Path(".runtime/creative/creative.db")


def creative_backend_from_environment() -> str:
    """Read the transitional storage switch, defaulting to the live SQLite store."""

    value = os.environ.get(STORAGE_ENVIRONMENT_VARIABLE, "").strip().lower()
    return value or SQLITE_BACKEND


def build_creative_repository(
    database: SystemDatabase,
    *,
    backend: str,
    legacy_sqlite_path: Path | str | None = None,
):
    """Build a creative repository for an explicitly chosen back end."""

    normalized = str(backend or "").strip().lower()
    if normalized == POSTGRESQL_BACKEND:
        return PostgresCreativeRepository(database)
    if normalized == SQLITE_BACKEND:
        from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository

        path = (
            Path(legacy_sqlite_path)
            if legacy_sqlite_path is not None
            else DEFAULT_LEGACY_SQLITE_PATH
        )
        return LegacySQLiteCreativeRepository(path)
    raise ValueError("AIWORKER_CREATIVE_STORAGE must be sqlite or postgresql")


__all__ = [
    "DEFAULT_LEGACY_SQLITE_PATH",
    "POSTGRESQL_BACKEND",
    "SQLITE_BACKEND",
    "STORAGE_ENVIRONMENT_VARIABLE",
    "build_creative_repository",
    "creative_backend_from_environment",
]
