# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading
from typing import Any
import uuid

from creative.original_direction_service import validate_original_direction_content
from creative.original_project_service import OriginalProjectService
from creative.repository import (
    SIDE_EFFECT_OPERATION_LEASE_SECONDS,
    CreativeRepository,
    canonical_request_hash,
)
from creative.schema import OriginalProjectCreateRequest
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobManager
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


MAX_INLINE_SOURCE_SNAPSHOT_BYTES = 2 * 1024 * 1024
SOURCE_SNAPSHOT_CHUNK_BYTES = 1024 * 1024


class CreativeContextConflict(ValueError):
    """Raised when the expected creative authority revision is stale."""


class CreativeTaskOccupied(PermissionError):
    """Raised when another assignee owns the creative workflow task."""


class CreativeProjectAccessDenied(PermissionError):
    """Raised when the actor cannot write creative content for the project."""


def build_generation_payload(
    *,
    project_id: int,
    operation_id: str,
    action: str = "blueprints",
    normalized_stage: str = "brief",
    created_by_user_id: str | None = None,
    workflow_task_id: str | None = None,
    generation_instruction: str = "",
    source_authority: dict[str, Any] | None = None,
    source_authority_sha256: str = "",
    generation_request_hash: str = "",
    retry_failed_operation: bool = False,
    snapshot_extra: dict[str, Any] | None = None,
    original_generation_extra: dict[str, Any] | None = None,
    legacy_extra: dict[str, Any] | None = None,
    runtime_dir: str | Path = ".runtime",
) -> dict[str, Any]:
    """Build the phase 4 worker payload.

    The worker resolves its own repository from the shared system database, so
    the payload carries business identity only: no database path, no database
    URL and no credentials ever travel through a task file.
    """

    return {
        "platform": "creative",
        "crawler_type": "phase4_generate",
        "keywords": [],
        "save_data_option": "json",
        "get_comments": False,
        "get_sub_comments": False,
        "extra": {
            "action": action,
            "normalized_stage": normalized_stage,
            "project_id": project_id,
            "created_by_user_id": created_by_user_id,
            "workflow_task_id": workflow_task_id,
            "operation_id": operation_id,
            "generation_instruction": generation_instruction,
            **(snapshot_extra or {}),
            "source_authority": source_authority,
            "source_authority_sha256": source_authority_sha256,
            "authority_schema_version": 1,
            "generation_request_hash": generation_request_hash,
            "retry_failed_operation": bool(retry_failed_operation),
            **(original_generation_extra or {}),
            **(legacy_extra or {}),
            "runtime_dir": str(Path(runtime_dir).resolve()),
        },
    }


class CreativeApplicationService:
    def __init__(
        self,
        repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
        transitions: TransitionService,
        job_manager: JobManager | None,
        runtime_dir: Path,
        original_project_service: OriginalProjectService,
        workflow_adapter: CreativeWorkflowAdapter,
        permission_service: PermissionService,
    ) -> None:
        self.repository = repository
        self.workflow_repository = workflow_repository
        self.transitions = transitions
        self.job_manager = job_manager
        self.runtime_dir = Path(runtime_dir)
        self.original_project_service = original_project_service
        self.workflow_adapter = workflow_adapter
        self.workflow_identity = workflow_adapter.workflow_identity
        self.permission_service = permission_service

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

    @staticmethod
    def _resource_fingerprint(content: Any) -> str:
        return canonical_request_hash(content or {})

    @staticmethod
    def highest_locked_episode_resource(
        project: dict[str, Any], stage: str, episode_number: int
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in project.get("episodes", [])
            if item.get("stage") == stage
            and int(item.get("episode_number") or 0) == int(episode_number)
            and item.get("status") in {"locked", "approved"}
        ]
        return max(
            candidates,
            key=lambda item: (int(item.get("version") or 0), int(item["id"])),
            default=None,
        )

    @staticmethod
    def _current_locked_outline(
        project: dict[str, Any], episode_number: int
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in project.get("episodes", [])
            if item.get("stage") == "outline"
            and int(item.get("episode_number") or 0) == int(episode_number)
            and item.get("batch_id") == project.get("outline_batch_id")
            and item.get("status") in {"locked", "approved"}
        ]
        return max(
            candidates,
            key=lambda item: (int(item.get("version") or 0), int(item["id"])),
            default=None,
        )

    @classmethod
    def compatible_locked_episode_resource(
        cls, project: dict[str, Any], stage: str, episode_number: int
    ) -> dict[str, Any] | None:
        """Select the highest locked episode resource on current upstream authority."""
        if stage not in {"beats", "script"}:
            raise ValueError("compatibility lookup only supports episode resources")
        outline = cls._current_locked_outline(project, episode_number)
        if outline is None:
            return None
        locked_brief_id = project.get("locked_brief_id")
        character_batch_id = project.get("character_batch_id")
        outline_batch_id = project.get("outline_batch_id")
        if not locked_brief_id or not character_batch_id or not outline_batch_id:
            return None
        brief = next(
            (
                item
                for item in project.get("blueprints", [])
                if item.get("id") == locked_brief_id
                and item.get("status") in {"brief_locked", "locked", "approved"}
            ),
            None,
        )
        characters = [
            item
            for item in project.get("characters", [])
            if item.get("batch_id") == character_batch_id
            and item.get("status") in {"locked", "approved"}
        ]
        relationships = [
            item
            for item in project.get("relationships", [])
            if item.get("batch_id") == character_batch_id
            and item.get("status") in {"locked", "approved"}
        ]
        if brief is None or not characters:
            return None
        character_fingerprint = CreativeRepository._content_fingerprint({
            "characters": [
                {
                    "id": item["id"],
                    "version": item.get("version"),
                    "content": item.get("content") or {},
                }
                for item in sorted(characters, key=lambda item: int(item["id"]))
            ],
            "relationships": [
                {
                    "id": item["id"],
                    "version": item.get("version"),
                    "content": item.get("content") or {},
                }
                for item in sorted(relationships, key=lambda item: int(item["id"]))
            ],
        })

        compatible_beats: dict[str, Any] | None = None
        if stage == "script":
            compatible_beats = cls.compatible_locked_episode_resource(
                project, "beats", episode_number
            )
            if compatible_beats is None:
                return None

        def is_compatible(item: dict[str, Any]) -> bool:
            refs = item.get("upstream_refs") or {}
            if (
                str(refs.get("brief_id") or "") != str(locked_brief_id)
                or int(refs.get("brief_version") or 0)
                != int(brief.get("version") or 0)
                or str(refs.get("brief_content_fingerprint") or "")
                != cls._resource_fingerprint(brief.get("content") or {})
                or str(refs.get("character_batch_id") or "")
                != str(character_batch_id)
                or str(refs.get("character_content_fingerprint") or "")
                != character_fingerprint
                or str(refs.get("outline_batch_id") or "")
                != str(outline_batch_id)
                or int(refs.get("outline_id") or 0) != int(outline["id"])
                or int(refs.get("outline_version") or 0)
                != int(outline.get("version") or 0)
                or not CreativeRepository._outline_content_fingerprint_matches(
                    refs.get("outline_content_fingerprint"),
                    outline.get("content") or {},
                )
            ):
                return False
            if stage == "beats":
                return True
            assert compatible_beats is not None
            return (
                int(refs.get("beats_id") or 0) == int(compatible_beats["id"])
                and int(refs.get("beats_version") or 0)
                == int(compatible_beats.get("version") or 0)
                and str(refs.get("beats_content_fingerprint") or "")
                == cls._resource_fingerprint(compatible_beats.get("content") or {})
            )

        candidates = [
            item
            for item in project.get("episodes", [])
            if item.get("stage") == stage
            and int(item.get("episode_number") or 0) == int(episode_number)
            and item.get("status") in {"locked", "approved"}
            and is_compatible(item)
        ]
        return max(
            candidates,
            key=lambda item: (int(item.get("version") or 0), int(item["id"])),
            default=None,
        )

    @classmethod
    def plan_expected_context(
        cls, project: dict[str, Any], episode_number: int | None = None
    ) -> dict[str, Any]:
        """Build the scoped project authority compared at each plan side effect."""
        beats = (
            cls.compatible_locked_episode_resource(project, "beats", episode_number)
            if episode_number is not None
            else None
        )
        return {
            "revision": int(project.get("revision") or 0),
            "locked_brief_id": project.get("locked_brief_id"),
            "character_batch_id": project.get("character_batch_id"),
            "outline_batch_id": project.get("outline_batch_id"),
            "beats": {
                "resource_id": str(beats["id"]),
                "version": int(beats.get("version") or 0),
                "fingerprint": cls._resource_fingerprint(beats.get("content") or {}),
            }
            if beats is not None
            else None,
        }

    def current_plan_context(
        self, project_id: int, episode_number: int | None = None
    ) -> dict[str, Any]:
        return self.plan_expected_context(
            self.get_project(project_id), episode_number
        )

    def assert_plan_context(
        self,
        project_id: int,
        expected_context: dict[str, Any],
        episode_number: int | None = None,
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """Compare scoped plan authority and return a CAS envelope for writes."""
        current = self.current_plan_context(project_id, episode_number)
        if canonical_request_hash(current) != canonical_request_hash(expected_context):
            raise CreativeContextConflict("project authority changed")
        authority = self.repository.generation_authority_envelope(
            project_id, episode_number, stage=stage
        )
        if (
            int(authority.get("revision") or 0)
            != int(expected_context.get("revision") or 0)
            or str((authority.get("brief") or {}).get("id") or "")
            != str(expected_context.get("locked_brief_id") or "")
            or str((authority.get("characters") or {}).get("batch_id") or "")
            != str(expected_context.get("character_batch_id") or "")
            or str((authority.get("outline") or {}).get("batch_id") or "")
            != str(expected_context.get("outline_batch_id") or "")
        ):
            raise CreativeContextConflict("project authority changed")
        confirmed = self.current_plan_context(project_id, episode_number)
        if canonical_request_hash(confirmed) != canonical_request_hash(
            expected_context
        ):
            raise CreativeContextConflict("project authority changed")
        return authority

    def advance_plan_context(
        self,
        project_id: int,
        expected_context: dict[str, Any],
        *,
        stage: str,
        kind: str,
        resource_id: str | None,
        episode_number: int | None,
    ) -> dict[str, Any]:
        """Advance only authority changes deterministically caused by one plan step."""
        advanced = {
            **expected_context,
            "beats": dict(expected_context["beats"])
            if isinstance(expected_context.get("beats"), dict)
            else None,
        }
        if kind == "lock":
            if not resource_id:
                raise ValueError("lock step requires resource_id")
            if stage == "brief":
                advanced.update({
                    "revision": int(expected_context["revision"]) + 1,
                    "locked_brief_id": int(resource_id),
                    "character_batch_id": None,
                    "outline_batch_id": None,
                    "beats": None,
                })
            elif stage == "characters":
                _, separator, batch_id = resource_id.partition(":")
                if not separator or not batch_id:
                    raise ValueError("character lock requires a batch resource id")
                advanced.update({
                    "revision": int(expected_context["revision"]) + 1,
                    "character_batch_id": batch_id,
                    "outline_batch_id": None,
                    "beats": None,
                })
            elif stage == "outline":
                _, separator, batch_id = resource_id.partition(":")
                if not separator or not batch_id:
                    raise ValueError("outline lock requires a batch resource id")
                advanced.update({
                    "revision": int(expected_context["revision"]) + 1,
                    "outline_batch_id": batch_id,
                    "beats": None,
                })
            elif stage == "beats":
                resource = self.repository.get_episode(int(resource_id))
                if (
                    resource is None
                    or resource.get("stage") != "beats"
                    or int(resource.get("episode_number") or 0)
                    != int(episode_number or 0)
                ):
                    raise ValueError("locked Beats does not match the plan episode")
                advanced["beats"] = {
                    "resource_id": str(resource["id"]),
                    "version": int(resource.get("version") or 0),
                    "fingerprint": self._resource_fingerprint(
                        resource.get("content") or {}
                    ),
                }
            elif stage != "script":
                raise ValueError("unsupported creative lock stage")
        current = self.current_plan_context(project_id, episode_number)
        if canonical_request_hash(current) != canonical_request_hash(advanced):
            raise CreativeContextConflict(
                "project authority changed outside the completed plan step"
            )
        return current

    def create_original_project(
        self,
        payload: dict[str, Any],
        actor: Principal,
        *,
        creation_request_id: str,
        can_read_reference: Callable[[str, int], bool],
    ) -> dict[str, Any]:
        project, _ = self.create_original_project_result(
            payload,
            actor,
            creation_request_id=creation_request_id,
            can_read_reference=can_read_reference,
        )
        return project

    def create_original_project_result(
        self,
        payload: dict[str, Any],
        actor: Principal,
        *,
        creation_request_id: str,
        can_read_reference: Callable[[str, int], bool],
    ) -> tuple[dict[str, Any], bool]:
        request = OriginalProjectCreateRequest.model_validate(
            {**payload, "creation_request_id": creation_request_id}
        )
        return self.original_project_service.create(
            request, actor, can_read_reference=can_read_reference
        )

    def ensure_stage_task(
        self,
        project_id: int,
        stage: str,
        actor: Principal,
        *,
        expected_context: dict[str, Any] | None = None,
        episode_number: int | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        if expected_context is not None:
            self.assert_plan_context(
                project_id, expected_context, episode_number, stage=stage
            )
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise ValueError("自由创作项目不能创建标准阶段任务")
        role_code = (
            "creative_planner" if stage in {"brief", "characters"} else "screenwriter"
        )
        task = self.workflow_repository.ensure_project_task(
            self.workflow_identity.project_id(project_id),
            project.get("topic_id"),
            task_type=stage,
            role_code=role_code,
            actor=actor,
            writer_operation_id=operation_id,
        )
        if task["status"] not in {"pool", "assigned", "in_progress", "returned"}:
            task = self.workflow_repository.create_next_task(
                task["id"],
                stage,
                role_code,
                actor=actor,
                writer_operation_id=operation_id,
            )
        if task["status"] == "pool":
            if not self.workflow_repository.can_claim_task(task["id"], actor):
                raise CreativeTaskOccupied("该创作阶段已由其他人员处理")
            try:
                task = self.workflow_repository.claim_task(
                    task["id"], actor, writer_operation_id=operation_id
                )
            except PermissionError as exc:
                raise CreativeTaskOccupied(
                    "该创作阶段已由其他人员处理"
                ) from exc
        if (
            task["status"] in {"assigned", "in_progress", "returned"}
            and task.get("assignee_user_id") not in {None, actor.user_id}
            and not self.workflow_repository.is_active_project_member(
                self.workflow_identity.project_id(project_id), actor
            )
        ):
            raise CreativeTaskOccupied("该创作阶段已由其他人员处理")
        return task

    def assert_stage_write_access(
        self, project_id: int, stage: str, actor: Principal
    ) -> None:
        globally_authorized = bool(
            actor.is_system_admin or "project_owner" in actor.role_codes
        )
        active_member = (
            False
            if globally_authorized
            else self.workflow_repository.is_active_project_member(
                self.workflow_identity.project_id(project_id), actor
            )
        )
        if not self.permission_service.can_create_project_content(
            actor,
            self.workflow_identity.project_id(project_id),
            active_member=active_member,
        ):
            raise CreativeProjectAccessDenied("无权创作该项目")

    def validate_task_for_creation(
        self,
        workflow_task_id: str,
        project_id: int,
        stage: str,
        actor: Principal,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise ValueError("自由创作项目不能使用标准阶段任务")
        try:
            return self.workflow_adapter.assert_task_for_creation(
                workflow_task_id, stage, actor, project_id=project_id
            )
        except PermissionError as exc:
            raise CreativeTaskOccupied(
                "该创作阶段已由其他人员处理"
            ) from exc

    def persist_draft(
        self,
        *,
        project_id: int,
        stage: str,
        payload: dict[str, Any],
        workflow_task_id: str,
        operation_id: str,
        actor: Principal,
        episode_number: int | None = None,
        reject_if_locked: bool = False,
        expected_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(
            workflow_task_id, project_id, stage, actor
        )
        expected_authority = (
            self.assert_plan_context(
                project_id, expected_context, episode_number, stage=stage
            )
            if expected_context is not None
            else None
        )
        expected_revision = (
            int(expected_context["revision"])
            if expected_context is not None
            else None
        )
        try:
            if stage == "brief":
                content = validate_original_direction_content(
                    payload, market_context=[], blocked_terms=[]
                )
                resource = self.repository.create_blueprints(
                    project_id,
                    [{"content": content}],
                    workflow_task_id=workflow_task_id,
                    direction_type="original",
                    operation_id=operation_id,
                    first_root_only=True,
                    expected_authority=expected_authority,
                )[0]
            elif stage == "characters":
                if not payload.get("characters"):
                    raise ValueError("characters must contain at least one item")
                resource = self.repository.create_character_batch(
                    project_id,
                    payload["characters"],
                    payload.get("relationships", []),
                    workflow_task_id=workflow_task_id,
                    operation_id=operation_id,
                    expected_project_revision=expected_revision,
                    expected_authority=expected_authority,
                    reject_if_locked=reject_if_locked,
                )
            elif stage == "outline":
                resource = self.repository.create_outline_batch(
                    project_id,
                    payload["episodes"],
                    payload.get("series_arc", ""),
                    workflow_task_id=workflow_task_id,
                    operation_id=operation_id,
                    expected_project_revision=expected_revision,
                    expected_authority=expected_authority,
                    reject_if_locked=reject_if_locked,
                )
            elif stage == "beats" and episode_number is not None:
                resource = self.repository.create_episode_beats(
                    project_id,
                    episode_number,
                    payload,
                    workflow_task_id=workflow_task_id,
                    operation_id=operation_id,
                    expected_project_revision=expected_revision,
                    expected_authority=expected_authority,
                    reject_if_locked=reject_if_locked,
                )
            elif stage == "script" and episode_number is not None:
                resource = self.repository.create_episode_script(
                    project_id,
                    episode_number,
                    payload,
                    workflow_task_id=workflow_task_id,
                    operation_id=operation_id,
                    expected_project_revision=expected_revision,
                    expected_authority=expected_authority,
                    reject_if_locked=reject_if_locked,
                )
            else:
                raise ValueError("unsupported creative stage payload")
        except ValueError as exc:
            if str(exc) in {
                "project revision changed",
                "server source authority changed",
            }:
                raise CreativeContextConflict(
                    "project authority changed"
                ) from exc
            raise
        return self.resource_ref(stage, resource)

    def persist_legacy_draft(
        self,
        *,
        replaces_resource_id: str | None = None,
        expected_project_revision: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Preserve legacy response bodies while enforcing the revision boundary."""
        stage = str(kwargs["stage"])
        episode_number = kwargs.get("episode_number")
        if (replaces_resource_id is None) != (expected_project_revision is None):
            raise ValueError(
                "replaces_resource_id and expected_project_revision are required together"
            )
        if replaces_resource_id is None:
            if stage != "brief" and self.repository.has_locked_stage_resource(
                int(kwargs["project_id"]),
                stage,
                episode_number=episode_number,
            ):
                raise ValueError(
                    "replaces_resource_id and expected_project_revision are required "
                    "after a resource is locked"
                )
            ref = self.persist_draft(**kwargs, reject_if_locked=True)
        else:
            ref = self.create_revision(
                **kwargs,
                replaces_resource_id=str(replaces_resource_id),
                expected_project_revision=int(expected_project_revision),
            )
        return self.hydrate_resource_ref(ref)

    def hydrate_resource_ref(self, ref: dict[str, Any]) -> dict[str, Any]:
        project_id = int(ref["project_id"])
        stage = str(ref["resource_type"])
        if stage == "characters":
            return self.repository.get_character_batch(
                project_id, str(ref.get("batch_id") or ref["resource_id"]).split(":")[-1]
            )
        if stage == "outline":
            return self.repository.get_outline_batch(
                project_id, str(ref.get("batch_id") or ref["resource_id"]).split(":")[-1]
            )
        if stage in {"beats", "script"}:
            resource = self.repository.get_episode(int(ref["resource_id"]))
        elif stage == "brief":
            resource = self.repository.get_blueprint(int(ref["resource_id"]))
        else:
            raise ValueError("unsupported creative resource reference")
        if resource is None:
            raise ValueError("creative resource no longer exists")
        return resource

    def resource_ref(
        self, stage: str, resource: dict[str, Any]
    ) -> dict[str, Any]:
        if stage == "characters":
            items = resource["characters"]
            project_id = int(items[0]["project_id"])
            version = max(int(item["version"]) for item in items)
            resource_id = f"{project_id}:{resource['batch_id']}"
        elif stage == "outline":
            items = resource["episodes"]
            project_id = int(items[0]["project_id"])
            version = max(int(item["version"]) for item in items)
            resource_id = f"{project_id}:{resource['batch_id']}"
        else:
            project_id = int(resource["project_id"])
            version = int(resource["version"])
            resource_id = str(resource["id"])
        return {
            "resource_type": stage,
            "resource_id": resource_id,
            "project_id": project_id,
            "version": version,
            "status": resource.get("status", "draft"),
            "parent_version_id": resource.get("parent_version_id"),
            "batch_id": resource.get("batch_id"),
            "episode_number": resource.get("episode_number"),
        }

    def recover_generation_result(
        self, *, operation_id: str, project_id: int, stage: str
    ) -> dict[str, Any]:
        """Rebuild durable resource references after a worker completed."""

        hydrated = self.repository.hydrate_operation_result(operation_id)
        resources: list[dict[str, Any]]
        if isinstance(hydrated, list):
            resources = [dict(item) for item in hydrated]
        elif isinstance(hydrated, dict):
            resources = [hydrated]
        else:
            resumable = (
                self.repository.find_resumable_outline_batch(
                    int(project_id),
                    operation_id=operation_id,
                    include_complete=True,
                )
                if stage == "outline"
                else None
            )
            if resumable is None:
                raise ValueError("completed generation result is unavailable")
            resources = [resumable]
        if not resources:
            raise ValueError("completed generation resources are unavailable")
        return {
            "resource_refs": [
                self.resource_ref(stage, resource) for resource in resources
            ]
        }

    def lock_resource(
        self,
        *,
        project_id: int,
        stage: str,
        resource_id: str,
        workflow_task_id: str,
        operation_id: str,
        actor: Principal,
        difference_note: str = "",
        expected_project_revision: int | None = None,
        expected_context: dict[str, Any] | None = None,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        request_hash = canonical_request_hash(
            {
                "workflow_task_id": workflow_task_id,
                "resource_id": resource_id,
                "difference_note": difference_note,
                **(
                    {"expected_project_revision": expected_project_revision}
                    if expected_project_revision is not None
                    else {}
                ),
                **(
                    {"expected_context": expected_context}
                    if expected_context is not None
                    else {}
                ),
            }
        )
        owner_id = uuid.uuid4().hex
        reservation = self.repository.reserve_side_effect_operation(
            operation_id=operation_id,
            project_id=project_id,
            stage=f"{stage}.lock",
            request_hash=request_hash,
            owner_id=owner_id,
        )
        if reservation["state"] == "complete":
            return reservation["result"]
        heartbeat_stop = threading.Event()
        heartbeat_interval = SIDE_EFFECT_OPERATION_LEASE_SECONDS / 3

        def renew_lease() -> None:
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    if not self.repository.renew_side_effect_operation(
                        operation_id=operation_id, owner_id=owner_id
                    ):
                        return
                except Exception:
                    continue

        heartbeat = threading.Thread(target=renew_lease, daemon=True)
        heartbeat.start()
        try:
            if reservation.get("recovered"):
                reconciled = self.transitions.reconcile_creative_without_review(
                    workflow_task_id, stage, resource_id
                )
                if reconciled is not None:
                    return self.repository.finalize_side_effect_operation(
                        operation_id=operation_id,
                        owner_id=owner_id,
                        result=reconciled,
                    )
            self.assert_stage_write_access(project_id, stage, actor)
            self.validate_task_for_creation(
                workflow_task_id, project_id, stage, actor
            )
            expected_authority = None
            if expected_context is not None:
                expected_authority = self.assert_plan_context(
                    project_id, expected_context, episode_number, stage=stage
                )
            kwargs: dict[str, Any] = {
                "expected_project_revision": (
                    int(expected_context["revision"])
                    if expected_context is not None
                    else expected_project_revision
                ),
                "expected_authority": expected_authority,
            }
            if stage == "brief":
                kwargs["difference_note"] = difference_note
            result = self.transitions.complete_creative_without_review(
                workflow_task_id,
                stage,
                resource_id,
                actor,
                writer_operation_id=operation_id,
                **kwargs,
            )
            return self.repository.finalize_side_effect_operation(
                operation_id=operation_id, owner_id=owner_id, result=result
            )
        except ValueError as exc:
            self.repository.abandon_side_effect_operation(
                operation_id=operation_id, owner_id=owner_id
            )
            if str(exc) in {
                "project revision changed",
                "server source authority changed",
            }:
                raise CreativeContextConflict(
                    "project authority changed"
                ) from exc
            raise
        except Exception:
            self.repository.abandon_side_effect_operation(
                operation_id=operation_id, owner_id=owner_id
            )
            raise
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1.0)

    def create_revision(
        self,
        *,
        project_id: int,
        stage: str,
        replaces_resource_id: str,
        payload: dict[str, Any],
        workflow_task_id: str,
        operation_id: str,
        expected_project_revision: int,
        actor: Principal,
        episode_number: int | None = None,
        expected_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(workflow_task_id, project_id, stage, actor)
        expected_authority = (
            self.assert_plan_context(
                project_id, expected_context, episode_number, stage=stage
            )
            if expected_context is not None
            else None
        )
        try:
            if stage == "brief":
                source = self.repository.get_blueprint(int(replaces_resource_id))
                if source is None:
                    raise ValueError("仿制方向不存在")
                content = (
                    validate_original_direction_content(
                        payload, market_context=[], blocked_terms=[]
                    )
                    if source.get("direction_type") == "original"
                    else dict(payload)
                )
                resource = self.repository.create_blueprint_revision(
                    int(replaces_resource_id),
                    content,
                    workflow_task_id,
                    operation_id,
                    expected_project_revision=expected_project_revision,
                    expected_project_id=project_id,
                )
            elif stage == "characters":
                resource = self.repository.create_character_batch(
                    project_id, payload["characters"], payload.get("relationships", []),
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                    expected_authority=expected_authority,
                )
            elif stage == "outline":
                resource = self.repository.create_outline_batch(
                    project_id, payload["episodes"], payload.get("series_arc", ""),
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                    expected_authority=expected_authority,
                )
            elif stage == "beats" and episode_number is not None:
                resource = self.repository.create_episode_beats(
                    project_id, episode_number, payload,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                    expected_authority=expected_authority,
                )
            elif stage == "script" and episode_number is not None:
                resource = self.repository.create_episode_script(
                    project_id, episode_number, payload,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                    expected_authority=expected_authority,
                )
            else:
                raise ValueError("unsupported creative revision payload")
        except ValueError as exc:
            if str(exc) in {
                "project revision changed",
                "server source authority changed",
            }:
                raise CreativeContextConflict("project authority revision changed") from exc
            raise
        return self.resource_ref(stage, resource)

    def _snapshot_extra(
        self, source_snapshot_id: str, source_snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        embedded_id = source_snapshot.get("snapshot_id") or source_snapshot.get("id")
        if embedded_id is not None and str(embedded_id) != source_snapshot_id:
            raise ValueError("source snapshot id mismatch")
        canonical = json.dumps(
            source_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = canonical_request_hash(source_snapshot)
        common = {
            "source_snapshot_id": source_snapshot_id,
            "source_snapshot_sha256": fingerprint,
        }
        if len(canonical) <= MAX_INLINE_SOURCE_SNAPSHOT_BYTES:
            return {**common, "source_snapshot": source_snapshot}

        snapshot_dir = (
            self.runtime_dir
            / "creative"
            / "source_snapshots"
            / fingerprint
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for index, offset in enumerate(
            range(0, len(canonical), SOURCE_SNAPSHOT_CHUNK_BYTES)
        ):
            chunk_path = snapshot_dir / f"chunk-{index:04d}.jsonpart"
            chunk_path.write_bytes(canonical[offset : offset + SOURCE_SNAPSHOT_CHUNK_BYTES])
            paths.append(chunk_path.relative_to(self.runtime_dir).as_posix())
        return {
            **common,
            "source_snapshot_ref": {
                "paths": paths,
                "sha256": fingerprint,
                "size": len(canonical),
                "read_only": True,
            },
        }

    def start_generation(
        self,
        *,
        project_id: int,
        stage: str,
        actor: Principal,
        workflow_task_id: str,
        operation_id: str,
        generation_instruction: str,
        source_snapshot_id: str,
        source_snapshot: dict[str, Any],
        episode_number: int | None = None,
        episode_start: int | None = None,
        episode_end: int | None = None,
        series: bool = False,
        count: int | None = None,
        seed_id: int | None = None,
        market_context: list[dict[str, Any]] | None = None,
        blocked_terms: list[str] | None = None,
        reference_snapshot: list[dict[str, Any]] | None = None,
        llm_model_record_id: str | None = None,
        expected_context: dict[str, Any] | None = None,
        retry_failed_operation: bool = False,
    ) -> dict[str, Any]:
        state = self.start_generation_compat(
            project_id=project_id,
            stage=stage,
            actor=actor,
            workflow_task_id=workflow_task_id,
            operation_id=operation_id,
            generation_instruction=generation_instruction,
            source_snapshot_id=source_snapshot_id,
            source_snapshot=source_snapshot,
            episode_number=episode_number,
            episode_start=episode_start,
            episode_end=episode_end,
            series=series,
            count=count,
            seed_id=seed_id,
            market_context=market_context,
            blocked_terms=blocked_terms,
            reference_snapshot=reference_snapshot,
            llm_model_record_id=llm_model_record_id,
            chunk_size=10,
            expected_context=expected_context,
            retry_failed_operation=retry_failed_operation,
        )
        return {
            "job_id": state.job_id,
            "status": state.status,
            "project_id": project_id,
            "stage": stage,
            "workflow_task_id": workflow_task_id,
            "operation_id": operation_id,
        }

    def retry_generation_job(
        self,
        *,
        job_state: dict[str, Any],
        actor: Principal,
    ) -> Any:
        """Retry one failed sequential generation through its owning operation."""

        if self.job_manager is None:
            raise RuntimeError("creative job manager is unavailable")
        if str(job_state.get("status") or "") != "failed":
            raise ValueError("只有失败的创作任务可以重试")
        payload = job_state.get("job")
        if not isinstance(payload, dict):
            raise ValueError("创作任务载荷缺失")
        if (
            str(payload.get("platform") or "") != "creative"
            or str(payload.get("crawler_type") or "") != "phase4_generate"
        ):
            raise ValueError("该任务不是创作生成任务")
        extra = payload.get("extra")
        if not isinstance(extra, dict):
            raise ValueError("创作任务上下文缺失")
        if str(extra.get("workflow_mode") or "sequential") != "sequential":
            raise ValueError("自由创作任务需要重新发起生成")

        operation_id = str(extra.get("operation_id") or "").strip()
        request_hash = str(extra.get("generation_request_hash") or "").strip()
        creator_id = str(extra.get("created_by_user_id") or "").strip()
        workflow_task_id = str(extra.get("workflow_task_id") or "").strip()
        try:
            project_id = int(extra.get("project_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("创作任务项目范围无效") from exc
        action = str(extra.get("action") or "").strip()
        stage = str(extra.get("normalized_stage") or "").strip()
        expected_stage = {
            "blueprints": "brief",
            "blueprint_variant": "brief",
            "characters": "characters",
            "outlines": "outline",
            "outline_overview": "outline",
            "beats": "beats",
            "script": "script",
        }.get(action)
        if (
            project_id < 1
            or not operation_id
            or not request_hash
            or not creator_id
            or not workflow_task_id
            or expected_stage is None
            or stage != expected_stage
        ):
            raise ValueError("创作任务业务身份不完整，无法安全重试")
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise ValueError("项目创作模式已变化，请重新发起生成")
        if not actor.is_system_admin and creator_id != actor.user_id:
            raise CreativeProjectAccessDenied("只有原任务创建人可以重试")

        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(
            workflow_task_id, project_id, stage, actor
        )
        authority = extra.get("source_authority")
        authority_sha = str(extra.get("source_authority_sha256") or "").strip()
        if (
            not isinstance(authority, dict)
            or not authority_sha
            or canonical_request_hash(authority) != authority_sha
        ):
            raise ValueError("创作任务来源校验失败，无法安全重试")
        try:
            self.repository.assert_generation_authority(project_id, authority)
        except ValueError as exc:
            raise CreativeContextConflict(
                "项目上游版本已变化，请重新发起生成"
            ) from exc

        retry_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        retry_payload["extra"]["retry_failed_operation"] = True
        get_or_create = getattr(self.job_manager, "get_or_create_job", None)
        if not callable(get_or_create):
            raise RuntimeError("creative operation retry is unavailable")
        return get_or_create(
            operation_id,
            retry_payload,
            created_by_user_id=creator_id,
            workflow_task_id=workflow_task_id,
            project_id=project_id,
        )

    def start_generation_compat(
        self,
        *,
        project_id: int,
        stage: str,
        actor: Principal | None,
        workflow_task_id: str | None,
        operation_id: str,
        generation_instruction: str,
        source_snapshot_id: str,
        source_snapshot: dict[str, Any],
        action: str | None = None,
        seed_id: int | None = None,
        market_context: list[dict[str, Any]] | None = None,
        blocked_terms: list[str] | None = None,
        reference_snapshot: list[dict[str, Any]] | None = None,
        expected_context: dict[str, Any] | None = None,
        retry_failed_operation: bool = False,
        **legacy_extra: Any,
    ) -> Any:
        if self.job_manager is None:
            raise RuntimeError("creative job manager is unavailable")
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise ValueError("自由创作项目必须使用自由创作生成接口")
        if actor is not None:
            self.assert_stage_write_access(project_id, stage, actor)
            if workflow_task_id is not None:
                self.validate_task_for_creation(
                    workflow_task_id, project_id, stage, actor
                )
        worker_action = action or {
            "brief": "blueprints",
            "characters": "characters",
            "outline": "outlines",
            "beats": "beats",
            "script": "script",
        }[stage]
        authority = (
            self.assert_plan_context(
                project_id,
                expected_context,
                legacy_extra.get("episode_number"),
                stage=stage,
            )
            if expected_context is not None
            else self.repository.generation_authority_envelope(
                project_id,
                legacy_extra.get("episode_number"),
                stage=stage,
            )
        )
        authority_sha = canonical_request_hash(authority)
        snapshot_extra = self._snapshot_extra(source_snapshot_id, source_snapshot)
        original_generation_extra = {
            key: value
            for key, value in {
                "seed_id": seed_id,
                "market_context": market_context,
                "blocked_terms": blocked_terms,
                "reference_snapshot": reference_snapshot,
            }.items()
            if value is not None
        }
        request_identity = {
            "project_id": project_id,
            "stage": stage,
            "action": worker_action,
            "workflow_task_id": workflow_task_id,
            "operation_id": operation_id,
            "generation_instruction": generation_instruction,
            "source_snapshot_id": source_snapshot_id,
            "source_snapshot_sha256": snapshot_extra["source_snapshot_sha256"],
            "source_authority_sha256": authority_sha,
            "flags": legacy_extra,
            **(
                {"original_generation": original_generation_extra}
                if original_generation_extra else {}
            ),
        }
        generation_request_hash = canonical_request_hash(request_identity)
        payload = build_generation_payload(
            project_id=project_id,
            operation_id=operation_id,
            action=worker_action,
            normalized_stage=stage,
            created_by_user_id=actor.user_id if actor is not None else None,
            workflow_task_id=workflow_task_id,
            generation_instruction=generation_instruction,
            source_authority=authority,
            source_authority_sha256=authority_sha,
            generation_request_hash=generation_request_hash,
            retry_failed_operation=retry_failed_operation,
            snapshot_extra=snapshot_extra,
            original_generation_extra=original_generation_extra,
            legacy_extra=legacy_extra,
            runtime_dir=self.runtime_dir,
        )
        if actor is not None and workflow_task_id is not None:
            get_or_create = getattr(self.job_manager, "get_or_create_job", None)
            if callable(get_or_create):
                return get_or_create(
                    operation_id,
                    payload,
                    created_by_user_id=actor.user_id,
                    workflow_task_id=workflow_task_id,
                    project_id=project_id,
                )
        return self.job_manager.create_job(payload)
