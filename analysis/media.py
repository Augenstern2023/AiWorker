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

import pathlib
from typing import Any, Dict, Optional

import aiofiles


def pick_best_dash_audio(play_url: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dash = play_url.get("dash") or {}
    audio_list = dash.get("audio") or []
    if not audio_list:
        return None
    return max(audio_list, key=lambda item: item.get("bandwidth", 0))


def get_media_url(media_item: Dict[str, Any]) -> str:
    backup_url = media_item.get("backupUrl") or media_item.get("backup_url") or []
    if not isinstance(backup_url, list):
        backup_url = []
    return (
        media_item.get("baseUrl")
        or media_item.get("base_url")
        or (backup_url[0] if backup_url else "")
        or ""
    )


def get_durl_video_url(play_url: Dict[str, Any]) -> str:
    durl_list = play_url.get("durl") or []
    best_url = ""
    max_size = -1
    for durl in durl_list:
        size = durl.get("size", 0)
        if size > max_size:
            max_size = size
            best_url = durl.get("url", "")
    return best_url


def get_duration_seconds(play_url: Dict[str, Any]) -> int:
    duration = play_url.get("timelength") or play_url.get("duration") or 0
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return 0
    if duration > 10000:
        return duration // 1000
    return duration


async def save_bytes(file_path: pathlib.Path, content: bytes) -> pathlib.Path:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(file_path, "wb") as file:
        await file.write(content)
    return file_path

