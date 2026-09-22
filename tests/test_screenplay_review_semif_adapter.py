# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json

import httpx
import pytest

from screenplay_review.schema import SemIfReviewRequest
from deploy.semif.fake_server import app as fake_semif_app
from screenplay_review.semif_adapter import (
    SemIfAdapter,
    SemIfContractError,
    SemIfRequestError,
)


def _request() -> SemIfReviewRequest:
    return SemIfReviewRequest(
        client_request_id="client-1",
        rule_package="short-drama-semif-v1",
        model_profile="fake",
        scope="project",
        scope_ref="project",
        context_summary={"title": "测试"},
        content_windows=[],
        decisions=[
            {
                "id": "script_completeness",
                "label": "剧本完整性",
                "scope": "project",
                "kind": "hard_gate",
                "weight": 0,
                "option_ids": [
                    "passed",
                    "needs_completion",
                    "severe_missing",
                    "insufficient_evidence",
                ],
            }
        ],
    )


@pytest.mark.asyncio
async def test_adapter_sends_only_the_fixed_contract_and_validates_response() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "schema_version": "semif-review-response-v1",
                "request_id": "server-1",
                "model": {"name": "fake", "revision": "r1"},
                "decisions": [
                    {
                        "dimension_id": "script_completeness",
                        "scope": "project",
                        "scope_ref": "project",
                        "selected_option": "passed",
                        "option_probabilities": {"passed": 1.0},
                        "evidence": [],
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SemIfAdapter("http://127.0.0.1:18766", client=client)
    response = await adapter.review(_request())
    await client.aclose()

    assert response.request_id == "server-1"
    assert set(captured) == {
        "schema_version",
        "client_request_id",
        "rule_package",
        "model_profile",
        "scope",
        "scope_ref",
        "context_summary",
        "content_windows",
        "decisions",
    }
    assert "api_key" not in json.dumps(captured)
    assert "prompt" not in json.dumps(captured)
    assert adapter.last_request_metadata["client_request_id"] == "client-1"
    assert adapter.last_request_metadata["model_revision"] == "r1"
    assert adapter.last_request_metadata["status_code"] == 200
    assert adapter.last_request_metadata["elapsed_ms"] >= 0
    assert "content_windows" not in adapter.last_request_metadata


@pytest.mark.asyncio
async def test_adapter_retries_5xx_but_not_contract_or_4xx_errors() -> None:
    attempts = 0

    def retry_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"detail": "loading"})
        return httpx.Response(
            200,
            json={
                "request_id": "server-2",
                "model": {"name": "fake", "revision": "r1"},
                "decisions": [
                    {
                        "dimension_id": "script_completeness",
                        "scope": "project",
                        "scope_ref": "project",
                        "selected_option": "passed",
                        "option_probabilities": {"passed": 1.0},
                        "evidence": [],
                    }
                ],
            },
        )

    async def no_sleep(_delay: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(retry_handler))
    adapter = SemIfAdapter(
        "http://127.0.0.1:18766",
        client=client,
        max_retries=1,
        sleep=no_sleep,
    )
    await adapter.review(_request())
    assert attempts == 2
    await client.aclose()

    bad_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(400, json={"detail": "bad"}))
    )
    bad_adapter = SemIfAdapter("http://127.0.0.1:18766", client=bad_client, max_retries=2)
    with pytest.raises(SemIfRequestError, match="HTTP 400"):
        await bad_adapter.review(_request())
    await bad_client.aclose()


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_or_missing_dimensions() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "request_id": "server-3",
                    "model": {"name": "fake", "revision": "r1"},
                    "decisions": [
                        {
                            "dimension_id": "invented",
                            "scope": "project",
                            "scope_ref": "project",
                            "selected_option": "passed",
                            "option_probabilities": {"passed": 1.0},
                            "evidence": [],
                        }
                    ],
                },
            )
        )
    )
    adapter = SemIfAdapter("http://127.0.0.1:18766", client=client)
    with pytest.raises(SemIfContractError, match="dimension mismatch"):
        await adapter.review(_request())
    await client.aclose()


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_probability_option() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "request_id": "server-unknown-option",
                    "model": {"name": "fake", "revision": "r1"},
                    "decisions": [
                        {
                            "dimension_id": "script_completeness",
                            "scope": "project",
                            "scope_ref": "project",
                            "selected_option": "passed",
                            "option_probabilities": {
                                "passed": 0.8,
                                "invented": 0.2,
                            },
                            "evidence": [],
                        }
                    ],
                },
            )
        )
    )
    adapter = SemIfAdapter("http://127.0.0.1:18766", client=client)

    with pytest.raises(SemIfContractError, match="unknown probability options"):
        await adapter.review(_request())

    await client.aclose()


@pytest.mark.asyncio
async def test_health_uses_the_stable_endpoint() -> None:
    seen_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.path
        return httpx.Response(
            200,
            json={"status": "ok", "model_name": "fake", "model_revision": "r1"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SemIfAdapter("http://127.0.0.1:18766", client=client)
    health = await adapter.health()
    await client.aclose()

    assert seen_path == "/health"
    assert health.model_revision == "r1"


@pytest.mark.asyncio
async def test_bundled_fake_sidecar_satisfies_the_adapter_contract() -> None:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_semif_app),
        base_url="http://semif.test",
    )
    adapter = SemIfAdapter("http://semif.test", client=client)

    assert (await adapter.health()).status == "ok"
    assert (await adapter.review(_request())).decisions[0].selected_option == "passed"
    await client.aclose()
