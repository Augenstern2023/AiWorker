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
_PAGE_API_DECISION_UNSET = object()


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


def permission_granted(request: Request, principal: Principal, code: str) -> bool:
    """Return whether an operation is granted by its page or permission code."""

    page_api_decision = _page_api_decision(request, principal)
    if page_api_decision is not None:
        return page_api_decision
    return principal.is_system_admin or code in principal.permissions


def page_api_access_granted(request: Request, principal: Principal) -> bool:
    """Return whether the current API is explicitly granted by a bound page."""

    return _page_api_decision(request, principal) is True


def enforce_permission(request: Request, principal: Principal, code: str) -> None:
    """Enforce an operation permission unless a bound page grant replaces it."""

    if not permission_granted(request, principal, code):
        raise HTTPException(status_code=403, detail="没有执行此操作的权限")


def require_permission(
    code: str, scope_resolver: ScopeResolver | None = None
) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        request: Request,
        principal: Principal = Depends(current_principal),
        permission_service: PermissionService = Depends(permission_service_for_request),
    ) -> Principal:
        enforce_permission(request, principal, code)
        if scope_resolver is not None:
            allowed = scope_resolver(permission_service, principal, request)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise HTTPException(status_code=403, detail="没有访问此数据的权限")
        return principal

    return dependency


def require_page_api() -> Callable[..., Awaitable[Principal]]:
    """Allow a bound page API grant, with the legacy admin gate as fallback."""

    async def dependency(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        decision = _page_api_decision(request, principal)
        if decision is False:
            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
        if decision is not True and not principal.is_system_admin:
            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
        return principal

    return dependency


def _page_api_decision(request: Request, principal: Principal) -> bool | None:
    decision = getattr(
        request.state, "page_api_access_decision", _PAGE_API_DECISION_UNSET
    )
    if decision is not _PAGE_API_DECISION_UNSET:
        return decision if decision in {True, False, None} else None
    service = getattr(request.app.state, "page_access_service", None)
    checker = getattr(service, "can_call_page_api", None)
    if not callable(checker):
        return None
    decision = checker(principal, request.method.upper(), request.url.path)
    request.state.page_api_access_decision = decision
    return decision


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
