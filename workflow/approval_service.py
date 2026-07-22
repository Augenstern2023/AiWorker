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
import json
import sqlite3
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from identity.repository import IdentityRepository
from identity.schema import Principal

from .repository import WorkflowRepository


_DRAFT_TASK_STATUSES = frozenset({"assigned", "in_progress", "returned"})


class ApprovalService:
    def __init__(
        self,
        repository: WorkflowRepository | IdentityRepository | str | Path,
    ) -> None:
        self.repository = (
            repository
            if isinstance(repository, WorkflowRepository)
            else WorkflowRepository(repository)
        )

    def get_submission(
        self,
        submission_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        return self.repository.get_submission(submission_id, connection=connection)

    def create_draft(
        self,
        *,
        task_id: str,
        resource_type: str,
        resource_id: str,
        content: dict[str, Any] | list[Any],
        actor: Principal,
        parent_submission_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_resource(resource_type, resource_id)
        canonical = self._canonical_json(content)
        actor_id = self.repository._actor_id(actor)
        with self.repository.identity_repository.transaction() as connection:
            self.repository._live_actor(connection, actor_id)
            task = self.repository._require_task(connection, task_id)
            if task["assignee_user_id"] != actor_id:
                raise PermissionError("only the task assignee may create a draft")
            if parent_submission_id is not None:
                parent = self._require_submission(connection, parent_submission_id)
                if (parent["resource_type"], parent["resource_id"]) != (
                    resource_type,
                    resource_id,
                ):
                    raise ValueError("parent submission must belong to the same resource")
            if task["status"] not in _DRAFT_TASK_STATUSES:
                raise ValueError(
                    "task status must be assigned, in_progress, or returned"
                )
            draft = self._insert_draft(
                connection,
                task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
                canonical=canonical,
                created_by=actor_id,
                parent_submission_id=parent_submission_id,
            )
            self.repository.record_task_event_in_transaction(
                task_id,
                "submission.create_draft",
                actor,
                connection=connection,
                details={"before": None, "after": draft},
            )
        return draft

    def assert_editable(
        self, submission_id: str, actor: Principal
    ) -> dict[str, Any]:
        actor_id = self.repository._actor_id(actor)
        with self.repository.identity_repository.connect() as connection:
            self.repository._live_actor(connection, actor_id)
            submission = self._require_submission(connection, submission_id)
            task = self.repository._require_task(connection, submission["task_id"])
        if submission["status"] != "draft":
            raise ValueError("only a draft submission is editable")
        if task["assignee_user_id"] != actor_id:
            raise PermissionError("only the task assignee may edit this draft")
        return submission

    def submit(self, submission_id: str, actor: Principal) -> dict[str, Any]:
        actor_id = self.repository._actor_id(actor)
        with self.repository.identity_repository.transaction() as connection:
            before = self._require_submission(connection, submission_id)
            if before["status"] != "draft":
                raise ValueError("only a draft submission can be submitted")
            task = self.repository._require_task(connection, before["task_id"])
            self.repository._live_actor(connection, actor_id)
            if task["assignee_user_id"] != actor_id:
                raise PermissionError("only the task assignee may submit")
            canonical = self._canonical_json(before["content_snapshot"])
            now = time.time()
            cursor = connection.execute(
                """
                UPDATE workflow_submissions
                SET status = 'submitted', content_snapshot_json = ?,
                    content_fingerprint = ?, submitted_by = ?, submitted_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (
                    canonical,
                    self._fingerprint(canonical),
                    actor_id,
                    now,
                    now,
                    submission_id,
                ),
            )
            self._assert_updated(cursor)
            after = self._require_submission(connection, submission_id)
            self.repository.transition_task_in_transaction(
                before["task_id"],
                "submit",
                actor,
                connection=connection,
                details={"before": before, "after": after},
            )
        return after

    def approve(
        self,
        submission_id: str,
        actor: Principal,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        actor_id = self.repository._actor_id(actor)
        transaction = (
            nullcontext(connection)
            if connection is not None
            else self.repository.identity_repository.transaction()
        )
        with transaction as active_connection:
            before = self._require_submission(active_connection, submission_id)
            if before["status"] != "submitted":
                raise ValueError("only a submitted submission can be approved")
            live_actor = self.repository._live_actor(active_connection, actor_id)
            if before["submitted_by"] == actor_id and not live_actor["is_system_admin"]:
                raise PermissionError("a submitter cannot approve their own submission")
            task = self.repository._require_task(active_connection, before["task_id"])
            self._assert_reviewer(
                active_connection, task, live_actor, "version.approve"
            )
            newer_approved = active_connection.execute(
                """
                SELECT 1 FROM workflow_submissions
                WHERE resource_type = ? AND resource_id = ?
                  AND status = 'approved' AND version > ?
                LIMIT 1
                """,
                (
                    before["resource_type"],
                    before["resource_id"],
                    before["version"],
                ),
            ).fetchone()
            if newer_approved is not None:
                raise ValueError("a newer approved submission already exists")
            now = time.time()
            cursor = active_connection.execute(
                """
                UPDATE workflow_submissions
                SET status = 'approved', reviewed_by = ?, reviewed_at = ?,
                    review_comment = '', updated_at = ?
                WHERE id = ? AND status = 'submitted'
                """,
                (actor_id, now, now, submission_id),
            )
            self._assert_updated(cursor)
            after = self._require_submission(active_connection, submission_id)
            previous_rows = active_connection.execute(
                """
                SELECT * FROM workflow_submissions
                WHERE resource_type = ? AND resource_id = ? AND status = 'approved'
                  AND version < ?
                ORDER BY version
                """,
                (
                    before["resource_type"],
                    before["resource_id"],
                    before["version"],
                ),
            ).fetchall()
            for row in previous_rows:
                previous = self.repository._submission_dict(row)
                active_connection.execute(
                    """
                    UPDATE workflow_submissions
                    SET status = 'superseded', updated_at = ?
                    WHERE id = ? AND status = 'approved'
                    """,
                    (now, previous["id"]),
                )
                superseded = self._require_submission(
                    active_connection, previous["id"]
                )
                self.repository.record_task_event_in_transaction(
                    previous["task_id"],
                    "submission.supersede",
                    actor,
                    connection=active_connection,
                    details={"before": previous, "after": superseded},
                )
            self.repository.transition_task_in_transaction(
                before["task_id"],
                "complete",
                actor,
                connection=active_connection,
                details={"before": before, "after": after},
                event_type="approve",
                authorization_context={"can_manage_task": True},
            )
        return after

    def return_submission(
        self,
        submission_id: str,
        actor: Principal,
        review_comment: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        comment = review_comment.strip()
        if not comment:
            raise ValueError("a non-empty review comment is required")
        actor_id = self.repository._actor_id(actor)
        transaction = (
            nullcontext(connection)
            if connection is not None
            else self.repository.identity_repository.transaction()
        )
        with transaction as active_connection:
            before = self._require_submission(active_connection, submission_id)
            if before["status"] != "submitted":
                raise ValueError("only a submitted submission can be returned")
            live_actor = self.repository._live_actor(active_connection, actor_id)
            task = self.repository._require_task(active_connection, before["task_id"])
            self._assert_reviewer(active_connection, task, live_actor, "version.return")
            now = time.time()
            cursor = active_connection.execute(
                """
                UPDATE workflow_submissions
                SET status = 'returned', reviewed_by = ?, reviewed_at = ?,
                    review_comment = ?, updated_at = ?
                WHERE id = ? AND status = 'submitted'
                """,
                (actor_id, now, comment, now, submission_id),
            )
            self._assert_updated(cursor)
            returned = self._require_submission(active_connection, submission_id)
            draft = self._insert_draft(
                active_connection,
                task_id=before["task_id"],
                resource_type=before["resource_type"],
                resource_id=before["resource_id"],
                canonical=before["content_snapshot_json"],
                created_by=before["submitted_by"],
                parent_submission_id=before["id"],
            )
            self.repository.transition_task_in_transaction(
                before["task_id"],
                "return",
                actor,
                connection=active_connection,
                assignee_user_id=before["submitted_by"],
                details={
                    "before": before,
                    "after": returned,
                    "new_draft": draft,
                },
                authorization_context={"can_manage_task": True},
            )
        return returned, draft

    def fork(self, submission_id: str, actor: Principal) -> dict[str, Any]:
        actor_id = self.repository._actor_id(actor)
        with self.repository.identity_repository.transaction() as connection:
            source = self._require_submission(connection, submission_id)
            if source["status"] not in {"approved", "returned"}:
                raise ValueError("only an approved or returned submission can be forked")
            self.repository._live_actor(connection, actor_id)
            draft = self._insert_draft(
                connection,
                task_id=source["task_id"],
                resource_type=source["resource_type"],
                resource_id=source["resource_id"],
                canonical=source["content_snapshot_json"],
                created_by=actor_id,
                parent_submission_id=source["id"],
            )
            self.repository.record_task_event_in_transaction(
                source["task_id"],
                "submission.fork",
                actor,
                connection=connection,
                details={"before": source, "after": draft},
            )
        return draft

    def _insert_draft(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        resource_type: str,
        resource_id: str,
        canonical: str,
        created_by: str,
        parent_submission_id: str | None,
    ) -> dict[str, Any]:
        version = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM workflow_submissions
            WHERE resource_type = ? AND resource_id = ?
            """,
            (resource_type, resource_id),
        ).fetchone()[0]
        now = time.time()
        submission_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO workflow_submissions (
                id, task_id, resource_type, resource_id, version,
                parent_submission_id, status, content_snapshot_json,
                content_fingerprint, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                task_id,
                resource_type,
                resource_id,
                version,
                parent_submission_id,
                canonical,
                self._fingerprint(canonical),
                created_by,
                now,
                now,
            ),
        )
        return self._require_submission(connection, submission_id)

    def _assert_reviewer(
        self,
        connection: sqlite3.Connection,
        task: dict[str, Any],
        actor: dict[str, Any],
        permission: str,
    ) -> None:
        if actor["is_system_admin"]:
            return
        if permission not in actor["permissions"]:
            raise PermissionError(f"{permission} permission is required")
        if actor["permission_scopes"].get(permission) not in {
            "managed_projects",
            "all",
        }:
            raise PermissionError(
                f"{permission} requires managed_projects permission scope"
            )
        project_id = task.get("project_id")
        if project_id is None:
            raise PermissionError("reviewer must manage the task project")
        self.repository._assert_managed_project_membership(
            connection, project_id, actor
        )

    def _require_submission(
        self, connection: sqlite3.Connection, submission_id: str
    ) -> dict[str, Any]:
        submission = self.repository.get_submission(
            submission_id, connection=connection
        )
        if submission is None:
            raise KeyError(submission_id)
        return submission

    @staticmethod
    def _validate_resource(resource_type: str, resource_id: str) -> None:
        if not resource_type.strip() or not resource_id.strip():
            raise ValueError("resource type and id are required")

    @staticmethod
    def _canonical_json(content: dict[str, Any] | list[Any]) -> str:
        if not isinstance(content, (dict, list)):
            raise TypeError("submission content must be a dict or list")
        return json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _fingerprint(canonical: str) -> str:
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _assert_updated(cursor: sqlite3.Cursor) -> None:
        if cursor.rowcount != 1:
            raise ValueError("submission state changed concurrently")
