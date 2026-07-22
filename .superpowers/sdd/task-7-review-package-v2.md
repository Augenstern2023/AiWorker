# Commits

9691f77 fix: tighten workflow actor and membership events
1fb3c18 feat: add human task pools and assignment

# Stat

 tests/test_workflow_api.py   | 251 ++++++++++++++
 tests/test_workflow_tasks.py | 460 ++++++++++++++++++++++++
 workflow/__init__.py         |  15 +
 workflow/api.py              | 141 ++++++++
 workflow/repository.py       | 810 +++++++++++++++++++++++++++++++++++++++++++
 workflow/schema.py           |  36 ++
 workflow/state_machine.py    | 111 ++++++
 7 files changed, 1824 insertions(+)

# Diff

diff --git a/tests/test_workflow_api.py b/tests/test_workflow_api.py
new file mode 100644
index 0000000..a727dab
--- /dev/null
+++ b/tests/test_workflow_api.py
@@ -0,0 +1,251 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from pathlib import Path
+
+import pytest
+from fastapi import FastAPI
+from fastapi.testclient import TestClient
+
+from identity.dependencies import current_principal
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+from identity.session_service import SessionService
+from workflow import SYSTEM_ACTOR
+from workflow.api import build_workflow_router
+from workflow.repository import WorkflowRepository
+
+
+@pytest.fixture
+def identity_repository(tmp_path: Path) -> IdentityRepository:
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    return repository
+
+
+@pytest.fixture
+def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
+    return WorkflowRepository(identity_repository)
+
+
+def _principal(
+    identity_repository: IdentityRepository, username: str, role_code: str
+) -> Principal:
+    role = identity_repository.get_role_by_code(role_code)
+    assert role is not None
+    user = identity_repository.create_user(username, username.title(), "hash")
+    identity_repository.replace_user_roles(user["id"], [role["id"]])
+    service = PermissionService(identity_repository, SessionService(identity_repository))
+    principal = service.principal_for_user(user["id"], f"session:{username}")
+    assert principal is not None
+    return principal
+
+
+def _client(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+    principal: Principal,
+) -> TestClient:
+    app = FastAPI()
+    app.state.identity_permission_service = PermissionService(
+        identity_repository, SessionService(identity_repository)
+    )
+    app.dependency_overrides[current_principal] = lambda: principal
+    app.include_router(build_workflow_router(repository))
+    return TestClient(app)
+
+
+def test_task_api_lists_claimable_owned_and_managed_tasks_only(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+):
+    selector = _principal(identity_repository, "selector_api", "selection_operator")
+    owner = _principal(identity_repository, "owner_api", "project_owner")
+    repository.add_project_member("project-1", owner.user_id, "owner", owner)
+    selection_pool = repository.create_task(
+        "wf-selection",
+        "breakdown.create",
+        "selection_operator",
+        topic_id=1,
+        actor=SYSTEM_ACTOR,
+    )
+    repository.create_task(
+        "wf-writing",
+        "outline.create",
+        "screenwriter",
+        topic_id=1,
+        actor=SYSTEM_ACTOR,
+    )
+    managed = repository.create_task(
+        "wf-managed",
+        "outline.review",
+        "project_owner",
+        project_id="project-1",
+        actor=SYSTEM_ACTOR,
+    )
+    repository.create_task(
+        "wf-other",
+        "outline.review",
+        "project_owner",
+        project_id="project-2",
+        actor=SYSTEM_ACTOR,
+    )
+
+    selector_tasks = _client(identity_repository, repository, selector).get(
+        "/api/workflow/tasks"
+    )
+    owner_tasks = _client(identity_repository, repository, owner).get(
+        "/api/workflow/tasks"
+    )
+
+    assert selector_tasks.status_code == 200
+    assert {task["id"] for task in selector_tasks.json()} == {selection_pool["id"]}
+    assert owner_tasks.status_code == 200
+    assert {task["id"] for task in owner_tasks.json()} == {managed["id"]}
+
+
+def test_claim_start_cancel_api_and_conflict_statuses(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+):
+    selector = _principal(identity_repository, "selector_mutation", "selection_operator")
+    admin = _principal(identity_repository, "admin_mutation", "system_admin")
+    task = repository.create_task(
+        "wf-api",
+        "breakdown.create",
+        "selection_operator",
+        topic_id=1,
+        actor=SYSTEM_ACTOR,
+    )
+    selector_client = _client(identity_repository, repository, selector)
+
+    claimed = selector_client.post(f"/api/workflow/tasks/{task['id']}/claim")
+    duplicate = selector_client.post(f"/api/workflow/tasks/{task['id']}/claim")
+    started = selector_client.post(f"/api/workflow/tasks/{task['id']}/start")
+    cancelled = _client(identity_repository, repository, admin).post(
+        f"/api/workflow/tasks/{task['id']}/cancel"
+    )
+
+    assert claimed.status_code == 200
+    assert claimed.json()["status"] == "assigned"
+    assert duplicate.status_code == 409
+    assert started.status_code == 200
+    assert started.json()["status"] == "in_progress"
+    assert cancelled.status_code == 200
+    assert cancelled.json()["status"] == "cancelled"
+
+
+def test_assign_and_reassign_api_require_manager_scope(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+):
+    owner = _principal(identity_repository, "owner_assign", "project_owner")
+    first = _principal(identity_repository, "selector_first", "selection_operator")
+    second = _principal(identity_repository, "selector_second", "selection_operator")
+    repository.add_project_member("project-1", owner.user_id, "owner", owner)
+    repository.add_project_member(
+        "project-1", first.user_id, "selection_operator", owner
+    )
+    repository.add_project_member(
+        "project-1", second.user_id, "selection_operator", owner
+    )
+    task = repository.create_task(
+        "wf-assign",
+        "breakdown.create",
+        "selection_operator",
+        project_id="project-1",
+        actor=SYSTEM_ACTOR,
+    )
+    client = _client(identity_repository, repository, owner)
+
+    assigned = client.post(
+        f"/api/workflow/tasks/{task['id']}/assign",
+        json={"assignee_user_id": first.user_id},
+    )
+    reassigned = client.post(
+        f"/api/workflow/tasks/{task['id']}/reassign",
+        json={"assignee_user_id": second.user_id},
+    )
+
+    assert assigned.status_code == 200
+    assert assigned.json()["assignee_user_id"] == first.user_id
+    assert reassigned.status_code == 200
+    assert reassigned.json()["assignee_user_id"] == second.user_id
+
+
+def test_project_member_api_adds_lists_and_protects_last_owner(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+):
+    admin = _principal(identity_repository, "admin_members", "system_admin")
+    owner = _principal(identity_repository, "member_owner", "project_owner")
+    client = _client(identity_repository, repository, admin)
+
+    created = client.post(
+        "/api/workflow/projects/project-1/members",
+        json={
+            "user_id": owner.user_id,
+            "project_role": "project_owner",
+            "status": "active",
+        },
+    )
+    updated = client.post(
+        "/api/workflow/projects/project-1/members",
+        json={
+            "user_id": owner.user_id,
+            "project_role": "project_owner",
+            "status": "active",
+        },
+    )
+    listed = client.get("/api/workflow/projects/project-1/members")
+    removed = client.delete(
+        f"/api/workflow/projects/project-1/members/{created.json()['id']}"
+    )
+    replacement = client.post(
+        "/api/workflow/projects/project-1/members",
+        json={
+            "user_id": admin.user_id,
+            "project_role": "project_owner",
+            "status": "active",
+        },
+    )
+    removed_with_replacement = client.delete(
+        f"/api/workflow/projects/project-1/members/{created.json()['id']}"
+    )
+
+    assert created.status_code == 201
+    assert updated.status_code == 201
+    assert listed.status_code == 200
+    assert listed.json()[0]["user_id"] == owner.user_id
+    assert removed.status_code == 409
+    assert replacement.status_code == 201
+    assert removed_with_replacement.status_code == 204
+    events = repository.list_project_member_events("project-1")
+    assert [event["event_type"] for event in events] == [
+        "project_member.add",
+        "project_member.update",
+        "project_member.add",
+        "project_member.remove",
+    ]
+    assert all(event["actor_user_id"] == admin.user_id for event in events)
+
+
+def test_member_api_rejects_non_manager(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+):
+    selector = _principal(identity_repository, "selector_members", "selection_operator")
+    client = _client(identity_repository, repository, selector)
+
+    response = client.get("/api/workflow/projects/project-1/members")
+
+    assert response.status_code == 403
diff --git a/tests/test_workflow_tasks.py b/tests/test_workflow_tasks.py
new file mode 100644
index 0000000..24272f3
--- /dev/null
+++ b/tests/test_workflow_tasks.py
@@ -0,0 +1,460 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from concurrent.futures import ThreadPoolExecutor
+from pathlib import Path
+
+import pytest
+
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+from identity.session_service import SessionService
+from workflow import SYSTEM_ACTOR, WorkflowRepository
+from workflow.state_machine import WorkflowStateMachine
+
+
+@pytest.fixture
+def identity_repository(tmp_path: Path) -> IdentityRepository:
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    return repository
+
+
+@pytest.fixture
+def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
+    return WorkflowRepository(identity_repository)
+
+
+def _user(
+    identity_repository: IdentityRepository,
+    username: str,
+    role_code: str,
+) -> Principal:
+    role = identity_repository.get_role_by_code(role_code)
+    assert role is not None
+    user = identity_repository.create_user(username, username.title(), "hash")
+    identity_repository.replace_user_roles(user["id"], [role["id"]])
+    service = PermissionService(identity_repository, SessionService(identity_repository))
+    principal = service.principal_for_user(user["id"], f"session:{username}")
+    assert principal is not None
+    return principal
+
+
+@pytest.fixture
+def selection_user(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "selector", "selection_operator")
+
+
+@pytest.fixture
+def writer_user(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "writer", "screenwriter")
+
+
+@pytest.fixture
+def owner_user(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "owner", "project_owner")
+
+
+@pytest.fixture
+def admin_user(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "admin", "system_admin")
+
+
+def test_role_pool_task_is_not_auto_assigned(repository: WorkflowRepository):
+    task = repository.create_task(
+        workflow_id="wf-1",
+        task_type="breakdown.create",
+        role_code="selection_operator",
+        project_id=None,
+        topic_id=12,
+        actor=SYSTEM_ACTOR,
+    )
+
+    assert task["status"] == "pool"
+    assert task["assignee_user_id"] is None
+
+
+def test_only_matching_current_role_can_claim(
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    selection_user: Principal,
+    writer_user: Principal,
+):
+    task = repository.create_task(
+        "wf-1",
+        "breakdown.create",
+        "selection_operator",
+        topic_id=12,
+        actor=SYSTEM_ACTOR,
+    )
+
+    with pytest.raises(PermissionError):
+        repository.claim_task(task["id"], writer_user)
+    claimed = repository.claim_task(task["id"], selection_user)
+
+    assert claimed["status"] == "assigned"
+    assert claimed["assignee_user_id"] == selection_user.user_id
+
+    # Role checks are live, not based on the roles cached in the Principal.
+    identity_repository.replace_user_roles(selection_user.user_id, [])
+    another = repository.create_task(
+        "wf-2",
+        "topic.prepare",
+        "selection_operator",
+        topic_id=13,
+        actor=SYSTEM_ACTOR,
+    )
+    with pytest.raises(PermissionError):
+        repository.claim_task(another["id"], selection_user)
+
+
+def test_claim_is_atomic_when_two_eligible_users_race(
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    selection_user: Principal,
+):
+    second = _user(identity_repository, "selector_two", "selection_operator")
+    task = repository.create_task(
+        "wf-race",
+        "breakdown.create",
+        "selection_operator",
+        topic_id=12,
+        actor=SYSTEM_ACTOR,
+    )
+
+    def claim(actor: Principal):
+        try:
+            return repository.claim_task(task["id"], actor)
+        except ValueError:
+            return None
+
+    with ThreadPoolExecutor(max_workers=2) as executor:
+        results = list(executor.map(claim, (selection_user, second)))
+
+    winners = [result for result in results if result is not None]
+    assert len(winners) == 1
+    assert winners[0]["assignee_user_id"] in {
+        selection_user.user_id,
+        second.user_id,
+    }
+
+
+def test_owner_can_assign_and_reassign_only_in_managed_project(
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    owner_user: Principal,
+    selection_user: Principal,
+    writer_user: Principal,
+):
+    repository.add_project_member(
+        "project-1", owner_user.user_id, "project_owner", owner_user
+    )
+    repository.add_project_member(
+        "project-1", selection_user.user_id, "selection_operator", owner_user
+    )
+    repository.add_project_member(
+        "project-1", writer_user.user_id, "selection_operator", owner_user
+    )
+    task = repository.create_task(
+        "wf-project",
+        "breakdown.create",
+        "selection_operator",
+        project_id="project-1",
+        actor=SYSTEM_ACTOR,
+    )
+
+    assigned = repository.assign_task(task["id"], selection_user.user_id, owner_user)
+    assert assigned["status"] == "assigned"
+    assert assigned["assignee_user_id"] == selection_user.user_id
+
+    selection_role = identity_repository.get_role_by_code("selection_operator")
+    assert selection_role is not None
+    identity_repository.replace_user_roles(writer_user.user_id, [selection_role["id"]])
+    reassigned = repository.reassign_task(
+        task["id"], writer_user.user_id, owner_user
+    )
+    assert reassigned["assignee_user_id"] == writer_user.user_id
+
+    outside = repository.create_task(
+        "wf-outside",
+        "breakdown.create",
+        "selection_operator",
+        project_id="project-2",
+        actor=SYSTEM_ACTOR,
+    )
+    with pytest.raises(PermissionError):
+        repository.assign_task(outside["id"], selection_user.user_id, owner_user)
+
+
+def test_project_task_cannot_be_assigned_to_user_outside_project(
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    owner_user: Principal,
+):
+    selector = _user(identity_repository, "outside_selector", "selection_operator")
+    repository.add_project_member(
+        "project-1", owner_user.user_id, "project_owner", owner_user
+    )
+    task = repository.create_task(
+        "wf-membership",
+        "breakdown.create",
+        "selection_operator",
+        project_id="project-1",
+        actor=SYSTEM_ACTOR,
+    )
+
+    with pytest.raises(ValueError, match="project member"):
+        repository.assign_task(task["id"], selector.user_id, owner_user)
+
+    with pytest.raises(PermissionError, match="project member"):
+        repository.claim_task(task["id"], selector)
+
+
+def test_assignee_can_start_and_manager_can_cancel_with_event_history(
+    repository: WorkflowRepository,
+    selection_user: Principal,
+    admin_user: Principal,
+):
+    task = repository.create_task(
+        "wf-events",
+        "breakdown.create",
+        "selection_operator",
+        topic_id=12,
+        actor=SYSTEM_ACTOR,
+    )
+    repository.claim_task(task["id"], selection_user)
+    started = repository.start_task(task["id"], selection_user)
+    cancelled = repository.cancel_task(task["id"], admin_user)
+
+    assert started["status"] == "in_progress"
+    assert cancelled["status"] == "cancelled"
+    assert [event["event_type"] for event in repository.list_task_events(task["id"])] == [
+        "create",
+        "claim",
+        "start",
+        "cancel",
+    ]
+    cancel_event = repository.list_task_events(task["id"])[-1]
+    assert cancel_event["actor_user_id"] == admin_user.user_id
+    assert cancel_event["before_status"] == "in_progress"
+    assert cancel_event["after_status"] == "cancelled"
+    assert cancel_event["created_at"]
+
+
+def test_project_member_add_remove_and_last_owner_guard(
+    repository: WorkflowRepository,
+    owner_user: Principal,
+    admin_user: Principal,
+    writer_user: Principal,
+):
+    owner_membership = repository.add_project_member(
+        "project-1", owner_user.user_id, "project_owner", admin_user
+    )
+    writer_membership = repository.add_project_member(
+        "project-1", writer_user.user_id, "screenwriter", owner_user
+    )
+    assert [member["id"] for member in repository.list_project_members("project-1")] == [
+        owner_membership["id"],
+        writer_membership["id"],
+    ]
+
+    with pytest.raises(ValueError, match="owner"):
+        repository.remove_project_member(
+            "project-1", owner_membership["id"], admin_user
+        )
+
+    manager = repository.add_project_member(
+        "project-1", admin_user.user_id, "manager", admin_user
+    )
+    with pytest.raises(ValueError, match="owner"):
+        repository.remove_project_member(
+            "project-1", owner_membership["id"], admin_user
+        )
+    second_owner = repository.add_project_member(
+        "project-1", admin_user.user_id, "project_owner", admin_user
+    )
+    repository.remove_project_member("project-1", owner_membership["id"], admin_user)
+    repository.remove_project_member("project-1", writer_membership["id"], admin_user)
+    assert [member["id"] for member in repository.list_project_members("project-1")] == [
+        manager["id"],
+        second_owner["id"],
+    ]
+
+
+def test_project_member_mutations_record_actor_before_after_and_timestamp(
+    repository: WorkflowRepository,
+    admin_user: Principal,
+    owner_user: Principal,
+    writer_user: Principal,
+):
+    repository.add_project_member(
+        "project-events", owner_user.user_id, "project_owner", admin_user
+    )
+    membership = repository.add_project_member(
+        "project-events", writer_user.user_id, "screenwriter", admin_user
+    )
+    repository.add_project_member(
+        "project-events",
+        writer_user.user_id,
+        "screenwriter",
+        admin_user,
+        status="inactive",
+    )
+    repository.remove_project_member(
+        "project-events", membership["id"], admin_user
+    )
+
+    events = repository.list_project_member_events("project-events")
+    assert [event["event_type"] for event in events] == [
+        "project_member.add",
+        "project_member.add",
+        "project_member.update",
+        "project_member.remove",
+    ]
+    assert all(event["actor_user_id"] == admin_user.user_id for event in events)
+    assert events[1]["before"] is None
+    assert events[1]["after"]["status"] == "active"
+    assert events[2]["before"]["status"] == "active"
+    assert events[2]["after"]["status"] == "inactive"
+    assert events[3]["before"]["id"] == membership["id"]
+    assert events[3]["after"] is None
+    assert all(event["created_at"] for event in events)
+
+
+def test_create_task_requires_actor_and_system_actor_is_recorded(
+    repository: WorkflowRepository,
+    selection_user: Principal,
+):
+    with pytest.raises(TypeError):
+        repository.create_task("wf-missing-actor", "topic.prepare", "selection_operator")
+    with pytest.raises(ValueError, match="actor"):
+        repository.create_task(
+            "wf-null-actor",
+            "topic.prepare",
+            "selection_operator",
+            actor=None,
+        )
+
+    task = repository.create_task(
+        "wf-system-actor",
+        "topic.prepare",
+        "selection_operator",
+        actor=SYSTEM_ACTOR,
+    )
+
+    event = repository.list_task_events(task["id"])[0]
+    assert event["actor_user_id"] == SYSTEM_ACTOR.user_id
+
+    user_task = repository.create_task(
+        "wf-user-actor",
+        "topic.prepare",
+        "selection_operator",
+        actor=selection_user,
+    )
+    user_event = repository.list_task_events(user_task["id"])[0]
+    assert user_event["actor_user_id"] == selection_user.user_id
+
+
+def test_system_admin_claim_requires_matching_target_role(
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    admin_user: Principal,
+    selection_user: Principal,
+):
+    task = repository.create_task(
+        "wf-admin-claim",
+        "topic.prepare",
+        "selection_operator",
+        actor=SYSTEM_ACTOR,
+    )
+    with pytest.raises(PermissionError, match="task pool role"):
+        repository.claim_task(task["id"], admin_user)
+
+    assigned_to_admin = repository.create_task(
+        "wf-admin-assign-self",
+        "topic.prepare",
+        "selection_operator",
+        actor=SYSTEM_ACTOR,
+    )
+    assigned_to_user = repository.create_task(
+        "wf-admin-assign-other",
+        "topic.prepare",
+        "selection_operator",
+        actor=SYSTEM_ACTOR,
+    )
+    assert repository.assign_task(
+        assigned_to_admin["id"], admin_user.user_id, admin_user
+    )["assignee_user_id"] == admin_user.user_id
+    assert repository.assign_task(
+        assigned_to_user["id"], selection_user.user_id, admin_user
+    )["assignee_user_id"] == selection_user.user_id
+
+    admin_role = identity_repository.get_role_by_code("system_admin")
+    selection_role = identity_repository.get_role_by_code("selection_operator")
+    assert admin_role is not None
+    assert selection_role is not None
+    identity_repository.replace_user_roles(
+        admin_user.user_id, [admin_role["id"], selection_role["id"]]
+    )
+
+    assert repository.claim_task(task["id"], admin_user)["status"] == "assigned"
+
+
+def test_claim_uses_state_machine_target_status_in_update_and_event(
+    identity_repository: IdentityRepository,
+    selection_user: Principal,
+):
+    class ClaimToInProgress(WorkflowStateMachine):
+        def transition(self, task, action, actor):
+            assert action == "claim"
+            return "in_progress"
+
+    repository = WorkflowRepository(
+        identity_repository, state_machine=ClaimToInProgress()
+    )
+    task = repository.create_task(
+        "wf-claim-target",
+        "topic.prepare",
+        "selection_operator",
+        actor=SYSTEM_ACTOR,
+    )
+
+    claimed = repository.claim_task(task["id"], selection_user)
+
+    assert claimed["status"] == "in_progress"
+    assert repository.list_task_events(task["id"])[-1]["after_status"] == "in_progress"
+
+
+def test_state_machine_declares_all_states_and_rejects_invalid_transition(
+    selection_user: Principal,
+):
+    machine = WorkflowStateMachine()
+    task = {
+        "status": "completed",
+        "role_code": "selection_operator",
+        "assignee_user_id": selection_user.user_id,
+        "project_id": None,
+    }
+
+    assert machine.states == frozenset(
+        {
+            "pool",
+            "assigned",
+            "in_progress",
+            "waiting_review",
+            "returned",
+            "completed",
+            "cancelled",
+        }
+    )
+    with pytest.raises(ValueError):
+        machine.transition(task, "start", selection_user)
diff --git a/workflow/__init__.py b/workflow/__init__.py
new file mode 100644
index 0000000..b58907a
--- /dev/null
+++ b/workflow/__init__.py
@@ -0,0 +1,15 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+"""Human workflow task pools and project membership management."""
+
+from .repository import SYSTEM_ACTOR, WorkflowRepository
+from .state_machine import WorkflowStateMachine
+
+__all__ = ["SYSTEM_ACTOR", "WorkflowRepository", "WorkflowStateMachine"]
diff --git a/workflow/api.py b/workflow/api.py
new file mode 100644
index 0000000..1dc3df4
--- /dev/null
+++ b/workflow/api.py
@@ -0,0 +1,141 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from typing import Any, Callable
+
+from fastapi import APIRouter, Depends, HTTPException, Response, status
+
+from identity.dependencies import current_principal, require_permission
+from identity.schema import Principal
+
+from .repository import WorkflowRepository
+from .schema import ProjectMemberCreateRequest, TaskAssignmentRequest
+
+
+def build_workflow_router(repository: WorkflowRepository) -> APIRouter:
+    router = APIRouter()
+
+    @router.get("/api/workflow/tasks")
+    def list_tasks(
+        principal: Principal = Depends(current_principal),
+    ) -> list[dict[str, Any]]:
+        return repository.list_tasks(principal)
+
+    @router.get("/api/workflow/tasks/{task_id}")
+    def get_task(
+        task_id: str,
+        principal: Principal = Depends(current_principal),
+    ) -> dict[str, Any]:
+        task = repository.get_visible_task(task_id, principal)
+        if task is None:
+            raise HTTPException(status_code=404, detail="workflow task not found")
+        return task
+
+    @router.post("/api/workflow/tasks/{task_id}/claim")
+    def claim_task(
+        task_id: str,
+        principal: Principal = Depends(require_permission("workflow.claim")),
+    ) -> dict[str, Any]:
+        return _call(repository.claim_task, task_id, principal)
+
+    @router.post("/api/workflow/tasks/{task_id}/assign")
+    def assign_task(
+        task_id: str,
+        payload: TaskAssignmentRequest,
+        principal: Principal = Depends(require_permission("workflow.assign")),
+    ) -> dict[str, Any]:
+        return _call(
+            repository.assign_task, task_id, payload.assignee_user_id, principal
+        )
+
+    @router.post("/api/workflow/tasks/{task_id}/reassign")
+    def reassign_task(
+        task_id: str,
+        payload: TaskAssignmentRequest,
+        principal: Principal = Depends(require_permission("workflow.reassign")),
+    ) -> dict[str, Any]:
+        return _call(
+            repository.reassign_task, task_id, payload.assignee_user_id, principal
+        )
+
+    @router.post("/api/workflow/tasks/{task_id}/start")
+    def start_task(
+        task_id: str,
+        principal: Principal = Depends(
+            require_permission("workflow.task.update")
+        ),
+    ) -> dict[str, Any]:
+        return _call(repository.start_task, task_id, principal)
+
+    @router.post("/api/workflow/tasks/{task_id}/cancel")
+    def cancel_task(
+        task_id: str,
+        principal: Principal = Depends(require_permission("workflow.cancel")),
+    ) -> dict[str, Any]:
+        return _call(repository.cancel_task, task_id, principal)
+
+    member_manager = require_permission("project.member.manage")
+
+    @router.get("/api/workflow/projects/{project_id}/members")
+    def list_project_members(
+        project_id: str,
+        principal: Principal = Depends(member_manager),
+    ) -> list[dict[str, Any]]:
+        _call(repository.assert_can_manage_project, project_id, principal)
+        return repository.list_project_members(project_id)
+
+    @router.post(
+        "/api/workflow/projects/{project_id}/members",
+        status_code=status.HTTP_201_CREATED,
+    )
+    def add_project_member(
+        project_id: str,
+        payload: ProjectMemberCreateRequest,
+        principal: Principal = Depends(member_manager),
+    ) -> dict[str, Any]:
+        return _call(
+            repository.add_project_member,
+            project_id,
+            payload.user_id,
+            payload.project_role,
+            principal,
+            status=payload.status,
+        )
+
+    @router.delete(
+        "/api/workflow/projects/{project_id}/members/{membership_id}",
+        status_code=status.HTTP_204_NO_CONTENT,
+    )
+    def remove_project_member(
+        project_id: str,
+        membership_id: str,
+        principal: Principal = Depends(member_manager),
+    ) -> Response:
+        _call(
+            repository.remove_project_member,
+            project_id,
+            membership_id,
+            principal,
+        )
+        return Response(status_code=status.HTTP_204_NO_CONTENT)
+
+    return router
+
+
+def _call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
+    try:
+        return operation(*args, **kwargs)
+    except KeyError as exc:
+        raise HTTPException(status_code=404, detail="resource not found") from exc
+    except PermissionError as exc:
+        raise HTTPException(status_code=403, detail=str(exc)) from exc
+    except ValueError as exc:
+        raise HTTPException(status_code=409, detail=str(exc)) from exc
diff --git a/workflow/repository.py b/workflow/repository.py
new file mode 100644
index 0000000..e26a428
--- /dev/null
+++ b/workflow/repository.py
@@ -0,0 +1,810 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+import json
+import sqlite3
+import uuid
+from pathlib import Path
+from typing import Any
+
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+
+from .state_machine import WorkflowStateMachine
+
+
+_MANAGED_PROJECT_ROLES = frozenset(
+    {"owner", "project_owner", "manager", "project_manager"}
+)
+SYSTEM_ACTOR = Principal(
+    user_id="workflow:system",
+    username="workflow-system",
+    display_name="Workflow System",
+    role_codes=("system_admin",),
+    permissions=frozenset({"*"}),
+    data_scope="all",
+    session_id="workflow:system",
+)
+
+_PROJECT_MEMBER_EVENTS_SQL = """
+CREATE TABLE IF NOT EXISTS workflow_project_member_events (
+    id TEXT PRIMARY KEY,
+    project_id TEXT NOT NULL,
+    membership_id TEXT NOT NULL,
+    event_type TEXT NOT NULL,
+    actor_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
+    before_json TEXT,
+    after_json TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE INDEX IF NOT EXISTS idx_workflow_project_member_events_project
+ON workflow_project_member_events(project_id, created_at);
+"""
+
+
+class WorkflowRepository:
+    def __init__(
+        self,
+        repository: IdentityRepository | str | Path,
+        state_machine: WorkflowStateMachine | None = None,
+    ) -> None:
+        self.identity_repository = (
+            repository
+            if isinstance(repository, IdentityRepository)
+            else IdentityRepository(repository)
+        )
+        self.state_machine = state_machine or WorkflowStateMachine()
+        with self.identity_repository.connect() as connection:
+            connection.executescript(_PROJECT_MEMBER_EVENTS_SQL)
+
+    def create_task(
+        self,
+        workflow_id: str,
+        task_type: str,
+        role_code: str,
+        *,
+        project_id: str | None = None,
+        topic_id: int | None = None,
+        due_at: str | None = None,
+        actor: Principal | dict[str, Any],
+    ) -> dict[str, Any]:
+        task_id = str(uuid.uuid4())
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            self._ensure_system_actor(connection, actor_id)
+            role = connection.execute(
+                "SELECT 1 FROM roles WHERE code = ? COLLATE NOCASE AND is_enabled = 1",
+                (role_code,),
+            ).fetchone()
+            if role is None:
+                raise ValueError(f"unknown or disabled role: {role_code}")
+            self._ensure_workflow(
+                connection,
+                workflow_id,
+                task_type,
+                project_id,
+                topic_id,
+                actor_id,
+            )
+            connection.execute(
+                """
+                INSERT INTO workflow_tasks
+                    (id, workflow_id, task_type, role_code, topic_id, project_id,
+                     assignee_id, assigned_by, status, due_at)
+                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pool', ?)
+                """,
+                (
+                    task_id,
+                    workflow_id,
+                    task_type,
+                    role_code,
+                    topic_id,
+                    project_id,
+                    due_at,
+                ),
+            )
+            self._record_event(
+                connection,
+                task_id,
+                "create",
+                actor_id,
+                None,
+                "pool",
+                {"role_code": role_code},
+            )
+            task = self._get_task(connection, task_id)
+        assert task is not None
+        return task
+
+    def get_task(self, task_id: str) -> dict[str, Any] | None:
+        with self.identity_repository.connect() as connection:
+            return self._get_task(connection, task_id)
+
+    def list_tasks(
+        self, actor: Principal | dict[str, Any]
+    ) -> list[dict[str, Any]]:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.connect() as connection:
+            live_actor = self._live_actor(connection, actor_id)
+            if live_actor["is_system_admin"]:
+                rows = connection.execute(
+                    "SELECT * FROM workflow_tasks ORDER BY created_at, id"
+                ).fetchall()
+            else:
+                role_codes = live_actor["role_codes"]
+                managed_projects = self._managed_project_ids(connection, actor_id)
+                assigned_projects = self._assigned_project_ids(connection, actor_id)
+                rows = connection.execute(
+                    "SELECT * FROM workflow_tasks ORDER BY created_at, id"
+                ).fetchall()
+                rows = [
+                    row
+                    for row in rows
+                    if row["assignee_id"] == actor_id
+                    or (
+                        row["status"] == "pool"
+                        and row["role_code"] in role_codes
+                        and (
+                            row["project_id"] is None
+                            or (
+                                row["role_code"] == "project_owner"
+                                and row["project_id"] in managed_projects
+                            )
+                            or row["project_id"] in assigned_projects
+                        )
+                    )
+                    or (
+                        row["project_id"] is not None
+                        and row["project_id"] in managed_projects
+                    )
+                ]
+        return [self._task_dict(row) for row in rows]
+
+    def get_visible_task(
+        self, task_id: str, actor: Principal | dict[str, Any]
+    ) -> dict[str, Any] | None:
+        return next(
+            (task for task in self.list_tasks(actor) if task["id"] == task_id), None
+        )
+
+    def claim_task(
+        self, task_id: str, actor: Principal | dict[str, Any]
+    ) -> dict[str, Any]:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            task = self._require_task(connection, task_id)
+            live_actor = self._live_actor(connection, actor_id)
+            if (
+                task["role_code"] == "project_owner"
+                and not live_actor["is_system_admin"]
+            ):
+                self._assert_task_manager(connection, task, live_actor)
+            elif (
+                task["project_id"] is not None
+                and not live_actor["is_system_admin"]
+                and not self._is_active_project_member(
+                    connection, task["project_id"], actor_id
+                )
+            ):
+                raise PermissionError("actor must be an active project member")
+            to_status = self.state_machine.transition(task, "claim", live_actor)
+            cursor = connection.execute(
+                """
+                UPDATE workflow_tasks
+                SET assignee_id = ?, assigned_by = ?, status = ?,
+                    updated_at = CURRENT_TIMESTAMP
+                WHERE id = ? AND status = 'pool' AND assignee_id IS NULL
+                """,
+                (actor_id, actor_id, to_status, task_id),
+            )
+            if cursor.rowcount != 1:
+                raise ValueError("task has already been claimed")
+            self._record_event(
+                connection,
+                task_id,
+                "claim",
+                actor_id,
+                task["status"],
+                to_status,
+                {"assignee_user_id": actor_id},
+            )
+            claimed = self._require_task(connection, task_id)
+        return claimed
+
+    def assign_task(
+        self,
+        task_id: str,
+        assignee_user_id: str,
+        actor: Principal | dict[str, Any],
+    ) -> dict[str, Any]:
+        return self._assign(task_id, assignee_user_id, actor, "assign")
+
+    def reassign_task(
+        self,
+        task_id: str,
+        assignee_user_id: str,
+        actor: Principal | dict[str, Any],
+    ) -> dict[str, Any]:
+        return self._assign(task_id, assignee_user_id, actor, "reassign")
+
+    def start_task(
+        self, task_id: str, actor: Principal | dict[str, Any]
+    ) -> dict[str, Any]:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            task = self._require_task(connection, task_id)
+            live_actor = self._live_actor(connection, actor_id)
+            to_status = self.state_machine.transition(task, "start", live_actor)
+            connection.execute(
+                """
+                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
+                WHERE id = ? AND status = ?
+                """,
+                (to_status, task_id, task["status"]),
+            )
+            self._record_event(
+                connection,
+                task_id,
+                "start",
+                actor_id,
+                task["status"],
+                to_status,
+            )
+            started = self._require_task(connection, task_id)
+        return started
+
+    def cancel_task(
+        self, task_id: str, actor: Principal | dict[str, Any]
+    ) -> dict[str, Any]:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            task = self._require_task(connection, task_id)
+            live_actor = self._live_actor(connection, actor_id)
+            self._assert_task_manager(connection, task, live_actor)
+            to_status = self.state_machine.transition(task, "cancel", live_actor)
+            connection.execute(
+                """
+                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
+                WHERE id = ? AND status = ?
+                """,
+                (to_status, task_id, task["status"]),
+            )
+            self._record_event(
+                connection,
+                task_id,
+                "cancel",
+                actor_id,
+                task["status"],
+                to_status,
+            )
+            cancelled = self._require_task(connection, task_id)
+        return cancelled
+
+    def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
+        with self.identity_repository.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT * FROM workflow_task_events
+                WHERE task_id = ? ORDER BY created_at, rowid
+                """,
+                (task_id,),
+            ).fetchall()
+        return [self._event_dict(row) for row in rows]
+
+    def add_project_member(
+        self,
+        project_id: str,
+        user_id: str,
+        project_role: str,
+        actor: Principal | dict[str, Any],
+        *,
+        status: str = "active",
+    ) -> dict[str, Any]:
+        if status not in {"active", "inactive"}:
+            raise ValueError("project member status must be active or inactive")
+        if not project_role.strip():
+            raise ValueError("project role is required")
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            live_actor = self._live_actor(connection, actor_id)
+            has_members = connection.execute(
+                "SELECT 1 FROM project_members WHERE project_id = ? LIMIT 1",
+                (project_id,),
+            ).fetchone()
+            is_owner_bootstrap = (
+                has_members is None
+                and actor_id == user_id
+                and project_role in _MANAGED_PROJECT_ROLES
+                and "project_owner" in live_actor["role_codes"]
+            )
+            if not is_owner_bootstrap:
+                self._assert_project_manager(connection, project_id, live_actor)
+            user = connection.execute(
+                """
+                SELECT 1 FROM users
+                WHERE id = ? AND status = 'active' AND is_deleted = 0
+                """,
+                (user_id,),
+            ).fetchone()
+            if user is None:
+                raise ValueError("project member user does not exist or is inactive")
+            existing = connection.execute(
+                """
+                SELECT * FROM project_members
+                WHERE project_id = ? AND user_id = ? AND project_role = ?
+                """,
+                (project_id, user_id, project_role),
+            ).fetchone()
+            membership_id = existing["id"] if existing else str(uuid.uuid4())
+            connection.execute(
+                """
+                INSERT INTO project_members
+                    (id, project_id, user_id, project_role, status, added_by)
+                VALUES (?, ?, ?, ?, ?, ?)
+                ON CONFLICT(project_id, user_id, project_role) DO UPDATE SET
+                    status = excluded.status,
+                    added_by = excluded.added_by,
+                    updated_at = CURRENT_TIMESTAMP
+                """,
+                (
+                    membership_id,
+                    project_id,
+                    user_id,
+                    project_role,
+                    status,
+                    actor_id,
+                ),
+            )
+            member = connection.execute(
+                "SELECT * FROM project_members WHERE id = ?", (membership_id,)
+            ).fetchone()
+            assert member is not None
+            self._record_project_member_event(
+                connection,
+                project_id,
+                membership_id,
+                "project_member.update" if existing else "project_member.add",
+                actor_id,
+                dict(existing) if existing else None,
+                dict(member),
+            )
+        return dict(member)
+
+    def list_project_members(self, project_id: str) -> list[dict[str, Any]]:
+        with self.identity_repository.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT * FROM project_members
+                WHERE project_id = ? ORDER BY created_at, rowid
+                """,
+                (project_id,),
+            ).fetchall()
+        return [dict(row) for row in rows]
+
+    def list_project_member_events(self, project_id: str) -> list[dict[str, Any]]:
+        with self.identity_repository.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT * FROM workflow_project_member_events
+                WHERE project_id = ? ORDER BY created_at, rowid
+                """,
+                (project_id,),
+            ).fetchall()
+        return [self._project_member_event_dict(row) for row in rows]
+
+    def assert_can_manage_project(
+        self, project_id: str, actor: Principal | dict[str, Any]
+    ) -> None:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.connect() as connection:
+            live_actor = self._live_actor(connection, actor_id)
+            self._assert_project_manager(connection, project_id, live_actor)
+
+    def remove_project_member(
+        self,
+        project_id: str,
+        membership_id: str,
+        actor: Principal | dict[str, Any],
+    ) -> None:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            live_actor = self._live_actor(connection, actor_id)
+            self._assert_project_manager(connection, project_id, live_actor)
+            member = connection.execute(
+                """
+                SELECT * FROM project_members WHERE project_id = ? AND id = ?
+                """,
+                (project_id, membership_id),
+            ).fetchone()
+            if member is None:
+                matches = connection.execute(
+                    """
+                    SELECT * FROM project_members
+                    WHERE project_id = ? AND user_id = ? ORDER BY rowid
+                    """,
+                    (project_id, membership_id),
+                ).fetchall()
+                if len(matches) == 1:
+                    member = matches[0]
+                elif len(matches) > 1:
+                    raise ValueError("user has multiple project roles; remove by membership id")
+            if member is None:
+                raise KeyError(membership_id)
+            if (
+                member["status"] == "active"
+                and member["project_role"] == "project_owner"
+            ):
+                other_owner = connection.execute(
+                    """
+                    SELECT 1 FROM project_members
+                    WHERE project_id = ? AND id <> ? AND status = 'active'
+                      AND project_role = 'project_owner'
+                    LIMIT 1
+                    """,
+                    (project_id, member["id"]),
+                ).fetchone()
+                if other_owner is None:
+                    raise ValueError(
+                        "current project owner cannot be removed until another owner is assigned"
+                    )
+            connection.execute(
+                "DELETE FROM project_members WHERE id = ?", (member["id"],)
+            )
+            self._record_project_member_event(
+                connection,
+                project_id,
+                member["id"],
+                "project_member.remove",
+                actor_id,
+                dict(member),
+                None,
+            )
+
+    def _assign(
+        self,
+        task_id: str,
+        assignee_user_id: str,
+        actor: Principal | dict[str, Any],
+        action: str,
+    ) -> dict[str, Any]:
+        actor_id = self._actor_id(actor)
+        with self.identity_repository.transaction() as connection:
+            task = self._require_task(connection, task_id)
+            live_actor = self._live_actor(connection, actor_id)
+            self._assert_task_manager(connection, task, live_actor)
+            to_status = self.state_machine.transition(task, action, live_actor)
+            assignee = self._live_actor(connection, assignee_user_id)
+            if (
+                not assignee["is_system_admin"]
+                and task["role_code"] not in assignee["role_codes"]
+            ):
+                raise ValueError("assignee does not have the task role")
+            if task["project_id"] is not None:
+                membership = connection.execute(
+                    """
+                    SELECT 1 FROM project_members
+                    WHERE project_id = ? AND user_id = ? AND status = 'active'
+                    """,
+                    (task["project_id"], assignee_user_id),
+                ).fetchone()
+                if membership is None:
+                    raise ValueError("assignee must be an active project member")
+            if action == "assign":
+                cursor = connection.execute(
+                    """
+                    UPDATE workflow_tasks
+                    SET assignee_id = ?, assigned_by = ?, status = ?,
+                        updated_at = CURRENT_TIMESTAMP
+                    WHERE id = ? AND status = 'pool' AND assignee_id IS NULL
+                    """,
+                    (assignee_user_id, actor_id, to_status, task_id),
+                )
+            else:
+                cursor = connection.execute(
+                    """
+                    UPDATE workflow_tasks
+                    SET assignee_id = ?, assigned_by = ?, status = ?,
+                        updated_at = CURRENT_TIMESTAMP
+                    WHERE id = ? AND status = ?
+                    """,
+                    (
+                        assignee_user_id,
+                        actor_id,
+                        to_status,
+                        task_id,
+                        task["status"],
+                    ),
+                )
+            if cursor.rowcount != 1:
+                raise ValueError("task assignment changed concurrently")
+            self._record_event(
+                connection,
+                task_id,
+                action,
+                actor_id,
+                task["status"],
+                to_status,
+                {
+                    "before_assignee_user_id": task["assignee_user_id"],
+                    "assignee_user_id": assignee_user_id,
+                },
+            )
+            assigned = self._require_task(connection, task_id)
+        return assigned
+
+    def _assert_task_manager(
+        self,
+        connection: sqlite3.Connection,
+        task: dict[str, Any],
+        actor: dict[str, Any],
+    ) -> None:
+        if actor["is_system_admin"]:
+            return
+        project_id = task.get("project_id")
+        if project_id is None:
+            raise PermissionError("only a system administrator may manage unscoped tasks")
+        self._assert_project_manager(connection, project_id, actor)
+
+    @staticmethod
+    def _assert_project_manager(
+        connection: sqlite3.Connection,
+        project_id: str,
+        actor: dict[str, Any],
+    ) -> None:
+        if actor["is_system_admin"]:
+            return
+        if "project_owner" not in actor["role_codes"]:
+            raise PermissionError("actor is not a project owner")
+        member = connection.execute(
+            """
+            SELECT 1 FROM project_members
+            WHERE project_id = ? AND user_id = ? AND status = 'active'
+              AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
+            """,
+            (project_id, actor["user_id"]),
+        ).fetchone()
+        if member is None:
+            raise PermissionError("actor does not manage this project")
+
+    @staticmethod
+    def _managed_project_ids(
+        connection: sqlite3.Connection, actor_id: str
+    ) -> set[str]:
+        return {
+            row["project_id"]
+            for row in connection.execute(
+                """
+                SELECT DISTINCT project_id FROM project_members
+                WHERE user_id = ? AND status = 'active'
+                  AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
+                """,
+                (actor_id,),
+            )
+        }
+
+    @staticmethod
+    def _assigned_project_ids(
+        connection: sqlite3.Connection, actor_id: str
+    ) -> set[str]:
+        return {
+            row["project_id"]
+            for row in connection.execute(
+                """
+                SELECT DISTINCT project_id FROM project_members
+                WHERE user_id = ? AND status = 'active'
+                """,
+                (actor_id,),
+            )
+        }
+
+    @staticmethod
+    def _is_active_project_member(
+        connection: sqlite3.Connection, project_id: str, actor_id: str
+    ) -> bool:
+        return (
+            connection.execute(
+                """
+                SELECT 1 FROM project_members
+                WHERE project_id = ? AND user_id = ? AND status = 'active'
+                LIMIT 1
+                """,
+                (project_id, actor_id),
+            ).fetchone()
+            is not None
+        )
+
+    @staticmethod
+    def _live_actor(connection: sqlite3.Connection, user_id: str) -> dict[str, Any]:
+        user = connection.execute(
+            """
+            SELECT id FROM users
+            WHERE id = ? AND status = 'active' AND is_deleted = 0
+            """,
+            (user_id,),
+        ).fetchone()
+        if user is None:
+            raise PermissionError("actor or assignee is inactive or unknown")
+        roles = {
+            row["code"]
+            for row in connection.execute(
+                """
+                SELECT r.code FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
+                WHERE ur.user_id = ?
+                """,
+                (user_id,),
+            )
+        }
+        return {
+            "user_id": user_id,
+            "role_codes": roles,
+            "permissions": {"*"} if "system_admin" in roles else set(),
+            "is_system_admin": "system_admin" in roles,
+        }
+
+    @staticmethod
+    def _actor_id(actor: Principal | dict[str, Any]) -> str:
+        if actor is None:
+            raise ValueError("a non-null actor is required")
+        if isinstance(actor, dict):
+            actor_id = actor.get("user_id") or actor.get("id")
+        else:
+            actor_id = actor.user_id
+        if not actor_id:
+            raise PermissionError("an authenticated actor is required")
+        return str(actor_id)
+
+    @staticmethod
+    def _ensure_system_actor(
+        connection: sqlite3.Connection, actor_id: str
+    ) -> None:
+        if actor_id != SYSTEM_ACTOR.user_id:
+            return
+        connection.execute(
+            """
+            INSERT INTO users
+                (id, username, normalized_username, display_name, password_hash,
+                 status, is_deleted)
+            VALUES (?, ?, ?, ?, ?, 'disabled', 1)
+            ON CONFLICT(id) DO NOTHING
+            """,
+            (
+                SYSTEM_ACTOR.user_id,
+                SYSTEM_ACTOR.username,
+                SYSTEM_ACTOR.username,
+                SYSTEM_ACTOR.display_name,
+                "system-managed-account",
+            ),
+        )
+
+    @staticmethod
+    def _ensure_workflow(
+        connection: sqlite3.Connection,
+        workflow_id: str,
+        task_type: str,
+        project_id: str | None,
+        topic_id: int | None,
+        actor_id: str | None,
+    ) -> None:
+        resource_type = "project" if project_id else "topic" if topic_id is not None else "task"
+        resource_id = project_id or (str(topic_id) if topic_id is not None else workflow_id)
+        connection.execute(
+            """
+            INSERT INTO workflow_instances
+                (id, workflow_type, resource_type, resource_id, project_id, created_by)
+            VALUES (?, ?, ?, ?, ?, ?)
+            ON CONFLICT(id) DO NOTHING
+            """,
+            (workflow_id, task_type, resource_type, resource_id, project_id, actor_id),
+        )
+
+    @classmethod
+    def _get_task(
+        cls, connection: sqlite3.Connection, task_id: str
+    ) -> dict[str, Any] | None:
+        row = connection.execute(
+            "SELECT * FROM workflow_tasks WHERE id = ?", (task_id,)
+        ).fetchone()
+        return cls._task_dict(row) if row is not None else None
+
+    @classmethod
+    def _require_task(
+        cls, connection: sqlite3.Connection, task_id: str
+    ) -> dict[str, Any]:
+        task = cls._get_task(connection, task_id)
+        if task is None:
+            raise KeyError(task_id)
+        return task
+
+    @staticmethod
+    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
+        task = dict(row)
+        task["assignee_user_id"] = task.pop("assignee_id")
+        task["assigned_by_user_id"] = task.pop("assigned_by")
+        return task
+
+    @staticmethod
+    def _record_event(
+        connection: sqlite3.Connection,
+        task_id: str,
+        event_type: str,
+        actor_id: str | None,
+        from_status: str | None,
+        to_status: str | None,
+        details: dict[str, Any] | None = None,
+    ) -> None:
+        connection.execute(
+            """
+            INSERT INTO workflow_task_events
+                (id, task_id, event_type, actor_id, from_status, to_status, details_json)
+            VALUES (?, ?, ?, ?, ?, ?, ?)
+            """,
+            (
+                str(uuid.uuid4()),
+                task_id,
+                event_type,
+                actor_id,
+                from_status,
+                to_status,
+                json.dumps(details or {}, ensure_ascii=True, sort_keys=True),
+            ),
+        )
+
+    @staticmethod
+    def _record_project_member_event(
+        connection: sqlite3.Connection,
+        project_id: str,
+        membership_id: str,
+        event_type: str,
+        actor_id: str,
+        before: dict[str, Any] | None,
+        after: dict[str, Any] | None,
+    ) -> None:
+        connection.execute(
+            """
+            INSERT INTO workflow_project_member_events
+                (id, project_id, membership_id, event_type, actor_id,
+                 before_json, after_json)
+            VALUES (?, ?, ?, ?, ?, ?, ?)
+            """,
+            (
+                str(uuid.uuid4()),
+                project_id,
+                membership_id,
+                event_type,
+                actor_id,
+                json.dumps(before, ensure_ascii=True, sort_keys=True)
+                if before is not None
+                else None,
+                json.dumps(after, ensure_ascii=True, sort_keys=True)
+                if after is not None
+                else None,
+            ),
+        )
+
+    @staticmethod
+    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
+        event = dict(row)
+        event["actor_user_id"] = event.pop("actor_id")
+        event["before_status"] = event.pop("from_status")
+        event["after_status"] = event.pop("to_status")
+        event["details"] = json.loads(event.pop("details_json"))
+        return event
+
+    @staticmethod
+    def _project_member_event_dict(row: sqlite3.Row) -> dict[str, Any]:
+        event = dict(row)
+        event["actor_user_id"] = event.pop("actor_id")
+        before_json = event.pop("before_json")
+        after_json = event.pop("after_json")
+        event["before"] = json.loads(before_json) if before_json else None
+        event["after"] = json.loads(after_json) if after_json else None
+        return event
diff --git a/workflow/schema.py b/workflow/schema.py
new file mode 100644
index 0000000..dbabb8c
--- /dev/null
+++ b/workflow/schema.py
@@ -0,0 +1,36 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from typing import Literal
+
+from pydantic import BaseModel, Field
+
+
+TaskStatus = Literal[
+    "pool",
+    "assigned",
+    "in_progress",
+    "waiting_review",
+    "returned",
+    "completed",
+    "cancelled",
+]
+ProjectMemberStatus = Literal["active", "inactive"]
+
+
+class TaskAssignmentRequest(BaseModel):
+    assignee_user_id: str = Field(min_length=1)
+
+
+class ProjectMemberCreateRequest(BaseModel):
+    user_id: str = Field(min_length=1)
+    project_role: str = Field(min_length=1, max_length=128)
+    status: ProjectMemberStatus = "active"
diff --git a/workflow/state_machine.py b/workflow/state_machine.py
new file mode 100644
index 0000000..64233a0
--- /dev/null
+++ b/workflow/state_machine.py
@@ -0,0 +1,111 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from dataclasses import dataclass
+from typing import Any
+
+from identity.schema import Principal
+
+
+@dataclass(frozen=True)
+class TransitionRule:
+    from_states: frozenset[str]
+    to_state: str
+    actor_requirement: str
+
+
+TRANSITIONS = {
+    "claim": TransitionRule(frozenset({"pool"}), "assigned", "matching_role"),
+    "assign": TransitionRule(frozenset({"pool"}), "assigned", "manager"),
+    "reassign": TransitionRule(
+        frozenset({"assigned", "in_progress", "waiting_review", "returned"}),
+        "assigned",
+        "manager",
+    ),
+    "start": TransitionRule(
+        frozenset({"assigned", "returned"}), "in_progress", "assignee"
+    ),
+    "submit": TransitionRule(
+        frozenset({"in_progress"}), "waiting_review", "assignee"
+    ),
+    "return": TransitionRule(
+        frozenset({"waiting_review"}), "returned", "manager"
+    ),
+    "complete": TransitionRule(
+        frozenset({"in_progress", "waiting_review"}), "completed", "manager"
+    ),
+    "cancel": TransitionRule(
+        frozenset(
+            {"pool", "assigned", "in_progress", "waiting_review", "returned"}
+        ),
+        "cancelled",
+        "manager",
+    ),
+}
+
+
+class WorkflowStateMachine:
+    states = frozenset(
+        {
+            "pool",
+            "assigned",
+            "in_progress",
+            "waiting_review",
+            "returned",
+            "completed",
+            "cancelled",
+        }
+    )
+    transitions = TRANSITIONS
+
+    def transition(
+        self, task: dict[str, Any], action: str, actor: Principal | dict[str, Any]
+    ) -> str:
+        rule = self.transitions.get(action)
+        if rule is None:
+            raise ValueError(f"unknown workflow action: {action}")
+        status = task.get("status")
+        if status not in rule.from_states:
+            raise ValueError(f"cannot {action} a task in {status!r} state")
+        self._check_actor(rule.actor_requirement, task, actor)
+        return rule.to_state
+
+    @staticmethod
+    def _check_actor(
+        requirement: str,
+        task: dict[str, Any],
+        actor: Principal | dict[str, Any],
+    ) -> None:
+        actor_id = _actor_value(actor, "user_id", "id")
+        role_codes = set(_actor_value(actor, "role_codes") or ())
+        permissions = set(_actor_value(actor, "permissions") or ())
+        is_admin = "system_admin" in role_codes or "*" in permissions
+        if requirement == "matching_role":
+            if task.get("role_code") not in role_codes:
+                raise PermissionError("actor does not have the task pool role")
+            return
+        if requirement == "assignee":
+            if not is_admin and actor_id != task.get("assignee_user_id"):
+                raise PermissionError("only the task assignee may perform this action")
+            return
+        if requirement == "manager" and not (
+            is_admin or "project_owner" in role_codes
+        ):
+            raise PermissionError("only a project owner or system administrator may act")
+
+
+def _actor_value(actor: Principal | dict[str, Any], *names: str) -> Any:
+    for name in names:
+        if isinstance(actor, dict) and name in actor:
+            return actor[name]
+        if hasattr(actor, name):
+            return getattr(actor, name)
+    return None
