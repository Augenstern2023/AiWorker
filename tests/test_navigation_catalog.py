# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import pytest
from sqlalchemy import Text

from identity.permission_catalog import NAVIGATION_CATALOG, PERMISSIONS
from identity.schema import Principal
from system_database.schema import SYSTEM_METADATA
from web.page_access_service import PageAccessService
from web.navigation_service import NavigationService
from web.page_routes import PAGE_ROUTES


class FakeNavigationRepository:
    def __init__(self, items=()) -> None:
        self.items = [dict(item) for item in items]
        self.access_by_route: dict[str, set[str]] = {}

    def list_items(self):
        items = []
        for raw_item in self.items:
            item = dict(raw_item)
            item.setdefault("is_enabled", True)
            items.append(item)
        return items

    def list_page_access_user_ids(self, route: str) -> set[str]:
        return set(self.access_by_route.get(route, set()))


def _principal(*permissions: str) -> Principal:
    return Principal(
        user_id="user-1",
        username="user",
        display_name="User",
        role_codes=(),
        permissions=frozenset(permissions),
        data_scope="task",
        session_id="session-1",
    )


def _system_administrator() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin",
        display_name="Administrator",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="admin-session-1",
    )


def test_admin_role_page_is_named_role_management():
    navigation_item = next(
        item for item in NAVIGATION_CATALOG if item["route"] == "/admin/roles"
    )
    page_route = next(item for item in PAGE_ROUTES if item[0] == "/admin/roles")

    assert navigation_item["label"] == "角色管理"
    assert page_route[2] == "角色管理"


def test_system_administrator_navigation_keeps_system_backend_page():
    repository = FakeNavigationRepository(NAVIGATION_CATALOG)
    navigation = NavigationService(
        page_access_service=PageAccessService(repository),
    )

    visible_routes = {
        item["route"]
        for group in navigation.items_for(_system_administrator())
        for item in group["items"]
    }

    assert "/admin/jobs" in visible_routes
    assert navigation.can_open(_system_administrator(), "/admin/jobs") is True


def test_team_management_routes_and_permissions_are_removed():
    removed_routes = {"/review/users", "/review/roles"}
    removed_navigation_id_prefix = "navigation:team"
    removed_permissions = {
        "page.team_management",
        "identity.user.read",
        "identity.user.create",
        "identity.user.manage_created",
        "identity.role.read",
        "identity.role.create",
        "identity.role.manage_created",
    }

    assert all(
        not item["id"].startswith(removed_navigation_id_prefix)
        for item in NAVIGATION_CATALOG
    )
    assert removed_routes.isdisjoint({item[0] for item in PAGE_ROUTES})
    assert removed_routes.isdisjoint({item["route"] for item in NAVIGATION_CATALOG})
    assert removed_permissions.isdisjoint(PERMISSIONS)
    assert all(
        item["required_permission"] not in removed_permissions
        for item in NAVIGATION_CATALOG
    )


def test_navigation_service_filters_configured_items_by_catalog_permissions():
    catalog = (
            {
                "id": "navigation:selection",
                "label": "Selection",
                "icon": "bi-funnel",
                "route": "/selection/dashboard",
                "required_permission": "page.selection",
                "sort_order": 10,
            },
            {
                "id": "navigation:selection:ranks",
                "parent_id": "navigation:selection",
                "label": "Ranks",
                "route": "/selection/ranks",
                "required_permission": "rank.read",
                "sort_order": 10,
            },
        )
    repository = FakeNavigationRepository(catalog)
    repository.access_by_route["/selection/ranks"] = {"user-1"}
    navigation = NavigationService(
        catalog=catalog,
        page_access_service=PageAccessService(repository),
    )

    assert navigation.items_for(_principal("rank.read")) == [
        {
            "label": "Selection",
            "icon": "bi-funnel",
            "route": "/selection/ranks",
            "items": ({"label": "Ranks", "route": "/selection/ranks"},),
        }
    ]


def test_navigation_service_ignores_legacy_page_permissions_without_whitelist():
    catalog = (
        {
            "id": "navigation:selection",
            "label": "Selection",
            "icon": "bi-funnel",
            "route": "/selection/dashboard",
            "required_permission": "page.selection",
            "sort_order": 10,
        },
    )
    repository = FakeNavigationRepository(catalog)
    navigation = NavigationService(
        catalog=catalog,
        page_access_service=PageAccessService(repository),
    )

    assert navigation.items_for(_principal("page.selection")) == []


def test_navigation_service_rejects_permissions_outside_the_permission_catalog():
    with pytest.raises(ValueError, match="unknown permission"):
        NavigationService(
            catalog=(
                {
                    "id": "navigation:unknown",
                    "label": "Unknown",
                    "icon": "bi-question-circle",
                    "route": "/unknown",
                    "required_permission": "page.unknown",
                    "sort_order": 10,
                },
            )
        )


def test_navigation_service_rejects_routes_outside_the_page_registry():
    with pytest.raises(ValueError, match="unknown page route"):
        NavigationService(
            catalog=(
                {
                    "id": "navigation:invalid-route",
                    "label": "Invalid route",
                    "icon": "bi-question-circle",
                    "route": "/not-a-page",
                    "required_permission": "page.selection",
                    "sort_order": 10,
                },
            )
        )


def test_navigation_service_omits_disabled_configured_items():
    catalog = (
            {
                "id": "navigation:selection",
                "label": "Selection",
                "icon": "bi-funnel",
                "route": "/selection/dashboard",
                "required_permission": "page.selection",
                "sort_order": 10,
                "is_enabled": False,
            },
        )
    repository = FakeNavigationRepository(catalog)
    repository.access_by_route["/selection/dashboard"] = {"user-1"}
    navigation = NavigationService(
        catalog=catalog,
        page_access_service=PageAccessService(repository),
    )

    assert navigation.items_for(_principal("page.selection")) == []


def test_navigation_service_refreshes_presentation_after_page_is_enabled():
    catalog = (
        {
            "id": "navigation:selection",
            "label": "Selection",
            "icon": "bi-funnel",
            "route": "/selection/dashboard",
            "required_permission": "page.selection",
            "sort_order": 10,
            "is_enabled": False,
        },
        {
            "id": "navigation:admin",
            "label": "System",
            "icon": "bi-gear",
            "route": "/admin/users",
            "required_permission": "*",
            "sort_order": 20,
            "is_enabled": True,
        },
    )
    repository = FakeNavigationRepository(catalog)
    repository.access_by_route["/selection/dashboard"] = {"user-1"}
    repository.access_by_route["/admin/users"] = {"user-1"}
    navigation = NavigationService(
        catalog=catalog,
        page_access_service=PageAccessService(repository),
    )

    assert navigation.items_for(_principal()) == [
        {
            "label": "System",
            "icon": "bi-gear",
            "route": "/admin/users",
            "items": ({"label": "System", "route": "/admin/users"},),
        }
    ]

    repository.items[0].update(
        is_enabled=True,
        label="Selection Updated",
        icon="bi-stars",
        sort_order=30,
    )

    assert navigation.can_open(_principal(), "/selection/dashboard") is True
    assert navigation.items_for(_principal()) == [
        {
            "label": "System",
            "icon": "bi-gear",
            "route": "/admin/users",
            "items": ({"label": "System", "route": "/admin/users"},),
        },
        {
            "label": "Selection Updated",
            "icon": "bi-stars",
            "route": "/selection/dashboard",
            "items": (
                {
                    "label": "Selection Updated",
                    "route": "/selection/dashboard",
                },
            ),
        }
    ]


def test_navigation_items_schema_uses_text_ids_and_permission_foreign_key():
    navigation_items = SYSTEM_METADATA.tables["navigation_items"]

    assert isinstance(navigation_items.c.id.type, Text)
    assert isinstance(navigation_items.c.parent_id.type, Text)
    assert {foreign_key.target_fullname for foreign_key in navigation_items.c.required_permission.foreign_keys} == {
        "permissions.code"
    }
