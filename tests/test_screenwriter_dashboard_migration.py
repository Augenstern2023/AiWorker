# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from identity.repository import IdentityRepository
from system_database import SystemDatabase, SystemDatabaseSettings
from web.navigation_repository import NavigationRepository


ROOT = Path(__file__).resolve().parents[1]


def test_migration_updates_existing_screenwriter_access_and_legacy_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the production system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    engine = create_engine(postgres_url)
    try:
        IdentityRepository(database).seed_defaults()
        NavigationRepository(database).seed_catalog()
        command.downgrade(config, "0012_writer_stage_edit")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_page_access WHERE route = '/writing/dashboard' "
                    "AND role_id IN (SELECT id FROM roles WHERE lower(code) = 'screenwriter')"
                )
            )
            connection.execute(
                text(
                    "UPDATE navigation_items SET label = '工作概览' "
                    "WHERE id = 'navigation:selection:dashboard'"
                )
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            grant_count = connection.execute(
                text(
                    "SELECT count(*) FROM role_page_access p JOIN roles r ON r.id = p.role_id "
                    "WHERE lower(r.code) = 'screenwriter' AND p.route = '/writing/dashboard'"
                )
            ).scalar_one()
            label = connection.execute(
                text(
                    "SELECT label FROM navigation_items "
                    "WHERE id = 'navigation:selection:dashboard'"
                )
            ).scalar_one()
        assert grant_count == 1
        assert label == "选品概览"
    finally:
        command.upgrade(config, "head")
        engine.dispose()
        database.dispose()
