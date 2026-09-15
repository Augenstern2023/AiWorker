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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.repository import CreativeRepository
from creative.prompt_store import PromptStore
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobNotFoundError
from screenplay_review.api import build_script_review_router
from screenplay_review import api as review_api
from screenplay_review.service import source_fingerprint


class FakeJobManager:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def create_job(self, payload: dict):
        self.payloads.append(payload)
        return SimpleNamespace(job_id="review-job-1")


class MembershipPermissionService(PermissionService):
    def __init__(self, active_project_ids: set[str]) -> None:
        self.active_project_ids = active_project_ids

    def is_active_project_member(
        self, principal: Principal, project_id: str
    ) -> bool:
        return project_id in self.active_project_ids

    def can_access_assigned_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        return self.is_active_project_member(principal, project_id)

    def can_access_managed_project(
        self, principal: Principal, project_id: str
    ) -> bool:
        return False


def _reviewable_project(repository: CreativeRepository) -> dict:
    topic = repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "member-review",
                "source_path": "member.json",
                "captured_at": "2026-09-14",
                "title": "成员审核项目",
                "description": "测试",
                "category": "都市",
                "rank_type": "热播榜",
                "score": 80,
                "confidence": "high",
                "score_details": {},
                "recommendation": "值得拆解",
                "evidence": [],
            }
        ]
    )[0]
    topic = repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(
        topic["id"],
        {
            "protagonist": "主角",
            "core_conflict": "主角反击",
            "hooks": ["反转"],
        },
        {},
        [],
    )
    breakdown = repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(
        topic["id"],
        breakdown["id"],
        "成员审核项目",
        1,
        created_by="owner-1",
    )
    brief = repository.create_blueprints(
        project["id"], [{"content": {"title": "方向"}}]
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "主角"}], [{"relation": "对手"}]
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(
        project["id"],
        [{"episode": 1, "summary": "主角反击"}],
        "单集弧线",
    )
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1, "goal": "反击"}]}
    )
    repository.lock_episode_beats(beats["id"])
    repository.create_episode_script(
        project["id"], 1, {"episode_title": "第 1 集", "scenes": []}
    )
    return repository.get_project(project["id"])


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


def test_active_project_member_without_review_permission_can_create_and_read_review(
    tmp_path,
) -> None:
    """Requiring an assigned/managed scope would reject a valid task-scoped member."""
    repository = CreativeRepository(tmp_path / "creative.db")
    project = _reviewable_project(repository)
    jobs = FakeJobManager()
    principal = Principal(
        user_id="member-1",
        username="member-1",
        display_name="项目成员",
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="task",
        session_id="session-member",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: principal
    app.state.identity_permission_service = MembershipPermissionService(
        {repository.workflow_project_id(project["id"])}
    )
    app.include_router(build_script_review_router(repository, jobs))
    client = TestClient(app)

    created = client.post(
        "/api/script-reviews",
        json={
            "source_type": "project",
            "mode": "single",
            "project_id": project["id"],
            "episode_numbers": [1],
        },
    )

    assert created.status_code == 201
    review_id = created.json()["review"]["id"]
    assert client.get(f"/api/script-reviews/{review_id}").status_code == 200
    assert jobs.payloads[0]["extra"]["project_id"] == project["id"]


def test_historical_project_member_identity_can_create_project_review(tmp_path) -> None:
    """Using only the current store ID would miss a proven legacy membership."""
    repository = CreativeRepository(tmp_path / "creative.db")
    project = _reviewable_project(repository)
    principal = Principal(
        user_id="member-1",
        username="member-1",
        display_name="项目成员",
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="task",
        session_id="session-member",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: principal
    app.state.identity_permission_service = MembershipPermissionService(
        {str(project["id"])}
    )
    app.state.workflow_identity_resolver = SimpleNamespace(
        project_id=lambda _project_id: str(project["id"])
    )
    app.include_router(build_script_review_router(repository, FakeJobManager()))

    created = TestClient(app).post(
        "/api/script-reviews",
        json={
            "source_type": "project",
            "mode": "single",
            "project_id": project["id"],
            "episode_numbers": [1],
        },
    )

    assert created.status_code == 201


def test_non_member_with_review_permission_cannot_create_project_review(
    tmp_path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = _reviewable_project(repository)
    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.state.identity_permission_service = MembershipPermissionService(set())
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    created = client.post(
        "/api/script-reviews",
        json={
            "source_type": "project",
            "mode": "single",
            "project_id": project["id"],
            "episode_numbers": [1],
        },
    )

    assert created.status_code == 403
    assert "项目成员" in created.json()["detail"]


def test_text_review_still_requires_review_generate_permission(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    principal = Principal(
        user_id="member-1",
        username="member-1",
        display_name="项目成员",
        role_codes=("screenwriter",),
        permissions=frozenset(),
        data_scope="task",
        session_id="session-member",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    created = client.post(
        "/api/script-reviews",
        json={"source_type": "text", "mode": "single", "text": "主角反击。"},
    )

    assert created.status_code == 403
    assert created.json()["detail"] == "没有执行此操作的权限"


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
    assert "prompt_snapshot" not in body["review"]["source_payload"]

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


@pytest.mark.parametrize(
    ("review_mode", "episode_numbers", "prompt_mode"),
    [("single", [1], "single"), ("multiple", [1, 2], "series")],
)
def test_project_review_maps_mode_and_freezes_selected_template_at_queue_time(
    tmp_path, monkeypatch, review_mode, episode_numbers, prompt_mode
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="身份错位轻喜剧",
        name="审核模板项目",
        target_episode_count=2,
        target_platform="抖音",
        genre="轻喜剧",
        target_audience="女频",
        constraints={},
        reference_refs=[],
        created_by="admin-1",
        creation_request_id="review-prompt-freeze",
        workflow_mode="sequential",
    )
    store = PromptStore(repository.db_path)
    store.enable_project(project["id"], actor_user_id="admin-1")

    def fake_source(_project, mode, episode_numbers):
        result = {
            "project_id": project["id"],
            "project_name": project["name"],
            "mode": mode,
            "formal": True,
            "episode_numbers": episode_numbers,
            "episodes": [
                {"episode_number": number, "script": {"content": {}}}
                for number in episode_numbers
            ],
        }
        result["fingerprint"] = source_fingerprint(result)
        return result

    monkeypatch.setattr(review_api, "build_project_source", fake_source)
    jobs = FakeJobManager()
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(build_script_review_router(repository, jobs))

    response = TestClient(app).post(
        "/api/script-reviews",
        json={
            "source_type": "project",
            "mode": review_mode,
            "project_id": project["id"],
            "episode_numbers": episode_numbers,
            "prompt_template_key": "urban-identity-romcom",
            "prompt_override": {"custom_requirements": "优先检查身份暴露节奏。"},
        },
    )

    assert response.status_code == 201, response.text
    review = repository.get_script_review(response.json()["review"]["id"])
    frozen = review["source_payload"]["prompt_snapshot"]
    assert frozen["stage"] == "review"
    assert frozen["mode"] == prompt_mode
    assert frozen["template_key"] == "urban-identity-romcom"
    assert "身份暴露节奏" in frozen["effective_instruction"]
    assert jobs.payloads[0]["extra"]["prompt_snapshot"] == frozen

    generic = store.get_template_by_key("generic-vertical-short")
    generic_revision = store.get_revision(generic["id"])
    store.set_binding(
        project_id=project["id"],
        stage="review",
        episode_number=0,
        template_id=generic["id"],
        template_revision=generic_revision["revision"],
        override={},
        actor_user_id="admin-1",
    )
    assert repository.get_script_review(review["id"])["source_payload"]["prompt_snapshot"] == frozen


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


def test_formal_review_list_and_confirmation_exclude_project_long_audit(tmp_path) -> None:
    """Removing audit-scope guards would expose project-long audits in the formal workflow."""
    repository = CreativeRepository(tmp_path / "creative.db")
    formal = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint="formal-fingerprint",
        source_payload={"formal": False},
        created_by="writer-1",
    )
    project_audit, _ = repository.create_project_review_once(
        project_id=11,
        episode_numbers=[1, 2],
        source_fingerprint="audit-fingerprint",
        source_payload={"audit_scope": "outline", "request_key": "confirm-1"},
        created_by="writer-1",
        request_key="confirm-1",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    listed = client.get("/api/script-reviews")
    confirmation = client.post(
        f"/api/script-reviews/{project_audit['id']}/confirm",
        json={"final_verdict": "通过"},
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [formal["id"]]
    assert confirmation.status_code == 409
    assert confirmation.json()["detail"] == "完整项目审核不进入人工确认流程"


def test_formal_review_routes_reject_project_long_audit_and_keep_formal_access(tmp_path) -> None:
    """Removing an endpoint audit-scope guard exposes project-long audit data."""
    repository = CreativeRepository(tmp_path / "creative.db")
    formal = repository.create_script_review(
        source_type="project",
        mode="single",
        project_id=11,
        episode_numbers=[1],
        source_fingerprint="formal-fingerprint",
        source_payload={"formal": True},
        created_by="writer-1",
    )
    repository.complete_script_review(
        formal["id"],
        {
            "ai_verdict": "通过",
            "summary": "结构清楚。",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    )
    project_audit, _ = repository.create_project_review_once(
        project_id=11,
        episode_numbers=[1, 2],
        source_fingerprint="audit-fingerprint",
        source_payload={"audit_scope": "outline", "request_key": "route-guard-1"},
        created_by="writer-1",
        request_key="route-guard-1",
    )
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(build_script_review_router(repository, FakeJobManager()))
    client = TestClient(app)

    formal_detail = client.get(f"/api/script-reviews/{formal['id']}")
    formal_export = client.get(f"/api/script-reviews/{formal['id']}/export")
    formal_delete = client.delete(f"/api/script-reviews/{formal['id']}")
    audit_detail = client.get(f"/api/script-reviews/{project_audit['id']}")
    audit_export = client.get(f"/api/script-reviews/{project_audit['id']}/export")
    audit_delete = client.delete(f"/api/script-reviews/{project_audit['id']}")

    assert formal_detail.status_code == 200
    assert formal_export.status_code == 200
    assert formal_delete.status_code == 200
    assert audit_detail.status_code == 409
    assert audit_export.status_code == 409
    assert audit_delete.status_code == 409
    assert repository.get_script_review(project_audit["id"]) is not None


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


@pytest.mark.parametrize("review_status", ["queued", "running"])
def test_missing_job_cancels_active_review_history(tmp_path, review_status: str) -> None:
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
    repository.set_script_review_job(review["id"], "missing-review-job")
    if review_status == "running":
        repository.start_script_review(review["id"])

    class MissingJobManager(FakeJobManager):
        def get_job(self, job_id: str) -> dict:
            assert job_id == "missing-review-job"
            raise JobNotFoundError(job_id)

    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, MissingJobManager()))
    client = TestClient(app)

    current = client.get(f"/api/script-reviews/{review['id']}")

    assert current.status_code == 200
    assert current.json()["status"] == "cancelled"
    assert current.json()["error"] == "审核任务已不存在"
    assert repository.get_script_review(review["id"])["status"] == "cancelled"


def test_delete_refreshes_missing_job_before_checking_active_status(tmp_path) -> None:
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
    repository.set_script_review_job(review["id"], "missing-review-job")

    class MissingJobManager(FakeJobManager):
        def get_job(self, job_id: str) -> dict:
            assert job_id == "missing-review-job"
            raise JobNotFoundError(job_id)

    app = FastAPI()
    app.dependency_overrides[current_principal] = _principal
    app.include_router(build_script_review_router(repository, MissingJobManager()))
    client = TestClient(app)

    deleted = client.delete(f"/api/script-reviews/{review['id']}")

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": review["id"]}
    assert repository.get_script_review(review["id"]) is None
