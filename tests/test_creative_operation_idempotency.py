# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from creative.repository import CreativeRepository


def valid_original_brief(title: str) -> dict[str, Any]:
    return {
        "title": title, "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择", "protagonist": "林岚",
        "protagonist_desire": "获得自主权", "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作", "main_opposition": "僵化制度",
        "core_conflict": "情绪考核", "main_goal": "改变制度",
        "background": "未来公司", "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核", "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"], "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁", "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low", "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"], "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }


def seed_original_project(repository: CreativeRepository, episodes: int = 1) -> dict:
    project, _ = repository.ensure_original_project(
        seed_text="未来公司按照员工情绪发工资", name="情绪公司",
        target_episode_count=episodes, target_platform="hg", genre="都市奇幻",
        target_audience="18-35岁", episode_duration_seconds=120,
        constraints={}, reference_refs=[], created_by="writer-1",
        creation_request_id=f"project-{episodes}",
    )
    return project


def seed_locked_outline_project(repository: CreativeRepository) -> dict:
    project = seed_original_project(repository)
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("情绪公司")}],
        direction_type="original",
    )[0]
    repository.lock_brief(brief["id"])
    characters = repository.create_character_batch(
        project["id"], [{"name": "林岚", "desire": "夺回选择权"}], []
    )
    repository.lock_character_batch(project["id"], characters["batch_id"])
    outline = repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "工资因愤怒清零"}], "反抗制度"
    )
    repository.lock_outline_batch(project["id"], outline["batch_id"])
    return repository.get_project(project["id"])


def test_same_operation_id_returns_the_same_beats_version(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)

    first = repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-1:beats:1",
    )
    second = repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-1:beats:1",
    )

    assert second["id"] == first["id"]
    versions = repository.list_episode_versions(project["id"], "beats", 1)
    assert [item["version"] for item in versions] == [1]


def test_project_revision_rejects_a_stale_pointer_change(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    revision = project["revision"]
    repository.update_project(
        project["id"], {"name": "新项目名"}, expected_revision=revision
    )

    with pytest.raises(ValueError, match="project revision changed"):
        repository.update_project(
            project["id"], {"genre": "悬疑"}, expected_revision=revision
        )


def test_normalized_project_no_op_preserves_revision_and_cas_semantics(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)

    no_op = repository.update_project(
        project["id"],
        {
            "name": "  情绪公司  ",
            "genre": " 都市奇幻 ",
            "target_episode_count": "1",
        },
        expected_revision=project["revision"],
    )
    assert no_op["revision"] == project["revision"]

    changed = repository.update_project(
        project["id"],
        {"genre": "悬疑"},
        expected_revision=project["revision"],
    )
    assert changed["revision"] == project["revision"] + 1

    with pytest.raises(ValueError, match="project revision changed"):
        repository.update_project(
            project["id"],
            {"name": "  情绪公司  "},
            expected_revision=project["revision"],
        )


def test_pre_revision_project_schema_preserves_data_and_defaults_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "creative.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE creative_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                breakdown_id INTEGER,
                name TEXT NOT NULL,
                genre TEXT NOT NULL DEFAULT '',
                target_platform TEXT NOT NULL DEFAULT '',
                target_audience TEXT NOT NULL DEFAULT '',
                target_episode_count INTEGER NOT NULL DEFAULT 20,
                episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
                status TEXT NOT NULL DEFAULT 'created',
                locked_brief_id INTEGER,
                character_batch_id TEXT,
                outline_batch_id TEXT,
                workflow_task_id TEXT,
                creation_workflow_task_id TEXT,
                creation_request_id TEXT,
                created_by TEXT NOT NULL DEFAULT 'local',
                deleted INTEGER NOT NULL DEFAULT 0,
                creation_mode TEXT NOT NULL DEFAULT 'original',
                bootstrap_status TEXT NOT NULL DEFAULT 'ready',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO creative_projects (
                id, name, genre, target_platform, target_audience,
                target_episode_count, episode_duration_seconds, creation_mode,
                created_by, created_at, updated_at
            ) VALUES (41, '旧库项目', '悬疑', 'hg', '18-35岁', 12, 90,
                      'original', 'legacy-writer', 1000, 1001)
            """
        )

    repository = CreativeRepository(db_path)
    project = repository.get_project(41)

    assert project is not None
    assert project["name"] == "旧库项目"
    assert project["target_episode_count"] == 12
    assert project["created_by"] == "legacy-writer"
    assert project["revision"] == 0
    with repository._connect() as conn:
        revision_default = {
            row[1]: row[4]
            for row in conn.execute("PRAGMA table_info(creative_projects)")
        }["revision"]
    assert revision_default == "0"


def test_operation_id_cannot_be_reused_for_different_payload(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)
    repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-2:beats:1",
    )

    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_REUSED"):
        repository.create_episode_beats(
            project["id"], 1, {"title": "第1集", "beats": [{"beat": "冲突"}]},
            operation_id="writer-action-2:beats:1",
        )


def test_replay_returns_existing_beats_after_authority_pointers_change(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)
    content = {"title": "第1集", "beats": [{"beat": "开场"}]}
    first = repository.create_episode_beats(
        project["id"], 1, content, operation_id="writer-action-replay:beats:1"
    )
    replacement = repository.create_blueprints(
        project["id"],
        [{"content": valid_original_brief("替代方向")}],
        direction_type="original",
    )[0]
    repository.lock_brief(replacement["id"])
    assert repository.get_project(project["id"])["outline_batch_id"] is None

    replay = repository.create_episode_beats(
        project["id"], 1, content, operation_id="writer-action-replay:beats:1"
    )

    assert replay == first


def test_reused_operation_key_reports_idempotency_before_scope_validation(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)
    repository.create_episode_beats(
        project["id"],
        1,
        {"title": "第1集", "beats": [{"beat": "开场"}]},
        operation_id="writer-action-reused-scope:beats",
    )

    with pytest.raises(ValueError) as exc_info:
        repository.create_episode_beats(
            project["id"],
            2,
            {"title": "第2集", "beats": [{"beat": "越界冲突"}]},
            operation_id="writer-action-reused-scope:beats",
        )

    assert str(exc_info.value) == "IDEMPOTENCY_KEY_REUSED"


def test_stable_request_hash_ignores_mapping_insertion_order(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)
    first = repository.create_episode_beats(
        project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场", "order": 1}]},
        operation_id="writer-action-3:beats:1",
    )
    second = repository.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1, "beat": "开场"}], "title": "第1集"},
        operation_id="writer-action-3:beats:1",
    )

    assert second == first


def test_parallel_replay_creates_one_episode_version(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_locked_outline_project(repository)

    def create() -> dict[str, Any]:
        return repository.create_episode_beats(
            project["id"], 1, {"title": "第1集", "beats": [{"beat": "开场"}]},
            operation_id="writer-action-4:beats:1",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: create(), range(2)))

    assert first["id"] == second["id"]
    assert [
        item["version"]
        for item in repository.list_episode_versions(project["id"], "beats", 1)
    ] == [1]


def test_authority_pointer_changes_increment_revision_but_drafts_do_not(
    tmp_path: Path,
) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("情绪公司")}],
        direction_type="original", operation_id="writer-action-5:brief",
    )[0]
    assert repository.get_project(project["id"])["revision"] == 0

    locked_brief = repository.lock_brief(brief["id"], expected_revision=0)
    assert locked_brief["revision"] == 1
    characters = repository.create_character_batch(
        project["id"], [{"name": "林岚"}], [], operation_id="writer-action-5:characters"
    )
    assert repository.get_project(project["id"])["revision"] == 1
    locked_characters = repository.lock_character_batch(
        project["id"], characters["batch_id"], expected_revision=1
    )
    assert locked_characters["revision"] == 2
    outline = repository.create_outline_batch(
        project["id"], [{"episode": 1}], "反抗制度",
        operation_id="writer-action-5:outline",
    )
    assert repository.get_project(project["id"])["revision"] == 2
    locked_outline = repository.lock_outline_batch(
        project["id"], outline["batch_id"], expected_revision=2
    )
    assert locked_outline["revision"] == 3


def test_stage_operation_replays_restore_original_return_shapes(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    blueprint_args = (
        project["id"], [{"content": valid_original_brief("情绪公司")}]
    )
    first_briefs = repository.create_blueprints(
        *blueprint_args, direction_type="original", operation_id="writer-action-6:brief"
    )
    second_briefs = repository.create_blueprints(
        *blueprint_args, direction_type="original", operation_id="writer-action-6:brief"
    )
    assert isinstance(second_briefs, list)
    assert second_briefs == first_briefs

    repository.lock_brief(first_briefs[0]["id"])
    first_characters = repository.create_character_batch(
        project["id"], [{"name": "林岚"}], [], operation_id="writer-action-6:characters"
    )
    second_characters = repository.create_character_batch(
        project["id"], [{"name": "林岚"}], [], operation_id="writer-action-6:characters"
    )
    assert second_characters == first_characters
    assert set(second_characters) >= {"batch_id", "characters", "relationships"}


def test_blueprint_revision_is_created_and_replayed_atomically(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    first = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("初版")}],
        direction_type="original",
    )[0]
    content = valid_original_brief("修订版")
    revision = repository.create_blueprint_revision(
        first["id"], content, "workflow-1", "writer-action-7:brief-revision"
    )
    replay = repository.create_blueprint_revision(
        first["id"], content, "workflow-1", "writer-action-7:brief-revision"
    )

    assert replay == revision
    assert revision["version"] == 2
    assert revision["parent_version_id"] == first["id"]
    assert revision["candidate_group_id"] == first["candidate_group_id"]
    assert revision["content"] == content


def test_recorded_lock_result_can_be_replayed_and_hydrated(tmp_path: Path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    project = seed_original_project(repository)
    brief = repository.create_blueprints(
        project["id"], [{"content": valid_original_brief("情绪公司")}],
        direction_type="original",
    )[0]
    locked = repository.lock_brief(brief["id"])
    resource_ref = {
        "resource_type": "brief",
        "resource_id": locked["id"],
        "revision": locked["revision"],
    }

    first_ref = repository.record_operation_result(
        operation_id="writer-action-8:brief-lock", project_id=project["id"],
        stage="brief_lock", request_hash="stable-lock-hash", resource_ref=resource_ref,
    )
    second_ref = repository.record_operation_result(
        operation_id="writer-action-8:brief-lock", project_id=project["id"],
        stage="brief_lock", request_hash="stable-lock-hash", resource_ref=resource_ref,
    )

    assert first_ref == second_ref == resource_ref
    assert repository.hydrate_operation_result("writer-action-8:brief-lock") == locked
