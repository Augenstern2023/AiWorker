# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any


SERIES_OVERVIEW_FIELDS = (
    "core_event",
    "character_progress",
    "conflict",
    "reversal",
    "highlight",
    "cliffhanger",
)

SERIES_OVERVIEW_FIELD_LABELS = (
    ("core_event", "核心事件"),
    ("character_progress", "人物推进"),
    ("conflict", "主要冲突"),
    ("reversal", "关键反转"),
    ("highlight", "剧情亮点"),
    ("cliffhanger", "结尾悬念"),
)


def normalize_series_overview(value: Any) -> dict[str, str]:
    """Return the strict six-field series overview contract."""

    if not isinstance(value, dict):
        raise ValueError("全集剧情概览必须是对象")
    invalid = [key for key in SERIES_OVERVIEW_FIELDS if not isinstance(value.get(key), str)]
    if invalid:
        raise ValueError(f"全集剧情概览字段必须是字符串：{invalid}")
    normalized = {key: value[key].strip() for key in SERIES_OVERVIEW_FIELDS}
    missing = [key for key, text in normalized.items() if not text]
    if missing:
        raise ValueError(f"全集剧情概览缺少字段：{missing}")
    return normalized


def read_series_overview(value: Any) -> dict[str, str] | None:
    """Read legacy-compatible overview data without failing project hydration."""

    try:
        return normalize_series_overview(value)
    except ValueError:
        return None
