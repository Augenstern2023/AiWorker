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
    ("project_owner", "/review/projects", {"项目管理", "团队管理"}),
    (
        "system_admin",
        "/admin/users",
        {"选品工作台", "创意策划", "编剧工作台", "项目管理", "系统管理"},
    ),
)
PROBE_ROUTES = {
    "选品工作台": "/selection/ranks",
    "创意策划": "/planning/projects",
    "编剧工作台": "/writing/projects",
    "项目管理": "/review/projects",
    "团队管理": "/review/users",
    "系统管理": "/admin/users",
}
PAGE_PERMISSION_BY_GROUP = {
    "选品工作台": "page.selection",
    "创意策划": "page.planning",
    "编剧工作台": "page.writing",
    "项目管理": "page.review",
    "团队管理": "page.team_management",
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

    if role_code == "project_owner":
        assert "项目管理" in html
        assert "审批中心" not in html
        return

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
        expected_status = (
            200
            if role_code == "system_admin" or permission in allowed_permissions
            else 403
        )
        assert response.status_code == expected_status


def test_screenwriter_cannot_open_selection_page(writer_client: TestClient):
    response = writer_client.get("/selection/ranks", follow_redirects=False)
    assert response.status_code == 403
    assert "今日榜单" not in writer_client.get("/writing/projects").text


def test_project_owner_can_batch_download_and_delete_data_files(
    page_app: tuple[FastAPI, IdentityRepository],
):
    client = _create_role_client(page_app, "project_owner")

    response = client.get("/admin/data")

    assert response.status_code == 200
    assert 'href="/admin/data"' in response.text
    assert "在线预览" in response.text
    assert "下载" in response.text
    assert "批量删除" in response.text
    assert "删除文件" in response.text


def test_system_admin_sees_all_navigation(admin_client: TestClient):
    html = admin_client.get("/admin/users").text
    for label in ("选品工作台", "创意策划", "编剧工作台", "项目管理", "系统管理"):
        assert label in html


def test_data_analytics_page_is_removed_from_admin_navigation(
    admin_client: TestClient,
):
    html = admin_client.get("/admin/users").text

    assert 'href="/admin/analytics"' not in html
    assert "数据分析" not in html
    assert admin_client.get("/admin/analytics").status_code == 404


def test_today_hits_and_topic_library_are_distinct_pages(
    page_app: tuple[FastAPI, IdentityRepository],
):
    client = _create_role_client(page_app, "selection_operator")

    hits = client.get("/selection/hits")
    library = client.get("/selection/topics")

    assert hits.status_code == 200
    assert '<h3 class="mb-0">今日爆款</h3>' in hits.text
    assert '<h3 class="mb-1">选题库</h3>' not in hits.text
    assert library.status_code == 200
    assert '<h3 class="mb-1">选题库</h3>' in library.text
    assert '<h3 class="mb-0">今日爆款</h3>' not in library.text
    assert 'href="/selection/hits"' in library.text
    assert 'href="/selection/topics"' in hits.text


def test_removed_directions_page_redirects_to_brief(
    page_app: tuple[FastAPI, IdentityRepository],
):
    client = _create_role_client(page_app, "creative_planner")

    response = client.get(
        "/planning/projects/project-1/directions", follow_redirects=False
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/planning/projects/project-1/brief"


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


def test_global_task_page_replaces_role_specific_task_pages(
    page_app: tuple[FastAPI, IdentityRepository],
):
    planner = _create_role_client(page_app, "creative_planner")
    writer = _create_role_client(page_app, "screenwriter")

    task_page = planner.get("/tasks")

    assert task_page.status_code == 200
    assert 'id="my-submission-list"' not in task_page.text
    assert 'id="my-project-list"' in task_page.text
    assert '/static/js/common/my_tasks.js' in task_page.text
    assert 'href="/planning/tasks"' not in task_page.text
    assert planner.get("/planning/tasks", follow_redirects=False).headers["location"] == "/tasks"
    assert writer.get("/writing/tasks", follow_redirects=False).headers["location"] == "/tasks"


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


def test_tasks_page_uses_the_authenticated_workspace_layout(
    writer_client: TestClient,
):
    html = writer_client.get("/tasks").text

    assert '/static/css/workspaces.css' in html
    assert 'data-workspace-page="tasks"' in html
    assert 'class="main-content"' in html
    assert '/static/js/common/tasks.js' in html


def test_delivery_only_permission_does_not_grant_system_admin_delivery_page(
    page_app: tuple[FastAPI, IdentityRepository],
):
    repository = page_app[1]
    role = repository.create_role("delivery_only", "交付专员", "仅导出批准成果")
    repository.replace_role_permissions(
        role["id"], [("page.admin.delivery", "all")]
    )
    client = _create_role_client(page_app, "delivery_only")

    delivery = client.get("/admin/deliveries")

    assert delivery.status_code == 403
    assert "成果交付" not in delivery.text
    for route in (
        "/admin/users",
        "/admin/roles",
        "/admin/config",
        "/admin/data",
        "/admin/jobs",
        "/admin/audit",
    ):
        assert client.get(route).status_code == 403
    assert client.get("/admin/analytics").status_code == 404
    for label in (
        "用户管理",
        "角色权限",
        "系统配置",
        "数据管理",
        "数据分析",
        "机器任务",
        "审计日志",
    ):
        assert label not in delivery.text
