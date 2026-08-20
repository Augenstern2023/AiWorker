# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import re
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


ROLE_TEMPLATES = (
    "selection/dashboard.html",
    "selection/hits.html",
    "selection/ranks.html",
    "selection/topics.html",
    "selection/dramas.html",
    "planning/projects.html",
    "planning/brief.html",
    "planning/characters.html",
    "writing/projects.html",
    "writing/outlines.html",
    "writing/beats.html",
    "review/projects.html",
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
        "templates/planning/projects.html": "/static/js/planning/projects.js",
        "templates/planning/brief.html": "/static/js/planning/brief.js",
        "templates/planning/characters.html": "/static/js/planning/characters.js",
        "templates/writing/outlines.html": "/static/js/writing/outlines.js",
        "templates/writing/beats.html": "/static/js/writing/beats.js",
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


def test_all_workspace_javascript_has_valid_syntax():
    node = Path(r"C:\Users\Administrator\tools\node-v24.16.0-win-x64\node.exe")
    assert node.exists()
    scripts = sorted((ROOT / "static/js").glob("**/*.js"))
    failures = []
    for script in scripts:
        result = subprocess.run(
            [str(node), "--check", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            failures.append(f"{script.relative_to(ROOT)}: {result.stderr}")
    assert failures == []


def test_selection_pages_load_topic_capabilities_before_alpine_init():
    tasks = _read("static/js/common/tasks.js")
    topics = _read("static/js/selection/topics.js")
    for page_name in ("topics",):
        page = _read(f"templates/selection/{page_name}.html")
        assert "/static/js/selection/topics.js?v={{ asset_version }}" in page
    assert "async fetchTopicLibrary()" in topics
    assert "typeof workspace.fetchTopicLibrary !== 'function'" in tasks


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
    brief = _read("templates/planning/brief.html")

    assert "`/planning/projects/${project.id}/brief`" in project_list
    assert "`/planning/projects/${data.id}/brief" in creative
    assert not Path("templates/planning/directions.html").exists()
    assert "仿写方向" not in brief
    assert 'href="/planning/projects/{{ project_id }}/characters"' in brief


def test_project_brief_uses_multiline_controls_for_generated_long_text():
    blueprint = _read("templates/common/workspace/project_blueprints.html")

    fields = (
        ("creativeWorkspace.project.target_audience", 16),
        ("selectedProjectBlueprint().content.protagonist", 24),
        ("selectedProjectBlueprint().content.background", 24),
        ("selectedProjectBlueprint().content.audience", 24),
    )
    for field, characters_per_line in fields:
        assert re.search(
            rf'<textarea[^>]+:rows="textareaRows\({re.escape(field)}, {characters_per_line}\)"[^>]+'
            rf'x-model="{re.escape(field)}"',
            blueprint,
        )
        assert f'<input class="form-control form-control-sm" x-model="{field}"' not in blueprint


def test_project_brief_long_text_controls_wrap_without_clipping():
    blueprint = _read("templates/common/workspace/project_blueprints.html")
    workspace_css = _read("static/css/workspaces.css")
    bootstrap_css = _read("static/css/bootstrap.min.css")
    markup = blueprint.replace(
        '<template x-if="selectedProjectBlueprint()"><div class="border-top pt-3">',
        '<div class="border-top pt-3">',
    ).replace("</div></template>", "</div>").replace(
        '<template x-if="creativeWorkspace.project?.creation_mode !== \'original\'"><div class="imitation-brief-workspace">',
        '<div class="imitation-brief-workspace">',
    ).replace("</div>\n</template>\n</div>\n", "</div>\n</div>\n")
    fields = (
        ("creativeWorkspace.project.target_audience", 16),
        ("selectedProjectBlueprint().content.protagonist", 24),
        ("selectedProjectBlueprint().content.background", 24),
        ("selectedProjectBlueprint().content.audience", 24),
    )
    value = "女性向短剧观众，偏好情感成长、家庭关系与高密度剧情冲突。" * 3

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.set_content(f"<style>{bootstrap_css}{workspace_css}</style>{markup}")
        for field, characters_per_line in fields:
            control = page.locator(f'textarea[x-model="{field}"]')
            rows = max(2, (len(value) + characters_per_line - 1) // characters_per_line)
            control.evaluate("(element, rows) => element.rows = rows", rows)
            control.fill(value)
            assert control.evaluate(
                "element => element.scrollHeight <= element.clientHeight + 1"
            )
        page.screenshot(path=str(ROOT / ".runtime/qa/brief-long-text-controls.png"))
        browser.close()


def test_project_beats_uses_multiline_controls_for_long_content():
    beats = _read("templates/common/workspace/project_beats.html")

    fields = (
        ("beat.scene", "beat.scene"),
        ("listText(beat.characters)", "beat.characters"),
        ("beat.goal", "beat.goal"),
        ("beat.obstacle", "beat.obstacle"),
        ("beat.emotion_change", "beat.emotion_change"),
        ("beat.state_change", "beat.state_change"),
        ("beat.cost", "beat.cost"),
        ("beat.opposition_tactic", "beat.opposition_tactic"),
        ("beat.turn", "beat.turn"),
        ("beat.hook", "beat.hook"),
    )
    for row_source, field in fields:
        assert re.search(
            rf'<textarea[^>]+:rows="textareaRows\({re.escape(row_source)}, 10\)"[^>]+',
            beats,
        )
        assert field in beats
        assert f'<input class="form-control form-control-sm" x-model="{field}"' not in beats


def test_project_beats_exposes_story_change_cost_and_continuity_ledger():
    beats = _read("templates/common/workspace/project_beats.html")

    for label, field in (
        ("局面变化", "beat.state_change"),
        ("代价", "beat.cost"),
        ("对抗策略", "beat.opposition_tactic"),
    ):
        assert label in beats
        assert field in beats
    assert "连续性变更" in beats
    assert "连续性账本" in beats


def test_project_beats_long_text_controls_wrap_without_clipping():
    beats = _read("templates/common/workspace/project_beats.html")
    workspace_css = _read("static/css/workspaces.css")
    bootstrap_css = _read("static/css/bootstrap.min.css")
    markup = (
        beats.replace('<template x-if="selectedProjectBeats()"><div>', "<div>")
        .replace(
            '<template x-for="beat in selectedProjectBeats().content.beats" :key="beat.order"><tr>',
            "<tr>",
        )
        .replace("</tr></template>", "</tr>")
        .replace("</div></template>", "</div>")
    )
    value = "主角在高压环境中做出艰难选择，关系与目标同时受到阻力。" * 3

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 2048, "height": 900})
        page.set_content(f"<style>{bootstrap_css}{workspace_css}</style>{markup}")
        controls = page.locator("tbody textarea")
        assert controls.count() == 10
        rows = max(2, (len(value) + 9) // 10)
        for index in range(controls.count()):
            control = controls.nth(index)
            control.evaluate("(element, rows) => element.rows = rows", rows)
            control.fill(value)
            assert control.evaluate(
                "element => element.scrollHeight <= element.clientHeight + 1"
            )
        page.screenshot(path=str(ROOT / ".runtime/qa/beats-long-text-controls.png"))
        browser.close()


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
    assert "红果抓包下载设置" in admin
    assert "capture_root" in admin_script
    assert "wait_timeout_seconds" in admin_script
    assert "activationCode" not in admin_script
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
    assert 'id="role-pages-modal"' in roles_html
    assert "/api/admin/permissions" in roles_js
    assert "/api/admin/roles/${role.id}/clone" in roles_js
    assert "${rolesApi}/${roleId}/pages" in roles_js
    assert "openRolePages" in roles_js
    assert "permissions" in roles_js
    assert "toggleRoleStatus" in roles_js


def test_user_list_displays_role_names_instead_of_role_codes():
    users_js = _read("static/js/admin/users.js")

    assert "function roleDisplayName(roleCode)" in users_js
    assert "(user.role_codes || []).map(roleDisplayName).join('、')" in users_js


def test_team_account_page_loads_the_current_users_script_version():
    users_page = _read("templates/admin/users.html")

    assert "/static/js/admin/users.js?v={{ asset_version }}" in users_page


def test_selection_topics_includes_planning_assignment_modal():
    topics = _read("templates/selection/topics.html")

    assert 'common/workspace/planning_assignment_modal.html' in topics


def test_review_projects_lists_managed_projects_only():
    page = _read("templates/review/projects.html")
    script = _read("static/js/review/projects.js")

    assert 'id="managed-project-list"' in page
    assert "待决策选题" not in page
    assert "workflow-bootstrap-topics" not in page
    assert "/api/creative/projects" in script
    assert "task.assignee_user_id" not in script
    assert "/api/creative/topics/workflow-candidates" not in script
    assert "/workflow`" not in script


def test_review_project_name_opens_its_creative_brief():
    script = _read("static/js/review/projects.js")

    assert "project-name-button" in script
    assert "`/planning/projects/${project.id}/brief`" in script
    assert "`/review/projects/${project.id}/members`" in script


def test_admin_delivery_page_lists_generated_resources_without_delivery_registry():
    html = _read("templates/admin/deliveries.html")
    source = _read("static/js/admin/deliveries.js")

    assert 'id="generated-resource-list"' in html
    assert 'id="delivery-create-form"' not in html
    assert 'id="delivery-history"' not in html
    assert "/api/admin/deliveries" not in source
    assert "method: 'POST'" not in source
    assert "/api/creative/generated-resources" in source
    assert "generated-content/download" in source


def test_admin_delivery_initializer_does_not_require_creative_workspace_module():
    tasks = _read("static/js/common/tasks.js")

    assert "this.page === 'admin-deliveries'" not in tasks


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


def test_selection_download_pages_load_workflow_task_resolvers():
    for relative_path, feature_script in (
        ("templates/selection/hits.html", "/static/js/selection/breakdowns.js"),
        ("templates/selection/topics.html", "/static/js/selection/breakdowns.js"),
        ("templates/selection/dramas.html", "/static/js/selection/dramas.js"),
    ):
        html = _read(relative_path)
        resolver = "/static/js/common/creative.js"

        assert resolver in html
        assert html.index(resolver) < html.index(feature_script)


def test_creative_workflow_ui_locks_versions_without_review_panels():
    creative = _read("static/js/common/creative.js")
    breakdowns = _read("static/js/selection/breakdowns.js")
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

    assert "提交审批" not in workspace
    assert "待审批" not in workspace
    assert "已批准" not in workspace
    assert "已退回" not in workspace
    for direct_lock in (
        "/lock-brief",
        "/characters/${batchId}/lock",
        "/outlines/${batchId}/lock",
        "/episodes/${resourceId}/lock",
    ):
        assert direct_lock in creative
    assert "/api/creative/breakdowns/${record.id}/lock" in breakdowns
    assert "完成并锁定" in workspace
    assert "/api/creative/workflow/submit" not in creative
    assert "breakdownEditor.record?.status === 'locked'" not in workspace
    assert "breakdownRecordStatusLabel" in workspace + breakdowns
    assert "creativeWorkflowStatusLabel(currentCharacterBatchStatus())" in workspace
    assert "creativeWorkflowStatusLabel(currentOutlineBatchStatus())" in workspace
    assert "creativeWorkflowStatusLabel(selectedProjectBeats().status)" in workspace
    assert "已替代" in workspace
    assert "!creativeResourceEditable(currentProjectCharacters()[0])" in workspace
    assert "!creativeResourceEditable(currentProjectOutlines()[0])" in workspace
