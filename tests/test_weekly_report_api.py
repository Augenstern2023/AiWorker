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
from analysis.weekly_report_service import WeeklyReportService
from identity.dependencies import current_principal, permission_service_for_request
from identity.schema import Principal


def test_weekly_report_api_lists_detail_and_exports(tmp_path):
    _write_complete_week(tmp_path)
    service = WeeklyReportService(root=tmp_path, runtime_dir=tmp_path / ".runtime")
    generated = service.generate_latest("dy", as_of=datetime(2026, 8, 10, 23, 59), with_llm=False)
    app = FastAPI()
    principal = Principal("admin", "admin", "管理员", ("system_admin",), frozenset({"*"}), "all", "session")
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[permission_service_for_request] = lambda: None
    app.include_router(build_analytics_router(root=tmp_path, runtime_dir=tmp_path / ".runtime"))
    client = TestClient(app)

    listing = client.get("/api/analytics/weekly-reports?platform=dy")
    detail = client.get(f"/api/analytics/weekly-reports/{generated['report_id']}")
    markdown = client.get(f"/api/analytics/weekly-reports/{generated['report_id']}/export?format=markdown")
    excel = client.get(f"/api/analytics/weekly-reports/{generated['report_id']}/export?format=excel")

    assert listing.status_code == 200
    assert listing.json()["items"][0]["report_id"] == generated["report_id"]
    assert detail.status_code == 200
    assert detail.json()["period"]["start"] == "2026-08-03"
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert excel.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "8月3日-8月9日" in Path(generated["markdown_path"]).name
    assert "8月3日-8月9日" in unquote(markdown.headers["content-disposition"])
    assert "8月3日-8月9日" in unquote(excel.headers["content-disposition"])


def test_legacy_sync_all_endpoint_is_not_exposed(tmp_path):
    app = FastAPI()
    principal = Principal("admin", "admin", "管理员", ("system_admin",), frozenset({"*"}), "all", "session")
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[permission_service_for_request] = lambda: None
    app.include_router(build_analytics_router(root=tmp_path, runtime_dir=tmp_path / ".runtime"))

    assert TestClient(app).post("/api/analytics/sync-all", json={"platforms": ["dy", "hg"]}).status_code == 404


def _write_complete_week(root: Path) -> None:
    for offset, day in enumerate(("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"), start=1):
        path = root / "data" / "dy" / "jobs" / f"job-{offset}" / "rank" / f"douyin_series_rank_2026080{offset + 2}_100000.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"captured_at": f"{day} 10:00:00", "records": [{"rank": 6 - offset, "title": "周报剧", "series_id": "week-1", "rank_category": "短剧", "tab": "榜单", "heat_text": str(offset * 100), "like_count": str(offset * 20)}]}, ensure_ascii=False), encoding="utf-8")
