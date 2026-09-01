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
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import WebGUI
from analysis.today_rank_service import TodayRankService


def test_today_rank_compares_douyin_snapshots(tmp_path):
    write_json(
        tmp_path / "data/dy/jobs/old/rank/douyin_series_rank_20260701_100000.json",
        {
            "captured_at": "2026-07-01 10:00:00",
            "records": [
                dy_record(1, "A", "s1", heat_text="100", like_count="10"),
                dy_record(2, "B", "s2", heat_text="80", like_count="20"),
                dy_record(3, "C", "s3", heat_text="60", like_count="30"),
            ],
        },
    )
    write_json(
        tmp_path / "data/dy/jobs/new/rank/douyin_series_rank_20260702_100000.json",
        {
            "captured_at": "2026-07-02 10:00:00",
            "records": [
                dy_record(1, "B", "s2", heat_text="180", like_count="2.5万"),
                dy_record(2, "A", "s1", heat_text="120", like_count="15"),
                dy_record(3, "D", "s4", heat_text="90", like_count="40"),
            ],
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")

    assert data["date"] == "2026-07-02"
    assert data["available_dates"] == ["2026-07-02", "2026-07-01"]
    assert data["summary"]["current_count"] == 3
    assert data["summary"]["previous_count"] == 3
    assert data["summary"]["common_count"] == 2
    assert data["summary"]["new_count"] == 1
    assert data["summary"]["dropped_count"] == 1
    assert data["new_entries"][0]["title"] == "D"
    assert data["dropped_entries"][0]["title"] == "C"
    assert data["rank_up"][0]["title"] == "B"
    assert data["rank_up"][0]["rank_delta"] == 1
    assert data["like_growth_top"][0]["title"] == "B"


def test_today_rank_splits_douyin_tabs_without_changing_heat_count(tmp_path):
    ranking_records = [
        {**dy_record(rank, f"榜单剧{rank}", f"ranking-{rank}"), "tab": "榜单"}
        for rank in range(1, 17)
    ]
    heat_records = [
        {**dy_record(rank, f"热榜剧{rank}", f"heat-{rank}"), "tab": "热榜"}
        for rank in range(1, 8)
    ]
    duplicated_heat_records = [{**record, "rank_category": "漫剧"} for record in heat_records]
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": ranking_records + heat_records + duplicated_heat_records},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy", limit=20)
    sections = {section["key"]: section for section in data["sections"]}

    assert [section["key"] for section in data["sections"]] == ["榜单", "热榜"]
    assert sections["榜单"]["summary"]["current_count"] == 16
    assert len(sections["榜单"]["rankings"][0]["current_entries"]) == 16
    assert sections["热榜"]["summary"]["current_count"] == 7
    assert len(sections["热榜"]["rankings"][0]["current_entries"]) == 7
    assert [item["rank"] for item in sections["热榜"]["rankings"][0]["current_entries"]] == list(range(1, 8))


def test_today_rank_normalizes_hongguo_nested_records(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260702_091652.json",
        {
            "captured_at": "2026-07-02 09:16:52",
            "records": [
                {
                    "rank": {
                        "rowNum": 1,
                        "play_name": "红果短剧",
                        "book_id": "book-1",
                        "play_type": "短剧",
                        "like_count": "1.2万",
                        "favorite_count": "300",
                        "comment_count": "45",
                        "internal_heat_score": 88.5,
                    },
                    "meta": {"category": "短剧", "description": "demo", "cover_url": "https://example.test/a.jpg"},
                }
            ],
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("hg")

    assert data["summary"]["current_count"] == 1
    assert data["summary"]["previous_count"] == 0
    assert data["new_entries"][0]["title"] == "红果短剧"
    assert data["new_entries"][0]["like_count"] == 12000
    assert data["new_entries"][0]["heat"] == 88.5
    assert data["new_entries"][0]["category_label"] == "短剧"


def test_today_rank_douyin_heat_falls_back_to_analysis_and_tags(tmp_path):
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [dy_record(1, "A", "s1", heat_text="")]},
    )
    write_json(
        tmp_path / "data/dy/jobs/job/analysis/01_A.json",
        {
            "analysis": {
                "title": "A",
                "series_id": "s1",
                "stats": {"heat": "12345"},
                "plot_tags": ["复仇", "逆袭"],
                "selling_points": ["开场强钩子"],
            }
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    item = data["new_entries"][0]

    assert item["heat"] == 12345
    assert item["topic_tags"] == ["复仇", "逆袭"]
    assert item["subject"] == "复仇 / 逆袭"
    assert item["selling_points"] == ["开场强钩子"]
    assert "heat_missing" not in item["validation_warnings"]


def test_today_rank_douyin_missing_heat_is_not_zero(tmp_path):
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [dy_record(1, "A", "s1", heat_text="")]},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    item = data["new_entries"][0]

    assert item["heat"] is None
    assert item["metric_status"]["heat"] == "missing"
    assert "heat_missing" in item["validation_warnings"]
    assert data["validation"]["missing_heat_count"] == 1


def test_today_rank_logs_import_and_data_quality_audit(tmp_path):
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [dy_record(1, "A", "s1", heat_text="")]},
    )
    records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("aiworker.web.today_rank")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        TodayRankService(root=tmp_path).get_today_rank("dy")
    finally:
        logger.removeHandler(handler)

    messages = [record.getMessage() for record in records]
    assert any("榜单数据导入" in message and "records=1" in message for message in messages)
    assert any("数据质量审查" in message and "missing_heat_count" in message for message in messages)


def test_today_rank_douyin_tags_fall_back_from_rank_text(tmp_path):
    record = dy_record(1, "再婚后，顾先生的马甲藏不住了", "s1")
    record["description"] = "离婚后前夫追悔莫及，豪门总裁身份曝光，女主逆袭复仇。"
    record["comments"] = [{"content": "虐恋追妻太上头了"}]
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [record]},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    item = data["new_entries"][0]

    assert item["topic_tags"]
    assert item["subject"]
    assert "虐恋" in item["topic_tags"]
    assert "复仇" in item["topic_tags"]
    assert item["selling_points"][0] == record["description"]


def test_today_rank_douyin_tags_fall_back_from_title_when_description_is_generic(tmp_path):
    record = dy_record(1, "再婚后，顾先生的马甲藏不住了", "s1")
    record["description"] = "抖音短剧新番计划，集均有效播放超200万即可开始分账。"
    write_json(
        tmp_path / "data/dy/jobs/job/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [record]},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    item = data["new_entries"][0]

    assert item["topic_tags"] == ["虐恋", "豪门", "身份反转"]
    assert item["subject"] == "虐恋 / 豪门 / 身份反转"


def test_today_rank_hongguo_category_mapping_and_favorite_missing(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260702_091652.json",
        {
            "captured_at": "2026-07-02 09:16:52",
            "records": [
                {"rank": {"rowNum": 1, "play_name": "短", "book_id": "b1", "play_type": "0", "internal_heat_score": 1}},
                {"rank": {"rowNum": 2, "play_name": "漫", "book_id": "b2", "play_type": "1", "internal_heat_score": 1}},
                {"rank": {"rowNum": 3, "play_name": "AI", "book_id": "b3", "play_type": "2", "internal_heat_score": 1}},
            ],
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("hg")
    items = data["new_entries"]

    assert [item["category_label"] for item in items] == ["短剧", "漫剧", "漫剧"]
    assert [item["source_category_label"] for item in items] == ["短剧", "漫剧", "AI漫剧"]
    assert items[0]["favorite_count"] is None
    assert "favorite_missing" in items[0]["validation_warnings"]
    assert data["validation"]["missing_favorite_count"] == 3
    assert data["validation"]["missing_comment_count"] == 3


def test_today_rank_hongguo_favorite_fallbacks_and_tags(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260702_091652.json",
        {
            "captured_at": "2026-07-02 09:16:52",
            "records": [
                {
                    "rank": {
                        "rowNum": 1,
                        "play_name": "红果短剧",
                        "book_id": "book-1",
                        "play_type": "0",
                        "like_count": "10",
                        "internal_heat_score": 1,
                        "internal_heat_metrics": {"favorites": 321},
                    }
                }
            ],
        },
    )
    write_json(
        tmp_path / "data/hg/jobs/hg-job/analysis/hongguo_analysis_manifest.json",
        {
            "items": [
                {
                    "analysis": {
                        "title": "红果短剧",
                        "book_id": "book-1",
                        "llm_analysis": {"剧情类型": "修仙逆袭 / 玄幻爽文短剧"},
                        "plot_tags": ["修仙", "逆袭"],
                        "selling_points": ["金手指"],
                    }
                }
            ]
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("hg")
    item = data["new_entries"][0]

    assert item["favorite_count"] == 321
    assert item["subject"] == "修仙逆袭 / 玄幻爽文短剧"
    assert item["topic_tags"] == ["修仙", "逆袭"]
    assert item["selling_points"] == ["金手指"]


def test_today_rank_hongguo_comment_zero_is_missing_but_detail_count_is_used(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260702_091652.json",
        {
            "captured_at": "2026-07-02 09:16:52",
            "records": [
                {
                    "rank": {
                        "rowNum": 1,
                        "play_name": "缺评论",
                        "book_id": "book-1",
                        "play_type": "0",
                        "like_count": "10",
                        "favorite_count": "5",
                        "comment_count": "0",
                        "internal_heat_score": 1,
                    }
                },
                {
                    "rank": {
                        "rowNum": 2,
                        "play_name": "有明细",
                        "book_id": "book-2",
                        "play_type": "0",
                        "like_count": "10",
                        "favorite_count": "5",
                        "comment_count": "0",
                        "detail_comment_count": "12",
                        "internal_heat_score": 1,
                    }
                },
            ],
        },
    )

    data = TodayRankService(root=tmp_path).get_today_rank("hg")
    items = data["new_entries"]

    assert items[0]["comment_count"] is None
    assert "comment_missing" in items[0]["validation_warnings"]
    assert items[1]["comment_count"] == 12
    assert "comment_missing" not in items[1]["validation_warnings"]
    assert data["validation"]["missing_comment_count"] == 1


def test_today_rank_hongguo_limits_business_scope_to_short_and_comic_top10(tmp_path):
    records = []
    for rank in range(1, 13):
        records.append(
            {
                "rank": {
                    "rowNum": rank,
                    "play_name": f"短剧{rank}",
                    "book_id": f"s{rank}",
                    "play_type": "0",
                    "like_count": str(1000 - rank),
                    "internal_heat_score": 1,
                }
            }
        )
    for rank in range(13, 25):
        records.append(
            {
                "rank": {
                    "rowNum": rank,
                    "play_name": f"漫剧{rank}",
                    "book_id": f"m{rank}",
                    "play_type": "1",
                    "like_count": str(1000 - rank),
                    "internal_heat_score": 1,
                }
            }
        )
    records.append(
        {
            "rank": {
                "rowNum": 13,
                "play_name": "AI漫剧",
                "book_id": "ai1",
                "play_type": "2",
                "like_count": "999",
                "internal_heat_score": 1,
            }
        }
    )
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260702_091652.json",
        {"captured_at": "2026-07-02 09:16:52", "records": records},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("hg", limit=30)

    assert data["summary"]["current_count"] == 20
    assert data["summary"]["new_count"] == 20
    assert {item["category_label"] for item in data["new_entries"]} == {"短剧", "漫剧"}
    assert "短剧11" not in {item["title"] for item in data["new_entries"]}
    titles = {item["title"] for item in data["new_entries"]}
    assert "AI漫剧" in titles
    assert "漫剧23" not in titles


def test_today_rank_reads_hongguo_csv_exports(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260707/top10_rankings.csv",
        [
            ["红果短剧热播榜", "1", "旧剧", "都市爱情", "旧简介", "9.1", "无", "1w", "2w", "100w", "无", "无", "无", "无", "无", "旧评"],
            ["红果短剧热播榜", "2", "共同剧", "逆袭", "共同简介", "9.2", "无", "2w", "3w", "200w", "无", "无", "无", "无", "无", "旧热评"],
        ],
    )
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [
            ["红果短剧热播榜", "1", "共同剧", "逆袭", "共同简介", "9.2", "无", "3w", "3.5w", "260w", "无", "无", "无", "无", "无", "新热评"],
            ["红果短剧热播榜", "2", "新剧", "古言脑洞·全80集", "新简介", "无", "无", "4.5w", "无", "300w", "998w", "无", "无", "5w", "无", "新评"],
        ],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    assert data["date"] == "2026-07-08"
    assert data["available_dates"] == ["2026-07-08", "2026-07-07"]
    assert data["summary"]["current_count"] == 2
    assert data["new_entries"][0]["title"] == "新剧"
    assert data["sections"][0]["rankings"][0]["current_entries"][0]["title"] == "共同剧"
    assert data["new_entries"][0]["like_count"] == 45000
    assert data["new_entries"][0]["heat"] == 3000000
    assert data["new_entries"][0]["topic_tags"] == ["古言脑洞", "全80集"]
    assert data["dropped_entries"][0]["title"] == "旧剧"
    assert data["rank_up"][0]["title"] == "共同剧"
    assert data["rank_up"][0]["rank_delta"] == 1
    assert data["heat_growth_top"][0]["heat_delta"] == 600000


def test_today_rank_reads_date_from_suffixed_hongguo_export_directory(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260713_top10" / "top10_rankings.csv",
        [["红果短剧热播榜", "1", "历史导出剧", "都市", "", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    assert data["date"] == "2026-07-13"
    assert data["available_dates"] == ["2026-07-13"]


def test_today_rank_merges_hongguo_json_history_with_newer_csv_export(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260707_090000.json",
        {
            "captured_at": "2026-07-07 09:00:00",
            "records": [
                {
                    "rank": {
                        "rowNum": 1,
                        "play_name": "历史剧",
                        "book_id": "history-1",
                        "play_type": "短剧",
                        "like_count": "100",
                        "internal_heat_score": 100,
                    }
                }
            ],
        },
    )
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "今日剧", "都市", "", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    assert data["available_dates"] == ["2026-07-08", "2026-07-07"]
    assert data["previous_snapshot"]["date"] == "2026-07-07"
    assert data["previous_snapshot"]["record_count"] == 1


def test_today_rank_prefers_hongguo_csv_export_over_import_on_same_date(tmp_path):
    write_json(
        tmp_path / "data/hg/jobs/hg-job/rank/hongguo_hot_rank_20260708_180000.json",
        {
            "captured_at": "2026-07-08 18:00:00",
            "records": [
                {
                    "rank": {
                        "rowNum": 1,
                        "play_name": "旧导入剧",
                        "book_id": "import-1",
                        "play_type": "短剧",
                        "like_count": "100",
                        "internal_heat_score": 100,
                    }
                }
            ],
        },
    )
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "CSV权威剧", "都市", "", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    assert data["available_dates"] == ["2026-07-08"]
    assert data["current_entries"][0]["title"] == "CSV权威剧"
    assert data["selected_snapshot"]["source_path"].endswith("top10_rankings.csv")


def test_today_rank_returns_dynamic_columns_for_each_rank_table(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [
            ["红果短剧推荐榜", "1", "推荐剧", "", "", "无", "无", "1w", "无", "100w", "88", "无", "无", "无", "无", "无"],
        ],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)
    ranking = data["sections"][0]["rankings"][0]

    assert [column["key"] for column in ranking["columns"]["current"]] == [
        "rank", "title", "category", "status", "like_count", "heat", "recommend_index", "rank_duration",
    ]
    assert [column["key"] for column in ranking["columns"]["new"]] == [
        "rank", "title", "category", "status", "like_count", "heat", "recommend_index", "rank_duration",
    ]


def test_today_rank_dynamic_columns_include_zero_and_all_csv_fields(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [
            ["红果短剧热播榜", "1", "完整剧", "复仇", "完整简介", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "热评"],
        ],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)
    ranking = data["sections"][0]["rankings"][0]
    column_keys = [column["key"] for column in ranking["columns"]["current"]]

    assert column_keys == [
        "rank", "title", "category", "status", "subject", "description", "score", "share_count",
        "like_count", "favorite_count", "heat", "recommend_index", "rating_count",
        "expectation_index", "search_index", "max_heat", "hot_comment", "rank_duration",
    ]
    assert ranking["current_entries"][0]["score"] == 0
    assert ranking["current_entries"][0]["like_count"] == 0


def test_today_rank_compares_hongguo_csv_by_section_and_rank_list(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260707/top10_rankings.csv",
        [
            ["红果短剧热播榜", "1", "全部旧", "都市", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["红果短剧热播榜", "2", "全部共同", "都市", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["真人剧热播榜", "1", "真人共同", "甜宠", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["真人剧推荐榜", "1", "真人推荐旧", "甜宠", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["漫剧热播榜", "1", "漫剧旧", "玄幻", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["AI剧热播榜", "1", "AI共同", "脑洞", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
        ],
    )
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [
            ["红果短剧热播榜", "1", "全部共同", "都市", "", "无", "无", "2w", "1w", "120w", "无", "无", "无", "无", "无", ""],
            ["红果短剧热播榜", "2", "全部新", "都市", "", "无", "无", "2w", "1w", "120w", "无", "无", "无", "无", "无", ""],
            ["真人剧热播榜", "1", "真人新", "甜宠", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["真人剧热播榜", "2", "真人共同", "甜宠", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["真人剧推荐榜", "1", "真人推荐新", "甜宠", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["漫剧热播榜", "1", "漫剧新", "玄幻", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
            ["AI剧热播榜", "1", "AI共同", "脑洞", "", "无", "无", "1w", "1w", "100w", "无", "无", "无", "无", "无", ""],
        ],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    sections = {section["label"]: section for section in data["sections"]}
    assert list(sections) == ["全部", "真人剧", "漫剧", "AI剧"]

    all_hot = next(item for item in sections["全部"]["rankings"] if item["label"] == "热播榜")
    assert all_hot["summary"]["new_count"] == 1
    assert [item["title"] for item in all_hot["current_entries"]] == ["全部共同", "全部新"]
    assert all_hot["summary"]["dropped_count"] == 1
    assert all_hot["summary"]["rank_up_count"] == 1
    assert all_hot["new_entries"][0]["title"] == "全部新"
    assert all_hot["dropped_entries"][0]["title"] == "全部旧"
    assert all_hot["rank_up"][0]["title"] == "全部共同"

    real_hot = next(item for item in sections["真人剧"]["rankings"] if item["label"] == "热播榜")
    real_recommend = next(item for item in sections["真人剧"]["rankings"] if item["label"] == "推荐榜")
    assert real_hot["summary"]["new_count"] == 1
    assert real_hot["summary"]["rank_down_count"] == 1
    assert real_recommend["summary"]["new_count"] == 1
    assert real_recommend["summary"]["dropped_count"] == 1


def test_today_rank_hongguo_csv_ignores_board_level_unavailable_favorite(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [
            ["红果短剧推荐榜", "1", "推荐一", "都市", "", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""],
            ["红果短剧推荐榜", "2", "推荐二", "都市", "", "无", "无", "2w", "无", "90w", "无", "无", "无", "无", "无", ""],
            ["红果短剧热播榜", "1", "热播一", "都市", "", "无", "无", "3w", "1w", "300w", "无", "无", "无", "无", "无", ""],
            ["红果短剧热播榜", "2", "热播二", "都市", "", "无", "无", "4w", "无", "200w", "无", "无", "无", "无", "无", ""],
        ],
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", limit=10)

    assert data["validation"]["missing_favorite_count"] == 1
    sections = {section["label"]: section for section in data["sections"]}
    recommend = next(item for item in sections["全部"]["rankings"] if item["label"] == "推荐榜")
    hot = next(item for item in sections["全部"]["rankings"] if item["label"] == "热播榜")
    assert recommend["validation"]["missing_favorite_count"] == 0
    assert recommend["new_entries"][0]["metric_status"]["favorite_count"] == "not_applicable"
    assert hot["validation"]["missing_favorite_count"] == 1


def test_today_rank_reads_sparse_hongguo_history_csv_with_flow_metadata(tmp_path):
    exports = tmp_path / "exports"
    sparse_header = (
        "榜单类型,排名,series_id,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,"
        "评分人数,期待指数,热搜指数,最高热度,热评,source_flow,selected_items,sub_selected_items,offset"
    )
    write_text(
        exports / "20260805_top10" / "top10_rankings.csv",
        "\n".join(
            [
                sparse_header,
                ",,series-a,,,,无,无,1w,无,100w,99w,无,无,无,无,,797,all,ranklist_hot_sc,",
                ",,series-b,,,,无,无,2w,无,200w,88w,无,无,无,无,,808,all,ranklist_hot_play_sc,",
                ",,series-c,,,,无,无,3w,无,300w,77w,无,无,无,无,,843,human,human_hot_play,",
            ]
        ),
    )
    write_text(
        exports / "20260806_top10" / "top10_rankings.csv",
        "\n".join(
            [
                sparse_header,
                "红果推荐榜,1,series-a,推荐历史剧,都市,简介,无,无,1w,无,100w,99w,无,无,无,无,,797,all,ranklist_hot_sc,",
                "红果热播榜,1,series-b,热播历史剧,逆袭,简介,无,无,2w,无,200w,88w,无,无,无,无,,808,all,ranklist_hot_play_sc,",
                "真人剧热播榜,1,series-c,真人历史剧,甜宠,简介,无,无,3w,无,300w,77w,无,无,无,无,,843,human,human_hot_play,",
            ]
        ),
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", date="2026-08-05", limit=10)

    assert data["summary"]["current_count"] == 3
    sections = {section["label"]: section for section in data["sections"]}
    recommend = next(item for item in sections["全部"]["rankings"] if item["label"] == "推荐榜")
    hot = next(item for item in sections["全部"]["rankings"] if item["label"] == "热播榜")
    real_hot = next(item for item in sections["真人剧"]["rankings"] if item["label"] == "热播榜")
    assert recommend["current_entries"][0]["rank"] == 1
    assert recommend["current_entries"][0]["title"] == "推荐历史剧"
    assert hot["current_entries"][0]["title"] == "热播历史剧"
    assert real_hot["current_entries"][0]["title"] == "真人历史剧"


def test_today_rank_enriches_sparse_hongguo_history_csv_from_raw_mitm_response(tmp_path):
    exports = tmp_path / "exports"
    sparse_header = (
        "榜单类型,排名,series_id,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,"
        "评分人数,期待指数,热搜指数,最高热度,热评,source_flow,selected_items,sub_selected_items,offset"
    )
    write_text(
        exports / "20260731_top10" / "top10_rankings.csv",
        "\n".join(
            [
                sparse_header,
                ",,series-raw,,,,无,无,12w,无,500w,900w,无,无,无,无,,610,all,ranklist_hot_play_sc,",
            ]
        ),
    )
    write_json(
        tmp_path / "raw" / "mitm_responses_20260731" / "response.json",
        {
            "data": {
                "cell_view": {
                    "cell_data": [
                        {
                            "video_data": [
                                {
                                    "series_id": "series-raw",
                                    "title": "原始响应剧名",
                                    "video_desc": "来自 MITM 原始响应的简介",
                                    "sub_title_list": [{"content": "漫剧"}, {"content": "玄幻"}],
                                }
                            ]
                        }
                    ]
                }
            }
        },
    )

    data = TodayRankService(root=tmp_path, hongguo_exports_root=exports).get_today_rank("hg", date="2026-07-31", limit=10)

    sections = {section["label"]: section for section in data["sections"]}
    hot = next(item for item in sections["全部"]["rankings"] if item["label"] == "热播榜")
    entry = hot["current_entries"][0]
    assert entry["title"] == "原始响应剧名"
    assert entry["description"] == "来自 MITM 原始响应的简介"
    assert entry["topic_tags"] == ["漫剧", "玄幻"]
    assert entry["subject"] == "漫剧 / 玄幻"


def test_today_rank_reuses_persistent_hongguo_cache_until_sources_change(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "缓存剧", "都市", "简介", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    first_service = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir)
    first = first_service.get_today_rank("hg", limit=10)
    restarted = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir)

    def fail_list_snapshots(*_args, **_kwargs):
        raise AssertionError("unchanged Hongguo sources must load the persistent response cache")

    monkeypatch.setattr(restarted, "list_snapshots", fail_list_snapshots)
    cached = restarted.get_today_rank("hg", limit=10)

    assert first["current_entries"][0]["title"] == "缓存剧"
    assert cached["current_entries"][0]["title"] == "缓存剧"
    assert cached["cache_status"] == "hit"

    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "更新剧", "都市", "简介", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )
    changed = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir).get_today_rank("hg", limit=10)

    assert changed["current_entries"][0]["title"] == "更新剧"
    assert changed["cache_status"] == "generated"


def test_today_rank_reuses_persistent_douyin_cache_until_sources_change(
    tmp_path, monkeypatch
):
    rank_path = (
        tmp_path
        / "data/dy/jobs/cache/rank/douyin_series_rank_20260708_100000.json"
    )
    write_json(
        rank_path,
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "缓存剧", "cache-series")],
        },
    )
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    first_service = TodayRankService(root=tmp_path, cache_dir=cache_dir)
    first = first_service.get_today_rank("dy")
    restarted = TodayRankService(root=tmp_path, cache_dir=cache_dir)

    def fail_list_snapshots(*_args, **_kwargs):
        raise AssertionError("unchanged Douyin sources must load the persistent cache")

    monkeypatch.setattr(restarted, "list_snapshots", fail_list_snapshots)
    cached = restarted.get_today_rank("dy")

    assert first["current_entries"][0]["title"] == "缓存剧"
    assert cached["current_entries"][0]["title"] == "缓存剧"
    assert cached["cache_status"] == "hit"

    write_json(
        rank_path,
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "更新剧", "cache-series")],
        },
    )
    changed = TodayRankService(root=tmp_path, cache_dir=cache_dir).get_today_rank(
        "dy"
    )

    assert changed["current_entries"][0]["title"] == "更新剧"
    assert changed["cache_status"] == "generated"


def test_today_rank_force_refresh_rebuilds_unchanged_cached_response(tmp_path):
    write_json(
        tmp_path
        / "data/dy/jobs/cache/rank/douyin_series_rank_20260708_100000.json",
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "强制刷新剧", "refresh-series")],
        },
    )
    service = TodayRankService(root=tmp_path)

    first = service.get_today_rank("dy")
    refreshed = service.get_today_rank("dy", force_refresh=True)

    assert first["cache_status"] == "generated"
    assert refreshed["cache_status"] == "refreshed"


def test_today_rank_memory_cache_ttl_starts_after_slow_generation(
    tmp_path, monkeypatch
):
    write_json(
        tmp_path
        / "data/dy/jobs/cache/rank/douyin_series_rank_20260708_100000.json",
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "慢生成剧", "slow-series")],
        },
    )
    service = TodayRankService(root=tmp_path)
    clock = {"now": 100.0}
    revision_calls = 0
    original_source_revision = service._source_revision
    original_list_snapshots = service.list_snapshots

    def counted_source_revision(platform):
        nonlocal revision_calls
        revision_calls += 1
        return original_source_revision(platform)

    def slow_list_snapshots(platform):
        clock["now"] += 60.0
        return original_list_snapshots(platform)

    monkeypatch.setattr(
        "analysis.today_rank_service.time.monotonic", lambda: clock["now"]
    )
    monkeypatch.setattr(service, "_source_revision", counted_source_revision)
    monkeypatch.setattr(service, "list_snapshots", slow_list_snapshots)

    service.get_today_rank("dy", force_refresh=True)
    cached = service.get_today_rank("dy")

    assert cached["cache_status"] == "hit"
    assert revision_calls == 1


def test_today_rank_source_change_invalidates_all_platform_memory_keys(tmp_path):
    rank_path = (
        tmp_path
        / "data/dy/jobs/cache/rank/douyin_series_rank_20260708_100000.json"
    )
    write_json(
        rank_path,
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "旧缓存剧", "cache-series")],
        },
    )
    service = TodayRankService(root=tmp_path)
    service.get_today_rank("dy", limit=10)
    service.get_today_rank("dy", limit=5)
    write_json(
        rank_path,
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "新缓存剧", "cache-series")],
        },
    )

    service.get_today_rank("dy", limit=10, force_refresh=True)
    other_key = service.get_today_rank("dy", limit=5)

    assert other_key["current_entries"][0]["title"] == "新缓存剧"


def test_today_rank_normalizes_and_validates_date_before_caching(tmp_path):
    write_json(
        tmp_path
        / "data/dy/jobs/cache/rank/douyin_series_rank_20260708_100000.json",
        {
            "captured_at": "2026-07-08 10:00:00",
            "records": [dy_record(1, "日期剧", "date-series")],
        },
    )
    service = TodayRankService(root=tmp_path)

    normalized = service.get_today_rank("dy", date=" 2026-07-08 ")

    assert normalized["date"] == "2026-07-08"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        service.get_today_rank("dy", date="2026/07/08")


def test_today_rank_cache_write_is_safe_for_concurrent_same_key(
    tmp_path, monkeypatch
):
    service = TodayRankService(root=tmp_path)
    observed_temporary_paths = []
    observed_paths_lock = threading.Lock()
    original_replace = type(tmp_path).replace

    def synchronized_replace(path, target):
        with observed_paths_lock:
            observed_temporary_paths.append(path)
        return original_replace(path, target)

    monkeypatch.setattr(type(tmp_path), "replace", synchronized_replace)

    def write_cache(marker):
        service._write_response_cache(
            "dy", "revision", "", 10, {"platform": "dy", "marker": marker}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_cache, marker) for marker in (1, 2)]
        for future in futures:
            future.result(timeout=10)

    assert len(set(observed_temporary_paths)) == 2
    cached = json.loads(
        service._response_cache_path("dy", "", 10).read_text(encoding="utf-8")
    )
    assert cached["response"]["marker"] in {1, 2}


def test_today_rank_hongguo_cache_is_scoped_by_date_and_limit(tmp_path):
    exports = tmp_path / "exports"
    for day, titles in (
        ("20260707", ("旧剧一", "旧剧二")),
        ("20260708", ("新剧一", "新剧二")),
    ):
        write_csv(
            exports / day / "top10_rankings.csv",
            [
                ["红果短剧热播榜", str(index), title, "都市", "简介", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]
                for index, title in enumerate(titles, start=1)
            ],
        )
    service = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=tmp_path / ".runtime" / "rank-cache")

    latest_one = service.get_today_rank("hg", limit=1)
    history_two = service.get_today_rank("hg", date="2026-07-07", limit=2)
    latest_one_cached = service.get_today_rank("hg", limit=1)

    assert len(latest_one["current_entries"]) == 1
    assert [item["title"] for item in history_two["current_entries"]] == ["旧剧一", "旧剧二"]
    assert latest_one_cached["current_entries"][0]["title"] == "新剧一"
    assert latest_one_cached["cache_status"] == "hit"


def test_today_rank_cache_hit_skips_raw_source_revision_scan(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "进程缓存剧", "都市", "简介", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )
    service = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=tmp_path / ".runtime" / "rank-cache")
    service.get_today_rank("hg", limit=10)

    def fail_revision():
        raise AssertionError("same-process cache hit must not rescan Hongguo source metadata")

    monkeypatch.setattr(service, "_hongguo_source_revision", fail_revision)

    cached = service.get_today_rank("hg", limit=10)

    assert cached["cache_status"] == "hit"


def test_hongguo_cache_revision_ignores_unrelated_raw_response_files(tmp_path):
    exports = tmp_path / "exports"
    write_csv(
        exports / "20260708/top10_rankings.csv",
        [["红果短剧热播榜", "1", "相关剧", "都市", "完整简介", "无", "无", "1w", "无", "100w", "无", "无", "无", "无", "无", ""]],
    )
    unrelated = tmp_path / "raw" / "mitm_responses_20260708" / "unrelated.json"
    write_json(unrelated, {"series_id": "not-ranked", "title": "无关剧"})
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir).get_today_rank("hg", limit=10)
    unrelated.write_text('{"series_id":"not-ranked","title":"已变化但仍无关"}', encoding="utf-8")

    cached = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir).get_today_rank("hg", limit=10)

    assert cached["cache_status"] == "hit"


def test_hongguo_cache_revision_tracks_raw_metadata_for_sparse_exports(tmp_path):
    exports = tmp_path / "exports"
    sparse_header = (
        "榜单类型,排名,series_id,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,"
        "评分人数,期待指数,热搜指数,最高热度,热评,source_flow,selected_items,sub_selected_items,offset"
    )
    write_text(
        exports / "20260731_top10" / "top10_rankings.csv",
        "\n".join([sparse_header, ",,series-raw,,,,无,无,12w,无,500w,900w,无,无,无,无,,610,all,ranklist_hot_play_sc,"]),
    )
    raw = tmp_path / "raw" / "mitm_responses_20260731" / "response.json"
    write_json(raw, {"series_id": "series-raw", "title": "初始剧名", "video_desc": "简介", "sub_title_list": [{"content": "玄幻"}]})
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    first = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir).get_today_rank("hg", limit=10)
    write_json(raw, {"series_id": "series-raw", "title": "更新剧名", "video_desc": "简介", "sub_title_list": [{"content": "玄幻"}]})

    changed = TodayRankService(root=tmp_path, hongguo_exports_root=exports, cache_dir=cache_dir).get_today_rank("hg", limit=10)

    assert first["current_entries"][0]["title"] == "初始剧名"
    assert changed["current_entries"][0]["title"] == "更新剧名"
    assert changed["cache_status"] == "generated"


def test_hongguo_cache_revision_tracks_raw_cover_for_complete_exports(tmp_path):
    exports = tmp_path / "exports"
    header = (
        "榜单类型,排名,series_id,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,"
        "评分人数,期待指数,热搜指数,最高热度,热评"
    )
    write_text(
        exports / "20260731_top10" / "top10_rankings.csv",
        "\n".join(
            [
                header,
                "红果热播榜,1,series-cover,完整剧名,都市,完整简介,无,无,12w,无,500w,"
                "900w,无,无,无,无,完整热评",
            ]
        ),
    )
    raw = tmp_path / "raw" / "mitm_responses_20260731" / "response.json"
    write_json(
        raw,
        {
            "series_id": "series-cover",
            "title": "完整剧名",
            "cover_url": "https://example.test/old.jpg",
        },
    )
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    first = TodayRankService(
        root=tmp_path,
        hongguo_exports_root=exports,
        cache_dir=cache_dir,
    ).get_today_rank("hg", limit=10)
    write_json(
        raw,
        {
            "series_id": "series-cover",
            "title": "完整剧名",
            "cover_url": "https://example.test/new.jpg",
        },
    )

    changed = TodayRankService(
        root=tmp_path,
        hongguo_exports_root=exports,
        cache_dir=cache_dir,
    ).get_today_rank("hg", limit=10)

    assert first["current_entries"][0]["cover_url"].endswith("old.jpg")
    assert changed["current_entries"][0]["cover_url"].endswith("new.jpg")
    assert changed["cache_status"] == "generated"


def test_hongguo_persistent_raw_index_skips_unchanged_file_reads(
    tmp_path, monkeypatch
):
    exports = tmp_path / "exports"
    header = "榜单类型,排名,series_id,剧名,标签,简介,热评"
    write_text(
        exports / "20260731_top10" / "top10_rankings.csv",
        "\n".join(
            [
                header,
                "红果热播榜,1,series-index,索引剧,都市,完整简介,完整热评",
            ]
        ),
    )
    raw = tmp_path / "raw" / "mitm_responses_20260731" / "response.json"
    write_json(
        raw,
        {
            "series_id": "series-index",
            "title": "索引剧",
            "cover_url": "https://example.test/cover.jpg",
        },
    )
    cache_dir = tmp_path / ".runtime" / "rank-cache"
    first = TodayRankService(
        root=tmp_path,
        hongguo_exports_root=exports,
        cache_dir=cache_dir,
    ).get_today_rank("hg", limit=10)
    original_read_text = type(tmp_path).read_text

    def reject_unchanged_raw_read(path, *args, **kwargs):
        if path == raw:
            raise AssertionError("unchanged raw file should use its persistent index")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "read_text", reject_unchanged_raw_read)
    cached = TodayRankService(
        root=tmp_path,
        hongguo_exports_root=exports,
        cache_dir=cache_dir,
    ).get_today_rank("hg", limit=10)

    assert first["current_entries"][0]["cover_url"].endswith("cover.jpg")
    assert cached["cache_status"] == "hit"


def test_today_rank_date_filter_and_missing_previous(tmp_path):
    write_json(
        tmp_path / "data/dy/jobs/only/rank/douyin_series_rank_20260702_100000.json",
        {"captured_at": "2026-07-02 10:00:00", "records": [dy_record(1, "A", "s1")]},
    )

    data = TodayRankService(root=tmp_path).get_today_rank("dy", date="2026-07-02")

    assert data["summary"]["previous_count"] == 0
    assert data["summary"]["new_count"] == 1
    assert data["rank_up"] == []


def test_today_rank_counts_uninterrupted_and_same_day_snapshots_once(tmp_path):
    for job, captured_at in (
        ("day1-am", "2026-07-01 09:00:00"),
        ("day1-pm", "2026-07-01 18:00:00"),
        ("day2", "2026-07-02 09:00:00"),
    ):
        filename_time = captured_at.replace("-", "").replace(":", "").replace(" ", "_")
        write_json(
            tmp_path / f"data/dy/jobs/{job}/rank/douyin_series_rank_{filename_time}.json",
            {"captured_at": captured_at, "records": [dy_record(1, "连续剧", "series-a")]},
        )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    entry = data["current_entries"][0]

    assert entry["continuous_rank_days"] == 2
    assert entry["cumulative_rank_days"] == 2
    assert entry["rank_duration"] == "连续在榜2天 / 累计在榜2天"


def test_today_rank_explicit_absence_resets_continuous_but_preserves_cumulative(tmp_path):
    records_by_date = {
        "2026-07-01": [dy_record(1, "回榜剧", "series-a")],
        "2026-07-02": [dy_record(1, "其他剧", "series-b")],
        "2026-07-03": [dy_record(1, "回榜剧", "series-a")],
    }
    for date, records in records_by_date.items():
        write_json(
            tmp_path / f"data/dy/jobs/{date}/rank/douyin_series_rank_{date.replace('-', '')}_090000.json",
            {"captured_at": f"{date} 09:00:00", "records": records},
        )

    data = TodayRankService(root=tmp_path).get_today_rank("dy")
    entry = data["current_entries"][0]

    assert entry["continuous_rank_days"] == 1
    assert entry["cumulative_rank_days"] == 2
    assert entry["rank_duration"] == "连续在榜1天 / 累计在榜2天"


def test_today_rank_duration_counts_only_dates_present_in_rank_csv_files(tmp_path):
    day1 = dy_record(1, "跨缺口剧", "series-a")
    day2_other_board = dy_record(1, "其他榜剧", "series-b")
    day2_other_board["tab"] = "推荐榜"
    day3 = dy_record(1, "跨缺口剧", "series-a")
    for date, records in (
        ("2026-07-01", [day1]),
        ("2026-07-02", [day2_other_board]),
        ("2026-07-03", [day3]),
    ):
        write_json(
            tmp_path / f"data/dy/jobs/{date}/rank/douyin_series_rank_{date.replace('-', '')}_090000.json",
            {"captured_at": f"{date} 09:00:00", "records": records},
        )

    current = TodayRankService(root=tmp_path).get_today_rank("dy")
    historical = TodayRankService(root=tmp_path).get_today_rank("dy", date="2026-07-01")

    assert current["current_entries"][0]["continuous_rank_days"] == 1
    assert current["current_entries"][0]["cumulative_rank_days"] == 2
    assert current["current_entries"][0]["rank_duration"] == "连续在榜1天 / 累计在榜2天"
    assert historical["current_entries"][0]["continuous_rank_days"] == 1
    assert historical["current_entries"][0]["cumulative_rank_days"] == 1


def test_today_rank_duration_uses_longest_recorded_streak_and_skips_weekends(tmp_path):
    for date in ("2026-07-07", "2026-07-08", "2026-07-11", "2026-07-12", "2026-07-13", "2026-07-27"):
        write_json(
            tmp_path / f"data/dy/jobs/{date}/rank/douyin_series_rank_{date.replace('-', '')}_090000.json",
            {"captured_at": f"{date} 09:00:00", "records": [dy_record(1, "连续剧", "series-a")]},
        )

    entry = TodayRankService(root=tmp_path).get_today_rank("dy")["current_entries"][0]

    assert entry["continuous_rank_days"] == 3
    assert entry["cumulative_rank_days"] == 6
    assert entry["rank_duration"] == "连续在榜3天 / 累计在榜6天"


def test_today_rank_duration_keeps_a_friday_to_monday_streak(tmp_path):
    for date in ("2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27"):
        write_json(
            tmp_path / f"data/dy/jobs/{date}/rank/douyin_series_rank_{date.replace('-', '')}_090000.json",
            {"captured_at": f"{date} 09:00:00", "records": [dy_record(1, "周末不断榜", "series-a")]},
        )

    entry = TodayRankService(root=tmp_path).get_today_rank("dy")["current_entries"][0]

    assert entry["continuous_rank_days"] == 6
    assert entry["cumulative_rank_days"] == 6


def test_today_rank_invalid_platform(tmp_path):
    with pytest.raises(ValueError):
        TodayRankService(root=tmp_path).get_today_rank("xhs")


def test_webgui_registers_today_rank_route():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/rank/today" in paths


def test_today_rank_api_requires_authentication():
    client = TestClient(WebGUI.app)

    response = client.get("/api/rank/today?platform=dy")

    assert response.status_code == 401


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "榜单类型,排名,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,评分人数,期待指数,热搜指数,最高热度,热评"
    lines = [header]
    for row in rows:
        lines.append(",".join(row))
    path.write_text("\n".join(lines), encoding="utf-8")


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def dy_record(rank, title, series_id, heat_text="100", like_count="10"):
    return {
        "rank": rank,
        "title": title,
        "rank_category": "短剧",
        "tab": "热榜",
        "heat_text": heat_text,
        "like_count": like_count,
        "favorite_count": "5",
        "comment_count": "1",
        "series_id": series_id,
        "item_id": f"item-{series_id}",
    }
