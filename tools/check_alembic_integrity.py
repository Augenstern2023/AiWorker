# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.alembic_guard import (
    GuardError,
    parse_push_updates,
    push_update_from_pre_commit_environment,
    read_database_revision,
    read_remote_main_sha,
    validate_production_preconditions,
    validate_push_updates,
    validate_repository,
    validate_staged_changes,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Alembic migration safety")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("repository")
    subparsers.add_parser("staged")
    subparsers.add_parser("production")
    push = subparsers.add_parser("pre-push")
    push.add_argument("remote_name", nargs="?")
    push.add_argument("remote_url", nargs="?")
    push.add_argument("--remote-name", dest="remote_name_option")
    push.add_argument("--remote-url", dest="remote_url_option")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "repository":
            reports = (validate_repository(PROJECT_ROOT),)
        elif args.mode == "staged":
            report = validate_staged_changes(PROJECT_ROOT)
            reports = (report,) if report else ()
        elif args.mode == "production":
            from system_database.config import SystemDatabaseSettings

            settings = SystemDatabaseSettings.from_environment()
            report = validate_production_preconditions(
                PROJECT_ROOT,
                remote_sha=read_remote_main_sha(PROJECT_ROOT),
                database_revision=read_database_revision(settings.url),
            )
            reports = (report,)
        else:
            remote_name = args.remote_name_option or args.remote_name
            pre_commit_context = push_update_from_pre_commit_environment(os.environ)
            if pre_commit_context:
                remote_name, update = pre_commit_context
                reports = validate_push_updates(PROJECT_ROOT, remote_name, (update,))
            elif not remote_name:
                reports = (validate_repository(PROJECT_ROOT),)
            else:
                updates = parse_push_updates(sys.stdin.read())
                reports = validate_push_updates(PROJECT_ROOT, remote_name, updates)
    except (GuardError, ValueError) as exc:
        print(f"Alembic guard failed: {exc}", file=sys.stderr)
        return 1
    if reports:
        heads = ", ".join(report.head for report in reports)
        print(f"Alembic migration head: {heads}")
    else:
        print("Alembic migration guard: no migration tree update")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
