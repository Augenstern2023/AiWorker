# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from writer_agent.schema import CreativePlan, PlanStep


def test_creative_plan_keeps_dependency_and_authority_contract() -> None:
    """Would catch a serialized plan that loses its execution ordering contract."""
    plan = CreativePlan(
        project_id=17,
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-1",
        assumptions=[],
        steps=[
            PlanStep(
                step_key="beats.lock:1", stage="beats", kind="lock",
                episode_number=1, depends_on=["beats.generate:1"],
                expected_context={"revision": 3},
            ),
        ],
    )

    restored = CreativePlan.model_validate(plan.model_dump(mode="json"))

    assert restored.steps[0].depends_on == ["beats.generate:1"]
    assert restored.steps[0].expected_context == {"revision": 3}
