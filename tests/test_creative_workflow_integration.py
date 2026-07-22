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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creative.api import build_creative_router
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository
from workflow.api import build_workflow_router
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
        conn.execute("UPDATE creative_breakdowns SET status = 'approved', locked_at = NULL")
        conn.execute("UPDATE creative_blueprints SET status = 'approved', locked_at = NULL")
        conn.execute("UPDATE creative_characters SET status = 'approved', locked_at = NULL")
        conn.execute("UPDATE creative_relationships SET status = 'approved', locked_at = NULL")
        conn.execute("UPDATE creative_episodes SET status = 'approved', locked_at = NULL WHERE stage = 'outline'")
        conn.execute("UPDATE creative_episodes SET status = 'draft', locked_at = NULL WHERE stage = 'beats'")
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


def _make_resource_draft(
    creative: CreativeRepository, resource_type: str, resource_id: str
) -> None:
    with creative._connect() as connection:
        if resource_type == "brief":
            connection.execute(
                "UPDATE creative_blueprints SET status = 'draft' WHERE id = ?",
                (int(resource_id),),
            )
            connection.execute(
                "UPDATE creative_projects SET locked_brief_id = NULL, character_batch_id = NULL, outline_batch_id = NULL"
            )
        elif resource_type == "characters":
            project_id, batch_id = resource_id.split(":", 1)
            for table in ("creative_characters", "creative_relationships"):
                connection.execute(
                    f"UPDATE {table} SET status = 'draft' WHERE project_id = ? AND batch_id = ?",
                    (int(project_id), batch_id),
                )
            connection.execute(
                "UPDATE creative_projects SET character_batch_id = NULL, outline_batch_id = NULL WHERE id = ?",
                (int(project_id),),
            )
        elif resource_type == "outline":
            project_id, batch_id = resource_id.split(":", 1)
            connection.execute(
                "UPDATE creative_episodes SET status = 'draft' WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
                (int(project_id), batch_id),
            )
            connection.execute(
                "UPDATE creative_projects SET outline_batch_id = NULL WHERE id = ?",
                (int(project_id),),
            )
        elif resource_type == "breakdown":
            connection.execute(
                "UPDATE creative_breakdowns SET status = 'draft' WHERE id = ?",
                (int(resource_id),),
            )


def _edit_resource(
    creative: CreativeRepository, resource_type: str, resource_id: str
) -> None:
    marker = {"review_marker": f"edited-{resource_type}"}
    if resource_type == "breakdown":
        creative.update_breakdown(int(resource_id), marker)
    elif resource_type == "brief":
        creative.update_blueprint(int(resource_id), marker)
    elif resource_type == "characters":
        project_id, batch_id = resource_id.split(":", 1)
        item = creative.get_character_batch(int(project_id), batch_id)["characters"][0]
        creative.update_character(item["id"], marker)
    else:
        if resource_type == "outline":
            project_id, batch_id = resource_id.split(":", 1)
            item = creative.get_outline_batch(int(project_id), batch_id)["episodes"][0]
        else:
            item = creative.get_episode(int(resource_id))
        creative.update_episode(item["id"], marker)


def _snapshot_has_marker(snapshot: dict, resource_type: str) -> bool:
    expected = f"edited-{resource_type}"
    if resource_type in {"breakdown", "brief", "beats"}:
        return snapshot["content"]["review_marker"] == expected
    if resource_type == "characters":
        return snapshot["characters"][0]["content"]["review_marker"] == expected
    return snapshot["episodes"][0]["content"]["review_marker"] == expected


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
    _make_resource_draft(creative, resource_type, resource_id)
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

    _edit_resource(creative, resource_type, new_resource_id)
    workflow.start_task(returned["task"]["id"], actors[resource_type])
    resubmitted = transitions.submit(returned["draft"]["id"], actors[resource_type])
    assert _snapshot_has_marker(resubmitted["content_snapshot"], resource_type)
    reviewed = transitions.approve(resubmitted["id"], owner)["submission"]
    assert reviewed["status"] == "approved"


def test_submit_rejects_wrong_task_node_assignee_and_resource_scope(environment) -> None:
    _, workflow, creative, adapter, _, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    _make_resource_draft(creative, "brief", resource_id)
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
    _make_resource_draft(creative, "brief", resource_id)
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
    _make_resource_draft(creative, "brief", resource_id)
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


def test_generic_creative_submission_ignores_client_snapshot_and_validates_scope(
    environment,
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    _make_resource_draft(creative, "brief", resource_id)
    task = _task(
        workflow, creative, admin, owner, actors["brief"], "brief", resource_id
    )

    draft = transitions.create_submission(
        task["id"], "brief", resource_id, {"client": "forged"}, actors["brief"]
    )

    assert draft["content_snapshot"] == adapter.snapshot("brief", resource_id)
    assert "client" not in draft["content_snapshot"]


def test_replayed_upstream_approval_does_not_clear_downstream_pointers(environment) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resources = _seed_resources(creative)
    brief_id = resources["brief"][0]
    characters_id = resources["characters"][0]
    project_id, character_batch_id = characters_id.split(":", 1)
    project_before = creative.get_project(int(project_id))
    outline_batch_id = project_before["outline_batch_id"]

    brief_task = _task(
        workflow, creative, admin, owner, actors["brief"], "brief", brief_id
    )
    with creative._connect() as connection:
        connection.execute("UPDATE creative_blueprints SET status = 'draft' WHERE id = ?", (int(brief_id),))
    brief = adapter.submit(brief_task["id"], "brief", brief_id, actors["brief"])
    transitions.approve(brief["id"], owner)
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET character_batch_id = ?, outline_batch_id = ? WHERE id = ?",
            (character_batch_id, outline_batch_id, int(project_id)),
        )
    transitions.approve(brief["id"], owner)
    after_brief_replay = creative.get_project(int(project_id))
    assert after_brief_replay["character_batch_id"] == character_batch_id
    assert after_brief_replay["outline_batch_id"] == outline_batch_id

    character_task = _task(
        workflow, creative, admin, owner, actors["characters"], "characters", characters_id
    )
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_characters SET status = 'draft' WHERE project_id = ? AND batch_id = ?",
            (int(project_id), character_batch_id),
        )
    character = adapter.submit(
        character_task["id"], "characters", characters_id, actors["characters"]
    )
    transitions.approve(character["id"], owner)
    with creative._connect() as connection:
        connection.execute(
            "UPDATE creative_projects SET outline_batch_id = ? WHERE id = ?",
            (outline_batch_id, int(project_id)),
        )
    transitions.approve(character["id"], owner)
    assert creative.get_project(int(project_id))["outline_batch_id"] == outline_batch_id


@pytest.mark.parametrize("resource_type", ["characters", "outline", "beats"])
def test_stale_downstream_resource_cannot_submit_or_restore_old_pointer(
    environment, resource_type: str
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resources = _seed_resources(creative)
    resource_id, _ = resources[resource_type]
    _make_resource_draft(creative, resource_type, resource_id)
    task = _task(
        workflow, creative, admin, owner, actors[resource_type], resource_type, resource_id
    )
    project_id = int(resource_id.split(":", 1)[0]) if ":" in resource_id else creative.get_episode(int(resource_id))["project_id"]
    project = creative.get_project(project_id)
    pointer_column = {
        "characters": "locked_brief_id",
        "outline": "character_batch_id",
        "beats": "outline_batch_id",
    }[resource_type]
    current_pointer = project[pointer_column]
    with creative._connect() as connection:
        if resource_type == "characters":
            connection.execute("UPDATE creative_projects SET locked_brief_id = 999 WHERE id = ?", (project_id,))
        elif resource_type == "outline":
            connection.execute("UPDATE creative_projects SET character_batch_id = 'new-batch' WHERE id = ?", (project_id,))
        else:
            connection.execute("UPDATE creative_projects SET outline_batch_id = 'new-outline' WHERE id = ?", (project_id,))

    with pytest.raises(ValueError, match="upstream|current|上游"):
        adapter.submit(task["id"], resource_type, resource_id, actors[resource_type])

    with creative._connect() as connection:
        connection.execute(
            f"UPDATE creative_projects SET {pointer_column} = ? WHERE id = ?",
            (current_pointer, project_id),
        )
    submitted = adapter.submit(
        task["id"], resource_type, resource_id, actors[resource_type]
    )
    with creative._connect() as connection:
        replacement = 999 if resource_type == "characters" else "stale-after-submit"
        connection.execute(
            f"UPDATE creative_projects SET {pointer_column} = ? WHERE id = ?",
            (replacement, project_id),
        )
    with pytest.raises(ValueError, match="upstream|current|上游"):
        transitions.approve(submitted["id"], owner)
    assert workflow.get_submission(submitted["id"])["status"] == "submitted"
    assert creative.get_project(project_id)[pointer_column] == replacement


def test_workflow_detail_read_retries_pending_creative_mirror(environment, monkeypatch) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)["brief"]
    _make_resource_draft(creative, "brief", resource_id)
    task = _task(workflow, creative, admin, owner, actors["brief"], "brief", resource_id)
    submitted = adapter.submit(task["id"], "brief", resource_id, actors["brief"])
    real_mirror = adapter.on_approved
    monkeypatch.setattr(adapter, "on_approved", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    transitions.approve(submitted["id"], owner)
    monkeypatch.setattr(adapter, "on_approved", real_mirror)

    submissions = transitions.list_task_submissions(task["id"])

    assert submissions[-1]["status"] == "approved"
    assert adapter.domain_status("brief", resource_id) == "approved"


def test_creative_project_get_retries_pending_mirror(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / ".runtime"
    identity = IdentityRepository(runtime / "system" / "system.db")
    identity.seed_defaults()
    workflow = WorkflowRepository(identity)
    creative = CreativeRepository(runtime / "creative" / "creative.db")
    adapter = CreativeWorkflowAdapter(creative, workflow)
    transitions = TransitionService(workflow, creative_adapter=adapter)
    admin = _user(identity, "read_admin", "system_admin")
    owner = _user(identity, "read_owner", "project_owner")
    actor = _user(identity, "read_planner", "creative_planner")
    resource_id, _ = _seed_resources(creative)["brief"]
    _make_resource_draft(creative, "brief", resource_id)
    task = _task(workflow, creative, admin, owner, actor, "brief", resource_id)
    submitted = adapter.submit(task["id"], "brief", resource_id, actor)
    monkeypatch.setattr(
        adapter,
        "on_approved",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    transitions.approve(submitted["id"], owner)
    project_id = creative.get_blueprint(int(resource_id))["project_id"]
    assert adapter.domain_status("brief", resource_id) == "submitted"
    app = FastAPI()
    app.include_router(
        build_creative_router(root=tmp_path, runtime_dir=runtime)
    )

    response = TestClient(app).get(f"/api/creative/projects/{project_id}")

    assert response.status_code == 200
    assert creative.get_blueprint(int(resource_id))["status"] == "approved"


@pytest.mark.parametrize(
    "resource_type", ["breakdown", "brief", "characters", "outline", "beats"]
)
def test_reading_older_approved_after_partial_supersede_failure_never_reverses_newer(
    environment, monkeypatch, resource_type: str
) -> None:
    _, workflow, creative, adapter, transitions, admin, owner, actors = environment
    resource_id, _ = _seed_resources(creative)[resource_type]
    _make_resource_draft(creative, resource_type, resource_id)
    first_task = _task(
        workflow,
        creative,
        admin,
        owner,
        actors[resource_type],
        resource_type,
        resource_id,
    )
    first = adapter.submit(
        first_task["id"], resource_type, resource_id, actors[resource_type]
    )
    transitions.approve(first["id"], owner)
    with creative._connect() as connection:
        newer_resource_id = adapter._clone(
            resource_type, resource_id, first_task["id"], connection
        )
    second_task = _task(
        workflow,
        creative,
        admin,
        owner,
        actors[resource_type],
        resource_type,
        newer_resource_id,
    )
    second = adapter.submit(
        second_task["id"],
        resource_type,
        newer_resource_id,
        actors[resource_type],
    )
    real_supersede = workflow.supersede_resource_approvals

    def fail_old_supersede(kind, old_resource_id, actor):
        if kind == resource_type and old_resource_id == resource_id:
            raise RuntimeError("injected workflow supersede outage")
        return real_supersede(kind, old_resource_id, actor)

    monkeypatch.setattr(workflow, "supersede_resource_approvals", fail_old_supersede)
    transitions.approve(second["id"], owner)
    assert workflow.get_submission(first["id"])["status"] == "approved"
    assert workflow.get_submission(second["id"])["status"] == "approved"
    monkeypatch.setattr(workflow, "supersede_resource_approvals", real_supersede)

    app = FastAPI()
    app.include_router(build_workflow_router(workflow, transitions))
    app.dependency_overrides[current_principal] = lambda: owner
    repeated = TestClient(app).post(
        f"/api/workflow/submissions/{first['id']}/approve"
    )

    assert repeated.status_code == 200
    assert repeated.json()["authoritative_submission_id"] == second["id"]
    assert workflow.get_submission(first["id"])["status"] == "superseded"
    assert workflow.get_submission(second["id"])["status"] == "approved"
    assert adapter.domain_status(resource_type, newer_resource_id) == "approved"
    if resource_type == "brief":
        project_id = creative.get_blueprint(int(newer_resource_id))["project_id"]
        assert creative.get_project(project_id)["locked_brief_id"] == int(newer_resource_id)
    elif resource_type == "characters":
        project_id, batch_id = newer_resource_id.split(":", 1)
        assert creative.get_project(int(project_id))["character_batch_id"] == batch_id
    elif resource_type == "outline":
        project_id, batch_id = newer_resource_id.split(":", 1)
        assert creative.get_project(int(project_id))["outline_batch_id"] == batch_id
