# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import pytest

from system_database.config import SystemDatabaseSettings


def test_settings_require_a_postgresql_sqlalchemy_url(monkeypatch):
    monkeypatch.delenv("AIWORKER_SYSTEM_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="AIWORKER_SYSTEM_DATABASE_URL"):
        SystemDatabaseSettings.from_environment()


def test_settings_redact_database_password(monkeypatch):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://app:secret@127.0.0.1:5432/aiworker_system",
    )

    settings = SystemDatabaseSettings.from_environment()

    assert settings.redacted_url == (
        "postgresql+psycopg://app:***@127.0.0.1:5432/aiworker_system"
    )
