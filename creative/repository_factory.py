# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Creative repository construction after the PostgreSQL runtime cutover.

Runtime construction is PostgreSQL-only and fails closed. Explicit SQLite
construction remains solely for offline import, rollback and contract tools.
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
    """Read the legacy tool switch, defaulting to PostgreSQL."""

    value = os.environ.get(STORAGE_ENVIRONMENT_VARIABLE, "").strip().lower()
    return value or POSTGRESQL_BACKEND


def normalize_creative_backend(backend: str | None) -> str:
    """Validate an explicitly chosen back end name."""

    normalized = str(backend or "").strip().lower()
    if normalized not in {SQLITE_BACKEND, POSTGRESQL_BACKEND}:
        raise ValueError("AIWORKER_CREATIVE_STORAGE must be sqlite or postgresql")
    return normalized


def build_creative_repository(
    database: SystemDatabase,
    *,
    backend: str,
    legacy_sqlite_path: Path | str | None = None,
):
    """Build a creative repository for an explicitly chosen back end."""

    normalized = normalize_creative_backend(backend)
    if normalized == POSTGRESQL_BACKEND:
        return PostgresCreativeRepository(database)
    if legacy_sqlite_path is None:
        raise ValueError("the sqlite back end requires an explicit legacy_sqlite_path")
    from creative.legacy_sqlite_repository import LegacySQLiteCreativeRepository

    return LegacySQLiteCreativeRepository(Path(legacy_sqlite_path))


def build_runtime_creative_repository(
    database: SystemDatabase,
    *,
    legacy_sqlite_path: Path | str | None = None,
):
    """Build the PostgreSQL runtime repository, failing closed on bad health."""

    del legacy_sqlite_path
    health = database.health()
    if not health.reachable or not health.schema_current:
        raise RuntimeError(
            "creative PostgreSQL is unavailable "
            f"(reachable={health.reachable}, schema_current={health.schema_current}, "
            f"revision={health.current_revision})"
        )
    return PostgresCreativeRepository(database)


__all__ = [
    "DEFAULT_LEGACY_SQLITE_PATH",
    "POSTGRESQL_BACKEND",
    "SQLITE_BACKEND",
    "STORAGE_ENVIRONMENT_VARIABLE",
    "build_creative_repository",
    "build_runtime_creative_repository",
    "creative_backend_from_environment",
    "normalize_creative_backend",
]
