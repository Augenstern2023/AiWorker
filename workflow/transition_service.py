# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import sqlite3
import time

from pathlib import Path
from typing import TYPE_CHECKING, Any

from identity.repository import IdentityRepository
from identity.schema import Principal

from .repository import SYSTEM_ACTOR, WorkflowRepository

if TYPE_CHECKING:
    from creative.workflow_adapter import CreativeWorkflowAdapter


WORKFLOW_NODES = {
    "brief": ("creative_planner", "project_owner", "characters"),
    "characters": ("creative_planner", "project_owner", "outline"),
    "outline": ("screenwriter", "project_owner", "beats"),
    "beats": ("screenwriter", "project_owner", "delivery"),
    "delivery": ("system_admin", None, None),
}

class TransitionService:
    def __init__(
        self,
        repository: WorkflowRepository | IdentityRepository | str | Path,
        creative_adapter: CreativeWorkflowAdapter | None = None,
    ) -> None:
        self.repository = (
            repository
            if isinstance(repository, WorkflowRepository)
            else WorkflowRepository(repository)
        )
        self.creative_adapter = creative_adapter or self._default_creative_adapter()

    def complete_creative_without_review(
        self,
        task_id: str,
        resource_type: str,
        resource_id: str,
        actor: Principal,
        *,
        difference_note: str = "",
    ) -> dict[str, Any]:
        """Finish a creative task by locking its version instead of submitting it."""
        if self.creative_adapter is None:
            raise RuntimeError("creative workflow adapter is not configured")
        if resource_type not in {"brief", "characters", "outline", "beats"}:
            raise ValueError("resource type requires its configured workflow")

        completed = self.creative_adapter.lock_without_review(
            task_id,
            resource_type,
            resource_id,
            actor,
            difference_note=difference_note,
        )
        with self.repository.identity_repository.transaction() as connection:
            task = self.repository.transition_task_in_transaction(
                task_id,
                "complete_work",
                actor,
                connection=connection,
                details={
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "completion": "locked_without_review",
                },
                event_type="creative.lock",
            )
            next_task = None
            beats_progress = None
            if resource_type == "beats":
                beats_progress = self.creative_adapter.beats_episode_progress(resource_id)
                if not beats_progress["complete"]:
                    connection.execute(
                        "UPDATE workflow_tasks SET status = 'in_progress', "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'completed'",
                        (task_id,),
                    )
                    self.repository._record_event(
                        connection,
                        task_id,
                        "beats.remaining",
                        actor.user_id,
                        "completed",
                        "in_progress",
                        beats_progress,
                    )
                    task = self.repository._require_task(connection, task_id)
            else:
                node = WORKFLOW_NODES[resource_type]
                next_node = node[2]
                if next_node is not None:
                    next_task = self.repository.create_next_task(
                        task_id,
                        next_node,
                        WORKFLOW_NODES[next_node][0],
                        actor=SYSTEM_ACTOR,
                        connection=connection,
                    )
            if resource_type == "beats" and beats_progress and beats_progress["complete"]:
                node = WORKFLOW_NODES[resource_type]
                next_node = node[2]
                if next_node is not None:
                    next_task = self.repository.create_next_task(
                        task_id,
                        next_node,
                        WORKFLOW_NODES[next_node][0],
                        actor=SYSTEM_ACTOR,
                        connection=connection,
                    )
        result = {"resource": completed["resource"], "task": task, "next_task": next_task}
        if beats_progress is not None:
            result["beats_progress"] = beats_progress
        return result

    def migrate_pending_creative_approvals(self) -> list[str]:
        """Lock legacy pending creative versions and remove their review wait state."""
        if self.creative_adapter is None:
            return []
        resource_types = ("brief", "characters", "outline", "beats")
        with self.repository.identity_repository.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_submissions WHERE status = 'submitted' "
                f"AND resource_type IN ({','.join('?' for _ in resource_types)}) "
                "ORDER BY submitted_at, created_at, id",
                resource_types,
            ).fetchall()
        migrated: list[str] = []
        for row in rows:
            submission = self.repository._submission_dict(row)
            try:
                self.creative_adapter.lock_submitted_without_review(
                    submission["resource_type"], submission["resource_id"]
                )
                self._bypass_pending_submission(submission)
            except (KeyError, ValueError, PermissionError):
                continue
            migrated.append(submission["id"])
        return migrated

    def _bypass_pending_submission(self, submission: dict[str, Any]) -> None:
        resource_type = submission["resource_type"]
        with self.repository.identity_repository.transaction() as connection:
            current = self.repository.get_submission(
                submission["id"], connection=connection
            )
            if current is None or current["status"] != "submitted":
                return
            task = self.repository._require_task(connection, current["task_id"])
            now = time.time()
            connection.execute(
                "UPDATE workflow_submissions SET status = 'superseded', updated_at = ? "
                "WHERE id = ? AND status = 'submitted'",
                (now, current["id"]),
            )
            self.repository._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
            self.repository._record_event(
                connection,
                task["id"],
                "creative.legacy_review_removed",
                SYSTEM_ACTOR.user_id,
                task["status"],
                "completed",
                {
                    "submission_id": current["id"],
                    "resource_type": resource_type,
                    "resource_id": current["resource_id"],
                    "submission_before": current,
                    "submission_after": {**current, "status": "superseded"},
                },
            )
            if task["status"] not in {"in_progress", "completed"}:
                raise ValueError("legacy task is not ready for migration")
            now = time.time()
            if resource_type == "beats":
                beats_progress = self.creative_adapter.beats_episode_progress(
                    current["resource_id"]
                )
                if not beats_progress["complete"]:
                    connection.execute(
                        "UPDATE workflow_tasks SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND status = 'completed'",
                        (task["id"],),
                    )
                    self.repository._record_event(
                        connection,
                        task["id"],
                        "beats.remaining",
                        SYSTEM_ACTOR.user_id,
                        "completed",
                        "in_progress",
                        beats_progress,
                    )
            if resource_type != "beats" or (beats_progress and beats_progress["complete"]):
                next_node = WORKFLOW_NODES[resource_type][2]
                if next_node is not None:
                    self.repository.create_next_task(
                        task["id"],
                        next_node,
                        WORKFLOW_NODES[next_node][0],
                        actor=SYSTEM_ACTOR,
                        connection=connection,
                    )


    def _default_creative_adapter(self) -> CreativeWorkflowAdapter | None:
        system_path = self.repository.identity_repository.db_path
        if system_path.parent.name != "system":
            return None
        creative_path = system_path.parent.parent / "creative" / "creative.db"
        from creative.repository import CreativeRepository
        from creative.workflow_adapter import CreativeWorkflowAdapter

        return CreativeWorkflowAdapter(
            CreativeRepository(creative_path), self.repository
        )

    def reconcile_task(self, task_id: str) -> None:
        # Legacy records are migrated at router startup; active tasks no longer
        # have a review state to reconcile.
        return None
