# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


ROLE_TEMPLATES = (
    "selection/dashboard.html",
    "selection/hits.html",
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
    hits = _read("templates/common/workspace/selection_hits.html")
    selection = _read("templates/common/workspace/selection_topics.html")
    writing = _read("templates/writing/beats.html")
    admin = _read("templates/admin/users.html")

    assert '<h3 class="mb-0">今日爆款</h3>' in hits
    assert '<h3 class="mb-1">选题库</h3>' not in hits
    assert '<h3 class="mb-1">选题库</h3>' in selection
    assert '<h3 class="mb-0">今日爆款</h3>' not in selection
    assert "单集节拍表" not in selection
    assert "用户管理" not in selection
    assert "单集节拍表" in writing
    assert "用户管理" not in writing
    assert "用户管理" in admin
    assert "今日爆款" not in admin


def test_each_operational_domain_loads_only_external_javascript():
    expected_scripts = {
        "templates/selection/hits.html": "/static/js/selection/topics.js",
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


def test_workspace_sidebar_navigation_can_scroll_on_short_viewports():
    css = _read("static/css/workspaces.css")

    assert re.search(r"\.sidebar\s+nav\s*\{[^}]*overflow-y:\s*auto", css, re.S)
    assert re.search(r"\.sidebar\s+nav\s*\{[^}]*flex-wrap:\s*nowrap", css, re.S)


def test_legacy_index_is_only_a_compatibility_redirect():
    html = _read("templates/index.html")

    assert "currentTab ===" not in html
    assert "创作项目" not in html
    assert "window.location.replace('/')" in html


def test_selection_dashboard_loads_every_module_used_by_its_initializer():
    dashboard = _read("templates/selection/dashboard.html")
    common = _read("static/js/common/tasks.js")

    assert "/static/js/selection/topics.js" in dashboard
    assert "/static/js/selection/ranks.js" not in dashboard
    assert "if (this.page === 'selection-ranks')" in common
    assert "this.page === 'selection-ranks' || this.page === 'selection-dashboard'" not in common


def test_planning_projects_use_real_stage_routes():
    project_list = _read("templates/common/workspace/project_list.html")
    creative = _read("static/js/common/creative.js")
    directions = _read("templates/planning/directions.html")
    brief = _read("templates/planning/brief.html")

    assert "`/planning/projects/${project.id}/directions`" in project_list
    assert "`/planning/projects/${data.id}/directions`" in creative
    assert 'href="/planning/projects/{{ project_id }}/brief"' in directions
    assert 'href="/planning/projects/{{ project_id }}/characters"' in brief


def test_writing_outlines_link_each_episode_to_beats_route():
    outlines = _read("templates/common/workspace/project_outlines.html")
    creative = _read("static/js/common/creative.js")

    assert (
        "`/writing/projects/${creativeWorkspace.project.id}/episodes/"
        "${item.episode_number}/beats`"
    ) in outlines
    assert "creativeWorkspace.step = 'beats'" not in creative


def test_selection_drama_page_cannot_edit_admin_credentials():
    selection = _read("templates/common/workspace/selection_dramas.html")
    selection_script = _read("static/js/selection/dramas.js")
    admin = _read("templates/admin/config.html")
    admin_script = _read("static/js/admin/workspace.js")

    for forbidden in (
        "配置中心地址",
        "激活码",
        "config_url",
        "activationCode",
        "saveDramaSettings",
        "testDramaInterface",
    ):
        assert forbidden not in selection
        assert forbidden not in selection_script
    assert "配置中心地址" in admin
    assert "activationCode" in admin
    assert "saveDramaSettings" in admin_script
    assert "testDramaInterface" in admin_script


def test_admin_identity_pages_expose_complete_management_flows():
    users_html = _read("templates/admin/users.html")
    users_js = _read("static/js/admin/users.js")
    roles_html = _read("templates/admin/roles.html")
    roles_js = _read("static/js/admin/roles.js")

    assert 'id="user-editor-modal"' in users_html
    assert 'id="user-role-select"' in users_html
    assert "/api/admin/users/${user.id}/reset-password" in users_js
    assert "method: 'PATCH'" in users_js
    assert "role_ids" in users_js
    assert "toggleUserStatus" in users_js

    assert 'id="role-editor-modal"' in roles_html
    assert 'id="permission-editor"' in roles_html
    assert "/api/admin/permissions" in roles_js
    assert "/api/admin/roles/${role.id}/clone" in roles_js
    assert "permissions" in roles_js
    assert "toggleRoleStatus" in roles_js


def test_admin_delivery_page_uses_approved_delivery_registry():
    html = _read("templates/admin/deliveries.html")
    source = _read("static/js/admin/deliveries.js")

    assert 'id="delivery-create-form"' in html
    assert 'id="delivery-submission"' in html
    assert 'id="delivery-history"' in html
    assert "/api/admin/deliveries" in source
    assert "/download`" in source
    assert "method: 'POST'" in source
    assert "/api/creative/projects/" not in html


def test_base_layout_globally_surfaces_permission_denials_without_rejection():
    base = _read("templates/layouts/base.html")
    session = _read("static/js/common/session.js")
    api = _read("static/js/common/api.js")

    assert 'id="permission-alert"' in base
    assert 'id="permission-alert-message"' in base
    assert "aiworker:permission-denied" in session
    assert "permissionAlert.classList.remove('d-none')" in session
    forbidden_branch = api.split("if (response.status === 403)", 1)[1].split(
        "return response", 1
    )[0]
    assert "window.dispatchEvent" in forbidden_branch
    assert "throw" not in forbidden_branch


def test_selection_drama_page_only_references_loaded_methods():
    page = _read("templates/selection/dramas.html")
    html = _read("templates/common/workspace/selection_dramas.html")
    if 'include "common/workspace/selection_modals.html"' in page:
        html += _read("templates/common/workspace/selection_modals.html")
    source = _read("static/js/common/tasks.js") + _read(
        "static/js/selection/dramas.js"
    )
    calls = set(
        re.findall(r"(?<![.\w])([A-Za-z_$][A-Za-z0-9_$]*)\(", html)
    )
    calls.update(
        re.findall(
            r'@(?:click|change|input|keydown\.enter)="([A-Za-z_$][A-Za-z0-9_$]*)"',
            html,
        )
    )
    definitions = set(
        re.findall(
            r"^\s*(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
            source,
            re.MULTILINE,
        )
    )

    assert "selection_modals.html" not in page
    assert calls - definitions - {"Number", "calc"} == set()


def test_selection_download_modal_gets_byte_formatter_from_loaded_module():
    modal = _read("templates/common/workspace/selection_modals.html")
    breakdowns = _read("static/js/selection/breakdowns.js")
    dramas = _read("static/js/selection/dramas.js")

    assert "formatDramaBytes(" in modal
    assert "formatDramaBytes(value)" in breakdowns
    assert "formatDramaBytes(value)" not in dramas


def test_creative_workflow_ui_submits_and_reviews_without_direct_locks():
    creative = _read("static/js/common/creative.js")
    breakdowns = _read("static/js/selection/breakdowns.js")
    approvals = _read("static/js/review/approvals.js")
    workspace = "\n".join(
        _read(f"templates/common/workspace/{name}")
        for name in (
            "breakdown_modals.html",
            "project_blueprints.html",
            "project_characters.html",
            "project_outlines.html",
            "project_beats.html",
        )
    )

    assert "提交审批" in workspace
    assert "待审批" in workspace
    assert "已批准" in workspace
    assert "已退回" in workspace
    for direct_lock in (
        "/lock-brief",
        "/characters/${batchId}/lock",
        "/outlines/${batchId}/lock",
        "/episodes/${item.id}/lock",
    ):
        assert direct_lock not in creative
    assert "/breakdowns/${" not in breakdowns or "/lock" not in breakdowns
    assert "/api/creative/workflow/submit" in creative + breakdowns
    assert "/api/workflow/submissions/${submission.id}/${action}" in approvals
    assert "writeReview(submission, 'approve')" in approvals
    assert "writeReview(submission, 'return'" in approvals
    assert "review_comment" in approvals
    assert "trim()" in approvals
    assert "breakdownEditor.record?.status === 'locked'" not in workspace
    assert "breakdownRecordStatusLabel" in workspace + breakdowns
    assert "creativeWorkflowStatusLabel(currentCharacterBatchStatus())" in workspace
    assert "creativeWorkflowStatusLabel(currentOutlineBatchStatus())" in workspace
    assert "creativeWorkflowStatusLabel(selectedProjectBeats().status)" in workspace
    assert "已替代" in workspace
    assert "!creativeResourceEditable(currentProjectCharacters()[0])" in workspace
    assert "!creativeResourceEditable(currentProjectOutlines()[0])" in workspace
