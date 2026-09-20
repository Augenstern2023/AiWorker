# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from analysis.llm import is_retryable_llm_exception


SCRIPT_SERIES_DEFAULT_CHUNK_SIZE = 2
SCRIPT_SERIES_HIGH_CAPACITY_CHUNK_SIZE = 3
SCRIPT_SERIES_HIGH_CAPACITY_MIN_OUTPUT_TOKENS = 65_536
SCRIPT_SERIES_MAX_CONCURRENCY = 2

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


def resolve_script_series_chunk_size(provider: Any) -> int:
    """Use a slightly larger batch only when the configured output budget is safe."""

    try:
        max_output_tokens = int(getattr(provider, "max_output_tokens", 0) or 0)
    except (TypeError, ValueError):
        max_output_tokens = 0
    if max_output_tokens >= SCRIPT_SERIES_HIGH_CAPACITY_MIN_OUTPUT_TOKENS:
        return SCRIPT_SERIES_HIGH_CAPACITY_CHUNK_SIZE
    return SCRIPT_SERIES_DEFAULT_CHUNK_SIZE


async def run_ordered_script_windows(
    items: Sequence[ItemT],
    *,
    generate: Callable[[ItemT], Awaitable[ResultT]],
    persist: Callable[[ItemT, ResultT], Awaitable[None]],
    on_downgrade: Callable[[Exception], None],
    max_concurrency: int = SCRIPT_SERIES_MAX_CONCURRENCY,
) -> None:
    """Generate bounded windows concurrently and persist their results in order."""

    concurrency = max(1, min(int(max_concurrency), SCRIPT_SERIES_MAX_CONCURRENCY))
    cursor = 0
    while cursor < len(items):
        window = list(items[cursor : cursor + concurrency])
        results = await asyncio.gather(
            *(generate(item) for item in window),
            return_exceptions=True,
        )
        persisted = 0
        retry_window = False
        for item, result in zip(window, results):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                if concurrency > 1 and is_retryable_llm_exception(result):
                    on_downgrade(result)
                    concurrency = 1
                    retry_window = True
                    break
                raise result
            await persist(item, result)
            persisted += 1
        cursor += persisted
        if retry_window:
            continue
        cursor += len(window) - persisted
