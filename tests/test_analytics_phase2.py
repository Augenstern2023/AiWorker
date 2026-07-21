# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis.analytics_api import build_analytics_router
from analysis.drama_repository import DramaRepository, classify_sentiment
from analysis.metrics import MetricsService


def test_repository_syncs_dy_job_idempotently(tmp_path):
    _write_dy_job(tmp_path)
    repository = DramaRepository(tmp_path / ".runtime" / "rag" / "analytics.db", root=tmp_path)

    first = repository.sync_job("job-1", "dy", rebuild=True)
    second = repository.sync_job("job-1", "dy", rebuild=True)
    dramas = repository.list_dramas(platform="dy")
    overview = repository.overview(platform="dy")

    assert first["drama_count"] == 2
    assert second["drama_count"] == 2
    assert len(dramas) == 2
    assert overview["drama_count"] == 2
    assert overview["comment_count"] == 3


def test_metrics_returns_tags_trends_and_sentiment(tmp_path):
    _write_dy_job(tmp_path)
    repository = DramaRepository(tmp_path / ".runtime" / "rag" / "analytics.db", root=tmp_path)
    repository.sync_job("job-1", "dy", rebuild=True)
    metrics = MetricsService(repository)

    tags = metrics.tags(platform="dy")
    trends = metrics.trends(platform="dy", metric="likes")
    sentiment = metrics.sentiment(platform="dy")

    assert any(item["tag"] == "短剧" for item in tags["categories"])
    assert trends["points"]
    assert {item["sentiment"] for item in sentiment} >= {"positive", "negative", "neutral"}


def test_heat_trend_falls_back_to_like_count_when_heat_is_empty(tmp_path):
    _write_hg_job_without_heat(tmp_path)
    repository = DramaRepository(tmp_path / ".runtime" / "rag" / "analytics.db", root=tmp_path)
    repository.sync_job("job-hg", "hg", rebuild=True)
    metrics = MetricsService(repository)

    trends = metrics.trends(platform="hg", metric="heat")

    assert trends["metric"] == "like_count"
    assert trends["points"][0]["value"] == 60059


def test_drama_heat_sort_falls_back_to_like_count(tmp_path):
    _write_hg_job_without_heat(tmp_path)
    repository = DramaRepository(tmp_path / ".runtime" / "rag" / "analytics.db", root=tmp_path)
    repository.sync_job("job-hg", "hg", rebuild=True)

    dramas = repository.list_dramas(platform="hg", sort="heat")

    assert dramas[0]["title"] == "八零知青"
    assert dramas[0]["like_count"] == 60059


def test_dramas_can_filter_by_captured_date(tmp_path):
    _write_dy_job(tmp_path)
    repository = DramaRepository(tmp_path / ".runtime" / "rag" / "analytics.db", root=tmp_path)
    repository.sync_job("job-1", "dy", rebuild=True)

    today_rows = repository.list_dramas(platform="dy", date="2026-07-02")
    other_rows = repository.list_dramas(platform="dy", date="2026-07-01")

    assert today_rows
    assert all(item["captured_at"] == "2026-07-02" for item in today_rows)
    assert other_rows == []


def test_dramas_backfill_missing_captured_date_for_legacy_rows(tmp_path):
    _write_dy_job(tmp_path)
    db_path = tmp_path / ".runtime" / "rag" / "analytics.db"
    repository = DramaRepository(db_path, root=tmp_path)
    repository.sync_job("job-1", "dy", rebuild=True)

    with repository._connect() as conn:
        conn.execute("UPDATE analytics_dramas SET captured_at = ''")

    repository = DramaRepository(db_path, root=tmp_path)
    rows = repository.list_dramas(platform="dy", date="2026/07/02")

    assert rows
    assert all(item["captured_at"] == "2026-07-02" for item in rows)


def test_analytics_api_sync_and_query(tmp_path):
    _write_dy_job(tmp_path)
    app = FastAPI()
    app.include_router(build_analytics_router(root=tmp_path, runtime_dir=tmp_path / ".runtime"))
    client = TestClient(app)

    sync_response = client.post("/api/analytics/sync", json={"job_id": "job-1", "platform": "dy", "rebuild": True})
    overview_response = client.get("/api/analytics/overview?platform=dy")
    dramas_response = client.get("/api/analytics/dramas?platform=dy&sort=likes&date=2026-07-02")
    tags_response = client.get("/api/analytics/tags?platform=dy")
    trends_response = client.get("/api/analytics/trends?platform=dy&metric=comments")
    assets_response = client.get("/api/analytics/assets")

    assert sync_response.status_code == 200
    assert sync_response.json()["drama_count"] == 2
    assert overview_response.json()["task_count"] == 1
    assert dramas_response.json()["items"][0]["title"] == "复仇之路"
    assert dramas_response.json()["items"][0]["captured_at"] == "2026-07-02"
    assert tags_response.json()["categories"]
    assert trends_response.json()["points"]
    assert assets_response.json()["jobs"][0]["job_id"] == "job-1"


def test_analytics_api_sync_all_scans_dy_and_hg_jobs(tmp_path):
    _write_dy_job(tmp_path)
    _write_hg_job_without_heat(tmp_path)
    app = FastAPI()
    app.include_router(build_analytics_router(root=tmp_path, runtime_dir=tmp_path / ".runtime"))
    client = TestClient(app)

    sync_response = client.post("/api/analytics/sync-all", json={"platforms": ["dy", "hg"]})
    overview_response = client.get("/api/analytics/overview")

    assert sync_response.status_code == 200
    assert sync_response.json()["synced_jobs"] == 2
    assert overview_response.json()["task_count"] == 2
    assert overview_response.json()["drama_count"] == 3


def test_classify_sentiment_rule_based():
    assert classify_sentiment("太好看了，很上头") == "positive"
    assert classify_sentiment("剧情太烂，有点难看") == "negative"
    assert classify_sentiment("已经看完") == "neutral"


def _write_dy_job(root: Path) -> None:
    rank_dir = root / "data" / "dy" / "jobs" / "job-1" / "rank"
    analysis_dir = root / "data" / "dy" / "jobs" / "job-1" / "analysis"
    rank_dir.mkdir(parents=True)
    analysis_dir.mkdir(parents=True)
    (rank_dir / "douyin_series_rank_20260702_101010.json").write_text(
        json.dumps(
            {
                "platform": "dy",
                "records": [
                    {
                        "rank": 1,
                        "title": "复仇之路",
                        "rank_category": "短剧",
                        "tab": "热榜",
                        "description": "女主被背叛后逆袭复仇。",
                        "heat_text": "2000",
                        "like_count": "1000",
                        "favorite_count": "300",
                        "comment_count": "3",
                        "comments": [
                            {"content": "太好看了，很上头"},
                            {"content": "剧情太烂"},
                            {"content": "已经看完"},
                        ],
                    },
                    {
                        "rank": 2,
                        "title": "田园春风",
                        "rank_category": "短剧",
                        "tab": "榜单",
                        "description": "家庭创业成长故事。",
                        "heat_text": "800",
                        "like_count": "500",
                        "favorite_count": "90",
                        "comment_count": "0",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (analysis_dir / "01_复仇之路.json").write_text(
        json.dumps(
            {
                "analysis": {
                    "title": "复仇之路",
                    "category": "短剧",
                    "summary": "逆袭复仇故事",
                    "plot_tags": ["复仇", "逆袭"],
                    "selling_points": ["复仇爽点", "女性成长"],
                    "hook_points": ["开局背叛"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_hg_job_without_heat(root: Path) -> None:
    rank_dir = root / "data" / "hg" / "jobs" / "job-hg" / "rank"
    rank_dir.mkdir(parents=True)
    (rank_dir / "hongguo_hot_rank_20260702_101010.json").write_text(
        json.dumps(
            {
                "platform": "hg",
                "records": [
                    {
                        "rank": {
                            "rowNum": 1,
                            "play_name": "八零知青",
                            "book_id": "book-1",
                            "play_type": "0",
                            "like_count": "60059",
                            "favorite_count": "",
                            "comment_count": "0",
                        },
                        "meta": {"category": "短剧", "description": "年代逆袭故事"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
