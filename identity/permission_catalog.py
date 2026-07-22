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
    "page.admin.delivery",
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
    "version.approve",
    "version.return",
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

# The catalog is intentionally a mapping: membership checks use permission codes,
# while seeding has a stable fallback display label to update idempotently.
PERMISSIONS = {code: code for code in _PERMISSION_CODES}

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
    "version.approve": {"version.read.managed", "version.compare", "page.review"},
    "workflow.assign": {"workflow.read.managed", "project.read.managed", "page.review"},
    "export.create": {"approved_content.read", "page.admin.delivery"},
}


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
        description="整理榜单、选题、素材与剧目拆解，并提交成果审批。",
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
        description="将批准的拆解转化为创作方案、任务书和人物关系。",
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
        description="依据批准的创作方案完成人物、全剧大纲与单集节拍。",
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
        description="负责项目组织、任务分配、版本审批和阶段流转。",
        is_system=True,
        permissions=_scoped(
            """page.review project.read.managed project.manage project.member.manage
            project.archive workflow.read.managed workflow.assign workflow.reassign
            workflow.reclaim workflow.cancel version.read.managed version.compare
            version.approve version.return job.read.project audit.read.project""",
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
