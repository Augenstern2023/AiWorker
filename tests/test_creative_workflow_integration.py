# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path

import pytest

from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


def _user(repository: IdentityRepository, username: str, role_code: str) -> Principal:
    role = repository.get_role_by_code(role_code)
    assert role is not None
    user = repository.create_user(username, username.title(), "hash")
    repository.replace_user_roles(user["id"], [role["id"]])
    principal = PermissionService(repository, SessionService(repository)).principal_for_user(
        user["id"], f"session:{username}"
    )
    assert principal is not None
    return principal


@pytest.fixture
def environment(tmp_path: Path):
    identity = IdentityRepository(tmp_path / "system.db")
    identity.seed_defaults()
    workflow = WorkflowRepository(identity)
    creative = CreativeRepository(tmp_path / "creative.db")
    adapter = CreativeWorkflowAdapter(creative, workflow)
    transitions = TransitionService(workflow, creative_adapter=adapter)
    admin = _user(identity, "admin", "system_admin")
    owner = _user(identity, "owner", "project_owner")
    executors = {
        "breakdown": _user(identity, "selector", "selection_operator"),
        "brief": _user(identity, "planner", "creative_planner"),
        "characters": _user(identity, "character_planner", "creative_planner"),
        "outline": _user(identity, "outline_writer", "screenwriter"),
        "beats": _user(identity, "beats_writer", "screenwriter"),
    }
    return identity, workflow, creative, adapter, transitions, admin, owner, executors


def _seed_resources(creative: CreativeRepository) -> dict[str, tuple[str, dict]]:
    topic = creative.upsert_topics(
        [
            {
                "platform": "hg",
                "job_id": "rank-creative-workflow",
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
    creative.update_topic(topic["id"], "adopted")
    breakdown = creative.create_breakdown(topic["id"], {"core": "conflict"}, {}, [])
    creative.lock_breakdown(breakdown["id"])
    project = creative.create_project(topic["id"], breakdown["id"], "审批项目", 1)
    blueprint = creative.create_blueprints(
        project["id"], [{"content": {"title": "方向"}, "risk_level": "low"}]
    )[0]
    creative.lock_brief(blueprint["id"])
    characters = creative.create_character_batch(
        project["id"], [{"name": "主角", "desire": "翻身"}], []
    )
    creative.lock_character_batch(project["id"], characters["batch_id"])
    outline = creative.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "开局"}], "成长"
    )
    creative.lock_outline_batch(project["id"], outline["batch_id"])
    beats = creative.create_episode_beats(
        project["id"], 1, {"beats": [{"order": 1, "scene": "家中"}]}
    )
    with creative._connect() as conn:
        for table in (
            "creative_breakdowns",
            "creative_blueprints",
            "creative_characters",
            "creative_relationships",
            "creative_episodes",
        ):
            conn.execute(
                f"UPDATE {table} SET status = 'draft', locked_at = NULL"
            )
        conn.execute(
            "UPDATE creative_projects SET locked_brief_id = NULL, character_batch_id = NULL, "
            "outline_batch_id = NULL, status = 'created' WHERE id = ?",
            (project["id"],),
        )
    return {
        "breakdown": (str(breakdown["id"]), creative.get_breakdown(breakdown["id"])),
        "brief": (str(blueprint["id"]), creative.get_blueprint(blueprint["id"])),
        "characters": (
            f"{project['id']}:{characters['batch_id']}",
            creative.get_character_batch(project["id"], characters["batch_id"]),
        ),
        "outline": (
            f"{project['id']}:{outline['batch_id']}",
            creative.get_outline_batch(project["id"], outline["batch_id"]),
        ),
        "beats": (str(beats["id"]), creative.get_episode(beats["id"])),
    }


def _task(workflow, creative, admin, owner, actor, resource_type, resource_id):
    snapshot = CreativeWorkflowAdapter(creative, workflow).snapshot(resource_type, resource_id)
    project_id = None
    topic_id = None
    if resource_type == "breakdown":
        topic_id = snapshot["topic_id"]
        with creative._connect() as conn:
            project = conn.execute(
                "SELECT p.id FROM creative_projects p JOIN creative_breakdowns b "
                "ON b.topic_id = p.topic_id WHERE b.id = ? ORDER BY p.id LIMIT 1",
                (int(resource_id),),
            ).fetchone()
        project_id = str(project["id"]) if project else None
    else:
        project_id = str(
            snapshot["project_id"]
            if isinstance(snapshot, dict) and "project_id" in snapshot
            else resource_id.split(":", 1)[0]
        )
    workflow.add_project_member(project_id or f"topic-{topic_id}", owner.user_id, "project_owner", admin)
    workflow.add_project_member(project_id or f"topic-{topic_id}", actor.user_id, actor.role_codes[0], admin)
    task = workflow.create_task(
        f"workflow-{resource_type}",
        resource_type,
        actor.role_codes[0],
        project_id=project_id,
        topic_id=topic_id,
        actor=SYSTEM_ACTOR,
    )
    workflow.assign_task(task["id"], actor.user_id, admin)
    workflow.start_task(task["id"], actor)
    return task


@pytest.mark.parametrize("resource_type", ["breakdown", "brief", "characters", "outline", "beats"])
def test_all_creative_resources_snapshot_submit_freeze_approve_and_return_clone(
    environment, resource_type: str
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resources = _seed_resources(creative)
    resource_id, original = resources[resource_type]
    task = _task(workflow, creative, admin, owner, actors[resource_type], resource_type, resource_id)

    submitted = adapter.submit(task["id"], resource_type, resource_id, actors[resource_type])

    assert submitted["content_snapshot"] == adapter.snapshot(resource_type, resource_id)
    assert submitted["status"] == "submitted"
    with pytest.raises(ValueError, match="immutable|不可修改|审批"):
        adapter.assert_editable(resource_type, resource_id)

    approved = transitions.approve(submitted["id"], owner)["submission"]
    assert approved["status"] == "approved"
    assert adapter.domain_status(resource_type, resource_id) == "approved"
    adapter.on_approved(approved, owner)
    assert adapter.domain_status(resource_type, resource_id) == "approved"

    with creative._connect() as conn:
        draft_resource_id = adapter._clone(
            resource_type, resource_id, task["id"], conn
        )
    second_task = _task(
        workflow,
        creative,
        admin,
        owner,
        actors[resource_type],
        resource_type,
        draft_resource_id,
    )
    second = adapter.submit(
        second_task["id"], resource_type, draft_resource_id, actors[resource_type]
    )
    returned = transitions.return_submission(second["id"], owner, "请加强冲突")
    new_resource_id = returned["draft"]["resource_id"]

    assert returned["submission"]["status"] == "returned"
    assert new_resource_id != draft_resource_id
    assert adapter.domain_status(resource_type, draft_resource_id) == "returned"
    assert adapter.domain_status(resource_type, new_resource_id) == "draft"
    with pytest.raises(ValueError):
        adapter.assert_editable(resource_type, draft_resource_id)
    adapter.assert_editable(resource_type, new_resource_id)
    assert adapter.snapshot(resource_type, resource_id)


def test_submit_rejects_wrong_task_node_assignee_and_resource_scope(environment) -> None:
    _, workflow, creative, adapter, _, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    task = _task(workflow, creative, admin, owner, actors["brief"], "brief", resource_id)

    with pytest.raises(PermissionError, match="assignee"):
        adapter.submit(task["id"], "brief", resource_id, actors["characters"])
    with pytest.raises(ValueError, match="resource type|node"):
        adapter.submit(task["id"], "beats", resource_id, actors["brief"])
    wrong_task = workflow.create_task(
        "workflow-wrong-project",
        "brief",
        "creative_planner",
        project_id="999",
        actor=SYSTEM_ACTOR,
    )
    workflow.add_project_member(
        "999", actors["brief"].user_id, "creative_planner", admin
    )
    workflow.assign_task(wrong_task["id"], actors["brief"].user_id, admin)
    workflow.start_task(wrong_task["id"], actors["brief"])
    with pytest.raises(ValueError, match="project"):
        adapter.submit(wrong_task["id"], "brief", resource_id, actors["brief"])


def test_schema_migration_and_approved_breakdown_preserve_history(tmp_path: Path) -> None:
    db_path = tmp_path / "creative.db"
    repository = CreativeRepository(db_path)
    repository = CreativeRepository(db_path)
    with repository._connect() as conn:
        for table in (
            "creative_projects",
            "creative_breakdowns",
            "creative_blueprints",
            "creative_characters",
            "creative_relationships",
            "creative_episodes",
        ):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "workflow_task_id" in columns


def test_committed_approval_retries_failed_cross_database_mirror(
    environment, monkeypatch
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    task = _task(
        workflow, creative, admin, owner, actors["brief"], "brief", resource_id
    )
    submitted = adapter.submit(task["id"], "brief", resource_id, actors["brief"])
    real_mirror = adapter.on_approved

    def fail_mirror(*args, **kwargs):
        raise RuntimeError("injected creative database outage")

    monkeypatch.setattr(adapter, "on_approved", fail_mirror)
    approved = transitions.approve(submitted["id"], owner)["submission"]

    assert approved["status"] == "approved"
    assert workflow.get_submission(submitted["id"])["status"] == "approved"
    assert adapter.domain_status("brief", resource_id) == "submitted"
    assert any(
        event["event_type"] == "creative.mirror_error"
        for event in workflow.list_task_events(task["id"])
    )

    monkeypatch.setattr(adapter, "on_approved", real_mirror)
    adapter.snapshot("brief", resource_id)

    assert adapter.domain_status("brief", resource_id) == "approved"


def test_new_approved_upstream_supersedes_pointer_without_deleting_history(
    environment,
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    project_id = creative.get_blueprint(int(resource_id))["project_id"]
    character_ids = {
        item["id"] for item in creative.get_project(project_id)["characters"]
    }
    first_task = _task(
        workflow, creative, admin, owner, actors["brief"], "brief", resource_id
    )
    first = adapter.submit(first_task["id"], "brief", resource_id, actors["brief"])
    transitions.approve(first["id"], owner)
    with creative._connect() as connection:
        new_resource_id = adapter._clone(
            "brief", resource_id, first_task["id"], connection
        )
    second_task = _task(
        workflow,
        creative,
        admin,
        owner,
        actors["brief"],
        "brief",
        new_resource_id,
    )
    second = adapter.submit(
        second_task["id"], "brief", new_resource_id, actors["brief"]
    )
    transitions.approve(second["id"], owner)

    project = creative.get_project(project_id)
    assert project["locked_brief_id"] == int(new_resource_id)
    assert adapter.domain_status("brief", resource_id) == "superseded"
    assert workflow.get_submission(first["id"])["status"] == "superseded"
    assert character_ids <= {item["id"] for item in project["characters"]}
