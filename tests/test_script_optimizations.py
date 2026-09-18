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

from creative.repository import CreativeRepository
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from screenplay_optimization.api import build_script_optimization_router
from screenplay_optimization.service import content_fingerprint, validate_optimization_request
from screenplay_optimization import worker as optimization_worker
from screenplay_review.service import build_project_source
from core.job import CrawlJob


def _project_with_review(repository: CreativeRepository, episode_count: int = 1) -> tuple[dict, dict, list[dict]]:
    topic = repository.upsert_topics([{"platform": "hg", "job_id": "job-1", "source_path": "rank.json", "captured_at": "2026-08-03", "title": "优化剧", "score": 90, "confidence": "high", "score_details": {}, "recommendation": "adopt", "evidence": []}])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "优化剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "优化项目", episode_count)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}, "risk_score": 1, "risk_level": "low"}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": number} for number in range(1, episode_count + 1)], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    scripts = []
    for number in range(1, episode_count + 1):
        beats = repository.create_episode_beats(project["id"], number, {"beats": [{"order": 1}]})
        repository.lock_episode_beats(beats["id"])
        scripts.append(repository.create_episode_script(project["id"], number, {"episode_title": f"原稿 {number}", "scenes": []}))
    project = repository.get_project(project["id"])
    assert project is not None
    mode = "single" if episode_count == 1 else "multiple"
    source = build_project_source(project, mode, list(range(1, episode_count + 1)))
    review = repository.create_script_review(source_type="project", mode=mode, project_id=project["id"], episode_numbers=list(range(1, episode_count + 1)), source_fingerprint=source["fingerprint"], source_payload=source, created_by="admin-1")
    repository.complete_script_review(review["id"], {"ai_verdict": "建议修改", "summary": "", "core_findings": [{"title": "节奏"}], "episode_reviews": [], "cross_episode_findings": []})
    review = repository.confirm_script_review(review["id"], "建议修改", "", "admin-1", source["fingerprint"])
    return project, review, scripts


def _admin_client(repository: CreativeRepository) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id="admin-1", username="admin", display_name="管理员", role_codes=("system_admin",),
        permissions=frozenset({"*"}), data_scope="all", session_id="session-admin",
    )
    app.include_router(build_script_optimization_router(repository, object()))
    return TestClient(app)


def test_optimization_task_keeps_generated_drafts_outside_project_versions(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    task = repository.create_script_optimization(
        review_id=12,
        project_id=7,
        mode="direct",
        source_fingerprint="source-fingerprint",
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1],
        created_by="writer-1",
    )

    completed = repository.complete_script_optimization(
        task["id"],
        [
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": 101,
                "source_fingerprint": "episode-fingerprint",
                "content": {"episode_title": "优化稿", "scenes": []},
            }
        ],
    )

    assert completed["status"] == "completed"
    assert completed["drafts"][0]["kind"] == "script"
    assert completed["drafts"][0]["status"] == "ready"
    assert repository.get_project(7) is None


def test_structure_optimization_links_beats_and_script_drafts(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    task = repository.create_script_optimization(
        review_id=12,
        project_id=7,
        mode="structure",
        source_fingerprint="source-fingerprint",
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1],
        created_by="writer-1",
    )

    completed = repository.complete_script_optimization(
        task["id"],
        [
            {
                "episode_number": 1,
                "kind": "beats",
                "source_episode_id": 51,
                "source_fingerprint": "beats-source",
                "content": {"beats": []},
            },
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": None,
                "source_fingerprint": "optimized-beats",
                "content": {"episode_title": "优化稿", "scenes": []},
            },
        ],
    )

    beats, script = completed["drafts"]
    assert beats["peer_draft_id"] == script["id"]
    assert script["peer_draft_id"] == beats["id"]


def test_optimization_request_expands_cross_episode_findings_to_review_scope() -> None:
    review = {
        "source_type": "project",
        "source_payload": {"formal": True},
        "status": "confirmed",
        "final_verdict": "建议修改",
        "episode_numbers": [2, 3],
        "report": {
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [{"title": "承接不足"}],
        },
    }

    result = validate_optimization_request(
        review,
        "direct",
        [{"ref": "cross:0", "episode_numbers": []}],
    )

    assert result["target_episode_numbers"] == [2, 3]


def test_optimization_request_rejects_a_finding_that_requires_outline_revision() -> None:
    review = {
        "source_type": "project",
        "source_payload": {"formal": True},
        "status": "confirmed",
        "final_verdict": "建议修改",
        "episode_numbers": [1],
        "report": {
            "core_findings": [
                {"title": "核心事件偏离大纲", "change_route": "outline_revision_required", "episode_number": 1}
            ],
            "episode_reviews": [],
            "cross_episode_findings": [],
        },
    }

    with pytest.raises(ValueError, match="需要先修订并锁定分集大纲"):
        validate_optimization_request(review, "direct", [{"ref": "core:0"}])


def test_text_review_cannot_create_an_optimization_task(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    review = repository.create_script_review(
        source_type="text",
        mode="single",
        project_id=None,
        episode_numbers=[1],
        source_fingerprint="text-source",
        source_payload={"episodes": []},
        created_by="writer-1",
    )
    repository.complete_script_review(
        review["id"],
        {"ai_verdict": "建议修改", "summary": "", "core_findings": [{}], "episode_reviews": [], "cross_episode_findings": []},
    )
    repository.confirm_script_review(review["id"], "建议修改", "", "writer-1", "text-source")
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id="writer-1", username="writer", display_name="编剧", role_codes=("screenwriter",),
        permissions=frozenset({"script.optimize.generate", "script.optimize.read", "script.optimize.accept"}),
        data_scope="project", session_id="session-1",
    )
    app.include_router(build_script_optimization_router(repository, object()))

    response = TestClient(app).post(
        f"/api/script-reviews/{review['id']}/optimizations",
        json={"mode": "direct", "findings": [{"ref": "core:0"}]},
    )

    assert response.status_code == 409


def test_accept_direct_optimization_creates_editable_script_without_status_change(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{"platform": "hg", "job_id": "job-1", "source_path": "rank.json", "captured_at": "2026-08-03", "title": "优化剧", "score": 90, "confidence": "high", "score_details": {}, "recommendation": "adopt", "evidence": []}])[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"title": "优化剧"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "优化项目", 1)
    brief = repository.create_blueprints(project["id"], [{"content": {"title": "方向"}, "risk_score": 1, "risk_level": "low"}])[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(project["id"], [{"name": "主角"}], [])
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outlines = repository.create_outline_batch(project["id"], [{"episode": 1}], "弧线")
    repository.lock_outline_batch(project["id"], outlines["batch_id"])
    beats = repository.create_episode_beats(project["id"], 1, {"beats": [{"order": 1}]})
    repository.lock_episode_beats(beats["id"])
    script = repository.create_episode_script(project["id"], 1, {"episode_title": "原稿", "scenes": []})
    project = repository.get_project(project["id"])
    assert project is not None
    source = build_project_source(project, "single", [1])
    review = repository.create_script_review(source_type="project", mode="single", project_id=project["id"], episode_numbers=[1], source_fingerprint=source["fingerprint"], source_payload=source, created_by="admin-1")
    repository.complete_script_review(review["id"], {"ai_verdict": "建议修改", "summary": "", "core_findings": [{"title": "节奏"}], "episode_reviews": [], "cross_episode_findings": []})
    repository.confirm_script_review(review["id"], "建议修改", "", "admin-1", source["fingerprint"])
    optimization = repository.create_script_optimization(review_id=review["id"], project_id=project["id"], mode="direct", source_fingerprint=source["fingerprint"], selected_findings=[{"ref": "core:0", "episode_numbers": [1]}], target_episode_numbers=[1], created_by="admin-1")
    repository.complete_script_optimization(optimization["id"], [{"episode_number": 1, "kind": "script", "source_episode_id": script["id"], "source_fingerprint": content_fingerprint(script["content"]), "content": {"episode_title": "优化稿", "scenes": []}}])
    before_status = repository.get_project(project["id"])["status"]
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: Principal(user_id="admin-1", username="admin", display_name="管理员", role_codes=("system_admin",), permissions=frozenset({"*"}), data_scope="all", session_id="session-admin")
    app.include_router(build_script_optimization_router(repository, object()))
    draft_id = repository.get_script_optimization(optimization["id"])["drafts"][0]["id"]

    response = TestClient(app).post(f"/api/script-optimization-drafts/{draft_id}/accept")

    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert repository.get_episode(accepted["accepted_episode_id"])["content"]["episode_title"] == "优化稿"
    assert repository.get_project(project["id"])["status"] == before_status


def test_structure_script_draft_requires_accepted_and_locked_paired_beats(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    source_beats = max(
        (item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "beats"),
        key=lambda item: item["version"],
    )
    optimized_beats = {"beats": [{"order": 1, "goal": "扩大反击"}]}
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [
            {"episode_number": 1, "kind": "beats", "source_episode_id": source_beats["id"], "source_fingerprint": content_fingerprint(source_beats["content"]), "content": optimized_beats},
            {"episode_number": 1, "kind": "script", "source_episode_id": None, "source_fingerprint": content_fingerprint(optimized_beats), "content": {"episode_title": "结构优化稿", "scenes": []}},
        ],
    )
    beats_draft, script_draft = completed["drafts"]
    client = _admin_client(repository)

    assert client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept").status_code == 409
    accepted_beats = client.post(f"/api/script-optimization-drafts/{beats_draft['id']}/accept")
    assert accepted_beats.status_code == 200, accepted_beats.text
    assert client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept").status_code == 409

    repository.lock_episode_beats(accepted_beats.json()["accepted_episode_id"])
    accepted_script = client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept")
    assert accepted_script.status_code == 200, accepted_script.text
    assert accepted_script.json()["status"] == "accepted"


def test_structure_script_draft_is_rejected_when_accepted_beats_changes(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    source_beats = max(
        (item for item in repository.get_project(project["id"])["episodes"] if item["stage"] == "beats"),
        key=lambda item: item["version"],
    )
    optimized_beats = {"beats": [{"order": 1, "goal": "扩大反击"}]}
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [
            {"episode_number": 1, "kind": "beats", "source_episode_id": source_beats["id"], "source_fingerprint": content_fingerprint(source_beats["content"]), "content": optimized_beats},
            {"episode_number": 1, "kind": "script", "source_episode_id": None, "source_fingerprint": content_fingerprint(optimized_beats), "content": {"episode_title": "结构优化稿", "scenes": []}},
        ],
    )
    beats_draft, script_draft = completed["drafts"]
    client = _admin_client(repository)
    accepted_beats = client.post(f"/api/script-optimization-drafts/{beats_draft['id']}/accept")
    assert accepted_beats.status_code == 200, accepted_beats.text
    repository.update_episode(accepted_beats.json()["accepted_episode_id"], {"beats": [{"order": 1, "goal": "改过的 Beats"}]})
    repository.lock_episode_beats(accepted_beats.json()["accepted_episode_id"])

    response = client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept")

    assert response.status_code == 409
    assert "已变化" in response.json()["detail"]


def test_direct_drafts_for_unchanged_episodes_remain_acceptable_after_another_episode_is_adopted(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository, episode_count=2)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1, 2]}],
        target_episode_numbers=[1, 2], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [
            {"episode_number": number, "kind": "script", "source_episode_id": source["id"], "source_fingerprint": content_fingerprint(source["content"]), "content": {"episode_title": f"优化稿 {number}", "scenes": []}}
            for number, source in enumerate(scripts, start=1)
        ],
    )
    client = _admin_client(repository)

    first = client.post(f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/accept")
    second = client.post(f"/api/script-optimization-drafts/{completed['drafts'][1]['id']}/accept")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text


def test_direct_draft_is_rejected_when_its_source_script_has_changed(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [{"episode_number": 1, "kind": "script", "source_episode_id": scripts[0]["id"], "source_fingerprint": content_fingerprint(scripts[0]["content"]), "content": {"episode_title": "优化稿", "scenes": []}}],
    )
    repository.create_episode_script(project["id"], 1, {"episode_title": "后续改过的原稿", "scenes": []})

    response = _admin_client(repository).post(f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/accept")

    assert response.status_code == 409
    assert "来源场次剧本已更新" in response.json()["detail"]


def test_episode_page_can_list_its_optimization_draft_entries(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [{"episode_number": 1, "kind": "script", "source_episode_id": scripts[0]["id"], "source_fingerprint": content_fingerprint(scripts[0]["content"]), "content": {"episode_title": "优化稿", "scenes": []}}],
    )

    response = _admin_client(repository).get(
        f"/api/creative/projects/{project['id']}/episodes/1/optimization-drafts?stage=script"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [completed["drafts"][0]["id"]]


def test_episode_drafts_use_historical_workflow_identity_for_project_access(
    tmp_path,
) -> None:
    """Catch optimization-draft access bypassing the historical ID resolver."""

    repository = CreativeRepository(tmp_path / "creative.db")
    project, _review, _scripts = _project_with_review(repository)
    principal = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"script.optimize.read"}),
        data_scope="project",
        session_id="session-writer",
    )
    historical_project_id = f"creative:historical-store:project:{project['id']}"
    permission_service = object.__new__(PermissionService)
    permission_service.can_access_assigned_project = (
        lambda _principal, project_id: project_id == historical_project_id
    )
    permission_service.can_access_managed_project = lambda _principal, _project_id: False
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.workflow_identity_resolver = SimpleNamespace(
        project_id=lambda _project_id: historical_project_id
    )
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_script_optimization_router(repository, object()))

    response = TestClient(app).get(
        f"/api/creative/projects/{project['id']}/episodes/1/optimization-drafts",
        params={"stage": "beats"},
    )

    assert response.status_code == 200


def test_applying_an_edited_script_draft_creates_a_locked_script_version(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [{"episode_number": 1, "kind": "script", "source_episode_id": scripts[0]["id"], "source_fingerprint": content_fingerprint(scripts[0]["content"]), "content": {"episode_title": "AI 原稿", "scenes": []}}],
    )
    edited_content = {"episode_title": "编剧修改后的优化稿", "scenes": []}

    response = _admin_client(repository).post(
        f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/accept",
        json={"content": edited_content, "lock_after_accept": True},
    )

    assert response.status_code == 200, response.text
    accepted = repository.get_episode(response.json()["accepted_episode_id"])
    assert accepted is not None
    assert accepted["content"] == edited_content
    assert accepted["status"] == "locked"


@pytest.mark.asyncio
async def test_optimization_worker_generates_isolated_direct_script_draft(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )

    class FakeProvider:
        async def complete_json(self, messages):
            assert messages[1]["role"] == "user"
            return {"script": {"episode_title": "优化后的第 1 集", "scenes": []}}

    monkeypatch.setattr(optimization_worker, "create_provider", lambda: FakeProvider())
    monkeypatch.chdir(tmp_path)
    await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-direct", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    completed = repository.get_script_optimization(optimization["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert [(item["kind"], item["source_episode_id"]) for item in completed["drafts"]] == [("script", scripts[0]["id"])]
    assert (tmp_path / "data" / "script_optimizations" / "optimization-direct" / "drafts.json").exists()


@pytest.mark.asyncio
async def test_optimization_worker_generates_paired_structure_drafts(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )

    class FakeProvider:
        async def complete_json(self, messages):
            return {
                "beats": {"beats": [{"order": 1, "goal": "反击"}]},
                "script": {"episode_title": "结构优化稿", "scenes": []},
            }

    monkeypatch.setattr(optimization_worker, "create_provider", lambda: FakeProvider())
    monkeypatch.chdir(tmp_path)
    await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-structure", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    completed = repository.get_script_optimization(optimization["id"])
    assert completed is not None
    beats, script = completed["drafts"]
    assert [beats["kind"], script["kind"]] == ["beats", "script"]
    assert script["source_episode_id"] is None
    assert beats["peer_draft_id"] == script["id"]
    assert script["peer_draft_id"] == beats["id"]


@pytest.mark.asyncio
async def test_optimization_worker_marks_task_failed_without_drafts(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )

    class FailingProvider:
        async def complete_json(self, messages):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(optimization_worker, "create_provider", lambda: FailingProvider())
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-failed", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    failed = repository.get_script_optimization(optimization["id"])
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["drafts"] == []


@pytest.mark.asyncio
async def test_optimization_worker_rejects_a_task_when_review_source_has_changed(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    repository.create_episode_script(project["id"], 1, {"episode_title": "变更后的原稿", "scenes": []})

    class FakeProvider:
        async def complete_json(self, messages):
            raise AssertionError("来源已变更时不应调用 AI")

    monkeypatch.setattr(optimization_worker, "create_provider", lambda: FakeProvider())
    with pytest.raises(ValueError, match="剧本已更新"):
        await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-stale", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    failed = repository.get_script_optimization(optimization["id"])
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["drafts"] == []
