# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_marketplace_panel_installs_and_rates_through_rendered_ui():
    template = {
        "id": "user-template-1",
        "template_key": "user-template-1",
        "title": "我的私人模板",
        "description": "可提交市场",
        "owner_scope": "user",
        "revision": 1,
        "content": {
            "dimensions": {
                "format": "竖屏短剧",
                "genre": "轻喜剧",
                "audience": "大众",
                "story_engine": "身份错位",
                "emotion": "轻松",
                "pacing": "快节奏",
                "expression": "口语化",
            },
            "shared_dna": "每集使用可拍摄的身份错位推动冲突。",
            "parameters": {},
            "stage_rules": {},
            "examples": [],
        },
    }
    listing = {
        "id": "listing-1",
        "title": "审核通过的强钩子模板",
        "description": "适合竖屏短剧",
        "attribution_name": "社区编剧",
        "source_url": "",
        "tags": ["竖屏", "强钩子"],
        "compatibility": {
            "schema_version": "prompt-library-v1",
            "supported_stages": ["script"],
        },
        "content": {"shared_dna": "每集结尾必须形成新的未完成动作。"},
        "rating": {"average": None, "count": 0},
        "install_count": 2,
        "installation": None,
        "my_rating": None,
        "is_owner": False,
        "status": "approved",
    }
    official = {
        "id": "system:generic-vertical-short",
        "template_key": "generic-vertical-short",
        "title": "通用竖屏短剧",
        "description": "官方内置模板",
        "owner_scope": "system",
        "revision": 2,
        "tags": ["竖屏短剧", "强情绪"],
        "compatibility": {
            "schema_version": "prompt-library-v1",
            "supported_stages": ["script"],
        },
        "content": {"shared_dna": "每集围绕一个清晰的戏剧动作推进。"},
    }
    bootstrap = {
        "project": {"id": 9},
        "template": template,
        "listing": listing,
        "official": official,
    }
    modal = _source("templates/common/workspace/prompt_library_modal.html")
    market_start = modal.index(
        '<section class="border border-info-subtle rounded p-3 mb-3"'
    )
    market_end = modal.index(
        '<section class="border border-primary-subtle rounded p-3 mb-3"',
        market_start,
    )
    market_panel = modal[market_start:market_end]
    html = (
        '<main x-data="appData()" '
        'x-init="creativeWorkspace.project = window.__marketBootstrap.project; '
        "creativeWorkspace.promptLibrary.stage = 'script'; "
        "creativeWorkspace.promptLibrary.settings = {enabled: true}; "
        "creativeWorkspace.promptLibrary.templates = [window.__marketBootstrap.template, window.__marketBootstrap.official]; "
        "creativeWorkspace.promptLibrary.selectedTemplateKey = window.__marketBootstrap.template.template_key; "
        'creativeWorkspace.promptLibrary.marketplaceOpen = true; loadPromptMarketplace()"'
        f'>{market_panel}</main>'
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto("about:blank")
        page.wait_for_load_state("networkidle")
        page.set_content(html)
        page.add_script_tag(
            content=f"window.__marketBootstrap = {json.dumps(bootstrap, ensure_ascii=False)};"
        )
        page.add_script_tag(
            content="""
            window.__marketInstalled = false;
            window.__marketRated = false;
            window.__marketSubmitted = false;
            window.__marketRequests = [];
            window.bootstrap = {
              Modal: {getOrCreateInstance: () => ({show() {}, hide() {}})},
              Tooltip: {getOrCreateInstance: () => ({})}
            };
            window.confirm = () => true;
            window.prompt = (message, value) => message.includes('评分') ? '5' : '实际生成后钩子更稳定';
            window.aiworkerApi = {
              request(url, options = {}) {
                const method = options.method || 'GET';
                const body = JSON.parse(options.body || '{}');
                window.__marketRequests.push({url, method, body});
                if (url.includes('/install')) {
                  window.__marketInstalled = true;
                  return Promise.resolve({ok: true, json: async () => ({bound: false})});
                }
                if (url.includes('/rating')) {
                  window.__marketRated = true;
                  return Promise.resolve({
                    ok: true,
                    json: async () => ({rating: {revision: 1}, summary: {average: 5, count: 1}})
                  });
                }
                if (url.includes('/marketplace-submissions')) {
                  window.__marketSubmitted = true;
                  return Promise.resolve({ok: true, json: async () => ({id: 'submission-1'})});
                }
                if (url.includes('/prompt-modules/')) {
                  return Promise.resolve({
                    ok: true,
                    json: async () => ({
                      settings: {enabled: true}, templates: [window.__marketBootstrap.template, window.__marketBootstrap.official],
                      effective: window.__marketBootstrap.template,
                      stage_binding: null, episode_binding: null
                    })
                  });
                }
                if (url.includes('/recommendations')) {
                  return Promise.resolve({ok: true, json: async () => ({items: []})});
                }
                const item = {
                  ...window.__marketBootstrap.listing,
                  installation: window.__marketInstalled ? {id: 'install-1'} : null,
                  my_rating: window.__marketRated ? {
                    score: 5, review_text: '实际生成后钩子更稳定', revision: 1
                  } : null,
                  rating: window.__marketRated ? {average: 5, count: 1} : {average: null, count: 0}
                };
                return Promise.resolve({
                  ok: true,
                  json: async () => ({
                    official_items: [window.__marketBootstrap.official],
                    items: [item], own_submissions: [], moderation_queue: [],
                    installations: item.installation ? [item.installation] : [],
                    source_types: [
                      {code: 'original', label: '原创'},
                      {code: 'authorized', label: '授权改编'}
                    ],
                    policy: ['安装生成独立副本'], can_review: false, can_install: true
                  })
                });
              }
            };
            """
        )
        page.add_script_tag(content=_source("static/js/common/creative.js"))
        page.add_script_tag(content=_source("static/js/common/tasks.js"))
        page.add_script_tag(content=_source("static/js/alpine.min.js"))
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        assert page_errors == [], "\n".join(page_errors)

        submit_button = page.get_by_role("button", name="提交市场审核")
        assert submit_button.is_enabled()
        submit_button.click()
        page.get_by_text("模板署名未填写", exact=True).wait_for()
        assert page.locator("#promptMarketplaceAttribution").evaluate(
            "element => document.activeElement === element"
        )
        page.get_by_text("来源与权利说明未填写", exact=True).wait_for()
        page.get_by_text("请确认模板会在公司内部展示并可被安装", exact=True).wait_for()
        page.get_by_text("请确认拥有发布和内部使用所需权利", exact=True).wait_for()
        assert not page.evaluate("window.__marketSubmitted")

        page.locator("#promptMarketplaceAttribution").fill("我的署名")
        page.locator("#promptMarketplaceRights").fill("tttt")
        submit_button.click()
        page.get_by_text("来源与权利说明至少填写 10 字（当前 4 字）", exact=True).wait_for()
        assert not page.evaluate("window.__marketSubmitted")

        page.locator("#promptMarketplaceSourceType").select_option("authorized")
        submit_button.click()
        page.get_by_text("非原创模板的可核验来源链接未填写", exact=True).wait_for()
        page.locator("#promptMarketplaceSourceUrl").fill("not-a-link")
        submit_button.click()
        page.get_by_text("来源链接需填写有效的 HTTP 或 HTTPS 地址", exact=True).wait_for()
        assert not page.evaluate("window.__marketSubmitted")

        page.locator("#promptMarketplaceSourceUrl").fill("https://example.com/source")
        page.locator("#promptMarketplaceRights").fill("已取得原作者授权，可在公司内部项目中使用。")
        page.locator("#promptMarketplacePublicConfirm").check()
        page.locator("#promptMarketplaceRightsConfirm").check()
        submit_button.click()
        page.wait_for_function("window.__marketSubmitted")
        submission = page.evaluate(
            "window.__marketRequests.find(item => item.url.includes('/marketplace-submissions'))"
        )
        assert submission["method"] == "POST"
        assert submission["body"]["source_url"] == "https://example.com/source"
        assert "license_code" not in submission["body"]

        page.evaluate(
            "Alpine.$data(document.querySelector('main')).creativeWorkspace."
            "promptLibrary.templates[0].owner_scope = 'project'"
        )
        assert submit_button.is_enabled()
        submit_button.click()
        page.get_by_text(
            '请先把模板“另存到我的模板”，再选择个人模板提交', exact=True
        ).wait_for()
        assert page.evaluate(
            "window.__marketRequests.filter(item => item.url.includes('/marketplace-submissions')).length"
        ) == 1

        install_button = page.get_by_role("button", name="安装到项目")
        install_button.wait_for()
        install_button.click()
        page.get_by_text("已安装为项目独立副本", exact=True).wait_for()
        install_request = page.evaluate(
            "window.__marketRequests.find(item => item.url.includes('/install'))"
        )
        assert install_request["method"] == "POST"
        assert "license_accepted" not in install_request["body"]

        page.get_by_role("button", name="评价已安装模板").click()
        page.get_by_role("button", name="更新我的评分").wait_for()
        rating_request = page.evaluate(
            "window.__marketRequests.find(item => item.url.includes('/rating'))"
        )
        assert rating_request["method"] == "PUT"
        assert rating_request["body"] == {
            "score": 5,
            "review_text": "实际生成后钩子更稳定",
            "expected_revision": 0,
        }

        page.get_by_role("button", name="立即使用").click()
        selection = page.evaluate(
            "Alpine.$data(document.querySelector('main')).creativeWorkspace."
            "promptLibrary.runSelections.script"
        )
        assert selection == {
            "templateKey": "generic-vertical-short",
            "revision": 2,
            "customRequirements": "",
        }
        assert page_errors == []
        browser.close()
