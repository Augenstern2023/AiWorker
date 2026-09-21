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

from creative.free_mode_service import FreeCreativeModeService
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from screenplay_optimization.api import build_script_optimization_router
from screenplay_optimization.service import (
    content_fingerprint,
    optimize_episode,
    validate_optimization_request,
)
from screenplay_optimization import worker as optimization_worker
from screenplay_review.service import build_project_source
from core.job import CrawlJob



@pytest.fixture(autouse=True)
def _use_postgres_creative(monkeypatch, postgres_creative):
    """Exercise runtime behavior against the isolated PostgreSQL store."""
    monkeypatch.setitem(
        globals(), "CreativeRepository", lambda *_args, **_kwargs: postgres_creative
    )


def _project_with_review(
    repository: CreativeRepository,
    episode_count: int = 1,
    change_route: str = "script_only",
) -> tuple[dict, dict, list[dict]]:
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
    repository.complete_script_review(review["id"], {"ai_verdict": "建议修改", "summary": "", "core_findings": [{"title": "节奏", "change_route": change_route}], "episode_reviews": [], "cross_episode_findings": []})
    review = repository.confirm_script_review(review["id"], "建议修改", "", "admin-1", source["fingerprint"])
    return project, review, scripts


def _admin_client(repository: CreativeRepository, job_manager=None) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id="admin-1", username="admin", display_name="管理员", role_codes=("system_admin",),
        permissions=frozenset({"*"}), data_scope="all", session_id="session-admin",
    )
    app.include_router(build_script_optimization_router(repository, job_manager or object()))
    return TestClient(app)


def _free_project_with_review(
    repository: CreativeRepository,
) -> tuple[dict, dict, dict]:
    project = repository.create_original_project(
        seed_text="普通女孩发现家族秘密",
        name="自由优化项目",
        target_episode_count=1,
        created_by="admin-1",
        creation_request_id="free-optimization-project",
        workflow_mode="free",
    )
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project["id"], {"title": "方向"},
        expected_head_revision=0, actor_user_id="admin-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"], actor_user_id="admin-1",
    )
    cast = service.create_characters(
        project["id"], [{"name": "主角"}], [],
        expected_head_revision=0, actor_user_id="admin-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=cast["resource"]["batch_id"],
        expected_head_revision=cast["head"]["revision"], actor_user_id="admin-1",
    )
    outline = service.create_outline(
        project["id"], [{"episode": 1, "core_event": "主角反击"}],
        expected_head_revision=0, actor_user_id="admin-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="outline",
        resource_ref=outline["resource"]["batch_id"],
        expected_head_revision=outline["head"]["revision"], actor_user_id="admin-1",
    )
    beats = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": [{"order": 1}]},
        expected_head_revision=0, actor_user_id="admin-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="beats", episode_number=1,
        resource_ref=str(beats["resource"]["id"]),
        expected_head_revision=beats["head"]["revision"], actor_user_id="admin-1",
    )
    script = service.create_episode_resource(
        project["id"], "script", 1,
        {"episode_title": "自由原稿", "scenes": []},
        expected_head_revision=0, actor_user_id="admin-1",
    )
    project = repository.get_project(project["id"])
    assert project is not None
    source = build_project_source(project, "single", [1])
    review = repository.create_script_review(
        source_type="project", mode="single", project_id=project["id"],
        episode_numbers=[1], source_fingerprint=source["fingerprint"],
        source_payload=source, created_by="admin-1",
    )
    repository.complete_script_review(review["id"], {
        "ai_verdict": "建议修改", "summary": "", "core_findings": [{"title": "节奏"}],
        "episode_reviews": [], "cross_episode_findings": [],
    })
    review = repository.confirm_script_review(
        review["id"], "建议修改", "", "admin-1", source["fingerprint"]
    )
    return project, review, script["resource"]


def test_failed_optimization_can_retry_with_its_saved_drafts(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository, episode_count=2)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1, 2]}],
        target_episode_numbers=[1, 2], created_by="admin-1",
    )
    repository.set_script_optimization_job(optimization["id"], "original-job")
    repository.start_script_optimization(optimization["id"])
    repository.save_script_optimization_episode(optimization["id"], 1, [{
        "episode_number": 1, "kind": "script", "source_episode_id": 1,
        "source_fingerprint": "frozen", "content": {"episode_title": "首集", "scenes": []},
    }])
    repository.fail_script_optimization(optimization["id"], "第二集失败")

    class JobManager:
        def __init__(self):
            self.retried = []

        def retry(self, job_id):
            self.retried.append(job_id)
            return SimpleNamespace(job_id="resumed-job")

    jobs = JobManager()
    client = _admin_client(repository, jobs)
    response = client.post(f"/api/script-optimizations/{optimization['id']}/retry")

    assert response.status_code == 200, response.text
    assert jobs.retried == ["original-job"]
    assert response.json()["optimization"]["job_id"] == "resumed-job"
    assert [(item["episode_number"], item["kind"]) for item in response.json()["optimization"]["drafts"]] == [(1, "script")]
    assert client.post(f"/api/script-optimizations/{optimization['id']}/retry").status_code == 409


def test_failed_optimization_without_an_original_job_recreates_it(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    repository.fail_script_optimization(optimization["id"], "启动失败")

    class JobManager:
        def __init__(self):
            self.payloads = []

        def create_job(self, payload):
            self.payloads.append(payload)
            return SimpleNamespace(job_id="replacement-job")

    jobs = JobManager()
    response = _admin_client(repository, jobs).post(
        f"/api/script-optimizations/{optimization['id']}/retry"
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "replacement-job"
    assert jobs.payloads[0]["extra"]["optimization_id"] == optimization["id"]
    assert repository.get_script_optimization(optimization["id"])["status"] == "queued"


def test_job_launch_failure_stays_visible_and_can_be_retried(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    PromptStore(repository).enable_project(
        project["id"], actor_user_id="admin-1"
    )

    class JobManager:
        def __init__(self) -> None:
            self.create_attempts = 0

        def create_job(self, _payload):
            self.create_attempts += 1
            if self.create_attempts == 1:
                raise RuntimeError("queue unavailable")
            return SimpleNamespace(job_id="retry-after-launch-failure")

    jobs = JobManager()
    client = _admin_client(repository, jobs)
    created = client.post(
        f"/api/script-reviews/{review['id']}/optimizations",
        json={
            "mode": "direct",
            "findings": [{"ref": "core:0", "episode_numbers": [1]}],
        },
    )

    assert created.status_code == 201, created.text
    failed = created.json()["optimization"]
    assert failed["status"] == "failed"
    assert created.json()["start_error"] == "无法创建剧本优化任务"
    history = client.get(
        f"/api/script-reviews/{review['id']}/optimizations"
    ).json()["items"]
    assert [item["id"] for item in history] == [failed["id"]]

    retried = client.post(
        f"/api/script-optimizations/{failed['id']}/retry"
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["job_id"] == "retry-after-launch-failure"
    assert retried.json()["optimization"]["status"] == "queued"


def test_failed_optimization_and_its_drafts_can_be_deleted(tmp_path) -> None:
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
        [{
            "episode_number": 1, "kind": "script",
            "source_episode_id": scripts[0]["id"],
            "source_fingerprint": content_fingerprint(scripts[0]["content"]),
            "content": {"episode_title": "待删除草稿", "scenes": []},
        }],
    )
    draft_id = completed["drafts"][0]["id"]
    repository.fail_script_optimization(optimization["id"], "生成失败")

    deleted = _admin_client(repository).delete(
        f"/api/script-optimizations/{optimization['id']}"
    )

    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "deleted": True,
        "optimization_id": optimization["id"],
        "deleted_draft_count": 1,
    }
    assert repository.get_script_optimization(optimization["id"]) is None
    assert repository.get_script_optimization_draft(draft_id) is None


def test_non_failed_optimization_cannot_be_deleted(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )

    response = _admin_client(repository).delete(
        f"/api/script-optimizations/{optimization['id']}"
    )

    assert response.status_code == 409, response.text
    assert repository.get_script_optimization(optimization["id"]) is not None


@pytest.mark.parametrize(
    ("optimization_mode", "change_route", "prompt_mode"),
    [("direct", "script_only", "single"), ("structure", "beats_then_script", "series")],
)
def test_optimization_api_maps_mode_and_freezes_selected_template(
    tmp_path, optimization_mode: str, change_route: str, prompt_mode: str
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(
        repository, change_route=change_route
    )
    store = PromptStore(repository)
    store.enable_project(project["id"], actor_user_id="admin-1")

    class FakeJobManager:
        def __init__(self) -> None:
            self.payloads = []

        def create_job(self, payload):
            self.payloads.append(payload)
            return SimpleNamespace(job_id="optimization-prompt-job")

    jobs = FakeJobManager()
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id="admin-1", username="admin", display_name="管理员",
        role_codes=("system_admin",), permissions=frozenset({"*"}),
        data_scope="all", session_id="session-admin",
    )
    app.include_router(build_script_optimization_router(repository, jobs))

    response = TestClient(app).post(
        f"/api/script-reviews/{review['id']}/optimizations",
        json={
            "mode": optimization_mode,
            "findings": [{"ref": "core:0", "episode_numbers": [1]}],
            "prompt_template_key": "urban-identity-romcom",
            "prompt_override": {"custom_requirements": "减少解释性台词。"},
        },
    )

    assert response.status_code == 201, response.text
    optimization_id = response.json()["optimization"]["id"]
    frozen = repository.get_script_optimization(optimization_id)["prompt_snapshot"]
    assert frozen["stage"] == "optimization"
    assert frozen["mode"] == prompt_mode
    assert frozen["template_key"] == "urban-identity-romcom"
    assert "减少解释性台词" in frozen["effective_instruction"]
    assert jobs.payloads[0]["extra"]["prompt_snapshot"] == frozen

    generic = store.get_template_by_key("generic-vertical-short")
    generic_revision = store.get_revision(generic["id"])
    store.set_binding(
        project_id=project["id"],
        stage="optimization",
        episode_number=0,
        template_id=generic["id"],
        template_revision=generic_revision["revision"],
        override={},
        actor_user_id="admin-1",
    )
    assert repository.get_script_optimization(optimization_id)["prompt_snapshot"] == frozen


def test_optimization_task_keeps_generated_drafts_outside_project_versions(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    original_episodes = repository.get_project(project["id"])["episodes"]
    task = repository.create_script_optimization(
        review_id=review["id"],
        project_id=project["id"],
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
                "source_episode_id": next(item["id"] for item in original_episodes if item["stage"] == "script"),
                "source_fingerprint": "episode-fingerprint",
                "content": {"episode_title": "优化稿", "scenes": []},
            }
        ],
    )

    assert completed["status"] == "completed"
    assert completed["drafts"][0]["kind"] == "script"
    assert completed["drafts"][0]["status"] == "ready"
    assert repository.get_project(project["id"])["episodes"] == original_episodes


def test_structure_optimization_links_beats_and_script_drafts(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    original_episodes = repository.get_project(project["id"])["episodes"]
    task = repository.create_script_optimization(
        review_id=review["id"],
        project_id=project["id"],
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
                "source_episode_id": next(item["id"] for item in original_episodes if item["stage"] == "beats"),
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


@pytest.mark.parametrize("finding_episode_number", [None, 0, "", "unknown", 4])
def test_optimization_request_keeps_episode_finding_in_its_review_scope(
    finding_episode_number,
) -> None:
    review = {
        "source_type": "project",
        "source_payload": {"formal": True},
        "status": "confirmed",
        "final_verdict": "建议修改",
        "episode_numbers": [1, 2, 3, 4, 5],
        "report": {
            "core_findings": [],
            "episode_reviews": [
                {
                    "episode_number": 2,
                    "summary": "第二集需要调整。",
                    "findings": [
                        {
                            "title": "仅第二集的问题",
                            "episode_number": finding_episode_number,
                            "change_route": "script_only",
                        }
                    ],
                }
            ],
            "cross_episode_findings": [],
        },
    }

    result = validate_optimization_request(
        review,
        "direct",
        [{"ref": "episode:0:0", "episode_numbers": []}],
    )

    assert result["target_episode_numbers"] == [2]
    assert result["selected_findings"] == [
        {"ref": "episode:0:0", "episode_numbers": [2]}
    ]


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


@pytest.mark.asyncio
async def test_optimization_template_cannot_replace_output_or_outline_guards() -> None:
    class FakeProvider:
        async def complete_json(self, messages):
            system = messages[0]["content"]
            assert "用更快进入冲突修复" in system
            assert "script 必须是完整场次剧本 JSON" in system
            assert "不得擅自改写" in system
            assert "不得覆盖前述输出契约" in system
            return {"script": {"episode_title": "优化稿", "scenes": []}}

    result = await optimize_episode(
        FakeProvider(),
        mode="direct",
        episode_number=1,
        script_content={"episode_title": "原稿", "scenes": []},
        beats_content=None,
        review_context={},
        findings=[{"title": "冲突过慢"}],
        summaries=[],
        prompt_snapshot={
            "stage": "optimization",
            "mode": "single",
            "effective_instruction": "用更快进入冲突修复。",
        },
    )

    assert result["script"]["episode_title"] == "优化稿"


@pytest.mark.asyncio
async def test_direct_optimization_sends_only_the_current_episode_summary() -> None:
    requests = []

    class FakeProvider:
        async def complete_json(self, messages):
            import json

            requests.append(json.loads(messages[-1]["content"]))
            return {"script": {"episode_title": "优化稿", "scenes": []}}

    await optimize_episode(
        FakeProvider(),
        mode="direct",
        episode_number=2,
        script_content={"episode_title": "原稿", "scenes": []},
        beats_content=None,
        review_context={},
        findings=[{"title": "第二集问题", "evidence": "证据", "suggestion": "建议"}],
        summaries=[
            {
                "episode_number": 1,
                "summary": "第一集摘要",
                "findings": [{"title": "第一集问题", "evidence": "不应发送"}],
            },
            {
                "episode_number": 2,
                "summary": "第二集摘要",
                "findings": [{"title": "已通过 selected_findings 单独发送"}],
            },
            {
                "episode_number": 3,
                "summary": "第三集摘要",
                "findings": [{"title": "第三集问题", "evidence": "不应发送"}],
            },
        ],
    )

    assert requests[0]["episode_summaries"] == [
        {"episode_number": 2, "summary": "第二集摘要"}
    ]
    assert requests[0]["selected_findings"][0]["title"] == "第二集问题"


@pytest.mark.asyncio
async def test_structure_optimization_splits_beats_and_script_without_duplicate_context() -> None:
    requests = []

    class FakeProvider:
        async def complete_json(self, messages):
            import json
            payload = json.loads(messages[-1]["content"])
            requests.append(payload)
            if len(requests) == 1:
                return {"beats": {"beats": [{"order": 1, "goal": "更快反击"}]}}
            return {"script": {"episode_title": "依据新 Beats 的优化稿", "scenes": []}}

    result = await optimize_episode(
        FakeProvider(),
        mode="structure",
        episode_number=2,
        script_content={"episode_title": "原稿", "scenes": []},
        beats_content={"beats": [{"order": 1, "goal": "反击"}]},
        review_context={
            "brief": {"content": {"theme": "成长"}},
            "episode": {
                "episode_number": 2,
                "text": "重复原稿",
                "script": {"content": {"episode_title": "原稿", "scenes": []}},
                "beats": {"content": {"beats": [{"order": 1, "goal": "反击"}]}},
                "outline": {"content": {"episode": 2}},
            },
        },
        findings=[{"title": "结构拖沓"}],
        summaries=[],
    )

    assert len(requests) == 2
    assert "script" not in requests[0]["review_context"]["episode"]
    assert "beats" not in requests[0]["review_context"]["episode"]
    assert "text" not in requests[0]["review_context"]["episode"]
    assert requests[1]["beats"] == result["beats"]
    assert result["script"]["episode_title"] == "依据新 Beats 的优化稿"


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


def test_accept_direct_optimization_creates_locked_script_without_project_status_change(tmp_path) -> None:
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
    accepted_episode = repository.get_episode(accepted["accepted_episode_id"])
    assert accepted_episode["content"]["episode_title"] == "优化稿"
    assert accepted_episode["status"] == "locked"
    assert repository.get_project(project["id"])["status"] == before_status


def test_accept_optimization_rejects_the_removed_unlocked_apply_mode(tmp_path) -> None:
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

    response = _admin_client(repository).post(
        f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/accept",
        json={"lock_after_accept": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "优化草稿必须锁定并采纳"
    assert repository.get_script_optimization_draft(completed["drafts"][0]["id"])["status"] == "ready"


def test_free_project_accepts_optimization_as_new_current_head(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, source_script = _free_project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    completed = repository.complete_script_optimization(optimization["id"], [{
        "episode_number": 1,
        "kind": "script",
        "source_episode_id": source_script["id"],
        "source_fingerprint": content_fingerprint(source_script["content"]),
        "content": {"episode_title": "自由优化稿", "scenes": []},
    }])

    response = _admin_client(repository).post(
        f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/accept",
        json={"lock_after_accept": True},
    )

    assert response.status_code == 200, response.text
    accepted = repository.get_episode(response.json()["accepted_episode_id"])
    assert accepted is not None
    assert accepted["resource_scope"] == "free"
    assert accepted["status"] == "locked"
    refreshed = repository.get_project(project["id"])
    head = next(
        item for item in refreshed["resource_heads"]
        if item["stage"] == "script" and item["episode_number"] == 1
    )
    assert head["resource_ref"] == str(accepted["id"])
    assert build_project_source(refreshed, "single", [1])["episodes"][0]["script"]["id"] == accepted["id"]


def test_structure_drafts_show_accepted_immediately_after_each_is_locked(tmp_path) -> None:
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
    assert accepted_beats.json()["status"] == "accepted"
    assert repository.get_episode(accepted_beats.json()["accepted_episode_id"])["status"] == "locked"

    accepted_script = client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept")
    assert accepted_script.status_code == 200, accepted_script.text
    assert accepted_script.json()["status"] == "accepted"
    assert repository.get_episode(accepted_script.json()["accepted_episode_id"])["status"] == "locked"
    assert repository.get_script_optimization_draft(beats_draft["id"])["status"] == "accepted"


def test_structure_script_draft_is_rejected_when_applied_beats_is_no_longer_locked(tmp_path) -> None:
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
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_episodes SET status = 'draft' WHERE id = ?",
            (accepted_beats.json()["accepted_episode_id"],),
        )

    response = client.post(f"/api/script-optimization-drafts/{script_draft['id']}/accept")

    assert response.status_code == 409
    assert "未锁定" in response.json()["detail"]


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


def test_ready_script_optimization_draft_can_be_saved_before_accepting(tmp_path) -> None:
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
        [{
            "episode_number": 1, "kind": "script",
            "source_episode_id": scripts[0]["id"],
            "source_fingerprint": content_fingerprint(scripts[0]["content"]),
            "content": {"episode_title": "AI 原稿", "scenes": []},
        }],
    )
    draft_id = completed["drafts"][0]["id"]
    edited = {
        "episode_title": "人工调整稿",
        "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日",
            "interior_exterior": "内", "location": "客厅", "props": [],
            "characters": ["主角"],
            "blocks": [{"type": "action", "text": "主角推门而入。"}],
        }],
    }

    saved = _admin_client(repository).patch(
        f"/api/script-optimization-drafts/{draft_id}", json={"content": edited}
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == edited
    assert repository.get_script_optimization_draft(draft_id)["status"] == "ready"


def test_ready_beats_optimization_draft_can_be_edited_and_saved(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(
        repository, change_route="beats_then_script"
    )
    project_episodes = repository.get_project(project["id"])["episodes"]
    source_beats = next(item for item in project_episodes if item["stage"] == "beats")
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    original_beats = {
        "beats": [{
            "order": 1, "scene": "旧场景", "characters": ["主角"],
            "goal": "旧目标", "obstacle": "旧阻力", "emotion_change": "警惕",
            "state_change": "得到线索", "cost": "暴露身份",
            "opposition_tactic": "对手设局", "turn": "发现内鬼", "hook": "门外枪响",
            "continuity_updates": [],
        }],
    }
    completed = repository.complete_script_optimization(
        optimization["id"],
        [
            {
                "episode_number": 1, "kind": "beats",
                "source_episode_id": source_beats["id"],
                "source_fingerprint": content_fingerprint(source_beats["content"]),
                "content": original_beats,
            },
            {
                "episode_number": 1, "kind": "script",
                "source_episode_id": None,
                "source_fingerprint": content_fingerprint(original_beats),
                "content": {"episode_title": "优化稿", "scenes": []},
            },
        ],
    )
    beats_draft = next(item for item in completed["drafts"] if item["kind"] == "beats")
    edited = {
        "beats": [{
            **original_beats["beats"][0],
            "scene": "人工调整后的场景",
            "goal": "人工调整后的目标",
            "continuity_updates": [{
                "kind": "information", "name": "关键证据", "event": "主角取得证据",
                "holder": "主角", "location": "仓库", "known_by": ["主角"],
                "status": "active",
            }],
        }],
    }

    saved = _admin_client(repository).patch(
        f"/api/script-optimization-drafts/{beats_draft['id']}",
        json={"content": edited},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["content"] == edited
    assert repository.get_script_optimization_draft(beats_draft["id"])["content"] == edited


def test_one_optimization_episode_can_be_regenerated_with_the_frozen_settings(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, scripts = _project_with_review(repository, episode_count=2)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1, 2]}],
        target_episode_numbers=[1, 2], created_by="admin-1",
        prompt_snapshot={
            "stage": "optimization", "mode": "single", "owner_scope": "system",
        },
    )
    completed = repository.complete_script_optimization(
        optimization["id"],
        [{
            "episode_number": number, "kind": "script",
            "source_episode_id": source["id"],
            "source_fingerprint": content_fingerprint(source["content"]),
            "content": {"episode_title": f"优化稿 {number}", "scenes": []},
        } for number, source in enumerate(scripts, start=1)],
    )

    class JobManager:
        def __init__(self):
            self.payloads = []

        def create_job(self, payload):
            self.payloads.append(payload)
            return SimpleNamespace(job_id="regenerated-episode")

    jobs = JobManager()
    response = _admin_client(repository, jobs).post(
        f"/api/script-optimization-drafts/{completed['drafts'][0]['id']}/regenerate"
    )

    assert response.status_code == 201, response.text
    regenerated = response.json()["optimization"]
    assert regenerated["target_episode_numbers"] == [1]
    assert regenerated["selected_findings"] == [
        {"ref": "core:0", "episode_numbers": [1]}
    ]
    assert regenerated["prompt_snapshot"] == optimization["prompt_snapshot"]
    assert jobs.payloads[0]["extra"]["optimization_id"] == regenerated["id"]


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

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: FakeProvider())
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

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: FakeProvider())
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

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: FailingProvider())
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-failed", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    failed = repository.get_script_optimization(optimization["id"])
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["drafts"] == []


@pytest.mark.asyncio
async def test_optimization_worker_emits_safe_retry_progress(tmp_path, monkeypatch) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1], created_by="admin-1",
    )
    events = []

    class RetryingProvider:
        max_retries = 2

        async def complete_json(self, messages):
            assert self.retry_callback is not None
            self.retry_callback(1, 2, "secret request body")
            return {"script": {"episode_title": "完成", "scenes": []}}

    provider = RetryingProvider()

    def provider_factory(*, retry_callback=None):
        provider.retry_callback = retry_callback
        return provider

    monkeypatch.setattr(optimization_worker, "create_provider", provider_factory)
    monkeypatch.setattr(
        optimization_worker, "emit",
        lambda job, event, message, level="info", payload=None: events.append(
            {"event": event, "message": message, "payload": payload or {}}
        ),
    )
    monkeypatch.chdir(tmp_path)
    await optimization_worker.run(CrawlJob(
        job_id="optimization-retry", platform="creative",
        crawler_type="screenplay_optimization",
        extra={"optimization_id": optimization["id"]},
    ))

    retry = next(item for item in events if "重试" in item["message"])
    assert retry["payload"]["current_episode"] == 1
    assert retry["payload"]["request_attempt"] == 2
    assert retry["payload"]["request_max_attempts"] == 3
    assert "secret request body" not in str(events)


@pytest.mark.asyncio
async def test_optimization_worker_keeps_first_episode_and_skips_it_on_resume(
    tmp_path, monkeypatch,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository, episode_count=2)
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="direct",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1, 2]}],
        target_episode_numbers=[1, 2], created_by="admin-1",
    )
    calls = []

    class InterruptingProvider:
        async def complete_json(self, messages):
            import json
            number = json.loads(messages[-1]["content"])["episode_number"]
            calls.append(number)
            if number == 2 and calls.count(2) == 1:
                raise RuntimeError("second episode unavailable")
            return {"script": {"episode_title": f"完成第 {number} 集", "scenes": []}}

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: InterruptingProvider())
    monkeypatch.chdir(tmp_path)
    job = CrawlJob(
        job_id="optimization-resume", platform="creative",
        crawler_type="screenplay_optimization",
        extra={"optimization_id": optimization["id"]},
    )
    with pytest.raises(RuntimeError, match="second episode unavailable"):
        await optimization_worker.run(job)

    failed = repository.get_script_optimization(optimization["id"])
    assert failed["status"] == "failed"
    assert [(item["episode_number"], item["kind"]) for item in failed["drafts"]] == [(1, "script")]
    await optimization_worker.run(job)
    completed = repository.get_script_optimization(optimization["id"])
    assert completed["status"] == "completed"
    assert [(item["episode_number"], item["kind"]) for item in completed["drafts"]] == [
        (1, "script"), (2, "script")
    ]
    assert calls == [1, 2, 2]


@pytest.mark.asyncio
async def test_structure_optimization_persists_each_complete_pair_before_next_episode(
    tmp_path, monkeypatch,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(
        repository, episode_count=2, change_route="beats_then_script"
    )
    optimization = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure",
        source_fingerprint=review["source_fingerprint"],
        selected_findings=[{"ref": "core:0", "episode_numbers": [1, 2]}],
        target_episode_numbers=[1, 2], created_by="admin-1",
    )

    class Provider:
        async def complete_json(self, messages):
            import json
            number = json.loads(messages[-1]["content"])["episode_number"]
            if number == 2:
                saved = repository.get_script_optimization(optimization["id"])["drafts"]
                assert [(item["episode_number"], item["kind"]) for item in saved] == [
                    (1, "beats"), (1, "script")
                ]
                raise RuntimeError("second pair unavailable")
            return {
                "beats": {"beats": [{"order": 1, "goal": "反击"}]},
                "script": {"episode_title": "优化第一集", "scenes": []},
            }

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: Provider())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="second pair unavailable"):
        await optimization_worker.run(CrawlJob(
            job_id="optimization-structure-partial", platform="creative",
            crawler_type="screenplay_optimization",
            extra={"optimization_id": optimization["id"]},
        ))
    failed = repository.get_script_optimization(optimization["id"])
    beats, script = failed["drafts"]
    assert failed["status"] == "failed"
    assert beats["peer_draft_id"] == script["id"]
    assert script["peer_draft_id"] == beats["id"]


def test_optimization_episode_checkpoint_rejects_half_a_structure_pair(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project, review, _ = _project_with_review(repository)
    original_episodes = repository.get_project(project["id"])["episodes"]
    task = repository.create_script_optimization(
        review_id=review["id"], project_id=project["id"], mode="structure", source_fingerprint="source",
        selected_findings=[], target_episode_numbers=[1], created_by="writer-1",
    )
    repository.start_script_optimization(task["id"])
    with pytest.raises(ValueError, match="不完整"):
        repository.save_script_optimization_episode(task["id"], 1, [{
            "episode_number": 1, "kind": "beats", "content": {"beats": []},
        }])
    assert repository.get_script_optimization(task["id"])["drafts"] == []
    with pytest.raises(ValueError, match="尚未完成"):
        repository.finish_script_optimization(task["id"])


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

    monkeypatch.setattr(optimization_worker, "create_provider", lambda **_: FakeProvider())
    with pytest.raises(ValueError, match="剧本已更新"):
        await optimization_worker._run_with_repository(CrawlJob(job_id="optimization-stale", platform="creative", crawler_type="screenplay_optimization", extra={"optimization_id": optimization["id"]}), repository)

    failed = repository.get_script_optimization(optimization["id"])
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["drafts"] == []
