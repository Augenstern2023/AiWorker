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

from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import WorkflowRepository


def test_project_creator_can_contribute_but_cannot_govern_or_delete(
    monkeypatch: pytest.MonkeyPatch,
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
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert owner is not None and creator is not None
    workflow = WorkflowRepository(identity)
    project_id = f"governance-{uuid.uuid4().hex}"
    try:
        workflow.add_project_member(project_id, creator.user_id, "project_creator", creator, allow_creator_bootstrap=True)

        assert permissions.can_create_project_content(creator, project_id, active_member=True)
        assert not permissions.can_govern_project(creator, project_id)
        assert not permissions.can_delete_project(creator, project_id)
        assert permissions.can_govern_project(owner, project_id)
        assert permissions.can_delete_project(owner, project_id)

        creator_member = workflow.list_project_members(project_id)[0]
        workflow.remove_project_member(project_id, creator_member["id"], owner)
        assert not workflow.is_active_project_member(project_id, creator)
        assert not permissions.can_create_project_content(creator, project_id, active_member=False)
        assert workflow.list_project_members(project_id)[0]["status"] == "inactive"
    finally:
        workflow.delete_project_data(project_id)
        identity.soft_delete_user(owner_user["id"])
        identity.soft_delete_user(creator_user["id"])
        database.dispose()
