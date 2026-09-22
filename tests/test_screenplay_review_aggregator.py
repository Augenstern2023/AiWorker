# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import pytest

from screenplay_review.aggregator import aggregate_review
from screenplay_review.rules import HARD_GATES, SCORING_DIMENSIONS
from screenplay_review.schema import SemIfDecision


def _decision(
    rule_id: str,
    option: str,
    *,
    scope_ref: str = "project",
    scope: str | None = None,
) -> SemIfDecision:
    return SemIfDecision.model_validate(
        {
            "dimension_id": rule_id,
            "scope": scope or ("project" if scope_ref == "project" else "episode"),
            "scope_ref": scope_ref,
            "selected_option": option,
            "option_probabilities": {option: 4.0, "insufficient_evidence": 1.0},
            "evidence": [],
            "request_id": f"request-{rule_id}-{scope_ref}",
        }
    )


def _complete_decisions(score_option: str = "good") -> list[SemIfDecision]:
    return [
        *[_decision(item.id, "passed", scope=item.scope) for item in HARD_GATES],
        *[
            _decision(
                item.id,
                score_option,
                scope=item.scope,
                scope_ref="project" if item.scope == "project" else f"{item.scope}-1",
            )
            for item in SCORING_DIMENSIONS
        ],
    ]


def test_aggregate_review_normalizes_probabilities_and_projects_legacy_verdict() -> None:
    report = aggregate_review(
        _complete_decisions("good"),
        input_snapshot_hash="snapshot",
        model={"name": "fake", "revision": "r1"},
    )

    assert report.overall_score == 67
    assert report.quality_status == "needs_revision"
    assert report.ai_verdict == "建议修改"
    assert report.dimensions[0].probabilities["good"] == pytest.approx(0.8)


def test_hard_gate_failure_rejects_even_with_high_score() -> None:
    decisions = _complete_decisions("excellent")
    decisions[0] = _decision(HARD_GATES[0].id, "severe_missing")
    report = aggregate_review(
        decisions,
        input_snapshot_hash="snapshot",
        model={"name": "fake", "revision": "r1"},
    )

    assert report.quality_status == "rejected"
    assert not report.hard_gate.passed


def test_too_much_insufficient_evidence_routes_to_manual_review() -> None:
    decisions = _complete_decisions("good")
    for index, item in enumerate(SCORING_DIMENSIONS[:5], start=len(HARD_GATES)):
        decisions[index] = _decision(
            item.id,
            "insufficient_evidence",
            scope=item.scope,
            scope_ref="project" if item.scope == "project" else f"{item.scope}-1",
        )
    report = aggregate_review(
        decisions,
        input_snapshot_hash="snapshot",
        model={"name": "fake", "revision": "r1"},
    )

    assert report.quality_status == "manual_review"


def test_conflicting_repeated_scope_decision_routes_to_manual_review() -> None:
    decisions = _complete_decisions("good")
    decisions.append(_decision("conflict_strength", "failed", scope_ref="ep-0001"))
    decisions.append(
        _decision("conflict_strength", "needs_revision", scope_ref="ep-0001")
    )

    report = aggregate_review(
        decisions,
        input_snapshot_hash="snapshot",
        model={"name": "fake", "revision": "r1"},
    )

    assert report.quality_status == "manual_review"
    assert any(
        item.get("reason_code") == "unstable_repeated_decision"
        for item in report.blocking_issues
    )


def test_missing_dimensions_are_rejected_as_an_invalid_response() -> None:
    with pytest.raises(ValueError, match="missing decisions"):
        aggregate_review(
            _complete_decisions()[:-1],
            input_snapshot_hash="snapshot",
            model={"name": "fake", "revision": "r1"},
        )
