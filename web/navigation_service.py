# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from copy import deepcopy
from typing import Any

from identity.schema import Principal


NAVIGATION_CATALOG = (
    {
        "label": "选品工作台",
        "icon": "bi-funnel",
        "permission": "page.selection",
        "route": "/selection/dashboard",
        "items": (
            {"label": "工作概览", "route": "/selection/dashboard"},
            {"label": "今日榜单", "route": "/selection/ranks"},
            {"label": "今日爆款", "route": "/selection/hits"},
            {"label": "选题库", "route": "/selection/topics"},
            {"label": "剧目检索下载", "route": "/selection/dramas"},
            {"label": "剧目拆解", "route": "/selection/breakdowns"},
        ),
    },
    {
        "label": "创意策划",
        "icon": "bi-lightbulb",
        "permission": "page.planning",
        "route": "/planning/projects",
        "items": (
            {"label": "策划项目", "route": "/planning/projects"},
        ),
    },
    {
        "label": "编剧工作台",
        "icon": "bi-pencil-square",
        "permission": "page.writing",
        "route": "/writing/projects",
        "items": (
            {"label": "编剧项目", "route": "/writing/projects"},
        ),
    },
    {
        "label": "审批中心",
        "icon": "bi-clipboard-check",
        "permission": "page.review",
        "route": "/review/tasks",
        "items": (
            {"label": "负责项目", "route": "/review/projects"},
            {"label": "审批任务", "route": "/review/tasks"},
            {"label": "审批详情", "route": "/review/approvals"},
        ),
    },
    {
        "label": "团队管理",
        "icon": "bi-people",
        "permission": "page.team_management",
        "route": "/review/users",
        "hide_for_system_admin": True,
        "items": (
            {"label": "团队账号", "route": "/review/users"},
            {"label": "角色授权", "route": "/review/roles"},
        ),
    },
    {
        "label": "系统管理",
        "icon": "bi-gear",
        "permission": "*",
        "route": "/admin/users",
        "items": (
            {"label": "用户管理", "route": "/admin/users", "permission": "*"},
            {"label": "角色权限", "route": "/admin/roles", "permission": "*"},
            {"label": "系统配置", "route": "/admin/config", "permission": "*"},
            {"label": "数据管理", "route": "/admin/data", "permission": "*"},
            {"label": "系统后台", "route": "/admin/jobs", "permission": "*"},
            {
                "label": "成果交付",
                "route": "/admin/deliveries",
                "permission": "*",
            },
            {"label": "审计日志", "route": "/admin/audit", "permission": "*"},
        ),
    },
)

PAGE_ROUTES = (
    ("/selection/dashboard", "page.selection", "工作概览"),
    ("/selection/ranks", "page.selection", "今日榜单"),
    ("/selection/hits", "page.selection", "今日爆款"),
    ("/selection/topics", "page.selection", "选题库"),
    ("/selection/dramas", "page.selection", "剧目检索下载"),
    ("/selection/breakdowns", "page.selection", "剧目拆解"),
    ("/planning/projects", "page.planning", "策划项目"),
    ("/planning/projects/{project_id}/brief", "page.planning", "创作任务书"),
    ("/planning/projects/{project_id}/characters", "page.planning", "人物关系"),
    ("/writing/projects", "page.writing", "编剧项目"),
    ("/writing/projects/{project_id}/outlines", "page.writing", "全剧大纲"),
    (
        "/writing/projects/{project_id}/episodes/{episode_number}/beats",
        "page.writing",
        "单集节拍",
    ),
    ("/review/projects", "page.review", "负责项目"),
    ("/review/tasks", "page.review", "审批任务"),
    ("/review/approvals", "page.review", "审批详情"),
    ("/review/users", "page.team_management", "团队账号"),
    ("/review/roles", "page.team_management", "角色授权"),
    ("/review/projects/{project_id}/members", "page.review", "项目成员"),
    ("/review/projects/{project_id}/versions", "page.review", "项目版本"),
    ("/admin/users", "*", "用户管理"),
    ("/admin/roles", "*", "角色权限"),
    ("/admin/config", "*", "系统配置"),
    ("/admin/data", "*", "数据管理"),
    ("/admin/jobs", "*", "机器任务"),
    ("/admin/deliveries", "*", "成果交付"),
    ("/admin/audit", "*", "审计日志"),
)


class NavigationService:
    """Build navigation and landing choices from effective page grants."""

    def __init__(self) -> None:
        self._route_patterns = tuple(
            (self._compile_route(route), permission)
            for route, permission, _ in PAGE_ROUTES
        )
        self._last_routes: OrderedDict[str, str] = OrderedDict()
        self._last_routes_lock = threading.Lock()

    def items_for(self, principal: Principal) -> list[dict[str, Any]]:
        visible_groups = []
        for group in NAVIGATION_CATALOG:
            if principal.is_system_admin and group.get("hide_for_system_admin"):
                continue
            items = [
                deepcopy(item)
                for item in group["items"]
                if principal.is_system_admin
                or item.get("permission", group["permission"])
                in principal.permissions
            ]
            if not items:
                continue
            visible_group = deepcopy(group)
            visible_group["items"] = tuple(items)
            visible_group["route"] = items[0]["route"]
            visible_groups.append(visible_group)
        return visible_groups

    def can_open(self, principal: Principal, route: str) -> bool:
        permission = self.permission_for(route)
        if permission is None:
            return route == "/tasks"
        return principal.is_system_admin or permission in principal.permissions

    def permission_for(self, route: str) -> str | None:
        path = route.split("?", 1)[0]
        for pattern, permission in self._route_patterns:
            if pattern.fullmatch(path):
                return permission
        return None

    def default_route(self, principal: Principal) -> str:
        items = self.items_for(principal)
        return items[0]["route"] if items else "/tasks"

    def remember_route(self, principal: Principal, route: str) -> None:
        if not self.can_open(principal, route):
            return
        with self._last_routes_lock:
            self._last_routes.pop(principal.session_id, None)
            self._last_routes[principal.session_id] = route
            if len(self._last_routes) > 4096:
                self._last_routes.popitem(last=False)

    def last_route_for(self, principal: Principal) -> str | None:
        with self._last_routes_lock:
            route = self._last_routes.get(principal.session_id)
            if route is not None:
                self._last_routes.move_to_end(principal.session_id)
            return route

    def forget_route(self, principal: Principal) -> None:
        with self._last_routes_lock:
            self._last_routes.pop(principal.session_id, None)

    @staticmethod
    def _compile_route(route: str) -> re.Pattern[str]:
        escaped = re.escape(route)
        dynamic = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
        return re.compile(dynamic)
