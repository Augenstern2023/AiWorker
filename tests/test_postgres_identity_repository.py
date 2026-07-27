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
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from audit.service import AuditService
from identity.repository import IdentityRepository
from identity.schema import Principal
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import WorkflowRepository


def test_identity_repository_uses_postgresql_system_database(
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
    repository = IdentityRepository(database)

    repository.seed_defaults()
    username = f"postgres-user-{uuid.uuid4()}"
    user = repository.create_user(username, "PostgreSQL User", "hash")

    assert repository.get_user(user["id"])["username"] == username
    assert repository.get_role_by_code("SYSTEM_ADMIN") is not None

    task = WorkflowRepository(repository).create_task(
        f"workflow:{uuid.uuid4()}",
        "selection",
        "selection_operator",
        actor=Principal(
            user_id=user["id"],
            username=username,
            display_name="PostgreSQL User",
            role_codes=(),
            permissions=frozenset(),
            data_scope="task",
            session_id="postgres-test",
        ),
    )
    assert task["status"] == "pool"

    audit = AuditService(repository)
    audit.record(
        actor=Principal(
            user_id=user["id"],
            username=username,
            display_name="PostgreSQL User",
            role_codes=(),
            permissions=frozenset(),
            data_scope="task",
            session_id="postgres-test",
        ),
        action="postgres.verify",
        target_type="workflow_task",
        target_id=task["id"],
        before=None,
        after={"status": "pool"},
        reason=None,
        result="success",
    )
    assert audit.query(action="postgres.verify")[0]["actor_display_name"] == "PostgreSQL User"
