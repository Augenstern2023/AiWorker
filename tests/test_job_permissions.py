# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import WebGUI
from audit.service import AuditService
from creative.api import register_creative_routes
from creative.repository import CreativeRepository
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from core.job import CrawlJob
from runtime.job_manager import JobManager
from runtime.job_state import JobState
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository


def test_workflow_repository_links_machine_job_to_human_task(tmp_path: Path):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    repository = WorkflowRepository(identity)
    task = repository.create_task(
        "workflow-1",
        "breakdown",
        "selection_operator",
        actor=SYSTEM_ACTOR,
    )

    repository.link_task_job(task["id"], "job-1")
    repository.link_task_job(task["id"], "job-1")

    assert repository.list_task_job_ids(task["id"]) == ["job-1"]


class CompletedProcess:
    pid = 8712
    stdout = None

    @staticmethod
    def wait(timeout=None):
        return 0

    @staticmethod
    def poll():
        return 0


class RunningProcess:
    pid = 8713
    stdout = None

    def __init__(self):
        self.return_code = None

    def wait(self, timeout=None):
        return self.return_code

    def poll(self):
        return self.return_code

    def terminate(self):
        self.return_code = -15

    def kill(self):
        self.return_code = -9


@pytest.fixture
def job_environment(tmp_path: Path, monkeypatch):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    workflow = WorkflowRepository(identity)
    audit = AuditService(identity)
    manager = JobManager(tmp_path / "runtime", cwd=tmp_path)
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker",
        lambda *_args, **_kwargs: CompletedProcess(),
    )
    monkeypatch.setattr(WebGUI, "job_manager", manager)
    monkeypatch.setattr(WebGUI, "workflow_repository", workflow)
    monkeypatch.setattr(WebGUI, "audit_service", audit)
    monkeypatch.setattr(WebGUI.app.state, "identity_session_service", sessions)
    monkeypatch.setattr(WebGUI.app.state, "identity_permission_service", permissions)
    monkeypatch.setattr(WebGUI.app.state, "workflow_repository", workflow)
    monkeypatch.setattr(WebGUI.app.state, "audit_service", audit)
    return SimpleNamespace(
        identity=identity,
        sessions=sessions,
        permissions=permissions,
        workflow=workflow,
        audit=audit,
        manager=manager,
    )


def role_client(job_environment, role_code: str, username: str) -> TestClient:
    role = job_environment.identity.get_role_by_code(role_code)
    assert role is not None
    user = job_environment.identity.create_user(username, username, "hash")
    job_environment.identity.replace_user_roles(user["id"], [role["id"]])
    session_app = FastAPI()

    @session_app.get("/session")
    async def create_session(request: Request):
        return job_environment.sessions.create_session(user["id"], request)

    raw_token, csrf_token = TestClient(session_app).get("/session").json()
    client = TestClient(WebGUI.app)
    client.cookies.set(job_environment.sessions.cookie_name, raw_token)
    client.headers["X-CSRF-Token"] = csrf_token
    client.user_id = user["id"]
    client.principal = job_environment.permissions.principal_for_user(
        user["id"], f"session:{username}"
    )
    assert client.principal is not None
    return client


def test_selection_download_job_records_owner_task_link_and_audit(job_environment):
    client = role_client(job_environment, "selection_operator", "selector")
    task = job_environment.workflow.create_task(
        "workflow-download",
        "breakdown",
        "selection_operator",
        actor=SYSTEM_ACTOR,
    )
    job_environment.workflow.claim_task(task["id"], client.principal)

    response = client.post(
        "/api/hongguo/drama/download/jobs",
        json={
            "title": "测试剧",
            "book_id": "book-1",
            "episodes": [1],
            "workflow_task_id": task["id"],
        },
    )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["created_by_user_id"] == client.user_id
    assert job["workflow_task_id"] == task["id"]
    assert job_environment.workflow.list_task_job_ids(task["id"]) == [job["job_id"]]
    events = job_environment.audit.query(action="job.create", target_id=job["job_id"])
    assert events[0]["actor_user_id"] == client.user_id


def add_job(
    job_environment,
    job_id: str,
    *,
    created_by_user_id: str | None = None,
    project_id: str | None = None,
    workflow_task_id: str | None = None,
    platform: str | None = None,
    crawler_type: str = "search",
    action: str | None = None,
    running: bool = False,
) -> None:
    extra = {}
    if created_by_user_id is not None:
        extra["created_by_user_id"] = created_by_user_id
    if project_id is not None:
        extra["project_id"] = project_id
    if workflow_task_id is not None:
        extra["workflow_task_id"] = workflow_task_id
    if action is not None:
        extra["action"] = action
    state = JobState(
        job=CrawlJob(
            job_id=job_id,
            platform=platform or job_id,
            crawler_type=crawler_type,
            extra=extra,
        ),
        status="running" if running else "succeeded",
    )
    job_environment.manager.jobs[job_id] = state
    job_environment.manager._save_state(state)
    state.job.save(job_environment.manager.jobs_dir / f"{job_id}.json")
    if running:
        job_environment.manager.processes[job_id] = RunningProcess()


def test_job_reads_are_filtered_for_creator_project_member_admin_and_legacy(
    job_environment,
):
    creator = role_client(job_environment, "selection_operator", "creator")
    stranger = role_client(job_environment, "selection_operator", "stranger")
    member = role_client(job_environment, "creative_planner", "member")
    admin = role_client(job_environment, "system_admin", "job-admin")
    job_environment.workflow.add_project_member(
        "project-1", member.user_id, "creative_planner", admin.principal
    )
    add_job(job_environment, "owned-job", created_by_user_id=creator.user_id)
    add_job(job_environment, "project-job", project_id="project-1")
    add_job(job_environment, "legacy-job")

    assert [item["job_id"] for item in creator.get("/api/jobs").json()["jobs"]] == [
        "owned-job"
    ]
    assert stranger.get("/api/jobs/owned-job").status_code == 403
    assert member.get("/api/jobs/project-job").status_code == 200
    assert member.get("/api/jobs/legacy-job").status_code == 403
    assert {item["job_id"] for item in admin.get("/api/jobs").json()["jobs"]} == {
        "owned-job",
        "project-job",
        "legacy-job",
    }


def test_job_control_allows_creator_and_project_owner_but_denies_other_users(
    job_environment,
):
    creator = role_client(job_environment, "selection_operator", "controller")
    stranger = role_client(job_environment, "selection_operator", "denied")
    owner = role_client(job_environment, "project_owner", "project-owner")
    admin = role_client(job_environment, "system_admin", "control-admin")
    job_environment.workflow.add_project_member(
        "project-1", owner.user_id, "project_owner", admin.principal
    )
    add_job(
        job_environment,
        "creator-running",
        created_by_user_id=creator.user_id,
        running=True,
    )
    add_job(job_environment, "project-running", project_id="project-1", running=True)
    add_job(job_environment, "project-terminal", project_id="project-1")
    add_job(job_environment, "legacy-running", running=True)

    assert stranger.post("/api/jobs/creator-running/stop").status_code == 403
    assert owner.post("/api/jobs/project-running/pause").status_code == 403
    assert owner.post("/api/jobs/project-running/resume").status_code == 403
    assert owner.post("/api/jobs/project-terminal/retry").status_code == 403
    assert creator.post("/api/jobs/creator-running/stop").status_code == 200
    assert owner.post("/api/jobs/project-running/stop").status_code == 200
    assert creator.post("/api/jobs/legacy-running/stop").status_code == 403
    assert admin.post("/api/jobs/legacy-running/stop").status_code == 200
    assert {
        event["target_id"]
        for event in job_environment.audit.query(action="job.stop")
    } == {"creator-running", "project-running", "legacy-running"}


def test_only_system_admin_may_delete_job_history(job_environment):
    creator = role_client(job_environment, "selection_operator", "delete-owner")
    admin = role_client(job_environment, "system_admin", "delete-admin")
    add_job(
        job_environment,
        "delete-job",
        created_by_user_id=creator.user_id,
    )

    assert creator.delete("/api/jobs/delete-job").status_code == 403
    assert admin.delete("/api/jobs/delete-job").status_code == 200
    events = job_environment.audit.query(action="job.delete", target_id="delete-job")
    assert events[0]["actor_user_id"] == admin.user_id


def test_legacy_latest_job_endpoints_require_authentication(job_environment):
    client = TestClient(WebGUI.app)

    assert client.get("/api/logs").status_code == 401
    assert client.post("/api/stop").status_code == 401
    assert client.post(
        "/api/run",
        json={
            "platform": "dy",
            "login_type": "qrcode",
            "crawler_type": "search",
            "keywords": "test",
            "start_page": 1,
            "headless": True,
            "save_option": "json",
            "get_comment": False,
            "get_sub_comment": False,
        },
    ).status_code == 401


def test_creator_pause_resume_and_retry_are_audited(job_environment):
    creator = role_client(job_environment, "selection_operator", "mutation-owner")
    add_job(
        job_environment,
        "mutable-running",
        created_by_user_id=creator.user_id,
        running=True,
    )

    assert creator.post("/api/jobs/mutable-running/pause").status_code == 200
    assert creator.post("/api/jobs/mutable-running/resume").status_code == 200
    assert creator.post("/api/jobs/mutable-running/stop").status_code == 200
    state = job_environment.manager.jobs["mutable-running"]
    state.status = "cancelled"
    job_environment.manager._save_state(state)
    retried = creator.post("/api/jobs/mutable-running/retry")

    assert retried.status_code == 200
    assert retried.json()["job"]["created_by_user_id"] == creator.user_id
    actions = {
        event["action"]
        for event in job_environment.audit.query(target_type="job", limit=20)
    }
    assert {"job.pause", "job.resume", "job.stop", "job.retry"} <= actions


def test_removed_project_creator_cannot_retry_project_job(job_environment):
    planner = role_client(job_environment, "creative_planner", "removed-planner")
    admin = role_client(job_environment, "system_admin", "removed-admin")
    job_environment.workflow.add_project_member(
        "project-removed", planner.user_id, "creative_planner", admin.principal
    )
    add_job(
        job_environment,
        "removed-project-job",
        created_by_user_id=planner.user_id,
        project_id="project-removed",
        platform="creative",
        crawler_type="phase4_generate",
        action="blueprints",
    )
    job_environment.workflow.add_project_member(
        "project-removed",
        planner.user_id,
        "creative_planner",
        admin.principal,
        status="inactive",
    )

    response = planner.post("/api/jobs/removed-project-job/retry")

    assert response.status_code == 403


def test_reassigned_task_creator_cannot_retry_linked_job(job_environment):
    original = role_client(job_environment, "selection_operator", "task-original")
    replacement = role_client(
        job_environment, "selection_operator", "task-replacement"
    )
    admin = role_client(job_environment, "system_admin", "task-admin")
    task = job_environment.workflow.create_task(
        "workflow-retry-reassignment",
        "breakdown",
        "selection_operator",
        actor=SYSTEM_ACTOR,
    )
    job_environment.workflow.claim_task(task["id"], original.principal)
    job_environment.workflow.reassign_task(
        task["id"], replacement.user_id, admin.principal
    )
    add_job(
        job_environment,
        "reassigned-task-job",
        created_by_user_id=original.user_id,
        workflow_task_id=task["id"],
    )

    response = original.post("/api/jobs/reassigned-task-job/retry")

    assert response.status_code == 403


def test_latest_stop_of_owned_job_does_not_set_legacy_stop_flag(
    job_environment, monkeypatch
):
    creator = role_client(job_environment, "selection_operator", "normal-stop")
    add_job(
        job_environment,
        "normal-running",
        created_by_user_id=creator.user_id,
        running=True,
    )
    monkeypatch.setattr(WebGUI.state, "stop_requested", False)

    response = creator.post("/api/stop")

    assert response.status_code == 200
    assert WebGUI.state.stop_requested is False


@pytest.mark.parametrize(
    ("platform", "crawler_type"),
    [
        ("creative", "phase4_generate"),
        ("hg", "drama_download"),
        ("dy", "phase4_generate"),
        ("unknown", "search"),
    ],
)
def test_generic_job_create_rejects_sensitive_or_unknown_worker_combinations(
    job_environment, platform: str, crawler_type: str
):
    creator = role_client(job_environment, "selection_operator", f"generic-{platform}")

    response = creator.post(
        "/api/jobs",
        json={
            "platform": platform,
            "crawler_type": crawler_type,
            "extra": {"action": "blueprints", "project_id": "project-secret"},
        },
    )

    assert response.status_code == 400
    assert job_environment.manager.list_jobs() == []


def test_generic_job_create_strips_unvalidated_client_project_ownership(
    job_environment,
):
    creator = role_client(job_environment, "selection_operator", "project-spoof")

    response = creator.post(
        "/api/jobs",
        json={
            "platform": "dy",
            "crawler_type": "search",
            "keywords": "test",
            "extra": {
                "project_id": "project-secret",
                "created_by_user_id": "other-user",
            },
        },
    )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["created_by_user_id"] == creator.user_id
    assert job["project_id"] is None
    assert "project_id" not in job["job"]["extra"]


def test_legacy_run_endpoint_rejects_sensitive_worker_combinations(job_environment):
    creator = role_client(job_environment, "selection_operator", "run-sensitive")

    response = creator.post(
        "/api/run",
        json={
            "platform": "creative",
            "login_type": "qrcode",
            "crawler_type": "phase4_generate",
            "keywords": "test",
            "start_page": 1,
            "headless": True,
            "save_option": "json",
            "get_comment": False,
            "get_sub_comment": False,
        },
    )

    assert response.status_code == 400
    assert job_environment.manager.list_jobs() == []


def test_legacy_stop_authorizes_before_mutation_and_audits_admin_stop(
    job_environment, monkeypatch
):
    creator = role_client(job_environment, "selection_operator", "legacy-denied")
    admin = role_client(job_environment, "system_admin", "legacy-admin")
    process = RunningProcess()
    monkeypatch.setattr(WebGUI.state, "process", process)
    monkeypatch.setattr(WebGUI.state, "stop_requested", False)
    monkeypatch.setattr(WebGUI.sys, "platform", "linux")

    denied = creator.post("/api/stop")

    assert denied.status_code == 403
    assert WebGUI.state.stop_requested is False
    assert process.return_code is None

    stopped = admin.post("/api/stop")

    assert stopped.status_code == 200
    assert WebGUI.state.stop_requested is True
    assert process.return_code == -15
    events = job_environment.audit.query(action="job.stop")
    assert events[0]["target_id"] == f"legacy-process:{process.pid}"


class RecordingJobManager:
    def __init__(self):
        self.payloads: list[dict] = []

    def create_job(self, payload: dict):
        payload = dict(payload)
        payload.setdefault("job_id", f"creative-job-{len(self.payloads) + 1}")
        self.payloads.append(payload)
        return JobState(job=CrawlJob.from_dict(payload), status="running")


def test_creative_generation_job_records_owner_project_task_link_and_audit(
    tmp_path: Path,
):
    runtime_dir = tmp_path / ".runtime"
    identity = IdentityRepository(runtime_dir / "system" / "system.db")
    identity.seed_defaults()
    sessions = SessionService(identity, secure_cookie=False)
    permissions = PermissionService(identity, sessions)
    workflow = WorkflowRepository(identity)
    audit = AuditService(identity)
    manager = RecordingJobManager()
    app = FastAPI()
    app.state.identity_permission_service = permissions
    app.state.audit_service = audit
    register_creative_routes(
        app, root=tmp_path, runtime_dir=runtime_dir, job_manager=manager
    )
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    topic = repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-1",
                "source_path": "rank.json",
                "captured_at": "2026-07-22",
                "title": "创作剧",
                "description": "",
                "category": "都市",
                "rank_type": "热播",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"core": "conflict"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(
        topic["id"], breakdown["id"], "创作项目", 1
    )
    role = identity.get_role_by_code("creative_planner")
    admin_role = identity.get_role_by_code("system_admin")
    assert role is not None
    assert admin_role is not None
    user = identity.create_user("creative-owner", "Creative Owner", "hash")
    admin_user = identity.create_user("creative-admin", "Creative Admin", "hash")
    identity.replace_user_roles(user["id"], [role["id"]])
    identity.replace_user_roles(admin_user["id"], [admin_role["id"]])
    principal = permissions.principal_for_user(user["id"], "session:creative")
    admin = permissions.principal_for_user(admin_user["id"], "session:admin")
    assert principal is not None
    assert admin is not None
    workflow.add_project_member(
        str(project["id"]), user["id"], "creative_planner", admin
    )
    task = workflow.create_task(
        "workflow-creative-job",
        "brief",
        "creative_planner",
        project_id=str(project["id"]),
        actor=SYSTEM_ACTOR,
    )
    workflow.claim_task(task["id"], principal)
    session_app = FastAPI()

    @session_app.get("/session")
    async def create_session(request: Request):
        return sessions.create_session(user["id"], request)

    raw_token, csrf_token = TestClient(session_app).get("/session").json()
    client = TestClient(app)
    client.cookies.set(sessions.cookie_name, raw_token)

    response = client.post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 3, "workflow_task_id": task["id"]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["created_by_user_id"] == user["id"]
    assert job["project_id"] == project["id"]
    assert job["workflow_task_id"] == task["id"]
    assert workflow.list_task_job_ids(task["id"]) == [job["job_id"]]
    event = audit.query(action="job.create", target_id=job["job_id"])[0]
    assert event["actor_user_id"] == user["id"]

    unauthenticated = TestClient(app).post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 3},
    )
    assert unauthenticated.status_code == 401

    outsider_user = identity.create_user("creative-outsider", "Outsider", "hash")
    identity.replace_user_roles(outsider_user["id"], [role["id"]])
    outsider_session_app = FastAPI()

    @outsider_session_app.get("/session")
    async def create_outsider_session(request: Request):
        return sessions.create_session(outsider_user["id"], request)

    outsider_token, _ = TestClient(outsider_session_app).get("/session").json()
    outsider = TestClient(app)
    outsider.cookies.set(sessions.cookie_name, outsider_token)

    denied = outsider.post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 3},
    )

    assert denied.status_code == 403
    assert len(manager.payloads) == 1


def test_creative_generation_fails_closed_without_identity_configuration(
    tmp_path: Path,
):
    runtime_dir = tmp_path / ".runtime"
    manager = RecordingJobManager()
    app = FastAPI()
    register_creative_routes(
        app, root=tmp_path, runtime_dir=runtime_dir, job_manager=manager
    )
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    topic = repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-unconfigured",
                "source_path": "rank.json",
                "captured_at": "2026-07-22",
                "title": "未配置身份服务",
                "description": "",
                "category": "都市",
                "rank_type": "热播",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"core": "conflict"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(
        topic["id"], breakdown["id"], "未配置项目", 1
    )

    response = TestClient(app).post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 3},
    )

    assert response.status_code == 401
    assert manager.payloads == []
