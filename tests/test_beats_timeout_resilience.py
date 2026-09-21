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
from creative import phase4_worker


def _read_timeout() -> LLMTimeoutError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    try:
        raise httpx.ReadTimeout("timed out", request=request)
    except httpx.ReadTimeout as exc:
        try:
            raise LLMTimeoutError("模型响应超时（600 秒）") from exc
        except LLMTimeoutError as wrapped:
            return wrapped


def test_generation_context_excludes_the_audit_snapshot() -> None:
    snapshot = {
        "snapshot_id": "audit-only",
        "project": {"generation_meta": {"stages": "AUDIT_ONLY_SENTINEL"}},
    }

    assert phase4_worker._generation_context(
        {
            "source_snapshot_id": "audit-only",
            "generation_instruction": "加强本批悬念",
        },
        snapshot,
    ) == {"generation_instruction": "加强本批悬念"}
    assert phase4_worker._generation_context(
        {"source_snapshot_id": "audit-only"}, snapshot
    ) is None


def test_beats_provider_uses_explicit_stage_effort_and_disables_read_retries(
    monkeypatch,
) -> None:
    provider = SimpleNamespace(
        model="gpt-5.6-luna",
        reasoning_effort="auto",
        retry_read_timeouts=True,
    )
    monkeypatch.setattr(phase4_worker, "create_provider", lambda: provider)
    monkeypatch.setattr(
        phase4_worker.config,
        "CREATIVE_BEATS_REASONING_EFFORT",
        "xhigh",
        raising=False,
    )

    resolved = phase4_worker._create_generation_provider({"action": "beats"})

    assert resolved.reasoning_effort == "xhigh"
    assert resolved.retry_read_timeouts is False


def test_beats_chunk_size_defaults_to_one_and_is_capped_at_two() -> None:
    resolver = getattr(phase4_worker, "_resolve_beats_series_chunk_size")

    assert resolver({}) == 1
    assert resolver({"chunk_size": 2}) == 2
    assert resolver({"chunk_size": 10}) == 2
    assert resolver({"chunk_size": "invalid"}) == 1


def test_two_episode_read_timeout_is_bisected_into_single_requests() -> None:
    calls: list[tuple[int, int]] = []
    splits: list[dict[str, object]] = []

    async def run_range(start: int, end: int) -> None:
        calls.append((start, end))
        if (start, end) == (1, 2):
            raise _read_timeout()

    async def on_split(payload: dict[str, object]) -> None:
        splits.append(payload)

    runner = getattr(phase4_worker, "_run_bisecting_beats_range")
    asyncio.run(runner(1, 2, run_range, on_split))

    assert calls == [(1, 2), (1, 1), (2, 2)]
    assert len(splits) == 1
    assert {
        key: splits[0][key]
        for key in (
            "split_reason",
            "split_start",
            "split_end",
            "left_start",
            "left_end",
            "right_start",
            "right_end",
        )
    } == {
        "split_reason": "read_timeout",
        "split_start": 1,
        "split_end": 2,
        "left_start": 1,
        "left_end": 1,
        "right_start": 2,
        "right_end": 2,
    }


def test_single_episode_read_timeout_is_not_retried_by_the_orchestrator() -> None:
    calls = 0

    async def run_range(_start: int, _end: int) -> None:
        nonlocal calls
        calls += 1
        raise _read_timeout()

    async def on_split(_payload: dict[str, object]) -> None:
        raise AssertionError("single episode must not split")

    runner = getattr(phase4_worker, "_run_bisecting_beats_range")
    try:
        asyncio.run(runner(1, 1, run_range, on_split))
    except LLMTimeoutError:
        pass
    else:
        raise AssertionError("single episode timeout must surface")

    assert calls == 1
