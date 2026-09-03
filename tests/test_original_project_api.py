# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import uuid

from alembic import command
from alembic.config import Config
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from creative.api import build_creative_router
from creative.market_reference_service import MarketReferenceService
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import WorkflowRepository


def original_project_payload(creation_request_id: str) -> dict[str, object]:
    return {
        "seed_text": "未来公司按照员工情绪发工资", "name": "情绪公司",
        "genre": "都市奇幻", "target_platform": "hg",
        "target_audience": "18-35 岁职场观众", "target_episode_count": 20,
        "episode_duration_seconds": 120, "cost_level": "low",
        "content_boundaries": ["不美化职场霸凌"], "reference_refs": [],
        "creation_request_id": creation_request_id,
    }


class FailOnce:
    """Fail only the first bootstrap task while retaining real persistence."""

    def __init__(self, wrapped: Callable[..., object]) -> None:
        self.wrapped = wrapped
        self.failed = False

    def __call__(self, *args: object, **kwargs: object) -> object:
        if not self.failed:
            self.failed = True
            raise RuntimeError("bootstrap failed")
        return self.wrapped(*args, **kwargs)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": []})


def _client_for_user(
    app: FastAPI, sessions: SessionService, user_id: str
) -> TestClient:
    raw_token, _ = sessions.create_session(user_id, _request())
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, raw_token)
    return client


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against production")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    identity = IdentityRepository(SystemDatabase(SystemDatabaseSettings(postgres_url)))
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    operator_role = identity.get_role_by_code("selection_operator")
    assert owner_role is not None
    assert operator_role is not None
    owner = identity.create_user(
        f"original-owner-{uuid.uuid4().hex}", "原创创建者", "hash"
    )
    identity.replace_user_roles(owner["id"], [owner_role["id"], operator_role["id"]])
    denied = identity.create_user(
        f"original-denied-{uuid.uuid4().hex}", "无权限用户", "hash"
    )
    identity.replace_user_roles(denied["id"], [operator_role["id"]])
    creator_role = identity.create_role(
        f"original-creator-{uuid.uuid4().hex}",
        "仅原创创建",
        "只允许创建项目，不允许读取市场参考",
    )
    identity.replace_role_permissions(
        creator_role["id"], [("project.create", "task")]
    )
    creator_only = identity.create_user(
        f"original-creator-only-{uuid.uuid4().hex}", "仅创建用户", "hash"
    )
    identity.replace_user_roles(creator_only["id"], [creator_role["id"]])
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    workflow = WorkflowRepository(identity)
    runtime_dir = tmp_path / "runtime"
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(
        build_creative_router(
            root=tmp_path, runtime_dir=runtime_dir, workflow_repository=workflow
        )
    )
    owner_client = _client_for_user(app, sessions, owner["id"])
    owner_principal = permissions.principal_for_user(owner["id"], "original-owner")
    assert owner_principal is not None
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    # The workflow test database is shared while each creative SQLite database
    # is per-test. Avoid reusing a project_id from another test's workflow rows.
    with repository._connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES ('creative_projects', ?)",
            (int(uuid.uuid4().int % 1_000_000_000) + 1_000_000_000,),
        )
    owner_client.repository = repository
    owner_client.workflow = workflow
    owner_client.principal = owner_principal
    owner_client.denied_client = _client_for_user(app, sessions, denied["id"])
    owner_client.creator_only_client = _client_for_user(
        app, sessions, creator_only["id"]
    )

    def cleanup() -> None:
        for project in owner_client.repository.list_projects_for_bootstrap(
            owner_principal.user_id
        ):
            workflow.delete_project_data(str(project["id"]))
        for user_id in (owner["id"], denied["id"], creator_only["id"]):
            if identity.get_user(user_id) is not None:
                identity.soft_delete_user(user_id)
        if identity.get_role(creator_role["id"]) is not None:
            identity.soft_delete_role(creator_role["id"])

    request.addfinalizer(cleanup)
    return owner_client


def create_original_project(client: TestClient, request_id: str) -> dict[str, object]:
    response = client.post(
        "/api/creative/projects/original", json=original_project_payload(request_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_original_project_can_ensure_a_successor_brief_task(
    client: TestClient,
) -> None:
    """Would catch coercing an original project's null topic_id to int."""
    project = create_original_project(client, "ensure-original-brief-successor")

    response = client.post(
        f"/api/creative/projects/{project['id']}/workflow-tasks/brief/ensure"
    )

    assert response.status_code == 200, response.text
    task = response.json()
    assert task["project_id"] == str(project["id"])
    assert task["topic_id"] is None
    assert task["task_type"] == "brief"
    assert task["status"] in {"assigned", "in_progress", "returned"}
    assert task["assignee_user_id"] == client.principal.user_id


def test_original_successor_brief_can_update_version_scoped_project_settings(
    client: TestClient,
) -> None:
    project = create_original_project(client, "successor-brief-settings")
    first = client.repository.create_blueprints(
        project["id"],
        [{"content": {"title": "20 集初版"}}],
        direction_type="original",
    )[0]
    client.repository.lock_brief(first["id"])
    second = client.repository.create_blueprint_version(first["id"])
    current = client.repository.get_project(project["id"])

    response = client.patch(
        f"/api/creative/projects/{project['id']}",
        json={
            "target_episode_count": 30,
            "episode_duration_seconds": 150,
            "draft_brief_id": second["id"],
            "expected_project_revision": current["revision"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["target_episode_count"] == 30
    assert response.json()["episode_duration_seconds"] == 150
    locked_second = client.repository.lock_brief(
        second["id"], expected_revision=response.json()["revision"]
    )
    assert client.repository.get_blueprint(first["id"])["content"][
        "target_episode_count"
    ] == 20
    assert locked_second["content"]["target_episode_count"] == 30
    assert locked_second["content"]["episode_duration_seconds"] == 150


def test_successor_brief_lock_requires_current_project_revision(
    client: TestClient,
) -> None:
    project = create_original_project(client, "successor-lock-revision")
    ensured = client.post(
        f"/api/creative/projects/{project['id']}/workflow-tasks/brief/ensure"
    )
    assert ensured.status_code == 200, ensured.text
    first_task = ensured.json()
    if first_task["status"] in {"assigned", "returned"}:
        first_task = client.workflow.start_task(first_task["id"], client.principal)
    first = client.repository.create_blueprints(
        project["id"],
        [{"content": {"title": "初版"}}],
        workflow_task_id=first_task["id"],
        direction_type="original",
    )[0]
    first_lock = client.post(
        f"/api/creative/blueprints/{first['id']}/lock-brief",
        json={
            "workflow_task_id": first_task["id"],
            "expected_project_revision": client.repository.get_project(project["id"])[
                "revision"
            ],
        },
    )
    assert first_lock.status_code == 200, first_lock.text

    successor_task_response = client.post(
        f"/api/creative/projects/{project['id']}/workflow-tasks/brief/ensure"
    )
    assert successor_task_response.status_code == 200, successor_task_response.text
    successor_task = successor_task_response.json()
    if successor_task["status"] in {"assigned", "returned"}:
        successor_task = client.workflow.start_task(
            successor_task["id"], client.principal
        )
    second = client.repository.create_blueprint_version(
        first["id"], workflow_task_id=successor_task["id"]
    )
    current_revision = client.repository.get_project(project["id"])["revision"]

    missing = client.post(
        f"/api/creative/blueprints/{second['id']}/lock-brief",
        json={"workflow_task_id": successor_task["id"]},
    )
    stale = client.post(
        f"/api/creative/blueprints/{second['id']}/lock-brief",
        json={
            "workflow_task_id": successor_task["id"],
            "expected_project_revision": current_revision - 1,
        },
    )

    assert missing.status_code == 409, missing.text
    assert stale.status_code == 409, stale.text
    assert client.repository.get_project(project["id"])["locked_brief_id"] == first["id"]
    locked = client.post(
        f"/api/creative/blueprints/{second['id']}/lock-brief",
        json={
            "workflow_task_id": successor_task["id"],
            "expected_project_revision": current_revision,
        },
    )
    assert locked.status_code == 200, locked.text
    assert client.repository.get_project(project["id"])["locked_brief_id"] == second["id"]


def test_project_delete_retries_cross_database_cleanup_from_durable_tombstone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_original_project(client, "delete-saga")
    delete_workflow = client.workflow.delete_project_data
    delete_creative = CreativeRepository.delete_project

    def delete_creative_after_workflow_fence(repository, *args, **kwargs):
        assert client.workflow.is_project_deleting(str(project["id"])) is True
        return delete_creative(repository, *args, **kwargs)

    monkeypatch.setattr(
        CreativeRepository, "delete_project", delete_creative_after_workflow_fence
    )
    monkeypatch.setattr(
        client.workflow, "delete_project_data", FailOnce(delete_workflow)
    )

    first = client.delete(f"/api/creative/projects/{project['id']}")

    assert first.status_code == 503
    assert client.repository.get_project(project["id"]) is None
    assert client.repository.get_pending_project_deletion(project["id"])[
        "requested_by"
    ] == client.principal.user_id

    second = client.delete(f"/api/creative/projects/{project['id']}")

    assert second.status_code == 200, second.text
    assert client.repository.get_pending_project_deletion(project["id"]) is None
    assert client.workflow.is_project_deleting(str(project["id"])) is False


def test_project_delete_recovers_from_workflow_marker_after_creative_tombstone_cleared(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash between the two marker clears must remain retryable by its requester."""

    project = create_original_project(client, "delete-marker-only-recovery")
    complete_workflow = client.workflow.complete_project_deletion
    monkeypatch.setattr(
        client.workflow,
        "complete_project_deletion",
        FailOnce(complete_workflow),
    )

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        client.delete(f"/api/creative/projects/{project['id']}")

    assert client.repository.get_project(project["id"]) is None
    assert client.repository.get_pending_project_deletion(project["id"]) is None
    pending = client.workflow.get_pending_project_deletion(str(project["id"]))
    assert pending is not None
    assert pending["requested_by"] == client.principal.user_id
    assert pending["project_snapshot"]["name"] == project["name"]

    denied = client.denied_client.delete(
        f"/api/creative/projects/{project['id']}"
    )

    assert denied.status_code == 403, denied.text
    assert client.workflow.is_project_deleting(str(project["id"])) is True

    recovered = client.delete(f"/api/creative/projects/{project['id']}")

    assert recovered.status_code == 200, recovered.text
    assert recovered.json() == {"id": project["id"], "name": project["name"]}
    assert client.workflow.is_project_deleting(str(project["id"])) is False


def test_project_delete_does_not_adopt_an_active_writer_rollback_marker(
    client: TestClient,
) -> None:
    """A normal DELETE must not take ownership of a pre-delete Writer fence."""

    project = create_original_project(client, "delete-writer-marker-conflict")
    project_id = str(project["id"])
    client.workflow.begin_project_deletion(
        project_id,
        owner_token="writer-rollback:other-operation",
        requested_by="another-writer",
        project_snapshot={"id": project["id"], "name": project["name"]},
    )

    response = client.delete(f"/api/creative/projects/{project['id']}")

    assert response.status_code == 409, response.text
    assert client.repository.get_project(project["id"]) is not None
    pending = client.workflow.get_pending_project_deletion(project_id)
    assert pending is not None
    assert pending["owner_token"] == "writer-rollback:other-operation"
    client.workflow.complete_project_deletion(
        project_id, owner_token="writer-rollback:other-operation"
    )


def test_project_delete_resumes_its_own_marker_before_creative_delete(
    client: TestClient,
) -> None:
    """A crash after the Workflow fence but before Creative must be retryable."""

    project = create_original_project(client, "delete-pre-creative-recovery")
    project_id = str(project["id"])
    owner_token = f"creative-delete:{project_id}:interrupted-request"
    client.workflow.begin_project_deletion(
        project_id,
        owner_token=owner_token,
        requested_by=client.principal.user_id,
        project_snapshot={"id": project["id"], "name": project["name"]},
    )

    recovered = client.delete(f"/api/creative/projects/{project['id']}")

    assert recovered.status_code == 200, recovered.text
    assert recovered.json() == {"id": project["id"], "name": project["name"]}
    assert client.repository.get_project(project["id"]) is None
    assert client.workflow.is_project_deleting(project_id) is False


def test_original_create_api_is_idempotent_and_bootstraps_empty_topic_tasks(
    client: TestClient,
) -> None:
    task_ids_before_create = {
        task["id"] for task in client.workflow.list_tasks(client.principal)
    }
    first = client.post(
        "/api/creative/projects/original", json=original_project_payload("same-request")
    )
    second = client.post(
        "/api/creative/projects/original", json=original_project_payload("same-request")
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    first_project = first.json()
    second_project = second.json()
    assert second_project["id"] == first_project["id"]
    assert first_project["creation_mode"] == "original"
    assert first_project["bootstrap_status"] == "ready"
    tasks = [
        task for task in client.workflow.list_tasks(client.principal)
        if task["id"] not in task_ids_before_create
    ]
    assert {task["task_type"] for task in tasks} == {
        "brief", "characters", "outline", "beats", "script",
    }
    assert all(task["topic_id"] is None for task in tasks)
    members = client.workflow.list_project_members(str(first_project["id"]))
    assert [item["project_role"] for item in members] == ["project_creator"]


def test_concurrent_original_create_reports_one_create_and_one_reuse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race after the service pre-check must not produce two 201 responses."""
    original_create = CreativeRepository.create_original_project
    both_prechecks_completed = threading.Barrier(2)
    original_resolve = MarketReferenceService.resolve

    def resolve_after_both_requests_arrive(
        service: MarketReferenceService,
        references: list[dict[str, object]],
        *,
        can_read: Callable[[str, int], bool],
    ) -> object:
        both_prechecks_completed.wait(timeout=5)
        return original_resolve(service, references, can_read=can_read)

    monkeypatch.setattr(
        MarketReferenceService, "resolve", resolve_after_both_requests_arrive
    )

    def create_after_both_prechecks(
        repository: CreativeRepository, **values: object
    ) -> dict[str, object]:
        both_prechecks_completed.wait(timeout=5)
        return original_create(repository, **values)

    monkeypatch.setattr(
        CreativeRepository, "create_original_project", create_after_both_prechecks
    )

    def post_original() -> object:
        return client.post(
            "/api/creative/projects/original",
            json=original_project_payload("concurrent-request"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: post_original(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 201], [
        response.text for response in responses
    ]
    assert len({response.json()["id"] for response in responses}) == 1


def test_original_create_api_returns_503_then_retries_the_same_failed_project(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client.workflow,
        "ensure_project_task",
        FailOnce(client.workflow.ensure_project_task),
    )

    failed = client.post(
        "/api/creative/projects/original", json=original_project_payload("retry-request")
    )
    hidden = client.get("/api/creative/projects")
    retried = client.post(
        "/api/creative/projects/original", json=original_project_payload("retry-request")
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "原创项目初始化失败，请重试"
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["items"] == []
    assert retried.status_code == 200, retried.text
    assert retried.json()["bootstrap_status"] == "ready"
    assert len(client.repository.list_projects()) == 1


def test_frozen_seed_patch_returns_conflict_and_new_version_uses_it_as_parent(
    client: TestClient,
) -> None:
    project = create_original_project(client, "seed-version-request")
    seeds = client.get(f"/api/creative/projects/{project['id']}/seeds")
    assert seeds.status_code == 200, seeds.text
    seed_v1 = seeds.json()["items"][0]
    client.repository.freeze_seed(seed_v1["id"])

    frozen_update = client.patch(
        f"/api/creative/seeds/{seed_v1['id']}",
        json={"seed_text": "冻结后不应修改"},
    )
    version = client.post(
        f"/api/creative/projects/{project['id']}/seeds/versions",
        json={"seed_text": "未来公司给情绪发工资", "constraints": {}},
    )

    assert frozen_update.status_code == 409
    assert version.status_code == 201, version.text
    assert version.json()["version"] == 2
    assert version.json()["parent_version_id"] == seed_v1["id"]


def test_blank_draft_seed_patch_returns_conflict_without_erasing_the_seed(
    client: TestClient,
) -> None:
    project = create_original_project(client, "blank-seed-request")
    seed = client.repository.get_current_seed(project["id"])
    assert seed is not None

    response = client.patch(
        f"/api/creative/seeds/{seed['id']}", json={"seed_text": "   "}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "原创种子内容不能为空"
    assert client.repository.get_seed(seed["id"])["seed_text"] == seed["seed_text"]


def test_project_list_api_reconciles_current_users_pending_original_project(
    client: TestClient,
) -> None:
    pending = client.repository.create_original_project(
        seed_text="未来公司按照员工情绪发工资",
        name="崩溃遗留项目",
        target_episode_count=20,
        created_by=client.principal.user_id,
        creation_request_id="crash-left-pending",
    )

    listed = client.get("/api/creative/projects")

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [pending["id"]]
    assert client.repository.get_project(pending["id"])["bootstrap_status"] == "ready"


def test_original_routes_enforce_create_and_reference_permissions_and_keep_imitation_create(
    client: TestClient,
) -> None:
    denied = client.denied_client.post(
        "/api/creative/projects/original", json=original_project_payload("denied")
    )
    forbidden_reference = client.post(
        "/api/creative/projects/original",
        json={
            **original_project_payload("missing-reference"),
            "reference_refs": [{"resource_type": "topic", "resource_id": 99999}],
        },
    )
    topic = client.repository.upsert_topics([{
        "platform": "hg", "job_id": "imitation", "source_path": "rank.json",
        "captured_at": "2026-08-19", "title": "仿写来源", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "adopt", "evidence": [],
    }])[0]
    client.repository.update_topic(topic["id"], "adopted")
    breakdown = client.repository.create_breakdown(topic["id"], {"hook": "冲突"}, {}, [])
    with client.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_breakdowns SET status = 'approved' WHERE id = ?",
            (breakdown["id"],),
        )
    imitation = client.post("/api/creative/projects", json={
        "topic_id": topic["id"], "breakdown_id": breakdown["id"], "name": "仿写项目",
        "target_episode_count": 20, "creation_request_id": "imitation-regression",
    })

    assert denied.status_code == 403
    assert forbidden_reference.status_code == 409
    assert "市场参考不存在" in forbidden_reference.json()["detail"]
    assert imitation.status_code == 200, imitation.text
    assert imitation.json()["creation_mode"] == "imitation"


def test_original_create_rejects_reference_without_read_permission(
    client: TestClient,
) -> None:
    topic = client.repository.upsert_topics([{
        "platform": "hg", "job_id": "forbidden-reference", "source_path": "rank.json",
        "captured_at": "2026-08-19", "title": "不可读参考", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "adopt",
        "evidence": [],
    }])[0]
    client.repository.update_topic(topic["id"], "adopted")

    response = client.creator_only_client.post(
        "/api/creative/projects/original",
        json={
            **original_project_payload("forbidden-reference"),
            "reference_refs": [
                {"resource_type": "topic", "resource_id": topic["id"]}
            ],
        },
    )

    assert response.status_code == 403
    assert f"topic/{topic['id']}" in response.json()["detail"]


def test_original_create_rejects_an_idempotency_key_owned_by_an_imitation_project(
    client: TestClient,
) -> None:
    topic = client.repository.upsert_topics([{
        "platform": "hg", "job_id": "request-id-collision", "source_path": "rank.json",
        "captured_at": "2026-08-19", "title": "请求键冲突来源", "description": "",
        "category": "都市", "rank_type": "热播榜", "score": 90,
        "confidence": "high", "score_details": {}, "recommendation": "adopt", "evidence": [],
    }])[0]
    client.repository.update_topic(topic["id"], "adopted")
    breakdown = client.repository.create_breakdown(topic["id"], {"hook": "冲突"}, {}, [])
    with client.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_breakdowns SET status = 'approved' WHERE id = ?",
            (breakdown["id"],),
        )
    imitation = client.post("/api/creative/projects", json={
        "topic_id": topic["id"], "breakdown_id": breakdown["id"], "name": "既有仿写项目",
        "target_episode_count": 20, "creation_request_id": "cross-mode-request-id",
    })
    assert imitation.status_code == 200, imitation.text

    original = client.post(
        "/api/creative/projects/original",
        json=original_project_payload("cross-mode-request-id"),
    )

    assert original.status_code == 409
    assert "其他项目模式" in original.json()["detail"]
