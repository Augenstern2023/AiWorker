# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from identity.dependencies import current_principal
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .service import AuditService


def build_audit_router(audit_service: AuditService) -> APIRouter:
    router = APIRouter()
    workflow_repository = WorkflowRepository(
        audit_service.repository.identity_repository
    )

    @router.get("/api/admin/audit")
    def list_audit_events(
        actor_user_id: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        project_id: str | None = None,
        result: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        if not principal.is_system_admin and "audit.read" not in principal.permissions:
            if "audit.read.project" not in principal.permissions or project_id is None:
                raise HTTPException(403, "没有执行此操作的权限")
            try:
                workflow_repository.assert_can_manage_project(project_id, principal)
            except PermissionError as exc:
                raise HTTPException(403, "没有访问此项目审计的权限") from exc
        return audit_service.query(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
            result=result,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )

    return router
