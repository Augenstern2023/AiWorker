# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.dependencies import current_principal
from identity.schema import Principal
from screenplay_review import api as review_api
from screenplay_review.api import build_script_review_router
from screenplay_review.service import build_text_source, source_fingerprint


class _PromptService:
    def __init__(self, _repository) -> None:
        pass

    def can_access_review_prompt(self, _review, _actor_user_id) -> bool:
        return True

    def resolve_generation_snapshot(self, **_kwargs):
        return None


class _Repository:
    def __init__(self, parent: dict) -> None:
        self.reviews = {int(parent["id"]): deepcopy(parent)}
        self.next_id = int(parent["id"]) + 1
        self.current_source = deepcopy(parent["source_payload"])

    def get_script_review(self, review_id: int):
        review = self.reviews.get(int(review_id))
        return deepcopy(review) if review is not None else None

    def list_script_reviews(self, *, created_by=None, project_ids=None):
        values = list(self.reviews.values())
        if created_by is not None:
            values = [item for item in values if item["created_by"] == created_by]
        if project_ids is not None:
            values = [item for item in values if item.get("project_id") in project_ids]
        return [deepcopy(item) for item in values]

    def create_script_review(self, **values):
        review_id = self.next_id
        self.next_id += 1
        review = {
            "id": review_id,
            "status": "queued",
            "job_id": None,
            "report": {},
            "ai_verdict": None,
            **deepcopy(values),
        }
        self.reviews[review_id] = review
        return deepcopy(review)

    def set_script_review_job(self, review_id: int, job_id: str):
        self.reviews[int(review_id)]["job_id"] = str(job_id)
        return deepcopy(self.reviews[int(review_id)])

    def fail_script_review(self, review_id: int, error: str):
        self.reviews[int(review_id)].update({"status": "failed", "error": error})
        return deepcopy(self.reviews[int(review_id)])

    def get_project(self, project_id: int):
        return {"id": int(project_id)}

    def workflow_project_id(self, project_id: int) -> str:
        return f"creative-project:{project_id}"


class _Jobs:
    def __init__(self) -> None:
        self.payloads = []

    def create_job(self, payload):
        self.payloads.append(deepcopy(payload))
        return SimpleNamespace(job_id=f"review-job-{len(self.payloads)}")


def _admin() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin-1",
        display_name="管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-1",
    )


def _v2_report() -> dict:
    return {
        "schema_version": "screenplay-review-report-v2",
        "review_version": "review-v001",
        "quality_status": "needs_revision",
        "overall_score": 68,
        "hard_gate": {"passed": True, "failed_items": [], "insufficient_items": []},
        "dimensions": [
            {
                "id": "conflict_strength",
                "selected_option": "needs_revision",
                "scope_refs": ["ep-0001"],
                "evidence": [{"episode_id": "ep-0001", "scene_id": "scene-001"}],
            }
        ],
        "blocking_issues": [
            {
                "dimension_id": "conflict_strength",
                "selected_option": "needs_revision",
            }
        ],
        "revision_plan": [{"priority": "P1", "action": "提高冲突代价"}],
        "engine": "semif",
        "model": {"name": "fake", "revision": "r1"},
    }


def _parent(*, source_type: str = "text") -> dict:
    source = build_text_source("主角推门而入。")
    project_id = None
    if source_type == "project":
        project_id = 7
        source.update(
            {
                "project_id": project_id,
                "project_name": "测试项目",
                "formal": True,
            }
        )
    source["execution_snapshot"] = {
        "engine": "semif",
        "model_profile": "approved-profile",
        "rule_package": "short-drama-semif-v1",
    }
    return {
        "id": 1,
        "source_type": source_type,
        "mode": "single",
        "project_id": project_id,
        "episode_numbers": [1],
        "source_fingerprint": source["fingerprint"],
        "source_payload": source,
        "created_by": "admin-1",
        "status": "pending_confirmation",
        "job_id": "parent-job",
        "report": _v2_report(),
        "ai_verdict": "建议修改",
    }


def _client(monkeypatch, parent: dict):
    repository = _Repository(parent)
    jobs = _Jobs()
    monkeypatch.setattr(review_api, "ProjectPromptService", _PromptService)
    monkeypatch.setattr(review_api.config, "SCREENPLAY_REVIEW_ENGINE", "legacy")
    monkeypatch.setattr(review_api.config, "SEMIF_MODEL_PROFILE", "local-default")

    async def healthy_semif() -> None:
        return None

    monkeypatch.setattr(review_api, "_require_semif_available", healthy_semif)
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin
    app.include_router(build_script_review_router(repository, jobs))
    return TestClient(app), repository, jobs


def test_v2_detail_projects_stable_report_fields(monkeypatch) -> None:
    client, _repository, _jobs = _client(monkeypatch, _parent())

    response = client.get("/api/script-reviews/1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["quality_status"] == "needs_revision"
    assert payload["overall_score"] == 68
    assert payload["dimensions"][0]["evidence"][0]["scene_id"] == "scene-001"
    assert payload["report"]["schema_version"] == "screenplay-review-report-v2"


def test_rerun_is_idempotent_and_rejects_scope_reuse(monkeypatch) -> None:
    client, repository, jobs = _client(monkeypatch, _parent())
    body = {"scope": "failed_dimensions_only", "request_key": "rerun-key-0001"}

    first = client.post("/api/script-reviews/1/reruns", json=body)
    repeated = client.post("/api/script-reviews/1/reruns", json=body)
    conflicting = client.post(
        "/api/script-reviews/1/reruns",
        json={"scope": "full", "request_key": body["request_key"]},
    )

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["review"]["id"] == first.json()["review"]["id"]
    assert conflicting.status_code == 409
    assert len(jobs.payloads) == 1
    child = repository.get_script_review(first.json()["review"]["id"])
    execution = child["source_payload"]["execution_snapshot"]
    assert execution["parent_review_id"] == 1
    assert execution["review_version"] == "review-v002"
    assert execution["model_profile"] == "approved-profile"
    assert source_fingerprint(child["source_payload"]) == child["source_fingerprint"]


def test_changed_project_snapshot_blocks_partial_but_allows_full_rerun(
    monkeypatch,
) -> None:
    client, repository, jobs = _client(monkeypatch, _parent(source_type="project"))
    changed = deepcopy(repository.current_source)
    changed["fingerprint"] = "changed-fingerprint"
    repository.current_source = changed
    monkeypatch.setattr(
        review_api,
        "build_project_source",
        lambda _project, _mode, _numbers: deepcopy(repository.current_source),
    )

    partial = client.post(
        "/api/script-reviews/1/reruns",
        json={"scope": "affected_episodes", "request_key": "rerun-key-0002"},
    )
    full = client.post(
        "/api/script-reviews/1/reruns",
        json={"scope": "full", "request_key": "rerun-key-0003"},
    )

    assert partial.status_code == 409
    assert full.status_code == 201
    assert full.json()["review"]["source_fingerprint"] == "changed-fingerprint"
    assert len(jobs.payloads) == 1


def test_new_request_defaults_to_legacy_and_rejects_blank_model_profile(
    monkeypatch,
) -> None:
    client, repository, jobs = _client(monkeypatch, _parent())

    created = client.post(
        "/api/script-reviews",
        json={"source_type": "text", "mode": "single", "text": "测试剧本"},
    )
    invalid = client.post(
        "/api/script-reviews",
        json={
            "source_type": "text",
            "mode": "single",
            "text": "测试剧本",
            "engine": "semif",
            "model_profile": "   ",
        },
    )

    assert created.status_code == 201
    assert invalid.status_code == 422
    child = repository.get_script_review(created.json()["review"]["id"])
    assert child["source_payload"]["execution_snapshot"]["engine"] == "legacy"
    assert jobs.payloads[0]["extra"]["review_engine"] == "legacy"


def test_unavailable_semif_is_rejected_before_a_review_is_created(monkeypatch) -> None:
    client, repository, jobs = _client(monkeypatch, _parent())

    async def unavailable_semif() -> None:
        from fastapi import HTTPException

        raise HTTPException(503, "SemIf 评审服务当前不可用")

    monkeypatch.setattr(review_api, "_require_semif_available", unavailable_semif)
    response = client.post(
        "/api/script-reviews",
        json={
            "source_type": "text",
            "mode": "single",
            "text": "测试剧本",
            "engine": "semif",
            "model_profile": "fake",
        },
    )

    assert response.status_code == 503
    assert len(repository.reviews) == 1
    assert jobs.payloads == []
