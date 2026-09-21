# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

from sqlalchemy.engine import Connection

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
        # Reference resolution reads other domains, so it stays outside every
        # database transaction; only its outcome is written afterwards.
        bundle = self.market_reference_service.resolve(
            raw_refs, can_read=can_read_reference
        )
        with self._create_lock:
            scope = getattr(self.creative_repository, "transaction_scope", None)
            if not callable(scope):
                # The frozen SQLite store cannot join another store's
                # transaction, so it keeps the resumable bootstrap: the project
                # row commits first, a failed bootstrap is stamped 'failed' and
                # reconcile_pending_for_actor retries it.
                project, created = self._ensure_original_project(payload, actor, bundle)
                try:
                    self._bootstrap(project, actor)
                except Exception:
                    self.creative_repository.mark_project_bootstrap_status(
                        int(project["id"]), "failed"
                    )
                    raise
                return (
                    self.creative_repository.mark_project_bootstrap_status(
                        int(project["id"]), "ready"
                    ),
                    created,
                )
            # One transaction for the creative project row, the workflow
            # bootstrap, the brief link and the ready status: a failure in the
            # second domain rolls the whole creation back instead of leaving a
            # half-bootstrapped project for a later repair to find.
            with scope() as connection:
                project, created = self._ensure_original_project(
                    payload, actor, bundle, connection=connection
                )
                self._bootstrap(project, actor, connection=connection)
                ready = self.creative_repository.mark_project_bootstrap_status(
                    int(project["id"]), "ready", connection=connection
                )
            return ready, created

    def _ensure_original_project(
        self,
        payload: OriginalProjectCreateRequest,
        actor: Principal,
        bundle: Any,
        *,
        connection: Connection | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Find or insert the creative project, optionally inside a caller's transaction."""

        request: dict[str, Any] = {
            "seed_text": payload.seed_text,
            "name": payload.name,
            "genre": payload.genre,
            "target_platform": payload.target_platform,
            "target_audience": payload.target_audience,
            "target_episode_count": payload.target_episode_count,
            "episode_duration_seconds": payload.episode_duration_seconds,
            "constraints": {
                "cost_level": payload.cost_level,
                "content_boundaries": payload.content_boundaries,
            },
            "reference_refs": bundle.source_refs,
            "created_by": actor.user_id,
            "creation_request_id": payload.creation_request_id,
            "workflow_mode": payload.workflow_mode,
        }
        if connection is not None:
            # Only a store that declares the parameter may be handed one; the
            # frozen SQLite store must be called exactly as it is today.
            request["connection"] = connection
        return self.creative_repository.ensure_original_project(**request)

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

    @contextmanager
    def workflow_transaction(
        self, connection: Connection | None = None
    ) -> Iterator[Connection | None]:
        """Own one workflow transaction for a bootstrap, or join the caller's.

        An externally supplied connection is never committed, rolled back or
        closed here. The creative store is the usual owner of that connection:
        project creation opens the creative transaction and hands it down, so
        the workflow rows, the brief link and the bootstrap status commit or
        roll back together.
        """

        if connection is not None:
            yield connection
            return
        identity = getattr(self.workflow_repository, "identity_repository", None)
        scope = getattr(identity, "transaction_scope", None)
        if callable(scope):
            with scope() as active:
                yield active
            return
        yield None

    def _bootstrap(
        self,
        project: dict[str, Any],
        actor: Principal,
        *,
        connection: Connection | None = None,
    ) -> None:
        project_id = str(project["id"])
        scoped_project_id = self.creative_repository.workflow_project_id(project_id)
        joinable = connection is not None
        brief_task_id: str | None = None
        # Every workflow row of one bootstrap is written on a single connection:
        # a failure while creating the fourth task must not leave the creator
        # membership and the first three tasks committed behind it.
        with self.workflow_transaction(connection) as active:
            self.workflow_repository.ensure_project_creator(
                scoped_project_id,
                actor.user_id,
                actor,
                project_uid=str(project.get("project_uid") or "") or None,
                connection=active,
            )
            if project.get("workflow_mode") == "free":
                return
            for task_type, role_code in self._STAGE_ROLES:
                task = self.workflow_repository.ensure_project_task(
                    scoped_project_id,
                    None,
                    task_type=task_type,
                    role_code=role_code,
                    actor=actor,
                    project_uid=str(project.get("project_uid") or "") or None,
                    connection=active,
                )
                if task_type == "brief":
                    brief_task_id = task["id"]
                    if joinable:
                        # Same transaction as the task it points at: the link
                        # and the task are committed or rolled back together, so
                        # no ordering compensation is needed.
                        self.creative_repository.reconcile_project_workflow_task(
                            brief_task_id, int(project_id), connection=active
                        )
                        brief_task_id = None
        if brief_task_id is not None:
            # No caller-owned transaction (frozen SQLite store, or the
            # reconcile path): the link is applied after the workflow rows
            # committed, so it can never point at a task a rollback removed.
            self.creative_repository.reconcile_project_workflow_task(
                brief_task_id, int(project_id)
            )
