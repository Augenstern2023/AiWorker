# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import asyncio
from copy import deepcopy
import csv
from datetime import datetime
from io import BytesIO, StringIO
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

import config
from analysis.llm import reasoning_efforts_for_model
from analysis.today_rank_service import TodayRankService

from creative.hit_breakdown import breakdown_field_guide, personalize_breakdown_field_guide
from creative.application_service import (
    CreativeApplicationService,
    CreativeContextConflict,
    CreativeProjectAccessDenied,
    CreativeTaskOccupied,
)
from creative.imitation_service import assess_similarity_risk
from creative.free_mode_service import FreeCreativeModeService
from creative.lifecycle_service import TopicLifecycleService, content_fingerprint
from creative.market_reference_service import MarketReferenceService
from creative.original_direction_service import validate_original_direction_content
from creative.original_project_service import OriginalProjectService
from creative.outline_fields import OUTLINE_TEXT_FIELDS, outline_field_source_fingerprint
from creative.outline_overview import SERIES_OVERVIEW_FIELD_LABELS, read_series_overview
from creative.prompt_marketplace import PromptMarketplaceService
from creative.prompt_observability import PromptObservabilityService
from creative.project_prompt_service import ProjectPromptService
from creative.prompt_response import prompt_projection_route
from creative.repository import CreativeRepository, TopicDecisionConflict, canonical_request_hash
from creative.script_service import normalize_script_content, rewrite_outline_field, validate_script
from creative.selection_stage_service import SelectionStageService
from creative.workflow_adapter import CreativeWorkflowAdapter
from creative.workflow_identity import WorkflowIdentityResolver
from creative.video_parameter_recommendation import (
    MODEL_CAPABILITIES,
    SCENARIOS,
    attach_recommendation_staleness,
    build_video_parameter_source,
    fallback_recommendation,
    video_parameter_analysis_deadline_seconds,
)

from creative.schema import (
    BlueprintDeriveRequest,
    BlueprintCreateRequest,
    BlueprintGenerateRequest,
    BlueprintUpdateRequest,
    BriefLockRequest,
    CharacterBatchRequest,
    CreativeLockRequest,
    CreativeItemUpdateRequest,
    CreativeModeChangeRequest,
    CreativeModeMigrationRequest,

    EpisodeBeatsRequest,
    FreeBriefCreateRequest,
    FreeCharacterBatchCreateRequest,
    FreeCreativeContextRefs,
    FreeCreativeGenerateRequest,
    FreeCreativeHeadRequest,
    FreeCreativeLockRequest,
    FreeCreativeSeriesGenerateRequest,
    FreeCreativeSeriesLockRequest,
    FreeEpisodeCreateRequest,
    FreeOutlineCreateRequest,
    OutlineBatchRequest,
    OutlineFieldRewriteApplyRequest,
    OutlineFieldRewriteRequest,
    OutlineOverviewRequest,
    OriginalProjectCreateRequest,
    PlanningAssignmentRequest,
    PromptBindingRequest,
    PromptGovernanceActionRequest,
    PromptImportPreviewRequest,
    PromptMarketplaceInstallRequest,
    PromptMarketplaceRatingRequest,
    PromptMarketplaceReviewRequest,
    PromptMarketplaceSubmissionRequest,
    PromptProjectEnableRequest,
    PromptTemplateCreateRequest,
    PromptTemplateDeleteRequest,
    PromptTemplateRebindDeleteRequest,
    PromptTemplateRevisionRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ReasoningEffort,
    VideoParameterConfirmRequest,
    SeedUpdateRequest,
    SeedVersionRequest,
    WorkflowLinkRequest,
)
from creative.topic_service import TopicService
from creative.transcript_assets import (
    find_downloaded_video_assets,
    find_transcript_bundle,
    render_transcript_bundle_text,
    transcript_bundle_filename,
)
from audit.service import AuditService
from runtime.job_manager import JobConflictError, JobNotFoundError
from identity.dependencies import current_principal, require_permission
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import (
    ESTABLISHED_WRITING_PERMISSION,
    ESTABLISHED_WRITING_TASK_TYPES,
    SYSTEM_ACTOR,
    WorkflowRepository,
)
from workflow.transition_service import TransitionService


logger = logging.getLogger("aiworker.creative")

_MODE_MIGRATION_PREVIEW_TIMEOUT_SECONDS = 8.0
_MODE_MIGRATION_NON_BLOCKING_JOB_TYPES = frozenset({
    "project_review",
    "screenplay_review",
    "video_parameter_recommendation",
})
_CREATIVE_PLATFORM_BUSY_ERROR = "platform 'creative' already has a running job"


def _recover_transient_video_parameter_conflict(
    repository: Any,
    project_id: int,
    record: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Recover a retained recommendation polluted by the old platform lock."""

    if (
        record
        and record.get("status") == "failed"
        and str(record.get("analysis_error") or "")
        == _CREATIVE_PLATFORM_BUSY_ERROR
    ):
        if record.get("source_fingerprint") and record.get("recommendation"):
            return (
                repository.restore_video_parameter_recommendation(project_id)
                or record
            )
        return None
    return record


class TopicGenerateRequest(BaseModel):
    platform: str = "dy"
    date: str = ""
    limit: int = 10
    rank_source: str = ""


class TopicAdoptFromRankRequest(BaseModel):
    platform: str
    date: str = ""
    record_id: str


class TopicUpdateRequest(BaseModel):
    status: str
    decision_note: str = ""



class BreakdownCreateRequest(BaseModel):
    topic_id: int
    force_regenerate: bool = False
    workflow_task_id: str | None = None
    llm_model_record_id: str | None = Field(None, min_length=1, max_length=64)
    reasoning_effort: ReasoningEffort | None = None


class BreakdownUpdateRequest(BaseModel):
    content: dict[str, Any]


def creative_api_principal(request: Request) -> Principal:
    permission_service = getattr(request.app.state, "identity_permission_service", None)
    if not isinstance(permission_service, PermissionService):
        raise HTTPException(401, "登录已失效，请重新登录")
    token = request.cookies.get(permission_service.session_service.cookie_name)
    principal = permission_service.principal_for_session(token)
    if principal is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    return principal


def has_creative_page_api_access(request: Request, principal: Principal) -> bool:
    page_access_service = getattr(request.app.state, "page_access_service", None)
    checker = getattr(page_access_service, "can_call_page_api", None)
    return bool(
        callable(checker)
        and checker(principal, request.method.upper(), request.url.path) is True
    )


def has_managed_project_preview_access(request: Request, principal: Principal) -> bool:
    page_access_service = getattr(request.app.state, "page_access_service", None)
    checker = getattr(page_access_service, "can_open_page", None)
    return bool(callable(checker) and checker(principal, "/review/projects"))


def established_writing_ensure_resource(path: str) -> str | None:
    match = re.fullmatch(
        r"/projects/\d+/workflow-tasks/(outline|beats|script)/ensure", path
    )
    return match.group(1) if match else None


def uses_project_member_stage_authorization(path: str, method: str) -> bool:
    """Defer project-scoped creative stages to the resource membership guard."""

    if path == "/projects" and method == "GET":
        return True
    if re.match(
        r"/(?:blueprints|characters|relationships|episodes|scripts)/\d+(?:/|$)",
        path,
    ):
        return True
    project_match = re.match(r"/projects/\d+(?P<suffix>/.*)?$", path)
    if project_match is None:
        return False
    suffix = project_match.group("suffix") or ""
    if method == "GET":
        return True
    if method == "PATCH" and not suffix:
        return True
    if suffix in {"/workflow-mode", "/migrate-to-free"}:
        return method in {"PATCH", "POST"}
    return bool(
        re.match(
            r"/(?:"
            r"free|blueprints|briefs|seeds|characters|character-batches|outlines|"
            r"beats|scripts|episodes/\d+/(?:beats|scripts)|generation-jobs|"
            r"video-parameter-recommendation|"
            r"prompt-library|prompt-marketplace|prompt-modules|prompt-system|"
            r"workflow-tasks/(?:brief|characters|outline|beats|script)"
            r")(?:/|$)",
            suffix,
        )
    )


def creative_api_authorization(
    request: Request,
    principal: Principal = Depends(creative_api_principal),
) -> Principal:
    path = request.url.path.removeprefix("/api/creative")
    method = request.method.upper()
    page_api_authorized = has_creative_page_api_access(request, principal)
    permissions: tuple[str, ...] | None
    if path.startswith("/topics"):
        if path.endswith("/planning-assignment"):
            permissions = ("project.create",)
        elif path == "/topics/workflow-candidates":
            permissions = ("project.manage",)
        elif path.endswith("/transcripts"):
            permissions = ("drama.transcript.read",)
        elif path.endswith("/workflow"):
            permissions = ("project.manage",)
        elif method == "GET":
            permissions = (
                "topic.read",
                "breakdown.read.approved",
                "brief.read.approved",
                "outline.read.approved",
            )
        elif path == "/topics/generate":
            permissions = ("topic.generate",)
        else:
            permissions = ("topic.edit",)
    elif path.startswith("/breakdowns"):
        permissions = (
            ("breakdown.read", "breakdown.read.approved") if method == "GET"
            else ("breakdown.generate",) if path == "/breakdowns" and method == "POST"
            else ("breakdown.edit", "project.manage")
        )
    elif path.startswith("/projects"):
        if path == "/projects/member-candidates":
            permissions = ("project.member.manage",)
        elif path == "/projects/original":
            permissions = ("project.create",)
        elif method == "DELETE" and re.fullmatch(r"/projects/\d+", path):
            permissions = ("project.delete",)
        elif method == "POST" and established_writing_ensure_resource(path):
            permissions = (ESTABLISHED_WRITING_PERMISSION,)
        elif method == "GET":
            permissions = (
                "project.read.assigned",
                "project.read.established",
                "project.read.managed",
            )
        elif path == "/projects":
            permissions = ("project.create",)
        elif "/blueprints/generate" in path:
            permissions = ("blueprint.generate",)
        elif "/characters" in path:
            permissions = (("character.generate",) if path.endswith(("/generate", "/generate-series")) else ("character.edit",))
        elif "/outlines" in path:
            permissions = (("outline.generate",) if path.endswith(("/generate", "/generate-series")) else ("outline.edit",))
        elif "/scripts" in path:
            permissions = (("script.generate",) if path.endswith(("/generate", "/generate-series")) else ("script.edit",))
        elif "/beats" in path:
            permissions = (("beats.generate",) if path.endswith(("/generate", "/generate-series")) else ("beats.edit",))
        else:
            permissions = ("project.edit.own_draft",)
    elif path.startswith("/blueprints"):
        permissions = ("brief.edit",)
    elif path.startswith("/characters"):
        permissions = ("character.edit",)
    elif path.startswith("/relationships"):
        permissions = ("relationship.edit",)
    elif path.startswith("/episodes"):
        permissions = (("script.edit", "beats.edit") if path.endswith("/lock") else ("outline.edit",))
    elif path.startswith("/seeds"):
        permissions = None
    elif path == "/workflow/submit":
        permissions = None
    else:
        permissions = ("*",)
    if (
        permissions is not None
        and not principal.is_system_admin
        and not page_api_authorized
        and not uses_project_member_stage_authorization(path, method)
        and not set(permissions).intersection(principal.permissions)
    ):
        raise HTTPException(403, "没有执行此操作的权限")
    match = re.match(r"/projects/(\d+)(?:/|$)", path)
    if (
        match
        and not principal.is_system_admin
        and not uses_project_member_stage_authorization(path, method)
    ):
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        local_project_id = match.group(1)
        creative_repository = getattr(
            request.app.state, "creative_repository", None
        )
        workflow_identity = getattr(
            request.app.state, "workflow_identity_resolver", None
        )
        project_id = (
            workflow_identity.project_id(local_project_id)
            if isinstance(workflow_identity, WorkflowIdentityResolver)
            else (
                creative_repository.workflow_project_id(local_project_id)
                if isinstance(creative_repository, CreativeRepository)
                else local_project_id
            )
        )
        can_claim_established_writing = bool(
            method == "POST"
            and established_writing_ensure_resource(path)
            and ESTABLISHED_WRITING_PERMISSION in principal.permissions
        )
        can_access = (
            permission_service.can_read_project(principal, project_id)
            if method == "GET"
            else (
                permission_service.can_access_assigned_project(principal, project_id)
                or permission_service.can_access_managed_project(principal, project_id)
                or can_claim_established_writing
            )
        )
        if not can_access:
            raise HTTPException(403, "只能访问当前分配或管理的项目")
    return principal


def _markdown_value(value: Any, level: int = 2) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            title = str(key).replace("_", " ")
            if isinstance(item, (dict, list)):
                lines.extend([f"{'#' * min(level, 6)} {title}", ""])
                lines.extend(_markdown_value(item, level + 1))
            else:
                lines.extend([f"- **{title}**：{item}", ""])
        return lines
    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            if isinstance(item, (dict, list)):
                lines.extend([f"{'#' * min(level, 6)} {index}", ""])
                lines.extend(_markdown_value(item, level + 1))
            else:
                lines.append(f"- {item}")
        lines.append("")
        return lines
    return [str(value), ""]


_BEATS_FIELD_GUIDE = (
    ("集数", "该组节拍所属的剧集序号。"),
    ("版本", "同一集节拍的版本号；数字越大表示版本越新。"),
    ("状态", "表示当前版本是草稿还是已锁定；已锁定版本不可直接修改。"),
    ("创建时间", "该版本首次写入系统的时间。"),
    ("更新时间", "该版本最近一次保存或锁定的时间。"),
    ("节拍序号", "该节拍在本集中的先后顺序。"),
    ("场景", "用于说明该节拍发生的具体地点和情境。"),
    ("出场人物", "参与当前节拍行动或冲突的角色。"),
    ("行动目标", "当前节拍中人物希望立即达成的结果。"),
    ("阻力", "阻止人物达成行动目标的人、事件或条件。"),
    ("情绪变化", "节拍开始到结束时，人物情绪或关系状态的变化。"),
    ("局面变化", "本节拍结束后不可逆改变的人物资源、关系、信息或选择。"),
    ("代价", "人物为本节拍行动付出的具体损失或后果。"),
    ("对抗策略", "对手在本节拍采用的具体施压或阻挠方式。"),
    ("连续性变更", "关键道具或信息的持有人、地点、知情人和状态变化。"),
    ("转折", "改变当前局面、信息或人物选择的关键事件。"),
    ("结尾钩子", "推动观众继续观看下一个节拍或下一集的悬念。"),
    ("上游版本", "记录本集节拍依赖的 Brief、人物关系和分集大纲版本。"),
    ("生成信息", "记录生成服务、提示词模板和生成任务，便于追溯。"),
)


def _format_export_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def _generation_model_label(meta: dict[str, Any] | None) -> str:
    values = dict(meta or {})
    provider = str(
        values.get("llm_provider_name") or values.get("provider") or ""
    ).strip()
    model = str(
        values.get("llm_model_display_name")
        or values.get("llm_model_id")
        or values.get("model")
        or ""
    ).strip()
    if provider and model and provider != model:
        return f"{provider} / {model}"
    return model or provider


def _generation_info_markdown(
    meta: dict[str, Any] | None, *, heading: str = "##"
) -> list[str]:
    values = dict(meta or {})
    model_label = _generation_model_label(values)
    if not model_label:
        return []
    return [
        f"{heading} 生成信息",
        "",
        f"- **生成模型**：{model_label or '-'}",
        "",
        f"- **提示词模板版本**：{values.get('prompt_template_version') or '-'}",
        "",
        f"- **生成任务 ID**：{values.get('job_id') or '-'}",
        "",
    ]


def _status_label(value: Any) -> str:
    return {
        "draft": "草稿",
        "locked": "已锁定",
        "brief_locked": "Brief 已锁定",
        "submitted": "历史待处理",
        "approved": "历史完成",
        "returned": "历史退回",
        "superseded": "已替代",
    }.get(str(value or ""), str(value or "-"))


def build_breakdown_markdown(breakdown: dict[str, Any]) -> str:
    content = breakdown.get("content") or {}
    title = str(content.get("title") or "剧目拆解").strip()
    lines = [
        f"# {title}",
        "",
        f"- 拆解版本：v{breakdown.get('version') or 1}",
        f"- 状态：{_status_label(breakdown.get('status'))}",
        "",
    ]
    seen: set[str] = set()
    for field in personalize_breakdown_field_guide(content):
        key = str(field.get("key") or "")
        if not key or key not in content:
            continue
        seen.add(key)
        lines.extend([f"## {field.get('label') or key}", ""])
        lines.extend(_markdown_value(content[key], 3))
    for key, value in content.items():
        if key in seen:
            continue
        lines.extend([f"## {str(key).replace('_', ' ')}", ""])
        lines.extend(_markdown_value(value, 3))
    risks = breakdown.get("risks") or []
    if risks:
        lines.extend(["## 风险提示", ""])
        lines.extend(_markdown_value(risks, 3))
    return "\n".join(lines).rstrip() + "\n"


def build_beats_markdown(project: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines = [
        f"# {project['name']}",
        "",
        "- 创作阶段：单集节拍",
        "",
        "## 字段说明",
        "",
        "| 字段 | 说明 |",
        "| --- | --- |",
        *(f"| {field} | {description} |" for field, description in _BEATS_FIELD_GUIDE),
        "",
    ]
    ordered_items = sorted(items, key=lambda item: (int(item.get("episode_number") or 0), int(item.get("version") or 0)))
    for item in ordered_items:
        episode_number = int(item.get("episode_number") or 0)
        version = int(item.get("version") or 1)
        lines.extend([
            f"## 第 {episode_number} 集 · v{version}",
            "",
            f"- **版本**：v{version}",
            "",
            f"- **状态**：{_status_label(item.get('status'))}",
            "",
            f"- **创建时间**：{_format_export_time(item.get('created_at'))}",
            "",
            f"- **更新时间**：{_format_export_time(item.get('updated_at'))}",
            "",
        ])
        if item.get("parent_version_id"):
            lines.extend([f"- **父版本记录 ID**：{item['parent_version_id']}", ""])

        beats = (item.get("content") or {}).get("beats") or []
        for index, beat in enumerate(beats, start=1):
            order = beat.get("order") or index
            characters = beat.get("characters") or []
            if not isinstance(characters, list):
                characters = [characters]
            lines.extend([
                f"### 节拍 {order}",
                "",
                f"- **节拍序号**：{order}",
                "",
                f"- **场景**：{beat.get('scene') or '-'}",
                "",
                "- **出场人物**：" + ("、".join(str(name) for name in characters if str(name).strip()) or "-"),
                "",
                f"- **行动目标**：{beat.get('goal') or '-'}",
                "",
                f"- **阻力**：{beat.get('obstacle') or '-'}",
                "",
                f"- **情绪变化**：{beat.get('emotion_change') or '-'}",
                "",
                f"- **局面变化**：{beat.get('state_change') or '-'}",
                "",
                f"- **代价**：{beat.get('cost') or '-'}",
                "",
                f"- **对抗策略**：{beat.get('opposition_tactic') or '-'}",
                "",
                f"- **转折**：{beat.get('turn') or '-'}",
                "",
                f"- **结尾钩子**：{beat.get('hook') or '-'}",
                "",
                "- **连续性变更**：" + (json.dumps(beat.get("continuity_updates") or [], ensure_ascii=False) if beat.get("continuity_updates") else "-"),
                "",
            ])

        refs = item.get("upstream_refs") or {}
        lines.extend([
            "### 上游版本",
            "",
            f"- **创作 Brief 记录 ID**：{refs.get('brief_id') or '-'}",
            "",
            f"- **人物关系批次 ID**：{refs.get('character_batch_id') or '-'}",
            "",
            f"- **分集大纲批次 ID**：{refs.get('outline_batch_id') or '-'}",
            "",
        ])
        meta = item.get("generation_meta") or {}
        if meta:
            lines.extend([
                "### 生成信息",
                "",
                f"- **生成服务**：{meta.get('provider') or '-'}",
                "",
                f"- **生成模型**：{_generation_model_label(meta) or '-'}",
                "",
                f"- **是否经过结构修复**：{'是' if meta.get('repair_attempted') else '否'}",
                "",
                f"- **提示词模板版本**：{meta.get('prompt_template_version') or '-'}",
                "",
                f"- **生成任务 ID**：{meta.get('job_id') or '-'}",
                "",
            ])
    return "\n".join(lines)


def build_outline_markdown(project: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Render outline episodes as a concise, writer-readable Markdown document."""
    lines = [
        f"# {project['name']}",
        "",
        "- 创作阶段：分集大纲",
        f"- 共 {len(items)} 集",
        "",
    ]
    fields = (
        ("core_event", "核心事件"),
        ("conflict", "冲突"),
        ("character_progress", "人物推进"),
        ("highlight", "爽点"),
        ("reversal", "反转"),
        ("cliffhanger", "结尾悬念"),
    )
    ordered_items = sorted(
        items,
        key=lambda item: int(item.get("episode_number") or (item.get("content") or {}).get("episode") or 0),
    )
    overview = read_series_overview(
        (ordered_items[0].get("content") or {}).get("series_overview")
    ) if ordered_items else None
    if ordered_items:
        lines.extend(
            _generation_info_markdown(
                ordered_items[0].get("generation_meta")
            )
        )
    if overview:
        lines.extend(["## 全集剧情概览", ""])
        for key, label in SERIES_OVERVIEW_FIELD_LABELS:
            lines.extend([f"- **{label}**：{overview[key]}", ""])
    for item in ordered_items:
        content = item.get("content") or {}
        episode_number = int(item.get("episode_number") or content.get("episode") or 0)
        lines.extend([f"## 第 {episode_number} 集", ""])
        for key, label in fields:
            lines.extend([f"- **{label}**：{content.get(key) or '-'}", ""])
        estimated_seconds = content.get("estimated_seconds")
        if estimated_seconds not in (None, ""):
            lines.extend([f"- **预计时长**：{estimated_seconds} 秒", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_script_text(
    project: dict[str, Any], content: dict[str, Any], *, episode_number: int
) -> str:
    """Render a structured episode script in the delivery TXT format."""
    content = normalize_script_content(content)
    title = str(content.get("episode_title") or f"第 {episode_number} 集").strip()
    lines = [f"第 {episode_number} 集:{title}", ""]
    for scene in content.get("scenes") or []:
        props = "、".join(str(item).strip() for item in scene.get("props") or [] if str(item).strip()) or "无"
        characters = "、".join(
            str(item).strip() for item in scene.get("characters") or [] if str(item).strip()
        ) or "无"
        lines.extend([
            f"[{int(scene.get('sequence_no') or 0)}]-[{int(scene.get('scene_no') or 0)}] "
            f"[{str(scene.get('time_of_day') or '').strip()}] "
            f"[{str(scene.get('interior_exterior') or '').strip()}] "
            f"[{str(scene.get('location') or '').strip()}]",
            f"道具:{props} 出场人物:{characters}",
            "",
        ])
        for block in scene.get("blocks") or []:
            block_type = str(block.get("type") or "")
            text = str(block.get("text") or "").strip()
            speaker = str(block.get("speaker") or "").strip()
            modifier = str(block.get("modifier") or "").strip()
            if block_type == "action":
                rendered = f"△ {text}"
            elif block_type == "dialogue":
                rendered = f"{speaker}({modifier}):{text}" if modifier else f"{speaker}:{text}"
            elif block_type == "inner_voice":
                rendered = f"{speaker}(内心OS):{text}"
            elif block_type == "voiceover":
                rendered = f"画外音（{speaker}）：{text}" if speaker else f"画外音：{text}"
            elif block_type == "cut":
                rendered = f"【切镜：{text}】" if text else "【切镜】"
            else:
                continue
            lines.extend([rendered, ""])
    return "\n".join(lines).rstrip() + "\n"


def script_download_filename(
    project: dict[str, Any], episode_number: int, version: int, format: str = "txt"
) -> str:
    drama_name = str(project.get("topic_title") or project.get("name") or "剧本").strip()
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", drama_name).strip(" .") or "剧本"
    return f"{safe_name}第{episode_number}集场次剧本v{version}.{format}"


def build_video_parameter_recommendation_text(
    record: dict[str, Any] | None,
) -> str:
    if not record or not isinstance(record.get("recommendation"), dict):
        return ""
    recommendation = record["recommendation"]
    models = recommendation.get("models") or {}
    confirmed = record.get("confirmed") or {}
    if not models or record.get("export_model_keys") == []:
        return ""
    lines = ["==================== AI 视频规格建议 ===================="]
    status_bits = [
        f"分析覆盖：{len(record.get('source_episode_numbers') or [])} 集",
        "完整全剧" if record.get("source_complete") else "部分剧本",
    ]
    if record.get("stale"):
        status_bits.append("剧本已变化，建议已过期")
    if recommendation.get("analysis_mode") == "rules_fallback":
        status_bits.append("规则默认建议（未完成 AI 剧本分析）")
    lines.extend([" · ".join(status_bits), ""])
    scenario_labels = {"platform_fit": "当前平台适配", "content_optimal": "内容最优"}
    export_model_keys = record.get("export_model_keys")
    for vendor, keys in (
        ("MiniMax", ("minimax_h3", "minimax_h3_max")),
        ("Seedance", ("seedance_2_5",)),
    ):
        lines.append(f"【{vendor}】")
        for key in keys:
            if isinstance(export_model_keys, list) and key not in export_model_keys:
                continue
            model = models.get(key) or {}
            capability = model.get("capability") or MODEL_CAPABILITIES.get(key, {})
            if not model:
                continue
            lines.append(
                f"- {capability.get('display_name') or key}"
                f"（{capability.get('model_id') or '-'}）"
            )
            selected = confirmed.get(key)
            if isinstance(selected, dict) and isinstance(selected.get("profile"), dict):
                state = "本次导出" if selected.get("export_adjusted") else "已确认"
                profiles = [(str(selected.get("scenario") or ""), selected["profile"], state)]
            else:
                profiles = [
                    (scenario, profile, "未确认")
                    for scenario, profile in (model.get("scenarios") or {}).items()
                    if isinstance(profile, dict)
                ]
            for scenario, profile, state in profiles:
                orientation = "竖屏" if profile.get("orientation") == "portrait" else "横屏"
                fps = (profile.get("fps") or {}).get("value")
                fps_text = f"，{fps} FPS" if fps else ""
                delivery = profile.get("delivery") or {}
                lines.append(
                    f"  · {scenario_labels.get(scenario, scenario)} [{state}]："
                    f"{orientation} {profile.get('ratio') or '-'}，"
                    f"生成档位 {profile.get('resolution_tier') or '-'}，"
                    f"建议单片段 {profile.get('clip_duration_seconds') or '-'} 秒"
                    f"{fps_text}，交付母版 {delivery.get('master_resolution') or '-'}"
                )
                rationale = str(profile.get("rationale") or "").strip()
                if rationale:
                    lines.append(f"    理由：{rationale}")
                risks = profile.get("special_scene_risks") or []
                for risk in risks[:3]:
                    episode = int(risk.get("episode_number") or 0)
                    prefix = f"第 {episode} 集" if episode else "特殊场景"
                    lines.append(
                        f"    风险：{prefix} {risk.get('scene') or ''}："
                        f"{risk.get('risk') or ''}；建议 {risk.get('suggestion') or ''}"
                    )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def video_parameter_export_record(
    record: dict[str, Any] | None, raw_options: str
) -> dict[str, Any] | None:
    """Apply validated, export-only ratio and resolution choices."""

    if not record or not raw_options:
        return record
    try:
        payload = json.loads(raw_options)
    except json.JSONDecodeError as exc:
        raise ValueError("视频尺寸导出参数不是有效 JSON") from exc
    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list) or len(raw_models) > len(MODEL_CAPABILITIES):
        raise ValueError("视频尺寸导出参数无效")
    recommendation = record.get("recommendation") or {}
    models = recommendation.get("models") or {}
    confirmed: dict[str, Any] = {}
    selected_keys: list[str] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            raise ValueError("视频尺寸导出参数无效")
        model_key = str(raw.get("model_key") or "")
        scenario = str(raw.get("scenario") or "platform_fit")
        ratio = str(raw.get("ratio") or "")
        resolution = str(raw.get("resolution_tier") or "")
        capability = MODEL_CAPABILITIES.get(model_key)
        model = models.get(model_key) if isinstance(models.get(model_key), dict) else None
        if capability is None or model is None or scenario not in SCENARIOS:
            raise ValueError("视频尺寸导出型号或方案无效")
        if ratio not in {"9:16", "16:9"}:
            raise ValueError("视频尺寸导出画幅无效")
        if resolution not in capability["resolution_tiers"]:
            raise ValueError("视频尺寸导出分辨率不受目标模型支持")
        current_confirmed = (record.get("confirmed") or {}).get(model_key) or {}
        if (
            current_confirmed.get("scenario") == scenario
            and isinstance(current_confirmed.get("profile"), dict)
        ):
            base_profile = current_confirmed["profile"]
        else:
            base_profile = ((model.get("scenarios") or {}).get(scenario))
        if not isinstance(base_profile, dict):
            raise ValueError("视频尺寸导出建议不存在")
        profile = deepcopy(base_profile)
        profile["ratio"] = ratio
        profile["orientation"] = "portrait" if ratio == "9:16" else "landscape"
        profile["resolution_tier"] = resolution
        delivery = dict(profile.get("delivery") or {})
        delivery.update(
            {
                "ratio": ratio,
                "orientation": profile["orientation"],
                "master_resolution": "1080×1920" if ratio == "9:16" else "1920×1080",
            }
        )
        profile["delivery"] = delivery
        confirmed[model_key] = {
            "scenario": scenario,
            "profile": profile,
            "capability": model.get("capability") or capability,
            "export_adjusted": True,
        }
        selected_keys.append(model_key)
    viewed = deepcopy(record)
    viewed["confirmed"] = confirmed
    viewed["export_model_keys"] = selected_keys
    return viewed


def build_merged_script_download(project: dict[str, Any]) -> tuple[str, str]:
    """Build one TXT download from scripts compatible with the current project context."""
    target_episode_count = int(project.get("target_episode_count") or 0)
    if target_episode_count < 1:
        target_episode_count = max(
            (int(item.get("episode_number") or 0) for item in project.get("episodes") or []),
            default=0,
        )
    current_context = {
        "brief_id": project.get("locked_brief_id"),
        "character_batch_id": project.get("character_batch_id"),
        "outline_batch_id": project.get("outline_batch_id"),
    }
    locked_beats: dict[int, dict[str, Any]] = {}
    for item in project.get("episodes") or []:
        if item.get("stage") != "beats" or item.get("status") not in {"locked", "approved"}:
            continue
        episode_number = int(item.get("episode_number") or 0)
        if not 1 <= episode_number <= target_episode_count:
            continue
        current = locked_beats.get(episode_number)
        if current is None or (
            int(item.get("version") or 0), int(item.get("id") or 0)
        ) > (
            int(current.get("version") or 0), int(current.get("id") or 0)
        ):
            locked_beats[episode_number] = item

    scripts: dict[int, dict[str, Any]] = {}
    for item in project.get("episodes") or []:
        if item.get("stage") != "script" or item.get("status") not in {"draft", "locked", "approved"}:
            continue
        episode_number = int(item.get("episode_number") or 0)
        if not 1 <= episode_number <= target_episode_count:
            continue
        beats = locked_beats.get(episode_number)
        refs = item.get("upstream_refs") or {}
        if any(refs.get(key) != value for key, value in current_context.items()):
            continue
        if beats is None or (
            int(refs.get("beats_id") or 0), int(refs.get("beats_version") or 0)
        ) != (int(beats.get("id") or 0), int(beats.get("version") or 0)):
            continue
        current = scripts.get(episode_number)
        if current is None or (
            int(item.get("version") or 0), int(item.get("id") or 0)
        ) > (
            int(current.get("version") or 0), int(current.get("id") or 0)
        ):
            scripts[episode_number] = item

    ordered_scripts = [scripts[number] for number in sorted(scripts)]
    if not ordered_scripts:
        raise ValueError("尚无可下载的已生成场次剧本")

    episode_numbers = [int(item["episode_number"]) for item in ordered_scripts]
    versions = sorted({int(item.get("version") or 1) for item in ordered_scripts})
    version_label = f"v{versions[0]}" if len(versions) == 1 else "-".join(f"v{version}" for version in versions)
    drama_name = str(project.get("topic_title") or project.get("name") or "剧本").strip()
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", drama_name).strip(" .") or "剧本"
    filename = (
        f"{safe_name}第{episode_numbers[0]}集-第{episode_numbers[-1]}集"
        f"场次剧本{version_label}.txt"
    )
    sections = []
    for item in ordered_scripts:
        episode_number = int(item["episode_number"])
        sections.append(
            "\n".join(
                [
                    f"==================== 第 {episode_number} 集 ====================",
                    "",
                    build_script_text(
                        project,
                        item.get("content") or {},
                        episode_number=episode_number,
                    ).rstrip(),
                ]
            )
        )
    recommendation_text = build_video_parameter_recommendation_text(
        project.get("video_parameter_recommendation")
    )
    body = "\n\n".join(sections).rstrip() + "\n"
    if recommendation_text:
        body = f"{recommendation_text}\n{body}"
    return filename, body


def current_episode_download_items(
    project: dict[str, Any], stage: str
) -> list[dict[str, Any]]:
    """Return the same current episode resources shown by the writing workspace."""

    if stage not in {"outline", "beats", "script"}:
        raise ValueError("stage 必须是 outline、beats 或 script")
    episodes = project.get("episodes") or []
    heads = project.get("resource_heads") or []
    free_mode = project.get("workflow_mode") == "free"
    target_count = int(project.get("target_episode_count") or 0)
    if target_count < 1:
        target_count = max(
            (int(item.get("episode_number") or 0) for item in episodes),
            default=0,
        )

    def head_ref(head_stage: str, episode_number: int = 0) -> str:
        head = next(
            (
                item for item in heads
                if item.get("stage") == head_stage
                and int(item.get("episode_number") or 0) == episode_number
            ),
            None,
        )
        return str((head or {}).get("resource_ref") or "")

    if stage == "outline":
        if free_mode:
            batch_id = head_ref("outline")
        else:
            compatible = [
                item for item in episodes
                if item.get("stage") == "outline"
                and int((item.get("upstream_refs") or {}).get("brief_id") or 0)
                == int(project.get("locked_brief_id") or 0)
                and str((item.get("upstream_refs") or {}).get("character_batch_id") or "")
                == str(project.get("character_batch_id") or "")
            ]
            drafts = sorted(
                (
                    item for item in compatible
                    if item.get("status") in {"draft", "submitted", "returned"}
                ),
                key=lambda item: (int(item.get("version") or 0), int(item.get("id") or 0)),
                reverse=True,
            )
            if project.get("status") == "outline_draft" and drafts:
                batch_id = str(drafts[0].get("batch_id") or "")
            elif project.get("outline_batch_id") and any(
                str(item.get("batch_id") or "") == str(project.get("outline_batch_id"))
                for item in compatible
            ):
                batch_id = str(project.get("outline_batch_id"))
            else:
                batch_id = str(drafts[0].get("batch_id") or "") if drafts else ""
        return sorted(
            (
                item for item in episodes
                if item.get("stage") == "outline"
                and str(item.get("batch_id") or "") == batch_id
            ),
            key=lambda item: int(item.get("episode_number") or 0),
        )

    def latest_by_episode(candidates: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        selected: dict[int, dict[str, Any]] = {}
        for item in candidates:
            number = int(item.get("episode_number") or 0)
            if not 1 <= number <= target_count:
                continue
            current = selected.get(number)
            if current is None or (
                int(item.get("version") or 0), int(item.get("id") or 0)
            ) > (
                int(current.get("version") or 0), int(current.get("id") or 0)
            ):
                selected[number] = item
        return selected

    beat_candidates = [
        item for item in episodes
        if item.get("stage") == "beats"
        and (
            str(item.get("id") or "") == head_ref(
                "beats", int(item.get("episode_number") or 0)
            )
            if free_mode
            else (item.get("upstream_refs") or {}).get("outline_batch_id")
            == project.get("outline_batch_id")
        )
    ]
    current_beats = latest_by_episode(beat_candidates)
    if stage == "beats":
        return [current_beats[number] for number in sorted(current_beats)]

    if free_mode:
        script_candidates = [
            item for item in episodes
            if item.get("stage") == "script"
            and str(item.get("id") or "") == head_ref(
                "script", int(item.get("episode_number") or 0)
            )
        ]
    else:
        locked_beats = {
            number: item for number, item in current_beats.items()
            if item.get("status") in {"locked", "approved"}
        }
        script_candidates = []
        for item in episodes:
            if item.get("stage") != "script":
                continue
            number = int(item.get("episode_number") or 0)
            beats = locked_beats.get(number)
            refs = item.get("upstream_refs") or {}
            if beats and (
                int(refs.get("beats_id") or 0),
                int(refs.get("beats_version") or 0),
            ) == (
                int(beats.get("id") or 0),
                int(beats.get("version") or 0),
            ):
                script_candidates.append(item)
    current_scripts = latest_by_episode(script_candidates)
    return [current_scripts[number] for number in sorted(current_scripts)]


def build_episode_selection_download(
    project: dict[str, Any],
    stage: str,
    episode_numbers: list[int] | None,
    format: str,
    video_parameter_record: dict[str, Any] | None = None,
) -> tuple[str, bytes, str]:
    """Build a writer-facing download for selected current episode resources."""

    allowed_formats = {"script": {"txt", "json"}, "outline": {"md", "json"}, "beats": {"md", "json"}}
    if stage not in allowed_formats:
        raise ValueError("stage 必须是 outline、beats 或 script")
    if format not in allowed_formats[stage]:
        raise ValueError(f"{stage} 不支持 {format} 格式")
    available = {
        int(item.get("episode_number") or 0): item
        for item in current_episode_download_items(project, stage)
    }
    requested = sorted(available) if episode_numbers is None else sorted(set(episode_numbers))
    missing = [number for number in requested if number not in available]
    if missing:
        raise ValueError(f"以下集数没有当前可下载内容：{'、'.join(map(str, missing))}")
    items = [available[number] for number in requested]
    if not items:
        raise ValueError(f"尚无可下载的{ {'outline': '分集大纲', 'beats': 'Beats', 'script': '场次剧本'}[stage] }")

    if format == "json":
        payload = {
                "project_id": project["id"],
                "project_name": project["name"],
                "stage": stage,
                "episode_numbers": requested,
                "content": items,
            }
        if stage == "script" and video_parameter_record:
            payload["video_parameter_recommendation"] = video_parameter_record
        body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        media_type = "application/json"
    elif stage == "outline":
        body = build_outline_markdown(project, items).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
    elif stage == "beats":
        body = build_beats_markdown(project, items).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
    else:
        sections = [
            "\n".join(
                [
                    f"==================== 第 {number} 集 ====================",
                    "",
                    build_script_text(
                        project, available[number].get("content") or {},
                        episode_number=number,
                    ).rstrip(),
                ]
            )
            for number in requested
        ]
        text = "\n\n".join(sections).rstrip() + "\n"
        recommendation_text = build_video_parameter_recommendation_text(
            video_parameter_record
        )
        if recommendation_text:
            text = f"{recommendation_text}\n{text}"
        body = text.encode("utf-8")
        media_type = "text/plain; charset=utf-8"

    project_name = str(project.get("topic_title") or project.get("name") or "项目").strip()
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", project_name).strip(" .") or "项目"
    if len(requested) == 1:
        scope = f"第{requested[0]}集"
    elif requested == list(range(requested[0], requested[-1] + 1)):
        scope = f"第{requested[0]}集-第{requested[-1]}集"
    else:
        scope = "第" + "、".join(map(str, requested)) + "集"
    stage_label = {"outline": "分集大纲", "beats": "Beats", "script": "场次剧本"}[stage]
    return f"{safe_name}{scope}{stage_label}.{format}", body, media_type


def build_stage_export(project: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage == "brief":
        selected = next(
            (item for item in project["blueprints"] if item["id"] == project.get("locked_brief_id")),
            None,
        )
        if not selected:
            raise ValueError("项目尚未锁定 Brief")
        content: Any = selected
    elif stage == "characters":
        batch_id = project.get("character_batch_id")
        if not batch_id:
            raise ValueError("项目尚未锁定人物关系")
        content = {
            "batch_id": batch_id,
            "characters": [item for item in project["characters"] if item["batch_id"] == batch_id],
            "relationships": [item for item in project["relationships"] if item["batch_id"] == batch_id],
        }
    elif stage == "outline":
        batch_id = project.get("outline_batch_id")
        if not batch_id:
            raise ValueError("项目尚未锁定分集大纲")
        content = [
            item for item in project["episodes"]
            if item["stage"] == "outline" and item["batch_id"] == batch_id
        ]
    elif stage == "beats":
        content = [
            item for item in project["episodes"]
            if item["stage"] == "beats"
            and item.get("upstream_refs", {}).get("outline_batch_id") == project.get("outline_batch_id")
        ]
        if not content:
            raise ValueError("项目尚无单集 Beats")
    else:
        raise ValueError("stage 必须是 brief、characters、outline 或 beats")
    return {
        "project_id": project["id"],
        "project_name": project["name"],
        "stage": stage,
        "source": {
            "topic_id": project["topic_id"],
            "breakdown_id": project["breakdown_id"],
            "breakdown_version": project["breakdown_version"],
        },
        "content": content,
    }


def build_creative_router(
    root: str | Path = ".",
    runtime_dir: str | Path = ".runtime",
    job_manager: Any | None = None,
    workflow_repository: WorkflowRepository | None = None,
    repository: CreativeRepository | None = None,
    application_service: CreativeApplicationService | None = None,
    llm_provider_factory: Callable[[], Any] | None = None,
    llm_model_resolver: Callable[[str], dict[str, Any]] | None = None,
    rank_service: TodayRankService | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/creative",
        tags=["creative"],
        dependencies=[Depends(creative_api_authorization)],
    )
    if repository is None:
        raise ValueError("build_creative_router requires an injected creative repository")

    if workflow_repository is None:
        workflow_repository = WorkflowRepository(
            IdentityRepository(
                SystemDatabase(SystemDatabaseSettings.from_environment())
            )
        )
    workflow_identity = WorkflowIdentityResolver(repository, workflow_repository)

    def workflow_project_id(project_id: int | str) -> str:
        return workflow_identity.project_id(project_id)

    workflow_adapter = (
        application_service.workflow_adapter
        if application_service is not None
        else CreativeWorkflowAdapter(repository, workflow_repository)
    )
    workflow_transitions = (
        application_service.transitions
        if application_service is not None
        else TransitionService(workflow_repository, creative_adapter=workflow_adapter)
    )
    workflow_transitions.migrate_pending_creative_approvals()
    service = TopicService(str(root), rank_service=rank_service)
    workspace_root = Path(root).resolve()
    lifecycle_service = TopicLifecycleService(repository, workspace_root, job_manager)
    market_reference_service = MarketReferenceService(repository)
    original_project_service = (
        application_service.original_project_service
        if application_service is not None
        else OriginalProjectService(
            repository, workflow_repository, market_reference_service
        )
    )
    free_mode_service = FreeCreativeModeService(repository)
    project_prompt_service = ProjectPromptService(repository)
    prompt_marketplace_service = PromptMarketplaceService(
        repository, project_prompt_service.store
    )
    prompt_observability_service = (
        PromptObservabilityService(repository, job_manager)
        if job_manager is not None
        else None
    )
    if application_service is None:
        workflow_identity_repository = getattr(
            workflow_repository, "identity_repository", None
        )
        if workflow_identity_repository is not None:
            application_permission_service: Any = PermissionService(
                workflow_identity_repository,
                SessionService(workflow_identity_repository),
            )
        else:
            class InjectedWorkflowPermissionBoundary:
                """Narrow fallback for routers built with a repository test double."""

                @staticmethod
                def can_create_project_content(
                    principal: Principal,
                    project_id: str,
                    *,
                    active_member: bool,
                ) -> bool:
                    del project_id
                    return bool(
                        active_member
                        or principal.is_system_admin
                        or "project_owner" in principal.role_codes
                    )

            application_permission_service = InjectedWorkflowPermissionBoundary()
        application_service = CreativeApplicationService(
            repository,
            workflow_repository,
            workflow_transitions,
            job_manager,
            Path(runtime_dir),
            original_project_service=original_project_service,
            workflow_adapter=workflow_adapter,
            permission_service=application_permission_service,
        )

    def topic_library_items(
        principal: Principal,
        platform: str = "",
        status: str = "",
        rank_source: str = "",
        category: str = "",
        min_score: float | None = None,
        max_score: float | None = None,
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        lifecycle_status: str = "",
        include_lifecycle: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        approved_context_only = (
            not principal.is_system_admin and "topic.read" not in principal.permissions
        )
        if approved_context_only:
            status = "adopted"
            has_locked_breakdown = True
        topics = repository.list_topics(
            platform=platform,
            status=status,
            rank_source=rank_source,
            category=category,
            min_score=min_score,
            max_score=max_score,
            captured_date=captured_date,
            has_locked_breakdown=has_locked_breakdown,
            limit=limit,
        )
        if not include_lifecycle and not lifecycle_status:
            return topics
        for topic in topics:
            if topic.get("latest_breakdown_id"):
                workflow_adapter.reconcile_resource(
                    "breakdown", str(topic["latest_breakdown_id"])
                )
        if any(topic.get("latest_breakdown_id") for topic in topics):
            topics = repository.list_topics(
                platform=platform,
                status=status,
                rank_source=rank_source,
                category=category,
                min_score=min_score,
                max_score=max_score,
                captured_date=captured_date,
                has_locked_breakdown=has_locked_breakdown,
                limit=limit,
            )
        items = lifecycle_service.enrich_topics(topics)
        if lifecycle_status:
            items = [item for item in items if item.get("lifecycle_status") == lifecycle_status]
        return items

    def add_topic_project_access(
        request: Request,
        principal: Principal,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        for item in items:
            project_id = item.get("lifecycle_project_id")
            item["can_open_project"] = bool(
                project_id
                and (
                    principal.is_system_admin
                    or workflow_repository.is_active_project_member(
                        workflow_project_id(project_id), principal
                    )
                    or (
                        isinstance(permission_service, PermissionService)
                        and permission_service.can_read_project(
                            principal, workflow_project_id(project_id)
                        )
                    )
                )
            )
        return items

    def topic_breakdown_export_label(topic: dict) -> str:
        if not topic.get("latest_breakdown_id"):
            return "未拆解"
        version = topic.get("latest_breakdown_version")
        suffix = f" v{version}" if version else ""
        if topic.get("latest_breakdown_status") in {"locked", "approved"}:
            return f"已锁定{suffix}"
        if topic.get("locked_breakdown_id"):
            return f"草稿{suffix} · 已有批准版"
        return f"草稿{suffix} · 待提交"

    def topic_export_row(topic: dict) -> list[str]:
        source_platforms = topic.get("source_platforms") or [topic.get("platform")]
        score = topic.get("score")
        return [
            str(topic.get("title") or ""),
            str(topic.get("description") or ""),
            "、".join(str(platform) for platform in source_platforms if platform),
            str(topic.get("rank_type") or "-"),
            str(topic.get("category") or "-"),
            f"{float(score or 0):.1f}",
            str(topic.get("lifecycle_label") or topic.get("status") or "-"),
            topic_breakdown_export_label(topic),
        ]

    def creative_resource_scope(
        request: Request,
        principal: Principal = Depends(creative_api_principal),
    ) -> None:
        if principal.is_system_admin or "project_owner" in principal.role_codes:
            return
        path = request.url.path.removeprefix("/api/creative")
        project_id: int | None = None
        task_id: str | None = None
        match = re.match(r"/projects/(\d+)(?:/|$)", path)
        if match:
            project_id = int(match.group(1))
        else:
            resource_patterns = (
                (r"/blueprints/(\d+)", repository.get_blueprint),
                (r"/characters/(\d+)", repository.get_character),
                (r"/relationships/(\d+)", repository.get_relationship),
                (r"/episodes/(\d+)", repository.get_episode),
            )
            for pattern, getter in resource_patterns:
                resource_match = re.match(pattern, path)
                if resource_match:
                    item = getter(int(resource_match.group(1)))
                    if item is None:
                        return
                    project_id = int(item["project_id"])
                    task_id = str(item.get("workflow_task_id") or "") or None
                    break
            breakdown_match = re.match(r"/breakdowns/(\d+)", path)
            if breakdown_match:
                item = repository.get_breakdown(int(breakdown_match.group(1)))
                if item is None:
                    return
                task_id = str(item.get("workflow_task_id") or "") or None
        if task_id is not None:
            task = workflow_repository.get_visible_task(task_id, principal)
            if task is None:
                raise HTTPException(403, "只能访问当前任务范围内的创作资源")
            active_project_member = bool(
                project_id is not None
                and workflow_repository.is_active_project_member(
                    workflow_project_id(project_id), principal
                )
            )
            if (
                request.method.upper() != "GET"
                and not active_project_member
                and task.get("assignee_user_id") != principal.user_id
            ):
                raise HTTPException(403, "只有当前任务执行人可以修改创作资源")
        if project_id is not None:
            permission_service = getattr(
                request.app.state, "identity_permission_service", None
            )
            if not isinstance(permission_service, PermissionService):
                raise HTTPException(500, "identity permission service is not configured")
            can_claim_established_writing = bool(
                request.method.upper() == "POST"
                and established_writing_ensure_resource(path)
                and ESTABLISHED_WRITING_PERMISSION in principal.permissions
            )
            can_access = (
                workflow_repository.is_active_project_member(
                    workflow_project_id(project_id), principal
                )
                or permission_service.can_read_project(
                    principal, workflow_project_id(project_id)
                )
                if request.method.upper() == "GET"
                else (
                    workflow_repository.is_active_project_member(
                        workflow_project_id(project_id), principal
                    )
                    or permission_service.can_access_assigned_project(
                        principal, workflow_project_id(project_id)
                    )
                    or permission_service.can_access_managed_project(
                        principal, workflow_project_id(project_id)
                    )
                    or can_claim_established_writing
                )
            )
            if not can_access:
                raise HTTPException(403, "只能访问当前分配或管理的项目")

    router.dependencies.append(Depends(creative_resource_scope))

    def optional_principal(request: Request) -> Principal | None:
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            return None
        token = request.cookies.get(permission_service.session_service.cookie_name)
        return permission_service.principal_for_session(token)

    def creative_job_principal(request: Request) -> Principal | None:
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        principal = optional_principal(request)
        if principal is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        return principal

    def assert_project_generation_access(
        project_id: int, principal: Principal | None
    ) -> None:
        if principal is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        if principal.is_system_admin or "project_owner" in principal.role_codes:
            return
        if not workflow_repository.is_active_project_member(
            workflow_project_id(project_id), principal
        ):
            raise HTTPException(403, "只能在已分配的项目中创建机器任务")

    def project_or_404(project_id: int) -> dict[str, Any]:
        project = repository.get_project(project_id)
        if not project:
            raise HTTPException(404, "creative project not found")
        if project.get("workflow_mode", "sequential") == "sequential":
            workflow_adapter.reconcile_project(project_id)
            project = repository.get_project(project_id) or project
        return project

    def sequential_project_or_409(project_id: int) -> dict[str, Any]:
        project = project_or_404(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise HTTPException(409, "自由创作项目必须使用自由创作接口")
        return project

    def assert_project_read_access(
        request: Request, project_id: int, principal: Principal
    ) -> None:
        if principal.is_system_admin:
            return
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        if (
            not workflow_repository.is_active_project_member(
                workflow_project_id(project_id), principal
            )
            and not permission_service.can_read_project(
                principal, workflow_project_id(project_id)
            )
        ):
            raise HTTPException(403, "只能访问当前分配或管理的项目")

    def assert_project_content_write_access(
        request: Request, project_id: int, principal: Principal
    ) -> None:
        if principal.is_system_admin:
            return
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        scoped_project_id = workflow_project_id(project_id)
        active_member = workflow_repository.is_active_project_member(
            scoped_project_id, principal
        )
        if not permission_service.can_create_project_content(
            principal, scoped_project_id, active_member=active_member
        ):
            raise HTTPException(403, "只有项目编剧成员可以分析或确认视频参数")

    def assert_project_owner_preview_access(
        request: Request, project_id: int, principal: Principal
    ) -> None:
        if (
            principal.is_system_admin
            or workflow_repository.is_active_project_member(
                workflow_project_id(project_id), principal
            )
            or has_creative_page_api_access(request, principal)
        ):
            return
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        if not permission_service.can_access_managed_project(
            principal, workflow_project_id(project_id)
        ):
            raise HTTPException(403, "只有项目负责人可以预览已生成内容")

    def generated_stage_versions(
        project: dict[str, Any], stage: str
    ) -> tuple[str, list[dict[str, Any]]]:
        def updated_at(item: dict[str, Any]) -> float:
            return float(item.get("updated_at") or item.get("created_at") or 0)

        if stage == "brief":
            versions = [
                {
                    "resource_id": str(item["id"]),
                    "label": f"Brief v{item.get('version') or 1}",
                    "updated_at": updated_at(item),
                    "version": int(item.get("version") or 1),
                }
                for item in project["blueprints"]
            ]
            resource_type = "brief"
        elif stage == "characters":
            groups: dict[str, list[dict[str, Any]]] = {}
            for item in [*project["characters"], *project["relationships"]]:
                groups.setdefault(str(item.get("batch_id") or ""), []).append(item)
            versions = [
                {
                    "resource_id": f"{project['id']}:{batch_id}",
                    "label": f"人物关系 v{max(int(item.get('version') or 1) for item in items)}",
                    "updated_at": max(updated_at(item) for item in items),
                    "version": max(int(item.get("version") or 1) for item in items),
                }
                for batch_id, items in groups.items()
                if batch_id and any(item in project["characters"] for item in items)
            ]
            resource_type = "characters"
        elif stage == "outline":
            groups: dict[str, list[dict[str, Any]]] = {}
            for item in project["episodes"]:
                if item.get("stage") == "outline":
                    groups.setdefault(str(item.get("batch_id") or ""), []).append(item)
            versions = [
                {
                    "resource_id": f"{project['id']}:{batch_id}",
                    "label": f"分集大纲 v{max(int(item.get('version') or 1) for item in items)}",
                    "updated_at": max(updated_at(item) for item in items),
                    "version": max(int(item.get("version") or 1) for item in items),
                }
                for batch_id, items in groups.items()
                if batch_id
            ]
            resource_type = "outline"
        elif stage == "beats":
            versions = [
                {
                    "resource_id": str(item["id"]),
                    "label": f"第 {item.get('episode_number')} 集 Beats v{item.get('version') or 1}",
                    "updated_at": updated_at(item),
                    "version": int(item.get("version") or 1),
                }
                for item in project["episodes"]
                if item.get("stage") == "beats"
            ]
            resource_type = "beats"
        elif stage == "script":
            versions = [
                {"resource_id": str(item["id"]), "label": f"第 {item.get('episode_number')} 集 场次剧本 v{item.get('version') or 1}", "updated_at": updated_at(item), "version": int(item.get("version") or 1)}
                for item in project["episodes"] if item.get("stage") == "script"
            ]
            resource_type = "script"
        else:
            raise HTTPException(400, "stage 必须是 brief、characters、outline、beats 或 script")
        return resource_type, sorted(
            versions,
            key=lambda item: (item["updated_at"], item["version"], item["resource_id"]),
            reverse=True,
        )

    def generated_stage_snapshot(
        project: dict[str, Any], stage: str, resource_id: str | None
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        resource_type, versions = generated_stage_versions(project, stage)
        if not versions:
            raise HTTPException(404, "该项目尚未生成此阶段内容")
        selected_resource_id = resource_id or versions[0]["resource_id"]
        if selected_resource_id not in {item["resource_id"] for item in versions}:
            raise HTTPException(404, "请求的历史版本不属于该项目或阶段")
        try:
            snapshot = workflow_adapter.snapshot(resource_type, selected_resource_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, "已生成内容不存在") from exc
        if int(snapshot.get("project_id") or 0) != int(project["id"]):
            raise HTTPException(404, "请求的历史版本不属于该项目")
        return versions, selected_resource_id, snapshot

    def serialize_generated_stage(
        project: dict[str, Any], stage: str, snapshot: dict[str, Any], format: str
    ) -> tuple[bytes, str]:
        if format not in {"md", "json", "txt"}:
            raise HTTPException(400, "format 必须是 md、txt 或 json")
        content: Any
        if stage in {"beats", "script"}:
            content = [snapshot]
        elif stage == "characters":
            content = {
                "batch_id": snapshot.get("batch_id"),
                "characters": snapshot.get("characters") or [],
                "relationships": snapshot.get("relationships") or [],
            }
        elif stage == "outline":
            content = snapshot.get("episodes") or []
        else:
            content = snapshot
        if stage == "characters":
            generation_items = snapshot.get("characters") or []
            generation_meta = (
                generation_items[0].get("generation_meta")
                if generation_items
                else {}
            )
        elif stage == "outline":
            generation_meta = (
                content[0].get("generation_meta") if content else {}
            )
        else:
            generation_meta = snapshot.get("generation_meta") or {}
        payload = {
            "project_id": project["id"],
            "project_name": project["name"],
            "stage": stage,
            "generation_meta": generation_meta,
            "content": content,
        }
        if format == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"
        if stage == "script":
            script = build_script_text(project, snapshot.get("content") or {}, episode_number=int(snapshot.get("episode_number") or 0))
            model_label = _generation_model_label(generation_meta)
            if model_label:
                script = f"生成模型:{model_label}\n\n{script}"
            return script.encode("utf-8"), "text/plain; charset=utf-8"
        if stage == "beats":
            markdown = build_beats_markdown(project, content)
        elif stage == "outline":
            markdown = build_outline_markdown(project, content)
        else:
            markdown = "\n".join(
                [f"# {project['name']}", "", f"- 阶段：{stage}", ""]
                + _generation_info_markdown(generation_meta)
                + _markdown_value(content)
            )
        return markdown.encode("utf-8"), "text/markdown; charset=utf-8"

    def project_view(
        project: dict[str, Any],
        principal: Principal,
        *,
        managed_preview: bool = False,
    ) -> dict[str, Any]:
        project = project_prompt_service.public_projection(project, principal.user_id, project["id"])
        if principal.is_system_admin:
            return project
        has_active_membership = any(
            member["user_id"] == principal.user_id
            and member["status"] == "active"
            for member in workflow_repository.list_project_members(
                workflow_project_id(project["id"])
            )
        )
        read_only_portfolio_preview = not has_active_membership and (
            managed_preview
            or "project.read.managed" in principal.permissions
            or "project.read.established" in principal.permissions
        )
        if read_only_portfolio_preview:
            viewed = dict(project)
            viewed["allowed_actions"] = []
            viewed["preview_only"] = True
            return viewed
        if has_active_membership:
            return project
        approved_statuses = {"approved", "locked", "brief_locked"}
        stage_permissions = {
            "blueprints": (("brief.read", "brief.read.approved"), "brief.read"),
            "characters": (("character.read", "character.read.approved"), "character.read"),
            "relationships": (("relationship.read", "relationship.read.approved"), "relationship.read"),
            "outline": (("outline.read", "outline.read.approved"), "outline.read"),
            "beats": (("beats.read",), "beats.read"),
            "script": (("script.read",), "script.read"),
        }

        def resource_identity(key: str, item: dict[str, Any]) -> tuple[str, str]:
            if key == "blueprints":
                return "brief", str(item["id"])
            if key in {"characters", "relationships"}:
                return "characters", f"{project['id']}:{item['batch_id']}"
            if item["stage"] == "outline":
                return "outline", f"{project['id']}:{item['batch_id']}"
            return str(item["stage"]), str(item["id"])

        def visible(key: str, item: dict[str, Any]) -> bool:
            stage_key = item.get("stage") if key == "episodes" else key
            approved_permissions, draft_permission = stage_permissions[stage_key]
            if item.get("status") in approved_statuses:
                return bool(set(approved_permissions).intersection(principal.permissions))
            if draft_permission not in principal.permissions:
                return False
            task_id = str(item.get("workflow_task_id") or "")
            task = workflow_repository.get_task(task_id) if task_id else None
            if task and task.get("assignee_user_id") == principal.user_id:
                return True
            resource_type, resource_id = resource_identity(key, item)
            submission = workflow_repository.get_latest_resource_submission(
                resource_type, resource_id
            )
            return bool(submission and submission.get("created_by") == principal.user_id)

        viewed = dict(project)
        for key in ("blueprints", "characters", "relationships", "episodes"):
            viewed[key] = [item for item in project.get(key, []) if visible(key, item)]
        viewed["allowed_actions"] = []
        return viewed

    def create_generation_job(
        project_id: int,
        action: str,
        principal: Principal | None,
        request: Request,
        **extra: Any,
    ) -> dict[str, Any]:
        project = project_or_404(project_id)
        if project.get("workflow_mode", "sequential") != "sequential":
            raise HTTPException(409, "自由创作项目必须使用自由创作接口")
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        required_permission = (
            "job.create.writing"
            if action in {"outlines", "outline_overview", "beats", "script"}
            else "job.create.creative"
        )
        if (
            principal is not None
            and not principal.is_system_admin
            and "project_owner" not in principal.role_codes
            and not workflow_repository.is_active_project_member(
                workflow_project_id(project_id), principal
            )
            and required_permission not in principal.permissions
        ):
            raise HTTPException(403, "没有创建此机器任务的权限")
        assert_project_generation_access(project_id, principal)
        workflow_task_id = str(extra.pop("workflow_task_id", "") or "") or None
        reasoning_effort = str(extra.pop("reasoning_effort", "") or "").strip()
        llm_model_record_id = validate_llm_model(
            extra.pop("llm_model_record_id", None), reasoning_effort
        )
        normalized_stage = {
            "blueprints": "brief",
            "blueprint_variant": "brief",
            "characters": "characters",
            "outlines": "outline",
            "outline_overview": "outline",
            "beats": "beats",
            "script": "script",
        }[action]
        operation_id = str(extra.pop("operation_id", "") or uuid.uuid4().hex)
        generation_instruction = str(
            extra.pop("generation_instruction", "") or ""
        ).strip()
        prompt_template_key = str(
            extra.pop("prompt_template_key", "") or ""
        ).strip() or None
        prompt_template_revision = extra.pop("prompt_template_revision", None)
        prompt_override = dict(extra.pop("prompt_override", {}) or {})
        prompt_mode = str(extra.pop("prompt_mode", "") or "").strip() or None
        if (
            prompt_mode is None
            and normalized_stage == "brief"
            and int(extra.get("count") or 1) > 1
        ):
            prompt_mode = "series"
        source_snapshot = extra.pop("source_snapshot", None)
        source_snapshot_id = str(extra.pop("source_snapshot_id", "") or "").strip()
        if source_snapshot is None:
            source_snapshot_id = source_snapshot_id or (
                f"project-{project_id}-r{int(project.get('revision') or 0)}-"
                f"{normalized_stage}-{operation_id}"
            )
            source_snapshot = {
                "snapshot_id": source_snapshot_id,
                "stage": normalized_stage,
                "project": project,
            }
        else:
            embedded_id = source_snapshot.get("snapshot_id") or source_snapshot.get("id")
            source_snapshot_id = source_snapshot_id or str(embedded_id or "").strip()
            if not source_snapshot_id:
                source_snapshot_id = f"creative-source-{operation_id}"
        source_snapshot = dict(source_snapshot)
        source_snapshot.pop("prompt_snapshot", None)
        try:
            prompt_snapshot = (
                project_prompt_service.resolve_generation_snapshot(
                    project_id=project_id,
                    stage=normalized_stage,
                    series=bool(extra.get("series")),
                    mode=prompt_mode,
                    episode_number=extra.get("episode_number"),
                    template_key=prompt_template_key,
                    template_revision=(
                        int(prompt_template_revision)
                        if prompt_template_revision is not None
                        else None
                    ),
                    run_override=prompt_override,
                    actor_user_id=principal.user_id if principal is not None else "",
                )
                if normalized_stage in {"brief", "characters", "outline", "beats", "script"}
                else None
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if prompt_snapshot is not None:
            source_snapshot["prompt_snapshot"] = prompt_snapshot
        job_observability = prompt_job_identity(prompt_snapshot)
        try:
            state = application_service.start_generation_compat(
                project_id=project_id,
                stage=normalized_stage,
                action=action,
                actor=principal,
                workflow_task_id=workflow_task_id,
                operation_id=operation_id,
                generation_instruction=generation_instruction,
                source_snapshot_id=source_snapshot_id,
                source_snapshot=source_snapshot,
                llm_model_record_id=llm_model_record_id,
                reasoning_effort=reasoning_effort,
                **job_observability,
                **extra,
            )
        except JobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        if workflow_task_id:
            workflow_repository.link_task_job(workflow_task_id, state.job_id)
        job = state.to_dict()
        job.update(
            {
                "job_id": state.job_id,
                "platform": state.platform,
                "created_by_user_id": state.job.extra.get("created_by_user_id"),
                "workflow_task_id": state.job.extra.get("workflow_task_id"),
                "project_id": state.job.extra.get("project_id"),
            }
        )
        audit_service = getattr(request.app.state, "audit_service", None)
        if principal is not None and isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="job.create",
                target_type="job",
                target_id=state.job_id,
                project_id=str(project_id),
                before=None,
                after=job,
                reason=None,
                result="success",
                request=request,
            )
        return {"status": "success", "job": job}

    def generation_request_extra(payload: Any | None) -> dict[str, Any]:
        if payload is None:
            return {}
        return {
            "operation_id": getattr(payload, "operation_id", None),
            "generation_instruction": getattr(
                payload, "generation_instruction", ""
            ),
            "source_snapshot_id": getattr(payload, "source_snapshot_id", None),
            "source_snapshot": getattr(payload, "source_snapshot", None),
            "llm_model_record_id": getattr(payload, "llm_model_record_id", None),
            "reasoning_effort": getattr(payload, "reasoning_effort", None),
            "prompt_template_key": getattr(payload, "prompt_template_key", None),
            "prompt_template_revision": getattr(
                payload, "prompt_template_revision", None
            ),
            "prompt_override": getattr(payload, "prompt_override", {}),
        }

    def can_manage_prompt_project(
        request: Request, project_id: int, principal: Principal
    ) -> bool:
        if principal.is_system_admin:
            return True
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if isinstance(permission_service, PermissionService) and (
            permission_service.can_access_managed_project(
                principal, workflow_project_id(project_id)
            )
        ):
            return True
        return False

    def assert_prompt_project_manager(
        request: Request, project_id: int, principal: Principal
    ) -> None:
        if can_manage_prompt_project(request, project_id, principal):
            return
        raise HTTPException(403, "只有项目负责人可以管理项目模板")

    def assert_prompt_project_read_access(
        request: Request, project_id: int, principal: Principal
    ) -> None:
        if can_manage_prompt_project(request, project_id, principal):
            return
        if workflow_repository.is_active_project_member(
            workflow_project_id(project_id), principal
        ):
            return
        raise HTTPException(403, "只能访问当前参与或管理项目的模板")

    def assert_prompt_stage_manager(
        request: Request, project_id: int, stage: str, principal: Principal
    ) -> None:
        if principal.is_system_admin:
            return
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if isinstance(permission_service, PermissionService) and (
            permission_service.can_access_managed_project(
                principal, workflow_project_id(project_id)
            )
        ):
            return
        permission = {
            "brief": "brief.edit",
            "characters": "character.edit",
            "outline": "outline.edit",
            "beats": "beats.edit",
            "script": "script.edit",
            "review": "script.review.generate",
            "optimization": "script.optimize.generate",
        }.get(stage)
        if (
            permission is not None
            and permission in principal.permissions
            and workflow_repository.is_active_project_member(
                workflow_project_id(project_id), principal
            )
        ):
            return
        raise HTTPException(403, "只有项目负责人或当前阶段负责人可以修改模板")

    router.route_class = prompt_projection_route(project_prompt_service, optional_principal)

    @router.get("/projects/{project_id}/prompt-marketplace")
    async def get_project_prompt_marketplace(
        project_id: int,
        request: Request,
        q: str = Query("", max_length=120),
        stage: str | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            result = prompt_marketplace_service.marketplace(
                project_id=project_id,
                actor_user_id=principal.user_id,
                query=q,
                stage=stage,
                is_admin=principal.is_system_admin,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result["can_install"] = can_manage_prompt_project(
            request, project_id, principal
        )
        return result

    @router.post(
        "/projects/{project_id}/prompt-library/templates/{template_id}/marketplace-submissions"
    )
    async def submit_project_prompt_marketplace_template(
        project_id: int,
        template_id: str,
        payload: PromptMarketplaceSubmissionRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return prompt_marketplace_service.submit(
                project_id=project_id,
                template_id=template_id,
                expected_template_revision=payload.expected_template_revision,
                operation_id=payload.operation_id,
                title=payload.title,
                description=payload.description,
                tags=payload.tags,
                attribution_name=payload.attribution_name,
                source_type=payload.source_type,
                source_url=payload.source_url,
                rights_statement=payload.rights_statement,
                publication_confirmed=payload.publication_confirmed,
                rights_confirmed=payload.rights_confirmed,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/prompt-marketplace/submissions/{submission_id}/review"
    )
    async def review_project_prompt_marketplace_submission(
        project_id: int,
        submission_id: str,
        payload: PromptMarketplaceReviewRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return prompt_marketplace_service.moderate(
                submission_id=submission_id,
                action=payload.action,
                note=payload.note,
                expected_review_revision=payload.expected_review_revision,
                operation_id=payload.operation_id,
                actor_user_id=principal.user_id,
                is_admin=principal.is_system_admin,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/prompt-marketplace/listings/{submission_id}/install"
    )
    async def install_project_prompt_marketplace_listing(
        project_id: int,
        submission_id: str,
        payload: PromptMarketplaceInstallRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        assert_prompt_project_manager(request, project_id, principal)
        try:
            return prompt_marketplace_service.install(
                project_id=project_id,
                submission_id=submission_id,
                operation_id=payload.operation_id,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put(
        "/projects/{project_id}/prompt-marketplace/listings/{submission_id}/rating"
    )
    async def rate_project_prompt_marketplace_listing(
        project_id: int,
        submission_id: str,
        payload: PromptMarketplaceRatingRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return prompt_marketplace_service.rate(
                project_id=project_id,
                submission_id=submission_id,
                score=payload.score,
                review_text=payload.review_text,
                expected_revision=payload.expected_revision,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects/{project_id}/prompt-library")
    async def get_project_prompt_library(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        return project_prompt_service.catalog(project_id, principal.user_id)

    @router.get("/projects/{project_id}/prompt-library/recommendations")
    async def get_project_prompt_recommendations(
        project_id: int,
        request: Request,
        stage: str | None = Query(None),
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return project_prompt_service.recommendations(
                project_id,
                principal.user_id,
                stage=stage,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/projects/{project_id}/prompt-library/observability")
    async def get_project_prompt_observability(
        project_id: int,
        request: Request,
        stage: str | None = Query(None),
        template_key: str | None = Query(None, max_length=160),
        limit: int = Query(200, ge=1, le=1000),
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        if prompt_observability_service is None:
            raise HTTPException(503, "模板可观测性任务服务不可用")
        try:
            return prompt_observability_service.report(
                project_id=project_id,
                actor_user_id=principal.user_id,
                stage=stage,
                template_key=template_key,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/projects/{project_id}/prompt-library/import-preview")
    async def preview_project_prompt_import(
        project_id: int,
        payload: PromptImportPreviewRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        if llm_provider_factory is None:
            raise HTTPException(503, "LLM provider is not configured")
        try:
            provider = llm_provider_factory()
        except Exception as exc:
            raise HTTPException(503, "LLM provider is not configured") from exc
        try:
            return await project_prompt_service.import_preview(
                project_id,
                provider=provider,
                source_text=payload.source_text,
                target_stage=payload.target_stage,
            )
        except ValueError as exc:
            raise HTTPException(
                422, f"导入分析结果不符合模板契约：{exc}"
            ) from exc
        except Exception as exc:
            logger.exception("Prompt template import preview failed")
            raise HTTPException(502, "外部提示词分析失败") from exc

    @router.post("/projects/{project_id}/prompt-system/enable")
    async def enable_project_prompt_library(
        project_id: int,
        payload: PromptProjectEnableRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_manager(request, project_id, principal)
        try:
            return project_prompt_service.enable_existing_project(
                project_id,
                principal.user_id,
                template_key=payload.template_key,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/prompt-library/templates")
    async def create_project_prompt_template(
        project_id: int,
        payload: PromptTemplateCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        if payload.owner_scope == "project":
            assert_prompt_project_manager(request, project_id, principal)
        try:
            return project_prompt_service.create_template(
                project_id=project_id,
                actor_user_id=principal.user_id,
                title=payload.title,
                description=payload.description,
                owner_scope=payload.owner_scope,
                content=payload.content,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/prompt-library/templates/{template_id}/revisions")
    async def revise_project_prompt_template(
        project_id: int,
        template_id: str,
        payload: PromptTemplateRevisionRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        template = project_prompt_service.store.get_template(template_id)
        if template and template.get("owner_scope") == "project":
            assert_prompt_project_manager(request, project_id, principal)
        try:
            return project_prompt_service.save_template_revision(
                project_id=project_id,
                template_id=template_id,
                actor_user_id=principal.user_id,
                expected_revision=payload.expected_revision,
                content=payload.content,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/projects/{project_id}/prompt-library/templates/{template_id}/deletion-impact"
    )
    async def get_personal_prompt_template_deletion_impact(
        project_id: int,
        template_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            impact = project_prompt_service.personal_template_deletion_impact(
                project_id=project_id,
                template_id=template_id,
                actor_user_id=principal.user_id,
            )
            impact["can_manage_current_project"] = can_manage_prompt_project(
                request, project_id, principal
            )
            return impact
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete(
        "/projects/{project_id}/prompt-library/templates/{template_id}"
    )
    async def delete_personal_prompt_template(
        project_id: int,
        template_id: str,
        payload: PromptTemplateDeleteRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return project_prompt_service.delete_personal_template(
                project_id=project_id,
                template_id=template_id,
                actor_user_id=principal.user_id,
                expected_revision=payload.expected_revision,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/projects/{project_id}/prompt-library/templates/{template_id}/rebind-and-delete"
    )
    async def rebind_and_delete_personal_prompt_template(
        project_id: int,
        template_id: str,
        payload: PromptTemplateRebindDeleteRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_manager(request, project_id, principal)
        try:
            return project_prompt_service.rebind_and_delete_personal_template(
                project_id=project_id,
                template_id=template_id,
                actor_user_id=principal.user_id,
                expected_revision=payload.expected_revision,
                replacement_template_id=payload.replacement_template_id,
                replacement_template_revision=payload.replacement_template_revision,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects/{project_id}/prompt-library/governance")
    async def get_project_prompt_governance(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        result = project_prompt_service.governance(project_id)
        result["can_manage"] = can_manage_prompt_project(
            request, project_id, principal
        )
        return result

    @router.post(
        "/projects/{project_id}/prompt-library/templates/{template_id}/governance"
    )
    async def govern_project_prompt_template(
        project_id: int,
        template_id: str,
        payload: PromptGovernanceActionRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        assert_prompt_project_manager(request, project_id, principal)
        try:
            return project_prompt_service.govern_template(
                project_id=project_id,
                template_id=template_id,
                action=payload.action,
                note=payload.note,
                expected_governance_revision=payload.expected_governance_revision,
                operation_id=payload.operation_id,
                actor_user_id=principal.user_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects/{project_id}/prompt-modules/{stage}")
    async def get_project_prompt_module(
        project_id: int,
        stage: str,
        request: Request,
        episode_number: int = Query(0, ge=0, le=100),
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_project_read_access(request, project_id, principal)
        try:
            return project_prompt_service.module_state(
                project_id,
                stage,
                episode_number=episode_number,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/projects/{project_id}/prompt-modules/{stage}")
    async def update_project_prompt_module(
        project_id: int,
        stage: str,
        payload: PromptBindingRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        assert_prompt_stage_manager(request, project_id, stage, principal)
        try:
            return project_prompt_service.bind(
                project_id=project_id,
                stage=stage,
                episode_number=payload.episode_number,
                template_key=payload.template_key,
                template_revision=payload.template_revision,
                override=payload.override,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/generation-jobs/{job_id}/retry")
    async def retry_project_generation_job(
        project_id: int,
        job_id: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        try:
            before = job_manager.get_job(job_id)
            job_project_id = int(
                before.get("project_id")
                or (before.get("job") or {}).get("extra", {}).get("project_id")
                or 0
            )
            if job_project_id != project_id:
                raise HTTPException(404, "创作任务不存在")
            state = application_service.retry_generation_job(
                job_state=before,
                actor=principal,
            )
        except JobNotFoundError as exc:
            raise HTTPException(404, "创作任务不存在") from exc
        except (CreativeProjectAccessDenied, CreativeTaskOccupied, PermissionError) as exc:
            raise HTTPException(403, str(exc)) from exc
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(404, "创作项目不存在") from exc
        except (CreativeContextConflict, JobConflictError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

        job = state.to_dict()
        job.update(
            {
                "job_id": state.job_id,
                "platform": state.platform,
                "created_by_user_id": state.job.extra.get("created_by_user_id"),
                "workflow_task_id": state.job.extra.get("workflow_task_id"),
                "project_id": state.job.extra.get("project_id"),
            }
        )
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="creative.generation.retry",
                target_type="job",
                target_id=state.job_id,
                project_id=str(project_id),
                before=before,
                after=job,
                reason=None,
                result="success",
                request=request,
            )
        return {"status": "success", "job": job}

    def validate_task(
        task_id: str | None,
        resource_type: str,
        principal: Principal | None,
        *,
        project_id: int | None = None,
        topic_id: int | None = None,
    ) -> str:
        if project_id is not None:
            sequential_project_or_409(project_id)
        if principal is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        if resource_type == "brief" and project_id is None and topic_id is not None:
            return str(task_id or "")
        resolved_task_id = task_id
        if not resolved_task_id:
            can_edit_project = bool(
                project_id is not None
                and workflow_repository.is_active_project_member(
                    workflow_project_id(project_id), principal
                )
            )
            candidates = [
                task
                for task in workflow_repository.list_tasks(principal)
                if task["task_type"] == resource_type
                and (
                    can_edit_project
                    or task.get("assignee_user_id") == principal.user_id
                )
                and task["status"] in {"assigned", "in_progress", "returned"}
                and (
                    project_id is None
                    or str(task.get("project_id") or "")
                    == workflow_project_id(project_id)
                )
                and (topic_id is None or task.get("topic_id") == topic_id)
                and (
                    project_id is not None
                    or topic_id is None
                    or task.get("workflow_id")
                    == f"topic:{workflow_identity.topic_scope(topic_id)}"
                )
            ]
            if len(candidates) != 1:
                raise HTTPException(
                    409,
                    "exactly one active assigned workflow task is required",
                )
            resolved_task_id = str(candidates[0]["id"])
        try:
            workflow_adapter.assert_task_for_creation(
                resolved_task_id,
                resource_type,
                principal,
                project_id=project_id,
                topic_id=topic_id,
            )
            if project_id is not None:
                repository.link_project_workflow_task(project_id, resolved_task_id)
        except KeyError as exc:
            raise HTTPException(404, "workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return resolved_task_id

    def resolve_task_for_creation(
        task_id: str | None,
        resource_type: str,
        principal: Principal,
        *,
        project_id: int,
    ) -> str:
        return validate_task(
            task_id,
            resource_type,
            principal,
            project_id=project_id,
        )

    def application_task_id(
        task_id: str | None,
        stage: str,
        project_id: int,
        principal: Principal,
    ) -> str:
        sequential_project_or_409(project_id)
        try:
            if task_id:
                application_service.validate_task_for_creation(
                    task_id, project_id, stage, principal
                )
                return task_id
            return str(
                application_service.ensure_stage_task(
                    project_id, stage, principal
                )["id"]
            )
        except KeyError as exc:
            raise HTTPException(404, "workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def persist_legacy_draft(**kwargs: Any) -> dict[str, Any]:
        sequential_project_or_409(int(kwargs["project_id"]))
        return application_service.persist_legacy_draft(**kwargs)

    def outline_field_context_or_http(episode_id: int) -> dict[str, Any]:
        try:
            return repository.get_outline_field_rewrite_context(episode_id)
        except ValueError as exc:
            detail = str(exc)
            status_code = 404 if detail in {"分集大纲不存在", "创作项目不存在"} else 409
            raise HTTPException(status_code, detail) from exc

    def batch_resource_for(table: str, item_id: int) -> str | None:
        if table not in {"creative_characters", "creative_relationships"}:
            raise ValueError("invalid creative table")
        with repository._connect() as connection:
            row = connection.execute(
                f"SELECT project_id, batch_id FROM {table} WHERE id = ?",
                (item_id,),
            ).fetchone()
        return f"{row['project_id']}:{row['batch_id']}" if row else None

    @router.post("/topics/generate")
    async def generate_topics(payload: TopicGenerateRequest):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(400, "platform must be dy or hg")
        try:
            rank_source = service.normalize_rank_source(
                payload.platform, payload.rank_source
            )
            candidates = service.generate(
                payload.platform,
                payload.date,
                max(payload.limit, 50),
                rank_source=rank_source,
            )
            seen_title_keys = repository.decided_title_keys()
            generated = []
            for candidate in candidates:
                title_key = repository._title_key(candidate.get("title"))
                if not title_key or title_key in seen_title_keys:
                    continue
                seen_title_keys.add(title_key)
                generated.append(candidate)
                if len(generated) >= payload.limit:
                    break
            if not generated:
                return {"status": "success", "items": []}
            repository.upsert_topics(generated)
            captured_date = str(generated[0].get("captured_at") or "")[:10]
            topics = repository.list_topics(
                platform=payload.platform,
                status="pending_review",
                rank_source=rank_source,
                captured_date=captured_date,
                limit=None,
            )
            return {
                "status": "success",
                "items": lifecycle_service.enrich_topics(topics[: payload.limit]),
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @router.post("/topics/adopt-from-rank", status_code=201)
    async def adopt_topic_from_rank(
        payload: TopicAdoptFromRankRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(400, "platform must be dy or hg")
        try:
            entry, response = service.find_rank_entry(payload.platform, payload.date, payload.record_id)
            topic = repository.adopt_rank_topic(
                service.rank_entry_to_topic(entry, response), principal.user_id
            )
        except TopicDecisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"收录接口错误：{exc}") from exc
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="selection.topic_adopt_from_rank",
                target_type="topic",
                target_id=str(topic["id"]),
                project_id=None,
                before=None,
                after=topic,
                reason=None,
                result="success",
                request=request,
            )
        return {"topic": lifecycle_service.enrich_topic(int(topic["id"]))}

    @router.get("/topics")
    async def list_topics(
        request: Request,
        principal: Principal = Depends(current_principal),
        platform: str = "",
        status: str = "",
        rank_source: str = "",
        category: str = "",
        min_score: float | None = Query(None, ge=0, le=100),
        max_score: float | None = Query(None, ge=0, le=100),
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        lifecycle_status: str = "",
        include_lifecycle: bool = True,
        all: bool = False,
        limit: int = Query(50, ge=1, le=500),
    ):
        if rank_source:
            try:
                rank_source = service.normalize_rank_source(platform, rank_source)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        repository_limit = None if all else (500 if lifecycle_status else limit)
        items = await asyncio.to_thread(
            topic_library_items,
            principal=principal,
            platform=platform,
            status=status,
            rank_source=rank_source,
            category=category,
            min_score=min_score,
            max_score=max_score,
            captured_date=captured_date,
            has_locked_breakdown=has_locked_breakdown,
            lifecycle_status=lifecycle_status,
            include_lifecycle=include_lifecycle,
            limit=repository_limit,
        )
        await asyncio.to_thread(add_topic_project_access, request, principal, items)
        return {"items": items if all else items[:limit]}

    @router.get("/topics/export")
    async def export_topics(
        principal: Principal = Depends(current_principal),
        format: str = "excel",
        platform: str = "",
        status: str = "",
        category: str = "",
        min_score: float | None = Query(None, ge=0, le=100),
        max_score: float | None = Query(None, ge=0, le=100),
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        lifecycle_status: str = "",
    ):
        export_format = format.lower()
        if export_format not in {"csv", "excel", "xlsx"}:
            raise HTTPException(400, "format must be csv or excel")
        items = topic_library_items(
            principal=principal,
            platform=platform,
            status=status,
            category=category,
            min_score=min_score,
            max_score=max_score,
            captured_date=captured_date,
            has_locked_breakdown=has_locked_breakdown,
            lifecycle_status=lifecycle_status,
            limit=None,
        )
        headers = ["选题", "简介", "来源", "榜单", "题材", "推荐分", "状态", "拆解版本"]
        rows = [topic_export_row(item) for item in items]

        if export_format == "csv":
            content = StringIO(newline="")
            writer = csv.writer(content)
            writer.writerow(headers)
            writer.writerows(rows)
            return StreamingResponse(
                iter([content.getvalue().encode("utf-8-sig")]),
                media_type="text/csv",
                headers={
                    "Content-Disposition": (
                        f"attachment; filename*=UTF-8''{quote('选题库导出.csv')}"
                    )
                },
            )

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "选题库"
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        worksheet.freeze_panes = "A2"
        for column, width in zip("ABCDEFGH", (28, 48, 18, 16, 16, 12, 24, 24)):
            worksheet.column_dimensions[column].width = width
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return StreamingResponse(
            output,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote('选题库导出.xlsx')}"
                )
            },
        )

    @router.get("/topics/{topic_id}")
    async def get_topic(
        topic_id: int,
        principal: Principal = Depends(current_principal),
    ):
        topic = lifecycle_service.enrich_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        if topic.get("latest_breakdown_id"):
            workflow_adapter.reconcile_resource(
                "breakdown", str(topic["latest_breakdown_id"])
            )
            topic = lifecycle_service.enrich_topic(topic_id)
            assert topic is not None
        if (
            not principal.is_system_admin
            and "topic.read" not in principal.permissions
            and not topic.get("locked_breakdown_id")
        ):
            raise HTTPException(403, "只能读取已完成的选题上下文")
        return topic

    @router.post("/topics/{topic_id}/planning-assignment")
    async def create_planning_assignment(
        topic_id: int,
        payload: PlanningAssignmentRequest,
        request: Request,
        principal: Principal = Depends(require_permission("project.create")),
    ):
        topic = repository.get_topic(topic_id)
        if topic is None:
            raise HTTPException(404, "选题不存在")
        if topic.get("status") != "adopted":
            raise HTTPException(409, "只有已采纳选题可以创建项目及任务")
        breakdown = repository.get_latest_locked_breakdown(topic_id)
        if breakdown is None:
            raise HTTPException(409, "请先完成并锁定剧目拆解")

        existing = repository.get_project_by_creation_request_id(
            principal.user_id, payload.creation_request_id
        )
        if existing is not None and (
            int(existing["topic_id"]) != topic_id
            or int(existing["breakdown_id"]) != int(breakdown["id"])
        ):
            raise HTTPException(409, "该创建请求已用于其他项目")
        if (payload.planner_member_ids or payload.writer_member_ids) and (
            "project.member.manage" not in principal.permissions
        ):
            raise HTTPException(403, "没有管理项目成员的权限")
        try:
            planner_member_ids = _validate_members(
                principal, payload.planner_member_ids, "creative_planner"
            )
            writer_member_ids = _validate_members(
                principal, payload.writer_member_ids, "screenwriter"
            )
            created_project = existing is None
            project = existing or repository.create_project(
                topic_id,
                int(breakdown["id"]),
                payload.name.strip(),
                payload.target_episode_count,
                target_platform=payload.target_platform.strip(),
                creation_request_id=payload.creation_request_id,
                created_by=principal.user_id,
            )
            if created_project:
                project_prompt_service.enable_new_project(
                    int(project["id"]), principal.user_id
                )
            project_id = str(project["id"])
            scoped_project_id = workflow_project_id(project_id)
            workflow_repository.ensure_project_creator(
                scoped_project_id,
                principal.user_id,
                principal,
                project_uid=str(project.get("project_uid") or "") or None,
            )
            for member_id in planner_member_ids:
                workflow_repository.add_project_member(
                    scoped_project_id,
                    member_id,
                    "creative_planner",
                    principal,
                    project_uid=str(project.get("project_uid") or "") or None,
                )
            for member_id in writer_member_ids:
                workflow_repository.add_project_member(
                    scoped_project_id,
                    member_id,
                    "screenwriter",
                    principal,
                    project_uid=str(project.get("project_uid") or "") or None,
                )
            tasks = _ensure_stage_tasks(
                project_id, topic_id, principal
            )
            project = repository.get_project(int(project_id)) or project
        except KeyError as exc:
            raise HTTPException(404, "创作项目或任务不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="project.create",
                target_type="project",
                target_id=project_id,
                project_id=str(project_id),
                before=None,
                after={
                    "topic_id": topic_id,
                    "breakdown_id": breakdown["id"],
                    "task_ids": [task["id"] for task in tasks],
                    "planner_member_ids": payload.planner_member_ids,
                    "writer_member_ids": payload.writer_member_ids,
                    "creation_request_id": payload.creation_request_id,
                },
                reason=None,
                result="success",
                request=request,
            )
        return {"project": project, "tasks": tasks}

    def _validate_members(
        principal: Principal,
        member_ids: list[str],
        role_code: str,
    ) -> list[str]:
        """Validate selected ids against the exact list shown by the candidates API."""
        selected = list(
            dict.fromkeys(
                str(raw).strip() for raw in (member_ids or []) if str(raw).strip()
            )
        )
        if not selected:
            return []
        task_type_by_role = {
            "creative_planner": "brief",
            "screenwriter": "outline",
        }
        task_type = task_type_by_role.get(role_code)
        if task_type is None:
            raise ValueError("unsupported project member role")
        candidates = workflow_repository.list_role_assignment_candidates(
            role_code,
            task_type,
            principal,
            required_permission="project.member.manage",
        )
        candidate_ids = {item["user_id"] for item in candidates}
        if any(user_id not in candidate_ids for user_id in selected):
            raise ValueError(f"所选账号不是可用的{role_code}成员")
        return selected

    def _ensure_stage_tasks(
        project_id: str,
        topic_id: int | None,
        principal: Principal,
    ) -> list[dict[str, Any]]:
        """Create all creative stage workflow tasks in the project task pool."""
        project = repository.get_project(int(project_id)) or {}
        project_uid = str(project.get("project_uid") or "") or None
        stage_roles = [
            ("brief", "creative_planner"),
            ("characters", "creative_planner"),
            ("outline", "screenwriter"),
            ("beats", "screenwriter"),
            ("script", "screenwriter"),
        ]
        tasks: list[dict[str, Any]] = []
        for task_type, role_code in stage_roles:
            task = workflow_repository.ensure_project_task(
                workflow_project_id(project_id),
                topic_id,
                task_type=task_type,
                role_code=role_code,
                actor=principal,
                project_uid=project_uid,
            )
            if task_type == "brief":
                repository.reconcile_project_workflow_task(
                    task["id"], int(project_id)
                )
            tasks.append(task)
        return tasks

    def can_read_market_reference(
        request: Request, principal: Principal, resource_type: str, resource_id: int
    ) -> bool:
        del resource_id
        if principal.is_system_admin or has_creative_page_api_access(request, principal):
            return True
        if resource_type == "topic":
            return bool(
                {"topic.read", "breakdown.read.approved", "brief.read.approved"}
                & principal.permissions
            )
        return bool(
            {"breakdown.read", "breakdown.read.approved"} & principal.permissions
        )

    @router.get("/projects/member-candidates")
    async def list_project_member_candidates(
        role_code: str,
        principal: Principal = Depends(require_permission("project.member.manage")),
    ) -> list[dict[str, Any]]:
        task_type_by_role = {
            "creative_planner": "brief",
            "screenwriter": "outline",
        }
        task_type = task_type_by_role.get(role_code)
        if task_type is None:
            raise HTTPException(400, "unsupported project member role")
        try:
            candidates = workflow_repository.list_role_assignment_candidates(
                role_code,
                task_type,
                principal,
                required_permission="project.member.manage",
            )
            return [
                {
                    **candidate,
                    "is_current_user": (
                        str(candidate.get("user_id") or "") == principal.user_id
                    ),
                }
                for candidate in candidates
            ]
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def topic_transcript_bundle_or_404(topic_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        topic = repository.get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        bundle = find_transcript_bundle(workspace_root, topic["title"], topic.get("platform") or "")
        episodes = bundle.get("episodes") or []
        if not episodes:
            raise HTTPException(404, "该剧目暂无可预览的转写")
        return topic, episodes

    @router.get("/topics/{topic_id}/transcripts")
    async def preview_topic_transcripts(
        topic_id: int,
        _: Principal = Depends(require_permission("drama.transcript.read")),
    ):
        topic, episodes = topic_transcript_bundle_or_404(topic_id)
        return {
            "topic_id": topic_id,
            "title": topic["title"],
            "episode_count": len(episodes),
            "episodes": [
                {
                    "episode_number": int(item["episode_number"]),
                    "text": str(item.get("text") or ""),
                    "character_count": len(str(item.get("text") or "")),
                    "transcript_source": str(item.get("transcript_source") or "transcript"),
                    "modified_at": float(item.get("modified_at") or 0),
                }
                for item in episodes
            ],
        }

    @router.get("/topics/{topic_id}/transcripts/download")
    async def download_topic_transcripts(
        topic_id: int,
        _: Principal = Depends(require_permission("drama.transcript.read")),
    ):
        topic, episodes = topic_transcript_bundle_or_404(topic_id)
        body = render_transcript_bundle_text(topic["title"], episodes)
        return Response(
            content=body.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename*=UTF-8\'\'{quote(transcript_bundle_filename(topic["title"], episodes))}'
                )
            },
        )

    @router.get("/topics/{topic_id}/breakdowns")
    async def list_topic_breakdowns(
        topic_id: int,
        status: str = "locked",
        principal: Principal = Depends(current_principal),
    ):
        topic = repository.get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        if status != "locked":
            raise HTTPException(400, "only locked breakdown versions are available for project creation")
        items = repository.list_locked_breakdowns(topic_id)
        for item in items:
            workflow_adapter.reconcile_resource("breakdown", str(item["id"]))
        items = repository.list_locked_breakdowns(topic_id)
        if not principal.is_system_admin and "breakdown.read" not in principal.permissions:
            items = [item for item in items if item["status"] in {"approved", "locked"}]
        for item in items:
            item["field_guide"] = personalize_breakdown_field_guide(item["content"])
        return {"items": items}

    @router.patch("/topics/{topic_id}")
    async def update_topic(
        topic_id: int,
        payload: TopicUpdateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        try:
            before = repository.get_topic(topic_id)
            topic = repository.decide_topic(
                topic_id, payload.status, payload.decision_note, principal.user_id
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not topic:
            raise HTTPException(404, "topic not found")
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="selection.topic_decision",
                target_type="topic",
                target_id=str(topic_id),
                project_id=None,
                before=before,
                after=topic,
                reason=payload.decision_note or None,
                result="success",
                request=request,
            )
        return lifecycle_service.enrich_topic(topic_id)

    @router.delete("/topics/{topic_id}")
    async def delete_topic(
        topic_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        try:
            topic = repository.delete_topic(topic_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if topic is None:
            raise HTTPException(404, "topic not found")
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="selection.topic_delete",
                target_type="topic",
                target_id=str(topic_id),
                project_id=None,
                before=topic,
                after=None,
                reason=None,
                result="success",
                request=request,
            )
        return {"deleted": True, "id": topic_id}


    @router.post("/breakdowns")
    async def create_breakdown(
        payload: BreakdownCreateRequest,
        request: Request,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        if (
            principal is not None
            and not principal.is_system_admin
            and "job.create.selection" not in principal.permissions
        ):
            raise HTTPException(403, "没有创建此机器任务的权限")
        topic = repository.get_topic(payload.topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        workflow_task_id = None
        if payload.workflow_task_id:
            workflow_task_id = validate_task(
                payload.workflow_task_id,
                "breakdown",
                principal,
                topic_id=payload.topic_id,
            )
        if not payload.force_regenerate:
            existing = repository.get_latest_breakdown(payload.topic_id)
            if existing:
                existing["field_guide"] = personalize_breakdown_field_guide(existing["content"])
                return {"status": "existing", "breakdown": existing}
        bundle = find_transcript_bundle(workspace_root, topic["title"], topic.get("platform") or "")
        episodes = bundle.get("episodes") or []
        video_assets = find_downloaded_video_assets(workspace_root, topic["title"])
        if not episodes and not video_assets:
            raise HTTPException(409, detail={
                "code": "transcript_required",
                "message": "请先下载剧目脚本再拆解",
                "topic_id": payload.topic_id,
                "title": topic["title"],
            })
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        llm_model_record_id = validate_llm_model(
            payload.llm_model_record_id, payload.reasoning_effort
        )
        coverage = sorted({
            *[int(item["episode_number"]) for item in episodes],
            *[int(item["episode_number"]) for item in video_assets],
        })
        task = (
            workflow_repository.get_task(workflow_task_id)
        )
        project_id = task.get("project_id") if task else None
        job_payload = {
            "platform": "creative",
            "crawler_type": "breakdown_generate",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "topic_id": payload.topic_id,
                "runtime_dir": str(Path(runtime_dir).resolve()),
                "workspace_root": str(workspace_root),
                "transcript_coverage": coverage,
                "material_snapshot_id": (
                    repository.get_latest_selection_stage_snapshot(
                        payload.topic_id, "source_package"
                    ) or {}
                ).get("id"),
                "workflow_task_id": workflow_task_id,
                "created_by_user_id": (
                    principal.user_id if principal is not None else None
                ),
                "project_id": project_id,
                **(
                    {"llm_model_record_id": llm_model_record_id}
                    if llm_model_record_id
                    else {}
                ),
                **(
                    {"reasoning_effort": payload.reasoning_effort}
                    if payload.reasoning_effort
                    else {}
                ),
            },
        }
        try:
            state = job_manager.create_job(job_payload)
        except JobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        if workflow_task_id:
            workflow_repository.link_task_job(workflow_task_id, state.job_id)
        job = state.to_dict()
        job.update(
            {
                "job_id": state.job_id,
                "platform": state.platform,
                "created_by_user_id": state.job.extra.get("created_by_user_id"),
                "workflow_task_id": state.job.extra.get("workflow_task_id"),
                "project_id": state.job.extra.get("project_id"),
            }
        )
        audit_service = getattr(request.app.state, "audit_service", None)
        if principal is not None and isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="job.create",
                target_type="job",
                target_id=state.job_id,
                project_id=(str(project_id) if project_id is not None else None),
                before=None,
                after=job,
                reason=None,
                result="success",
                request=request,
            )
        return {
            "status": "success",
            "job": job,
            "coverage": coverage,
            "requires_transcription": bool(video_assets),
        }

    @router.get("/breakdowns/template")
    async def get_breakdown_template():
        return {"field_guide": breakdown_field_guide()}

    @router.get("/breakdowns/{breakdown_id}")
    async def get_breakdown(breakdown_id: int):
        workflow_adapter.reconcile_resource("breakdown", str(breakdown_id))
        breakdown = repository.get_breakdown(breakdown_id)
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.get("/breakdowns/{breakdown_id}/download")
    async def download_breakdown(breakdown_id: int):
        workflow_adapter.reconcile_resource("breakdown", str(breakdown_id))
        breakdown = repository.get_breakdown(breakdown_id)
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        return Response(
            content=build_breakdown_markdown(breakdown).encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="breakdown-{breakdown_id}.md"'
            },
        )

    @router.patch("/breakdowns/{breakdown_id}")
    async def update_breakdown(breakdown_id: int, payload: BreakdownUpdateRequest):
        try:
            workflow_adapter.assert_editable("breakdown", str(breakdown_id))
            breakdown = repository.update_breakdown(breakdown_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.post("/breakdowns/{breakdown_id}/lock")
    async def lock_breakdown(
        breakdown_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        before = repository.get_breakdown(breakdown_id)
        if before is None:
            raise HTTPException(404, "breakdown not found")
        breakdown = repository.lock_breakdown(breakdown_id)
        SelectionStageService(repository, workspace_root).record_breakdown_completion(
            breakdown_id, principal.user_id
        )
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="selection.breakdown_complete",
                target_type="breakdown",
                target_id=str(breakdown_id),
                project_id=None,
                before=before,
                after=breakdown,
                reason=None,
                result="success",
                request=request,
            )
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.post("/projects")
    async def create_project(
        payload: ProjectCreateRequest,
        request: Request,
        principal: Principal = Depends(require_permission("project.create")),
    ):
        try:
            workflow_task_id = payload.workflow_task_id or ""
            if payload.workflow_mode == "free" and workflow_task_id:
                raise HTTPException(409, "自由创作项目不能绑定标准工作流任务")
            breakdown = repository.get_breakdown(payload.breakdown_id)
            if breakdown is None or breakdown.get("status") not in {"locked", "approved"}:
                raise HTTPException(
                    409, "project creation requires a locked breakdown"
                )
            if int(breakdown["topic_id"]) != payload.topic_id:
                raise HTTPException(409, "breakdown belongs to another topic")
            task = None
            if workflow_task_id:
                try:
                    task = workflow_adapter.assert_task_for_creation(
                        workflow_task_id,
                        "brief",
                        principal,
                        topic_id=payload.topic_id,
                    )
                except KeyError as exc:
                    raise HTTPException(404, "workflow task not found") from exc
                except PermissionError as exc:
                    raise HTTPException(403, str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
            project = (
                repository.get_project_by_creation_workflow_task_id(workflow_task_id)
                if workflow_task_id else repository.get_project_by_creation_request_id(
                    principal.user_id, payload.creation_request_id
                )
            )
            bound_id = task.get("project_id") if task else None
            is_existing_project = project is not None or bound_id is not None
            if bound_id is not None:
                bound_project_id = repository.local_project_id_from_workflow(bound_id)
                if bound_project_id is None:
                    raise HTTPException(
                        409, "workflow task belongs to another creative store"
                    )
                if project is None or int(project["id"]) != bound_project_id:
                    try:
                        project = repository.reconcile_project_workflow_task(
                            workflow_task_id, bound_project_id
                        )
                    except ValueError as exc:
                        raise HTTPException(409, str(exc)) from exc
            if project is not None and (
                int(project["topic_id"]) != payload.topic_id
                or int(project["breakdown_id"]) != payload.breakdown_id
            ):
                raise HTTPException(
                    409, "workflow task is already linked to another project request"
                )
            if project is not None and str(
                project.get("workflow_mode") or "sequential"
            ) != payload.workflow_mode:
                raise HTTPException(409, "创建请求标识已用于其他工作流模式")
            if project is None and task is not None and task.get("project_id") is not None:
                raise HTTPException(
                    409, "brief workflow task is already linked to a project"
                )
            if project is None:
                project = repository.create_project(
                    payload.topic_id,
                    payload.breakdown_id,
                    payload.name,
                    payload.target_episode_count,
                    genre=payload.genre,
                    target_platform=payload.target_platform,
                    target_audience=payload.target_audience,
                    episode_duration_seconds=payload.episode_duration_seconds,
                    workflow_task_id=workflow_task_id or None,
                    creation_request_id=payload.creation_request_id,
                    created_by=principal.user_id,
                    workflow_mode=payload.workflow_mode,
                    seed_text=payload.seed_text,
                )
            resolved_project = repository.get_project(int(project["id"])) or project
            if not is_existing_project:
                project_prompt_service.enable_new_project(
                    int(resolved_project["id"]), principal.user_id
                )
            if resolved_project.get("created_by") == principal.user_id:
                workflow_repository.ensure_project_creator(
                    workflow_project_id(resolved_project["id"]),
                    principal.user_id,
                    principal,
                    project_uid=(
                        str(resolved_project.get("project_uid") or "") or None
                    ),
                )
            if payload.workflow_mode == "sequential" and workflow_task_id and bound_id is None:
                try:
                    workflow_repository.bind_workflow_project(
                        workflow_task_id,
                        workflow_project_id(resolved_project["id"]),
                        principal,
                        project_uid=(
                            str(resolved_project.get("project_uid") or "") or None
                        ),
                        expected_task_type="brief",
                        expected_topic_id=payload.topic_id,
                        require_creator_assignment=True,
                    )
                except PermissionError as exc:
                    raise HTTPException(403, str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(
                        503,
                        "project created but workflow binding requires reconciliation",
                    ) from exc
            if (
                payload.workflow_mode == "sequential"
                and resolved_project.get("created_by") == principal.user_id
            ):
                _ensure_stage_tasks(
                    str(resolved_project["id"]), payload.topic_id, principal
                )
            if is_existing_project:
                assert_project_read_access(
                    request, int(resolved_project["id"]), principal
                )
                return project_view(resolved_project, principal)
            return resolved_project
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/original")
    async def create_original_project(
        payload: OriginalProjectCreateRequest,
        request: Request,
        principal: Principal = Depends(require_permission("project.create")),
    ):
        try:
            project, created = application_service.create_original_project_result(
                payload.model_dump(exclude={"creation_request_id"}),
                principal,
                creation_request_id=payload.creation_request_id,
                can_read_reference=lambda kind, identifier: can_read_market_reference(
                    request, principal, kind, identifier
                ),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "原创项目初始化失败，请重试") from exc
        if created:
            project_prompt_service.enable_new_project(
                int(project["id"]), principal.user_id
            )
        return JSONResponse(project, status_code=201 if created else 200)

    @router.get("/projects/{project_id}/seeds")
    async def list_project_seeds(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        project = project_or_404(project_id)
        if project.get("creation_mode") != "original":
            raise HTTPException(409, "只有原创项目拥有原创种子")
        return {"items": repository.list_seeds(project_id)}

    @router.patch("/seeds/{seed_id}")
    async def update_original_seed(
        seed_id: int,
        payload: SeedUpdateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        seed = repository.get_seed(seed_id)
        if seed is None:
            raise HTTPException(404, "原创种子不存在")
        assert_project_generation_access(int(seed["project_id"]), principal)
        values = payload.model_dump(exclude_none=True)
        try:
            if "reference_refs" in values:
                bundle = market_reference_service.resolve(
                    [item.model_dump() for item in payload.reference_refs or []],
                    can_read=lambda kind, identifier: can_read_market_reference(
                        request, principal, kind, identifier
                    ),
                )
                values["reference_refs"] = bundle.source_refs
            updated = repository.update_seed(seed_id, values)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if updated is None:
            raise HTTPException(409, "已冻结的原创种子不能修改")
        return updated

    @router.post("/projects/{project_id}/seeds/versions")
    async def create_original_seed_version(
        project_id: int,
        payload: SeedVersionRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_generation_access(project_id, principal)
        project = project_or_404(project_id)
        if project.get("creation_mode") != "original":
            raise HTTPException(409, "只有原创项目可以创建种子版本")
        try:
            bundle = market_reference_service.resolve(
                [item.model_dump() for item in payload.reference_refs],
                can_read=lambda kind, identifier: can_read_market_reference(
                    request, principal, kind, identifier
                ),
            )
            seed = repository.create_seed_version(
                project_id,
                seed_text=payload.seed_text,
                constraints=payload.constraints,
                reference_refs=bundle.source_refs,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(seed, status_code=201)

    @router.get("/projects")
    async def list_projects(
        request: Request,
        topic_id: int | None = None,
        participating_only: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        items = await asyncio.to_thread(repository.list_projects, topic_id=topic_id)
        for item in items:
            task_id = str(item.get("creation_workflow_task_id") or "")
            if not task_id:
                item["workflow_link_status"] = "unbound"
                continue
            try:
                task = workflow_repository.get_task(task_id)
            except Exception as exc:
                logger.exception(
                    "Project workflow lookup failed: project_id=%s task_id=%s",
                    item["id"],
                    task_id,
                )
                raise HTTPException(
                    503, "项目工作流状态暂不可用，请稍后重试"
                ) from exc
            if task is None:
                logger.warning(
                    "Project creation workflow task missing; retaining source reference: "
                    "project_id=%s task_id=%s",
                    item["id"],
                    task_id,
                )
                item["workflow_link_status"] = "missing"
                item["workflow_task_missing"] = True
                continue
            expected_project_id = workflow_project_id(item["id"])
            item["workflow_link_status"] = (
                "linked"
                if task.get("task_type") == "brief"
                and task.get("project_id") == expected_project_id
                else "mismatched"
            )
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if participating_only:
            items = [
                item
                for item in items
                if isinstance(permission_service, PermissionService)
                and permission_service.can_participate_in_project(
                    principal, workflow_project_id(item["id"])
                )
            ]
        elif not principal.is_system_admin:
            items = [
                item
                for item in items
                if isinstance(permission_service, PermissionService)
                and (
                    workflow_repository.is_active_project_member(
                        workflow_project_id(item["id"]), principal
                    )
                    or permission_service.can_read_project(
                        principal, workflow_project_id(item["id"])
                    )
                )
            ]
        for item in items:
            item["can_delete"] = bool(
                isinstance(permission_service, PermissionService)
                and permission_service.can_delete_project(
                    principal, workflow_project_id(item["id"])
                )
            )
            item["can_production_export"] = bool(
                principal.is_system_admin
                or (
                    isinstance(permission_service, PermissionService)
                    and permission_service.can_access_managed_project(
                        principal, workflow_project_id(item["id"])
                    )
                )
            )
        return {"items": items}

    @router.get("/projects/{project_id}")
    async def get_project(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        return project_view(
            project_or_404(project_id),
            principal,
            managed_preview=has_managed_project_preview_access(request, principal),
        )

    def assert_mode_member(project: dict[str, Any], principal: Principal) -> None:
        if principal.is_system_admin or workflow_repository.is_active_project_member(
            workflow_project_id(project["id"]), principal
        ):
            return
        raise HTTPException(403, "只有项目组成员可以修改创作模式")

    def project_creative_tasks(project_id: int, principal: Principal) -> list[dict[str, Any]]:
        return [
            task
            for task in workflow_repository.list_tasks(principal)
            if str(task.get("project_id") or "") == workflow_project_id(project_id)
            and str(task.get("task_type") or "")
            in {"brief", "characters", "outline", "beats", "script", "delivery"}
        ]

    def cancel_pending_creative_tasks(project_id: int, principal: Principal) -> None:
        tasks = project_creative_tasks(project_id, principal)
        has_resources = free_mode_service.has_creative_resources(project_id)
        if any(
            task.get("status") == "returned"
            or (has_resources and task.get("status") == "in_progress")
            for task in tasks
        ):
            raise HTTPException(409, "项目存在进行中的创作或交付任务，暂不能切换模式")
        for task in tasks:
            if task.get("status") in {"pool", "assigned"} or (
                not has_resources and task.get("status") == "in_progress"
            ):
                workflow_repository.cancel_task_for_mode_change(
                    str(task["id"]), principal
                )

    @router.patch("/projects/{project_id}/workflow-mode")
    async def change_project_workflow_mode(
        project_id: int,
        payload: CreativeModeChangeRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        project = project_or_404(project_id)
        assert_mode_member(project, principal)
        if payload.workflow_mode == "free":
            active_jobs = active_project_job_labels(project_id)
            if active_jobs:
                raise HTTPException(409, "项目存在运行中的后台任务，暂不能切换模式")
            cancel_pending_creative_tasks(project_id, principal)
        try:
            changed = free_mode_service.switch_empty_mode(
                project_id=project_id,
                workflow_mode=payload.workflow_mode,
                expected_mode_revision=payload.expected_mode_revision,
                actor_user_id=principal.user_id,
                seed_text=payload.seed_text,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if payload.workflow_mode == "sequential":
            _ensure_stage_tasks(str(project_id), changed.get("topic_id"), principal)
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="creative.project.workflow_mode.change",
                target_type="creative_project",
                target_id=str(project_id),
                project_id=str(project_id),
                before={"workflow_mode": project.get("workflow_mode")},
                after={"workflow_mode": changed.get("workflow_mode")},
                reason=None,
                result="success",
                request=request,
            )
        return changed

    def active_project_job_labels(
        project_id: int,
        *,
        ignored_crawler_types: frozenset[str] | None = None,
    ) -> list[str]:
        if job_manager is None:
            return []
        labels: list[str] = []
        for item in job_manager.list_jobs():
            job = item.get("job") if isinstance(item.get("job"), dict) else item
            extra = job.get("extra") if isinstance(job.get("extra"), dict) else {}
            bound_project = extra.get("project_id") or item.get("project_id")
            status = str(item.get("status") or job.get("status") or "").lower()
            if str(bound_project or "") != str(project_id):
                continue
            if status in {"completed", "success", "succeeded", "failed", "cancelled", "stopped"}:
                continue
            crawler_type = str(job.get("crawler_type") or "job")
            if crawler_type in (ignored_crawler_types or ()):
                continue
            labels.append(crawler_type)
        return labels

    @router.get("/projects/{project_id}/mode-migration-preview")
    async def creative_mode_migration_preview(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        project = project_or_404(project_id)
        assert_mode_member(project, principal)
        try:
            preview, tasks, active_jobs = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(free_mode_service.migration_preview, project_id),
                    asyncio.to_thread(
                        project_creative_tasks, project_id, principal
                    ),
                    asyncio.to_thread(
                        active_project_job_labels,
                        project_id,
                        ignored_crawler_types=_MODE_MIGRATION_NON_BLOCKING_JOB_TYPES,
                    ),
                ),
                timeout=_MODE_MIGRATION_PREVIEW_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise HTTPException(503, "迁移预检超时，请稍后重试") from exc
        blockers = list(preview["blockers"])
        if active_jobs:
            blockers.append("项目存在运行中的后台任务")
        return {
            **preview,
            "blockers": blockers,
            "can_migrate": not blockers,
            "pending_task_count": sum(
                task.get("status")
                in {"pool", "assigned", "in_progress", "returned"}
                for task in tasks
            ),
            "active_jobs": active_jobs,
        }

    @router.post("/projects/{project_id}/migrate-to-free")
    async def migrate_project_to_free(
        project_id: int,
        payload: CreativeModeMigrationRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        project = project_or_404(project_id)
        assert_mode_member(project, principal)
        try:
            prior = free_mode_service.completed_migration(
                project_id, payload.operation_id
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if prior is not None:
            return prior

        def rollback_migration_members(compensation: dict[str, Any]) -> None:
            workflow_repository.rollback_copied_project_members(
                compensation["target_project_id"], compensation["membership_ids"]
            )

        try:
            free_mode_service.recover_migration_compensation(
                project_id, payload.operation_id, rollback_migration_members
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        active_jobs = active_project_job_labels(
            project_id,
            ignored_crawler_types=_MODE_MIGRATION_NON_BLOCKING_JOB_TYPES,
        )
        if active_jobs:
            raise HTTPException(409, "项目存在运行中的后台任务，暂不能迁移")
        migration_preview = free_mode_service.migration_preview(project_id)
        if migration_preview["blockers"]:
            raise HTTPException(409, "；".join(migration_preview["blockers"]))

        def copy_migration_members(target_project_id: int) -> dict[str, Any]:
            # The target is new and uncommitted; it cannot have a legacy identity.
            target_identity = repository.workflow_project_id(target_project_id)
            members = workflow_repository.copy_active_project_members(
                workflow_project_id(project_id), target_identity, principal
            )
            membership_ids = [str(member["id"]) for member in members]
            return {
                "target_project_id": target_identity,
                "membership_ids": membership_ids,
            }

        try:
            migrated = free_mode_service.migrate_to_free(
                project_id=project_id,
                expected_mode_revision=payload.expected_mode_revision,
                operation_id=payload.operation_id,
                actor_user_id=principal.user_id,
                seed_text=payload.seed_text,
                copy_members=copy_migration_members,
                rollback_members=rollback_migration_members,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        target_project = migrated.get("project") or {}
        target_project_id = target_project.get("id")
        if target_project_id is None:
            raise HTTPException(503, "自由创作副本成员同步失败，请重试")
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="creative.project.workflow_mode.migrate",
                target_type="creative_project",
                target_id=str(project_id),
                project_id=str(project_id),
                before={"workflow_mode": project.get("workflow_mode")},
                after={
                    "workflow_mode": project.get("workflow_mode"),
                    "free_copy_project_id": migrated.get("project", {}).get("id"),
                    "migration_operation_id": payload.operation_id,
                },
                reason=None,
                result="success",
                request=request,
            )
        return migrated

    @router.get("/projects/{project_id}/legacy-creative-resources")
    async def list_legacy_creative_resources(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        try:
            return free_mode_service.list_legacy_resources(project_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects/{project_id}/module-capabilities")
    async def creative_module_capabilities(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        project = project_or_404(project_id)
        capabilities: dict[str, Any] = {}
        for stage in ("brief", "characters", "outline", "beats", "script"):
            try:
                application_service.assert_stage_write_access(project_id, stage, principal)
                can_write = True
                reason = ""
            except PermissionError as exc:
                can_write = False
                reason = str(exc)
            capabilities[stage] = {
                "can_view": True,
                "can_edit": can_write,
                "can_create_blank": can_write and project.get("workflow_mode") == "free",
                "can_create_revision": can_write and project.get("workflow_mode") == "free",
                "can_generate": can_write and project.get("workflow_mode") == "free",
                "can_lock": can_write and project.get("workflow_mode") == "free",
                "reason": reason,
            }
        return {
            "project_id": project_id,
            "workflow_mode": project.get("workflow_mode", "sequential"),
            "capabilities": capabilities,
        }

    @router.get("/projects/{project_id}/free/context-options")
    async def free_context_options(
        project_id: int,
        request: Request,
        episode_number: int | None = Query(None, ge=1, le=100),
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        try:
            return free_mode_service.context_options(project_id, episode_number)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/briefs")
    async def create_free_brief(
        project_id: int,
        payload: FreeBriefCreateRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            application_service.assert_stage_write_access(project_id, "brief", principal)
            project = project_or_404(project_id)
            content = payload.content or {"title": project.get("name", ""), "logline": ""}
            return free_mode_service.create_brief(
                project_id,
                content,
                expected_head_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                generation_meta={"origin": "manual"},
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/characters")
    async def create_free_characters(
        project_id: int,
        payload: FreeCharacterBatchCreateRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            application_service.assert_stage_write_access(project_id, "characters", principal)
            return free_mode_service.create_characters(
                project_id,
                payload.characters,
                payload.relationships,
                expected_head_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                generation_meta={"origin": "manual"},
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/outlines")
    async def create_free_outline(
        project_id: int,
        payload: FreeOutlineCreateRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            application_service.assert_stage_write_access(project_id, "outline", principal)
            return free_mode_service.create_outline(
                project_id,
                payload.episodes,
                series_arc=payload.series_arc,
                series_overview=payload.series_overview,
                expected_head_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                generation_meta={"origin": "manual"},
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/episodes/{episode_number}/{stage}")
    async def create_free_episode_resource(
        project_id: int,
        episode_number: int,
        stage: str,
        payload: FreeEpisodeCreateRequest,
        principal: Principal = Depends(current_principal),
    ):
        normalized = {"beats": "beats", "scripts": "script"}.get(stage)
        if normalized is None:
            raise HTTPException(404, "unsupported creative stage")
        try:
            application_service.assert_stage_write_access(project_id, normalized, principal)
            default_content = (
                {"beats": [], "updates": []}
                if normalized == "beats"
                else {"episode_title": "", "scenes": []}
            )
            return free_mode_service.create_episode_resource(
                project_id,
                normalized,
                episode_number,
                payload.content or default_content,
                expected_head_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                generation_meta={"origin": "manual"},
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/projects/{project_id}/free/items/{item_type}/{item_id}")
    async def update_free_item(
        project_id: int,
        item_type: str,
        item_id: int,
        payload: CreativeItemUpdateRequest,
        principal: Principal = Depends(current_principal),
    ):
        stage = {
            "brief": "brief",
            "character": "characters",
            "relationship": "characters",
            "episode": "outline",
        }.get(item_type)
        if stage is None:
            raise HTTPException(404, "unsupported free creative item type")
        episode = repository.get_episode(item_id) if item_type == "episode" else None
        if episode and episode.get("stage") in {"beats", "script"}:
            stage = str(episode["stage"])
        try:
            application_service.assert_stage_write_access(project_id, stage, principal)
            return free_mode_service.update_item(
                project_id=project_id,
                item_type=item_type,
                item_id=item_id,
                content=payload.content,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/{stage}/{resource_ref}/lock")
    async def lock_free_resource(
        project_id: int,
        stage: str,
        resource_ref: str,
        payload: FreeCreativeLockRequest,
        episode_number: int | None = Query(None, ge=1, le=100),
        principal: Principal = Depends(current_principal),
    ):
        normalized = {"briefs": "brief", "characters": "characters", "outlines": "outline", "beats": "beats", "scripts": "script"}.get(stage)
        if normalized is None:
            raise HTTPException(404, "unsupported creative stage")
        try:
            application_service.assert_stage_write_access(project_id, normalized, principal)
            return free_mode_service.lock_resource(
                project_id=project_id,
                stage=normalized,
                resource_ref=resource_ref,
                expected_head_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                episode_number=episode_number,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/free/{stage}/lock-series")
    async def lock_free_episode_series(
        project_id: int,
        stage: str,
        payload: FreeCreativeSeriesLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        normalized = {"beats": "beats", "scripts": "script"}.get(stage)
        if normalized is None:
            raise HTTPException(404, "unsupported creative stage")
        try:
            application_service.assert_stage_write_access(
                project_id, normalized, principal
            )
            return free_mode_service.lock_episode_series(
                project_id=project_id,
                stage=normalized,
                expected_head_revisions=payload.expected_head_revisions,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put("/projects/{project_id}/free/{stage}/head")
    async def set_free_resource_head(
        project_id: int,
        stage: str,
        payload: FreeCreativeHeadRequest,
        episode_number: int | None = Query(None, ge=1, le=100),
        principal: Principal = Depends(current_principal),
    ):
        normalized = {"briefs": "brief", "characters": "characters", "outlines": "outline", "beats": "beats", "scripts": "script"}.get(stage)
        if normalized is None:
            raise HTTPException(404, "unsupported creative stage")
        try:
            application_service.assert_stage_write_access(project_id, normalized, principal)
            return free_mode_service.set_head(
                project_id=project_id,
                stage=normalized,
                resource_ref=payload.resource_ref,
                expected_revision=payload.expected_head_revision,
                actor_user_id=principal.user_id,
                episode_number=episode_number,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def validate_llm_model(
        model_record_id: str | None, reasoning_effort: str | None = None
    ) -> str:
        normalized = str(model_record_id or "").strip()
        requested_effort = str(reasoning_effort or "").strip().lower()
        if not normalized:
            if requested_effort:
                raise HTTPException(409, "选择推理档位时必须同时指定模型")
            return ""
        if llm_model_resolver is None:
            raise HTTPException(503, "模型配置服务不可用")
        try:
            resolved = llm_model_resolver(normalized)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if requested_effort:
            model_id = str(
                (resolved or {}).get("model")
                or (resolved or {}).get("model_id")
                or ""
            ).strip()
            if not model_id:
                raise HTTPException(409, "所选模型缺少能力信息，不能设置推理档位")
            supported = reasoning_efforts_for_model(model_id)
            if requested_effort not in supported:
                raise HTTPException(
                    409,
                    f"模型 {model_id} 不支持推理档位 {requested_effort}",
                )
        return normalized

    def prompt_job_identity(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or not snapshot:
            return {}
        return {
            "prompt_template_id": str(snapshot.get("template_id") or ""),
            "prompt_template_key": str(snapshot.get("template_key") or ""),
            "prompt_template_revision": int(snapshot.get("revision") or 0),
            "prompt_content_hash": str(snapshot.get("content_hash") or ""),
        }

    def start_free_generation_job(
        *,
        project_id: int,
        stage: str,
        episode_number: int | None,
        payload: FreeCreativeGenerateRequest,
        principal: Principal,
    ) -> dict[str, Any]:
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        llm_model_record_id = validate_llm_model(
            payload.llm_model_record_id, payload.reasoning_effort
        )
        try:
            application_service.assert_stage_write_access(project_id, stage, principal)
            prompt_snapshot = (
                project_prompt_service.resolve_generation_snapshot(
                    project_id=project_id,
                    stage=stage,
                    series=False,
                    mode=(
                        "series"
                        if stage == "outline"
                        or (stage == "brief" and int(payload.count) > 1)
                        else None
                    ),
                    episode_number=episode_number,
                    template_key=payload.prompt_template_key,
                    template_revision=payload.prompt_template_revision,
                    run_override=payload.prompt_override,
                    actor_user_id=principal.user_id,
                )
                if stage in {"brief", "characters", "outline", "beats", "script"}
                else None
            )
            context = free_mode_service.create_generation_context(
                context_id=payload.operation_id,
                project_id=project_id,
                stage=stage,
                episode_number=episode_number,
                instruction=payload.generation_instruction,
                context_refs=payload.context_refs.model_dump(),
                actor_user_id=principal.user_id,
                prompt_snapshot=prompt_snapshot,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        action = {
            "brief": "blueprints",
            "characters": "characters",
            "outline": "outlines",
            "beats": "beats",
            "script": "script",
        }[stage]
        request_hash = canonical_request_hash({
            "project_id": project_id,
            "stage": stage,
            "episode_number": episode_number,
            "operation_id": payload.operation_id,
            "context_hash": context["context_hash"],
            "expected_head_revision": payload.expected_head_revision,
            "count": payload.count if stage == "brief" else 1,
            "llm_model_record_id": llm_model_record_id,
            "reasoning_effort": payload.reasoning_effort or "",
            "prompt_content_hash": str(
                (prompt_snapshot or {}).get("content_hash") or ""
            ),
        })
        state = job_manager.create_job(
            {
                "platform": "creative",
                "crawler_type": "phase4_generate",
                "keywords": [],
                "save_data_option": "json",
                "get_comments": False,
                "get_sub_comments": False,
                "extra": {
                    "action": action,
                    "normalized_stage": stage,
                    "workflow_mode": "free",
                    "project_id": project_id,
                    "episode_number": episode_number,
                    "created_by_user_id": principal.user_id,
                    "operation_id": payload.operation_id,
                    "free_context_id": context["id"],
                    "generation_request_hash": request_hash,
                    "expected_head_revision": payload.expected_head_revision,
                    "count": payload.count if stage == "brief" else 1,
                    "llm_model_record_id": llm_model_record_id,
                    "reasoning_effort": payload.reasoning_effort or "",
                    **prompt_job_identity(prompt_snapshot),
                    "runtime_dir": str(Path(runtime_dir).resolve()),
                },
            },
            created_by_user_id=principal.user_id,
            project_id=project_id,
        )
        return {"status": "success", "job": state.to_dict()}

    def start_free_episode_series_generation_job(
        *,
        project_id: int,
        stage: str,
        payload: FreeCreativeSeriesGenerateRequest,
        principal: Principal,
    ) -> dict[str, Any]:
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        if stage not in {"beats", "script"}:
            raise HTTPException(404, "unsupported creative stage")
        llm_model_record_id = validate_llm_model(
            payload.llm_model_record_id, payload.reasoning_effort
        )
        context_refs = payload.context_refs.model_dump()
        if stage == "script" and context_refs.get("beats_id") is not None:
            raise HTTPException(400, "全集剧本生成不能把单集 Beats 引用应用到全部集")
        resource_label = "Beats" if stage == "beats" else "场次剧本"
        project = project_or_404(project_id)
        total = int(project["target_episode_count"])
        required_episodes = set(range(1, total + 1))
        expected_head_revisions = {
            int(number): int(revision)
            for number, revision in payload.expected_head_revisions.items()
        }
        if set(expected_head_revisions) != required_episodes:
            raise HTTPException(
                400, f"必须提供项目全部集的{resource_label} Head 版本"
            )
        try:
            application_service.assert_stage_write_access(project_id, stage, principal)
            free_mode_service.assert_free_project(project_id)
            refs_by_episode = {
                episode_number: dict(context_refs)
                for episode_number in range(1, total + 1)
            }
            if stage == "script":
                missing_beats: list[int] = []
                for episode_number in range(1, total + 1):
                    head = free_mode_service.get_head(
                        project_id, "beats", episode_number
                    )
                    beats = (
                        repository.get_episode(int(head["resource_ref"]))
                        if head is not None
                        else None
                    )
                    if (
                        beats is None
                        or beats.get("stage") != "beats"
                        or int(beats.get("episode_number") or 0) != episode_number
                        or beats.get("status") not in {"locked", "approved"}
                    ):
                        missing_beats.append(episode_number)
                        continue
                    refs_by_episode[episode_number]["beats_id"] = int(beats["id"])
                if missing_beats:
                    missing_text = "、".join(str(number) for number in missing_beats)
                    raise ValueError(
                        "全剧场次剧本生成前必须锁定全部 Beats；"
                        f"未锁定集数：{missing_text}"
                    )
            contexts = {
                episode_number: free_mode_service.create_generation_context(
                    context_id=f"{payload.operation_id}:{stage}:{episode_number}",
                    project_id=project_id,
                    stage=stage,
                    episode_number=episode_number,
                    instruction=payload.generation_instruction,
                    context_refs=refs_by_episode[episode_number],
                    actor_user_id=principal.user_id,
                    prompt_snapshot=project_prompt_service.resolve_generation_snapshot(
                        project_id=project_id,
                        stage=stage,
                        series=True,
                        episode_number=episode_number,
                        template_key=payload.prompt_template_key,
                        template_revision=payload.prompt_template_revision,
                        run_override=payload.prompt_override,
                        actor_user_id=principal.user_id,
                    ),
                )
                for episode_number in range(1, total + 1)
            }
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        request_hash = canonical_request_hash({
            "project_id": project_id,
            "stage": stage,
            "series": True,
            "operation_id": payload.operation_id,
            "context_hashes": {
                number: context["context_hash"]
                for number, context in contexts.items()
            },
            "expected_head_revisions": expected_head_revisions,
            "llm_model_record_id": llm_model_record_id,
            "reasoning_effort": payload.reasoning_effort or "",
        })
        state = job_manager.create_job(
            {
                "platform": "creative",
                "crawler_type": "phase4_generate",
                "keywords": [],
                "save_data_option": "json",
                "get_comments": False,
                "get_sub_comments": False,
                "extra": {
                    "action": stage,
                    "normalized_stage": stage,
                    "workflow_mode": "free",
                    "project_id": project_id,
                    "episode_number": None,
                    "episode_start": 1,
                    "episode_end": total,
                    "series": True,
                    "created_by_user_id": principal.user_id,
                    "operation_id": payload.operation_id,
                    "free_context_ids": {
                        str(number): context["id"]
                        for number, context in contexts.items()
                    },
                    "generation_request_hash": request_hash,
                    "expected_head_revisions": {
                        str(number): revision
                        for number, revision in expected_head_revisions.items()
                    },
                    "llm_model_record_id": llm_model_record_id,
                    "reasoning_effort": payload.reasoning_effort or "",
                    **prompt_job_identity(
                        dict(next(iter(contexts.values()))["context_snapshot"]).get(
                            "prompt_snapshot"
                        )
                    ),
                    "runtime_dir": str(Path(runtime_dir).resolve()),
                },
            },
            created_by_user_id=principal.user_id,
            project_id=project_id,
        )
        return {"status": "success", "job": state.to_dict()}

    @router.post("/projects/{project_id}/free/beats/generate-series")
    async def generate_free_beats_series(
        project_id: int,
        payload: FreeCreativeSeriesGenerateRequest,
        principal: Principal = Depends(current_principal),
    ):
        return start_free_episode_series_generation_job(
            project_id=project_id,
            stage="beats",
            payload=payload,
            principal=principal,
        )

    @router.post("/projects/{project_id}/free/scripts/generate-series")
    async def generate_free_scripts_series(
        project_id: int,
        payload: FreeCreativeSeriesGenerateRequest,
        principal: Principal = Depends(current_principal),
    ):
        return start_free_episode_series_generation_job(
            project_id=project_id,
            stage="script",
            payload=payload,
            principal=principal,
        )

    @router.post("/projects/{project_id}/free/{stage}/generate")
    async def generate_free_resource(
        project_id: int,
        stage: str,
        payload: FreeCreativeGenerateRequest,
        episode_number: int | None = Query(None, ge=1, le=100),
        principal: Principal = Depends(current_principal),
    ):
        normalized = {"briefs": "brief", "characters": "characters", "outlines": "outline", "beats": "beats", "scripts": "script"}.get(stage)
        if normalized is None:
            raise HTTPException(404, "unsupported creative stage")
        if normalized in {"beats", "script"} and episode_number is None:
            raise HTTPException(400, "Beats 和场次剧本生成需要集数")
        if normalized not in {"beats", "script"}:
            episode_number = None
        return start_free_generation_job(
            project_id=project_id,
            stage=normalized,
            episode_number=episode_number,
            payload=payload,
            principal=principal,
        )

    @router.get("/generated-resources")
    async def list_generated_resources(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, list[dict[str, Any]]]:
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        resources: list[dict[str, Any]] = []
        page_authorized = has_creative_page_api_access(request, principal)
        for summary in repository.list_projects():
            project_id = int(summary["id"])
            if (
                not principal.is_system_admin
                and not page_authorized
                and not workflow_repository.is_active_project_member(
                    workflow_project_id(project_id), principal
                )
                and not permission_service.can_read_project(
                    principal, workflow_project_id(project_id)
                )
            ):
                continue
            project = project_or_404(project_id)
            for item in project.get("blueprints", []):
                resources.append({
                    "resource_id": str(item["id"]), "stage": "brief",
                    "resource_version": int(item["version"]),
                    "resource_status": str(item["status"]),
                    "project_id": str(project_id), "project_name": project["name"],
                    "generated_at": float(item.get("created_at") or 0),
                })
            character_batches = {
                str(item["batch_id"]): item for item in project.get("characters", [])
                if item.get("batch_id")
            }
            for batch_id, item in character_batches.items():
                resources.append({
                    "resource_id": f"{project_id}:{batch_id}", "stage": "characters",
                    "resource_version": int(item["version"]),
                    "resource_status": str(item["status"]),
                    "project_id": str(project_id), "project_name": project["name"],
                    "generated_at": float(item.get("created_at") or 0),
                })
            outline_batches = {
                str(item["batch_id"]): item for item in project.get("episodes", [])
                if item.get("stage") == "outline" and item.get("batch_id")
            }
            for batch_id, item in outline_batches.items():
                resources.append({
                    "resource_id": f"{project_id}:{batch_id}", "stage": "outline",
                    "resource_version": int(item["version"]),
                    "resource_status": str(item["status"]),
                    "project_id": str(project_id), "project_name": project["name"],
                    "generated_at": float(item.get("created_at") or 0),
                })
            for item in project.get("episodes", []):
                if item.get("stage") in {"beats", "script"}:
                    resources.append({
                        "resource_id": str(item["id"]), "stage": str(item["stage"]),
                        "resource_version": int(item["version"]),
                        "resource_status": str(item["status"]),
                        "project_id": str(project_id), "project_name": project["name"],
                        "generated_at": float(item.get("created_at") or 0),
                    })
        return {"resources": resources}

    @router.post("/projects/{project_id}/workflow-tasks/{resource_type}/ensure")
    async def ensure_project_workflow_task(
        project_id: int,
        resource_type: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        role_by_resource = {
            "brief": "creative_planner",
            "characters": "creative_planner",
            "outline": "screenwriter",
            "beats": "screenwriter",
            "script": "screenwriter",
        }
        role_code = role_by_resource.get(resource_type)
        if role_code is None:
            raise HTTPException(400, "unsupported creative resource type")
        can_claim_established_writing = bool(
            resource_type in ESTABLISHED_WRITING_TASK_TYPES
            and ESTABLISHED_WRITING_PERMISSION in principal.permissions
        )
        if not can_claim_established_writing:
            assert_project_generation_access(project_id, principal)
        project = project_or_404(project_id)
        try:
            task = workflow_repository.ensure_project_task(
                workflow_project_id(project_id),
                (
                    int(project["topic_id"])
                    if project.get("topic_id") is not None
                    else None
                ),
                task_type=resource_type,
                role_code=role_code,
                actor=principal,
                project_uid=str(project.get("project_uid") or "") or None,
            )
            if task["status"] not in {"pool", "assigned", "in_progress", "returned"}:
                task = workflow_repository.create_next_task(
                    task["id"], resource_type, role_code, actor=principal
                )
            if task["status"] == "pool":
                if workflow_repository.can_claim_task(task["id"], principal):
                    task = workflow_repository.claim_task(task["id"], principal)
                elif can_claim_established_writing:
                    raise HTTPException(409, "该创作阶段已由其他编剧处理")
            if (
                can_claim_established_writing
                and task["status"] in {"assigned", "in_progress", "returned"}
                and task.get("assignee_user_id") != principal.user_id
            ):
                raise HTTPException(409, "该创作阶段已由其他编剧处理")
            if resource_type == "brief":
                repository.reconcile_project_workflow_task(task["id"], project_id)
        except HTTPException:
            raise
        except (PermissionError, ValueError) as exc:
            if can_claim_established_writing:
                raise HTTPException(409, "该创作阶段已由其他编剧处理") from exc
            raise
        return task

    @router.get("/projects/{project_id}/generated-content")
    async def get_generated_content(
        project_id: int,
        request: Request,
        stage: str = Query(...),
        resource_id: str | None = Query(None),
        principal: Principal = Depends(current_principal),
    ):
        assert_project_owner_preview_access(request, project_id, principal)
        project = project_or_404(project_id)
        versions, selected_resource_id, snapshot = generated_stage_snapshot(
            project, stage, resource_id
        )
        snapshot = project_prompt_service.public_projection(snapshot, principal.user_id, project_id)
        return {
            "project": {"id": project["id"], "name": project["name"]},
            "stage": stage,
            "versions": versions,
            "selected_resource_id": selected_resource_id,
            "snapshot": snapshot,
        }

    @router.get("/projects/{project_id}/generated-content/download")
    async def download_generated_content(
        project_id: int,
        request: Request,
        stage: str = Query(...),
        resource_id: str = Query(..., min_length=1),
        format: str = Query("md"),
        principal: Principal = Depends(current_principal),
    ):
        assert_project_owner_preview_access(request, project_id, principal)
        project = project_or_404(project_id)
        _, _, snapshot = generated_stage_snapshot(project, stage, resource_id)
        snapshot = project_prompt_service.public_projection(snapshot, principal.user_id, project_id)
        body, media_type = serialize_generated_stage(project, stage, snapshot, format)
        if stage == "script":
            filename = script_download_filename(
                project,
                int(snapshot.get("episode_number") or 0),
                int(snapshot.get("version") or 1),
                format,
            )
            content_disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        else:
            content_disposition = f'attachment; filename="project-{project_id}-{stage}.{format}"'
        return Response(
            content=body,
            media_type=media_type,
            headers={
                "Content-Disposition": content_disposition
            },
        )

    @router.get("/projects/{project_id}/scripts/download")
    async def download_merged_project_scripts(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_owner_preview_access(request, project_id, principal)
        project = project_or_404(project_id)
        project["video_parameter_recommendation"] = attach_recommendation_staleness(
            project, project.get("video_parameter_recommendation")
        )
        try:
            filename, body = build_merged_script_download(project)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(
            content=body.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    def video_parameter_recommendation_view(
        project: dict[str, Any]
    ) -> dict[str, Any]:
        record = repository.get_video_parameter_recommendation(int(project["id"]))
        record = _recover_transient_video_parameter_conflict(
            repository, int(project["id"]), record
        )
        deadline_seconds = video_parameter_analysis_deadline_seconds(config)
        job_view: dict[str, Any] | None = None

        def complete_timeout_fallback(reason: str) -> dict[str, Any]:
            latest = repository.get_video_parameter_recommendation(
                int(project["id"])
            )
            if latest and latest.get("status") not in {"queued", "running"}:
                return latest
            try:
                source = build_video_parameter_source(project)
                expected = str(
                    (((job_view or {}).get("job") or {}).get("extra") or {}).get(
                        "source_fingerprint"
                    )
                    or ""
                )
                if expected and expected != source["fingerprint"]:
                    return repository.fail_video_parameter_recommendation(
                        int(project["id"]),
                        "剧本已变化，请重新发起视频参数分析",
                    )
                return repository.complete_video_parameter_recommendation(
                    int(project["id"]),
                    source_fingerprint=source["fingerprint"],
                    source_episode_numbers=source["episode_numbers"],
                    source_complete=source["complete"],
                    recommendation=fallback_recommendation(source, reason),
                )
            except ValueError as exc:
                return repository.fail_video_parameter_recommendation(
                    int(project["id"]), str(exc)
                )

        if (
            record
            and record.get("status") in {"queued", "running"}
            and record.get("job_id")
            and job_manager is not None
        ):
            try:
                job = job_manager.get_job(str(record["job_id"]))
                job_view = {
                    key: job.get(key)
                    for key in (
                        "job_id", "status", "message", "progress", "started_at",
                        "finished_at", "job",
                    )
                }
                job_status = str(job.get("status") or "")
                if job_status in {"failed", "cancelled"}:
                    record = repository.fail_video_parameter_recommendation(
                        int(project["id"]),
                        str(job.get("message") or "视频参数分析任务失败"),
                    )
                elif job_status == "succeeded":
                    record = complete_timeout_fallback(
                        "AI 分析任务未返回有效结果，已自动生成规则建议"
                    )
            except JobNotFoundError:
                record = repository.fail_video_parameter_recommendation(
                    int(project["id"]), "视频参数分析任务已不存在"
                )
            except Exception:
                pass
        if record and record.get("status") in {"queued", "running"}:
            elapsed = max(0.0, time.time() - float(record.get("updated_at") or 0))
            if elapsed >= deadline_seconds + 15:
                record = complete_timeout_fallback(
                    f"AI 分析超过 {deadline_seconds} 秒，已自动生成规则建议"
                )
        return {
            "project_id": int(project["id"]),
            "capabilities": MODEL_CAPABILITIES,
            "scenarios": list(SCENARIOS),
            "analysis_deadline_seconds": deadline_seconds,
            "job": {
                key: value
                for key, value in (job_view or {}).items()
                if key != "job"
            } or None,
            "record": attach_recommendation_staleness(project, record),
        }

    @router.get("/projects/{project_id}/video-parameter-recommendation")
    async def get_video_parameter_recommendation(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        return video_parameter_recommendation_view(project_or_404(project_id))

    @router.post(
        "/projects/{project_id}/video-parameter-recommendation/analyze",
        status_code=202,
    )
    async def analyze_project_video_parameters(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        assert_project_content_write_access(request, project_id, principal)
        project = project_or_404(project_id)
        try:
            source = build_video_parameter_source(project)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        existing = repository.get_video_parameter_recommendation(project_id)
        if existing and existing.get("status") in {"queued", "running"}:
            return video_parameter_recommendation_view(project)
        if job_manager is None:
            raise HTTPException(503, "后台任务服务不可用")
        repository.queue_video_parameter_recommendation(
            project_id, created_by=principal.user_id
        )
        payload = {
            "platform": "creative",
            "crawler_type": "video_parameter_recommendation",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "project_id": project_id,
                "source_fingerprint": source["fingerprint"],
                "created_by_user_id": principal.user_id,
                "runtime_dir": str(Path(runtime_dir).resolve()),
            },
        }
        try:
            state = job_manager.create_job(
                payload,
                created_by_user_id=principal.user_id,
                project_id=project_id,
            )
        except JobConflictError as exc:
            if repository.restore_video_parameter_recommendation(project_id) is None:
                repository.fail_video_parameter_recommendation(project_id, str(exc))
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            repository.fail_video_parameter_recommendation(project_id, str(exc))
            raise HTTPException(503, "无法创建视频参数分析任务") from exc
        repository.set_video_parameter_recommendation_job(
            project_id, str(state.job_id)
        )
        return video_parameter_recommendation_view(project)

    @router.post("/projects/{project_id}/video-parameter-recommendation/confirm")
    async def confirm_project_video_parameters(
        project_id: int,
        payload: VideoParameterConfirmRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        assert_project_read_access(request, project_id, principal)
        assert_project_content_write_access(request, project_id, principal)
        project = project_or_404(project_id)
        try:
            source = build_video_parameter_source(project)
            if source["fingerprint"] != payload.source_fingerprint:
                raise ValueError("剧本已变化，请重新分析后再确认")
            repository.confirm_video_parameter_recommendation(
                project_id,
                model_key=payload.model_key,
                scenario=payload.scenario,
                source_fingerprint=payload.source_fingerprint,
                confirmed_by=principal.user_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return video_parameter_recommendation_view(project)

    @router.get("/projects/{project_id}/episodes/download")
    async def download_project_episode_selection(
        project_id: int,
        request: Request,
        stage: str = Query(...),
        episodes: str = Query("all", min_length=1),
        format: str = Query("md"),
        video_parameters: str = Query("", max_length=10_000),
        principal: Principal = Depends(current_principal),
    ):
        assert_project_owner_preview_access(request, project_id, principal)
        project = project_or_404(project_id)
        allowed_formats = {
            "outline": {"md", "json"},
            "beats": {"md", "json"},
            "script": {"txt", "json"},
        }
        if stage not in allowed_formats:
            raise HTTPException(422, "stage 必须是 outline、beats 或 script")
        if format not in allowed_formats[stage]:
            raise HTTPException(422, f"{stage} 不支持 {format} 格式")
        episode_numbers: list[int] | None
        if episodes == "all":
            episode_numbers = None
        elif re.fullmatch(r"\d+(?:,\d+)*", episodes):
            episode_numbers = sorted({int(value) for value in episodes.split(",")})
            target_count = int(project.get("target_episode_count") or 0)
            if any(number < 1 or number > target_count for number in episode_numbers):
                raise HTTPException(422, "下载集数超出项目范围")
        else:
            raise HTTPException(422, "episodes 必须是 all 或逗号分隔的集数")
        try:
            export_video_parameters = None
            if stage == "script":
                current_record = attach_recommendation_staleness(
                    project, project.get("video_parameter_recommendation")
                )
                export_video_parameters = video_parameter_export_record(
                    current_record, video_parameters
                )
            filename, body, media_type = build_episode_selection_download(
                project,
                stage,
                episode_numbers,
                format,
                video_parameter_record=export_video_parameters,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(
            content=body,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            },
        )

    @router.patch("/projects/{project_id}")
    async def update_project(
        project_id: int,
        payload: ProjectUpdateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        try:
            permission_service = getattr(
                request.app.state, "identity_permission_service", None
            )
            if not isinstance(permission_service, PermissionService):
                raise HTTPException(500, "identity permission service is not configured")
            project = project_or_404(project_id)
            scoped_project_id = workflow_project_id(project_id)
            is_active_member = workflow_repository.is_active_project_member(
                scoped_project_id, principal
            )
            if (
                not principal.is_system_admin
                and not permission_service.can_create_project_content(
                    principal, scoped_project_id, active_member=is_active_member
                )
            ):
                raise HTTPException(403, "只能修改本人创建或参与的项目草稿")
            updates = payload.model_dump(exclude_none=True)
            draft_brief_id = updates.pop("draft_brief_id", None)
            expected_project_revision = updates.pop(
                "expected_project_revision", None
            )
            if updates.get("status") == "archived":
                if not principal.is_system_admin and "project.archive" not in principal.permissions:
                    raise HTTPException(403, "没有归档项目的权限")
                if not principal.is_system_admin and not permission_service.can_access_managed_project(
                    principal, scoped_project_id
                ):
                    raise HTTPException(403, "无权管理该项目")
            return repository.update_project(
                project_id,
                updates,
                draft_brief_id=draft_brief_id,
                expected_revision=expected_project_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/projects/{project_id}")
    async def delete_project(
        project_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        scoped_project_id = workflow_project_id(project_id)
        project = repository.get_project(project_id)
        pending_deletion = repository.get_pending_project_deletion(project_id)
        get_workflow_deletion = getattr(
            workflow_repository, "get_pending_project_deletion", None
        )
        workflow_deletion = (
            get_workflow_deletion(scoped_project_id)
            if callable(get_workflow_deletion)
            else None
        )
        if project is None and pending_deletion is None and workflow_deletion is None:
            raise HTTPException(404, "创作项目不存在")
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        if project is not None:
            if not permission_service.can_delete_project(principal, scoped_project_id):
                raise HTTPException(403, "无权管理该项目")
        elif pending_deletion is not None and not (
            principal.is_system_admin
            or (
                pending_deletion.get("requested_by") == principal.user_id
                and "project.delete" in principal.permissions
            )
        ):
            raise HTTPException(403, "无权继续该项目的删除清理")
        elif (
            pending_deletion is None
            and workflow_deletion is not None
            and not (
                principal.is_system_admin
                or (
                    workflow_deletion.get("requested_by") == principal.user_id
                    and "project.delete" in principal.permissions
                )
            )
        ):
            raise HTTPException(403, "无权继续该项目的删除清理")
        if project is not None and workflow_deletion is not None:
            existing_owner = str(workflow_deletion.get("owner_token") or "")
            resumable_api_delete = existing_owner.startswith("creative-delete:") and (
                principal.is_system_admin
                or workflow_deletion.get("requested_by") == principal.user_id
            )
            if not resumable_api_delete:
                raise HTTPException(409, "项目删除正在由另一个请求处理")
        deletion_owner = (
            str(workflow_deletion["owner_token"])
            if workflow_deletion is not None
            else f"creative-delete:{project_id}:{uuid.uuid4().hex}"
        )
        recovery_project = (
            project
            or ((pending_deletion or {}).get("project"))
            or ((workflow_deletion or {}).get("project_snapshot"))
            or {}
        )
        marker_snapshot = {
            "id": project_id,
            "name": str(recovery_project.get("name") or ""),
            "creative_store_id": repository.store_id,
            "workflow_project_id": scoped_project_id,
            "project_uid": str(recovery_project.get("project_uid") or ""),
        }
        try:
            marker = workflow_repository.begin_project_deletion(
                scoped_project_id,
                project_uid=(
                    str(recovery_project.get("project_uid") or "") or None
                ),
                owner_token=deletion_owner,
                requested_by=principal.user_id,
                project_snapshot=marker_snapshot,
            )
            if (
                isinstance(marker, dict)
                and marker.get("owner_token") not in {None, deletion_owner}
            ):
                raise HTTPException(409, "项目删除正在由另一个请求处理")
            if project is not None:
                removed = repository.delete_project(
                    project_id, requested_by=principal.user_id
                )
            else:
                pending_snapshot = (
                    pending_deletion["project"]
                    if pending_deletion is not None
                    else workflow_deletion["project_snapshot"]
                )
                removed = {
                    **pending_snapshot,
                    "delivery_paths": list(
                        (pending_deletion or {}).get("delivery_paths") or []
                    ),
                }
            workflow_repository.delete_project_data(scoped_project_id)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "项目关联任务清理失败，请稍后重试") from exc
        for raw_path in removed.pop("delivery_paths", []):
            try:
                Path(raw_path).unlink(missing_ok=True)
            except OSError:
                continue
        repository.complete_project_deletion(project_id)
        workflow_repository.complete_project_deletion(
            scoped_project_id, owner_token=deletion_owner
        )
        audit_service = getattr(request.app.state, "audit_service", None)
        if isinstance(audit_service, AuditService):
            audit_service.record(
                actor=principal,
                action="creative.project.delete",
                target_type="creative_project",
                target_id=str(project_id),
                project_id=str(project_id),
                before=project or removed,
                after=None,
                reason=None,
                result="success",
                request=request,
            )
        return {"id": project_id, "name": removed["name"]}

    @router.post("/projects/{project_id}/blueprints/generate")
    async def generate_project_blueprints(
        project_id: int,
        payload: BlueprintGenerateRequest,
        request: Request,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        workflow_task_id = validate_task(
            payload.workflow_task_id, "brief", principal, project_id=project_id
        )
        project = project_or_404(project_id)
        if project.get("creation_mode") == "original":
            preserved_ids = list(dict.fromkeys(payload.preserve_blueprint_ids))
            if len(preserved_ids) != len(payload.preserve_blueprint_ids):
                raise HTTPException(409, "保留方向不能重复")
            for blueprint_id in preserved_ids:
                blueprint = repository.get_blueprint(blueprint_id)
                if (
                    blueprint is None
                    or int(blueprint.get("project_id") or 0) != project_id
                    or blueprint.get("direction_type") != "original"
                    or blueprint.get("status") != "draft"
                ):
                    raise HTTPException(409, f"不可保留原创方向：{blueprint_id}")
            missing_count = payload.count - len(preserved_ids)
            if missing_count <= 0:
                raise HTTPException(409, "已保留方向数已达到目标，无需补生成")
            seed = repository.get_current_seed(project_id)
            if seed is None:
                raise HTTPException(409, "原创项目缺少种子")
            try:
                bundle = market_reference_service.resolve(
                    list(seed.get("reference_refs") or []),
                    can_read=lambda kind, identifier: can_read_market_reference(
                        request, principal, kind, identifier
                    ),
                )
                frozen_seed = repository.freeze_seed(int(seed["id"]))
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return create_generation_job(
                project_id,
                "blueprints",
                principal,
                request,
                count=missing_count,
                target_count=payload.count,
                preserve_blueprint_ids=preserved_ids,
                seed_id=frozen_seed["id"],
                market_context=bundle.context,
                blocked_terms=bundle.blocked_terms,
                reference_snapshot=[
                    {**item, "captured_at": bundle.captured_at}
                    for item in bundle.source_refs
                ],
                workflow_task_id=workflow_task_id,
                **generation_request_extra(payload),
            )
        if payload.preserve_blueprint_ids:
            raise HTTPException(409, "仿写方向不支持保留补生成")
        return create_generation_job(
            project_id,
            "blueprints",
            principal,
            request,
            count=payload.count,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/blueprints")
    async def create_project_blueprint(
        project_id: int,
        payload: BlueprintCreateRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            return persist_legacy_draft(
                project_id=project_id,
                stage="brief",
                payload=payload.content,
                workflow_task_id=application_task_id(
                    payload.workflow_task_id, "brief", project_id, principal
                ),
                operation_id=payload.operation_id,
                replaces_resource_id=payload.replaces_resource_id,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/blueprints/{blueprint_id}/derive")
    async def derive_project_blueprint(
        blueprint_id: int,
        payload: BlueprintDeriveRequest,
        request: Request,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        parent = repository.get_blueprint(blueprint_id)
        if parent is None:
            raise HTTPException(404, "原创方向不存在")
        project_id = int(parent["project_id"])
        if parent.get("direction_type") != "original":
            raise HTTPException(409, "只有原创方向可以派生")
        workflow_task_id = validate_task(
            payload.workflow_task_id, "brief", principal, project_id=project_id
        )
        seed = repository.get_current_seed(project_id)
        if seed is None:
            raise HTTPException(409, "原创项目缺少种子")
        try:
            bundle = market_reference_service.resolve(
                list(seed.get("reference_refs") or []),
                can_read=lambda kind, identifier: can_read_market_reference(
                    request, principal, kind, identifier
                ),
            )
            frozen_seed = repository.freeze_seed(int(seed["id"]))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return create_generation_job(
            project_id,
            "blueprint_variant",
            principal,
            request,
            seed_id=frozen_seed["id"],
            parent_blueprint_id=blueprint_id,
            instruction=payload.instruction,
            market_context=bundle.context,
            blocked_terms=bundle.blocked_terms,
            reference_snapshot=[
                {**item, "captured_at": bundle.captured_at}
                for item in bundle.source_refs
            ],
            workflow_task_id=workflow_task_id,
            operation_id=payload.operation_id,
            generation_instruction=payload.instruction,
            source_snapshot_id=payload.source_snapshot_id,
            source_snapshot=payload.source_snapshot,
            llm_model_record_id=payload.llm_model_record_id,
            reasoning_effort=payload.reasoning_effort,
            prompt_template_key=payload.prompt_template_key,
            prompt_template_revision=payload.prompt_template_revision,
            prompt_override=payload.prompt_override,
            prompt_mode="rewrite",
        )

    @router.patch("/blueprints/{blueprint_id}")
    async def update_blueprint(blueprint_id: int, payload: BlueprintUpdateRequest):
        current = repository.get_blueprint(blueprint_id)
        if not current:
            raise HTTPException(404, "blueprint not found")
        sequential_project_or_409(int(current["project_id"]))
        try:
            workflow_adapter.assert_editable("brief", str(blueprint_id))
            if current.get("direction_type") == "original":
                content = validate_original_direction_content(
                    payload.content, market_context=[], blocked_terms=[]
                )
                risk = {"risk_score": 0, "risk_level": "low"}
            else:
                inputs = repository.get_project_inputs(int(current["project_id"]))
                content = payload.content
                risk = assess_similarity_risk(
                    inputs["breakdown"].get("content") or {}, content
                )
            return repository.update_blueprint(
                blueprint_id,
                content,
                risk_score=risk["risk_score"],
                risk_level=risk["risk_level"],
                difference_note=payload.difference_note,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/blueprints/{blueprint_id}/versions")
    async def create_blueprint_version(
        blueprint_id: int,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal = Depends(current_principal),
    ):
        try:
            current = repository.get_blueprint(blueprint_id)
            if not current:
                raise ValueError("仿制方向不存在")
            workflow_task_id = payload.workflow_task_id if payload else None
            workflow_task_id = validate_task(
                workflow_task_id,
                "brief",
                principal,
                project_id=int(current["project_id"]),
            )
            if payload is None or payload.expected_project_revision is None:
                raise CreativeContextConflict(
                    "expected_project_revision is required for a revision"
                )
            ref = application_service.create_revision(
                project_id=int(current["project_id"]),
                stage="brief",
                replaces_resource_id=str(blueprint_id),
                payload=current["content"],
                workflow_task_id=workflow_task_id,
                operation_id=(payload.operation_id if payload else None) or uuid.uuid4().hex,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
            )
            return application_service.hydrate_resource_ref(ref)
        except CreativeContextConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/blueprints/{blueprint_id}/lock-brief")
    async def lock_blueprint_brief(
        blueprint_id: int,
        payload: BriefLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            blueprint = repository.get_blueprint(blueprint_id)
            if blueprint is None:
                raise KeyError(blueprint_id)
            return application_service.lock_resource(
                project_id=int(blueprint["project_id"]),
                stage="brief",
                resource_id=str(blueprint_id),
                workflow_task_id=payload.workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                actor=principal,
                difference_note=payload.difference_note,
                expected_project_revision=payload.expected_project_revision,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/briefs/{blueprint_id}/select")
    async def select_project_brief(
        project_id: int,
        blueprint_id: int,
    ):
        sequential_project_or_409(project_id)
        try:
            return repository.select_locked_brief(project_id, blueprint_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/character-batches/{batch_id}/select")
    async def select_project_character_batch(
        project_id: int,
        batch_id: str,
    ):
        sequential_project_or_409(project_id)
        try:
            return repository.select_locked_character_batch(project_id, batch_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/characters/generate")
    async def generate_project_characters(
        project_id: int,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if not project.get("locked_brief_id"):
            raise HTTPException(409, "必须先锁定 Brief")
        workflow_task_id = payload.workflow_task_id if payload else None
        workflow_task_id = validate_task(
            workflow_task_id, "characters", principal, project_id=project_id
        )
        return create_generation_job(
            project_id,
            "characters",
            principal,
            request,
            workflow_task_id=workflow_task_id,
            prompt_mode="series",
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/characters")
    async def create_project_characters(
        project_id: int,
        payload: CharacterBatchRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = application_task_id(
                payload.workflow_task_id,
                "characters",
                project_id,
                principal,
            )
            return persist_legacy_draft(
                project_id=project_id,
                stage="characters",
                payload={
                    "characters": payload.characters,
                    "relationships": payload.relationships,
                },
                workflow_task_id=workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                replaces_resource_id=payload.replaces_resource_id,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/characters/{character_id}")
    async def update_character(character_id: int, payload: CreativeItemUpdateRequest):
        try:
            resource_id = batch_resource_for("creative_characters", character_id)
            if resource_id:
                sequential_project_or_409(int(resource_id.split(":", 1)[0]))
                workflow_adapter.assert_editable("characters", resource_id)
            result = repository.update_character(character_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "character not found")
        return result

    @router.patch("/relationships/{relationship_id}")
    async def update_relationship(relationship_id: int, payload: CreativeItemUpdateRequest):
        try:
            resource_id = batch_resource_for(
                "creative_relationships", relationship_id
            )
            if resource_id:
                sequential_project_or_409(int(resource_id.split(":", 1)[0]))
                workflow_adapter.assert_editable("characters", resource_id)
            result = repository.update_relationship(relationship_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "relationship not found")
        return result

    @router.post("/projects/{project_id}/characters/{batch_id}/lock")
    async def lock_project_characters(
        project_id: int,
        batch_id: str,
        payload: CreativeLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            return application_service.lock_resource(
                project_id=project_id,
                stage="characters",
                resource_id=f"{project_id}:{batch_id}",
                workflow_task_id=payload.workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                actor=principal,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/outlines/generate")
    async def generate_project_outlines(
        project_id: int,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if not project.get("character_batch_id"):
            raise HTTPException(409, "必须先锁定人物关系版本")
        workflow_task_id = payload.workflow_task_id if payload else None
        workflow_task_id = validate_task(
            workflow_task_id, "outline", principal, project_id=project_id
        )
        return create_generation_job(
            project_id,
            "outlines",
            principal,
            request,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/outlines")
    async def create_project_outlines(
        project_id: int,
        payload: OutlineBatchRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = application_task_id(
                payload.workflow_task_id,
                "outline",
                project_id,
                principal,
            )
            return persist_legacy_draft(
                project_id=project_id,
                stage="outline",
                payload={
                    "episodes": payload.episodes,
                    "series_arc": payload.series_arc,
                },
                workflow_task_id=workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                replaces_resource_id=payload.replaces_resource_id,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/episodes/{episode_id}/fields/{field}/rewrite")
    async def generate_outline_field_rewrite(
        episode_id: int,
        field: str,
        payload: OutlineFieldRewriteRequest,
        principal: Principal = Depends(current_principal),
    ):
        context = outline_field_context_or_http(episode_id)
        if field not in OUTLINE_TEXT_FIELDS:
            raise HTTPException(400, "不支持的大纲字段")
        episode = context["episode"]
        project_id = int(episode["project_id"])
        try:
            application_service.assert_stage_write_access(
                project_id, "outline", principal
            )
            if (
                context["project"].get("workflow_mode", "sequential") == "sequential"
                and episode.get("status") == "draft"
            ):
                workflow_adapter.assert_editable(
                    "outline", f"{project_id}:{episode['batch_id']}"
                )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if llm_provider_factory is None:
            raise HTTPException(503, "LLM provider is not configured")
        try:
            provider = llm_provider_factory()
        except Exception as exc:
            raise HTTPException(503, "LLM provider is not configured") from exc
        try:
            prompt_snapshot = project_prompt_service.resolve_generation_snapshot(
                project_id=project_id,
                stage="outline",
                series=False,
                mode="rewrite",
                episode_number=0,
                template_key=None,
                template_revision=None,
                run_override=None,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            candidate, meta = await rewrite_outline_field(
                provider,
                project=context["project"],
                brief=context["brief"],
                characters=context["characters"],
                relationships=context["relationships"],
                episode=episode["content"],
                previous_episode=(
                    context["previous_episode"]["content"]
                    if context["previous_episode"]
                    else None
                ),
                next_episode=(
                    context["next_episode"]["content"]
                    if context["next_episode"]
                    else None
                ),
                field=field,
                instruction=payload.instruction,
                prompt_snapshot=prompt_snapshot,
            )
        except Exception as exc:
            raise HTTPException(502, "大纲字段重写失败") from exc
        return {
            "episode_id": episode["id"],
            "project_id": project_id,
            "batch_id": episode["batch_id"],
            "field": field,
            "original_value": episode["content"].get(field, ""),
            "candidate_value": candidate,
            "source_fingerprint": outline_field_source_fingerprint(episode, field),
            "source_status": episode["status"],
            "meta": meta,
        }

    @router.post("/episodes/{episode_id}/fields/{field}/apply-rewrite")
    async def apply_outline_field_rewrite(
        episode_id: int,
        field: str,
        payload: OutlineFieldRewriteApplyRequest,
        principal: Principal = Depends(current_principal),
    ):
        context = outline_field_context_or_http(episode_id)
        if field not in OUTLINE_TEXT_FIELDS:
            raise HTTPException(400, "不支持的大纲字段")
        episode = context["episode"]
        project_id = int(episode["project_id"])
        is_free = context["project"].get("workflow_mode", "sequential") == "free"
        try:
            application_service.assert_stage_write_access(
                project_id, "outline", principal
            )
            if is_free:
                if payload.expected_head_revision is None:
                    raise CreativeContextConflict("缺少自由创作版本校验信息")
                return free_mode_service.apply_outline_field_rewrite(
                    project_id=project_id,
                    episode_id=episode_id,
                    field=field,
                    candidate_value=payload.candidate_value,
                    source_fingerprint=payload.source_fingerprint,
                    expected_head_revision=payload.expected_head_revision,
                    actor_user_id=principal.user_id,
                    operation_id=payload.operation_id,
                )
            if episode.get("status") == "draft":
                workflow_adapter.assert_editable(
                    "outline", f"{project_id}:{episode['batch_id']}"
                )
                saved = repository.apply_outline_field_rewrite(
                    episode_id,
                    field,
                    payload.candidate_value,
                    payload.source_fingerprint,
                )
                return {
                    "episode": saved,
                    "batch_id": saved["batch_id"],
                    "version": saved["version"],
                    "created_new_version": False,
                }

            if outline_field_source_fingerprint(episode, field) != payload.source_fingerprint:
                raise CreativeContextConflict("该字段已被更新，请重新生成")
            if int(context["project"].get("revision") or 0) != payload.expected_project_revision:
                raise CreativeContextConflict("project authority changed")
            source_batch = repository.get_outline_batch(
                project_id, str(episode["batch_id"])
            )
            source_episodes = sorted(
                source_batch["episodes"], key=lambda item: int(item["episode_number"])
            )
            episodes = [dict(item["content"]) for item in source_episodes]
            target_number = int(episode["episode_number"])
            for item in episodes:
                if int(item.get("episode") or 0) == target_number:
                    item[field] = payload.candidate_value.strip()
                    break
            workflow_task_id = application_task_id(
                None, "outline", project_id, principal
            )
            ref = application_service.create_revision(
                project_id=project_id,
                stage="outline",
                replaces_resource_id=f"{project_id}:{episode['batch_id']}",
                payload={
                    "episodes": episodes,
                    "series_arc": str(
                        (source_episodes[0].get("content") or {}).get("series_arc") or ""
                    ),
                },
                workflow_task_id=workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
            )
            derived = application_service.hydrate_resource_ref(ref)
            saved = next(
                item
                for item in derived["episodes"]
                if int(item["episode_number"]) == target_number
            )
            return {
                "episode": saved,
                "batch_id": derived["batch_id"],
                "version": saved["version"],
                "created_new_version": True,
            }
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except CreativeContextConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/outlines/{batch_id}/overview/generate")
    async def generate_outline_overview(
        project_id: int,
        batch_id: str,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        try:
            project_or_404(project_id)
            batch = repository.assert_outline_overview_generation_ready(
                project_id, batch_id
            )
            statuses = {item.get("status") for item in batch["episodes"]}
            if statuses == {"draft"}:
                workflow_adapter.assert_editable(
                    "outline", f"{project_id}:{batch_id}"
                )
                workflow_task_id = validate_task(
                    payload.workflow_task_id if payload else None,
                    "outline",
                    principal,
                    project_id=project_id,
                )
            else:
                workflow_task_id = None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return create_generation_job(
            project_id,
            "outline_overview",
            principal,
            request,
            workflow_task_id=workflow_task_id,
            batch_id=batch_id,
            prompt_mode="rewrite",
            **generation_request_extra(payload),
        )

    @router.patch("/projects/{project_id}/outlines/{batch_id}/overview")
    async def update_outline_overview(
        project_id: int,
        batch_id: str,
        payload: OutlineOverviewRequest,
    ):
        sequential_project_or_409(project_id)
        try:
            workflow_adapter.assert_editable(
                "outline", f"{project_id}:{batch_id}"
            )
            return repository.update_outline_overview(
                project_id, batch_id, payload.series_overview
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/episodes/{episode_id}")
    async def update_episode(episode_id: int, payload: CreativeItemUpdateRequest):
        try:
            episode = repository.get_episode(episode_id)
            if episode:
                sequential_project_or_409(int(episode["project_id"]))
                resource_type = episode["stage"] if episode["stage"] in {"beats", "script"} else "outline"
                resource_id = str(episode_id) if resource_type in {"beats", "script"} else f"{episode['project_id']}:{episode['batch_id']}"
                workflow_adapter.assert_editable(resource_type, resource_id)
            result = repository.update_episode(episode_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "episode not found")
        return result

    @router.post("/projects/{project_id}/outlines/{batch_id}/lock")
    async def lock_project_outlines(
        project_id: int,
        batch_id: str,
        payload: CreativeLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            return application_service.lock_resource(
                project_id=project_id,
                stage="outline",
                resource_id=f"{project_id}:{batch_id}",
                workflow_task_id=payload.workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                actor=principal,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/episodes/{episode_number}/beats/generate")
    async def generate_episode_beats(
        project_id: int,
        episode_number: int,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if not project.get("outline_batch_id"):
            raise HTTPException(409, "必须先锁定分集大纲")
        if not 1 <= episode_number <= int(project["target_episode_count"]):
            raise HTTPException(400, "集数超出项目范围")
        workflow_task_id = payload.workflow_task_id if payload else None
        workflow_task_id = validate_task(
            workflow_task_id, "beats", principal, project_id=project_id
        )
        return create_generation_job(
            project_id,
            "beats",
            principal,
            request,
            episode_number=episode_number,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/beats/generate-series")
    async def generate_project_beats_series(
        project_id: int,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if not project.get("outline_batch_id"):
            raise HTTPException(409, "必须先锁定分集大纲")
        workflow_task_id = validate_task(
            payload.workflow_task_id if payload else None,
            "beats", principal, project_id=project_id,
        )
        total = int(project["target_episode_count"])
        return create_generation_job(
            project_id, "beats", principal, request,
            series=True, chunk_size=1, episode_start=1, episode_end=total,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/episodes/{episode_number}/beats")
    async def create_project_episode_beats(
        project_id: int,
        episode_number: int,
        payload: EpisodeBeatsRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = application_task_id(
                payload.workflow_task_id,
                "beats",
                project_id,
                principal,
            )
            return persist_legacy_draft(
                project_id=project_id,
                stage="beats",
                payload=payload.content,
                workflow_task_id=workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                replaces_resource_id=payload.replaces_resource_id,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
                episode_number=episode_number,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/episodes/{episode_id}/lock")
    async def lock_episode_beats(
        episode_id: int,
        payload: CreativeLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            episode = repository.get_episode(episode_id)
            if episode is None:
                raise KeyError(episode_id)
            return application_service.lock_resource(
                project_id=int(episode["project_id"]),
                stage="beats",
                resource_id=str(episode_id),
                workflow_task_id=payload.workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                actor=principal,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/beats/lock")
    async def lock_project_beats_batch(
        project_id: int,
        payload: CreativeLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        try:
            return workflow_transitions.complete_creative_beats_batch_without_review(
                payload.workflow_task_id, project_id, principal
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/episodes/{episode_number}/scripts/generate")
    async def generate_episode_script(
        project_id: int, episode_number: int, request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if episode_number in repository.missing_current_locked_beats(project_id):
            raise HTTPException(409, "必须先锁定当前集 Beats")
        workflow_task_id = validate_task(payload.workflow_task_id if payload else None, "script", principal, project_id=project_id)
        return create_generation_job(
            project_id,
            "script",
            principal,
            request,
            episode_number=episode_number,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/scripts/generate-series")
    async def generate_project_scripts_series(
        project_id: int,
        request: Request,
        payload: WorkflowLinkRequest | None = None,
        principal: Principal | None = Depends(creative_job_principal),
    ):
        project = project_or_404(project_id)
        if not project.get("outline_batch_id"):
            raise HTTPException(409, "必须先锁定分集大纲")
        total = int(project["target_episode_count"])
        missing = repository.missing_current_locked_beats(project_id)
        if missing:
            missing_text = "、".join(str(number) for number in missing)
            raise HTTPException(409, f"全剧场次剧本生成前必须锁定全部 Beats；未锁定集数：{missing_text}")
        workflow_task_id = validate_task(
            payload.workflow_task_id if payload else None,
            "script", principal, project_id=project_id,
        )
        return create_generation_job(
            project_id, "script", principal, request,
            series=True, chunk_size=2, episode_start=1, episode_end=total,
            workflow_task_id=workflow_task_id,
            **generation_request_extra(payload),
        )

    @router.post("/projects/{project_id}/episodes/{episode_number}/scripts")
    async def create_project_episode_script(project_id: int, episode_number: int, payload: EpisodeBeatsRequest, principal: Principal = Depends(current_principal)):
        try:
            workflow_task_id = application_task_id(
                payload.workflow_task_id, "script", project_id, principal
            )
            return persist_legacy_draft(
                project_id=project_id,
                stage="script",
                payload=payload.content,
                workflow_task_id=workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                replaces_resource_id=payload.replaces_resource_id,
                expected_project_revision=payload.expected_project_revision,
                actor=principal,
                episode_number=episode_number,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/scripts/{episode_id}/lock")
    async def lock_episode_script(episode_id: int, payload: CreativeLockRequest, principal: Principal = Depends(current_principal)):
        try:
            episode = repository.get_episode(episode_id)
            if episode is None:
                raise KeyError(episode_id)
            return application_service.lock_resource(
                project_id=int(episode["project_id"]),
                stage="script",
                resource_id=str(episode_id),
                workflow_task_id=payload.workflow_task_id,
                operation_id=payload.operation_id or uuid.uuid4().hex,
                actor=principal,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(409 if isinstance(exc, ValueError) else 403, str(exc)) from exc

    @router.post("/projects/{project_id}/scripts/lock")
    async def lock_project_scripts_batch(
        project_id: int,
        payload: CreativeLockRequest,
        principal: Principal = Depends(current_principal),
    ):
        project_or_404(project_id)
        try:
            return workflow_transitions.complete_creative_scripts_batch_without_review(
                payload.workflow_task_id, project_id, principal
            )
        except KeyError as exc:
            raise HTTPException(
                404, "creative resource or workflow task not found"
            ) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


    @router.get("/projects/{project_id}/export")
    async def export_project_stage(
        request: Request,
        project_id: int,
        stage: str,
        format: str = Query("md", pattern="^(md|json)$"),
        submission_id: str | None = None,
    ):
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        principal = optional_principal(request)
        if principal is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        if not principal.is_system_admin:
            raise HTTPException(403, "only system administrators may export deliveries")
        if not submission_id:
            raise HTTPException(409, "approved submission_id is required")
        submission = workflow_repository.get_submission(submission_id)
        if submission is None:
            raise HTTPException(404, "submission not found")
        if submission["status"] != "approved":
            raise HTTPException(409, "delivery requires an approved submission")
        raise HTTPException(
            410,
            "use POST /api/admin/deliveries to create an audited delivery",
        )

    return router


def register_creative_routes(
    app: FastAPI,
    root: str | Path = ".",
    runtime_dir: str | Path = ".runtime",
    job_manager: Any | None = None,
    workflow_repository: WorkflowRepository | None = None,
    repository: CreativeRepository | None = None,
    application_service: CreativeApplicationService | None = None,
    llm_provider_factory: Callable[[], Any] | None = None,
    llm_model_resolver: Callable[[str], dict[str, Any]] | None = None,
    rank_service: TodayRankService | None = None,
) -> None:
    if repository is None:
        raise ValueError("register_creative_routes requires an injected creative repository")
    if workflow_repository is None:
        workflow_repository = WorkflowRepository(
            IdentityRepository(
                SystemDatabase(SystemDatabaseSettings.from_environment())
            )
        )
    app.state.creative_repository = repository
    app.state.workflow_repository = workflow_repository
    app.state.workflow_identity_resolver = WorkflowIdentityResolver(
        repository, workflow_repository
    )
    app.include_router(
        build_creative_router(
            root=root,
            runtime_dir=runtime_dir,
            job_manager=job_manager,
            workflow_repository=workflow_repository,
            repository=repository,
            application_service=application_service,
            llm_provider_factory=llm_provider_factory,
            llm_model_resolver=llm_model_resolver,
            rank_service=rank_service,
        )
    )
