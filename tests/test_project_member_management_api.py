# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from threading import Event
import uuid

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import make_url

from audit.service import AuditService
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository
from workflow import SYSTEM_ACTOR


def _postgres_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SystemDatabase, IdentityRepository]:
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
    return database, identity


def _role_user(identity: IdentityRepository, role_code: str, label: str) -> dict:
    role = identity.get_role_by_code(role_code)
    assert role is not None
    user = identity.create_user(
        f"member-editor-{role_code}-{uuid.uuid4().hex}", label, "hash"
    )
    identity.replace_user_roles(user["id"], [role["id"]])
    return user


def test_member_route_resolves_local_project_id_before_repository_calls() -> None:
    calls: list[tuple[str, str, str | None]] = []

    class RecordingIdentityRepository:
        @staticmethod
        def get_user(user_id: str) -> dict[str, str]:
            return {
                "id": user_id,
                "username": "creator",
                "display_name": "项目创建人",
            }

    class RecordingWorkflowRepository:
        identity_repository = RecordingIdentityRepository()

        @staticmethod
        def assert_can_manage_project(
            project_id: str,
            principal: object,
            *,
            project_uid: str | None = None,
        ) -> None:
            del principal
            calls.append(("assert", project_id, project_uid))

        @staticmethod
        def list_project_members(
            project_id: str,
            *,
            project_uid: str | None = None,
        ) -> list[dict[str, str]]:
            calls.append(("list", project_id, project_uid))
            return [
                {
                    "id": "membership-8",
                    "project_id": project_id,
                    "project_uid": project_uid or "",
                    "user_id": "creator-8",
                    "project_role": "project_creator",
                    "status": "active",
                }
            ]

    resolved_uid = "3f1d2c4b-5a69-4e78-9b01-2c3d4e5f6a7b"
    router = build_workflow_router(
        RecordingWorkflowRepository(),  # type: ignore[arg-type]
        project_identity_resolver=lambda local_id: (
            f"creative:store:project:{local_id}",
            resolved_uid,
        ),
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/workflow/projects/{project_id}/members"
        and "GET" in route.methods
    )

    result = endpoint("8", SYSTEM_ACTOR)

    assert calls == [
        ("assert", "creative:store:project:8", resolved_uid),
        ("list", "creative:store:project:8", resolved_uid),
    ]
    assert result[0]["project_uid"] == resolved_uid


def test_project_creator_can_edit_but_cannot_manage_members(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    creator_user = _role_user(identity, "screenwriter", "项目创建负责人")
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert creator is not None
    project_id = f"creator-{uuid.uuid4().hex}"

    def cleanup() -> None:
        workflow.delete_project_data(project_id)
        if identity.get_user(creator_user["id"]) is not None:
            identity.soft_delete_user(creator_user["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    workflow.add_project_member(
        project_id,
        creator.user_id,
        "project_creator",
        creator,
        allow_creator_bootstrap=True,
    )

    assert workflow.is_active_project_member(project_id, creator)
    with pytest.raises(PermissionError):
        workflow.assert_can_manage_project(project_id, creator)


def test_ensure_project_creator_cannot_revive_concurrently_deactivated_membership(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    project_id = f"creator-interleave-{uuid.uuid4().hex}"
    owner_user = _role_user(identity, "project_owner", "并发停用负责人")
    creator_user = _role_user(identity, "screenwriter", "并发创建负责人")
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "owner-session")
    creator = permissions.principal_for_user(
        creator_user["id"], "creator-session"
    )
    assert owner is not None and creator is not None
    transaction_committed = Event()
    allow_ensure_to_return = Event()

    class InterleavingIdentityRepository:
        """Pause the ensure request after its first real transaction commits."""

        def __init__(self, repository: IdentityRepository) -> None:
            self.repository = repository
            self.paused = False

        def __getattr__(self, name: str):
            return getattr(self.repository, name)

        @contextmanager
        def transaction(self):
            with self.repository.transaction() as connection:
                yield connection
            if not self.paused:
                self.paused = True
                transaction_committed.set()
                assert allow_ensure_to_return.wait(timeout=5)

    workflow = WorkflowRepository(identity)
    interleaved_workflow = WorkflowRepository(
        InterleavingIdentityRepository(identity)
    )

    def cleanup() -> None:
        allow_ensure_to_return.set()
        workflow.delete_project_data(project_id)
        for user in (owner_user, creator_user):
            if identity.get_user(user["id"]) is not None:
                identity.soft_delete_user(user["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            interleaved_workflow.ensure_project_creator,
            project_id,
            creator.user_id,
            creator,
        )
        assert transaction_committed.wait(timeout=5)
        active = workflow.add_project_member(
            project_id, creator.user_id, "project_creator", owner
        )
        workflow.remove_project_member(project_id, active["id"], owner)
        allow_ensure_to_return.set()
        assert future.result(timeout=5) is not None

    assert [
        (member["project_role"], member["status"])
        for member in workflow.list_project_members(project_id)
    ] == [("project_creator", "inactive")]


def test_project_member_editor_lists_candidates_and_member_identity(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    project_id = f"member-editor-{uuid.uuid4().hex}"
    owner_user = _role_user(identity, "project_owner", "成员编辑负责人")
    planner_user = _role_user(identity, "creative_planner", "成员编辑策划")
    writer_user = _role_user(identity, "screenwriter", "成员编辑编剧")
    outsider_user = _role_user(identity, "selection_operator", "成员编辑无权用户")
    users = (owner_user, planner_user, writer_user, outsider_user)

    def cleanup() -> None:
        workflow.delete_project_data(project_id)
        for user in users:
            if identity.get_user(user["id"]) is not None:
                identity.soft_delete_user(user["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "member-owner")
    outsider = permissions.principal_for_user(outsider_user["id"], "member-outsider")
    assert owner is not None and outsider is not None
    workflow.add_project_member(project_id, owner.user_id, "project_owner", owner)

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = AuditService(identity)
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_workflow_router(workflow))
    client = TestClient(app)

    members = client.get(f"/api/workflow/projects/{project_id}/members")
    writer_candidates = client.get(
        f"/api/workflow/projects/{project_id}/member-candidates",
        params={"project_role": "screenwriter"},
    )
    planner_candidates = client.get(
        f"/api/workflow/projects/{project_id}/member-candidates",
        params={"project_role": "creative_planner"},
    )
    owner_candidates = client.get(
        f"/api/workflow/projects/{project_id}/member-candidates",
        params={"project_role": "project_owner"},
    )

    assert members.status_code == 200, members.text
    assert members.json() == [
        {
            **workflow.list_project_members(project_id)[0],
            "username": owner_user["username"],
            "display_name": owner_user["display_name"],
        }
    ]
    assert writer_candidates.status_code == 200, writer_candidates.text
    assert writer_user["id"] in {item["user_id"] for item in writer_candidates.json()}
    assert planner_user["id"] in {item["user_id"] for item in planner_candidates.json()}
    assert owner_user["id"] in {item["user_id"] for item in owner_candidates.json()}
    assert client.get(
        f"/api/workflow/projects/{project_id}/member-candidates",
        params={"project_role": "unsupported"},
    ).status_code == 400

    created = client.post(
        f"/api/workflow/projects/{project_id}/members",
        json={
            "user_id": writer_user["id"],
            "project_role": "screenwriter",
            "status": "active",
        },
    )
    assert created.status_code == 201, created.text
    membership_id = created.json()["id"]
    removed = client.delete(
        f"/api/workflow/projects/{project_id}/members/{membership_id}"
    )
    assert removed.status_code == 204, removed.text

    owner_membership_id = next(
        member["id"]
        for member in workflow.list_project_members(project_id)
        if member["project_role"] == "project_owner"
    )
    removed_last_owner = client.delete(
        f"/api/workflow/projects/{project_id}/members/{owner_membership_id}"
    )
    assert removed_last_owner.status_code == 204, removed_last_owner.text
    assert not workflow.list_project_members(project_id)

    app.dependency_overrides[current_principal] = lambda: outsider
    assert client.get(
        f"/api/workflow/projects/{project_id}/member-candidates",
        params={"project_role": "screenwriter"},
    ).status_code == 403
