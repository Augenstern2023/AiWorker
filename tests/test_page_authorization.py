# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from identity.permission_catalog import NAVIGATION_CATALOG
from identity.dependencies import current_principal
from identity.schema import Principal
from web.navigation_service import NavigationService
from web.page_access_service import PageAccessService
from web import page_router
from web.page_router import build_page_router


class FakeNavigationRepository:
    def __init__(self) -> None:
        self.access_by_route: dict[str, set[str]] = {}
        self.disabled_routes: set[str] = set()

    def list_items(self) -> list[dict[str, object]]:
        items = []
        for raw_item in NAVIGATION_CATALOG:
            item = dict(raw_item)
            item.setdefault("parent_id", None)
            item.setdefault("icon", "")
            item.setdefault("is_enabled", True)
            item.setdefault("hide_for_system_admin", False)
            item["is_enabled"] = (
                item["is_enabled"] and item["route"] not in self.disabled_routes
            )
            items.append(item)
        return items

    def list_page_access_user_ids(self, route: str) -> set[str]:
        return set(self.access_by_route.get(route, set()))


class FakeIdentityPermissionService:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal
        self.session_service = SimpleNamespace(
            cookie_name="aiworker_session",
            resolve_session=lambda raw_token: None,
        )

    def principal_for_session(self, raw_token: str | None) -> Principal | None:
        return self.principal


def _principal(
    *,
    user_id: str = "user-1",
    roles: tuple[str, ...] = (),
    permissions: tuple[str, ...] = (),
) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        role_codes=roles,
        permissions=frozenset(permissions),
        data_scope="task",
        session_id=f"session-{user_id}",
    )


def test_asset_version_changes_when_a_static_file_changes(tmp_path: Path, monkeypatch):
    asset = tmp_path / "selection-topics.js"
    asset.write_text("first", encoding="utf-8")
    monkeypatch.setattr(page_router, "STATIC_ASSET_ROOT", tmp_path)
    os.utime(asset, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    first_version = page_router.static_asset_version()

    asset.write_text("second", encoding="utf-8")
    os.utime(asset, ns=(1_700_000_002_000_000_000, 1_700_000_002_000_000_000))

    assert page_router.static_asset_version() != first_version


@pytest.fixture
def page_app() -> tuple[FastAPI, FakeNavigationRepository]:
    repository = FakeNavigationRepository()
    page_access_service = PageAccessService(repository)
    navigation_service = NavigationService(
        repository.list_items(),
        page_access_service=page_access_service,
    )
    app = FastAPI()
    app.state.page_access_service = page_access_service
    app.state.identity_permission_service = FakeIdentityPermissionService(_principal())

    def principal_override() -> Principal:
        principal = app.state.identity_permission_service.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return principal

    app.dependency_overrides[current_principal] = principal_override
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parents[1] / "templates")
    )
    app.include_router(build_page_router(templates, navigation_service))
    return app, repository


def _client(
    page_app: tuple[FastAPI, FakeNavigationRepository], principal: Principal | None
) -> TestClient:
    app, _ = page_app
    app.state.identity_permission_service.principal = principal
    return TestClient(app)


def test_root_always_redirects_to_profile_even_after_visiting_an_allowed_page(page_app):
    principal = _principal(permissions=("page.selection",))
    page_app[1].access_by_route["/selection/topics"] = {principal.user_id}
    client = _client(page_app, principal)

    assert client.get("/selection/topics").status_code == 200
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"


def test_unlisted_page_is_hidden_and_direct_url_returns_403(page_app):
    principal = _principal(permissions=("page.selection", "*"))
    client = _client(page_app, principal)

    profile = client.get("/profile")
    forbidden = client.get("/selection/ranks")

    assert profile.status_code == 200
    assert 'href="/selection/ranks"' not in profile.text
    assert forbidden.status_code == 403
    assert "无权访问" in forbidden.text


def test_whitelist_update_changes_navigation_and_page_access_immediately(page_app):
    principal = _principal()
    client = _client(page_app, principal)

    page_app[1].access_by_route["/selection/ranks"] = {principal.user_id}
    assert 'href="/selection/ranks"' in client.get("/profile").text
    assert client.get("/selection/ranks").status_code == 200

    page_app[1].access_by_route["/selection/ranks"] = set()
    assert 'href="/selection/ranks"' not in client.get("/profile").text
    assert client.get("/selection/ranks").status_code == 403


def test_system_admin_role_code_bypasses_whitelist_but_wildcard_permission_does_not(
    page_app,
):
    wildcard = _client(page_app, _principal(permissions=("*",)))
    assert wildcard.get("/admin/users").status_code == 403

    administrator = _client(page_app, _principal(roles=("system_admin",)))
    assert administrator.get("/admin/users").status_code == 200
    assert 'href="/admin/users"' in administrator.get("/profile").text


def test_project_owner_does_not_have_navigation_configuration_privilege(page_app):
    owner = _client(page_app, _principal(roles=("project_owner",)))

    profile = owner.get("/profile")

    assert owner.get("/admin/navigation").status_code == 403
    assert 'href="/admin/navigation"' not in profile.text
    assert owner.get("/review/projects").status_code == 403
    assert 'href="/review/projects"' not in profile.text


def test_dynamic_legacy_route_uses_the_same_static_whitelist_mapping(page_app):
    principal = _principal()
    client = _client(page_app, principal)

    assert (
        client.get(
            "/planning/projects/project-1/directions", follow_redirects=False
        ).status_code
        == 403
    )

    page_app[1].access_by_route["/planning/projects"] = {principal.user_id}
    response = client.get(
        "/planning/projects/project-1/directions", follow_redirects=False
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/planning/projects/project-1/brief"


def test_managed_project_page_grant_allows_its_creative_brief_preview(page_app):
    principal = _principal()
    page_app[1].access_by_route["/review/projects"] = {principal.user_id}
    client = _client(page_app, principal)

    assert client.get("/planning/projects/project-1/brief").status_code == 200


def test_project_member_editor_renders_only_with_member_management_permission(
    page_app,
):
    viewer = _principal(roles=("project_owner",))
    page_app[1].access_by_route["/review/projects"] = {viewer.user_id}

    viewer_response = _client(page_app, viewer).get(
        "/review/projects/project-1/members"
    )
    manager = _principal(
        roles=("project_owner",), permissions=("project.member.manage",)
    )
    manager_response = _client(page_app, manager).get(
        "/review/projects/project-1/members"
    )

    assert viewer_response.status_code == 200
    assert 'data-can-manage-members="false"' in viewer_response.text
    assert 'id="project-member-form"' not in viewer_response.text
    assert manager_response.status_code == 200
    assert 'data-can-manage-members="true"' in manager_response.text
    assert 'id="project-member-form"' in manager_response.text
    assert '<option value="project_owner">项目负责人</option>' in manager_response.text
    assert '<option value="creative_planner">创意策划</option>' in manager_response.text
    assert '<option value="screenwriter">编剧</option>' in manager_response.text


def test_writer_can_open_read_only_planning_pages_from_a_project(page_app):
    principal = _principal(roles=("screenwriter",))
    page_app[1].access_by_route["/writing/projects"] = {principal.user_id}
    client = _client(page_app, principal)

    response = client.get("/planning/projects/project-1/brief")
    characters_response = client.get("/planning/projects/project-1/characters")

    assert response.status_code == 200
    assert 'href="/writing/projects">项目列表</a>' in response.text
    assert characters_response.status_code == 200


def test_project_brief_versions_permission_and_workspace_scripts(page_app):
    """A deployed permission fix must not be hidden by cached workspace assets."""

    principal = _principal(roles=("screenwriter",))
    page_app[1].access_by_route["/writing/projects"] = {principal.user_id}

    response = _client(page_app, principal).get(
        "/planning/projects/project-1/brief"
    )

    assert response.status_code == 200
    assert 'src="/static/js/common/api.js?v=' in response.text
    assert 'src="/static/js/common/creative.js?v=' in response.text
    assert 'src="/static/js/planning/brief.js?v=' in response.text


def test_managed_project_page_grant_keeps_project_preview_navigation_available(
    page_app,
):
    principal = _principal()
    page_app[1].access_by_route["/review/projects"] = {principal.user_id}
    client = _client(page_app, principal)

    response = client.get("/planning/projects", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/review/projects"
    for route in (
        "/planning/projects/project-1/characters",
        "/writing/projects/project-1/outlines",
        "/writing/projects/project-1/episodes/1/beats",
        "/writing/projects/project-1/episodes/1/scripts",
    ):
        assert client.get(route).status_code == 200


@pytest.mark.parametrize("route", ("/", "/tasks", "/writing/projects"))
def test_unauthenticated_page_redirects_to_login(page_app, route: str):
    response = _client(page_app, None).get(route, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_legacy_task_pages_are_not_opened_by_role_page_permissions(page_app):
    client = _client(page_app, _principal(permissions=("page.planning",)))

    assert client.get("/tasks").status_code == 403
    assert client.get("/planning/tasks", follow_redirects=False).status_code == 403
