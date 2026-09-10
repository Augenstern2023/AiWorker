# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .schema import WriterActionError
from .store import WriterAgentStore


_ACTIVE_ACTION_STATUSES = frozenset({"queued", "running"})
_PROJECT_MUTATION_ACTIONS = frozenset(
    {"project.create_original", "creative.run_plan", "creative.lock"}
)


def _action_is_durable_noop(action: dict[str, Any]) -> bool:
    return (
        action.get("status") in {"cancelled", "failed", "expired"}
        and action.get("started_at") is None
    ) or (
        action.get("status") == "failed"
        and action.get("error_code") == "ROLLBACK_PREPARE_FAILED"
    )


class RollbackService:
    """Coordinate compensating rollback across Writer, Creative and Workflow."""

    def __init__(
        self,
        store: WriterAgentStore,
        creative_repository: Any,
        workflow_repository: Any,
        *,
        permission_service: Any | None = None,
        action_runner: Any | None = None,
    ) -> None:
        self.store = store
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.permission_service = permission_service
        self.action_runner = action_runner
        self._mutation_lease_seconds = 120.0

    def _assert_current_project_write_authority(
        self,
        undos: list[dict[str, Any]],
        actor: Any | None,
        *,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        project_ids = {
            int(project_id)
            for undo in undos
            if (
                project_id := (undo.get("postcondition") or {}).get("project_id")
            )
            is not None
        }
        project_create_without_id = False
        for action in actions or []:
            if action.get("action_type") not in _PROJECT_MUTATION_ACTIONS:
                continue
            if action.get("status") == "pending" or _action_is_durable_noop(action):
                continue
            action_project_id = action.get("project_id")
            if action_project_id is None and isinstance(action.get("result"), dict):
                action_project_id = action["result"].get("project_id")
            if action_project_id is not None:
                project_ids.add(int(action_project_id))
            elif action.get("action_type") == "project.create_original":
                project_create_without_id = True
        if project_create_without_id and not (
            actor is not None
            and (
                bool(getattr(actor, "is_system_admin", False))
                or "project.create" in getattr(actor, "permissions", frozenset())
            )
        ):
            raise WriterActionError(
                "ROLLBACK_PERMISSION_DENIED", "当前账号已无权修改受影响项目"
            )
        if not project_ids:
            return
        if (
            actor is None
            or self.permission_service is None
            or self.workflow_repository is None
        ):
            raise WriterActionError(
                "ROLLBACK_PERMISSION_DENIED", "当前账号已无权修改受影响项目"
            )
        for project_id in project_ids:
            try:
                scope = getattr(
                    self.creative_repository, "workflow_project_id", None
                )
                project_key = (
                    scope(project_id) if callable(scope) else str(project_id)
                )
                active_member = self.workflow_repository.is_active_project_member(
                    project_key, actor
                )
                allowed = self.permission_service.can_create_project_content(
                    actor, project_key, active_member=active_member
                )
            except (AttributeError, OSError, TypeError, ValueError):
                allowed = False
            if not allowed:
                raise WriterActionError(
                    "ROLLBACK_PERMISSION_DENIED",
                    "当前账号已无权修改受影响项目",
                )

    def prepare_action_undo(
        self, action: dict[str, Any], actor: Any
    ) -> dict[str, Any] | None:
        del actor
        checkpoint_id = str(action.get("turn_checkpoint_id") or "")
        if not checkpoint_id:
            return None
        try:
            return self.store.require_action_undo(str(action["id"]))
        except WriterActionError as exc:
            if exc.code != "TURN_ROLLBACK_UNSUPPORTED":
                raise

        action_type = str(action.get("action_type") or "")
        project_id = action.get("project_id") or (
            (action.get("result") or {}).get("project_id")
            if isinstance(action.get("result"), dict)
            else None
        )
        if action_type == "project.create_original":
            if action.get("retry_of_action_id"):
                root_action_id = str(
                    action.get("root_action_id")
                    or action.get("retry_of_action_id")
                )
                root_undo = self.store.require_action_undo(root_action_id)
                root_ownership = (root_undo.get("preimage") or {}).get(
                    "ownership"
                ) or {}
                if (
                    (root_undo.get("preimage") or {}).get("kind")
                    != "new_project"
                    or int(root_ownership.get("project_id") or 0)
                    != int(project_id or 0)
                ):
                    raise WriterActionError(
                        "TURN_ROLLBACK_UNSUPPORTED",
                        "创建重试缺少原始项目撤销记录",
                    )
                preimage = {
                    "kind": "session_only",
                    "root_create_action_id": root_action_id,
                }
            else:
                preimage = {"kind": "new_project"}
            workflow_preimage = {}
            undo_project_id = None
        elif action_type in _PROJECT_MUTATION_ACTIONS:
            if type(project_id) is not int or project_id < 1:
                raise WriterActionError(
                    "PROJECT_NOT_AVAILABLE", "创作动作缺少有效项目"
                )
            preimage = {
                "kind": "existing_project",
                "creative": self.creative_repository.export_writer_rollback_state(
                    project_id
                ),
            }
            workflow_preimage = (
                self.workflow_repository.export_writer_rollback_state(
                    str(project_id)
                )
            )
            undo_project_id = project_id
        else:
            preimage = {"kind": "session_only"}
            workflow_preimage = {}
            undo_project_id = None
        return self.store.prepare_action_undo(
            str(action["id"]),
            checkpoint_id,
            project_id=undo_project_id,
            preimage=preimage,
            workflow_preimage=workflow_preimage,
        )

    def capture_new_project_ownership(
        self, action: dict[str, Any], actor: Any
    ) -> dict[str, Any]:
        """Freeze the rows that existed before a new project became visible."""

        del actor
        project_id = action.get("project_id")
        if (
            action.get("action_type") != "project.create_original"
            or type(project_id) is not int
            or project_id < 1
        ):
            raise WriterActionError(
                "PROJECT_NOT_AVAILABLE", "新建项目动作缺少有效项目"
            )
        try:
            creative_fingerprint = (
                self.creative_repository.writer_project_external_use_fingerprint(
                    project_id, str(action["id"])
                )
            )
            export_workflow_state = getattr(
                self.workflow_repository,
                "export_writer_project_external_use_state",
                None,
            )
            workflow_state = (
                export_workflow_state(str(project_id))
                if callable(export_workflow_state)
                else None
            )
            workflow_fingerprint = (
                self.workflow_repository.writer_project_external_use_fingerprint(
                    str(project_id)
                )
            )
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise WriterActionError(
                "ROLLBACK_OWNERSHIP_CAPTURE_FAILED",
                "无法记录新建项目的独占状态",
                retryable=True,
            ) from exc
        return self.store.record_new_project_undo_ownership(
            str(action["id"]),
            project_id=project_id,
            creative_external_fingerprint=creative_fingerprint,
            workflow_external_fingerprint=workflow_fingerprint,
            workflow_external_state=workflow_state,
            session_id=int(action["session_id"]),
            actor_user_id=str(action["actor_user_id"]),
        )

    def _workflow_ownership_manifest(
        self,
        ownership: dict[str, Any],
        *,
        actions: list[dict[str, Any]] | None = None,
        rollback_operation_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        root_action_id = str(ownership.get("action_id") or "")
        project_id = int(ownership.get("project_id") or 0)
        session_id = int(ownership.get("session_id") or 0)
        task_ids_reader = getattr(
            self.creative_repository, "writer_action_workflow_task_ids", None
        )
        task_ids: set[str] = set()
        job_ids: set[str] = set()
        operation_ids = {
            str(operation_id)
            for operation_id in rollback_operation_ids or []
            if operation_id
        }
        if session_id:
            candidate_actions = (
                actions
                if actions is not None
                else self.store.list_session_actions(session_id)
            )
            for action in candidate_actions:
                action_id = str(action.get("id") or "")
                if actions is None and not (
                    action_id == root_action_id
                    or str(action.get("root_action_id") or "") == root_action_id
                ):
                    continue
                action_project_id = action.get("project_id")
                if action_project_id is None and isinstance(
                    action.get("result"), dict
                ):
                    action_project_id = action["result"].get("project_id")
                if (
                    actions is not None
                    and action_project_id is not None
                    and int(action_project_id) != project_id
                ):
                    continue
                if callable(task_ids_reader):
                    try:
                        task_ids.update(task_ids_reader(project_id, action_id))
                    except ValueError:
                        # A resumed rollback may already have deleted Creative
                        # rows; durable Writer step ownership remains available.
                        pass
                for step in self.store.list_steps(action_id):
                    if step.get("operation_id"):
                        operation_ids.add(str(step["operation_id"]))
                    if step.get("job_id"):
                        job_ids.add(str(step["job_id"]))
        return {
            "task_ids": sorted(task_ids),
            "job_ids": sorted(job_ids),
            "operation_ids": sorted(operation_ids),
        }

    def _workflow_external_ownership(
        self,
        project_id: int,
        ownership: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        baseline_state = ownership.get("workflow_external_state")
        baseline_fingerprint = getattr(
            self.workflow_repository,
            "writer_project_external_use_baseline_fingerprint",
            None,
        )
        if not isinstance(baseline_state, dict) or not callable(
            baseline_fingerprint
        ):
            return (
                str(ownership.get("workflow_external_fingerprint") or ""),
                None,
                None,
            )
        if manifest is None:
            manifest = self._workflow_ownership_manifest(ownership)
        return (
            str(baseline_fingerprint(baseline_state, manifest=manifest)),
            baseline_state,
            manifest,
        )

    def finalize_action_undo(
        self, action: dict[str, Any], actor: Any
    ) -> dict[str, Any] | None:
        del actor
        checkpoint_id = str(action.get("turn_checkpoint_id") or "")
        if not checkpoint_id:
            return None
        undo = self.store.require_action_undo(str(action["id"]))
        project_id = action.get("project_id") or (
            (action.get("result") or {}).get("project_id")
            if isinstance(action.get("result"), dict)
            else None
        )
        kind = str(undo["preimage"].get("kind") or "")
        postcondition: dict[str, Any] = {"kind": kind}
        if kind in {"existing_project", "new_project"} and project_id is not None:
            project_id = int(project_id)
            if kind == "new_project":
                creative_fingerprint = (
                    self.creative_repository.writer_project_deletion_fingerprint(
                        project_id
                    )
                )
                workflow_fingerprint = (
                    self.workflow_repository.writer_project_deletion_fingerprint(
                        str(project_id)
                    )
                )
            else:
                creative_fingerprint = (
                    self.creative_repository.writer_rollback_fingerprint(
                        project_id
                    )
                )
                workflow_fingerprint = (
                    self.workflow_repository.writer_rollback_fingerprint(
                        str(project_id)
                    )
                )
            postcondition.update(
                {
                    "project_id": project_id,
                    "creative_fingerprint": creative_fingerprint,
                    "workflow_fingerprint": workflow_fingerprint,
                }
            )
        return self.store.finalize_action_undo(
            str(action["id"]),
            project_id=(int(project_id) if project_id is not None else None),
            postcondition=postcondition,
        )

    def preview(
        self,
        session_id: int,
        user_message_id: str,
        *,
        actor: Any | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        actor_user_id = str(
            getattr(actor, "user_id", "") or actor_user_id or ""
        )
        session = self.store.get(session_id, actor_user_id)
        if session is None:
            raise WriterActionError(
                "PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问"
            )
        checkpoint = self.store.require_turn(
            session_id, user_message_id, actor_user_id
        )
        turns = self.store.list_turns_from(checkpoint["id"])
        checkpoint_ids = [str(turn["id"]) for turn in turns]
        actions = self.store.list_actions_for_turns(checkpoint_ids)
        reviews = self._project_reviews_for_turns(
            session_id, checkpoint_ids
        )
        active = [
            action
            for action in actions
            if action.get("status") in _ACTIVE_ACTION_STATUSES
        ]
        unsupported: list[str] = []
        undos: list[dict[str, Any]] = []
        project_change_count = 0
        for action in actions:
            if action.get("action_type") not in _PROJECT_MUTATION_ACTIONS:
                continue
            if action.get("status") == "pending" or _action_is_durable_noop(action):
                continue
            try:
                undo = self.store.require_action_undo(str(action["id"]))
            except WriterActionError:
                unsupported.append(str(action["id"]))
                continue
            if undo.get("status") in {"applied", "creative_restored"}:
                project_change_count += 1
                undos.append(undo)
        status = str(checkpoint.get("status") or "")
        blockers: list[str] = []
        if status != "completed":
            blockers.append("该轮对话已失效或尚未完成")
        if active:
            blockers.append("相关创作任务仍在运行")
        if unsupported:
            blockers.append("历史动作缺少可验证的撤销记录")
        if any(
            str(review.get("status") or "") in {"queued", "running"}
            for review in reviews
        ):
            blockers.append("完整项目审核任务仍在运行")
        elif reviews:
            blockers.append("该轮已创建完整项目审核，暂不支持自动回档")
        try:
            self._assert_current_project_write_authority(undos, actor)
        except WriterActionError:
            blockers.append("当前账号已无权修改受影响项目")
        if undos:
            try:
                self._validate_compensation_chain(undos)
            except (AttributeError, OSError, TypeError, ValueError, WriterActionError):
                blockers.append("项目或任务状态已从其他入口更新")
        return {
            "session_id": session_id,
            "message_id": user_message_id,
            "history_revision": int(session.get("history_revision") or 0),
            "messages_to_remove": max(
                0,
                len(session.get("messages") or [])
                - int(checkpoint["message_count_before"]),
            ),
            "reports_to_remove": max(
                0,
                len(session.get("reports") or [])
                - int(checkpoint["report_count_before"]),
            ),
            "actions_to_rollback": len(actions),
            "project_changes_to_rollback": project_change_count,
            "running_actions": len(active),
            "can_rollback": not blockers,
            "blockers": blockers,
        }

    def _validate_compensation_chain(
        self,
        undos: list[dict[str, Any]],
        *,
        workflow_external_ownership: dict[
            int, tuple[str, dict[str, Any] | None, dict[str, Any] | None]
        ]
        | None = None,
    ) -> None:
        creative_state: dict[int, str | None] = {}
        workflow_state: dict[int, str | None] = {}
        for undo in undos:
            post = undo.get("postcondition") or {}
            project_id = post.get("project_id")
            if project_id is None:
                continue
            project_id = int(project_id)
            if project_id not in creative_state:
                try:
                    if (undo.get("preimage") or {}).get("kind") == "new_project":
                        creative_state[project_id] = self.creative_repository.writer_project_deletion_fingerprint(
                            project_id
                        )
                    else:
                        creative_state[project_id] = self.creative_repository.writer_rollback_fingerprint(
                            project_id
                        )
                except ValueError:
                    creative_state[project_id] = None
                try:
                    if (undo.get("preimage") or {}).get("kind") == "new_project":
                        workflow_state[project_id] = self.workflow_repository.writer_project_deletion_fingerprint(
                            str(project_id)
                        )
                    else:
                        workflow_state[project_id] = self.workflow_repository.writer_rollback_fingerprint(
                            str(project_id)
                        )
                except ValueError as exc:
                    raise WriterActionError(
                        "ROLLBACK_CONFLICT", "项目任务已不存在或已从其他入口修改"
                    ) from exc
            preimage = undo.get("preimage") or {}
            if (
                preimage.get("kind") == "new_project"
                and undo.get("status") == "applied"
                and creative_state[project_id] is not None
            ):
                ownership = preimage.get("ownership") or {}
                if not ownership:
                    raise WriterActionError(
                        "TURN_ROLLBACK_UNSUPPORTED",
                        "新建项目缺少独占状态记录",
                    )
                if (
                    self.creative_repository.writer_project_external_use_fingerprint(
                        project_id, str(ownership.get("action_id") or "")
                    )
                    != ownership.get("creative_external_fingerprint")
                ):
                    raise WriterActionError(
                        "ROLLBACK_CONFLICT",
                        "新建项目已被其他入口使用，不能自动删除",
                    )
                workflow_expected, workflow_baseline, workflow_manifest = (
                    (workflow_external_ownership or {}).get(project_id)
                    or self._workflow_external_ownership(project_id, ownership)
                )
                workflow_current = (
                    self.workflow_repository.writer_project_external_use_fingerprint(
                        str(project_id),
                        baseline_state=workflow_baseline,
                        manifest=workflow_manifest,
                    )
                    if workflow_baseline is not None
                    else self.workflow_repository.writer_project_external_use_fingerprint(
                        str(project_id)
                    )
                )
                if workflow_current != workflow_expected:
                    raise WriterActionError(
                        "ROLLBACK_CONFLICT",
                        "新建项目已被其他入口使用，不能自动删除",
                    )
            restored_creative = (
                (preimage.get("creative") or {}).get("fingerprint")
                if preimage.get("kind") == "existing_project"
                else None
            )
            allowed_creative = {post.get("creative_fingerprint")}
            if undo.get("status") == "creative_restored":
                allowed_creative = {restored_creative}
            else:
                allowed_creative.add(restored_creative)
            if creative_state[project_id] not in allowed_creative:
                raise WriterActionError(
                    "ROLLBACK_CONFLICT", "项目内容已从其他入口更新，不能覆盖"
                )
            restored_workflow = (
                (undo.get("workflow_preimage") or {}).get("fingerprint")
                if preimage.get("kind") == "existing_project"
                else None
            )
            allowed_workflow = {
                post.get("workflow_fingerprint"),
                restored_workflow,
            }
            project_data_exists = getattr(
                self.workflow_repository, "writer_project_data_exists", None
            )
            if (
                preimage.get("kind") == "new_project"
                and undo.get("status") == "creative_restored"
                and callable(project_data_exists)
                and not project_data_exists(str(project_id))
            ):
                allowed_workflow.add(workflow_state[project_id])
            if workflow_state[project_id] not in allowed_workflow:
                raise WriterActionError(
                    "ROLLBACK_CONFLICT", "项目任务状态已变化，不能覆盖"
                )
            if preimage.get("kind") == "existing_project":
                creative_state[project_id] = str(
                    (preimage.get("creative") or {}).get("fingerprint") or ""
                )
                workflow_state[project_id] = str(
                    (undo.get("workflow_preimage") or {}).get("fingerprint")
                    or ""
                )
            else:
                creative_state[project_id] = None
                workflow_state[project_id] = None

    def _project_reviews_for_turns(
        self, session_id: int, checkpoint_ids: list[str]
    ) -> list[dict[str, Any]]:
        list_reviews = getattr(
            self.creative_repository, "list_script_reviews", None
        )
        if not callable(list_reviews) or not checkpoint_ids:
            return []
        selected = set(checkpoint_ids)
        reviews: list[dict[str, Any]] = []
        for review in list_reviews():
            source = dict(review.get("source_payload") or {})
            if (
                int(source.get("writer_session_id") or 0) == int(session_id)
                and str(source.get("writer_turn_checkpoint_id") or "")
                in selected
            ):
                reviews.append(review)
        return reviews

    @staticmethod
    def _assert_project_reviews_rollbackable(
        reviews: list[dict[str, Any]],
    ) -> None:
        if any(
            str(review.get("status") or "") in {"queued", "running"}
            for review in reviews
        ):
            raise WriterActionError(
                "ROLLBACK_BUSY", "完整项目审核任务仍在运行"
            )
        if reviews:
            raise WriterActionError(
                "TURN_ROLLBACK_UNSUPPORTED",
                "该轮已创建完整项目审核，暂不支持自动回档",
            )

    def rollback(
        self,
        session_id: int,
        user_message_id: str,
        *,
        actor: Any,
        mode: str,
        client_request_id: str,
        expected_history_revision: int,
        mutation_token: str | None = None,
    ) -> dict[str, Any]:
        owns_mutation = mutation_token is None
        if mutation_token is None:
            mutation_token = f"rollback-{uuid4().hex}"
            self.store.acquire_session_mutation(
                session_id,
                str(actor.user_id),
                owner_token=mutation_token,
                mutation_kind=mode,
                lease_seconds=self._mutation_lease_seconds,
            )
        heartbeat = (
            self.store.start_session_mutation_heartbeat(
                session_id,
                owner_token=mutation_token,
                lease_seconds=self._mutation_lease_seconds,
            )
            if owns_mutation
            else None
        )
        try:
            try:
                return self._rollback_with_lease(
                    session_id,
                    user_message_id,
                    actor=actor,
                    mode=mode,
                    client_request_id=client_request_id,
                    expected_history_revision=expected_history_revision,
                    mutation_token=mutation_token,
                )
            except WriterActionError as exc:
                try:
                    rollback = self.store.require_rollback_by_request(
                        str(actor.user_id), client_request_id
                    )
                    if rollback.get("stage") != "completed":
                        self.store.advance_rollback(
                            rollback["id"],
                            str(rollback["stage"]),
                            error_code=exc.code,
                            error_message=str(exc),
                            expected_stage=str(rollback["stage"]),
                            mutation_token=mutation_token,
                        )
                except WriterActionError:
                    pass
                raise
        finally:
            if owns_mutation:
                if heartbeat is not None:
                    heartbeat_stop, heartbeat_thread = heartbeat
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=1.0)
                self.store.release_session_mutation(
                    session_id, owner_token=mutation_token
                )

    def _rollback_with_lease(
        self,
        session_id: int,
        user_message_id: str,
        *,
        actor: Any,
        mode: str,
        client_request_id: str,
        expected_history_revision: int,
        mutation_token: str,
    ) -> dict[str, Any]:
        actor_user_id = str(actor.user_id)
        fence = lambda: self.store.assert_session_mutation(
            session_id, owner_token=mutation_token
        )
        fence()
        checkpoint = self.store.require_turn(
            session_id, user_message_id, actor_user_id
        )
        rollback, created = self.store.begin_rollback(
            session_id,
            actor_user_id,
            checkpoint["id"],
            mode=mode,
            client_request_id=client_request_id,
            expected_history_revision=expected_history_revision,
            mutation_token=mutation_token,
        )
        if not created and rollback.get("stage") == "completed":
            return self.store.get(session_id, actor_user_id) or {}

        turns = self.store.list_turns_from(checkpoint["id"])
        checkpoint_ids = [str(turn["id"]) for turn in turns]
        self._assert_project_reviews_rollbackable(
            self._project_reviews_for_turns(session_id, checkpoint_ids)
        )
        actions = self.store.list_actions_for_turns(checkpoint_ids)
        self._assert_current_project_write_authority(
            [], actor, actions=actions
        )
        active_actions = [
            action
            for action in actions
            if action.get("status") in _ACTIVE_ACTION_STATUSES
        ]
        if active_actions and self.action_runner is not None:
            for action in active_actions:
                try:
                    fence()
                    self.action_runner.cancel(str(action["id"]), actor)
                    fence()
                except WriterActionError as exc:
                    if exc.code != "ACTION_STATE_INVALID":
                        raise
            actions = self.store.list_actions_for_turns(
                [str(turn["id"]) for turn in turns]
            )
            active_actions = [
                action
                for action in actions
                if action.get("status") in _ACTIVE_ACTION_STATUSES
            ]
        if active_actions:
            raise WriterActionError(
                "ROLLBACK_BUSY", "相关创作任务仍在运行，请停止后重试"
            )
        if str(rollback.get("stage") or "requested") == "requested":
            rollback = self.store.advance_rollback(
                rollback["id"],
                "jobs_settled",
                expected_stage="requested",
                mutation_token=mutation_token,
            )
        rollback_actions: list[dict[str, Any]] = []
        rollback_undos: list[dict[str, Any]] = []
        undos: list[dict[str, Any]] = []
        for action in reversed(actions):
            if action.get("status") == "pending" or _action_is_durable_noop(action):
                continue
            if action.get("action_type") not in _PROJECT_MUTATION_ACTIONS:
                continue
            undo = self.store.require_action_undo(str(action["id"]))
            if undo.get("status") not in {
                "applied",
                "creative_restored",
                "compensated",
            }:
                raise WriterActionError(
                    "TURN_ROLLBACK_UNSUPPORTED", "动作撤销记录尚未完成"
                )
            rollback_actions.append(action)
            rollback_undos.append(undo)
            if undo.get("status") == "compensated":
                continue
            undos.append(undo)

        workflow_external_ownership: dict[
            int, tuple[str, dict[str, Any] | None, dict[str, Any] | None]
        ] = {}
        for undo in rollback_undos:
            preimage = undo.get("preimage") or {}
            postcondition = undo.get("postcondition") or {}
            if preimage.get("kind") != "new_project":
                continue
            project_id = postcondition.get("project_id")
            if project_id is None:
                continue
            project_id = int(project_id)
            rollback_operation_ids = [
                f"writer-rollback:{rollback['id']}:{candidate['action_id']}"
                for candidate in rollback_undos
                if (
                    (candidate.get("preimage") or {}).get("kind")
                    == "existing_project"
                    and int(
                        (candidate.get("postcondition") or {}).get("project_id")
                        or 0
                    )
                    == project_id
                )
            ]
            ownership = preimage.get("ownership") or {}
            manifest = self._workflow_ownership_manifest(
                ownership,
                actions=rollback_actions,
                rollback_operation_ids=rollback_operation_ids,
            )
            workflow_external_ownership[project_id] = (
                self._workflow_external_ownership(
                    project_id, ownership, manifest=manifest
                )
            )

        rollback_stage = str(rollback.get("stage") or "requested")
        partial_started = rollback_stage in {
            "creative_restored",
            "workflow_restored",
            "conversation_restored",
            "completed",
        } or any(undo.get("status") == "creative_restored" for undo in undos)
        workflow_deletion_markers: list[tuple[int, str]] = []

        def abort_unstarted_project_deletions() -> bool:
            abort_deletion = getattr(
                self.workflow_repository, "abort_project_deletion", None
            )
            pending_creative_deletion = getattr(
                self.creative_repository, "get_pending_project_deletion", None
            )
            if not callable(abort_deletion) or not callable(
                pending_creative_deletion
            ):
                return bool(workflow_deletion_markers)
            unresolved_marker = False
            for candidate_project_id, marker_owner in reversed(
                workflow_deletion_markers
            ):
                try:
                    creative_project_exists = (
                        self.creative_repository.get_project(candidate_project_id)
                        is not None
                    )
                    creative_deletion_started = (
                        pending_creative_deletion(candidate_project_id) is not None
                    )
                    if creative_project_exists and not creative_deletion_started:
                        removed = abort_deletion(
                            str(candidate_project_id), owner_token=marker_owner
                        )
                        unresolved_marker = unresolved_marker or not removed
                    else:
                        unresolved_marker = True
                except Exception:
                    unresolved_marker = True
            return unresolved_marker

        try:
            self._assert_current_project_write_authority(undos, actor)
            if rollback_stage in {"requested", "jobs_settled"}:
                self._validate_compensation_chain(
                    undos,
                    workflow_external_ownership=workflow_external_ownership,
                )
                rollback = self.store.advance_rollback(
                    rollback["id"],
                    "validated",
                    expected_stage=rollback_stage,
                    mutation_token=mutation_token,
                )
                rollback_stage = "validated"
            for undo in undos:
                preimage = undo["preimage"]
                post = undo["postcondition"]
                project_id = post.get("project_id")
                if project_id is None:
                    undo = self.store.mark_action_undo_creative_restored(
                        undo["action_id"]
                    )
                    self.store.mark_action_undo_compensated(undo["action_id"])
                    continue
                project_id = int(project_id)
                if preimage.get("kind") == "new_project":
                    marker_owner = (
                        f"writer-rollback:{rollback['id']}:{undo['action_id']}"
                    )
                    begin_workflow_deletion = getattr(
                        self.workflow_repository,
                        "begin_project_deletion",
                        None,
                    )
                    if callable(begin_workflow_deletion):
                        fence()
                        marker = begin_workflow_deletion(
                            str(project_id),
                            owner_token=marker_owner,
                            requested_by=actor_user_id,
                            project_snapshot={
                                "id": project_id,
                                "name": str(
                                    (
                                        self.creative_repository.get_project(
                                            project_id
                                        )
                                        or {}
                                    ).get("name")
                                    or ""
                                ),
                            },
                        )
                        if (
                            isinstance(marker, dict)
                            and marker.get("owner_token") not in {None, marker_owner}
                        ):
                            raise ValueError("project deletion is owned by another operation")
                        workflow_deletion_markers.append(
                            (project_id, marker_owner)
                        )
                        fence()
                if undo.get("status") == "applied":
                    fence()
                    if preimage.get("kind") == "new_project":
                        ownership = preimage.get("ownership") or {}
                        if self.creative_repository.get_project(project_id) is not None:
                            self.creative_repository.delete_project(
                                project_id,
                                requested_by=actor_user_id,
                                expected_writer_fingerprint=str(
                                    post["creative_fingerprint"]
                                ),
                                expected_writer_external_fingerprint=str(
                                    ownership["creative_external_fingerprint"]
                                ),
                                writer_action_id=str(ownership["action_id"]),
                            )
                    else:
                        self.creative_repository.restore_writer_rollback_state(
                            project_id,
                            preimage["creative"],
                            expected_fingerprint=str(post["creative_fingerprint"]),
                        )
                    partial_started = True
                    fence()
                    undo = self.store.mark_action_undo_creative_restored(
                        undo["action_id"]
                    )
                    rollback = self.store.advance_rollback(
                        rollback["id"],
                        "creative_restored",
                        expected_stage=rollback_stage,
                        mutation_token=mutation_token,
                    )
                    rollback_stage = "creative_restored"
                if preimage.get("kind") == "new_project":
                    project_data_exists = getattr(
                        self.workflow_repository,
                        "writer_project_data_exists",
                        None,
                    )
                    if not callable(project_data_exists) or project_data_exists(
                        str(project_id)
                    ):
                        workflow_expected, workflow_baseline, workflow_manifest = (
                            workflow_external_ownership[project_id]
                        )
                        fence()
                        workflow_fingerprint = (
                            self.workflow_repository.writer_project_deletion_fingerprint(
                                str(project_id)
                            )
                        )
                        fence()
                        self.workflow_repository.delete_project_data(
                            str(project_id),
                            expected_writer_fingerprint=str(
                                workflow_fingerprint
                            ),
                            expected_writer_external_fingerprint=str(
                                workflow_expected
                            ),
                            writer_external_baseline_state=workflow_baseline,
                            writer_ownership_manifest=workflow_manifest,
                        )
                        fence()
                    complete_deletion = getattr(
                        self.creative_repository,
                        "complete_project_deletion",
                        None,
                    )
                    if callable(complete_deletion):
                        fence()
                        complete_deletion(project_id)
                        fence()
                    complete_workflow_deletion = getattr(
                        self.workflow_repository,
                        "complete_project_deletion",
                        None,
                    )
                    if callable(complete_workflow_deletion):
                        fence()
                        complete_workflow_deletion(
                            str(project_id), owner_token=marker_owner
                        )
                        fence()
                else:
                    fence()
                    self.workflow_repository.restore_writer_rollback_state(
                        str(project_id),
                        undo["workflow_preimage"],
                        expected_fingerprint=str(post["workflow_fingerprint"]),
                        actor=actor,
                        writer_operation_id=(
                            f"writer-rollback:{rollback['id']}:{undo['action_id']}"
                        ),
                    )
                    fence()
                self.store.mark_action_undo_compensated(undo["action_id"])
            rollback = self.store.advance_rollback(
                rollback["id"],
                "workflow_restored",
                expected_stage=rollback_stage,
                mutation_token=mutation_token,
            )
            rollback_stage = "workflow_restored"
            return self.store.restore_conversation(
                rollback["id"], mutation_token=mutation_token
            )
        except WriterActionError as exc:
            partial_started = (
                abort_unstarted_project_deletions() or partial_started
            )
            if not partial_started:
                raise
            self.store.advance_rollback(
                rollback["id"],
                rollback_stage,
                error_code="ROLLBACK_PARTIAL_FAILURE",
                error_message=str(exc),
                expected_stage=rollback_stage,
                mutation_token=mutation_token,
            )
            raise WriterActionError(
                "ROLLBACK_PARTIAL_FAILURE",
                "回档已部分完成，请使用相同请求重试",
                retryable=True,
            ) from exc
        except Exception as exc:
            partial_started = (
                abort_unstarted_project_deletions() or partial_started
            )
            error_code = (
                "ROLLBACK_PARTIAL_FAILURE"
                if partial_started
                else "ROLLBACK_CONFLICT"
                if isinstance(exc, (KeyError, TypeError, ValueError))
                else "ROLLBACK_PARTIAL_FAILURE"
            )
            self.store.advance_rollback(
                rollback["id"],
                rollback_stage,
                error_code=error_code,
                error_message=str(exc),
                expected_stage=rollback_stage,
                mutation_token=mutation_token,
            )
            raise WriterActionError(
                error_code,
                (
                    "回档已部分完成，请使用相同请求重试"
                    if error_code == "ROLLBACK_PARTIAL_FAILURE"
                    else "系统状态已变化，回档已停止"
                ),
                retryable=error_code == "ROLLBACK_PARTIAL_FAILURE",
            ) from exc
