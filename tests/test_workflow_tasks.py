# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from workflow.repository import WorkflowRepository
from workflow.state_machine import WorkflowStateMachine


@pytest.fixture
def identity_repository(tmp_path: Path) -> IdentityRepository:
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()
    return repository


@pytest.fixture
def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
    return WorkflowRepository(identity_repository)


def _user(
    identity_repository: IdentityRepository,
    username: str,
    role_code: str,
) -> Principal:
    role = identity_repository.get_role_by_code(role_code)
    assert role is not None
    user = identity_repository.create_user(username, username.title(), "hash")
    identity_repository.replace_user_roles(user["id"], [role["id"]])
    service = PermissionService(identity_repository, SessionService(identity_repository))
    principal = service.principal_for_user(user["id"], f"session:{username}")
    assert principal is not None
    return principal


@pytest.fixture
def selection_user(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "selector", "selection_operator")


@pytest.fixture
def writer_user(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "writer", "screenwriter")


@pytest.fixture
def owner_user(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "owner", "project_owner")


@pytest.fixture
def admin_user(identity_repository: IdentityRepository) -> Principal:
    return _user(identity_repository, "admin", "system_admin")


def test_role_pool_task_is_not_auto_assigned(repository: WorkflowRepository):
    task = repository.create_task(
        workflow_id="wf-1",
        task_type="breakdown.create",
        role_code="selection_operator",
        project_id=None,
        topic_id=12,
    )

    assert task["status"] == "pool"
    assert task["assignee_user_id"] is None


def test_only_matching_current_role_can_claim(
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    selection_user: Principal,
    writer_user: Principal,
):
    task = repository.create_task(
        "wf-1", "breakdown.create", "selection_operator", topic_id=12
    )

    with pytest.raises(PermissionError):
        repository.claim_task(task["id"], writer_user)
    claimed = repository.claim_task(task["id"], selection_user)

    assert claimed["status"] == "assigned"
    assert claimed["assignee_user_id"] == selection_user.user_id

    # Role checks are live, not based on the roles cached in the Principal.
    identity_repository.replace_user_roles(selection_user.user_id, [])
    another = repository.create_task(
        "wf-2", "topic.prepare", "selection_operator", topic_id=13
    )
    with pytest.raises(PermissionError):
        repository.claim_task(another["id"], selection_user)


def test_claim_is_atomic_when_two_eligible_users_race(
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    selection_user: Principal,
):
    second = _user(identity_repository, "selector_two", "selection_operator")
    task = repository.create_task(
        "wf-race", "breakdown.create", "selection_operator", topic_id=12
    )

    def claim(actor: Principal):
        try:
            return repository.claim_task(task["id"], actor)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (selection_user, second)))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["assignee_user_id"] in {
        selection_user.user_id,
        second.user_id,
    }


def test_owner_can_assign_and_reassign_only_in_managed_project(
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    owner_user: Principal,
    selection_user: Principal,
    writer_user: Principal,
):
    repository.add_project_member(
        "project-1", owner_user.user_id, "project_owner", owner_user
    )
    repository.add_project_member(
        "project-1", selection_user.user_id, "selection_operator", owner_user
    )
    repository.add_project_member(
        "project-1", writer_user.user_id, "selection_operator", owner_user
    )
    task = repository.create_task(
        "wf-project", "breakdown.create", "selection_operator", project_id="project-1"
    )

    assigned = repository.assign_task(task["id"], selection_user.user_id, owner_user)
    assert assigned["status"] == "assigned"
    assert assigned["assignee_user_id"] == selection_user.user_id

    selection_role = identity_repository.get_role_by_code("selection_operator")
    assert selection_role is not None
    identity_repository.replace_user_roles(writer_user.user_id, [selection_role["id"]])
    reassigned = repository.reassign_task(
        task["id"], writer_user.user_id, owner_user
    )
    assert reassigned["assignee_user_id"] == writer_user.user_id

    outside = repository.create_task(
        "wf-outside", "breakdown.create", "selection_operator", project_id="project-2"
    )
    with pytest.raises(PermissionError):
        repository.assign_task(outside["id"], selection_user.user_id, owner_user)


def test_project_task_cannot_be_assigned_to_user_outside_project(
    repository: WorkflowRepository,
    identity_repository: IdentityRepository,
    owner_user: Principal,
):
    selector = _user(identity_repository, "outside_selector", "selection_operator")
    repository.add_project_member(
        "project-1", owner_user.user_id, "project_owner", owner_user
    )
    task = repository.create_task(
        "wf-membership", "breakdown.create", "selection_operator", project_id="project-1"
    )

    with pytest.raises(ValueError, match="project member"):
        repository.assign_task(task["id"], selector.user_id, owner_user)

    with pytest.raises(PermissionError, match="project member"):
        repository.claim_task(task["id"], selector)


def test_assignee_can_start_and_manager_can_cancel_with_event_history(
    repository: WorkflowRepository,
    selection_user: Principal,
    admin_user: Principal,
):
    task = repository.create_task(
        "wf-events", "breakdown.create", "selection_operator", topic_id=12
    )
    repository.claim_task(task["id"], selection_user)
    started = repository.start_task(task["id"], selection_user)
    cancelled = repository.cancel_task(task["id"], admin_user)

    assert started["status"] == "in_progress"
    assert cancelled["status"] == "cancelled"
    assert [event["event_type"] for event in repository.list_task_events(task["id"])] == [
        "create",
        "claim",
        "start",
        "cancel",
    ]
    cancel_event = repository.list_task_events(task["id"])[-1]
    assert cancel_event["actor_user_id"] == admin_user.user_id
    assert cancel_event["before_status"] == "in_progress"
    assert cancel_event["after_status"] == "cancelled"
    assert cancel_event["created_at"]


def test_project_member_add_remove_and_last_owner_guard(
    repository: WorkflowRepository,
    owner_user: Principal,
    admin_user: Principal,
    writer_user: Principal,
):
    owner_membership = repository.add_project_member(
        "project-1", owner_user.user_id, "project_owner", admin_user
    )
    writer_membership = repository.add_project_member(
        "project-1", writer_user.user_id, "screenwriter", owner_user
    )
    assert [member["id"] for member in repository.list_project_members("project-1")] == [
        owner_membership["id"],
        writer_membership["id"],
    ]

    with pytest.raises(ValueError, match="owner"):
        repository.remove_project_member(
            "project-1", owner_membership["id"], admin_user
        )

    second_owner = repository.add_project_member(
        "project-1", admin_user.user_id, "owner", admin_user
    )
    repository.remove_project_member("project-1", owner_membership["id"], admin_user)
    repository.remove_project_member("project-1", writer_membership["id"], admin_user)
    assert [member["id"] for member in repository.list_project_members("project-1")] == [
        second_owner["id"]
    ]


def test_state_machine_declares_all_states_and_rejects_invalid_transition(
    selection_user: Principal,
):
    machine = WorkflowStateMachine()
    task = {
        "status": "completed",
        "role_code": "selection_operator",
        "assignee_user_id": selection_user.user_id,
        "project_id": None,
    }

    assert machine.states == frozenset(
        {
            "pool",
            "assigned",
            "in_progress",
            "waiting_review",
            "returned",
            "completed",
            "cancelled",
        }
    )
    with pytest.raises(ValueError):
        machine.transition(task, "start", selection_user)
