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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from audit.service import AuditService
from identity.dependencies import current_principal, require_permission
from identity.schema import Principal

from .repository import WorkflowRepository
from .schema import (
    ProjectMemberCreateRequest,
    TaskAssignmentRequest,
)
from .transition_service import TransitionService


class CreateAssignmentRequest(BaseModel):
    topic_id: int
    assignee_user_id: str
    role_code: str
    task_type: str


def build_workflow_router(
    repository: WorkflowRepository,
    transition_service: TransitionService | None = None,
) -> APIRouter:
    router = APIRouter()
    transitions = transition_service or TransitionService(repository)

    @router.get("/api/workflow/tasks")
    def list_tasks(
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        return [
            _task_view(repository, task, principal)
            for task in repository.list_tasks(principal)
        ]


    @router.get("/api/workflow/assignment-candidates")
    def list_role_assignment_candidates(
        role_code: str,
        task_type: str,
        principal: Principal = Depends(require_permission("workflow.assign")),
    ) -> list[dict[str, Any]]:
        return _call(
            repository.list_role_assignment_candidates,
            role_code,
            task_type,
            principal,
        )

    @router.get("/api/workflow/tasks/{task_id}")
    def get_task(
        task_id: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        task = repository.get_visible_task(task_id, principal)
        if task is None:
            raise HTTPException(status_code=404, detail="workflow task not found")
        transitions.reconcile_task(task_id)
        task = repository.get_visible_task(task_id, principal)
        assert task is not None
        return _task_view(repository, task, principal)

    @router.get("/api/workflow/tasks/{task_id}/assignment-candidates")
    def list_assignment_candidates(
        task_id: str,
        principal: Principal = Depends(require_permission("workflow.assign")),
    ) -> list[dict[str, Any]]:
        return _call(repository.list_task_assignment_candidates, task_id, principal)

    @router.post("/api/workflow/tasks/{task_id}/claim")
    def claim_task(
        task_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        _require_task_permission_or_project_member(
            repository, task_id, principal, "workflow.claim"
        )
        return _audited_call(
            repository,
            request,
            principal,
            "workflow.claim",
            "task",
            task_id,
            repository.claim_task,
            task_id,
            principal,
        )

    @router.post("/api/workflow/tasks/{task_id}/assign")
    def assign_task(
        task_id: str,
        payload: TaskAssignmentRequest,
        request: Request,
        principal: Principal = Depends(require_permission("workflow.assign")),
    ) -> dict[str, Any]:
        return _audited_call(
            repository, request, principal, "workflow.assign", "task", task_id,
            repository.assign_task, task_id, payload.assignee_user_id, principal
        )

    @router.post("/api/workflow/create-assignment")
    def create_assignment(
        payload: CreateAssignmentRequest,
        request: Request,
        principal: Principal = Depends(require_permission("workflow.assign")),
    ) -> dict[str, Any]:
        return _call(repository.create_and_assign_task, payload.topic_id, payload.assignee_user_id, payload.role_code, payload.task_type, principal)

    @router.post("/api/workflow/tasks/{task_id}/reassign")
    def reassign_task(
        task_id: str,
        payload: TaskAssignmentRequest,
        request: Request,
        principal: Principal = Depends(require_permission("workflow.reassign")),
    ) -> dict[str, Any]:
        return _audited_call(
            repository, request, principal, "workflow.reassign", "task", task_id,
            repository.reassign_task, task_id, payload.assignee_user_id, principal
        )

    @router.post("/api/workflow/tasks/{task_id}/start")
    def start_task(
        task_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        _require_task_permission_or_project_member(
            repository, task_id, principal, "workflow.task.update"
        )
        return _audited_call(
            repository, request, principal, "workflow.start", "task", task_id,
            repository.start_task, task_id, principal
        )

    @router.post("/api/workflow/tasks/{task_id}/cancel")
    def cancel_task(
        task_id: str,
        request: Request,
        principal: Principal = Depends(require_permission("workflow.cancel")),
    ) -> dict[str, Any]:
        return _audited_call(
            repository, request, principal, "workflow.cancel", "task", task_id,
            repository.cancel_task, task_id, principal
        )

    member_manager = require_permission("project.member.manage")

    @router.get("/api/workflow/projects/{project_id}/members")
    def list_project_members(
        project_id: str,
        principal: Principal = Depends(member_manager),
    ) -> list[dict[str, Any]]:
        _call(repository.assert_can_manage_project, project_id, principal)
        members = repository.list_project_members(project_id)
        enriched = []
        for member in members:
            user = repository.identity_repository.get_user(member["user_id"])
            enriched.append(
                {
                    **member,
                    "username": user["username"] if user else "",
                    "display_name": user["display_name"] if user else "已停用账号",
                }
            )
        return enriched

    @router.get("/api/workflow/projects/{project_id}/member-candidates")
    def list_project_member_candidates(
        project_id: str,
        project_role: str,
        principal: Principal = Depends(member_manager),
    ) -> list[dict[str, Any]]:
        task_types = {
            "project_owner": "brief",
            "creative_planner": "brief",
            "screenwriter": "outline",
        }
        task_type = task_types.get(project_role)
        if task_type is None:
            raise HTTPException(400, "unsupported project member role")
        _call(repository.assert_can_manage_project, project_id, principal)
        return _call(
            repository.list_role_assignment_candidates,
            project_role,
            task_type,
            principal,
            required_permission="project.member.manage",
        )

    @router.post(
        "/api/workflow/projects/{project_id}/members",
        status_code=status.HTTP_201_CREATED,
    )
    def add_project_member(
        project_id: str,
        payload: ProjectMemberCreateRequest,
        request: Request,
        principal: Principal = Depends(member_manager),
    ) -> dict[str, Any]:
        return _audited_call(
            repository,
            request,
            principal,
            "project.member.add",
            "project_member",
            payload.user_id,
            repository.add_project_member,
            project_id,
            payload.user_id,
            payload.project_role,
            principal,
            status=payload.status,
            audit_project_id=project_id,
        )

    @router.delete(
        "/api/workflow/projects/{project_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_project_member(
        project_id: str,
        user_id: str,
        request: Request,
        principal: Principal = Depends(member_manager),
    ) -> Response:
        _audited_call(
            repository,
            request,
            principal,
            "project.member.remove",
            "project_member",
            user_id,
            repository.remove_project_member,
            project_id,
            user_id,
            principal,
            audit_project_id=project_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _visible_task(
    repository: WorkflowRepository, task_id: str, principal: Principal
) -> dict[str, Any]:
    task = repository.get_visible_task(task_id, principal)
    if task is None:
        raise HTTPException(status_code=404, detail="workflow task not found")
    return task


def _task_view(
    repository: WorkflowRepository,
    task: dict[str, Any],
    principal: Principal,
) -> dict[str, Any]:
    viewed = dict(task)
    is_active_project_member = bool(
        task.get("project_id")
        and repository.is_active_project_member(
            str(task["project_id"]), principal
        )
    )
    assignee_id = task.get("assignee_user_id")
    assignee = (
        repository.identity_repository.get_user(str(assignee_id))
        if assignee_id
        else None
    )
    viewed["assignee_display_name"] = (
        assignee.get("display_name") if assignee else None
    )
    viewed["can_claim"] = (
        task["status"] == "pool"
        and (
            principal.is_system_admin
            or is_active_project_member
            or (
                "workflow.claim" in principal.permissions
                and repository.can_claim_task(task["id"], principal)
            )
        )
    )
    viewed["can_start"] = (
        task["status"] in {"assigned", "returned"}
        and (
            is_active_project_member
            or task["assignee_user_id"] == principal.user_id
        )
        and (
            principal.is_system_admin
            or is_active_project_member
            or "workflow.task.update" in principal.permissions
        )
    )
    viewed["can_assign"] = (
        task["status"] == "pool"
        and (
            principal.is_system_admin
            or (
                "workflow.assign" in principal.permissions
                and repository.can_assign_task(task["id"], principal)
            )
        )
    )
    return viewed


def _require_task_permission_or_project_member(
    repository: WorkflowRepository,
    task_id: str,
    principal: Principal,
    permission: str,
) -> None:
    if principal.is_system_admin or permission in principal.permissions:
        return
    task = repository.get_task(task_id)
    project_id = task.get("project_id") if task else None
    if project_id and repository.is_active_project_member(
        str(project_id), principal
    ):
        return
    raise HTTPException(status_code=403, detail="没有执行此操作的权限")



def _call(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resource not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _audited_call(
    repository: WorkflowRepository,
    request: Request,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: str,
    operation: Callable[..., Any],
    *args: Any,
    audit_project_id: str | None = None,
    **kwargs: Any,
) -> Any:
    audit = getattr(request.app.state, "audit_service", None)
    task = (
        repository.get_task(target_id)
        if target_type == "task"
        else None
    )
    if target_type == "submission":
        submission = repository.get_submission(target_id)
        task = repository.get_task(submission["task_id"]) if submission else None
    try:
        result = _call(operation, *args, **kwargs)
    except HTTPException as exc:
        if isinstance(audit, AuditService):
            audit.record(
                actor=principal,
                action=action,
                target_type=target_type,
                target_id=target_id,
                project_id=(
                    audit_project_id
                    or (str(task["project_id"]) if task and task["project_id"] else None)
                ),
                before=task,
                after={"detail": exc.detail},
                reason=None,
                result="failure",
                request=request,
            )
        raise
    if isinstance(audit, AuditService):
        audit.record(
            actor=principal,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=(
                audit_project_id
                or (str(task["project_id"]) if task and task["project_id"] else None)
            ),
            before=task,
            after=result,
            reason=None,
            result="success",
            request=request,
        )
    return result
