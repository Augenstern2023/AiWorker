# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOPICS_SCRIPT = PROJECT_ROOT / "static/js/selection/topics.js"


def _open_project_modal(Page: Page, planner_candidates: list[dict], writer_candidates: list[dict]) -> dict:
    Page.set_content("<main></main>")
    Page.evaluate("window.aiworkerModules = []")
    Page.add_script_tag(path=str(TOPICS_SCRIPT))
    return Page.evaluate(
        """
        async ({plannerCandidates, writerCandidates}) => {
            const module = window.aiworkerModules.at(-1)();
            const app = {
                ...module,
                modal: () => ({show: () => {}}),
                loadProjectMemberCandidates: async roleCode => (
                    roleCode === 'creative_planner' ? plannerCandidates : writerCandidates
                ),
            };
            await app.openCreateProject({
                id: 9,
                title: '默认成员测试剧',
                platform: 'hg',
                source_platforms: ['hg'],
            });
            return {
                plannerMemberIds: app.planningAssignment.plannerMemberIds,
                writerMemberIds: app.planningAssignment.writerMemberIds,
            };
        }
        """,
        {
            "plannerCandidates": planner_candidates,
            "writerCandidates": writer_candidates,
        },
    )


def test_create_project_defaults_to_current_user_id() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        selected = _open_project_modal(
            page,
            [{"user_id": "planner-1", "is_current_user": False}],
            [
                {"user_id": "writer-1", "is_current_user": False},
                {"user_id": "current-user", "is_current_user": True},
            ],
        )

        assert selected == {
            "plannerMemberIds": [],
            "writerMemberIds": ["current-user"],
        }
        browser.close()


def test_create_project_does_not_default_same_user_twice() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        current = {"user_id": "current-user", "is_current_user": True}

        selected = _open_project_modal(page, [current], [current])

        assert selected == {
            "plannerMemberIds": ["current-user"],
            "writerMemberIds": [],
        }
        browser.close()
