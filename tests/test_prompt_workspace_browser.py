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

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _modal(surface: str) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True
    )
    return environment.get_template(
        "common/workspace/prompt_library_modal.html"
    ).render(prompt_library_surface=surface)


def _install_workspace_runtime(
    page: Page, marketplace_data: dict | None = None
) -> list[str]:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    template = {
        "id": "personal-template-1",
        "template_key": "personal-template-1",
        "title": "我的强钩子模板",
        "description": "个人模板",
        "owner_scope": "user",
        "revision": 1,
        "supported_stages": ["brief"],
        "content": {
            "dimensions": {
                "format": "竖屏短剧",
                "genre": "都市",
                "audience": "大众",
                "story_engine": "身份错位",
                "emotion": "爽感",
                "pacing": "快节奏",
                "expression": "口语化",
            },
            "shared_dna": "开场快速建立未完成动作。",
            "parameters": {"max_beats": 3, "strong_ending_hook": True},
            "stage_rules": {
                "brief": {
                    "single": "生成 Brief",
                    "series": "生成多个 Brief",
                    "rewrite": "改写 Brief",
                    "repair": "修复 Brief",
                },
                "script": {
                    "single": "生成场次剧本",
                    "series": "批量生成场次剧本",
                }
            },
            "examples": [],
        },
    }
    page.add_script_tag(
        content=(
            f"window.__promptTemplate = {json.dumps(template, ensure_ascii=False)};"
            f"window.__marketplaceData = {json.dumps(marketplace_data, ensure_ascii=False)};"
        )
    )
    page.add_script_tag(
        content="""
        window.bootstrap = {
          Modal: {getOrCreateInstance(element) {
            return {
              show() { element.style.display = 'block'; element.classList.add('show'); },
              hide() { element.style.display = 'none'; element.classList.remove('show'); }
            };
          }},
          Tooltip: {getOrCreateInstance() { return {}; }}
        };
        window.aiworkerApi = {request(url) {
          if (url.includes('/prompt-modules/')) return Promise.resolve({ok: true, json: async () => ({
            settings: {enabled: true}, templates: [window.__promptTemplate],
            effective: window.__promptTemplate, stage_binding: null, episode_binding: null
          })});
          if (url.includes('/recommendations')) return Promise.resolve({ok: true, json: async () => ({items: []})});
          if (url.includes('/governance')) return Promise.resolve({ok: true, json: async () => ({items: [], policy: [], can_manage: true})});
          if (url.includes('/observability')) return Promise.resolve({ok: true, json: async () => ({summary: {}, coverage: {}, versions: [], alerts: [], watch_items: []})});
          if (url.includes('/prompt-marketplace')) return Promise.resolve({ok: true, json: async () => (window.__marketplaceData || {official_items: [], items: [], own_submissions: [], moderation_queue: [], source_types: [], policy: [], can_review: false, can_install: true})});
          return Promise.resolve({ok: true, json: async () => ({})});
        }};
        """
    )
    page.add_script_tag(content=_source("static/js/common/tasks.js"))
    page.add_script_tag(content=_source("static/js/common/creative.js"))
    page.add_script_tag(content=_source("static/js/common/prompt_templates.js"))
    page.add_script_tag(content=_source("static/js/alpine.min.js"))
    page.wait_for_timeout(400)
    return page_errors


def test_prompt_center_and_effects_open_focused_panels_without_browser_errors():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        center_page = browser.new_page()
        center_page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-personal" @click="openPromptWorkspacePanel(\'personal\')">我的模板</button>'
            '<button id="open-project" @click="openPromptWorkspacePanel(\'project\')">项目模板</button>'
            f"{_modal('center')}</main>"
        )
        center_errors = _install_workspace_runtime(center_page)
        center_page.locator("#open-personal").click()
        center_page.get_by_text("个人模板只有你能编辑", exact=False).wait_for()
        center_page.locator("#open-project").click()
        center_page.get_by_text("项目成员在这里看共享模板状态", exact=False).wait_for()
        assert center_errors == [], "\n".join(center_errors)

        effects_page = browser.new_page()
        effects_page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-observability" @click="openPromptWorkspacePanel(\'observability\')">效果数据</button>'
            f"{_modal('effects')}</main>"
        )
        effects_errors = _install_workspace_runtime(effects_page)
        effects_page.locator("#open-observability").click()
        effects_page.get_by_text("按模板版本汇总真实任务", exact=False).wait_for()
        effects_page.get_by_text("模板归因覆盖", exact=False).wait_for()
        assert effects_errors == [], "\n".join(effects_errors)

        browser.close()


def test_marketplace_cards_preview_one_stage_at_a_time():
    content = {
        "shared_dna": "市场模板共享 DNA",
        "stage_rules": {
            "brief": {"single": "市场 Brief 规则"},
            "script": {"single": "市场场次剧本规则"},
        },
    }
    marketplace_data = {
        "official_items": [
            {
                "id": "official-1",
                "template_key": "official-1",
                "title": "官方多阶段模板",
                "description": "官方模板说明",
                "tags": ["官方"],
                "compatibility": {"supported_stages": ["brief", "script"]},
                "content": content,
            }
        ],
        "items": [
            {
                "id": "community-1",
                "title": "社区多阶段模板",
                "description": "社区模板说明",
                "tags": ["社区"],
                "attribution_name": "测试作者",
                "install_count": 0,
                "rating": {"average": None, "count": 0},
                "installation": None,
                "is_owner": False,
                "source_url": "",
                "compatibility": {"supported_stages": ["brief", "script"]},
                "content": content,
            }
        ],
        "own_submissions": [],
        "moderation_queue": [],
        "source_types": [],
        "policy": [],
        "can_review": False,
        "can_install": True,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-marketplace" @click="openPromptWorkspacePanel(\'marketplace\')">打开模板市场</button>'
            f"{_modal('center')}</main>"
        )
        page_errors = _install_workspace_runtime(page, marketplace_data)
        page.locator("#open-marketplace").click()
        page.get_by_text("官方多阶段模板", exact=True).wait_for()

        official = page.locator("article").filter(has_text="官方多阶段模板")
        official.locator("summary").click()
        assert official.locator('[role="tab"]').all_inner_texts() == ["Brief", "场次剧本"]
        assert "市场 Brief 规则" in official.inner_text()
        assert "市场场次剧本规则" not in official.inner_text()
        official.locator('[role="tab"]').filter(has_text="场次剧本").click()
        assert "市场场次剧本规则" in official.inner_text()
        assert "市场 Brief 规则" not in official.inner_text()

        community = page.locator(".card").filter(has_text="社区多阶段模板")
        community.locator("summary").click()
        assert community.locator('[role="tab"]').all_inner_texts() == ["Brief", "场次剧本"]
        community.locator('[role="tab"]').filter(has_text="场次剧本").click()
        assert "市场场次剧本规则" in community.inner_text()
        assert "市场 Brief 规则" not in community.inner_text()
        assert page_errors == [], "\n".join(page_errors)
        browser.close()


def test_prompt_picker_card_click_selects_template_and_reveals_full_details():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'; "
            "creativeWorkspace.promptLibrary.settings = {enabled: true}; "
            "creativeWorkspace.promptLibrary.templates = [window.__promptTemplate]; "
            "creativeWorkspace.promptLibrary.effective = window.__promptTemplate\">"
            f"{_modal('picker')}</main>"
        )
        page_errors = _install_workspace_runtime(page)

        card = page.locator(".prompt-template-card")
        card.click()

        page.get_by_text("所选模板详情", exact=True).wait_for()
        assert page.locator('input[name="prompt-template"]').is_checked()
        indicator = card.locator('[aria-hidden="true"]').first
        assert "bg-primary" in (indicator.get_attribute("class") or "")
        assert indicator.inner_text() == "✓"
        details = page.locator("[data-prompt-template-details]")
        assert "我的强钩子模板" in details.inner_text()
        assert "开场快速建立未完成动作。" in details.inner_text()
        assert "生成 Brief" in details.inner_text()
        assert "生成场次剧本" not in details.inner_text()
        assert page.locator('[role="tab"]').all_inner_texts() == ["Brief", "场次剧本"]
        page.locator('[role="tab"]').filter(has_text="场次剧本").click()
        assert "生成场次剧本" in details.inner_text()
        assert "生成 Brief" not in details.inner_text()

        details.get_by_text("查看模板参数和示例", exact=True).click()
        details.get_by_text("节拍数量上限", exact=True).wait_for()
        assert details.get_by_text("3", exact=True).is_visible()
        assert details.get_by_text("强化结尾钩子", exact=True).is_visible()
        assert details.get_by_text("开启", exact=True).is_visible()
        raw_parameters = details.locator(".prompt-raw-parameters")
        raw_json = raw_parameters.locator(".prompt-parameter-json")
        assert not raw_parameters.evaluate("element => element.open")
        assert not raw_json.is_visible()
        raw_parameters.locator("summary").click()
        assert raw_json.is_visible()
        assert '"max_beats": 3' in raw_json.inner_text()
        assert raw_json.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) != "rgb(255, 255, 255)"
        assert details.locator('input[readonly], textarea[readonly]').count() == 0
        assert page_errors == [], "\n".join(page_errors)

        browser.close()


def test_personal_template_delete_previews_impact_and_removes_card():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-personal" @click="openPromptWorkspacePanel(\'personal\')">我的模板</button>'
            f"{_modal('center')}</main>"
        )
        page_errors = _install_workspace_runtime(page)
        page.locator("#open-personal").click()
        page.get_by_text("我的强钩子模板", exact=True).wait_for()
        page.evaluate(
            """
            window.__deleteEvents = [];
            window.__templateDeleted = false;
            window.confirm = () => {
              window.__deleteEvents.push('confirm');
              return true;
            };
            const originalRequest = window.aiworkerApi.request;
            window.aiworkerApi.request = (url, options = {}) => {
              if (url.includes('/deletion-impact')) {
                window.__deleteEvents.push('impact');
                return Promise.resolve({ok: true, json: async () => ({
                  can_delete: true,
                  market_submission_count: 0,
                  historical_data_preserved: true,
                  delete_blockers: [],
                })});
              }
              if (options.method === 'DELETE') {
                window.__deleteEvents.push(`delete:${JSON.parse(options.body).expected_revision}`);
                window.__templateDeleted = true;
                return Promise.resolve({ok: true, json: async () => ({deleted: true})});
              }
              if (window.__templateDeleted && url.includes('/prompt-modules/')) {
                return Promise.resolve({ok: true, json: async () => ({
                  settings: {enabled: true}, templates: [], effective: null,
                  stage_binding: null, episode_binding: null,
                })});
              }
              return originalRequest(url, options);
            };
            undefined;
            """
        )

        page.locator("article").filter(
            has_text="我的强钩子模板"
        ).locator("button.btn-outline-danger").click()
        page.get_by_text("当前步骤还没有个人模板", exact=False).wait_for()

        assert page.evaluate("window.__deleteEvents") == [
            "impact",
            "confirm",
            "delete:1",
        ]
        assert page.get_by_text("我的强钩子模板", exact=True).count() == 0
        assert page_errors == [], "\n".join(page_errors)
        browser.close()


def test_personal_template_delete_blocker_does_not_send_delete_request():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-personal" @click="openPromptWorkspacePanel(\'personal\')">我的模板</button>'
            f"{_modal('center')}</main>"
        )
        page_errors = _install_workspace_runtime(page)
        page.locator("#open-personal").click()
        page.get_by_text("我的强钩子模板", exact=True).wait_for()
        page.evaluate(
            """
            window.__deleteMethods = [];
            const originalRequest = window.aiworkerApi.request;
            window.aiworkerApi.request = (url, options = {}) => {
              window.__deleteMethods.push(options.method || 'GET');
              if (url.includes('/deletion-impact')) {
                return Promise.resolve({ok: true, json: async () => ({
                  can_delete: false,
                  delete_blockers: ['模板仍被项目默认设置、阶段或单集绑定使用，请先改绑'],
                  active_binding_count: 1,
                  current_project_binding_count: 1,
                  current_project_default_bindings: [],
                  current_project_stage_bindings: [{project_id: 9, stage: 'brief', episode_number: 0}],
                  other_project_references: [],
                  replacement_candidates: [],
                  can_manage_current_project: false,
                })});
              }
              return originalRequest(url, options);
            };
            undefined;
            """
        )

        page.locator("article").filter(
            has_text="我的强钩子模板"
        ).locator("button.btn-outline-danger").click()
        page.get_by_text("先改绑，再删除", exact=True).wait_for()
        page.get_by_text("只有项目负责人可以批量改绑", exact=False).wait_for()

        assert "DELETE" not in page.evaluate("window.__deleteMethods")
        assert page_errors == [], "\n".join(page_errors)
        browser.close()


def test_personal_template_can_rebind_and_delete_from_inline_recovery_panel():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(
            '<main x-data="appData()" '
            'x-init="creativeWorkspace.project = {id: 9, target_episode_count: 20}; '
            "creativeWorkspace.promptLibrary.stage = 'brief'\">"
            '<button id="open-personal" @click="openPromptWorkspacePanel(\'personal\')">我的模板</button>'
            f"{_modal('center')}</main>"
        )
        page_errors = _install_workspace_runtime(page)
        page.locator("#open-personal").click()
        page.get_by_text("我的强钩子模板", exact=True).wait_for()
        page.evaluate(
            """
            window.__rebindEvents = [];
            window.__templateDeleted = false;
            window.confirm = () => {
              window.__rebindEvents.push('confirm');
              return true;
            };
            const originalRequest = window.aiworkerApi.request;
            window.aiworkerApi.request = (url, options = {}) => {
              if (url.includes('/deletion-impact')) {
                window.__rebindEvents.push('impact');
                return Promise.resolve({ok: true, json: async () => ({
                  can_delete: false,
                  active_binding_count: 1,
                  current_project_binding_count: 1,
                  current_project_default_bindings: [],
                  current_project_stage_bindings: [
                    {project_id: 9, stage: 'brief', episode_number: 0},
                  ],
                  other_project_references: [],
                  replacement_candidates: [{
                    id: 'system:generic-vertical-short',
                    template_key: 'generic-vertical-short',
                    title: '通用竖屏短剧',
                    owner_scope: 'system',
                    revision: 1,
                    supported_stages: ['brief'],
                  }],
                  can_manage_current_project: true,
                })});
              }
              if (url.includes('/rebind-and-delete') && options.method === 'POST') {
                const body = JSON.parse(options.body);
                window.__rebindEvents.push(`rebind:${body.replacement_template_id}:${body.expected_revision}`);
                window.__templateDeleted = true;
                return Promise.resolve({ok: true, json: async () => ({
                  rebound: true, deleted: true, remaining_binding_count: 0,
                })});
              }
              if (window.__templateDeleted && url.includes('/prompt-modules/')) {
                return Promise.resolve({ok: true, json: async () => ({
                  settings: {enabled: true}, templates: [], effective: null,
                  stage_binding: null, episode_binding: null,
                })});
              }
              return originalRequest(url, options);
            };
            undefined;
            """
        )

        page.locator("article").filter(
            has_text="我的强钩子模板"
        ).locator("button.btn-outline-danger").click()
        page.get_by_text("先改绑，再删除", exact=True).wait_for()
        assert page.locator("#prompt-delete-replacement").input_value() == "system:generic-vertical-short"
        page.locator("#prompt-delete-rebind-panel button.btn-danger").click()
        page.get_by_text("当前步骤还没有个人模板", exact=False).wait_for()

        assert page.evaluate("window.__rebindEvents") == [
            "impact",
            "confirm",
            "rebind:system:generic-vertical-short:1",
        ]
        assert page_errors == [], "\n".join(page_errors)
        browser.close()
