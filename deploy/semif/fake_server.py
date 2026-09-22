# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Deterministic contract-only SemIf server for local integration tests."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import FastAPI

from screenplay_review.schema import (
    EvidenceRef,
    SemIfDecision,
    SemIfHealth,
    SemIfReviewRequest,
    SemIfReviewResponse,
)


app = FastAPI(title="AIWorker Fake SemIf", version="1")


@app.get("/health", response_model=SemIfHealth)
async def health() -> SemIfHealth:
    return SemIfHealth(status="ok", model_name="fake-semif", model_revision="fake-v1")


@app.post("/v1/reviews", response_model=SemIfReviewResponse)
async def review(request: SemIfReviewRequest) -> SemIfReviewResponse:
    evidence = _first_evidence(request)
    decisions = []
    for rule in request.decisions:
        kind = str(rule.get("kind") or "score")
        selected = "passed" if kind == "hard_gate" else "good"
        decisions.append(
            SemIfDecision(
                dimension_id=str(rule["id"]),
                scope=request.scope,
                scope_ref=request.scope_ref,
                selected_option=selected,
                option_probabilities={selected: 0.9, "insufficient_evidence": 0.1},
                evidence=[evidence] if evidence is not None else [],
                reason_code="fake_contract_response",
                request_id=request.client_request_id,
            )
        )
    return SemIfReviewResponse(
        request_id=request.client_request_id,
        model={"name": "fake-semif", "revision": "fake-v1"},
        decisions=decisions,
    )


def _first_evidence(request: SemIfReviewRequest) -> EvidenceRef | None:
    window: dict[str, Any] | None = next(
        (item for item in request.content_windows if str(item.get("text") or "")),
        None,
    )
    if window is None:
        return None
    text = str(window.get("text") or "")[:240]
    if not text:
        return None
    return EvidenceRef(
        source_ref=f"{window.get('scene_id') or request.scope_ref}:window:0",
        episode_id=str(window.get("episode_id") or "") or None,
        scene_id=str(window.get("scene_id") or "") or None,
        resource_id=str(window.get("resource_id") or "") or None,
        version=max(1, int(window.get("version") or 1)),
        source_url=str(window.get("source_url") or ""),
        start=0,
        end=len(text),
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
