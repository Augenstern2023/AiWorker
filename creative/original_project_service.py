# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from creative.market_reference_service import MarketReferenceService
from creative.repository import CreativeRepository
from creative.schema import OriginalProjectCreateRequest
from identity.schema import Principal
from workflow.repository import WorkflowRepository


class OriginalProjectService:
    _STAGE_ROLES = (
        ("brief", "creative_planner"),
        ("characters", "creative_planner"),
        ("outline", "screenwriter"),
        ("beats", "screenwriter"),
        ("script", "screenwriter"),
    )

    def __init__(
        self,
        creative_repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
        market_reference_service: MarketReferenceService,
    ) -> None:
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.market_reference_service = market_reference_service
        self._create_lock = Lock()

    def create(
        self,
        payload: OriginalProjectCreateRequest,
        actor: Principal,
        *,
        can_read_reference: Callable[[str, int], bool],
    ) -> tuple[dict[str, Any], bool]:
        raw_refs = [item.model_dump() for item in payload.reference_refs]
        bundle = self.market_reference_service.resolve(
            raw_refs, can_read=can_read_reference
        )
        with self._create_lock:
            project, created = self.creative_repository.ensure_original_project(
                seed_text=payload.seed_text,
                name=payload.name,
                genre=payload.genre,
                target_platform=payload.target_platform,
                target_audience=payload.target_audience,
                target_episode_count=payload.target_episode_count,
                episode_duration_seconds=payload.episode_duration_seconds,
                constraints={
                    "cost_level": payload.cost_level,
                    "content_boundaries": payload.content_boundaries,
                },
                reference_refs=bundle.source_refs,
                created_by=actor.user_id,
                creation_request_id=payload.creation_request_id,
                workflow_mode=payload.workflow_mode,
            )
            try:
                self._bootstrap(project, actor)
            except Exception:
                self.creative_repository.mark_project_bootstrap_status(
                    int(project["id"]), "failed"
                )
                raise
            ready = self.creative_repository.mark_project_bootstrap_status(
                int(project["id"]), "ready"
            )
        return ready, created

    def reconcile_pending_for_actor(self, actor: Principal) -> list[dict[str, Any]]:
        reconciled: list[dict[str, Any]] = []
        projects = self.creative_repository.list_projects_for_bootstrap(
            actor.user_id, "pending"
        )
        for project in projects:
            try:
                self._bootstrap(project, actor)
                reconciled.append(
                    self.creative_repository.mark_project_bootstrap_status(
                        int(project["id"]), "ready"
                    )
                )
            except Exception:
                self.creative_repository.mark_project_bootstrap_status(
                    int(project["id"]), "failed"
                )
        return reconciled

    def _bootstrap(self, project: dict[str, Any], actor: Principal) -> None:
        project_id = str(project["id"])
        self.workflow_repository.ensure_project_creator(project_id, actor.user_id, actor)
        if project.get("workflow_mode") == "free":
            return
        for task_type, role_code in self._STAGE_ROLES:
            task = self.workflow_repository.ensure_project_task(
                project_id,
                None,
                task_type=task_type,
                role_code=role_code,
                actor=actor,
            )
            if task_type == "brief":
                self.creative_repository.reconcile_project_workflow_task(
                    task["id"], int(project_id)
                )
