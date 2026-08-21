# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any

from creative.application_service import (
    CreativeApplicationService,
    CreativeContextConflict,
)
from identity.permission_service import PermissionService
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .action_service import validate_original_project_action_payload
from .schema import CreativePlan, PlanStep, Stage, WriterActionError
from .store import WriterAgentStore


class WriterProjectOrchestrator:
    """Execute durable writer-session actions that select a current project."""

    def __init__(
        self,
        store: WriterAgentStore,
        creative_service: CreativeApplicationService,
        permission_service: PermissionService,
        workflow_repository: WorkflowRepository,
        can_read_reference: Callable[[Principal, str, int], bool],
    ) -> None:
        self.store = store
        self.creative_service = creative_service
        self.permission_service = permission_service
        self.workflow_repository = workflow_repository
        self.can_read_reference = can_read_reference

    @staticmethod
    def project_url(
        project_id: int, stage: str = "brief", episode_number: int | None = None
    ) -> str:
        if stage == "brief":
            return f"/planning/projects/{project_id}/brief"
        if stage == "characters":
            return f"/planning/projects/{project_id}/characters"
        if stage == "outline":
            return f"/writing/projects/{project_id}/outlines"
        if stage in {"beats", "script"} and episode_number is not None:
            suffix = "beats" if stage == "beats" else "scripts"
            return f"/writing/projects/{project_id}/episodes/{episode_number}/{suffix}"
        return "/writing/projects"

    @staticmethod
    def _fingerprint(value: Any) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _locked_resource(
        project: dict[str, Any], stage: Stage, episode_number: int | None = None
    ) -> dict[str, Any] | None:
        locked_statuses = {"locked", "approved", "brief_locked"}
        if stage == "brief":
            identifier = project.get("locked_brief_id")
            return next(
                (
                    item for item in project.get("blueprints", [])
                    if item.get("id") == identifier
                    and item.get("status") in locked_statuses
                ),
                None,
            )
        if stage == "characters":
            batch_id = project.get("character_batch_id")
            items = [
                item for item in project.get("characters", [])
                if item.get("batch_id") == batch_id
                and item.get("status") in locked_statuses
            ]
            return {"batch_id": batch_id, "items": items} if batch_id and items else None
        if stage == "outline":
            batch_id = project.get("outline_batch_id")
            items = [
                item for item in project.get("episodes", [])
                if item.get("stage") == "outline" and item.get("batch_id") == batch_id
                and item.get("status") in locked_statuses
            ]
            expected = set(range(1, int(project.get("target_episode_count") or 0) + 1))
            if batch_id and {item.get("episode_number") for item in items} == expected:
                return {"batch_id": batch_id, "items": items}
            return None
        return next(
            (
                item for item in project.get("episodes", [])
                if item.get("stage") == stage
                and item.get("episode_number") == episode_number
                and item.get("status") in locked_statuses
            ),
            None,
        )

    @staticmethod
    def _resource_reference(
        project: dict[str, Any], stage: Stage, resource: dict[str, Any]
    ) -> dict[str, Any]:
        if stage in {"characters", "outline"}:
            return {
                "resource_type": stage,
                "resource_id": f"{project['id']}:{resource['batch_id']}",
                "batch_id": resource["batch_id"],
            }
        return {
            "resource_type": stage,
            "resource_id": str(resource["id"]),
            "version": resource.get("version"),
            "episode_number": resource.get("episode_number"),
        }

    def _expected_context(self, project: dict[str, Any]) -> dict[str, Any]:
        beats: dict[int, dict[str, Any]] = {}
        for item in project.get("episodes", []):
            if item.get("stage") != "beats" or item.get("status") not in {"locked", "approved"}:
                continue
            number = int(item["episode_number"])
            if number not in beats or int(item.get("version") or 0) >= int(beats[number].get("version") or 0):
                beats[number] = {
                    "resource_id": str(item["id"]),
                    "version": item.get("version"),
                    "fingerprint": item.get("fingerprint") or self._fingerprint(item.get("content", {})),
                }
        return {
            "revision": int(project.get("revision") or 0),
            "locked_brief_id": project.get("locked_brief_id"),
            "character_batch_id": project.get("character_batch_id"),
            "outline_batch_id": project.get("outline_batch_id"),
            "beats": beats,
        }

    def build_plan(
        self,
        *,
        project: dict[str, Any],
        target_stage: Stage,
        episode_start: int | None,
        episode_end: int | None,
        source_snapshot_id: str,
        confirmed_brief_payload: dict[str, Any] | None = None,
        assumptions: list[str] | None = None,
        rework_stage: Stage | None = None,
    ) -> CreativePlan:
        """Build the shortest safe baseline chain without performing writes."""
        if target_stage in {"beats", "script"} and (
            episode_start is None or episode_end is None or episode_start > episode_end
        ):
            raise WriterActionError("RESULT_VALIDATION_FAILED", "分集创作范围无效")
        if not source_snapshot_id:
            raise WriterActionError("RESULT_VALIDATION_FAILED", "缺少材料快照")

        context = self._expected_context(project)
        reused_resources: list[dict[str, Any]] = []
        steps: list[PlanStep] = []

        def add(
            step_key: str, stage: Stage, kind: str, *,
            episode_number: int | None = None, depends_on: list[str] | None = None,
            replaces_resource_id: str | None = None,
            invalidates_downstream: bool = False,
        ) -> PlanStep:
            step = PlanStep(
                step_key=step_key, stage=stage, kind=kind,
                episode_number=episode_number, depends_on=depends_on or [],
                expected_context=context, replaces_resource_id=replaces_resource_id,
                invalidates_downstream=invalidates_downstream,
            )
            steps.append(step)
            return step

        if rework_stage is not None:
            resource = self._locked_resource(project, rework_stage)
            if resource is None:
                raise WriterActionError("UPSTREAM_REQUIRED", "没有可重做的已锁定成果")
            reference = self._resource_reference(project, rework_stage, resource)
            add(
                f"{rework_stage}.persist", rework_stage, "persist",
                replaces_resource_id=reference["resource_id"],
                invalidates_downstream=True,
            )
            return CreativePlan(
                project_id=project.get("id"), target_stage=target_stage,
                episode_start=episode_start, episode_end=episode_end,
                source_snapshot_id=source_snapshot_id, assumptions=assumptions or [],
                steps=steps,
            )

        target_order = ["brief", "characters", "outline", "beats", "script"]
        target_index = target_order.index(target_stage)
        dependency: str | None = None
        script_planned_with_beats = False
        for stage in target_order[: target_index + 1]:
            if stage == "script" and script_planned_with_beats:
                continue
            if stage == "brief":
                resource = self._locked_resource(project, "brief")
                if resource is not None:
                    reused_resources.append(self._resource_reference(project, "brief", resource))
                    continue
                if confirmed_brief_payload is None:
                    add("brief.generate", "brief", "generate")
                    return CreativePlan(
                        project_id=project.get("id"), target_stage=target_stage,
                        episode_start=episode_start, episode_end=episode_end,
                        source_snapshot_id=source_snapshot_id, assumptions=assumptions or [],
                        reused_resources=reused_resources, requires_selection=True,
                        selection_stage="brief", steps=steps,
                    )
                persisted = add("brief.persist", "brief", "persist")
                dependency = persisted.step_key
                if target_stage != "brief":
                    dependency = add("brief.lock", "brief", "lock", depends_on=[dependency]).step_key
                continue
            if stage in {"characters", "outline"}:
                resource = self._locked_resource(project, stage)  # type: ignore[arg-type]
                if resource is not None:
                    reused_resources.append(self._resource_reference(project, stage, resource))  # type: ignore[arg-type]
                    continue
                generated = add(f"{stage}.generate", stage, "generate", depends_on=[dependency] if dependency else [])  # type: ignore[arg-type]
                dependency = generated.step_key
                if stage != target_stage:
                    dependency = add(f"{stage}.lock", stage, "lock", depends_on=[dependency]).step_key  # type: ignore[arg-type]
                continue
            for episode_number in range(int(episode_start or 1), int(episode_end or 0) + 1):
                resource = self._locked_resource(project, stage, episode_number)  # type: ignore[arg-type]
                if resource is not None:
                    reused_resources.append(self._resource_reference(project, stage, resource))  # type: ignore[arg-type]
                    if stage == "beats" and target_stage == "script":
                        add(
                            f"script.generate:{episode_number}", "script", "generate",
                            episode_number=episode_number,
                        )
                        script_planned_with_beats = True
                    continue
                generated = add(
                    f"{stage}.generate:{episode_number}", stage, "generate",
                    episode_number=episode_number,
                    depends_on=[dependency] if dependency else [],
                )  # type: ignore[arg-type]
                dependency_for_episode = generated.step_key
                if stage != target_stage:
                    dependency_for_episode = add(
                        f"{stage}.lock:{episode_number}", stage, "lock",
                        episode_number=episode_number, depends_on=[dependency_for_episode],
                    ).step_key  # type: ignore[arg-type]
                if stage == "beats" and target_stage == "script":
                    add(
                        f"script.generate:{episode_number}", "script", "generate",
                        episode_number=episode_number,
                        depends_on=[dependency_for_episode],
                    )
                    script_planned_with_beats = True
        return CreativePlan(
            project_id=project.get("id"), target_stage=target_stage,
            episode_start=episode_start, episode_end=episode_end,
            source_snapshot_id=source_snapshot_id, assumptions=assumptions or [],
            reused_resources=reused_resources, steps=steps,
        )

    def execute_step(
        self,
        *,
        plan: CreativePlan,
        step: PlanStep,
        actor: Principal,
        source_snapshot: dict[str, Any],
        payload: dict[str, Any],
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute one visible plan step through the shared creative application service."""
        if plan.project_id is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作计划缺少项目")
        operation_id = operation_id or f"writer-plan:{plan.source_snapshot_id}:{step.step_key}"
        try:
            task = self.creative_service.ensure_stage_task(plan.project_id, step.stage, actor)
            workflow_task_id = str(task["id"])
            if step.kind == "persist":
                if step.replaces_resource_id:
                    result = self.creative_service.create_revision(
                        project_id=plan.project_id, stage=step.stage,
                        replaces_resource_id=step.replaces_resource_id, payload=payload,
                        workflow_task_id=workflow_task_id, operation_id=operation_id,
                        expected_project_revision=int(step.expected_context["revision"]),
                        actor=actor, episode_number=step.episode_number,
                    )
                else:
                    result = self.creative_service.persist_draft(
                        project_id=plan.project_id, stage=step.stage, payload=payload,
                        workflow_task_id=workflow_task_id, operation_id=operation_id,
                        actor=actor, episode_number=step.episode_number,
                        reject_if_locked=True,
                    )
            elif step.kind == "generate":
                result = self.creative_service.start_generation(
                    project_id=plan.project_id, stage=step.stage, actor=actor,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    generation_instruction=str(payload.get("generation_instruction", "")),
                    source_snapshot_id=plan.source_snapshot_id,
                    source_snapshot=source_snapshot,
                    episode_number=step.episode_number,
                    episode_start=step.episode_number,
                    episode_end=step.episode_number,
                )
            else:
                resource_id = payload.get("resource_id")
                if not resource_id:
                    raise ValueError("lock step requires resource_id")
                result = self.creative_service.lock_resource(
                    project_id=plan.project_id, stage=step.stage,
                    resource_id=str(resource_id), workflow_task_id=workflow_task_id,
                    operation_id=operation_id, actor=actor,
                )
        except CreativeContextConflict as exc:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目权威版本已变化", retryable=False
            ) from exc
        except PermissionError as exc:
            raise WriterActionError(
                "TASK_ASSIGNED_TO_OTHER", "该创作阶段已由其他人员处理", retryable=False
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED", "创作结果校验失败", retryable=False
            ) from exc
        return {"resource_refs": [result] if "resource_id" in result else [], **result}

    def execute_project_action(
        self, action_id: str, actor: Principal
    ) -> dict[str, Any]:
        action = self.store.require_action(action_id, actor.user_id)
        if action.get("result"):
            return action["result"]
        payload = action["payload"]
        if action["action_type"] == "project.link":
            try:
                raw_project_id = action["project_id"]
                if type(raw_project_id) is not int or raw_project_id < 1:
                    raise ValueError
                project_id = raw_project_id
            except (KeyError, TypeError, ValueError) as exc:
                raise WriterActionError(
                    "RESULT_VALIDATION_FAILED", "关联项目 ID 缺失或无效"
                ) from exc
            project = self.creative_service.get_project(project_id)
            if not self.permission_service.can_read_project(actor, str(project_id)):
                raise WriterActionError("PROJECT_ACCESS_DENIED", "无权关联该项目")
        elif action["action_type"] == "project.create_original":
            if "project.create" not in actor.permissions:
                raise WriterActionError("PROJECT_CREATE_DENIED", "无权创建原创项目")
            creation_payload = validate_original_project_action_payload(payload)
            for reference in creation_payload["reference_refs"]:
                if not self.can_read_reference(
                    actor,
                    str(reference["resource_type"]),
                    int(reference["resource_id"]),
                ):
                    raise WriterActionError(
                        "REFERENCE_UNAVAILABLE", "参考已失效或无权读取"
                    )
            project = self.creative_service.create_original_project(
                creation_payload, actor,
                creation_request_id=(
                    f"writer-session:{action['session_id']}:action:{action_id}"
                ),
                can_read_reference=lambda resource_type, resource_id:
                    self.can_read_reference(actor, resource_type, resource_id),
            )
            project_id = int(project["id"])
        else:
            raise WriterActionError("ACTION_TYPE_INVALID", "该动作不是项目动作")
        session = self.store.get(int(action["session_id"]), actor.user_id)
        if session is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问")
        if session.get("project_id") != project_id:
            self.store.bind_project(
                int(action["session_id"]), project_id,
                expected_context_revision=int(
                    action["expected_context"]["session_revision"]
                ),
            )
        result = {
            "project_id": project_id, "name": project["name"],
            "creation_mode": project.get("creation_mode"),
            "open_url": self.project_url(project_id),
        }
        self.store.record_action_result(action_id, result)
        return result
