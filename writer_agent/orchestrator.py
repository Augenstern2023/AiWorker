# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from creative.application_service import CreativeApplicationService
from identity.permission_service import PermissionService
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .action_service import validate_original_project_action_payload
from .schema import WriterActionError
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
