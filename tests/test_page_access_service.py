# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path

import pytest

from identity.schema import Principal
from web.page_access_catalog import PAGE_ACCESS_CATALOG
from web.page_access_service import PageAccessService
from web.page_routes import (
    DENIED_PAGE_ROUTES,
    PAGE_ACCESS_GRANT_ROUTES,
    PAGE_ROUTES,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeNavigationRepository:
    def __init__(self) -> None:
        self.access_by_route: dict[str, set[str]] = {}
        self.overrides_by_route: dict[str, dict[str, bool]] = {}
        self.role_routes_by_code: dict[str, set[str]] = {}
        self.disabled_routes: set[str] = set()

    def list_items(self) -> list[dict[str, object]]:
        return [
            {
                "id": f"navigation:{index}",
                "parent_id": None,
                "label": title,
                "icon": "",
                "route": route,
                "required_permission": permission,
                "sort_order": index,
                "is_enabled": route not in self.disabled_routes,
                "is_system": True,
                "hide_for_system_admin": False,
            }
            for index, (route, permission, title) in enumerate(PAGE_ROUTES)
            if "{" not in route
        ]

    def list_page_access_user_ids(self, route: str) -> set[str]:
        return set(self.access_by_route.get(route, set()))

    def get_page_access_override(self, user_id: str, route: str) -> bool | None:
        return self.overrides_by_route.get(route, {}).get(user_id)

    def list_role_page_access_routes(self, role_codes: tuple[str, ...]) -> set[str]:
        return set().union(
            *(self.role_routes_by_code.get(role_code, set()) for role_code in role_codes)
        ) if role_codes else set()


def _principal(
    user_id: str = "user-1", *, roles: tuple[str, ...] = (), permissions: tuple[str, ...] = ()
) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        role_codes=roles,
        permissions=frozenset(permissions),
        data_scope="task",
        session_id=f"session-{user_id}",
    )


def test_regular_account_only_has_profile_without_whitelist_entries() -> None:
    service = PageAccessService(FakeNavigationRepository())
    principal = _principal(
        permissions=("page.selection", "identity.user.read", "data.media.preview")
    )

    assert service.pages_for(principal) == frozenset({"/profile"})
    assert service.can_open_page(principal, "/profile") is True
    assert service.can_open_page(principal, "/admin/users") is False
    assert service.can_call_page_api(principal, "GET", "/api/admin/users") is False
    assert service.can_call_page_api(principal, "GET", "/api/auth/me") is None


def test_wildcard_permission_does_not_bypass_account_page_whitelist() -> None:
    repository = FakeNavigationRepository()
    service = PageAccessService(repository)
    principal = _principal(permissions=("*",))

    assert service.pages_for(principal) == frozenset({"/profile"})
    assert service.can_open_page(principal, "/admin/users") is False
    assert service.can_call_page_api(principal, "GET", "/api/admin/users") is False


def test_whitelist_entry_grants_only_its_page_and_bound_api_capabilities() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/admin/users"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert service.can_open_page(principal, "/admin/users") is True
    assert service.can_open_page(principal, "/admin/roles") is False
    assert service.can_call_page_api(principal, "GET", "/api/admin/users/export") is True
    assert (
        service.can_call_page_api(
            principal, "POST", "/api/admin/users/account-7/reset-password"
        )
        is True
    )
    assert (
        service.can_call_page_api(principal, "GET", "/api/admin/roles/export")
        is False
    )

    repository.access_by_route["/admin/users"] = set()
    assert service.can_open_page(principal, "/admin/users") is False
    assert service.can_call_page_api(principal, "GET", "/api/admin/users") is False


def test_delivery_page_grant_includes_generated_resource_list_and_download() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/admin/deliveries"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert service.can_call_page_api(
        principal, "GET", "/api/creative/generated-resources"
    ) is True
    assert service.can_call_page_api(
        principal,
        "GET",
        "/api/creative/projects/project-1/generated-content/download",
    ) is True


def test_planning_grant_controls_project_parameter_updates_immediately() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/planning/projects"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert (
        service.can_call_page_api(
            principal, "PATCH", "/api/creative/projects/project-1"
        )
        is True
    )
    assert (
        service.can_call_page_api(
            principal, "DELETE", "/api/creative/projects/project-1"
        )
        is True
    )

    repository.access_by_route["/planning/projects"] = set()
    assert (
        service.can_call_page_api(
            principal, "PATCH", "/api/creative/projects/project-1"
        )
        is False
    )
    assert (
        service.can_call_page_api(
            principal, "DELETE", "/api/creative/projects/project-1"
        )
        is False
    )


def test_api_path_templates_match_the_complete_path_and_method_only() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/admin/users"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert (
        service.can_call_page_api(
            principal, "PATCH", "/api/admin/users/account-7"
        )
        is True
    )
    assert (
        service.can_call_page_api(
            principal, "PATCH", "/api/admin/users/account-7/roles/extra"
        )
        is None
    )
    assert service.can_call_page_api(principal, "PATCH", "/api/admin/users/") is None
    assert service.can_call_page_api(principal, "TRACE", "/api/admin/users") is None


def test_dynamic_project_pages_inherit_their_static_workspace_whitelist() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/planning/projects"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert service.can_open_page(
        principal, "/planning/projects/project-7/brief"
    ) is True
    assert service.can_open_page(
        principal, "/writing/projects/project-7/outlines"
    ) is False


def test_managed_project_grant_opens_its_creative_brief_preview() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/review/projects"] = {"user-1"}
    service = PageAccessService(repository)

    assert service.can_open_page(
        _principal(), "/planning/projects/project-7/brief"
    ) is True


def test_writing_project_grant_opens_read_only_planning_stages() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/writing/projects"] = {"user-1"}
    service = PageAccessService(repository)

    assert service.can_open_page(
        _principal(), "/planning/projects/project-7/brief"
    ) is True
    assert service.can_open_page(
        _principal(), "/planning/projects/project-7/characters"
    ) is True


def test_managed_project_preview_returns_generated_project_content_read_only() -> None:
    source = (ROOT / "creative/api.py").read_text(encoding="utf-8")

    assert '"project.read.managed" in principal.permissions' in source
    assert 'viewed["preview_only"] = True' in source
    assert "def has_managed_project_preview_access" in source
    assert "managed_preview=has_managed_project_preview_access(request, principal)" in source


def test_system_administrator_can_access_all_pages_even_when_globally_disabled() -> None:
    repository = FakeNavigationRepository()
    repository.disabled_routes.update(
        route for route, _, _ in PAGE_ROUTES if "{" not in route
    )
    service = PageAccessService(repository)
    administrator = _principal(roles=("system_admin",))

    assert service.pages_for(administrator) == frozenset(
        {"/profile", *(route for route, _, _ in PAGE_ROUTES)}
    )
    assert service.can_open_page(administrator, "/admin/users") is True
    assert service.can_call_page_api(
        administrator, "DELETE", "/api/admin/users/account-7"
    ) is True
    assert service.can_call_page_api(administrator, "GET", "/api/not-cataloged") is None


def test_role_page_grants_apply_without_a_new_login() -> None:
    repository = FakeNavigationRepository()
    repository.role_routes_by_code["project_owner"] = {
        "/admin/roles",
        "/admin/audit",
        "/admin/data",
        "/admin/drama-player",
        "/selection/dashboard",
        "/selection/ranks",
        "/selection/hits",
        "/selection/topics",
        "/selection/dramas",
    }
    service = PageAccessService(repository)
    owner = _principal(roles=("project_owner",))

    assert service.can_open_page(owner, "/admin/roles") is True
    assert service.can_call_page_api(owner, "GET", "/api/admin/roles") is True
    assert service.can_call_page_api(
        owner, "PUT", "/api/admin/roles/role-1/pages"
    ) is True
    assert service.can_call_page_api(
        owner, "GET", "/api/admin/roles/page-catalog"
    ) is True
    assert service.can_call_page_api(
        owner, "GET", "/api/drama-player/library"
    ) is True
    assert service.can_open_page(owner, "/admin/navigation") is False
    assert service.can_open_page(owner, "/planning/projects") is False
    assert service.can_call_page_api(owner, "GET", "/api/creative/projects") is False


def test_account_override_takes_precedence_over_role_page_grants() -> None:
    repository = FakeNavigationRepository()
    repository.role_routes_by_code["selection_operator"] = {"/selection/ranks"}
    repository.overrides_by_route["/selection/ranks"] = {"user-1": False}
    repository.overrides_by_route["/admin/data"] = {"user-1": True}
    service = PageAccessService(repository)
    principal = _principal(roles=("selection_operator",))

    assert service.can_open_page(principal, "/selection/ranks") is False
    assert service.can_call_page_api(principal, "GET", "/api/rank/today") is False
    assert service.can_open_page(principal, "/admin/data") is True
    assert service.can_call_page_api(principal, "GET", "/api/files") is True


def test_global_disable_precedes_whitelist_and_project_owner_navigation_privilege() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/admin/users"] = {"user-1"}
    repository.disabled_routes.update({"/admin/users", "/admin/navigation"})
    service = PageAccessService(repository)

    assert service.can_open_page(_principal(), "/admin/users") is False
    assert service.can_call_page_api(
        _principal(), "GET", "/api/admin/users"
    ) is False
    assert service.can_open_page(
        _principal(roles=("project_owner",)), "/admin/navigation"
    ) is False


@pytest.mark.parametrize(
    ("page_route", "method", "api_path"),
    (
        ("/selection/dashboard", "GET", "/api/creative/topics"),
        ("/selection/dashboard", "GET", "/api/analytics/overview"),
        ("/selection/ranks", "POST", "/api/jobs"),
        ("/selection/ranks", "GET", "/api/jobs/rank-refresh-1"),
        ("/selection/hits", "GET", "/api/creative/topics/topic-1"),
        ("/selection/hits", "POST", "/api/creative/breakdowns"),
        ("/selection/hits", "GET", "/api/jobs/breakdown-1/logs"),
        ("/selection/hits", "POST", "/api/hongguo/drama/download/search"),
        ("/selection/hits", "GET", "/api/jobs"),
        ("/selection/topics", "GET", "/api/workflow/tasks"),
        ("/selection/topics", "POST", "/api/hongguo/reviews/capture"),
        (
            "/selection/topics",
            "GET",
            "/api/douyin/reviews/jobs/review-job-1/download",
        ),
        ("/selection/topics", "GET", "/api/workflow/assignment-candidates"),
        ("/selection/topics", "POST", "/api/workflow/create-assignment"),
        (
            "/selection/topics",
            "GET",
            "/api/creative/projects/project-1/generated-content",
        ),
        ("/admin/users", "GET", "/api/admin/departments"),
        ("/admin/users", "GET", "/api/admin/roles"),
        ("/review/users", "GET", "/api/team/roles"),
        ("/review/scripts", "POST", "/api/script-reviews/review-1/optimizations"),
        ("/review/scripts", "GET", "/api/script-optimizations/optimization-1"),
        ("/review/scripts", "POST", "/api/script-optimization-drafts/draft-1/accept"),
        ("/admin/departments", "GET", "/api/admin/users"),
        ("/admin/data", "GET", "/api/jobs/file-operation-1"),
        ("/admin/config", "GET", "/api/hongguo/drama/settings"),
        ("/admin/jobs", "GET", "/api/config"),
        ("/admin/jobs", "GET", "/api/file/download"),
        ("/admin/jobs", "GET", "/api/qrcode/status"),
        ("/selection/dramas", "GET", "/api/workflow/tasks"),
        ("/selection/dramas", "GET", "/api/creative/topics/topic-1"),
        (
            "/selection/dramas",
            "POST",
            "/api/workflow/tasks/task-1/start",
        ),
        ("/selection/dramas", "GET", "/api/jobs"),
        ("/planning/projects", "GET", "/api/creative/topics"),
        ("/planning/projects", "GET", "/api/jobs"),
        (
            "/planning/projects",
            "GET",
            "/api/creative/projects/project-1/export",
        ),
        (
            "/planning/projects",
            "GET",
            "/api/creative/topics/topic-1/breakdowns",
        ),
        (
            "/planning/projects",
            "GET",
            "/api/workflow/tasks",
        ),
        (
            "/planning/projects",
            "GET",
            "/api/jobs/character-job-1/logs",
        ),
        (
            "/writing/projects",
            "POST",
            "/api/jobs/outline-job-1/retry",
        ),
        ("/writing/projects", "GET", "/api/creative/topics"),
        ("/writing/projects", "GET", "/api/jobs"),
        (
            "/writing/projects",
            "GET",
            "/api/creative/projects/project-1/export",
        ),
        (
            "/writing/projects",
            "GET",
            "/api/workflow/tasks",
        ),
    ),
)
def test_page_grant_includes_the_apis_used_by_that_page(
    page_route: str, method: str, api_path: str
) -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route[page_route] = {"user-1"}
    service = PageAccessService(repository)

    assert service.can_call_page_api(_principal(), method, api_path) is True


def test_page_grants_do_not_expose_analytics_without_the_analytics_page_grant() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route.update(
        {
            "/selection/dashboard": {"user-1"},
            "/selection/hits": {"user-1"},
            "/selection/dramas": {"user-1"},
            "/admin/data": {"user-1"},
        }
    )
    service = PageAccessService(repository)
    principal = _principal()

    assert (
        service.can_call_page_api(principal, "POST", "/api/analytics/sync-all")
        is None
    )
    assert service.can_call_page_api(principal, "GET", "/api/analytics/dramas") is None
    assert service.can_call_page_api(principal, "GET", "/api/qrcode/status") is False
    assert (
        service.can_call_page_api(
            principal, "PUT", "/api/hongguo/drama/settings"
        )
        is False
    )


def test_rank_page_does_not_grant_heavyweight_file_analysis_endpoints() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route["/selection/ranks"] = {"user-1"}
    service = PageAccessService(repository)
    principal = _principal()

    assert (
        service.can_call_page_api(
            principal, "POST", "/api/hongguo/analyze-rank"
        )
        is None
    )
    assert (
        service.can_call_page_api(
            principal, "POST", "/api/douyin/analyze-rank"
        )
        is None
    )


def test_page_grants_do_not_group_authorize_sensitive_workflow_writes() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route.update(
        {
            "/selection/dramas": {"user-1"},
            "/planning/projects": {"user-1"},
        }
    )
    service = PageAccessService(repository)
    principal = _principal()

    assert (
        service.can_call_page_api(
            principal, "POST", "/api/workflow/projects/project-1/members"
        )
        is False
    )
    assert (
        service.can_call_page_api(
            principal, "POST", "/api/workflow/tasks/task-1/reassign"
        )
        is None
    )
    assert (
        service.can_call_page_api(
            principal, "POST", "/api/workflow/tasks/task-1/cancel"
        )
        is None
    )


def test_page_grants_only_include_job_controls_rendered_by_that_page() -> None:
    repository = FakeNavigationRepository()
    repository.access_by_route.update(
        {
            "/selection/dramas": {"drama-user"},
            "/selection/topics": {"topic-user"},
        }
    )
    service = PageAccessService(repository)

    assert (
        service.can_call_page_api(
            _principal("drama-user"), "POST", "/api/jobs/job-1/pause"
        )
        is False
    )
    assert (
        service.can_call_page_api(
            _principal("topic-user"), "POST", "/api/jobs/job-1/retry"
        )
        is False
    )
def test_catalog_covers_all_registered_page_routes_once() -> None:
    catalog_routes = [entry["page_route"] for entry in PAGE_ACCESS_CATALOG]
    registered_routes = [route for route, _, _ in PAGE_ROUTES]

    assert catalog_routes == registered_routes
    assert len(catalog_routes) == len(set(catalog_routes))


def test_legacy_task_pages_are_explicitly_denied_without_page_grants() -> None:
    service = PageAccessService(FakeNavigationRepository())
    principal = _principal()

    assert DENIED_PAGE_ROUTES == frozenset(
        {"/tasks", "/planning/tasks", "/writing/tasks"}
    )
    assert all(
        service.can_open_page(principal, route) is False
        for route in DENIED_PAGE_ROUTES
    )


def test_legacy_directions_page_inherits_planning_projects_grant() -> None:
    repository = FakeNavigationRepository()
    service = PageAccessService(repository)
    principal = _principal()

    assert PAGE_ACCESS_GRANT_ROUTES[
        "/planning/projects/{project_id}/directions"
    ] == "/planning/projects"
    assert service.can_open_page(
        principal, "/planning/projects/project-1/directions"
    ) is False

    repository.access_by_route["/planning/projects"] = {"user-1"}
    assert service.can_open_page(
        principal, "/planning/projects/project-1/directions"
    ) is True
