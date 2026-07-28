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

from typing import Any, Dict, List, Optional


def parse_bilibili_subtitle(subtitle_json: Dict[str, Any], language: str = "") -> Dict[str, Any]:
    """
    Convert Bilibili subtitle JSON into a stable transcript structure.
    """
    segments: List[Dict[str, Any]] = []
    for item in subtitle_json.get("body") or []:
        text = str(item.get("content", "")).strip()
        if not text:
            continue

        try:
            start = float(item.get("from", 0))
            end = float(item.get("to", start))
        except (TypeError, ValueError):
            continue

        segments.append({
            "start": start,
            "end": end,
            "text": text,
        })

    return {
        "source": "subtitle",
        "language": language,
        "segments": segments,
        "text": "\n".join(segment["text"] for segment in segments),
    }


def get_subtitle_items(player_info: Dict[str, Any]) -> List[Dict[str, Any]]:
    subtitle = player_info.get("subtitle") or {}
    subtitles = subtitle.get("subtitles") or []
    if not isinstance(subtitles, list):
        return []
    return [item for item in subtitles if isinstance(item, dict)]


def select_subtitle(subtitles: List[Dict[str, Any]], language_priority: List[str]) -> Optional[Dict[str, Any]]:
    """
    Pick a subtitle by language priority, falling back to the first usable item.
    """
    usable = [
        item for item in subtitles
        if item.get("subtitle_url") or item.get("subtitleUrl")
    ]
    if not usable:
        return None

    for language in language_priority:
        for item in usable:
            if item.get("lan") == language:
                return item

    return usable[0]


def get_subtitle_url(subtitle_item: Dict[str, Any]) -> str:
    return subtitle_item.get("subtitle_url") or subtitle_item.get("subtitleUrl") or ""


def get_subtitle_language(subtitle_item: Dict[str, Any]) -> str:
    return subtitle_item.get("lan") or subtitle_item.get("lan_doc") or ""
