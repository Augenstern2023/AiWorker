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

from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from screenplay_review.api import build_script_review_router


class FakeJobManager:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_job(self, payload: dict):
        self.payloads.append(payload)
        return SimpleNamespace(job_id="review-job-1")


def _principal() -> Principal:
    return Principal(
        user_id="writer-1",
        username="writer-1",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"script.review.generate", "script.review.read"}),
        data_scope="project",
        session_id="session-1",
    )


def _admin_principal() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin-1",
        display_name="管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-admin",
    )


def test_text_review_is_created_as_an_unconfirmable_quick_diagnosis(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    jobs = FakeJobManager()
    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, jobs))
    client = TestClient(app)

    created = client.post(
        "/api/script-reviews",
        json={"source_type": "text", "mode": "single", "text": "主角被赶出家门后反击。"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["review"]["status"] == "queued"
    assert body["job_id"] == "review-job-1"
    assert jobs.payloads[0]["crawler_type"] == "screenplay_review"

    review = repository.get_script_review(body["review"]["id"])
    assert review is not None
    repository.complete_script_review(
        review["id"],
        {
            "ai_verdict": "通过",
            "summary": "结构清楚。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )

    confirmed = client.post(
        f"/api/script-reviews/{review['id']}/confirm",
        json={"final_verdict": "建议修改", "confirmation_note": "补强反击动机"},
    )

    assert confirmed.status_code == 409
    assert "不能人工确认" in confirmed.json()["detail"]


def test_project_review_is_marked_stale_and_confirmation_is_rejected(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    review = repository.create_script_review(
        source_type="project",
        mode="single",
        project_id=99,
        episode_numbers=[1],
        source_fingerprint="original-fingerprint",
        source_payload={"project_id": 99, "episode_numbers": [1]},
        created_by="writer-1",
    )
    repository.complete_script_review(
        review["id"],
        {
            "ai_verdict": "通过",
            "summary": "结构清楚。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    detail = client.get(f"/api/script-reviews/{review['id']}")
    confirmation = client.post(
        f"/api/script-reviews/{review['id']}/confirm",
        json={"final_verdict": "通过"},
    )

    assert detail.status_code == 200
    assert detail.json()["status"] == "stale"
    assert confirmation.status_code == 409


def test_completed_review_history_can_be_deleted_by_its_owner(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint="review-fingerprint",
        source_payload={"episode_numbers": [1]},
        created_by="writer-1",
    )
    repository.complete_script_review(
        review["id"],
        {
            "ai_verdict": "通过",
            "summary": "结构清楚。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    deleted = client.delete(f"/api/script-reviews/{review['id']}")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": review["id"]}
    assert repository.get_script_review(review["id"]) is None
    assert client.get(f"/api/script-reviews/{review['id']}").status_code == 404


def test_running_review_history_cannot_be_deleted(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint="review-fingerprint",
        source_payload={"episode_numbers": [1]},
        created_by="writer-1",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    response = client.delete(f"/api/script-reviews/{review['id']}")

    assert response.status_code == 409
    assert "审核仍在执行" in response.json()["detail"]


def test_cancelled_job_updates_its_review_history_status(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint="review-fingerprint",
        source_payload={"episode_numbers": [1]},
        created_by="writer-1",
    )
    repository.set_script_review_job(review["id"], "cancelled-review-job")
    repository.start_script_review(review["id"])

    class CancelledJobManager(FakeJobManager):
        def get_job(self, job_id: str) -> dict:
            assert job_id == "cancelled-review-job"
            return {"status": "cancelled", "message": "任务已取消"}

    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, CancelledJobManager()))
    client = TestClient(app)

    current = client.get(f"/api/script-reviews/{review['id']}")

    assert current.status_code == 200
    assert current.json()["status"] == "cancelled"
    assert "已取消" in current.json()["error"]
