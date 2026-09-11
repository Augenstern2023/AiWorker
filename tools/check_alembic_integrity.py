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
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.alembic_guard import GuardError, validate_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Alembic migration safety")
    parser.add_argument("mode", choices=("repository",))
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        report = validate_repository(PROJECT_ROOT)
    except GuardError as exc:
        print(f"Alembic guard failed: {exc}", file=sys.stderr)
        return 1
    print(f"Alembic migration head: {report.head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
