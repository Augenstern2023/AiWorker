# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from analysis.weekly_report_service import WeeklyReportService, _tag_analysis, render_markdown, write_excel


def test_builds_latest_complete_week_and_keeps_latest_snapshot_per_day(tmp_path):
    for day, rank, heat in (
        ("2026-08-03", 5, 100),
        ("2026-08-04", 4, 130),
        ("2026-08-05", 3, 160),
        ("2026-08-06", 2, 210),
        ("2026-08-07", 1, 280),
    ):
        _write_snapshot(tmp_path, day, [_record(rank, "上升剧", "series-1", heat), _record(1, "稳定剧", "series-2", 90)])
    _write_snapshot(tmp_path, "2026-08-07", [_record(1, "上升剧", "series-1", 320), _record(2, "稳定剧", "series-2", 90)], suffix="180000")

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))

    assert report["status"] == "ready"
    assert report["period"] == {"start": "2026-08-03", "end": "2026-08-09"}
    assert report["data_quality"]["snapshot_days"] == 5
    assert report["recommendations"][0]["title"] == "上升剧"
    assert report["recommendations"][0]["current_rank"] == 1
    assert report["recommendations"][0]["description"] == "逆袭家庭故事"
    assert report["recommendations"][0]["metrics"]["heat"] == 320
    assert "第 1 名" in report["recommendations"][0]["reason"]
    assert "连续在榜 5/5 天" in report["recommendations"][0]["reason"]
    assert report["heat_analysis"]["metric"] == "heat"
    assert report["heat_analysis"]["unit"] == "index"
    assert report["heat_analysis"]["points"][0]["value"] == 100.0
    assert report["heat_analysis"]["points"][-1]["value"] == 215.8
    trend = report["recommendation_metrics"]
    assert trend["unit"] == "weekly_average_index"
    assert trend["points"] == ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    assert trend["series"][0]["title"] == "上升剧"
    assert trend["series"][0]["metric"] == "heat"
    assert trend["series"][0]["weekly_average"] == 184.0
    assert trend["series"][0]["index_values"][-1] == 173.9
    assert len(report["summary"]["trend_analysis"]) >= 100
    assert "上升剧" in report["summary"]["trend_analysis"]
    assert "热度" in report["summary"]["trend_analysis"]
    assert "点赞" in report["summary"]["trend_analysis"]
    assert "收藏" in report["summary"]["trend_analysis"]
    assert "评论" in report["summary"]["trend_analysis"]


def test_theme_tags_aggregate_subject_tags_and_exclude_format_labels():
    records = {
        "series-1": {
            "topic_tags": ["女性成长", "穿越", "漫剧"],
            "subject": "女性成长 / 穿越 / 漫剧",
            "heat": 100,
        }
    }
    tags = {item["tag"]: item for item in _tag_analysis({"榜单": [records, records]})["theme_tags"]}

    assert tags["女性成长"]["count"] == 1
    assert tags["穿越"]["count"] == 1
    assert "漫剧" not in tags


def test_recommendation_chart_falls_back_to_a_complete_nonzero_metric(tmp_path):
    for day, heat in (
        ("2026-08-03", 100),
        ("2026-08-04", 120),
        ("2026-08-05", 0),
        ("2026-08-06", 160),
        ("2026-08-07", 180),
    ):
        record = _record(1, "指标切换剧", "metric-fallback", 100)
        record["heat_text"] = str(heat)
        _write_snapshot(tmp_path, day, [record])

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))
    series = report["recommendation_metrics"]["series"]

    assert series[0]["metric"] == "like_count"
    assert all(value is not None and value > 0 for value in series[0]["values"])


def test_recommendation_chart_uses_one_metric_for_every_displayed_drama(tmp_path):
    for index, day in enumerate(("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07")):
        heat_drama = _record(1, "热度剧", "heat-drama", 100)
        heat_drama.update({"like_count": "0", "favorite_count": "0", "comment_count": "0"})
        like_dramas = []
        for rank, title, series_id in ((2, "点赞剧甲", "like-a"), (3, "点赞剧乙", "like-b")):
            record = _record(rank, title, series_id, 100)
            record.update({
                "heat_text": "0" if index == 2 else "100",
                "favorite_count": "0",
                "comment_count": "0",
            })
            like_dramas.append(record)
        _write_snapshot(tmp_path, day, [heat_drama, *like_dramas])

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))
    series = report["recommendation_metrics"]["series"]

    assert [item["title"] for item in series] == ["点赞剧甲", "点赞剧乙"]
    assert {item["metric"] for item in series} == {"like_count"}


def test_reports_insufficient_data_when_no_complete_week_exists(tmp_path):
    for day in ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"):
        _write_snapshot(tmp_path, day, [_record(1, "剧目", "series-1", 100)])

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))

    assert report == {"platform": "dy", "status": "insufficient_data"}


def test_exports_include_the_rule_based_comprehensive_trend_observation(tmp_path):
    for day, rank, heat in (
        ("2026-08-03", 5, 100),
        ("2026-08-04", 4, 130),
        ("2026-08-05", 3, 160),
        ("2026-08-06", 2, 210),
        ("2026-08-07", 1, 280),
    ):
        _write_snapshot(tmp_path, day, [_record(rank, "上升剧", "series-1", heat)])

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))
    markdown = render_markdown(report)
    excel_path = tmp_path / "weekly_report.xlsx"
    write_excel(report, excel_path)

    assert "## 综合趋势观察" in markdown
    assert report["summary"]["trend_analysis"] in markdown
    rows = list(load_workbook(excel_path, read_only=True)["管理摘要"].values)
    assert ("综合趋势观察", report["summary"]["trend_analysis"]) in rows


def test_tag_and_rank_movements_are_scoped_to_each_board(tmp_path):
    for day, first_rank, second_rank in (
        ("2026-08-03", 4, 1),
        ("2026-08-04", 3, 2),
        ("2026-08-05", 2, 3),
        ("2026-08-06", 1, 4),
        ("2026-08-07", 1, 5),
    ):
        records = [
            {**_record(first_rank, "同名剧", "series-1", 100 + first_rank), "tab": "榜单", "description": "逆袭家庭故事"},
            {**_record(second_rank, "同名剧", "series-1", 200 + second_rank), "tab": "热榜", "description": "悬疑复仇故事"},
        ]
        _write_snapshot(tmp_path, day, records)

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))

    movements = report["movements"]
    assert any("榜单" in item["board"] and item["title"] == "同名剧" for item in movements["rank_up"])
    assert any("热榜" in item["board"] and item["title"] == "同名剧" for item in movements["rank_down"])
    assert report["tags"]["topic_tags"]


def test_missing_board_on_a_snapshot_is_counted_as_a_board_level_drop(tmp_path):
    for index, day in enumerate(("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"), start=1):
        records = [{**_record(1, "常驻剧", "series-1", 100), "tab": "榜单"}]
        if index < 5:
            records.append({**_record(1, "下榜剧", "series-2", 90), "tab": "热榜"})
        _write_snapshot(tmp_path, day, records)

    report = WeeklyReportService(root=tmp_path).build_latest("dy", as_of=datetime(2026, 8, 10, 23, 59))

    assert any(item["title"] == "下榜剧" and "热榜" in item["board"] for item in report["movements"]["dropped_entries"])


def test_historical_report_is_marked_stale_when_a_new_complete_week_exists(tmp_path):
    for day in ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"):
        _write_snapshot(tmp_path, day, [_record(1, "旧周剧", "old", 100)])
    service = WeeklyReportService(root=tmp_path, runtime_dir=tmp_path / ".runtime")
    service.generate_latest("dy", as_of=datetime(2026, 8, 10, 23, 59), with_llm=False)
    for day in ("2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"):
        _write_snapshot(tmp_path, day, [_record(1, "新周剧", "new", 100)])

    reports = service.list_reports("dy", as_of=datetime(2026, 8, 17, 23, 59))

    assert reports[0]["data_quality"]["is_stale"] is True


def test_generates_every_available_complete_week_for_historical_backfill(tmp_path):
    for title, days in (
        ("七月剧", ("2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17")),
        ("八月剧", ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07")),
    ):
        for day in days:
            _write_snapshot(tmp_path, day, [_record(1, title, title, 100)])

    service = WeeklyReportService(root=tmp_path, runtime_dir=tmp_path / ".runtime")
    reports = service.generate_all_available("dy", as_of=datetime(2026, 8, 10, 23, 59), with_llm=False)

    assert [report["report_id"] for report in reports] == ["dy-2026-08-03", "dy-2026-07-13"]
    assert [report["report_id"] for report in service.list_reports("dy")] == ["dy-2026-08-03", "dy-2026-07-13"]


def _write_snapshot(root: Path, day: str, records: list[dict], suffix: str = "100000") -> None:
    target = root / "data" / "dy" / "jobs" / f"job-{day}" / "rank" / f"douyin_series_rank_{day.replace('-', '')}_{suffix}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"captured_at": f"{day} {suffix[:2]}:{suffix[2:4]}:{suffix[4:]}", "records": records}, ensure_ascii=False), encoding="utf-8")


def _record(rank: int, title: str, series_id: str, heat: int) -> dict:
    return {
        "rank": rank,
        "title": title,
        "series_id": series_id,
        "rank_category": "短剧",
        "tab": "榜单",
        "description": "逆袭家庭故事",
        "heat_text": str(heat),
        "like_count": str(heat * 10),
        "favorite_count": str(heat),
        "comment_count": str(heat // 2),
    }
