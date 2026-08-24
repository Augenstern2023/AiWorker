# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import sqlite3

import pytest

from creative.repository import CreativeRepository


def create_legacy_creative_database(db_path):
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE creative_topics (id INTEGER PRIMARY KEY);
        CREATE TABLE creative_breakdowns (
            id INTEGER PRIMARY KEY,
            topic_id INTEGER NOT NULL REFERENCES creative_topics(id)
        );
        CREATE TABLE creative_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL REFERENCES creative_topics(id),
            breakdown_id INTEGER NOT NULL REFERENCES creative_breakdowns(id),
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
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        INSERT INTO creative_topics(id) VALUES (1);
        INSERT INTO creative_breakdowns(id, topic_id) VALUES (1, 1);
        INSERT INTO creative_projects(
            id, topic_id, breakdown_id, name, target_episode_count,
            episode_duration_seconds, created_at, updated_at
        ) VALUES (1, 1, 1, '旧仿写项目', 20, 120, 1, 1);
        """
    )
    connection.close()


def create_pending_original_project(repository):
    return repository.create_original_project(
        seed_text="一个误入时间循环的外卖员",
        name="时间外卖",
        target_episode_count=12,
        target_platform="抖音",
        genre="悬疑",
        target_audience="都市年轻人",
        constraints={"tone": "轻悬疑"},
        reference_refs=[{"type": "note", "value": "无仿写来源"}],
        created_by="tester",
        creation_request_id="original-request-1",
    )


def ready_original_project(repository):
    project = create_pending_original_project(repository)
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def test_migrates_legacy_projects_to_imitation_schema_with_valid_foreign_keys(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    create_legacy_creative_database(db_path)

    CreativeRepository._migrate_project_table_if_needed(db_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    project = connection.execute("SELECT * FROM creative_projects WHERE id = 1").fetchone()
    columns = {row[1]: row for row in connection.execute("PRAGMA table_info(creative_projects)")}
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    connection.close()

    assert project["creation_mode"] == "imitation"
    assert project["bootstrap_status"] == "ready"
    assert columns["topic_id"][3] == 0
    assert columns["breakdown_id"][3] == 0
    assert foreign_key_errors == []


def test_original_project_starts_pending_with_a_draft_seed_and_is_hidden_until_ready(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")

    project = create_pending_original_project(repository)

    assert project["creation_mode"] == "original"
    assert project["bootstrap_status"] == "pending"
    assert project["topic_id"] is None
    assert project["breakdown_id"] is None
    assert repository.list_projects() == []
    inputs = repository.get_project_inputs(project["id"])
    assert inputs["topic"] == {}
    assert inputs["breakdown"] == {}
    assert inputs["seed"]["version"] == 1
    assert inputs["seed"]["status"] == "draft"
    assert inputs["seed"]["seed_text"] == "一个误入时间循环的外卖员"

    ready = repository.mark_project_bootstrap_status(project["id"], "ready")

    assert ready["bootstrap_status"] == "ready"
    assert [item["id"] for item in repository.list_projects()] == [project["id"]]


def test_frozen_seed_is_immutable_and_next_version_points_to_it(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = ready_original_project(repository)
    first_seed = repository.get_current_seed(project["id"])
    assert first_seed is not None

    frozen = repository.freeze_seed(first_seed["id"])
    second_seed = repository.create_seed_version(
        project["id"], seed_text="升级后的原创种子", constraints={"tone": "黑色幽默"}
    )

    assert frozen["status"] == "frozen"
    assert repository.update_seed(first_seed["id"], {"seed_text": "不能覆盖"}) is None
    assert repository.update_seed(first_seed["id"], {}) is None
    assert second_seed["version"] == 2
    assert second_seed["parent_version_id"] == first_seed["id"]
    assert repository.get_seed(second_seed["id"])["seed_text"] == "升级后的原创种子"
    assert [seed["id"] for seed in repository.list_seeds(project["id"])] == [
        first_seed["id"],
        second_seed["id"],
    ]


def test_draft_seed_rejects_a_blank_seed_text_update(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = ready_original_project(repository)
    seed = repository.get_current_seed(project["id"])
    assert seed is not None

    with pytest.raises(ValueError, match="原创种子内容不能为空"):
        repository.update_seed(seed["id"], {"seed_text": "   "})

    assert repository.get_seed(seed["id"])["seed_text"] == seed["seed_text"]


def test_deleting_original_project_removes_its_seed_versions(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = ready_original_project(repository)

    deleted = repository.delete_project(project["id"])

    assert deleted["id"] == project["id"]
    assert repository.get_project(project["id"]) is None
    assert repository.list_seeds(project["id"]) == []


@pytest.mark.parametrize("status", ["active", "failed", "complete"])
def test_project_delete_cleans_generation_reservations(tmp_path, status):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = ready_original_project(repository)
    reservation = repository.reserve_generation_operation(
        operation_id=f"delete-{status}",
        project_id=project["id"],
        stage="outline",
        request_hash=f"hash-{status}",
        owner_id="worker-1",
    )
    if status == "failed":
        assert repository.fail_generation_operation(
            operation_id=f"delete-{status}",
            request_hash=f"hash-{status}",
            owner_id="worker-1",
        )
    elif status == "complete":
        repository.complete_generation_operation(
            operation_id=f"delete-{status}",
            request_hash=f"hash-{status}",
            batch_id=reservation["batch_id"],
            owner_id="worker-1",
            result={"resource_refs": []},
        )

    repository.delete_project(project["id"])

    assert repository.get_project(project["id"]) is None
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM creative_generation_operations WHERE project_id = ?",
            (project["id"],),
        ).fetchone()[0] == 0


def test_original_blueprints_persist_direction_type_and_seed_source(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = ready_original_project(repository)
    seed = repository.get_current_seed(project["id"])
    assert seed is not None

    blueprints = repository.create_blueprints(
        project["id"],
        [{"content": {"title": "方向 A"}, "risk_score": 0, "risk_level": "low"}],
        direction_type="original",
        source_refs={"seed_id": seed["id"], "seed_version": seed["version"]},
    )

    assert blueprints[0]["direction_type"] == "original"
    assert blueprints[0]["source_refs"]["seed_id"] == seed["id"]


@pytest.mark.parametrize("status", ["pending", "ready", "failed"])
def test_bootstrap_status_accepts_the_declared_values(tmp_path, status):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_pending_original_project(repository)

    updated = repository.mark_project_bootstrap_status(project["id"], status)

    assert updated["bootstrap_status"] == status
