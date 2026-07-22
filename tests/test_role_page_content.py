# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


ROLE_TEMPLATES = (
    "selection/dashboard.html",
    "selection/ranks.html",
    "selection/topics.html",
    "selection/dramas.html",
    "selection/breakdowns.html",
    "planning/projects.html",
    "planning/directions.html",
    "planning/brief.html",
    "planning/characters.html",
    "writing/projects.html",
    "writing/outlines.html",
    "writing/beats.html",
    "review/projects.html",
    "review/tasks.html",
    "review/approvals.html",
    "review/versions.html",
    "admin/users.html",
    "admin/roles.html",
    "admin/config.html",
    "admin/data.html",
    "admin/analytics.html",
    "admin/jobs.html",
    "admin/deliveries.html",
    "admin/audit.html",
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_role_templates_are_standalone_pages_with_external_scripts():
    for relative_path in ROLE_TEMPLATES:
        html = _read(f"templates/{relative_path}")
        assert '{% extends "layouts/base.html" %}' in html
        assert "currentTab" not in html
        assert "<script>" not in html


def test_role_templates_do_not_share_unrelated_workspaces():
    selection = _read("templates/selection/topics.html")
    writing = _read("templates/writing/beats.html")
    admin = _read("templates/admin/users.html")

    assert "今日爆款" in selection
    assert "单集节拍表" not in selection
    assert "用户管理" not in selection
    assert "单集节拍表" in writing
    assert "用户管理" not in writing
    assert "用户管理" in admin
    assert "今日爆款" not in admin


def test_each_operational_domain_loads_only_external_javascript():
    expected_scripts = {
        "templates/selection/ranks.html": "/static/js/selection/ranks.js",
        "templates/selection/topics.html": "/static/js/selection/topics.js",
        "templates/selection/dramas.html": "/static/js/selection/dramas.js",
        "templates/selection/breakdowns.html": "/static/js/selection/breakdowns.js",
        "templates/planning/projects.html": "/static/js/planning/projects.js",
        "templates/planning/brief.html": "/static/js/planning/brief.js",
        "templates/planning/characters.html": "/static/js/planning/characters.js",
        "templates/writing/outlines.html": "/static/js/writing/outlines.js",
        "templates/writing/beats.html": "/static/js/writing/beats.js",
        "templates/review/tasks.html": "/static/js/review/tasks.js",
        "templates/review/approvals.html": "/static/js/review/approvals.js",
        "templates/admin/users.html": "/static/js/admin/users.js",
        "templates/admin/roles.html": "/static/js/admin/roles.js",
        "templates/admin/jobs.html": "/static/js/admin/jobs.js",
        "templates/admin/deliveries.html": "/static/js/admin/deliveries.js",
        "templates/admin/audit.html": "/static/js/admin/audit.js",
    }
    for template, script in expected_scripts.items():
        assert script in _read(template)


def test_common_api_owns_authenticated_request_behavior():
    source = _read("static/js/common/api.js")

    assert "X-CSRF-Token" in source
    assert "csrf-token" in source
    assert "response.status === 401" in source
    assert "window.location.assign('/login')" in source
    assert "response.status === 403" in source
    assert "无权执行此操作" in source


def test_role_scripts_do_not_depend_on_legacy_current_tab():
    role_scripts = (ROOT / "static/js").glob("*/*.js")
    for script in role_scripts:
        if script.parent.name == "common":
            continue
        assert "currentTab" not in script.read_text(encoding="utf-8")


def test_legacy_index_is_only_a_compatibility_redirect():
    html = _read("templates/index.html")

    assert "currentTab ===" not in html
    assert "创作项目" not in html
    assert "window.location.replace('/')" in html
