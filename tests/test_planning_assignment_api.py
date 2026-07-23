# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.service import AuditService
from creative.api import build_creative_router, creative_api_principal
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository


def _topic(repository: CreativeRepository) -> dict:
    return repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "planning-assignment-test",
                "source_path": "data/hg/rank.json",
                "captured_at": "2026-07-23",
                "title": "策划分配测试剧",
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


def test_project_owner_creates_and_assigns_planning_project_idempotently(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system/system.db")
    identity.seed_defaults()
    owner_role = identity.get_role_by_code("project_owner")
    planner_role = identity.get_role_by_code("creative_planner")
    assert owner_role is not None and planner_role is not None
    owner_user = identity.create_user("owner", "项目负责人", "hash")
    planner_user = identity.create_user("planner", "创意策划", "hash")
    identity.replace_user_roles(owner_user["id"], [owner_role["id"]])
    identity.replace_user_roles(planner_user["id"], [planner_role["id"]])
    permissions = PermissionService(identity, SessionService(identity))
    owner = permissions.principal_for_user(owner_user["id"], "owner-session")
    assert owner is not None

    creative = CreativeRepository(runtime / "creative/creative.db")
    topic = _topic(creative)
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"title": topic["title"]}, {}, [])
    creative.lock_breakdown(breakdown["id"])

    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = AuditService(identity)
    app.dependency_overrides[creative_api_principal] = lambda: owner
    app.dependency_overrides[current_principal] = lambda: owner
    app.include_router(build_creative_router(root=tmp_path, runtime_dir=runtime))
    client = TestClient(app)
    payload = {
        "assignee_user_id": planner_user["id"],
        "name": "负责人创建的策划项目",
        "target_platform": "hg",
        "target_episode_count": 20,
        "creation_request_id": "owner-assignment-1",
    }

    first = client.post(
        f"/api/creative/topics/{topic['id']}/planning-assignment", json=payload
    )
    repeated = client.post(
        f"/api/creative/topics/{topic['id']}/planning-assignment", json=payload
    )

    assert first.status_code == 200, first.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["project"]["id"] == first.json()["project"]["id"]
    assert repeated.json()["task"]["id"] == first.json()["task"]["id"]
    assert first.json()["task"]["status"] == "assigned"
    assert first.json()["task"]["assignee_user_id"] == planner_user["id"]
    members = WorkflowRepository(identity).list_project_members(
        str(first.json()["project"]["id"])
    )
    assert {
        (item["user_id"], item["project_role"], item["status"])
        for item in members
    } == {
        (owner.user_id, "project_owner", "active"),
        (planner_user["id"], "creative_planner", "active"),
    }
