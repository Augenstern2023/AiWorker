# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_SCRIPT = PROJECT_ROOT / "static/js/review/projects.js"


def _install_api(page: Page) -> None:
    page.evaluate(
        """
        () => {
        window.projectMemberApiCalls = [];
        window.projectMemberConfirmations = [];
        window.confirm = (message) => {
            window.projectMemberConfirmations.push(message);
            return true;
        };
        window.aiworkerApi = {
            request: async (url, options = {}) => {
                const method = options.method || 'GET';
                window.projectMemberApiCalls.push({url, method, body: options.body || ''});
                window.__members = window.__members || [
                    {id: 'owner-membership', user_id: 'owner-1', username: 'owner', display_name: '项目负责人', project_role: 'project_owner', status: 'active'},
                    {id: 'creator-membership', user_id: 'creator-1', username: 'creator', display_name: '项目创建负责人', project_role: 'project_creator', status: 'active'}
                ];
                if (url === '/api/creative/projects') {
                    return new Response(JSON.stringify({items: [{id: 7, name: '成员编辑项目', topic_title: '来源选题', status: 'created'}]}), {status: 200});
                }
                if (url === '/api/workflow/projects/7/members' && method === 'GET') {
                    return new Response(JSON.stringify(window.__members), {status: 200});
                }
                if (url.includes('/api/workflow/projects/7/member-candidates')) {
                    const role = new URL(url, 'http://local').searchParams.get('project_role');
                    const candidates = {
                        project_owner: [{user_id: 'owner-2', username: 'owner2', display_name: '候选负责人'}],
                        creative_planner: [{user_id: 'planner-1', username: 'planner', display_name: '候选策划'}],
                        screenwriter: [{user_id: 'writer-1', username: 'writer', display_name: '候选编剧'}],
                    }[role] || [];
                    return new Response(JSON.stringify(candidates), {status: 200});
                }
                if (url === '/api/workflow/projects/7/members' && method === 'POST') {
                    const payload = JSON.parse(options.body);
                    const member = {
                        id: `${payload.user_id}-${payload.project_role}`,
                        user_id: payload.user_id,
                        username: payload.user_id === 'writer-1' ? 'writer' : 'member',
                        display_name: payload.user_id === 'writer-1' ? '候选编剧' : '新成员',
                        project_role: payload.project_role,
                        status: 'active',
                    };
                    window.__members.push(member);
                    return new Response(JSON.stringify(member), {status: 201});
                }
                if (url.includes('/api/workflow/projects/7/members/') && method === 'DELETE') {
                    const membershipId = decodeURIComponent(url.split('/').pop());
                    window.__members = window.__members.filter(item => item.id !== membershipId);
                    return new Response(null, {status: 204});
                }
                return new Response(JSON.stringify({detail: `unexpected ${method} ${url}`}), {status: 500});
            }
        };
        }
        """
    )


def _list_markup() -> str:
    return """
    <div id="managed-project-error" class="d-none"></div>
    <div id="managed-projects" data-project-id="" data-can-manage-members="true">
      <table><tbody id="managed-project-list"></tbody></table>
    </div>
    """


def _editor_markup() -> str:
    return """
    <div id="managed-project-error" class="d-none"></div>
    <div id="managed-projects" data-project-id="7" data-can-manage-members="true">
      <span id="member-project-title"></span>
      <div id="project-member-error" class="d-none"></div>
      <form id="project-member-form">
        <select id="project-member-role">
          <option value="project_owner">项目负责人</option>
          <option value="creative_planner" selected>创意策划</option>
          <option value="screenwriter">编剧</option>
        </select>
        <select id="project-member-user"></select>
        <button id="project-member-add" type="submit">添加成员</button>
      </form>
      <div id="project-member-loading" class="d-none"></div>
      <table><tbody id="project-member-list"></tbody></table>
    </div>
    """


def test_managed_project_operation_opens_member_editor() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _install_api(page)
        page.set_content(_list_markup())
        page.add_script_tag(path=str(PROJECTS_SCRIPT))

        member_link = page.get_by_role("link", name="编辑项目成员")
        expect(member_link).to_have_attribute("href", "/review/projects/7/members")
        expect(page.get_by_role("link", name="打开创作任务书")).to_be_visible()
        browser.close()


def test_member_editor_adds_and_removes_a_project_role() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _install_api(page)
        page.set_content(_editor_markup())
        page.add_script_tag(path=str(PROJECTS_SCRIPT))

        expect(page.locator("#member-project-title")).to_have_text("成员编辑项目")
        expect(page.locator("#project-member-list")).to_contain_text("项目负责人")
        page.locator("#project-member-role").select_option("screenwriter")
        expect(page.locator("#project-member-user")).to_have_value("writer-1")

        page.locator("#project-member-add").click()
        expect(page.locator("#project-member-list")).to_contain_text("候选编剧")
        expect(page.locator("#project-member-list")).to_contain_text("编剧")

        page.get_by_role("button", name="移除候选编剧的编剧角色").click()
        expect(page.locator("#project-member-list")).not_to_contain_text("候选编剧")
        browser.close()


def test_member_editor_uses_deactivation_copy_for_project_creator() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _install_api(page)
        page.set_content(_editor_markup())
        page.add_script_tag(path=str(PROJECTS_SCRIPT))

        deactivate = page.get_by_role(
            "button", name="停用项目创建负责人的项目创建负责人角色"
        )
        expect(deactivate).to_have_text("停用")
        deactivate.click()

        assert page.evaluate("window.projectMemberConfirmations") == [
            "确定停用项目创建负责人的项目创建负责人角色吗？"
        ]
        browser.close()
