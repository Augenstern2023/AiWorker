# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from creative.workflow_identity import WorkflowIdentityResolver
from identity.repository import IdentityRepository
from system_database import SystemDatabase
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


def _original_project(
    repository: CreativeRepository,
    *,
    created_by: str = SYSTEM_ACTOR.user_id,
) -> dict:
    return repository.create_original_project(
        seed_text="历史项目种子",
        name="历史项目",
        target_episode_count=1,
        created_by=created_by,
        creation_request_id=str(uuid.uuid4()),
    )


def _record_project_task(
    repository: CreativeRepository, project_id: int, task_id: str
) -> None:
    with repository._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET workflow_task_id = ?, "
            "creation_workflow_task_id = ? WHERE id = ?",
            (task_id, task_id, project_id),
        )


def _workflow(database: SystemDatabase) -> WorkflowRepository:
    identity = IdentityRepository(database)
    identity.seed_defaults()
    workflow = WorkflowRepository(identity)
    with identity.transaction() as connection:
        workflow._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
    return workflow


def test_creative_store_identity_is_stable_per_database(tmp_path: Path) -> None:
    first_path = tmp_path / "first" / "creative.db"
    second_path = tmp_path / "second" / "creative.db"

    first = CreativeRepository(first_path)
    reloaded = CreativeRepository(first_path)
    second = CreativeRepository(second_path)

    assert uuid.UUID(first.store_id)
    assert reloaded.store_id == first.store_id
    assert second.store_id != first.store_id
    assert first.workflow_project_id(6) == (
        f"creative:{first.store_id}:project:6"
    )
    assert first.workflow_topic_id(6) == f"creative:{first.store_id}:topic:6"


def test_same_local_project_id_is_isolated_between_creative_stores(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    first_store = CreativeRepository(tmp_path / "first" / "creative.db")
    second_store = CreativeRepository(tmp_path / "second" / "creative.db")
    workflow = _workflow(isolated_system_database)
    first_project_id = first_store.workflow_project_id(6)
    second_project_id = second_store.workflow_project_id(6)

    first = workflow.ensure_project_task(
        first_project_id,
        101,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )
    second = workflow.ensure_project_task(
        second_project_id,
        202,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )

    assert first["id"] != second["id"]
    assert first["project_id"] == first_project_id
    assert second["project_id"] == second_project_id
    assert first["local_project_id"] == "6"
    assert second["local_project_id"] == "6"

    workflow.delete_project_data(second_project_id)

    assert workflow.get_task(first["id"]) is not None
    assert workflow.get_task(second["id"]) is None
    workflow.delete_project_data(first_project_id)


def test_project_task_reuse_rejects_a_different_business_topic(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    project_id = store.workflow_project_id(6)
    workflow.ensure_project_task(
        project_id,
        101,
        task_type="brief",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )

    with pytest.raises(ValueError, match="another topic"):
        workflow.ensure_project_task(
            project_id,
            202,
            task_type="brief",
            role_code="creative_planner",
            actor=SYSTEM_ACTOR,
        )

    workflow.delete_project_data(project_id)


def test_same_local_topic_id_gets_distinct_workflows_between_stores(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    first_store = CreativeRepository(tmp_path / "first" / "creative.db")
    second_store = CreativeRepository(tmp_path / "second" / "creative.db")
    workflow = _workflow(isolated_system_database)

    first = workflow.ensure_topic_task(
        5,
        task_type="breakdown",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
        workflow_scope=first_store.workflow_topic_id(5),
    )
    second = workflow.ensure_topic_task(
        5,
        task_type="breakdown",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
        workflow_scope=second_store.workflow_topic_id(5),
    )

    assert first["id"] != second["id"]
    assert first["workflow_id"] != second["workflow_id"]


def test_resolver_uses_legacy_project_id_when_local_task_proves_ownership(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    project = _original_project(store)
    legacy_id = str(project["id"])
    task = workflow.ensure_project_task(
        legacy_id,
        None,
        actor=SYSTEM_ACTOR,
    )
    _record_project_task(store, project["id"], task["id"])

    resolved = WorkflowIdentityResolver(store, workflow).project_id(project["id"])

    assert resolved == legacy_id


def test_resolver_uses_historical_scoped_id_when_local_task_proves_ownership(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    unrelated_store = CreativeRepository(tmp_path / "unrelated" / "creative.db")
    workflow = _workflow(isolated_system_database)
    project = _original_project(store)
    unrelated_project = _original_project(unrelated_store)
    assert unrelated_project["id"] == project["id"]
    legacy_id = str(project["id"])
    historical_scoped_id = (
        f"creative:{uuid.uuid4()}:project:{project['id']}"
    )
    assert historical_scoped_id != store.workflow_project_id(project["id"])
    task = workflow.ensure_project_task(legacy_id, None, actor=SYSTEM_ACTOR)
    with workflow.identity_repository.transaction() as connection:
        connection.exec_driver_sql(
            "UPDATE workflow_tasks SET project_id = %s WHERE id = %s",
            (historical_scoped_id, task["id"]),
        )
    _record_project_task(store, project["id"], task["id"])
    task_before = workflow.get_task(task["id"])

    resolved = WorkflowIdentityResolver(store, workflow).project_id(project["id"])
    unrelated_resolved = WorkflowIdentityResolver(
        unrelated_store, workflow
    ).project_id(unrelated_project["id"])

    assert resolved == historical_scoped_id
    assert unrelated_resolved == unrelated_store.workflow_project_id(
        unrelated_project["id"]
    )
    assert workflow.get_task(task["id"]) == task_before


def test_resolver_requires_local_evidence_before_using_colliding_legacy_id(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    first_store = CreativeRepository(tmp_path / "first" / "creative.db")
    second_store = CreativeRepository(tmp_path / "second" / "creative.db")
    workflow = _workflow(isolated_system_database)
    first_project = _original_project(first_store)
    second_project = _original_project(second_store)
    assert first_project["id"] == second_project["id"]
    legacy_id = str(first_project["id"])
    task = workflow.ensure_project_task(legacy_id, None, actor=SYSTEM_ACTOR)
    _record_project_task(first_store, first_project["id"], task["id"])

    first_resolved = WorkflowIdentityResolver(first_store, workflow).project_id(
        first_project["id"]
    )
    second_resolved = WorkflowIdentityResolver(second_store, workflow).project_id(
        second_project["id"]
    )

    assert first_resolved == legacy_id
    assert second_resolved == second_store.workflow_project_id(second_project["id"])


def test_resolver_prefers_scoped_project_rows_over_legacy_task_evidence(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    project = _original_project(store)
    legacy_id = str(project["id"])
    legacy_task = workflow.ensure_project_task(legacy_id, None, actor=SYSTEM_ACTOR)
    _record_project_task(store, project["id"], legacy_task["id"])
    scoped_id = store.workflow_project_id(project["id"])
    workflow.ensure_project_task(scoped_id, None, actor=SYSTEM_ACTOR)

    resolved = WorkflowIdentityResolver(store, workflow).project_id(project["id"])

    assert resolved == scoped_id


def test_resolver_accepts_legacy_creator_membership_as_ownership_evidence(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    creator = workflow.identity_repository.create_user(
        f"legacy-creator-{uuid.uuid4().hex}", "历史创建者", "hash"
    )
    project = _original_project(store, created_by=creator["id"])
    legacy_id = str(project["id"])
    with workflow.identity_repository.transaction() as connection:
        connection.exec_driver_sql(
            "INSERT INTO project_members "
            "(id, project_id, user_id, project_role, status, added_by) "
            "VALUES (%s, %s, %s, 'project_creator', 'active', %s)",
            (str(uuid.uuid4()), legacy_id, creator["id"], creator["id"]),
        )

    resolved = WorkflowIdentityResolver(store, workflow).project_id(project["id"])

    assert resolved == legacy_id


def test_resolver_uses_legacy_topic_scope_when_breakdown_task_proves_ownership(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    topic = store.upsert_topics(
        [{
            "platform": "dy",
            "job_id": "legacy-topic",
            "source_path": "legacy-topic.json",
            "captured_at": "2026-09-11",
            "title": "历史选题",
            "score": 90,
            "confidence": 0.9,
            "score_details": {},
            "recommendation": "采用",
            "evidence": [],
        }]
    )[0]
    task = workflow.ensure_topic_task(
        topic["id"],
        task_type="breakdown",
        role_code="creative_planner",
        actor=SYSTEM_ACTOR,
    )
    store.create_breakdown(
        topic["id"],
        {},
        {},
        [],
        workflow_task_id=task["id"],
    )

    resolved = WorkflowIdentityResolver(store, workflow).topic_scope(topic["id"])

    assert resolved == str(topic["id"])


def test_workflow_adapter_accepts_a_locally_proven_legacy_project_task(
    tmp_path: Path,
    isolated_system_database: SystemDatabase,
) -> None:
    store = CreativeRepository(tmp_path / "creative.db")
    workflow = _workflow(isolated_system_database)
    project = _original_project(store)
    legacy_id = str(project["id"])
    task = workflow.ensure_project_task(legacy_id, None, actor=SYSTEM_ACTOR)
    _record_project_task(store, project["id"], task["id"])
    with workflow.identity_repository.transaction() as connection:
        connection.exec_driver_sql(
            "UPDATE workflow_tasks SET status = 'assigned' WHERE id = %s",
            (task["id"],),
        )

    validated = CreativeWorkflowAdapter(store, workflow).assert_task_for_creation(
        task["id"],
        "brief",
        SYSTEM_ACTOR,
        project_id=project["id"],
    )

    assert validated["project_id"] == legacy_id
