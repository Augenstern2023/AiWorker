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
from creative.workflow_identity_migration import (
    migrate_legacy_workflow_project_identities,
)
from identity.repository import IdentityRepository
from workflow.repository import WorkflowRepository


class LegacyWorkflowRepository:
    def __init__(self, task: dict) -> None:
        self.task = task
        self.migrations: list[tuple[str, str]] = []

    def get_task(self, _task_id: str) -> dict:
        return self.task

    def migrate_legacy_project_identity(
        self, legacy_project_id: str, scoped_project_id: str
    ) -> bool:
        self.migrations.append((legacy_project_id, scoped_project_id))
        if self.task["project_id"] != legacy_project_id:
            return False
        self.task["project_id"] = scoped_project_id
        return True


def test_explicit_migration_repairs_legacy_task_project_binding(
    tmp_path: Path,
) -> None:
    creative = CreativeRepository(tmp_path / "creative.db")
    project, _ = creative.ensure_original_project(
        seed_text="测试种子",
        name="历史项目",
        target_episode_count=1,
        created_by="writer-1",
        creation_request_id="legacy-workflow-project",
    )
    task = {
        "id": "legacy-brief-task",
        "task_type": "brief",
        "assignee_user_id": "writer-1",
        "project_id": str(project["id"]),
        "topic_id": None,
        "status": "in_progress",
    }
    workflow = LegacyWorkflowRepository(task)

    assert migrate_legacy_workflow_project_identities(creative, workflow) == 1
    assert migrate_legacy_workflow_project_identities(creative, workflow) == 0
    assert workflow.migrations[0] == (
        str(project["id"]), creative.workflow_project_id(project["id"])
    )
    assert task["project_id"] == creative.workflow_project_id(project["id"])


def test_webgui_does_not_run_unproven_legacy_identity_migration_on_startup() -> None:
    source = (Path(__file__).resolve().parents[1] / "WebGUI.py").read_text(
        encoding="utf-8"
    )

    assert "migrate_legacy_workflow_project_identities" not in source


def test_postgres_migration_moves_tasks_instances_and_memberships(
    tmp_path: Path, isolated_system_database
) -> None:
    identity = IdentityRepository(isolated_system_database)
    identity.seed_defaults()
    user = identity.create_user(
        f"legacy-writer-{uuid.uuid4().hex}", "历史编剧", "hash"
    )
    creative = CreativeRepository(tmp_path / "creative.db")
    project, _ = creative.ensure_original_project(
        seed_text="测试种子",
        name="PostgreSQL 历史项目",
        target_episode_count=1,
        created_by=user["id"],
        creation_request_id="postgres-legacy-workflow-project",
    )
    legacy = str(project["id"])
    scoped = creative.workflow_project_id(project["id"])
    workflow_id = f"project:{legacy}"
    task_id = str(uuid.uuid4())
    membership_id = str(uuid.uuid4())
    with identity.transaction() as connection:
        connection.exec_driver_sql(
            "INSERT INTO workflow_instances "
            "(id, workflow_type, resource_type, resource_id, project_id, created_by) "
            "VALUES (%s, 'brief', 'project', %s, %s, %s)",
            (workflow_id, legacy, legacy, user["id"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_tasks "
            "(id, workflow_id, task_type, role_code, project_id, assignee_id, status) "
            "VALUES (%s, %s, 'brief', 'creative_planner', %s, %s, 'in_progress')",
            (task_id, workflow_id, legacy, user["id"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO project_members "
            "(id, project_id, user_id, project_role, status, added_by) "
            "VALUES (%s, %s, %s, 'creative_planner', 'active', %s)",
            (membership_id, legacy, user["id"], user["id"]),
        )

    workflow = WorkflowRepository(identity)
    assert workflow.migrate_legacy_project_identity(legacy, scoped) is True
    assert workflow.migrate_legacy_project_identity(legacy, scoped) is False
    assert workflow.get_task(task_id)["project_id"] == scoped
    assert workflow.list_project_members(legacy) == []
    assert workflow.list_project_members(scoped)[0]["id"] == membership_id
    with identity.connect() as connection:
        instance = connection.exec_driver_sql(
            "SELECT project_id, resource_id FROM workflow_instances WHERE id = %s",
            (workflow_id,),
        ).mappings().one()
    assert instance == {"project_id": scoped, "resource_id": scoped}
