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
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static/js/writing/dashboard.js"


def _mount(page: Page, *, fail_projects: bool = False, fail_tasks: bool = False) -> None:
    page.set_content(
        """
        <main id="writer-dashboard" data-user-id="writer-1">
          <span id="stat-projects"></span><span id="stat-pending"></span>
          <span id="stat-in-progress"></span><span id="stat-completed"></span>
          <span id="progress-outline-count"></span><span id="progress-outline-percent"></span><div id="progress-outline-bar"></div>
          <span id="progress-beats-count"></span><span id="progress-beats-percent"></span><div id="progress-beats-bar"></div>
          <span id="progress-script-count"></span><span id="progress-script-percent"></span><div id="progress-script-bar"></div>
          <table><tbody id="recent-writing-tasks"></tbody></table>
          <div id="recent-writing-tasks-empty"></div>
          <div id="writer-projects-error"></div><div id="writer-tasks-error"></div>
        </main>
        """
    )
    page.evaluate(
        """([failProjects, failTasks]) => {
          const projects = {items: [
            {id: 10, name: '逆风翻盘', updated_at: '2026-08-19T09:00:00Z'},
            {id: 20, name: '她的第二人生', updated_at: '2026-08-18T09:00:00Z'}
          ]};
          const tasks = [
            {id: 'o1', project_id: '10', task_type: 'outline', status: 'completed', assignee_user_id: 'writer-1', updated_at: '2026-08-19T10:00:00Z'},
            {id: 'o2', project_id: '10', task_type: 'outline', status: 'returned', assignee_user_id: 'writer-1', updated_at: '2026-08-19T11:00:00Z'},
            {id: 'o3', project_id: '10', task_type: 'outline', status: 'cancelled', assignee_user_id: 'writer-1', updated_at: '2026-08-19T11:30:00Z'},
            {id: 'b1', project_id: '10', task_type: 'beats', status: 'in_progress', assignee_user_id: 'writer-1', updated_at: '2026-08-19T12:00:00Z'},
            {id: 's1', project_id: '20', task_type: 'script', status: 'assigned', assignee_user_id: 'writer-1', updated_at: '2026-08-19T13:00:00Z'},
            {id: 's2', project_id: '20', task_type: 'script', status: 'completed', assignee_user_id: 'writer-1', updated_at: '2026-08-19T14:00:00Z'},
            {id: 'x1', project_id: '20', task_type: 'script', status: 'completed', assignee_user_id: 'writer-2', updated_at: '2026-08-19T15:00:00Z'},
            {id: 'c1', project_id: '20', task_type: 'characters', status: 'completed', assignee_user_id: 'writer-1', updated_at: '2026-08-19T16:00:00Z'}
          ];
          window.aiworkerApi = {
            request: async (url) => {
              const failed = url.includes('/projects') ? failProjects : failTasks;
              const body = url.includes('/projects') ? projects : tasks;
              return {ok: !failed, json: async () => body};
            },
            parseErrorDetail: async () => '服务暂时不可用'
          };
        }""",
        [fail_projects, fail_tasks],
    )
    page.add_script_tag(path=str(SCRIPT))
    page.wait_for_function("document.querySelector('#stat-completed').textContent !== ''")


def test_dashboard_counts_only_current_writers_writing_tasks() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount(page)

        assert page.locator("#stat-projects").inner_text() == "2"
        assert page.locator("#stat-pending").inner_text() == "2"
        assert page.locator("#stat-in-progress").inner_text() == "1"
        assert page.locator("#stat-completed").inner_text() == "2"
        assert page.locator("#progress-outline-count").inner_text() == "1 / 2"
        assert page.locator("#progress-outline-percent").inner_text() == "50%"
        assert page.locator("#progress-beats-count").inner_text() == "0 / 1"
        assert page.locator("#progress-script-count").inner_text() == "1 / 2"
        assert page.locator("#recent-writing-tasks tr").count() == 6
        assert page.locator("#recent-writing-tasks tr").first.inner_text().startswith("场次剧本")
        assert "她的第二人生" in page.locator("#recent-writing-tasks tr").first.inner_text()
        browser.close()


def test_dashboard_keeps_task_metrics_when_projects_fail() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount(page, fail_projects=True)

        assert page.locator("#stat-projects").inner_text() == "0"
        assert page.locator("#stat-completed").inner_text() == "2"
        assert "参与项目加载失败" in page.locator("#writer-projects-error").inner_text()
        assert page.locator("#writer-tasks-error").inner_text() == ""
        browser.close()


def test_dashboard_keeps_project_metric_when_tasks_fail() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount(page, fail_tasks=True)

        assert page.locator("#stat-projects").inner_text() == "2"
        assert page.locator("#stat-completed").inner_text() == "0"
        assert "编剧任务加载失败" in page.locator("#writer-tasks-error").inner_text()
        assert page.locator("#writer-projects-error").inner_text() == ""
        browser.close()


def test_dashboard_layout_is_responsive_without_horizontal_overflow() -> None:
    environment = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
    html = environment.get_template("writing/dashboard.html").render(
        principal=SimpleNamespace(user_id="writer-1", display_name="测试编剧"),
        navigation=[], current_route="/writing/dashboard", page_title="工作概览",
        workspace_page="writing-dashboard", show_tasks=True, asset_version="test",
        csrf_token="",
    )
    bootstrap_css = (ROOT / "static/css/bootstrap.min.css").read_text(encoding="utf-8")
    workspace_css = (ROOT / "static/css/workspaces.css").read_text(encoding="utf-8")
    html = html.replace(
        "<style>", f"<style>{bootstrap_css}\n{workspace_css}</style><style>", 1
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_content(html)

        cards = page.locator(".writer-metric-card")
        assert cards.count() == 4
        desktop_tops = [round(cards.nth(index).bounding_box()["y"]) for index in range(4)]
        assert len(set(desktop_tops)) == 1
        page.screenshot(path=str(ROOT / ".runtime/qa/screenwriter-dashboard-desktop.png"), full_page=True)

        page.set_viewport_size({"width": 900, "height": 900})
        tablet_tops = [round(cards.nth(index).bounding_box()["y"]) for index in range(4)]
        assert len(set(tablet_tops)) == 2

        page.set_viewport_size({"width": 390, "height": 844})
        mobile_tops = [round(cards.nth(index).bounding_box()["y"]) for index in range(4)]
        assert mobile_tops == sorted(mobile_tops)
        assert len(set(mobile_tops)) == 4
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.screenshot(path=str(ROOT / ".runtime/qa/screenwriter-dashboard-mobile.png"), full_page=True)
        browser.close()
