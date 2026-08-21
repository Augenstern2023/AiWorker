# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Page-to-API capability bindings used by account page access checks."""

from __future__ import annotations

from typing import TypedDict

from web.page_routes import PAGE_ROUTES


ApiRoute = tuple[str, str]


class PageAccessCatalogEntry(TypedDict):
    page_route: str
    api_routes: tuple[ApiRoute, ...]


_ADMIN_USERS: tuple[ApiRoute, ...] = (
    ("GET", "/api/admin/users"),
    ("GET", "/api/admin/users/export"),
    ("POST", "/api/admin/users"),
    ("PATCH", "/api/admin/users/{user_id}"),
    ("DELETE", "/api/admin/users/{user_id}"),
    ("POST", "/api/admin/users/{user_id}/reset-password"),
    ("PUT", "/api/admin/users/{user_id}/roles"),
)

_ADMIN_ROLES: tuple[ApiRoute, ...] = (
    ("GET", "/api/admin/roles"),
    ("GET", "/api/admin/roles/page-catalog"),
    ("GET", "/api/admin/roles/export"),
    ("POST", "/api/admin/roles"),
    ("PATCH", "/api/admin/roles/{role_id}"),
    ("DELETE", "/api/admin/roles/{role_id}"),
    ("POST", "/api/admin/roles/{role_id}/clone"),
    ("PUT", "/api/admin/roles/{role_id}/permissions"),
    ("PUT", "/api/admin/roles/{role_id}/pages"),
    ("GET", "/api/admin/permissions"),
)

_TEAM_USERS: tuple[ApiRoute, ...] = (
    ("GET", "/api/team/users"),
    ("GET", "/api/team/roles"),
    ("POST", "/api/team/users"),
    ("PATCH", "/api/team/users/{user_id}"),
    ("DELETE", "/api/team/users/{user_id}"),
    ("POST", "/api/team/users/{user_id}/reset-password"),
    ("PUT", "/api/team/users/{user_id}/roles"),
)

_TEAM_ROLES: tuple[ApiRoute, ...] = (
    ("GET", "/api/team/roles"),
    ("GET", "/api/team/permissions"),
    ("POST", "/api/team/roles"),
    ("PATCH", "/api/team/roles/{role_id}"),
    ("DELETE", "/api/team/roles/{role_id}"),
    ("POST", "/api/team/roles/{role_id}/clone"),
)

_CREATIVE_TASK_SUPPORT: tuple[ApiRoute, ...] = (
    ("GET", "/api/workflow/tasks"),
    ("POST", "/api/workflow/tasks/{task_id}/claim"),
    ("POST", "/api/workflow/tasks/{task_id}/start"),
    ("POST", "/api/creative/projects/{project_id}/workflow-tasks/{resource_type}/ensure"),
)

_PROJECT_MANAGEMENT: tuple[ApiRoute, ...] = (
    ("GET", "/api/creative/projects"),
    ("PATCH", "/api/creative/projects/{project_id}"),
    ("DELETE", "/api/creative/projects/{project_id}"),
    ("GET", "/api/workflow/tasks"),
)

_JOB_READ: tuple[ApiRoute, ...] = (
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/jobs/{job_id}/logs"),
)

_JOB_CONTROLS: tuple[ApiRoute, ...] = (
    ("POST", "/api/jobs/{job_id}/stop"),
    ("POST", "/api/jobs/{job_id}/pause"),
    ("POST", "/api/jobs/{job_id}/resume"),
)

_JOB_MONITOR = _JOB_READ + _JOB_CONTROLS

_CREATIVE_JOB_MONITOR: tuple[ApiRoute, ...] = _JOB_MONITOR + (
    ("POST", "/api/jobs/{job_id}/retry"),
)

_SCRIPT_REVIEWS: tuple[ApiRoute, ...] = (
    ("GET", "/api/creative/projects"),
    ("GET", "/api/creative/projects/{project_id}"),
    ("GET", "/api/script-reviews"),
    ("POST", "/api/script-reviews"),
    ("GET", "/api/script-reviews/{review_id}"),
    ("POST", "/api/script-reviews/{review_id}/confirm"),
    ("POST", "/api/script-reviews/{review_id}/optimizations"),
    ("GET", "/api/script-reviews/{review_id}/optimizations"),
    ("GET", "/api/script-optimizations/{optimization_id}"),
    ("GET", "/api/script-optimization-drafts/{draft_id}"),
    ("POST", "/api/script-optimization-drafts/{draft_id}/accept"),
    ("POST", "/api/script-optimization-drafts/{draft_id}/reject"),
)

_WRITER_AGENT: tuple[ApiRoute, ...] = (
    ("GET", "/api/writer-agent/sessions"),
    ("POST", "/api/writer-agent/sessions"),
    ("POST", "/api/writer-agent/sessions/file"),
    ("POST", "/api/writer-agent/sessions/{session_id}/materials/file"),
    ("GET", "/api/writer-agent/sessions/{session_id}"),
    ("POST", "/api/writer-agent/sessions/{session_id}/messages"),
    ("GET", "/api/writer-agent/sessions/{session_id}/export"),
    ("GET", "/api/writer-agent/sessions/{session_id}/reports/{report_id}/export"),
)

_DRAMA_DOWNLOAD: tuple[ApiRoute, ...] = (
    ("POST", "/api/hongguo/drama/download/status"),
    ("POST", "/api/hongguo/drama/download/search"),
    ("POST", "/api/hongguo/drama/douyin-fallback/series"),
    ("POST", "/api/hongguo/drama/episodes"),
    ("POST", "/api/hongguo/drama/download/jobs"),
    ("POST", "/api/hongguo/drama/douyin-fallback/jobs"),
    ("GET", "/api/file/preview"),
)

_DRAMA_REVIEWS: tuple[ApiRoute, ...] = (
    ("POST", "/api/hongguo/reviews/capture"),
    ("GET", "/api/hongguo/reviews/jobs/{job_id}"),
    ("GET", "/api/hongguo/reviews/jobs/{job_id}/download"),
    ("POST", "/api/douyin/reviews/jobs"),
    ("GET", "/api/douyin/reviews/jobs/{job_id}"),
    ("GET", "/api/douyin/reviews/jobs/{job_id}/download"),
)

_SELECTION_CONTENT_WORKSPACE: tuple[ApiRoute, ...] = (
    ("POST", "/api/creative/topics/generate"),
    ("GET", "/api/creative/topics"),
    ("GET", "/api/creative/topics/{topic_id}"),
    ("PATCH", "/api/creative/topics/{topic_id}"),
    ("GET", "/api/creative/topics/{topic_id}/transcripts"),
    ("GET", "/api/creative/topics/{topic_id}/transcripts/download"),
    ("GET", "/api/creative/topics/{topic_id}/breakdowns"),
    ("POST", "/api/creative/breakdowns"),
    ("GET", "/api/creative/breakdowns/template"),
    ("GET", "/api/creative/breakdowns/{breakdown_id}"),
    ("GET", "/api/creative/breakdowns/{breakdown_id}/download"),
    ("PATCH", "/api/creative/breakdowns/{breakdown_id}"),
    ("POST", "/api/creative/breakdowns/{breakdown_id}/lock"),
    ("GET", "/api/creative/projects/{project_id}/generated-content"),
    ("GET", "/api/creative/projects/{project_id}/generated-content/download"),
) + _JOB_MONITOR + _DRAMA_DOWNLOAD + _DRAMA_REVIEWS

_SELECTION_TOPIC_ASSIGNMENT: tuple[ApiRoute, ...] = (
    ("POST", "/api/creative/topics/{topic_id}/planning-assignment"),
    ("GET", "/api/creative/projects/member-candidates"),
    ("GET", "/api/workflow/tasks"),
    ("GET", "/api/workflow/assignment-candidates"),
    ("POST", "/api/workflow/create-assignment"),
)

_PAGE_API_ROUTES: dict[str, tuple[ApiRoute, ...]] = {
    "/selection/dashboard": (
        ("GET", "/api/creative/topics"),
        ("GET", "/api/analytics/overview"),
    ),
    "/selection/ranks": (
        ("GET", "/api/rank/today"),
        ("POST", "/api/jobs"),
        ("GET", "/api/jobs/{job_id}"),
    ),
    "/selection/hits": _SELECTION_CONTENT_WORKSPACE,
    "/selection/topics": _SELECTION_CONTENT_WORKSPACE
    + _SELECTION_TOPIC_ASSIGNMENT,
    "/selection/dramas": (
        ("POST", "/api/hongguo/drama/search"),
        ("POST", "/api/hongguo/drama/suggest"),
        ("POST", "/api/hongguo/drama/episodes"),
        ("POST", "/api/hongguo/drama/download/status"),
        ("POST", "/api/hongguo/drama/download/search"),
        ("POST", "/api/hongguo/drama/jobs"),
        ("POST", "/api/hongguo/drama/download/jobs"),
        ("POST", "/api/hongguo/drama/douyin-fallback/series"),
        ("POST", "/api/hongguo/drama/douyin-fallback/jobs"),
        ("POST", "/api/hongguo/reviews/capture"),
        ("GET", "/api/hongguo/reviews/jobs/{job_id}"),
        ("GET", "/api/hongguo/reviews/jobs/{job_id}/download"),
        ("POST", "/api/douyin/drama/search"),
        ("POST", "/api/douyin/reviews/jobs"),
        ("GET", "/api/douyin/reviews/jobs/{job_id}"),
        ("GET", "/api/douyin/reviews/jobs/{job_id}/download"),
        ("GET", "/api/creative/topics/{topic_id}"),
    )
    + _CREATIVE_TASK_SUPPORT
    + (("GET", "/api/jobs"),),
    "/planning/projects": (
        ("POST", "/api/creative/projects"),
        ("GET", "/api/creative/projects"),
        ("PATCH", "/api/creative/projects/{project_id}"),
        ("DELETE", "/api/creative/projects/{project_id}"),
        ("GET", "/api/creative/topics"),
        ("GET", "/api/creative/topics/{topic_id}/breakdowns"),
    ),
    "/planning/projects/{project_id}/brief": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("GET", "/api/creative/projects/{project_id}/generated-content"),
        ("GET", "/api/creative/projects/{project_id}/generated-content/download"),
        ("POST", "/api/creative/projects/{project_id}/blueprints/generate"),
        ("PATCH", "/api/creative/blueprints/{blueprint_id}"),
        ("POST", "/api/creative/blueprints/{blueprint_id}/versions"),
        ("POST", "/api/creative/blueprints/{blueprint_id}/lock-brief"),
        ("GET", "/api/creative/projects/{project_id}/export"),
    )
    + _CREATIVE_TASK_SUPPORT
    + _CREATIVE_JOB_MONITOR,
    "/planning/projects/{project_id}/characters": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("POST", "/api/creative/projects/{project_id}/characters/generate"),
        ("POST", "/api/creative/projects/{project_id}/characters"),
        ("PATCH", "/api/creative/characters/{character_id}"),
        ("PATCH", "/api/creative/relationships/{relationship_id}"),
        ("POST", "/api/creative/projects/{project_id}/characters/{batch_id}/lock"),
        ("GET", "/api/creative/projects/{project_id}/export"),
    )
    + _CREATIVE_TASK_SUPPORT
    + _CREATIVE_JOB_MONITOR,
    "/writing/projects": (
        ("GET", "/api/creative/projects"),
        ("GET", "/api/creative/topics"),
    ),
    "/writing/projects/{project_id}/outlines": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("POST", "/api/creative/projects/{project_id}/outlines/generate"),
        ("POST", "/api/creative/projects/{project_id}/outlines"),
        ("PATCH", "/api/creative/episodes/{episode_id}"),
        ("POST", "/api/creative/projects/{project_id}/outlines/{batch_id}/lock"),
        ("GET", "/api/creative/projects/{project_id}/export"),
    )
    + _CREATIVE_TASK_SUPPORT
    + _CREATIVE_JOB_MONITOR,
    "/writing/projects/{project_id}/episodes/{episode_number}/beats": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("GET", "/api/creative/projects/{project_id}/episodes/{episode_number}/optimization-drafts"),
        ("POST", "/api/creative/projects/{project_id}/episodes/{episode_number}/beats/generate"),
        ("POST", "/api/creative/projects/{project_id}/episodes/{episode_number}/beats"),
        ("PATCH", "/api/creative/episodes/{episode_id}"),
        ("POST", "/api/creative/episodes/{episode_id}/lock"),
        ("GET", "/api/creative/projects/{project_id}/export"),
    )
    + _CREATIVE_TASK_SUPPORT
    + _CREATIVE_JOB_MONITOR,
    "/writing/projects/{project_id}/episodes/{episode_number}/scripts": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("GET", "/api/creative/projects/{project_id}/episodes/{episode_number}/optimization-drafts"),
        ("POST", "/api/creative/projects/{project_id}/episodes/{episode_number}/scripts/generate"),
        ("POST", "/api/creative/projects/{project_id}/episodes/{episode_number}/scripts"),
        ("PATCH", "/api/creative/episodes/{episode_id}"),
        ("POST", "/api/creative/scripts/{episode_id}/lock"),
        ("GET", "/api/creative/projects/{project_id}/generated-content/download"),
    )
    + _CREATIVE_TASK_SUPPORT
    + _CREATIVE_JOB_MONITOR,
    "/review/scripts": _SCRIPT_REVIEWS,
    "/review/writer-agent": _WRITER_AGENT,
    "/review/projects": _PROJECT_MANAGEMENT,
    "/review/users": _TEAM_USERS,
    "/review/roles": _TEAM_ROLES,
    "/review/projects/{project_id}/members": (
        ("GET", "/api/workflow/projects/{project_id}/members"),
        ("GET", "/api/workflow/projects/{project_id}/member-candidates"),
        ("POST", "/api/workflow/projects/{project_id}/members"),
        ("DELETE", "/api/workflow/projects/{project_id}/members/{user_id}"),
    ),
    "/review/projects/{project_id}/versions": (
        ("GET", "/api/creative/projects/{project_id}"),
        ("GET", "/api/creative/projects/{project_id}/generated-content"),
        ("GET", "/api/creative/projects/{project_id}/generated-content/download"),
        ("GET", "/api/creative/projects/{project_id}/export"),
    ),
    "/admin/users": _ADMIN_USERS
    + (
        ("GET", "/api/admin/roles"),
        ("GET", "/api/admin/departments"),
    ),
    "/admin/roles": _ADMIN_ROLES,
    "/admin/config": (
        ("GET", "/api/config"),
        ("GET", "/api/config/form"),
        ("POST", "/api/config/form"),
        ("GET", "/api/config/raw"),
        ("POST", "/api/config/raw"),
        ("GET", "/api/config/backups"),
        ("POST", "/api/config/backup/create"),
        ("POST", "/api/config/backup/restore"),
        ("POST", "/api/config/backup/delete"),
        ("GET", "/api/hongguo/drama/settings"),
        ("PUT", "/api/hongguo/drama/settings"),
        ("POST", "/api/hongguo/drama/test"),
    ),
    "/admin/analytics": (
        ("GET", "/api/analytics/weekly-reports"),
        ("GET", "/api/analytics/weekly-reports/{report_id}"),
        ("GET", "/api/analytics/weekly-reports/{report_id}/export"),
    ),
    "/admin/data": (
        ("GET", "/api/files"),
        ("GET", "/api/files/page"),
        ("POST", "/api/files/operations"),
        ("GET", "/api/files/operations/{job_id}/download"),
        ("GET", "/api/file/content"),
        ("GET", "/api/file/preview"),
        ("GET", "/api/file/download"),
        ("POST", "/api/files/download"),
        ("DELETE", "/api/files"),
        ("POST", "/api/batch/crawl_comments"),
        ("POST", "/api/file/analyze"),
        ("POST", "/api/audio/analyze"),
        ("POST", "/api/file/review"),
        ("POST", "/api/batch/run"),
        ("POST", "/api/export/excel"),
        ("GET", "/api/media/list"),
        ("GET", "/api/media/download"),
    )
    + (("GET", "/api/jobs/{job_id}"),),
    "/admin/drama-player": (
        ("GET", "/api/drama-player/library"),
        ("GET", "/api/drama-player/episodes/{episode_id}/stream"),
    ),
    "/admin/jobs": (
        ("GET", "/api/config"),
        ("GET", "/api/server/ip"),
        ("GET", "/api/file/download"),
        ("GET", "/api/qrcode/status"),
        ("POST", "/api/qrcode/login"),
        ("POST", "/api/qrcode/clear"),
        ("POST", "/api/run"),
        ("POST", "/api/jobs"),
        ("GET", "/api/jobs"),
        ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/logs"),
        ("POST", "/api/jobs/{job_id}/stop"),
        ("POST", "/api/jobs/{job_id}/pause"),
        ("POST", "/api/jobs/{job_id}/resume"),
        ("POST", "/api/jobs/{job_id}/retry"),
        ("DELETE", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/delete"),
        ("POST", "/api/stop"),
        ("GET", "/api/logs"),
    ),
    "/admin/deliveries": (
        ("GET", "/api/creative/generated-resources"),
        ("GET", "/api/creative/projects/{project_id}/generated-content/download"),
    ),
    "/admin/audit": (
        ("GET", "/api/admin/audit"),
        ("GET", "/api/admin/audit/filter-options"),
        ("GET", "/api/admin/audit/export"),
        ("GET", "/api/admin/audit/{event_id}"),
    ),
    "/admin/departments": (
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/departments"),
        ("POST", "/api/admin/departments"),
        ("PATCH", "/api/admin/departments/{department_id}"),
        ("POST", "/api/admin/departments/{department_id}/disable"),
        ("DELETE", "/api/admin/departments/{department_id}"),
        ("GET", "/api/admin/users/{user_id}/departments"),
        ("PUT", "/api/admin/users/{user_id}/departments/{department_id}"),
        ("DELETE", "/api/admin/users/{user_id}/departments/{department_id}"),
    ),
    "/admin/navigation": (
        ("GET", "/api/admin/navigation"),
        ("PATCH", "/api/admin/navigation/{item_id}"),
        ("GET", "/api/admin/navigation/{item_id}/accounts"),
        ("PUT", "/api/admin/navigation/{item_id}/accounts"),
    ),
}


PAGE_ACCESS_CATALOG: tuple[PageAccessCatalogEntry, ...] = tuple(
    {
        "page_route": page_route,
        "api_routes": _PAGE_API_ROUTES.get(page_route, ()),
    }
    for page_route, _, _ in PAGE_ROUTES
)
