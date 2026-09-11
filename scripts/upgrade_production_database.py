# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system_database.config import SystemDatabaseSettings
from tools.alembic_guard import (
    GuardError,
    MigrationReport,
    read_database_revision,
    read_remote_main_sha,
    validate_production_preconditions,
)


def guarded_upgrade(
    repo_root: Path,
    *,
    read_remote_sha: Callable[[], str],
    read_database_revision: Callable[[], str | None],
    upgrade: Callable[[str], None],
) -> MigrationReport:
    remote_sha = read_remote_sha()
    database_revision = read_database_revision()
    report = validate_production_preconditions(
        repo_root,
        remote_sha=remote_sha,
        database_revision=database_revision,
    )
    try:
        upgrade("head")
    except Exception as exc:
        raise GuardError(
            "Alembic 升级失败；数据库事务已由 Alembic 处理"
        ) from exc
    return report


def main() -> int:
    try:
        settings = SystemDatabaseSettings.from_environment()
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        report = guarded_upgrade(
            PROJECT_ROOT,
            read_remote_sha=lambda: read_remote_main_sha(PROJECT_ROOT),
            read_database_revision=lambda: read_database_revision(settings.url),
            upgrade=lambda target: command.upgrade(config, target),
        )
    except (GuardError, ValueError) as exc:
        print(f"Production migration blocked: {exc}", file=sys.stderr)
        return 1
    print(
        "Production migration completed: "
        f"database={settings.redacted_url}, head={report.head}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
