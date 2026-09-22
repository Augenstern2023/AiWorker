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
from pydantic import ValidationError

from screenplay_review.schema import EvidenceRef, ScriptPackage, SemIfDecision


def test_script_package_requires_versioned_project_episode_and_scene_refs() -> None:
    package = ScriptPackage.model_validate(
        {
            "schema_version": "script-package-v1",
            "source_fingerprint": "source-fp",
            "project": {"project_id": "drama-001", "title": "测试剧本"},
            "characters": [],
            "relationships": [],
            "episodes": [
                {
                    "episode_id": "ep-0001",
                    "episode_number": 1,
                    "resource_id": "script-1",
                    "version": 1,
                    "content_fingerprint": "content-fp",
                    "scenes": [
                        {
                            "scene_id": "script-1:1:1",
                            "episode_id": "ep-0001",
                            "scene_number": "1",
                            "sequence_number": 1,
                            "resource_id": "script-1",
                            "version": 1,
                            "content_fingerprint": "content-fp",
                            "text": "开场",
                            "evidence": [
                                {
                                    "source_ref": "script-1:1:1:0",
                                    "episode_id": "ep-0001",
                                    "scene_id": "script-1:1:1",
                                    "resource_id": "script-1",
                                    "version": 1,
                                    "block_index": 0,
                                    "start": 0,
                                    "end": 2,
                                    "text": "开场",
                                    "text_hash": "hash",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert package.episodes[0].scenes[0].evidence[0].text == "开场"
    with pytest.raises(ValidationError):
        ScriptPackage.model_validate({"project": {}, "episodes": []})


def test_evidence_offsets_must_form_a_valid_unicode_slice() -> None:
    with pytest.raises(ValidationError, match="end must be greater"):
        EvidenceRef.model_validate(
            {
                "source_ref": "ref",
                "start": 2,
                "end": 1,
                "text": "错",
                "text_hash": "hash",
            }
        )


def test_decision_probabilities_reject_non_finite_values() -> None:
    with pytest.raises(ValidationError, match="finite and non-negative"):
        SemIfDecision.model_validate(
            {
                "dimension_id": "core_logic",
                "scope": "project",
                "scope_ref": "project",
                "selected_option": "passed",
                "option_probabilities": {"passed": float("nan")},
            }
        )
