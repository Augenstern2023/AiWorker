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
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOPICS_SCRIPT = PROJECT_ROOT / "static/js/selection/topics.js"
TEMPLATES_ROOT = PROJECT_ROOT / "templates"


def _load_topics_module(page: Page) -> None:
    page.set_content("<main></main>")
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=str(TOPICS_SCRIPT))


def test_create_project_modal_shows_current_account_as_read_only_owner() -> None:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_ROOT),
        autoescape=select_autoescape(("html",)),
    )
    html = environment.get_template(
        "common/workspace/planning_assignment_modal.html"
    ).render(
        principal=SimpleNamespace(display_name="测试编剧", username="writer")
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        owner = page.get_by_label("项目负责人")
        assert owner.input_value() == "测试编剧（writer）"
        assert owner.get_attribute("readonly") is not None
        assert page.get_by_label("策划团队成员").count() == 0
        assert page.get_by_label("编剧团队成员").count() == 0
        browser.close()


def test_open_create_project_does_not_load_member_candidates() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _load_topics_module(page)

        requested_roles = page.evaluate(
            """
            async () => {
                const module = window.aiworkerModules.at(-1)();
                const requestedRoles = [];
                const app = {
                    ...module,
                    modal: () => ({show: () => {}}),
                    loadProjectMemberCandidates: async roleCode => {
                        requestedRoles.push(roleCode);
                        return [];
                    },
                };
                await app.openCreateProject({
                    id: 9,
                    title: '默认负责人测试剧',
                    platform: 'hg',
                    source_platforms: ['hg'],
                });
                return requestedRoles;
            }
            """
        )

        assert requested_roles == []
        browser.close()


def test_create_project_payload_does_not_send_team_member_fields() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _load_topics_module(page)

        payload = page.evaluate(
            """
            async () => {
                const module = window.aiworkerModules.at(-1)();
                let submittedPayload = null;
                window.aiworkerApi = {
                    request: async (url, options) => {
                        submittedPayload = JSON.parse(options.body);
                        return {
                            ok: true,
                            json: async () => ({
                                project: {id: 12, name: submittedPayload.name},
                                tasks: [],
                            }),
                        };
                    },
                };
                const app = {
                    ...module,
                    modal: () => ({show: () => {}}),
                    loadProjectMemberCandidates: async () => [],
                    fetchTopicLibrary: async () => {},
                };
                await app.openCreateProject({
                    id: 9,
                    title: '默认负责人测试剧',
                    platform: 'hg',
                    source_platforms: ['hg'],
                });
                await app.submitCreateProject();
                return submittedPayload;
            }
            """
        )

        assert payload["name"] == "默认负责人测试剧创作项目"
        assert payload["target_platform"] == "hg"
        assert payload["target_episode_count"] == 20
        assert payload["creation_request_id"]
        assert set(payload) == {
            "name",
            "target_platform",
            "target_episode_count",
            "creation_request_id",
        }
        browser.close()
