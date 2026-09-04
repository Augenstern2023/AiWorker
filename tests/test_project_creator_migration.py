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

from creative.project_creator_migration import migrate_project_creator_memberships
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import WorkflowRepository


def test_creator_migration_is_exact_idempotent_and_preserves_inactive_new_role(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the production system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity = IdentityRepository(database)
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    writer_role = identity.get_role_by_code("screenwriter")
    assert owner_role is not None and writer_role is not None
    owner_user = identity.create_user(f"owner-{uuid.uuid4().hex}", "负责人", "hash")
    creator_user = identity.create_user(f"creator-{uuid.uuid4().hex}", "创建人", "hash")
    identity.replace_user_roles(owner_user["id"], [owner_role["id"]])
    identity.replace_user_roles(creator_user["id"], [writer_role["id"]])
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "owner-session")
    assert owner is not None
    creative = CreativeRepository(tmp_path / "creative.db")
    project, _ = creative.ensure_original_project(
        seed_text="种子", name="迁移项目", genre="", target_platform="",
        target_audience="", target_episode_count=20, episode_duration_seconds=90,
        constraints={}, reference_refs=[], created_by=creator_user["id"],
        creation_request_id=uuid.uuid4().hex,
    )
    workflow = WorkflowRepository(identity)
    project_id = str(project["id"])
    try:
        workflow.add_project_member(project_id, owner.user_id, "project_owner", owner)
        workflow.add_project_member(project_id, creator_user["id"], "project_owner", owner)

        assert migrate_project_creator_memberships(creative, workflow) == 1
        assert [(item["project_role"], item["status"]) for item in workflow.list_project_members(project_id) if item["user_id"] == creator_user["id"]] == [("project_creator", "active")]

        creator_member = next(
            item for item in workflow.list_project_members(project_id)
            if item["user_id"] == creator_user["id"]
        )
        workflow.remove_project_member(project_id, creator_member["id"], owner)
        workflow.add_project_member(project_id, creator_user["id"], "project_owner", owner)

        assert migrate_project_creator_memberships(creative, workflow) == 1
        assert [(item["project_role"], item["status"]) for item in workflow.list_project_members(project_id) if item["user_id"] == creator_user["id"]] == [("project_creator", "inactive")]
        assert migrate_project_creator_memberships(creative, workflow) == 0
    finally:
        workflow.delete_project_data(project_id)
        identity.soft_delete_user(owner_user["id"])
        identity.soft_delete_user(creator_user["id"])
        database.dispose()
