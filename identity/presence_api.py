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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .permission_service import PermissionService
from .presence_service import PresenceService
from .schema import Principal
from .session_service import SessionService


class PresenceHeartbeatRequest(BaseModel):
    tab_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    path: str = Field(min_length=1, max_length=512, pattern=r"^/.*")
    is_visible: bool
    idle_seconds: float = Field(ge=0, le=86400)


def build_presence_router(
    service: PresenceService,
    session_service: SessionService,
    *,
    permission_service: PermissionService | None = None,
) -> APIRouter:
    permission_service = permission_service or PermissionService(
        service.repository, session_service
    )
    router = APIRouter()

    def current_principal(request: Request) -> Principal:
        principal = getattr(request.state, "principal", None)
        if isinstance(principal, Principal):
            return principal
        raw_token = request.cookies.get(session_service.cookie_name)
        principal = permission_service.principal_for_session(raw_token)
        if principal is None:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return principal

    def system_admin(
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        if "system_admin" not in principal.role_codes:
            raise HTTPException(status_code=403, detail="没有查看在线情况的权限")
        return principal

    @router.post("/api/presence/heartbeat", status_code=204)
    def heartbeat(
        payload: PresenceHeartbeatRequest,
        principal: Principal = Depends(current_principal),
    ) -> Response:
        service.record_heartbeat(
            user_id=principal.user_id,
            session_id=principal.session_id,
            tab_id=payload.tab_id,
            path=payload.path,
            is_visible=payload.is_visible,
            idle_seconds=payload.idle_seconds,
        )
        return Response(status_code=204)

    @router.get("/api/admin/presence")
    def presence_snapshot(
        _principal: Principal = Depends(system_admin),
    ) -> dict[str, Any]:
        return service.snapshot()

    return router
