# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from identity.schema import Principal
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository

from .repository import CreativeRepository


RESOURCE_TYPES = frozenset({"breakdown", "brief", "characters", "outline", "beats", "script"})
APPROVAL_FREE_RESOURCE_TYPES = frozenset({"brief", "characters", "outline", "beats", "script"})
IMMUTABLE_WORKFLOW_STATUSES = frozenset(
    {"submitted", "approved", "returned", "superseded"}
)


class CreativeWorkflowAdapter:
    """Bridges creative versions to the workflow registry in the system database."""

    def __init__(
        self,
        creative_repository: CreativeRepository | str | Path,
        workflow_repository: WorkflowRepository,
    ) -> None:
        self.creative_repository = (
            creative_repository
            if isinstance(creative_repository, CreativeRepository)
            else CreativeRepository(creative_repository)
        )
        self.workflow_repository = workflow_repository

    def snapshot(
        self, resource_type: str, resource_id: str, *, reconcile: bool = True
    ) -> dict | list:
        self._validate_type(resource_type)
        if reconcile:
            self._retry_mirror(resource_type, resource_id)
        if resource_type == "breakdown":
            item = self._required(self.creative_repository.get_breakdown(int(resource_id)))
            return self._snapshot_item(
                item, ("id", "topic_id", "version", "parent_version_id", "content", "field_sources", "risks")
            )
        if resource_type == "brief":
            item = self._required(self.creative_repository.get_blueprint(int(resource_id)))
            project = self._required(
                self.creative_repository.get_project(int(item["project_id"]))
            )
            content = dict(item["content"])
            content.update(
                {
                    "project_name": project["name"],
                    "genre": project["genre"],
                    "target_platform": project["target_platform"],
                    "target_audience": content.get("audience")
                    or project["target_audience"],
                    "target_episode_count": project["target_episode_count"],
                    "episode_duration_seconds": project[
                        "episode_duration_seconds"
                    ],
                }
            )
            snapshot = self._snapshot_item(
                item,
                (
                    "id", "project_id", "candidate_group_id", "version",
                    "parent_version_id", "content", "risk_score", "risk_level",
                    "difference_note", "source_refs", "generation_meta",
                ),
            )
            snapshot["content"] = content
            return snapshot
        if resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            batch = self.creative_repository.get_character_batch(project_id, batch_id)
            if not batch["characters"]:
                raise KeyError(resource_id)
            return {
                "project_id": project_id,
                "batch_id": batch_id,
                "characters": [self._creative_item_snapshot(item) for item in batch["characters"]],
                "relationships": [self._creative_item_snapshot(item) for item in batch["relationships"]],
            }
        if resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            batch = self.creative_repository.get_outline_batch(project_id, batch_id)
            if not batch["episodes"]:
                raise KeyError(resource_id)
            return {
                "project_id": project_id,
                "batch_id": batch_id,
                "episodes": [self._episode_snapshot(item) for item in batch["episodes"]],
            }
        item = self._required(self.creative_repository.get_episode(int(resource_id)))
        if item["stage"] != resource_type:
            raise ValueError(f"{resource_type} resource must reference a matching episode")
        return self._episode_snapshot(item)

    def submit(
        self,
        task_id: str,
        resource_type: str,
        resource_id: str,
        actor: Principal,
    ) -> dict:
        task = self.workflow_repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["task_type"] != resource_type:
            raise ValueError("workflow node does not match resource type")
        if task["assignee_user_id"] != actor.user_id and not actor.is_system_admin:
            raise PermissionError("only the task assignee may submit")
        draft = self._find_draft(task_id, resource_type, resource_id)
        if draft is None:
            draft = self.create_draft(task_id, resource_type, resource_id, actor)
        return self.submit_draft(draft["id"], actor)

    def create_draft(
        self,
        task_id: str,
        resource_type: str,
        resource_id: str,
        actor: Principal,
    ) -> dict:
        snapshot = self.snapshot(resource_type, resource_id)
        self.assert_editable(resource_type, resource_id)
        self._assert_task(task_id, resource_type, resource_id, actor)
        self._assert_current_upstream(resource_type, resource_id, snapshot)
        self._assert_brief_risk(resource_type, resource_id)
        from workflow.approval_service import ApprovalService

        return ApprovalService(self.workflow_repository).create_draft(
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
            content=snapshot,
            actor=actor,
        )

    def submit_draft(self, submission_id: str, actor: Principal) -> dict:
        submission = self.workflow_repository.get_submission(submission_id)
        if submission is None:
            raise KeyError(submission_id)
        resource_type = submission["resource_type"]
        resource_id = submission["resource_id"]
        snapshot = self.snapshot(resource_type, resource_id)
        self.assert_editable(resource_type, resource_id)
        task = self._assert_task(
            submission["task_id"], resource_type, resource_id, actor
        )
        self._assert_current_upstream(resource_type, resource_id, snapshot)
        self._assert_brief_risk(resource_type, resource_id)
        from workflow.approval_service import ApprovalService

        submitted = ApprovalService(self.workflow_repository).submit(
            submission_id, actor, content_snapshot=snapshot
        )
        try:
            with self.creative_repository._connect() as connection:
                self.set_domain_status(
                    resource_type,
                    resource_id,
                    "submitted",
                    conn=connection,
                    workflow_task_id=task["id"],
                )
        except Exception as exc:
            self._record_mirror_error(submitted, exc)
        return submitted

    def assert_approvable(self, submission: dict) -> None:
        if submission["resource_type"] not in RESOURCE_TYPES:
            return
        snapshot = self.snapshot(
            submission["resource_type"], submission["resource_id"], reconcile=False
        )
        self._assert_current_upstream(
            submission["resource_type"], submission["resource_id"], snapshot
        )

    def on_approved(
        self,
        submission: dict,
        actor: Principal,
        conn: sqlite3.Connection | None = None,
    ) -> dict:
        del conn
        requested_submission = submission
        authoritative = self._authoritative_approved_submission(
            submission["resource_type"], submission["resource_id"]
        )
        if authoritative is not None:
            submission = authoritative
        resource_type = submission["resource_type"]
        resource_id = submission["resource_id"]
        self._validate_type(resource_type)
        self.assert_approvable(submission)
        now = time.time()
        superseded_resources: list[str] = []
        with self.creative_repository._connect() as connection:
            self.set_domain_status(resource_type, resource_id, "approved", conn=connection)
            if resource_type == "breakdown":
                item = self._required(
                    self.creative_repository.get_breakdown(int(resource_id))
                )
                old_rows = connection.execute(
                    "SELECT id FROM creative_breakdowns WHERE topic_id = ? AND id <> ? AND status = 'approved'",
                    (item["topic_id"], int(resource_id)),
                ).fetchall()
                superseded_resources.extend(str(row["id"]) for row in old_rows)
                connection.execute(
                    "UPDATE creative_breakdowns SET status = 'superseded', updated_at = ? "
                    "WHERE topic_id = ? AND id <> ? AND status = 'approved'",
                    (now, item["topic_id"], int(resource_id)),
                )
            elif resource_type == "brief":
                item = self._required(self.creative_repository.get_blueprint(int(resource_id)))
                content = dict(submission["content_snapshot"]["content"])
                connection.execute(
                    "UPDATE creative_blueprints SET content_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), now, int(resource_id)),
                )
                project = connection.execute(
                    "SELECT locked_brief_id FROM creative_projects WHERE id = ?",
                    (item["project_id"],),
                ).fetchone()
                old_id = project["locked_brief_id"] if project else None
                switching = not old_id or int(old_id) != int(resource_id)
                if old_id and switching:
                    connection.execute(
                        "UPDATE creative_blueprints SET status = 'superseded', updated_at = ? WHERE id = ? AND status = 'approved'",
                        (now, old_id),
                    )
                    superseded_resources.append(str(old_id))
                if switching:
                    connection.execute(
                        "UPDATE creative_projects SET locked_brief_id = ?, character_batch_id = NULL, "
                        "outline_batch_id = NULL, status = 'brief_approved', updated_at = ? WHERE id = ?",
                        (int(resource_id), now, item["project_id"]),
                    )
            elif resource_type == "characters":
                project_id, batch_id = self._batch_key(resource_id)
                project = connection.execute(
                    "SELECT character_batch_id FROM creative_projects WHERE id = ?", (project_id,)
                ).fetchone()
                old_batch = project["character_batch_id"] if project else None
                switching = not old_batch or old_batch != batch_id
                if old_batch and switching:
                    self._set_batch_status(connection, "characters", project_id, old_batch, "superseded")
                    superseded_resources.append(f"{project_id}:{old_batch}")
                if switching:
                    connection.execute(
                        "UPDATE creative_projects SET character_batch_id = ?, outline_batch_id = NULL, "
                        "status = 'characters_approved', updated_at = ? WHERE id = ?",
                        (batch_id, now, project_id),
                    )
            elif resource_type == "outline":
                project_id, batch_id = self._batch_key(resource_id)
                project = connection.execute(
                    "SELECT outline_batch_id FROM creative_projects WHERE id = ?", (project_id,)
                ).fetchone()
                old_batch = project["outline_batch_id"] if project else None
                switching = not old_batch or old_batch != batch_id
                if old_batch and switching:
                    self._set_batch_status(connection, "outline", project_id, old_batch, "superseded")
                    superseded_resources.append(f"{project_id}:{old_batch}")
                if switching:
                    connection.execute(
                        "UPDATE creative_projects SET outline_batch_id = ?, status = 'outline_approved', "
                        "updated_at = ? WHERE id = ?",
                        (batch_id, now, project_id),
                    )
            elif resource_type == "beats":
                episode = self._required(self.creative_repository.get_episode(int(resource_id)))
                old_rows = connection.execute(
                    "SELECT id FROM creative_episodes WHERE project_id = ? AND stage = 'beats' "
                    "AND episode_number = ? AND id <> ? AND status = 'approved'",
                    (episode["project_id"], episode["episode_number"], int(resource_id)),
                ).fetchall()
                superseded_resources.extend(str(row["id"]) for row in old_rows)
                connection.execute(
                    "UPDATE creative_episodes SET status = 'superseded', updated_at = ? "
                    "WHERE project_id = ? AND stage = 'beats' AND episode_number = ? "
                    "AND id <> ? AND status = 'approved'",
                    (now, episode["project_id"], episode["episode_number"], int(resource_id)),
                )
            if resource_type == "breakdown":
                rows = connection.execute(
                    "SELECT id FROM creative_breakdowns WHERE topic_id = ? AND status = 'superseded'",
                    (item["topic_id"],),
                ).fetchall()
                superseded_resources.extend(str(row["id"]) for row in rows)
            elif resource_type == "brief":
                rows = connection.execute(
                    "SELECT id FROM creative_blueprints WHERE project_id = ? AND status = 'superseded'",
                    (item["project_id"],),
                ).fetchall()
                superseded_resources.extend(str(row["id"]) for row in rows)
            elif resource_type == "characters":
                rows = connection.execute(
                    "SELECT DISTINCT batch_id FROM creative_characters WHERE project_id = ? AND status = 'superseded'",
                    (project_id,),
                ).fetchall()
                superseded_resources.extend(
                    f"{project_id}:{row['batch_id']}" for row in rows
                )
            elif resource_type == "outline":
                rows = connection.execute(
                    "SELECT DISTINCT batch_id FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND status = 'superseded'",
                    (project_id,),
                ).fetchall()
                superseded_resources.extend(
                    f"{project_id}:{row['batch_id']}" for row in rows
                )
            elif resource_type == "beats":
                rows = connection.execute(
                    "SELECT id FROM creative_episodes WHERE project_id = ? AND stage = 'beats' "
                    "AND episode_number = ? AND status = 'superseded'",
                    (episode["project_id"], episode["episode_number"]),
                ).fetchall()
                superseded_resources.extend(str(row["id"]) for row in rows)
        for old_resource_id in dict.fromkeys(superseded_resources):
            self.workflow_repository.supersede_resource_approvals(
                resource_type, old_resource_id, actor
            )
        if requested_submission["id"] != submission["id"]:
            self.workflow_repository.supersede_resource_approvals(
                requested_submission["resource_type"],
                requested_submission["resource_id"],
                actor,
            )
        return submission

    def on_returned(
        self,
        submission: dict,
        new_draft: dict,
        actor: Principal,
        conn: sqlite3.Connection | None = None,
    ) -> dict:
        del actor
        resource_type = submission["resource_type"]
        old_id = submission["resource_id"]
        task_id = submission["task_id"]
        with self.creative_repository._connect() as connection:
            self.set_domain_status(resource_type, old_id, "returned", conn=connection)
            new_id = self._clone(resource_type, old_id, task_id, connection)
        self.workflow_repository.update_submission_resource_id(
            new_draft["id"], new_id, connection=conn
        )
        updated = self.workflow_repository.get_submission(new_draft["id"], connection=conn)
        assert updated is not None
        return updated

    def assert_editable(self, resource_type: str, resource_id: str) -> None:
        self._validate_type(resource_type)
        latest = self.workflow_repository.get_latest_resource_submission(
            resource_type, resource_id
        )
        if latest and latest["status"] in IMMUTABLE_WORKFLOW_STATUSES:
            raise ValueError("creative resource is immutable after submission")
        if self.domain_status(resource_type, resource_id) != "draft":
            raise ValueError("creative resource is immutable after completion")

    def lock_without_review(
        self,
        task_id: str,
        resource_type: str,
        resource_id: str,
        actor: Principal,
        *,
        difference_note: str = "",
    ) -> dict:
        """Lock an assigned creative version without creating a submission."""
        if resource_type not in APPROVAL_FREE_RESOURCE_TYPES:
            raise ValueError("resource type requires its configured workflow")
        self._validate_type(resource_type)
        self._assert_task(task_id, resource_type, resource_id, actor)
        self.assert_editable(resource_type, resource_id)
        snapshot = self.snapshot(resource_type, resource_id)
        self._assert_current_upstream(resource_type, resource_id, snapshot)
        self._assert_brief_risk(resource_type, resource_id)

        if resource_type == "brief":
            resource = self.creative_repository.lock_brief(
                int(resource_id), difference_note
            )
        elif resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.lock_character_batch(project_id, batch_id)
        elif resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.lock_outline_batch(project_id, batch_id)
        elif resource_type == "beats":
            resource = self.creative_repository.lock_episode_beats(int(resource_id))
        else:
            resource = self.creative_repository.lock_episode_script(int(resource_id))
        return {"resource": resource, "snapshot": snapshot}

    def lock_beats_batch_without_review(
        self, task_id: str, project_id: int, actor: Principal
    ) -> dict[str, Any]:
        """Validate every current Beats draft before one repository-level batch lock."""
        drafts = self.creative_repository.batch_lockable_episode_beats(project_id)
        snapshots: list[dict[str, Any]] = []
        for draft in drafts:
            resource_id = str(draft["id"])
            self._assert_task(task_id, "beats", resource_id, actor)
            self.assert_editable("beats", resource_id)
            snapshot = self.snapshot("beats", resource_id)
            self._assert_current_upstream("beats", resource_id, snapshot)
            snapshots.append(snapshot)
        resources = self.creative_repository.lock_current_episode_beats_batch(project_id)
        return {"resources": resources, "snapshots": snapshots}

    def lock_submitted_without_review(
        self, resource_type: str, resource_id: str
    ) -> dict:
        """Convert a legacy submitted version into the same locked domain state."""
        if resource_type not in APPROVAL_FREE_RESOURCE_TYPES:
            raise ValueError("resource type requires its configured workflow")
        if resource_type == "brief":
            return self.creative_repository.lock_brief(int(resource_id))
        if resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            return self.creative_repository.lock_character_batch(project_id, batch_id)
        if resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            return self.creative_repository.lock_outline_batch(project_id, batch_id)
        return self.creative_repository.lock_episode_beats(int(resource_id)) if resource_type == "beats" else self.creative_repository.lock_episode_script(int(resource_id))

    def assert_task_for_creation(
        self,
        task_id: str,
        resource_type: str,
        actor: Principal,
        *,
        project_id: int | None = None,
        topic_id: int | None = None,
    ) -> dict:
        self._validate_type(resource_type)
        task = self.workflow_repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["task_type"] != resource_type:
            raise ValueError("workflow node does not match resource type")
        if task["assignee_user_id"] != actor.user_id and not actor.is_system_admin:
            raise PermissionError("only the task assignee may create this resource")
        if project_id is not None and str(task["project_id"] or "") != str(project_id):
            raise ValueError("resource does not belong to the task project")
        if topic_id is not None and task["topic_id"] != topic_id:
            raise ValueError("resource does not belong to the task topic")
        if task["status"] not in {"assigned", "in_progress", "returned"}:
            raise ValueError("workflow task is not open for creative work")
        return task

    def reconcile_resource(self, resource_type: str, resource_id: str) -> None:
        submission = self.workflow_repository.get_latest_resource_submission(
            resource_type, resource_id
        )
        if submission is None:
            return
        try:
            if submission["status"] == "approved":
                authoritative = self._authoritative_approved_submission(
                    resource_type, resource_id
                )
                if authoritative is None:
                    return
                self.on_approved(authoritative, SYSTEM_ACTOR)
            elif submission["status"] in {
                "submitted",
                "returned",
                "superseded",
            }:
                if self.domain_status(resource_type, resource_id) != submission["status"]:
                    with self.creative_repository._connect() as connection:
                        self.set_domain_status(
                            resource_type,
                            resource_id,
                            submission["status"],
                            conn=connection,
                        )
        except Exception as exc:
            self._record_mirror_error(submission, exc)

    def _authoritative_approved_submission(
        self, resource_type: str, resource_id: str
    ) -> dict | None:
        scope = self._logical_scope(resource_type, resource_id)
        if scope is None:
            return None
        candidates = [
            submission
            for submission in self.workflow_repository.list_approved_submissions(
                resource_type
            )
            if self._logical_scope(resource_type, submission["resource_id"])
            == scope
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda submission: (
                float(submission.get("reviewed_at") or 0),
                str(submission["id"]),
            ),
        )

    def _logical_scope(
        self, resource_type: str, resource_id: str
    ) -> tuple[Any, ...] | None:
        try:
            if resource_type == "breakdown":
                item = self.creative_repository.get_breakdown(int(resource_id))
                return (resource_type, int(item["topic_id"])) if item else None
            if resource_type == "brief":
                item = self.creative_repository.get_blueprint(int(resource_id))
                return (resource_type, int(item["project_id"])) if item else None
            if resource_type == "characters":
                project_id, batch_id = self._batch_key(resource_id)
                batch = self.creative_repository.get_character_batch(
                    project_id, batch_id
                )
                return (
                    (resource_type, project_id)
                    if batch["characters"]
                    else None
                )
            if resource_type == "outline":
                project_id, batch_id = self._batch_key(resource_id)
                batch = self.creative_repository.get_outline_batch(
                    project_id, batch_id
                )
                return (
                    (resource_type, project_id) if batch["episodes"] else None
                )
            item = self.creative_repository.get_episode(int(resource_id))
            if not item or item["stage"] not in {"beats", "script"}:
                return None
            return (
                resource_type,
                int(item["project_id"]),
                int(item["episode_number"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def reconcile_submission(self, submission: dict) -> None:
        if submission.get("resource_type") == "topic_decision":
            if submission.get("status") == "approved":
                self.on_topic_decision_approved(submission)
            return
        if submission.get("resource_type") in RESOURCE_TYPES:
            self.reconcile_resource(
                submission["resource_type"], submission["resource_id"]
            )

    def on_topic_decision_approved(self, submission: dict) -> dict:
        topic_id = int(submission["resource_id"])
        topic = self._required(self.creative_repository.get_topic(topic_id))
        if topic["status"] != "adopted":
            topic = self._required(
                self.creative_repository.update_topic(topic_id, "adopted")
            )
        return topic

    def reconcile_project(self, project_id: int) -> None:
        project = self.creative_repository.get_project(project_id)
        if not project:
            return
        resources = {
            ("breakdown", str(project["breakdown_id"])),
            *(
                ("brief", str(item["id"]))
                for item in project.get("blueprints", [])
            ),
            *(
                ("characters", f"{project_id}:{item['batch_id']}")
                for item in project.get("characters", [])
            ),
            *(
                (
                    "outline" if item["stage"] == "outline" else item["stage"],
                    f"{project_id}:{item['batch_id']}"
                    if item["stage"] == "outline"
                    else str(item["id"]),
                )
                for item in project.get("episodes", [])
                if item["stage"] in {"outline", "beats", "script"}
            ),
        }
        for resource_type, resource_id in resources:
            self.reconcile_resource(resource_type, resource_id)

    def audit_legacy_force(
        self,
        resource_type: str,
        resource_id: str,
        actor: Principal,
    ) -> None:
        with self.creative_repository._connect() as connection:
            if resource_type == "breakdown":
                row = connection.execute(
                    "SELECT workflow_task_id FROM creative_breakdowns WHERE id = ?",
                    (int(resource_id),),
                ).fetchone()
            elif resource_type == "brief":
                row = connection.execute(
                    "SELECT workflow_task_id FROM creative_blueprints WHERE id = ?",
                    (int(resource_id),),
                ).fetchone()
            elif resource_type in {"characters", "outline"}:
                project_id, batch_id = self._batch_key(resource_id)
                table = "creative_characters" if resource_type == "characters" else "creative_episodes"
                stage = "" if resource_type == "characters" else " AND stage = 'outline'"
                row = connection.execute(
                    f"SELECT workflow_task_id FROM {table} WHERE project_id = ? AND batch_id = ?{stage} LIMIT 1",
                    (project_id, batch_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT workflow_task_id FROM creative_episodes WHERE id = ? AND stage = ?",
                    (int(resource_id), resource_type),
                ).fetchone()
        task_id = str(row["workflow_task_id"] or "") if row else ""
        if not task_id:
            raise ValueError("强制操作需要关联工作任务以记录审计")
        self.workflow_repository.record_legacy_force(
            task_id, actor, resource_type, resource_id
        )

    def domain_status(self, resource_type: str, resource_id: str) -> str:
        self._validate_type(resource_type)
        with self.creative_repository._connect() as connection:
            if resource_type == "breakdown":
                row = connection.execute(
                    "SELECT status FROM creative_breakdowns WHERE id = ?", (int(resource_id),)
                ).fetchone()
            elif resource_type == "brief":
                row = connection.execute(
                    "SELECT status FROM creative_blueprints WHERE id = ?", (int(resource_id),)
                ).fetchone()
            elif resource_type == "characters":
                project_id, batch_id = self._batch_key(resource_id)
                row = connection.execute(
                    "SELECT status FROM creative_characters WHERE project_id = ? AND batch_id = ? LIMIT 1",
                    (project_id, batch_id),
                ).fetchone()
            elif resource_type == "outline":
                project_id, batch_id = self._batch_key(resource_id)
                row = connection.execute(
                    "SELECT status FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND batch_id = ? LIMIT 1",
                    (project_id, batch_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT status FROM creative_episodes WHERE id = ? AND stage = ?",
                    (int(resource_id), resource_type),
                ).fetchone()
        if row is None:
            raise KeyError(resource_id)
        return str(row["status"])

    def beats_episode_progress(self, resource_id: str) -> dict[str, Any]:
        """Return current-outline coverage, treating this completion as accepted."""
        episode = self._required(
            self.creative_repository.get_episode(int(resource_id))
        )
        if episode["stage"] != "beats":
            raise ValueError("beats resource must reference a beats episode")
        project = self._required(
            self.creative_repository.get_project(int(episode["project_id"]))
        )
        outline_batch_id = project.get("outline_batch_id")
        required = {
            int(item["episode_number"])
            for item in project.get("episodes", [])
            if item["stage"] == "outline" and item["batch_id"] == outline_batch_id
        }
        approved = {
            int(item["episode_number"])
            for item in project.get("episodes", [])
            if item["stage"] == "beats"
            and item["status"] in {"approved", "locked"}
            and (item.get("upstream_refs") or {}).get("outline_batch_id")
            == outline_batch_id
        }
        if (episode.get("upstream_refs") or {}).get("outline_batch_id") == outline_batch_id:
            approved.add(int(episode["episode_number"]))
        return {
            "required_episodes": sorted(required),
            "approved_episodes": sorted(approved),
            "remaining_episodes": sorted(required - approved),
            "complete": bool(required) and required <= approved,
        }

    def script_episode_progress(self, resource_id: str) -> dict[str, Any]:
        episode = self._required(self.creative_repository.get_episode(int(resource_id)))
        if episode["stage"] != "script":
            raise ValueError("script resource must reference a script episode")
        project = self._required(self.creative_repository.get_project(int(episode["project_id"])))
        required = {
            int(item["episode_number"])
            for item in project.get("episodes", [])
            if item["stage"] == "beats" and item.get("status") in {"locked", "approved"}
        }
        complete = {
            int(item["episode_number"])
            for item in project.get("episodes", [])
            if item["stage"] == "script" and item.get("status") in {"locked", "approved"}
            and any(
                beat["stage"] == "beats" and beat["id"] == (item.get("upstream_refs") or {}).get("beats_id")
                and beat.get("status") in {"locked", "approved"}
                for beat in project.get("episodes", [])
            )
        }
        complete.add(int(episode["episode_number"]))
        return {"required_episodes": sorted(required), "approved_episodes": sorted(complete), "remaining_episodes": sorted(required - complete), "complete": bool(required) and required <= complete}

    def set_domain_status(
        self,
        resource_type: str,
        resource_id: str,
        status: str,
        *,
        conn: sqlite3.Connection,
        workflow_task_id: str | None = None,
    ) -> None:
        now = time.time()
        task_sql = ", workflow_task_id = COALESCE(?, workflow_task_id)"
        if resource_type == "breakdown":
            conn.execute(
                f"UPDATE creative_breakdowns SET status = ?, updated_at = ?{task_sql} WHERE id = ?",
                (status, now, workflow_task_id, int(resource_id)),
            )
        elif resource_type == "brief":
            conn.execute(
                f"UPDATE creative_blueprints SET status = ?, updated_at = ?{task_sql} WHERE id = ?",
                (status, now, workflow_task_id, int(resource_id)),
            )
        elif resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            for table in ("creative_characters", "creative_relationships"):
                conn.execute(
                    f"UPDATE {table} SET status = ?, updated_at = ?{task_sql} WHERE project_id = ? AND batch_id = ?",
                    (status, now, workflow_task_id, project_id, batch_id),
                )
        elif resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            conn.execute(
                f"UPDATE creative_episodes SET status = ?, updated_at = ?{task_sql} "
                "WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
                (status, now, workflow_task_id, project_id, batch_id),
            )
        else:
            conn.execute(
                f"UPDATE creative_episodes SET status = ?, updated_at = ?{task_sql} WHERE id = ? AND stage = ?",
                (status, now, workflow_task_id, int(resource_id), resource_type),
            )

    def _assert_task(
        self, task_id: str, resource_type: str, resource_id: str, actor: Principal
    ) -> dict:
        task = self.workflow_repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["task_type"] != resource_type:
            raise ValueError("workflow node does not match resource type")
        if task["assignee_user_id"] != actor.user_id and not actor.is_system_admin:
            raise PermissionError("only the task assignee may submit")
        snapshot = self.snapshot(resource_type, resource_id)
        if resource_type == "breakdown":
            if task["topic_id"] != snapshot["topic_id"]:
                raise ValueError("resource does not belong to the task topic")
        elif str(task["project_id"] or "") != str(snapshot["project_id"]):
            raise ValueError("resource does not belong to the task project")
        return task

    def _assert_brief_risk(self, resource_type: str, resource_id: str) -> None:
        if resource_type != "brief":
            return
        brief = self._required(
            self.creative_repository.get_blueprint(int(resource_id))
        )
        if brief["risk_level"] == "high" and not str(
            brief.get("difference_note") or ""
        ).strip():
            raise ValueError("高风险方向必须填写差异化修改说明")

    def _assert_current_upstream(
        self,
        resource_type: str,
        resource_id: str,
        snapshot: dict | list,
    ) -> None:
        if resource_type not in {"characters", "outline", "beats", "script"}:
            return
        if not isinstance(snapshot, dict):
            raise ValueError("creative upstream snapshot is invalid")
        if resource_type == "characters":
            project_id = int(snapshot["project_id"])
            refs = [
                item.get("source_refs") or {}
                for item in snapshot.get("characters", [])
            ] + [
                item.get("source_refs") or {}
                for item in snapshot.get("relationships", [])
            ]
        elif resource_type == "outline":
            project_id = int(snapshot["project_id"])
            refs = [
                item.get("upstream_refs") or {}
                for item in snapshot.get("episodes", [])
            ]
        else:
            project_id = int(snapshot["project_id"])
            refs = [snapshot.get("upstream_refs") or {}]
        project = self._required(self.creative_repository.get_project(project_id))
        brief_id = project.get("locked_brief_id")
        if not brief_id or not self._brief_is_approved(int(brief_id)):
            raise ValueError("current completed Brief upstream is required")
        if any(str(ref.get("brief_id") or "") != str(brief_id) for ref in refs):
            raise ValueError("creative resource references a stale Brief upstream")
        if resource_type in {"outline", "beats", "script"}:
            character_batch_id = project.get("character_batch_id")
            if not character_batch_id or not self._character_batch_is_approved(
                project_id, character_batch_id
            ):
                raise ValueError("current completed character upstream is required")
            if any(
                str(ref.get("character_batch_id") or "")
                != str(character_batch_id)
                for ref in refs
            ):
                raise ValueError("creative resource references a stale character upstream")
        if resource_type in {"beats", "script"}:
            outline_batch_id = project.get("outline_batch_id")
            if not outline_batch_id or not self._outline_batch_is_approved(
                project_id, outline_batch_id
            ):
                raise ValueError("current completed outline upstream is required")
            if any(
                str(ref.get("outline_batch_id") or "") != str(outline_batch_id)
                for ref in refs
            ):
                raise ValueError("creative resource references a stale outline upstream")
        if resource_type == "script":
            current = sorted(
                [item for item in project.get("episodes", []) if item["stage"] == "beats" and int(item["episode_number"]) == int(snapshot["episode_number"]) and item.get("status") in {"locked", "approved"}],
                key=lambda item: (int(item.get("version") or 0), int(item["id"])), reverse=True,
            )
            if not current or any(str(ref.get("beats_id") or "") != str(current[0]["id"]) for ref in refs):
                raise ValueError("场次剧本引用了过期 Beats")

    def _brief_is_approved(self, brief_id: int) -> bool:
        item = self.creative_repository.get_blueprint(brief_id)
        return bool(item and item["status"] in {"approved", "brief_locked"})

    def _character_batch_is_approved(self, project_id: int, batch_id: str) -> bool:
        batch = self.creative_repository.get_character_batch(project_id, batch_id)
        return bool(
            batch["characters"]
            and all(
                item["status"] in {"approved", "locked"}
                for item in batch["characters"] + batch["relationships"]
            )
        )

    def _outline_batch_is_approved(self, project_id: int, batch_id: str) -> bool:
        batch = self.creative_repository.get_outline_batch(project_id, batch_id)
        return bool(
            batch["episodes"]
            and all(
                item["status"] in {"approved", "locked"}
                for item in batch["episodes"]
            )
        )

    def _clone(
        self,
        resource_type: str,
        resource_id: str,
        workflow_task_id: str,
        connection: sqlite3.Connection,
    ) -> str:
        now = time.time()
        if resource_type == "breakdown":
            source = connection.execute(
                "SELECT * FROM creative_breakdowns WHERE id = ?", (int(resource_id),)
            ).fetchone()
            existing = connection.execute(
                "SELECT id FROM creative_breakdowns WHERE parent_version_id = ? AND status = 'draft' ORDER BY id DESC LIMIT 1",
                (int(resource_id),),
            ).fetchone()
            if existing:
                return str(existing["id"])
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM creative_breakdowns WHERE topic_id = ?",
                (source["topic_id"],),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO creative_breakdowns (topic_id, version, parent_version_id, status, content_json, "
                "field_sources_json, risks_json, workflow_task_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
                (source["topic_id"], version, int(resource_id), source["content_json"], source["field_sources_json"],
                 source["risks_json"], workflow_task_id, now, now),
            )
            return str(cursor.lastrowid)
        if resource_type == "brief":
            source = connection.execute(
                "SELECT * FROM creative_blueprints WHERE id = ?", (int(resource_id),)
            ).fetchone()
            existing = connection.execute(
                "SELECT id FROM creative_blueprints WHERE parent_version_id = ? AND status = 'draft' ORDER BY id DESC LIMIT 1",
                (int(resource_id),),
            ).fetchone()
            if existing:
                return str(existing["id"])
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM creative_blueprints WHERE project_id = ? AND candidate_group_id = ?",
                (source["project_id"], source["candidate_group_id"]),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO creative_blueprints (project_id, candidate_group_id, version, parent_version_id, status, "
                "content_json, risk_score, risk_level, difference_note, source_refs_json, generation_meta_json, "
                "workflow_task_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source["project_id"], source["candidate_group_id"], version, int(resource_id), source["content_json"],
                 source["risk_score"], source["risk_level"], source["difference_note"], source["source_refs_json"],
                 source["generation_meta_json"], workflow_task_id, now, now),
            )
            return str(cursor.lastrowid)
        if resource_type == "characters":
            return self._clone_character_batch(resource_id, workflow_task_id, connection, now)
        if resource_type == "outline":
            return self._clone_outline_batch(resource_id, workflow_task_id, connection, now)
        if resource_type not in {"beats", "script"}:
            raise ValueError("unsupported episode resource type")
        source = connection.execute(
            "SELECT * FROM creative_episodes WHERE id = ? AND stage = ?",
            (int(resource_id), resource_type),
        ).fetchone()
        if source is None:
            raise KeyError(resource_id)
        existing = connection.execute(
            "SELECT id FROM creative_episodes WHERE parent_version_id = ? AND stage = ? AND status = 'draft' ORDER BY id DESC LIMIT 1",
            (int(resource_id), resource_type),
        ).fetchone()
        if existing:
            return str(existing["id"])
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM creative_episodes WHERE project_id = ? AND stage = ? AND episode_number = ?",
            (source["project_id"], resource_type, source["episode_number"]),
        ).fetchone()[0]
        cursor = connection.execute(
            "INSERT INTO creative_episodes (project_id, stage, episode_number, batch_id, version, parent_version_id, "
            "status, content_json, upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
            (source["project_id"], resource_type, source["episode_number"], uuid.uuid4().hex, version, int(resource_id),
             source["content_json"], source["upstream_refs_json"], source["generation_meta_json"], workflow_task_id, now, now),
        )
        return str(cursor.lastrowid)

    def _clone_character_batch(self, resource_id, task_id, connection, now) -> str:
        project_id, batch_id = self._batch_key(resource_id)
        new_batch = uuid.uuid4().hex
        rows = connection.execute(
            "SELECT * FROM creative_characters WHERE project_id = ? AND batch_id = ? ORDER BY id",
            (project_id, batch_id),
        ).fetchall()
        existing = connection.execute(
            "SELECT batch_id FROM creative_characters WHERE project_id = ? AND parent_version_id IN "
            "(SELECT id FROM creative_characters WHERE project_id = ? AND batch_id = ?) AND status = 'draft' LIMIT 1",
            (project_id, project_id, batch_id),
        ).fetchone()
        if existing:
            return f"{project_id}:{existing['batch_id']}"
        version = max((row["version"] for row in rows), default=0) + 1
        for table, key in (("creative_characters", "character_key"), ("creative_relationships", "relationship_key")):
            source_rows = connection.execute(
                f"SELECT * FROM {table} WHERE project_id = ? AND batch_id = ? ORDER BY id",
                (project_id, batch_id),
            ).fetchall()
            for source in source_rows:
                connection.execute(
                    f"INSERT INTO {table} (project_id, batch_id, version, parent_version_id, {key}, status, content_json, "
                    "source_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
                    (project_id, new_batch, version, source["id"], source[key], source["content_json"],
                     source["source_refs_json"], source["generation_meta_json"], task_id, now, now),
                )
        return f"{project_id}:{new_batch}"

    def _clone_outline_batch(self, resource_id, task_id, connection, now) -> str:
        project_id, batch_id = self._batch_key(resource_id)
        rows = connection.execute(
            "SELECT * FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND batch_id = ? ORDER BY id",
            (project_id, batch_id),
        ).fetchall()
        existing = connection.execute(
            "SELECT batch_id FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND parent_version_id IN "
            "(SELECT id FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND batch_id = ?) "
            "AND status = 'draft' LIMIT 1",
            (project_id, project_id, batch_id),
        ).fetchone()
        if existing:
            return f"{project_id}:{existing['batch_id']}"
        new_batch = uuid.uuid4().hex
        version = max((row["version"] for row in rows), default=0) + 1
        for source in rows:
            connection.execute(
                "INSERT INTO creative_episodes (project_id, stage, episode_number, batch_id, version, parent_version_id, "
                "status, content_json, upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at) "
                "VALUES (?, 'outline', ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
                (project_id, source["episode_number"], new_batch, version, source["id"], source["content_json"],
                 source["upstream_refs_json"], source["generation_meta_json"], task_id, now, now),
            )
        return f"{project_id}:{new_batch}"

    def _retry_mirror(self, resource_type: str, resource_id: str) -> None:
        self.reconcile_resource(resource_type, resource_id)

    def _find_draft(self, task_id: str, resource_type: str, resource_id: str) -> dict | None:
        drafts = self.workflow_repository.list_task_submissions(task_id)
        return next(
            (item for item in reversed(drafts) if item["resource_type"] == resource_type and item["resource_id"] == resource_id and item["status"] == "draft"),
            None,
        )

    def _record_mirror_error(self, submission: dict, exc: Exception) -> None:
        try:
            self.workflow_repository.record_mirror_error(submission["task_id"], submission, str(exc))
        except Exception:
            pass

    @staticmethod
    def _set_batch_status(connection, resource_type, project_id, batch_id, status):
        now = time.time()
        if resource_type == "characters":
            for table in ("creative_characters", "creative_relationships"):
                connection.execute(
                    f"UPDATE {table} SET status = ?, updated_at = ? WHERE project_id = ? AND batch_id = ?",
                    (status, now, project_id, batch_id),
                )
        else:
            connection.execute(
                "UPDATE creative_episodes SET status = ?, updated_at = ? WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
                (status, now, project_id, batch_id),
            )

    @staticmethod
    def _snapshot_item(item: dict, keys: tuple[str, ...]) -> dict:
        return {key: item.get(key) for key in keys}

    @classmethod
    def _creative_item_snapshot(cls, item: dict) -> dict:
        return cls._snapshot_item(
            item,
            ("id", "project_id", "batch_id", "version", "parent_version_id", "character_key", "relationship_key", "content", "source_refs", "generation_meta"),
        )

    @classmethod
    def _episode_snapshot(cls, item: dict) -> dict:
        return cls._snapshot_item(
            item,
            ("id", "project_id", "stage", "episode_number", "batch_id", "version", "parent_version_id", "content", "upstream_refs", "generation_meta"),
        )

    @staticmethod
    def _required(item: dict | None) -> dict:
        if item is None:
            raise KeyError("creative resource not found")
        return item

    @staticmethod
    def _batch_key(resource_id: str) -> tuple[int, str]:
        try:
            project_id, batch_id = resource_id.split(":", 1)
            if not batch_id:
                raise ValueError
            return int(project_id), batch_id
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("batch resource ID must be project_id:batch_id") from exc

    @staticmethod
    def _validate_type(resource_type: str) -> None:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError("unsupported creative resource type")
