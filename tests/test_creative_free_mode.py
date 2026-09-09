# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import pytest
import json
import sqlite3
import time
import uuid

from creative.free_mode_service import FreeCreativeModeService
from creative.repository import CreativeRepository


def create_legacy_workflow_mode_database(db_path) -> None:
    CreativeRepository(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO creative_topics (
                id, platform, job_id, source_path, captured_at, title, status,
                score, confidence, score_details_json, recommendation,
                evidence_json, created_at, updated_at
            ) VALUES (
                1, 'hg', 'legacy-job', 'legacy.json', '2026-09-09',
                '旧库选题', 'adopted', 80, 'high', '{}', '可立项', '[]', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO creative_breakdowns (
                id, topic_id, version, status, content_json,
                field_sources_json, risks_json, created_at, updated_at
            ) VALUES (1, 1, 1, 'locked', '{}', '{}', '[]', 1, 1)
            """
        )
        connection.commit()
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'creative_projects'"
        ).fetchone()[0]
        legacy_sql = table_sql.replace(
            "CREATE TABLE creative_projects",
            "CREATE TABLE creative_projects_legacy",
            1,
        ).replace(
            "DEFAULT 'sequential'",
            "DEFAULT 'strict'",
        ).replace(
            "('sequential', 'free')",
            "('free', 'strict')",
        )
        assert legacy_sql != table_sql

        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(legacy_sql)
        connection.execute(
            """
            INSERT INTO creative_projects_legacy (
                id, name, creation_mode, workflow_mode, created_at, updated_at
            ) VALUES
                (1, '旧标准流程项目', 'original', 'strict', 1, 1),
                (2, '旧自由创作项目', 'original', 'free', 1, 1)
            """
        )
        connection.execute("DROP TABLE creative_projects")
        connection.execute(
            "ALTER TABLE creative_projects_legacy RENAME TO creative_projects"
        )
        connection.commit()
    finally:
        connection.close()


def create_free_project(repository: CreativeRepository) -> dict:
    project = repository.create_original_project(
        seed_text="一个能听见谎言的普通女孩",
        name="谎言回声",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="free-project-1",
        workflow_mode="free",
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def test_existing_projects_default_to_sequential_mode(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个时间循环故事",
        name="循环",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="sequential-project-1",
    )

    assert project["workflow_mode"] == "sequential"
    assert project["mode_revision"] == 0
    assert project["resource_heads"] == []


def test_legacy_strict_workflow_mode_is_migrated_without_losing_projects(tmp_path):
    db_path = tmp_path / "legacy-workflow-mode.sqlite3"
    create_legacy_workflow_mode_database(db_path)

    repository = CreativeRepository(db_path)

    assert repository.get_project(1)["workflow_mode"] == "sequential"
    assert repository.get_project(2)["workflow_mode"] == "free"
    created = repository.create_project(
        topic_id=1,
        breakdown_id=1,
        name="新标准流程项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="post-workflow-mode-migration",
    )
    assert created["workflow_mode"] == "sequential"

    connection = sqlite3.connect(db_path)
    try:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'creative_projects'"
        ).fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(creative_projects)")
        }
    finally:
        connection.close()

    assert "('sequential', 'free')" in table_sql
    assert "('free', 'strict')" not in table_sql
    assert foreign_key_errors == []
    assert "idx_creative_projects_topic" in indexes


def test_topic_based_free_project_does_not_require_manual_seed(tmp_path):
    db_path = tmp_path / "topic-free-without-seed.sqlite3"
    create_legacy_workflow_mode_database(db_path)
    repository = CreativeRepository(db_path)

    project = repository.create_project(
        topic_id=1,
        breakdown_id=1,
        name="直接创建的自由项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="topic-free-without-seed",
        workflow_mode="free",
    )

    seed = repository.get_current_seed(project["id"])
    assert seed is not None
    assert "旧库选题" in seed["seed_text"]
    context = FreeCreativeModeService(repository).create_generation_context(
        context_id="topic-free-without-seed-context",
        project_id=project["id"],
        stage="brief",
        episode_number=None,
        instruction="",
        context_refs={},
        actor_user_id="owner-1",
    )
    assert "旧库选题" in context["context_snapshot"]["seed"]["seed_text"]


def test_free_resources_do_not_update_sequential_authority(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    brief_result = service.create_brief(
        project["id"],
        {"title": "谎言回声", "logline": "女孩借能力揭开家族秘密"},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    character_result = service.create_characters(
        project["id"],
        [],
        [],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    outline_result = service.create_outline(
        project["id"],
        [],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    beats_result = service.create_episode_resource(
        project["id"],
        "beats",
        1,
        {"beats": []},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    script_result = service.create_episode_resource(
        project["id"],
        "script",
        1,
        {"episode_title": "", "scenes": []},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )

    refreshed = repository.get_project(project["id"])
    assert refreshed["locked_brief_id"] is None
    assert refreshed["character_batch_id"] is None
    assert refreshed["outline_batch_id"] is None
    assert len(refreshed["resource_heads"]) == 5
    assert brief_result["resource"]["resource_scope"] == "free"
    assert character_result["resource"]["characters"][0]["resource_scope"] == "free"
    assert len(outline_result["resource"]["episodes"]) == 3
    assert beats_result["resource"]["resource_scope"] == "free"
    assert script_result["resource"]["resource_scope"] == "free"


def test_context_only_accepts_locked_same_project_resources(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    other = repository.create_original_project(
        seed_text="另一个项目",
        name="另一个项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="free-project-2",
        workflow_mode="free",
    )
    other = repository.mark_project_bootstrap_status(other["id"], "ready")
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project["id"], {"title": "主项目"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    other_brief = service.create_brief(
        other["id"], {"title": "其他项目"},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="已锁定"):
        service.create_generation_context(
            context_id="context-draft",
            project_id=project["id"],
            stage="characters",
            episode_number=None,
            instruction="",
            context_refs={"brief_id": brief["resource"]["id"]},
            actor_user_id="owner-1",
        )
    with pytest.raises(ValueError, match="同项目"):
        service.create_generation_context(
            context_id="context-cross-project",
            project_id=project["id"],
            stage="characters",
            episode_number=None,
            instruction="",
            context_refs={"brief_id": other_brief["resource"]["id"]},
            actor_user_id="owner-1",
        )

    locked = service.lock_resource(
        project_id=project["id"],
        stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    context = service.create_generation_context(
        context_id="context-locked",
        project_id=project["id"],
        stage="characters",
        episode_number=None,
        instruction="扩展配角",
        context_refs={"brief_id": brief["resource"]["id"]},
        actor_user_id="owner-1",
    )

    assert locked["head"]["revision"] == 2
    assert context["context_snapshot"]["brief"]["title"] == "主项目"
    assert context["context_manifest"]["brief"]["resource_scope"] == "free"
    assert context["context_hash"]


def test_generation_context_id_cannot_move_to_another_episode(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    service.create_generation_context(
        context_id="context-fixed-episode",
        project_id=project["id"], stage="beats", episode_number=1,
        instruction="", context_refs={}, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="different context"):
        service.create_generation_context(
            context_id="context-fixed-episode",
            project_id=project["id"], stage="beats", episode_number=2,
            instruction="", context_refs={}, actor_user_id="owner-1",
        )


def test_generation_context_rejects_a_partially_unlocked_character_bundle(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    created = service.create_characters(
        project["id"],
        [{"name": "林真", "role": "主角"}],
        [{"source": "林真", "target": "母亲", "relation": "母女"}],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=created["resource"]["batch_id"],
        expected_head_revision=created["head"]["revision"],
        actor_user_id="owner-1",
    )
    with repository._connect() as conn:
        conn.execute(
            "UPDATE creative_relationships SET status = 'draft' "
            "WHERE project_id = ? AND batch_id = ?",
            (project["id"], created["resource"]["batch_id"]),
        )

    with pytest.raises(ValueError, match="已锁定"):
        service.create_generation_context(
            context_id="context-partial-characters",
            project_id=project["id"], stage="outline", episode_number=None,
            instruction="",
            context_refs={"character_batch_id": created["resource"]["batch_id"]},
            actor_user_id="owner-1",
        )


def test_free_head_uses_per_module_compare_and_swap(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    first = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="head changed"):
        service.create_episode_resource(
            project["id"], "beats", 1, {"beats": [{"beat": "冲突"}]},
            expected_head_revision=0, actor_user_id="owner-1",
        )

    second = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": [{"beat": "冲突"}]},
        expected_head_revision=first["head"]["revision"], actor_user_id="owner-1",
    )
    assert second["head"]["revision"] == 2


def test_free_brief_versions_follow_the_current_head(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    first = service.create_brief(
        project["id"], {"title": "第一版"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    locked = service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(first["resource"]["id"]),
        expected_head_revision=first["head"]["revision"],
        actor_user_id="owner-1",
    )
    second = service.create_brief(
        project["id"], {"title": "第二版"},
        expected_head_revision=locked["head"]["revision"], actor_user_id="owner-1",
    )

    assert first["resource"]["version"] == 1
    assert second["resource"]["version"] == 2
    assert second["resource"]["parent_version_id"] == first["resource"]["id"]
    assert repository.get_blueprint(first["resource"]["id"])["status"] == "brief_locked"
    assert second["resource"]["status"] == "draft"
    assert second["head"]["resource_ref"] == str(second["resource"]["id"])
    assert second["head"]["revision"] == locked["head"]["revision"] + 1


def test_locked_character_bundle_can_create_a_draft_revision(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    first = service.create_characters(
        project["id"],
        [{
            "character_key": "hero",
            "content": {"name": "林真", "role": "主角"},
        }],
        [{
            "relationship_key": "hero-mother",
            "content": {"source": "林真", "target": "母亲", "relation": "母女"},
        }],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    first_character = first["resource"]["characters"][0]
    first_relationship = first["resource"]["relationships"][0]
    locked = service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=first["resource"]["batch_id"],
        expected_head_revision=first["head"]["revision"],
        actor_user_id="owner-1",
    )

    second = service.create_characters(
        project["id"],
        [{
            "character_key": "hero",
            "content": dict(first_character["content"]),
        }],
        [{
            "relationship_key": "hero-mother",
            "content": dict(first_relationship["content"]),
        }],
        expected_head_revision=locked["head"]["revision"],
        actor_user_id="owner-1",
    )

    old_bundle = repository.get_character_batch(
        project["id"], first["resource"]["batch_id"]
    )
    new_character = second["resource"]["characters"][0]
    new_relationship = second["resource"]["relationships"][0]
    assert old_bundle["characters"][0]["status"] == "locked"
    assert old_bundle["relationships"][0]["status"] == "locked"
    assert new_character["status"] == "draft"
    assert new_relationship["status"] == "draft"
    assert new_character["parent_version_id"] == first_character["id"]
    assert new_relationship["parent_version_id"] == first_relationship["id"]
    assert second["head"]["resource_ref"] == second["resource"]["batch_id"]
    assert second["head"]["revision"] == locked["head"]["revision"] + 1


def test_locked_outline_bundle_can_create_a_draft_revision(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    episodes = [
        {"episode": number, "core_event": f"第 {number} 集事件"}
        for number in range(1, 4)
    ]
    first = service.create_outline(
        project["id"], episodes,
        series_arc="第一版全剧弧线",
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    first_by_episode = {
        item["episode_number"]: item for item in first["resource"]["episodes"]
    }
    locked = service.lock_resource(
        project_id=project["id"], stage="outline",
        resource_ref=first["resource"]["batch_id"],
        expected_head_revision=first["head"]["revision"],
        actor_user_id="owner-1",
    )

    second = service.create_outline(
        project["id"], [dict(item) for item in episodes],
        series_arc="第一版全剧弧线",
        expected_head_revision=locked["head"]["revision"],
        actor_user_id="owner-1",
    )

    old_bundle = repository.get_outline_batch(
        project["id"], first["resource"]["batch_id"]
    )
    assert all(item["status"] == "locked" for item in old_bundle["episodes"])
    assert all(item["status"] == "draft" for item in second["resource"]["episodes"])
    assert all(
        item["parent_version_id"] == first_by_episode[item["episode_number"]]["id"]
        for item in second["resource"]["episodes"]
    )
    assert second["head"]["resource_ref"] == second["resource"]["batch_id"]
    assert second["head"]["revision"] == locked["head"]["revision"] + 1


@pytest.mark.parametrize(
    ("stage", "content"),
    [
        ("beats", {"beats": [{"beat": "开场冲突"}], "updates": []}),
        ("script", {"episode_title": "第一集", "scenes": []}),
    ],
)
def test_locked_episode_resource_can_create_a_draft_revision(
    tmp_path, stage, content,
):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    first = service.create_episode_resource(
        project["id"], stage, 1, content,
        expected_head_revision=0, actor_user_id="owner-1",
    )
    locked = service.lock_resource(
        project_id=project["id"], stage=stage,
        resource_ref=str(first["resource"]["id"]), episode_number=1,
        expected_head_revision=first["head"]["revision"],
        actor_user_id="owner-1",
    )

    second = service.create_episode_resource(
        project["id"], stage, 1, dict(content),
        expected_head_revision=locked["head"]["revision"],
        actor_user_id="owner-1",
    )

    assert repository.get_episode(first["resource"]["id"])["status"] == "locked"
    assert second["resource"]["status"] == "draft"
    assert second["resource"]["parent_version_id"] == first["resource"]["id"]
    assert second["head"]["resource_ref"] == str(second["resource"]["id"])
    assert second["head"]["revision"] == locked["head"]["revision"] + 1


def test_empty_project_can_switch_mode_but_content_project_cannot(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个空项目",
        name="空项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="switch-project-1",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    service = FreeCreativeModeService(repository)

    switched = service.switch_empty_mode(
        project_id=project["id"], workflow_mode="free",
        expected_mode_revision=0, actor_user_id="owner-1",
    )
    assert switched["workflow_mode"] == "free"
    assert switched["mode_revision"] == 1

    service.create_brief(
        project["id"], {"title": "已创建"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    with pytest.raises(ValueError, match="已有创作资源"):
        service.switch_empty_mode(
            project_id=project["id"], workflow_mode="sequential",
            expected_mode_revision=1, actor_user_id="owner-1",
        )


def test_topic_project_can_switch_to_free_without_manual_seed(tmp_path):
    db_path = tmp_path / "switch-topic-project-without-seed.sqlite3"
    create_legacy_workflow_mode_database(db_path)
    repository = CreativeRepository(db_path)
    project = repository.create_project(
        topic_id=1,
        breakdown_id=1,
        name="待切换项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="switch-topic-project-without-seed",
    )
    assert repository.get_current_seed(project["id"]) is None

    switched = FreeCreativeModeService(repository).switch_empty_mode(
        project_id=project["id"],
        workflow_mode="free",
        expected_mode_revision=0,
        actor_user_id="owner-1",
    )

    assert switched["workflow_mode"] == "free"
    assert "旧库选题" in repository.get_current_seed(project["id"])["seed_text"]


@pytest.mark.asyncio
async def test_empty_project_preview_ignores_started_task_without_resources(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from creative.api import build_creative_router
    from creative.schema import CreativeModeChangeRequest

    repository = CreativeRepository(tmp_path / "empty-preview.sqlite3")
    project = repository.create_original_project(
        seed_text="尚未开始生成的项目",
        name="空项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="empty-preview-project",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    workflow = Mock()
    workflow.list_tasks.return_value = [{
        "id": "brief-started",
        "project_id": str(project["id"]),
        "task_type": "brief",
        "status": "in_progress",
    }]
    router = build_creative_router(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        repository=repository,
        workflow_repository=workflow,
        application_service=Mock(),
    )
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/mode-migration-preview"
    )

    preview = await endpoint(
        project_id=project["id"],
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        principal=SimpleNamespace(is_system_admin=True, user_id="owner-1"),
    )

    assert preview["can_migrate"] is True
    assert "项目存在进行中的创作任务" not in preview["blockers"]

    change_endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/workflow-mode"
    )
    changed = await change_endpoint(
        project_id=project["id"],
        payload=CreativeModeChangeRequest(
            workflow_mode="free", expected_mode_revision=0
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        principal=SimpleNamespace(is_system_admin=True, user_id="owner-1"),
    )
    assert changed["workflow_mode"] == "free"
    workflow.cancel_task.assert_called_once()


@pytest.mark.asyncio
async def test_content_project_migration_copies_resources_and_cancels_workflow_tasks(
    tmp_path,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from creative.api import build_creative_router
    from creative.schema import CreativeModeMigrationRequest

    repository = CreativeRepository(tmp_path / "content-migration.sqlite3")
    project = repository.create_original_project(
        seed_text="已有标准流程内容",
        name="非空迁移项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="content-migration-project",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    blueprint = repository.create_blueprints(
        project["id"], [{"content": {"title": "标准流程方向"}}]
    )[0]
    workflow = Mock()
    workflow.list_tasks.return_value = [
        {
            "id": "brief-started",
            "project_id": str(project["id"]),
            "task_type": "brief",
            "status": "in_progress",
        },
        {
            "id": "characters-returned",
            "project_id": str(project["id"]),
            "task_type": "characters",
            "status": "returned",
        },
    ]
    router = build_creative_router(
        root=tmp_path,
        runtime_dir=tmp_path / ".runtime",
        repository=repository,
        workflow_repository=workflow,
        application_service=Mock(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    principal = SimpleNamespace(is_system_admin=True, user_id="owner-1")
    preview_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path
        == "/api/creative/projects/{project_id}/mode-migration-preview"
    )

    preview = await preview_endpoint(
        project_id=project["id"], request=request, principal=principal
    )

    assert preview["resource_counts"]["brief"] == 1
    assert preview["can_migrate"] is True
    assert "项目存在进行中的创作任务" not in preview["blockers"]

    migrate_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/migrate-to-free"
    )
    migrated = await migrate_endpoint(
        project_id=project["id"],
        payload=CreativeModeMigrationRequest(
            expected_mode_revision=0,
            operation_id="content-migration-operation",
        ),
        request=request,
        principal=principal,
    )

    mapping = migrated["mapping"]["brief"]
    assert migrated["project"]["workflow_mode"] == "free"
    assert mapping["source"] == blueprint["id"]
    assert mapping["target"] != blueprint["id"]
    assert repository.get_blueprint(mapping["target"])["resource_scope"] == "free"
    assert workflow.cancel_task.call_count == 2


def test_content_project_migrates_to_free_without_manual_seed(tmp_path):
    db_path = tmp_path / "migrate-topic-project-without-seed.sqlite3"
    create_legacy_workflow_mode_database(db_path)
    repository = CreativeRepository(db_path)
    project = repository.create_project(
        topic_id=1,
        breakdown_id=1,
        name="已有内容待迁移项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="migrate-topic-project-without-seed",
    )
    repository.create_blueprints(
        project["id"], [{"content": {"title": "已有方向"}}]
    )

    migrated = FreeCreativeModeService(repository).migrate_to_free(
        project_id=project["id"],
        expected_mode_revision=0,
        operation_id="migrate-topic-project-without-seed",
        actor_user_id="owner-1",
    )

    assert migrated["project"]["workflow_mode"] == "free"
    assert "旧库选题" in repository.get_current_seed(project["id"])["seed_text"]


def test_returned_workflow_task_can_be_cancelled_during_mode_migration():
    from workflow.state_machine import WorkflowStateMachine

    status = WorkflowStateMachine().transition(
        {
            "status": "returned",
            "role_code": "creative_planner",
            "assignee_user_id": "planner-1",
        },
        "cancel",
        {"permissions": {"*"}},
    )

    assert status == "cancelled"


def test_migration_copies_current_resources_and_preserves_legacy_history(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个带着旧版本的项目",
        name="迁移项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-project-1",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    now = time.time()
    with repository._connect() as conn:
        brief_cursor = conn.execute(
            """
            INSERT INTO creative_blueprints (
                project_id, candidate_group_id, version, status, content_json,
                source_refs_json, direction_type, generation_meta_json,
                resource_scope, created_at, updated_at
            ) VALUES (?, ?, 1, 'brief_locked', ?, '{}', 'original', '{}', 'sequential', ?, ?)
            """,
            (project["id"], uuid.uuid4().hex, json.dumps({"title": "旧 Brief"}), now, now),
        )
        brief_id = int(brief_cursor.lastrowid)
        conn.execute(
            "UPDATE creative_projects SET locked_brief_id = ?, status = 'brief_locked' WHERE id = ?",
            (brief_id, project["id"]),
        )
        beats_cursor = conn.execute(
            """
            INSERT INTO creative_episodes (
                project_id, stage, episode_number, batch_id, version, status,
                content_json, upstream_refs_json, generation_meta_json,
                resource_scope, created_at, updated_at
            ) VALUES (?, 'beats', 1, ?, 1, 'draft', ?, '{}', '{}', 'sequential', ?, ?)
            """,
            (project["id"], uuid.uuid4().hex, json.dumps({"beats": []}), now, now),
        )
        source_beats_id = int(beats_cursor.lastrowid)
    service = FreeCreativeModeService(repository)

    migrated = service.migrate_to_free(
        project_id=project["id"],
        expected_mode_revision=0,
        operation_id="migration-operation-1",
        actor_user_id="owner-1",
    )
    repeated = service.migrate_to_free(
        project_id=project["id"],
        expected_mode_revision=0,
        operation_id="migration-operation-1",
        actor_user_id="owner-1",
    )

    free_project = migrated["project"]
    legacy = service.list_legacy_resources(project["id"])
    assert free_project["workflow_mode"] == "free"
    assert free_project["locked_brief_id"] == brief_id
    assert all(item["resource_scope"] == "free" for item in free_project["blueprints"])
    assert all(item["resource_scope"] == "free" for item in free_project["episodes"])
    assert legacy["blueprints"][0]["id"] == brief_id
    assert legacy["episodes"][0]["id"] == source_beats_id
    assert migrated["mapping"]["brief"]["source"] == brief_id
    assert migrated["mapping"]["beats"]["1"]["source"] == source_beats_id
    assert repeated["created"] is False
    assert repeated["mapping"] == migrated["mapping"]


def test_migrated_free_project_can_update_settings_without_rewriting_legacy_authority(
    tmp_path,
):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个带旧 Brief 的项目",
        name="迁移前名称",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-settings",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    now = time.time()
    with repository._connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO creative_blueprints (
                project_id, candidate_group_id, version, status, content_json,
                source_refs_json, direction_type, generation_meta_json,
                resource_scope, created_at, updated_at
            ) VALUES (?, ?, 1, 'brief_locked', ?, '{}', 'original', '{}',
                      'sequential', ?, ?)
            """,
            (project["id"], uuid.uuid4().hex, json.dumps({"title": "旧 Brief"}), now, now),
        )
        legacy_brief_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE creative_projects SET locked_brief_id = ? WHERE id = ?",
            (legacy_brief_id, project["id"]),
        )
    service = FreeCreativeModeService(repository)
    migrated = service.migrate_to_free(
        project_id=project["id"], expected_mode_revision=0,
        operation_id="migration-settings-operation", actor_user_id="owner-1",
    )["project"]

    updated = repository.update_project(
        project["id"], {"name": "迁移后名称"},
        expected_revision=migrated["revision"],
    )

    assert updated["name"] == "迁移后名称"
    assert updated["workflow_mode"] == "free"
    assert updated["locked_brief_id"] == legacy_brief_id


def test_migration_is_blocked_by_active_review(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个等待审核的项目",
        name="审核中",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-project-2",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    now = time.time()
    with repository._connect() as conn:
        conn.execute(
            """
            INSERT INTO script_reviews (
                project_id, source_type, mode, episode_numbers_json,
                source_fingerprint, source_payload_json, status, created_by,
                created_at, updated_at
            ) VALUES (?, 'project', 'full', '[]', 'hash', '{}', 'running', 'owner-1', ?, ?)
            """,
            (project["id"], now, now),
        )
    service = FreeCreativeModeService(repository)

    preview = service.migration_preview(project["id"])
    assert preview["can_migrate"] is False
    with pytest.raises(ValueError, match="审核"):
        service.migrate_to_free(
            project_id=project["id"],
            expected_mode_revision=0,
            operation_id="migration-operation-2",
            actor_user_id="owner-1",
        )


def test_project_deletion_cleans_free_mode_authority_and_audit_rows(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = repository.create_original_project(
        seed_text="一个等待迁移后删除的项目",
        name="待删除项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="free-project-delete",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    service = FreeCreativeModeService(repository)
    migrated = service.migrate_to_free(
        project_id=project["id"],
        expected_mode_revision=0,
        operation_id="migration-before-delete",
        actor_user_id="owner-1",
    )
    brief = service.create_brief(
        project["id"], {"title": "待删除 Brief"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    service.create_generation_context(
        context_id="context-before-delete",
        project_id=project["id"], stage="characters", episode_number=None,
        instruction="", context_refs={"brief_id": brief["resource"]["id"]},
        actor_user_id="owner-1",
    )

    assert migrated["created"] is True
    repository.delete_project(project["id"], requested_by="owner-1")

    with repository._connect() as conn:
        for table in (
            "creative_resource_heads",
            "creative_generation_contexts",
            "creative_mode_migrations",
        ):
            count = conn.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE project_id = ?",
                (project["id"],),
            ).fetchone()["count"]
            assert count == 0


@pytest.mark.parametrize("stage", ["beats", "script"])
def test_free_episode_series_lock_locks_all_current_heads_atomically(
    tmp_path,
    stage: str,
):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    content = (
        {"beats": []}
        if stage == "beats"
        else {"episode_title": "", "scenes": []}
    )
    resources = {
        episode_number: service.create_episode_resource(
            project["id"],
            stage,
            episode_number,
            content,
            expected_head_revision=0,
            actor_user_id="owner-1",
        )
        for episode_number in range(1, 4)
    }
    service.lock_resource(
        project_id=project["id"],
        stage=stage,
        resource_ref=str(resources[2]["resource"]["id"]),
        expected_head_revision=1,
        actor_user_id="owner-1",
        episode_number=2,
    )

    result = service.lock_episode_series(
        project_id=project["id"],
        stage=stage,
        expected_head_revisions={1: 1, 2: 2, 3: 1},
        actor_user_id="owner-1",
    )

    assert result["locked_count"] == 2
    assert len(result["resources"]) == 3
    assert all(item["status"] == "locked" for item in result["resources"])
    heads = {
        int(item["episode_number"]): item
        for item in service.list_heads(project["id"])
        if item["stage"] == stage
    }
    assert {number: head["revision"] for number, head in heads.items()} == {
        1: 2,
        2: 2,
        3: 2,
    }


def test_free_episode_series_lock_rejects_missing_episode_without_partial_lock(
    tmp_path,
):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    first = service.create_episode_resource(
        project["id"],
        "beats",
        1,
        {"beats": []},
        expected_head_revision=0,
        actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="第 2 集尚未生成 Beats"):
        service.lock_episode_series(
            project_id=project["id"],
            stage="beats",
            expected_head_revisions={1: 1, 2: 0, 3: 0},
            actor_user_id="owner-1",
        )

    unchanged = repository.get_episode(first["resource"]["id"])
    assert unchanged is not None
    assert unchanged["status"] == "draft"
    assert service.get_head(project["id"], "beats", 1)["revision"] == 1


def test_free_episode_series_lock_rejects_stale_head_without_partial_lock(
    tmp_path,
):
    repository = CreativeRepository(tmp_path / "creative.sqlite3")
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    first = service.create_episode_resource(
        project["id"], "script", 1, {"episode_title": "一", "scenes": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.create_episode_resource(
        project["id"], "script", 2, {"episode_title": "二", "scenes": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.create_episode_resource(
        project["id"], "script", 3, {"episode_title": "三", "scenes": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.create_episode_resource(
        project["id"], "script", 2, {"episode_title": "二·新", "scenes": []},
        expected_head_revision=1, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="第 2 集场次剧本 Head 已发生变化"):
        service.lock_episode_series(
            project_id=project["id"],
            stage="script",
            expected_head_revisions={1: 1, 2: 1, 3: 1},
            actor_user_id="owner-1",
        )

    unchanged = repository.get_episode(first["resource"]["id"])
    assert unchanged is not None
    assert unchanged["status"] == "draft"
