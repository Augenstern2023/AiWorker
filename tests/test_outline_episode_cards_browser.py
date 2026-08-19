# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for the episode-outline card workspace."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]

OUTLINE_ITEMS = [
    {"episode_number": 1, "content": {"core_event": "密室账本曝光"}},
    {"episode_number": 2, "content": {"core_event": "董事会逼宫"}},
    {"episode_number": 12, "content": {"core_event": "终局反击"}},
]

CARD_OUTLINE_ITEMS = [
    {
        "id": 101,
        "episode_number": 1,
        "stage": "outline",
        "status": "draft",
        "content": {
            "core_event": "密室账本曝光，林澈发现旧厂财务记录被人系统性篡改",
            "conflict": "董事会要求立即停产，林澈坚持继续追查",
            "character_progress": "林澈从被动接手转为主动承担",
            "highlight": "林澈用废弃仓库中的旧样品现场反证",
            "reversal": "匿名举报人正是财务主管",
            "cliffhanger": "仓库监控拍到一个本应离职的人",
        },
    },
    {
        "id": 102,
        "episode_number": 2,
        "stage": "outline",
        "status": "draft",
        "content": {
            "core_event": "董事会逼宫，林澈用隐藏订单争取三天时间",
            "conflict": "资金链断裂与内部质疑同时升级",
            "character_progress": "合伙人首次公开支持林澈",
            "highlight": "隐藏订单在最后一分钟完成验真",
            "reversal": "订单客户与竞争对手存在关联",
            "cliffhanger": "财务主管收到匿名离职威胁",
        },
    },
    {
        "id": 112,
        "episode_number": 12,
        "stage": "outline",
        "status": "draft",
        "content": {
            "core_event": "终局反击，所有伏笔在发布会现场闭环",
            "conflict": "林澈必须在直播中证明完整证据链",
            "character_progress": "团队完成最终和解",
            "highlight": "直播观众共同补全关键时间线",
            "reversal": "真正主谋主动进入直播间",
            "cliffhanger": "新订单来自一个未知海外地址",
        },
    },
]


@pytest.fixture
def outline_state_page() -> Iterator[Page]:
    """Load the real page module around a small, deterministic outline state."""
    outline_script = ROOT / "static/js/writing/outlines.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(2_000)
        page.set_content('<body data-workspace-page="writing-outlines"></body>')
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=outline_script)
        page.evaluate(
            """
            ([items]) => {
              window.state = Object.assign({
                creativeWorkspace: {},
                outlineItems: items,
                currentProjectOutlines() { return this.outlineItems; }
              }, window.aiworkerModules.at(-1)?.());
            }
            """,
            [OUTLINE_ITEMS],
        )
        yield page
        browser.close()


@pytest.fixture
def outline_cards_page() -> Iterator[Page]:
    """Render the real outline workspace with its page script and Alpine."""
    template = (ROOT / "templates/common/workspace/project_outlines.html").read_text(
        encoding="utf-8"
    )
    outline_script = ROOT / "static/js/writing/outlines.js"
    bootstrap_css = (ROOT / "static/css/bootstrap.min.css").read_text(encoding="utf-8")
    workspace_css = (ROOT / "static/css/workspaces.css").read_text(encoding="utf-8")
    alpine_script = ROOT / "static/js/alpine.min.js"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(2_000)
        page.set_content(
            f'<main class="main-content" x-data="appData()">{template}</main>'
        )
        page.add_style_tag(content=f"{bootstrap_css}\n{workspace_css}")
        page.evaluate("window.aiworkerModules = []")
        page.add_script_tag(path=outline_script)
        page.evaluate(
            """
            ([items]) => {
              window.savedEpisodeNumbers = [];
              window.updateEpisodeNumbers = [];
              window.appData = () => Object.assign({
                outlineItems: items,
                creativeWorkspace: {
                  project: {
                    id: 8, target_episode_count: 3, locked_brief_id: 4,
                    character_batch_id: 'characters-v2', outline_batch_id: 'outlines-v3'
                  },
                  outlineQuery: '', expandedOutlineEpisodes: {}, outlineCardContextKey: '',
                  selectedOutlineBriefId: 4, selectedOutlineCharacterBatchId: 'characters-v2',
                  startingAction: '', saveState: {}
                },
                currentProjectOutlines() { return this.outlineItems; },
                creativeWorkflowStatusLabel(status) {
                  return status === 'draft' ? '草稿' : '已完成';
                },
                currentOutlineBatchStatus() { return this.outlineItems[0]?.status || 'draft'; },
                projectHasGeneratedScripts() { return true; },
                mergedScriptDownloadUrl() { return '/api/creative/projects/8/scripts/download'; },
                generateProjectOutlines() {},
                creativeActionDisabled() { return false; },
                creativeActionRunning() { return false; },
                outlineGenerationActionLabel() { return '重新生成'; },
                creativeResourceEditable(item) { return item?.status === 'draft'; },
                completeProjectOutlines() {},
                latestOutlineBatchId() { return 'outlines-v3'; },
                currentGeneratedResourceId() { return ''; },
                creativeGeneratedDownloadUrl() { return '#download-outline'; },
                availableLockedBriefs() { return []; },
                selectOutlineBrief() {},
                availableLockedCharacterBatches() { return []; },
                selectOutlineCharacterBatch() {},
                projectEpisodeScript(episodeNumber) {
                  return Number(episodeNumber) === 1 ? {status: 'locked'} : null;
                },
                projectEpisodeScriptLabel(episodeNumber) {
                  return Number(episodeNumber) === 1 ? '剧本已完成' : '';
                },
                hasProjectEpisodeUpdates(episodeNumber) {
                  return Number(episodeNumber) === 1;
                },
                projectEpisodeUpdateSummary(episodeNumber) {
                  return Number(episodeNumber) === 1 ? 'Beats 更新' : '';
                },
                openOutlineEpisodeUpdates(episodeNumber) {
                  window.updateEpisodeNumbers.push(Number(episodeNumber));
                },
                saveProjectEpisode(item) {
                  window.savedEpisodeNumbers.push(Number(item.episode_number));
                }
              }, ...window.aiworkerModules.map(factory => factory()));
            }
            """,
            [CARD_OUTLINE_ITEMS],
        )
        page.add_script_tag(path=alpine_script)
        page.wait_for_function(
            "window.Alpine && Alpine.$data(document.querySelector('main')).outlineItems.length === 3"
        )
        yield page
        browser.close()


def filtered_episode_numbers(page: Page) -> list[int]:
    return page.evaluate(
        "state.filteredProjectOutlines().map(item => item.episode_number)"
    )


def expanded_episode_numbers(page: Page) -> list[int]:
    return page.evaluate(
        """
        state.outlineItems
          .filter(item => state.outlineEpisodeExpanded(item.episode_number))
          .map(item => item.episode_number)
        """
    )


def test_outline_filter_matches_episode_number_and_core_event(
    outline_state_page: Page,
) -> None:
    """Removing either supported search path must make this test fail."""
    page = outline_state_page

    assert filtered_episode_numbers(page) == [1, 2, 12]
    page.evaluate("state.creativeWorkspace.outlineQuery = '2'")
    assert filtered_episode_numbers(page) == [2, 12]
    page.evaluate("state.creativeWorkspace.outlineQuery = '  密室  '")
    assert filtered_episode_numbers(page) == [1]
    assert page.evaluate(
        "state.filteredProjectOutlines()[0] === state.outlineItems[0]"
    )


def test_outline_expand_and_collapse_actions_only_change_visible_results(
    outline_state_page: Page,
) -> None:
    """Batch actions must not disturb cards hidden by the current filter."""
    page = outline_state_page

    page.evaluate("state.toggleOutlineEpisode(1)")
    assert expanded_episode_numbers(page) == [1]
    page.evaluate("state.creativeWorkspace.outlineQuery = '董事会'")
    page.evaluate("state.expandAllOutlineEpisodes()")
    assert expanded_episode_numbers(page) == [1, 2]
    page.evaluate("state.collapseAllOutlineEpisodes()")
    assert expanded_episode_numbers(page) == [1]


def test_outline_card_context_only_resets_for_a_different_project_or_version(
    outline_state_page: Page,
) -> None:
    """Refreshing the same context must not unexpectedly close a writer's card."""
    page = outline_state_page
    original = {
        "id": 8,
        "locked_brief_id": 4,
        "character_batch_id": "characters-v2",
        "outline_batch_id": "outlines-v3",
    }

    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", original)
    page.evaluate("state.creativeWorkspace.outlineQuery = '密室'")
    page.evaluate("state.toggleOutlineEpisode(1)")
    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", original)
    assert page.evaluate("state.creativeWorkspace.outlineQuery") == "密室"
    assert expanded_episode_numbers(page) == [1]

    changed = {**original, "outline_batch_id": "outlines-v4"}
    page.evaluate("project => state.syncOutlineEpisodeCardContext(project)", changed)
    assert page.evaluate("state.creativeWorkspace.outlineQuery") == ""
    assert expanded_episode_numbers(page) == []


def test_outline_cards_default_to_compact_previews_and_keep_actions_visible(
    outline_cards_page: Page,
) -> None:
    """Restoring the wide table or hiding actions in details must fail here."""
    page = outline_cards_page

    assert page.locator(".outline-episode-card").count() == 3
    assert page.locator("table").count() == 0
    assert page.locator(".outline-episode-details:visible").count() == 0

    first = page.locator('.outline-episode-card[data-episode-number="1"]')
    assert first.get_by_text("剧本已完成", exact=True).is_visible()
    assert first.get_by_text("Beats 更新", exact=True).is_visible()
    assert first.locator(".outline-save-action").is_visible()
    assert first.locator(".outline-beats-action").is_visible()
    assert first.locator(".outline-event-preview").get_attribute("title") == (
        CARD_OUTLINE_ITEMS[0]["content"]["core_event"]
    )

    page.get_by_role("button", name="展开第 1 集").click()
    first.locator(".outline-episode-details").wait_for(state="visible")
    assert first.locator(".outline-episode-details").is_visible()
    assert first.locator(".outline-field-label").all_inner_texts() == [
        "核心事件",
        "冲突",
        "人物推进",
        "亮点",
        "反转",
        "结尾悬念",
    ]
    assert first.get_by_text("剧本已完成", exact=True).is_visible()
    assert first.get_by_text("Beats 更新", exact=True).is_visible()
    assert first.locator(".outline-beats-action").is_visible()


def test_outline_search_and_batch_controls_operate_on_visible_cards(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page

    page.locator("#outline-episode-search").fill("董事会")
    assert page.locator(".outline-episode-card:visible").count() == 1
    assert page.locator('.outline-episode-card[data-episode-number="2"]').is_visible()

    page.locator("#outline-expand-all").click()
    page.locator(
        '.outline-episode-card[data-episode-number="2"] .outline-episode-details'
    ).wait_for(state="visible")
    assert page.locator(".outline-episode-details:visible").count() == 1
    page.locator("#outline-collapse-all").click()
    page.locator(
        '.outline-episode-card[data-episode-number="2"] .outline-episode-details'
    ).wait_for(state="hidden")
    assert page.locator(".outline-episode-details:visible").count() == 0

    page.locator("#outline-episode-search").fill("不存在的剧情")
    empty_result = page.get_by_text("未找到匹配的分集大纲", exact=True)
    empty_result.wait_for(state="visible")
    assert empty_result.is_visible()


def test_outline_drafts_are_editable_but_locked_versions_are_read_only(
    outline_cards_page: Page,
) -> None:
    """Removing the existing version guard must expose an enabled locked field."""
    page = outline_cards_page
    first = page.locator('.outline-episode-card[data-episode-number="1"]')

    page.get_by_role("button", name="展开第 1 集").click()
    fields = first.locator("textarea")
    assert fields.count() == 6
    assert fields.nth(0).is_enabled()
    fields.nth(0).fill("编辑后的核心事件")
    first.locator(".outline-save-action").click()
    assert page.evaluate("window.savedEpisodeNumbers") == [1]
    assert page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems[0].content.core_event"
    ) == "编辑后的核心事件"

    page.evaluate(
        "Alpine.$data(document.querySelector('main')).outlineItems[0].status = 'locked'"
    )
    assert fields.nth(0).is_disabled()
    assert first.locator(".outline-save-action").is_hidden()


def test_outline_field_grid_is_responsive_without_horizontal_page_overflow(
    outline_cards_page: Page,
) -> None:
    page = outline_cards_page
    page.get_by_role("button", name="展开第 1 集").click()
    grid = page.locator(
        '.outline-episode-card[data-episode-number="1"] .outline-field-grid'
    )

    desktop_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(desktop_columns.split()) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    screenshot_dir = os.getenv("AIWORKER_OUTLINE_SCREENSHOT_DIR")
    if screenshot_dir:
        output_dir = Path(screenshot_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=output_dir / "outline-cards-desktop.png", full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    mobile_columns = page.evaluate(
        "element => getComputedStyle(element).gridTemplateColumns", grid.element_handle()
    )
    assert len(mobile_columns.split()) == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    primary_action_boxes = page.locator(
        ".outline-workspace > .d-flex:first-child > .d-flex > .btn:visible"
    ).evaluate_all(
        "elements => elements.map(element => ({width: element.offsetWidth, height: element.offsetHeight}))"
    )
    assert primary_action_boxes
    assert all(box["width"] >= 80 and box["height"] <= 44 for box in primary_action_boxes)
    heading_copy_box = page.locator(
        ".outline-section-heading > div:first-child"
    ).bounding_box()
    primary_actions_box = page.locator(".outline-primary-actions").bounding_box()
    assert heading_copy_box is not None and primary_actions_box is not None
    assert primary_actions_box["y"] >= heading_copy_box["y"] + heading_copy_box["height"]
    if screenshot_dir:
        page.screenshot(
            path=Path(screenshot_dir) / "outline-cards-mobile.png", full_page=True
        )
