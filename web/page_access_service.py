# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Account page whitelist authorization shared by pages and bound APIs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from identity.schema import Principal
from web.page_access_catalog import PAGE_ACCESS_CATALOG
from web.page_routes import (
    DENIED_PAGE_ROUTES,
    PAGE_ACCESS_ADDITIONAL_GRANT_ROUTES,
    PAGE_ACCESS_GRANT_ROUTES,
)


class PageAccessRepository(Protocol):
    def list_items(self) -> list[dict[str, Any]]: ...

    def list_page_access_user_ids(self, route: str) -> set[str]: ...

    def get_page_access_override(self, user_id: str, route: str) -> bool | None: ...

    def list_role_page_access_routes(self, role_codes: tuple[str, ...]) -> set[str]: ...


class PageAccessService:
    """Resolve effective page grants from roles and account-level overrides."""

    def __init__(
        self,
        repository: PageAccessRepository,
        catalog: Sequence[Mapping[str, Any]] = PAGE_ACCESS_CATALOG,
    ) -> None:
        self.repository = repository
        self._catalog = tuple(dict(entry) for entry in catalog)
        self._page_patterns = tuple(
            (_compile_path(entry["page_route"]), entry["page_route"])
            for entry in self._catalog
        )
        catalog_page_routes = {entry["page_route"] for entry in self._catalog}
        self._grant_alias_patterns = tuple(
            (_compile_path(page_route), page_route)
            for page_route in PAGE_ACCESS_GRANT_ROUTES
            if page_route not in catalog_page_routes
        )
        api_patterns: list[tuple[str, re.Pattern[str], str]] = []
        for entry in self._catalog:
            grant_route = self._grant_route(entry["page_route"])
            for method, path_template in entry["api_routes"]:
                api_patterns.append(
                    (method.upper(), _compile_path(path_template), grant_route)
                )
        self._api_patterns = tuple(api_patterns)

    def can_open_page(self, principal: Principal, route: str) -> bool:
        if route == "/profile":
            return True
        if self._is_system_admin(principal):
            return True
        if route == "/admin/navigation":
            return False
        if route in DENIED_PAGE_ROUTES:
            return False
        page_route = self._page_route_for(route)
        if page_route is None:
            return False
        return any(
            self._is_enabled(grant_route)
            and self._is_allowed(principal, grant_route)
            for grant_route in self._grant_routes(page_route)
        )

    def can_call_page_api(
        self, principal: Principal, method: str, path: str
    ) -> bool | None:
        normalized_method = method.upper()
        matching_grant_routes = {
            grant_route
            for registered_method, pattern, grant_route in self._api_patterns
            if registered_method == normalized_method and pattern.fullmatch(path)
        }
        if not matching_grant_routes:
            return None
        if self._is_system_admin(principal):
            return True
        for grant_route in matching_grant_routes:
            if grant_route == "/admin/navigation":
                continue
            if not self._is_enabled(grant_route):
                continue
            if self._is_allowed(principal, grant_route):
                return True
        return False

    def pages_for(self, principal: Principal) -> frozenset[str]:
        if self._is_system_admin(principal):
            return frozenset(
                {"/profile", *(entry["page_route"] for entry in self._catalog)}
            )
        return frozenset(
            {"/profile"}
            | {
                entry["page_route"]
                for entry in self._catalog
                if self.can_open_page(principal, entry["page_route"])
            }
        )

    def _page_route_for(self, route: str) -> str | None:
        for pattern, page_route in self._page_patterns:
            if pattern.fullmatch(route):
                return page_route
        for pattern, page_route in self._grant_alias_patterns:
            if pattern.fullmatch(route):
                return page_route
        return None

    def _is_enabled(self, route: str) -> bool:
        matching_items = [
            item for item in self.repository.list_items() if item.get("route") == route
        ]
        return bool(matching_items) and all(
            bool(item.get("is_enabled")) for item in matching_items
        )

    def _is_allowed(self, principal: Principal, route: str) -> bool:
        override_reader = getattr(self.repository, "get_page_access_override", None)
        if callable(override_reader):
            override = override_reader(principal.user_id, route)
            if override is not None:
                return bool(override)

        # Backward compatibility for repositories which only expose the
        # original allow-list query while migrations are being applied.
        if principal.user_id in self.repository.list_page_access_user_ids(route):
            return True

        role_reader = getattr(self.repository, "list_role_page_access_routes", None)
        if not callable(role_reader):
            return False
        return route in role_reader(principal.role_codes)

    @staticmethod
    def _grant_route(page_route: str) -> str:
        return PAGE_ACCESS_GRANT_ROUTES.get(page_route, page_route)

    @classmethod
    def _grant_routes(cls, page_route: str) -> tuple[str, ...]:
        return (
            cls._grant_route(page_route),
            *PAGE_ACCESS_ADDITIONAL_GRANT_ROUTES.get(page_route, ()),
        )

    @staticmethod
    def _is_system_admin(principal: Principal) -> bool:
        return "system_admin" in principal.role_codes


def _compile_path(path_template: str) -> re.Pattern[str]:
    escaped = re.escape(path_template)
    dynamic = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile(dynamic)
