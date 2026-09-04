# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import WebGUI

from core.job import CrawlJob
from creative.api import (
    build_creative_router,
    creative_api_authorization,
    uses_project_member_stage_authorization,
)
from creative.delivery import DeliveryService, build_delivery_router
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.dependencies import current_principal, require_permission
from identity.permission_service import PermissionService
from identity.schema import Principal
from identity.session_service import SessionContext
from runtime.job_state import JobState
from WebGUI import (
    _assert_creator_has_live_job_scope,
    _can_read_job,
    _require_permission,
    install_security_middleware,
    project_member_stage_api_path,
)
from web.page_access_service import PageAccessService
from workflow.repository import WorkflowRepository


class MutablePageAccessRepository:
    def __init__(self) -> None:
        self.user_ids: set[str] = set()
        self.overrides: dict[tuple[str, str], bool] = {}
        self.role_routes: dict[str, set[str]] = {}
        self.items = [{"route": "/admin/users", "is_enabled": True}]

    def list_items(self) -> list[dict[str, Any]]:
        return list(self.items)

    def list_page_access_user_ids(self, route: str) -> set[str]:
        return set(self.user_ids)

    def get_page_access_override(self, user_id: str, route: str) -> bool | None:
        return self.overrides.get((user_id, route))

    def list_role_page_access_routes(self, role_codes: tuple[str, ...]) -> set[str]:
        return set().union(
            *(self.role_routes.get(code, set()) for code in role_codes)
        ) if role_codes else set()


class DeniedProjectStagePageAccess:
    @staticmethod
    def can_call_page_api(
        principal: Principal, method: str, path: str
    ) -> bool | None:
        del principal
        if method == "POST" and path == (
            "/api/creative/projects/8/blueprints/generate"
        ):
            return False
        return None


class FakeSessionService:
    cookie_name = "aiworker_session"

    def __init__(self, principal: Principal) -> None:
        self.repository = object()
        self.principal = principal

    def resolve_session(self, raw_token: str | None) -> SessionContext | None:
        if raw_token != "valid-session":
            return None
        return SessionContext(principal=self.principal, csrf_token="valid-csrf")

    @staticmethod
    def verify_csrf(raw_token: str, csrf_token: str | None) -> bool:
        return raw_token == "valid-session" and csrf_token == "valid-csrf"


def _session_client(app: FastAPI) -> TestClient:
    client = TestClient(app)
    client.cookies.set("aiworker_session", "valid-session")
    return client


def _secured_identity_app(
    *, permissions: frozenset[str] = frozenset(), roles: tuple[str, ...] = ()
) -> tuple[FastAPI, MutablePageAccessRepository, str]:
    user_id = "page-user"
    principal = Principal(
        user_id=user_id,
        username=user_id,
        display_name="Page User",
        role_codes=roles,
        permissions=permissions,
        data_scope="all" if "*" in permissions else "task",
        session_id="page-session",
    )
    sessions = FakeSessionService(principal)
    permission_service = object.__new__(PermissionService)
    permission_service.repository = sessions.repository  # type: ignore[assignment]
    permission_service.session_service = sessions  # type: ignore[assignment]
    permission_service.principal_for_session = (  # type: ignore[method-assign]
        lambda raw_token: principal if raw_token == "valid-session" else None
    )
    page_repository = MutablePageAccessRepository()
    page_access_service = PageAccessService(
        page_repository,
        catalog=(
            {
                "page_route": "/admin/users",
                "api_routes": (
                    ("GET", "/api/admin/users"),
                    ("POST", "/api/admin/users"),
                    ("GET", "/api/admin/users/scoped"),
                    ("GET", "/api/admin/users/manual"),
                    ("GET", "/api/admin/users/{user_id}/resource"),
                ),
            },
        ),
    )
    app = FastAPI()

    @app.get("/api/admin/users")
    def list_users(
        _: Principal = Depends(require_permission("*")),
    ) -> list[dict[str, str]]:
        return [{"id": "managed-user"}]

    @app.post("/api/admin/users", status_code=201)
    def create_user(
        _: Principal = Depends(require_permission("*")),
    ) -> dict[str, str]:
        return {"id": "managed-user"}

    @app.get("/api/admin/users/scoped")
    def scoped_user_data(
        _: Principal = Depends(require_permission("business.user.scope")),
    ) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/admin/users/manual")
    def manually_scoped_user_data(
        principal: Principal = Depends(current_principal),
        request: Request = None,
    ) -> dict[str, bool]:
        _require_permission(principal, "business.user.scope", request)
        return {"ok": True}

    @app.get("/api/admin/users/{user_id}/resource")
    def user_resource(
        user_id: str,
        _: Principal = Depends(
            require_permission(
                "business.user.scope",
                lambda service, principal, request: request.path_params["user_id"]
                == "allowed",
            )
        ),
    ) -> dict[str, str]:
        return {"user_id": user_id}

    @app.get("/api/unassigned")
    def unassigned_api(
        _: Principal = Depends(require_permission("*")),
    ) -> dict[str, bool]:
        return {"ok": True}

    app.state.identity_permission_service = permission_service
    app.state.page_access_service = page_access_service
    install_security_middleware(app, sessions)
    return app, page_repository, user_id


def test_page_grant_enables_bound_user_management_apis_and_revokes_immediately(
) -> None:
    app, page_repository, user_id = _secured_identity_app()
    client = _session_client(app)

    assert client.get("/api/admin/users").status_code == 403

    page_repository.user_ids.add(user_id)
    allowed = client.get("/api/admin/users")
    assert allowed.status_code == 200, allowed.text

    missing_csrf = client.post(
        "/api/admin/users",
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json() == {"detail": "CSRF 校验失败"}

    created = client.post(
        "/api/admin/users",
        headers={"X-CSRF-Token": "valid-csrf"},
    )
    assert created.status_code == 201, created.text

    page_repository.user_ids.clear()
    assert client.get("/api/admin/users").status_code == 403


def test_delivery_page_grant_bypasses_creative_generated_resource_permission() -> None:
    principal = Principal(
        user_id="delivery-user",
        username="delivery-user",
        display_name="Delivery User",
        role_codes=("project_owner",),
        permissions=frozenset(),
        data_scope="task",
        session_id="delivery-session",
    )
    page_repository = MutablePageAccessRepository()
    page_repository.items = [{"route": "/admin/deliveries", "is_enabled": True}]
    page_repository.role_routes["project_owner"] = {"/admin/deliveries"}
    page_access_service = PageAccessService(
        page_repository,
        catalog=(
            {
                "page_route": "/admin/deliveries",
                "api_routes": (("GET", "/api/creative/generated-resources"),),
            },
        ),
    )
    app = FastAPI()
    app.state.page_access_service = page_access_service
    request = Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/creative/generated-resources",
        "headers": [],
        "app": app,
    })

    assert creative_api_authorization(request, principal) is principal


def test_original_project_create_path_uses_project_create_permission() -> None:
    principal = Principal(
        user_id="original-creator",
        username="original-creator",
        display_name="Original Creator",
        role_codes=(),
        permissions=frozenset({"project.create"}),
        data_scope="task",
        session_id="original-creator-session",
    )
    app = FastAPI()
    app.state.page_access_service = None
    request = Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/creative/projects/original",
        "headers": [],
        "app": app,
    })

    assert creative_api_authorization(request, principal) is principal


def test_page_denial_overrides_legacy_wildcard_permission() -> None:
    app, _, _ = _secured_identity_app(permissions=frozenset({"*"}))
    client = _session_client(app)

    response = client.get("/api/admin/users")

    assert response.status_code == 403
    assert response.json() == {"detail": "没有执行此操作的权限"}


def test_project_member_stage_routes_defer_outer_page_denial_to_resource_scope() -> None:
    paths = (
        "/api/creative/projects/8/blueprints/generate",
        "/api/creative/projects/8/characters/generate",
        "/api/creative/projects/8/outlines/generate",
        "/api/creative/projects/8/outlines/batch-1/overview/generate",
        "/api/creative/projects/8/episodes/1/beats/generate",
        "/api/creative/projects/8/episodes/1/scripts/generate",
    )

    for path in paths:
        assert project_member_stage_api_path(path, "POST") is True
        assert uses_project_member_stage_authorization(
            path.removeprefix("/api/creative"), "POST"
        ) is True

    assert project_member_stage_api_path(
        "/api/creative/projects/8", "DELETE"
    ) is False
    assert project_member_stage_api_path(
        "/api/creative/projects/8/outlines/batch-1/overview", "PATCH"
    ) is True
    assert uses_project_member_stage_authorization(
        "/projects/8/outlines/batch-1/overview", "PATCH"
    ) is True


def test_outer_middleware_allows_project_stage_request_to_reach_resource_scope() -> None:
    principal = Principal(
        user_id="project-member",
        username="project-member",
        display_name="Project Member",
        role_codes=("screenwriter",),
        permissions=frozenset({"blueprint.generate"}),
        data_scope="project",
        session_id="project-member-session",
    )
    sessions = FakeSessionService(principal)
    permission_service = object.__new__(PermissionService)
    permission_service.repository = sessions.repository  # type: ignore[assignment]
    permission_service.session_service = sessions  # type: ignore[assignment]
    permission_service.principal_for_session = (  # type: ignore[method-assign]
        lambda raw_token: principal if raw_token == "valid-session" else None
    )
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.page_access_service = DeniedProjectStagePageAccess()

    @app.post("/api/creative/projects/{project_id}/blueprints/generate")
    def generate_blueprint(project_id: int) -> dict[str, int]:
        return {"project_id": project_id}

    install_security_middleware(app, sessions)
    client = _session_client(app)

    response = client.post(
        "/api/creative/projects/8/blueprints/generate",
        headers={"X-CSRF-Token": "valid-csrf"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"project_id": 8}


def test_role_page_grant_and_account_override_control_bound_api_immediately() -> None:
    app, page_repository, user_id = _secured_identity_app(roles=("selection_operator",))
    page_repository.role_routes["selection_operator"] = {"/admin/users"}
    client = _session_client(app)

    assert client.get("/api/admin/users").status_code == 200

    page_repository.overrides[(user_id, "/admin/users")] = False
    assert client.get("/api/admin/users").status_code == 403

    page_repository.overrides[(user_id, "/admin/users")] = True
    assert client.get("/api/admin/users").status_code == 200


def test_unassigned_api_keeps_legacy_permission_check_after_page_grant(
) -> None:
    app, page_repository, user_id = _secured_identity_app()
    page_repository.user_ids.add(user_id)
    client = _session_client(app)

    response = client.get("/api/unassigned")

    assert response.status_code == 403
    assert response.json() == {"detail": "没有执行此操作的权限"}


def test_page_grant_replaces_endpoint_permission_for_bound_page_api() -> None:
    app, page_repository, user_id = _secured_identity_app()
    page_repository.user_ids.add(user_id)
    client = _session_client(app)

    response = client.get("/api/admin/users/scoped")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


def test_page_grant_replaces_manual_endpoint_permission_check() -> None:
    app, page_repository, user_id = _secured_identity_app()
    page_repository.user_ids.add(user_id)
    client = _session_client(app)

    response = client.get("/api/admin/users/manual")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}


def test_page_grant_keeps_bound_api_resource_scope_check() -> None:
    app, page_repository, user_id = _secured_identity_app()
    page_repository.user_ids.add(user_id)
    client = _session_client(app)

    allowed = client.get("/api/admin/users/allowed/resource")
    denied = client.get("/api/admin/users/denied/resource")

    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问此数据的权限"}


def _bound_page_request(
    principal: Principal,
    *,
    page_route: str,
    method: str,
    api_path: str,
) -> Request:
    repository = MutablePageAccessRepository()
    repository.items = [{"route": page_route, "is_enabled": True}]
    repository.user_ids.add(principal.user_id)
    app = FastAPI()
    app.state.page_access_service = PageAccessService(
        repository,
        catalog=(
            {
                "page_route": page_route,
                "api_routes": ((method, api_path),),
            },
        ),
    )
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "http",
            "path": api_path,
            "headers": [],
            "app": app,
        }
    )


def test_page_grant_replaces_job_read_permission_but_keeps_ownership_scope() -> None:
    principal = Principal(
        user_id="job-owner",
        username="job-owner",
        display_name="Job Owner",
        role_codes=(),
        permissions=frozenset(),
        data_scope="task",
        session_id="job-owner-session",
    )
    request = _bound_page_request(
        principal,
        page_route="/admin/jobs",
        method="GET",
        api_path="/api/jobs/job-1",
    )

    assert (
        _can_read_job(
            {"job_id": "job-1", "created_by_user_id": principal.user_id},
            principal,
            request,
        )
        is True
    )
    assert (
        _can_read_job(
            {"job_id": "job-2", "created_by_user_id": "another-user"},
            principal,
            request,
        )
        is False
    )


def test_page_grant_replaces_job_control_permissions_but_keeps_creator_scope() -> None:
    principal = Principal(
        user_id="job-owner",
        username="job-owner",
        display_name="Job Owner",
        role_codes=(),
        permissions=frozenset(),
        data_scope="task",
        session_id="job-owner-session",
    )
    request = _bound_page_request(
        principal,
        page_route="/admin/jobs",
        method="POST",
        api_path="/api/jobs/job-1/pause",
    )

    _assert_creator_has_live_job_scope(
        {"job_id": "job-1", "created_by_user_id": principal.user_id},
        principal,
        request,
    )
    with pytest.raises(HTTPException, match="只有机器任务创建人"):
        _assert_creator_has_live_job_scope(
            {"job_id": "job-2", "created_by_user_id": "another-user"},
            principal,
            request,
        )


@pytest.mark.asyncio
async def test_admin_jobs_page_grant_replaces_delete_system_admin_gate(
    monkeypatch,
) -> None:
    principal = Principal(
        user_id="jobs-page-user",
        username="jobs-page-user",
        display_name="Jobs Page User",
        role_codes=(),
        permissions=frozenset(),
        data_scope="task",
        session_id="jobs-page-session",
    )
    request = _bound_page_request(
        principal,
        page_route="/admin/jobs",
        method="DELETE",
        api_path="/api/jobs/job-1",
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        WebGUI.job_manager,
        "get_job",
        lambda job_id: {"job_id": job_id, "project_id": None},
    )
    monkeypatch.setattr(
        WebGUI.job_manager,
        "delete_job",
        lambda job_id, force_missing: deleted.append(job_id),
    )
    monkeypatch.setattr(WebGUI.audit_service, "record", lambda **kwargs: None)

    response = await WebGUI.delete_job("job-1", request, principal)

    assert response == {"status": "success", "message": "job deleted"}
    assert deleted == ["job-1"]


def test_delivery_service_rejects_non_admin_calls_without_page_authorization() -> None:
    service = object.__new__(DeliveryService)
    principal = Principal(
        user_id="page-user",
        username="page-user",
        display_name="Page User",
        role_codes=(),
        permissions=frozenset(),
        data_scope="task",
        session_id="page-session",
    )

    with pytest.raises(PermissionError, match="page authorization"):
        service.create(
            project_id=1,
            stage="brief",
            resource_id="brief-1",
            format="md",
            actor=principal,
        )


def test_delivery_page_filters_records_to_the_principal_project_scope() -> None:
    principal = Principal(
        user_id="delivery-user",
        username="delivery-user",
        display_name="Delivery User",
        role_codes=(),
        permissions=frozenset(),
        data_scope="project",
        session_id="delivery-session",
    )
    sessions = FakeSessionService(principal)
    permission_service = object.__new__(PermissionService)
    permission_service.repository = sessions.repository  # type: ignore[assignment]
    permission_service.session_service = sessions  # type: ignore[assignment]
    permission_service.principal_for_session = (  # type: ignore[method-assign]
        lambda raw_token: principal if raw_token == "valid-session" else None
    )
    permission_service.can_access_assigned_project = (  # type: ignore[method-assign]
        lambda actor, project_id: project_id == "allowed-project"
    )
    permission_service.can_access_managed_project = (  # type: ignore[method-assign]
        lambda actor, project_id: False
    )

    class DeliveryPageRepository:
        def list_items(self):
            return [{"route": "/admin/deliveries", "is_enabled": True}]

        def list_page_access_user_ids(self, route: str):
            assert route == "/admin/deliveries"
            return {principal.user_id}

    class FakeDeliveryService:
        records = [
            {"id": "allowed", "project_id": "allowed-project"},
            {"id": "forbidden", "project_id": "other-project"},
        ]

        def list(self, *, page_authorized=False, project_authorizer=None):
            del page_authorized
            if project_authorizer is None:
                return list(self.records)
            return [
                record
                for record in self.records
                if project_authorizer(record["project_id"])
            ]

    page_access_service = PageAccessService(
        DeliveryPageRepository(),
        catalog=(
            {
                "page_route": "/admin/deliveries",
                "api_routes": (("GET", "/api/admin/deliveries"),),
            },
        ),
    )
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.state.page_access_service = page_access_service
    app.include_router(build_delivery_router(FakeDeliveryService()))  # type: ignore[arg-type]
    install_security_middleware(app, sessions)
    client = _session_client(app)

    response = client.get("/api/admin/deliveries")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["deliveries"]] == ["allowed"]


def test_global_project_owner_generates_for_project_without_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / ".runtime"
    repository = CreativeRepository(runtime_dir / "creative" / "creative.db")
    topic = repository.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "owner-generation",
                "source_path": "rank.json",
                "captured_at": "2026-07-29",
                "title": "Global owner project",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "adopt",
                "evidence": [],
            }
        ]
    )[0]
    repository.update_topic(topic["id"], "adopted")
    breakdown = repository.create_breakdown(topic["id"], {"hook": "conflict"}, {}, [])
    repository.lock_breakdown(breakdown["id"])
    project = repository.create_project(topic["id"], breakdown["id"], "Owner project", 1)

    principal = Principal(
        user_id="global-owner",
        username="global-owner",
        display_name="Global Owner",
        role_codes=("project_owner",),
        permissions=frozenset({"blueprint.generate", "job.create.creative"}),
        data_scope="managed_projects",
        session_id="owner-session",
    )
    sessions = FakeSessionService(principal)
    permission_service = object.__new__(PermissionService)
    permission_service.repository = sessions.repository  # type: ignore[assignment]
    permission_service.session_service = sessions  # type: ignore[assignment]
    permission_service.principal_for_session = (  # type: ignore[method-assign]
        lambda raw_token: principal if raw_token == "valid-session" else None
    )

    workflow = object.__new__(WorkflowRepository)
    workflow.list_project_members = lambda project_id: []  # type: ignore[method-assign]
    workflow.link_task_job = lambda task_id, job_id: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        "creative.api.TransitionService.migrate_pending_creative_approvals",
        lambda self: [],
    )
    monkeypatch.setattr(
        CreativeWorkflowAdapter,
        "assert_task_for_creation",
        lambda self, task_id, resource_type, actor, **kwargs: None,
    )
    monkeypatch.setattr(
        CreativeWorkflowAdapter,
        "reconcile_project",
        lambda self, project_id: None,
    )

    class RecordingJobManager:
        def __init__(self) -> None:
            self.payloads: list[dict[str, Any]] = []

        def create_job(self, payload: dict[str, Any]) -> JobState:
            recorded = dict(payload)
            recorded["job_id"] = "global-owner-job"
            self.payloads.append(recorded)
            return JobState(job=CrawlJob.from_dict(recorded), status="running")

    manager = RecordingJobManager()
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.include_router(
        build_creative_router(
            root=tmp_path,
            runtime_dir=runtime_dir,
            job_manager=manager,
            workflow_repository=workflow,
        )
    )
    client = _session_client(app)

    response = client.post(
        f"/api/creative/projects/{project['id']}/blueprints/generate",
        json={"count": 3, "workflow_task_id": "owner-task"},
    )

    assert response.status_code == 200, response.text
    assert manager.payloads[0]["extra"]["project_id"] == project["id"]
