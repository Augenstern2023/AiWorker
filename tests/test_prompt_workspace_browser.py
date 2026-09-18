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


def _install_workspace_runtime(page: Page) -> list[str]:
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
            "parameters": {},
            "stage_rules": {
                "brief": {
                    "single": "生成 Brief",
                    "series": "生成多个 Brief",
                    "rewrite": "改写 Brief",
                    "repair": "修复 Brief",
                }
            },
            "examples": [],
        },
    }
    page.add_script_tag(content=f"window.__promptTemplate = {json.dumps(template, ensure_ascii=False)};")
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
          if (url.includes('/prompt-marketplace')) return Promise.resolve({ok: true, json: async () => ({items: [], own_submissions: [], moderation_queue: [], licenses: [], source_types: [], policy: [], can_review: false, can_install: true})});
          return Promise.resolve({ok: true, json: async () => ({})});
        }};
        """
    )
    page.add_script_tag(content=_source("static/js/common/creative.js"))
    page.add_script_tag(content=_source("static/js/common/tasks.js"))
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
        assert page_errors == [], "\n".join(page_errors)

        browser.close()
