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
from datetime import datetime, timezone

import analysis.today_rank_service as today_rank_service_module
from analysis.today_rank_service import TodayRankService


def test_today_rank_response_marks_only_a_real_shanghai_business_date_snapshot(
    tmp_path, monkeypatch
):
    _write_snapshot(tmp_path, "2026-09-02", "今日剧")
    monkeypatch.setattr(
        today_rank_service_module,
        "rank_business_date",
        lambda now=None: "2026-09-02",
    )

    service = TodayRankService.from_files(root=tmp_path)
    current = service.get_today_rank("dy")
    empty = service.get_today_rank("dy", date="2026-09-03")

    assert current["business_date"] == "2026-09-02"
    assert current["has_today_snapshot"] is True
    assert empty["date"] == "2026-09-03"
    assert empty["selected_snapshot"] is None
    assert empty["has_today_snapshot"] is True


def test_empty_today_rank_response_does_not_claim_a_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        today_rank_service_module,
        "rank_business_date",
        lambda now=None: "2026-09-02",
    )

    data = TodayRankService.from_files(root=tmp_path).get_today_rank(
        "dy", date="2026-09-02"
    )

    assert data["date"] == "2026-09-02"
    assert data["selected_snapshot"] is None
    assert data["has_today_snapshot"] is False


def test_today_rank_cached_response_recomputes_daily_status_after_midnight(
    tmp_path, monkeypatch
):
    _write_snapshot(tmp_path, "2026-09-01", "昨日剧")
    business_date = {"value": "2026-09-01"}
    monkeypatch.setattr(
        today_rank_service_module,
        "rank_business_date",
        lambda now=None: business_date["value"],
    )
    service = TodayRankService.from_files(root=tmp_path)

    before_midnight = service.get_today_rank("dy")
    business_date["value"] = "2026-09-02"
    after_midnight = service.get_today_rank("dy")

    assert before_midnight["has_today_snapshot"] is True
    assert after_midnight["cache_status"] == "hit"
    assert after_midnight["business_date"] == "2026-09-02"
    assert after_midnight["has_today_snapshot"] is False


def test_rank_business_date_uses_asia_shanghai_timezone():
    utc_time = datetime(2026, 9, 1, 16, 30, tzinfo=timezone.utc)

    assert today_rank_service_module.rank_business_date(utc_time) == "2026-09-02"


def _write_snapshot(root, captured_date: str, title: str) -> None:
    path = (
        root
        / "data/dy/jobs/day/rank"
        / f"douyin_series_rank_{captured_date.replace('-', '')}_100000.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": f"{captured_date} 10:00:00",
                "records": [
                    {
                        "rank": 1,
                        "title": title,
                        "series_id": f"series-{captured_date}",
                        "rank_category": "短剧",
                        "tab": "热榜",
                        "heat_text": "100",
                        "like_count": "10",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
