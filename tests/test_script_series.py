# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from analysis.llm import LLMTimeoutError
from creative.script_series import (
    resolve_script_series_chunk_size,
    run_bisecting_script_batch,
    run_ordered_script_windows,
)


def test_script_chunk_size_stays_at_two_even_with_large_output_budget() -> None:
    assert resolve_script_series_chunk_size(SimpleNamespace()) == 2
    assert resolve_script_series_chunk_size(SimpleNamespace(max_output_tokens=32_768)) == 2
    assert resolve_script_series_chunk_size(SimpleNamespace(max_output_tokens=65_536)) == 2


def test_script_batch_read_timeout_splits_singles_concurrently() -> None:
    async def exercise() -> tuple[list[list[int]], list[list[int]], list[str]]:
        calls: list[list[int]] = []
        split_reasons: list[str] = []
        singles_started: set[int] = set()
        both_singles_started = asyncio.Event()
        release_singles = asyncio.Event()

        async def generate(items: list[int]) -> str:
            calls.append(list(items))
            if len(items) == 2:
                try:
                    raise httpx.ReadTimeout("read timed out")
                except httpx.ReadTimeout as exc:
                    raise LLMTimeoutError("模型响应超时") from exc
            singles_started.add(items[0])
            if len(singles_started) == 2:
                both_singles_started.set()
            await release_singles.wait()
            return f"result-{items[0]}"

        async def on_split(payload: dict[str, object]) -> None:
            split_reasons.append(str(payload["split_reason"]))

        task = asyncio.create_task(
            run_bisecting_script_batch(
                [1, 2],
                generate=generate,
                on_split=on_split,
            )
        )
        await asyncio.wait_for(both_singles_started.wait(), timeout=0.5)
        release_singles.set()
        checkpoints = await task
        return calls, [items for items, _result in checkpoints], split_reasons

    calls, checkpoint_items, split_reasons = asyncio.run(exercise())

    assert calls == [[1, 2], [1], [2]]
    assert checkpoint_items == [[1], [2]]
    assert split_reasons == ["read_timeout"]


def test_script_windows_run_two_generations_in_parallel_and_persist_in_order() -> None:
    active = 0
    max_active = 0
    persisted: list[int] = []

    async def generate(item: int) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.02 if item % 2 else 0.005)
            return f"result-{item}"
        finally:
            active -= 1

    async def persist(item: int, result: str) -> None:
        assert result == f"result-{item}"
        persisted.append(item)

    asyncio.run(
        run_ordered_script_windows(
            [1, 2, 3, 4],
            generate=generate,
            persist=persist,
            on_downgrade=lambda _exc: None,
        )
    )

    assert max_active == 2
    assert persisted == [1, 2, 3, 4]


def test_script_windows_persist_ready_prefix_before_slow_peer_finishes() -> None:
    async def exercise() -> list[int]:
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        first_persisted = asyncio.Event()
        third_started = asyncio.Event()
        persisted: list[int] = []

        async def generate(item: int) -> str:
            if item == 2:
                slow_started.set()
                await release_slow.wait()
            elif item == 3:
                third_started.set()
            return f"result-{item}"

        async def persist(item: int, _result: str) -> None:
            persisted.append(item)
            if item == 1:
                first_persisted.set()

        task = asyncio.create_task(
            run_ordered_script_windows(
                [1, 2, 3],
                generate=generate,
                persist=persist,
                on_downgrade=lambda _exc: None,
            )
        )
        await asyncio.wait_for(slow_started.wait(), timeout=0.5)
        try:
            await asyncio.wait_for(first_persisted.wait(), timeout=0.05)
            await asyncio.wait_for(third_started.wait(), timeout=0.05)
        finally:
            release_slow.set()
        await task
        return persisted

    assert asyncio.run(exercise()) == [1, 2, 3]


def test_script_windows_raise_concurrency_after_two_successes() -> None:
    async def exercise() -> int:
        release_initial = asyncio.Event()
        release_promoted = asyncio.Event()
        two_initial_started = asyncio.Event()
        three_promoted_started = asyncio.Event()
        initial_started: set[int] = set()
        promoted_started: set[int] = set()
        active = 0
        max_active = 0

        async def generate(item: int) -> str:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                if item <= 2:
                    initial_started.add(item)
                    if len(initial_started) == 2:
                        two_initial_started.set()
                    await release_initial.wait()
                else:
                    promoted_started.add(item)
                    if len(promoted_started) == 3:
                        three_promoted_started.set()
                    await release_promoted.wait()
                return f"result-{item}"
            finally:
                active -= 1

        async def persist(_item: int, _result: str) -> None:
            return None

        task = asyncio.create_task(
            run_ordered_script_windows(
                [1, 2, 3, 4, 5],
                generate=generate,
                persist=persist,
                on_downgrade=lambda _exc: None,
                initial_concurrency=2,
                max_concurrency=3,
            )
        )
        await asyncio.wait_for(two_initial_started.wait(), timeout=0.5)
        assert not promoted_started
        release_initial.set()
        try:
            await asyncio.wait_for(three_promoted_started.wait(), timeout=0.5)
        finally:
            release_promoted.set()
        await task
        return max_active

    assert asyncio.run(exercise()) == 3


def test_script_windows_downgrade_to_serial_only_after_http_429() -> None:
    calls: list[int] = []
    persisted: list[int] = []
    downgraded: list[str] = []
    first_failure = True
    active_after_rate_limit = 0
    max_active_after_rate_limit = 0

    async def generate(item: int) -> str:
        nonlocal first_failure, active_after_rate_limit, max_active_after_rate_limit
        calls.append(item)
        started_after_rate_limit = not first_failure
        if started_after_rate_limit:
            active_after_rate_limit += 1
            max_active_after_rate_limit = max(
                max_active_after_rate_limit, active_after_rate_limit
            )
        try:
            await asyncio.sleep(0.01)
            if item == 1 and first_failure:
                first_failure = False
                request = httpx.Request(
                    "POST", "https://example.test/v1/responses"
                )
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError(
                    "rate limited", request=request, response=response
                )
            return f"result-{item}"
        finally:
            if started_after_rate_limit:
                active_after_rate_limit -= 1

    async def persist(item: int, _result: str) -> None:
        persisted.append(item)

    asyncio.run(
        run_ordered_script_windows(
            [1, 2, 3, 4, 5],
            generate=generate,
            persist=persist,
            on_downgrade=lambda exc: downgraded.append(str(exc)),
        )
    )

    assert calls == [1, 2, 1, 2, 3, 4, 5]
    assert persisted == [1, 2, 3, 4, 5]
    assert downgraded == ["rate limited"]
    assert max_active_after_rate_limit == 1


def test_script_windows_retry_read_timeout_without_serial_downgrade() -> None:
    calls: list[int] = []
    persisted: list[int] = []
    downgraded: list[str] = []
    first_failure = True

    async def generate(item: int) -> str:
        nonlocal first_failure
        calls.append(item)
        await asyncio.sleep(0)
        if item == 1 and first_failure:
            first_failure = False
            try:
                raise httpx.ReadTimeout("read timed out")
            except httpx.ReadTimeout as exc:
                raise LLMTimeoutError("模型响应超时") from exc
        return f"result-{item}"

    async def persist(item: int, _result: str) -> None:
        persisted.append(item)

    asyncio.run(
        run_ordered_script_windows(
            [1, 2, 3],
            generate=generate,
            persist=persist,
            on_downgrade=lambda exc: downgraded.append(str(exc)),
        )
    )

    assert calls == [1, 2, 1, 3]
    assert persisted == [1, 2, 3]
    assert downgraded == []
