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
from typing import Any
import uuid

from creative.original_direction_service import validate_original_direction_content
from creative.original_project_service import OriginalProjectService
from creative.repository import CreativeRepository, canonical_request_hash
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
        self.permission_service = permission_service

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

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
        self, project_id: int, stage: str, actor: Principal
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        role_code = (
            "creative_planner" if stage in {"brief", "characters"} else "screenwriter"
        )
        self.assert_stage_write_access(project_id, stage, actor)
        task = self.workflow_repository.ensure_project_task(
            str(project_id),
            project.get("topic_id"),
            task_type=stage,
            role_code=role_code,
            actor=actor,
        )
        if task["status"] not in {"pool", "assigned", "in_progress", "returned"}:
            task = self.workflow_repository.create_next_task(
                task["id"], stage, role_code, actor=actor
            )
        if task["status"] == "pool":
            if not self.workflow_repository.can_claim_task(task["id"], actor):
                raise PermissionError("该创作阶段已由其他人员处理")
            task = self.workflow_repository.claim_task(task["id"], actor)
        if (
            task["status"] in {"assigned", "in_progress", "returned"}
            and task.get("assignee_user_id") not in {None, actor.user_id}
            and not self.workflow_repository.is_active_project_member(
                str(project_id), actor
            )
        ):
            raise PermissionError("该创作阶段已由其他人员处理")
        return task

    def assert_stage_write_access(
        self, project_id: int, stage: str, actor: Principal
    ) -> None:
        if not self.permission_service.can_create_project_content(
            actor,
            str(project_id),
            active_member=self.workflow_repository.is_active_project_member(
                str(project_id), actor
            ),
        ):
            raise PermissionError("无权创作该项目")

    def validate_task_for_creation(
        self,
        workflow_task_id: str,
        project_id: int,
        stage: str,
        actor: Principal,
    ) -> dict[str, Any]:
        return self.workflow_adapter.assert_task_for_creation(
            workflow_task_id, stage, actor, project_id=project_id
        )

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
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(
            workflow_task_id, project_id, stage, actor
        )
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
            )
        elif stage == "outline":
            resource = self.repository.create_outline_batch(
                project_id,
                payload["episodes"],
                payload.get("series_arc", ""),
                workflow_task_id=workflow_task_id,
                operation_id=operation_id,
            )
        elif stage == "beats" and episode_number is not None:
            resource = self.repository.create_episode_beats(
                project_id,
                episode_number,
                payload,
                workflow_task_id=workflow_task_id,
                operation_id=operation_id,
            )
        elif stage == "script" and episode_number is not None:
            resource = self.repository.create_episode_script(
                project_id,
                episode_number,
                payload,
                workflow_task_id=workflow_task_id,
                operation_id=operation_id,
            )
        else:
            raise ValueError("unsupported creative stage payload")
        return self.resource_ref(stage, resource)

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
    ) -> dict[str, Any]:
        request_hash = canonical_request_hash(
            {
                "workflow_task_id": workflow_task_id,
                "resource_id": resource_id,
                "difference_note": difference_note,
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
        try:
            kwargs = {"difference_note": difference_note} if stage == "brief" else {}
            result = self.transitions.complete_creative_without_review(
                workflow_task_id, stage, resource_id, actor, **kwargs
            )
            return self.repository.finalize_side_effect_operation(
                operation_id=operation_id, owner_id=owner_id, result=result
            )
        except Exception:
            self.repository.abandon_side_effect_operation(
                operation_id=operation_id, owner_id=owner_id
            )
            raise

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
    ) -> dict[str, Any]:
        self.assert_stage_write_access(project_id, stage, actor)
        self.validate_task_for_creation(workflow_task_id, project_id, stage, actor)
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
                )
            elif stage == "outline":
                resource = self.repository.create_outline_batch(
                    project_id, payload["episodes"], payload.get("series_arc", ""),
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                )
            elif stage == "beats" and episode_number is not None:
                resource = self.repository.create_episode_beats(
                    project_id, episode_number, payload,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                )
            elif stage == "script" and episode_number is not None:
                resource = self.repository.create_episode_script(
                    project_id, episode_number, payload,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    replaces_resource_id=replaces_resource_id,
                    expected_project_revision=expected_project_revision,
                )
            else:
                raise ValueError("unsupported creative revision payload")
        except ValueError as exc:
            if "project revision changed" in str(exc):
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
            chunk_size=10,
        )
        return {
            "job_id": state.job_id,
            "status": state.status,
            "project_id": project_id,
            "stage": stage,
            "workflow_task_id": workflow_task_id,
            "operation_id": operation_id,
        }

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
        **legacy_extra: Any,
    ) -> Any:
        if self.job_manager is None:
            raise RuntimeError("creative job manager is unavailable")
        worker_action = action or {
            "brief": "blueprints",
            "characters": "characters",
            "outline": "outlines",
            "beats": "beats",
            "script": "script",
        }[stage]
        authority = self.repository.generation_authority_envelope(project_id)
        authority_sha = canonical_request_hash(authority)
        snapshot_extra = self._snapshot_extra(source_snapshot_id, source_snapshot)
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
        }
        generation_request_hash = canonical_request_hash(request_identity)
        payload = {
            "platform": "creative",
            "crawler_type": "phase4_generate",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "action": worker_action,
                "normalized_stage": stage,
                "project_id": project_id,
                "created_by_user_id": actor.user_id if actor is not None else None,
                "workflow_task_id": workflow_task_id,
                "operation_id": operation_id,
                "generation_instruction": generation_instruction,
                **snapshot_extra,
                "source_authority": authority,
                "source_authority_sha256": authority_sha,
                "generation_request_hash": generation_request_hash,
                **legacy_extra,
                "db_path": str(self.repository.db_path.resolve()),
                "runtime_dir": str(self.runtime_dir.resolve()),
            },
        }
        return self.job_manager.create_job(payload)
