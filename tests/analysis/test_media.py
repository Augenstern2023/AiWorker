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

import pytest

from analysis.media import (
    get_durl_video_url,
    get_duration_seconds,
    get_media_url,
    pick_best_dash_audio,
    save_bytes,
)


def test_pick_best_dash_audio_uses_highest_bandwidth():
    play_url = {
        "dash": {
            "audio": [
                {"id": 1, "bandwidth": 128000, "baseUrl": "low"},
                {"id": 2, "bandwidth": 320000, "baseUrl": "high"},
            ]
        }
    }

    assert pick_best_dash_audio(play_url) == {"id": 2, "bandwidth": 320000, "baseUrl": "high"}


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"baseUrl": "base"}, "base"),
        ({"base_url": "base_snake"}, "base_snake"),
        ({"backupUrl": ["backup"]}, "backup"),
        ({"backup_url": ["backup_snake"]}, "backup_snake"),
    ],
)
def test_get_media_url_supports_bilibili_url_variants(item, expected):
    assert get_media_url(item) == expected


def test_get_durl_video_url_picks_largest_size():
    assert get_durl_video_url({"durl": [{"size": 1, "url": "small"}, {"size": 2, "url": "large"}]}) == "large"


def test_get_duration_seconds_handles_milliseconds_and_seconds():
    assert get_duration_seconds({"timelength": 60000}) == 60
    assert get_duration_seconds({"duration": 60}) == 60
    assert get_duration_seconds({"duration": "bad"}) == 0


@pytest.mark.asyncio
async def test_save_bytes_writes_arbitrary_media_file(tmp_path):
    file_path = await save_bytes(tmp_path / "123" / "audio.m4s", b"audio")

    assert file_path.read_bytes() == b"audio"

