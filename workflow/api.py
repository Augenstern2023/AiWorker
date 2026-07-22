# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response, status

from identity.dependencies import current_principal, require_permission
from identity.schema import Principal

from .repository import WorkflowRepository
from .schema import ProjectMemberCreateRequest, TaskAssignmentRequest


def build_workflow_router(repository: WorkflowRepository) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workflow/tasks")
    def list_tasks(
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        return repository.list_tasks(principal)

    @router.get("/api/workflow/tasks/{task_id}")
    def get_task(
        task_id: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        task = repository.get_visible_task(task_id, principal)
        if task is None:
            raise HTTPException(status_code=404, detail="workflow task not found")
        return task

    @router.post("/api/workflow/tasks/{task_id}/claim")
    def claim_task(
        task_id: str,
        principal: Principal = Depends(require_permission("workflow.claim")),
    ) -> dict[str, Any]:
        return _call(repository.claim_task, task_id, principal)

    @router.post("/api/workflow/tasks/{task_id}/assign")
    def assign_task(
        task_id: str,
        payload: TaskAssignmentRequest,
        principal: Principal = Depends(require_permission("workflow.assign")),
    ) -> dict[str, Any]:
        return _call(
            repository.assign_task, task_id, payload.assignee_user_id, principal
        )

    @router.post("/api/workflow/tasks/{task_id}/reassign")
    def reassign_task(
        task_id: str,
        payload: TaskAssignmentRequest,
        principal: Principal = Depends(require_permission("workflow.reassign")),
    ) -> dict[str, Any]:
        return _call(
            repository.reassign_task, task_id, payload.assignee_user_id, principal
        )

    @router.post("/api/workflow/tasks/{task_id}/start")
    def start_task(
        task_id: str,
        principal: Principal = Depends(
            require_permission("workflow.task.update")
        ),
    ) -> dict[str, Any]:
        return _call(repository.start_task, task_id, principal)

    @router.post("/api/workflow/tasks/{task_id}/cancel")
    def cancel_task(
        task_id: str,
        principal: Principal = Depends(require_permission("workflow.cancel")),
    ) -> dict[str, Any]:
        return _call(repository.cancel_task, task_id, principal)

    member_manager = require_permission("project.member.manage")

    @router.get("/api/workflow/projects/{project_id}/members")
    def list_project_members(
        project_id: str,
        principal: Principal = Depends(member_manager),
    ) -> list[dict[str, Any]]:
        _call(repository.assert_can_manage_project, project_id, principal)
        return repository.list_project_members(project_id)

    @router.post(
        "/api/workflow/projects/{project_id}/members",
        status_code=status.HTTP_201_CREATED,
    )
    def add_project_member(
        project_id: str,
        payload: ProjectMemberCreateRequest,
        principal: Principal = Depends(member_manager),
    ) -> dict[str, Any]:
        return _call(
            repository.add_project_member,
            project_id,
            payload.user_id,
            payload.project_role,
            principal,
            status=payload.status,
        )

    @router.delete(
        "/api/workflow/projects/{project_id}/members/{membership_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_project_member(
        project_id: str,
        membership_id: str,
        principal: Principal = Depends(member_manager),
    ) -> Response:
        _call(
            repository.remove_project_member,
            project_id,
            membership_id,
            principal,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
