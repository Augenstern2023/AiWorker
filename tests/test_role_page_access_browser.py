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
      <div class="row g-2" id="role-pages-list"></div><div id="role-pages-error"></div><button type="submit">保存</button>
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
          is_enabled: true, is_system: true, permissions: [], page_routes: ['/writing/projects']
        }]), {status: 200});
        if (url === '/api/admin/permissions') return new Response('[]', {status: 200});
        if (url === '/api/admin/roles/page-catalog') return new Response(JSON.stringify([
          {route: '/writing/projects', label: '编剧项目'},
          {route: '/admin/data', label: '数据管理'},
          ...Array.from({length: 18}, (_, index) => ({
            route: `/page/${index + 1}`, label: `业务页面 ${index + 1}`
          }))
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
    bootstrap_css = (ROOT / "static" / "css" / "bootstrap.min.css").read_text(
        encoding="utf-8"
    )
    bootstrap_icons_css = (
        ROOT / "static" / "css" / "bootstrap-icons.css"
    ).read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        page.set_content(html)
        page.add_style_tag(content=bootstrap_css)
        page.add_style_tag(content=bootstrap_icons_css)
        page.add_script_tag(content=script)
        page.wait_for_selector('button[title="页面授权"]')

        clone_icon_content = page.locator('button[title="克隆角色"] i').evaluate(
            "element => getComputedStyle(element, '::before').content"
        )
        assert clone_icon_content not in {"none", "normal", '""'}
        locked_status = page.locator('button[title="系统角色不可停用"]')
        assert locked_status.is_disabled()
        assert "bi-lock" in (locked_status.locator("i").get_attribute("class") or "")

        page.locator('button[title="页面授权"]').click()
        assert page.locator("#role-pages-modal").get_attribute("data-open") == "true"
        assert page.locator('input[value="/writing/projects"]').is_checked()
        card_box = page.locator("#role-pages-list label").first.bounding_box()
        checkbox_box = page.locator("#role-pages-list input").first.bounding_box()
        assert card_box is not None
        assert checkbox_box is not None
        assert checkbox_box["x"] >= card_box["x"] + 8
        cards = page.locator("#role-pages-list label")
        second_card_box = cards.nth(1).bounding_box()
        third_card_box = cards.nth(2).bounding_box()
        assert second_card_box is not None
        assert third_card_box is not None
        assert second_card_box["x"] > card_box["x"] + card_box["width"]
        assert third_card_box["y"] >= card_box["y"] + card_box["height"]

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
