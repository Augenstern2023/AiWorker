# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any

from fastapi import Request

from audit.service import AuditService
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .repository import CreativeRepository


class WorkflowBootstrapService:
    """Creates the durable entry point from an adopted topic into workflow."""

    def __init__(
        self,
        creative_repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
        audit_service: AuditService | None = None,
    ) -> None:
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.audit_service = audit_service

    def ensure_topic_workflow(
        self,
        topic_id: int,
        actor: Principal,
        request: Request | None = None,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        topic = self.creative_repository.get_topic(topic_id)
        if topic is None:
            raise KeyError(topic_id)
        self._assert_authorized(actor, project_id)
        task = self.workflow_repository.ensure_topic_task(
            topic_id,
            task_type="topic_decision",
            role_code="selection_operator",
            actor=actor,
            project_id=project_id,
        )
        if self.audit_service is not None:
            self.audit_service.record(
                actor=actor,
                action="workflow.bootstrap",
                target_type="topic",
                target_id=str(topic_id),
                project_id=None,
                before=None,
                after={"workflow_id": task["workflow_id"], "task_id": task["id"]},
                reason=None,
                result="success",
                request=request,
            )
        return {"topic": topic, "task": task}

    def adopt_topic(
        self,
        topic_id: int,
        actor: Principal,
        request: Request | None = None,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if self.creative_repository.get_topic(topic_id) is None:
            raise KeyError(topic_id)
        self._assert_authorized(actor, project_id)
        result = self.ensure_topic_workflow(
            topic_id, actor, request, project_id=project_id
        )
        topic = self.creative_repository.update_topic(topic_id, "adopted")
        if topic is None:
            raise KeyError(topic_id)
        result["topic"] = topic
        return result

    def _assert_authorized(
        self, actor: Principal, project_id: str | None
    ) -> None:
        with self.workflow_repository.identity_repository.connect() as connection:
            live_actor = self.workflow_repository._live_actor(connection, actor.user_id)
        if not live_actor["is_system_admin"] and "project.manage" not in live_actor["permissions"]:
            raise PermissionError(
                "project.manage permission is required to bootstrap workflow"
            )
        if project_id is not None and not live_actor["is_system_admin"]:
            self.workflow_repository.assert_can_manage_project(project_id, actor)
