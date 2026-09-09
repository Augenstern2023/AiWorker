# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Web endpoints for complete production package exports.

Authorization is project-scoped on purpose: system admins plus owners covered by
the managed-project scope may preview, create, list and download. Page access
alone never opens these endpoints, and the client only ever sends the project id
and the confirmed fingerprint - never a database path, output path, or package
body. Responses stay server-relative; downloads are attachments with no-store.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from identity.dependencies import current_principal, permission_service_for_request
from identity.permission_service import PermissionService
from identity.schema import Principal

from .production_exports import (
    AuditUnavailable,
    ExportBlocked,
    ExportUnavailable,
    FingerprintConflict,
    ProductionExportService,
)


def build_production_export_router(service: ProductionExportService) -> APIRouter:
    router = APIRouter(prefix="/api/creative", tags=["production-exports"])

    def assert_project_access(request: Request, principal: Principal, project_id: int) -> None:
        if principal.is_system_admin:
            return
        permission_service = permission_service_for_request(request)
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        if not permission_service.can_access_managed_project(principal, str(project_id)):
            raise HTTPException(403, "没有导出该项目生产包的权限")

    def verify_csrf(request: Request) -> None:
        permission_service = permission_service_for_request(request)
        raw_token = request.cookies.get(permission_service.session_service.cookie_name)
        if not permission_service.session_service.verify_csrf(
            raw_token or "", request.headers.get("X-CSRF-Token")
        ):
            raise HTTPException(403, "CSRF 校验失败")

    @router.get("/projects/{project_id}/production-export/preview")
    def preview_export(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_access(request, principal, project_id)
        try:
            return service.preview(project_id)
        except KeyError as exc:
            raise HTTPException(404, "创作项目不存在") from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "创作项目数据库不存在") from exc
        except ValueError as exc:
            raise HTTPException(500, "生产包检查失败，请稍后重试") from exc

    @router.post("/projects/{project_id}/production-exports")
    def create_export(
        project_id: int,
        payload: dict,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> JSONResponse:
        verify_csrf(request)
        assert_project_access(request, principal, project_id)
        expected = str(payload.get("expected_fingerprint") or "")
        if len(expected) != 64:
            raise HTTPException(409, "请先完成导出检查并确认当前版本")
        try:
            record, created = service.create(project_id, expected, principal, request)
        except KeyError as exc:
            raise HTTPException(404, "创作项目不存在") from exc
        except ExportBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        except FingerprintConflict as exc:
            raise HTTPException(409, "版本已变化，请重新检查并确认") from exc
        except ExportUnavailable as exc:
            raise HTTPException(410, str(exc)) from exc
        except AuditUnavailable as exc:
            raise HTTPException(503, "审计记录暂不可用，请稍后重试") from exc
        return JSONResponse(
            status_code=201 if created else 200,
            content={"export": record, "created": created},
        )

    @router.get("/projects/{project_id}/production-exports")
    def list_exports(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        assert_project_access(request, principal, project_id)
        return service.list_for_project(project_id, page=page, page_size=page_size)

    @router.get("/production-exports/{export_id}/download")
    def download_export(
        export_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> FileResponse:
        try:
            path, filename = service.download_target(
                export_id,
                principal,
                request,
                can_access=lambda project_id: (
                    principal.is_system_admin
                    or permission_service_for_request(
                        request
                    ).can_access_managed_project(principal, str(project_id))
                ),
            )
        except KeyError as exc:
            raise HTTPException(404, "导出记录或创作项目不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except AuditUnavailable as exc:
            raise HTTPException(503, "审计记录暂不可用，请稍后重试") from exc
        except ExportUnavailable as exc:
            raise HTTPException(410, str(exc)) from exc
        # Explicit UTF-8 disposition keeps the Chinese download name intact
        # across browsers; no-store prevents proxies from caching content that
        # is authorized per request.
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return FileResponse(
            path,
            media_type="application/json",
            headers={
                "content-disposition": disposition,
                "cache-control": "no-store",
            },
        )

    return router
