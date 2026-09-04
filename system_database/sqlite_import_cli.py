# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Command helpers for the one-way SQLite to PostgreSQL system import."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from system_database.database import SystemDatabase
from system_database.sqlite_import import (
    import_sqlite_system,
    inspect_sqlite_system,
    verify_sqlite_import,
)


ImportMode = Literal["check", "apply", "verify"]


def execute_import_command(
    mode: ImportMode, source_path: str | Path, database: SystemDatabase | None = None
) -> dict[str, Any]:
    """Execute one explicit import phase and return a JSON-serialisable report."""

    if mode == "check":
        return {"mode": mode, **inspect_sqlite_system(source_path).to_dict()}
    if database is None:
        raise ValueError(f"a PostgreSQL SystemDatabase is required for --{mode}")
    if mode == "apply":
        return {"mode": mode, **import_sqlite_system(source_path, database).to_dict()}
    if mode == "verify":
        inspection = inspect_sqlite_system(source_path)
        return {
            "mode": mode,
            **inspection.to_dict(),
            "verified": verify_sqlite_import(source_path, database),
        }
    raise ValueError(f"unsupported import mode: {mode}")
