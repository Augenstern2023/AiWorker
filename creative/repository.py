# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Public creative repository facade after the PostgreSQL cutover.

Runtime callers importing ``CreativeRepository`` receive the shared-system-
database implementation. The frozen SQLite class remains available only from
``creative.legacy_sqlite_repository`` to offline migration and rollback tools.
"""

from __future__ import annotations

from creative.legacy_sqlite_repository import (
    GENERATION_OPERATION_LEASE_SECONDS,
    SIDE_EFFECT_OPERATION_LEASE_SECONDS,
    DownloadTopicDecisionConflict,
    DuplicateTopicError,
    TopicDecisionConflict,
    canonical_request_hash,
)
from creative.postgres_repository import PostgresCreativeRepository


CreativeRepository = PostgresCreativeRepository

__all__ = [
    "CreativeRepository",
    "PostgresCreativeRepository",
    "TopicDecisionConflict",
    "DuplicateTopicError",
    "DownloadTopicDecisionConflict",
    "GENERATION_OPERATION_LEASE_SECONDS",
    "SIDE_EFFECT_OPERATION_LEASE_SECONDS",
    "canonical_request_hash",
]
