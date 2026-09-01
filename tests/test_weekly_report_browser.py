# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Browser coverage for weekly report refresh feedback."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ALPINE_SCRIPT = ROOT / "static/js/alpine.min.js"
BOOTSTRAP_STYLE = ROOT / "static/css/bootstrap.min.css"
UI_STATE_SCRIPT = ROOT / "static/js/common/ui-state.js"
ANALYTICS_SCRIPT = ROOT / "static/js/admin/workspace.js"
ANALYTICS_TEMPLATE = ROOT / "templates/common/workspace/admin_analytics.html"


def _mount_analytics_page(page: Page, response_payload: dict, *, ok: bool) -> None:
    page.set_default_timeout(3_000)
    template = ANALYTICS_TEMPLATE.read_text(encoding="utf-8")
    page.set_content(
        '<div id="ui-live-region" class="visually-hidden" aria-live="polite"></div>'
        '<div id="ui-toast-region" class="toast-container"></div>'
        f'<main x-data="appData()">{template}</main>'
    )
    page.add_style_tag(path=BOOTSTRAP_STYLE)
    page.add_script_tag(path=UI_STATE_SCRIPT)
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=ANALYTICS_SCRIPT)
    page.evaluate(
        """
        ({payload, ok}) => {
          window.aiworkerApi = {
            async request() {
              return {
                ok,
                status: ok ? 200 : 500,
                async json() { return payload; }
              };
            }
          };
          const module = window.aiworkerModules.at(-1)();
          window.appData = () => Object.assign({
            analyticsPlatform: '', analyticsError: '',
            weeklyReports: [], weeklyReport: null,
            weeklyReportLoading: false, weeklyReportError: ''
          }, module);
        }
        """,
        {"payload": response_payload, "ok": ok},
    )
    page.add_script_tag(path=ALPINE_SCRIPT)


def test_manual_weekly_report_refresh_shows_updated_period() -> None:
    payload = {
        "items": [
            {
                "report_id": "hg-2026-08-24",
                "platform": "hg",
                "period": {"start": "2026-08-24", "end": "2026-08-30"},
                "cache_status": "generated",
                "data_quality": {},
                "recommendations": [],
                "tags": {"theme_tags": []},
                "movements": {"summary": {}},
                "heat_analysis": {},
                "recommendation_metrics": {},
            }
        ]
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_analytics_page(page, payload, ok=True)

        page.get_by_role("button", name="刷新", exact=True).click()

        toast = page.locator("#ui-toast-region")
        toast.get_by_text("周报已更新", exact=True).wait_for()
        assert "2026-08-24 至 2026-08-30" in toast.inner_text()
        browser.close()


def test_manual_weekly_report_refresh_pops_up_backend_error() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount_analytics_page(
            page,
            {"detail": "上周有效快照不足：仅找到 3/5 天"},
            ok=False,
        )

        page.get_by_role("button", name="刷新", exact=True).click()

        toast = page.locator("#ui-toast-region")
        toast.get_by_text("周报更新失败", exact=True).wait_for()
        assert "上周有效快照不足：仅找到 3/5 天" in toast.inner_text()
        assert page.locator("main").evaluate(
            "element => element._x_dataStack[0].weeklyReportError"
        ) == "上周有效快照不足：仅找到 3/5 天"
        browser.close()
