# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from analysis.monthly_report_service import (
    MonthlyReportService,
    render_markdown,
    write_excel,
)
from analysis.rank_models import RankSnapshot


class StaticRankService:
    def __init__(self, snapshots: list[RankSnapshot]) -> None:
        self.snapshots = snapshots

    def list_snapshots(self, platform: str) -> list[RankSnapshot]:
        return [item for item in self.snapshots if item.platform == platform]

    def source_revision(self, platform: str) -> str:
        return f"{platform}-{len(self.snapshots)}"


class StaticWeeklyService:
    def __init__(self, reports: list[dict] | None = None) -> None:
        self.reports = reports or []

    def list_reports(self, platform: str, as_of: datetime) -> list[dict]:
        return [item for item in self.reports if item["platform"] == platform]


def _snapshot(day: str, records: list[dict], *, platform: str = "dy") -> RankSnapshot:
    return RankSnapshot(
        platform=platform,
        job_id=f"job-{day}",
        source_path=Path(f"central/{day}.json"),
        captured_at=datetime.fromisoformat(f"{day}T10:00:00"),
        records=records,
        validation={"status": "passed"},
    )


def _record(identity: str, title: str, rank: int, metric: int) -> dict:
    return {
        "id": identity,
        "title": title,
        "rank": rank,
        "category": "短剧",
        "tab": "热榜",
        "heat": metric,
        "like_count": metric // 2,
        "topic_tags": ["逆袭" if identity == "alpha" else "家庭"],
    }


def _weekly_report(report_id: str, start: str, end: str) -> dict:
    return {
        "report_id": report_id,
        "platform": "dy",
        "period": {"start": start, "end": end},
        "source_fingerprint": f"fingerprint-{report_id}",
        "summary": {
            "management_summary": f"{report_id} 管理摘要",
            "trend_analysis": f"{report_id} 趋势分析",
            "risks": ["周度风险"],
            "actions": ["周度行动"],
        },
        "llm": {"status": "not_requested", "content": {}},
    }


def _service(tmp_path, snapshots: list[RankSnapshot], reports: list[dict] | None = None):
    return MonthlyReportService(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        rank_service=StaticRankService(snapshots),
        weekly_service=StaticWeeklyService(reports),
    )


def test_current_month_uses_weekdays_and_exposes_partial_data_quality(tmp_path):
    snapshots = [
        _snapshot("2026-09-01", [_record("alpha", "甲剧", 5, 100)]),
        _snapshot("2026-09-02", [_record("alpha", "甲剧", 3, 150)]),
        _snapshot("2026-09-04", [_record("beta", "乙剧", 1, 300)]),
        _snapshot("2026-09-05", [_record("weekend", "周末剧", 1, 900)]),
    ]
    reports = [
        _weekly_report("dy-2026-08-31", "2026-08-31", "2026-09-06"),
        _weekly_report("dy-2026-08-24", "2026-08-24", "2026-08-30"),
    ]

    report = _service(tmp_path, snapshots, reports).build_month(
        "dy",
        "2026-09",
        as_of=datetime(2026, 9, 4, 23, 59),
    )

    assert report["status"] == "partial"
    assert report["period"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert report["data_quality"]["snapshot_days"] == 3
    assert report["data_quality"]["elapsed_weekdays"] == 4
    assert report["data_quality"]["missing_dates"] == ["2026-09-03"]
    assert report["source_weekly_report_ids"] == ["dy-2026-08-31"]
    assert "进行中" in report["recommendation_metrics"]["points"][0]
    assert any(item["title"] == "乙剧" for item in report["movements"]["new_entries"])
    assert any(item["title"] == "甲剧" for item in report["movements"]["dropped_entries"])
    assert all(item["title"] != "周末剧" for item in report["recommendations"])


def test_finished_month_is_ready_even_when_snapshots_are_missing(tmp_path):
    report = _service(
        tmp_path,
        [_snapshot("2026-09-01", [_record("alpha", "甲剧", 1, 100)])],
    ).build_month("dy", "2026-09", as_of=datetime(2026, 10, 1, 9, 0))

    assert report["status"] == "ready"
    assert report["data_quality"]["status"] == "partial"
    assert report["data_quality"]["expected_weekdays"] == 22
    assert report["data_quality"]["has_gaps"] is True
    assert "缺少 21 个" in report["data_quality"]["warning"]


def test_leap_year_month_uses_the_correct_workday_denominator(tmp_path):
    report = _service(
        tmp_path,
        [_snapshot("2024-02-29", [_record("alpha", "甲剧", 1, 100)])],
    ).build_month("dy", "2024-02", as_of=datetime(2024, 3, 1, 9, 0))

    assert report["period"]["end"] == "2024-02-29"
    assert report["data_quality"]["expected_weekdays"] == 21


def test_monthly_recommendations_score_month_stability_not_weekly_frequency(tmp_path):
    snapshots = []
    for offset, day in enumerate(
        ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"),
        start=1,
    ):
        snapshots.append(
            _snapshot(
                day,
                [
                    _record("stable", "稳定剧", 2, 100 + offset * 20),
                    _record("flash", "闪现剧", 1, 1000) if offset == 4 else {},
                ],
            )
        )
        snapshots[-1].records[:] = [record for record in snapshots[-1].records if record]

    report = _service(tmp_path, snapshots).build_month(
        "dy", "2026-09", as_of=datetime(2026, 9, 4, 23, 59)
    )

    stable = next(item for item in report["recommendations"] if item["title"] == "稳定剧")
    flash = next(item for item in report["recommendations"] if item["title"] == "闪现剧")
    assert stable["persistence"] == 1.0
    assert stable["score_components"]["persistence"] == 100.0
    assert stable["score"] > flash["score"]


def test_monthly_report_cache_rebuilds_after_source_changes(tmp_path):
    snapshots = [_snapshot("2026-09-01", [_record("alpha", "甲剧", 2, 100)])]
    service = _service(tmp_path, snapshots)

    first = service.list_reports("dy", as_of=datetime(2026, 9, 2, 9, 0))[0]
    second = service.list_reports("dy", as_of=datetime(2026, 9, 2, 9, 0))[0]
    snapshots.append(_snapshot("2026-09-02", [_record("alpha", "甲剧", 1, 200)]))
    third = service.list_reports("dy", as_of=datetime(2026, 9, 2, 23, 59))[0]

    assert first["cache_status"] == "generated"
    assert second["cache_status"] == "hit"
    assert third["cache_status"] == "generated"
    assert third["source_fingerprint"] != first["source_fingerprint"]


def test_monthly_exports_include_summary_weekly_sources_and_traceability(tmp_path):
    weekly = _weekly_report("dy-2026-08-31", "2026-08-31", "2026-09-06")
    report = _service(
        tmp_path,
        [_snapshot("2026-09-01", [_record("alpha", "甲剧", 1, 100)])],
        [weekly],
    ).build_month("dy", "2026-09", as_of=datetime(2026, 9, 1, 23, 59))
    markdown = render_markdown(report)
    excel_path = tmp_path / "monthly.xlsx"
    write_excel(report, excel_path)

    assert "## 月度综合趋势" in markdown
    assert "## 分周回顾" in markdown
    assert "dy-2026-08-31" in markdown
    assert "## 来源追溯" in markdown
    assert load_workbook(excel_path, read_only=True).sheetnames == [
        "管理摘要",
        "分周回顾",
        "月度推荐",
        "题材标签",
        "来源追溯",
    ]
