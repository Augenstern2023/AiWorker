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
from types import SimpleNamespace

import httpx
import pytest

from screenplay_review.semif_client import SemIfClient, SemIfContractError
from screenplay_review.semif_contract import (
    SEMIF_UPSTREAM_COMMIT,
    SemIfScoreRequest,
)
from screenplay_review.semif_review import review_source_with_semif
from screenplay_review.semif_rules import RULES, criteria
from screenplay_review.semif_state import build_semif_state_windows
from screenplay_review.strategy import estimate_tokens
from screenplay_review.worker import (
    _run_with_repository,
    _screenplay_review_engine,
    _semif_fallback_engine,
)
from screenplay_review import worker


def _source(text: str = "主角发现秘密后决定反击。") -> dict:
    return {
        "fingerprint": "source-fingerprint",
        "project_id": 7,
        "project_name": "SemIf 试点",
        "formal": True,
        "mode": "single",
        "episode_numbers": [1],
        "brief": {"id": 1, "content": {"theme": "成长"}},
        "characters": [{"id": 2, "content": {"name": "主角"}}],
        "relationships": [],
        "episodes": [
            {
                "episode_number": 1,
                "script": {
                    "id": 11,
                    "version": 3,
                    "content_fingerprint": "script-fingerprint",
                    "content": {
                        "episode_title": "第 1 集",
                        "scenes": [
                            {
                                "scene_no": 1,
                                "blocks": [
                                    {
                                        "type": "dialogue",
                                        "speaker": "主角",
                                        "text": text,
                                    }
                                ],
                            }
                        ],
                    },
                },
                "outline": {"id": 21, "content": {"core_event": "主角反击"}},
                "beats": {"id": 31, "content": {"beats": [{"order": 1}]}},
            }
        ],
    }


def test_rule_package_has_explicit_questions_and_described_options() -> None:
    contract = criteria()

    assert len(contract) == 15
    assert len({rule.id for rule in RULES}) == len(RULES)
    assert all(item.question.endswith("？") for item in contract)
    assert all(
        option.description
        for item in contract
        for option in item.options
    )


def test_state_builder_splits_long_block_without_losing_text() -> None:
    original = "甲乙丙丁" * 1_200

    windows = build_semif_state_windows(_source(original), target_tokens=500)

    assert len(windows) > 1
    reconstructed = "".join(
        segment["text"]
        for window in windows
        for segment in window.state["script_segments"]
    )
    assert reconstructed == original
    assert all(window.estimated_tokens <= 500 for window in windows)
    assert all(estimate_tokens(window.state) <= 500 for window in windows)
    refs = [reference for window in windows for reference in window.state_refs]
    assert refs[0]["start"] == 0
    assert refs[-1]["end"] == len(original)
    assert all(left["end"] == right["start"] for left, right in zip(refs, refs[1:]))


def test_state_builder_refuses_oversized_fixed_context_instead_of_truncating() -> None:
    source = _source("短正文")
    source["brief"]["content"]["very_long"] = "背景" * 1_000

    with pytest.raises(ValueError, match="固定上下文已超过"):
        build_semif_state_windows(source, target_tokens=300)


@pytest.mark.asyncio
async def test_client_validates_real_semif_score_shape_and_commit() -> None:
    criterion = criteria()[0]
    request = SemIfScoreRequest(
        request_id="request-1",
        state={"script": "主角反击"},
        criteria=[criterion],
    )

    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url.path == "/v1/score"
        payload = json.loads(http_request.content)
        option_ids = [item["id"] for item in payload["criteria"][0]["options"]]
        return httpx.Response(
            200,
            json={
                "schema_version": "semif-score-v1",
                "request_id": "request-1",
                "mode": "shared",
                "upstream_commit": SEMIF_UPSTREAM_COMMIT,
                "scores": [
                    {
                        "id": criterion.id,
                        "option_ids": option_ids,
                        "probabilities": [0.7, 0.2, 0.05, 0.05],
                        "option_logits": [3.0, 2.0, 1.0, 1.0],
                        "input_tokens": 123,
                        "prompt_sha256": "a" * 64,
                        "prompt_version": "direct-v1",
                        "model": {"revision": "model-revision"},
                        "readout": "native selected suffix-position logits",
                        "probability_status": "conditional option score",
                    }
                ],
                "timing": {"total_seconds": 1.2},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://semif") as http:
        client = SemIfClient("http://semif", client=http)
        response = await client.score(request)

    assert response.scores[0].selected_option_id == "passed"
    assert response.scores[0].probabilities[0] == 0.7


@pytest.mark.asyncio
async def test_client_rejects_unpinned_upstream_commit() -> None:
    criterion = criteria()[0]
    request = SemIfScoreRequest(
        request_id="request-1",
        state="剧本",
        criteria=[criterion],
    )
    response = {
        "schema_version": "semif-score-v1",
        "request_id": "request-1",
        "mode": "shared",
        "upstream_commit": "0" * 40,
        "scores": [
            {
                "id": criterion.id,
                "option_ids": [option.id for option in criterion.options],
                "probabilities": [0.7, 0.2, 0.05, 0.05],
                "option_logits": [3.0, 2.0, 1.0, 1.0],
                "input_tokens": 10,
                "prompt_sha256": "a" * 64,
                "prompt_version": "direct-v1",
                "model": {},
                "readout": "native logits",
                "probability_status": "conditional option score",
            }
        ],
        "timing": {},
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=response))

    async with httpx.AsyncClient(transport=transport, base_url="http://semif") as http:
        with pytest.raises(SemIfContractError, match="commit mismatch"):
            await SemIfClient("http://semif", client=http).score(request)


@pytest.mark.asyncio
async def test_semif_review_keeps_raw_scores_and_requires_manual_review() -> None:
    class Client:
        async def score(self, request):
            scores = []
            for index, criterion in enumerate(request.criteria):
                option_ids = [option.id for option in criterion.options]
                selected = 2 if index == 0 else 0
                probabilities = [0.05] * len(option_ids)
                probabilities[selected] = 0.8
                scores.append(
                    {
                        "id": criterion.id,
                        "option_ids": option_ids,
                        "probabilities": probabilities,
                        "option_logits": [float(value) for value in range(len(option_ids))],
                        "input_tokens": 200,
                        "prompt_sha256": f"{index:064x}",
                        "prompt_version": "semif-direct-v1",
                        "model": {"revision": "pinned-model"},
                        "readout": "native logits",
                        "probability_status": "conditional option score",
                    }
                )
            from screenplay_review.semif_contract import SemIfScoreResponse

            return SemIfScoreResponse.model_validate(
                {
                    "schema_version": "semif-score-v1",
                    "request_id": request.request_id,
                    "mode": request.mode,
                    "upstream_commit": SEMIF_UPSTREAM_COMMIT,
                    "scores": scores,
                    "timing": {"total_seconds": 0.5},
                }
            )

    checkpoints = []
    report = await review_source_with_semif(
        Client(),
        _source(),
        on_checkpoint=lambda item: checkpoints.append(item),
    )

    assert checkpoints[-1]["partial"] is True
    assert report["engine"] == "semif"
    assert report["quality_status"] == "manual_review"
    assert report["manual_review_required"] is True
    assert report["ai_verdict"] == "建议修改"
    assert report["strategy"]["source_was_truncated"] is False
    assert report["semif"]["states"][0]["state_refs"]
    decision = report["semif"]["decisions"][0]
    assert decision["selected_option_id"] == "failed"
    assert decision["observations"][0]["probabilities"]
    assert report["core_findings"][0]["evidence"] == ""


def test_worker_feature_flags_keep_legacy_default(monkeypatch) -> None:
    monkeypatch.delenv("SCREENPLAY_REVIEW_ENGINE", raising=False)
    monkeypatch.delenv("SCREENPLAY_REVIEW_SEMIF_FALLBACK", raising=False)

    assert _screenplay_review_engine() == "legacy"
    assert _semif_fallback_engine() == "legacy"


def test_task_review_method_overrides_process_default(monkeypatch) -> None:
    monkeypatch.setenv("SCREENPLAY_REVIEW_ENGINE", "legacy")
    local_review = {"source_payload": {"review_method": "local"}}
    assert _screenplay_review_engine(local_review) == "semif"
    assert _semif_fallback_engine(local_review) == "none"

    monkeypatch.setenv("SCREENPLAY_REVIEW_ENGINE", "semif")
    complete_review = {"source_payload": {"review_method": "complete"}}
    assert _screenplay_review_engine(complete_review) == "legacy"


def test_worker_rejects_unknown_engine(monkeypatch) -> None:
    monkeypatch.setenv("SCREENPLAY_REVIEW_ENGINE", "unknown")

    with pytest.raises(ValueError, match="legacy or semif"):
        _screenplay_review_engine()


@pytest.mark.asyncio
async def test_worker_uses_semif_inside_existing_review_lifecycle(
    tmp_path, monkeypatch
) -> None:
    source = _source()
    review = {
        "id": 9,
        "mode": "single",
        "source_payload": source,
        "report": {},
    }

    class Repository:
        completed = None

        def get_script_review(self, review_id):
            assert review_id == 9
            return review

        def start_script_review(self, review_id):
            assert review_id == 9

        def checkpoint_script_review(self, _review_id, _report):
            pass

        def complete_script_review(self, review_id, report):
            self.completed = report
            return {"id": review_id, "source_fingerprint": source["fingerprint"]}

        def fail_script_review(self, _review_id, error):
            raise AssertionError(error)

    async def fake_semif(*_args, **_kwargs):
        return {
            "ai_verdict": "建议修改",
            "engine": "semif",
            "summary": "人工复核",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        }

    async def reject_legacy(*_args, **_kwargs):
        raise AssertionError("Legacy should not run")

    repository = Repository()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCREENPLAY_REVIEW_ENGINE", "semif")
    monkeypatch.setattr(worker, "_run_semif_review", fake_semif)
    monkeypatch.setattr(worker, "_run_legacy_review", reject_legacy)
    monkeypatch.setattr(worker, "emit", lambda *_args, **_kwargs: None)

    await _run_with_repository(
        SimpleNamespace(job_id="semif-job", extra={"review_id": 9}),
        repository,
    )

    assert repository.completed["engine"] == "semif"
    assert (tmp_path / "data" / "script_reviews" / "semif-job" / "report.json").exists()


@pytest.mark.asyncio
async def test_worker_falls_back_to_legacy_when_sidecar_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    source = _source()
    review = {
        "id": 10,
        "mode": "single",
        "source_payload": source,
        "report": {},
    }

    class Repository:
        completed = None

        def get_script_review(self, _review_id):
            return review

        def start_script_review(self, _review_id):
            pass

        def checkpoint_script_review(self, _review_id, _report):
            pass

        def complete_script_review(self, review_id, report):
            self.completed = report
            return {"id": review_id, "source_fingerprint": source["fingerprint"]}

        def fail_script_review(self, _review_id, error):
            raise AssertionError(error)

    async def broken_semif(*_args, **_kwargs):
        raise RuntimeError("sidecar unavailable")

    async def fake_legacy(*_args, **_kwargs):
        return {
            "ai_verdict": "通过",
            "summary": "Legacy fallback",
            "core_findings": [],
            "episode_reviews": [],
            "cross_episode_findings": [],
        }

    events = []
    repository = Repository()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCREENPLAY_REVIEW_ENGINE", "semif")
    monkeypatch.setenv("SCREENPLAY_REVIEW_SEMIF_FALLBACK", "legacy")
    monkeypatch.setattr(worker, "_run_semif_review", broken_semif)
    monkeypatch.setattr(worker, "_run_legacy_review", fake_legacy)
    monkeypatch.setattr(
        worker,
        "emit",
        lambda _job, event, message, *args, **kwargs: events.append((event, message)),
    )

    await _run_with_repository(
        SimpleNamespace(job_id="fallback-job", extra={"review_id": 10}),
        repository,
    )

    assert repository.completed["summary"] == "Legacy fallback"
    assert any("切换完整审核" in message for _event, message in events)
