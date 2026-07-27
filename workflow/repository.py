# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from identity.repository import IdentityRepository
from identity.schema import DATA_SCOPE_ORDER, Principal

from .state_machine import WorkflowStateMachine


OWNER_PROJECT_ROLES = frozenset({"owner", "project_owner"})
_MANAGED_PROJECT_ROLES = OWNER_PROJECT_ROLES | frozenset(
    {"manager", "project_manager"}
)
SYSTEM_ACTOR = Principal(
    user_id="workflow:system",
    username="workflow-system",
    display_name="Workflow System",
    role_codes=("system_admin",),
    permissions=frozenset({"*"}),
    data_scope="all",
    session_id="workflow:system",
)

_PROJECT_MEMBER_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS workflow_project_member_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workflow_project_member_events_project
ON workflow_project_member_events(project_id, created_at);
"""


class WorkflowRepository:
    def __init__(
        self,
        repository: IdentityRepository | str | Path,
        state_machine: WorkflowStateMachine | None = None,
    ) -> None:
        self.identity_repository = (
            repository
            if isinstance(repository, IdentityRepository)
            else IdentityRepository(repository)
        )
        self.state_machine = state_machine or WorkflowStateMachine()
        if not self.identity_repository.is_postgres:
            with self.identity_repository.connect() as connection:
                connection.executescript(_PROJECT_MEMBER_EVENTS_SQL)

    def create_task(
        self,
        workflow_id: str,
        task_type: str,
        role_code: str,
        *,
        project_id: str | None = None,
        topic_id: int | None = None,
        due_at: str | None = None,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._ensure_system_actor(connection, actor_id)
            role = connection.execute(
                "SELECT 1 FROM roles WHERE code = ? COLLATE NOCASE AND is_enabled = 1",
                (role_code,),
            ).fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            self._ensure_workflow(
                connection,
                workflow_id,
                task_type,
                project_id,
                topic_id,
                actor_id,
            )
            connection.execute(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pool', ?)
                """,
                (
                    task_id,
                    workflow_id,
                    task_type,
                    role_code,
                    topic_id,
                    project_id,
                    due_at,
                ),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {"role_code": role_code},
            )
            task = self._get_task(connection, task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.identity_repository.connect() as connection:
            return self._get_task(connection, task_id)

    def ensure_topic_task(
        self,
        topic_id: int,
        *,
        task_type: str,
        role_code: str,
        actor: Principal | dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        workflow_id = f"topic:{topic_id}"
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = ? AND task_type = ? AND topic_id = ?
                ORDER BY created_at, id LIMIT 1
                """,
                (workflow_id, task_type, topic_id),
            ).fetchone()
            if existing is not None:
                if project_id is not None and existing["project_id"] != project_id:
                    connection.execute(
                        "UPDATE workflow_instances SET project_id = ?, resource_type = 'project', "
                        "resource_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (project_id, project_id, workflow_id),
                    )
                    connection.execute(
                        "UPDATE workflow_tasks SET project_id = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE workflow_id = ? AND project_id IS NULL",
                        (project_id, workflow_id),
                    )
                    existing = connection.execute(
                        "SELECT * FROM workflow_tasks WHERE id = ?", (existing["id"],)
                    ).fetchone()
                return self._task_dict(existing)
            self._ensure_workflow(
                connection, workflow_id, task_type, project_id, topic_id, actor_id
            )
            task_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pool', NULL)
                """,
                (task_id, workflow_id, task_type, role_code, topic_id, project_id),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {"role_code": role_code, "bootstrap": True},
            )
            return self._require_task(connection, task_id)

    def ensure_project_task(
        self,
        project_id: str,
        topic_id: int,
        task_type: str = "brief",
        role_code: str = "creative_planner",
        *,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        workflow_id = f"project:{project_id}"
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._ensure_system_actor(connection, actor_id)
            existing = connection.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = ? AND task_type = ? AND project_id = ?
                ORDER BY created_at, id LIMIT 1
                """,
                (workflow_id, task_type, project_id),
            ).fetchone()
            if existing is not None:
                return self._task_dict(existing)
            self._ensure_workflow(
                connection,
                workflow_id,
                task_type,
                project_id,
                topic_id,
                actor_id,
            )
            role = connection.execute(
                "SELECT 1 FROM roles WHERE code = ? COLLATE NOCASE "
                "AND is_enabled = 1 AND is_deleted = 0",
                (role_code,),
            ).fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            task_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'pool', NULL)
                """,
                (
                    task_id,
                    workflow_id,
                    task_type,
                    role_code,
                    topic_id,
                    project_id,
                ),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {"role_code": role_code, "project_bootstrap": True},
            )
            return self._require_task(connection, task_id)

    def link_task_job(self, task_id: str, job_id: str) -> None:
        with self.identity_repository.transaction() as connection:
            self._require_task(connection, task_id)
            connection.execute(
                "INSERT OR IGNORE INTO workflow_task_jobs (task_id, job_id) VALUES (?, ?)",
                (task_id, job_id),
            )

    def bind_workflow_project(
        self,
        task_id: str,
        project_id: str,
        creator: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        creator_id = self._actor_id(creator)
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            if task["project_id"] not in {None, project_id}:
                raise ValueError("workflow task is already linked to another project")
            self._live_actor(connection, creator_id)
            self._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
            connection.execute(
                "UPDATE workflow_instances SET project_id = ?, resource_type = 'project', "
                "resource_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id, project_id, task["workflow_id"]),
            )
            connection.execute(
                "UPDATE workflow_tasks SET project_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE workflow_id = ? AND project_id IS NULL",
                (project_id, task["workflow_id"]),
            )
            members = {creator_id: task["role_code"]}
            reviewers = connection.execute(
                """
                SELECT DISTINCT s.reviewed_by
                FROM workflow_submissions s
                JOIN workflow_tasks t ON t.id = s.task_id
                WHERE t.workflow_id = ? AND s.reviewed_by IS NOT NULL
                """,
                (task["workflow_id"],),
            ).fetchall()
            for reviewer in reviewers:
                members.setdefault(str(reviewer["reviewed_by"]), "project_owner")
            for user_id, project_role in members.items():
                existing = connection.execute(
                    "SELECT * FROM project_members WHERE project_id = ? "
                    "AND user_id = ? AND project_role = ?",
                    (project_id, user_id, project_role),
                ).fetchone()
                if existing is not None:
                    continue
                membership_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO project_members
                        (id, project_id, user_id, project_role, status, added_by)
                    VALUES (?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        membership_id,
                        project_id,
                        user_id,
                        project_role,
                        SYSTEM_ACTOR.user_id,
                    ),
                )
                member = connection.execute(
                    "SELECT * FROM project_members WHERE id = ?",
                    (membership_id,),
                ).fetchone()
                assert member is not None
                self._record_project_member_event(
                    connection,
                    project_id,
                    membership_id,
                    "project_member.add",
                    SYSTEM_ACTOR.user_id,
                    None,
                    dict(member),
                )
            return self._require_task(connection, task_id)

    def list_task_job_ids(self, task_id: str) -> list[str]:
        with self.identity_repository.connect() as connection:
            self._require_task(connection, task_id)
            rows = connection.execute(
                "SELECT job_id FROM workflow_task_jobs "
                "WHERE task_id = ? ORDER BY created_at, job_id",
                (task_id,),
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def get_submission(
        self,
        submission_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self.identity_repository.connection_scope(connection) as active_connection:
            row = active_connection.execute(
                "SELECT * FROM workflow_submissions WHERE id = ?",
                (submission_id,),
            ).fetchone()
        return self._submission_dict(row) if row is not None else None

    def list_task_submissions(self, task_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            self._require_task(connection, task_id)
            rows = connection.execute(
                """
                SELECT * FROM workflow_submissions
                WHERE task_id = ? ORDER BY version, created_at, id
                """,
                (task_id,),
            ).fetchall()
        return [self._submission_dict(row) for row in rows]

    def get_latest_resource_submission(
        self,
        resource_type: str,
        resource_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with self.identity_repository.connection_scope(connection) as active_connection:
            row = active_connection.execute(
                """
                SELECT * FROM workflow_submissions
                WHERE resource_type = ? AND resource_id = ?
                ORDER BY version DESC, created_at DESC, id DESC LIMIT 1
                """,
                (resource_type, resource_id),
            ).fetchone()
        return self._submission_dict(row) if row is not None else None

    def list_approved_submissions(
        self, resource_type: str
    ) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_submissions
                WHERE resource_type = ? AND status = 'approved'
                ORDER BY reviewed_at DESC, id DESC
                """,
                (resource_type,),
            ).fetchall()
        return [self._submission_dict(row) for row in rows]

    def update_submission_resource_id(
        self,
        submission_id: str,
        resource_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        with self.identity_repository.connection_scope(connection) as active_connection:
            cursor = active_connection.execute(
                "UPDATE workflow_submissions SET resource_id = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'draft'",
                (resource_id, submission_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only a draft registry entry may change resource")
            updated = self.get_submission(submission_id, connection=active_connection)
        assert updated is not None
        return updated

    def record_mirror_error(
        self, task_id: str, submission: dict[str, Any], message: str
    ) -> None:
        with self.identity_repository.transaction() as connection:
            self._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
            self._record_event(
                connection,
                task_id,
                "creative.mirror_error",
                SYSTEM_ACTOR.user_id,
                None,
                None,
                {"submission_id": submission["id"], "error": message},
            )

    def record_legacy_force(
        self,
        task_id: str,
        actor: Principal,
        resource_type: str,
        resource_id: str,
    ) -> None:
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            self._record_event(
                connection,
                task_id,
                "creative.legacy_force_lock",
                actor.user_id,
                task["status"],
                task["status"],
                {"resource_type": resource_type, "resource_id": resource_id},
            )

    def supersede_resource_approvals(
        self,
        resource_type: str,
        resource_id: str,
        actor: Principal,
    ) -> None:
        with self.identity_repository.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_submissions WHERE resource_type = ? "
                "AND resource_id = ? AND status = 'approved'",
                (resource_type, resource_id),
            ).fetchall()
            for row in rows:
                before = self._submission_dict(row)
                connection.execute(
                    "UPDATE workflow_submissions SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (time.time(), before["id"]),
                )
                after = self.get_submission(before["id"], connection=connection)
                self._record_event(
                    connection,
                    before["task_id"],
                    "submission.supersede",
                    actor.user_id,
                    "approved",
                    "superseded",
                    {"before": before, "after": after},
                )

    def create_next_task(
        self,
        predecessor_task_id: str,
        task_type: str,
        role_code: str,
        *,
        actor: Principal | dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        transaction = (
            nullcontext(connection)
            if connection is not None
            else self.identity_repository.transaction()
        )
        with transaction as active_connection:
            predecessor = self._require_task(
                active_connection, predecessor_task_id
            )
            existing = active_connection.execute(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = ? AND task_type = ?
                  AND predecessor_task_id = ?
                """,
                (
                    predecessor["workflow_id"],
                    task_type,
                    predecessor_task_id,
                ),
            ).fetchone()
            if existing is not None:
                return self._task_dict(existing)
            self._ensure_system_actor(active_connection, actor_id)
            role = active_connection.execute(
                "SELECT 1 FROM roles WHERE code = ? COLLATE NOCASE AND is_enabled = 1",
                (role_code,),
            ).fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            task_id = str(uuid.uuid4())
            active_connection.execute(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, predecessor_task_id, status, due_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, 'pool', NULL)
                """,
                (
                    task_id,
                    predecessor["workflow_id"],
                    task_type,
                    role_code,
                    predecessor["topic_id"],
                    predecessor["project_id"],
                    predecessor_task_id,
                ),
            )
            self._record_event(
                active_connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {
                    "role_code": role_code,
                    "predecessor_task_id": predecessor_task_id,
                },
            )
            task = self._require_task(active_connection, task_id)
        return task

    def transition_task_in_transaction(
        self,
        task_id: str,
        action: str,
        actor: Principal | dict[str, Any],
        *,
        connection: sqlite3.Connection,
        assignee_user_id: str | None = None,
        details: dict[str, Any] | None = None,
        event_type: str | None = None,
        authorization_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        task = self._require_task(connection, task_id)
        live_actor = self._live_actor(connection, actor_id)
        transition_actor = dict(live_actor)
        transition_actor.update(authorization_context or {})
        to_status = self.state_machine.transition(task, action, transition_actor)
        if assignee_user_id is None:
            cursor = connection.execute(
                """
                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (to_status, task_id, task["status"]),
            )
        else:
            self._live_actor(connection, assignee_user_id)
            cursor = connection.execute(
                """
                UPDATE workflow_tasks
                SET status = ?, assignee_id = ?, assigned_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (to_status, assignee_user_id, actor_id, task_id, task["status"]),
            )
        if cursor.rowcount != 1:
            raise ValueError("task state changed concurrently")
        transitioned = self._require_task(connection, task_id)
        event_details = dict(details or {})
        event_details["task_before"] = task
        event_details["task_after"] = transitioned
        self._record_event(
            connection,
            task_id,
            event_type or action,
            actor_id,
            task["status"],
            to_status,
            event_details,
        )
        return transitioned

    def record_task_event_in_transaction(
        self,
        task_id: str,
        event_type: str,
        actor: Principal | dict[str, Any],
        *,
        connection: sqlite3.Connection,
        details: dict[str, Any],
    ) -> None:
        actor_id = self._actor_id(actor)
        self._live_actor(connection, actor_id)
        task = self._require_task(connection, task_id)
        event_details = dict(details)
        event_details["task_before"] = task
        event_details["task_after"] = task
        self._record_event(
            connection,
            task_id,
            event_type,
            actor_id,
            task["status"],
            task["status"],
            event_details,
        )

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
            else:
                role_codes = live_actor["role_codes"]
                managed_projects = self._managed_project_ids(connection, actor_id)
                assigned_projects = self._assigned_project_ids(connection, actor_id)
                rows = connection.execute(
                    "SELECT * FROM workflow_tasks ORDER BY created_at, id"
                ).fetchall()
                rows = [
                    row
                    for row in rows
                    if row["assignee_id"] == actor_id
                    or (
                        row["status"] == "pool"
                        and (
                            row["role_code"] in role_codes
                            or row["task_type"] in live_actor["claimable_task_types"]
                        )
                        and (
                            row["project_id"] is None
                            or (
                                row["role_code"] == "project_owner"
                                and row["project_id"] in managed_projects
                            )
                            or row["project_id"] in assigned_projects
                        )
                    )
                    or (
                        row["project_id"] is not None
                        and row["project_id"] in managed_projects
                    )
                ]
        return [self._task_dict(row) for row in rows]

    def get_visible_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any] | None:
        return next(
            (task for task in self.list_tasks(actor) if task["id"] == task_id), None
        )

    def list_my_submissions(
        self, actor: Principal | dict[str, Any]
    ) -> list[dict[str, Any]]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, t.task_type, t.project_id, t.role_code
                FROM workflow_submissions s
                JOIN workflow_tasks t ON s.task_id = t.id
                WHERE s.created_by = ?
                ORDER BY s.created_at DESC
                """,
                (actor_id,),
            ).fetchall()
        return [self._submission_dict(row) for row in rows]

    def can_claim_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                task = self._require_task(connection, task_id)
                live_actor = self._live_actor(connection, actor_id)
            except (KeyError, PermissionError):
                return False
            if task["status"] != "pool":
                return False
            if live_actor["is_system_admin"]:
                return True
            if (
                task["role_code"] not in live_actor["role_codes"]
                and task["task_type"] not in live_actor["claimable_task_types"]
            ):
                return False
            return task["project_id"] is None or self._is_active_project_member(
                connection, task["project_id"], actor_id
            )

    def can_assign_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                task = self._require_task(connection, task_id)
                live_actor = self._live_actor(connection, actor_id)
                self._assert_task_manager(connection, task, live_actor)
            except (KeyError, PermissionError):
                return False
        return task["status"] == "pool"

    def claim_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            if (
                task["role_code"] == "project_owner"
                and not live_actor["is_system_admin"]
            ):
                self._assert_task_manager(connection, task, live_actor)
            elif (
                task["project_id"] is not None
                and not live_actor["is_system_admin"]
                and not self._is_active_project_member(
                    connection, task["project_id"], actor_id
                )
            ):
                raise PermissionError("actor must be an active project member")
            to_status = self.state_machine.transition(task, "claim", live_actor)
            cursor = connection.execute(
                """
                UPDATE workflow_tasks
                SET assignee_id = ?, assigned_by = ?, status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pool' AND assignee_id IS NULL
                """,
                (actor_id, actor_id, to_status, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("task has already been claimed")
            self._record_event(
                connection,
                task_id,
                "claim",
                actor_id,
                task["status"],
                to_status,
                {"assignee_user_id": actor_id},
            )
            claimed = self._require_task(connection, task_id)
        return claimed

    def assign_task(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        return self._assign(task_id, assignee_user_id, actor, "assign")

    def reassign_task(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        return self._assign(task_id, assignee_user_id, actor, "reassign")

    def start_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            to_status = self.state_machine.transition(task, "start", live_actor)
            connection.execute(
                """
                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (to_status, task_id, task["status"]),
            )
            self._record_event(
                connection,
                task_id,
                "start",
                actor_id,
                task["status"],
                to_status,
            )
            started = self._require_task(connection, task_id)
        return started

    def cancel_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            self._assert_task_manager(connection, task, live_actor)
            to_status = self.state_machine.transition(task, "cancel", live_actor)
            connection.execute(
                """
                UPDATE workflow_tasks SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = ?
                """,
                (to_status, task_id, task["status"]),
            )
            self._record_event(
                connection,
                task_id,
                "cancel",
                actor_id,
                task["status"],
                to_status,
            )
            cancelled = self._require_task(connection, task_id)
        return cancelled

    def list_role_assignment_candidates(
        self,
        role_code: str,
        task_type: str,
        actor: Principal | dict[str, Any],
    ) -> list[dict[str, Any]]:
        normalized_role = role_code.strip()
        normalized_task = task_type.strip()
        if not normalized_role or not normalized_task:
            raise ValueError("role_code and task_type are required")
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            if not live_actor["is_system_admin"] and "workflow.assign" not in live_actor["permissions"]:
                raise PermissionError("workflow.assign permission is required")
            rows = connection.execute(
                """
                SELECT DISTINCT u.id, u.username, u.display_name
                FROM users AS u
                JOIN user_roles AS ur ON ur.user_id = u.id
                JOIN roles AS r ON r.id = ur.role_id
                LEFT JOIN role_claimable_task_types AS rct ON rct.role_id = r.id
                WHERE u.status = 'active' AND u.is_deleted = 0
                  AND r.is_enabled = 1
                  AND (r.code = ? OR rct.task_type = ?)
                ORDER BY u.display_name COLLATE NOCASE, u.username COLLATE NOCASE, u.id
                """,
                (normalized_role, normalized_task),
            ).fetchall()
            candidates = []
            for row in rows:
                candidate = self._live_actor(connection, str(row["id"]))
                if (
                    not candidate["is_system_admin"]
                    and normalized_role not in candidate["role_codes"]
                    and normalized_task not in candidate["claimable_task_types"]
                ):
                    continue
                candidates.append(
                    {
                        "user_id": str(row["id"]),
                        "username": str(row["username"]),
                        "display_name": str(row["display_name"]),
                        "role_codes": sorted(candidate["role_codes"]),
                        "claimable_task_types": sorted(candidate["claimable_task_types"]),
                    }
                )
        return candidates

    def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_task_events
                WHERE task_id = ? ORDER BY created_at, rowid
                """,
                (task_id,),
            ).fetchall()
        return [self._event_dict(row) for row in rows]

    def add_project_member(
        self,
        project_id: str,
        user_id: str,
        project_role: str,
        actor: Principal | dict[str, Any],
        *,
        status: str = "active",
    ) -> dict[str, Any]:
        if status not in {"active", "inactive"}:
            raise ValueError("project member status must be active or inactive")
        if not project_role.strip():
            raise ValueError("project role is required")
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            live_actor = self._live_actor(connection, actor_id)
            has_members = connection.execute(
                "SELECT 1 FROM project_members WHERE project_id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            is_owner_bootstrap = (
                has_members is None
                and actor_id == user_id
                and project_role in OWNER_PROJECT_ROLES
                and "project_owner" in live_actor["role_codes"]
            )
            if not is_owner_bootstrap:
                self._assert_project_manager(connection, project_id, live_actor)
            user = connection.execute(
                """
                SELECT 1 FROM users
                WHERE id = ? AND status = 'active' AND is_deleted = 0
                """,
                (user_id,),
            ).fetchone()
            if user is None:
                raise ValueError("project member user does not exist or is inactive")
            existing = connection.execute(
                """
                SELECT * FROM project_members
                WHERE project_id = ? AND user_id = ? AND project_role = ?
                """,
                (project_id, user_id, project_role),
            ).fetchone()
            membership_id = existing["id"] if existing else str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO project_members
                    (id, project_id, user_id, project_role, status, added_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, user_id, project_role) DO UPDATE SET
                    status = excluded.status,
                    added_by = excluded.added_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    membership_id,
                    project_id,
                    user_id,
                    project_role,
                    status,
                    actor_id,
                ),
            )
            member = connection.execute(
                "SELECT * FROM project_members WHERE id = ?", (membership_id,)
            ).fetchone()
            assert member is not None
            self._record_project_member_event(
                connection,
                project_id,
                membership_id,
                "project_member.update" if existing else "project_member.add",
                actor_id,
                dict(existing) if existing else None,
                dict(member),
            )
        return dict(member)

    def list_project_members(self, project_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM project_members
                WHERE project_id = ? ORDER BY created_at, rowid
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_project_member_events(self, project_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_project_member_events
                WHERE project_id = ? ORDER BY created_at, rowid
                """,
                (project_id,),
            ).fetchall()
        return [self._project_member_event_dict(row) for row in rows]

    def assert_can_manage_project(
        self, project_id: str, actor: Principal | dict[str, Any]
    ) -> None:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            self._assert_project_manager(connection, project_id, live_actor)

    def can_review_task(
        self,
        task_id: str,
        actor: Principal | dict[str, Any],
        permission: str,
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                live_actor = self._live_actor(connection, actor_id)
                task = self._require_task(connection, task_id)
                if live_actor["is_system_admin"]:
                    return True
                if permission not in live_actor["permissions"]:
                    return False
                if live_actor["permission_scopes"].get(permission) not in {
                    "managed_projects",
                    "all",
                }:
                    return False
                project_id = task.get("project_id")
                if project_id is None:
                    return bool(
                        task.get("topic_id") is not None
                        and "project.manage" in live_actor["permissions"]
                    )
                self._assert_managed_project_membership(
                    connection, project_id, live_actor
                )
            except (KeyError, PermissionError):
                return False
        return True

    def remove_project_member(
        self,
        project_id: str,
        membership_id: str,
        actor: Principal | dict[str, Any],
    ) -> None:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            live_actor = self._live_actor(connection, actor_id)
            self._assert_project_manager(connection, project_id, live_actor)
            members = connection.execute(
                """
                SELECT * FROM project_members
                WHERE project_id = ? AND user_id = ? ORDER BY rowid
                """,
                (project_id, membership_id),
            ).fetchall()
            if not members:
                member = connection.execute(
                    """
                    SELECT * FROM project_members WHERE project_id = ? AND id = ?
                    """,
                    (project_id, membership_id),
                ).fetchone()
                members = [member] if member is not None else []
            if not members:
                raise KeyError(membership_id)
            removed_user_id = members[0]["user_id"]
            removes_active_owner = any(
                member["status"] == "active"
                and member["project_role"] in OWNER_PROJECT_ROLES
                for member in members
            )
            if removes_active_owner:
                other_roles = connection.execute(
                    """
                    SELECT project_role FROM project_members
                    WHERE project_id = ? AND user_id <> ? AND status = 'active'
                    """,
                    (project_id, removed_user_id),
                ).fetchall()
                if not any(
                    row["project_role"] in OWNER_PROJECT_ROLES
                    for row in other_roles
                ):
                    raise ValueError(
                        "current project owner cannot be removed until another owner is assigned"
                    )
            for member in members:
                connection.execute(
                    "DELETE FROM project_members WHERE id = ?", (member["id"],)
                )
                self._record_project_member_event(
                    connection,
                    project_id,
                    member["id"],
                    "project_member.remove",
                    actor_id,
                    dict(member),
                    None,
                )

    def _assign(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._require_task(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            self._assert_task_manager(connection, task, live_actor)
            to_status = self.state_machine.transition(task, action, live_actor)
            assignee = self._live_actor(connection, assignee_user_id)
            if (
                not assignee["is_system_admin"]
                and task["role_code"] not in assignee["role_codes"]
                and task["task_type"] not in assignee["claimable_task_types"]
            ):
                raise ValueError("assignee is not live-eligible for this task type")
            if task["project_id"] is not None:
                membership = connection.execute(
                    """
                    SELECT 1 FROM project_members
                    WHERE project_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (task["project_id"], assignee_user_id),
                ).fetchone()
                if membership is None:
                    raise ValueError("assignee must be an active project member")
            if action == "assign":
                cursor = connection.execute(
                    """
                    UPDATE workflow_tasks
                    SET assignee_id = ?, assigned_by = ?, status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pool' AND assignee_id IS NULL
                    """,
                    (assignee_user_id, actor_id, to_status, task_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE workflow_tasks
                    SET assignee_id = ?, assigned_by = ?, status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = ?
                    """,
                    (
                        assignee_user_id,
                        actor_id,
                        to_status,
                        task_id,
                        task["status"],
                    ),
                )
            if cursor.rowcount != 1:
                raise ValueError("task assignment changed concurrently")
            self._record_event(
                connection,
                task_id,
                action,
                actor_id,
                task["status"],
                to_status,
                {
                    "before_assignee_user_id": task["assignee_user_id"],
                    "assignee_user_id": assignee_user_id,
                },
            )
            assigned = self._require_task(connection, task_id)
        return assigned

    def _assert_task_manager(
        self,
        connection: sqlite3.Connection,
        task: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"]:
            return
        project_id = task.get("project_id")
        if project_id is None:
            raise PermissionError("only a system administrator may manage unscoped tasks")
        self._assert_project_manager(connection, project_id, actor)

    @staticmethod
    def _assert_project_manager(
        connection: sqlite3.Connection,
        project_id: str,
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"]:
            return
        if "project_owner" not in actor["role_codes"]:
            raise PermissionError("actor is not a project owner")
        WorkflowRepository._assert_managed_project_membership(
            connection, project_id, actor
        )

    @staticmethod
    def _assert_managed_project_membership(
        connection: sqlite3.Connection,
        project_id: str,
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"]:
            return
        member = connection.execute(
            """
            SELECT 1 FROM project_members
            WHERE project_id = ? AND user_id = ? AND status = 'active'
              AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
            """,
            (project_id, actor["user_id"]),
        ).fetchone()
        if member is None:
            raise PermissionError("actor does not manage this project")

    @staticmethod
    def _managed_project_ids(
        connection: sqlite3.Connection, actor_id: str
    ) -> set[str]:
        return {
            row["project_id"]
            for row in connection.execute(
                """
                SELECT DISTINCT project_id FROM project_members
                WHERE user_id = ? AND status = 'active'
                  AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
                """,
                (actor_id,),
            )
        }

    @staticmethod
    def _assigned_project_ids(
        connection: sqlite3.Connection, actor_id: str
    ) -> set[str]:
        return {
            row["project_id"]
            for row in connection.execute(
                """
                SELECT DISTINCT project_id FROM project_members
                WHERE user_id = ? AND status = 'active'
                """,
                (actor_id,),
            )
        }

    @staticmethod
    def _is_active_project_member(
        connection: sqlite3.Connection, project_id: str, actor_id: str
    ) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM project_members
                WHERE project_id = ? AND user_id = ? AND status = 'active'
                LIMIT 1
                """,
                (project_id, actor_id),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _live_actor(connection: sqlite3.Connection, user_id: str) -> dict[str, Any]:
        user = connection.execute(
            """
            SELECT id FROM users
            WHERE id = ? AND status = 'active' AND is_deleted = 0
            """,
            (user_id,),
        ).fetchone()
        if user is None:
            raise PermissionError("actor or assignee is inactive or unknown")
        roles = {
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
        permission_scopes: dict[str, str] = {}
        permission_rows = connection.execute(
            """
            SELECT rp.permission_code, rp.data_scope FROM user_roles AS ur
            JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
            JOIN role_permissions AS rp ON rp.role_id = r.id
            WHERE ur.user_id = ?
            """,
            (user_id,),
        )
        for row in permission_rows:
            current_scope = permission_scopes.get(row["permission_code"])
            if current_scope is None or (
                DATA_SCOPE_ORDER[row["data_scope"]]
                > DATA_SCOPE_ORDER[current_scope]
            ):
                permission_scopes[row["permission_code"]] = row["data_scope"]
        if "system_admin" in roles:
            permission_scopes = {"*": "all"}
        claimable_task_types = {
            row["task_type"]
            for row in connection.execute(
                """
                SELECT rct.task_type FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                JOIN role_claimable_task_types AS rct ON rct.role_id = r.id
                WHERE ur.user_id = ?
                """,
                (user_id,),
            )
        }
        return {
            "user_id": user_id,
            "role_codes": roles,
            "permissions": set(permission_scopes),
            "permission_scopes": permission_scopes,
            "is_system_admin": "system_admin" in roles,
            "claimable_task_types": claimable_task_types,
        }

    @staticmethod
    def _actor_id(actor: Principal | dict[str, Any]) -> str:
        if actor is None:
            raise ValueError("a non-null actor is required")
        if isinstance(actor, dict):
            actor_id = actor.get("user_id") or actor.get("id")
        else:
            actor_id = actor.user_id
        if not actor_id:
            raise PermissionError("an authenticated actor is required")
        return str(actor_id)

    @staticmethod
    def _ensure_system_actor(
        connection: sqlite3.Connection, actor_id: str
    ) -> None:
        if actor_id != SYSTEM_ACTOR.user_id:
            return
        connection.execute(
            """
            INSERT INTO users
                (id, username, normalized_username, display_name, password_hash,
                 status, is_deleted)
            VALUES (?, ?, ?, ?, ?, 'disabled', 1)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                SYSTEM_ACTOR.user_id,
                SYSTEM_ACTOR.username,
                SYSTEM_ACTOR.username,
                SYSTEM_ACTOR.display_name,
                "system-managed-account",
            ),
        )

    @staticmethod
    def _ensure_workflow(
        connection: sqlite3.Connection,
        workflow_id: str,
        task_type: str,
        project_id: str | None,
        topic_id: int | None,
        actor_id: str | None,
    ) -> None:
        resource_type = "project" if project_id else "topic" if topic_id is not None else "task"
        resource_id = project_id or (str(topic_id) if topic_id is not None else workflow_id)
        connection.execute(
            """
            INSERT INTO workflow_instances
                (id, workflow_type, resource_type, resource_id, project_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (workflow_id, task_type, resource_type, resource_id, project_id, actor_id),
        )

    @classmethod
    def _get_task(
        cls, connection: sqlite3.Connection, task_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM workflow_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return cls._task_dict(row) if row is not None else None

    @classmethod
    def _require_task(
        cls, connection: sqlite3.Connection, task_id: str
    ) -> dict[str, Any]:
        task = cls._get_task(connection, task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["assignee_user_id"] = task.pop("assignee_id")
        task["assigned_by_user_id"] = task.pop("assigned_by")
        return task

    @staticmethod
    def _submission_dict(row: sqlite3.Row) -> dict[str, Any]:
        submission = dict(row)
        submission["content_snapshot"] = json.loads(
            submission["content_snapshot_json"]
        )
        return submission

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
        connection.execute(
            """
            INSERT INTO workflow_task_events
                (id, task_id, event_type, actor_id, from_status, to_status, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                actor_id,
                from_status,
                to_status,
                json.dumps(details or {}, ensure_ascii=True, sort_keys=True),
            ),
        )

    @staticmethod
    def _record_project_member_event(
        connection: sqlite3.Connection,
        project_id: str,
        membership_id: str,
        event_type: str,
        actor_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_project_member_events
                (id, project_id, membership_id, event_type, actor_id,
                 before_json, after_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                membership_id,
                event_type,
                actor_id,
                json.dumps(before, ensure_ascii=True, sort_keys=True)
                if before is not None
                else None,
                json.dumps(after, ensure_ascii=True, sort_keys=True)
                if after is not None
                else None,
            ),
        )

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event["actor_user_id"] = event.pop("actor_id")
        event["before_status"] = event.pop("from_status")
        event["after_status"] = event.pop("to_status")
        event["details"] = json.loads(event.pop("details_json"))
        return event

    @staticmethod
    def _project_member_event_dict(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event["actor_user_id"] = event.pop("actor_id")
        before_json = event.pop("before_json")
        after_json = event.pop("after_json")
        event["before"] = json.loads(before_json) if before_json else None
        event["after"] = json.loads(after_json) if after_json else None
        return event
