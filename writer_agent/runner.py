# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Durable, restart-safe execution for confirmed Writer Agent actions."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable
import uuid

from identity.schema import Principal
from runtime.job_manager import JobManager, JobNotFoundError

from .orchestrator import WriterProjectOrchestrator
from .schema import WriterActionError
from .store import WriterAgentStore


def partition_episode_range(
    start: int, end: int, batch_size: int = 10
) -> list[tuple[int, int]]:
    """Split an inclusive episode range into adjacent inclusive batches."""

    if start < 1 or end < start or batch_size < 1:
        raise ValueError("invalid episode range or batch size")
    return [
        (batch_start, min(end, batch_start + batch_size - 1))
        for batch_start in range(start, end + 1, batch_size)
    ]


class WriterActionRunner:
    """Run persisted action steps while preserving partial progress."""

    def __init__(
        self,
        *,
        store: WriterAgentStore,
        orchestrator: WriterProjectOrchestrator,
        job_manager: JobManager,
        principal_loader: Callable[[str], Principal | None],
        audit_service: Any | None = None,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.job_manager = job_manager
        self.principal_loader = principal_loader
        self.audit_service = audit_service
        self._poll_event = threading.Event()
        self._owner_id = f"writer-runner-{uuid.uuid4().hex}"
        self._lease_seconds = 30.0

    def _load_current_principal(self, action: dict[str, Any]) -> Principal:
        actor_user_id = str(action.get("actor_user_id") or "")
        current = self.principal_loader(actor_user_id)
        if current is None or current.user_id != actor_user_id:
            raise WriterActionError(
                "PROJECT_ACCESS_DENIED",
                "登录账户已停用或不再拥有项目创作权限",
                retryable=False,
            )
        return current

    def _execute_with_lease(
        self,
        action: dict[str, Any],
        step: dict[str, Any],
        actor: Principal,
    ) -> dict[str, Any]:
        heartbeat_stop = threading.Event()

        def renew() -> None:
            while not heartbeat_stop.wait(self._lease_seconds / 3):
                if not self.store.renew_step_claim(
                    str(action["id"]),
                    str(step["step_key"]),
                    owner_id=self._owner_id,
                    lease_seconds=self._lease_seconds,
                ):
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            return self.orchestrator.execute_action_step(
                action,
                step,
                actor,
                operation_id=step["operation_id"],
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1.0)

    def enqueue(self, action_id: str, actor: Principal) -> None:
        thread = threading.Thread(
            target=self.run, args=(action_id, actor), daemon=True
        )
        thread.start()

    def _audit_terminal(
        self,
        action: dict[str, Any],
        actor: Principal,
        *,
        error_code: str | None = None,
    ) -> None:
        if self.audit_service is None:
            return
        record = getattr(self.audit_service, "record", None)
        if not callable(record):
            return
        job_refs = [
            {"job_id": str(step["job_id"])}
            for step in self.store.list_steps(str(action["id"]))
            if step.get("job_id")
        ]
        try:
            record(
                actor=actor,
                action="writer.action.execution",
                target_type="writer_action",
                target_id=str(action["id"]),
                project_id=(
                    str(action["project_id"])
                    if action.get("project_id") is not None
                    else None
                ),
                before=None,
                after={
                    "action_id": action["id"],
                    "project_id": action.get("project_id"),
                    "stage": action.get("target_stage"),
                    "status": action.get("status"),
                    "job_refs": job_refs,
                    **({"error_code": error_code} if error_code else {}),
                },
                reason=None,
                result=("success" if action.get("status") == "succeeded" else "failure"),
            )
        except TypeError:
            return

    @staticmethod
    def _terminal_result(
        action: dict[str, Any], steps: list[dict[str, Any]], *, partial: bool
    ) -> dict[str, Any]:
        """Merge durable project metadata with a concise execution summary."""

        result = dict(action.get("result") or {})
        succeeded = [step for step in steps if step["status"] == "succeeded"]
        failed = [
            step for step in steps if step["status"] in {"failed", "blocked"}
        ]
        result.update(
            {
                "partial": partial,
                "succeeded_steps": [str(step["step_key"]) for step in succeeded],
                "failed_steps": [str(step["step_key"]) for step in failed],
                "executed_episodes": sorted(
                    {
                        int(step["episode_start"])
                        for step in succeeded
                        if step.get("episode_start") is not None
                    }
                ),
            }
        )
        return result

    @staticmethod
    def _job_status(job: Any) -> str:
        if isinstance(job, dict):
            return str(job.get("status") or "")
        return str(getattr(job, "status", "") or "")

    @staticmethod
    def _job_id(result: dict[str, Any]) -> str | None:
        job = result.get("job")
        value = result.get("job_id")
        if value is None and isinstance(job, dict):
            value = job.get("job_id") or job.get("id")
        return str(value) if value else None

    def _wait_for_job(
        self,
        action: dict[str, Any],
        step: dict[str, Any],
        actor: Principal,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = str(action["id"])
        job_id = self._job_id(result)
        if not job_id:
            return result
        assigned = self.store.assign_step_job(
            action_id,
            step["step_key"],
            job_id,
            execution_owner=self._owner_id,
        )
        if assigned.get("action_cancelling"):
            try:
                self.job_manager.stop(job_id)
            except JobNotFoundError:
                pass
        while True:
            try:
                job = (
                    self.job_manager.get_job(job_id)
                    if hasattr(self.job_manager, "get_job")
                    else self.job_manager.get(job_id)
                )
            except JobNotFoundError as exc:
                raise WriterActionError(
                    "JOB_NOT_FOUND",
                    "生成任务不存在或状态已损坏",
                    retryable=True,
                ) from exc
            status = self._job_status(job)
            if status in {"succeeded", "completed"}:
                recover = getattr(
                    self.orchestrator, "recover_action_step_result", None
                )
                if callable(recover):
                    return recover(action, step, actor)
                return result
            if status in {"failed", "cancelled"}:
                raise WriterActionError(
                    "GENERATION_FAILED" if status == "failed" else "CANCELLED",
                    "生成任务失败" if status == "failed" else "生成任务已取消",
                    retryable=status == "failed",
                )
            self._poll_event.wait(0.2)

    def _refresh_job_steps(
        self, action_id: str, actor: Principal
    ) -> dict[str, Any]:
        action = self.store.require_action(action_id, actor.user_id)
        for step in self.store.list_steps(action_id):
            if not step.get("job_id") or step["status"] in {
                "succeeded",
                "failed",
                "cancelled",
                "blocked",
            }:
                continue
            try:
                job = self.job_manager.get_job(str(step["job_id"]))
            except JobNotFoundError:
                self.store.fail_step(
                    action_id,
                    step["step_key"],
                    code="JOB_NOT_FOUND",
                    message="生成任务不存在或状态已损坏",
                    retryable=True,
                )
                continue
            status = self._job_status(job)
            if status in {"succeeded", "completed"}:
                recover = getattr(
                    self.orchestrator, "recover_action_step_result", None
                )
                try:
                    current_actor = self._load_current_principal(action)
                    result = (
                        recover(action, step, current_actor)
                        if callable(recover)
                        else {"resource_refs": step["resource_refs"]}
                    )
                    action = self.store.complete_step_with_context(
                        action_id, step["step_key"], result
                    )
                except WriterActionError as exc:
                    self.store.fail_step(
                        action_id,
                        step["step_key"],
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                        conflicting_action_id=exc.conflicting_action_id,
                    )
            elif status == "failed":
                self.store.fail_step(
                    action_id,
                    step["step_key"],
                    code="GENERATION_FAILED",
                    message="生成任务失败",
                    retryable=True,
                )
            elif status == "cancelled":
                self.store.cancel_step(action_id, step["step_key"])
        return self.store.require_action(action_id, actor.user_id)

    def _recover_unbound_operation_jobs(self, action_id: str) -> list[str]:
        """Bind jobs created before a crashed runner persisted the step job_id."""

        find_job_method = getattr(
            type(self.job_manager), "find_job_by_operation", None
        )
        if not callable(find_job_method):
            return []
        find_job = getattr(self.job_manager, "find_job_by_operation")
        recovered: list[str] = []
        action = self.store.require_action(action_id, None)
        for step in self.store.list_steps(action_id):
            if (
                step.get("job_id")
                or step["status"] not in {"pending", "running"}
                or ".generate" not in str(step.get("step_key") or "")
                or (
                    step["status"] == "running"
                    and float(step.get("lease_expires_at") or 0) > time.time()
                )
            ):
                continue
            job = find_job(str(step["operation_id"]))
            if not job:
                continue
            if (
                int(step.get("attempt") or 1) > 1
                and self._job_status(job) in {"failed", "cancelled"}
            ):
                continue
            job_id = str(job.get("job_id") or job.get("id") or "")
            if not job_id:
                continue
            self.store.assign_step_job(action_id, step["step_key"], job_id)
            recovered.append(job_id)
            if action.get("phase") == "cancelling" and self._job_status(job) not in {
                "succeeded",
                "completed",
                "failed",
                "cancelled",
            }:
                try:
                    self.job_manager.stop(job_id)
                except JobNotFoundError:
                    pass
        return recovered

    def run(self, action_id: str, actor: Principal) -> None:
        action = self.store.require_action(action_id, actor.user_id)
        if action["status"] == "queued":
            try:
                action = self.store.start_action(action_id)
            except WriterActionError:
                action = self.store.require_action(action_id, actor.user_id)
                if action["status"] != "running":
                    raise
        elif action["status"] != "running":
            raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不可执行")

        continued_project_action = bool(
            action["action_type"] == "project.create_original"
            and action.get("project_id") is not None
            and self.store.list_steps(action_id)
        )
        if (
            action["action_type"] in {"project.create_original", "project.link"}
            and not continued_project_action
        ):
            try:
                current_actor = self._load_current_principal(action)
                result = self.orchestrator.execute_project_action(
                    action_id, current_actor
                )
                continue_to_stage = (
                    action["action_type"] == "project.create_original"
                    and (action.get("payload") or {}).get("target_stage")
                    in {"brief", "characters", "outline", "beats", "script"}
                )
                if continue_to_stage:
                    self.store.record_action_result(action_id, result)
                    project_id = result.get("project_id")
                    project_context = result.get("project_context")
                    if not isinstance(project_context, dict):
                        read_context = getattr(
                            self.orchestrator, "project_context", None
                        )
                        project_context = (
                            read_context(project_id)
                            if callable(read_context)
                            else None
                        )
                    self.store.attach_project_to_action(
                        action_id,
                        project_id,
                        project_context=(
                            project_context
                            if isinstance(project_context, dict)
                            else {"project_revision": 0}
                        ),
                    )
                    self.orchestrator.prepare_action_plan(
                        action_id, current_actor
                    )
            except WriterActionError as exc:
                terminal = self.store.fail_action(
                    action_id,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    conflicting_action_id=exc.conflicting_action_id,
                )
                self._audit_terminal(terminal, actor, error_code=exc.code)
                return
            if continue_to_stage:
                action = self.store.require_action(action_id, actor.user_id)
            else:
                terminal = self.store.finish_action(
                    action_id, status="succeeded", result=result
                )
                self._audit_terminal(terminal, actor)
                return

        if action["action_type"] in {"creative.run_plan", "creative.lock"} and not self.store.list_steps(
            action_id
        ):
            try:
                current_actor = self._load_current_principal(action)
                self.orchestrator.prepare_action_plan(action_id, current_actor)
            except WriterActionError as exc:
                terminal = self.store.fail_action(
                    action_id,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    conflicting_action_id=exc.conflicting_action_id,
                )
                self._audit_terminal(terminal, actor, error_code=exc.code)
                return

        while True:
            action = self.store.require_action(action_id, actor.user_id)
            if action["status"] != "running":
                return
            if action.get("phase") == "cancelling":
                action = self.reconcile(action_id, actor)
                if action["status"] == "cancelled":
                    self._audit_terminal(action, actor)
                    return
                self._poll_event.wait(0.2)
                continue
            self._recover_unbound_operation_jobs(action_id)
            if self.store.active_job_ids(action_id):
                action = self._refresh_job_steps(action_id, actor)
            runnable = self.store.list_runnable_steps(action_id)
            if not runnable:
                if self.store.block_failed_dependents(action_id):
                    continue
                if self.store.active_job_ids(action_id):
                    self._poll_event.wait(0.2)
                    continue
                if self.store.has_running_steps(action_id):
                    self._poll_event.wait(0.2)
                    continue
                break
            pointer_steps = [
                step
                for step in runnable
                if ".generate" not in str(step.get("step_key") or "")
            ]
            if pointer_steps:
                scheduled = pointer_steps[:1]
            else:
                first_step = runnable[0]
                first_operation = str(first_step.get("step_key") or "").split(
                    ":", 1
                )[0]
                scheduled = [
                    step
                    for step in runnable
                    if step.get("stage") == first_step.get("stage")
                    and str(step.get("step_key") or "").split(":", 1)[0]
                    == first_operation
                    and step.get("episode_start") is not None
                ][:10]
                if not scheduled:
                    scheduled = [first_step]
            launched_job_ids: list[str] = []
            for step in scheduled:
                claimed = self.store.claim_runnable_step(
                    action_id,
                    step["step_key"],
                    owner_id=self._owner_id,
                    lease_seconds=self._lease_seconds,
                )
                if claimed is None:
                    continue
                step = claimed
                try:
                    current_actor = self._load_current_principal(action)
                    self.orchestrator.assert_expected_context(
                        action, current_actor
                    )
                    result = self._execute_with_lease(
                        action, step, current_actor
                    )
                    job_id = self._job_id(result)
                    if job_id:
                        assigned = self.store.assign_step_job(
                            action_id,
                            step["step_key"],
                            job_id,
                            execution_owner=self._owner_id,
                        )
                        launched_job_ids.append(job_id)
                        if assigned.get("action_cancelling"):
                            try:
                                self.job_manager.stop(job_id)
                            except JobNotFoundError:
                                pass
                    else:
                        action = self.store.complete_step_with_context(
                            action_id,
                            step["step_key"],
                            result,
                            execution_owner=self._owner_id,
                        )
                except WriterActionError as exc:
                    current = self.store.require_action(action_id, actor.user_id)
                    if (
                        exc.code == "CANCELLED"
                        and current.get("phase") == "cancelling"
                    ):
                        self._refresh_job_steps(action_id, actor)
                        continue
                    self.store.fail_step(
                        action_id,
                        step["step_key"],
                        code=exc.code,
                        message=str(exc),
                        retryable=exc.retryable,
                        conflicting_action_id=exc.conflicting_action_id,
                        execution_owner=self._owner_id,
                    )
            while launched_job_ids:
                current = self.store.require_action(action_id, actor.user_id)
                if current.get("phase") == "cancelling":
                    break
                action = self._refresh_job_steps(action_id, actor)
                active = set(self.store.active_job_ids(action_id))
                launched_job_ids = [
                    job_id for job_id in launched_job_ids if job_id in active
                ]
                if launched_job_ids:
                    self._poll_event.wait(0.2)

        steps = self.store.list_steps(action_id)
        action = self.store.require_action(action_id, actor.user_id)
        failed = [step for step in steps if step["status"] == "failed"]
        blocked = [step for step in steps if step["status"] == "blocked"]
        cancelled = [step for step in steps if step["status"] == "cancelled"]
        succeeded = [step for step in steps if step["status"] == "succeeded"]
        if failed or blocked:
            first = failed[0] if failed else blocked[0]
            terminal = self.store.fail_action(
                action_id,
                code=str(first.get("error_code") or "DEPENDENCY_FAILED"),
                message=str(first.get("error_message") or "依赖步骤失败"),
                retryable=any(bool(item.get("retryable")) for item in failed),
                conflicting_action_id=first.get("conflicting_action_id"),
                result=self._terminal_result(
                    action, steps, partial=bool(succeeded)
                ),
            )
            self._audit_terminal(
                terminal, actor, error_code=str(first.get("error_code") or "DEPENDENCY_FAILED")
            )
        elif cancelled:
            terminal = self.store.finish_action(
                action_id,
                status="cancelled",
                result=self._terminal_result(
                    action, steps, partial=bool(succeeded)
                ),
            )
            self._audit_terminal(terminal, actor)
        else:
            terminal = self.store.finish_action(
                action_id,
                status="succeeded",
                result=self._terminal_result(action, steps, partial=False),
            )
            self._audit_terminal(terminal, actor)

    def reconcile(self, action_id: str, actor: Principal) -> dict[str, Any]:
        self.store.require_action(action_id, actor.user_id)
        self._recover_unbound_operation_jobs(action_id)
        action = self._refresh_job_steps(action_id, actor)
        if action.get("phase") == "cancelling":
            self.store.cancel_expired_unbound_steps(action_id)
        if action.get("phase") == "cancelling" and not self.store.active_job_ids(
            action_id
        ) and not self.store.has_running_steps(action_id):
            terminal = self.store.finish_action(action_id, status="cancelled")
            self._audit_terminal(terminal, actor)
            return terminal
        return action

    def retry_failed(
        self,
        action_id: str,
        actor: Principal,
        *,
        retry_request_id: str | None = None,
    ) -> dict[str, Any]:
        source = self.store.require_retryable_action(action_id, actor.user_id)
        return self.store.create_retry_action(
            source,
            actor.user_id,
            retry_request_id=retry_request_id or str(source["id"]),
        )

    def retry_failed_once(
        self,
        action_id: str,
        actor: Principal,
        *,
        retry_request_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        source = self.store.require_retryable_action(action_id, actor.user_id)
        return self.store.create_retry_action_once(
            source,
            actor.user_id,
            retry_request_id=retry_request_id or str(source["id"]),
        )

    def cancel(self, action_id: str, actor: Principal) -> dict[str, Any]:
        action = self.store.require_action(action_id, actor.user_id)
        if action["status"] not in {"queued", "running"}:
            raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不可取消")
        if action.get("phase") != "cancelling":
            action = self.store.set_action_phase(
                action["id"], phase="cancelling"
            )
        active_jobs = self.store.active_job_ids(action_id)
        for job_id in active_jobs:
            try:
                self.job_manager.stop(job_id)
            except JobNotFoundError:
                continue
        if active_jobs or self.store.has_running_steps(action_id):
            return self.reconcile(action_id, actor)
        terminal = self.store.finish_action(action["id"], status="cancelled")
        self._audit_terminal(terminal, actor)
        return terminal

    def resume_active_actions(self) -> None:
        for action in self.store.list_active_actions():
            actor = self.principal_loader(action["actor_user_id"])
            if actor is not None:
                self.enqueue(action["id"], actor)
            else:
                self.store.fail_action(
                    action["id"],
                    code="PROJECT_ACCESS_DENIED",
                    message="登录账户已停用或不再拥有项目创作权限",
                    retryable=False,
                )
