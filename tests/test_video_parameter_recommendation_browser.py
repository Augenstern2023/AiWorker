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


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        current.set_default_timeout(3_000)
        yield current
        browser.close()


def test_running_video_parameter_analysis_has_bounded_progress_feedback(
    page: Page,
) -> None:
    template = _source("templates/common/workspace/project_scripts.html")
    page.set_content(
        '<body data-workspace-page="writing-scripts">'
        '<main x-data="appData()" '
        'x-init="creativeWorkspace.project = window.__project; '
        'creativeWorkspace.videoParameters.record = window.__record; '
        'creativeWorkspace.videoParameters.job = window.__job">'
        f"{template}</main></body>"
    )
    page.add_script_tag(
        content="""
        window.__project = {
          id: 7, workflow_mode: 'free', target_episode_count: 1,
          resource_heads: [{stage: 'script', episode_number: 1, resource_ref: '31'}],
          episodes: [{
            id: 31, stage: 'script', episode_number: 1, version: 1,
            status: 'locked', content: {scenes: []}
          }]
        };
        window.__record = {
          status: 'running', updated_at: Date.now() / 1000 - 15
        };
        window.__job = {
          status: 'running', message: '正在分析镜头节奏',
          progress: {completed: 1, total: 3}
        };
        window.aiworkerApi = {request() { return new Promise(() => {}); }};
        """
    )
    page.add_script_tag(content=_source("static/js/common/tasks.js"))
    page.add_script_tag(content=_source("static/js/common/creative.js"))
    page.add_script_tag(content=_source("static/js/writing/creative_episodes.js"))
    page.add_script_tag(content=_source("static/js/common/prompt_templates.js"))
    page.add_script_tag(content=_source("static/js/common/creative_generation.js"))
    page.add_script_tag(content=_source("static/js/alpine.min.js"))

    progress = page.locator('[role="progressbar"]')
    progress.wait_for(state="visible")

    body = page.locator("section[aria-labelledby='video-parameter-heading'] .card-body")
    assert "正在分析镜头节奏" in body.inner_text()
    assert "最迟 45 秒后完成" in body.inner_text()
    assert "不会一直等待" in body.inner_text()
    assert body.locator(".spinner-border").count() == 0
    assert "33%" in progress.locator(".progress-bar").get_attribute("style")
