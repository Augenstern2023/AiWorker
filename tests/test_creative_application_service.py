# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.job import CrawlJob
from creative.application_service import (
    MAX_INLINE_SOURCE_SNAPSHOT_BYTES,
    CreativeApplicationService,
)
from creative.repository import CreativeRepository
from identity.schema import Principal


@pytest.fixture
def creative_context(tmp_path: Path) -> SimpleNamespace:
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.ensure_project_task.return_value = {
        "id": "brief-task",
        "task_type": "brief",
        "status": "in_progress",
        "project_id": "1",
        "assignee_user_id": "writer-1",
    }
    transitions = MagicMock()
    jobs = MagicMock()
    workflow_adapter = MagicMock(unsafe=True)
    permission_service = MagicMock()
    permission_service.can_read_project.return_value = True
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    service = CreativeApplicationService(
        repository,
        workflow,
        transitions,
        jobs,
        tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permission_service,
    )

    def create_original_project() -> dict:
        project, _ = repository.ensure_original_project(
            seed_text="未来公司按照员工情绪发工资",
            name="情绪公司",
            target_episode_count=1,
            created_by=actor.user_id,
            creation_request_id="creative-service-test",
        )
        workflow.ensure_project_task.return_value["project_id"] = str(project["id"])
        return project

    return SimpleNamespace(
        application_service=service,
        repository=repository,
        workflow=workflow,
        transitions=transitions,
        jobs=jobs,
        workflow_adapter=workflow_adapter,
        permission_service=permission_service,
        actor=actor,
        create_original_project=create_original_project,
    )


def valid_original_brief(title: str) -> dict[str, Any]:
    return {
        "title": title,
        "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择",
        "protagonist": "林岚",
        "protagonist_desire": "获得自主权",
        "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作",
        "main_opposition": "僵化制度",
        "core_conflict": "情绪考核",
        "main_goal": "改变制度",
        "background": "未来公司",
        "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核",
        "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"],
        "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁",
        "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low",
        "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"],
        "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }


def test_application_service_creates_first_original_brief_idempotently(
    creative_context,
) -> None:
    service = creative_context.application_service
    project = creative_context.create_original_project()
    task = service.ensure_stage_task(project["id"], "brief", creative_context.actor)
    brief = valid_original_brief(title="情绪公司")

    first = service.persist_draft(
        project_id=project["id"],
        stage="brief",
        payload=brief,
        workflow_task_id=task["id"],
        operation_id="act-1:brief",
        actor=creative_context.actor,
    )
    second = service.persist_draft(
        project_id=project["id"],
        stage="brief",
        payload=brief,
        workflow_task_id=task["id"],
        operation_id="act-1:brief",
        actor=creative_context.actor,
    )

    assert second == first
    assert first["resource_type"] == "brief"
    assert first["status"] == "draft"
    creative_context.workflow_adapter.assert_task_for_creation.assert_called_with(
        task["id"], "brief", creative_context.actor, project_id=project["id"]
    )
    creative_context.permission_service.can_create_project_content.assert_called()


def test_application_service_generation_job_contains_context(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.jobs.create_job.return_value = SimpleNamespace(
        job_id="job-7", status="pending"
    )

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="outline",
        actor=creative_context.actor,
        workflow_task_id="outline-task",
        operation_id="act-7:outline",
        generation_instruction="加强第3集反转",
        source_snapshot_id="snapshot-7",
        source_snapshot={"project": {"id": project["id"], "revision": 7}},
    )

    payload = creative_context.jobs.create_job.call_args.args[0]
    job = SimpleNamespace(job=CrawlJob.from_dict({"job_id": "job-7", **payload}))
    assert job.job.extra["generation_instruction"] == "加强第3集反转"
    assert job.job.extra["source_snapshot_id"] == "snapshot-7"
    assert job.job.extra["operation_id"] == "act-7:outline"


def test_application_service_spills_large_snapshot_to_relative_chunks(
    creative_context,
) -> None:
    project = creative_context.create_original_project()
    creative_context.jobs.create_job.return_value = SimpleNamespace(
        job_id="job-large", status="pending"
    )
    snapshot = {
        "snapshot_id": "snapshot-large",
        "material": "字" * MAX_INLINE_SOURCE_SNAPSHOT_BYTES,
    }

    creative_context.application_service.start_generation(
        project_id=project["id"],
        stage="brief",
        actor=creative_context.actor,
        workflow_task_id="brief-task",
        operation_id="act-large:brief",
        generation_instruction="保留材料中的因果关系",
        source_snapshot_id="snapshot-large",
        source_snapshot=snapshot,
    )

    extra = creative_context.jobs.create_job.call_args.args[0]["extra"]
    assert "source_snapshot" not in extra
    reference = extra["source_snapshot_ref"]
    assert reference["read_only"] is True
    assert len(reference["paths"]) > 1
    assert all(not Path(path).is_absolute() for path in reference["paths"])
    assert all(
        (creative_context.application_service.runtime_dir / path).is_file()
        for path in reference["paths"]
    )
