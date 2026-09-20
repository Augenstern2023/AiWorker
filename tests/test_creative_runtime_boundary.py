# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""The runtime boundary between the creative store and its callers.

Production runtime modules must reach the creative store only through an
injected PostgreSQL repository. They must never derive a database path, never
carry a database location inside a task payload, and never fall back to the
frozen SQLite store when no repository was supplied.

Migration tooling and the frozen implementation legitimately mention the legacy
store, so they are deliberately outside this scan:

* ``creative/repository.py``, ``creative/legacy_sqlite_repository.py``
* ``creative/repository_factory.py``, ``creative/sqlite_import.py``,
  ``creative/sqlite_rollback.py``, ``creative/database_schema.py``
* ``creative/production_export_cli.py``, ``scripts/``, ``tools/`` and tests
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RUNTIME_MODULES = (
    "WebGUI.py",
    "creative/api.py",
    "creative/application_service.py",
    "creative/selection_stage_service.py",
    "creative/breakdown_worker.py",
    "creative/phase4_worker.py",
    "creative/workflow_adapter.py",
    "screenplay_review/api.py",
    "screenplay_review/worker.py",
    "screenplay_optimization/api.py",
    "screenplay_optimization/worker.py",
    "project_review/worker.py",
    "workflow/transition_service.py",
)

FORBIDDEN_PATTERNS = (
    ("the legacy SQLite store name", re.compile(r"creative\.db")),
    ("the creative_db_path payload key", re.compile(r"creative_db_path")),
    ("a db_path payload key", re.compile(r"""["']db_path["']""")),
    (
        "a CreativeRepository built from a path",
        re.compile(r"(?<![A-Za-z0-9_])CreativeRepository\s*\("),
    ),
)


@pytest.mark.parametrize("module", RUNTIME_MODULES)
def test_runtime_module_never_names_a_creative_database_location(module: str) -> None:
    source = (PROJECT_ROOT / module).read_text(encoding="utf-8")
    for label, pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(source)
        assert match is None, (
            f"{module} still contains {label}: {match.group(0)!r} "
            f"(offset {match.start()})"
        )


def test_creative_worker_payload_does_not_expose_database_location() -> None:
    from creative.application_service import build_generation_payload

    payload = build_generation_payload(project_id=12, operation_id="op-1")

    assert "db_path" not in payload
    assert "database_url" not in payload
    extra = payload["extra"]
    assert "db_path" not in extra
    assert "creative_db_path" not in extra
    assert "database_url" not in extra
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "creative.db" not in serialized
    assert "AIWORKER_SYSTEM_DATABASE_URL" not in serialized
    assert "postgresql" not in serialized


def test_webgui_drama_job_payloads_do_not_expose_database_location() -> None:
    """WebGUI builds its drama job payloads inline; scan them by hand."""

    source = (PROJECT_ROOT / "WebGUI.py").read_text(encoding="utf-8")
    assert "creative_db_path" not in source
    assert "db_path" not in source
