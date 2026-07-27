# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from system_database.sqlite_import import inspect_sqlite_system
from system_database.sqlite_import_cli import execute_import_command


def _source_database(path: Path, *, details_json: str = "{}") -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, details_json TEXT);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT);
            INSERT INTO users (id, username, details_json)
            VALUES ('role:system', 'admin', :details_json);
            INSERT INTO sessions (id, user_id) VALUES ('session-1', 'role:system');
            """.replace(":details_json", f"'{details_json}'")
        )
    return path


def test_inspection_keeps_text_identifiers_and_excludes_legacy_sessions(tmp_path: Path):
    report = inspect_sqlite_system(_source_database(tmp_path / "system.db"))

    assert report.source_counts == {"users": 1}
    assert report.skipped_session_count == 1
    assert report.primary_key_samples["users"] == ("role:system",)


def test_inspection_rejects_invalid_json_before_an_import(tmp_path: Path):
    source = _source_database(tmp_path / "invalid-system.db", details_json="not-json")

    with pytest.raises(ValueError, match="users.details_json"):
        inspect_sqlite_system(source)


def test_check_command_is_read_only_and_returns_machine_readable_report(tmp_path: Path):
    report = execute_import_command("check", _source_database(tmp_path / "check.db"))

    assert report["mode"] == "check"
    assert report["source_counts"] == {"users": 1}
    assert report["skipped_session_count"] == 1
