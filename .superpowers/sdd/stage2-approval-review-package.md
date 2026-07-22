# Commits

682d41a feat: add lightweight approval state machine

# Stat

 identity/repository.py           |  24 +++
 tests/test_workflow_approvals.py | 409 +++++++++++++++++++++++++++++++++++++++
 workflow/approval_service.py     | 369 +++++++++++++++++++++++++++++++++++
 workflow/repository.py           | 111 ++++++++++-
 workflow/schema.py               |   7 +
 5 files changed, 919 insertions(+), 1 deletion(-)

# Diff

diff --git a/identity/repository.py b/identity/repository.py
index 1ca5098..a0ee225 100644
--- a/identity/repository.py
+++ b/identity/repository.py
@@ -122,20 +122,42 @@ CREATE TABLE IF NOT EXISTS workflow_task_jobs (
 CREATE TABLE IF NOT EXISTS workflow_task_events (
     id TEXT PRIMARY KEY,
     task_id TEXT NOT NULL REFERENCES workflow_tasks(id) ON DELETE CASCADE,
     event_type TEXT NOT NULL,
     actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
     from_status TEXT,
     to_status TEXT,
     details_json TEXT NOT NULL DEFAULT '{}',
     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
 );
+CREATE TABLE IF NOT EXISTS workflow_submissions (
+    id TEXT PRIMARY KEY,
+    task_id TEXT NOT NULL,
+    resource_type TEXT NOT NULL,
+    resource_id TEXT NOT NULL,
+    version INTEGER NOT NULL,
+    parent_submission_id TEXT,
+    status TEXT NOT NULL CHECK (
+        status IN ('draft', 'submitted', 'approved', 'returned', 'superseded')
+    ),
+    content_snapshot_json TEXT NOT NULL,
+    content_fingerprint TEXT NOT NULL,
+    created_by TEXT NOT NULL,
+    submitted_by TEXT,
+    submitted_at REAL,
+    reviewed_by TEXT,
+    reviewed_at REAL,
+    review_comment TEXT NOT NULL DEFAULT '',
+    created_at REAL NOT NULL,
+    updated_at REAL NOT NULL,
+    UNIQUE(resource_type, resource_id, version)
+);
 CREATE TABLE IF NOT EXISTS approval_records (
     id TEXT PRIMARY KEY,
     workflow_id TEXT REFERENCES workflow_instances(id) ON DELETE SET NULL,
     task_id TEXT REFERENCES workflow_tasks(id) ON DELETE SET NULL,
     version_id TEXT NOT NULL,
     stage TEXT NOT NULL,
     decision TEXT NOT NULL,
     reviewer_id TEXT REFERENCES users(id) ON DELETE SET NULL,
     comment TEXT,
     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
@@ -148,20 +170,22 @@ CREATE TABLE IF NOT EXISTS audit_logs (
     resource_id TEXT,
     project_id TEXT,
     reason TEXT,
     details_json TEXT NOT NULL DEFAULT '{}',
     ip_address TEXT,
     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
 );
 CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
 CREATE INDEX IF NOT EXISTS idx_project_members_project_id ON project_members(project_id);
 CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pool ON workflow_tasks(role_code, status);
+CREATE INDEX IF NOT EXISTS idx_workflow_submissions_task
+ON workflow_submissions(task_id, status);
 CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
 """
 
 
 class IdentityRepository:
     def __init__(self, db_path: str | Path):
         self.db_path = Path(db_path)
         self.db_path.parent.mkdir(parents=True, exist_ok=True)
         self._init_schema()
 
diff --git a/tests/test_workflow_approvals.py b/tests/test_workflow_approvals.py
new file mode 100644
index 0000000..9999a0c
--- /dev/null
+++ b/tests/test_workflow_approvals.py
@@ -0,0 +1,409 @@
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
+import hashlib
+from pathlib import Path
+
+import pytest
+
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+from identity.session_service import SessionService
+from workflow import SYSTEM_ACTOR, WorkflowRepository
+from workflow.approval_service import ApprovalService
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
+def _user(
+    repository: IdentityRepository,
+    username: str,
+    *role_codes: str,
+) -> Principal:
+    roles = [repository.get_role_by_code(code) for code in role_codes]
+    assert all(role is not None for role in roles)
+    user = repository.create_user(username, username.title(), "hash")
+    repository.replace_user_roles(user["id"], [role["id"] for role in roles])
+    principal = PermissionService(
+        repository, SessionService(repository)
+    ).principal_for_user(user["id"], f"session:{username}")
+    assert principal is not None
+    return principal
+
+
+@pytest.fixture
+def author(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "author", "selection_operator")
+
+
+@pytest.fixture
+def reviewer(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "reviewer", "project_owner")
+
+
+@pytest.fixture
+def admin(identity_repository: IdentityRepository) -> Principal:
+    return _user(identity_repository, "admin", "system_admin")
+
+
+@pytest.fixture
+def repository(identity_repository: IdentityRepository) -> WorkflowRepository:
+    return WorkflowRepository(identity_repository)
+
+
+@pytest.fixture
+def service(repository: WorkflowRepository) -> ApprovalService:
+    return ApprovalService(repository)
+
+
+def _started_task(
+    repository: WorkflowRepository,
+    author: Principal,
+    admin: Principal,
+    *,
+    project_id: str = "project-1",
+    workflow_id: str = "workflow-1",
+) -> dict:
+    repository.add_project_member(
+        project_id, author.user_id, "selection_operator", admin
+    )
+    task = repository.create_task(
+        workflow_id,
+        "breakdown.create",
+        "selection_operator",
+        project_id=project_id,
+        actor=SYSTEM_ACTOR,
+    )
+    repository.assign_task(task["id"], author.user_id, admin)
+    return repository.start_task(task["id"], author)
+
+
+def _managed_project(
+    repository: WorkflowRepository,
+    reviewer: Principal,
+    admin: Principal,
+    project_id: str = "project-1",
+) -> None:
+    repository.add_project_member(
+        project_id, reviewer.user_id, "project_owner", admin
+    )
+
+
+def _submitted(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    admin: Principal,
+    *,
+    resource_id: str = "resource-1",
+    project_id: str = "project-1",
+    workflow_id: str = "workflow-1",
+) -> tuple[dict, dict]:
+    task = _started_task(
+        repository,
+        author,
+        admin,
+        project_id=project_id,
+        workflow_id=workflow_id,
+    )
+    draft = service.create_draft(
+        task_id=task["id"],
+        resource_type="breakdown",
+        resource_id=resource_id,
+        content={"title": "\u4e00", "items": [2, 1]},
+        actor=author,
+    )
+    return task, service.submit(draft["id"], author)
+
+
+def test_draft_is_editable_only_by_task_assignee(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    task = _started_task(repository, author, admin)
+    draft = service.create_draft(
+        task_id=task["id"],
+        resource_type="breakdown",
+        resource_id="resource-1",
+        content={"draft": True},
+        actor=author,
+    )
+
+    assert service.assert_editable(draft["id"], author)["status"] == "draft"
+    with pytest.raises(PermissionError, match="assignee"):
+        service.assert_editable(draft["id"], reviewer)
+
+
+def test_submit_requires_assignee_and_freezes_canonical_snapshot(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    identity_repository: IdentityRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    task = _started_task(repository, author, admin)
+    draft = service.create_draft(
+        task_id=task["id"],
+        resource_type="breakdown",
+        resource_id="resource-1",
+        content={"z": [3, 2], "name": "\u4e2d\u6587"},
+        actor=author,
+    )
+
+    with pytest.raises(PermissionError, match="assignee"):
+        service.submit(draft["id"], reviewer)
+    submitted = service.submit(draft["id"], author)
+
+    canonical = '{"name":"\u4e2d\u6587","z":[3,2]}'
+    assert submitted["status"] == "submitted"
+    assert submitted["content_snapshot"] == {"name": "\u4e2d\u6587", "z": [3, 2]}
+    assert submitted["content_fingerprint"] == hashlib.sha256(
+        canonical.encode("utf-8")
+    ).hexdigest()
+    assert repository.get_task(task["id"])["status"] == "waiting_review"
+    with identity_repository.connect() as connection:
+        stored = connection.execute(
+            "SELECT content_snapshot_json FROM workflow_submissions WHERE id = ?",
+            (submitted["id"],),
+        ).fetchone()
+    assert stored["content_snapshot_json"] == canonical
+    with pytest.raises(ValueError, match="draft"):
+        service.assert_editable(submitted["id"], author)
+
+
+def test_self_approval_is_rejected_but_system_admin_can_approve(
+    identity_repository: IdentityRepository,
+    repository: WorkflowRepository,
+    admin: Principal,
+):
+    owner_author = _user(
+        identity_repository,
+        "owner_author",
+        "selection_operator",
+        "project_owner",
+    )
+    repository.add_project_member(
+        "project-self", owner_author.user_id, "project_owner", admin
+    )
+    task = _started_task(
+        repository,
+        owner_author,
+        admin,
+        project_id="project-self",
+        workflow_id="workflow-self",
+    )
+    service = ApprovalService(repository)
+    draft = service.create_draft(
+        task_id=task["id"],
+        resource_type="brief",
+        resource_id="self-resource",
+        content={"body": "draft"},
+        actor=owner_author,
+    )
+    submitted = service.submit(draft["id"], owner_author)
+
+    with pytest.raises(PermissionError, match="own submission"):
+        service.approve(submitted["id"], owner_author)
+    approved = service.approve(submitted["id"], admin)
+
+    assert approved["status"] == "approved"
+    assert approved["reviewed_by"] == admin.user_id
+    assert repository.get_task(task["id"])["status"] == "completed"
+
+
+def test_managed_project_reviewer_can_approve_and_events_have_snapshots(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    _managed_project(repository, reviewer, admin)
+    task, submitted = _submitted(service, repository, author, admin)
+
+    approved = service.approve(submitted["id"], reviewer)
+
+    assert approved["status"] == "approved"
+    events = repository.list_task_events(task["id"])
+    approval_events = [event for event in events if event["event_type"] == "approve"]
+    assert len(approval_events) == 1
+    event = approval_events[0]
+    assert event["actor_user_id"] == reviewer.user_id
+    assert event["before_status"] == "waiting_review"
+    assert event["after_status"] == "completed"
+    assert event["details"]["before"]["status"] == "submitted"
+    assert event["details"]["after"]["status"] == "approved"
+
+
+def test_return_requires_comment_preserves_source_and_creates_next_draft(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    _managed_project(repository, reviewer, admin)
+    task, submitted = _submitted(service, repository, author, admin)
+    frozen_content = submitted["content_snapshot"]
+    frozen_fingerprint = submitted["content_fingerprint"]
+
+    with pytest.raises(ValueError, match="comment"):
+        service.return_submission(submitted["id"], reviewer, "   ")
+    returned, draft = service.return_submission(
+        submitted["id"], reviewer, "Add source evidence"
+    )
+
+    assert returned["status"] == "returned"
+    assert returned["review_comment"] == "Add source evidence"
+    assert returned["content_snapshot"] == frozen_content
+    assert returned["content_fingerprint"] == frozen_fingerprint
+    assert draft["status"] == "draft"
+    assert draft["version"] == returned["version"] + 1
+    assert draft["parent_submission_id"] == returned["id"]
+    assert draft["content_snapshot"] == frozen_content
+    reopened = repository.get_task(task["id"])
+    assert reopened["status"] == "returned"
+    assert reopened["assignee_user_id"] == author.user_id
+    with pytest.raises(ValueError, match="submitted"):
+        service.return_submission(submitted["id"], reviewer, "Again")
+
+
+def test_approving_newer_version_supersedes_previous_approval(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    _managed_project(repository, reviewer, admin)
+    _, first_submitted = _submitted(service, repository, author, admin)
+    first_approved = service.approve(first_submitted["id"], reviewer)
+    _, second_submitted = _submitted(
+        service,
+        repository,
+        author,
+        admin,
+        workflow_id="workflow-2",
+    )
+
+    second_approved = service.approve(second_submitted["id"], reviewer)
+
+    assert first_approved["version"] == 1
+    assert second_approved["version"] == 2
+    assert service.get_submission(first_approved["id"])["status"] == "superseded"
+    assert service.get_submission(second_approved["id"])["status"] == "approved"
+
+
+@pytest.mark.parametrize("source_state", ["approved", "returned"])
+def test_fork_creates_next_draft_without_mutating_source(
+    source_state: str,
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    _managed_project(repository, reviewer, admin)
+    _, submitted = _submitted(service, repository, author, admin)
+    if source_state == "approved":
+        source = service.approve(submitted["id"], reviewer)
+    else:
+        source, _ = service.return_submission(
+            submitted["id"], reviewer, "Needs another direction"
+        )
+
+    forked = service.fork(source["id"], author)
+
+    assert service.get_submission(source["id"])["status"] == source_state
+    assert forked["status"] == "draft"
+    assert forked["parent_submission_id"] == source["id"]
+    assert forked["version"] > source["version"]
+    assert forked["content_snapshot"] == source["content_snapshot"]
+
+
+def test_repeated_terminal_transitions_are_rejected(
+    service: ApprovalService,
+    repository: WorkflowRepository,
+    author: Principal,
+    reviewer: Principal,
+    admin: Principal,
+):
+    _managed_project(repository, reviewer, admin)
+    _, submitted = _submitted(service, repository, author, admin)
+    approved = service.approve(submitted["id"], reviewer)
+
+    with pytest.raises(ValueError, match="submitted"):
+        service.approve(approved["id"], reviewer)
+    with pytest.raises(ValueError, match="submitted"):
+        service.return_submission(approved["id"], reviewer, "Late change")
+    with pytest.raises(ValueError, match="draft"):
+        service.submit(approved["id"], author)
+
+
+def test_submit_rolls_back_when_task_transition_fails(
+    identity_repository: IdentityRepository,
+    author: Principal,
+    admin: Principal,
+):
+    class RejectSubmit(WorkflowStateMachine):
+        def transition(self, task, action, actor):
+            if action == "submit":
+                raise RuntimeError("injected task transition failure")
+            return super().transition(task, action, actor)
+
+    repository = WorkflowRepository(
+        identity_repository, state_machine=RejectSubmit()
+    )
+    service = ApprovalService(repository)
+    task = _started_task(repository, author, admin)
+    draft = service.create_draft(
+        task_id=task["id"],
+        resource_type="breakdown",
+        resource_id="rollback-resource",
+        content={"body": "unchanged"},
+        actor=author,
+    )
+    event_count = len(repository.list_task_events(task["id"]))
+
+    with pytest.raises(RuntimeError, match="injected"):
+        service.submit(draft["id"], author)
+
+    rolled_back = service.get_submission(draft["id"])
+    assert rolled_back["status"] == "draft"
+    assert rolled_back["submitted_by"] is None
+    assert repository.get_task(task["id"])["status"] == "in_progress"
+    assert len(repository.list_task_events(task["id"])) == event_count
+
+
+def test_schema_does_not_introduce_creative_stage_versions(
+    identity_repository: IdentityRepository,
+):
+    with identity_repository.connect() as connection:
+        tables = {
+            row["name"]
+            for row in connection.execute(
+                "SELECT name FROM sqlite_master WHERE type = 'table'"
+            )
+        }
+
+    assert "workflow_submissions" in tables
+    assert "creative_stage_versions" not in tables
diff --git a/workflow/approval_service.py b/workflow/approval_service.py
new file mode 100644
index 0000000..9754106
--- /dev/null
+++ b/workflow/approval_service.py
@@ -0,0 +1,369 @@
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
+import hashlib
+import json
+import sqlite3
+import time
+import uuid
+from pathlib import Path
+from typing import Any
+
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+
+from .repository import WorkflowRepository
+
+
+class ApprovalService:
+    def __init__(
+        self,
+        repository: WorkflowRepository | IdentityRepository | str | Path,
+    ) -> None:
+        self.repository = (
+            repository
+            if isinstance(repository, WorkflowRepository)
+            else WorkflowRepository(repository)
+        )
+
+    def get_submission(self, submission_id: str) -> dict[str, Any] | None:
+        return self.repository.get_submission(submission_id)
+
+    def create_draft(
+        self,
+        *,
+        task_id: str,
+        resource_type: str,
+        resource_id: str,
+        content: dict[str, Any] | list[Any],
+        actor: Principal,
+        parent_submission_id: str | None = None,
+    ) -> dict[str, Any]:
+        self._validate_resource(resource_type, resource_id)
+        canonical = self._canonical_json(content)
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.transaction() as connection:
+            self.repository._live_actor(connection, actor_id)
+            task = self.repository._require_task(connection, task_id)
+            if task["assignee_user_id"] != actor_id:
+                raise PermissionError("only the task assignee may create a draft")
+            if parent_submission_id is not None:
+                parent = self._require_submission(connection, parent_submission_id)
+                if (parent["resource_type"], parent["resource_id"]) != (
+                    resource_type,
+                    resource_id,
+                ):
+                    raise ValueError("parent submission must belong to the same resource")
+            draft = self._insert_draft(
+                connection,
+                task_id=task_id,
+                resource_type=resource_type,
+                resource_id=resource_id,
+                canonical=canonical,
+                created_by=actor_id,
+                parent_submission_id=parent_submission_id,
+            )
+            self.repository.record_task_event_in_transaction(
+                task_id,
+                "submission.create_draft",
+                actor,
+                connection=connection,
+                details={"before": None, "after": draft},
+            )
+        return draft
+
+    def assert_editable(
+        self, submission_id: str, actor: Principal
+    ) -> dict[str, Any]:
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.connect() as connection:
+            self.repository._live_actor(connection, actor_id)
+            submission = self._require_submission(connection, submission_id)
+            task = self.repository._require_task(connection, submission["task_id"])
+        if submission["status"] != "draft":
+            raise ValueError("only a draft submission is editable")
+        if task["assignee_user_id"] != actor_id:
+            raise PermissionError("only the task assignee may edit this draft")
+        return submission
+
+    def submit(self, submission_id: str, actor: Principal) -> dict[str, Any]:
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.transaction() as connection:
+            before = self._require_submission(connection, submission_id)
+            if before["status"] != "draft":
+                raise ValueError("only a draft submission can be submitted")
+            task = self.repository._require_task(connection, before["task_id"])
+            self.repository._live_actor(connection, actor_id)
+            if task["assignee_user_id"] != actor_id:
+                raise PermissionError("only the task assignee may submit")
+            canonical = self._canonical_json(before["content_snapshot"])
+            now = time.time()
+            cursor = connection.execute(
+                """
+                UPDATE workflow_submissions
+                SET status = 'submitted', content_snapshot_json = ?,
+                    content_fingerprint = ?, submitted_by = ?, submitted_at = ?,
+                    updated_at = ?
+                WHERE id = ? AND status = 'draft'
+                """,
+                (
+                    canonical,
+                    self._fingerprint(canonical),
+                    actor_id,
+                    now,
+                    now,
+                    submission_id,
+                ),
+            )
+            self._assert_updated(cursor)
+            after = self._require_submission(connection, submission_id)
+            self.repository.transition_task_in_transaction(
+                before["task_id"],
+                "submit",
+                actor,
+                connection=connection,
+                details={"before": before, "after": after},
+            )
+        return after
+
+    def approve(self, submission_id: str, actor: Principal) -> dict[str, Any]:
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.transaction() as connection:
+            before = self._require_submission(connection, submission_id)
+            if before["status"] != "submitted":
+                raise ValueError("only a submitted submission can be approved")
+            live_actor = self.repository._live_actor(connection, actor_id)
+            if before["submitted_by"] == actor_id and not live_actor["is_system_admin"]:
+                raise PermissionError("a submitter cannot approve their own submission")
+            task = self.repository._require_task(connection, before["task_id"])
+            self._assert_reviewer(connection, task, live_actor, "version.approve")
+            now = time.time()
+            cursor = connection.execute(
+                """
+                UPDATE workflow_submissions
+                SET status = 'approved', reviewed_by = ?, reviewed_at = ?,
+                    review_comment = '', updated_at = ?
+                WHERE id = ? AND status = 'submitted'
+                """,
+                (actor_id, now, now, submission_id),
+            )
+            self._assert_updated(cursor)
+            after = self._require_submission(connection, submission_id)
+            previous_rows = connection.execute(
+                """
+                SELECT * FROM workflow_submissions
+                WHERE resource_type = ? AND resource_id = ? AND status = 'approved'
+                  AND version < ?
+                ORDER BY version
+                """,
+                (
+                    before["resource_type"],
+                    before["resource_id"],
+                    before["version"],
+                ),
+            ).fetchall()
+            for row in previous_rows:
+                previous = self.repository._submission_dict(row)
+                connection.execute(
+                    """
+                    UPDATE workflow_submissions
+                    SET status = 'superseded', updated_at = ?
+                    WHERE id = ? AND status = 'approved'
+                    """,
+                    (now, previous["id"]),
+                )
+                superseded = self._require_submission(connection, previous["id"])
+                self.repository.record_task_event_in_transaction(
+                    previous["task_id"],
+                    "submission.supersede",
+                    actor,
+                    connection=connection,
+                    details={"before": previous, "after": superseded},
+                )
+            self.repository.transition_task_in_transaction(
+                before["task_id"],
+                "complete",
+                actor,
+                connection=connection,
+                details={"before": before, "after": after},
+                event_type="approve",
+            )
+        return after
+
+    def return_submission(
+        self,
+        submission_id: str,
+        actor: Principal,
+        review_comment: str,
+    ) -> tuple[dict[str, Any], dict[str, Any]]:
+        comment = review_comment.strip()
+        if not comment:
+            raise ValueError("a non-empty review comment is required")
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.transaction() as connection:
+            before = self._require_submission(connection, submission_id)
+            if before["status"] != "submitted":
+                raise ValueError("only a submitted submission can be returned")
+            live_actor = self.repository._live_actor(connection, actor_id)
+            task = self.repository._require_task(connection, before["task_id"])
+            self._assert_reviewer(connection, task, live_actor, "version.return")
+            now = time.time()
+            cursor = connection.execute(
+                """
+                UPDATE workflow_submissions
+                SET status = 'returned', reviewed_by = ?, reviewed_at = ?,
+                    review_comment = ?, updated_at = ?
+                WHERE id = ? AND status = 'submitted'
+                """,
+                (actor_id, now, comment, now, submission_id),
+            )
+            self._assert_updated(cursor)
+            returned = self._require_submission(connection, submission_id)
+            draft = self._insert_draft(
+                connection,
+                task_id=before["task_id"],
+                resource_type=before["resource_type"],
+                resource_id=before["resource_id"],
+                canonical=before["content_snapshot_json"],
+                created_by=before["submitted_by"],
+                parent_submission_id=before["id"],
+            )
+            self.repository.transition_task_in_transaction(
+                before["task_id"],
+                "return",
+                actor,
+                connection=connection,
+                assignee_user_id=before["submitted_by"],
+                details={
+                    "before": before,
+                    "after": returned,
+                    "new_draft": draft,
+                },
+            )
+        return returned, draft
+
+    def fork(self, submission_id: str, actor: Principal) -> dict[str, Any]:
+        actor_id = self.repository._actor_id(actor)
+        with self.repository.identity_repository.transaction() as connection:
+            source = self._require_submission(connection, submission_id)
+            if source["status"] not in {"approved", "returned"}:
+                raise ValueError("only an approved or returned submission can be forked")
+            self.repository._live_actor(connection, actor_id)
+            draft = self._insert_draft(
+                connection,
+                task_id=source["task_id"],
+                resource_type=source["resource_type"],
+                resource_id=source["resource_id"],
+                canonical=source["content_snapshot_json"],
+                created_by=actor_id,
+                parent_submission_id=source["id"],
+            )
+            self.repository.record_task_event_in_transaction(
+                source["task_id"],
+                "submission.fork",
+                actor,
+                connection=connection,
+                details={"before": source, "after": draft},
+            )
+        return draft
+
+    def _insert_draft(
+        self,
+        connection: sqlite3.Connection,
+        *,
+        task_id: str,
+        resource_type: str,
+        resource_id: str,
+        canonical: str,
+        created_by: str,
+        parent_submission_id: str | None,
+    ) -> dict[str, Any]:
+        version = connection.execute(
+            """
+            SELECT COALESCE(MAX(version), 0) + 1
+            FROM workflow_submissions
+            WHERE resource_type = ? AND resource_id = ?
+            """,
+            (resource_type, resource_id),
+        ).fetchone()[0]
+        now = time.time()
+        submission_id = str(uuid.uuid4())
+        connection.execute(
+            """
+            INSERT INTO workflow_submissions (
+                id, task_id, resource_type, resource_id, version,
+                parent_submission_id, status, content_snapshot_json,
+                content_fingerprint, created_by, created_at, updated_at
+            ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
+            """,
+            (
+                submission_id,
+                task_id,
+                resource_type,
+                resource_id,
+                version,
+                parent_submission_id,
+                canonical,
+                self._fingerprint(canonical),
+                created_by,
+                now,
+                now,
+            ),
+        )
+        return self._require_submission(connection, submission_id)
+
+    def _assert_reviewer(
+        self,
+        connection: sqlite3.Connection,
+        task: dict[str, Any],
+        actor: dict[str, Any],
+        permission: str,
+    ) -> None:
+        if actor["is_system_admin"]:
+            return
+        if permission not in actor["permissions"]:
+            raise PermissionError(f"{permission} permission is required")
+        self.repository._assert_task_manager(connection, task, actor)
+
+    def _require_submission(
+        self, connection: sqlite3.Connection, submission_id: str
+    ) -> dict[str, Any]:
+        submission = self.repository.get_submission(
+            submission_id, connection=connection
+        )
+        if submission is None:
+            raise KeyError(submission_id)
+        return submission
+
+    @staticmethod
+    def _validate_resource(resource_type: str, resource_id: str) -> None:
+        if not resource_type.strip() or not resource_id.strip():
+            raise ValueError("resource type and id are required")
+
+    @staticmethod
+    def _canonical_json(content: dict[str, Any] | list[Any]) -> str:
+        if not isinstance(content, (dict, list)):
+            raise TypeError("submission content must be a dict or list")
+        return json.dumps(
+            content,
+            ensure_ascii=False,
+            sort_keys=True,
+            separators=(",", ":"),
+        )
+
+    @staticmethod
+    def _fingerprint(canonical: str) -> str:
+        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
+
+    @staticmethod
+    def _assert_updated(cursor: sqlite3.Cursor) -> None:
+        if cursor.rowcount != 1:
+            raise ValueError("submission state changed concurrently")
diff --git a/workflow/repository.py b/workflow/repository.py
index e26a428..968c43a 100644
--- a/workflow/repository.py
+++ b/workflow/repository.py
@@ -121,20 +121,109 @@ class WorkflowRepository:
                 {"role_code": role_code},
             )
             task = self._get_task(connection, task_id)
         assert task is not None
         return task
 
     def get_task(self, task_id: str) -> dict[str, Any] | None:
         with self.identity_repository.connect() as connection:
             return self._get_task(connection, task_id)
 
+    def get_submission(
+        self,
+        submission_id: str,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> dict[str, Any] | None:
+        with self.identity_repository.connection_scope(connection) as active_connection:
+            row = active_connection.execute(
+                "SELECT * FROM workflow_submissions WHERE id = ?",
+                (submission_id,),
+            ).fetchone()
+        return self._submission_dict(row) if row is not None else None
+
+    def transition_task_in_transaction(
+        self,
+        task_id: str,
+        action: str,
+        actor: Principal | dict[str, Any],
+        *,
+        connection: sqlite3.Connection,
+        assignee_user_id: str | None = None,
+        details: dict[str, Any] | None = None,
+        event_type: str | None = None,
+    ) -> dict[str, Any]:
+        actor_id = self._actor_id(actor)
+        task = self._require_task(connection, task_id)
+        live_actor = self._live_actor(connection, actor_id)
+        to_status = self.state_machine.transition(task, action, live_actor)
+        if assignee_user_id is None:
+            cursor = connection.execute(
+                """
+                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
+                WHERE id = ? AND status = ?
+                """,
+                (to_status, task_id, task["status"]),
+            )
+        else:
+            self._live_actor(connection, assignee_user_id)
+            cursor = connection.execute(
+                """
+                UPDATE workflow_tasks
+                SET status = ?, assignee_id = ?, assigned_by = ?,
+                    updated_at = CURRENT_TIMESTAMP
+                WHERE id = ? AND status = ?
+                """,
+                (to_status, assignee_user_id, actor_id, task_id, task["status"]),
+            )
+        if cursor.rowcount != 1:
+            raise ValueError("task state changed concurrently")
+        transitioned = self._require_task(connection, task_id)
+        event_details = dict(details or {})
+        event_details["task_before"] = task
+        event_details["task_after"] = transitioned
+        self._record_event(
+            connection,
+            task_id,
+            event_type or action,
+            actor_id,
+            task["status"],
+            to_status,
+            event_details,
+        )
+        return transitioned
+
+    def record_task_event_in_transaction(
+        self,
+        task_id: str,
+        event_type: str,
+        actor: Principal | dict[str, Any],
+        *,
+        connection: sqlite3.Connection,
+        details: dict[str, Any],
+    ) -> None:
+        actor_id = self._actor_id(actor)
+        self._live_actor(connection, actor_id)
+        task = self._require_task(connection, task_id)
+        event_details = dict(details)
+        event_details["task_before"] = task
+        event_details["task_after"] = task
+        self._record_event(
+            connection,
+            task_id,
+            event_type,
+            actor_id,
+            task["status"],
+            task["status"],
+            event_details,
+        )
+
     def list_tasks(
         self, actor: Principal | dict[str, Any]
     ) -> list[dict[str, Any]]:
         actor_id = self._actor_id(actor)
         with self.identity_repository.connect() as connection:
             live_actor = self._live_actor(connection, actor_id)
             if live_actor["is_system_admin"]:
                 rows = connection.execute(
                     "SELECT * FROM workflow_tasks ORDER BY created_at, id"
                 ).fetchall()
@@ -636,24 +725,36 @@ class WorkflowRepository:
             row["code"]
             for row in connection.execute(
                 """
                 SELECT r.code FROM user_roles AS ur
                 JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                 WHERE ur.user_id = ?
                 """,
                 (user_id,),
             )
         }
+        permissions = {
+            row["permission_code"]
+            for row in connection.execute(
+                """
+                SELECT rp.permission_code FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
+                JOIN role_permissions AS rp ON rp.role_id = r.id
+                WHERE ur.user_id = ?
+                """,
+                (user_id,),
+            )
+        }
         return {
             "user_id": user_id,
             "role_codes": roles,
-            "permissions": {"*"} if "system_admin" in roles else set(),
+            "permissions": {"*"} if "system_admin" in roles else permissions,
             "is_system_admin": "system_admin" in roles,
         }
 
     @staticmethod
     def _actor_id(actor: Principal | dict[str, Any]) -> str:
         if actor is None:
             raise ValueError("a non-null actor is required")
         if isinstance(actor, dict):
             actor_id = actor.get("user_id") or actor.get("id")
         else:
@@ -724,20 +825,28 @@ class WorkflowRepository:
             raise KeyError(task_id)
         return task
 
     @staticmethod
     def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
         task = dict(row)
         task["assignee_user_id"] = task.pop("assignee_id")
         task["assigned_by_user_id"] = task.pop("assigned_by")
         return task
 
+    @staticmethod
+    def _submission_dict(row: sqlite3.Row) -> dict[str, Any]:
+        submission = dict(row)
+        submission["content_snapshot"] = json.loads(
+            submission["content_snapshot_json"]
+        )
+        return submission
+
     @staticmethod
     def _record_event(
         connection: sqlite3.Connection,
         task_id: str,
         event_type: str,
         actor_id: str | None,
         from_status: str | None,
         to_status: str | None,
         details: dict[str, Any] | None = None,
     ) -> None:
diff --git a/workflow/schema.py b/workflow/schema.py
index dbabb8c..4987364 100644
--- a/workflow/schema.py
+++ b/workflow/schema.py
@@ -16,20 +16,27 @@ from pydantic import BaseModel, Field
 
 TaskStatus = Literal[
     "pool",
     "assigned",
     "in_progress",
     "waiting_review",
     "returned",
     "completed",
     "cancelled",
 ]
+SubmissionStatus = Literal[
+    "draft",
+    "submitted",
+    "approved",
+    "returned",
+    "superseded",
+]
 ProjectMemberStatus = Literal["active", "inactive"]
 
 
 class TaskAssignmentRequest(BaseModel):
     assignee_user_id: str = Field(min_length=1)
 
 
 class ProjectMemberCreateRequest(BaseModel):
     user_id: str = Field(min_length=1)
     project_role: str = Field(min_length=1, max_length=128)
