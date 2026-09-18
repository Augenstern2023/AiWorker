# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

import sqlite3

import pytest

from creative import production_export
from tests.test_production_export import db, free_db, imitation_db


def preview(db):
    return production_export.preview_production_package(db, 1)


def test_preview_matches_export_without_exposing_content(db):
    result = preview(db)
    package = production_export.export_production_package(db, 1)
    assert result['ready'] is True
    assert result['fingerprint'] == package['fingerprint']
    assert result['issues'] == []
    assert len(result['stages']) == 10
    assert all(item['state'] == 'ready' for item in result['stages'])
    assert result['version_selection']['strategy'] == 'current'
    assert result['version_selection']['manifest'] == package['version_manifest']
    assert result['version_selection']['candidates']['brief'][0]['selected'] is True
    assert '寻找真相' not in str(result)


def test_preview_blocks_missing_video_parameter_recommendation(db):
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM creative_video_parameter_recommendations")

    result = preview(db)

    assert result["ready"] is False
    issue = next(
        item for item in result["issues"] if item["stage"] == "video_parameters"
    )
    assert issue["code"] == "missing_video_parameters"
    assert issue["edit_url"] == "/writing/projects/1/episodes/1/scripts"


def test_preview_blocks_stale_video_parameter_recommendation(db):
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE creative_video_parameter_recommendations "
            "SET source_fingerprint = ?",
            ("0" * 64,),
        )

    result = preview(db)

    assert result["ready"] is False
    issue = next(
        item for item in result["issues"] if item["stage"] == "video_parameters"
    )
    assert issue["code"] == "stale_video_parameters"


def test_preview_blocks_partial_video_parameter_recommendation(db):
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE creative_video_parameter_recommendations "
            "SET source_episode_numbers_json = '[1]', source_complete = 0"
        )

    result = preview(db)

    assert result["ready"] is False
    issue = next(
        item for item in result["issues"] if item["stage"] == "video_parameters"
    )
    assert issue["code"] == "incomplete_video_parameters"


def test_free_custom_version_selection_is_explicitly_warned(free_db):
    path, project_id = free_db
    current = production_export.export_production_package(path, project_id)

    result = production_export.preview_production_package(
        path, project_id, current["version_manifest"],
    )

    assert result["ready"] is True
    assert result["version_selection"]["strategy"] == "custom"
    assert result["version_selection"]["warnings"]
    assert "自由创作" in result["version_selection"]["warnings"][0]


def test_standard_imitation_preview_starts_with_source_breakdown(imitation_db):
    result = production_export.preview_production_package(imitation_db, 1)

    assert result["ready"] is True
    assert result["stages"][0] == {
        "stage": "breakdown",
        "episode_number": None,
        "state": "ready",
        "versions": [{"id": 1, "version": 2}],
        "message": "检查通过",
        "edit_url": "/selection/topics",
    }


def test_standard_imitation_preview_blocks_unlocked_source_breakdown(imitation_db):
    with sqlite3.connect(imitation_db) as connection:
        connection.execute(
            "UPDATE creative_breakdowns SET status='draft' WHERE id=1"
        )

    result = production_export.preview_production_package(imitation_db, 1)

    assert result["ready"] is False
    issue = next(item for item in result["issues"] if item["stage"] == "breakdown")
    assert issue["code"] == "missing_locked_input"
    assert issue["edit_url"] == "/selection/topics"


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ({"creation_mode": "imitation", "workflow_mode": "sequential"}, True),
        ({"creation_mode": "imitation", "workflow_mode": "free"}, False),
        ({"creation_mode": "original", "workflow_mode": "sequential"}, False),
        ({"creation_mode": "original", "workflow_mode": "free"}, False),
    ],
)
def test_breakdown_export_scope_depends_on_project_type(project, expected):
    assert production_export._exports_breakdown(project) is expected


def test_preview_collects_independent_issues_and_blocks_dependents(db):
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM creative_episodes WHERE stage='beats' AND episode_number=1")
        connection.execute("UPDATE creative_episodes SET content_json='invalid' WHERE stage='script' AND episode_number=2")
    result = preview(db)
    assert not result['ready']
    assert result['fingerprint'] is None
    states = {(item['stage'], item['episode_number']): item['state'] for item in result['stages']}
    assert states['beats', 1] == 'invalid'
    assert states['script', 1] == 'blocked'
    assert states['script', 2] == 'invalid'
    assert {(item['stage'], item['episode_number']) for item in result['issues']} == {('beats', 1), ('script', 2)}


@pytest.mark.parametrize('sql', [
    "DELETE FROM creative_blueprints",
    "UPDATE creative_blueprints SET content_json='invalid'",
    "UPDATE creative_blueprints SET status='draft'",
])
def test_missing_upstream_is_not_reported_as_downstream_success(db, sql):
    with sqlite3.connect(db) as connection:
        connection.execute(sql)
    result = preview(db)
    assert not result['ready']
    assert result['stages'][0]['state'] == 'invalid'
    assert all(item['state'] == 'blocked' for item in result['stages'][1:])


def test_preview_missing_project_retains_not_found_semantics(db):
    with pytest.raises(KeyError):
        production_export.preview_production_package(db, 99)


def test_free_preview_reports_the_exact_missing_episode_head(free_db):
    path, project_id = free_db
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM creative_resource_heads WHERE project_id = ? "
            "AND resource_scope = 'free' AND stage = 'script' "
            "AND episode_number = 2",
            (project_id,),
        )

    result = production_export.preview_production_package(path, project_id)

    assert result["ready"] is False
    assert {
        (issue["stage"], issue["episode_number"])
        for issue in result["issues"]
    } == {("script", 2)}
