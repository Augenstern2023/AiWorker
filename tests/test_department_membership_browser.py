# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


PAGE_HTML = """
    <div id="department-error" class="d-none"></div>
    <table><tbody id="department-tree"></tbody></table>
    <form id="department-editor">
      <input id="department-id"><input id="department-name">
      <select id="department-parent"></select>
      <button id="department-editor-cancel" type="button"></button>
      <span id="department-submit-label"></span><i id="department-submit-icon"></i>
    </form>
    <select id="department-user"></select>
    <div id="department-memberships"></div>
    <button id="save-user-departments" type="button"></button>
    <div id="department-primary-modal" data-shown="false">
      <div id="primary-department-options"></div>
      <button id="confirm-primary-department" type="button"></button>
    </div>
    <div id="department-save-success-modal" data-shown="false"></div>
"""


def test_single_selected_department_is_saved_as_primary_without_an_extra_choice():
    script = (ROOT / "static/js/admin/departments.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(PAGE_HTML)
        page.evaluate(
            """
            window.calls = [];
            window.bootstrap = {Modal: {getOrCreateInstance: node => ({
              show: () => node.dataset.shown = 'true',
              hide: () => {
                node.dataset.shown = 'false';
                node.dispatchEvent(new Event('hidden.bs.modal'));
              }
            })}};
            window.aiworkerApi = {request: async (url, options = {}) => {
              window.calls.push({url, options});
              if (url === '/api/admin/departments') return new Response(JSON.stringify([
                {id: 'department:management', name: '管理层', parent_id: null,
                 status: 'active', sort_order: 0, created_at: '2026-01-01', children: []}
              ]), {status: 200});
              if (url === '/api/admin/users') return new Response(JSON.stringify([
                {id: 'user:chenqi', username: 'chenqi', display_name: 'chenqi'}
              ]), {status: 200});
              if (url === '/api/admin/users/user%3Achenqi/departments') {
                return new Response('[]', {status: 200});
              }
              return new Response('{}', {status: 200});
            }};
            """
        )
        page.add_script_tag(content=script)
        page.locator('.department-membership').check()
        page.locator('#save-user-departments').click()
        page.wait_for_timeout(50)

        assignment = page.evaluate(
            """window.calls.find(call =>
              call.url.includes('/departments/department%3Amanagement') &&
              call.options.method === 'PUT')"""
        )
        error = page.locator('#department-error').text_content()
        success_shown = page.locator('#department-save-success-modal').get_attribute(
            'data-shown'
        )
        browser.close()

    assert assignment is not None
    assert assignment["options"]["body"] == '{"is_primary":true}'
    assert error == ""
    assert success_shown == "true"


def test_multiple_selected_departments_choose_the_primary_in_a_modal():
    script = (ROOT / "static/js/admin/departments.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(PAGE_HTML)
        page.evaluate(
            """
            window.calls = [];
            window.bootstrap = {Modal: {getOrCreateInstance: node => ({
              show: () => node.dataset.shown = 'true',
              hide: () => {
                node.dataset.shown = 'false';
                node.dispatchEvent(new Event('hidden.bs.modal'));
              }
            })}};
            window.aiworkerApi = {request: async (url, options = {}) => {
              window.calls.push({url, options});
              if (url === '/api/admin/departments') return new Response(JSON.stringify([
                {id: 'department:management', name: '管理层', parent_id: null,
                 status: 'active', sort_order: 0, created_at: '2026-01-01', children: []},
                {id: 'department:production', name: '制作部', parent_id: null,
                 status: 'active', sort_order: 1, created_at: '2026-01-02', children: []}
              ]), {status: 200});
              if (url === '/api/admin/users') return new Response(JSON.stringify([
                {id: 'user:chenqi', username: 'chenqi', display_name: 'chenqi'}
              ]), {status: 200});
              if (url === '/api/admin/users/user%3Achenqi/departments') {
                return new Response('[]', {status: 200});
              }
              return new Response('{}', {status: 200});
            }};
            """
        )
        page.add_script_tag(content=script)
        page.locator('.department-membership').nth(0).check()
        page.locator('.department-membership').nth(1).check()

        assert page.locator('#department-memberships input[type="radio"]').count() == 0
        page.locator('#save-user-departments').click()
        assert page.locator('#department-primary-modal').get_attribute('data-shown') == 'true'
        assert page.locator('.department-primary-option').count() == 2
        assert page.evaluate(
            "window.calls.filter(call => call.options.method === 'PUT').length"
        ) == 0

        page.locator('.department-primary-option[value="department:production"]').check()
        page.locator('#confirm-primary-department').click()
        page.wait_for_timeout(50)
        assignments = page.evaluate(
            """window.calls.filter(call => call.options.method === 'PUT').map(call => ({
              url: call.url, body: call.options.body
            }))"""
        )
        success_shown = page.locator('#department-save-success-modal').get_attribute(
            'data-shown'
        )
        browser.close()

    assert assignments == [
        {
            "url": "/api/admin/users/user%3Achenqi/departments/department%3Amanagement",
            "body": '{"is_primary":false}',
        },
        {
            "url": "/api/admin/users/user%3Achenqi/departments/department%3Aproduction",
            "body": '{"is_primary":true}',
        },
    ]
    assert success_shown == "true"
