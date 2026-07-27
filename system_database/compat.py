# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Small DB-API compatibility surface used while system repositories move to Core."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy.engine import Connection, CursorResult, Row


class PostgreSQLCompatConnection:
    """Adapts legacy qmark calls to PostgreSQL without creating a SQLite fallback."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def execute(
        self, statement: str, parameters: Sequence[Any] | Mapping[str, Any] | None = None
    ) -> PostgreSQLCompatResult | _NoopResult:
        translated = _translate(statement)
        if translated is None:
            return _NoopResult()
        return PostgreSQLCompatResult(
            self._connection.exec_driver_sql(translated, _parameters(parameters))
        )

    def executemany(
        self, statement: str, parameters: Iterable[Sequence[Any]]
    ) -> None:
        for item in parameters:
            self.execute(statement, item)


class _NoopResult:
    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class PostgreSQLCompatResult:
    def __init__(self, result: CursorResult[Any]) -> None:
        self._result = result

    @property
    def rowcount(self) -> int:
        return self._result.rowcount

    def fetchone(self) -> PostgreSQLCompatRow | None:
        row = self._result.fetchone()
        return PostgreSQLCompatRow(row) if row is not None else None

    def fetchall(self) -> list[PostgreSQLCompatRow]:
        return [PostgreSQLCompatRow(row) for row in self._result.fetchall()]


class PostgreSQLCompatRow(dict[str, Any]):
    def __init__(self, row: Row[Any]) -> None:
        super().__init__(row._mapping)
        self._values = tuple(row)

    def __getitem__(self, key: str | int) -> Any:
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)


def _parameters(
    parameters: Sequence[Any] | Mapping[str, Any] | None,
) -> tuple[Any, ...] | Mapping[str, Any]:
    if parameters is None:
        return ()
    if isinstance(parameters, Mapping):
        return parameters
    return tuple(parameters)


def _translate(statement: str) -> str | None:
    translated = statement.strip()
    if translated.upper() == "BEGIN IMMEDIATE":
        return None
    translated = translated.replace("?", "%s")
    translated = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", translated)
    translated = re.sub(
        r"=\s*%s\s+COLLATE\s+NOCASE", "ILIKE %s", translated, flags=re.IGNORECASE
    )
    translated = re.sub(r"\s+COLLATE\s+NOCASE", "", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bWHERE\s+0\b", "WHERE FALSE", translated)
    if re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", translated, flags=re.IGNORECASE):
        translated = re.sub(
            r"^INSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            translated,
            flags=re.IGNORECASE,
        )
        translated = translated.rstrip(";") + " ON CONFLICT DO NOTHING"
    return translated
