# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for the episode-outline card workspace."""

from __future__ import annotations

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
                creativeWorkspace: {
                  outlineQuery: '', expandedOutlineEpisodes: {}, outlineCardContextKey: ''
                },
                outlineItems: items,
                currentProjectOutlines() { return this.outlineItems; }
              }, window.aiworkerModules.at(-1)?.());
            }
            """,
            [OUTLINE_ITEMS],
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
