# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""The PostgreSQL creative repository.

This class is the single public entry point for creative reads and writes once
the store lives in PostgreSQL. It composes one mixin per domain: topics,
projects, versions, operations, reviews and deliveries. The composition is
deliberately flat so that

* every domain method shares one connection/transaction boundary, and
* :meth:`public_surface` can prove the PostgreSQL class implements the whole
  legacy surface the plan requires.

Each domain mixin lives in :mod:`creative.repositories` and assumes only the
:class:`~creative.repositories.base.CreativeRepositoryBase` contract.
"""

from __future__ import annotations

import inspect as _inspect
from typing import Any

from creative.repositories.base import CreativeRepositoryBase
from creative.repositories.deliveries import DeliveriesRepository
from creative.repositories.operations import OperationsRepository
from creative.repositories.projects import ProjectsRepository
from creative.repositories.reviews import ReviewsRepository
from creative.repositories.topics import TopicsRepository
from creative.repositories.versions import VersionsRepository


class PostgresCreativeRepository(
    TopicsRepository,
    ProjectsRepository,
    VersionsRepository,
    OperationsRepository,
    ReviewsRepository,
    DeliveriesRepository,
    CreativeRepositoryBase,
):
    """Creative store backed by the shared PostgreSQL system database."""

    @classmethod
    def public_surface(cls) -> frozenset[str]:
        """Names callable by legacy callers, used by the surface-parity test."""

        return frozenset(
            name
            for name, value in _inspect.getmembers(cls)
            if callable(value) and not name.startswith("_")
        )

    def describe(self) -> dict[str, Any]:
        """Small, credential-free summary useful in health and smoke output."""

        return {
            "backend": "postgresql",
            "store_id": self.store_id,
            "health": self.health(),
        }


__all__ = ["PostgresCreativeRepository"]
