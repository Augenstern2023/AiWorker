# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
import pytest
from sqlalchemy.engine import make_url

from audit.service import AuditService
from creative.api import build_creative_router, creative_api_principal
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository



@pytest.fixture(autouse=True)
def _use_postgres_creative(monkeypatch, postgres_creative):
    """Exercise runtime behavior against the isolated PostgreSQL store."""
    monkeypatch.setitem(
        globals(), "CreativeRepository", lambda *_args, **_kwargs: postgres_creative
    )
    monkeypatch.setenv(
        "AIWORKER_TEST_SYSTEM_DATABASE_URL", postgres_creative.database.settings.url
    )


class _AllowPageApi:
    @staticmethod
    def can_call_page_api(principal, method: str, path: str) -> bool:
        del principal, method, path
        return True


def _topic(repository: CreativeRepository) -> dict:
    return repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "planning-assignment-test",
                "source_path": "data/hg/rank.json",
                "captured_at": "2026-07-23",
                "title": "创建项目及任务测试剧",
                "description": "测试简介",
                "category": "都市",
                "rank_type": "热播榜",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]


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
    repository = IdentityRepository(database)
    repository.seed_defaults()
    return database, repository


def _create_role_user(
    identity: IdentityRepository, role_codes: tuple[str, ...], label: str
) -> dict:
    suffix = uuid.uuid4().hex
    user = identity.create_user(f"{label}-{suffix}", label, "hash")
    role_ids = []
    for role_code in role_codes:
        role = identity.get_role_by_code(role_code)
        assert role is not None
        role_ids.append(role["id"])
    identity.replace_user_roles(user["id"], role_ids)
    return user


def _render_topic_table(principal) -> str:
    root = Path(__file__).resolve().parents[1]
    environment = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    return environment.get_template("selection/topics.html").render(
        principal=principal,
        navigation=[],
        page_title="选题库",
        workspace_page="selection-topics",
        asset_version="test",
    )


def test_screenwriter_creates_project_with_team_and_pool_tasks_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    runtime = tmp_path / ".runtime"
    database, identity = _postgres_identity(monkeypatch)
    cleanup: dict[str, object] = {"project_id": None, "user_ids": []}
    workflow = WorkflowRepository(identity)

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    creator_user = _create_role_user(
        identity,
        ("project_owner", "screenwriter", "selection_operator"),
        "创建负责人",
    )
    cleanup["user_ids"].append(creator_user["id"])
    planner_role = identity.create_role(
        f"brief_specialist_{uuid.uuid4().hex}", "任务书专家", ""
    )
    identity.replace_role_claimable_task_types(planner_role["id"], ["brief"])
    planner_user = _create_role_user(identity, (planner_role["code"],), "任务书专家")
    cleanup["user_ids"].append(planner_user["id"])
    writer_user = _create_role_user(identity, ("screenwriter",), "项目编剧")
    cleanup["user_ids"].append(writer_user["id"])
    outsider_user = _create_role_user(
        identity, ("screenwriter", "selection_operator"), "外部编剧"
    )
    cleanup["user_ids"].append(outsider_user["id"])
    selection_reader_user = _create_role_user(
        identity, ("selection_operator",), "普通选题读者"
    )
    cleanup["user_ids"].append(selection_reader_user["id"])
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    writer = permissions.principal_for_user(writer_user["id"], "writer-session")
    outsider = permissions.principal_for_user(outsider_user["id"], "outsider-session")
    selection_reader = permissions.principal_for_user(
        selection_reader_user["id"], "selection-reader-session"
    )
    assert (
        creator is not None
        and writer is not None
        and outsider is not None
        and selection_reader is not None
    )
    assert "project.create" in creator.permissions
    assert "project.write.established.writing" in outsider.permissions
    assert "project.manage" not in outsider.permissions
    creator = replace(
        creator,
        permissions=creator.permissions | frozenset({"project.read.managed"}),
    )

    creative = CreativeRepository(runtime / "creative/creative.db")
    project_sequence = 1_000_000_000 + (uuid.uuid4().int % 1_000_000_000)
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = AuditService(identity)
    app.dependency_overrides[creative_api_principal] = lambda: creator
    app.dependency_overrides[current_principal] = lambda: creator
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime, repository=creative
        )
    )
    client = TestClient(app)
    payload = {
        "name": "编剧创建的项目",
        "target_platform": "hg",
        "target_episode_count": 20,
        "planner_member_ids": [planner_user["id"]],
        "writer_member_ids": [writer_user["id"]],
        "creation_request_id": uuid.uuid4().hex,
    }
    project_id: str | None = None
    try:
        planner_candidates = client.get(
            "/api/creative/projects/member-candidates",
            params={"role_code": "creative_planner"},
        )
        writer_candidates = client.get(
            "/api/creative/projects/member-candidates",
            params={"role_code": "screenwriter"},
        )
        assert planner_candidates.status_code == 200, planner_candidates.text
        assert writer_candidates.status_code == 200, writer_candidates.text
        assert planner_user["id"] in {
            item["user_id"] for item in planner_candidates.json()
        }
        assert writer_user["id"] in {
            item["user_id"] for item in writer_candidates.json()
        }
        writer_candidates_by_id = {
            item["user_id"]: item for item in writer_candidates.json()
        }
        assert writer_candidates_by_id[creator.user_id]["is_current_user"] is True
        assert all(
            item["is_current_user"] is (item["user_id"] == creator.user_id)
            for item in writer_candidates.json()
        )

        first = client.post(
            f"/api/creative/topics/{topic['id']}/planning-assignment", json=payload
        )
        repeated = client.post(
            f"/api/creative/topics/{topic['id']}/planning-assignment", json=payload
        )

        assert first.status_code == 200, first.text
        assert repeated.status_code == 200, repeated.text
        project_id = str(first.json()["project"]["id"])
        workflow_project_id = creative.workflow_project_id(project_id)
        cleanup["project_id"] = workflow_project_id
        assert str(repeated.json()["project"]["id"]) == project_id
        first_tasks = first.json()["tasks"]
        repeated_tasks = repeated.json()["tasks"]
        assert len(first_tasks) == 5
        assert [task["id"] for task in repeated_tasks] == [
            task["id"] for task in first_tasks
        ]
        assert {task["task_type"] for task in first_tasks} == {
            "brief",
            "characters",
            "outline",
            "beats",
            "script",
        }
        assert all(task["status"] == "pool" for task in first_tasks)
        assert all(task["assignee_user_id"] is None for task in first_tasks)
        with identity.connect() as connection:
            task_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id = %s",
                (f"project:{workflow_project_id}",),
            ).scalar_one()
        assert task_count == 5
        members = workflow.list_project_members(workflow_project_id)
        assert {
            (item["user_id"], item["project_role"], item["status"])
            for item in members
        } == {
            (creator.user_id, "project_creator", "active"),
            (planner_user["id"], "creative_planner", "active"),
            (writer_user["id"], "screenwriter", "active"),
        }

        creator_topics = client.get("/api/creative/topics", params={"all": "true"})
        assert creator_topics.status_code == 200, creator_topics.text
        creator_topic = next(
            item
            for item in creator_topics.json()["items"]
            if item["id"] == topic["id"]
        )
        assert creator_topic["can_open_project"] is True

        app.dependency_overrides[creative_api_principal] = lambda: outsider
        app.dependency_overrides[current_principal] = lambda: outsider
        outsider_topics = client.get("/api/creative/topics", params={"all": "true"})
        assert outsider_topics.status_code == 200, outsider_topics.text
        outsider_topic = next(
            item
            for item in outsider_topics.json()["items"]
            if item["id"] == topic["id"]
        )
        assert outsider_topic["can_open_project"] is True
        assert permissions.can_read_project(outsider, workflow_project_id) is True
        assert (
            permissions.can_access_assigned_project(outsider, workflow_project_id)
            is False
        )
        outsider_project = client.get(f"/api/creative/projects/{project_id}")
        assert outsider_project.status_code == 200, outsider_project.text
        assert outsider_project.json()["preview_only"] is True
        assert outsider_project.json()["allowed_actions"] == []
        outsider_projects = client.get("/api/creative/projects")
        assert outsider_projects.status_code == 200, outsider_projects.text
        assert project_id in {
            str(item["id"]) for item in outsider_projects.json()["items"]
        }
        outsider_outline = client.post(
            f"/api/creative/projects/{project_id}/workflow-tasks/outline/ensure"
        )
        assert outsider_outline.status_code == 200, outsider_outline.text
        assert outsider_outline.json()["task_type"] == "outline"
        assert outsider_outline.json()["assignee_user_id"] == outsider.user_id
        assert outsider_outline.json()["status"] == "assigned"
        outsider_characters = client.post(
            f"/api/creative/projects/{project_id}/workflow-tasks/characters/ensure"
        )
        assert outsider_characters.status_code == 200, outsider_characters.text
        assert outsider_characters.json()["task_type"] == "characters"
        assert (
            outsider_characters.json()["assignee_user_id"] == outsider.user_id
        )

        script_task = next(
            task for task in first_tasks if task["task_type"] == "script"
        )
        workflow.claim_task(script_task["id"], writer)
        outsider_script = client.post(
            f"/api/creative/projects/{project_id}/workflow-tasks/script/ensure"
        )
        assert outsider_script.status_code == 409, outsider_script.text
        outsider_update = client.patch(
            f"/api/creative/projects/{project_id}", json={"name": "越权修改"}
        )
        assert outsider_update.status_code == 200, outsider_update.text
        assert outsider_update.json()["name"] == "越权修改"

        app.dependency_overrides[creative_api_principal] = lambda: selection_reader
        app.dependency_overrides[current_principal] = lambda: selection_reader
        reader_topics = client.get("/api/creative/topics", params={"all": "true"})
        assert reader_topics.status_code == 200, reader_topics.text
        reader_topic = next(
            item
            for item in reader_topics.json()["items"]
            if item["id"] == topic["id"]
        )
        assert reader_topic["can_open_project"] is False
        assert client.get(f"/api/creative/projects/{project_id}").status_code == 403

        app.dependency_overrides[creative_api_principal] = lambda: creator
        app.dependency_overrides[current_principal] = lambda: creator
        creator_project = client.get(f"/api/creative/projects/{project_id}")
        assert creator_project.status_code == 200, creator_project.text
        assert creator_project.json().get("preview_only") is not True

        creator_html = _render_topic_table(creator)
        outsider_html = _render_topic_table(outsider)
        assert '@click="openCreateProject(topic)"' in creator_html
        assert '@click="openTopicProject(topic)"' in creator_html
        assert '@click="openTopicProject(topic)"' in outsider_html
    finally:
        if cleanup["project_id"] is not None:
            workflow.delete_project_data(str(cleanup["project_id"]))
        for user in (
            creator_user,
            planner_user,
            writer_user,
            outsider_user,
            selection_reader_user,
        ):
            identity.soft_delete_user(user["id"])
        database.dispose()


def test_page_grant_replaces_project_create_permission_for_bound_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    runtime = tmp_path / ".runtime"
    database, identity = _postgres_identity(monkeypatch)
    user = _create_role_user(identity, ("selection_operator",), "只读选题用户")

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    principal = permissions.principal_for_user(user["id"], "selection-session")
    assert principal is not None
    assert "project.create" not in principal.permissions
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.page_access_service = _AllowPageApi()
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime, repository=creative
        )
    )
    client = TestClient(app)
    created_project_ids: list[str] = []
    try:
        candidates = client.get(
            "/api/creative/projects/member-candidates",
            params={"role_code": "screenwriter"},
        )
        created = client.post(
            f"/api/creative/topics/{topic['id']}/planning-assignment",
            json={
                "name": "不应创建",
                "target_platform": "hg",
                "target_episode_count": 20,
                "planner_member_ids": [],
                "writer_member_ids": [],
                "creation_request_id": uuid.uuid4().hex,
            },
        )
        direct_created = client.post(
            "/api/creative/projects",
            json={
                "topic_id": topic["id"],
                "breakdown_id": breakdown["id"],
                "name": "同样不应创建",
                "creation_request_id": uuid.uuid4().hex,
            },
        )
        assert candidates.status_code == 403
        assert created.status_code == 200, created.text
        created_project_ids.append(str(created.json()["project"]["id"]))
        assert direct_created.status_code == 200, direct_created.text
        created_project_ids.append(str(direct_created.json()["id"]))
        html = _render_topic_table(principal)
        assert '@click="openCreateProject(topic)"' in html
        assert '@click="openTopicProject(topic)"' in html
    finally:
        workflow = WorkflowRepository(identity)
        for project_id in dict.fromkeys(created_project_ids):
            workflow.delete_project_data(project_id)
        identity.soft_delete_user(user["id"])
        database.dispose()


def test_member_candidates_require_project_member_manage_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Project creation alone must not expose member candidates."""
    database, identity = _postgres_identity(monkeypatch)
    role = identity.create_role(
        f"project_creator_{uuid.uuid4().hex}", "最小项目创建者", ""
    )
    identity.replace_role_permissions(
        role["id"], [("project.create", "project")]
    )
    user = _create_role_user(identity, (role["code"],), "最小项目创建者")

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    principal = permissions.principal_for_user(user["id"], "creator-session")
    assert principal is not None
    assert principal.permissions == frozenset({"project.create"})

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: principal
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=tmp_path / ".runtime",
            repository=CreativeRepository(tmp_path / ".runtime"),
        )
    )

    response = TestClient(app).get(
        "/api/creative/projects/member-candidates",
        params={"role_code": "screenwriter"},
    )

    assert response.status_code == 403, response.text


def test_project_stage_task_creation_is_concurrency_safe(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Concurrent retries must converge on one pool task for a project stage."""
    database, identity = _postgres_identity(monkeypatch)
    user = _create_role_user(identity, ("screenwriter",), "并发创建编剧")
    permissions = PermissionService(identity, SessionService(identity))
    principal = permissions.principal_for_user(user["id"], "creator-session")
    assert principal is not None
    project_id = f"concurrent-{uuid.uuid4().hex}"
    workflow = WorkflowRepository(identity)

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    workers = 8
    barrier = Barrier(workers)

    def ensure_once() -> str:
        barrier.wait()
        task = WorkflowRepository(identity).ensure_project_task(
            project_id,
            123456789,
            task_type="brief",
            role_code="creative_planner",
            actor=principal,
        )
        return str(task["id"])

    with ThreadPoolExecutor(max_workers=workers) as executor:
        task_ids = list(executor.map(lambda _: ensure_once(), range(workers)))

    assert len(set(task_ids)) == 1
    with identity.connect() as connection:
        count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM workflow_tasks WHERE workflow_id = %s "
            "AND task_type = 'brief' AND project_id = %s",
            (f"project:{project_id}", project_id),
        ).scalar_one()
    assert count == 1


def test_unassigned_writer_claims_only_idle_established_writing_stage(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    project_id = f"writer-claim-{uuid.uuid4().hex}"
    owner_user = _create_role_user(identity, ("project_owner",), "领取项目负责人")
    writer_user = _create_role_user(identity, ("screenwriter",), "领取编剧")
    busy_writer_user = _create_role_user(identity, ("screenwriter",), "在岗编剧")

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "claim-owner-session")
    writer = permissions.principal_for_user(writer_user["id"], "claim-writer-session")
    assert owner is not None and writer is not None
    assert "project.write.established.writing" in writer.permissions
    workflow.add_project_member(project_id, owner.user_id, "project_owner", owner)
    workflow.add_project_member(
        project_id, busy_writer_user["id"], "screenwriter", owner
    )
    outline = workflow.create_task(
        f"{project_id}:outline", "outline", "screenwriter",
        project_id=project_id, actor=owner,
    )
    characters = workflow.create_task(
        f"{project_id}:characters", "characters", "creative_planner",
        project_id=project_id, actor=owner,
    )
    busy_script = workflow.create_task(
        f"{project_id}:script-busy", "script", "screenwriter",
        project_id=project_id, actor=owner,
    )
    workflow.assign_task(busy_script["id"], busy_writer_user["id"], owner)
    spare_script = workflow.create_task(
        f"{project_id}:script-spare", "script", "screenwriter",
        project_id=project_id, actor=owner,
    )

    visible_ids = {task["id"] for task in workflow.list_tasks(writer)}

    assert outline["id"] in visible_ids
    assert characters["id"] not in visible_ids
    assert spare_script["id"] not in visible_ids
    assert workflow.can_claim_task(outline["id"], writer) is True
    assert workflow.can_claim_task(spare_script["id"], writer) is False

    claimed = workflow.claim_task(outline["id"], writer)

    assert claimed["assignee_user_id"] == writer.user_id
    assert (writer.user_id, "screenwriter", "active") in {
        (member["user_id"], member["project_role"], member["status"])
        for member in workflow.list_project_members(project_id)
    }
    claimed_characters = workflow.claim_task(characters["id"], writer)
    assert claimed_characters["assignee_user_id"] == writer.user_id
    with pytest.raises(PermissionError, match="already being handled"):
        workflow.claim_task(spare_script["id"], writer)


def test_only_one_outside_writer_can_claim_parallel_pools_for_same_stage(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    project_id = f"writer-race-{uuid.uuid4().hex}"
    owner_user = _create_role_user(identity, ("project_owner",), "并发领取负责人")
    writer_users = (
        _create_role_user(identity, ("screenwriter",), "并发领取编剧甲"),
        _create_role_user(identity, ("screenwriter",), "并发领取编剧乙"),
    )

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "race-owner-session")
    writers = tuple(
        permissions.principal_for_user(user["id"], f"race-writer-{index}")
        for index, user in enumerate(writer_users)
    )
    assert owner is not None and all(writer is not None for writer in writers)
    workflow.add_project_member(project_id, owner.user_id, "project_owner", owner)
    tasks = (
        workflow.create_task(
            f"{project_id}:beats-a", "beats", "screenwriter",
            project_id=project_id, actor=owner,
        ),
        workflow.create_task(
            f"{project_id}:beats-b", "beats", "screenwriter",
            project_id=project_id, actor=owner,
        ),
    )
    barrier = Barrier(2)

    def claim(index: int) -> dict | None:
        barrier.wait()
        try:
            return workflow.claim_task(tasks[index]["id"], writers[index])
        except (PermissionError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))

    assert len([result for result in results if result is not None]) == 1
    persisted = [workflow.get_task(task["id"]) for task in tasks]
    assert sorted(task["status"] for task in persisted) == ["assigned", "pool"]
    writer_members = {
        member["user_id"]
        for member in workflow.list_project_members(project_id)
        if member["project_role"] == "screenwriter"
    }
    assert len(writer_members) == 1
    assert writer_members <= {user["id"] for user in writer_users}


def test_invalid_team_selection_leaves_no_partial_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """All selected members are validated before either database is mutated."""
    runtime = tmp_path / ".runtime"
    database, identity = _postgres_identity(monkeypatch)
    creator_user = _create_role_user(
        identity, ("screenwriter", "selection_operator"), "创建编剧"
    )
    invalid_user = _create_role_user(identity, ("selection_operator",), "无效成员")

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert creator is not None
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: creator
    app.dependency_overrides[current_principal] = lambda: creator
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime, repository=creative
        )
    )

    response = TestClient(app).post(
        f"/api/creative/topics/{topic['id']}/planning-assignment",
        json={
            "name": "不应留下半成品",
            "planner_member_ids": [],
            "writer_member_ids": [invalid_user["id"]],
            "creation_request_id": uuid.uuid4().hex,
        },
    )

    assert response.status_code == 403, response.text
    assert creative.list_projects() == []


def test_project_creator_can_create_with_empty_member_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    database, identity = _postgres_identity(monkeypatch)
    creator_user = _create_role_user(identity, ("screenwriter",), "空成员创建编剧")

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    assert creator is not None
    runtime = tmp_path / ".runtime"
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: creator
    app.dependency_overrides[current_principal] = lambda: creator
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime, repository=creative
        )
    )

    response = TestClient(app).post(
        f"/api/creative/topics/{topic['id']}/planning-assignment",
        json={
            "name": "空成员项目",
            "planner_member_ids": [],
            "writer_member_ids": [],
            "creation_request_id": uuid.uuid4().hex,
        },
    )

    assert response.status_code == 200, response.text


def test_direct_project_creation_rejects_foreign_task_and_initializes_own_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """A task id is authority, not merely an idempotency key."""
    runtime = tmp_path / ".runtime"
    database, identity = _postgres_identity(monkeypatch)
    workflow = WorkflowRepository(identity)
    creator_user = _create_role_user(identity, ("screenwriter",), "创建编剧")
    victim_user = _create_role_user(identity, ("screenwriter",), "任务持有人")
    admin_user = _create_role_user(identity, ("system_admin",), "测试管理员")
    workflow_ids: list[str] = []

    def cleanup_postgres() -> None:
        # The isolated schema fixture removes all test records.
        database.dispose()

    request.addfinalizer(cleanup_postgres)
    permissions = PermissionService(identity, SessionService(identity))
    creator = permissions.principal_for_user(creator_user["id"], "creator-session")
    admin = permissions.principal_for_user(admin_user["id"], "admin-session")
    assert creator is not None and admin is not None
    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    topic_scope = creative.workflow_topic_id(topic["id"])

    foreign_task = workflow.create_task(
        f"topic:{topic_scope}:foreign-brief-{uuid.uuid4().hex}",
        "brief",
        "screenwriter",
        topic_id=topic["id"],
        actor=admin,
    )
    foreign_task = workflow.assign_task(
        foreign_task["id"], victim_user["id"], admin
    )
    own_task = workflow.create_task(
        f"topic:{topic_scope}:own-brief-{uuid.uuid4().hex}",
        "brief",
        "screenwriter",
        topic_id=topic["id"],
        actor=admin,
    )
    own_task = workflow.assign_task(own_task["id"], creator.user_id, admin)
    wrong_type_task = workflow.create_task(
        f"topic:{topic_scope}:wrong-type-{uuid.uuid4().hex}",
        "outline",
        "screenwriter",
        topic_id=topic["id"],
        actor=admin,
    )
    wrong_type_task = workflow.assign_task(
        wrong_type_task["id"], creator.user_id, admin
    )
    wrong_topic_task = workflow.create_task(
        f"topic:{creative.workflow_topic_id(topic['id'] + 1000)}:"
        f"wrong-topic-{uuid.uuid4().hex}",
        "brief",
        "screenwriter",
        topic_id=topic["id"] + 1000,
        actor=admin,
    )
    wrong_topic_task = workflow.assign_task(
        wrong_topic_task["id"], creator.user_id, admin
    )
    pool_task = workflow.create_task(
        f"topic:{topic_scope}:pool-brief-{uuid.uuid4().hex}",
        "brief",
        "screenwriter",
        topic_id=topic["id"],
        actor=admin,
    )
    pool_task = workflow.assign_task(pool_task["id"], creator.user_id, admin)
    pool_task = workflow.cancel_task(pool_task["id"], admin)
    workflow_ids.extend(
        str(task["workflow_id"])
        for task in (
            foreign_task,
            own_task,
            wrong_type_task,
            wrong_topic_task,
            pool_task,
        )
    )

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.dependency_overrides[creative_api_principal] = lambda: creator
    app.dependency_overrides[current_principal] = lambda: creator
    app.include_router(build_workflow_router(workflow))
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime, repository=creative
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    base_payload = {
        "topic_id": topic["id"],
        "breakdown_id": breakdown["id"],
        "name": "任务创建项目",
        "creation_request_id": uuid.uuid4().hex,
    }

    foreign_response = client.post(
        "/api/creative/projects",
        json={**base_payload, "workflow_task_id": foreign_task["id"]},
    )
    missing_response = client.post(
        "/api/creative/projects",
        json={**base_payload, "workflow_task_id": str(uuid.uuid4())},
    )
    wrong_type_response = client.post(
        "/api/creative/projects",
        json={**base_payload, "workflow_task_id": wrong_type_task["id"]},
    )
    wrong_topic_response = client.post(
        "/api/creative/projects",
        json={**base_payload, "workflow_task_id": wrong_topic_task["id"]},
    )
    pool_response = client.post(
        "/api/creative/projects",
        json={**base_payload, "workflow_task_id": pool_task["id"]},
    )
    own_response = client.post(
        "/api/creative/projects",
        json={
            **base_payload,
            "creation_request_id": uuid.uuid4().hex,
            "workflow_task_id": own_task["id"],
        },
    )

    assert foreign_response.status_code == 403, foreign_response.text
    assert missing_response.status_code == 404, missing_response.text
    assert wrong_type_response.status_code == 409, wrong_type_response.text
    assert wrong_topic_response.status_code == 409, wrong_topic_response.text
    assert pool_response.status_code == 409, pool_response.text
    assert own_response.status_code == 200, own_response.text
    project_id = str(own_response.json()["id"])
    workflow_project_id = creative.workflow_project_id(project_id)
    assert workflow.get_task(own_task["id"])["project_id"] == workflow_project_id
    assert {
        (member["user_id"], member["project_role"])
        for member in workflow.list_project_members(workflow_project_id)
    } >= {
        (creator.user_id, "project_creator"),
        (creator.user_id, "screenwriter"),
    }
    tasks = [
        task
        for task in workflow.list_tasks(creator)
        if str(task.get("project_id") or "") == workflow_project_id
    ]
    assert {task["task_type"] for task in tasks} == {
        "brief",
        "characters",
        "outline",
        "beats",
        "script",
    }

    with identity.transaction() as connection:
        connection.exec_driver_sql(
            "UPDATE project_members SET status = 'inactive' WHERE project_id = %s "
            "AND project_role = 'project_creator'",
            (workflow_project_id,),
        )
        connection.exec_driver_sql(
            "DELETE FROM workflow_instances WHERE id = %s",
            (f"project:{workflow_project_id}",),
        )
    replay = client.post(
        "/api/creative/projects",
        json={
            **base_payload,
            "creation_request_id": uuid.uuid4().hex,
            "workflow_task_id": own_task["id"],
        },
    )

    assert replay.status_code == 200, replay.text
    replay_members = workflow.list_project_members(workflow_project_id)
    assert (creator.user_id, "project_creator", "inactive") in {
        (member["user_id"], member["project_role"], member["status"])
        for member in replay_members
    }
    replay_tasks = [
        task
        for task in workflow.list_tasks(creator)
        if task.get("workflow_id") == f"project:{workflow_project_id}"
    ]
    assert len(replay_tasks) == 5

    workflow.add_project_member(
        workflow_project_id, victim_user["id"], "screenwriter", admin
    )
    reactivated_creator = workflow.add_project_member(
        workflow_project_id, creator.user_id, "project_creator", admin
    )
    created_by = creative.get_project(int(project_id))["created_by"]
    app.dependency_overrides[creative_api_principal] = lambda: admin
    app.dependency_overrides[current_principal] = lambda: admin
    deactivated = client.delete(
        f"/api/workflow/projects/{workflow_project_id}/members/"
        f"{reactivated_creator['id']}"
    )
    assert deactivated.status_code == 204, deactivated.text

    members_after_deactivation = workflow.list_project_members(workflow_project_id)
    assert {
        (member["project_role"], member["status"])
        for member in members_after_deactivation
        if member["user_id"] == creator.user_id
    } == {
        ("project_creator", "inactive"),
        ("screenwriter", "inactive"),
    }
    assert any(
        member["user_id"] == victim_user["id"]
        and member["project_role"] == "screenwriter"
        and member["status"] == "active"
        for member in members_after_deactivation
    )
    assert creative.get_project(int(project_id))["created_by"] == created_by

    app.dependency_overrides[creative_api_principal] = lambda: creator
    app.dependency_overrides[current_principal] = lambda: creator
    assert client.patch(
        f"/api/creative/projects/{project_id}",
        json={"name": "停用后不得修改"},
    ).status_code == 403
    assert creative.get_project(int(project_id))["created_by"] == created_by
