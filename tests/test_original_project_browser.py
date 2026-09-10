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

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def render_workspace_template(
    template_name: str, permissions: set[str] | None = None
) -> str:
    environment = Environment(loader=FileSystemLoader(ROOT / "templates"))
    principal = SimpleNamespace(
        is_system_admin=False,
        permissions=frozenset(permissions or {"project.create"}),
    )
    return environment.get_template(template_name).render(
        principal=principal,
        asset_version="test",
        navigation_items=[],
        request=SimpleNamespace(url=SimpleNamespace(path="/projects")),
    )


def mount_original_project_form(page) -> None:
    modal = render_workspace_template("common/workspace/original_project_modal.html")
    page.set_content(
        '<main x-data="appData()" '
        'x-init="creativeWorkspace.eligibleTopics = window.__eligibleTopics">'
        f"{modal}</main>"
    )
    page.add_script_tag(
        content="""
        window.__eligibleTopics = [{
          id: 23, title: '市场样本', raw_content: '绝不能提交的原始内容',
          locked_breakdown_id: 31, locked_breakdown_version: 2
        }];
        window.__lastOriginalProjectRequest = null;
        window.aiworkerApi = {
          request(url, options = {}) {
            window.__lastOriginalProjectRequest = {
              url, method: options.method || 'GET', body: JSON.parse(options.body || '{}')
            };
            return new Promise(() => {});
          }
        };
        """
    )
    page.add_script_tag(content=_source("static/js/common/creative.js"))
    page.add_script_tag(content=_source("static/js/common/tasks.js"))
    page.add_script_tag(content=_source("static/js/alpine.min.js"))


def mount_blueprint_workspace(
    page,
    *,
    blueprint_status: str = "draft",
    locked_brief_id: int | None = None,
    parent_version_id: int | None = None,
    creation_mode: str = "original",
) -> None:
    blueprint = _source("templates/common/workspace/project_blueprints.html")
    bootstrap_css = _source("static/css/bootstrap.min.css")
    workspace_css = _source("static/css/workspaces.css")
    page.set_content(
        f"<style>{bootstrap_css}{workspace_css}</style>"
        '<main x-data="appData()" '
        'x-init="creativeWorkspace.project = window.__project; '
        'creativeWorkspace.selectedBlueprintId = window.__project.blueprints[0].id">'
        f"{blueprint}</main>"
    )
    page.add_script_tag(
        content=(
            f"window.__blueprintStatus = {blueprint_status!r};"
            f"window.__lockedBriefId = {locked_brief_id if locked_brief_id is not None else 'null'};"
            f"window.__parentVersionId = {parent_version_id if parent_version_id is not None else 'null'};"
            f"window.__creationMode = {creation_mode!r};"
        )
    )
    page.add_script_tag(
        content="""
        window.__project = {
          id: 9, revision: 7, name: '情绪公司', creation_mode: window.__creationMode, genre: '都市奇幻',
          locked_brief_id: window.__lockedBriefId,
          target_platform: '长篇小说', target_audience: '18-35岁',
          target_episode_count: 100, episode_duration_seconds: 900,
          seed: {id: 7, version: 1, status: 'frozen'},
          blueprints: [{
            id: 11, version: 2, parent_version_id: window.__parentVersionId,
            status: window.__blueprintStatus, risk_level: 'low', risk_score: 0,
            content: {
              title: '情绪公司', theme: '人在制度中保持选择', protagonist: '林岚',
              core_conflict: '情绪考核', differentiators: ['情绪劳动具象化'],
              cost_level: 'low', risks: ['设定解释成本'], logline: '未来公司按情绪发工资',
              story_engine: '每集解决一种情绪考核', originality_statement: '独立发展',
              assumptions: ['观众理解情绪积分'], open_questions: ['制度从何而来']
            }
          }]
        };
        window.__blueprintRequests = [];
        window.aiworkerApi = {request: async (url, options = {}) => {
          const body = JSON.parse(options.body || '{}');
          window.__blueprintRequests.push({url, method: options.method || 'GET', body});
          if (url === '/api/workflow/tasks') return {ok: true, json: async () => []};
          if (url.includes('/workflow-tasks/brief/ensure')) {
            return {ok: true, json: async () => ({id: 'brief-task-9', status: 'in_progress'})};
          }
          if (url.endsWith('/versions')) {
            return {ok: true, json: async () => ({id: 12, version: 2, status: 'draft'})};
          }
          if (url === '/api/creative/projects/9' && options.method === 'PATCH') {
            Object.assign(window.__project, body, {revision: 8});
            return {ok: true, json: async () => ({...window.__project})};
          }
          if (url === '/api/creative/projects') {
            return {ok: true, json: async () => ({items: [window.__project]})};
          }
          if (url.startsWith('/api/creative/topics')) {
            return {ok: true, json: async () => ({items: []})};
          }
          if (url === '/api/creative/projects/9') {
            return {ok: true, json: async () => ({...window.__project})};
          }
          return {ok: true, json: async () => ({...window.__project, ...body})};
        }};
        """
    )
    page.add_script_tag(content=_source("static/js/common/creative.js"))
    page.add_script_tag(content=_source("static/js/common/tasks.js"))
    page.add_script_tag(content=_source("static/js/alpine.min.js"))


@pytest.mark.parametrize(
    "template_name", ["planning/projects.html", "writing/projects.html"]
)
def test_both_role_pages_render_original_project_entry(template_name: str) -> None:
    html = render_workspace_template(template_name)
    assert 'data-action="create-original-project"' in html
    assert 'id="original-seed-text"' in html
    assert "一句话灵感或题材" in html


def test_planning_keeps_topic_creation_but_writing_does_not_force_it() -> None:
    planning = render_workspace_template("planning/projects.html")
    writing = render_workspace_template("writing/projects.html")
    assert 'data-section="topic-project-creation"' in planning
    assert 'data-section="topic-project-creation"' not in writing
    assert 'data-action="create-original-project"' in writing


def test_original_entry_is_hidden_without_project_create_permission() -> None:
    html = render_workspace_template(
        "writing/projects.html", permissions={"project.read"}
    )
    assert 'data-action="create-original-project"' not in html


def test_shared_project_table_exposes_mode_source_and_stage_columns() -> None:
    html = render_workspace_template("writing/projects.html")
    for label in ("项目", "创作模式", "创作来源", "目标集数", "当前阶段", "更新时间/操作"):
        assert label in html


@pytest.mark.parametrize(
    ("workspace_page", "expected_project_url"),
    (
        ("writing-projects", "/api/creative/projects?participating_only=true"),
        ("planning-projects", "/api/creative/projects"),
    ),
)
def test_project_list_requests_the_page_visibility_scope(
    workspace_page: str, expected_project_url: str
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            f'<body data-workspace-page="{workspace_page}"></body>'
        )
        page.add_script_tag(
            content="""
            window.__projectListUrls = [];
            window.aiworkerApi = {request: async (url) => {
              window.__projectListUrls.push(url);
              return {ok: true, json: async () => ({items: []})};
            }};
            """
        )
        page.add_script_tag(content=_source("static/js/common/creative.js"))
        page.add_script_tag(content=_source("static/js/common/tasks.js"))

        urls = page.evaluate(
            """async () => {
                const workspace = appData();
                await workspace.fetchCreativeProjects();
                return window.__projectListUrls;
            }"""
        )
        browser.close()

    assert urls[0] == expected_project_url
    assert urls[1] == "/api/creative/topics?status=adopted&limit=500"


def test_original_brief_uses_mode_templates_and_original_fields() -> None:
    source = (ROOT / "templates/common/workspace/project_blueprints.html").read_text(
        encoding="utf-8"
    )
    assert "原创方向与 Brief" in source
    assert "原创性说明" in source
    assert 'x-if="creativeWorkspace.project?.creation_mode === \'original\'"' in source
    assert 'x-if="creativeWorkspace.project?.creation_mode !== \'original\'"' in source


def test_original_brief_shows_and_saves_project_episode_settings() -> None:
    """Would catch original Brief omitting the project settings shown for imitation."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(page)

        episode_count = page.get_by_label("目标集数/章数")
        duration = page.get_by_label("单集/章时长（秒）")
        assert episode_count.input_value() == "100"
        assert duration.input_value() == "900"

        episode_count.fill("80")
        duration.fill("600")
        page.get_by_role("button", name="保存任务书参数").click()
        page.wait_for_function(
            "window.__blueprintRequests.some(item => item.method === 'PATCH')"
        )
        request = page.evaluate(
            "window.__blueprintRequests.find(item => item.method === 'PATCH')"
        )
        browser.close()

    assert request["url"] == "/api/creative/projects/9"
    assert request["body"]["target_episode_count"] == 80
    assert request["body"]["episode_duration_seconds"] == 600
    assert request["body"]["expected_project_revision"] == 7


def test_successor_brief_save_and_lock_requests_keep_revision_scope() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(
            page,
            blueprint_status="draft",
            locked_brief_id=10,
            parent_version_id=10,
        )

        page.get_by_label("目标集数/章数").fill("80")
        page.get_by_role("button", name="完成并锁定").click()
        page.wait_for_function(
            "window.__blueprintRequests.some(item => item.url.endsWith('/lock-brief'))"
        )
        requests = page.evaluate("window.__blueprintRequests")
        browser.close()

    settings_request = next(
        request for request in requests
        if request["url"] == "/api/creative/projects/9" and request["method"] == "PATCH"
    )
    lock_request = next(
        request for request in requests if request["url"].endswith("/lock-brief")
    )
    assert settings_request["body"]["draft_brief_id"] == 11
    assert settings_request["body"]["expected_project_revision"] == 7
    assert lock_request["body"]["expected_project_revision"] == 8


def test_locked_original_brief_can_create_an_editable_version() -> None:
    """Would catch the locked-version action existing only in imitation mode."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(2_000)
        mount_blueprint_workspace(page, blueprint_status="brief_locked")

        page.get_by_role("button", name="基于此版本新建").click()
        page.wait_for_function(
            "window.__blueprintRequests.some(item => item.url.endsWith('/versions'))"
        )
        requests = page.evaluate("window.__blueprintRequests")
        browser.close()

    version_request = next(
        request for request in requests if request["url"].endswith("/versions")
    )
    assert version_request == {
        "url": "/api/creative/blueprints/11/versions",
        "method": "POST",
        "body": {
            "workflow_task_id": "brief-task-9",
            "expected_project_revision": 7,
        },
    }


def test_original_brief_does_not_mount_imitation_only_fields() -> None:
    source = (ROOT / "templates/common/workspace/project_blueprints.html").read_text(
        encoding="utf-8"
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(source)
        assert page.get_by_text("仿制原因", exact=True).count() == 0
        assert page.get_by_text("借鉴元素", exact=True).count() == 0
        browser.close()


def test_imitation_direction_edit_button_reveals_editor() -> None:
    """The pencil action must reveal the editor even when the row is preselected."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 180})
        page.set_default_timeout(2_000)
        mount_blueprint_workspace(page, creation_mode="imitation")

        edit_button = page.locator(
            ".imitation-brief-workspace table tbody button"
        ).first
        edit_button.click()
        page.wait_for_function(
            "document.querySelector('[data-creative-blueprint-editor]')"
            ".getBoundingClientRect().top <= 8"
        )
        browser.close()


def test_imitation_direction_table_keeps_reference_column_proportions() -> None:
    """Long generated copy must not squeeze risk, version, and action columns."""
    expected_ratios = (
        0.025, 0.115, 0.16, 0.14, 0.14, 0.13, 0.11, 0.08, 0.06, 0.04,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 2048, "height": 900})
        mount_blueprint_workspace(page, creation_mode="imitation")
        page.evaluate(
            """
            () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              Object.assign(workspace.creativeWorkspace.project.blueprints[0].content, {
                title: '方向标题'.repeat(4),
                imitation_reason: '仿制原因'.repeat(24),
                protagonist: '主角身份'.repeat(18),
                core_conflict: '核心冲突'.repeat(18),
                background: '故事背景'.repeat(12),
                cost_level: '成本说明'.repeat(10),
                risks: ['风险说明'.repeat(12)],
              });
            }
            """
        )
        table = page.locator(".imitation-brief-workspace table")
        table_width = table.evaluate("element => element.getBoundingClientRect().width")
        column_widths = table.locator("thead th").evaluate_all(
            "elements => elements.map(element => element.getBoundingClientRect().width)"
        )
        line_clamps = table.locator(".blueprint-conflict-clamp").evaluate_all(
            "elements => elements.map(element => getComputedStyle(element).webkitLineClamp)"
        )
        browser.close()

    actual_ratios = tuple(width / table_width for width in column_widths)
    assert actual_ratios == pytest.approx(expected_ratios, abs=0.012)
    assert set(line_clamps) == {"none"}


def test_imitation_editor_uses_reference_grid() -> None:
    """The imitation editor should follow the reference four/three-column grid."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 2048, "height": 900})
        mount_blueprint_workspace(page, creation_mode="imitation")
        editor = page.locator("[data-creative-blueprint-editor]")
        audience = editor.get_by_text("目标受众", exact=True).locator("..")
        audience.locator("textarea").fill("偏向中老年婚恋与家庭关系观众，关注人物成长和代际冲突。" * 4)
        project_boxes = {
            label: editor.get_by_text(label, exact=True).locator("..").bounding_box()
            for label in ("项目名称", "题材", "目标平台/载体", "目标受众")
        }
        save_button_box = editor.get_by_role(
            "button", name="保存任务书参数"
        ).bounding_box()
        primary_columns = [
            editor.get_by_text(label, exact=True).locator("..").evaluate(
                "element => element.getBoundingClientRect().width"
            )
            for label in ("方向名称", "新主角", "核心冲突")
        ]
        browser.close()

    assert all(box is not None for box in project_boxes.values())
    assert save_button_box is not None
    project_widths = [box["width"] for box in project_boxes.values() if box]
    project_tops = [box["y"] for box in project_boxes.values() if box]
    audience_box = project_boxes["目标受众"]
    assert max(project_widths) - min(project_widths) <= 2
    assert max(project_tops) - min(project_tops) <= 2
    assert audience_box is not None
    assert abs(
        audience_box["y"] + audience_box["height"]
        - save_button_box["y"] - save_button_box["height"]
    ) <= 2
    assert max(primary_columns) - min(primary_columns) <= 2


def test_imitation_direction_table_keeps_the_preserve_option() -> None:
    """The reference layout includes a working preserve checkbox per draft."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 2048, "height": 900})
        mount_blueprint_workspace(page, creation_mode="imitation")

        table = page.locator(".creative-direction-table-imitation")
        preserve = table.locator('tbody input[type="checkbox"]')
        assert preserve.count() == 1
        preserve.check()
        preserved_ids = page.evaluate(
            "document.querySelector('main')._x_dataStack[0]"
            ".creativeWorkspace.preservedBlueprintIds"
        )
        browser.close()

    assert preserved_ids == [11]


def test_writer_can_submit_a_lightweight_original_project_request() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_original_project_form(page)
        page.locator("#original-seed-text").fill("未来公司按照员工情绪发工资")
        page.get_by_text("可选市场参考", exact=True).click()
        page.get_by_label("使用《市场样本》的锁定拆解 v2").check()
        page.locator('button:has-text("创建并生成方向")').click(force=True)
        page.wait_for_function("window.__lastOriginalProjectRequest !== null")
        request = page.evaluate("window.__lastOriginalProjectRequest")
        browser.close()

    assert request["url"] == "/api/creative/projects/original"
    assert request["method"] == "POST"
    assert request["body"]["seed_text"] == "未来公司按照员工情绪发工资"
    assert request["body"]["target_episode_count"] == 20
    assert request["body"]["creation_request_id"]
    assert request["body"]["reference_refs"] == [
        {"resource_type": "breakdown", "resource_id": 31}
    ]
    assert "raw_content" not in str(request["body"])


def test_original_brief_query_starts_direction_generation_once() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route(
            "**/planning/projects/9/brief?generate_original=1",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    '<body data-workspace-page="planning-brief">'
                    '<div id="creativeGenerationProgressModal"></div>'
                    '</body>'
                ),
            ),
        )
        page.goto("http://aiworker.test/planning/projects/9/brief?generate_original=1")
        page.add_script_tag(
            content="""
            window.bootstrap = {Modal: {getOrCreateInstance: () => ({show() {}})}};
            window.__originalDirectionRequests = [];
            window.aiworkerApi = {
              request: async (url, options = {}) => {
                if (url === '/api/creative/projects') {
                  return {ok: true, json: async () => ({items: [{id: 9}]})};
                }
                if (url.startsWith('/api/creative/topics?')) {
                  return {ok: true, json: async () => ({items: []})};
                }
                if (url === '/api/creative/projects/9') {
                  return {ok: true, json: async () => ({
                    id: 9, creation_mode: 'original', target_episode_count: 20,
                    seed: {id: 7, status: 'frozen'}, blueprints: []
                  })};
                }
                if (url === '/api/workflow/tasks') {
                  return {ok: true, json: async () => []};
                }
                if (url === '/api/creative/projects/9/workflow-tasks/brief/ensure') {
                  return {ok: true, json: async () => ({id: 'brief-9', status: 'in_progress'})};
                }
                if (url === '/api/creative/projects/9/blueprints/generate') {
                  window.__originalDirectionRequests.push({url, options});
                  return {ok: true, json: async () => ({job: {job_id: 'original-9', status: 'queued'}})};
                }
                throw new Error(`unexpected request: ${url}`);
              },
              isAbortError: () => false,
            };
            """
        )
        page.add_script_tag(content=_source("static/js/common/tasks.js"))
        page.add_script_tag(content=_source("static/js/common/creative.js"))
        result = page.evaluate(
            """async () => {
                const workspace = appData();
                workspace.fetchJobs = async () => {};
                workspace.initTooltips = () => {};
                workspace.initBeforeUnload = () => {};
                await workspace.init();
                await workspace.init();
                return {
                  requests: window.__originalDirectionRequests,
                  search: window.location.search,
                };
            }"""
        )
        browser.close()

    assert len(result["requests"]) == 1
    assert result["requests"][0]["url"] == "/api/creative/projects/9/blueprints/generate"
    assert "generate_original" not in result["search"]


def test_direction_button_tracks_running_server_job_until_it_finishes() -> None:
    """Would catch losing the generation lock after a page reload."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(page)
        button = page.locator(".original-direction-actions button.btn-primary")

        page.evaluate(
            """
            () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              workspace.jobs = [{
                job_id: 'creative-running-9',
                platform: 'creative',
                crawler_type: 'phase4_generate',
                status: 'running',
                project_id: '9',
                job: {extra: {project_id: '9', action: 'blueprints'}},
              }];
            }
            """
        )
        page.wait_for_function("document.querySelector('button.btn-primary').disabled")
        assert button.is_disabled()

        page.evaluate(
            """
            () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              workspace.jobs = workspace.jobs.map(job => ({...job, status: 'succeeded'}));
            }
            """
        )
        page.wait_for_function("!document.querySelector('button.btn-primary').disabled")
        assert button.is_enabled()
        browser.close()


def test_direction_button_keeps_last_running_state_when_job_poll_temporarily_fails() -> None:
    """Would catch a transient poll error reopening a duplicate-submit window."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(page)
        disabled = page.evaluate(
            """
            async () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              workspace.jobs = [{
                job_id: 'creative-running-9',
                platform: 'creative',
                crawler_type: 'phase4_generate',
                status: 'running',
                project_id: '9',
                job: {extra: {project_id: '9', action: 'blueprints'}},
              }];
              window.aiworkerApi.request = async () => {
                throw new Error('temporary poll failure');
              };
              await workspace.fetchJobs();
              return workspace.creativeActionDisabled('blueprints');
            }
            """
        )
        browser.close()

    assert disabled is True


def test_direction_button_keeps_last_running_state_on_job_poll_http_error() -> None:
    """Would catch an HTTP 5xx response being mistaken for an empty job list."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(page)
        disabled = page.evaluate(
            """
            async () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              workspace.jobs = [{
                job_id: 'creative-running-9',
                platform: 'creative',
                crawler_type: 'phase4_generate',
                status: 'running',
                project_id: '9',
                job: {extra: {project_id: '9', action: 'blueprints'}},
              }];
              window.aiworkerApi.request = async () => ({
                ok: false,
                status: 503,
                json: async () => ({detail: 'temporarily unavailable'}),
              });
              await workspace.fetchJobs();
              return workspace.creativeActionDisabled('blueprints');
            }
            """
        )
        browser.close()

    assert disabled is True


def test_series_job_only_disables_episode_buttons_inside_its_range() -> None:
    """Would catch a partial-series task locking unrelated episodes."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        mount_blueprint_workspace(page)
        result = page.evaluate(
            """
            () => {
              const workspace = document.querySelector('main')._x_dataStack[0];
              workspace.jobs = [{
                job_id: 'creative-series-9',
                platform: 'creative',
                crawler_type: 'phase4_generate',
                status: 'running',
                project_id: '9',
                job: {extra: {
                  project_id: '9', action: 'beats', series: true,
                  episode_start: 4, episode_end: 6,
                }},
              }];
              workspace.creativeWorkspace.selectedEpisode = 2;
              const outside = workspace.creativeActionDisabled('beats');
              const wholeSeries = workspace.creativeActionDisabled('beats', {series: true});
              workspace.creativeWorkspace.selectedEpisode = 5;
              const inside = workspace.creativeActionDisabled('beats');
              return {outside, inside, wholeSeries};
            }
            """
        )
        browser.close()

    assert result == {"outside": False, "inside": True, "wholeSeries": True}


def test_original_direction_workspace_has_no_horizontal_overflow_at_390px() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        mount_blueprint_workspace(page)
        page.get_by_text("原创方向与 Brief", exact=True).wait_for()
        dimensions = page.evaluate(
            "({scrollWidth: document.documentElement.scrollWidth, "
            "clientWidth: document.documentElement.clientWidth})"
        )
        browser.close()

    assert dimensions["scrollWidth"] <= dimensions["clientWidth"]
