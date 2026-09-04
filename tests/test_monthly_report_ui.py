# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from pathlib import Path

from web.page_access_catalog import PAGE_ACCESS_CATALOG


def test_analytics_workspace_exposes_monthly_mode_and_content():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(
        encoding="utf-8"
    )
    script = (root / "static/js/admin/workspace.js").read_text(encoding="utf-8")
    state = (root / "static/js/common/tasks.js").read_text(encoding="utf-8")

    assert "月报看板" in template
    assert "月报预览" in template
    assert "分周回顾" in template
    assert "月度 Top 10" in template
    assert "行动建议" in template
    assert "setAnalyticsReportMode('monthly')" in template
    assert "/api/analytics/monthly-reports" in script
    assert "monthlyReportExport" in script
    assert "aiworker-analytics-report-mode" in state
    assert "analyticsReportMode" in state


def test_analytics_page_permissions_include_monthly_report_apis():
    api = next(
        item
        for item in PAGE_ACCESS_CATALOG
        if item["page_route"] == "/admin/analytics"
    )

    assert ("GET", "/api/analytics/monthly-reports") in api["api_routes"]
    assert (
        "GET",
        "/api/analytics/monthly-reports/{report_id}",
    ) in api["api_routes"]
    assert (
        "GET",
        "/api/analytics/monthly-reports/{report_id}/export",
    ) in api["api_routes"]


def test_monthly_dashboard_reuses_the_shared_report_controls_and_charts():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(
        encoding="utf-8"
    )
    script = (root / "static/js/admin/workspace.js").read_text(encoding="utf-8")
    styles = (root / "static/css/workspaces.css").read_text(encoding="utf-8")

    assert "activeAnalyticsReport()" in template
    assert "activeAnalyticsReports()" in template
    assert "analyticsReportExport('markdown')" in template
    assert "推荐剧目分周趋势" in template
    assert "period_average" in script
    assert ".monthly-weekly-highlights" in styles
    assert ".analytics-period-switch" in styles
