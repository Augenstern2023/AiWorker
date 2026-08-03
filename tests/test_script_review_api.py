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


def test_text_review_is_created_and_exposes_pending_confirmation(tmp_path) -> None:
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

    assert confirmed.status_code == 200
    assert confirmed.json()["final_verdict"] == "建议修改"
    assert confirmed.json()["confirmed_by"] == "writer-1"


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
