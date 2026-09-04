# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from identity.permission_catalog import DEFAULT_ROLE_PAGE_ROUTES, NAVIGATION_CATALOG
from identity.schema import Principal
from web.page_access_catalog import PAGE_ACCESS_CATALOG
from web.page_router import PAGE_TEMPLATES, _page_response
from web.page_routes import PAGE_ROUTES


ROOT = Path(__file__).resolve().parents[1]


class _DashboardNavigation:
    def items_for(self, principal: Principal) -> list:
        del principal
        return []

    def can_open(self, principal: Principal, route: str) -> bool:
        del principal, route
        return False

    def remember_route(self, principal: Principal, route: str) -> None:
        del principal, route


def test_screenwriter_dashboard_is_registered_with_narrow_api_access() -> None:
    route = next(item for item in PAGE_ROUTES if item[0] == "/writing/dashboard")
    access = next(
        item for item in PAGE_ACCESS_CATALOG
        if item["page_route"] == "/writing/dashboard"
    )

    assert route == ("/writing/dashboard", "page.writing", "工作概览")
    assert PAGE_TEMPLATES["/writing/dashboard"] == "writing/dashboard.html"
    assert access["api_routes"] == (
        ("GET", "/api/creative/projects"),
        ("GET", "/api/workflow/tasks"),
    )
    assert "/writing/dashboard" in DEFAULT_ROLE_PAGE_ROUTES["screenwriter"]


def test_writing_navigation_starts_with_dashboard() -> None:
    writing_root = next(
        item for item in NAVIGATION_CATALOG if item["id"] == "navigation:writing"
    )
    writing_items = [
        item for item in NAVIGATION_CATALOG
        if item.get("parent_id") == "navigation:writing"
    ]
    selection_dashboard = next(
        item for item in NAVIGATION_CATALOG
        if item["id"] == "navigation:selection:dashboard"
    )

    assert writing_root["route"] == "/writing/dashboard"
    assert [(item["label"], item["route"]) for item in writing_items] == [
        ("工作概览", "/writing/dashboard"),
        ("编剧项目", "/writing/projects"),
    ]
    assert selection_dashboard["label"] == "选品概览"


def test_screenwriter_dashboard_copy_excludes_selection_metrics() -> None:
    template = (ROOT / "templates/writing/dashboard.html").read_text(encoding="utf-8")
    script = (ROOT / "static/js/writing/dashboard.js").read_text(encoding="utf-8")

    for expected in ("参与项目", "待处理任务", "进行中任务", "已完成任务", "最近编剧任务"):
        assert expected in template
    for forbidden in ("候选选题", "选题库", "已采纳选题", "已采集评论", "已转写素材", "分析任务"):
        assert forbidden not in template
        assert forbidden not in script
    assert "/api/creative/projects?participating_only=true" in script
    assert "/api/workflow/tasks" in script
    assert "/api/analytics/overview" not in script
    assert "/api/creative/topics" not in script


def test_projects_api_can_limit_dashboard_results_to_active_memberships() -> None:
    source = (ROOT / "creative/api.py").read_text(encoding="utf-8")

    assert "participating_only: bool = False" in source
    assert "if participating_only:" in source
    assert "workflow_repository.is_active_project_member(" in source


def test_standalone_dashboards_do_not_boot_the_alpine_workspace_runtime() -> None:
    app = FastAPI()
    request = Request(
        {
            "type": "http", "method": "GET", "path": "/writing/dashboard",
            "headers": [], "query_string": b"", "server": ("test", 80),
            "client": ("test", 50000), "scheme": "http", "app": app,
            "path_params": {},
        }
    )
    principal = Principal(
        user_id="writer-1", username="writer", display_name="测试编剧",
        role_codes=("screenwriter",), permissions=frozenset({"page.writing"}),
        data_scope="project", session_id="session-1",
    )
    response = _page_response(
        Jinja2Templates(directory=str(ROOT / "templates")),
        _DashboardNavigation(), request, principal,
        page_title="工作概览", template_name="writing/dashboard.html",
    )
    body = response.body.decode("utf-8")

    assert 'data-workspace-page="writing-dashboard"' in body
    assert 'x-data="appData()"' not in body
    assert "/static/js/alpine.min.js" not in body
    assert "/static/js/common/tasks.js" not in body
