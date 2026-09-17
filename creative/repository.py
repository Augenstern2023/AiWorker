# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Transitional facade over the creative repository back ends.

While the migration is in flight ``CreativeRepository`` still resolves to the
frozen SQLite implementation, so the ~40 existing call sites keep working
unchanged. The PostgreSQL repositories are built and contract-tested alongside
it, and the cutover swaps the binding below to
:class:`creative.postgres_repository.PostgresCreativeRepository`.

New code must not import from this module. Use
:func:`creative.repository_factory.build_creative_repository` or the concrete
PostgreSQL repository so the SQLite dependency is explicit.
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
from creative.legacy_sqlite_repository import (
    CreativeRepository as LegacySQLiteCreativeRepository,
)


# Swapped to PostgresCreativeRepository when the production cutover lands.
CreativeRepository = LegacySQLiteCreativeRepository

__all__ = [
    "CreativeRepository",
    "LegacySQLiteCreativeRepository",
    "TopicDecisionConflict",
    "DuplicateTopicError",
    "DownloadTopicDecisionConflict",
    "GENERATION_OPERATION_LEASE_SECONDS",
    "SIDE_EFFECT_OPERATION_LEASE_SECONDS",
    "canonical_request_hash",
]
