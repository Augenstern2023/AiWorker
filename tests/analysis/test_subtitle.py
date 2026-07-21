# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from analysis.subtitle import (
    get_subtitle_items,
    get_subtitle_language,
    get_subtitle_url,
    parse_bilibili_subtitle,
    select_subtitle,
)


def test_parse_bilibili_subtitle_builds_segments_and_text():
    result = parse_bilibili_subtitle(
        {
            "body": [
                {"from": 0, "to": 1.5, "content": "第一句"},
                {"from": 1.5, "to": 3, "content": "第二句"},
            ]
        },
        language="zh-CN",
    )

    assert result["source"] == "subtitle"
    assert result["language"] == "zh-CN"
    assert result["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "第一句"},
        {"start": 1.5, "end": 3.0, "text": "第二句"},
    ]
    assert result["text"] == "第一句\n第二句"


def test_parse_bilibili_subtitle_skips_empty_and_malformed_entries():
    result = parse_bilibili_subtitle(
        {
            "body": [
                {"from": 0, "to": 1, "content": "  "},
                {"from": "bad", "to": 1, "content": "坏时间"},
                {"from": 2, "to": 3, "content": "正常"},
            ]
        }
    )

    assert result["segments"] == [{"start": 2.0, "end": 3.0, "text": "正常"}]
    assert result["text"] == "正常"


def test_get_subtitle_items_handles_missing_or_invalid_shape():
    assert get_subtitle_items({}) == []
    assert get_subtitle_items({"subtitle": {"subtitles": "bad"}}) == []
    assert get_subtitle_items({"subtitle": {"subtitles": [{"lan": "zh-CN"}, "bad"]}}) == [{"lan": "zh-CN"}]


def test_select_subtitle_uses_language_priority_then_fallback():
    subtitles = [
        {"lan": "en-US", "subtitle_url": "//example/en.json"},
        {"lan": "zh-CN", "subtitle_url": "//example/zh.json"},
    ]

    selected = select_subtitle(subtitles, ["zh-CN", "en-US"])
    assert selected == subtitles[1]

    fallback = select_subtitle(subtitles, ["ja-JP"])
    assert fallback == subtitles[0]


def test_select_subtitle_and_accessors_support_url_variants():
    selected = select_subtitle(
        [{"lan_doc": "中文", "subtitleUrl": "//example/zh.json"}],
        ["zh-CN"],
    )

    assert selected is not None
    assert get_subtitle_url(selected) == "//example/zh.json"
    assert get_subtitle_language(selected) == "中文"

