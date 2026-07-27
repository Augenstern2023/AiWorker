# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from system_database.config import SystemDatabaseSettings
from system_database.health import DatabaseHealth


class SystemDatabase:
    def __init__(self, settings: SystemDatabaseSettings) -> None:
        self.settings = settings
        self.engine: Engine = create_engine(settings.url, pool_pre_ping=True)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as connection:
            yield connection

    def health(self) -> DatabaseHealth:
        try:
            with self.connection() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return DatabaseHealth(
                reachable=False,
                database_url_redacted=self.settings.redacted_url,
            )
        return DatabaseHealth(
            reachable=True,
            database_url_redacted=self.settings.redacted_url,
        )

    def dispose(self) -> None:
        self.engine.dispose()
