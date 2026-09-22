# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Validated HTTP boundary for an independently deployed SemIf sidecar."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Any

import httpx
from pydantic import ValidationError

from .schema import SemIfHealth, SemIfReviewRequest, SemIfReviewResponse


class SemIfError(RuntimeError):
    """Base error for the SemIf boundary."""


class SemIfRequestError(SemIfError):
    """A transport or HTTP error after bounded retries."""


class SemIfContractError(SemIfError):
    """The sidecar returned data outside the frozen contract."""


class SemIfAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 120,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        if not self.base_url:
            raise ValueError("SemIf base URL cannot be empty")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        self._owns_client = client is None
        self._sleep = sleep
        self.last_request_metadata: dict[str, Any] = {}

    async def __aenter__(self) -> "SemIfAdapter":
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
            raise SemIfContractError(f"invalid SemIf health response: {exc}") from exc
        self.last_request_metadata.update(
            {
                "model_name": health.model_name,
                "model_revision": health.model_revision,
            }
        )
        return health

    async def review(self, request: SemIfReviewRequest) -> SemIfReviewResponse:
        payload = await self._request(
            "POST",
            "/v1/reviews",
            json=request.model_dump(mode="json"),
            request_id=request.client_request_id,
        )
        try:
            response = SemIfReviewResponse.model_validate(payload)
        except ValidationError as exc:
            raise SemIfContractError(f"invalid SemIf review response: {exc}") from exc
        expected = [str(item.get("id") or "") for item in request.decisions]
        actual = [item.dimension_id for item in response.decisions]
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise SemIfContractError(
                f"dimension mismatch: expected {sorted(expected)}, got {sorted(actual)}"
            )
        rules = {str(item.get("id") or ""): item for item in request.decisions}
        normalized = []
        for decision in response.decisions:
            rule = rules[decision.dimension_id]
            allowed = {str(item) for item in rule.get("option_ids") or []}
            if decision.selected_option not in allowed:
                raise SemIfContractError(
                    f"unknown option {decision.selected_option!r} for {decision.dimension_id}"
                )
            unknown_probabilities = set(decision.option_probabilities) - allowed
            if unknown_probabilities:
                raise SemIfContractError(
                    f"unknown probability options for {decision.dimension_id}: "
                    f"{sorted(unknown_probabilities)}"
                )
            if decision.scope != request.scope or decision.scope_ref != request.scope_ref:
                raise SemIfContractError(
                    f"scope mismatch for {decision.dimension_id}: "
                    f"expected {request.scope}/{request.scope_ref}"
                )
            normalized.append(
                decision.model_copy(
                    update={"request_id": decision.request_id or response.request_id}
                )
            )
        self.last_request_metadata.update(
            {
                "server_request_id": response.request_id,
                "model_name": str(response.model.get("name") or ""),
                "model_revision": str(response.model.get("revision") or ""),
            }
        )
        return response.model_copy(update={"decisions": normalized})

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        request_id: str = "",
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started_at = time.perf_counter()
            self.last_request_metadata = {
                "path": path,
                "client_request_id": request_id,
                "attempt": attempt + 1,
                "max_attempts": self.max_retries + 1,
            }
            try:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    timeout=self.timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.last_request_metadata["elapsed_ms"] = round(
                    (time.perf_counter() - started_at) * 1000, 3
                )
                self.last_request_metadata["error_type"] = type(exc).__name__
                last_error = exc
                if attempt < self.max_retries:
                    await self._sleep(min(2**attempt, 4))
                    continue
                raise SemIfRequestError(
                    f"SemIf request failed after {attempt + 1} attempts: {type(exc).__name__}"
                ) from exc
            self.last_request_metadata["elapsed_ms"] = round(
                (time.perf_counter() - started_at) * 1000, 3
            )
            self.last_request_metadata["status_code"] = response.status_code
            if response.status_code == 429 or response.status_code >= 500:
                last_error = SemIfRequestError(f"SemIf HTTP {response.status_code}")
                if attempt < self.max_retries:
                    await self._sleep(min(2**attempt, 4))
                    continue
                raise last_error
            if response.status_code >= 400:
                raise SemIfRequestError(f"SemIf HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise SemIfContractError("SemIf response is not valid JSON") from exc
        raise SemIfRequestError("SemIf request failed") from last_error
