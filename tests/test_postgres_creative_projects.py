# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL coverage for the projects/seeds/workflow-identity mixin.

Every test runs against the shared ``postgres_creative`` fixture, which is the
composed repository bound to a fresh isolated PostgreSQL schema.  Prerequisite
rows (topics, breakdowns, blueprints, characters, episodes) are inserted with
raw SQL through ``isolated_system_database.transaction()`` so this module never
depends on another domain repository.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_seeds,
    creative_topics,
)
from creative.repositories.base import utc_now
from creative.script_service import normalize_script_content
from system_database import SystemDatabase


# ---------------------------------------------------------------------------
# raw-SQL prerequisites
# ---------------------------------------------------------------------------


def _create_topic(
    database: SystemDatabase,
    *,
    title: str,
    status: str = "adopted",
    category: str = "都市",
    description: str = "测试简介",
    platform: str = "hg",
    source_platforms: list[str] | None = None,
) -> int:
    marker = uuid.uuid4().hex
    with database.transaction() as connection:
        return int(
            connection.execute(
                creative_topics.insert()
                .values(
                    platform=platform,
                    job_id=f"job-{marker}",
                    source_path=f"/ranks/{marker}",
                    captured_at="2026-01-01T00:00:00Z",
                    title=title,
                    category=category,
                    description=description,
                    status=status,
                    score=0.9,
                    confidence="high",
                    score_details_json={"imitability": 0.5, "production_cost": 0.5},
                    recommendation="adopt",
                    evidence_json=[{"source": "test"}],
                    source_platforms_json=source_platforms or [platform],
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(creative_topics.c.id)
            ).scalar_one()
        )


def _create_breakdown(
    database: SystemDatabase,
    topic_id: int,
    *,
    version: int = 1,
    status: str = "locked",
    content: dict[str, Any] | None = None,
    workflow_task_id: str | None = None,
) -> int:
    with database.transaction() as connection:
        return int(
            connection.execute(
                creative_breakdowns.insert()
                .values(
                    topic_id=int(topic_id),
                    version=version,
                    status=status,
                    content_json=(
                        content
                        if content is not None
                        else {
                            "target_audience": " 18-30岁女性 ",
                            "core_conflict": "身份互换",
                        }
                    ),
                    field_sources_json={},
                    risks_json=[],
                    workflow_task_id=workflow_task_id,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(creative_breakdowns.c.id)
            ).scalar_one()
        )


def _create_blueprint(
    database: SystemDatabase,
    project_id: int,
    *,
    candidate_group_id: str = "group-a",
    version: int = 1,
    status: str = "draft",
    parent_version_id: int | None = None,
    workflow_task_id: str | None = None,
    resource_scope: str = "sequential",
) -> int:
    with database.transaction() as connection:
        return int(
            connection.execute(
                creative_blueprints.insert()
                .values(
                    project_id=int(project_id),
                    candidate_group_id=candidate_group_id,
                    version=version,
                    parent_version_id=parent_version_id,
                    status=status,
                    content_json={"premise": "测试"},
                    workflow_task_id=workflow_task_id,
                    resource_scope=resource_scope,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(creative_blueprints.c.id)
            ).scalar_one()
        )


def _create_character(
    database: SystemDatabase,
    project_id: int,
    *,
    status: str = "draft",
    content: dict[str, Any] | None = None,
    resource_scope: str = "sequential",
) -> int:
    with database.transaction() as connection:
        return int(
            connection.execute(
                creative_characters.insert()
                .values(
                    project_id=int(project_id),
                    batch_id="batch-1",
                    version=1,
                    character_key=f"hero-{status}-{resource_scope}",
                    status=status,
                    content_json=content if content is not None else {"name": "甲"},
                    resource_scope=resource_scope,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(creative_characters.c.id)
            ).scalar_one()
        )


def _create_episode(
    database: SystemDatabase,
    project_id: int,
    *,
    stage: str = "outline",
    status: str = "draft",
    workflow_task_id: str | None = None,
    resource_scope: str = "sequential",
) -> int:
    with database.transaction() as connection:
        return int(
            connection.execute(
                creative_episodes.insert()
                .values(
                    project_id=int(project_id),
                    stage=stage,
                    episode_number=1,
                    batch_id="batch-1",
                    version=1,
                    status=status,
                    content_json={"beats": []},
                    workflow_task_id=workflow_task_id,
                    resource_scope=resource_scope,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(creative_episodes.c.id)
            ).scalar_one()
        )


def _raw_project(database: SystemDatabase, project_id: int) -> dict[str, Any]:
    with database.connection() as connection:
        return dict(
            connection.execute(
                select(creative_projects).where(
                    creative_projects.c.id == int(project_id)
                )
            ).mappings().one()
        )


def _project_ids_for_request(
    database: SystemDatabase, created_by: str, creation_request_id: str
) -> list[int]:
    with database.connection() as connection:
        return [
            int(value)
            for value in connection.execute(
                select(creative_projects.c.id).where(
                    creative_projects.c.created_by == created_by,
                    creative_projects.c.creation_request_id == creation_request_id,
                )
            ).scalars()
        ]


def _concurrently(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run calls in real threads so they take separate pooled connections."""

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result() for future in futures]


@pytest.fixture
def seeded_topic(isolated_system_database: SystemDatabase) -> dict[str, int]:
    topic_id = _create_topic(isolated_system_database, title="都市重生")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)
    return {"topic_id": topic_id, "breakdown_id": breakdown_id}


# ---------------------------------------------------------------------------
# project creation
# ---------------------------------------------------------------------------


def test_create_project_imitation_mode_matches_the_legacy_shape(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    project = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="tester",
        creation_request_id="req-imitation",
    )

    assert project["name"] == "都市重生创作项目"
    assert project["genre"] == "都市"
    assert project["target_platform"] == ""
    assert project["target_audience"] == "18-30岁女性"
    assert project["target_episode_count"] == 20
    assert project["episode_duration_seconds"] == 120
    assert project["status"] == "created"
    assert project["creation_mode"] == "imitation"
    assert project["bootstrap_status"] == "ready"
    assert project["revision"] == 0
    assert project["mode_revision"] == 0
    assert project["created_by"] == "tester"
    assert project["deleted"] is False
    assert project["workflow_mode"] == "sequential"
    assert project["creation_request_id"] == "req-imitation"
    assert project["creation_workflow_task_id"] is None
    assert project["workflow_task_id"] is None
    assert project["topic_id"] == seeded_topic["topic_id"]
    assert project["breakdown_id"] == seeded_topic["breakdown_id"]
    assert project["topic_title"] == "都市重生"
    assert project["topic_description"] == "测试简介"
    assert project["breakdown_version"] == 1
    assert project["source_platforms"] == ["hg"]
    assert "source_platforms_json" not in project
    assert isinstance(project["created_at"], float)
    assert isinstance(project["updated_at"], float)
    assert project["mode_changed_at"] is None
    assert project["blueprints"] == []
    assert project["characters"] == []
    assert project["relationships"] == []
    assert project["episodes"] == []
    assert project["resource_heads"] == []
    assert project["allowed_actions"] == ["generate_blueprints"]
    # The legacy read only decorates original projects with topic/breakdown/seed.
    assert "topic" not in project
    assert "seed" not in project

    stored = _raw_project(postgres_creative.database, project["id"])
    assert stored["deleted"] is False
    assert stored["created_at"].tzinfo is not None


def test_get_project_returns_none_for_unknown_and_soft_deleted_rows(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    assert postgres_creative.get_project(987654321) is None

    project = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="tester",
        creation_request_id="req-soft-delete-read",
    )
    with postgres_creative.database.transaction() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == project["id"])
            .values(deleted=True)
        )

    assert postgres_creative.get_project(project["id"]) is None


def test_create_project_validates_episode_count_and_duration(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    with pytest.raises(ValueError, match="目标集数必须在 1-100 之间"):
        postgres_creative.create_project(
            seeded_topic["topic_id"], seeded_topic["breakdown_id"],
            target_episode_count=0,
        )
    with pytest.raises(ValueError, match="目标集数必须在 1-100 之间"):
        postgres_creative.create_project(
            seeded_topic["topic_id"], seeded_topic["breakdown_id"],
            target_episode_count=101,
        )
    with pytest.raises(ValueError, match="单集时长必须在 30-1800 秒之间"):
        postgres_creative.create_project(
            seeded_topic["topic_id"], seeded_topic["breakdown_id"],
            episode_duration_seconds=10,
        )
    with pytest.raises(ValueError, match="invalid creative workflow mode"):
        postgres_creative.create_project(
            seeded_topic["topic_id"], seeded_topic["breakdown_id"],
            workflow_mode="strict",
        )


def test_create_project_requires_adopted_topic_and_locked_same_title_breakdown(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    pending_topic = _create_topic(
        isolated_system_database, title="待审选题", status="pending_review"
    )
    pending_breakdown = _create_breakdown(
        isolated_system_database, pending_topic, status="locked"
    )
    with pytest.raises(ValueError, match="只有已采纳选题可以创建创作项目"):
        postgres_creative.create_project(pending_topic, pending_breakdown)

    other_topic = _create_topic(isolated_system_database, title="其他选题")
    adopted_topic = _create_topic(isolated_system_database, title="同名选题")
    other_breakdown = _create_breakdown(
        isolated_system_database, other_topic, status="locked"
    )
    draft_breakdown = _create_breakdown(
        isolated_system_database, adopted_topic, status="draft"
    )

    with pytest.raises(ValueError, match="创建项目需要该选题的已锁定或已批准拆解卡"):
        postgres_creative.create_project(adopted_topic, other_breakdown)
    with pytest.raises(ValueError, match="创建项目需要该选题的已锁定或已批准拆解卡"):
        postgres_creative.create_project(adopted_topic, draft_breakdown)
    with pytest.raises(ValueError, match="创建项目需要该选题的已锁定或已批准拆解卡"):
        postgres_creative.create_project(adopted_topic, 987654321)
    with pytest.raises(ValueError, match="选题不存在"):
        postgres_creative.create_project(987654321, 987654321)

    deleted_topic = _create_topic(isolated_system_database, title="已删选题")
    deleted_breakdown = _create_breakdown(
        isolated_system_database, deleted_topic, status="locked"
    )
    with isolated_system_database.transaction() as connection:
        connection.execute(
            update(creative_topics)
            .where(creative_topics.c.id == deleted_topic)
            .values(deleted=True)
        )
    with pytest.raises(ValueError, match="选题不存在"):
        postgres_creative.create_project(deleted_topic, deleted_breakdown)


def test_create_project_is_idempotent_by_creation_request_id(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    first = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="tester",
        creation_request_id="  req-repeat  ",
    )
    second = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="tester",
        creation_request_id="req-repeat",
        name="不应生效的名字",
    )

    assert second["id"] == first["id"]
    assert second["name"] == first["name"]
    assert _project_ids_for_request(
        postgres_creative.database, "tester", "req-repeat"
    ) == [first["id"]]
    assert postgres_creative.get_project_by_creation_request_id(
        "tester", " req-repeat "
    )["id"] == first["id"]
    assert postgres_creative.get_project_by_creation_request_id(
        "other", "req-repeat"
    ) is None


def test_create_project_is_idempotent_by_workflow_task_id(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="工作流选题")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)
    first = postgres_creative.create_project(
        topic_id, breakdown_id, created_by="tester", workflow_task_id="task-abc"
    )
    assert first["workflow_task_id"] == "task-abc"
    assert first["creation_workflow_task_id"] == "task-abc"

    other_topic = _create_topic(isolated_system_database, title="第二个选题")
    other_breakdown = _create_breakdown(isolated_system_database, other_topic)
    second = postgres_creative.create_project(
        other_topic, other_breakdown, created_by="tester", workflow_task_id="task-abc"
    )

    assert second["id"] == first["id"]
    assert second["topic_id"] == topic_id
    assert postgres_creative.get_project_by_workflow_task_id("task-abc")["id"] == first["id"]
    assert (
        postgres_creative.get_project_by_creation_workflow_task_id("task-abc")["id"]
        == first["id"]
    )
    assert postgres_creative.get_project_by_workflow_task_id("task-missing") is None


def test_concurrent_creation_request_id_creates_exactly_one_project(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    def create() -> int:
        project = postgres_creative.create_project(
            seeded_topic["topic_id"],
            seeded_topic["breakdown_id"],
            created_by="racer",
            creation_request_id="req-race",
        )
        return int(project["id"])

    ids = _concurrently([create] * 6)

    assert len(set(ids)) == 1
    assert _project_ids_for_request(
        postgres_creative.database, "racer", "req-race"
    ) == [ids[0]]


def test_create_project_free_mode_writes_the_frozen_seed(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="自由选题")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)

    project = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="tester",
        workflow_mode="free",
        creation_request_id="req-free",
    )

    assert project["status"] == "free_active"
    assert project["workflow_mode"] == "free"
    seed = postgres_creative.get_current_seed(project["id"])
    assert seed["version"] == 1
    assert seed["status"] == "frozen"
    assert seed["parent_version_id"] is None
    assert seed["seed_text"] == "选题：自由选题\n简介：测试简介\n核心冲突：身份互换"
    assert seed["constraints"] == {}
    assert seed["reference_refs"] == []
    assert seed["locked_at"] is None

    explicit = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="tester",
        workflow_mode="free",
        creation_request_id="req-free-explicit",
        seed_text=" 自定义种子 ",
    )
    assert postgres_creative.get_current_seed(explicit["id"])["seed_text"] == "自定义种子"


def test_creation_mode_reference_check_constraint_is_respected(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="约束选题")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)

    original, created = postgres_creative.ensure_original_project(
        seed_text="原创种子",
        name="原创项目",
        target_episode_count=10,
        created_by="constraint-user",
        creation_request_id="req-constraint-original",
    )
    assert created is True
    assert original["topic_id"] is None
    assert original["breakdown_id"] is None

    imitation = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="constraint-user",
        creation_request_id="req-constraint-imitation",
    )
    assert imitation["topic_id"] is not None
    assert imitation["breakdown_id"] is not None

    with pytest.raises(IntegrityError) as original_with_topic:
        with isolated_system_database.transaction() as connection:
            connection.execute(
                creative_projects.insert().values(
                    topic_id=topic_id,
                    breakdown_id=breakdown_id,
                    name="非法原创",
                    creation_mode="original",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
    assert "ck_creative_projects_creation_mode_reference" in str(
        original_with_topic.value
    )

    with pytest.raises(IntegrityError) as imitation_without_source:
        with isolated_system_database.transaction() as connection:
            connection.execute(
                creative_projects.insert().values(
                    topic_id=None,
                    breakdown_id=None,
                    name="非法仿制",
                    creation_mode="imitation",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
    assert "ck_creative_projects_creation_mode_reference" in str(
        imitation_without_source.value
    )


# ---------------------------------------------------------------------------
# original project lifecycle
# ---------------------------------------------------------------------------


def test_ensure_original_project_is_idempotent_and_mode_guarded(
    postgres_creative,
) -> None:
    project, created = postgres_creative.ensure_original_project(
        seed_text="  原创种子内容  ",
        name="原创项目",
        target_episode_count=12,
        target_platform=" dy ",
        genre=" 悬疑 ",
        target_audience=" 25-40 ",
        episode_duration_seconds=90,
        constraints={"tone": "紧张"},
        reference_refs=[{"kind": "url", "value": "https://example.com"}],
        created_by="maker",
        creation_request_id="req-original",
    )

    assert created is True
    assert project["name"] == "原创项目"
    assert project["creation_mode"] == "original"
    assert project["bootstrap_status"] == "pending"
    assert project["status"] == "created"
    assert project["topic_id"] is None
    assert project["breakdown_id"] is None
    assert project["target_platform"] == "dy"
    assert project["genre"] == "悬疑"
    assert project["target_audience"] == "25-40"
    assert project["target_episode_count"] == 12
    assert project["episode_duration_seconds"] == 90
    assert project["topic"] == {}
    assert project["breakdown"] == {}
    assert project["seed"]["version"] == 1
    assert project["seed"]["status"] == "draft"
    assert project["seed"]["seed_text"] == "原创种子内容"
    assert project["seed"]["constraints"] == {"tone": "紧张"}
    assert project["seed"]["reference_refs"] == [
        {"kind": "url", "value": "https://example.com"}
    ]

    again, created_again = postgres_creative.ensure_original_project(
        seed_text="另一个种子",
        name="另一个名字",
        target_episode_count=12,
        created_by="maker",
        creation_request_id="req-original",
    )
    assert created_again is False
    assert again["id"] == project["id"]
    assert again["name"] == "原创项目"

    with pytest.raises(ValueError, match="创建请求标识已用于其他工作流模式"):
        postgres_creative.ensure_original_project(
            seed_text="种子",
            name="自由原创",
            target_episode_count=12,
            created_by="maker",
            creation_request_id="req-original",
            workflow_mode="free",
        )

    wrapped = postgres_creative.create_original_project(
        seed_text="包装种子",
        name="包装项目",
        target_episode_count=8,
        created_by="maker",
        creation_request_id="req-wrapped",
    )
    assert wrapped["creation_mode"] == "original"
    assert wrapped["seed"]["seed_text"] == "包装种子"

    with pytest.raises(ValueError, match="目标集数必须在 1-100 之间"):
        postgres_creative.ensure_original_project(
            seed_text="种子", name="越界", target_episode_count=0,
            created_by="maker", creation_request_id="req-oob",
        )
    with pytest.raises(ValueError, match="单集时长必须在 30-1800 秒之间"):
        postgres_creative.ensure_original_project(
            seed_text="种子", name="越界", target_episode_count=10,
            episode_duration_seconds=5, created_by="maker",
            creation_request_id="req-oob",
        )
    with pytest.raises(ValueError, match="原创种子不能为空"):
        postgres_creative.ensure_original_project(
            seed_text="   ", name="空种子", target_episode_count=10,
            created_by="maker", creation_request_id="req-oob",
        )
    with pytest.raises(ValueError, match="invalid creative workflow mode"):
        postgres_creative.ensure_original_project(
            seed_text="种子", name="模式错误", target_episode_count=10,
            created_by="maker", creation_request_id="req-oob",
            workflow_mode="strict",
        )


def test_creation_request_id_cannot_be_reused_across_creation_modes(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="混用选题")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)
    imitation = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="mixed",
        creation_request_id="req-mixed",
    )

    with pytest.raises(ValueError, match="创建请求标识已用于其他项目模式"):
        postgres_creative.ensure_original_project(
            seed_text="种子",
            name="冲突原创",
            target_episode_count=10,
            created_by="mixed",
            creation_request_id="req-mixed",
        )

    # create_project keeps the legacy "return the winner" behaviour instead.
    assert postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="mixed",
        creation_request_id="req-mixed",
    )["id"] == imitation["id"]


def test_ensure_original_project_free_mode_seeds_a_frozen_free_project(
    postgres_creative,
) -> None:
    project, created = postgres_creative.ensure_original_project(
        seed_text="自由原创种子",
        name="自由原创",
        target_episode_count=15,
        created_by="maker",
        creation_request_id="req-original-free",
        workflow_mode="free",
    )

    assert created is True
    assert project["status"] == "free_active"
    assert project["workflow_mode"] == "free"
    assert project["seed"]["status"] == "draft"
    assert project["seed"]["seed_text"] == "自由原创种子"


def test_original_project_without_creation_request_id_is_never_deduplicated(
    postgres_creative,
) -> None:
    first, first_created = postgres_creative.ensure_original_project(
        seed_text="种子一", name="项目一", target_episode_count=10,
        created_by="maker", creation_request_id="",
    )
    second, second_created = postgres_creative.ensure_original_project(
        seed_text="种子一", name="项目一", target_episode_count=10,
        created_by="maker", creation_request_id="",
    )

    assert first_created is True and second_created is True
    assert first["id"] != second["id"]


# ---------------------------------------------------------------------------
# seeds
# ---------------------------------------------------------------------------


def test_seed_update_freeze_and_version_chain(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="第一版种子",
        name="种子项目",
        target_episode_count=10,
        constraints={"tone": "紧张"},
        reference_refs=[{"kind": "doc", "value": "a"}],
        created_by="maker",
        creation_request_id="req-seeds",
    )

    seed = postgres_creative.get_current_seed(project["id"])
    assert seed["version"] == 1
    assert seed["status"] == "draft"
    assert postgres_creative.list_seeds(project["id"]) == [seed]
    assert postgres_creative.get_seed(seed["id"]) == seed
    assert postgres_creative.get_seed(987654321) is None
    assert postgres_creative.get_current_seed(987654321) is None

    unchanged = postgres_creative.update_seed(seed["id"], {"unknown": 1})
    assert unchanged["id"] == seed["id"]
    assert unchanged["seed_text"] == "第一版种子"

    updated = postgres_creative.update_seed(
        seed["id"],
        {
            "seed_text": "  第二稿种子  ",
            "constraints": {"tone": "轻松"},
            "unknown": "ignored",
        },
    )
    assert updated["seed_text"] == "第二稿种子"
    assert updated["constraints"] == {"tone": "轻松"}
    assert updated["reference_refs"] == [{"kind": "doc", "value": "a"}]

    with pytest.raises(ValueError, match="原创种子内容不能为空"):
        postgres_creative.update_seed(seed["id"], {"seed_text": "   "})
    assert postgres_creative.update_seed(987654321, {"seed_text": "x"}) is None

    second = postgres_creative.create_seed_version(project["id"], seed_text="第三版")
    assert second["version"] == 2
    assert second["parent_version_id"] == seed["id"]
    assert second["status"] == "draft"
    assert second["seed_text"] == "第三版"
    assert second["constraints"] == {"tone": "轻松"}
    assert second["reference_refs"] == [{"kind": "doc", "value": "a"}]

    third = postgres_creative.create_seed_version(
        project["id"], constraints={"pov": "first"}
    )
    assert third["version"] == 3
    assert third["parent_version_id"] == second["id"]
    assert third["seed_text"] == "第三版"
    assert third["constraints"] == {"pov": "first"}

    assert [
        item["version"] for item in postgres_creative.list_seeds(project["id"])
    ] == [1, 2, 3]
    assert postgres_creative.get_current_seed(project["id"])["id"] == third["id"]

    frozen = postgres_creative.freeze_seed(third["id"])
    assert frozen["status"] == "frozen"
    assert isinstance(frozen["locked_at"], float)
    assert postgres_creative.freeze_seed(third["id"])["status"] == "frozen"
    assert postgres_creative.update_seed(third["id"], {"seed_text": "改"}) is None

    with pytest.raises(ValueError, match="原创种子不存在"):
        postgres_creative.freeze_seed(987654321)
    assert postgres_creative.update_seed(987654321, {"seed_text": "x"}) is None


def test_create_seed_version_requires_an_existing_seed(postgres_creative) -> None:
    with pytest.raises(ValueError, match="原创项目种子不存在"):
        postgres_creative.create_seed_version(987654321)


def test_concurrent_seed_versions_never_share_a_version_number(
    postgres_creative,
) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="并发种子",
        name="并发项目",
        target_episode_count=10,
        created_by="maker",
        creation_request_id="req-concurrent-seeds",
    )

    def bump(index: int) -> dict[str, Any]:
        return postgres_creative.create_seed_version(
            project["id"], seed_text=f"并发第 {index} 次"
        )

    created = _concurrently([lambda index=index: bump(index) for index in range(6)])

    versions = sorted(item["version"] for item in created)
    assert versions == [2, 3, 4, 5, 6, 7]
    assert len({item["id"] for item in created}) == 6
    assert [
        item["version"] for item in postgres_creative.list_seeds(project["id"])
    ] == [1, 2, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------------------
# project updates and soft delete
# ---------------------------------------------------------------------------


def test_update_project_bumps_revision_and_only_allows_archiving(
    postgres_creative,
) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="待改项目", target_episode_count=10,
        created_by="maker", creation_request_id="req-update",
    )

    updated = postgres_creative.update_project(
        project["id"], {"name": "  改名后  ", "target_episode_count": 30}
    )
    assert updated["name"] == "改名后"
    assert updated["target_episode_count"] == 30
    assert updated["revision"] == 1

    unchanged = postgres_creative.update_project(project["id"], {"name": "改名后"})
    assert unchanged["revision"] == 1

    archived = postgres_creative.update_project(project["id"], {"status": "archived"})
    assert archived["status"] == "archived"
    assert archived["revision"] == 1

    assert postgres_creative.update_project(
        project["id"], {"name": None}
    )["revision"] == 1
    with pytest.raises(ValueError, match="创作项目不存在"):
        postgres_creative.update_project(987654321, {"name": "x"})
    with pytest.raises(ValueError, match="项目名称不能为空"):
        postgres_creative.update_project(project["id"], {"name": "   "})
    with pytest.raises(ValueError, match="项目仅支持归档状态更新"):
        postgres_creative.update_project(project["id"], {"status": "created"})
    with pytest.raises(ValueError, match="目标集数必须在 1-100 之间"):
        postgres_creative.update_project(project["id"], {"target_episode_count": 0})
    with pytest.raises(ValueError, match="单集时长必须在 30-1800 秒之间"):
        postgres_creative.update_project(
            project["id"], {"episode_duration_seconds": 1}
        )


def test_update_project_revision_guard(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="版本项目", target_episode_count=10,
        created_by="maker", creation_request_id="req-revision",
    )
    assert project["revision"] == 0

    bumped = postgres_creative.update_project(
        project["id"], {"name": "第一次"}, expected_revision=0
    )
    assert bumped["revision"] == 1

    with pytest.raises(ValueError, match="project revision changed"):
        postgres_creative.update_project(
            project["id"], {"name": "第二次"}, expected_revision=0
        )
    assert postgres_creative.get_project(project["id"])["name"] == "第一次"

    # A matching expectation still succeeds and bumps again.
    again = postgres_creative.update_project(
        project["id"], {"name": "第二次"}, expected_revision=1
    )
    assert again["revision"] == 2


def test_update_project_respects_the_brief_lifecycle(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="Brief 项目", target_episode_count=10,
        created_by="maker", creation_request_id="req-brief",
    )

    submitted = _create_blueprint(
        postgres_creative.database, project["id"], status="submitted"
    )
    with pytest.raises(ValueError, match="Brief 完成后不能修改项目 Brief 参数"):
        postgres_creative.update_project(project["id"], {"name": "阻塞"})
    with postgres_creative.database.transaction() as connection:
        connection.execute(
            update(creative_blueprints)
            .where(creative_blueprints.c.id == submitted)
            .values(status="superseded")
        )

    locked = _create_blueprint(
        postgres_creative.database,
        project["id"],
        candidate_group_id="group-locked",
        status="locked",
    )
    with postgres_creative.database.transaction() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == project["id"])
            .values(locked_brief_id=locked)
        )

    with pytest.raises(ValueError, match="锁定 Brief 后不能修改项目 Brief 参数"):
        postgres_creative.update_project(project["id"], {"name": "无草稿"})

    draft = _create_blueprint(
        postgres_creative.database,
        project["id"],
        candidate_group_id="group-locked",
        version=2,
        parent_version_id=locked,
        status="draft",
    )
    with pytest.raises(ValueError, match="只能通过当前锁定 Brief 的新草稿版本修改项目参数"):
        postgres_creative.update_project(
            project["id"], {"name": "错误草稿"}, draft_brief_id=999999,
            expected_revision=0,
        )

    allowed = postgres_creative.update_project(
        project["id"],
        {"name": "通过草稿修改"},
        draft_brief_id=draft,
        expected_revision=0,
    )
    assert allowed["name"] == "通过草稿修改"
    assert allowed["revision"] == 1


def test_soft_delete_hides_the_project_from_every_read(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="删除选题")
    breakdown_id = _create_breakdown(isolated_system_database, topic_id)
    project = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="deleter",
        creation_request_id="req-delete",
    )

    with postgres_creative.database.transaction() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == project["id"])
            .values(deleted=True)
        )

    assert postgres_creative.get_project(project["id"]) is None
    assert postgres_creative.get_project_by_creation_request_id(
        "deleter", "req-delete"
    ) is None
    assert project["id"] not in postgres_creative.list_active_project_ids()
    assert project["id"] not in [
        int(raw_id) for raw_id, _ in postgres_creative.list_project_creator_audit_rows()
    ]
    assert project["id"] not in [
        item["id"] for item in postgres_creative.list_projects()
    ]
    assert postgres_creative.mark_project_bootstrap_status(project["id"], "ready") == {}
    assert postgres_creative.project_workflow_identity_evidence(project["id"]) == {
        "created_by": "",
        "task_ids": set(),
    }

    # The partial unique index ignores `deleted`, exactly like the legacy store,
    # so re-using the request id recovers the soft-deleted row and the read then
    # yields an empty project rather than a duplicate row.
    assert postgres_creative.create_project(
        project["topic_id"],
        project["breakdown_id"],
        created_by="deleter",
        creation_request_id="req-delete",
    ) == {}
    assert _project_ids_for_request(
        postgres_creative.database, "deleter", "req-delete"
    ) == [project["id"]]


def test_list_projects_filters_bootstrap_status_and_topic(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    ready = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="lister",
        creation_request_id="req-list-ready",
    )
    pending, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="待引导项目", target_episode_count=10,
        created_by="lister", creation_request_id="req-list-pending",
    )

    listed = postgres_creative.list_projects()
    listed_ids = [item["id"] for item in listed]
    assert ready["id"] in listed_ids
    assert pending["id"] not in listed_ids
    assert listed[0]["topic_title"] == "都市重生"
    assert isinstance(listed[0]["updated_at"], float)

    with postgres_creative.database.transaction() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == pending["id"])
            .values(bootstrap_status="ready")
        )
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == pending["id"])
            .values(updated_at=utc_now() + timedelta(hours=1))
        )
    reordered = postgres_creative.list_projects()
    assert [item["id"] for item in reordered][0] == pending["id"]
    assert reordered[0]["topic_title"] is None

    assert [
        item["id"]
        for item in postgres_creative.list_projects(topic_id=seeded_topic["topic_id"])
    ] == [ready["id"]]
    assert postgres_creative.list_projects(topic_id=987654321) == []


def test_bootstrap_status_transitions_and_listing(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="引导项目", target_episode_count=10,
        created_by="bootstrapper", creation_request_id="req-bootstrap",
    )
    other, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="别人的项目", target_episode_count=10,
        created_by="someone-else", creation_request_id="req-bootstrap-other",
    )

    assert [item["id"] for item in postgres_creative.list_projects_for_bootstrap(
        "bootstrapper"
    )] == [project["id"]]
    assert [
        item["id"]
        for item in postgres_creative.list_projects_for_bootstrap(
            "bootstrapper", "pending"
        )
    ] == [project["id"]]
    assert postgres_creative.list_projects_for_bootstrap("bootstrapper", "ready") == []
    assert postgres_creative.list_projects_for_bootstrap("nobody") == []
    with pytest.raises(ValueError, match="invalid bootstrap status"):
        postgres_creative.list_projects_for_bootstrap("bootstrapper", "bogus")

    marked = postgres_creative.mark_project_bootstrap_status(project["id"], "failed")
    assert marked["bootstrap_status"] == "failed"
    assert [
        item["id"]
        for item in postgres_creative.list_projects_for_bootstrap(
            "bootstrapper", "failed"
        )
    ] == [project["id"]]
    with pytest.raises(ValueError, match="invalid bootstrap status"):
        postgres_creative.mark_project_bootstrap_status(project["id"], "bogus")

    assert postgres_creative.mark_project_bootstrap_status(987654321, "ready") == {}
    assert other["id"] not in [
        item["id"] for item in postgres_creative.list_projects_for_bootstrap(
            "bootstrapper"
        )
    ]


# ---------------------------------------------------------------------------
# workflow identity
# ---------------------------------------------------------------------------


def test_workflow_identity_is_store_scoped_and_round_trips(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    project = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="identity",
        creation_request_id="req-identity",
        workflow_task_id="task-identity",
    )

    assert postgres_creative.workflow_project_id(project["id"]) == (
        f"creative:{postgres_creative.store_id}:project:{project['id']}"
    )
    assert postgres_creative.get_project_by_workflow_task_id(
        "task-identity"
    )["id"] == project["id"]
    assert postgres_creative.get_project_by_creation_workflow_task_id(
        "task-identity"
    )["id"] == project["id"]


def test_reconcile_moves_the_creation_task_and_clears_the_previous_owner(
    postgres_creative, seeded_topic: dict[str, int]
) -> None:
    first = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="identity",
        creation_request_id="req-reconcile-a",
        workflow_task_id="task-move",
    )
    second = postgres_creative.create_project(
        seeded_topic["topic_id"],
        seeded_topic["breakdown_id"],
        created_by="identity",
        creation_request_id="req-reconcile-b",
    )

    reconciled = postgres_creative.reconcile_project_workflow_task(
        "task-move", second["id"]
    )

    assert reconciled["id"] == second["id"]
    assert reconciled["creation_workflow_task_id"] == "task-move"
    assert reconciled["workflow_task_id"] == "task-move"
    previous_owner = postgres_creative.get_project(first["id"])
    assert previous_owner["creation_workflow_task_id"] is None
    assert previous_owner["workflow_task_id"] is None
    assert postgres_creative.get_project_by_creation_workflow_task_id(
        "task-move"
    )["id"] == second["id"]

    with pytest.raises(ValueError, match="system-bound creative project does not exist"):
        postgres_creative.reconcile_project_workflow_task("task-move", 987654321)

    postgres_creative.clear_project_creation_task(second["id"], "other-task")
    assert postgres_creative.get_project(second["id"])[
        "creation_workflow_task_id"
    ] == "task-move"
    postgres_creative.clear_project_creation_task(second["id"], "task-move")
    cleared = postgres_creative.get_project(second["id"])
    assert cleared["creation_workflow_task_id"] is None
    assert cleared["workflow_task_id"] == "task-move"


def test_link_project_workflow_task_updates_only_the_workflow_column(
    postgres_creative,
) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="链接项目", target_episode_count=10,
        created_by="linker", creation_request_id="req-link",
    )

    postgres_creative.link_project_workflow_task(project["id"], "task-link")

    linked = postgres_creative.get_project(project["id"])
    assert linked["workflow_task_id"] == "task-link"
    assert linked["creation_workflow_task_id"] is None
    # The legacy lookup follows the creation task only.
    assert postgres_creative.get_project_by_workflow_task_id("task-link") is None

    with pytest.raises(ValueError, match="创作项目不存在"):
        postgres_creative.link_project_workflow_task(987654321, "task-link")


def test_workflow_identity_evidence_collects_project_and_resource_tasks(
    postgres_creative, isolated_system_database: SystemDatabase
) -> None:
    topic_id = _create_topic(isolated_system_database, title="证据选题")
    breakdown_id = _create_breakdown(
        isolated_system_database, topic_id, workflow_task_id="task-breakdown"
    )
    project = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="evidence-user",
        creation_request_id="req-evidence",
        workflow_task_id="task-creation",
    )
    _create_blueprint(
        postgres_creative.database, project["id"], workflow_task_id="task-blueprint"
    )
    _create_episode(
        postgres_creative.database, project["id"], workflow_task_id="task-episode"
    )
    _create_blueprint(  # a different project must not leak into the evidence
        postgres_creative.database,
        postgres_creative.create_project(
            topic_id, breakdown_id, created_by="other-user",
            creation_request_id="req-evidence-other",
        )["id"],
        workflow_task_id="task-other",
    )

    evidence = postgres_creative.project_workflow_identity_evidence(project["id"])

    assert evidence["created_by"] == "evidence-user"
    assert evidence["task_ids"] == {
        "task-creation",
        "task-blueprint",
        "task-episode",
    }
    assert postgres_creative.project_workflow_identity_evidence(987654321) == {
        "created_by": "",
        "task_ids": set(),
    }
    assert postgres_creative.topic_workflow_task_ids(topic_id) == {"task-breakdown"}
    assert postgres_creative.topic_workflow_task_ids(987654321) == set()


def test_get_project_inputs_returns_the_original_seed(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="输入种子", name="输入项目", target_episode_count=10,
        created_by="inputter", creation_request_id="req-inputs",
    )

    inputs = postgres_creative.get_project_inputs(project["id"])

    assert inputs["project"]["id"] == project["id"]
    assert inputs["topic"] == {}
    assert inputs["breakdown"] == {}
    assert inputs["seed"]["seed_text"] == "输入种子"

    with pytest.raises(ValueError, match="创作项目不存在"):
        postgres_creative.get_project_inputs(987654321)


# ---------------------------------------------------------------------------
# private creative-item helpers
# ---------------------------------------------------------------------------


def test_creative_item_helpers_use_native_json(postgres_creative) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="角色项目", target_episode_count=10,
        created_by="item-user", creation_request_id="req-items",
    )
    character_id = _create_character(postgres_creative.database, project["id"])

    fetched = postgres_creative._get_creative_item(
        "creative_characters", character_id
    )
    assert fetched["content"] == {"name": "甲"}
    assert fetched["source_refs"] == {}
    assert fetched["generation_meta"] == {}
    assert "content_json" not in fetched
    assert isinstance(fetched["updated_at"], float)
    assert postgres_creative._get_creative_item(
        "creative_characters", 987654321
    ) is None
    with pytest.raises(ValueError, match="invalid creative table"):
        postgres_creative._get_creative_item("creative_episodes", character_id)

    updated = postgres_creative._update_creative_item(
        "creative_characters",
        character_id,
        {"name": "乙", "locked": "keep-me"},
        preserve_content_keys=("locked",),
    )
    assert updated["content"] == {"name": "乙"}

    preserved = postgres_creative._update_creative_item(
        "creative_characters",
        character_id,
        {"name": "丙"},
        preserve_content_keys=("name",),
    )
    assert preserved["content"] == {"name": "乙"}

    with pytest.raises(ValueError, match="invalid creative table"):
        postgres_creative._update_creative_item("bogus", character_id, {})
    assert postgres_creative._update_creative_item(
        "creative_characters", 987654321, {}
    ) is None

    locked_id = _create_character(
        postgres_creative.database, project["id"], status="locked"
    )
    with pytest.raises(ValueError, match="已完成的版本不能原地修改"):
        postgres_creative._update_creative_item(
            "creative_characters", locked_id, {"name": "禁止"}
        )

    episode_id = _create_episode(postgres_creative.database, project["id"])
    episode = postgres_creative._update_creative_item(
        "creative_episodes", episode_id, {"beats": [{"index": 1}]}, episode=True
    )
    assert episode["content"] == {"beats": [{"index": 1}]}
    assert episode["upstream_refs"] == {}
    assert episode["generation_meta"] == {}


def test_get_project_embeds_versioned_children_in_legacy_order(
    postgres_creative,
) -> None:
    project, _ = postgres_creative.ensure_original_project(
        seed_text="种子", name="子资源项目", target_episode_count=10,
        created_by="child-user", creation_request_id="req-children",
    )
    _create_blueprint(postgres_creative.database, project["id"], version=1)
    _create_blueprint(
        postgres_creative.database, project["id"], candidate_group_id="group-b",
        version=2,
    )
    _create_character(postgres_creative.database, project["id"])
    _create_episode(
        postgres_creative.database, project["id"], stage="script", status="draft"
    )
    _create_episode(postgres_creative.database, project["id"], stage="beats")

    loaded = postgres_creative.get_project(project["id"])

    assert [item["version"] for item in loaded["blueprints"]] == [1, 2]
    assert loaded["blueprints"][0]["content"] == {"premise": "测试"}
    assert len(loaded["characters"]) == 1
    assert loaded["characters"][0]["content"] == {"name": "甲"}
    assert [item["stage"] for item in loaded["episodes"]] == ["beats", "script"]
    assert loaded["episodes"][1]["content"] == normalize_script_content({"beats": []})
    assert loaded["resource_heads"] == []


def test_imitation_project_has_no_seed_chain(postgres_creative) -> None:
    """An imitation project owns no seeds, so it cannot grow a version chain."""

    topic_id = _create_topic(postgres_creative.database, title="无种子选题")
    breakdown_id = _create_breakdown(postgres_creative.database, topic_id)
    project = postgres_creative.create_project(
        topic_id,
        breakdown_id,
        created_by="no-seed",
        creation_request_id="req-no-seed",
    )

    assert postgres_creative.get_current_seed(project["id"]) is None
    with pytest.raises(ValueError, match="原创项目种子不存在"):
        postgres_creative.create_seed_version(project["id"])
    with postgres_creative.database.connection() as connection:
        assert connection.execute(
            select(creative_seeds.c.id).where(
                creative_seeds.c.project_id == project["id"]
            )
        ).all() == []
