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


ROOT = Path(__file__).resolve().parents[1]
ALPINE_SCRIPT = ROOT / "static/js/alpine.min.js"
BOOTSTRAP_STYLE = ROOT / "static/css/bootstrap.min.css"
UI_STATE_SCRIPT = ROOT / "static/js/common/ui-state.js"
ANALYTICS_SCRIPT = ROOT / "static/js/admin/workspace.js"
ANALYTICS_TEMPLATE = ROOT / "templates/common/workspace/admin_analytics.html"


def _monthly_payload() -> dict:
    return {
        "items": [
            {
                "report_id": "dy-2026-09",
                "platform": "dy",
                "month": "2026-09",
                "status": "partial",
                "period": {"start": "2026-09-01", "end": "2026-09-30"},
                "cache_status": "generated",
                "data_quality": {
                    "snapshot_days": 3,
                    "elapsed_weekdays": 4,
                    "coverage": 0.75,
                    "missing_dates": ["2026-09-03"],
                    "has_gaps": True,
                    "warning": "缺少 1 个已过去工作日快照，结论需结合覆盖率判断。",
                    "latest_snapshot_at": "2026-09-04 10:00:00",
                },
                "summary": {
                    "management_summary": "本月重点关注甲剧。",
                    "trend_analysis": "甲剧连续上行，家庭题材热度稳定。",
                    "risks": ["存在一天数据缺口"],
                    "actions": ["继续观察甲剧"],
                },
                "llm": {"status": "not_requested", "content": {}},
                "source_weekly_report_ids": ["dy-2026-08-31"],
                "weekly_highlights": [
                    {
                        "report_id": "dy-2026-08-31",
                        "period": {"start": "2026-08-31", "end": "2026-09-06"},
                        "management_summary": "首周甲剧上行。",
                    }
                ],
                "recommendations": [
                    {
                        "title": "甲剧",
                        "board": "热榜",
                        "current_rank": 1,
                        "present_at_end": True,
                        "score": 88.0,
                        "recommendation": "重点关注",
                        "tags": ["家庭"],
                        "reason": "在榜 3/3 个有效工作日",
                    }
                ],
                "tags": {
                    "theme_tags": [
                        {"tag": "家庭", "count": 1, "net_change": 1}
                    ]
                },
                "movements": {
                    "summary": {
                        "new_entries": 1,
                        "dropped_entries": 0,
                        "rank_up": 1,
                        "rank_down": 0,
                    }
                },
                "recommendation_metrics": {
                    "source_label": "来源：月度推荐剧目的热度工作日周均值",
                    "points": ["09-01~09-04（进行中）"],
                    "series": [],
                },
            }
        ]
    }


def _mount(page: Page) -> None:
    page.set_default_timeout(5_000)
    template = ANALYTICS_TEMPLATE.read_text(encoding="utf-8")
    html = (
        '<div id="ui-live-region" class="visually-hidden" aria-live="polite"></div>'
        '<div id="ui-toast-region" class="toast-container"></div>'
        f'<main x-data="appData()">{template}</main>'
    )
    page.route(
        "http://aiworker.test/",
        lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=html,
        ),
    )
    page.goto("http://aiworker.test/")
    page.add_style_tag(path=BOOTSTRAP_STYLE)
    page.add_script_tag(path=UI_STATE_SCRIPT)
    page.evaluate("window.aiworkerModules = []")
    page.add_script_tag(path=ANALYTICS_SCRIPT)
    page.evaluate(
        """
        payload => {
          window.analyticsCalls = [];
          window.openedReportUrl = '';
          window.open = url => { window.openedReportUrl = url; };
          window.aiworkerApi = {
            async request(url) {
              window.analyticsCalls.push(url);
              const data = url.includes('/monthly-reports') ? payload : {items: []};
              return {ok: true, status: 200, async json() { return data; }};
            }
          };
          const module = window.aiworkerModules.at(-1)();
          window.appData = () => Object.assign({
            analyticsPlatform: '', analyticsError: '', analyticsReportMode: 'weekly',
            weeklyReports: [], weeklyReport: null,
            weeklyReportLoading: false, weeklyReportError: '',
            monthlyReports: [], monthlyReport: null,
            monthlyReportLoading: false, monthlyReportError: ''
          }, module);
        }
        """,
        _monthly_payload(),
    )
    page.add_script_tag(path=ALPINE_SCRIPT)


def test_switching_to_monthly_loads_the_monthly_dashboard_and_remembers_mode() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount(page)

        assert page.evaluate("window.analyticsCalls") == []
        page.get_by_role("button", name="月报", exact=True).click()

        page.get_by_text("本月重点关注甲剧。", exact=True).wait_for()
        assert page.get_by_text("月度进展", exact=True).count() >= 1
        assert page.get_by_text("首周甲剧上行。", exact=True).is_visible()
        assert "覆盖率 75%" in page.locator("main").inner_text()
        assert page.evaluate(
            "sessionStorage.getItem('aiworker-analytics-report-mode')"
        ) == "monthly"
        assert page.evaluate(
            "window.analyticsCalls.some(url => url.startsWith('/api/analytics/monthly-reports?'))"
        )
        browser.close()


def test_monthly_refresh_feedback_and_export_use_monthly_endpoints() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _mount(page)
        page.get_by_role("button", name="月报", exact=True).click()
        page.get_by_text("本月重点关注甲剧。", exact=True).wait_for()

        page.get_by_role("button", name="刷新", exact=True).click()
        page.locator("#ui-toast-region").get_by_text("月报已更新", exact=True).wait_for()
        page.get_by_role("button", name="导出 Markdown", exact=True).click()

        assert page.evaluate("window.openedReportUrl") == (
            "/api/analytics/monthly-reports/dy-2026-09/export?format=markdown"
        )
        browser.close()
