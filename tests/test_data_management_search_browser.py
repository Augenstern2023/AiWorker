# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for data-management search feedback."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data_search_page() -> Iterator[Page]:
    template = (ROOT / "templates/common/workspace/admin_data.html").read_text(
        encoding="utf-8"
    )
    workspace_script = (ROOT / "static/js/admin/workspace.js").read_text(
        encoding="utf-8"
    )
    bootstrap_css = (ROOT / "static/css/bootstrap.min.css").read_text(encoding="utf-8")
    workspace_css = (ROOT / "static/css/workspaces.css").read_text(encoding="utf-8")
    alpine_script = ROOT / "static/js/alpine.min.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(2_000)
        page.set_content(f'<main x-data="appData()">{template}</main>')
        page.add_style_tag(content=f"{bootstrap_css}\n{workspace_css}")
        page.add_script_tag(content=workspace_script)
        page.evaluate(
            """
            window.aiworkerApi = {
              request: async (url) => {
                window.requestedFileUrls.push(url);
                const query = new URL(url, 'http://aiworker.test').searchParams.get('query');
                await new Promise(resolve => setTimeout(resolve, query === 'slow' ? 500 : 20));
                window.resolvedQueries.push(query);
                if (query === 'error' && window.errorAttempts++ === 0) {
                  return new Response(JSON.stringify({detail: '服务暂时不可用'}), {status: 500});
                }
                const item = {
                  name: 'douyin_analysis_overview.md',
                  path: 'data/dy/jobs/20260814-100008-dy-8590/analysis/douyin_analysis_overview.md',
                  size: 100,
                  modified: 1,
                  file_group: '剧本与文稿',
                  drama_name: '其他数据',
                  preview_kind: 'text'
                };
                const total = query === 'fast' ? 2 : 144;
                return new Response(JSON.stringify({
                  items: query === 'none' ? [] : [item],
                  total: query === 'none' ? 0 : total,
                  next_cursor: null
                }), {status: 200});
              }
            };
            window.requestedFileUrls = [];
            window.resolvedQueries = [];
            window.errorAttempts = 0;
            window.appData = () => Object.assign({
              fileFilter: 'all', fileSearch: '', files: [], filteredFiles: [],
              selectedFilePaths: [], batchDeleteMode: false, batchDownloadMode: false,
              filePage: {
                total: 0, currentCursor: null, nextCursor: null, cursorHistory: [],
                loading: false, searchTimer: null, requestSequence: 0, selectionMode: 'explicit',
                selectionFilter: 'all', selectionQuery: '', excludedPaths: [],
                operation: null, error: '', appliedQuery: ''
              },
              formatSize: value => `${value} B`,
              formatDate: value => String(value)
            }, ...window.aiworkerModules.map(factory => factory()));
            """
        )
        page.add_script_tag(path=alpine_script)
        page.wait_for_selector("#file-search-input")
        yield page
        browser.close()


def test_search_reports_path_matches_even_when_the_visible_list_looks_unchanged(
    data_search_page: Page,
) -> None:
    """Removing live result feedback must make this regression test fail."""
    page = data_search_page

    page.locator("#file-search-input").fill("100")
    assert page.locator("#file-search-status").inner_text() == "正在检索“100”…"
    assert page.locator("#file-search-input").get_attribute("aria-busy") == "true"
    assert page.get_by_role("button", name="刷新").is_disabled()
    page.wait_for_function("window.requestedFileUrls.length === 1")
    page.wait_for_function("!Alpine.$data(document.querySelector('main')).filePage.loading")

    assert "query=100" in page.evaluate("window.requestedFileUrls[0]")
    assert page.locator("#file-search-status").inner_text() == (
        "已按文件名或路径检索“100”，找到 144 个文件"
    )


def test_search_failure_is_visible_and_retryable(data_search_page: Page) -> None:
    """Swallowing a failed search request must make this regression test fail."""
    page = data_search_page

    page.locator("#file-search-input").fill("error")
    page.wait_for_function("window.requestedFileUrls.length === 1")
    page.wait_for_function("!Alpine.$data(document.querySelector('main')).filePage.loading")

    assert page.locator("#file-search-status").inner_text() == "文件加载失败，请重试"
    page.locator("#retry-file-search").click()
    page.wait_for_function("window.requestedFileUrls.length === 2")
    page.wait_for_function("!Alpine.$data(document.querySelector('main')).filePage.loading")

    assert page.locator("#file-search-status").inner_text() == (
        "已按文件名或路径检索“error”，找到 144 个文件"
    )


def test_search_empty_result_explains_that_no_file_matched(data_search_page: Page) -> None:
    page = data_search_page

    page.locator("#file-search-input").fill("none")
    page.wait_for_function("window.requestedFileUrls.length === 1")
    page.wait_for_function("!Alpine.$data(document.querySelector('main')).filePage.loading")

    assert page.get_by_text("未找到匹配文件", exact=True).is_visible()


def test_slow_old_search_cannot_overwrite_the_latest_result(data_search_page: Page) -> None:
    """Removing request sequencing must let the slow response overwrite the latest query."""
    page = data_search_page

    page.locator("#file-search-input").fill("slow")
    page.wait_for_function("window.requestedFileUrls.length === 1")
    page.locator("#file-search-input").fill("fast")
    page.wait_for_function("window.requestedFileUrls.length === 2")
    page.wait_for_function("window.resolvedQueries.length === 2")

    assert page.locator("#file-search-status").inner_text() == (
        "已按文件名或路径检索“fast”，找到 2 个文件"
    )


def test_search_toolbar_wraps_below_the_title_on_mobile(data_search_page: Page) -> None:
    page = data_search_page
    page.set_viewport_size({"width": 390, "height": 844})

    title_box = page.get_by_role("heading", name="数据管理").bounding_box()
    search_box = page.locator("#file-search-input").bounding_box()

    assert title_box is not None and title_box["width"] > 80 and title_box["height"] < 40
    assert search_box is not None and search_box["y"] >= title_box["y"] + title_box["height"]


def test_bulk_selection_control_stays_hidden_outside_bulk_mode(
    data_search_page: Page,
) -> None:
    assert data_search_page.get_by_text("全选当前结果", exact=False).is_hidden()
