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
import logging
from typing import Any

from creative.application_service import (
    CreativeApplicationService,
    CreativeContextConflict,
    CreativeProjectAccessDenied,
    CreativeTaskOccupied,
)
from creative.market_reference_service import MarketReferenceService
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobConflictError
from workflow.repository import WorkflowRepository

from .action_service import validate_original_project_action_payload
from .schema import CreativePlan, PlanStep, Stage, WriterActionError
from .store import WriterAgentStore


logger = logging.getLogger(__name__)


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

    @staticmethod
    def _expected_context(
        project: dict[str, Any], episode_number: int | None = None
    ) -> dict[str, Any]:
        return CreativeApplicationService.plan_expected_context(
            project, episode_number
        )

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
        episode_scoped = target_stage in {"beats", "script"} or rework_stage in {
            "beats", "script",
        }
        target_episode_count = int(project.get("target_episode_count") or 0)
        if episode_scoped and (
            episode_start is None
            or episode_end is None
            or not 1 <= episode_start <= episode_end <= target_episode_count
        ):
            raise WriterActionError("RESULT_VALIDATION_FAILED", "分集创作范围无效")
        if not source_snapshot_id:
            raise WriterActionError("RESULT_VALIDATION_FAILED", "缺少材料快照")

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
                expected_context=self._expected_context(project, episode_number),
                replaces_resource_id=replaces_resource_id,
                invalidates_downstream=invalidates_downstream,
            )
            steps.append(step)
            return step

        if rework_stage is not None:
            if rework_stage in {"beats", "script"}:
                for episode_number in range(episode_start or 1, (episode_end or 0) + 1):
                    resource = CreativeApplicationService.highest_locked_episode_resource(
                        project, rework_stage, episode_number
                    )
                    if resource is None:
                        raise WriterActionError(
                            "UPSTREAM_REQUIRED", "没有可重做的已锁定成果"
                        )
                    reference = self._resource_reference(
                        project, rework_stage, resource
                    )
                    add(
                        f"{rework_stage}.persist:{episode_number}",
                        rework_stage,
                        "persist",
                        episode_number=episode_number,
                        replaces_resource_id=reference["resource_id"],
                        invalidates_downstream=True,
                    )
            else:
                resource = self._locked_resource(project, rework_stage)
                if resource is None:
                    raise WriterActionError(
                        "UPSTREAM_REQUIRED", "没有可重做的已锁定成果"
                    )
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
        for stage in target_order[: min(target_index, 2) + 1]:
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
        if target_index >= 3:
            from .runner import partition_episode_range

            for batch_start, batch_end in partition_episode_range(
                int(episode_start or 1), int(episode_end or 0), batch_size=10
            ):
                for episode_number in range(batch_start, batch_end + 1):
                    beats = CreativeApplicationService.compatible_locked_episode_resource(
                        project, "beats", episode_number
                    )
                    if beats is not None:
                        reused_resources.append(
                            self._resource_reference(project, "beats", beats)
                        )
                        if target_stage == "script":
                            script = CreativeApplicationService.compatible_locked_episode_resource(
                                project, "script", episode_number
                            )
                            if script is not None:
                                reused_resources.append(
                                    self._resource_reference(project, "script", script)
                                )
                            else:
                                add(
                                    f"script.generate:{episode_number}",
                                    "script",
                                    "generate",
                                    episode_number=episode_number,
                                )
                        continue

                    generated = add(
                        f"beats.generate:{episode_number}", "beats", "generate",
                        episode_number=episode_number,
                        depends_on=[dependency] if dependency else [],
                    )
                    if target_stage == "script":
                        locked = add(
                            f"beats.lock:{episode_number}", "beats", "lock",
                            episode_number=episode_number,
                            depends_on=[generated.step_key],
                        )
                        add(
                            f"script.generate:{episode_number}", "script", "generate",
                            episode_number=episode_number,
                            depends_on=[locked.step_key],
                        )
        return CreativePlan(
            project_id=project.get("id"), target_stage=target_stage,
            episode_start=episode_start, episode_end=episode_end,
            source_snapshot_id=source_snapshot_id, assumptions=assumptions or [],
            reused_resources=reused_resources, steps=steps,
        )

    def _original_brief_generation_context(
        self, project_id: int, actor: Principal
    ) -> dict[str, Any]:
        """Freeze the current original seed after revalidating its market references."""
        project = self.creative_service.get_project(project_id)
        if project is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "原创项目不存在", retryable=False)
        if project.get("creation_mode") != "original":
            return {}
        repository = self.creative_service.repository
        seed = repository.get_current_seed(project_id)
        if seed is None:
            raise WriterActionError(
                "ORIGINAL_SEED_UNAVAILABLE", "原创项目缺少种子版本", retryable=False
            )
        reference_refs = seed.get("reference_refs")
        if not isinstance(reference_refs, list) or any(
            not isinstance(reference, dict) for reference in reference_refs
        ):
            raise WriterActionError(
                "ORIGINAL_REFERENCE_INVALID", "原创种子参考无效", retryable=False
            )
        try:
            bundle = MarketReferenceService(repository).resolve(
                reference_refs,
                can_read=lambda kind, identifier: self.can_read_reference(
                    actor, kind, identifier
                ),
            )
        except PermissionError as exc:
            raise WriterActionError(
                "REFERENCE_UNAVAILABLE", "参考已失效或无权读取", retryable=False
            ) from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise WriterActionError(
                "ORIGINAL_REFERENCE_INVALID", "原创种子参考无效", retryable=False
            ) from exc
        try:
            frozen_seed = repository.freeze_seed(int(seed["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise WriterActionError(
                "ORIGINAL_SEED_UNAVAILABLE", "原创种子版本无效", retryable=False
            ) from exc
        return {
            "seed_id": frozen_seed["id"],
            "market_context": bundle.context,
            "blocked_terms": bundle.blocked_terms,
            "reference_snapshot": [
                {**item, "captured_at": frozen_seed["locked_at"]}
                for item in bundle.source_refs
            ],
        }

    def execute_step(
        self,
        *,
        plan: CreativePlan,
        step: PlanStep,
        actor: Principal,
        source_snapshot: dict[str, Any],
        payload: dict[str, Any],
        operation_id: str | None = None,
        retry_failed_operation: bool = False,
    ) -> dict[str, Any]:
        """Execute one visible plan step through the shared creative application service."""
        if plan.project_id is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作计划缺少项目")
        operation_id = operation_id or f"writer-plan:{plan.source_snapshot_id}:{step.step_key}"
        try:
            task = self.creative_service.ensure_stage_task(
                plan.project_id,
                step.stage,
                actor,
                expected_context=step.expected_context,
                episode_number=step.episode_number,
            )
            workflow_task_id = str(task["id"])
            if step.kind == "persist":
                if step.replaces_resource_id:
                    result = self.creative_service.create_revision(
                        project_id=plan.project_id, stage=step.stage,
                        replaces_resource_id=step.replaces_resource_id, payload=payload,
                        workflow_task_id=workflow_task_id, operation_id=operation_id,
                        expected_project_revision=int(step.expected_context["revision"]),
                        actor=actor, episode_number=step.episode_number,
                        expected_context=step.expected_context,
                    )
                else:
                    result = self.creative_service.persist_draft(
                        project_id=plan.project_id, stage=step.stage, payload=payload,
                        workflow_task_id=workflow_task_id, operation_id=operation_id,
                        actor=actor, episode_number=step.episode_number,
                        reject_if_locked=True,
                        expected_context=step.expected_context,
                    )
            elif step.kind == "generate":
                original_brief_context = (
                    self._original_brief_generation_context(plan.project_id, actor)
                    if step.stage == "brief"
                    else {}
                )
                result = self.creative_service.start_generation(
                    project_id=plan.project_id, stage=step.stage, actor=actor,
                    workflow_task_id=workflow_task_id, operation_id=operation_id,
                    generation_instruction=str(payload.get("generation_instruction", "")),
                    source_snapshot_id=plan.source_snapshot_id,
                    source_snapshot=source_snapshot,
                    episode_number=step.episode_number,
                    episode_start=step.episode_number,
                    episode_end=step.episode_number,
                    expected_context=step.expected_context,
                    retry_failed_operation=retry_failed_operation,
                    count=1 if original_brief_context else None,
                    **original_brief_context,
                )
            else:
                resource_id = payload.get("resource_id")
                if not resource_id:
                    raise ValueError("lock step requires resource_id")
                if task.get("status") == "assigned":
                    self.creative_service.workflow_repository.start_task(
                        workflow_task_id, actor
                    )
                result = self.creative_service.lock_resource(
                    project_id=plan.project_id, stage=step.stage,
                    resource_id=str(resource_id), workflow_task_id=workflow_task_id,
                    operation_id=operation_id, actor=actor,
                    expected_context=step.expected_context,
                    episode_number=step.episode_number,
                )
            if not isinstance(result, dict):
                result = {
                    "job_id": str(result.job_id),
                    "status": str(result.status),
                    "message": str(getattr(result, "message", "") or ""),
                }
        except CreativeContextConflict as exc:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目权威版本已变化", retryable=False
            ) from exc
        except CreativeProjectAccessDenied as exc:
            raise WriterActionError(
                "PROJECT_ACCESS_DENIED", "无权创作该项目", retryable=False
            ) from exc
        except CreativeTaskOccupied as exc:
            raise WriterActionError(
                "TASK_ASSIGNED_TO_OTHER", "该创作阶段已由其他人员处理", retryable=False
            ) from exc
        except JobConflictError as exc:
            if exc.kind == "operation_payload_mismatch":
                raise WriterActionError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "幂等操作已用于不同创作请求",
                    retryable=False,
                ) from exc
            if exc.kind == "creative_scope_collision":
                conflicting_action_id = self.store.find_action_id_by_operation(
                    str(exc.conflicting_operation_id or "")
                )
                raise WriterActionError(
                    "ACTIVE_ACTION_CONFLICT",
                    "目标范围已有进行中的创作动作",
                    retryable=True,
                    conflicting_action_id=conflicting_action_id,
                ) from exc
            raise WriterActionError(
                "CREATIVE_OPERATION_FAILED", "创作操作暂时不可用", retryable=True
            ) from exc
        except WriterActionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED", "创作结果校验失败", retryable=False
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected writer plan operation failure: project=%s step=%s",
                plan.project_id,
                step.step_key,
            )
            raise WriterActionError(
                "CREATIVE_OPERATION_FAILED", "创作操作暂时不可用", retryable=True
            ) from exc
        dependencies = {item.step_key: set(item.depends_on) for item in plan.steps}

        def depends_on_completed(step_key: str, seen: set[str] | None = None) -> bool:
            seen = seen or set()
            if step_key in seen:
                return False
            seen.add(step_key)
            direct = dependencies.get(step_key, set())
            return step.step_key in direct or any(
                depends_on_completed(parent, seen) for parent in direct
            )

        advanced_contexts: dict[str, dict[str, Any]] = {}
        try:
            pending_contexts: list[tuple[PlanStep, dict[str, Any]]] = []
            for candidate in plan.steps:
                if not depends_on_completed(candidate.step_key):
                    continue
                current_context = self.creative_service.advance_plan_context(
                    plan.project_id,
                    candidate.expected_context,
                    stage=step.stage,
                    kind=step.kind,
                    resource_id=str(payload.get("resource_id"))
                    if step.kind == "lock" and payload.get("resource_id") is not None
                    else None,
                    episode_number=candidate.episode_number,
                )
                if isinstance(current_context, dict):
                    pending_contexts.append((candidate, current_context))
            for candidate, current_context in pending_contexts:
                candidate.expected_context = current_context
                advanced_contexts[candidate.step_key] = current_context
        except CreativeContextConflict as exc:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目权威版本已变化", retryable=False
            ) from exc
        except Exception as exc:
            logger.exception(
                "Failed to advance writer plan authority: project=%s step=%s",
                plan.project_id,
                step.step_key,
            )
            raise WriterActionError(
                "CREATIVE_OPERATION_FAILED", "创作操作暂时不可用", retryable=True
            ) from exc
        return {
            "resource_refs": [result] if "resource_id" in result else [],
            **result,
            "advanced_contexts": advanced_contexts,
        }

    @staticmethod
    def _plan_context_from_action(
        action: dict[str, Any], step_key: str, episode_number: int | None
    ) -> dict[str, Any]:
        context = dict(action.get("expected_context") or {})
        authority_refs = context.get("authority_refs")
        if isinstance(authority_refs, dict):
            advanced = authority_refs.get(step_key)
            if isinstance(advanced, dict):
                return dict(advanced)
            if "revision" in authority_refs:
                return dict(authority_refs)
        locked_brief = context.get("locked_brief") or {}
        locked_characters = context.get("locked_characters") or {}
        locked_outline = context.get("locked_outline") or {}
        locked_beats = context.get("locked_beats") or []
        locked_beat = next(
            (
                item
                for item in locked_beats
                if int(item.get("episode_number") or 0)
                == int(episode_number or 0)
            ),
            None,
        )
        beats = (
            {
                "resource_id": str(locked_beat["id"]),
                "version": int(locked_beat.get("version") or 0),
                "fingerprint": CreativeApplicationService._resource_fingerprint(
                    locked_beat.get("content") or {}
                ),
            }
            if locked_beat is not None
            else None
        )
        return {
            "revision": int(
                context.get("project_revision", context.get("revision", 0)) or 0
            ),
            "locked_brief_id": context.get("locked_brief_id")
            or locked_brief.get("id"),
            "character_batch_id": context.get("character_batch_id")
            or locked_characters.get("batch_id"),
            "outline_batch_id": context.get("outline_batch_id")
            or locked_outline.get("batch_id"),
            "beats": beats,
        }

    def assert_expected_context(
        self, action: dict[str, Any], actor: Principal
    ) -> None:
        """Reject an externally advanced project before a runner side effect."""

        del actor
        project_id = action.get("project_id")
        if type(project_id) is not int or project_id < 1:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作计划缺少项目")
        expected = action.get("expected_context") or {}
        expected_revision = int(
            expected.get("project_revision", expected.get("revision", 0)) or 0
        )
        current = self.creative_service.get_project(project_id)
        if int(current.get("revision") or 0) != expected_revision:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目权威版本已变化", retryable=False
            )

    def execute_action_step(
        self,
        action: dict[str, Any],
        step: dict[str, Any],
        actor: Principal,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        """Adapt a persisted action/step record to the typed production executor."""

        if action.get("action_type") not in {
            "creative.run_plan",
            "creative.lock",
            "project.create_original",
        }:
            raise WriterActionError(
                "ACTION_STATE_INVALID", "动作不是可执行创作计划"
            )
        persisted_steps = self.store.list_steps(str(action["id"]))
        action_payload = dict(action.get("payload") or {})
        action_authority = dict(action.get("expected_context") or {})

        def rework_resource_id(
            stage: str, episode_number: int | None
        ) -> str | None:
            if action_payload.get("rework_stage") != stage:
                return None
            supplied = action_payload.get("replaces_resource_id")
            if supplied is not None:
                return str(supplied)
            if stage == "brief":
                locked = action_authority.get("locked_brief") or {}
                return str(locked["id"]) if locked.get("id") is not None else None
            if stage in {"characters", "outline"}:
                locked = action_authority.get(f"locked_{stage}") or {}
                batch_id = locked.get("batch_id")
                project_id = action_authority.get("project_id")
                return (
                    f"{project_id}:{batch_id}"
                    if project_id is not None and batch_id
                    else None
                )
            for item in action_authority.get(f"locked_{stage}") or []:
                if int(item.get("episode_number") or 0) == int(
                    episode_number or 0
                ):
                    return str(item["id"])
            return None

        plan_steps: list[PlanStep] = []
        for persisted in persisted_steps:
            operation = str(persisted["step_key"]).split(":", 1)[0]
            kind = operation.rsplit(".", 1)[-1]
            if kind not in {"generate", "persist", "lock"}:
                raise WriterActionError(
                    "RESULT_VALIDATION_FAILED", "创作步骤类型无效"
                )
            episode_number = persisted.get("episode_start")
            plan_steps.append(
                PlanStep(
                    step_key=str(persisted["step_key"]),
                    stage=persisted["stage"],
                    kind=kind,
                    episode_number=episode_number,
                    depends_on=list(persisted.get("depends_on") or []),
                    expected_context=self._plan_context_from_action(
                        action, str(persisted["step_key"]), episode_number
                    ),
                    replaces_resource_id=(
                        (action.get("payload") or {})
                        .get("step_payloads", {})
                        .get(str(persisted["step_key"]), {})
                        .get("replaces_resource_id")
                        or rework_resource_id(
                            str(persisted["stage"]), episode_number
                        )
                    ),
                )
            )
        selected = next(
            (item for item in plan_steps if item.step_key == step["step_key"]),
            None,
        )
        if selected is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作步骤不存在")
        payload = dict(action.get("payload") or {})
        step_payloads = payload.get("step_payloads")
        if isinstance(step_payloads, dict) and isinstance(
            step_payloads.get(selected.step_key), dict
        ):
            payload = dict(step_payloads[selected.step_key])
        if selected.kind == "lock":
            explicit_resource_id = payload.get("resource_id")
            if explicit_resource_id is None:
                resource_ids = payload.get("resource_ids")
                if isinstance(resource_ids, list) and len(resource_ids) == 1:
                    explicit_resource_id = resource_ids[0]
            dependencies = {
                str(item["step_key"]): item
                for item in persisted_steps
                if str(item["step_key"]) in selected.depends_on
            }
            resource_ids = {
                str(reference["resource_id"])
                for dependency in dependencies.values()
                if dependency.get("status") == "succeeded"
                and dependency.get("stage") == selected.stage
                and dependency.get("episode_start") == selected.episode_number
                for reference in dependency.get("resource_refs") or []
                if isinstance(reference, dict) and reference.get("resource_id")
            }
            if action.get("action_type") == "creative.lock":
                if explicit_resource_id is None:
                    raise WriterActionError(
                        "RESULT_VALIDATION_FAILED",
                        "独立锁定动作缺少唯一成果",
                        retryable=False,
                    )
                resolved_resource_id = str(explicit_resource_id)
            else:
                if len(resource_ids) != 1:
                    raise WriterActionError(
                        "RESULT_VALIDATION_FAILED",
                        "锁定步骤未找到唯一的上游成果",
                        retryable=False,
                    )
                resolved_resource_id = resource_ids.pop()
                if (
                    explicit_resource_id is not None
                    and str(explicit_resource_id) != resolved_resource_id
                ):
                    raise WriterActionError(
                        "RESULT_VALIDATION_FAILED",
                        "锁定步骤指定的成果与唯一上游成果不一致",
                        retryable=False,
                    )
            payload["resource_id"] = resolved_resource_id
        snapshot_id = str(action.get("source_snapshot_id") or "")
        if not snapshot_id:
            raise WriterActionError("REFERENCE_UNAVAILABLE", "材料快照不存在")
        snapshot_record = self.store.require_source_snapshot(snapshot_id)
        plan = CreativePlan(
            project_id=action.get("project_id"),
            target_stage=(action.get("payload") or {}).get("target_stage")
            or selected.stage,
            episode_start=(action.get("payload") or {}).get("episode_start"),
            episode_end=(action.get("payload") or {}).get("episode_end"),
            source_snapshot_id=snapshot_id,
            assumptions=list((action.get("payload") or {}).get("assumptions") or []),
            steps=plan_steps,
        )
        result = self.execute_step(
            plan=plan,
            step=selected,
            actor=actor,
            source_snapshot=snapshot_record["snapshot"],
            payload=payload,
            operation_id=operation_id,
            retry_failed_operation=int(step.get("attempt") or 1) > 1,
        )
        advanced = result.get("advanced_contexts")
        if isinstance(advanced, dict) and advanced:
            revisions = [
                int(item.get("revision") or 0)
                for item in advanced.values()
                if isinstance(item, dict)
            ]
            if revisions:
                result["authority_revision"] = max(revisions)
                result["authority_refs"] = advanced
        return result

    def prepare_action_plan(
        self, action_id: str, actor: Principal
    ) -> list[dict[str, Any]]:
        """Materialize a confirmed target-stage plan into durable runner steps."""

        action = self.store.require_action(action_id, actor.user_id)
        if action.get("action_type") not in {
            "creative.run_plan",
            "creative.lock",
            "project.create_original",
        }:
            raise WriterActionError(
                "ACTION_TYPE_INVALID", "该动作不是目标阶段创作计划"
            )
        project_id = action.get("project_id")
        if type(project_id) is not int or project_id < 1:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作计划缺少项目")
        payload = dict(action.get("payload") or {})
        target_stage = payload.get("target_stage")
        if target_stage not in {"brief", "characters", "outline", "beats", "script"}:
            raise WriterActionError("RESULT_VALIDATION_FAILED", "目标创作阶段无效")
        project = self.creative_service.get_project(project_id)
        if action.get("action_type") == "creative.lock":
            resource_id = payload.get("resource_id")
            if resource_id is None:
                resource_ids = payload.get("resource_ids")
                if isinstance(resource_ids, list) and len(resource_ids) == 1:
                    resource_id = resource_ids[0]
            if resource_id is None:
                raise WriterActionError(
                    "RESULT_VALIDATION_FAILED", "独立锁定动作缺少唯一成果"
                )
            episode_number: int | None = None
            if target_stage in {"beats", "script"}:
                episode_start = payload.get("episode_start")
                episode_end = payload.get("episode_end")
                if (
                    type(episode_start) is not int
                    or type(episode_end) is not int
                    or not 1 <= episode_start <= 100
                    or episode_start != episode_end
                ):
                    raise WriterActionError(
                        "RESULT_VALIDATION_FAILED",
                        "分集锁定动作必须且只能指定一集",
                    )
                episode_number = episode_start
            self.store.upsert_step(
                action_id=action_id,
                step_key=(
                    f"{target_stage}.lock:{episode_number}"
                    if episode_number is not None
                    else f"{target_stage}.lock"
                ),
                stage=target_stage,
                episode_start=episode_number,
                episode_end=episode_number,
                status="pending",
                operation_id=(
                    f"{action_id}:{target_stage}.lock"
                    + (f":{episode_number}" if episode_number is not None else "")
                ),
                depends_on=[],
                attempt=1,
                step_order=1,
            )
            return self.store.list_steps(action_id)
        confirmed_brief = payload.get("confirmed_brief_payload")
        if not isinstance(confirmed_brief, dict):
            confirmed_brief = None
        rework_stage = payload.get("rework_stage")
        if rework_stage not in {None, "brief", "characters", "outline", "beats", "script"}:
            raise WriterActionError("RESULT_VALIDATION_FAILED", "重做阶段无效")
        plan = self.build_plan(
            project=project,
            target_stage=target_stage,
            episode_start=payload.get("episode_start"),
            episode_end=payload.get("episode_end"),
            source_snapshot_id=str(action.get("source_snapshot_id") or ""),
            confirmed_brief_payload=confirmed_brief,
            assumptions=list(payload.get("assumptions") or []),
            rework_stage=rework_stage,
        )
        for order, step in enumerate(plan.steps, start=1):
            self.store.upsert_step(
                action_id=action_id,
                step_key=step.step_key,
                stage=step.stage,
                episode_start=step.episode_number,
                episode_end=step.episode_number,
                status="pending",
                operation_id=f"{action_id}:{step.step_key}",
                depends_on=step.depends_on,
                attempt=1,
                step_order=order,
            )
        return self.store.list_steps(action_id)

    def project_context(self, project_id: int) -> dict[str, Any]:
        """Read the current authority context without exposing it in action results."""

        return CreativeApplicationService.plan_expected_context(
            self.creative_service.get_project(project_id)
        )

    def recover_action_step_result(
        self,
        action: dict[str, Any],
        step: dict[str, Any],
        actor: Principal,
    ) -> dict[str, Any]:
        """Rehydrate a completed worker result and downstream authority."""

        del actor
        project_id = action.get("project_id")
        if type(project_id) is not int or project_id < 1:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作计划缺少项目")
        try:
            result = self.creative_service.recover_generation_result(
                operation_id=str(step["operation_id"]),
                project_id=project_id,
                stage=str(step["stage"]),
            )
            persisted = self.store.list_steps(str(action["id"]))
            dependencies = {
                str(item["step_key"]): set(item.get("depends_on") or [])
                for item in persisted
            }

            def depends_on_recovered(
                step_key: str, seen: set[str] | None = None
            ) -> bool:
                seen = seen or set()
                if step_key in seen:
                    return False
                seen.add(step_key)
                direct = dependencies.get(step_key, set())
                return str(step["step_key"]) in direct or any(
                    depends_on_recovered(parent, seen) for parent in direct
                )

            advanced: dict[str, dict[str, Any]] = {}
            for candidate in persisted:
                candidate_key = str(candidate["step_key"])
                if not depends_on_recovered(candidate_key):
                    continue
                episode_number = candidate.get("episode_start")
                context = self.creative_service.advance_plan_context(
                    project_id,
                    self._plan_context_from_action(
                        action, candidate_key, episode_number
                    ),
                    stage=str(step["stage"]),
                    kind="generate",
                    resource_id=None,
                    episode_number=episode_number,
                )
                if isinstance(context, dict):
                    advanced[candidate_key] = context
            if advanced:
                result["authority_refs"] = advanced
                result["authority_revision"] = max(
                    int(context.get("revision") or 0)
                    for context in advanced.values()
                )
            return result
        except WriterActionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED",
                "已完成生成任务的结果无法恢复",
                retryable=False,
            ) from exc
        except Exception as exc:
            raise WriterActionError(
                "CREATIVE_OPERATION_FAILED",
                "恢复生成结果暂时不可用",
                retryable=True,
            ) from exc

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
            if not (
                actor.is_system_admin or "project.create" in actor.permissions
            ):
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
