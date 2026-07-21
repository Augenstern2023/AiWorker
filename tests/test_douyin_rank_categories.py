# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from tools.fetch_douyin_series_rank_md import CommentInfo
from tools.fetch_douyin_series_rank_md import DramaInfo
from tools.fetch_douyin_series_rank_md import current_douyin_records
from tools.fetch_douyin_series_rank_md import find_first_heat
from tools.fetch_douyin_series_rank_md import group_key
from tools.fetch_douyin_series_rank_md import heat_from_visible_text
from tools.fetch_douyin_series_rank_md import render_json_payload
from tools.fetch_douyin_series_rank_md import render_markdown


def test_current_records_keep_short_drama_and_comic_drama_separate():
    grouped = {
        group_key("短剧", "热榜"): [
            DramaInfo(rank=1, title="同名作品", rank_category="短剧", tab="热榜", heat_text="10万")
        ],
        group_key("漫剧", "热榜"): [
            DramaInfo(rank=1, title="同名作品", rank_category="漫剧", tab="热榜", heat_text="20万")
        ],
    }

    records = current_douyin_records(grouped)

    assert len(records) == 2
    assert records["短剧::热榜::同名作品"]["category"] == "短剧"
    assert records["漫剧::热榜::同名作品"]["category"] == "漫剧"


def test_render_markdown_contains_all_douyin_rank_sections():
    markdown = render_markdown(
        {
            group_key("短剧", "热榜"): [
                DramaInfo(rank=1, title="短剧A", rank_category="短剧", tab="热榜")
            ],
            group_key("短剧", "榜单"): [],
            group_key("漫剧", "热榜"): [
                DramaInfo(rank=1, title="漫剧A", rank_category="漫剧", tab="热榜")
            ],
            group_key("漫剧", "榜单"): [],
        },
        captured_at="2026-06-30 10:00:00",
        limit=1,
        comment_limit=1,
        previous_path=None,
    )

    assert "# 抖音短剧/漫剧榜单" in markdown
    assert "## 短剧热榜" in markdown
    assert "## 短剧榜单" in markdown
    assert "## 漫剧热榜" in markdown
    assert "## 漫剧榜单" in markdown
    assert "| 分类 | 短剧 |" in markdown
    assert "| 分类 | 漫剧 |" in markdown


def test_render_json_payload_contains_rank_detail_and_comments():
    payload = render_json_payload(
        {
            group_key("短剧", "热榜"): [
                DramaInfo(
                    rank=1,
                    title="短剧A",
                    rank_category="短剧",
                    tab="热榜",
                    detail_url="https://www.douyin.com/video/1",
                    item_id="1",
                    comments=[CommentInfo(user="u", content="好看", like_count="10")],
                )
            ]
        },
        captured_at="2026-07-02 10:00:00",
    )

    item = payload["records"][0]
    assert item["rank_category"] == "短剧"
    assert item["tab"] == "热榜"
    assert item["detail_url"] == "https://www.douyin.com/video/1"
    assert item["item_id"] == "1"
    assert item["comments"][0]["content"] == "好看"


def test_douyin_heat_helpers_parse_nested_api_and_visible_text():
    assert find_first_heat({"data": {"series": {"rank_value": "987654"}}}) == "987654"
    assert find_first_heat({"aweme": {"statistics": {"play_count": "12.3万"}}}) == "12.3万"
    assert heat_from_visible_text("短剧A 热度 456789 点赞 1万") == "456789"
