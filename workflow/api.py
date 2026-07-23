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
    SubmissionCreateRequest,
    SubmissionReturnRequest,
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
    creative_resource_types = {
        "breakdown", "brief", "characters", "outline", "beats"
    }

    @router.get("/api/workflow/tasks")
    def list_tasks(
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        return [
            _task_view(repository, task, principal)
            for task in repository.list_tasks(principal)
        ]

    @router.get("/api/workflow/approval-flow")
    def list_approval_flow(
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        return repository.list_approval_flow(principal)

    @router.get("/api/workflow/my-submissions")
    def list_my_submissions(
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        return _call(repository.list_my_submissions, principal)

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
        principal: Principal = Depends(require_permission("workflow.claim")),
    ) -> dict[str, Any]:
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
        principal: Principal = Depends(
            require_permission("workflow.task.update")
        ),
    ) -> dict[str, Any]:
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
        return repository.list_project_members(project_id)

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

    @router.post(
        "/api/workflow/tasks/{task_id}/submissions",
        status_code=status.HTTP_201_CREATED,
    )
    def create_submission(
        task_id: str,
        payload: SubmissionCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        def create_visible_submission() -> dict[str, Any]:
            _visible_task(repository, task_id, principal)
            if (
                payload.resource_type in creative_resource_types
                and transitions.creative_adapter is None
            ):
                raise HTTPException(
                    status_code=503,
                    detail="creative workflow adapter is unavailable",
                )
            return transitions.create_submission(
                task_id,
                payload.resource_type,
                payload.resource_id,
                payload.content,
                principal,
            )

        return _audited_call(
            repository,
            request,
            principal,
            "workflow.submission.create",
            "task",
            task_id,
            create_visible_submission,
        )

    @router.get("/api/workflow/tasks/{task_id}/submissions")
    def list_submissions(
        task_id: str,
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        _visible_task(repository, task_id, principal)
        return _call(transitions.list_task_submissions, task_id)

    @router.post("/api/workflow/submissions/{submission_id}/submit")
    def submit_submission(
        submission_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        submission = _visible_submission(repository, submission_id, principal)
        if (
            submission["resource_type"] in creative_resource_types
            and transitions.creative_adapter is None
        ):
            raise HTTPException(
                status_code=503, detail="creative workflow adapter is unavailable"
            )
        return _audited_call(
            repository, request, principal, "workflow.submit", "submission",
            submission_id, transitions.submit, submission_id, principal
        )

    @router.post("/api/workflow/submissions/{submission_id}/approve")
    def approve_submission(
        submission_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        submission = _visible_submission(repository, submission_id, principal)
        if (
            submission["resource_type"] in creative_resource_types
            and transitions.creative_adapter is None
        ):
            raise HTTPException(
                status_code=503, detail="creative workflow adapter is unavailable"
            )
        return _audited_call(
            repository, request, principal, "workflow.approve", "submission",
            submission_id, transitions.approve, submission_id, principal
        )

    @router.post("/api/workflow/submissions/{submission_id}/return")
    def return_submission(
        submission_id: str,
        payload: SubmissionReturnRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        submission = _visible_submission(repository, submission_id, principal)
        if (
            submission["resource_type"] in creative_resource_types
            and transitions.creative_adapter is None
        ):
            raise HTTPException(
                status_code=503, detail="creative workflow adapter is unavailable"
            )
        return _audited_call(
            repository, request, principal, "workflow.return", "submission",
            submission_id, transitions.return_submission,
            submission_id,
            principal,
            payload.review_comment,
        )

    @router.post("/api/workflow/submissions/{submission_id}/fork")
    def fork_submission(
        submission_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        submission = _visible_submission(repository, submission_id, principal)
        if (
            submission["resource_type"] in creative_resource_types
            and transitions.creative_adapter is None
        ):
            raise HTTPException(
                status_code=503, detail="creative workflow adapter is unavailable"
            )
        return _audited_call(
            repository, request, principal, "workflow.fork", "submission",
            submission_id, transitions.fork, submission_id, principal
        )

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
            or (
                "workflow.claim" in principal.permissions
                and repository.can_claim_task(task["id"], principal)
            )
        )
    )
    viewed["can_start"] = (
        task["status"] in {"assigned", "returned"}
        and task["assignee_user_id"] == principal.user_id
        and (
            principal.is_system_admin
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
    viewed["can_approve"] = (
        task["status"] == "waiting_review"
        and repository.can_review_task(task["id"], principal, "version.approve")
    )
    viewed["can_return"] = (
        task["status"] == "waiting_review"
        and repository.can_review_task(task["id"], principal, "version.return")
    )
    return viewed


def _visible_submission(
    repository: WorkflowRepository,
    submission_id: str,
    principal: Principal,
) -> dict[str, Any]:
    submission = repository.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="submission not found")
    task = repository.get_visible_task(submission["task_id"], principal)
    if task is None and not repository.can_review_task(
        submission["task_id"], principal, "version.approve"
    ) and not repository.can_review_task(
        submission["task_id"], principal, "version.return"
    ):
        raise HTTPException(status_code=404, detail="workflow task not found")
    return submission


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
