# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request

from .permission_service import PermissionService
from .schema import Principal


ScopeResolver = Callable[
    [PermissionService, Principal, Request], bool | Awaitable[bool]
]


def permission_service_for_request(request: Request) -> PermissionService:
    service = getattr(request.state, "identity_permission_service", None)
    if service is None:
        service = getattr(request.app.state, "identity_permission_service", None)
    if not isinstance(service, PermissionService):
        raise RuntimeError("identity permission service is not configured")
    return service


def current_principal(
    request: Request,
    permission_service: PermissionService = Depends(permission_service_for_request),
) -> Principal:
    raw_token = request.cookies.get(permission_service.session_service.cookie_name)
    principal = permission_service.principal_for_session(raw_token)
    if principal is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return principal


def require_permission(
    code: str, scope_resolver: ScopeResolver | None = None
) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        request: Request,
        principal: Principal = Depends(current_principal),
        permission_service: PermissionService = Depends(permission_service_for_request),
    ) -> Principal:
        if not principal.is_system_admin and code not in principal.permissions:
            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
        if scope_resolver is not None:
            allowed = scope_resolver(permission_service, principal, request)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise HTTPException(status_code=403, detail="没有访问此数据的权限")
        return principal

    return dependency


def require_page(code: str) -> Callable[..., Awaitable[Principal]]:
    return require_permission(code)


def task_scope(path_parameter: str = "task_id") -> ScopeResolver:
    def resolve(
        service: PermissionService, principal: Principal, request: Request
    ) -> bool:
        return service.can_access_task(principal, request.path_params[path_parameter])

    return resolve


def assigned_project_scope(path_parameter: str = "project_id") -> ScopeResolver:
    def resolve(
        service: PermissionService, principal: Principal, request: Request
    ) -> bool:
        return service.can_access_assigned_project(
            principal, request.path_params[path_parameter]
        )

    return resolve


def managed_project_scope(path_parameter: str = "project_id") -> ScopeResolver:
    def resolve(
        service: PermissionService, principal: Principal, request: Request
    ) -> bool:
        return service.can_access_managed_project(
            principal, request.path_params[path_parameter]
        )

    return resolve


def all_data_scope() -> ScopeResolver:
    def resolve(
        service: PermissionService, principal: Principal, request: Request
    ) -> bool:
        del service, request
        return PermissionService.can_access_all(principal)

    return resolve
