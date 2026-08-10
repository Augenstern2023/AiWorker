# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from pathlib import Path

from identity.permission_catalog import NAVIGATION_CATALOG
from web.page_access_catalog import PAGE_ACCESS_CATALOG
from web.page_router import PAGE_TEMPLATES
from web.page_routes import PAGE_ROUTES


def test_analytics_workspace_includes_weekly_report_view_and_exports():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(encoding="utf-8")
    script = (root / "static/js/admin/workspace.js").read_text(encoding="utf-8")

    assert "周报预览" in template
    assert "weeklyReports" in script
    assert "/api/analytics/weekly-reports" in script
    assert "weeklyReportExport" in script
    assert 'src="/static/js/echarts.min.js?v={{ asset_version }}"' in (root / "templates/admin/analytics.html").read_text(encoding="utf-8")
    assert "if (!window.echarts) return;" in script.split("resizeAnalyticsCharts()", 1)[1].split("renderPieChart", 1)[0]


def test_analytics_page_is_a_weekly_report_dashboard_without_legacy_sync():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(encoding="utf-8")
    script = (root / "static/js/admin/workspace.js").read_text(encoding="utf-8")

    assert "周报看板" in template
    assert "同步全部" not in template
    assert "/api/analytics/sync-all" not in script
    assert "/api/analytics/tags" not in script
    assert "/api/analytics/trends" not in script
    assert "weeklyReport?.tags?.theme_tags" in script
    assert "weeklyReport?.recommendation_metrics?.points" in script


def test_weekly_report_selector_keeps_the_full_period_visible():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(
        encoding="utf-8"
    )
    styles = (root / "static/css/workspaces.css").read_text(encoding="utf-8")

    assert "weekly-report-controls" in template
    assert "weekly-report-selector" in template
    assert ".weekly-report-selector" in styles
    assert "flex: 0 1 300px" in styles
    assert ".weekly-report-controls" in styles


def test_weekly_report_is_reachable_from_the_navigation_and_bound_to_analytics_api():
    page = next(item for item in PAGE_ROUTES if item[0] == "/admin/analytics")
    nav = next(item for item in NAVIGATION_CATALOG if item["route"] == "/admin/analytics")
    api = next(item for item in PAGE_ACCESS_CATALOG if item["page_route"] == "/admin/analytics")

    assert page[1] == "analytics.read"
    assert nav["label"] == "数据分析"
    assert PAGE_TEMPLATES["/admin/analytics"] == "admin/analytics.html"
    assert ("GET", "/api/analytics/weekly-reports") in api["api_routes"]


def test_analytics_dashboard_uses_weekly_report_preview_instead_of_legacy_drama_metrics():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/common/workspace/admin_analytics.html").read_text(encoding="utf-8")
    script = (root / "static/js/admin/workspace.js").read_text(encoding="utf-8")

    assert "周报预览" in template
    assert "题材标签分布" in template
    assert 'card-header">剧目指标' not in template
    assert "评论情绪" not in template
    assert "高频卖点" not in template
    assert "analyticsDramas" not in template
    assert "renderSentimentChart" not in script
    assert "formatAnalyticsMetric" in script
    assert "weeklyReport?.tags?.theme_tags" in script
    assert "weeklyReport?.tags?.categories" not in script
    assert "recommendation_metrics?.source_label" in template
    assert "推荐说明" in template
    assert 'x-text="item.reason"' in template
    assert "text-body" not in template
    assert "推荐剧目指标趋势" in template
    assert "recommendation_metrics" in script
    assert "RECOMMENDATION_SERIES_COLORS" in script
    assert "综合趋势观察" in template
    assert "trend_analysis" in template


def test_work_overview_shows_data_asset_metrics():
    root = Path(__file__).resolve().parents[1]
    dashboard = (root / "templates/selection/dashboard.html").read_text(encoding="utf-8")
    script = (root / "static/js/selection/dashboard.js").read_text(encoding="utf-8")

    assert 'id="stat-drama-count"' in dashboard
    assert 'id="stat-comment-count"' in dashboard
    assert 'id="stat-transcript-count"' in dashboard
    assert 'id="stat-task-count"' in dashboard
    assert "/api/analytics/overview" in script


def test_analytics_dashboard_does_not_start_global_job_polling():
    root = Path(__file__).resolve().parents[1]
    tasks = (root / "static/js/common/tasks.js").read_text(encoding="utf-8")

    assert "if (this.page !== 'admin-analytics') setInterval(() => this.fetchJobs(), 2000);" in tasks
