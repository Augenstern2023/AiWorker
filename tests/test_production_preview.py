# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Preview contract tests for the snapshot reader against PostgreSQL."""

import pytest
from sqlalchemy import delete, update

from creative import production_export
from creative.database_schema import (
    creative_blueprints,
    creative_episodes,
    creative_resource_heads,
)
from tests.test_production_export import free_project, sequential_project


def preview(repository, project_id):
    return production_export.preview_production_package(repository, project_id)


def test_preview_matches_export_without_exposing_content(
    postgres_creative, sequential_project,
):
    result = preview(postgres_creative, sequential_project)
    package = production_export.export_production_package(
        postgres_creative, sequential_project,
    )
    assert result['ready'] is True
    assert result['fingerprint'] == package['fingerprint']
    assert result['issues'] == []
    assert len(result['stages']) == 9
    assert all(item['state'] == 'ready' for item in result['stages'])
    assert result['version_selection']['strategy'] == 'current'
    assert result['version_selection']['manifest'] == package['version_manifest']
    assert result['version_selection']['candidates']['brief'][0]['selected'] is True
    assert '寻找真相' not in str(result)


def test_free_custom_version_selection_is_explicitly_warned(
    postgres_creative, free_project,
):
    current = production_export.export_production_package(
        postgres_creative, free_project,
    )

    result = production_export.preview_production_package(
        postgres_creative, free_project, current["version_manifest"],
    )

    assert result["ready"] is True
    assert result["version_selection"]["strategy"] == "custom"
    assert result["version_selection"]["warnings"]
    assert "自由创作" in result["version_selection"]["warnings"][0]


def test_preview_collects_independent_issues_and_blocks_dependents(
    postgres_creative, sequential_project,
):
    with postgres_creative.transaction_scope() as connection:
        connection.execute(
            delete(creative_episodes).where(
                creative_episodes.c.stage == 'beats',
                creative_episodes.c.episode_number == 1,
            )
        )
        connection.execute(
            update(creative_episodes)
            .where(
                creative_episodes.c.stage == 'script',
                creative_episodes.c.episode_number == 2,
            )
            .values(content_json='invalid')
        )
    result = preview(postgres_creative, sequential_project)
    assert not result['ready']
    assert result['fingerprint'] is None
    states = {(item['stage'], item['episode_number']): item['state'] for item in result['stages']}
    assert states['beats', 1] == 'invalid'
    assert states['script', 1] == 'blocked'
    assert states['script', 2] == 'invalid'
    assert {(item['stage'], item['episode_number']) for item in result['issues']} == {
        ('beats', 1), ('script', 2),
    }


@pytest.mark.parametrize('mutate', [
    lambda connection: connection.execute(delete(creative_blueprints)),
    lambda connection: connection.execute(
        update(creative_blueprints).values(content_json='invalid')
    ),
    lambda connection: connection.execute(
        update(creative_blueprints).values(status='draft')
    ),
])
def test_missing_upstream_is_not_reported_as_downstream_success(
    postgres_creative, sequential_project, mutate,
):
    with postgres_creative.transaction_scope() as connection:
        mutate(connection)
    result = preview(postgres_creative, sequential_project)
    assert not result['ready']
    assert result['stages'][0]['state'] == 'invalid'
    assert all(item['state'] == 'blocked' for item in result['stages'][1:])


def test_preview_missing_project_retains_not_found_semantics(
    postgres_creative, sequential_project,
):
    with pytest.raises(KeyError):
        production_export.preview_production_package(postgres_creative, 99)


def test_free_preview_reports_the_exact_missing_episode_head(
    postgres_creative, free_project,
):
    with postgres_creative.transaction_scope() as connection:
        connection.execute(
            delete(creative_resource_heads).where(
                creative_resource_heads.c.project_id == free_project,
                creative_resource_heads.c.resource_scope == 'free',
                creative_resource_heads.c.stage == 'script',
                creative_resource_heads.c.episode_number == 2,
            )
        )

    result = production_export.preview_production_package(
        postgres_creative, free_project,
    )

    assert result["ready"] is False
    assert {
        (issue["stage"], issue["episode_number"])
        for issue in result["issues"]
    } == {("script", 2)}
