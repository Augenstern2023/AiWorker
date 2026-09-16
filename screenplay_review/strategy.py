# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Execution planning for differently sized screenplay reviews."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from typing import Any


STRATEGY_SCHEMA_VERSION = "screenplay-review-strategy-v1"
DEFAULT_UNIT_TARGET_TOKENS = 32_000
DEFAULT_BATCH_SOURCE_TARGET_TOKENS = 72_000


@dataclass(frozen=True)
class ReviewExecutionPlan:
    mode: str
    scale: str
    episode_numbers: tuple[int, ...]
    batches: tuple[tuple[int, ...], ...]
    use_batch_summaries: bool
    estimated_source_tokens: int
    unit_target_tokens: int
    oversized_episodes: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = STRATEGY_SCHEMA_VERSION
        payload["episode_numbers"] = list(self.episode_numbers)
        payload["batches"] = [list(batch) for batch in self.batches]
        payload["oversized_episodes"] = list(self.oversized_episodes)
        return payload


def plan_review_execution(
    source: dict[str, Any],
    *,
    unit_target_tokens: int = DEFAULT_UNIT_TARGET_TOKENS,
    batch_source_target_tokens: int = DEFAULT_BATCH_SOURCE_TARGET_TOKENS,
) -> ReviewExecutionPlan:
    """Choose a semantic review pipeline without truncating source material."""
    episodes = [item for item in source.get("episodes") or [] if isinstance(item, dict)]
    episode_numbers = tuple(
        int(item.get("episode_number") or 0)
        for item in episodes
        if int(item.get("episode_number") or 0) > 0
    )
    if not episode_numbers:
        raise ValueError("审核来源没有可用剧本")

    mode = str(source.get("mode") or "single")
    count = len(episode_numbers)
    if count == 1:
        scale = "single"
    elif mode == "collection":
        scale = "collection"
    elif count <= 5:
        scale = "small_multiple"
    else:
        scale = "large_multiple"

    weights = {
        int(item["episode_number"]): estimate_tokens(item)
        for item in episodes
        if int(item.get("episode_number") or 0) > 0
    }
    use_batch_summaries = scale in {"large_multiple", "collection"}
    batches = (
        _dynamic_batches(
            episode_numbers,
            weights,
            target_tokens=max(1, int(batch_source_target_tokens)),
        )
        if use_batch_summaries
        else (episode_numbers,)
    )
    oversized = tuple(
        number
        for number in episode_numbers
        if weights.get(number, 0) > max(1, int(unit_target_tokens))
    )
    return ReviewExecutionPlan(
        mode=mode,
        scale=scale,
        episode_numbers=episode_numbers,
        batches=batches,
        use_batch_summaries=use_batch_summaries,
        estimated_source_tokens=estimate_tokens(source),
        unit_target_tokens=max(1, int(unit_target_tokens)),
        oversized_episodes=oversized,
    )


def estimate_tokens(value: Any) -> int:
    """Estimate planning load; this value never clips or rejects input."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    ascii_count = sum(1 for character in text if ord(character) < 128)
    non_ascii_count = len(text) - ascii_count
    return max(1, non_ascii_count + math.ceil(ascii_count / 4))


def _dynamic_batches(
    episode_numbers: tuple[int, ...],
    weights: dict[int, int],
    *,
    target_tokens: int,
) -> tuple[tuple[int, ...], ...]:
    """Prefer 3-5 episode batches and split earlier when semantic load is high."""
    batches: list[list[int]] = []
    current: list[int] = []
    current_weight = 0
    for number in episode_numbers:
        weight = weights.get(number, 1)
        should_flush = bool(current) and (
            len(current) >= 5
            or (len(current) >= 3 and current_weight + weight > target_tokens)
        )
        if should_flush:
            batches.append(current)
            current = []
            current_weight = 0
        current.append(number)
        current_weight += weight
    if current:
        batches.append(current)

    if len(batches) > 1 and len(batches[-1]) < 3:
        tail = batches[-1]
        previous = batches[-2]
        while len(tail) < 3 and len(previous) > 3:
            tail.insert(0, previous.pop())
        if len(previous) + len(tail) <= 5:
            batches[-2:] = [previous + tail]
    return tuple(tuple(batch) for batch in batches if batch)
