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

from analysis.llm import LLMTimeoutError
from creative.script_series import (
    resolve_script_series_chunk_size,
    run_ordered_script_windows,
)


def test_script_chunk_size_adapts_to_model_output_budget() -> None:
    assert resolve_script_series_chunk_size(SimpleNamespace()) == 2
    assert resolve_script_series_chunk_size(SimpleNamespace(max_output_tokens=32_768)) == 2
    assert resolve_script_series_chunk_size(SimpleNamespace(max_output_tokens=65_536)) == 3


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


def test_script_windows_downgrade_to_serial_after_retryable_failure() -> None:
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
            raise LLMTimeoutError("模拟模型超时")
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

    assert calls == [1, 2, 1, 2, 3]
    assert persisted == [1, 2, 3]
    assert downgraded == ["模拟模型超时"]
