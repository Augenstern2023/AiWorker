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


def test_action_card_renders_server_ledger_progress_and_result_link(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [],
            "actions": [{
                "id": "ledger-action",
                "action_type": "creative.run_plan",
                "status": "succeeded",
                "revision": 4,
                "title": "生成第1-2集剧本",
                "summary": "共 2 个可审计步骤",
                "completed_steps": 2,
                "total_steps": 2,
                "resource_refs": [
                    {"resource_type": "script", "resource_id": "501"}
                ],
                "open_url": "/writing/projects",
            }],
        },
    )

    card = page.locator(".writer-action-card")
    assert card.get_by_text("2 / 2").is_visible()
    assert card.get_by_text("script").is_visible()
    assert card.get_by_role("link", name="打开成果").get_attribute("href") == (
        "/writing/projects"
    )


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


def test_writer_agent_ignores_server_owned_cards_in_message_history(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "messages": [
                {
                    "role": "assistant",
                    "content": "已整理创作方案",
                    "cards": [
                        {
                            "card_id": "directions-1",
                            "card_type": "directions",
                            "items": [
                                {
                                    "title": "情绪审判庭",
                                    "protagonist": "劳动仲裁律师",
                                    "background": "情绪量化城市",
                                    "core_conflict": "替同事夺回被清零的工资",
                                    "relationship_engine": "律师与前主管互相试探",
                                    "emotional_payoff": "普通人夺回情绪解释权",
                                    "difference_dimensions": ["protagonist", "conflict"],
                                }
                            ],
                        },
                        {
                            "card_id": "brief-1",
                            "card_type": "brief",
                            "content": {"title": "情绪公司", "logline": "愤怒会让工资归零"},
                            "assumptions": ["每集 2 分钟"],
                        },
                        {
                            "card_id": "plan-1",
                            "card_type": "plan",
                            "title": "生成全剧大纲",
                            "summary": "补齐创作基线",
                            "steps": [{"stage": "brief", "kind": "lock"}],
                        },
                        {
                            "card_id": "progress-1",
                            "card_type": "progress",
                            "action_id": "a1",
                            "status": "running",
                            "completed_steps": 2,
                            "total_steps": 5,
                        },
                        {
                            "card_id": "result-1",
                            "card_type": "result",
                            "project_id": 9,
                            "resource_refs": [{"resource_type": "outline", "version": 2}],
                            "open_url": "/writing/projects/9/outlines",
                        },
                        {
                            "card_id": "conflict-1",
                            "card_type": "conflict",
                            "code": "STALE_PROJECT_CONTEXT",
                            "message": "项目版本已经变化",
                            "current_revision": 12,
                        },
                    ],
                }
            ],
            "actions": [],
        },
    )

    cards = page.locator(".writer-creative-card")
    assert cards.count() == 4
    assert cards.nth(0).get_by_text("情绪审判庭").is_visible()
    assert cards.nth(1).get_by_text("愤怒会让工资归零").is_visible()
    assert cards.nth(2).get_by_text("补齐创作基线").is_visible()
    assert cards.nth(3).get_by_text("项目版本已经变化").is_visible()
    assert page.get_by_text("2 / 5").count() == 0
    assert page.get_by_role("link", name="打开项目").count() == 0
    assert cards.nth(3).get_by_role("button", name="重新生成计划").is_visible()


def test_writer_agent_project_context_shows_mode_stage_and_valid_entry(page: Page) -> None:
    mount_writer_agent(
        page,
        session={
            "id": 1,
            "project_id": 9,
            "project": {
                "id": 9,
                "name": "情绪公司",
                "creation_mode": "original",
                "outline_batch_id": "outlines-v2",
            },
            "messages": [],
            "actions": [],
        },
    )

    context = page.locator("#writer-agent-project-context")
    assert "原创" in context.inner_text()
    assert "全剧大纲" in context.inner_text()
    assert context.get_by_role("link", name="打开项目").get_attribute("href") == "/writing/projects/9/outlines"


def test_writer_agent_project_picker_submits_structured_project_id(page: Page) -> None:
    mount_writer_agent(
        page,
        session={"id": 1, "messages": [], "actions": []},
    )
    page.evaluate(
        """
        window.__linkedProjectIds = [];
        window.writerAgentWorkbench.configure({
          sendProjectLink(projectId) { window.__linkedProjectIds.push(projectId); }
        });
        """
    )
    page.evaluate(
        """
        () => {
          window.aiworkerApi.request = async (url, options = {}) => {
            window.__writerActionRequests.push({url, method: options.method || 'GET'});
            if (url.startsWith('/api/creative/projects')) {
              return {items: [{id: 23, name: '可关联项目'}]};
            }
            return {};
          };
        }
        """
    )

    page.get_by_role("button", name="关联已有项目").click()
    page.get_by_role("button", name="可关联项目").click()

    assert page.evaluate("window.__linkedProjectIds") == [23]
