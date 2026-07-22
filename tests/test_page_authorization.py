# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.session_service import SessionService
from web.navigation_service import PAGE_ROUTES, NavigationService
from web.page_router import build_page_router


ROLE_CASES = (
    ("selection_operator", "/selection/ranks", {"选品工作台"}),
    ("creative_planner", "/planning/projects", {"创意策划"}),
    ("screenwriter", "/writing/projects", {"编剧工作台"}),
    ("project_owner", "/review/tasks", {"审批中心"}),
    (
        "system_admin",
        "/admin/users",
        {"选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"},
    ),
)
PROBE_ROUTES = {
    "选品工作台": "/selection/ranks",
    "创意策划": "/planning/projects",
    "编剧工作台": "/writing/projects",
    "审批中心": "/review/tasks",
    "系统管理": "/admin/users",
}
PAGE_PERMISSION_BY_GROUP = {
    "选品工作台": "page.selection",
    "创意策划": "page.planning",
    "编剧工作台": "page.writing",
    "审批中心": "page.review",
    "系统管理": "page.admin.delivery",
}


@pytest.fixture
def page_app(tmp_path: Path) -> tuple[FastAPI, IdentityRepository]:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    session_service = SessionService(repository, secure_cookie=False)
    permission_service = PermissionService(repository, session_service)
    app = FastAPI()
    app.state.identity_permission_service = permission_service
    app.include_router(
        build_identity_router(
            repository,
            session_service,
            permission_service=permission_service,
        )
    )
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parents[1] / "templates")
    )
    app.include_router(build_page_router(templates, NavigationService()))
    return app, repository


def _create_role_client(
    page_app: tuple[FastAPI, IdentityRepository],
    role_code: str,
    *,
    username: str | None = None,
    client: TestClient | None = None,
) -> TestClient:
    app, repository = page_app
    role = repository.get_role_by_code(role_code)
    assert role is not None
    username = username or role_code.replace("_", "-")
    password = "Role-Access-123!"
    user = repository.create_user(
        username, role["name"], PasswordService().hash(password)
    )
    repository.replace_user_roles(user["id"], [role["id"]])
    client = client or TestClient(app)
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return client


@pytest.fixture
def writer_client(page_app: tuple[FastAPI, IdentityRepository]) -> TestClient:
    return _create_role_client(page_app, "screenwriter")


@pytest.fixture
def admin_client(page_app: tuple[FastAPI, IdentityRepository]) -> TestClient:
    return _create_role_client(page_app, "system_admin")


@pytest.mark.parametrize(("role_code", "own_route", "visible_groups"), ROLE_CASES)
def test_default_roles_have_exact_navigation_and_page_visibility(
    page_app: tuple[FastAPI, IdentityRepository],
    role_code: str,
    own_route: str,
    visible_groups: set[str],
):
    client = _create_role_client(page_app, role_code)

    html = client.get(own_route).text

    for label in PROBE_ROUTES:
        assert (label in html) is (label in visible_groups)
    allowed_permissions = {
        PAGE_PERMISSION_BY_GROUP[label] for label in visible_groups
    }
    for route, permission, _ in PAGE_ROUTES:
        concrete_route = route.replace("{project_id}", "project-1").replace(
            "{episode_number}", "2"
        )
        response = client.get(concrete_route, follow_redirects=False)
        expected_status = 200 if permission in allowed_permissions else 403
        assert response.status_code == expected_status


def test_screenwriter_cannot_open_selection_page(writer_client: TestClient):
    response = writer_client.get("/selection/ranks", follow_redirects=False)
    assert response.status_code == 403
    assert "今日榜单" not in writer_client.get("/writing/projects").text


def test_system_admin_sees_all_navigation(admin_client: TestClient):
    html = admin_client.get("/admin/users").text
    for label in ("选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"):
        assert label in html


def test_root_uses_role_priority_then_last_authorized_page(
    page_app: tuple[FastAPI, IdentityRepository],
):
    client = _create_role_client(page_app, "selection_operator")
    assert client.get("/", follow_redirects=False).headers["location"] == (
        "/selection/dashboard"
    )

    assert client.get("/selection/topics").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == (
        "/selection/topics"
    )

    assert client.get("/tasks").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/tasks"

    assert client.get("/selection/topics").status_code == 200
    role = page_app[1].get_role_by_code("selection_operator")
    assert role is not None
    page_app[1].replace_role_permissions(role["id"], [])
    assert client.get("/", follow_redirects=False).headers["location"] == "/tasks"


def test_last_page_is_isolated_between_login_sessions(
    page_app: tuple[FastAPI, IdentityRepository],
):
    client = _create_role_client(
        page_app, "selection_operator", username="first-selector"
    )
    assert client.get("/selection/topics").status_code == 200
    csrf_token = client.get("/api/auth/me").json()["csrf_token"]
    assert client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
    ).status_code == 204

    _create_role_client(
        page_app,
        "selection_operator",
        username="second-selector",
        client=client,
    )

    assert client.get("/", follow_redirects=False).headers["location"] == (
        "/selection/dashboard"
    )


@pytest.mark.parametrize("route", ("/", "/tasks", "/writing/projects"))
def test_unauthenticated_page_redirects_to_login(page_app, route: str):
    app, _ = page_app
    response = TestClient(app).get(route, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_forbidden_page_uses_html_error_template(writer_client: TestClient):
    response = writer_client.get("/admin/users")

    assert response.status_code == 403
    assert "无权访问" in response.text
