# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
#
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Iterator
import json
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


def _manifest() -> dict:
    return {
        "project": {"id": 11, "name": "雾港来信", "revision": 3},
        "project_id": 11,
        "audit_scope": "outline",
        "brief": {
            "id": 31, "stage_kind": "brief", "version": 3,
            "content_chars": 1200, "summary": "雾港谜案的核心设定",
            "source_url": "/planning/projects/11/brief",
        },
        "characters": {
            "batch_id": "characters-v3", "character_count": 4,
            "relationship_count": 5, "content_chars": 2300, "items": [],
            "relationships": [],
        },
        "outline_count": 90,
        "outline_episode_numbers": list(range(1, 91)),
        "missing_episode_numbers": [17],
        "stages": {
            "outline": {
                "stage_kind": "outline", "batch_id": "outlines-v8",
                "episode_numbers": list(range(1, 91)), "unit_count": 90,
                "content_chars": 123456,
                "units": [{
                    "episode_number": 1,
                    "resource": {
                        "id": 501, "stage_kind": "outline", "episode_number": 1,
                        "version": 8, "status": "locked", "content_chars": 1400,
                        "summary": "第一集大纲", "source_url": "/writing/projects/11/outlines",
                    },
                }],
            },
        },
        "unreadable_resources": [],
        "upstream_mismatches": [{"episode_number": 17, "stage_kind": "outline"}],
        "excluded_statuses": ["historical", "superseded"],
    }


def _review(status: str, *, current_episode: int = 0, attempt: int = 1) -> dict:
    report = {
        "all_unit_results": [{"episode_number": number} for number in range(1, current_episode)],
        "source_manifest": _manifest(),
        "request_started_at": 1_000_000,
        "request_attempt": attempt,
        "current_episode": current_episode,
        "batch_start": 31 if current_episode else 0,
        "batch_end": 40 if current_episode else 0,
    }
    return {
        "id": 87, "status": status, "audit_scope": "outline",
        "source_payload": {"manifest": _manifest()}, "report": report,
        "error": "模型暂时不可用" if status == "failed" else "",
    }


def mount_review_ui(
    page: Page, responses: list[dict], *, job_responses: list[dict] | None = None
) -> None:
    page.set_content(
        '<main><section id="writer-agent-conversation"></section>'
        '<aside id="writer-project-review-drawer" class="writer-project-review-drawer" '
        'hidden aria-hidden="true"><button id="writer-project-review-close" '
        'type="button">关闭</button><div id="writer-project-review-content"></div>'
        '</aside></main>'
    )
    page.add_script_tag(
        content="""
        window.__reviewRequests = [];
        window.__reviewResponses = %s;
        window.__reviewJobResponses = %s;
        window.aiworkerApi = { async request(url, options = {}) {
          window.__reviewRequests.push({url, method: options.method || 'GET', body: JSON.parse(options.body || '{}')});
          const next = url.startsWith('/api/jobs/')
            ? window.__reviewJobResponses.shift()
            : window.__reviewResponses.shift();
          if (next && next.__error) throw new Error(next.__error);
          return {ok: true, async json() { return next; }};
        }};
        """ % (
            json.dumps(responses, ensure_ascii=False),
            json.dumps(job_responses or [], ensure_ascii=False),
        )
    )
    page.add_script_tag(path=ROOT / "static/js/review/writer_project_review.js")


def test_project_review_offer_previews_frozen_manifest_before_confirmation(page: Page) -> None:
    """Would catch confirming a long review without showing its frozen source first."""
    preview = {"audit_scope": "outline", "manifest": _manifest(), "fingerprint": "frozen-v3"}
    mount_review_ui(page, [preview, {"review": _review("queued")}])
    article = page.locator("#writer-agent-conversation")
    page.evaluate(
        """([article]) => window.writerProjectReviewUI.renderMessage(article, {
            kind: 'project_review_offer', role: 'assistant', audit_scope: 'outline'
        }, {session: {id: 44}})""",
        [article.element_handle()],
    )

    page.get_by_role("button", name="查看本次加载内容").click()
    drawer = page.locator("#writer-project-review-drawer")
    drawer.get_by_text("本次加载内容").wait_for()
    assert drawer.get_by_text("项目 #11").is_visible()
    assert drawer.get_by_text("Brief v3").is_visible()
    assert drawer.get_by_text("实际加载第 1–90 集").is_visible()
    assert drawer.get_by_text("历史废弃版本").is_visible()
    assert drawer.get_by_role("button", name="确认并开始完整审核").is_enabled()

    drawer.get_by_text("Brief v3").click()
    assert drawer.get_by_text("雾港谜案的核心设定").is_visible()
    assert drawer.get_by_role("link", name="打开源内容").get_attribute("href") == "/planning/projects/11/brief"

    drawer.get_by_role("button", name="确认并开始完整审核").click()
    request = page.evaluate("window.__reviewRequests")[-1]["body"]
    assert request["audit_scope"] == "outline"
    assert request["expected_fingerprint"] == "frozen-v3"
    assert request["client_request_id"]


def test_project_review_running_slow_retry_resume_and_download_states(page: Page) -> None:
    """Would catch losing worker-owned checkpoint progress or its recovery controls."""
    now = 1_000_000 + 8 * 60 + 1
    mount_review_ui(page, [
        _review("running", current_episode=31, attempt=2),
        _review("failed", current_episode=31, attempt=2),
        _review("failed", current_episode=31, attempt=2),
        {"review": _review("running", current_episode=31, attempt=2)},
        _review("completed", current_episode=90, attempt=2),
    ])
    page.evaluate(f"Date.now = () => {now * 1000};")
    page.evaluate("window.setInterval = callback => { window.__reviewPoll = callback; return 99; }; window.clearInterval = () => {};")
    page.evaluate("window.writerProjectReviewUI.mountSession({id: 44, messages: [{kind: 'project_review', review_id: 87}]}, {})")

    drawer = page.locator("#writer-project-review-drawer")
    drawer.get_by_text("正在审核第 31 集（当前批次第 31–40 集）").wait_for()
    assert drawer.get_by_text("模型响应较慢，任务仍在运行").is_visible()
    assert drawer.get_by_text("第 31 集正在进行第 2 次尝试").is_visible()

    page.keyboard.press("Escape")
    assert drawer.is_hidden()
    page.evaluate("window.__reviewPoll()")
    assert drawer.is_hidden()
    assert len(page.evaluate("window.__reviewRequests")) == 2
    page.evaluate("window.writerProjectReviewUI.renderMessage(document.querySelector('#writer-agent-conversation'), {kind: 'project_review', review_id: 87}, {session: {id: 44}})")
    page.get_by_role("button", name="查看审核进度").click()
    drawer.get_by_text("从第 31 集继续").wait_for()
    drawer.get_by_role("button", name="从第 31 集继续").click()
    page.evaluate("window.__reviewPoll()")
    drawer.get_by_text("已完成 90/90 集").wait_for()
    assert drawer.get_by_role("link", name="下载审核报告").is_visible()


def test_project_review_uses_worker_event_payload_for_live_request_metadata(page: Page) -> None:
    """Would catch reading live request metadata from an unpersisted review report."""
    now = 1_000_000 + 8 * 60 + 1
    review = _review("running")
    review["job_id"] = "project-review-job-87"
    review["report"] = {"source_manifest": _manifest(), "all_unit_results": []}
    mount_review_ui(page, [review], job_responses=[{
        "job_id": "project-review-job-87", "status": "running",
        "events": [{"event": "progress", "payload": {
            "request_kind": "unit_review", "stage_kind": "outline",
            "current_episode": 31, "batch_start": 31, "batch_end": 40,
            "request_started_at": 1_000_000, "request_attempt": 2,
            "reasoning_effort": "high", "prompt_chars": 4567,
            "loaded_resource_refs": [{
                "kind": "resource", "id": 501, "version": 8,
                "stage_kind": "outline", "episode_number": 31,
            }],
        }}],
    }])
    page.evaluate(f"Date.now = () => {now * 1000};")
    page.evaluate("window.setInterval = () => 99")
    page.evaluate("window.writerProjectReviewUI.mountSession({id: 44, messages: [{kind: 'project_review', review_id: 87}]}, {})")

    drawer = page.locator("#writer-project-review-drawer")
    drawer.get_by_text("正在审核第 31 集（当前批次第 31–40 集）").wait_for()
    assert drawer.get_by_text("模型响应较慢，任务仍在运行").is_visible()
    assert drawer.get_by_text("第 31 集正在进行第 2 次尝试").is_visible()
    assert drawer.get_by_text("当前请求内容").is_visible()
    assert drawer.get_by_text("逐集审核 · 大纲").is_visible()
    assert drawer.get_by_text("提示词 4,567 字符 · 推理档位 high").is_visible()
    assert drawer.get_by_text("大纲第 31 集 · 资源 #501 · v8").is_visible()
    assert "/api/jobs/project-review-job-87" in [
        request["url"] for request in page.evaluate("window.__reviewRequests")
    ]


def test_project_review_reuses_its_create_id_after_an_ambiguous_network_failure(page: Page) -> None:
    """Would catch a lost create response starting a second long-running review."""
    preview = {"audit_scope": "outline", "manifest": _manifest(), "fingerprint": "stable-fingerprint"}
    mount_review_ui(page, [
        preview, {"__error": "连接中断"}, preview, {"review": _review("queued")},
    ])
    page.evaluate("window.writerProjectReviewUI.renderMessage(document.querySelector('#writer-agent-conversation'), {kind: 'project_review_offer', audit_scope: 'outline'}, {session: {id: 44}})")
    page.get_by_role("button", name="查看本次加载内容").click()
    page.get_by_role("button", name="确认并开始完整审核").click()
    page.get_by_role("button", name="重试").click()
    page.get_by_role("button", name="确认并开始完整审核").click()

    creates = [
        request["body"] for request in page.evaluate("window.__reviewRequests")
        if request["url"] == "/api/writer-agent/sessions/44/project-reviews"
    ]
    assert len(creates) == 2
    assert creates[0]["client_request_id"] == creates[1]["client_request_id"]


def test_project_review_drawer_is_keyboard_safe_and_mobile_without_motion(page: Page) -> None:
    """Would catch an inaccessible drawer or a 760px horizontal overflow regression."""
    page.set_viewport_size({"width": 760, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    mount_review_ui(page, [{"audit_scope": "outline", "manifest": _manifest(), "fingerprint": "mobile-fingerprint"}])
    page.evaluate("window.writerProjectReviewUI.renderMessage(document.querySelector('#writer-agent-conversation'), {kind: 'project_review_offer', audit_scope: 'outline'}, {session: {id: 44}})")
    page.get_by_role("button", name="查看本次加载内容").press("Enter")
    drawer = page.locator("#writer-project-review-drawer")
    assert drawer.get_attribute("aria-hidden") == "false"
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.keyboard.press("Escape")
    assert drawer.is_hidden()
