# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Deterministically aggregate SemIf choices; probabilities are audit data only."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .rules import HARD_GATES, RULE_PACKAGE, RULES_BY_ID, SCORING_DIMENSIONS
from .schema import EvidenceRef, HardGateResult, ReviewDimension, ReviewReport, SemIfDecision


def aggregate_review(
    decisions: list[SemIfDecision],
    *,
    input_snapshot_hash: str,
    model: dict[str, str],
    prompt_hash: str = "",
    review_version: str = "review-v001",
) -> ReviewReport:
    """Apply hard gates, ordinal weights and stable quality-state precedence."""

    grouped: dict[str, list[SemIfDecision]] = defaultdict(list)
    unknown: set[str] = set()
    for decision in decisions:
        rule = RULES_BY_ID.get(decision.dimension_id)
        if rule is None:
            unknown.add(decision.dimension_id)
            continue
        if decision.scope != rule.scope:
            raise ValueError(
                f"scope mismatch for {decision.dimension_id}: "
                f"expected {rule.scope}, got {decision.scope}"
            )
        if decision.selected_option not in rule.option_ids:
            raise ValueError(
                f"unknown option {decision.selected_option!r} for {decision.dimension_id}"
            )
        grouped[decision.dimension_id].append(decision)
    if unknown:
        raise ValueError(f"unknown decisions: {', '.join(sorted(unknown))}")
    missing = [item.id for item in (*HARD_GATES, *SCORING_DIMENSIONS) if not grouped[item.id]]
    if missing:
        raise ValueError(f"missing decisions: {', '.join(missing)}")

    repeated_options: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for decision in decisions:
        repeated_options[
            (decision.dimension_id, decision.scope, decision.scope_ref)
        ].add(decision.selected_option)
    unstable_items = [
        {
            "dimension_id": dimension_id,
            "scope": scope,
            "scope_ref": scope_ref,
            "selected_options": sorted(options),
            "reason_code": "unstable_repeated_decision",
        }
        for (dimension_id, scope, scope_ref), options in sorted(
            repeated_options.items()
        )
        if len(options) > 1
    ]

    failed_gates: list[dict[str, object]] = []
    insufficient_gates: list[dict[str, object]] = []
    for rule in HARD_GATES:
        for decision in grouped[rule.id]:
            item = {
                "dimension_id": rule.id,
                "label": rule.label,
                "scope_ref": decision.scope_ref,
                "selected_option": decision.selected_option,
            }
            if decision.selected_option == "insufficient_evidence":
                insufficient_gates.append(item)
            elif decision.selected_option != "passed":
                failed_gates.append(item)

    dimensions: list[ReviewDimension] = []
    blocking: list[dict[str, object]] = [
        *failed_gates,
        *insufficient_gates,
        *unstable_items,
    ]
    all_evidence: list[EvidenceRef] = []
    for rule in SCORING_DIMENSIONS:
        items = grouped[rule.id]
        sufficient = [item for item in items if item.selected_option != "insufficient_evidence"]
        ordinal = (
            sum(rule.option_scores[item.selected_option] for item in sufficient) / len(sufficient)
            if sufficient
            else 0.0
        )
        weighted = rule.weight * ordinal / 3
        conservative = min(
            items,
            key=lambda item: rule.option_scores.get(item.selected_option, -1),
        ).selected_option
        probabilities = _average_probabilities(items)
        evidence = _unique_evidence(item for decision in items for item in decision.evidence)
        all_evidence.extend(evidence)
        dimensions.append(
            ReviewDimension(
                id=rule.id,
                label=rule.label,
                selected_option=conservative,
                ordinal=round(ordinal, 4),
                weight=rule.weight,
                weighted_score=round(weighted, 4),
                probabilities=probabilities,
                scope_refs=sorted({item.scope_ref for item in items}),
                evidence=evidence,
            )
        )
        if conservative in {"failed", "needs_revision", "insufficient_evidence"}:
            blocking.append(
                {
                    "dimension_id": rule.id,
                    "label": rule.label,
                    "selected_option": conservative,
                    "scope_refs": sorted({item.scope_ref for item in items}),
                }
            )

    overall_score = max(0, min(100, round(sum(item.weighted_score for item in dimensions))))
    insufficient_count = sum(
        1 for item in decisions if item.selected_option == "insufficient_evidence"
    )
    insufficient_ratio = insufficient_count / len(decisions)
    if failed_gates:
        quality_status = "rejected"
    elif unstable_items or insufficient_gates or insufficient_ratio > 0.30:
        quality_status = "manual_review"
    elif overall_score >= 75:
        quality_status = "passed"
    elif overall_score >= 60:
        quality_status = "needs_revision"
    else:
        quality_status = "rejected"
    ai_verdict = "通过" if quality_status == "passed" else "建议修改"
    summary = {
        "passed": f"综合得分 {overall_score}，建议通过。",
        "needs_revision": f"综合得分 {overall_score}，建议修改后复审。",
        "rejected": f"综合得分 {overall_score} 或硬门槛未通过。",
        "manual_review": f"综合得分 {overall_score}，证据不足，需人工复核。",
    }[quality_status]
    return ReviewReport(
        rule_package=RULE_PACKAGE,
        review_version=review_version,
        quality_status=quality_status,
        overall_score=overall_score,
        hard_gate=HardGateResult(
            passed=not failed_gates and not insufficient_gates,
            failed_items=failed_gates,
            insufficient_items=insufficient_gates,
        ),
        dimensions=dimensions,
        blocking_issues=blocking,
        evidence=_unique_evidence(all_evidence),
        model={str(key): str(value) for key, value in model.items()},
        prompt_hash=prompt_hash,
        input_snapshot_hash=input_snapshot_hash,
        semif_request_ids=sorted({item.request_id for item in decisions if item.request_id}),
        semif_decisions=decisions,
        ai_verdict=ai_verdict,
        summary=summary,
        core_findings=[
            {
                "title": str(item.get("label") or item.get("dimension_id") or "审核问题"),
                "severity": "P1" if item.get("selected_option") == "failed" else "P2",
                "rule_id": str(item.get("dimension_id") or ""),
                "category": "semif",
                "evidence": "、".join(str(value) for value in item.get("scope_refs") or [])
                or "证据不足",
                "suggestion": "请结合证据完成修改后复审。",
            }
            for item in blocking[:5]
        ],
    )


def _average_probabilities(items: list[SemIfDecision]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for item in items:
        denominator = sum(item.option_probabilities.values())
        for option, value in item.option_probabilities.items():
            totals[option] += value / denominator
    return {
        option: round(value / len(items), 8)
        for option, value in sorted(totals.items())
    }


def _unique_evidence(items: Iterable[EvidenceRef]) -> list[EvidenceRef]:
    unique: dict[str, EvidenceRef] = {}
    for item in items:
        unique.setdefault(item.source_ref, item)
    return list(unique.values())
