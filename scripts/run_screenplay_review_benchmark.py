# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Run replayable SemIf screenplay-review calibration fixtures."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screenplay_review.aggregator import aggregate_review
from screenplay_review.rules import RULE_PACKAGE, decision_set_for
from screenplay_review.schema import (
    EvidenceRef,
    ScriptPackage,
    SemIfDecision,
    SemIfReviewRequest,
)
from screenplay_review.semif_adapter import SemIfAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the SemIf screenplay review contract")
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--engine", choices=("fake", "semif"), default="fake")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model-profile", default="local-default")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--enforce-gate", action="store_true")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.engine == "semif" and not str(args.base_url).strip():
        raise ValueError("--engine semif requires an explicit --base-url")
    cases = []
    for directory in sorted(item for item in args.fixtures.iterdir() if item.is_dir()):
        package_path = directory / "package.json"
        label_path = directory / "human_label.json"
        response_path = directory / "semif_response.json"
        if not package_path.exists() or not label_path.exists():
            continue
        package = ScriptPackage.model_validate(_read_json(package_path))
        label = _read_json(label_path)
        _validate_label(label, case_id=directory.name)
        if args.engine == "fake":
            if not response_path.exists():
                raise ValueError(f"missing fake response: {response_path}")
            response_spec = _read_json(response_path)
            decisions = _fake_decisions(package, response_spec)
            model = {
                str(key): str(value)
                for key, value in dict(response_spec.get("model") or {}).items()
            }
        else:
            adapter = SemIfAdapter(str(args.base_url))
            try:
                decisions, model = await _live_decisions(
                    package, adapter, model_profile=str(args.model_profile)
                )
            finally:
                await adapter.close()
        report = aggregate_review(
            decisions,
            input_snapshot_hash=package.source_fingerprint,
            model=model or {"name": args.engine, "revision": "unknown"},
        ).model_dump(mode="json")
        replay = aggregate_review(
            [SemIfDecision.model_validate(item.model_dump(mode="json")) for item in decisions],
            input_snapshot_hash=package.source_fingerprint,
            model=model or {"name": args.engine, "revision": "unknown"},
        ).model_dump(mode="json")
        cases.append(
            {
                "case_id": directory.name,
                "human": label,
                "predicted": report,
                "replay_stable": replay == report,
            }
        )
    if not cases:
        raise ValueError("fixture directory contains no benchmark cases")
    return _metrics(cases, engine=args.engine)


def _fake_decisions(
    package: ScriptPackage, spec: dict[str, Any]
) -> list[SemIfDecision]:
    defaults = {
        "hard_gate": str(spec.get("default_gate_option") or "passed"),
        "score": str(spec.get("default_score_option") or "good"),
    }
    overrides = {str(key): str(value) for key, value in dict(spec.get("overrides") or {}).items()}
    result = []
    for scope, scope_ref, evidence in _scope_units(package):
        for rule in decision_set_for(scope):
            selected = overrides.get(f"{scope_ref}:{rule.id}", overrides.get(rule.id, defaults[rule.kind]))
            result.append(
                SemIfDecision(
                    dimension_id=rule.id,
                    scope=scope,
                    scope_ref=scope_ref,
                    selected_option=selected,
                    option_probabilities={selected: 1.0},
                    evidence=[evidence] if evidence is not None else [],
                    reason_code="fixture",
                    request_id=f"fixture-{scope_ref}",
                )
            )
    return result


async def _live_decisions(
    package: ScriptPackage,
    adapter: SemIfAdapter,
    *,
    model_profile: str,
) -> tuple[list[SemIfDecision], dict[str, str]]:
    decisions: list[SemIfDecision] = []
    model: dict[str, str] = {}
    for scope, scope_ref, evidence in _scope_units(package):
        rules = decision_set_for(scope)
        windows = []
        if evidence is not None:
            windows.append(
                {
                    "scene_id": evidence.scene_id,
                    "episode_id": evidence.episode_id,
                    "resource_id": evidence.resource_id,
                    "version": evidence.version,
                    "source_url": evidence.source_url,
                    "start": evidence.start,
                    "end": evidence.end,
                    "text": evidence.text,
                    "text_hash": evidence.text_hash,
                }
            )
        request_id = hashlib.sha256(
            f"benchmark:{package.source_fingerprint}:{scope}:{scope_ref}".encode("utf-8")
        ).hexdigest()[:32]
        response = await adapter.review(
            SemIfReviewRequest(
                client_request_id=f"benchmark-{request_id}",
                rule_package=RULE_PACKAGE,
                model_profile=model_profile,
                scope=scope,
                scope_ref=scope_ref,
                context_summary={"project_id": package.project.project_id},
                content_windows=windows,
                decisions=[item.to_contract() for item in rules],
            )
        )
        decisions.extend(response.decisions)
        model = dict(response.model)
    return decisions, model


def _scope_units(
    package: ScriptPackage,
) -> list[tuple[str, str, EvidenceRef | None]]:
    units: list[tuple[str, str, EvidenceRef | None]] = [("project", "project", None)]
    for episode in package.episodes:
        evidence = next(
            (item for scene in episode.scenes for item in scene.evidence), None
        )
        units.append(("episode", episode.episode_id, evidence))
    for episode in package.episodes:
        for scene in episode.scenes:
            units.append(("scene", scene.scene_id, scene.evidence[0] if scene.evidence else None))
    return units  # type: ignore[return-value]


def _metrics(cases: list[dict[str, Any]], *, engine: str) -> dict[str, Any]:
    total = len(cases)
    exact = sum(
        item["human"].get("quality_status") == item["predicted"].get("quality_status")
        for item in cases
    )
    false_allow = sum(
        item["predicted"].get("quality_status") == "passed"
        and item["human"].get("quality_status") != "passed"
        for item in cases
    )
    false_block = sum(
        item["human"].get("quality_status") == "passed"
        and item["predicted"].get("quality_status") != "passed"
        for item in cases
    )
    dimension_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    evidence_hits = 0
    evidence_total = 0
    for item in cases:
        predicted_dimensions = {
            value["id"]: value for value in item["predicted"].get("dimensions") or []
        }
        for dimension_id, human_option in dict(item["human"].get("dimensions") or {}).items():
            predicted = predicted_dimensions.get(dimension_id, {})
            dimension_pairs[dimension_id].append(
                (str(human_option), str(predicted.get("selected_option") or "missing"))
            )
        expected_scenes = {str(value) for value in item["human"].get("evidence_scene_ids") or []}
        if expected_scenes:
            predicted_scenes = {
                str(evidence.get("scene_id") or "")
                for dimension in predicted_dimensions.values()
                for evidence in dimension.get("evidence") or []
            }
            evidence_hits += len(expected_scenes & predicted_scenes)
            evidence_total += len(expected_scenes)
    macro_f1 = {
        dimension_id: round(_macro_f1(pairs), 6)
        for dimension_id, pairs in sorted(dimension_pairs.items())
    }
    false_allow_rate = false_allow / total
    sample_sufficient = total >= 100
    paired_improvement = _paired_improvement(cases)
    return {
        "schema_version": "screenplay-review-benchmark-v1",
        "engine": engine,
        "case_count": total,
        "quality_status_accuracy": round(exact / total, 6),
        "human_agreement_rate": round(exact / total, 6),
        "false_allow_rate": round(false_allow_rate, 6),
        "false_block_rate": round(false_block / total, 6),
        "dimension_macro_f1": macro_f1,
        "evidence_scene_hit_rate": round(evidence_hits / evidence_total, 6)
        if evidence_total
        else None,
        "replay_stability": round(
            sum(bool(item["replay_stable"]) for item in cases) / total, 6
        ),
        "calibration_sample_sufficient": sample_sufficient,
        "release_gate_passed": bool(sample_sufficient and false_allow_rate < 0.05),
        **paired_improvement,
        "cases": [
            {
                "case_id": item["case_id"],
                "human_quality_status": item["human"].get("quality_status"),
                "predicted_quality_status": item["predicted"].get("quality_status"),
                "replay_stable": item["replay_stable"],
            }
            for item in cases
        ],
    }


def _paired_improvement(cases: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in cases:
        human = item["human"]
        pair_id = str(human.get("pair_id") or "").strip()
        variant = str(human.get("pair_variant") or "").strip()
        if pair_id and variant in {"before", "after"}:
            pairs[pair_id][variant] = item
    complete = [value for value in pairs.values() if {"before", "after"} <= set(value)]
    improved = sum(
        int(pair["after"]["predicted"].get("overall_score") or 0)
        > int(pair["before"]["predicted"].get("overall_score") or 0)
        for pair in complete
    )
    return {
        "paired_case_count": len(complete),
        "paired_improvement_rate": round(improved / len(complete), 6)
        if complete
        else None,
    }


def _macro_f1(pairs: list[tuple[str, str]]) -> float:
    labels = sorted({value for pair in pairs for value in pair})
    scores = []
    for label in labels:
        true_positive = sum(human == label and predicted == label for human, predicted in pairs)
        false_positive = sum(human != label and predicted == label for human, predicted in pairs)
        false_negative = sum(human == label and predicted != label for human, predicted in pairs)
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append((2 * true_positive / denominator) if denominator else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_label(label: dict[str, Any], *, case_id: str) -> None:
    allowed_statuses = {"passed", "needs_revision", "rejected", "manual_review"}
    if str(label.get("quality_status") or "") not in allowed_statuses:
        raise ValueError(f"{case_id}: invalid human quality_status")
    if not isinstance(label.get("dimensions"), dict):
        raise ValueError(f"{case_id}: human dimensions must be an object")
    if not isinstance(label.get("evidence_scene_ids", []), list):
        raise ValueError(f"{case_id}: evidence_scene_ids must be an array")
    pair_id = str(label.get("pair_id") or "").strip()
    pair_variant = str(label.get("pair_variant") or "").strip()
    if bool(pair_id) != bool(pair_variant):
        raise ValueError(f"{case_id}: paired labels require pair_id and pair_variant")
    if pair_variant and pair_variant not in {"before", "after"}:
        raise ValueError(f"{case_id}: pair_variant must be before or after")


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"benchmark cases={result['case_count']} false_allow={result['false_allow_rate']:.2%} "
        f"gate={'passed' if result['release_gate_passed'] else 'blocked'} output={args.output}"
    )
    return 1 if args.enforce_gate and not result["release_gate_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
