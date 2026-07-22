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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from identity.repository import IdentityRepository
from identity.schema import Principal

from .approval_service import ApprovalService
from .repository import SYSTEM_ACTOR, WorkflowRepository

if TYPE_CHECKING:
    from creative.workflow_adapter import CreativeWorkflowAdapter


WORKFLOW_NODES = {
    "topic_decision": ("selection_operator", "project_owner", "source_package"),
    "source_package": ("selection_operator", "project_owner", "breakdown"),
    "breakdown": ("selection_operator", "project_owner", "brief"),
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
        self.approval_service = ApprovalService(self.repository)
        self.creative_adapter = creative_adapter or self._default_creative_adapter()

    def create_submission(
        self,
        task_id: str,
        resource_type: str,
        resource_id: str,
        content: Any,
        actor: Principal,
    ) -> dict[str, Any]:
        return self.approval_service.create_draft(
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
            content=content,
            actor=actor,
        )

    def submit(self, submission_id: str, actor: Principal) -> dict[str, Any]:
        return self.approval_service.submit(submission_id, actor)

    def approve(self, submission_id: str, actor: Principal) -> dict[str, Any]:
        with self.repository.identity_repository.transaction() as connection:
            current = self.approval_service.get_submission(
                submission_id, connection=connection
            )
            if current is None:
                raise KeyError(submission_id)
            if current["status"] == "approved":
                self._assert_repeated_approval_allowed(
                    current, actor, connection=connection
                )
                submission = current
            else:
                submission = self.approval_service.approve(
                    submission_id, actor, connection=connection
                )

            task = self.repository._require_task(
                connection, submission["task_id"]
            )
            node = WORKFLOW_NODES.get(task["task_type"])
            if node is None:
                raise ValueError(f"unknown workflow node: {task['task_type']}")
            next_node = node[2]
            next_task = None
            if next_node is not None:
                next_role = WORKFLOW_NODES[next_node][0]
                next_task = self.repository.create_next_task(
                    task["id"],
                    next_node,
                    next_role,
                    actor=SYSTEM_ACTOR,
                    connection=connection,
                )
        result = {
            "submission": submission,
            "task": task,
            "next_task": next_task,
        }
        if self.creative_adapter is not None and submission["resource_type"] in {
            "breakdown", "brief", "characters", "outline", "beats"
        }:
            try:
                self.creative_adapter.on_approved(submission, actor)
            except Exception as exc:
                self.repository.record_mirror_error(
                    submission["task_id"], submission, str(exc)
                )
        return result

    def return_submission(
        self,
        submission_id: str,
        actor: Principal,
        review_comment: str,
    ) -> dict[str, Any]:
        with self.repository.identity_repository.transaction() as connection:
            submission, draft = self.approval_service.return_submission(
                submission_id, actor, review_comment, connection=connection
            )
            if self.creative_adapter is not None and submission["resource_type"] in {
                "breakdown", "brief", "characters", "outline", "beats"
            }:
                draft = self.creative_adapter.on_returned(
                    submission, draft, actor, conn=connection
                )
        task = self.repository.get_task(submission["task_id"])
        if task is None:
            raise KeyError(submission["task_id"])
        return {"submission": submission, "task": task, "draft": draft}

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

    def fork(self, submission_id: str, actor: Principal) -> dict[str, Any]:
        return self.approval_service.fork(submission_id, actor)

    def _assert_repeated_approval_allowed(
        self,
        submission: dict[str, Any],
        actor: Principal,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        actor_id = self.repository._actor_id(actor)
        with self.repository.identity_repository.connection_scope(
            connection
        ) as active_connection:
            live_actor = self.repository._live_actor(active_connection, actor_id)
            if (
                submission["submitted_by"] == actor_id
                and not live_actor["is_system_admin"]
            ):
                raise PermissionError("a submitter cannot approve their own submission")
            task = self.repository._require_task(
                active_connection, submission["task_id"]
            )
            self.approval_service._assert_reviewer(
                active_connection, task, live_actor, "version.approve"
            )
