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
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis.analytics_api import build_analytics_router
from analysis.today_rank_service import TodayRankService
from identity.dependencies import current_principal, permission_service_for_request
from identity.schema import Principal


def _client(tmp_path, monkeypatch) -> TestClient:
    for offset, day in enumerate(
        ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"),
        start=1,
    ):
        path = (
            tmp_path
            / "data"
            / "dy"
            / "jobs"
            / f"job-{offset}"
            / "rank"
            / f"douyin_series_rank_{day.replace('-', '')}_100000.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "captured_at": f"{day} 10:00:00",
                    "records": [
                        {
                            "rank": 5 - offset,
                            "title": "月报剧",
                            "series_id": "monthly-1",
                            "rank_category": "短剧",
                            "tab": "榜单",
                            "heat_text": str(offset * 100),
                            "like_count": str(offset * 20),
                            "topic_tags": ["逆袭"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr("analysis.monthly_report_service.datetime", _FixedDateTime)
    app = FastAPI()
    principal = Principal(
        "admin",
        "admin",
        "管理员",
        ("system_admin",),
        frozenset({"*"}),
        "all",
        "session",
    )
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[permission_service_for_request] = lambda: None
    app.include_router(
        build_analytics_router(
            root=tmp_path,
            runtime_dir=tmp_path / ".runtime",
            rank_service=TodayRankService.from_files(root=tmp_path),
        )
    )
    return TestClient(app)


def test_monthly_report_api_lists_detail_and_exports(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    listing = client.get("/api/analytics/monthly-reports?platform=dy")
    report_id = listing.json()["items"][0]["report_id"]
    detail = client.get(f"/api/analytics/monthly-reports/{report_id}")
    markdown = client.get(
        f"/api/analytics/monthly-reports/{report_id}/export?format=markdown"
    )
    excel = client.get(
        f"/api/analytics/monthly-reports/{report_id}/export?format=excel"
    )

    assert listing.status_code == 200
    assert report_id == "dy-2026-09"
    assert listing.json()["items"][0]["status"] == "partial"
    assert detail.status_code == 200
    assert detail.json()["period"] == {
        "start": "2026-09-01",
        "end": "2026-09-30",
    }
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert excel.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "2026-09" in unquote(markdown.headers["content-disposition"])
    assert "2026-09" in unquote(excel.headers["content-disposition"])


def test_monthly_report_api_reuses_cache_and_validates_parameters(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    first = client.get("/api/analytics/monthly-reports?platform=dy")
    second = client.get("/api/analytics/monthly-reports?platform=dy")
    report_id = second.json()["items"][0]["report_id"]

    assert first.json()["items"][0]["cache_status"] == "generated"
    assert second.json()["items"][0]["cache_status"] == "hit"
    assert client.get("/api/analytics/monthly-reports?platform=xhs").status_code == 400
    assert (
        client.get(
            f"/api/analytics/monthly-reports/{report_id}/export?format=pdf"
        ).status_code
        == 400
    )
    assert client.get("/api/analytics/monthly-reports/missing").status_code == 404


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 4, 23, 59, tzinfo=tz)
