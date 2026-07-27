# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from identity.repository import IdentityRepository
from system_database import SystemDatabase, SystemDatabaseSettings
from web.navigation_repository import NavigationRepository


def test_navigation_repository_seeds_and_only_updates_presentation(
    monkeypatch: pytest.MonkeyPatch,
):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    IdentityRepository(database).seed_defaults()
    repository = NavigationRepository(database)

    repository.seed_catalog()
    original = next(item for item in repository.list_items() if item["id"] == "navigation:admin:audit")
    changed = repository.update_presentation(
        original["id"], label="审计中心", icon="bi-shield-check", sort_order=99, is_enabled=False
    )

    assert changed["label"] == "审计中心"
    assert changed["route"] == original["route"]
    assert changed["required_permission"] == original["required_permission"]
