# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from screenplay_review.normalizer import normalize_script_package


def _formal_source() -> dict:
    return {
        "project_id": 7,
        "project_name": "逆袭",
        "fingerprint": "source-fp",
        "brief": {"core_conflict": "林一反击"},
        "characters": [{"id": 1, "content": {"name": "林一"}}],
        "relationships": [],
        "episodes": [
            {
                "episode_number": 1,
                "resource_id": 51,
                "version": 2,
                "script": {
                    "id": 51,
                    "version": 2,
                    "content_fingerprint": "script-fp",
                    "content": {
                        "episode_title": "反击",
                        "scenes": [
                            {
                                "sequence_no": 1,
                                "scene_no": 1,
                                "blocks": [
                                    {"type": "action", "text": "林一推门而入。"},
                                    {"type": "dialogue", "speaker": "林一", "text": "轮到我了。"},
                                ],
                            }
                        ],
                    },
                },
            }
        ],
    }


def test_normalizer_reads_structured_scene_content_and_unicode_offsets() -> None:
    package = normalize_script_package(_formal_source())
    scene = package.episodes[0].scenes[0]

    assert scene.scene_id == "51:2:1"
    assert scene.text == "林一推门而入。\n林一：轮到我了。"
    assert [item.text for item in scene.evidence] == ["林一推门而入。", "林一：轮到我了。"]
    assert all(scene.text[item.start : item.end] == item.text for item in scene.evidence)
    assert package.brief["core_conflict"] == "林一反击"


def test_normalizer_is_deterministic_and_does_not_mutate_source() -> None:
    source = _formal_source()
    first = normalize_script_package(source).model_dump(mode="json")
    second = normalize_script_package(source).model_dump(mode="json")

    assert first == second
    assert "scene_id" not in source["episodes"][0]["script"]["content"]["scenes"][0]


def test_text_source_becomes_one_diagnostic_scene() -> None:
    package = normalize_script_package(
        {
            "fingerprint": "text-fp",
            "source_filename": "demo.fountain",
            "episodes": [{"episode_number": 1, "text": "内景 客厅 日\n主角进门。"}],
        }
    )

    assert package.project.project_id == "text:text-fp"
    assert package.episodes[0].scenes[0].scene_id == "text:text-fp:ep-0001:1:1"
    assert not package.insufficient_evidence


def test_empty_episode_is_kept_and_marked_insufficient() -> None:
    package = normalize_script_package(
        {
            "fingerprint": "empty-fp",
            "episodes": [{"episode_number": 3, "resource_id": 9, "version": 1, "text": ""}],
        }
    )

    assert package.episodes[0].episode_number == 3
    assert package.episodes[0].scenes == []
    assert package.insufficient_evidence[0].scope_ref == "ep-0003"


def test_structured_scene_without_text_is_kept_and_marked_insufficient() -> None:
    source = _formal_source()
    source["episodes"][0]["script"]["content"]["scenes"][0]["blocks"] = []

    package = normalize_script_package(source)

    assert package.episodes[0].scenes[0].scene_id == "51:2:1"
    assert {(item.scope, item.scope_ref) for item in package.insufficient_evidence} == {
        ("episode", "ep-0001"),
        ("scene", "51:2:1"),
    }
