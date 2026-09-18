# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
)
from creative.repositories.base import CreativeRepositoryBase, utc_now
from identity.schema import Principal
from workflow.repository import SYSTEM_ACTOR, WorkflowRepository

from .workflow_identity import WorkflowIdentityResolver


RESOURCE_TYPES = frozenset({"breakdown", "brief", "characters", "outline", "beats", "script"})
APPROVAL_FREE_RESOURCE_TYPES = frozenset({"brief", "characters", "outline", "beats", "script"})
IMMUTABLE_WORKFLOW_STATUSES = frozenset(
    {"submitted", "approved", "returned", "superseded"}
)


class CreativeWorkflowAdapter:
    """Bridges creative versions to the workflow registry in the system database."""

    def __init__(
        self,
        creative_repository: CreativeRepositoryBase,
        workflow_repository: WorkflowRepository,
    ) -> None:
        # The adapter never builds a store of its own: the caller injects the
        # one runtime creative repository (PostgreSQL in production).
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.workflow_identity = WorkflowIdentityResolver(
            self.creative_repository, self.workflow_repository
        )

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
        if not self._actor_can_edit_task(task, actor):
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
            self.set_domain_status(
                resource_type,
                resource_id,
                "submitted",
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
        conn: Connection | None = None,
    ) -> dict:
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
        now = utc_now()
        superseded_resources: list[str] = []
        project_revision: int | None = None
        with self.creative_repository.transaction_scope(conn) as connection:
            self.set_domain_status(
                resource_type, resource_id, "approved", connection=connection
            )
            if resource_type == "breakdown":
                item = self._required(
                    self.creative_repository.get_breakdown(int(resource_id))
                )
                topic_id = int(item["topic_id"])
                old_rows = connection.execute(
                    select(creative_breakdowns.c.id).where(
                        creative_breakdowns.c.topic_id == topic_id,
                        creative_breakdowns.c.id != int(resource_id),
                        creative_breakdowns.c.status == "approved",
                    )
                ).scalars().all()
                superseded_resources.extend(str(old_id) for old_id in old_rows)
                connection.execute(
                    update(creative_breakdowns)
                    .where(
                        creative_breakdowns.c.topic_id == topic_id,
                        creative_breakdowns.c.id != int(resource_id),
                        creative_breakdowns.c.status == "approved",
                    )
                    .values(status="superseded", updated_at=now)
                )
            elif resource_type == "brief":
                item = self._required(
                    self.creative_repository.get_blueprint(int(resource_id))
                )
                content = dict(submission["content_snapshot"]["content"])
                connection.execute(
                    update(creative_blueprints)
                    .where(creative_blueprints.c.id == int(resource_id))
                    .values(content_json=content, updated_at=now)
                )
                project = connection.execute(
                    select(
                        creative_projects.c.locked_brief_id,
                        creative_projects.c.revision,
                    ).where(creative_projects.c.id == int(item["project_id"]))
                ).mappings().first()
                old_id = project["locked_brief_id"] if project else None
                expected_revision = int(project["revision"]) if project else -1
                switching = not old_id or int(old_id) != int(resource_id)
                if old_id and switching:
                    connection.execute(
                        update(creative_blueprints)
                        .where(
                            creative_blueprints.c.id == int(old_id),
                            creative_blueprints.c.status == "approved",
                        )
                        .values(status="superseded", updated_at=now)
                    )
                    superseded_resources.append(str(old_id))
                if switching:
                    cursor = connection.execute(
                        update(creative_projects)
                        .where(
                            creative_projects.c.id == int(item["project_id"]),
                            creative_projects.c.revision == expected_revision,
                        )
                        .values(
                            locked_brief_id=int(resource_id),
                            character_batch_id=None,
                            outline_batch_id=None,
                            status="brief_approved",
                            revision=creative_projects.c.revision + 1,
                            updated_at=now,
                        )
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("project revision changed")
                    project_revision = expected_revision + 1
                else:
                    project_revision = expected_revision
            elif resource_type == "characters":
                project_id, batch_id = self._batch_key(resource_id)
                project = connection.execute(
                    select(
                        creative_projects.c.character_batch_id,
                        creative_projects.c.revision,
                    ).where(creative_projects.c.id == project_id)
                ).mappings().first()
                old_batch = project["character_batch_id"] if project else None
                expected_revision = int(project["revision"]) if project else -1
                switching = not old_batch or old_batch != batch_id
                if old_batch and switching:
                    self._set_batch_status(
                        connection, "characters", project_id, old_batch, "superseded"
                    )
                    superseded_resources.append(f"{project_id}:{old_batch}")
                if switching:
                    cursor = connection.execute(
                        update(creative_projects)
                        .where(
                            creative_projects.c.id == project_id,
                            creative_projects.c.revision == expected_revision,
                        )
                        .values(
                            character_batch_id=batch_id,
                            outline_batch_id=None,
                            status="characters_approved",
                            revision=creative_projects.c.revision + 1,
                            updated_at=now,
                        )
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("project revision changed")
                    project_revision = expected_revision + 1
                else:
                    project_revision = expected_revision
            elif resource_type == "outline":
                project_id, batch_id = self._batch_key(resource_id)
                project = connection.execute(
                    select(
                        creative_projects.c.outline_batch_id,
                        creative_projects.c.revision,
                    ).where(creative_projects.c.id == project_id)
                ).mappings().first()
                old_batch = project["outline_batch_id"] if project else None
                expected_revision = int(project["revision"]) if project else -1
                switching = not old_batch or old_batch != batch_id
                if old_batch and switching:
                    self._set_batch_status(
                        connection, "outline", project_id, old_batch, "superseded"
                    )
                    superseded_resources.append(f"{project_id}:{old_batch}")
                if switching:
                    cursor = connection.execute(
                        update(creative_projects)
                        .where(
                            creative_projects.c.id == project_id,
                            creative_projects.c.revision == expected_revision,
                        )
                        .values(
                            outline_batch_id=batch_id,
                            status="outline_approved",
                            revision=creative_projects.c.revision + 1,
                            updated_at=now,
                        )
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("project revision changed")
                    project_revision = expected_revision + 1
                else:
                    project_revision = expected_revision
            elif resource_type == "beats":
                episode = self._required(self.creative_repository.get_episode(int(resource_id)))
                episode_criteria = (
                    creative_episodes.c.project_id == int(episode["project_id"]),
                    creative_episodes.c.stage == "beats",
                    creative_episodes.c.episode_number == int(episode["episode_number"]),
                    creative_episodes.c.id != int(resource_id),
                    creative_episodes.c.status == "approved",
                )
                old_rows = connection.execute(
                    select(creative_episodes.c.id).where(*episode_criteria)
                ).scalars().all()
                superseded_resources.extend(str(old_id) for old_id in old_rows)
                connection.execute(
                    update(creative_episodes)
                    .where(*episode_criteria)
                    .values(status="superseded", updated_at=now)
                )
            if resource_type == "breakdown":
                rows = connection.execute(
                    select(creative_breakdowns.c.id).where(
                        creative_breakdowns.c.topic_id == topic_id,
                        creative_breakdowns.c.status == "superseded",
                    )
                ).scalars().all()
                superseded_resources.extend(str(old_id) for old_id in rows)
            elif resource_type == "brief":
                rows = connection.execute(
                    select(creative_blueprints.c.id).where(
                        creative_blueprints.c.project_id == int(item["project_id"]),
                        creative_blueprints.c.status == "superseded",
                    )
                ).scalars().all()
                superseded_resources.extend(str(old_id) for old_id in rows)
            elif resource_type == "characters":
                rows = connection.execute(
                    select(creative_characters.c.batch_id)
                    .where(
                        creative_characters.c.project_id == project_id,
                        creative_characters.c.status == "superseded",
                    )
                    .distinct()
                ).scalars().all()
                superseded_resources.extend(
                    f"{project_id}:{batch}" for batch in rows
                )
            elif resource_type == "outline":
                rows = connection.execute(
                    select(creative_episodes.c.batch_id)
                    .where(
                        creative_episodes.c.project_id == project_id,
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.status == "superseded",
                    )
                    .distinct()
                ).scalars().all()
                superseded_resources.extend(
                    f"{project_id}:{batch}" for batch in rows
                )
            elif resource_type == "beats":
                rows = connection.execute(
                    select(creative_episodes.c.id).where(
                        creative_episodes.c.project_id == int(episode["project_id"]),
                        creative_episodes.c.stage == "beats",
                        creative_episodes.c.episode_number
                        == int(episode["episode_number"]),
                        creative_episodes.c.status == "superseded",
                    )
                ).scalars().all()
                superseded_resources.extend(str(old_id) for old_id in rows)

            # The workflow supersede writes join the same transaction as the
            # creative status changes above. Moving them outside the scope would
            # let a workflow failure leave Creative superseded with the workflow
            # registry still approving the old version.
            for old_resource_id in dict.fromkeys(superseded_resources):
                self.workflow_repository.supersede_resource_approvals(
                    resource_type,
                    old_resource_id,
                    actor,
                    connection=connection,
                )
            if requested_submission["id"] != submission["id"]:
                self.workflow_repository.supersede_resource_approvals(
                    requested_submission["resource_type"],
                    requested_submission["resource_id"],
                    actor,
                    connection=connection,
                )
        if project_revision is None:
            return submission
        result = dict(submission)
        result["revision"] = project_revision
        return result

    def on_returned(
        self,
        submission: dict,
        new_draft: dict,
        actor: Principal,
        conn: Connection | None = None,
    ) -> dict:
        del actor
        resource_type = submission["resource_type"]
        old_id = submission["resource_id"]
        task_id = submission["task_id"]
        with self.creative_repository.transaction_scope(conn) as connection:
            self.set_domain_status(
                resource_type, old_id, "returned", connection=connection
            )
            new_id = self._clone(resource_type, old_id, task_id, connection)
            # The submission repoint belongs to the same transaction: outside it
            # a failure would leave the cloned creative draft unreachable from
            # the workflow submission that is supposed to reference it.
            self.workflow_repository.update_submission_resource_id(
                new_draft["id"], new_id, connection=connection
            )
            updated = self.workflow_repository.get_submission(
                new_draft["id"], connection=connection
            )
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
        expected_project_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
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
        join = self._joined_kwargs(connection)
        if resource_type == "brief":
            resource = self.creative_repository.lock_brief(
                int(resource_id),
                difference_note,
                expected_revision=expected_project_revision,
                expected_authority=expected_authority,
                **join,
            )
        elif resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.lock_character_batch(
                project_id,
                batch_id,
                expected_revision=expected_project_revision,
                expected_authority=expected_authority,
                **join,
            )
        elif resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.lock_outline_batch(
                project_id,
                batch_id,
                expected_revision=expected_project_revision,
                expected_authority=expected_authority,
                **join,
            )
        elif resource_type == "beats":
            resource = self.creative_repository.lock_episode_beats(
                int(resource_id),
                expected_revision=expected_project_revision,
                expected_authority=expected_authority,
                **join,
            )
        else:
            resource = self.creative_repository.lock_episode_script(
                int(resource_id),
                expected_revision=expected_project_revision,
                expected_authority=expected_authority,
                **join,
            )
        return {"resource": resource, "snapshot": snapshot}

    def _joined_kwargs(self, connection: Connection | None) -> dict[str, Any]:
        """Forward a caller's connection only to a store that can join it.

        The frozen SQLite repository owns its writes internally and declares no
        ``connection`` parameter, so it must be called exactly as before.
        """

        if connection is None or not hasattr(
            self.creative_repository, "transaction_scope"
        ):
            return {}
        return {"connection": connection}

    def lock_beats_batch_without_review(
        self,
        task_id: str,
        project_id: int,
        actor: Principal,
        *,
        connection: Connection | None = None,
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
        resources = self.creative_repository.lock_current_episode_beats_batch(
            project_id, **self._joined_kwargs(connection)
        )
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
        if not self._actor_can_edit_task(task, actor):
            raise PermissionError("only the task assignee may create this resource")
        if (
            project_id is not None
            and str(task["project_id"] or "")
            != self.workflow_identity.project_id(project_id)
        ):
            raise ValueError("resource does not belong to the task project")
        if topic_id is not None and task["topic_id"] != topic_id:
            raise ValueError("resource does not belong to the task topic")
        if (
            topic_id is not None
            and project_id is None
            and not (
                str(task.get("workflow_id") or "")
                == f"topic:{self.workflow_identity.topic_scope(topic_id)}"
                or str(task.get("workflow_id") or "").startswith(
                    f"topic:{self.workflow_identity.topic_scope(topic_id)}:"
                )
            )
        ):
            raise ValueError("workflow task belongs to another creative store")
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
                    self.set_domain_status(
                        resource_type, resource_id, submission["status"]
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
        if resource_type == "breakdown":
            item = self.creative_repository.get_breakdown(int(resource_id))
            task_id = str((item or {}).get("workflow_task_id") or "")
        elif resource_type == "brief":
            item = self.creative_repository.get_blueprint(int(resource_id))
            task_id = str((item or {}).get("workflow_task_id") or "")
        elif resource_type in {"characters", "outline"}:
            project_id, batch_id = self._batch_key(resource_id)
            if resource_type == "characters":
                rows = self.creative_repository.get_character_batch(
                    project_id, batch_id
                )["characters"]
            else:
                rows = self.creative_repository.get_outline_batch(
                    project_id, batch_id
                )["episodes"]
            task_id = str((rows[0].get("workflow_task_id") if rows else "") or "")
        else:
            item = self.creative_repository.get_episode(int(resource_id))
            task_id = str(
                (
                    item.get("workflow_task_id")
                    if item is not None and item.get("stage") == resource_type
                    else ""
                )
                or ""
            )
        if not task_id:
            raise ValueError("强制操作需要关联工作任务以记录审计")
        self.workflow_repository.record_legacy_force(
            task_id, actor, resource_type, resource_id
        )

    def domain_status(self, resource_type: str, resource_id: str) -> str:
        self._validate_type(resource_type)
        status: Any = None
        if resource_type == "breakdown":
            item = self.creative_repository.get_breakdown(int(resource_id))
            status = item["status"] if item else None
        elif resource_type == "brief":
            item = self.creative_repository.get_blueprint(int(resource_id))
            status = item["status"] if item else None
        elif resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            batch = self.creative_repository.get_character_batch(project_id, batch_id)
            item = (batch["characters"] or [None])[0]
            status = item["status"] if item else None
        elif resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            batch = self.creative_repository.get_outline_batch(project_id, batch_id)
            item = (batch["episodes"] or [None])[0]
            status = item["status"] if item else None
        else:
            item = self.creative_repository.get_episode(int(resource_id))
            status = (
                item["status"]
                if item is not None and item.get("stage") == resource_type
                else None
            )
        if status is None:
            raise KeyError(resource_id)
        return str(status)

    def locked_resource(self, resource_type: str, resource_id: str) -> dict:
        """Return the persisted resource body for completion reconciliation."""
        self._validate_type(resource_type)
        if resource_type == "brief":
            return self._required(
                self.creative_repository.get_blueprint(int(resource_id))
            )
        if resource_type == "characters":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.get_character_batch(
                project_id, batch_id
            )
            if not resource["characters"]:
                raise KeyError(resource_id)
            return resource
        if resource_type == "outline":
            project_id, batch_id = self._batch_key(resource_id)
            resource = self.creative_repository.get_outline_batch(project_id, batch_id)
            if not resource["episodes"]:
                raise KeyError(resource_id)
            return resource
        resource = self._required(
            self.creative_repository.get_episode(int(resource_id))
        )
        if resource["stage"] != resource_type:
            raise ValueError("creative resource type mismatch")
        return resource

    def beats_episode_progress(
        self, resource_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        """Return current-outline coverage, treating this completion as accepted."""
        join = self._joined_kwargs(connection)
        episode = self._required(
            self.creative_repository.get_episode(int(resource_id), **join)
        )
        if episode["stage"] != "beats":
            raise ValueError("beats resource must reference a beats episode")
        project = self._required(
            self.creative_repository.get_project(int(episode["project_id"]), **join)
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

    def script_episode_progress(
        self, resource_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        join = self._joined_kwargs(connection)
        episode = self._required(
            self.creative_repository.get_episode(int(resource_id), **join)
        )
        if episode["stage"] != "script":
            raise ValueError("script resource must reference a script episode")
        project = self._required(
            self.creative_repository.get_project(int(episode["project_id"]), **join)
        )
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
        connection: Connection | None = None,
        workflow_task_id: str | None = None,
    ) -> None:
        now = utc_now()
        with self.creative_repository.transaction_scope(connection) as connection:
            if resource_type == "breakdown":
                self._update_status(
                    connection,
                    creative_breakdowns,
                    creative_breakdowns.c.id == int(resource_id),
                    status,
                    now,
                    workflow_task_id,
                )
            elif resource_type == "brief":
                self._update_status(
                    connection,
                    creative_blueprints,
                    creative_blueprints.c.id == int(resource_id),
                    status,
                    now,
                    workflow_task_id,
                )
            elif resource_type == "characters":
                project_id, batch_id = self._batch_key(resource_id)
                for table in (creative_characters, creative_relationships):
                    self._update_status(
                        connection,
                        table,
                        (table.c.project_id == project_id, table.c.batch_id == batch_id),
                        status,
                        now,
                        workflow_task_id,
                    )
            elif resource_type == "outline":
                project_id, batch_id = self._batch_key(resource_id)
                self._update_status(
                    connection,
                    creative_episodes,
                    (
                        creative_episodes.c.project_id == project_id,
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == batch_id,
                    ),
                    status,
                    now,
                    workflow_task_id,
                )
            else:
                self._update_status(
                    connection,
                    creative_episodes,
                    (
                        creative_episodes.c.id == int(resource_id),
                        creative_episodes.c.stage == resource_type,
                    ),
                    status,
                    now,
                    workflow_task_id,
                )

    @staticmethod
    def _update_status(
        connection: Connection,
        table: Any,
        criteria: tuple[Any, ...] | Any,
        status: str,
        now: datetime,
        workflow_task_id: str | None,
    ) -> None:
        if not isinstance(criteria, tuple):
            criteria = (criteria,)
        values: dict[str, Any] = {"status": status, "updated_at": now}
        if workflow_task_id:
            values["workflow_task_id"] = workflow_task_id
        connection.execute(update(table).where(*criteria).values(**values))

    def _assert_task(
        self, task_id: str, resource_type: str, resource_id: str, actor: Principal
    ) -> dict:
        task = self.workflow_repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["task_type"] != resource_type:
            raise ValueError("workflow node does not match resource type")
        if not self._actor_can_edit_task(task, actor):
            raise PermissionError("only the task assignee may submit")
        snapshot = self.snapshot(resource_type, resource_id)
        if resource_type == "breakdown":
            if task["topic_id"] != snapshot["topic_id"]:
                raise ValueError("resource does not belong to the task topic")
        elif str(task["project_id"] or "") != self.workflow_identity.project_id(
            snapshot["project_id"]
        ):
            raise ValueError("resource does not belong to the task project")
        return task

    def _actor_can_edit_task(self, task: dict, actor: Principal) -> bool:
        if actor.is_system_admin or task.get("assignee_user_id") == actor.user_id:
            return True
        project_id = task.get("project_id")
        return bool(
            project_id is not None
            and self.workflow_repository.is_active_project_member(
                str(project_id), actor
            )
        )

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
            if not self.creative_repository.episode_generation_refs_match(
                project,
                resource_type,
                int(snapshot["episode_number"]),
                refs[0],
            ):
                raise ValueError(
                    "creative resource authority is incomplete or stale"
                )
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
        connection: Connection | None = None,
    ) -> str:
        now = utc_now()
        with self.creative_repository.transaction_scope(connection) as connection:
            if resource_type == "breakdown":
                return self._clone_breakdown(
                    resource_id, workflow_task_id, connection, now
                )
            if resource_type == "brief":
                return self._clone_brief(resource_id, workflow_task_id, connection, now)
            if resource_type == "characters":
                return self._clone_character_batch(
                    resource_id, workflow_task_id, connection, now
                )
            if resource_type == "outline":
                return self._clone_outline_batch(
                    resource_id, workflow_task_id, connection, now
                )
            if resource_type not in {"beats", "script"}:
                raise ValueError("unsupported episode resource type")
            source = connection.execute(
                select(creative_episodes).where(
                    creative_episodes.c.id == int(resource_id),
                    creative_episodes.c.stage == resource_type,
                )
            ).mappings().first()
            if source is None:
                raise KeyError(resource_id)
            existing = self._existing_episode_draft(
                connection,
                parent_version_id=int(resource_id),
                stage=resource_type,
            )
            if existing is not None:
                return str(existing)
            version = self._next_episode_version(
                connection,
                project_id=int(source["project_id"]),
                stage=resource_type,
                episode_number=int(source["episode_number"]),
            )
            new_id = self._insert_episode(
                connection,
                project_id=int(source["project_id"]),
                stage=resource_type,
                episode_number=int(source["episode_number"]),
                batch_id=uuid.uuid4().hex,
                version=version,
                parent_version_id=int(resource_id),
                content_json=source["content_json"],
                upstream_refs_json=source["upstream_refs_json"],
                generation_meta_json=source["generation_meta_json"],
                workflow_task_id=workflow_task_id,
                now=now,
                resource_scope=source["resource_scope"],
            )
            return str(new_id)

    def _clone_breakdown(
        self, resource_id: str, workflow_task_id: str, connection: Connection, now: datetime
    ) -> str:
        source = connection.execute(
            select(creative_breakdowns).where(
                creative_breakdowns.c.id == int(resource_id)
            )
        ).mappings().first()
        if source is None:
            raise KeyError(resource_id)
        existing = connection.execute(
            select(creative_breakdowns.c.id)
            .where(
                creative_breakdowns.c.parent_version_id == int(resource_id),
                creative_breakdowns.c.status == "draft",
            )
            .order_by(creative_breakdowns.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return str(existing)
        version = int(
            connection.execute(
                select(func.coalesce(func.max(creative_breakdowns.c.version), 0)).where(
                    creative_breakdowns.c.topic_id == source["topic_id"]
                )
            ).scalar_one()
        ) + 1
        new_id = connection.execute(
            insert(creative_breakdowns)
            .values(
                topic_id=int(source["topic_id"]),
                version=version,
                parent_version_id=int(resource_id),
                status="draft",
                content_json=source["content_json"],
                field_sources_json=source["field_sources_json"],
                risks_json=source["risks_json"],
                workflow_task_id=workflow_task_id,
                created_at=now,
                updated_at=now,
            )
            .returning(creative_breakdowns.c.id)
        ).scalar_one()
        return str(new_id)

    def _clone_brief(
        self, resource_id: str, workflow_task_id: str, connection: Connection, now: datetime
    ) -> str:
        source = connection.execute(
            select(creative_blueprints).where(
                creative_blueprints.c.id == int(resource_id)
            )
        ).mappings().first()
        if source is None:
            raise KeyError(resource_id)
        existing = connection.execute(
            select(creative_blueprints.c.id)
            .where(
                creative_blueprints.c.parent_version_id == int(resource_id),
                creative_blueprints.c.status == "draft",
            )
            .order_by(creative_blueprints.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return str(existing)
        version = self._next_blueprint_version(
            connection,
            project_id=int(source["project_id"]),
            candidate_group_id=source["candidate_group_id"],
        )
        new_id = connection.execute(
            insert(creative_blueprints)
            .values(
                project_id=int(source["project_id"]),
                candidate_group_id=source["candidate_group_id"],
                version=version,
                parent_version_id=int(resource_id),
                status="draft",
                content_json=source["content_json"],
                risk_score=source["risk_score"],
                risk_level=source["risk_level"],
                difference_note=source["difference_note"],
                source_refs_json=source["source_refs_json"],
                generation_meta_json=source["generation_meta_json"],
                direction_type=source["direction_type"],
                resource_scope=source["resource_scope"],
                workflow_task_id=workflow_task_id,
                created_at=now,
                updated_at=now,
            )
            .returning(creative_blueprints.c.id)
        ).scalar_one()
        return str(new_id)

    def _clone_character_batch(self, resource_id, task_id, connection, now) -> str:
        project_id, batch_id = self._batch_key(resource_id)
        new_batch = uuid.uuid4().hex
        existing = connection.execute(
            select(creative_characters.c.batch_id)
            .where(
                creative_characters.c.project_id == project_id,
                creative_characters.c.parent_version_id.in_(
                    select(creative_characters.c.id).where(
                        creative_characters.c.project_id == project_id,
                        creative_characters.c.batch_id == batch_id,
                    )
                ),
                creative_characters.c.status == "draft",
            )
            .limit(1)
        ).scalar_one_or_none()
        if existing:
            return f"{project_id}:{existing}"
        rows = connection.execute(
            select(creative_characters)
            .where(
                creative_characters.c.project_id == project_id,
                creative_characters.c.batch_id == batch_id,
            )
            .order_by(creative_characters.c.id)
        ).mappings().all()
        version = max((int(row["version"]) for row in rows), default=0) + 1
        for table, key in (
            (creative_characters, "character_key"),
            (creative_relationships, "relationship_key"),
        ):
            source_rows = connection.execute(
                select(table)
                .where(table.c.project_id == project_id, table.c.batch_id == batch_id)
                .order_by(table.c.id)
            ).mappings().all()
            if not source_rows:
                continue
            connection.execute(
                insert(table),
                [
                    {
                        "project_id": project_id,
                        "batch_id": new_batch,
                        "version": version,
                        "parent_version_id": int(row["id"]),
                        key: row[key],
                        "status": "draft",
                        "content_json": row["content_json"],
                        "source_refs_json": row["source_refs_json"],
                        "generation_meta_json": row["generation_meta_json"],
                        "resource_scope": row["resource_scope"],
                        "workflow_task_id": task_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for row in source_rows
                ],
            )
        return f"{project_id}:{new_batch}"

    def _clone_outline_batch(self, resource_id, task_id, connection, now) -> str:
        project_id, batch_id = self._batch_key(resource_id)
        rows = connection.execute(
            select(creative_episodes)
            .where(
                creative_episodes.c.project_id == project_id,
                creative_episodes.c.stage == "outline",
                creative_episodes.c.batch_id == batch_id,
            )
            .order_by(creative_episodes.c.id)
        ).mappings().all()
        existing = connection.execute(
            select(creative_episodes.c.batch_id)
            .where(
                creative_episodes.c.project_id == project_id,
                creative_episodes.c.stage == "outline",
                creative_episodes.c.parent_version_id.in_(
                    select(creative_episodes.c.id).where(
                        creative_episodes.c.project_id == project_id,
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == batch_id,
                    )
                ),
                creative_episodes.c.status == "draft",
            )
            .limit(1)
        ).scalar_one_or_none()
        if existing:
            return f"{project_id}:{existing}"
        new_batch = uuid.uuid4().hex
        version = max((int(row["version"]) for row in rows), default=0) + 1
        for source in rows:
            self._insert_episode(
                connection,
                project_id=project_id,
                stage="outline",
                episode_number=int(source["episode_number"]),
                batch_id=new_batch,
                version=version,
                parent_version_id=int(source["id"]),
                content_json=source["content_json"],
                upstream_refs_json=source["upstream_refs_json"],
                generation_meta_json=source["generation_meta_json"],
                workflow_task_id=task_id,
                now=now,
                resource_scope=source["resource_scope"],
            )
        return f"{project_id}:{new_batch}"

    @staticmethod
    def _insert_episode(
        connection: Connection,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        batch_id: str,
        version: int,
        parent_version_id: int,
        content_json: Any,
        upstream_refs_json: Any,
        generation_meta_json: Any,
        workflow_task_id: str | None,
        now: datetime,
        resource_scope: str | None = None,
    ) -> int:
        values: dict[str, Any] = {
            "project_id": project_id,
            "stage": stage,
            "episode_number": episode_number,
            "batch_id": batch_id,
            "version": version,
            "parent_version_id": parent_version_id,
            "status": "draft",
            "content_json": content_json,
            "upstream_refs_json": upstream_refs_json,
            "generation_meta_json": generation_meta_json,
            "workflow_task_id": workflow_task_id,
            "created_at": now,
            "updated_at": now,
        }
        if resource_scope is not None:
            values["resource_scope"] = resource_scope
        return int(
            connection.execute(
                insert(creative_episodes).values(**values).returning(creative_episodes.c.id)
            ).scalar_one()
        )

    @staticmethod
    def _existing_episode_draft(
        connection: Connection, *, parent_version_id: int, stage: str
    ) -> str | None:
        return connection.execute(
            select(creative_episodes.c.id)
            .where(
                creative_episodes.c.parent_version_id == parent_version_id,
                creative_episodes.c.stage == stage,
                creative_episodes.c.status == "draft",
            )
            .order_by(creative_episodes.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _next_blueprint_version(
        connection: Connection, *, project_id: int, candidate_group_id: Any
    ) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_blueprints.c.version), 0)).where(
                creative_blueprints.c.project_id == project_id,
                creative_blueprints.c.candidate_group_id == candidate_group_id,
            )
        ).scalar_one()
        return int(latest) + 1

    @staticmethod
    def _next_episode_version(
        connection: Connection,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
    ) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_episodes.c.version), 0)).where(
                creative_episodes.c.project_id == project_id,
                creative_episodes.c.stage == stage,
                creative_episodes.c.episode_number == episode_number,
            )
        ).scalar_one()
        return int(latest) + 1

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
    def _set_batch_status(
        connection: Connection,
        resource_type,
        project_id,
        batch_id,
        status,
        now: datetime | None = None,
    ):
        timestamp = now or utc_now()
        if resource_type == "characters":
            for table in (creative_characters, creative_relationships):
                connection.execute(
                    update(table)
                    .where(table.c.project_id == project_id, table.c.batch_id == batch_id)
                    .values(status=status, updated_at=timestamp)
                )
        else:
            connection.execute(
                update(creative_episodes)
                .where(
                    creative_episodes.c.project_id == project_id,
                    creative_episodes.c.stage == "outline",
                    creative_episodes.c.batch_id == batch_id,
                )
                .values(status=status, updated_at=timestamp)
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
