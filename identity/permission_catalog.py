# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import dataclass


_PERMISSION_CODES = (
    "*",
    "page.selection",
    "page.planning",
    "page.writing",
    "page.review",
    "page.team_management",
    "page.admin.delivery",
    "page.data_management",
    "rank.read",
    "analytics.read",
    "topic.read",
    "topic.generate",
    "topic.edit",
    "topic.submit",
    "drama.search",
    "drama.download",
    "drama.transcript.read",
    "data.media.preview",
    "data.media.delete",
    "breakdown.read",
    "breakdown.read.approved",
    "breakdown.generate",
    "breakdown.edit",
    "breakdown.version.create",
    "breakdown.submit",
    "project.read.assigned",
    "project.read.managed",
    "project.create",
    "project.edit.own_draft",
    "project.manage",
    "project.member.manage",
    "project.archive",
    "blueprint.read",
    "blueprint.generate",
    "blueprint.edit",
    "blueprint.version.create",
    "brief.read",
    "brief.read.approved",
    "brief.edit",
    "brief.submit",
    "character.read",
    "character.read.approved",
    "character.generate",
    "character.edit",
    "character.submit",
    "relationship.read",
    "relationship.read.approved",
    "relationship.edit",
    "outline.read",
    "outline.read.approved",
    "outline.generate",
    "outline.edit",
    "outline.version.create",
    "outline.submit",
    "beats.read",
    "beats.generate",
    "beats.edit",
    "beats.version.create",
    "beats.submit",
    "workflow.pool.read",
    "workflow.claim",
    "workflow.task.update",
    "workflow.read.managed",
    "workflow.assign",
    "workflow.reassign",
    "workflow.reclaim",
    "workflow.cancel",
    "version.read.managed",
    "version.compare",

    "job.create.selection",
    "job.create.creative",
    "job.create.writing",
    "job.read.own",
    "job.read.project",
    "job.cancel.own",
    "audit.read",
    "audit.read.project",
    "approved_content.read",
    "export.create",
    "identity.user.read",
    "identity.user.create",
    "identity.user.manage_created",
    "identity.role.read",
    "identity.role.create",
    "identity.role.manage_created",
)

# The catalog is intentionally a mapping: membership checks use permission codes,
# while seeding has a stable fallback display label to update idempotently.
PERMISSIONS = {code: code for code in _PERMISSION_CODES}

# Navigation configuration refers to permission codes from the catalog above;
# routes remain page locations, never permissions themselves.  The stable TEXT
# identifiers are also the seed identifiers for PostgreSQL navigation_items.
NAVIGATION_CATALOG = (
    {
        "id": "navigation:selection",
        "label": "选品工作台",
        "icon": "bi-funnel",
        "route": "/selection/dashboard",
        "required_permission": "page.selection",
        "sort_order": 10,
    },
    {
        "id": "navigation:selection:dashboard",
        "parent_id": "navigation:selection",
        "label": "工作概览",
        "route": "/selection/dashboard",
        "required_permission": "page.selection",
        "sort_order": 10,
    },
    {
        "id": "navigation:selection:ranks",
        "parent_id": "navigation:selection",
        "label": "今日榜单",
        "route": "/selection/ranks",
        "required_permission": "page.selection",
        "sort_order": 20,
    },
    {
        "id": "navigation:selection:hits",
        "parent_id": "navigation:selection",
        "label": "今日爆款",
        "route": "/selection/hits",
        "required_permission": "page.selection",
        "sort_order": 30,
    },
    {
        "id": "navigation:selection:topics",
        "parent_id": "navigation:selection",
        "label": "选题库",
        "route": "/selection/topics",
        "required_permission": "page.selection",
        "sort_order": 40,
    },
    {
        "id": "navigation:selection:dramas",
        "parent_id": "navigation:selection",
        "label": "剧目检索下载",
        "route": "/selection/dramas",
        "required_permission": "page.selection",
        "sort_order": 50,
    },
    {
        "id": "navigation:planning",
        "label": "创意策划",
        "icon": "bi-lightbulb",
        "route": "/planning/projects",
        "required_permission": "page.planning",
        "sort_order": 20,
    },
    {
        "id": "navigation:planning:projects",
        "parent_id": "navigation:planning",
        "label": "策划项目",
        "route": "/planning/projects",
        "required_permission": "page.planning",
        "sort_order": 10,
    },
    {
        "id": "navigation:writing",
        "label": "编剧工作台",
        "icon": "bi-pencil-square",
        "route": "/writing/projects",
        "required_permission": "page.writing",
        "sort_order": 30,
    },
    {
        "id": "navigation:writing:projects",
        "parent_id": "navigation:writing",
        "label": "编剧项目",
        "route": "/writing/projects",
        "required_permission": "page.writing",
        "sort_order": 10,
    },
    {
        "id": "navigation:review",
        "label": "项目管理",
        "icon": "bi-kanban",
        "route": "/review/projects",
        "required_permission": "page.review",
        "sort_order": 40,
    },
    {
        "id": "navigation:review:projects",
        "parent_id": "navigation:review",
        "label": "负责项目",
        "route": "/review/projects",
        "required_permission": "page.review",
        "sort_order": 10,
    },
    {
        "id": "navigation:review:data",
        "parent_id": "navigation:review",
        "label": "数据管理",
        "route": "/admin/data",
        "required_permission": "page.data_management",
        "sort_order": 20,
    },
    {
        "id": "navigation:review:drama-player",
        "parent_id": "navigation:review",
        "label": "已下载剧目播放",
        "route": "/admin/drama-player",
        "required_permission": "page.data_management",
        "sort_order": 30,
    },
    {
        "id": "navigation:team",
        "label": "团队管理",
        "icon": "bi-people",
        "route": "/review/users",
        "required_permission": "page.team_management",
        "sort_order": 50,
        "hide_for_system_admin": True,
    },
    {
        "id": "navigation:team:users",
        "parent_id": "navigation:team",
        "label": "团队账号",
        "route": "/review/users",
        "required_permission": "page.team_management",
        "sort_order": 10,
    },
    {
        "id": "navigation:team:roles",
        "parent_id": "navigation:team",
        "label": "角色授权",
        "route": "/review/roles",
        "required_permission": "page.team_management",
        "sort_order": 20,
    },
    {
        "id": "navigation:admin",
        "label": "系统管理",
        "icon": "bi-gear",
        "route": "/admin/users",
        "required_permission": "*",
        "sort_order": 60,
    },
    {
        "id": "navigation:admin:users",
        "parent_id": "navigation:admin",
        "label": "用户管理",
        "route": "/admin/users",
        "required_permission": "*",
        "sort_order": 10,
    },
    {
        "id": "navigation:admin:roles",
        "parent_id": "navigation:admin",
        "label": "角色权限",
        "route": "/admin/roles",
        "required_permission": "*",
        "sort_order": 20,
    },
    {
        "id": "navigation:admin:config",
        "parent_id": "navigation:admin",
        "label": "系统配置",
        "route": "/admin/config",
        "required_permission": "*",
        "sort_order": 30,
    },
    {
        "id": "navigation:admin:jobs",
        "parent_id": "navigation:admin",
        "label": "系统后台",
        "route": "/admin/jobs",
        "required_permission": "*",
        "sort_order": 40,
    },
    {
        "id": "navigation:admin:deliveries",
        "parent_id": "navigation:admin",
        "label": "成果交付",
        "route": "/admin/deliveries",
        "required_permission": "*",
        "sort_order": 50,
    },
    {
        "id": "navigation:admin:audit",
        "parent_id": "navigation:admin",
        "label": "审计日志",
        "route": "/admin/audit",
        "required_permission": "*",
        "sort_order": 60,
    },
    {
        "id": "navigation:admin:navigation",
        "parent_id": "navigation:admin",
        "label": "导航配置",
        "icon": "bi-menu-button-wide",
        "route": "/admin/navigation",
        "required_permission": "*",
        "sort_order": 70,
        "is_system": True,
    },
    {
        "id": "navigation:admin:schedules",
        "parent_id": "navigation:admin",
        "label": "调度中心",
        "icon": "bi-clock-history",
        "route": "/admin/schedules",
        "required_permission": "*",
        "sort_order": 80,
        "is_system": True,
    },
)

PERMISSION_DEPENDENCIES = {
    "breakdown.edit": {"breakdown.read", "topic.read", "page.selection"},
    "brief.edit": {"brief.read", "project.read.assigned", "page.planning"},
    "character.edit": {"character.read", "brief.read.approved", "page.planning"},
    "outline.edit": {
        "outline.read",
        "brief.read.approved",
        "character.read.approved",
        "page.writing",
    },
    "beats.edit": {"beats.read", "outline.read.approved", "page.writing"},

    "workflow.assign": {"workflow.read.managed", "project.read.managed", "page.review"},
    "export.create": {"approved_content.read", "page.admin.delivery"},
}


# Project owners may build execution roles, but cannot delegate identity,
# project governance, approval, audit, delivery, or unrestricted data access.
_NON_DELEGABLE_PERMISSIONS = {
    "*",
    "page.review",
    "page.team_management",
    "page.admin.delivery",
    "page.data_management",
    "project.read.managed",
    "project.manage",
    "project.member.manage",
    "project.archive",
    "workflow.read.managed",
    "workflow.assign",
    "workflow.reassign",
    "workflow.reclaim",
    "workflow.cancel",
    "version.read.managed",
    "version.compare",

    "audit.read",
    "audit.read.project",
    "approved_content.read",
    "export.create",
    "identity.user.read",
    "identity.user.create",
    "identity.user.manage_created",
    "identity.role.read",
    "identity.role.create",
    "identity.role.manage_created",
}
DELEGABLE_PERMISSIONS = frozenset(PERMISSIONS) - _NON_DELEGABLE_PERMISSIONS
DELEGABLE_TASK_TYPES = frozenset({"brief", "characters", "outline", "beats"})


@dataclass(frozen=True)
class RoleSeed:
    code: str
    name: str
    description: str
    is_system: bool
    permissions: tuple[tuple[str, str], ...]


def _scoped(codes: str, scope: str) -> tuple[tuple[str, str], ...]:
    return tuple((code, scope) for code in codes.split())


DEFAULT_ROLES = (
    RoleSeed(
        code="selection_operator",
        name="选品运营",
        description="整理榜单、选题、素材与剧目拆解，并直接完成阶段成果。",
        is_system=True,
        permissions=_scoped(
            """page.selection rank.read analytics.read topic.read topic.generate topic.edit
            topic.submit drama.search drama.download drama.transcript.read data.media.preview
            breakdown.read breakdown.generate breakdown.edit breakdown.version.create
            breakdown.submit workflow.pool.read workflow.claim workflow.task.update
            job.create.selection job.read.own job.cancel.own""",
            "task",
        ),
    ),
    RoleSeed(
        code="creative_planner",
        name="创意策划",
        description="将已完成的拆解转化为创作方案、任务书和人物关系。",
        is_system=True,
        permissions=_scoped(
            """page.planning project.read.assigned project.create project.edit.own_draft
            breakdown.read.approved blueprint.read blueprint.generate blueprint.edit
            blueprint.version.create brief.read brief.edit brief.submit character.read
            character.generate character.edit character.submit relationship.read
            relationship.edit workflow.pool.read workflow.claim workflow.task.update
            job.create.creative job.read.project job.cancel.own""",
            "project",
        ),
    ),
    RoleSeed(
        code="screenwriter",
        name="编剧",
        description="依据已完成的创作方案完成人物、全剧大纲与单集节拍。",
        is_system=True,
        permissions=_scoped(
            """page.writing project.read.assigned brief.read.approved character.read.approved
            relationship.read.approved outline.read outline.generate outline.edit
            outline.version.create outline.submit beats.read beats.generate beats.edit
            beats.version.create beats.submit workflow.pool.read workflow.claim
            workflow.task.update job.create.writing job.read.project job.cancel.own""",
            "project",
        ),
    ),
    RoleSeed(
        code="project_owner",
        name="项目负责人",
        description="负责项目组织、任务分配和阶段流转。",
        is_system=True,
        permissions=_scoped(
            """page.review page.team_management project.read.managed project.manage project.member.manage
            project.archive workflow.read.managed workflow.assign workflow.reassign
            workflow.reclaim workflow.cancel version.read.managed version.compare
            job.read.project audit.read.project page.data_management data.media.preview data.media.delete
            identity.user.read identity.user.create identity.user.manage_created
            identity.role.read identity.role.create identity.role.manage_created""",
            "managed_projects",
        ),
    ),
    RoleSeed(
        code="system_admin",
        name="系统管理员",
        description="负责系统权限、平台运维、数据治理、成果交付和异常恢复。",
        is_system=True,
        permissions=(("*", "all"),),
    ),
)
