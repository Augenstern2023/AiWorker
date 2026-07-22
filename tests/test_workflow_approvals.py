# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR, WorkflowRepository
from workflow.approval_service import ApprovalService
from workflow.state_machine import WorkflowStateMachine


@pytest.fixture
def identity_repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


def _user(
    repository: IdentityRepository,
    username: str,
    *role_codes: str,
) -> Principal:
    roles = [repository.get_role_by_code(code) for code in role_codes]
    assert all(role is not None for role in roles)
    user = repository.create_user(username, username.title(), "hash")
    repository.replace_user_roles(user["id"], [role["id"] for role in roles])
    principal = PermissionService(
        repository, SessionService(repository)
    ).principal_for_user(user["id"], f"session:{username}")
    assert principal is not None
    return principal


@pytest.fixture
def author(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "author", "selection_operator")


@pytest.fixture
def reviewer(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "reviewer", "project_owner")


@pytest.fixture
def admin(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "admin", "system_admin")


@pytest.fixture
def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
    return WorkflowRepository(identity_repository)


@pytest.fixture
def service(repository: WorkflowRepository) -> ApprovalService:
    return ApprovalService(repository)


def _started_task(
    repository: WorkflowRepository,
    author: Principal,
    admin: Principal,
    *,
    project_id: str = "project-1",
    workflow_id: str = "workflow-1",
) -> dict:
    repository.add_project_member(
        project_id, author.user_id, "selection_operator", admin
    )
    task = repository.create_task(
        workflow_id,
        "breakdown.create",
        "selection_operator",
        project_id=project_id,
        actor=SYSTEM_ACTOR,
    )
    repository.assign_task(task["id"], author.user_id, admin)
    return repository.start_task(task["id"], author)


def _managed_project(
    repository: WorkflowRepository,
    reviewer: Principal,
    admin: Principal,
    project_id: str = "project-1",
) -> None:
    repository.add_project_member(
        project_id, reviewer.user_id, "project_owner", admin
    )


def _custom_reviewer(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
    admin: Principal,
    *,
    username: str,
    project_role: str,
    project_id: str = "project-1",
    permission_scope: str = "managed_projects",
) -> Principal:
    role = identity_repository.create_role(
        f"{username}_role", username.title(), "Custom managed reviewer"
    )
    identity_repository.replace_role_permissions(
        role["id"],
        [
            ("version.approve", permission_scope),
            ("version.return", permission_scope),
        ],
    )
    custom_reviewer = _user(identity_repository, username, role["code"])
    repository.add_project_member(
        project_id, custom_reviewer.user_id, project_role, admin
    )
    return custom_reviewer


def _submitted(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    admin: Principal,
    *,
    resource_id: str = "resource-1",
    project_id: str = "project-1",
    workflow_id: str = "workflow-1",
) -> tuple[dict, dict]:
    task = _started_task(
        repository,
        author,
        admin,
        project_id=project_id,
        workflow_id=workflow_id,
    )
    draft = service.create_draft(
        task_id=task["id"],
        resource_type="breakdown",
        resource_id=resource_id,
        content={"title": "\u4e00", "items": [2, 1]},
        actor=author,
    )
    return task, service.submit(draft["id"], author)


def test_draft_is_editable_only_by_task_assignee(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    task = _started_task(repository, author, admin)
    draft = service.create_draft(
        task_id=task["id"],
        resource_type="breakdown",
        resource_id="resource-1",
        content={"draft": True},
        actor=author,
    )

    assert service.assert_editable(draft["id"], author)["status"] == "draft"
    with pytest.raises(PermissionError, match="assignee"):
        service.assert_editable(draft["id"], reviewer)


def test_submit_requires_assignee_and_freezes_canonical_snapshot(
    service: ApprovalService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    task = _started_task(repository, author, admin)
    draft = service.create_draft(
        task_id=task["id"],
        resource_type="breakdown",
        resource_id="resource-1",
        content={"z": [3, 2], "name": "\u4e2d\u6587"},
        actor=author,
    )

    with pytest.raises(PermissionError, match="assignee"):
        service.submit(draft["id"], reviewer)
    submitted = service.submit(draft["id"], author)

    canonical = '{"name":"\u4e2d\u6587","z":[3,2]}'
    assert submitted["status"] == "submitted"
    assert submitted["content_snapshot"] == {"name": "\u4e2d\u6587", "z": [3, 2]}
    assert submitted["content_fingerprint"] == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert repository.get_task(task["id"])["status"] == "waiting_review"
    with identity_repository.connect() as connection:
        stored = connection.execute(
            "SELECT content_snapshot_json FROM workflow_submissions WHERE id = ?",
            (submitted["id"],),
        ).fetchone()
    assert stored["content_snapshot_json"] == canonical
    with pytest.raises(ValueError, match="draft"):
        service.assert_editable(submitted["id"], author)


def test_self_approval_is_rejected_but_system_admin_can_approve(
    identity_repository: IdentityRepository,
    repository: WorkflowRepository,
    admin: Principal,
):
    owner_author = _user(
        identity_repository,
        "owner_author",
        "selection_operator",
        "project_owner",
    )
    repository.add_project_member(
        "project-self", owner_author.user_id, "project_owner", admin
    )
    task = _started_task(
        repository,
        owner_author,
        admin,
        project_id="project-self",
        workflow_id="workflow-self",
    )
    service = ApprovalService(repository)
    draft = service.create_draft(
        task_id=task["id"],
        resource_type="brief",
        resource_id="self-resource",
        content={"body": "draft"},
        actor=owner_author,
    )
    submitted = service.submit(draft["id"], owner_author)

    with pytest.raises(PermissionError, match="own submission"):
        service.approve(submitted["id"], owner_author)
    approved = service.approve(submitted["id"], admin)

    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == admin.user_id
    assert repository.get_task(task["id"])["status"] == "completed"


def test_managed_project_reviewer_can_approve_and_events_have_snapshots(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    task, submitted = _submitted(service, repository, author, admin)

    approved = service.approve(submitted["id"], reviewer)

    assert approved["status"] == "approved"
    events = repository.list_task_events(task["id"])
    approval_events = [event for event in events if event["event_type"] == "approve"]
    assert len(approval_events) == 1
    event = approval_events[0]
    assert event["actor_user_id"] == reviewer.user_id
    assert event["before_status"] == "waiting_review"
    assert event["after_status"] == "completed"
    assert event["details"]["before"]["status"] == "submitted"
    assert event["details"]["after"]["status"] == "approved"


def test_custom_role_managed_reviewer_can_approve(
    service: ApprovalService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    author: Principal,
    admin: Principal,
):
    custom_reviewer = _custom_reviewer(
        identity_repository,
        repository,
        admin,
        username="approval_reviewer",
        project_role="manager",
    )
    task, submitted = _submitted(service, repository, author, admin)

    approved = service.approve(submitted["id"], custom_reviewer)

    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == custom_reviewer.user_id
    assert repository.get_task(task["id"])["status"] == "completed"


def test_custom_role_managed_reviewer_can_return(
    service: ApprovalService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    author: Principal,
    admin: Principal,
):
    custom_reviewer = _custom_reviewer(
        identity_repository,
        repository,
        admin,
        username="return_reviewer",
        project_role="project_manager",
    )
    task, submitted = _submitted(service, repository, author, admin)

    returned, draft = service.return_submission(
        submitted["id"], custom_reviewer, "Revise the evidence"
    )

    assert returned["status"] == "returned"
    assert returned["reviewed_by"] == custom_reviewer.user_id
    assert draft["parent_submission_id"] == returned["id"]
    assert repository.get_task(task["id"])["status"] == "returned"


def test_task_scoped_review_permission_cannot_review_managed_project(
    service: ApprovalService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    author: Principal,
    admin: Principal,
):
    task_scoped_reviewer = _custom_reviewer(
        identity_repository,
        repository,
        admin,
        username="task_scoped_reviewer",
        project_role="manager",
        permission_scope="task",
    )
    _, submitted = _submitted(service, repository, author, admin)

    with pytest.raises(PermissionError, match="managed_projects"):
        service.approve(submitted["id"], task_scoped_reviewer)


def test_return_requires_comment_preserves_source_and_creates_next_draft(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    task, submitted = _submitted(service, repository, author, admin)
    frozen_content = submitted["content_snapshot"]
    frozen_fingerprint = submitted["content_fingerprint"]

    with pytest.raises(ValueError, match="comment"):
        service.return_submission(submitted["id"], reviewer, "   ")
    returned, draft = service.return_submission(
        submitted["id"], reviewer, "Add source evidence"
    )

    assert returned["status"] == "returned"
    assert returned["review_comment"] == "Add source evidence"
    assert returned["content_snapshot"] == frozen_content
    assert returned["content_fingerprint"] == frozen_fingerprint
    assert draft["status"] == "draft"
    assert draft["version"] == returned["version"] + 1
    assert draft["parent_submission_id"] == returned["id"]
    assert draft["content_snapshot"] == frozen_content
    reopened = repository.get_task(task["id"])
    assert reopened["status"] == "returned"
    assert reopened["assignee_user_id"] == author.user_id
    with pytest.raises(ValueError, match="submitted"):
        service.return_submission(submitted["id"], reviewer, "Again")


def test_approving_newer_version_supersedes_previous_approval(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    _, first_submitted = _submitted(service, repository, author, admin)
    first_approved = service.approve(first_submitted["id"], reviewer)
    _, second_submitted = _submitted(
        service,
        repository,
        author,
        admin,
        workflow_id="workflow-2",
    )

    second_approved = service.approve(second_submitted["id"], reviewer)

    assert first_approved["version"] == 1
    assert second_approved["version"] == 2
    assert service.get_submission(first_approved["id"])["status"] == "superseded"
    assert service.get_submission(second_approved["id"])["status"] == "approved"


def test_older_submission_cannot_replace_a_newer_approved_version(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    first_task, first_submitted = _submitted(
        service, repository, author, admin, workflow_id="workflow-old"
    )
    _, second_submitted = _submitted(
        service, repository, author, admin, workflow_id="workflow-new"
    )
    second_approved = service.approve(second_submitted["id"], reviewer)

    with pytest.raises(ValueError, match="newer approved"):
        service.approve(first_submitted["id"], reviewer)

    assert service.get_submission(second_approved["id"])["status"] == "approved"
    assert service.get_submission(first_submitted["id"])["status"] == "submitted"
    assert repository.get_task(first_task["id"])["status"] == "waiting_review"


@pytest.mark.parametrize("source_state", ["approved", "returned"])
def test_fork_creates_next_draft_without_mutating_source(
    source_state: str,
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    _, submitted = _submitted(service, repository, author, admin)
    if source_state == "approved":
        source = service.approve(submitted["id"], reviewer)
    else:
        source, _ = service.return_submission(
            submitted["id"], reviewer, "Needs another direction"
        )

    forked = service.fork(source["id"], author)

    assert service.get_submission(source["id"])["status"] == source_state
    assert forked["status"] == "draft"
    assert forked["parent_submission_id"] == source["id"]
    assert forked["version"] > source["version"]
    assert forked["content_snapshot"] == source["content_snapshot"]


def test_repeated_terminal_transitions_are_rejected(
    service: ApprovalService,
    repository: WorkflowRepository,
    author: Principal,
    reviewer: Principal,
    admin: Principal,
):
    _managed_project(repository, reviewer, admin)
    _, submitted = _submitted(service, repository, author, admin)
    approved = service.approve(submitted["id"], reviewer)

    with pytest.raises(ValueError, match="submitted"):
        service.approve(approved["id"], reviewer)
    with pytest.raises(ValueError, match="submitted"):
        service.return_submission(approved["id"], reviewer, "Late change")
    with pytest.raises(ValueError, match="draft"):
        service.submit(approved["id"], author)


def test_submit_rolls_back_when_task_transition_fails(
    identity_repository: IdentityRepository,
    author: Principal,
    admin: Principal,
):
    class RejectSubmit(WorkflowStateMachine):
        def transition(self, task, action, actor):
            if action == "submit":
                raise RuntimeError("injected task transition failure")
            return super().transition(task, action, actor)

    repository = WorkflowRepository(
        identity_repository, state_machine=RejectSubmit()
    )
    service = ApprovalService(repository)
    task = _started_task(repository, author, admin)
    draft = service.create_draft(
        task_id=task["id"],
        resource_type="breakdown",
        resource_id="rollback-resource",
        content={"body": "unchanged"},
        actor=author,
    )
    event_count = len(repository.list_task_events(task["id"]))

    with pytest.raises(RuntimeError, match="injected"):
        service.submit(draft["id"], author)

    rolled_back = service.get_submission(draft["id"])
    assert rolled_back["status"] == "draft"
    assert rolled_back["submitted_by"] is None
    assert repository.get_task(task["id"])["status"] == "in_progress"
    assert len(repository.list_task_events(task["id"])) == event_count


def test_schema_does_not_introduce_creative_stage_versions(
    identity_repository: IdentityRepository,
):
    with identity_repository.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "workflow_submissions" in tables
    assert "creative_stage_versions" not in tables
