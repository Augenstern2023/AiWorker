# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from typing import Any

from .schema import WriterActionError
from .store import WriterAgentStore


_ACTIVE_ACTION_STATUSES = frozenset({"queued", "running"})
_PROJECT_MUTATION_ACTIONS = frozenset(
    {"project.create_original", "creative.run_plan", "creative.lock"}
)


class RollbackService:
    """Coordinate compensating rollback across Writer, Creative and Workflow."""

    def __init__(
        self,
        store: WriterAgentStore,
        creative_repository: Any,
        workflow_repository: Any,
    ) -> None:
        self.store = store
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository

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
        project_id = action.get("project_id")
        if action_type == "project.create_original":
            preimage = {"kind": "new_project"}
            workflow_preimage: dict[str, Any] = {}
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

    def finalize_action_undo(
        self, action: dict[str, Any], actor: Any
    ) -> dict[str, Any] | None:
        del actor
        checkpoint_id = str(action.get("turn_checkpoint_id") or "")
        if not checkpoint_id:
            return None
        undo = self.store.require_action_undo(str(action["id"]))
        project_id = action.get("project_id")
        kind = str(undo["preimage"].get("kind") or "")
        postcondition: dict[str, Any] = {"kind": kind}
        if kind in {"existing_project", "new_project"} and project_id is not None:
            project_id = int(project_id)
            postcondition.update(
                {
                    "project_id": project_id,
                    "creative_fingerprint": (
                        self.creative_repository.writer_rollback_fingerprint(
                            project_id
                        )
                    ),
                    "workflow_fingerprint": (
                        self.workflow_repository.writer_rollback_fingerprint(
                            str(project_id)
                        )
                    ),
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
        actor_user_id: str,
    ) -> dict[str, Any]:
        session = self.store.get(session_id, actor_user_id)
        if session is None:
            raise WriterActionError(
                "PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问"
            )
        checkpoint = self.store.require_turn(
            session_id, user_message_id, actor_user_id
        )
        turns = self.store.list_turns_from(checkpoint["id"])
        actions = self.store.list_actions_for_turns(
            [str(turn["id"]) for turn in turns]
        )
        active = [
            action
            for action in actions
            if action.get("status") in _ACTIVE_ACTION_STATUSES
        ]
        unsupported: list[str] = []
        project_change_count = 0
        for action in actions:
            if action.get("action_type") not in _PROJECT_MUTATION_ACTIONS:
                continue
            if action.get("status") == "pending":
                continue
            try:
                undo = self.store.require_action_undo(str(action["id"]))
            except WriterActionError:
                unsupported.append(str(action["id"]))
                continue
            if undo.get("status") == "applied":
                project_change_count += 1
        status = str(checkpoint.get("status") or "")
        blockers: list[str] = []
        if status != "completed":
            blockers.append("该轮对话已失效或尚未完成")
        if active:
            blockers.append("相关创作任务仍在运行")
        if unsupported:
            blockers.append("历史动作缺少可验证的撤销记录")
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
        self, undos: list[dict[str, Any]]
    ) -> None:
        creative_state: dict[int, str] = {}
        workflow_state: dict[int, str] = {}
        for undo in undos:
            post = undo.get("postcondition") or {}
            project_id = post.get("project_id")
            if project_id is None:
                continue
            project_id = int(project_id)
            if project_id not in creative_state:
                try:
                    creative_state[project_id] = (
                        self.creative_repository.writer_rollback_fingerprint(
                            project_id
                        )
                    )
                except ValueError as exc:
                    raise WriterActionError(
                        "ROLLBACK_CONFLICT", "项目已不存在或已从其他入口修改"
                    ) from exc
                workflow_state[project_id] = (
                    self.workflow_repository.writer_rollback_fingerprint(
                        str(project_id)
                    )
                )
            if creative_state[project_id] != post.get("creative_fingerprint"):
                raise WriterActionError(
                    "ROLLBACK_CONFLICT", "项目内容已从其他入口更新，不能覆盖"
                )
            if workflow_state[project_id] != post.get("workflow_fingerprint"):
                raise WriterActionError(
                    "ROLLBACK_CONFLICT", "项目任务状态已变化，不能覆盖"
                )
            preimage = undo.get("preimage") or {}
            if preimage.get("kind") == "existing_project":
                creative_state[project_id] = str(
                    (preimage.get("creative") or {}).get("fingerprint") or ""
                )
                workflow_state[project_id] = str(
                    (undo.get("workflow_preimage") or {}).get("fingerprint")
                    or ""
                )
            else:
                creative_state.pop(project_id, None)
                workflow_state.pop(project_id, None)

    def rollback(
        self,
        session_id: int,
        user_message_id: str,
        *,
        actor: Any,
        mode: str,
        client_request_id: str,
        expected_history_revision: int,
    ) -> dict[str, Any]:
        actor_user_id = str(actor.user_id)
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
        )
        if not created and rollback.get("stage") == "completed":
            return self.store.get(session_id, actor_user_id) or {}

        turns = self.store.list_turns_from(checkpoint["id"])
        actions = self.store.list_actions_for_turns(
            [str(turn["id"]) for turn in turns]
        )
        if any(
            action.get("status") in _ACTIVE_ACTION_STATUSES
            for action in actions
        ):
            raise WriterActionError(
                "ROLLBACK_BUSY", "相关创作任务仍在运行，请停止后重试"
            )
        undos: list[dict[str, Any]] = []
        for action in reversed(actions):
            if action.get("status") == "pending":
                continue
            if action.get("action_type") not in _PROJECT_MUTATION_ACTIONS:
                continue
            undo = self.store.require_action_undo(str(action["id"]))
            if undo.get("status") == "compensated":
                continue
            if undo.get("status") != "applied":
                raise WriterActionError(
                    "TURN_ROLLBACK_UNSUPPORTED", "动作撤销记录尚未完成"
                )
            undos.append(undo)

        try:
            self._validate_compensation_chain(undos)
            self.store.advance_rollback(rollback["id"], "validated")
            for undo in undos:
                preimage = undo["preimage"]
                post = undo["postcondition"]
                project_id = post.get("project_id")
                if project_id is None:
                    self.store.mark_action_undo_compensated(undo["action_id"])
                    continue
                project_id = int(project_id)
                if preimage.get("kind") == "new_project":
                    if self.creative_repository.get_project(project_id) is not None:
                        self.creative_repository.delete_project(
                            project_id, requested_by=actor_user_id
                        )
                    self.workflow_repository.delete_project_data(str(project_id))
                    complete_deletion = getattr(
                        self.creative_repository,
                        "complete_project_deletion",
                        None,
                    )
                    if callable(complete_deletion):
                        complete_deletion(project_id)
                else:
                    self.creative_repository.restore_writer_rollback_state(
                        project_id,
                        preimage["creative"],
                        expected_fingerprint=str(post["creative_fingerprint"]),
                    )
                    self.workflow_repository.restore_writer_rollback_state(
                        str(project_id),
                        undo["workflow_preimage"],
                        expected_fingerprint=str(post["workflow_fingerprint"]),
                        actor=actor,
                    )
                self.store.mark_action_undo_compensated(undo["action_id"])
            self.store.advance_rollback(rollback["id"], "workflow_restored")
            return self.store.restore_conversation(rollback["id"])
        except WriterActionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            self.store.advance_rollback(
                rollback["id"],
                str(rollback.get("stage") or "requested"),
                error_code="ROLLBACK_CONFLICT",
                error_message=str(exc),
            )
            raise WriterActionError(
                "ROLLBACK_CONFLICT", "系统状态已变化，回档已停止"
            ) from exc
