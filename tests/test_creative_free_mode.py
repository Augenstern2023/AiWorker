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
import time
import uuid

from creative.free_mode_service import FreeCreativeModeService
from creative.repository import CreativeRepository


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
