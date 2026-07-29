# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com

"""Browser coverage for the role page-access editor."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def test_role_page_authorization_modal_saves_selected_routes() -> None:
    html = """
    <!doctype html><html><body>
    <div id="role-error"></div><form id="role-filters"></form><span id="role-total"></span>
    <table><tbody id="role-list"></tbody></table>
    <div id="role-editor-modal"></div><form id="role-editor-form">
      <input id="role-id"><input id="role-code"><input id="role-name"><input id="role-description">
      <input id="role-claimable-task-types"><tbody id="permission-list"></tbody><div id="role-form-error"></div>
    </form>
    <div id="role-pages-modal"></div><form id="role-pages-form">
      <input id="role-pages-role-id"><span id="role-pages-role-name"></span>
      <div id="role-pages-list"></div><div id="role-pages-error"></div><button type="submit">保存</button>
    </form>
    <script>
      window.bootstrap = { Modal: { getOrCreateInstance(element) {
        return { show() { element.dataset.open = 'true'; }, hide() { element.dataset.open = 'false'; } };
      } } };
      window.calls = [];
      window.aiworkerApi = { request: async (url, options = {}) => {
        window.calls.push({url, options});
        if (url.startsWith('/api/admin/roles?')) return new Response(JSON.stringify([{
          id: 'role:writer', code: 'screenwriter', name: '编剧', description: '',
          is_enabled: true, permissions: [], page_routes: ['/writing/projects']
        }]), {status: 200});
        if (url === '/api/admin/permissions') return new Response('[]', {status: 200});
        if (url === '/api/admin/roles/page-catalog') return new Response(JSON.stringify([
          {route: '/writing/projects', label: '编剧项目'},
          {route: '/admin/data', label: '数据管理'}
        ]), {status: 200});
        if (url === '/api/admin/roles/role:writer/pages') return new Response(JSON.stringify({
          id: 'role:writer', page_routes: ['/admin/data', '/writing/projects']
        }), {status: 200});
        return new Response(JSON.stringify({detail: 'unexpected'}), {status: 404});
      } };
    </script>
    </body></html>
    """
    script = (ROOT / "static" / "js" / "admin" / "roles.js").read_text(
        encoding="utf-8"
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.add_script_tag(content=script)
        page.wait_for_selector('button[title="页面授权"]')

        page.locator('button[title="页面授权"]').click()
        assert page.locator("#role-pages-modal").get_attribute("data-open") == "true"
        assert page.locator('input[value="/writing/projects"]').is_checked()

        page.locator('input[value="/admin/data"]').check()
        page.locator("#role-pages-form button[type=submit]").click()
        page.wait_for_function(
            "window.calls.some((call) => call.url === '/api/admin/roles/role:writer/pages')"
        )
        saved = page.evaluate(
            """() => JSON.parse(window.calls.find(
                (call) => call.url === '/api/admin/roles/role:writer/pages'
            ).options.body)"""
        )
        assert saved == {"page_routes": ["/writing/projects", "/admin/data"]}
        browser.close()
