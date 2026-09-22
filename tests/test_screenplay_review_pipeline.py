# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any

import pytest

from screenplay_review.normalizer import normalize_script_package
from screenplay_review.pipeline import _episode_windows, run_semif_review
from screenplay_review.schema import SemIfDecision, SemIfReviewResponse


def _source() -> dict[str, Any]:
    return {
        "project_id": 7,
        "project_name": "测试项目",
        "fingerprint": "source-fp",
        "brief": {"core_conflict": "主角夺回公司"},
        "characters": [{"name": "主角", "goal": "夺回公司"}],
        "relationships": [{"from": "主角", "to": "反派", "type": "对手"}],
        "episodes": [
            {
                "episode_number": number,
                "outline": {"summary": f"第 {number} 集冲突"},
                "beats": {"hook": f"第 {number} 集钩子"},
                "script": {
                    "id": 50 + number,
                    "version": 1,
                    "content_fingerprint": f"fp-{number}",
                    "content": {
                        "episode_title": f"第 {number} 集",
                        "scenes": [
                            {
                                "sequence_no": 1,
                                "scene_no": 1,
                                "blocks": [{"type": "action", "text": f"第 {number} 集正文"}],
                            }
                        ],
                    },
                },
            }
            for number in (1, 2)
        ],
    }


class FakeSemIf:
    def __init__(self, *, fail_scope_ref: str = "") -> None:
        self.requests = []
        self.fail_scope_ref = fail_scope_ref

    async def review(self, request):
        self.requests.append(request)
        if request.scope_ref == self.fail_scope_ref:
            raise RuntimeError("sidecar interrupted")
        decisions = []
        for rule in request.decisions:
            option = "passed" if rule["kind"] == "hard_gate" else "good"
            decisions.append(
                SemIfDecision(
                    dimension_id=rule["id"],
                    scope=request.scope,
                    scope_ref=request.scope_ref,
                    selected_option=option,
                    option_probabilities={option: 1.0},
                    evidence=[],
                )
            )
        return SemIfReviewResponse(
            request_id=f"request-{request.scope_ref}",
            model={"name": "fake", "revision": "r1"},
            decisions=decisions,
        )


class FakeWriter:
    async def complete_json(self, _messages):
        return {
            "summary": "结构化评审说明。",
            "revision_plan": [{"priority": "P1", "action": "加强冲突"}],
            "quality_status": "passed",
        }


@pytest.mark.asyncio
async def test_pipeline_runs_project_then_episodes_then_scenes_and_keeps_semif_verdict() -> None:
    semif = FakeSemIf()
    checkpoints = []
    report = await run_semif_review(
        _source(),
        semif=semif,
        writer_provider=FakeWriter(),
        model_profile="fake",
        on_checkpoint=checkpoints.append,
    )

    assert [(item.scope, item.scope_ref) for item in semif.requests] == [
        ("project", "project"),
        ("episode", "ep-0001"),
        ("episode", "ep-0002"),
        ("scene", "51:1:1"),
        ("scene", "52:1:1"),
    ]
    assert report["quality_status"] == "needs_revision"
    assert report["summary"] == "结构化评审说明。"
    assert report["revision_plan"][0]["priority"] == "P1"
    assert checkpoints[-1]["partial"] is True
    assert "第 1 集正文" not in str(checkpoints)
    project_context = semif.requests[0].context_summary
    assert project_context["brief"]["core_conflict"] == "主角夺回公司"
    assert project_context["characters"][0]["goal"] == "夺回公司"
    assert project_context["episode_structure"][0]["outline"]["summary"] == "第 1 集冲突"
    assert project_context["parent_decisions"] == []
    assert any(
        item["scope"] == "project"
        for item in semif.requests[1].context_summary["parent_decisions"]
    )
    assert any(
        item["scope"] == "episode" and item["scope_ref"] == "ep-0001"
        for item in semif.requests[3].context_summary["parent_decisions"]
    )


@pytest.mark.asyncio
async def test_pipeline_resumes_only_missing_scope_from_checkpoint() -> None:
    first = FakeSemIf(fail_scope_ref="ep-0002")
    checkpoints = []
    with pytest.raises(RuntimeError, match="interrupted"):
        await run_semif_review(
            _source(),
            semif=first,
            model_profile="fake",
            on_checkpoint=checkpoints.append,
        )

    resumed = FakeSemIf()
    await run_semif_review(
        _source(),
        semif=resumed,
        model_profile="fake",
        initial_report=checkpoints[-1],
    )

    assert [(item.scope, item.scope_ref) for item in resumed.requests] == [
        ("episode", "ep-0002"),
        ("scene", "51:1:1"),
        ("scene", "52:1:1"),
    ]


@pytest.mark.asyncio
async def test_failed_dimension_rerun_calls_only_that_dimension_in_each_scope() -> None:
    initial = await run_semif_review(
        _source(), semif=FakeSemIf(), model_profile="fake"
    )
    target = next(item for item in initial["dimensions"] if item["id"] == "episode_hook")
    target["selected_option"] = "needs_revision"
    initial["blocking_issues"] = [
        {"dimension_id": "episode_hook", "selected_option": "needs_revision"}
    ]
    rerun = FakeSemIf()

    await run_semif_review(
        _source(),
        semif=rerun,
        model_profile="fake",
        initial_report=initial,
        rerun_scope="failed_dimensions_only",
        review_version="review-v002",
    )

    assert [(item.scope_ref, [rule["id"] for rule in item.decisions]) for item in rerun.requests] == [
        ("ep-0001", ["episode_hook"]),
        ("ep-0002", ["episode_hook"]),
    ]


@pytest.mark.asyncio
async def test_affected_episode_rerun_rechecks_episode_and_its_scenes_only() -> None:
    initial = await run_semif_review(
        _source(), semif=FakeSemIf(), model_profile="fake"
    )
    target = next(item for item in initial["dimensions"] if item["id"] == "conflict_strength")
    target["selected_option"] = "needs_revision"
    target["scope_refs"] = ["ep-0002"]
    initial["blocking_issues"] = [
        {
            "dimension_id": "conflict_strength",
            "selected_option": "needs_revision",
            "scope_refs": ["ep-0002"],
        }
    ]
    rerun = FakeSemIf()

    await run_semif_review(
        _source(),
        semif=rerun,
        model_profile="fake",
        initial_report=initial,
        rerun_scope="affected_episodes",
        review_version="review-v002",
    )

    assert [(item.scope, item.scope_ref) for item in rerun.requests] == [
        ("episode", "ep-0002"),
        ("scene", "52:1:1"),
    ]


@pytest.mark.asyncio
async def test_episode_without_scenes_keeps_explicit_insufficient_scene_decisions() -> None:
    source = _source()
    source["episodes"][1]["script"]["content"]["scenes"] = []

    report = await run_semif_review(
        source,
        semif=FakeSemIf(),
        model_profile="fake",
    )

    missing = [
        item
        for item in report["semif_decisions"]
        if item["scope"] == "scene" and item["scope_ref"] == "ep-0002"
    ]
    assert {item["dimension_id"] for item in missing} == {
        "dialogue_effectiveness",
        "visual_feasibility",
    }
    assert all(item["selected_option"] == "insufficient_evidence" for item in missing)


def test_episode_context_samples_first_middle_and_last_scenes_with_a_hard_size_cap() -> None:
    source = _source()
    source["episodes"] = source["episodes"][:1]
    source["episodes"][0]["script"]["content"]["scenes"] = [
        {
            "sequence_no": number,
            "scene_no": number,
            "blocks": [{"type": "action", "text": f"场次 {number} " + "字" * 2_000}],
        }
        for number in range(1, 31)
    ]
    episode = normalize_script_package(source).episodes[0]

    windows = _episode_windows(episode)

    assert len(windows) == 16
    assert windows[0]["scene_id"] == "51:1:1"
    assert windows[-1]["scene_id"] == "51:1:30"
    assert sum(len(item["text"]) for item in windows) <= 12_000
