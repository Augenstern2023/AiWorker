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

import httpx

from analysis.llm import (
    LLMOutputTruncatedError,
    LLMTimeoutError,
    is_retryable_llm_exception,
)


SCRIPT_SERIES_DEFAULT_CHUNK_SIZE = 2
SCRIPT_SERIES_INITIAL_CONCURRENCY = 2
SCRIPT_SERIES_MAX_CONCURRENCY = 3
SCRIPT_SERIES_SUCCESS_WINDOW_FOR_INCREASE = 2

ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        chain.append(current)
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def is_http_rate_limit_exception(exc: BaseException) -> bool:
    """Return whether an exception chain contains an explicit HTTP 429."""

    for current in _exception_chain(exc):
        if (
            isinstance(current, httpx.HTTPStatusError)
            and int(current.response.status_code) == 429
        ):
            return True
    return False


def is_read_timeout_exception(exc: BaseException) -> bool:
    """Return whether an exception chain contains an HTTP read timeout."""

    return any(isinstance(item, httpx.ReadTimeout) for item in _exception_chain(exc))


def script_series_split_reason(exc: BaseException) -> str | None:
    """Classify failures that become safer after bisecting a script batch."""

    chain = _exception_chain(exc)
    if any(isinstance(item, LLMOutputTruncatedError) for item in chain):
        return "output_truncated"
    if any(isinstance(item, LLMTimeoutError) for item in chain) and any(
        isinstance(item, httpx.ReadTimeout) for item in chain
    ):
        return "read_timeout"
    return None


async def run_bisecting_script_batch(
    items: Sequence[ItemT],
    *,
    generate: Callable[[list[ItemT]], Awaitable[ResultT]],
    on_split: Callable[[dict[str, object]], Awaitable[None]],
    parallel_splits: Callable[[], bool] | None = None,
) -> list[tuple[list[ItemT], ResultT]]:
    """Bisect oversized or timed-out batches and preserve ordered results."""

    batch = list(items)
    try:
        result = await generate(batch)
        return [(batch, result)]
    except Exception as exc:
        split_reason = script_series_split_reason(exc)
        if split_reason is None or len(batch) <= 1:
            raise
        midpoint = len(batch) // 2
        left = batch[:midpoint]
        right = batch[midpoint:]
        payload: dict[str, object] = {
            "split_reason": split_reason,
            "split_items": batch,
            "left_items": left,
            "right_items": right,
            "exception_type": type(exc).__name__,
            "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else "",
        }
        truncated = next(
            (
                item
                for item in _exception_chain(exc)
                if isinstance(item, LLMOutputTruncatedError)
            ),
            None,
        )
        if truncated is not None:
            payload.update(
                finish_reason=truncated.finish_reason,
                returned_chars=truncated.returned_chars,
            )
        await on_split(payload)

        run_parallel = parallel_splits is None or parallel_splits()
        if not run_parallel:
            return [
                *await run_bisecting_script_batch(
                    left,
                    generate=generate,
                    on_split=on_split,
                    parallel_splits=parallel_splits,
                ),
                *await run_bisecting_script_batch(
                    right,
                    generate=generate,
                    on_split=on_split,
                    parallel_splits=parallel_splits,
                ),
            ]

        left_task = asyncio.create_task(
            run_bisecting_script_batch(
                left,
                generate=generate,
                on_split=on_split,
                parallel_splits=parallel_splits,
            )
        )
        right_task = asyncio.create_task(
            run_bisecting_script_batch(
                right,
                generate=generate,
                on_split=on_split,
                parallel_splits=parallel_splits,
            )
        )
        try:
            left_results, right_results = await asyncio.gather(
                left_task, right_task
            )
        except BaseException:
            left_task.cancel()
            right_task.cancel()
            await asyncio.gather(left_task, right_task, return_exceptions=True)
            raise
        return [*left_results, *right_results]


def resolve_script_series_chunk_size(provider: Any) -> int:
    """Keep screenplay requests small enough to recover reliably."""

    del provider
    return SCRIPT_SERIES_DEFAULT_CHUNK_SIZE


async def run_ordered_script_windows(
    items: Sequence[ItemT],
    *,
    generate: Callable[[ItemT], Awaitable[ResultT]],
    persist: Callable[[ItemT, ResultT], Awaitable[None]],
    on_downgrade: Callable[[Exception], None],
    initial_concurrency: int = SCRIPT_SERIES_INITIAL_CONCURRENCY,
    max_concurrency: int = SCRIPT_SERIES_MAX_CONCURRENCY,
) -> None:
    """Generate concurrently while persisting each ready ordered prefix."""

    concurrency_limit = max(
        1, min(int(max_concurrency), SCRIPT_SERIES_MAX_CONCURRENCY)
    )
    concurrency = max(1, min(int(initial_concurrency), concurrency_limit))
    successful_persists = 0
    next_to_schedule = 0
    next_to_persist = 0
    tasks: dict[int, asyncio.Task[ResultT]] = {}
    outcomes: dict[int, ResultT | BaseException] = {}
    retry_attempts: dict[int, int] = {}

    def schedule_available() -> None:
        nonlocal next_to_schedule
        while next_to_schedule < len(items) and len(tasks) < concurrency:
            index = next_to_schedule
            tasks[index] = asyncio.create_task(generate(items[index]))
            next_to_schedule += 1

    async def cancel_pending() -> None:
        if not tasks:
            return
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        tasks.clear()

    schedule_available()
    try:
        while next_to_persist < len(items):
            if tasks:
                done, _pending = await asyncio.wait(
                    tasks.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for index, task in list(tasks.items()):
                    if task not in done:
                        continue
                    tasks.pop(index)
                    try:
                        outcomes[index] = task.result()
                    except BaseException as exc:
                        outcomes[index] = exc

            while next_to_persist in outcomes:
                result = outcomes.pop(next_to_persist)
                if isinstance(result, BaseException):
                    if not isinstance(result, Exception):
                        raise result
                    successful_persists = 0
                    if concurrency > 1 and is_http_rate_limit_exception(result):
                        on_downgrade(result)
                        concurrency = 1
                        concurrency_limit = 1
                        retry_attempts[next_to_persist] = (
                            retry_attempts.get(next_to_persist, 0) + 1
                        )
                        await cancel_pending()
                        outcomes.clear()
                        next_to_schedule = next_to_persist
                        break
                    if (
                        is_retryable_llm_exception(result)
                        and retry_attempts.get(next_to_persist, 0) < 1
                    ):
                        retry_attempts[next_to_persist] = 1
                        tasks[next_to_persist] = asyncio.create_task(
                            generate(items[next_to_persist])
                        )
                        break
                    raise result
                await persist(items[next_to_persist], result)
                retry_attempts.pop(next_to_persist, None)
                next_to_persist += 1
                successful_persists += 1
                if (
                    concurrency < concurrency_limit
                    and successful_persists
                    >= SCRIPT_SERIES_SUCCESS_WINDOW_FOR_INCREASE
                ):
                    concurrency += 1
                    successful_persists = 0
                schedule_available()
            schedule_available()
    finally:
        await cancel_pending()
