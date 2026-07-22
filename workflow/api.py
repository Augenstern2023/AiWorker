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
from .schema import (
    ProjectMemberCreateRequest,
    SubmissionCreateRequest,
    SubmissionReturnRequest,
    TaskAssignmentRequest,
)
from .transition_service import TransitionService


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
        "/api/workflow/projects/{project_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def remove_project_member(
        project_id: str,
        user_id: str,
        principal: Principal = Depends(member_manager),
    ) -> Response:
        _call(
            repository.remove_project_member,
            project_id,
            user_id,
            principal,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/api/workflow/tasks/{task_id}/submissions",
        status_code=status.HTTP_201_CREATED,
    )
    def create_submission(
        task_id: str,
        payload: SubmissionCreateRequest,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        _visible_task(repository, task_id, principal)
        if (
            payload.resource_type in creative_resource_types
            and transitions.creative_adapter is None
        ):
            raise HTTPException(
                status_code=503, detail="creative workflow adapter is unavailable"
            )
        return _call(
            transitions.create_submission,
            task_id,
            payload.resource_type,
            payload.resource_id,
            payload.content,
            principal,
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
        return _call(transitions.submit, submission_id, principal)

    @router.post("/api/workflow/submissions/{submission_id}/approve")
    def approve_submission(
        submission_id: str,
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
        return _call(transitions.approve, submission_id, principal)

    @router.post("/api/workflow/submissions/{submission_id}/return")
    def return_submission(
        submission_id: str,
        payload: SubmissionReturnRequest,
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
        return _call(
            transitions.return_submission,
            submission_id,
            principal,
            payload.review_comment,
        )

    @router.post("/api/workflow/submissions/{submission_id}/fork")
    def fork_submission(
        submission_id: str,
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
        return _call(transitions.fork, submission_id, principal)

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
    viewed["can_claim"] = (
        task["status"] == "pool"
        and (
            principal.is_system_admin
            or (
                "workflow.claim" in principal.permissions
                and task["role_code"] in principal.role_codes
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
    _visible_task(repository, submission["task_id"], principal)
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
