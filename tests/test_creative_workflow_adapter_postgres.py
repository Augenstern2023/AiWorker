# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Dynamic boundary tests for the creative workflow adapter on PostgreSQL.

``CreativeWorkflowAdapter`` used to reach into its creative store with
``self.creative_repository._connect()`` and run ``?``-placeholder SQLite SQL.
That worked while the creative store was SQLite and raised ``AttributeError``
the moment ``PostgresCreativeRepository`` was injected, so the adapter could
not be trusted on the production backend.

A text scan cannot prove the methods work, so every method that used to poke
the private connection (``audit_legacy_force``, ``domain_status``,
``on_approved``, ``on_returned``, ``reconcile_resource``, ``submit_draft``) is
exercised here against a real PostgreSQL creative repository, and the stored
creative rows are read back to prove the writes landed.

The workflow side of the adapter is replaced by a recording double on purpose:
``workflow/repository.py`` still writes ``time.time()`` floats into the
``timestamp with time zone`` columns of ``workflow_submissions`` /
``workflow_tasks``, so a real submission cannot be drafted or submitted on
PostgreSQL at all.  That is a separate defect on the workflow store, outside
this adapter's port; the double keeps this module's assertions on the creative
rows the adapter owns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, update

import workflow.approval_service as approval_service_module
from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
)
from creative.postgres_repository import PostgresCreativeRepository
from creative.repositories.base import utc_now
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.schema import Principal

ADAPTER_PATH = Path(__file__).resolve().parents[1] / "creative" / "workflow_adapter.py"

RESOURCE_TYPES = ("breakdown", "brief", "characters", "outline", "beats")

TABLES = {
    "breakdown": creative_breakdowns,
    "brief": creative_blueprints,
}


class RecordingWorkflowRepository:
    """Minimal workflow double that records the calls the adapter mirrors."""

    def __init__(self) -> None:
        self.superseded: list[tuple[str, str]] = []
        self.forces: list[tuple[str, str, str]] = []
        self.mirror_errors: list[tuple[str, str]] = []
        self.approved: list[dict[str, Any]] = []
        self.latest: dict[str, Any] | None = None
        self.submissions: dict[str, dict[str, Any]] = {}
        self.resource_ids: list[tuple[str, str]] = []
        self.task: dict[str, Any] | None = None

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        if self.task is None or self.task["id"] != task_id:
            return None
        return self.task

    def list_approved_submissions(self, resource_type: str) -> list[dict[str, Any]]:
        return [
            item for item in self.approved if item["resource_type"] == resource_type
        ]

    def get_latest_resource_submission(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        if self.latest is None:
            return None
        if (self.latest["resource_type"], self.latest["resource_id"]) != (
            resource_type,
            resource_id,
        ):
            return None
        return self.latest

    def supersede_resource_approvals(
        self,
        resource_type: str,
        resource_id: str,
        actor: Principal,
        *,
        connection: Any = None,
    ) -> None:
        del actor, connection
        self.superseded.append((resource_type, resource_id))

    def record_legacy_force(
        self, task_id: str, actor: Principal, resource_type: str, resource_id: str
    ) -> None:
        del actor
        self.forces.append((task_id, resource_type, resource_id))

    def record_mirror_error(self, task_id: str, submission: dict, message: str) -> None:
        del submission
        self.mirror_errors.append((task_id, message))

    def update_submission_resource_id(
        self, submission_id: str, resource_id: str, connection: Any = None
    ) -> None:
        del connection
        self.resource_ids.append((submission_id, resource_id))
        self.submissions[submission_id] = {
            "id": submission_id,
            "resource_id": resource_id,
            "status": "returned",
        }

    def get_submission(self, submission_id: str, connection: Any = None) -> dict:
        del connection
        return self.submissions[submission_id]


class StubApprovalService:
    """Stands in for ``workflow.approval_service.ApprovalService``.

    The adapter only needs ``submit`` to hand back the submitted submission;
    everything else it does with the result is the creative write under test.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def submit(
        self,
        submission_id: str,
        actor: Principal,
        *,
        content_snapshot: Any = None,
    ) -> dict[str, Any]:
        del actor, content_snapshot
        return {"id": submission_id, "status": "submitted"}


def _principal(role_codes: tuple[str, ...] = ("system_admin",)) -> Principal:
    return Principal(
        user_id="user-1",
        username="user",
        display_name="User",
        role_codes=role_codes,
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-user",
    )


def _seed_resources(creative: PostgresCreativeRepository) -> dict[str, Any]:
    """Seed one project with every creative resource type, approved and locked."""

    topic = creative.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-creative-workflow-postgres",
                "source_path": "workflow.json",
                "captured_at": "2026-07-22",
                "title": "审批短剧",
                "description": "",
                "category": "都市",
                "rank_type": "热播榜",
                "score": 90,
                "confidence": "high",
                "score_details": {},
                "recommendation": "推荐",
                "evidence": [],
            }
        ]
    )[0]
    creative.update_topic(int(topic["id"]), "adopted")
    breakdown = creative.create_breakdown(
        int(topic["id"]), {"core": "conflict"}, {}, []
    )
    creative.lock_breakdown(int(breakdown["id"]))
    project = creative.create_project(
        int(topic["id"]), int(breakdown["id"]), "审批项目", 1
    )
    project_id = int(project["id"])
    brief = creative.create_blueprints(
        project_id, [{"content": {"title": "方向"}, "risk_level": "low"}]
    )[0]
    creative.lock_brief(int(brief["id"]))
    characters = creative.create_character_batch(
        project_id, [{"name": "主角", "desire": "翻身"}], []
    )
    creative.lock_character_batch(project_id, characters["batch_id"])
    # The character authority fingerprint covers relationships too, so the row
    # has to exist before the outline/beats refs are generated from it.
    with creative.transaction_scope() as connection:
        connection.execute(
            insert(creative_relationships).values(
                project_id=project_id,
                batch_id=characters["batch_id"],
                version=1,
                relationship_key="relationship-1",
                status="locked",
                content_json={"from": "主角", "to": "对手"},
                source_refs_json={"brief_id": int(brief["id"])},
                generation_meta_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    outline = creative.create_outline_batch(
        project_id, [{"episode": 1, "summary": "开局"}], "成长"
    )
    creative.lock_outline_batch(project_id, outline["batch_id"])
    beats = creative.create_episode_beats(
        project_id, 1, {"beats": [{"order": 1, "scene": "家中"}]}
    )
    now = utc_now()
    with creative.transaction_scope() as connection:
        connection.execute(
            update(creative_breakdowns)
            .where(creative_breakdowns.c.id == int(breakdown["id"]))
            .values(status="approved", updated_at=now)
        )
        connection.execute(
            update(creative_blueprints)
            .where(creative_blueprints.c.id == int(brief["id"]))
            .values(status="approved", updated_at=now)
        )
        for table in (creative_characters, creative_relationships):
            connection.execute(
                update(table)
                .where(
                    table.c.project_id == project_id,
                    table.c.batch_id == characters["batch_id"],
                )
                .values(status="approved", updated_at=now)
            )
        connection.execute(
            update(creative_episodes)
            .where(
                creative_episodes.c.project_id == project_id,
                creative_episodes.c.stage == "outline",
                creative_episodes.c.batch_id == outline["batch_id"],
            )
            .values(status="approved", updated_at=now)
        )
        connection.execute(
            update(creative_episodes)
            .where(creative_episodes.c.id == int(beats["id"]))
            .values(status="draft", updated_at=now)
        )
    return {
        "topic_id": int(topic["id"]),
        "project_id": project_id,
        "breakdown": str(breakdown["id"]),
        "brief": str(brief["id"]),
        "characters": f"{project_id}:{characters['batch_id']}",
        "outline": f"{project_id}:{outline['batch_id']}",
        "beats": str(beats["id"]),
    }


def _make_resource_draft(
    creative: PostgresCreativeRepository, resource_type: str, resource_id: str
) -> None:
    """Put a seeded resource back into the editable state its task expects."""

    now = utc_now()
    with creative.transaction_scope() as connection:
        if resource_type == "breakdown":
            connection.execute(
                update(creative_breakdowns)
                .where(creative_breakdowns.c.id == int(resource_id))
                .values(status="draft", updated_at=now)
            )
            return
        if resource_type == "brief":
            brief = creative.get_blueprint(int(resource_id))
            assert brief is not None
            connection.execute(
                update(creative_blueprints)
                .where(creative_blueprints.c.id == int(resource_id))
                .values(status="draft", updated_at=now)
            )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(brief["project_id"]))
                .values(
                    locked_brief_id=None,
                    character_batch_id=None,
                    outline_batch_id=None,
                    updated_at=now,
                )
            )
            return
        if resource_type == "characters":
            project_id, batch_id = resource_id.split(":", 1)
            for table in (creative_characters, creative_relationships):
                connection.execute(
                    update(table)
                    .where(
                        table.c.project_id == int(project_id),
                        table.c.batch_id == batch_id,
                    )
                    .values(status="draft", updated_at=now)
                )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(character_batch_id=None, outline_batch_id=None, updated_at=now)
            )
            return
        if resource_type == "outline":
            project_id, batch_id = resource_id.split(":", 1)
            connection.execute(
                update(creative_episodes)
                .where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == "outline",
                    creative_episodes.c.batch_id == batch_id,
                )
                .values(status="draft", updated_at=now)
            )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(outline_batch_id=None, updated_at=now)
            )


def _submission(
    adapter: CreativeWorkflowAdapter,
    resource_type: str,
    resource_id: str,
    *,
    submission_id: str = "submission-1",
    task_id: str = "task-1",
    status: str = "submitted",
) -> dict[str, Any]:
    return {
        "id": submission_id,
        "task_id": task_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "content_snapshot": adapter.snapshot(
            resource_type, resource_id, reconcile=False
        ),
        "reviewed_at": 1000,
    }


def _status(creative: PostgresCreativeRepository, table, **criteria: Any) -> str:
    with creative.connection_scope() as connection:
        return str(
            connection.execute(
                select(table.c.status).where(
                    *[table.c[column] == value for column, value in criteria.items()]
                )
            ).scalar_one()
        )


def _statuses(creative: PostgresCreativeRepository, table, **criteria: Any) -> set[str]:
    with creative.connection_scope() as connection:
        return {
            str(value)
            for value in connection.execute(
                select(table.c.status).where(
                    *[table.c[column] == value for column, value in criteria.items()]
                )
            ).scalars()
        }


def _task_link(
    creative: PostgresCreativeRepository, table, **criteria: Any
) -> str | None:
    with creative.connection_scope() as connection:
        return connection.execute(
            select(table.c.workflow_task_id).where(
                *[table.c[column] == value for column, value in criteria.items()]
            )
        ).scalar_one()


def test_domain_status_reads_postgres_rows_for_every_resource_type(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    adapter = CreativeWorkflowAdapter(creative, RecordingWorkflowRepository())

    assert adapter.domain_status("breakdown", resources["breakdown"]) == "approved"
    assert adapter.domain_status("brief", resources["brief"]) == "approved"
    assert adapter.domain_status("characters", resources["characters"]) == "approved"
    assert adapter.domain_status("outline", resources["outline"]) == "approved"
    assert adapter.domain_status("beats", resources["beats"]) == "draft"

    project_id = resources["project_id"]
    _, batch_id = resources["characters"].split(":", 1)
    with creative.transaction_scope() as connection:
        connection.execute(
            update(creative_characters)
            .where(
                creative_characters.c.project_id == project_id,
                creative_characters.c.batch_id == batch_id,
            )
            .values(status="locked", updated_at=utc_now())
        )

    assert adapter.domain_status("characters", resources["characters"]) == "locked"


def test_domain_status_raises_key_error_for_an_unknown_resource(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    adapter = CreativeWorkflowAdapter(creative, RecordingWorkflowRepository())

    with pytest.raises(KeyError):
        adapter.domain_status("breakdown", "987654321")
    with pytest.raises(KeyError):
        adapter.domain_status("brief", "987654321")
    with pytest.raises(KeyError):
        adapter.domain_status("characters", f"{resources['project_id']}:missing")
    with pytest.raises(KeyError):
        adapter.domain_status("outline", f"{resources['project_id']}:missing")
    # an episode whose stage does not match the requested resource type
    with pytest.raises(KeyError):
        adapter.domain_status("script", resources["beats"])


def test_set_domain_status_writes_status_and_workflow_task_id(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    adapter = CreativeWorkflowAdapter(creative, RecordingWorkflowRepository())
    project_id, batch_id = resources["characters"].split(":", 1)

    adapter.set_domain_status(
        "characters",
        resources["characters"],
        "submitted",
        workflow_task_id="task-characters",
    )
    for table in (creative_characters, creative_relationships):
        with creative.connection_scope() as connection:
            rows = connection.execute(
                select(table.c.status, table.c.workflow_task_id).where(
                    table.c.project_id == int(project_id),
                    table.c.batch_id == batch_id,
                )
            ).mappings().all()
        assert rows, table.name
        assert {row["status"] for row in rows} == {"submitted"}
        assert {row["workflow_task_id"] for row in rows} == {"task-characters"}

    adapter.set_domain_status("outline", resources["outline"], "returned")
    assert (
        _status(
            creative,
            creative_episodes,
            project_id=int(project_id),
            batch_id=resources["outline"].split(":", 1)[1],
        )
        == "returned"
    )

    adapter.set_domain_status(
        "breakdown", resources["breakdown"], "draft", workflow_task_id="task-breakdown"
    )
    breakdown = creative.get_breakdown(int(resources["breakdown"]))
    assert breakdown is not None
    assert breakdown["status"] == "draft"
    assert breakdown["workflow_task_id"] == "task-breakdown"

    # omitting the task id must keep the previously linked task
    adapter.set_domain_status("breakdown", resources["breakdown"], "submitted")
    breakdown = creative.get_breakdown(int(resources["breakdown"]))
    assert breakdown is not None
    assert breakdown["status"] == "submitted"
    assert breakdown["workflow_task_id"] == "task-breakdown"

    adapter.set_domain_status("beats", resources["beats"], "draft")
    assert _status(creative, creative_episodes, id=int(resources["beats"])) == "draft"


def test_on_approved_switches_the_brief_pointer_and_supersedes_the_previous_brief(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    project_id = resources["project_id"]
    previous = int(resources["brief"])
    revision_before = int(creative.get_project(project_id)["revision"])
    candidate = creative.create_blueprints(
        project_id, [{"content": {"title": "新方向"}, "risk_level": "low"}]
    )[0]
    candidate_id = int(candidate["id"])
    actor = _principal(("project_owner",))

    approved = adapter.on_approved(
        _submission(adapter, "brief", str(candidate_id)), actor
    )

    project = creative.get_project(project_id)
    assert project["locked_brief_id"] == candidate_id
    assert project["revision"] == revision_before + 1
    assert approved["revision"] == revision_before + 1
    assert _status(creative, creative_blueprints, id=candidate_id) == "approved"
    assert _status(creative, creative_blueprints, id=previous) == "superseded"
    assert workflow.superseded == [("brief", str(previous))]

    replayed = adapter.on_approved(
        _submission(adapter, "brief", str(candidate_id)), actor
    )
    assert creative.get_project(project_id)["revision"] == revision_before + 1
    assert replayed["revision"] == revision_before + 1


def test_on_approved_supersedes_the_previous_approved_breakdown(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    source = resources["breakdown"]

    clone_id = adapter._clone("breakdown", source, "task-breakdown")
    approved = adapter.on_approved(
        _submission(adapter, "breakdown", clone_id), _principal()
    )

    assert approved["resource_id"] == clone_id
    assert _status(creative, creative_breakdowns, id=int(clone_id)) == "approved"
    assert _status(creative, creative_breakdowns, id=int(source)) == "superseded"
    assert workflow.superseded == [("breakdown", source)]
    clone = creative.get_breakdown(int(clone_id))
    original = creative.get_breakdown(int(source))
    assert clone is not None and original is not None
    assert clone["parent_version_id"] == int(source)
    assert clone["version"] == original["version"] + 1


def test_on_approved_switches_the_character_batch_pointer(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    project_id = resources["project_id"]
    previous_batch = resources["characters"].split(":", 1)[1]
    revision_before = int(creative.get_project(project_id)["revision"])
    candidate = creative.create_character_batch(
        project_id, [{"name": "新主角", "desire": "赢得自由"}], []
    )
    candidate_id = f"{project_id}:{candidate['batch_id']}"

    approved = adapter.on_approved(
        _submission(adapter, "characters", candidate_id), _principal(("project_owner",))
    )

    project = creative.get_project(project_id)
    assert project["character_batch_id"] == candidate["batch_id"]
    assert project["revision"] == revision_before + 1
    assert approved["revision"] == revision_before + 1
    for table in (creative_characters, creative_relationships):
        assert (
            _statuses(
                creative, table, project_id=project_id, batch_id=candidate["batch_id"]
            )
            <= {"approved"}
        )
        assert _statuses(
            creative, table, project_id=project_id, batch_id=previous_batch
        ) == {"superseded"}
    assert _statuses(
        creative, creative_characters, project_id=project_id, batch_id=candidate["batch_id"]
    ) == {"approved"}
    assert workflow.superseded == [("characters", f"{project_id}:{previous_batch}")]


def test_on_approved_switches_the_outline_batch_pointer(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    project_id = resources["project_id"]
    previous_batch = resources["outline"].split(":", 1)[1]
    revision_before = int(creative.get_project(project_id)["revision"])
    candidate = creative.create_outline_batch(
        project_id, [{"episode": 1, "summary": "评审通过的新开局"}], "新成长线"
    )
    candidate_id = f"{project_id}:{candidate['batch_id']}"

    approved = adapter.on_approved(
        _submission(adapter, "outline", candidate_id), _principal(("project_owner",))
    )

    project = creative.get_project(project_id)
    assert project["outline_batch_id"] == candidate["batch_id"]
    assert project["revision"] == revision_before + 1
    assert approved["revision"] == revision_before + 1
    assert (
        _status(
            creative,
            creative_episodes,
            project_id=project_id,
            batch_id=candidate["batch_id"],
        )
        == "approved"
    )
    assert (
        _status(
            creative, creative_episodes, project_id=project_id, batch_id=previous_batch
        )
        == "superseded"
    )
    assert workflow.superseded == [("outline", f"{project_id}:{previous_batch}")]


def test_on_approved_supersedes_the_previous_beats_version(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    actor = _principal(("project_owner",))
    first = resources["beats"]

    adapter.on_approved(_submission(adapter, "beats", first), actor)
    assert _status(creative, creative_episodes, id=int(first)) == "approved"

    second = adapter._clone("beats", first, "task-beats")
    adapter.on_approved(_submission(adapter, "beats", second), actor)

    assert _status(creative, creative_episodes, id=int(second)) == "approved"
    assert _status(creative, creative_episodes, id=int(first)) == "superseded"
    assert workflow.superseded == [("beats", first)]
    clone = creative.get_episode(int(second))
    original = creative.get_episode(int(first))
    assert clone is not None and original is not None
    assert clone["stage"] == "beats"
    assert clone["parent_version_id"] == int(first)
    assert clone["version"] == original["version"] + 1


def test_audit_legacy_force_records_the_task_linked_to_the_postgres_row(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    actor = _principal()

    adapter.set_domain_status(
        "breakdown", resources["breakdown"], "draft", workflow_task_id="task-breakdown"
    )
    adapter.audit_legacy_force("breakdown", resources["breakdown"], actor)
    assert workflow.forces == [("task-breakdown", "breakdown", resources["breakdown"])]

    adapter.set_domain_status(
        "characters",
        resources["characters"],
        "draft",
        workflow_task_id="task-characters",
    )
    adapter.audit_legacy_force("characters", resources["characters"], actor)
    assert workflow.forces[-1] == (
        "task-characters",
        "characters",
        resources["characters"],
    )

    adapter.set_domain_status(
        "outline", resources["outline"], "draft", workflow_task_id="task-outline"
    )
    adapter.audit_legacy_force("outline", resources["outline"], actor)
    assert workflow.forces[-1] == ("task-outline", "outline", resources["outline"])

    adapter.set_domain_status(
        "beats", resources["beats"], "draft", workflow_task_id="task-beats"
    )
    adapter.audit_legacy_force("beats", resources["beats"], actor)
    assert workflow.forces[-1] == ("task-beats", "beats", resources["beats"])

    with pytest.raises(ValueError, match="工作任务"):
        adapter.audit_legacy_force("brief", resources["brief"], actor)


def test_reconcile_resource_mirrors_a_returned_submission_onto_postgres(
    postgres_creative: PostgresCreativeRepository,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    workflow.latest = {
        "id": "submission-returned",
        "task_id": "task-brief",
        "resource_type": "brief",
        "resource_id": resources["brief"],
        "status": "returned",
    }

    adapter.reconcile_resource("brief", resources["brief"])

    assert (
        _status(creative, creative_blueprints, id=int(resources["brief"])) == "returned"
    )
    assert workflow.mirror_errors == []


def test_submit_draft_marks_the_postgres_row_submitted_with_the_task_id(
    postgres_creative: PostgresCreativeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    _make_resource_draft(creative, "brief", resources["brief"])
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    actor = _principal(("creative_planner",))
    task_id = "task-brief-submit"
    workflow.task = {
        "id": task_id,
        "task_type": "brief",
        "project_id": creative.workflow_project_id(resources["project_id"]),
        "topic_id": None,
        "assignee_user_id": actor.user_id,
        "status": "in_progress",
    }
    workflow.submissions["submission-brief"] = {
        "id": "submission-brief",
        "task_id": task_id,
        "resource_type": "brief",
        "resource_id": resources["brief"],
        "status": "draft",
        "content_snapshot": adapter.snapshot("brief", resources["brief"]),
        "version": 1,
    }
    monkeypatch.setattr(
        approval_service_module, "ApprovalService", StubApprovalService
    )

    submitted = adapter.submit_draft("submission-brief", actor)

    assert submitted["status"] == "submitted"
    brief_id = int(resources["brief"])
    assert _status(creative, creative_blueprints, id=brief_id) == "submitted"
    assert _task_link(creative, creative_blueprints, id=brief_id) == task_id


@pytest.mark.parametrize("resource_type", RESOURCE_TYPES)
def test_submit_approve_return_and_clone_change_real_postgres_rows(
    postgres_creative: PostgresCreativeRepository,
    monkeypatch: pytest.MonkeyPatch,
    resource_type: str,
) -> None:
    creative = postgres_creative
    resources = _seed_resources(creative)
    resource_id = resources[resource_type]
    _make_resource_draft(creative, resource_type, resource_id)
    workflow = RecordingWorkflowRepository()
    adapter = CreativeWorkflowAdapter(creative, workflow)
    actor = _principal()
    task_id = f"task-{resource_type}"
    workflow.task = {
        "id": task_id,
        "task_type": resource_type,
        "project_id": creative.workflow_project_id(resources["project_id"]),
        "topic_id": resources["topic_id"],
        "assignee_user_id": actor.user_id,
        "status": "in_progress",
    }
    workflow.submissions["submission-flow"] = {
        "id": "submission-flow",
        "task_id": task_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": "draft",
        "content_snapshot": adapter.snapshot(resource_type, resource_id),
        "version": 1,
    }
    monkeypatch.setattr(
        approval_service_module, "ApprovalService", StubApprovalService
    )

    submitted = adapter.submit_draft("submission-flow", actor)
    assert submitted["status"] == "submitted"
    assert adapter.domain_status(resource_type, resource_id) == "submitted"

    approved = adapter.on_approved(
        _submission(adapter, resource_type, resource_id, task_id=task_id), actor
    )
    assert approved["status"] == "submitted"
    assert adapter.domain_status(resource_type, resource_id) == "approved"

    clone_id = adapter._clone(resource_type, resource_id, task_id)
    assert clone_id != resource_id
    assert adapter.domain_status(resource_type, clone_id) == "draft"

    returned = adapter.on_returned(
        _submission(
            adapter,
            resource_type,
            clone_id,
            submission_id="submission-returned",
            task_id=task_id,
        ),
        {"id": "submission-draft"},
        actor,
    )
    assert adapter.domain_status(resource_type, clone_id) == "returned"
    new_id = returned["resource_id"]
    assert new_id != clone_id
    assert adapter.domain_status(resource_type, new_id) == "draft"
    assert workflow.resource_ids == [("submission-draft", new_id)]

    # returning the same version again must reuse the existing draft
    repeated = adapter.on_returned(
        _submission(
            adapter,
            resource_type,
            clone_id,
            submission_id="submission-returned",
            task_id=task_id,
        ),
        {"id": "submission-draft-2"},
        actor,
    )
    assert repeated["resource_id"] == new_id
    assert workflow.resource_ids[-1] == ("submission-draft-2", new_id)


def test_adapter_source_has_no_sqlite_connection_access() -> None:
    """A narrow scan, kept alongside the dynamic coverage above."""

    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert "_connect()" not in source
    assert "sqlite3" not in source
    assert "?" not in source
    assert not re.search(r"lastrowid|COALESCE\(\?", source)
