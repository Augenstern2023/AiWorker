# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest

from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow import SYSTEM_ACTOR
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService, WORKFLOW_NODES


EXPECTED_NODES = {
    "topic_decision": ("selection_operator", "project_owner", "source_package"),
    "source_package": ("selection_operator", "project_owner", "breakdown"),
    "breakdown": ("selection_operator", "project_owner", "brief"),
    "brief": ("creative_planner", "project_owner", "characters"),
    "characters": ("creative_planner", "project_owner", "outline"),
    "outline": ("screenwriter", "project_owner", "beats"),
    "beats": ("screenwriter", "project_owner", "delivery"),
    "delivery": ("system_admin", None, None),
}


@pytest.fixture
def identity_repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


def _user(
    repository: IdentityRepository, username: str, role_code: str
) -> Principal:
    role = repository.get_role_by_code(role_code)
    assert role is not None
    user = repository.create_user(username, username.title(), "hash")
    repository.replace_user_roles(user["id"], [role["id"]])
    principal = PermissionService(
        repository, SessionService(repository)
    ).principal_for_user(user["id"], f"session:{username}")
    assert principal is not None
    return principal


@pytest.fixture
def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
    return WorkflowRepository(identity_repository)


@pytest.fixture
def service(repository: WorkflowRepository) -> TransitionService:
    return TransitionService(repository)


@pytest.fixture
def admin(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "transition_admin", "system_admin")


@pytest.fixture
def reviewer(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "transition_owner", "project_owner")


def _submitted_node(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
    node: str,
    *,
    suffix: str,
) -> tuple[dict, dict, Principal]:
    executor_role = EXPECTED_NODES[node][0]
    actor = (
        admin
        if executor_role == "system_admin"
        else _user(identity_repository, f"{executor_role}_{suffix}", executor_role)
    )
    project_id = f"project-{suffix}"
    repository.add_project_member(
        project_id, reviewer.user_id, "project_owner", admin
    )
    repository.add_project_member(
        project_id, actor.user_id, executor_role, admin
    )
    task = repository.create_task(
        f"workflow-{suffix}",
        node,
        executor_role,
        project_id=project_id,
        topic_id=731,
        actor=SYSTEM_ACTOR,
    )
    repository.assign_task(task["id"], actor.user_id, admin)
    repository.start_task(task["id"], actor)
    draft = service.create_submission(
        task["id"], node, f"resource-{suffix}", {"node": node}, actor
    )
    submitted = service.submit(draft["id"], actor)
    return task, submitted, actor


def test_node_catalog_is_exact() -> None:
    assert WORKFLOW_NODES == EXPECTED_NODES


@pytest.mark.parametrize(
    ("node", "next_node", "target_role"),
    [
        (node, config[2], EXPECTED_NODES[config[2]][0])
        for node, config in EXPECTED_NODES.items()
        if config[2] is not None
    ],
)
def test_approval_creates_exact_next_role_pool_task_with_copied_identifiers(
    node: str,
    next_node: str,
    target_role: str,
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    task, submitted, _ = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        node,
        suffix=node,
    )

    result = service.approve(submitted["id"], reviewer)

    assert result["submission"]["status"] == "approved"
    assert result["task"]["status"] == "completed"
    assert result["next_task"] == {
        **result["next_task"],
        "workflow_id": task["workflow_id"],
        "task_type": next_node,
        "role_code": target_role,
        "topic_id": task["topic_id"],
        "project_id": task["project_id"],
        "assignee_user_id": None,
        "status": "pool",
        "predecessor_task_id": task["id"],
    }


def test_repeated_approval_returns_same_next_task_without_duplicate(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    task, submitted, _ = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "breakdown",
        suffix="idempotent",
    )

    first = service.approve(submitted["id"], reviewer)
    second = service.approve(submitted["id"], reviewer)

    assert second["submission"]["id"] == first["submission"]["id"]
    assert second["next_task"]["id"] == first["next_task"]["id"]
    workflow_tasks = [
        item
        for item in repository.list_tasks(admin)
        if item["workflow_id"] == task["workflow_id"]
    ]
    assert [item["task_type"] for item in workflow_tasks].count("brief") == 1


def test_successor_failure_rolls_back_approval_and_task_completion(
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    class RejectSuccessorRepository(WorkflowRepository):
        def create_next_task(
            self,
            predecessor_task_id: str,
            task_type: str,
            role_code: str,
            *,
            actor: Principal | dict,
            connection=None,
        ) -> dict:
            if connection is None:
                raise AssertionError("successor must use the approval transaction")
            raise RuntimeError("injected successor failure")

    repository = RejectSuccessorRepository(identity_repository)
    service = TransitionService(repository)
    task, submitted, _ = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "breakdown",
        suffix="successor-rollback",
    )

    with pytest.raises(RuntimeError, match="injected successor failure"):
        service.approve(submitted["id"], reviewer)

    assert service.approval_service.get_submission(submitted["id"])["status"] == (
        "submitted"
    )
    assert repository.get_task(task["id"])["status"] == "waiting_review"
    assert all(
        item["predecessor_task_id"] != task["id"]
        for item in repository.list_tasks(admin)
    )


def test_return_reopens_task_without_creating_next_task(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    task, submitted, _ = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "outline",
        suffix="returned",
    )

    result = service.return_submission(
        submitted["id"], reviewer, "Add a stronger ending"
    )

    assert result["submission"]["status"] == "returned"
    assert result["task"]["status"] == "returned"
    assert result["draft"]["parent_submission_id"] == submitted["id"]
    assert all(
        item["predecessor_task_id"] != task["id"]
        for item in repository.list_tasks(admin)
    )


def test_delivery_approval_is_terminal(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    _, submitted, _ = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "delivery",
        suffix="delivery",
    )

    result = service.approve(submitted["id"], admin)

    assert result["task"]["status"] == "completed"
    assert result["next_task"] is None


def test_unauthorized_reviewer_cannot_approve(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    _, submitted, actor = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "characters",
        suffix="unauthorized",
    )

    with pytest.raises(PermissionError):
        service.approve(submitted["id"], actor)


def test_review_capabilities_follow_live_managed_project_permissions(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
    reviewer: Principal,
) -> None:
    task, _, actor = _submitted_node(
        service,
        repository,
        identity_repository,
        admin,
        reviewer,
        "characters",
        suffix="capabilities",
    )

    assert repository.can_review_task(
        task["id"], reviewer, "version.approve"
    ) is True
    assert repository.can_review_task(
        task["id"], reviewer, "version.return"
    ) is True
    assert repository.can_review_task(
        task["id"], actor, "version.approve"
    ) is False


@pytest.mark.parametrize("status", ["assigned", "in_progress", "returned"])
def test_submission_can_be_created_only_for_allowed_assignee_task_states(
    status: str,
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
) -> None:
    actor = _user(identity_repository, f"state_{status}", "selection_operator")
    task = repository.create_task(
        f"workflow-state-{status}",
        "breakdown",
        "selection_operator",
        actor=SYSTEM_ACTOR,
    )
    repository.assign_task(task["id"], actor.user_id, admin)
    if status == "in_progress":
        repository.start_task(task["id"], actor)
    elif status == "returned":
        with identity_repository.connect() as connection:
            connection.execute(
                "UPDATE workflow_tasks SET status = 'returned' WHERE id = ?",
                (task["id"],),
            )

    draft = service.create_submission(
        task["id"], "breakdown", f"resource-{status}", {"ok": True}, actor
    )

    assert draft["status"] == "draft"


def test_submission_rejects_pool_task_and_non_assignee(
    service: TransitionService,
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    admin: Principal,
) -> None:
    actor = _user(identity_repository, "state_actor", "selection_operator")
    other = _user(identity_repository, "state_other", "selection_operator")
    task = repository.create_task(
        "workflow-state-rejected",
        "breakdown",
        "selection_operator",
        actor=SYSTEM_ACTOR,
    )
    with pytest.raises(PermissionError, match="assignee"):
        service.create_submission(
            task["id"], "breakdown", "resource-pool", {}, actor
        )
    repository.assign_task(task["id"], actor.user_id, admin)
    with pytest.raises(PermissionError, match="assignee"):
        service.create_submission(
            task["id"], "breakdown", "resource-other", {}, other
        )


def test_task_ui_renders_real_status_rows_and_permission_gated_actions() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "common" / "tasks.js"
    ).read_text(encoding="utf-8")

    for status in ("pool", "assigned", "waiting_review"):
        assert status in source
    for action in ("claim", "start", "approve", "return"):
        assert f"/{action}`" in source
    assert "task.can_approve" in source
    assert "task.can_return" in source
    assert "workflow-task-row" in source
