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
from typing import Any

from identity.repository import IdentityRepository
from identity.schema import Principal

from .approval_service import ApprovalService
from .repository import SYSTEM_ACTOR, WorkflowRepository


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
    ) -> None:
        self.repository = (
            repository
            if isinstance(repository, WorkflowRepository)
            else WorkflowRepository(repository)
        )
        self.approval_service = ApprovalService(self.repository)

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
        return {
            "submission": submission,
            "task": task,
            "next_task": next_task,
        }

    def return_submission(
        self,
        submission_id: str,
        actor: Principal,
        review_comment: str,
    ) -> dict[str, Any]:
        submission, draft = self.approval_service.return_submission(
            submission_id, actor, review_comment
        )
        task = self.repository.get_task(submission["task_id"])
        if task is None:
            raise KeyError(submission["task_id"])
        return {"submission": submission, "task": task, "draft": draft}

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
