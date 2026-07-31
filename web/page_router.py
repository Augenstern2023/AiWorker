# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from collections.abc import Callable
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from identity.dependencies import current_principal
from identity.schema import Principal

from .navigation_service import PAGE_ROUTES, NavigationService


ASSET_VERSION = str(time.time_ns())


PAGE_TEMPLATES = {
    "/selection/dashboard": "selection/dashboard.html",
    "/selection/ranks": "selection/ranks.html",
    "/selection/hits": "selection/hits.html",
    "/selection/topics": "selection/topics.html",
    "/selection/dramas": "selection/dramas.html",
    "/planning/projects": "planning/projects.html",
    "/planning/projects/{project_id}/brief": "planning/brief.html",
    "/planning/projects/{project_id}/characters": "planning/characters.html",
    "/writing/projects": "writing/projects.html",
    "/writing/projects/{project_id}/outlines": "writing/outlines.html",
    "/writing/projects/{project_id}/episodes/{episode_number}/beats": "writing/beats.html",
    "/writing/projects/{project_id}/episodes/{episode_number}/scripts": "writing/scripts.html",
    "/review/projects": "review/projects.html",
    "/review/users": "admin/users.html",
    "/review/roles": "admin/roles.html",
    "/review/projects/{project_id}/members": "review/projects.html",
    "/review/projects/{project_id}/versions": "review/versions.html",
    "/admin/users": "admin/users.html",
    "/admin/roles": "admin/roles.html",
    "/admin/config": "admin/config.html",
    "/admin/data": "admin/data.html",
    "/admin/drama-player": "admin/drama_player.html",
    "/admin/jobs": "admin/jobs.html",
    "/admin/deliveries": "admin/deliveries.html",
    "/admin/audit": "admin/audit.html",
    "/admin/departments": "admin/departments.html",
    "/admin/navigation": "admin/navigation.html",
    "/admin/schedules": "admin/schedules.html",
}


def build_page_router(
    templates: Jinja2Templates, navigation_service: NavigationService
) -> APIRouter:
    def require_page_access(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> Principal:
        if not navigation_service.can_open(principal, request.url.path):
            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
        return principal

    class PageRoute(APIRoute):
        def get_route_handler(self) -> Callable:
            handler = super().get_route_handler()

            async def handle(request: Request) -> Response:
                try:
                    return await handler(request)
                except HTTPException as exc:
                    if exc.status_code == 401:
                        return RedirectResponse("/login", status_code=303)
                    if exc.status_code != 403:
                        raise
                    principal = _principal_from_request(request)
                    return templates.TemplateResponse(
                        request,
                        "common/403.html",
                        _template_context(
                            request,
                            principal,
                            navigation_service,
                            page_title="无权访问",
                        ),
                        status_code=403,
                    )

            return handle

    router = APIRouter(route_class=PageRoute)

    @router.get("/", include_in_schema=False)
    def landing(
        request: Request, principal: Principal = Depends(current_principal)
    ) -> RedirectResponse:
        return RedirectResponse("/profile", status_code=303)

    @router.get("/profile", response_class=HTMLResponse)
    def profile(
        request: Request, principal: Principal = Depends(current_principal)
    ) -> Response:
        return _page_response(
            templates,
            navigation_service,
            request,
            principal,
            page_title="个人信息",
            template_name="profile.html",
        )

    @router.get("/tasks", response_class=HTMLResponse)
    def tasks(
        request: Request, principal: Principal = Depends(require_page_access)
    ) -> Response:
        return _page_response(
            templates,
            navigation_service,
            request,
            principal,
            page_title="我的任务",
            template_name="common/my_tasks.html",
        )

    @router.get("/planning/tasks", response_class=HTMLResponse)
    def planning_tasks_redirect(
        principal: Principal = Depends(require_page_access),
    ) -> RedirectResponse:
        return RedirectResponse("/tasks", status_code=307)

    @router.get("/writing/tasks", response_class=HTMLResponse)
    def writing_tasks_redirect(
        principal: Principal = Depends(require_page_access),
    ) -> RedirectResponse:
        return RedirectResponse("/tasks", status_code=307)

    @router.get("/admin/audit", response_class=HTMLResponse)
    def audit_page(
        request: Request, principal: Principal = Depends(require_page_access)
    ) -> Response:
        return _page_response(
            templates,
            navigation_service,
            request,
            principal,
            page_title="审计日志",
            template_name="admin/audit.html",
        )

    def page_endpoint(page_title: str, permission: str, template_name: str):
        def endpoint(
            request: Request,
            principal: Principal = Depends(require_page_access),
        ) -> Response:
            return _page_response(
                templates,
                navigation_service,
                request,
                principal,
                page_title=page_title,
                template_name=template_name,
            )

        return endpoint

    @router.get(
        "/planning/projects/{project_id}/directions",
        include_in_schema=False,
    )
    def legacy_directions_page(
        project_id: str,
        principal: Principal = Depends(require_page_access),
    ) -> RedirectResponse:
        return RedirectResponse(
            f"/planning/projects/{project_id}/brief", status_code=307
        )

    @router.get("/planning/projects", response_class=HTMLResponse)
    def planning_projects_page(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> Response:
        if navigation_service.can_open(principal, "/planning/projects"):
            return _page_response(
                templates,
                navigation_service,
                request,
                principal,
                page_title="策划项目",
                template_name="planning/projects.html",
            )
        if navigation_service.can_open(principal, "/review/projects"):
            return RedirectResponse("/review/projects", status_code=307)
        raise HTTPException(status_code=403, detail="没有执行此操作的权限")

    for route, permission, title in PAGE_ROUTES:
        if route in {"/admin/audit", "/planning/projects"}:
            continue
        router.add_api_route(
            route,
            page_endpoint(title, permission, PAGE_TEMPLATES[route]),
            methods=["GET"],
            response_class=HTMLResponse,
            name=f"page_{permission}_{title}",
            openapi_extra={"required_page_permission": permission},
        )

    return router


def _page_response(
    templates: Jinja2Templates,
    navigation_service: NavigationService,
    request: Request,
    principal: Principal,
    *,
    page_title: str,
    template_name: str = "common/tasks.html",
) -> Response:
    response = templates.TemplateResponse(
        request,
        template_name,
        _template_context(
            request,
            principal,
            navigation_service,
            page_title=page_title,
            workspace_page=(
                "tasks"
                if template_name in {"common/tasks.html", "common/my_tasks.html"}
                else template_name.removesuffix(".html").replace("/", "-").replace("_", "-")
            ),
        ),
    )
    navigation_service.remember_route(principal, request.url.path)
    return response


def _template_context(
    request: Request,
    principal: Principal | None,
    navigation_service: NavigationService,
    *,
    page_title: str,
    workspace_page: str = "",
) -> dict:
    csrf_token = ""
    service = getattr(request.app.state, "identity_permission_service", None)
    if principal is not None and service is not None:
        raw_token = request.cookies.get(service.session_service.cookie_name)
        session = service.session_service.resolve_session(raw_token)
        csrf_token = session.csrf_token if session is not None else ""
    return {
        "request": request,
        "principal": principal,
        "navigation": navigation_service.items_for(principal) if principal else [],
        "current_route": request.url.path,
        "page_title": page_title,
        "workspace_page": workspace_page,
        "can_manage_data": bool(
            principal
            and (
                principal.is_system_admin
                or "data.media.delete" in principal.permissions
            )
        ),
        "can_preview_data": bool(
            principal
            and (
                principal.is_system_admin
                or "data.media.preview" in principal.permissions
            )
        ),
        "asset_version": ASSET_VERSION,
        "csrf_token": csrf_token,
        "show_tasks": bool(
            principal and navigation_service.can_open(principal, "/tasks")
        ),
        "can_open_planning_projects": bool(
            principal
            and navigation_service.can_open(principal, "/planning/projects")
        ),
        **request.path_params,
    }


def _principal_from_request(request: Request) -> Principal | None:
    service = getattr(request.app.state, "identity_permission_service", None)
    if service is None:
        return None
    raw_token = request.cookies.get(service.session_service.cookie_name)
    return service.principal_for_session(raw_token)
