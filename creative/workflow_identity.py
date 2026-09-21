# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from creative.repository import CreativeRepository
from workflow.repository import WorkflowRepository


class WorkflowIdentityResolver:
    """Resolve legacy Workflow keys without rewriting persisted history."""

    def __init__(
        self,
        creative_repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
    ) -> None:
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository

    def project_id(self, local_project_id: int | str) -> str:
        local_id = int(local_project_id)
        scoped_id = self.creative_repository.workflow_project_id(local_id)
        resolve = getattr(
            self.workflow_repository, "resolve_creative_project_identity", None
        )
        if not callable(resolve):
            return scoped_id
        evidence = self.creative_repository.project_workflow_identity_evidence(
            local_id
        )
        return resolve(
            scoped_id=scoped_id,
            legacy_id=str(local_id),
            task_ids=evidence["task_ids"],
            creator_user_id=evidence["created_by"],
        )

    def project_reference(self, local_project_id: int | str) -> tuple[str, str]:
        """Return the canonical workflow key and permanent UUID for one project."""

        local_id = int(local_project_id)
        project = self.creative_repository.get_project(local_id)
        if project is None:
            raise KeyError(local_id)
        project_uid = str(project.get("project_uid") or "").strip()
        if not project_uid:
            raise RuntimeError("creative project has no permanent identity")
        return self.creative_repository.workflow_project_id(local_id), project_uid

    def project_candidates(self, local_project_id: int | str) -> tuple[str, ...]:
        local_id = int(local_project_id)
        scoped_id = self.creative_repository.workflow_project_id(local_id)
        resolved_id = self.project_id(local_id)
        if resolved_id == scoped_id:
            return (scoped_id,)
        return (scoped_id, resolved_id)

    def topic_scope(self, local_topic_id: int | str) -> str:
        local_id = int(local_topic_id)
        scoped_scope = self.creative_repository.workflow_topic_id(local_id)
        resolve = getattr(
            self.workflow_repository, "resolve_creative_topic_scope", None
        )
        if not callable(resolve):
            return scoped_scope
        task_ids = self.creative_repository.topic_workflow_task_ids(local_id)
        return resolve(
            scoped_scope=scoped_scope,
            legacy_scope=str(local_id),
            task_ids=task_ids,
        )
