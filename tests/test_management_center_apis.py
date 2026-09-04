# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_management_center_apis.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

# Copyright (c) 2025 relakkes@gmail.com

"""HTTP contracts for the system-admin navigation center."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.dependencies import current_principal
from identity.schema import Principal
from web.navigation_api import build_navigation_router


def _principal(*, admin: bool) -> Principal:
    return Principal(
        user_id="user:admin" if admin else "user:member",
        username="admin" if admin else "member",
        display_name="管理员" if admin else "成员",
        role_codes=("system_admin",) if admin else ("member",),
        permissions=frozenset({"*"}) if admin else frozenset(),
        data_scope="all" if admin else "task",
        session_id="session:test",
    )


class _AuditRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, **event) -> None:
        self.events.append(event)


class _NavigationRepository:
    def __init__(self) -> None:
        self.items = [{
            "id": "navigation:admin:users", "parent_id": "navigation:admin",
            "label": "用户管理", "icon": "bi-people", "route": "/admin/users",
            "required_permission": "*", "sort_order": 10, "is_enabled": True,
            "is_system": True, "hide_for_system_admin": False,
        }]

    def list_items(self):
        return [dict(item) for item in self.items]

    def update_presentation(self, item_id: str, **changes):
        for item in self.items:
            if item["id"] == item_id:
                item.update(changes)
                return dict(item)
        raise KeyError(item_id)


def _app(principal: Principal) -> tuple[FastAPI, _AuditRecorder]:
    app = FastAPI()
    audit = _AuditRecorder()
    app.state.audit_service = audit
    app.state.navigation_repository = _NavigationRepository()
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(build_navigation_router())
    return app, audit


def test_navigation_api_allows_only_presentation_updates_and_audits_them():
    app, audit = _app(_principal(admin=True))

    response = TestClient(app).patch(
        "/api/admin/navigation/navigation:admin:users",
        json={"label": "本机调度", "icon": "bi-clock", "sort_order": 9,
              "is_enabled": False, "route": "/should-not-change"},
    )

    assert response.status_code == 200
    assert response.json()["route"] == "/admin/users"
    assert response.json()["label"] == "本机调度"
    assert audit.events[-1]["action"] == "navigation.update"


def test_management_apis_reject_non_system_administrators():
    app, _ = _app(_principal(admin=False))
    client = TestClient(app)

    assert client.get("/api/admin/navigation").status_code == 403


def test_navigation_configuration_is_not_available_to_project_owners():
    app, _ = _app(
        Principal(
            user_id="user:owner",
            username="owner",
            display_name="负责人",
            role_codes=("project_owner",),
            permissions=frozenset(),
            data_scope="managed_projects",
            session_id="session:owner",
        )
    )

    assert TestClient(app).get("/api/admin/navigation").status_code == 403


def test_navigation_page_renders_safe_fields():
    root = Path(__file__).resolve().parents[1]
    navigation_js = (root / "static/js/admin/navigation.js").read_text(encoding="utf-8")
    assert "innerHTML" not in navigation_js


def test_management_pages_include_department_editor_audit_pagination_and_job_links():
    root = Path(__file__).resolve().parents[1]
    departments_html = (root / "templates/admin/departments.html").read_text(
        encoding="utf-8"
    )
    departments_js = (root / "static/js/admin/departments.js").read_text(
        encoding="utf-8"
    )
    audit_html = (root / "templates/admin/audit.html").read_text(encoding="utf-8")
    audit_js = (root / "static/js/admin/audit.js").read_text(encoding="utf-8")

    assert "department-tree" in departments_html
    assert 'id="department-submit"' in departments_html
    assert 'id="department-submit-label">新增<' in departments_html
    assert "/api/admin/users/" in departments_js
    assert "/departments/" in departments_js
    assert "setEditorMode" in departments_js
    assert "audit-target-type" in audit_html
    assert "audit-pagination" in audit_html
    assert "<th>操作类型</th>" in audit_html
    assert '<select class="form-select form-select-sm" id="audit-actor"' in audit_html
    assert '<select class="form-select form-select-sm" id="audit-project"' in audit_html
    assert "audit-actor-help" in audit_html
    assert "/static/js/admin/audit.js?v={{ asset_version }}&audit_ui=2" in audit_html
    assert "/api/admin/audit/" in audit_js
    assert "/api/admin/audit/filter-options" in audit_js
    assert "loadFilterOptions" in audit_js
    assert "project_scope_required" in audit_js
    assert "请选择一个负责项目" in audit_js


def test_dark_workspace_inputs_keep_audit_filters_legible_and_editable():
    root = Path(__file__).resolve().parents[1]
    audit_html = (root / "templates/admin/audit.html").read_text(encoding="utf-8")
    workspace_css = (root / "static/css/workspaces.css").read_text(encoding="utf-8")

    assert 'type="datetime-local"' in audit_html
    assert ".form-control::placeholder" in workspace_css
    assert '.form-control[type="datetime-local"]' in workspace_css
    assert "color-scheme: dark" in workspace_css
    assert ".form-text" in workspace_css


def test_audit_list_uses_consistent_second_precision_timestamp_display():
    root = Path(__file__).resolve().parents[1]
    audit_js = (root / "static/js/admin/audit.js").read_text(encoding="utf-8")

    assert "function formatAuditTimestamp" in audit_js
    assert "formatAuditTimestamp(event.created_at)" in audit_js


def test_user_directory_and_profile_page_include_contact_management_contracts():
    root = Path(__file__).resolve().parents[1]
    users_html = (root / "templates/admin/users.html").read_text(encoding="utf-8")
    users_js = (root / "static/js/admin/users.js").read_text(encoding="utf-8")
    profile_html = (root / "templates/profile.html").read_text(encoding="utf-8")
    profile_js = (root / "static/js/profile.js").read_text(encoding="utf-8")
    profile_css = (root / "static/css/profile.css").read_text(encoding="utf-8")
    page_router = (root / "web/page_router.py").read_text(encoding="utf-8")

    assert 'id="user-filters"' in users_html
    assert 'id="user-email"' in users_html
    assert 'id="export-users-csv"' in users_html
    assert "/api/admin/users/export" in users_js
    assert '"/profile"' in page_router
    assert '"profile.html"' in page_router
    assert 'id="profile-feedback"' in profile_html
    assert "profile-toast" in profile_html
    assert "showFeedback" in profile_js
    assert "保存成功" in profile_js
    assert "background: #171a24" in profile_css
    assert "color: #aeb8cd" in profile_css


def test_role_directory_includes_filter_export_and_result_summary_controls():
    root = Path(__file__).resolve().parents[1]
    roles_html = (root / "templates/admin/roles.html").read_text(encoding="utf-8")
    roles_js = (root / "static/js/admin/roles.js").read_text(encoding="utf-8")

    assert 'id="role-filters"' in roles_html
    assert 'id="role-filter-keyword"' in roles_html
    assert 'id="role-filter-status"' in roles_html
    assert 'id="role-total"' in roles_html
    assert 'id="export-roles-csv"' in roles_html
    assert 'id="export-roles-xlsx"' in roles_html
    assert "/api/admin/roles/export" in roles_js
    assert "exportRoles" in roles_js
