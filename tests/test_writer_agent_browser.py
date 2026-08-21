# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        current.set_default_timeout(2_000)
        yield current
        browser.close()


def mount_writer_agent(page: Page, *, session: dict) -> None:
    page.set_content(
        '<main id="writer-agent-workbench">'
        '<section id="writer-agent-project-context">'
        '<button id="writer-agent-create-project" type="button">创建原创项目</button>'
        '<button id="writer-agent-link-project" type="button">关联已有项目</button>'
        '<div id="writer-agent-project-picker" hidden></div></section>'
        '<section id="writer-agent-messages"></section>'
        '<form id="writer-agent-message-form">'
        '<textarea id="writer-agent-message"></textarea>'
        '<button type="submit">发送</button></form></main>'
    )
    page.add_script_tag(
        content="""
        window.__writerActionRequests = [];
        window.aiworkerApi = {
          async request(url, options = {}) {
            window.__writerActionRequests.push({
              url, method: options.method || 'GET',
              body: JSON.parse(options.body || '{}')
            });
            return {id: url.split('/').at(-2), status: 'queued'};
          }
        };
        """
    )
    page.add_script_tag(path=ROOT / "static/js/review/writer_agent.js")
    page.evaluate(
        "session => window.writerAgentWorkbench.mount(session)", session
    )


def test_writer_agent_renders_project_context_and_confirms_action(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "project_id": 9,
            "project": {"id": 9, "name": "情绪公司", "current_stage": "outline"},
            "messages": [],
            "actions": [
                {
                    "id": "a1",
                    "action_type": "creative.run_plan",
                    "status": "pending",
                    "revision": 2,
                    "title": "生成第1-5集剧本",
                    "summary": "复用已锁定大纲",
                    "payload": {
                        "target_stage": "script",
                        "episode_start": 1,
                        "episode_end": 5,
                    },
                }
            ],
        },
    )

    assert page.locator("#writer-agent-project-context").inner_text().startswith(
        "情绪公司"
    )
    page.get_by_role("button", name="确认生成").click()
    request = page.evaluate("window.__writerActionRequests[0]")
    assert request["url"].endswith("/actions/a1/decision")
    assert request["body"]["expected_revision"] == 2


def test_multiple_action_cards_disable_only_the_clicked_card(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {"id": "a1", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "大纲"},
                {"id": "a2", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "剧本"},
            ],
        },
    )

    cards = page.locator(".writer-action-card")
    cards.nth(0).get_by_role("button", name="确认生成").click()
    assert cards.nth(1).get_by_role("button", name="确认生成").is_enabled()


def test_writer_agent_renders_model_text_without_executing_html(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {
                    "id": "a1",
                    "action_type": "creative.run_plan",
                    "status": "pending",
                    "revision": 1,
                    "title": '<img src=x onerror="window.__xss=1">',
                    "summary": "安全文本",
                }
            ],
        },
    )

    assert page.locator(".writer-action-card img").count() == 0
    assert page.evaluate("window.__xss || 0") == 0


def test_writer_agent_has_no_horizontal_overflow_at_390px(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [
                {"id": "a1", "action_type": "creative.run_plan", "status": "pending", "revision": 1, "title": "生成全剧大纲"}
            ],
        },
    )

    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
