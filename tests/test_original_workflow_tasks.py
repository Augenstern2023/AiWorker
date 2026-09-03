# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import os
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.engine import make_url

from identity.repository import IdentityRepository
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository


@pytest.fixture
def workflow_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> WorkflowRepository:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the production system database")
    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    identity = IdentityRepository(database)
    identity.seed_defaults()
    return WorkflowRepository(identity)


def test_original_project_tasks_use_project_as_the_only_resource(
    workflow_repository: WorkflowRepository,
) -> None:
    project_id = f"project-original-{uuid.uuid4().hex}"
    try:
        first = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        repeated = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )

        assert repeated["id"] == first["id"]
        assert first["topic_id"] is None
        assert first["project_id"] == project_id
        with workflow_repository.identity_repository.connect() as connection:
            workflow = connection.exec_driver_sql(
                "SELECT resource_type, resource_id FROM workflow_instances WHERE id = %s",
                (first["workflow_id"],),
            ).mappings().one()
        assert workflow["resource_type"] == "project"
        assert workflow["resource_id"] == project_id
    finally:
        workflow_repository.delete_project_data(project_id)


def test_next_original_task_preserves_null_topic(
    workflow_repository: WorkflowRepository,
) -> None:
    project_id = f"project-original-{uuid.uuid4().hex}"
    try:
        brief = workflow_repository.ensure_project_task(
            project_id, None,
            task_type="brief", role_code="creative_planner", actor=SYSTEM_ACTOR,
        )
        characters = workflow_repository.create_next_task(
            brief["id"], "characters", "creative_planner", actor=SYSTEM_ACTOR
        )

        assert characters["topic_id"] is None
        assert characters["project_id"] == brief["project_id"]
    finally:
        workflow_repository.delete_project_data(project_id)


def test_writer_rollback_snapshot_restores_tasks_and_cancels_new_work(
    workflow_repository: WorkflowRepository,
) -> None:
    """Would catch restored creative content leaving workflow tasks advanced."""

    project_id = f"project-writer-rollback-{uuid.uuid4().hex}"
    try:
        original = workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="brief",
            role_code="creative_planner",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )
        before = workflow_repository.export_writer_rollback_state(project_id)
        with workflow_repository.identity_repository.transaction() as connection:
            connection.exec_driver_sql(
                "UPDATE workflow_tasks SET status = %s WHERE id = %s",
                ("in_progress", original["id"]),
            )
        created = workflow_repository.create_task(
            workflow_id=f"project:{project_id}",
            task_type="script",
            role_code="screenwriter",
            project_id=project_id,
            actor=SYSTEM_ACTOR,
        )
        after_fingerprint = workflow_repository.writer_rollback_fingerprint(
            project_id
        )

        workflow_repository.restore_writer_rollback_state(
            project_id,
            before,
            expected_fingerprint=after_fingerprint,
            actor=SYSTEM_ACTOR,
        )

        with workflow_repository.identity_repository.connect() as connection:
            rows = {
                row["id"]: row["status"]
                for row in connection.exec_driver_sql(
                    "SELECT id, status FROM workflow_tasks WHERE project_id = %s",
                    (project_id,),
                ).mappings()
            }
            rollback_events = connection.exec_driver_sql(
                "SELECT COUNT(*) AS count FROM workflow_task_events "
                "WHERE task_id = %s AND event_type = %s",
                (original["id"], "writer.rollback"),
            ).mappings().one()["count"]
        assert rows[original["id"]] == "pool"
        assert rows[created["id"]] == "cancelled"
        assert rollback_events == 1
        assert (
            workflow_repository.writer_rollback_fingerprint(project_id)
            == before["fingerprint"]
        )
    finally:
        workflow_repository.delete_project_data(project_id)
