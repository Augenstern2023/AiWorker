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

from identity.schema import Principal
from system_database.schema import SYSTEM_METADATA
from web.navigation_service import NavigationService


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


def test_navigation_service_filters_configured_items_by_catalog_permissions():
    navigation = NavigationService(
        catalog=(
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
    )

    assert navigation.items_for(_principal("rank.read")) == [
        {
            "label": "Selection",
            "icon": "bi-funnel",
            "route": "/selection/ranks",
            "items": ({"label": "Ranks", "route": "/selection/ranks"},),
        }
    ]


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
    navigation = NavigationService(
        catalog=(
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
    )

    assert navigation.items_for(_principal("page.selection")) == []


def test_navigation_items_schema_uses_text_ids_and_permission_foreign_key():
    navigation_items = SYSTEM_METADATA.tables["navigation_items"]

    assert isinstance(navigation_items.c.id.type, Text)
    assert isinstance(navigation_items.c.parent_id.type, Text)
    assert {foreign_key.target_fullname for foreign_key in navigation_items.c.required_permission.foreign_keys} == {
        "permissions.code"
    }
