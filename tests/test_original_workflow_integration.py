# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
import uuid

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import make_url

from core.job import CrawlJob
from creative import phase4_worker
from creative.api import build_creative_router
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository


class FixedProvider:
    provider_name = "fixed"

    async def complete_json(
        self, _messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {"directions": _directions()}


def _direction(
    title: str, protagonist: str, background: str, conflict: str
) -> dict[str, Any]:
    return {
        "title": title,
        "logline": f"{protagonist}在{background}解决{conflict}",
        "theme": "人在制度中保持选择",
        "protagonist": protagonist,
        "protagonist_desire": "获得自主权",
        "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作",
        "main_opposition": "僵化制度",
        "core_conflict": conflict,
        "main_goal": "改变制度",
        "background": background,
        "worldview": "情绪可以被量化但不能被定义",
        "story_engine": f"围绕{conflict}推进",
        "first_three_hooks": ["工资因愤怒清零", "同事出售快乐", "AI 拒绝考核"],
        "highlights": ["规则反杀", "身份反差"],
        "differentiators": ["情绪劳动具象化"],
        "audience": "18-35 岁职场观众",
        "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low",
        "originality_statement": "人物和冲突均由种子独立发展",
        "risks": ["设定解释成本"],
        "assumptions": ["观众理解情绪积分"],
        "open_questions": ["公司为何采用该制度"],
    }


def _directions() -> list[dict[str, Any]]:
    return [
        _direction("情绪公司", "林岚", "未来公司", "情绪考核"),
        _direction("潮汐公约", "周屿", "海上社区", "资源配给"),
        _direction("满分之外", "许真", "虚拟学校", "人格评分"),
    ]


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "headers": []})


@pytest.fixture
def original_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> TestClient:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against production")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity = IdentityRepository(database)
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    operator_role = identity.get_role_by_code("selection_operator")
    assert owner_role is not None
    assert operator_role is not None
    owner = identity.create_user(
        f"original-flow-{uuid.uuid4().hex}", "原创全链路创建者", "hash"
    )
    identity.replace_user_roles(
        owner["id"], [owner_role["id"], operator_role["id"]]
    )
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    workflow = WorkflowRepository(identity)
    runtime_dir = tmp_path / "runtime"
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    with repository._connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES "
            "('creative_projects', ?)",
            (int(uuid.uuid4().int % 1_000_000_000) + 1_000_000_000,),
        )
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.include_router(build_workflow_router(workflow))
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=runtime_dir,
            workflow_repository=workflow,
        )
    )
    raw_token, _ = sessions.create_session(owner["id"], _request())
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, raw_token)
    principal = permissions.principal_for_user(owner["id"], "original-flow")
    assert principal is not None
    client.repository = repository
    client.workflow = workflow
    client.principal = principal

    def cleanup() -> None:
        for project in repository.list_projects_for_bootstrap(principal.user_id):
            workflow.delete_project_data(str(project["id"]))
        if identity.get_user(owner["id"]) is not None:
            identity.soft_delete_user(owner["id"])
        database.dispose()

    request.addfinalizer(cleanup)
    return client


def _create_ready_original_project(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/creative/projects/original",
        json={
            "seed_text": "未来公司按照员工情绪发工资",
            "name": "情绪公司",
            "genre": "都市奇幻",
            "target_platform": "hg",
            "target_audience": "18-35 岁职场观众",
            "target_episode_count": 1,
            "episode_duration_seconds": 120,
            "cost_level": "low",
            "content_boundaries": ["不美化职场霸凌"],
            "reference_refs": [],
            "creation_request_id": f"original-flow-{uuid.uuid4().hex}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run_original_blueprint_worker(
    client: TestClient,
    project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    seed = client.repository.get_current_seed(project["id"])
    assert seed is not None
    frozen = client.repository.freeze_seed(seed["id"])
    monkeypatch.setattr(phase4_worker, "create_provider", FixedProvider)
    job = CrawlJob(
        job_id=f"original-flow-{uuid.uuid4().hex}",
        platform="creative",
        crawler_type="phase4_generate",
        extra={
            "action": "blueprints",
            "project_id": project["id"],
            "count": 3,
            "db_path": str(client.repository.db_path),
            "seed_id": frozen["id"],
            "market_context": [],
            "blocked_terms": [],
            "reference_snapshot": [],
        },
    )
    asyncio.run(phase4_worker.run(job))
    directions = client.repository.get_project(project["id"])["blueprints"]
    assert len(directions) == 3
    return directions[0]


def _lock_brief_via_workflow(
    client: TestClient,
    project: dict[str, Any],
    direction: dict[str, Any],
) -> None:
    task = next(
        item
        for item in client.workflow.list_tasks(client.principal)
        if item["project_id"] == str(project["id"])
        and item["task_type"] == "brief"
    )
    claimed = client.post(f"/api/workflow/tasks/{task['id']}/claim")
    assert claimed.status_code == 200, claimed.text
    started = client.post(f"/api/workflow/tasks/{task['id']}/start")
    assert started.status_code == 200, started.text
    locked = client.post(
        f"/api/creative/blueprints/{direction['id']}/lock-brief",
        json={"workflow_task_id": task["id"]},
    )
    assert locked.status_code == 200, locked.text


def test_original_project_continues_through_existing_script_workflow(
    original_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _create_ready_original_project(original_client)
    direction = _run_original_blueprint_worker(
        original_client, project, monkeypatch
    )
    _lock_brief_via_workflow(original_client, project, direction)

    characters = original_client.repository.create_character_batch(
        project["id"],
        [{"name": "林岚"}],
        [{"from": "林岚", "to": "AI", "relation": "互相训练"}],
    )
    original_client.repository.lock_character_batch(
        project["id"], characters["batch_id"]
    )
    outline = original_client.repository.create_outline_batch(
        project["id"],
        [{"episode": 1, "summary": "第一次拒绝加班"}],
        "学会拒绝",
    )
    original_client.repository.lock_outline_batch(
        project["id"], outline["batch_id"]
    )
    beats = original_client.repository.create_episode_beats(
        project["id"],
        1,
        {"beats": [{"goal": "拒绝加班", "hook": "AI 主动辞职"}]},
    )
    original_client.repository.lock_episode_beats(beats["id"])
    script = original_client.repository.create_episode_script(
        project["id"],
        1,
        {"scenes": [{"scene": 1, "action": "AI 关闭工位"}]},
    )

    assert script["upstream_refs"]["brief_id"] == direction["id"]
    assert original_client.repository.get_project(project["id"])["topic_id"] is None
