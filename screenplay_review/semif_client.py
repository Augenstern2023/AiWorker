# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Async boundary to the separately installed SemIf process."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from .semif_contract import (
    SEMIF_UPSTREAM_COMMIT,
    SemIfHealth,
    SemIfScoreRequest,
    SemIfScoreResponse,
)


class SemIfError(RuntimeError):
    """Base exception for a SemIf request."""


class SemIfTransportError(SemIfError):
    """The sidecar could not be reached after bounded retries."""


class SemIfContractError(SemIfError):
    """The sidecar response did not match the pinned contract."""


class SemIfClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 300,
        max_retries: int = 1,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        if not self.base_url:
            raise ValueError("SemIf sidecar URL cannot be empty")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        self._owns_client = client is None
        self._sleep = sleep

    async def __aenter__(self) -> "SemIfClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> SemIfHealth:
        payload = await self._request("GET", "/health")
        try:
            health = SemIfHealth.model_validate(payload)
        except ValidationError as exc:
            raise SemIfContractError(f"SemIf health response is invalid: {exc}") from exc
        self._assert_commit(health.upstream_commit)
        return health

    async def score(self, request: SemIfScoreRequest) -> SemIfScoreResponse:
        payload = await self._request(
            "POST",
            "/v1/score",
            json=request.model_dump(mode="json"),
        )
        try:
            response = SemIfScoreResponse.model_validate(payload)
        except ValidationError as exc:
            raise SemIfContractError(f"SemIf score response is invalid: {exc}") from exc
        self._assert_commit(response.upstream_commit)
        if response.request_id != request.request_id or response.mode != request.mode:
            raise SemIfContractError("SemIf response does not match its request identity")
        expected = {criterion.id: criterion for criterion in request.criteria}
        actual_ids = [score.id for score in response.scores]
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
            raise SemIfContractError(
                f"SemIf criterion mismatch: expected {sorted(expected)}, got {sorted(actual_ids)}"
            )
        for score in response.scores:
            option_ids = [option.id for option in expected[score.id].options]
            if score.option_ids != option_ids:
                raise SemIfContractError(
                    f"SemIf option mismatch for {score.id}: {score.option_ids}"
                )
        return response

    def _assert_commit(self, commit: str) -> None:
        if commit != SEMIF_UPSTREAM_COMMIT:
            raise SemIfContractError(
                f"SemIf upstream commit mismatch: expected {SEMIF_UPSTREAM_COMMIT}, got {commit}"
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    timeout=self.timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await self._sleep(min(2**attempt, 4))
                    continue
                raise SemIfTransportError(
                    f"SemIf request failed after {attempt + 1} attempts: {type(exc).__name__}"
                ) from exc
            if response.status_code == 429 or response.status_code >= 500:
                last_error = SemIfTransportError(f"SemIf HTTP {response.status_code}")
                if attempt < self.max_retries:
                    await self._sleep(min(2**attempt, 4))
                    continue
                raise last_error
            if response.status_code >= 400:
                detail = response.text[:500]
                raise SemIfTransportError(
                    f"SemIf HTTP {response.status_code}: {detail}"
                )
            try:
                return response.json()
            except ValueError as exc:
                raise SemIfContractError("SemIf response is not valid JSON") from exc
        raise SemIfTransportError("SemIf request failed") from last_error
