# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.legacy_sqlite_repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from screenplay_review.api import build_script_review_router


class FakeJobs:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_job(self, payload: dict) -> SimpleNamespace:
        self.payloads.append(payload)
        return SimpleNamespace(job_id="review-method-job")


class FakeLocalRuntime:
    def __init__(self, *, available: bool, installed: bool = True) -> None:
        self.available = available
        self.installed = installed
        self.status_calls = 0
        self.start_calls = 0

    async def status(self) -> dict:
        self.status_calls += 1
        return {
            "available": self.available,
            "installed": self.installed,
            "state": "ready" if self.available else "stopped",
            "message": "本地初审已就绪" if self.available else "首次使用时将自动准备本地初审",
        }

    async def start(self) -> dict:
        self.start_calls += 1
        return {
            "available": False,
            "installed": self.installed,
            "state": "starting",
            "message": "正在准备本地初审",
        }


class FakePromptService:
    def can_access_review_prompt(self, _review: dict, _actor_user_id: str) -> bool:
        return True


def principal() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin",
        display_name="管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="review-method-session",
    )


def client_for(
    tmp_path, runtime: FakeLocalRuntime
) -> tuple[TestClient, CreativeRepository, FakeJobs]:
    repository = CreativeRepository(tmp_path / "review-method.db")
    jobs = FakeJobs()
    app = FastAPI()
    app.dependency_overrides[current_principal] = principal
    app.include_router(
        build_script_review_router(
            repository,
            jobs,
            local_runtime_manager=runtime,
            project_prompt_service=FakePromptService(),
        )
    )
    return TestClient(app), repository, jobs


def test_review_capabilities_and_start_use_writer_facing_states(tmp_path) -> None:
    runtime = FakeLocalRuntime(available=False)
    client, _repository, _jobs = client_for(tmp_path, runtime)

    capabilities = client.get("/api/script-reviews/capabilities")
    started = client.post("/api/script-reviews/local-runtime/start")

    assert capabilities.status_code == 200
    assert capabilities.json()["default_method"] == "complete"
    assert capabilities.json()["methods"]["complete"]["available"] is True
    assert capabilities.json()["methods"]["local"]["state"] == "stopped"
    assert started.status_code == 202
    assert started.json()["state"] == "starting"
    assert runtime.start_calls == 1


def test_local_runtime_start_requires_review_permission(tmp_path) -> None:
    runtime = FakeLocalRuntime(available=False)
    client, _repository, _jobs = client_for(tmp_path, runtime)
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id="member-1",
        username="member",
        display_name="成员",
        role_codes=(),
        permissions=frozenset(),
        data_scope="self",
        session_id="review-method-session",
    )

    response = client.post("/api/script-reviews/local-runtime/start")

    assert response.status_code == 403
    assert runtime.start_calls == 0


def test_local_review_method_is_frozen_into_review_and_job(tmp_path) -> None:
    runtime = FakeLocalRuntime(available=True)
    client, repository, jobs = client_for(tmp_path, runtime)

    response = client.post(
        "/api/script-reviews",
        json={
            "source_type": "text",
            "mode": "single",
            "review_method": "local",
            "text": "主角发现账本后逃离仓库。",
        },
    )

    assert response.status_code == 201, response.text
    review = repository.get_script_review(response.json()["review"]["id"])
    assert review["source_payload"]["review_method"] == "local"
    assert jobs.payloads[0]["extra"]["review_method"] == "local"


def test_local_review_is_not_silently_queued_while_runtime_is_unavailable(
    tmp_path,
) -> None:
    runtime = FakeLocalRuntime(available=False)
    client, repository, jobs = client_for(tmp_path, runtime)

    response = client.post(
        "/api/script-reviews",
        json={
            "source_type": "text",
            "mode": "single",
            "review_method": "local",
            "text": "主角发现账本。",
        },
    )

    assert response.status_code == 503
    assert "改用完整审核" in response.json()["detail"]
    assert repository.list_script_reviews() == []
    assert jobs.payloads == []


def test_complete_review_remains_default_without_local_runtime_check(tmp_path) -> None:
    runtime = FakeLocalRuntime(available=False)
    client, repository, jobs = client_for(tmp_path, runtime)

    response = client.post(
        "/api/script-reviews",
        json={"source_type": "text", "mode": "single", "text": "主角反击。"},
    )

    assert response.status_code == 201, response.text
    review = repository.get_script_review(response.json()["review"]["id"])
    assert review["source_payload"]["review_method"] == "complete"
    assert jobs.payloads[0]["extra"]["review_method"] == "complete"
    assert runtime.status_calls == 0
