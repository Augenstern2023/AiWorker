# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import pytest

from tools.fetch_douyin_series_rank_md import CommentInfo
from tools.fetch_douyin_series_rank_md import DramaInfo
from tools.fetch_douyin_series_rank_md import assert_distinct_category_rankings
from tools.fetch_douyin_series_rank_md import category_rankings_are_identical
from tools.fetch_douyin_series_rank_md import current_douyin_records
from tools.fetch_douyin_series_rank_md import first_video_id_from_captures
from tools.fetch_douyin_series_rank_md import find_first_heat
from tools.fetch_douyin_series_rank_md import group_key
from tools.fetch_douyin_series_rank_md import heat_from_visible_text
from tools.fetch_douyin_series_rank_md import merge_detail_from_json
from tools.fetch_douyin_series_rank_md import merge_records_from_json
from tools.fetch_douyin_series_rank_md import normalize_series_item
from tools.fetch_douyin_series_rank_md import prepare_series_page
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


def test_detail_merge_uses_only_json_object_matching_current_item_id():
    info = DramaInfo(
        title="室友偷刷我会员卡后",
        series_id="series-1",
        item_id="item-1",
    )
    captures = [
        {
            "aweme_detail": {
                "aweme_id": "unrelated-item",
                "desc": "无关新闻",
                "statistics": {"digg_count": 999999, "collect_count": 888888, "comment_count": 777777},
                "author": {"nickname": "无关作者"},
            }
        },
        {
            "aweme_detail": {
                "aweme_id": "item-1",
                "desc": "室友滥用会员卡后引发冲突。",
                "statistics": {"digg_count": 101, "collect_count": 52, "comment_count": 13},
                "author": {"nickname": "目标作者"},
            }
        },
    ]

    merge_detail_from_json(info, captures)

    assert info.description == "室友滥用会员卡后引发冲突。"
    assert info.like_count == "101"
    assert info.favorite_count == "52"
    assert info.comment_count == "13"


def test_detail_merge_prefers_item_match_over_series_level_fallback():
    info = DramaInfo(title="目标剧", series_id="series-1", item_id="item-1")
    captures = [
        {"series_id": "series-1", "description": "系列层简介"},
        {"aweme_id": "item-1", "series_id": "series-1", "description": "当前视频简介"},
    ]

    merge_detail_from_json(info, captures)

    assert info.description == "当前视频简介"


def test_detail_merge_leaves_fields_empty_when_no_json_object_matches_identity():
    info = DramaInfo(title="目标剧", series_id="series-1", item_id="item-1")

    merge_detail_from_json(
        info,
        [{"aweme_detail": {"aweme_id": "unrelated-item", "desc": "无关简介", "digg_count": 999}}],
    )

    assert info.description == ""
    assert info.like_count == ""


def test_video_id_fallback_uses_only_object_matching_current_series():
    captures = [
        {"aweme_id": "7000000000000000001", "series_id": "7000000000000000002"},
        {"series_id": "7000000000000000003", "watched_item": "7000000000000000004"},
    ]

    assert first_video_id_from_captures(captures, "7000000000000000003") == "7000000000000000004"


def test_rank_list_merge_does_not_use_container_desc_from_unrelated_item():
    record = DramaInfo(title="目标剧", series_id="series-1", item_id="item-1")
    captures = [
        {
            "card_list": [
                {
                    "series_id": "series-1",
                    "title": "目标剧",
                    "description": "目标剧情简介",
                },
                {
                    "series_id": "series-2",
                    "title": "其他剧",
                    "description": "无关新闻",
                },
            ]
        }
    ]

    merge_records_from_json([record], captures)

    assert record.description == "目标剧情简介"


def test_rank_item_merge_does_not_replace_known_identity_from_generic_nested_id():
    record = DramaInfo(title="目标剧", series_id="series-1", item_id="item-1")
    captures = [
        {
            "series_id": "series-1",
            "title": "目标剧",
            "description": "目标剧情简介",
            "tracking": {"id": "unrelated-tracking-id"},
        }
    ]

    merge_records_from_json([record], captures)

    assert record.series_id == "series-1"
    assert record.item_id == "item-1"


def test_rank_item_normalization_prefers_explicit_item_identity_over_nested_generic_id():
    record = normalize_series_item(
        {
            "series_id": "7000000000000000001",
            "series_name": "目标剧",
            "tracking": {"id": "7000000000000000002"},
            "watched_item": "7000000000000000003",
        },
        "短剧",
        "榜单",
        1,
    )

    assert record.series_id == "7000000000000000001"
    assert record.item_id == "7000000000000000003"
    assert record.detail_url == "https://www.douyin.com/video/7000000000000000003"


def test_rank_item_normalization_ignores_zero_watched_item_before_raw_aweme_id():
    record = normalize_series_item(
        {
            "aweme_id": "7000000000000000003",
            "series": {
                "series_id": "7000000000000000001",
                "series_name": "目标剧",
                "watched_item": "0",
            },
        },
        "短剧",
        "榜单",
        1,
    )

    assert record.item_id == "7000000000000000003"
    assert record.detail_url == "https://www.douyin.com/video/7000000000000000003"


def test_rank_item_normalization_does_not_use_cover_image_as_detail_url():
    record = normalize_series_item(
        {
            "series": {
                "series_id": "7000000000000000001",
                "series_name": "目标剧",
                "cover_url": "https://p3-sign.douyinpic.com/example.webp?x=1",
            },
        },
        "短剧",
        "热榜",
        1,
    )

    assert record.detail_url == "https://www.douyin.com/series/7000000000000000001"


def test_rank_list_enrichment_does_not_replace_detail_url_with_cover_url():
    record = DramaInfo(
        title="目标剧",
        series_id="series-1",
        item_id="item-1",
        detail_url="https://www.douyin.com/video/item-1",
    )
    merge_records_from_json(
        [record],
        [{"series_id": "series-1", "cover_url": "https://example.com/cover.webp"}],
    )

    assert record.detail_url == "https://www.douyin.com/video/item-1"


def test_identical_short_and_comic_rankings_are_detected():
    grouped = {
        group_key("短剧", "榜单"): [
            DramaInfo(rank=1, title="重复剧", rank_category="短剧", tab="榜单", series_id="series-1")
        ],
        group_key("漫剧", "榜单"): [
            DramaInfo(rank=1, title="重复剧", rank_category="漫剧", tab="榜单", series_id="series-1")
        ],
    }

    assert category_rankings_are_identical(grouped, "榜单") is True
    with pytest.raises(RuntimeError, match="短剧与漫剧榜单完全相同"):
        assert_distinct_category_rankings(grouped)


def test_one_identical_tab_is_not_enough_to_reject_distinct_categories():
    grouped = {
        group_key("短剧", "热榜"): [
            DramaInfo(rank=1, title="同一热剧", rank_category="短剧", tab="热榜", series_id="shared-hot")
        ],
        group_key("漫剧", "热榜"): [
            DramaInfo(rank=1, title="同一热剧", rank_category="漫剧", tab="热榜", series_id="shared-hot")
        ],
        group_key("短剧", "榜单"): [
            DramaInfo(rank=1, title="短剧A", rank_category="短剧", tab="榜单", series_id="short-1")
        ],
        group_key("漫剧", "榜单"): [
            DramaInfo(rank=1, title="漫剧A", rank_category="漫剧", tab="榜单", series_id="comic-1")
        ],
    }

    assert_distinct_category_rankings(grouped)


def test_distinct_short_and_comic_rankings_are_not_flagged():
    grouped = {
        group_key("短剧", "榜单"): [
            DramaInfo(rank=1, title="短剧A", rank_category="短剧", tab="榜单", series_id="short-1")
        ],
        group_key("漫剧", "榜单"): [
            DramaInfo(rank=1, title="漫剧A", rank_category="漫剧", tab="榜单", series_id="comic-1")
        ],
    }

    assert category_rankings_are_identical(grouped, "榜单") is False


@pytest.mark.asyncio
async def test_prepare_series_page_uses_navigation_entry_that_bootstraps_rank_content(
    monkeypatch,
):
    class FakePage:
        def __init__(self):
            self.rank_content_ready = False

        async def goto(self, url, **_kwargs):
            self.rank_content_ready = (
                url == "https://www.douyin.com/series?from_nav=1"
            )

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    async def fake_click_text(page, _category, timeout=0):
        return page.rank_content_ready

    async def fake_active_category(_page):
        return "短剧"

    monkeypatch.setattr(
        "tools.fetch_douyin_series_rank_md.click_text", fake_click_text
    )
    monkeypatch.setattr(
        "tools.fetch_douyin_series_rank_md.active_rank_category",
        fake_active_category,
    )

    await prepare_series_page(FakePage(), "短剧")


@pytest.mark.asyncio
async def test_prepare_series_page_rejects_category_click_that_does_not_activate(monkeypatch):
    class FakePage:
        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    async def fake_click_text(_page, _category, timeout=0):
        return True

    async def fake_active_category(_page):
        return "短剧"

    monkeypatch.setattr("tools.fetch_douyin_series_rank_md.click_text", fake_click_text)
    monkeypatch.setattr("tools.fetch_douyin_series_rank_md.active_rank_category", fake_active_category)

    with pytest.raises(RuntimeError, match="分类切换未生效"):
        await prepare_series_page(FakePage(), "漫剧")


@pytest.mark.asyncio
async def test_prepare_series_page_rejects_category_missing_from_current_page(monkeypatch):
    class FakePage:
        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

    async def fake_click_text(_page, _category, timeout=0):
        return False

    monkeypatch.setattr("tools.fetch_douyin_series_rank_md.click_text", fake_click_text)

    with pytest.raises(RuntimeError, match="无法点击左侧漫剧入口"):
        await prepare_series_page(FakePage(), "漫剧")
