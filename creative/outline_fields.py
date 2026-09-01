# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
from typing import Any


OUTLINE_TEXT_FIELDS = (
    "core_event",
    "conflict",
    "character_progress",
    "highlight",
    "reversal",
    "cliffhanger",
)

OUTLINE_FIELD_LABELS = {
    "core_event": "核心事件",
    "conflict": "冲突",
    "character_progress": "人物推进",
    "highlight": "亮点",
    "reversal": "反转",
    "cliffhanger": "结尾悬念",
}


def outline_field_source_fingerprint(episode: dict[str, Any], field: str) -> str:
    if field not in OUTLINE_TEXT_FIELDS:
        raise ValueError("不支持的大纲字段")
    content = episode.get("content")
    source = content if isinstance(content, dict) else episode
    payload = {
        "project_id": episode.get("project_id"),
        "batch_id": episode.get("batch_id"),
        "episode_id": episode.get("id"),
        "episode_number": episode.get("episode_number", source.get("episode")),
        "field": field,
        "value": source.get(field),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
