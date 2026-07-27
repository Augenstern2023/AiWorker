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
from collections.abc import Mapping, Sequence
from typing import Any

from identity.permission_catalog import NAVIGATION_CATALOG, PERMISSIONS
from identity.schema import Principal

PAGE_ROUTES = (
    ("/selection/dashboard", "page.selection", "工作概览"),
    ("/selection/ranks", "page.selection", "今日榜单"),
    ("/selection/hits", "page.selection", "今日爆款"),
    ("/selection/topics", "page.selection", "选题库"),
    ("/selection/dramas", "page.selection", "剧目检索下载"),
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
    ("/review/users", "page.team_management", "团队账号"),
    ("/review/roles", "page.team_management", "角色授权"),
    ("/review/projects/{project_id}/members", "page.review", "项目成员"),
    ("/review/projects/{project_id}/versions", "page.review", "项目版本"),
    ("/admin/users", "*", "用户管理"),
    ("/admin/roles", "*", "角色权限"),
    ("/admin/config", "*", "系统配置"),
    ("/admin/data", "page.data_management", "数据管理"),
    ("/admin/drama-player", "page.data_management", "已下载剧目播放"),
    ("/admin/jobs", "*", "机器任务"),
    ("/admin/deliveries", "*", "成果交付"),
    ("/admin/audit", "*", "审计日志"),
)


class NavigationService:
    """Build navigation and landing choices from effective page grants."""

    def __init__(
        self, catalog: Sequence[Mapping[str, Any]] = NAVIGATION_CATALOG
    ) -> None:
        registered_routes = frozenset(route for route, _, _ in PAGE_ROUTES)
        self._catalog = self._validate_catalog(catalog, registered_routes)
        self._route_patterns = tuple(
            (self._compile_route(route), permission)
            for route, permission, _ in PAGE_ROUTES
        )
        self._last_routes: OrderedDict[str, str] = OrderedDict()
        self._last_routes_lock = threading.Lock()

    def items_for(self, principal: Principal) -> list[dict[str, Any]]:
        visible_groups: list[dict[str, Any]] = []
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        roots: list[dict[str, Any]] = []
        for item in self._catalog:
            if not item["is_enabled"]:
                continue
            parent_id = item.get("parent_id")
            if parent_id is None:
                roots.append(item)
            else:
                children_by_parent.setdefault(parent_id, []).append(item)

        for group in roots:
            if principal.is_system_admin and group["hide_for_system_admin"]:
                continue
            items = []
            for item in children_by_parent.get(group["id"], []):
                if (
                    principal.is_system_admin
                    or item["required_permission"] in principal.permissions
                ):
                    items.append({"label": item["label"], "route": item["route"]})
            if not items and (
                principal.is_system_admin
                or group["required_permission"] in principal.permissions
            ):
                items.append({"label": group["label"], "route": group["route"]})
            if not items:
                continue
            visible_groups.append(
                {
                    "label": group["label"],
                    "icon": group["icon"],
                    "route": items[0]["route"],
                    "items": tuple(items),
                }
            )
        return visible_groups

    @staticmethod
    def _validate_catalog(
        catalog: Sequence[Mapping[str, Any]], registered_routes: frozenset[str]
    ) -> tuple[dict[str, Any], ...]:
        configured: list[dict[str, Any]] = []
        identifiers: set[str] = set()
        for raw_item in catalog:
            item = dict(raw_item)
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("navigation item id is required")
            if identifier in identifiers:
                raise ValueError(f"duplicate navigation item id: {identifier}")
            identifiers.add(identifier)
            permission = item.get("required_permission")
            if permission not in PERMISSIONS:
                raise ValueError(f"unknown permission: {permission}")
            if not isinstance(item.get("label"), str) or not item["label"]:
                raise ValueError("navigation item label is required")
            if not isinstance(item.get("route"), str) or not item["route"].startswith(
                "/"
            ):
                raise ValueError("navigation item route must be an internal path")
            if item["route"] not in registered_routes:
                raise ValueError(f"unknown page route: {item['route']}")
            item.setdefault("parent_id", None)
            item.setdefault("icon", "")
            item.setdefault("sort_order", 0)
            item.setdefault("is_enabled", True)
            item.setdefault("hide_for_system_admin", False)
            configured.append(item)
        for item in configured:
            parent_id = item["parent_id"]
            if parent_id is not None and parent_id not in identifiers:
                raise ValueError(f"unknown navigation parent: {parent_id}")
        return tuple(
            sorted(
                configured,
                key=lambda item: (
                    item["parent_id"] is not None,
                    item["parent_id"] or "",
                    item["sort_order"],
                    item["id"],
                ),
            )
        )

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
