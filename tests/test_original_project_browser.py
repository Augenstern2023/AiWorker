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


def mount_blueprint_workspace(page) -> None:
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
        content="""
        window.__project = {
          id: 9, creation_mode: 'original', seed: {id: 7, version: 1, status: 'frozen'},
          blueprints: [{
            id: 11, version: 1, status: 'draft', risk_level: 'low', risk_score: 0,
            content: {
              title: '情绪公司', theme: '人在制度中保持选择', protagonist: '林岚',
              core_conflict: '情绪考核', differentiators: ['情绪劳动具象化'],
              cost_level: 'low', risks: ['设定解释成本'], logline: '未来公司按情绪发工资',
              story_engine: '每集解决一种情绪考核', originality_statement: '独立发展',
              assumptions: ['观众理解情绪积分'], open_questions: ['制度从何而来']
            }
          }]
        };
        window.aiworkerApi = {request: async () => ({ok: true, json: async () => ({})})};
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


def test_original_brief_uses_mode_templates_and_original_fields() -> None:
    source = (ROOT / "templates/common/workspace/project_blueprints.html").read_text(
        encoding="utf-8"
    )
    assert "原创方向与 Brief" in source
    assert "原创性说明" in source
    assert 'x-if="creativeWorkspace.project?.creation_mode === \'original\'"' in source
    assert 'x-if="creativeWorkspace.project?.creation_mode !== \'original\'"' in source


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
