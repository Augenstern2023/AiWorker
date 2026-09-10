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
from identity.repository import IdentityRepository
from system_database import SystemDatabase
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


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
    workflow = WorkflowRepository(IdentityRepository(isolated_system_database))
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
    workflow = WorkflowRepository(IdentityRepository(isolated_system_database))
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
    workflow = WorkflowRepository(IdentityRepository(isolated_system_database))

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
