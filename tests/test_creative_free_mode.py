# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Free-creative-mode service tests.

:class:`~creative.free_mode_service.FreeCreativeModeService` now talks to
:class:`~creative.postgres_repository.PostgresCreativeRepository` through
``connection_scope``/``transaction_scope`` and SQLAlchemy Core, so every service
scenario in this module runs against the ``postgres_creative`` fixture: an
isolated PostgreSQL schema with the creative tables migrated. The service used to
open the frozen SQLite store with ``repository._connect()``, and the two
remaining SQLite tests below cover the legacy repository's own workflow-mode
migration, which this service never touched.

Prerequisite rows that no repository method can create are written with
SQLAlchemy Core inserts through ``postgres_creative.database``, exactly like
``tests/test_postgres_creative_operations.py`` does.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, text, update

from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
    creative_resource_heads,
    creative_seeds,
    creative_topics,
    script_reviews,
)
from creative.free_mode_service import FreeCreativeModeService
from creative.legacy_sqlite_repository import (
    CreativeRepository as LegacyCreativeRepository,
)
from creative.outline_fields import outline_field_source_fingerprint
from creative.prompt_templates import PROMPT_TEMPLATE_VERSION
from creative.repository import CreativeRepository
from creative.repositories.base import utc_now


# ---------------------------------------------------------------------------
# frozen-legacy SQLite coverage (no service calls)
# ---------------------------------------------------------------------------


def create_legacy_workflow_mode_database(db_path) -> None:
    LegacyCreativeRepository(db_path)
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


def test_existing_projects_default_to_sequential_mode(tmp_path):
    repository = LegacyCreativeRepository(tmp_path / "creative.sqlite3")
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

    repository = LegacyCreativeRepository(db_path)

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


# ---------------------------------------------------------------------------
# PostgreSQL prerequisites
# ---------------------------------------------------------------------------


def create_free_project(repository) -> dict:
    project = repository.create_original_project(
        seed_text="一个能听见谎言的普通女孩",
        name="谎言回声",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="free-project-1",
        workflow_mode="free",
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def create_topic_with_locked_breakdown(
    repository, *, title: str = "旧库选题"
) -> tuple[int, int]:
    """Insert an adopted topic plus its locked breakdown card."""

    marker = uuid.uuid4().hex
    now = utc_now()
    with repository.database.transaction() as connection:
        topic_id = int(
            connection.execute(
                creative_topics.insert()
                .values(
                    platform="hg",
                    job_id=f"legacy-job-{marker}",
                    source_path=f"/legacy/{marker}.json",
                    captured_at="2026-09-09",
                    title=title,
                    category="都市",
                    description="旧库选题简介",
                    status="adopted",
                    score=80,
                    confidence="high",
                    score_details_json={},
                    recommendation="可立项",
                    evidence_json=[],
                    source_platforms_json=["hg"],
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_topics.c.id)
            ).scalar_one()
        )
        breakdown_id = int(
            connection.execute(
                creative_breakdowns.insert()
                .values(
                    topic_id=topic_id,
                    version=1,
                    status="locked",
                    content_json={"core_conflict": "谎言与真相"},
                    field_sources_json={},
                    risks_json=[],
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_breakdowns.c.id)
            ).scalar_one()
        )
    return topic_id, breakdown_id


def create_topic_project(
    repository,
    *,
    workflow_mode: str = "sequential",
    creation_request_id: str = "topic-project-1",
    target_episode_count: int = 3,
) -> dict:
    topic_id, breakdown_id = create_topic_with_locked_breakdown(repository)
    project = repository.create_project(
        topic_id=topic_id,
        breakdown_id=breakdown_id,
        name="选题项目",
        target_episode_count=target_episode_count,
        created_by="owner-1",
        creation_request_id=creation_request_id,
        workflow_mode=workflow_mode,
    )
    return repository.mark_project_bootstrap_status(project["id"], "ready")


def insert_sequential_brief(
    repository,
    project_id: int,
    *,
    content: dict[str, Any] | None = None,
    status: str = "brief_locked",
    link_to_project: bool = True,
) -> int:
    now = utc_now()
    with repository.database.transaction() as connection:
        brief_id = int(
            connection.execute(
                creative_blueprints.insert()
                .values(
                    project_id=int(project_id),
                    candidate_group_id=uuid.uuid4().hex,
                    version=1,
                    status=status,
                    content_json=content if content is not None else {"title": "旧 Brief"},
                    risk_score=0,
                    risk_level="low",
                    difference_note="",
                    source_refs_json={},
                    direction_type="original",
                    generation_meta_json={},
                    resource_scope="sequential",
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_blueprints.c.id)
            ).scalar_one()
        )
        if link_to_project:
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(
                    locked_brief_id=brief_id,
                    status="brief_locked",
                    updated_at=now,
                )
            )
    return brief_id


def insert_sequential_episode(
    repository,
    project_id: int,
    *,
    stage: str = "beats",
    episode_number: int = 1,
    content: dict[str, Any] | None = None,
    status: str = "draft",
    batch_id: str | None = None,
    version: int = 1,
) -> int:
    now = utc_now()
    with repository.database.transaction() as connection:
        return int(
            connection.execute(
                creative_episodes.insert()
                .values(
                    project_id=int(project_id),
                    stage=stage,
                    episode_number=int(episode_number),
                    batch_id=batch_id or uuid.uuid4().hex,
                    version=int(version),
                    status=status,
                    content_json=content if content is not None else {"beats": []},
                    upstream_refs_json={},
                    generation_meta_json={},
                    resource_scope="sequential",
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_episodes.c.id)
            ).scalar_one()
        )


def insert_running_review(repository, project_id: int) -> int:
    now = utc_now()
    with repository.database.transaction() as connection:
        return int(
            connection.execute(
                script_reviews.insert()
                .values(
                    project_id=int(project_id),
                    source_type="project",
                    mode="full",
                    episode_numbers_json=[],
                    source_fingerprint="hash",
                    source_payload_json={},
                    status="running",
                    created_by="owner-1",
                    created_at=now,
                    updated_at=now,
                )
                .returning(script_reviews.c.id)
            ).scalar_one()
        )


def count_project_rows(repository, table: str, project_id: int) -> int:
    with repository.database.connection() as connection:
        return int(
            connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE project_id = :project_id"),
                {"project_id": int(project_id)},
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# free-mode outline rewrites
# ---------------------------------------------------------------------------


def create_free_outline_for_rewrite(
    repository, *, locked: bool
) -> tuple[dict, dict, dict]:
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    created = service.create_outline(
        project["id"],
        [
            {
                "episode": number,
                "core_event": f"事件 {number}",
                "conflict": f"冲突 {number}",
                "character_progress": f"人物推进 {number}",
                "highlight": f"亮点 {number}",
                "reversal": f"反转 {number}",
                "cliffhanger": f"悬念 {number}",
            }
            for number in range(1, 4)
        ],
        series_arc="调查、受阻、反击",
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    if locked:
        service.lock_resource(
            project_id=project["id"],
            stage="outline",
            resource_ref=created["resource"]["batch_id"],
            expected_head_revision=created["head"]["revision"],
            actor_user_id="owner-1",
        )
    return (
        repository.get_project(project["id"]),
        repository.get_outline_batch(project["id"], created["resource"]["batch_id"]),
        service.get_head(project["id"], "outline"),
    )


def test_free_outline_field_rewrite_updates_only_current_draft_field(
    postgres_creative,
):
    repository = postgres_creative
    project, batch, head = create_free_outline_for_rewrite(
        repository, locked=False
    )
    source = batch["episodes"][0]
    original_conflict = source["content"]["conflict"]

    result = FreeCreativeModeService(repository).apply_outline_field_rewrite(
        project_id=project["id"],
        episode_id=source["id"],
        field="core_event",
        candidate_value="重写后的自由大纲核心事件",
        source_fingerprint=outline_field_source_fingerprint(source, "core_event"),
        expected_head_revision=head["revision"],
        actor_user_id="owner-1",
        operation_id="free-draft-rewrite",
    )

    assert result["created_new_version"] is False
    assert result["episode"]["content"]["core_event"] == "重写后的自由大纲核心事件"
    assert result["episode"]["content"]["conflict"] == original_conflict
    assert result["batch_id"] == batch["batch_id"]


def test_free_locked_outline_field_rewrite_creates_complete_new_head(
    postgres_creative,
):
    repository = postgres_creative
    project, locked, head = create_free_outline_for_rewrite(
        repository, locked=True
    )
    source = locked["episodes"][0]
    original = [dict(item["content"]) for item in locked["episodes"]]
    service = FreeCreativeModeService(repository)

    result = service.apply_outline_field_rewrite(
        project_id=project["id"],
        episode_id=source["id"],
        field="core_event",
        candidate_value="锁定版派生出的自由大纲核心事件",
        source_fingerprint=outline_field_source_fingerprint(source, "core_event"),
        expected_head_revision=head["revision"],
        actor_user_id="owner-1",
        operation_id="free-locked-rewrite",
    )

    assert result["created_new_version"] is True
    assert result["batch_id"] != locked["batch_id"]
    derived = repository.get_outline_batch(project["id"], result["batch_id"])
    assert len(derived["episodes"]) == len(locked["episodes"]) == 3
    assert derived["episodes"][0]["content"]["core_event"] == (
        "锁定版派生出的自由大纲核心事件"
    )
    assert derived["episodes"][1]["content"] == original[1]
    assert repository.get_outline_batch(
        project["id"], locked["batch_id"]
    )["episodes"][0]["content"] == original[0]
    assert service.get_head(project["id"], "outline")["resource_ref"] == result[
        "batch_id"
    ]


def test_free_outline_field_rewrite_rejects_stale_head_and_old_version(
    postgres_creative,
):
    repository = postgres_creative
    project, locked, head = create_free_outline_for_rewrite(
        repository, locked=True
    )
    source = locked["episodes"][0]
    service = FreeCreativeModeService(repository)
    fingerprint = outline_field_source_fingerprint(source, "core_event")

    service.apply_outline_field_rewrite(
        project_id=project["id"],
        episode_id=source["id"],
        field="core_event",
        candidate_value="先创建一个新 Head",
        source_fingerprint=fingerprint,
        expected_head_revision=head["revision"],
        actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="只能编辑当前自由创作大纲版本"):
        service.apply_outline_field_rewrite(
            project_id=project["id"],
            episode_id=source["id"],
            field="core_event",
            candidate_value="迟到请求不得创建分支",
            source_fingerprint=fingerprint,
            expected_head_revision=head["revision"],
            actor_user_id="owner-1",
        )


def test_free_draft_outline_field_rewrite_rejects_stale_head_revision(
    postgres_creative,
):
    repository = postgres_creative
    project, batch, head = create_free_outline_for_rewrite(
        repository, locked=False
    )
    source = batch["episodes"][0]
    service = FreeCreativeModeService(repository)
    service.set_head(
        project_id=project["id"],
        stage="outline",
        resource_ref=batch["batch_id"],
        expected_revision=head["revision"],
        actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="creative resource head changed"):
        service.apply_outline_field_rewrite(
            project_id=project["id"],
            episode_id=source["id"],
            field="core_event",
            candidate_value="旧请求不得覆盖当前草稿",
            source_fingerprint=outline_field_source_fingerprint(source, "core_event"),
            expected_head_revision=head["revision"],
            actor_user_id="owner-1",
        )


# ---------------------------------------------------------------------------
# seeds and mode switching
# ---------------------------------------------------------------------------


def test_topic_based_free_project_does_not_require_manual_seed(postgres_creative):
    repository = postgres_creative
    project = create_topic_project(repository, workflow_mode="free")

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


def test_topic_project_can_switch_to_free_without_manual_seed(postgres_creative):
    repository = postgres_creative
    project = create_topic_project(repository)
    assert repository.get_current_seed(project["id"]) is None

    switched = FreeCreativeModeService(repository).switch_empty_mode(
        project_id=project["id"],
        workflow_mode="free",
        expected_mode_revision=0,
        actor_user_id="owner-1",
    )

    assert switched["workflow_mode"] == "free"
    assert "旧库选题" in repository.get_current_seed(project["id"])["seed_text"]


def test_content_project_migrates_to_free_without_manual_seed(postgres_creative):
    repository = postgres_creative
    project = create_topic_project(repository)
    repository.create_blueprints(
        project["id"], [{"content": {"title": "已有方向"}}]
    )

    migrated = FreeCreativeModeService(repository).migrate_to_free(
        project_id=project["id"],
        expected_mode_revision=0,
        operation_id="migrate-topic-project-without-seed",
        actor_user_id="owner-1",
    )

    source_project = repository.get_project(project["id"])
    free_project = migrated["project"]
    assert source_project["workflow_mode"] == "sequential"
    assert free_project["id"] != source_project["id"]
    assert free_project["workflow_mode"] == "free"
    assert repository.get_current_seed(source_project["id"]) is None
    assert "旧库选题" in repository.get_current_seed(free_project["id"])["seed_text"]


def test_empty_project_can_switch_mode_but_content_project_cannot(
    postgres_creative,
):
    repository = postgres_creative
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


def test_ensure_seed_freezes_a_seed_once(postgres_creative):
    repository = postgres_creative
    service = FreeCreativeModeService(repository)

    # A topic project starts without a seed version: the seed text is derived
    # from the adopted topic and its locked breakdown card.
    topic_project = create_topic_project(repository)
    assert repository.get_current_seed(topic_project["id"]) is None
    from_topic = service.ensure_seed(topic_project["id"])
    assert from_topic["seed_text"] == (
        "选题：旧库选题\n简介：旧库选题简介\n核心冲突：谎言与真相"
    )
    assert from_topic["status"] == "frozen"
    assert from_topic["version"] == 1
    assert from_topic["constraints"] == {}
    assert from_topic["reference_refs"] == []

    # Without a topic the project name is the fallback, and a second call is a
    # no-op that returns the same frozen seed.
    project = repository.create_original_project(
        seed_text="一个还没有种子版本的项目",
        name="无种子项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="ensure-seed-project",
    )
    with repository.transaction_scope() as connection:
        connection.execute(
            delete(creative_seeds).where(
                creative_seeds.c.project_id == int(project["id"])
            )
        )
    assert repository.get_current_seed(project["id"]) is None

    first = service.ensure_seed(project["id"])
    second = service.ensure_seed(project["id"], seed_text="被忽略的新种子")

    assert first["id"] == second["id"]
    assert first["seed_text"] == "项目：无种子项目"


# ---------------------------------------------------------------------------
# resource creation and authority
# ---------------------------------------------------------------------------


def test_free_resources_do_not_update_sequential_authority(postgres_creative):
    repository = postgres_creative
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


def test_create_briefs_saves_every_direction_and_selects_the_first(
    postgres_creative,
):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    result = service.create_briefs(
        project["id"],
        [{"title": "方向一"}, {"title": "方向二"}, {"title": "方向三"}],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )

    assert [item["content"]["title"] for item in result["resources"]] == [
        "方向一",
        "方向二",
        "方向三",
    ]
    assert [item["version"] for item in result["resources"]] == [1, 1, 1]
    assert result["head"]["resource_ref"] == str(result["resources"][0]["id"])
    assert result["head"]["revision"] == 1

    with pytest.raises(ValueError, match="1-5"):
        service.create_briefs(
            project["id"], [], expected_head_revision=1, actor_user_id="owner-1"
        )


def test_free_head_uses_per_module_compare_and_swap(postgres_creative):
    repository = postgres_creative
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


def test_free_brief_versions_follow_the_current_head(postgres_creative):
    repository = postgres_creative
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


def test_locked_character_bundle_can_create_a_draft_revision(postgres_creative):
    repository = postgres_creative
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


def test_locked_outline_bundle_can_create_a_draft_revision(postgres_creative):
    repository = postgres_creative
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
    postgres_creative, stage, content,
):
    repository = postgres_creative
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


def test_lock_resource_rejects_incomplete_or_unknown_resources(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    # A batch with only two of the three required episodes cannot be locked.
    batch_id = uuid.uuid4().hex
    for number in (1, 2):
        insert_sequential_episode(
            repository,
            project["id"],
            stage="outline",
            episode_number=number,
            content={"episode": number},
            batch_id=batch_id,
        )
    with repository.transaction_scope() as connection:
        connection.execute(
            update(creative_episodes)
            .where(
                creative_episodes.c.project_id == int(project["id"]),
                creative_episodes.c.batch_id == batch_id,
            )
            .values(resource_scope="free")
        )

    with pytest.raises(ValueError, match="分集大纲不完整"):
        service.lock_resource(
            project_id=project["id"],
            stage="outline",
            resource_ref=batch_id,
            expected_head_revision=0,
            actor_user_id="owner-1",
        )
    with pytest.raises(ValueError, match="不存在或不属于当前模块"):
        service.lock_resource(
            project_id=project["id"],
            stage="brief",
            resource_ref="999999",
            expected_head_revision=0,
            actor_user_id="owner-1",
        )


def test_set_head_rejects_a_reference_from_another_module(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project["id"], {"title": "主 Brief"},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="不存在或不属于当前模块"):
        service.set_head(
            project_id=project["id"],
            stage="beats",
            episode_number=1,
            resource_ref=str(brief["resource"]["id"]),
            expected_revision=0,
            actor_user_id="owner-1",
        )
    with pytest.raises(ValueError, match="unsupported creative stage"):
        service.set_head(
            project_id=project["id"],
            stage="unknown",
            resource_ref=str(brief["resource"]["id"]),
            expected_revision=0,
            actor_user_id="owner-1",
        )


# ---------------------------------------------------------------------------
# heads
# ---------------------------------------------------------------------------


def test_list_heads_and_get_head_read_only_the_free_scope(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    service.create_brief(
        project["id"], {"title": "自由 Brief"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    heads = service.list_heads(project["id"])
    assert [(item["stage"], item["episode_number"]) for item in heads] == [
        ("beats", 1),
        ("brief", 0),
    ]
    brief_head = service.get_head(project["id"], "brief")
    assert brief_head["resource_scope"] == "free"
    assert brief_head["revision"] == 1
    assert brief_head["episode_number"] == 0
    assert service.get_head(project["id"], "script", 1) is None
    with pytest.raises(ValueError, match="unsupported creative stage"):
        service.get_head(project["id"], "storyboard")

    # A sequential head is invisible to the free-mode lookup, and a sequential
    # project cannot list free heads at all.
    sequential = repository.create_original_project(
        seed_text="标准流程项目",
        name="标准流程项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="sequential-head-project",
    )
    sequential = repository.mark_project_bootstrap_status(sequential["id"], "ready")
    brief_id = insert_sequential_brief(repository, sequential["id"])
    with repository.transaction_scope() as connection:
        connection.execute(
            creative_resource_heads.insert().values(
                project_id=int(sequential["id"]),
                resource_scope="sequential",
                stage="brief",
                episode_number=0,
                resource_ref=str(brief_id),
                resource_version=1,
                revision=3,
                updated_at=utc_now(),
                updated_by="owner-1",
            )
        )
    assert service.get_head(sequential["id"], "brief") is None
    with pytest.raises(ValueError, match="自由创作项目"):
        service.list_heads(sequential["id"])


# ---------------------------------------------------------------------------
# item updates
# ---------------------------------------------------------------------------


def test_update_resource_rewrites_only_the_mutable_draft(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project["id"], {"title": "初稿"},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    updated = service.update_resource(
        project_id=project["id"],
        stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        content={"title": "改后"},
    )
    assert updated["content"] == {"title": "改后"}
    assert updated["updated_at"] > brief["resource"]["updated_at"]

    beats = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    assert service.update_resource(
        project_id=project["id"],
        stage="beats",
        resource_ref=str(beats["resource"]["id"]),
        content={"beats": [{"beat": "开场"}]},
    )["content"] == {"beats": [{"beat": "开场"}]}

    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    with pytest.raises(ValueError, match="不存在或已锁定"):
        service.update_resource(
            project_id=project["id"],
            stage="brief",
            resource_ref=str(brief["resource"]["id"]),
            content={"title": "锁定后不得修改"},
        )
    with pytest.raises(ValueError, match="批次保存接口"):
        service.update_resource(
            project_id=project["id"],
            stage="outline",
            resource_ref="1",
            content={},
        )


def test_update_item_covers_every_free_item_type(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project["id"], {"title": "初稿"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    characters = service.create_characters(
        project["id"],
        [{"character_key": "hero", "content": {"name": "林真"}}],
        [{"relationship_key": "hero-mother", "content": {"relation": "母女"}}],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    episode = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    character_id = characters["resource"]["characters"][0]["id"]
    relationship_id = characters["resource"]["relationships"][0]["id"]

    assert service.update_item(
        project_id=project["id"], item_type="brief",
        item_id=brief["resource"]["id"], content={"title": "改后"},
    )["content"] == {"title": "改后"}
    assert service.update_item(
        project_id=project["id"], item_type="character",
        item_id=character_id, content={"name": "林真改"},
    )["content"] == {"name": "林真改"}
    assert service.update_item(
        project_id=project["id"], item_type="relationship",
        item_id=relationship_id, content={"relation": "姐妹"},
    )["content"] == {"relation": "姐妹"}
    assert service.update_item(
        project_id=project["id"], item_type="episode",
        item_id=episode["resource"]["id"], content={"beats": [{"beat": "改"}]},
    )["content"] == {"beats": [{"beat": "改"}]}

    with pytest.raises(ValueError, match="unsupported free creative item type"):
        service.update_item(
            project_id=project["id"], item_type="outline",
            item_id=1, content={},
        )
    with pytest.raises(ValueError, match="自由创作资源不存在"):
        service.update_item(
            project_id=project["id"], item_type="character",
            item_id=999999, content={},
        )

    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    with pytest.raises(ValueError, match="已锁定的自由创作资源不能修改"):
        service.update_item(
            project_id=project["id"], item_type="brief",
            item_id=brief["resource"]["id"], content={"title": "锁定后"},
        )


# ---------------------------------------------------------------------------
# generation contexts
# ---------------------------------------------------------------------------


def test_context_only_accepts_locked_same_project_resources(postgres_creative):
    repository = postgres_creative
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
    assert context["prompt_schema_version"] == PROMPT_TEMPLATE_VERSION


def test_generation_context_is_idempotent_and_readable(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    first = service.create_generation_context(
        context_id="context-idempotent",
        project_id=project["id"],
        stage="beats",
        episode_number=1,
        instruction="第一集",
        context_refs={},
        actor_user_id="owner-1",
    )
    repeated = service.create_generation_context(
        context_id="context-idempotent",
        project_id=project["id"],
        stage="beats",
        episode_number=1,
        instruction="第一集",
        context_refs={},
        actor_user_id="owner-1",
    )
    stored = service.get_generation_context("context-idempotent")

    assert repeated["id"] == first["id"]
    assert stored["context_hash"] == first["context_hash"]
    assert stored["context_snapshot"] == first["context_snapshot"]
    assert stored["created_at"] == first["created_at"]
    with pytest.raises(ValueError, match="自由创作生成上下文不存在"):
        service.get_generation_context("context-missing")
    with pytest.raises(ValueError, match="不支持引用"):
        service.create_generation_context(
            context_id="context-invalid-ref",
            project_id=project["id"],
            stage="brief",
            episode_number=None,
            instruction="",
            context_refs={"outline_batch_id": "batch"},
            actor_user_id="owner-1",
        )


def test_generation_context_id_cannot_move_to_another_episode(postgres_creative):
    repository = postgres_creative
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


def test_generation_context_rejects_a_partially_unlocked_character_bundle(
    postgres_creative,
):
    repository = postgres_creative
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
    with repository.transaction_scope() as connection:
        connection.execute(
            update(creative_relationships)
            .where(
                creative_relationships.c.project_id == int(project["id"]),
                creative_relationships.c.batch_id == created["resource"]["batch_id"],
            )
            .values(status="draft")
        )

    with pytest.raises(ValueError, match="已锁定"):
        service.create_generation_context(
            context_id="context-partial-characters",
            project_id=project["id"], stage="outline", episode_number=None,
            instruction="",
            context_refs={"character_batch_id": created["resource"]["batch_id"]},
            actor_user_id="owner-1",
        )


def test_context_options_lists_only_locked_free_resources(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    service.create_brief(
        project["id"], {"title": "草稿方向"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    assert service.context_options(project["id"])["briefs"] == []

    brief = service.create_brief(
        project["id"], {"title": "锁定方向"},
        expected_head_revision=1, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    characters = service.create_characters(
        project["id"],
        [{"character_key": "hero", "content": {"name": "林真"}}],
        [],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=characters["resource"]["batch_id"],
        expected_head_revision=characters["head"]["revision"],
        actor_user_id="owner-1",
    )
    outline = service.create_outline(
        project["id"],
        [{"episode": number, "core_event": f"事件 {number}"} for number in range(1, 4)],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="outline",
        resource_ref=outline["resource"]["batch_id"],
        expected_head_revision=outline["head"]["revision"],
        actor_user_id="owner-1",
    )
    beats = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="beats", episode_number=1,
        resource_ref=str(beats["resource"]["id"]),
        expected_head_revision=beats["head"]["revision"],
        actor_user_id="owner-1",
    )

    options = service.context_options(project["id"], 1)

    assert [item["id"] for item in options["briefs"]] == [brief["resource"]["id"]]
    assert [item["batch_id"] for item in options["character_batches"]] == [
        characters["resource"]["batch_id"]
    ]
    assert [item["batch_id"] for item in options["outline_batches"]] == [
        outline["resource"]["batch_id"]
    ]
    assert [item["id"] for item in options["beats"]] == [beats["resource"]["id"]]
    assert options["beats"][0]["episode_number"] == 1
    assert service.context_options(project["id"], 2)["beats"] == []
    assert len(options["heads"]) == 4


# ---------------------------------------------------------------------------
# episode series locking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["beats", "script"])
def test_free_episode_series_lock_locks_all_current_heads_atomically(
    postgres_creative,
    stage: str,
):
    repository = postgres_creative
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
    postgres_creative,
):
    repository = postgres_creative
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
    postgres_creative,
):
    repository = postgres_creative
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


# ---------------------------------------------------------------------------
# counts and migration
# ---------------------------------------------------------------------------


def test_has_creative_resources_counts_every_resource_table(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    assert service.has_creative_resources(project["id"]) is False
    service.create_episode_resource(
        project["id"], "beats", 1, {"beats": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    assert service.has_creative_resources(project["id"]) is True
    assert service.has_creative_resources(999999) is False


def test_migration_copies_current_resources_and_preserves_legacy_history(
    postgres_creative,
):
    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="一个带着旧版本的项目",
        name="迁移项目",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-project-1",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    brief_id = insert_sequential_brief(
        repository, project["id"], content={"title": "旧 Brief"}
    )
    source_beats_id = insert_sequential_episode(
        repository, project["id"], stage="beats", episode_number=1
    )
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
    source_project = repository.get_project(project["id"])
    assert free_project["workflow_mode"] == "free"
    assert free_project["id"] != source_project["id"]
    assert free_project["locked_brief_id"] is None
    assert all(item["resource_scope"] == "free" for item in free_project["blueprints"])
    assert all(item["resource_scope"] == "free" for item in free_project["episodes"])
    assert source_project["workflow_mode"] == "sequential"
    assert source_project["locked_brief_id"] == brief_id
    assert source_project["blueprints"][0]["id"] == brief_id
    assert source_project["episodes"][0]["id"] == source_beats_id
    assert migrated["mapping"]["project"] == {
        "source": source_project["id"],
        "target": free_project["id"],
    }
    assert migrated["mapping"]["brief"]["source"] == brief_id
    assert migrated["mapping"]["beats"]["1"]["source"] == source_beats_id
    assert repeated["created"] is False
    assert repeated["project"]["id"] == free_project["id"]
    assert repeated["mapping"] == migrated["mapping"]
    # The copy keeps the migrated seed and the original stays untouched.
    assert free_project["seed"]["seed_text"] == "一个带着旧版本的项目"


def test_migrated_free_project_can_update_settings_without_rewriting_legacy_authority(
    postgres_creative,
):
    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="一个带旧 Brief 的项目",
        name="迁移前名称",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-settings",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    legacy_brief_id = insert_sequential_brief(repository, project["id"])
    service = FreeCreativeModeService(repository)
    migrated = service.migrate_to_free(
        project_id=project["id"], expected_mode_revision=0,
        operation_id="migration-settings-operation", actor_user_id="owner-1",
    )["project"]

    updated = repository.update_project(
        migrated["id"], {"name": "迁移后名称"},
        expected_revision=migrated["revision"],
    )

    assert updated["name"] == "迁移后名称"
    assert updated["workflow_mode"] == "free"
    assert updated["locked_brief_id"] is None
    source_project = repository.get_project(project["id"])
    assert source_project["name"] == "迁移前名称"
    assert source_project["workflow_mode"] == "sequential"
    assert source_project["locked_brief_id"] == legacy_brief_id


def test_migration_is_blocked_by_active_review(postgres_creative):
    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="一个等待审核的项目",
        name="审核中",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="migration-project-2",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    insert_running_review(repository, project["id"])
    service = FreeCreativeModeService(repository)

    preview = service.migration_preview(project["id"])
    assert preview["can_migrate"] is False
    assert any("审核" in item for item in preview["blockers"])
    assert preview["resource_counts"] == {
        "brief": 0,
        "characters": 0,
        "outline": 0,
        "beats": 0,
        "script": 0,
    }
    assert preview["has_seed"] is True
    with pytest.raises(ValueError, match="审核"):
        service.migrate_to_free(
            project_id=project["id"],
            expected_mode_revision=0,
            operation_id="migration-operation-2",
            actor_user_id="owner-1",
        )


def test_project_deletion_cleans_free_mode_authority_and_audit_rows(
    postgres_creative,
):
    repository = postgres_creative
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
    free_project_id = migrated["project"]["id"]
    brief = service.create_brief(
        free_project_id, {"title": "待删除 Brief"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=free_project_id, stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    service.create_generation_context(
        context_id="context-before-delete",
        project_id=free_project_id, stage="characters", episode_number=None,
        instruction="", context_refs={"brief_id": brief["resource"]["id"]},
        actor_user_id="owner-1",
    )

    assert migrated["created"] is True
    repository.delete_project(free_project_id, requested_by="owner-1")
    assert repository.get_project(project["id"])["workflow_mode"] == "sequential"

    for table in ("creative_resource_heads", "creative_generation_contexts"):
        assert count_project_rows(repository, table, free_project_id) == 0


def test_list_legacy_resources_returns_sequential_rows(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)
    brief_id = insert_sequential_brief(
        repository, project["id"], content={"title": "旧 Brief"}, link_to_project=False
    )
    episode_id = insert_sequential_episode(
        repository, project["id"], stage="beats", episode_number=1,
        content={"beats": []},
    )
    service.create_brief(
        project["id"], {"title": "自由 Brief"},
        expected_head_revision=0, actor_user_id="owner-1",
    )

    rows = service.list_legacy_resources(project["id"])

    assert [item["id"] for item in rows["blueprints"]] == [brief_id]
    assert rows["blueprints"][0]["content"] == {"title": "旧 Brief"}
    assert [item["id"] for item in rows["episodes"]] == [episode_id]
    assert rows["episodes"][0]["content"] == {"beats": []}
    assert rows["characters"] == []
    assert rows["relationships"] == []


# ---------------------------------------------------------------------------
# the live API path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_project_preview_ignores_started_task_without_resources(
    tmp_path, postgres_creative,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from creative.api import build_creative_router
    from creative.schema import CreativeModeMigrationRequest

    repository = postgres_creative
    project = repository.create_original_project(
        seed_text="尚未开始生成的项目",
        name="空项目",
        target_episode_count=3,
        created_by="owner-1",
        creation_request_id="empty-preview-project",
    )
    project = repository.mark_project_bootstrap_status(project["id"], "ready")
    workflow = Mock()
    workflow.copy_active_project_members.return_value = []
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

    migrate_endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/creative/projects/{project_id}/migrate-to-free"
    )
    migrated = await migrate_endpoint(
        project_id=project["id"],
        payload=CreativeModeMigrationRequest(
            expected_mode_revision=0,
            operation_id="empty-project-free-copy",
        ),
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        principal=SimpleNamespace(is_system_admin=True, user_id="owner-1"),
    )
    assert repository.get_project(project["id"])["workflow_mode"] == "sequential"
    assert migrated["project"]["id"] != project["id"]
    assert migrated["project"]["workflow_mode"] == "free"
    workflow.cancel_task.assert_not_called()


@pytest.mark.asyncio
async def test_content_project_migration_creates_free_copy_and_preserves_source(
    tmp_path, postgres_creative,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from creative.api import build_creative_router
    from creative.schema import CreativeModeMigrationRequest

    repository = postgres_creative
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
    workflow.copy_active_project_members.return_value = []
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
        route.endpoint for route in router.routes
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

    source_project = repository.get_project(project["id"])
    free_project = migrated["project"]
    project_mapping = migrated["mapping"]["project"]
    brief_mapping = migrated["mapping"]["brief"]
    assert source_project["workflow_mode"] == "sequential"
    assert source_project["mode_revision"] == 0
    assert free_project["id"] != source_project["id"]
    assert free_project["workflow_mode"] == "free"
    assert free_project["name"] == "非空迁移项目（自由副本）"
    assert project_mapping == {
        "source": source_project["id"],
        "target": free_project["id"],
    }
    assert brief_mapping["source"] == blueprint["id"]
    assert brief_mapping["target"] != blueprint["id"]
    assert repository.get_blueprint(blueprint["id"])["resource_scope"] == "sequential"
    copied_brief = repository.get_blueprint(brief_mapping["target"])
    assert copied_brief["project_id"] == free_project["id"]
    assert copied_brief["resource_scope"] == "free"
    workflow.cancel_task.assert_not_called()


# ---------------------------------------------------------------------------
# one end-to-end pass over every ported entry point
# ---------------------------------------------------------------------------


def test_free_mode_end_to_end_flow_on_postgresql(postgres_creative):
    repository = postgres_creative
    project = create_free_project(repository)
    service = FreeCreativeModeService(repository)

    brief = service.create_briefs(
        project["id"],
        [{"title": "方向一"}, {"title": "方向二"}],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resources"][0]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    characters = service.create_characters(
        project["id"],
        [{"character_key": "hero", "content": {"name": "林真"}}],
        [{"relationship_key": "hero-mother", "content": {"relation": "母女"}}],
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=characters["resource"]["batch_id"],
        expected_head_revision=characters["head"]["revision"],
        actor_user_id="owner-1",
    )
    outline = service.create_outline(
        project["id"],
        [{"episode": number, "core_event": f"事件 {number}"} for number in range(1, 4)],
        series_arc="调查、受阻、反击",
        expected_head_revision=0,
        actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="outline",
        resource_ref=outline["resource"]["batch_id"],
        expected_head_revision=outline["head"]["revision"],
        actor_user_id="owner-1",
    )
    beats = service.create_episode_resource(
        project["id"], "beats", 1, {"beats": [{"beat": "开场"}]},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="beats", episode_number=1,
        resource_ref=str(beats["resource"]["id"]),
        expected_head_revision=beats["head"]["revision"],
        actor_user_id="owner-1",
    )
    context = service.create_generation_context(
        context_id="end-to-end-context",
        project_id=project["id"],
        stage="script",
        episode_number=1,
        instruction="写第一集",
        context_refs={
            "brief_id": brief["resources"][0]["id"],
            "character_batch_id": characters["resource"]["batch_id"],
            "outline_batch_id": outline["resource"]["batch_id"],
            "beats_id": beats["resource"]["id"],
        },
        actor_user_id="owner-1",
    )
    assert set(context["context_manifest"]) == {
        "brief",
        "characters",
        "outline",
        "beats",
    }
    script = service.create_episode_resource(
        project["id"], "script", 1,
        {"episode_title": "第一集", "scenes": []},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.update_resource(
        project_id=project["id"], stage="script",
        resource_ref=str(script["resource"]["id"]),
        content={"episode_title": "第一集·改", "scenes": []},
    )
    service.update_item(
        project_id=project["id"], item_type="brief",
        item_id=brief["resources"][1]["id"],
        content={"title": "方向二·改"},
    )

    heads = {(item["stage"], item["episode_number"]) for item in service.list_heads(project["id"])}
    assert heads == {
        ("beats", 1),
        ("brief", 0),
        ("characters", 0),
        ("outline", 0),
        ("script", 1),
    }
    assert service.get_head(project["id"], "script", 1)["resource_ref"] == str(
        script["resource"]["id"]
    )
    assert service.has_creative_resources(project["id"]) is True
    options = service.context_options(project["id"], 1)
    assert options["briefs"] and options["character_batches"]
    assert options["outline_batches"]
    assert options["beats"]
    assert len(options["heads"]) == 5
    assert service.list_legacy_resources(project["id"])["blueprints"] == []
    # The migrated-copy preview stays blocked because the project is already free.
    assert service.migration_preview(project["id"])["can_migrate"] is False
