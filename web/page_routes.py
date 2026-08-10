# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Neutral page route registry shared by navigation and authorization."""

from __future__ import annotations


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
    (
        "/writing/projects/{project_id}/episodes/{episode_number}/scripts",
        "page.writing",
        "场次剧本",
    ),
    ("/review/scripts", "page.script_review", "剧本审核"),
    ("/review/writer-agent", "page.script_review", "编剧大师"),
    ("/review/projects", "page.review", "负责项目"),
    ("/review/users", "page.team_management", "团队账号"),
    ("/review/roles", "page.team_management", "角色授权"),
    ("/review/projects/{project_id}/members", "page.review", "项目成员"),
    ("/review/projects/{project_id}/versions", "page.review", "项目版本"),
    ("/admin/users", "*", "用户管理"),
    ("/admin/roles", "*", "角色管理"),
    ("/admin/config", "*", "系统配置"),
    ("/admin/analytics", "analytics.read", "数据分析"),
    ("/admin/data", "page.data_management", "数据管理"),
    ("/admin/drama-player", "page.data_management", "已下载剧目播放"),
    ("/admin/jobs", "*", "机器任务"),
    ("/admin/deliveries", "*", "成果交付"),
    ("/admin/audit", "*", "审计日志"),
    ("/admin/departments", "*", "组织架构"),
    ("/admin/navigation", "*", "导航配置"),
)


# Dynamic detail and redirect routes inherit one configurable static page.
PAGE_ACCESS_GRANT_ROUTES: dict[str, str] = {
    "/planning/projects/{project_id}/brief": "/planning/projects",
    "/planning/projects/{project_id}/characters": "/planning/projects",
    "/planning/projects/{project_id}/directions": "/planning/projects",
    "/writing/projects/{project_id}/outlines": "/writing/projects",
    "/writing/projects/{project_id}/episodes/{episode_number}/beats": "/writing/projects",
    "/writing/projects/{project_id}/episodes/{episode_number}/scripts": "/writing/projects",
    "/review/projects/{project_id}/members": "/review/projects",
    "/review/projects/{project_id}/versions": "/review/projects",
}


# Project owners preview generated project content from the review workspace
# without being granted either authoring workspace.
PAGE_ACCESS_ADDITIONAL_GRANT_ROUTES: dict[str, tuple[str, ...]] = {
    "/planning/projects/{project_id}/brief": ("/review/projects",),
    "/planning/projects/{project_id}/characters": ("/review/projects",),
    "/writing/projects/{project_id}/outlines": ("/review/projects",),
    "/writing/projects/{project_id}/episodes/{episode_number}/beats": (
        "/review/projects",
    ),
    "/writing/projects/{project_id}/episodes/{episode_number}/scripts": (
        "/review/projects",
    ),
}


# These obsolete task pages remain registered endpoints but are not grantable.
DENIED_PAGE_ROUTES = frozenset({"/tasks", "/planning/tasks", "/writing/tasks"})
