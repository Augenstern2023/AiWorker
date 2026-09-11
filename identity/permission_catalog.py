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
    "page.script_review",
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
    "project.read.established",
    "project.read.managed",
    "project.write.established.writing",
    "project.create",
    "project.edit.own_draft",
    "project.manage",
    "project.member.manage",
    "project.archive",
    "project.delete",
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
    "script.read",
    "script.generate",
    "script.edit",
    "script.version.create",
    "script.submit",
    "script.review.generate",
    "script.review.read",
    "script.optimize.generate",
    "script.optimize.read",
    "script.optimize.accept",
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
)

# The catalog keeps the stable permission code for authorization and a clear
# Chinese label for role editors and exported permission lists.
PERMISSIONS = {
    "*": "全部权限",
    "page.selection": "访问选品工作台",
    "page.planning": "访问创意策划",
    "page.writing": "访问编剧工作台",
    "page.review": "访问项目管理",
    "page.script_review": "访问剧本审核",
    "page.admin.delivery": "访问成果交付",
    "page.data_management": "访问数据管理",
    "rank.read": "查看榜单",
    "analytics.read": "查看数据分析",
    "topic.read": "查看选题",
    "topic.generate": "生成选题",
    "topic.edit": "编辑选题",
    "topic.submit": "提交选题",
    "drama.search": "检索剧目",
    "drama.download": "下载剧目",
    "drama.transcript.read": "查看剧目转写",
    "data.media.preview": "预览媒体文件",
    "data.media.delete": "删除媒体文件",
    "breakdown.read": "查看剧目拆解",
    "breakdown.read.approved": "查看已完成剧目拆解",
    "breakdown.generate": "生成剧目拆解",
    "breakdown.edit": "编辑剧目拆解",
    "breakdown.version.create": "创建剧目拆解版本",
    "breakdown.submit": "提交剧目拆解",
    "project.read.assigned": "查看参与项目",
    "project.read.established": "查看已设立项目",
    "project.read.managed": "查看负责项目",
    "project.write.established.writing": "领取已设立项目编剧阶段",
    "project.create": "创建项目",
    "project.edit.own_draft": "编辑本人草稿项目",
    "project.manage": "管理项目",
    "project.member.manage": "管理项目成员",
    "project.archive": "归档项目",
    "project.delete": "删除项目",
    "blueprint.read": "查看创作方案",
    "blueprint.generate": "生成创作方案",
    "blueprint.edit": "编辑创作方案",
    "blueprint.version.create": "创建创作方案版本",
    "brief.read": "查看创作任务书",
    "brief.read.approved": "查看已完成创作任务书",
    "brief.edit": "编辑创作任务书",
    "brief.submit": "提交创作任务书",
    "character.read": "查看人物设定",
    "character.read.approved": "查看已完成人物设定",
    "character.generate": "生成人物设定",
    "character.edit": "编辑人物设定",
    "character.submit": "提交人物设定",
    "relationship.read": "查看人物关系",
    "relationship.read.approved": "查看已完成人物关系",
    "relationship.edit": "编辑人物关系",
    "outline.read": "查看全剧大纲",
    "outline.read.approved": "查看已完成全剧大纲",
    "outline.generate": "生成全剧大纲",
    "outline.edit": "编辑全剧大纲",
    "outline.version.create": "创建全剧大纲版本",
    "outline.submit": "提交全剧大纲",
    "beats.read": "查看单集节拍",
    "beats.generate": "生成单集节拍",
    "beats.edit": "编辑单集节拍",
    "beats.version.create": "创建单集节拍版本",
    "beats.submit": "提交单集节拍",
    "script.read": "查看场次剧本",
    "script.generate": "生成场次剧本",
    "script.edit": "编辑场次剧本",
    "script.version.create": "创建场次剧本版本",
    "script.submit": "提交场次剧本",
    "script.review.generate": "发起和确认剧本审核",
    "script.review.read": "查看剧本审核报告",
    "script.optimize.generate": "生成剧本优化草稿",
    "script.optimize.read": "查看剧本优化草稿",
    "script.optimize.accept": "采纳或拒绝剧本优化草稿",
    "workflow.pool.read": "查看任务池",
    "workflow.claim": "领取任务",
    "workflow.task.update": "更新任务状态",
    "workflow.read.managed": "查看负责项目任务",
    "workflow.assign": "分配任务",
    "workflow.reassign": "重新分配任务",
    "workflow.reclaim": "收回任务",
    "workflow.cancel": "取消任务",
    "version.read.managed": "查看负责项目版本",
    "version.compare": "对比版本",
    "job.create.selection": "创建选品任务",
    "job.create.creative": "创建策划任务",
    "job.create.writing": "创建编剧任务",
    "job.read.own": "查看本人任务",
    "job.read.project": "查看项目任务",
    "job.cancel.own": "取消本人任务",
    "audit.read": "查看审计日志",
    "audit.read.project": "查看项目审计日志",
    "approved_content.read": "查看已完成内容",
    "export.create": "导出成果",
}

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
        "label": "选品概览",
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
        "id": "navigation:selection:analytics",
        "parent_id": "navigation:selection",
        "label": "数据分析",
        "route": "/admin/analytics",
        "required_permission": "analytics.read",
        "sort_order": 60,
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
        "route": "/writing/dashboard",
        "required_permission": "page.writing",
        "sort_order": 30,
    },
    {
        "id": "navigation:writing:dashboard",
        "parent_id": "navigation:writing",
        "label": "工作概览",
        "route": "/writing/dashboard",
        "required_permission": "page.writing",
        "sort_order": 10,
    },
    {
        "id": "navigation:writing:projects",
        "parent_id": "navigation:writing",
        "label": "编剧项目",
        "route": "/writing/projects",
        "required_permission": "page.writing",
        "sort_order": 20,
    },
    {
        "id": "navigation:script-review",
        "label": "审核中心",
        "icon": "bi-clipboard-check",
        "route": "/review/scripts",
        "required_permission": "page.script_review",
        "sort_order": 35,
    },
    {
        "id": "navigation:script-review:scripts",
        "parent_id": "navigation:script-review",
        "label": "剧本审核",
        "route": "/review/scripts",
        "required_permission": "page.script_review",
        "sort_order": 10,
    },
    {
        "id": "navigation:script-review:writer-agent",
        "parent_id": "navigation:script-review",
        "label": "编剧大师",
        "route": "/review/writer-agent",
        "required_permission": "page.script_review",
        "sort_order": 20,
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
        "label": "角色管理",
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
        "required_permission": "audit.read",
        "sort_order": 60,
    },
    {
        "id": "navigation:admin:departments",
        "parent_id": "navigation:admin",
        "label": "组织架构",
        "icon": "bi-diagram-3",
        "route": "/admin/departments",
        "required_permission": "*",
        "sort_order": 65,
        "is_system": True,
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
    "script.edit": {"script.read", "beats.read", "page.writing"},
    "script.review.generate": {"script.review.read", "page.script_review"},
    "script.optimize.generate": {"script.optimize.read", "page.script_review"},
    "script.optimize.accept": {"script.optimize.read", "page.script_review"},

    "workflow.assign": {"workflow.read.managed", "project.read.managed", "page.review"},
    "export.create": {"approved_content.read", "page.admin.delivery"},
}


# Project owners may build execution roles, but cannot delegate identity,
# project governance, approval, audit, delivery, or unrestricted data access.
_NON_DELEGABLE_PERMISSIONS = {
    "*",
    "page.review",
    "page.admin.delivery",
    "page.data_management",
    "project.read.managed",
    "project.manage",
    "project.member.manage",
    "project.archive",
    "project.delete",
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
}
DELEGABLE_PERMISSIONS = frozenset(PERMISSIONS) - _NON_DELEGABLE_PERMISSIONS
DELEGABLE_TASK_TYPES = frozenset({"brief", "characters", "outline", "beats", "script"})


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
        description="依据已完成的创作方案完成全剧大纲、单集节拍与场次剧本。",
        is_system=True,
        permissions=_scoped(
            """page.writing project.read.assigned project.read.established project.write.established.writing project.create brief.read.approved character.read.approved
            relationship.read.approved outline.read outline.generate outline.edit
            outline.version.create outline.submit beats.read beats.generate beats.edit
            beats.version.create beats.submit script.read script.generate script.edit
            script.version.create script.submit page.script_review script.review.generate
            script.review.read script.optimize.generate script.optimize.read script.optimize.accept workflow.pool.read workflow.claim
            workflow.task.update job.create.writing job.read.project job.cancel.own""",
            "project",
        ),
    ),
    RoleSeed(
        code="project_owner",
        name="项目负责人",
        description="负责项目组织、任务分配、阶段流转与全局审计监督。",
        is_system=True,
        permissions=_scoped(
            """page.review page.script_review script.review.generate script.review.read
            script.optimize.generate script.optimize.read script.optimize.accept
            project.read.managed project.create project.manage project.member.manage
            project.archive project.delete workflow.read.managed workflow.assign workflow.reassign
            workflow.reclaim workflow.cancel version.read.managed version.compare
            job.read.project page.data_management data.media.preview data.media.delete""",
            "managed_projects",
        ) + (("audit.read", "all"),),
    ),
    RoleSeed(
        code="system_admin",
        name="系统管理员",
        description="负责系统权限、平台运维、数据治理、成果交付和异常恢复。",
        is_system=True,
        permissions=(("*", "all"),),
    ),
)


# Page access is intentionally separate from business permissions.  These are
# inserted only when a built-in role is first created, then remain editable.
DEFAULT_ROLE_PAGE_ROUTES: dict[str, frozenset[str]] = {
    "selection_operator": frozenset(
        {
            "/selection/dashboard",
            "/selection/ranks",
            "/selection/hits",
            "/selection/topics",
            "/selection/dramas",
        }
    ),
    "creative_planner": frozenset({"/planning/projects"}),
    "screenwriter": frozenset(
        {"/writing/dashboard", "/writing/projects", "/review/scripts"}
    ),
    "project_owner": frozenset(
        {
            "/admin/roles",
            "/admin/audit",
            "/admin/data",
            "/admin/drama-player",
            "/selection/dashboard",
            "/selection/ranks",
            "/selection/hits",
            "/selection/topics",
            "/selection/dramas",
            "/review/scripts",
        }
    ),
}
