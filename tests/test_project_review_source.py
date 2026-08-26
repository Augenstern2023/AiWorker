# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from project_review.source import (
    batch_size_for,
    build_batches,
    build_project_review_source,
    project_review_fingerprint,
)


def _project() -> dict:
    project = {
        "id": 17,
        "name": "长审核测试项目",
        "revision": 4,
        "target_episode_count": 200,
        "locked_brief_id": 12,
        "character_batch_id": "characters-v2",
        "outline_batch_id": "outlines-v2",
        "blueprints": [
            {"id": 11, "version": 1, "status": "historical", "content": {"title": "旧 Brief"}},
            {"id": 12, "version": 2, "status": "locked", "content": {"title": "锁定 Brief"}},
            {"id": 13, "version": 3, "status": "returned", "content": {"title": "退回 Brief"}},
        ],
        "characters": [
            {"id": 21, "batch_id": "characters-v1", "version": 9, "status": "historical", "content": {"name": "旧主角"}},
            {"id": 22, "batch_id": "characters-v2", "version": 2, "status": "locked", "content": {"name": "锁定主角"}},
            {"id": 23, "batch_id": "characters-v3", "version": 3, "status": "draft", "content": {"name": "草稿主角"}},
        ],
        "relationships": [
            {"id": 31, "batch_id": "characters-v2", "version": 2, "status": "locked", "content": {"from": "锁定主角", "to": "反派"}},
            {"id": 32, "batch_id": "characters-v3", "version": 3, "status": "draft", "content": {"from": "草稿主角", "to": "反派"}},
        ],
        "episodes": [
            {"id": 41, "stage": "outline", "batch_id": "outlines-v1", "episode_number": 1, "version": 1, "status": "historical", "content": {"core_event": "历史事件"}},
            {"id": 42, "stage": "outline", "batch_id": "outlines-v2", "episode_number": 1, "version": 2, "status": "locked", "content": {"core_event": "锁定事件"}},
            {"id": 43, "stage": "outline", "batch_id": "outlines-v3", "episode_number": 1, "version": 3, "status": "draft", "content": {"core_event": "草稿事件"}},
            {"id": 44, "stage": "outline", "batch_id": "outlines-v3", "episode_number": 2, "version": 3, "status": "draft", "content": {"core_event": "第二集事件"}},
            {"id": 51, "stage": "beats", "episode_number": 1, "version": 1, "status": "locked", "content": {"beats": ["旧节拍"]}},
            {"id": 52, "stage": "beats", "episode_number": 1, "version": 2, "status": "draft", "content": {"beats": ["新节拍"]}},
            {
                "id": 61,
                "stage": "script",
                "episode_number": 1,
                "version": 1,
                "status": "draft",
                "content": {"scenes": [{"text": "第一集剧本"}]},
                "upstream_refs": {"beats_id": 51, "beats_version": 1},
            },
        ],
    }
    brief = project["blueprints"][-1]
    characters = project["characters"][-1:]
    relationships = project["relationships"][-1:]
    base_refs = {
        "brief_id": brief["id"],
        "brief_version": brief["version"],
        "brief_content_fingerprint": _fingerprint(brief["content"]),
        "character_batch_id": "characters-v3",
        "character_content_fingerprint": _fingerprint({
            "characters": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in characters
            ],
            "relationships": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in relationships
            ],
        }),
    }
    outline_by_number = {item["episode_number"]: item for item in project["episodes"] if item["stage"] == "outline"}
    current_outline = outline_by_number[1]
    current_outline_refs = {
        **base_refs,
        "outline_batch_id": "outlines-v3",
    }
    current_outline["upstream_refs"] = current_outline_refs
    outline_by_number[2]["upstream_refs"] = current_outline_refs
    current_beats = project["episodes"][-2]
    current_beats["upstream_refs"] = {
        **base_refs,
        "outline_batch_id": "outlines-v3",
        "outline_id": current_outline["id"],
        "outline_version": current_outline["version"],
        "outline_content_fingerprint": _fingerprint(current_outline["content"]),
    }
    project["episodes"][-1]["upstream_refs"] = {
        **base_refs,
        "outline_batch_id": "outlines-v3",
        "outline_id": current_outline["id"],
        "outline_version": current_outline["version"],
        "outline_content_fingerprint": _fingerprint(current_outline["content"]),
        "beats_id": 51,
        "beats_version": 1,
        "beats_content_fingerprint": _fingerprint(project["episodes"][-3]["content"]),
    }
    return project


def _fingerprint(content: object) -> str:
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_dynamic_batch_policy_uses_actual_stage_unit_count() -> None:
    assert [batch_size_for(total) for total in (1, 9, 10, 200, 201, 205)] == [1, 1, 10, 10, 20, 20]
    assert build_batches([*range(1, 202)])[-1] == [201]
    assert build_batches([*range(1, 206)])[-1] == [201, 202, 203, 204, 205]


def test_source_uses_current_resources_and_actual_stage_ranges() -> None:
    source = build_project_review_source(_project(), audit_scope="project")

    assert source["audit_scope"] == "project"
    assert source["brief"]["version"] == 3
    assert source["characters"]["batch_id"] == "characters-v3"
    assert source["stages"]["outline"]["episode_numbers"] == [1, 2]
    assert source["stages"]["outline"]["units"][0]["resource"]["version"] == 3
    assert source["stages"]["script"]["episode_numbers"] == [1]
    assert source["manifest"]["outline_count"] == 2
    assert source["manifest"]["script_count"] == 1
    assert source["manifest"]["missing_episode_numbers"] == []
    assert source["upstream_mismatches"][0]["episode_number"] == 1
    assert source["upstream_mismatches"][0]["stage_kind"] == "script"
    assert source["stages"]["outline"]["batches"] == [[1], [2]]
    assert source["stages"]["script"]["batches"] == [[1]]


def test_scope_and_batch_counts_only_include_real_work_units() -> None:
    project = _project()

    outline = build_project_review_source(project, audit_scope="outline")
    script = build_project_review_source(project, audit_scope="script")

    assert set(outline["stages"]) == {"outline"}
    assert set(script["stages"]) == {"script"}
    assert script["stages"]["script"]["batches"] == [[1]]
    assert script["manifest"]["outline_count"] == 1
    assert script["manifest"]["script_count"] == 1
    assert script["manifest"]["project"]["target_episode_count"] == 200


def test_scope_manifest_and_diagnostics_only_include_loaded_resources() -> None:
    outline = build_project_review_source(_project(), audit_scope="outline")
    script = build_project_review_source(_project(), audit_scope="script")

    assert outline["manifest"]["script_count"] == 0
    assert outline["manifest"]["context"]["beats"] == []
    assert all(item["stage_kind"] == "outline" for item in outline["upstream_mismatches"])
    assert script["manifest"]["outline_episode_numbers"] == [1]
    assert [item["episode_number"] for item in script["manifest"]["context"]["beats"]] == [1]


def test_manifest_limits_summaries_and_reports_empty_and_missing_outlines() -> None:
    project = _project()
    project["episodes"] = [
        {"id": 71, "stage": "outline", "batch_id": "outlines-v4", "episode_number": 1, "version": 4, "status": "draft", "content": {}},
        {"id": 73, "stage": "outline", "batch_id": "outlines-v4", "episode_number": 3, "version": 4, "status": "draft", "content": {"core_event": "X" * 300}},
    ]

    source = build_project_review_source(project, audit_scope="outline")

    assert source["manifest"]["missing_episode_numbers"] == [2]
    assert source["unreadable_resources"][0]["episode_number"] == 1
    summaries = [
        unit["resource"]["summary"]
        for unit in source["manifest"]["stages"]["outline"]["units"]
    ]
    assert all(len(summary) <= 200 for summary in summaries)
    assert "content" not in source["manifest"]["stages"]["outline"]["units"][0]["resource"]


def test_manifest_does_not_expand_a_single_actual_outline_to_target_count() -> None:
    project = _project()
    project["episodes"] = [
        {"id": 81, "stage": "outline", "batch_id": "outlines-v5", "episode_number": 7, "version": 5, "status": "draft", "content": {"core_event": "唯一一集"}},
    ]

    source = build_project_review_source(project, audit_scope="outline")

    assert source["manifest"]["outline_episode_numbers"] == [7]
    assert source["manifest"]["missing_episode_numbers"] == []


def test_active_batch_selection_ranks_active_resources_before_locked_versions() -> None:
    project = _project()
    project["episodes"] = [
        {"id": 91, "stage": "outline", "batch_id": "outlines-v9", "episode_number": 1, "version": 9, "status": "locked", "content": {"core_event": "旧锁定"}},
        {"id": 92, "stage": "outline", "batch_id": "outlines-v9", "episode_number": 1, "version": 1, "status": "returned", "content": {"core_event": "旧活动"}},
        {"id": 93, "stage": "outline", "batch_id": "outlines-v10", "episode_number": 1, "version": 2, "status": "draft", "content": {"core_event": "最新活动"}},
    ]

    source = build_project_review_source(project, audit_scope="outline")

    assert source["outline_batch_id"] == "outlines-v10"


def test_upstream_mismatches_detect_missing_refs_and_accept_outline_authority_hash() -> None:
    project = _project()
    project["episodes"][2]["content"]["series_overview"] = {"theme": "仅概览变化"}
    del project["episodes"][-1]["upstream_refs"]["brief_version"]

    source = build_project_review_source(project, audit_scope="project")

    script_mismatch = next(item for item in source["upstream_mismatches"] if item["stage_kind"] == "script")
    fields = {item["field"] for item in script_mismatch["mismatches"]}
    assert "brief_version" in fields
    assert not any(
        item["stage_kind"] == "beats" and "outline_content_fingerprint" in {
            mismatch["field"] for mismatch in item["mismatches"]
        }
        for item in source["upstream_mismatches"]
    )


def test_beats_manifest_source_url_uses_the_real_episode_route() -> None:
    source = build_project_review_source(_project(), audit_scope="script")

    assert source["manifest"]["context"]["beats"][0]["source_url"] == "/writing/projects/17/episodes/1/beats"


def test_outline_generation_refs_only_require_the_fields_the_repository_persists() -> None:
    project = _project()
    for item in project["episodes"]:
        if item["stage"] == "outline":
            item["upstream_refs"] = {
                "brief_id": 13,
                "character_batch_id": "characters-v3",
            }

    source = build_project_review_source(project, audit_scope="outline")

    assert source["upstream_mismatches"] == []


def test_script_keeps_work_unit_and_marks_missing_current_outline_and_beats() -> None:
    project = _project()
    project["episodes"] = [
        item for item in project["episodes"]
        if not (
            item["episode_number"] == 1
            and item["stage"] in {"outline", "beats"}
        )
    ]

    source = build_project_review_source(project, audit_scope="project")

    script_unit = source["stages"]["script"]["units"][0]
    mismatch = next(item for item in source["upstream_mismatches"] if item["stage_kind"] == "script")
    fields = {item["field"] for item in mismatch["mismatches"]}
    assert script_unit["outline"] is None
    assert script_unit["beats"] is None
    assert {"missing_current_outline", "missing_current_beats"} <= fields


def test_source_fingerprint_is_deterministic_and_covers_resource_content() -> None:
    first = build_project_review_source(_project(), audit_scope="project")
    second = build_project_review_source(_project(), audit_scope="project")
    changed_project = _project()
    changed_project["episodes"][-1]["content"]["scenes"][0]["text"] = "修改后的剧本"
    changed = build_project_review_source(changed_project, audit_scope="project")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"] == project_review_fingerprint(first)
    assert first["fingerprint"] != changed["fingerprint"]
    recomputed = deepcopy(first)
    recomputed["fingerprint"] = "outdated"
    assert project_review_fingerprint(recomputed) == first["fingerprint"]
