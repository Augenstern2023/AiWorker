# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""System-administrator HTTP endpoints for organization management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from identity.dependencies import require_page_api
from identity.schema import Principal
from organization.service import DepartmentService


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    sort_order: int = 0


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = None


class DepartmentAssignmentRequest(BaseModel):
    is_primary: bool = False


def build_organization_router(
    service: DepartmentService, audit_service: Any
) -> APIRouter:
    router = APIRouter()

    administrator = require_page_api()

    def record(
        principal: Principal,
        action: str,
        target_type: str,
        target_id: str | None,
        before: Any,
        after: Any,
    ) -> None:
        audit_service.record(
            actor=principal,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            reason=None,
            result="success",
        )

    @router.get("/api/admin/departments")
    def list_departments(_: Principal = Depends(administrator)) -> list[dict[str, Any]]:
        return service.list_tree()

    @router.post("/api/admin/departments", status_code=201)
    def create_department(
        payload: DepartmentCreateRequest,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        department = service.create_department(
            payload.name, parent_id=payload.parent_id, sort_order=payload.sort_order
        )
        record(principal, "organization.department.create", "department", department["id"], None, department)
        return department

    @router.patch("/api/admin/departments/{department_id}")
    def update_department(
        department_id: str,
        payload: DepartmentUpdateRequest,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        before = service.get_department(department_id)
        if before is None:
            raise HTTPException(404, "部门不存在")
        department = before
        if payload.name is not None:
            department = service.rename_department(department_id, payload.name)
            record(principal, "organization.department.rename", "department", department_id, before, department)
        if "parent_id" in payload.model_fields_set and payload.parent_id != before["parent_id"]:
            moved_from = department
            department = service.move_department(department_id, payload.parent_id)
            record(principal, "organization.department.move", "department", department_id, moved_from, department)
        return department

    @router.post("/api/admin/departments/{department_id}/disable")
    def disable_department(
        department_id: str, principal: Principal = Depends(administrator)
    ) -> dict[str, Any]:
        before = service.get_department(department_id)
        if before is None:
            raise HTTPException(404, "部门不存在")
        try:
            department = service.disable_department(department_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        record(principal, "organization.department.disable", "department", department_id, before, department)
        return department

    @router.delete("/api/admin/departments/{department_id}", status_code=204)
    def delete_department(
        department_id: str, principal: Principal = Depends(administrator)
    ) -> Response:
        before = service.get_department(department_id)
        if before is None:
            raise HTTPException(404, "部门不存在")
        try:
            service.delete_department(department_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        record(principal, "organization.department.delete", "department", department_id, before, None)
        return Response(status_code=204)

    @router.get("/api/admin/users/{user_id}/departments")
    def list_user_departments(
        user_id: str, _: Principal = Depends(administrator)
    ) -> list[dict[str, Any]]:
        return service.list_user_departments(user_id)

    @router.put("/api/admin/users/{user_id}/departments/{department_id}")
    def assign_user_department(
        user_id: str,
        department_id: str,
        payload: DepartmentAssignmentRequest,
        principal: Principal = Depends(administrator),
    ) -> dict[str, Any]:
        assignment = service.assign_user_department(
            user_id, department_id, is_primary=payload.is_primary
        )
        record(
            principal,
            "organization.user_department.assign",
            "user_department",
            f"{user_id}:{department_id}",
            None,
            assignment,
        )
        return assignment

    @router.delete(
        "/api/admin/users/{user_id}/departments/{department_id}", status_code=204
    )
    def remove_user_department(
        user_id: str,
        department_id: str,
        principal: Principal = Depends(administrator),
    ) -> Response:
        try:
            assignment = service.remove_user_department(user_id, department_id)
        except KeyError as exc:
            raise HTTPException(404, "用户部门归属不存在") from exc
        record(
            principal,
            "organization.user_department.remove",
            "user_department",
            f"{user_id}:{department_id}",
            assignment,
            None,
        )
        return Response(status_code=204)

    return router
