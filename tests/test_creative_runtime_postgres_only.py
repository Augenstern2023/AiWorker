# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""The runtime must not be able to reach the frozen creative SQLite store.

Task 16 step 5 of the migration. Once the cutover is declared, no runtime module
may import ``sqlite3``, name the legacy store, emit SQLite-specific SQL, or build
a creative repository from a path. A single surviving call site would be enough
to read stale data while PostgreSQL is being written, which is the one failure
mode this whole migration exists to prevent.

This scan reads the module with :mod:`ast` rather than matching substrings. A
substring scan was tried first and reported ten hits in the runtime, all of them
false: nine were prose explaining what the *legacy* implementation used to do
(``sqlite3.Row``, ``sqlite3.IntegrityError``) and two were the text ``pragma``
inside ``# pragma: no cover`` comments. Comments are invisible to ``ast`` and
docstrings are excluded explicitly, so what remains is code that could actually
run.

Deliberately outside the scan:

* ``creative/sqlite_import.py`` and ``creative/sqlite_rollback.py`` - offline
  migration tooling whose entire job is to read and write the legacy store.
* ``creative/legacy_sqlite_repository.py`` - the frozen implementation itself,
  retained until the observation window closes.
* ``creative/repository.py`` - the transitional facade, which the cutover turns
  into a plain alias of ``PostgresCreativeRepository``.
* ``creative/repository_factory.py`` - the explicit storage switch, which must
  name both back ends in order to refuse to guess.
* ``creative/database_schema.py`` - the table declarations.
* ``creative/production_export_cli.py`` - an operator tool that may be pointed at
  a legacy file on purpose.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RUNTIME_PATHS = (
    "creative",
    "screenplay_review",
    "screenplay_optimization",
    "project_review",
    "workflow",
    "WebGUI.py",
)

#: Files that are allowed to know the legacy store exists, with the reason.
EXCLUDED = {
    "creative/sqlite_import.py": "offline importer; reads the legacy store by design",
    "creative/sqlite_rollback.py": "offline exporter; writes the legacy store by design",
    "creative/legacy_sqlite_repository.py": "the frozen implementation, kept for rollback",
    "creative/repository.py": "transitional facade; becomes the PostgreSQL alias",
    "creative/repository_factory.py": "the storage switch; must name both back ends",
    "creative/database_schema.py": "the table declarations",
    "creative/production_export_cli.py": "offline operator tool",
}

#: SQL that only makes sense against SQLite. Matched on word boundaries: a plain
#: substring test flags ``Content-Disposition: attachment`` as ``ATTACH``.
FORBIDDEN_SQL = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bPRAGMA\b",
        r"\bCREATE\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
        r"\bATTACH\b",
        r"\bVACUUM\b",
    )
)
FORBIDDEN_NAMES = (re.compile(r"creative[\w.-]*\.db"), re.compile(r"aiworker_creative"))

#: A floor, so a scan that silently stops collecting files cannot pass.
MINIMUM_FILES_SCANNED = 60


def _runtime_files() -> list[Path]:
    files: list[Path] = []
    for raw in RUNTIME_PATHS:
        path = PROJECT_ROOT / raw
        if path.is_file():
            files.append(path)
            continue
        files.extend(sorted(path.rglob("*.py")))
    return [
        file
        for file in files
        if file.relative_to(PROJECT_ROOT).as_posix() not in EXCLUDED
    ]


def _docstring_value_ids(tree: ast.AST) -> set[int]:
    """Identity of the constants that are docstrings, so prose can be skipped."""

    ids: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _parsed(file: Path) -> tuple[ast.AST, set[int]]:
    # utf-8-sig, not utf-8: workflow/repository.py carries a BOM, and ast.parse
    # rejects a leading U+FEFF outright.
    source = file.read_bytes().decode("utf-8-sig")
    tree = ast.parse(source, filename=str(file))
    return tree, _docstring_value_ids(tree)


def _relative(file: Path) -> str:
    return file.relative_to(PROJECT_ROOT).as_posix()


def test_the_scan_actually_collects_the_runtime() -> None:
    """A scan that finds no files would pass every other test in this module."""

    files = _runtime_files()
    assert len(files) >= MINIMUM_FILES_SCANNED, (
        f"only {len(files)} runtime files collected; the scan is not covering the runtime"
    )
    assert PROJECT_ROOT / "WebGUI.py" in files


def test_every_exclusion_names_a_file_that_still_exists() -> None:
    """An exclusion that outlives its file would silently widen the allowance."""

    missing = [name for name in EXCLUDED if not (PROJECT_ROOT / name).is_file()]
    assert missing == [], f"excluded files no longer exist: {missing}"


@pytest.mark.parametrize("file", _runtime_files(), ids=_relative)
def test_runtime_module_never_imports_sqlite(file: Path) -> None:
    tree, _ = _parsed(file)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "sqlite3", (
                    f"{_relative(file)}:{node.lineno} imports sqlite3"
                )
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "sqlite3", (
                f"{_relative(file)}:{node.lineno} imports from sqlite3"
            )


@pytest.mark.parametrize("file", _runtime_files(), ids=_relative)
def test_runtime_module_never_uses_the_sqlite3_module(file: Path) -> None:
    tree, _ = _parsed(file)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert node.value.id != "sqlite3", (
                f"{_relative(file)}:{node.lineno} uses sqlite3.{node.attr}"
            )


@pytest.mark.parametrize("file", _runtime_files(), ids=_relative)
def test_runtime_module_has_no_sqlite_only_sql(file: Path) -> None:
    """Comments are invisible to ``ast``; docstrings are skipped on purpose."""

    tree, docstrings = _parsed(file)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        for pattern in FORBIDDEN_SQL:
            match = pattern.search(node.value)
            assert match is None, (
                f"{_relative(file)}:{node.lineno} contains {match.group(0)!r} "
                "outside a docstring"
            )
        for pattern in FORBIDDEN_NAMES:
            match = pattern.search(node.value)
            assert match is None, (
                f"{_relative(file)}:{node.lineno} names {match.group(0)!r} "
                "outside a docstring"
            )


@pytest.mark.parametrize("file", _runtime_files(), ids=_relative)
def test_runtime_module_never_builds_a_repository_from_a_path(file: Path) -> None:
    tree, _ = _parsed(file)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in {"CreativeRepository", "LegacySQLiteCreativeRepository"}:
            continue
        assert not node.args, (
            f"{_relative(file)}:{node.lineno} builds {name}() from a positional "
            "argument; the runtime must be handed a repository"
        )


def test_the_frozen_store_is_never_referenced_by_the_runtime_it_serves() -> None:
    """The one path that matters most: nothing should point at the legacy file."""

    offenders = []
    for file in _runtime_files():
        tree, docstrings = _parsed(file)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            if any(pattern.search(node.value) for pattern in FORBIDDEN_NAMES):
                offenders.append(f"{_relative(file)}:{node.lineno}")
    assert offenders == [], f"runtime still names the legacy store: {offenders}"
