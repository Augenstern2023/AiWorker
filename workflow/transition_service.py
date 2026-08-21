# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from identity.repository import IdentityRepository
from identity.schema import Principal
from system_database.schema import workflow_submissions, workflow_tasks

from .repository import SYSTEM_ACTOR, WorkflowRepository

if TYPE_CHECKING:
    from creative.workflow_adapter import CreativeWorkflowAdapter


WORKFLOW_NODES = {
    "brief": ("creative_planner", "project_owner", "characters"),
    "characters": ("creative_planner", "project_owner", "outline"),
    "outline": ("screenwriter", "project_owner", "beats"),
    "beats": ("screenwriter", "project_owner", "script"),
    "script": ("screenwriter", "project_owner", "delivery"),
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
        if resource_type not in {"brief", "characters", "outline", "beats", "script"}:
            raise ValueError("resource type requires its configured workflow")

        try:
            completed = self.creative_adapter.lock_without_review(
                task_id,
                resource_type,
                resource_id,
                actor,
                difference_note=difference_note,
            )
        except ValueError as exc:
            # A prior request may have locked the resource before a later
            # workflow write failed. Resume only that specific partial state.
            if (
                str(exc) != "creative resource is immutable after completion"
                or self.creative_adapter.domain_status(resource_type, resource_id)
                not in {"locked", "brief_locked"}
            ):
                raise
            completed = {"resource": {"id": resource_id, "status": "locked"}}
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
            if resource_type in {"beats", "script"}:
                beats_progress = (
                    self.creative_adapter.beats_episode_progress(resource_id)
                    if resource_type == "beats"
                    else self.creative_adapter.script_episode_progress(resource_id)
                )
                if not beats_progress["complete"]:
                    connection.execute(
                        update(workflow_tasks)
                        .where(
                            workflow_tasks.c.id == task_id,
                            workflow_tasks.c.status == "completed",
                        )
                        .values(status="in_progress")
                    )
                    self.repository._record_event(
                        connection,
                        task_id,
                        f"{resource_type}.remaining",
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
            if resource_type in {"beats", "script"} and beats_progress and beats_progress["complete"]:
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

    def reconcile_creative_without_review(
        self, task_id: str, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        """Recover the result when domain and workflow completion already committed."""
        if self.creative_adapter is None:
            raise RuntimeError("creative workflow adapter is not configured")
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] != "completed":
            return None
        if self.creative_adapter.domain_status(resource_type, resource_id) not in {
            "locked",
            "brief_locked",
        }:
            return None
        resource = self.creative_adapter.locked_resource(resource_type, resource_id)
        with self.repository.identity_repository.connect() as connection:
            successor = connection.exec_driver_sql(
                "SELECT * FROM workflow_tasks WHERE predecessor_task_id = %s "
                "ORDER BY created_at, id LIMIT 1",
                (task_id,),
            ).mappings().fetchone()
        result: dict[str, Any] = {
            "resource": resource,
            "task": task,
            "next_task": (
                self.repository._task_dict(successor)
                if successor is not None
                else None
            ),
        }
        if resource_type in {"beats", "script"}:
            result["beats_progress"] = (
                self.creative_adapter.beats_episode_progress(resource_id)
                if resource_type == "beats"
                else self.creative_adapter.script_episode_progress(resource_id)
            )
        return result

    def complete_creative_beats_batch_without_review(
        self, task_id: str, project_id: int, actor: Principal
    ) -> dict[str, Any]:
        """Lock all validated current Beats drafts, then complete the shared workflow task once."""
        if self.creative_adapter is None:
            raise RuntimeError("creative workflow adapter is not configured")
        completed = self.creative_adapter.lock_beats_batch_without_review(
            task_id, project_id, actor
        )
        resources = completed["resources"]
        with self.repository.identity_repository.transaction() as connection:
            task = self.repository.transition_task_in_transaction(
                task_id,
                "complete_work",
                actor,
                connection=connection,
                details={
                    "resource_type": "beats",
                    "resource_ids": [str(item["id"]) for item in resources],
                    "completion": "batch_locked_without_review",
                },
                event_type="creative.lock.batch",
            )
            beats_progress = self.creative_adapter.beats_episode_progress(str(resources[0]["id"]))
            if not beats_progress["complete"]:
                connection.execute(
                    update(workflow_tasks)
                    .where(workflow_tasks.c.id == task_id, workflow_tasks.c.status == "completed")
                    .values(status="in_progress")
                )
                self.repository._record_event(
                    connection, task_id, "beats.remaining", actor.user_id,
                    "completed", "in_progress", beats_progress,
                )
                task = self.repository._require_task(connection, task_id)
                next_task = None
            else:
                node = WORKFLOW_NODES["beats"]
                next_task = self.repository.create_next_task(
                    task_id, node[2], WORKFLOW_NODES[node[2]][0],
                    actor=SYSTEM_ACTOR, connection=connection,
                )
        return {
            "resources": resources,
            "task": task,
            "next_task": next_task,
            "beats_progress": beats_progress,
        }

    def migrate_pending_creative_approvals(self) -> list[str]:
        """Lock legacy pending creative versions and remove their review wait state."""
        if self.creative_adapter is None:
            return []
        resource_types = ("brief", "characters", "outline", "beats", "script")
        with self.repository.identity_repository.connect() as connection:
            rows = connection.execute(
                select(workflow_submissions)
                .where(
                    workflow_submissions.c.status == "submitted",
                    workflow_submissions.c.resource_type.in_(resource_types),
                )
                .order_by(
                    workflow_submissions.c.submitted_at,
                    workflow_submissions.c.created_at,
                    workflow_submissions.c.id,
                )
            ).mappings().all()
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
                update(workflow_submissions)
                .where(
                    workflow_submissions.c.id == current["id"],
                    workflow_submissions.c.status == "submitted",
                )
                .values(status="superseded", updated_at=now)
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
            if resource_type in {"beats", "script"}:
                beats_progress = (
                    self.creative_adapter.beats_episode_progress(current["resource_id"])
                    if resource_type == "beats"
                    else self.creative_adapter.script_episode_progress(current["resource_id"])
                )
                if not beats_progress["complete"]:
                    connection.execute(
                        update(workflow_tasks)
                        .where(
                            workflow_tasks.c.id == task["id"],
                            workflow_tasks.c.status == "completed",
                        )
                        .values(status="in_progress")
                    )
                    self.repository._record_event(
                        connection,
                        task["id"],
                        f"{resource_type}.remaining",
                        SYSTEM_ACTOR.user_id,
                        "completed",
                        "in_progress",
                        beats_progress,
                    )
            if resource_type not in {"beats", "script"} or (beats_progress and beats_progress["complete"]):
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
        system_path = getattr(self.repository.identity_repository, "db_path", None)
        if system_path is None or system_path.parent.name != "system":
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
