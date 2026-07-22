# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from creative.hit_breakdown import breakdown_field_guide, personalize_breakdown_field_guide
from creative.imitation_service import assess_similarity_risk
from creative.lifecycle_service import TopicLifecycleService, content_fingerprint
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from creative.workflow_bootstrap import WorkflowBootstrapService
from creative.schema import (
    BlueprintGenerateRequest,
    BlueprintUpdateRequest,
    BriefLockRequest,
    CharacterBatchRequest,
    CreativeItemUpdateRequest,
    CreativeSubmitRequest,
    EpisodeBeatsRequest,
    OutlineBatchRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    WorkflowLinkRequest,
)
from creative.topic_service import TopicService
from creative.transcript_assets import find_downloaded_video_assets, find_transcript_bundle
from audit.service import AuditService
from runtime.job_manager import JobConflictError
from identity.dependencies import current_principal, require_permission
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from workflow.repository import WorkflowRepository


class TopicGenerateRequest(BaseModel):
    platform: str = "dy"
    date: str = ""
    limit: int = 10


class TopicUpdateRequest(BaseModel):
    status: str
    decision_note: str = ""


class TopicWorkflowBootstrapRequest(BaseModel):
    project_id: str | None = None


class BreakdownCreateRequest(BaseModel):
    topic_id: int
    force_regenerate: bool = False
    workflow_task_id: str | None = None


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


def creative_api_authorization(
    request: Request,
    principal: Principal = Depends(creative_api_principal),
) -> Principal:
    path = request.url.path.removeprefix("/api/creative")
    method = request.method.upper()
    permissions: tuple[str, ...] | None
    if path.startswith("/topics"):
        if path == "/topics/workflow-candidates":
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
            else ("breakdown.edit",)
        )
    elif path.startswith("/projects"):
        if method == "GET":
            permissions = ("project.read.assigned", "project.read.managed")
        elif path == "/projects":
            permissions = ("project.create",)
        elif "/blueprints/generate" in path:
            permissions = ("blueprint.generate",)
        elif "/characters" in path:
            permissions = (("character.generate",) if path.endswith("/generate") else ("character.edit",))
        elif "/outlines" in path:
            permissions = (("outline.generate",) if path.endswith("/generate") else ("outline.edit",))
        elif "/beats" in path:
            permissions = (("beats.generate",) if path.endswith("/generate") else ("beats.edit",))
        else:
            permissions = ("project.edit.own_draft",)
    elif path.startswith("/blueprints"):
        permissions = ("brief.edit",)
    elif path.startswith("/characters"):
        permissions = ("character.edit",)
    elif path.startswith("/relationships"):
        permissions = ("relationship.edit",)
    elif path.startswith("/episodes"):
        permissions = (("beats.edit",) if path.endswith("/lock") else ("outline.edit",))
    elif path == "/workflow/submit":
        permissions = None
    else:
        permissions = ("*",)
    if (
        permissions is not None
        and not principal.is_system_admin
        and not set(permissions).intersection(principal.permissions)
    ):
        raise HTTPException(403, "没有执行此操作的权限")
    match = re.match(r"/projects/(\d+)(?:/|$)", path)
    if match and not principal.is_system_admin:
        permission_service = getattr(
            request.app.state, "identity_permission_service", None
        )
        if not isinstance(permission_service, PermissionService):
            raise HTTPException(500, "identity permission service is not configured")
        project_id = match.group(1)
        if not (
            permission_service.can_access_assigned_project(principal, project_id)
            or permission_service.can_access_managed_project(principal, project_id)
        ):
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


def _status_label(value: Any) -> str:
    return {
        "draft": "草稿",
        "locked": "已锁定",
        "brief_locked": "Brief 已锁定",
        "submitted": "待审批",
        "approved": "已批准",
        "returned": "已退回",
        "superseded": "已替代",
    }.get(str(value or ""), str(value or "-"))


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
                f"- **转折**：{beat.get('turn') or '-'}",
                "",
                f"- **结尾钩子**：{beat.get('hook') or '-'}",
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
                f"- **是否经过结构修复**：{'是' if meta.get('repair_attempted') else '否'}",
                "",
                f"- **提示词模板版本**：{meta.get('prompt_template_version') or '-'}",
                "",
                f"- **生成任务 ID**：{meta.get('job_id') or '-'}",
                "",
            ])
    return "\n".join(lines)


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
) -> APIRouter:
    router = APIRouter(
        prefix="/api/creative",
        tags=["creative"],
        dependencies=[Depends(creative_api_authorization)],
    )
    repository = CreativeRepository(Path(runtime_dir) / "creative" / "creative.db")
    workflow_repository = WorkflowRepository(
        IdentityRepository(Path(runtime_dir) / "system" / "system.db")
    )
    workflow_adapter = CreativeWorkflowAdapter(repository, workflow_repository)
    service = TopicService(str(root))
    workspace_root = Path(root).resolve()
    lifecycle_service = TopicLifecycleService(repository, workspace_root, job_manager)
    bootstrap_service = WorkflowBootstrapService(repository, workflow_repository)

    def creative_resource_scope(
        request: Request,
        principal: Principal = Depends(creative_api_principal),
    ) -> None:
        if principal.is_system_admin:
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
            if request.method.upper() != "GET" and task.get("assignee_user_id") != principal.user_id:
                raise HTTPException(403, "只有当前任务执行人可以修改创作资源")
        if project_id is not None:
            permission_service = getattr(
                request.app.state, "identity_permission_service", None
            )
            if not isinstance(permission_service, PermissionService):
                raise HTTPException(500, "identity permission service is not configured")
            if not (
                permission_service.can_access_assigned_project(
                    principal, str(project_id)
                )
                or permission_service.can_access_managed_project(
                    principal, str(project_id)
                )
            ):
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
        if principal.is_system_admin:
            return
        project_key = str(project_id)
        active_member = any(
            member["user_id"] == principal.user_id and member["status"] == "active"
            for member in workflow_repository.list_project_members(project_key)
        )
        if not active_member:
            raise HTTPException(403, "只能在已分配的项目中创建机器任务")
        if "project_owner" in principal.role_codes:
            try:
                workflow_repository.assert_can_manage_project(project_key, principal)
            except PermissionError as exc:
                raise HTTPException(403, "项目负责人只能操作其管理的项目") from exc

    def project_or_404(project_id: int) -> dict[str, Any]:
        workflow_adapter.reconcile_project(project_id)
        project = repository.get_project(project_id)
        if not project:
            raise HTTPException(404, "creative project not found")
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
        if not (
            permission_service.can_access_assigned_project(
                principal, str(project_id)
            )
            or permission_service.can_access_managed_project(
                principal, str(project_id)
            )
        ):
            raise HTTPException(403, "只能访问当前分配或管理的项目")

    def project_view(project: dict[str, Any], principal: Principal) -> dict[str, Any]:
        if principal.is_system_admin:
            return project
        approved_statuses = {"approved", "locked", "brief_locked"}
        stage_permissions = {
            "blueprints": (("brief.read", "brief.read.approved"), "brief.read"),
            "characters": (("character.read", "character.read.approved"), "character.read"),
            "relationships": (("relationship.read", "relationship.read.approved"), "relationship.read"),
            "outline": (("outline.read", "outline.read.approved"), "outline.read"),
            "beats": (("beats.read",), "beats.read"),
        }

        def resource_identity(key: str, item: dict[str, Any]) -> tuple[str, str]:
            if key == "blueprints":
                return "brief", str(item["id"])
            if key in {"characters", "relationships"}:
                return "characters", f"{project['id']}:{item['batch_id']}"
            if item["stage"] == "outline":
                return "outline", f"{project['id']}:{item['batch_id']}"
            return "beats", str(item["id"])

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
        project_or_404(project_id)
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
        required_permission = (
            "job.create.writing"
            if action in {"outlines", "beats"}
            else "job.create.creative"
        )
        if (
            principal is not None
            and not principal.is_system_admin
            and required_permission not in principal.permissions
        ):
            raise HTTPException(403, "没有创建此机器任务的权限")
        assert_project_generation_access(project_id, principal)
        workflow_task_id = str(extra.get("workflow_task_id") or "") or None
        payload = {
            "platform": "creative",
            "crawler_type": "phase4_generate",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "action": action,
                "project_id": project_id,
                "created_by_user_id": (
                    principal.user_id if principal is not None else None
                ),
                "workflow_task_id": workflow_task_id,
                "db_path": str(repository.db_path.resolve()),
                "runtime_dir": str(Path(runtime_dir).resolve()),
                **extra,
            },
        }
        try:
            state = job_manager.create_job(payload)
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

    def validate_task(
        task_id: str | None,
        resource_type: str,
        principal: Principal | None,
        *,
        project_id: int | None = None,
        topic_id: int | None = None,
    ) -> str:
        if principal is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        resolved_task_id = task_id
        if not resolved_task_id:
            candidates = [
                task
                for task in workflow_repository.list_tasks(principal)
                if task["task_type"] == resource_type
                and task.get("assignee_user_id") == principal.user_id
                and task["status"] in {"assigned", "in_progress", "returned"}
                and (
                    project_id is None
                    or str(task.get("project_id") or "") == str(project_id)
                )
                and (topic_id is None or task.get("topic_id") == topic_id)
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
            repository.upsert_topics(service.generate(payload.platform, payload.date, payload.limit))
            topics = repository.list_topics(platform=payload.platform, limit=payload.limit)
            return {"status": "success", "items": lifecycle_service.enrich_topics(topics)}
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @router.get("/topics")
    async def list_topics(
        principal: Principal = Depends(current_principal),
        platform: str = "",
        status: str = "",
        category: str = "",
        min_score: float | None = Query(None, ge=0, le=100),
        max_score: float | None = Query(None, ge=0, le=100),
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        lifecycle_status: str = "",
        limit: int = Query(50, ge=1, le=500),
    ):
        approved_context_only = (
            not principal.is_system_admin and "topic.read" not in principal.permissions
        )
        if approved_context_only:
            status = "adopted"
            has_locked_breakdown = True
        topics = repository.list_topics(
            platform=platform,
            status=status,
            category=category,
            min_score=min_score,
            max_score=max_score,
            captured_date=captured_date,
            has_locked_breakdown=has_locked_breakdown,
            limit=500 if lifecycle_status else limit,
        )
        items = lifecycle_service.enrich_topics(topics)
        for item in items:
            if item.get("latest_breakdown_id"):
                workflow_adapter.reconcile_resource(
                    "breakdown", str(item["latest_breakdown_id"])
                )
        if any(item.get("latest_breakdown_id") for item in items):
            topics = repository.list_topics(
                platform=platform,
                status=status,
                category=category,
                min_score=min_score,
                max_score=max_score,
                captured_date=captured_date,
                has_locked_breakdown=has_locked_breakdown,
                limit=500 if lifecycle_status else limit,
            )
            items = lifecycle_service.enrich_topics(topics)
        if lifecycle_status:
            items = [item for item in items if item.get("lifecycle_status") == lifecycle_status]
        return {"items": items[:limit]}

    @router.get("/topics/workflow-candidates")
    async def workflow_candidates(
        _: Principal = Depends(require_permission("project.manage")),
    ):
        topics = repository.list_topics(status="pending_review", limit=500)
        return {
            "items": [
                {
                    key: topic.get(key)
                    for key in (
                        "id", "title", "platform", "category", "status", "captured_at"
                    )
                }
                for topic in topics
            ]
        }

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
            raise HTTPException(403, "只能读取已批准的选题上下文")
        return topic

    @router.get("/topics/{topic_id}/transcripts")
    async def preview_topic_transcripts(
        topic_id: int,
        _: Principal = Depends(require_permission("drama.transcript.read")),
    ):
        topic = repository.get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        bundle = find_transcript_bundle(workspace_root, topic["title"], topic.get("platform") or "")
        episodes = bundle.get("episodes") or []
        if not episodes:
            raise HTTPException(404, "该剧目暂无可预览的转写")
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
        principal: Principal = Depends(current_principal),
    ):
        if payload.status == "adopted":
            raise HTTPException(
                409, "topic adoption requires an approved topic_decision workflow"
            )
        try:
            topic = repository.update_topic(topic_id, payload.status, payload.decision_note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not topic:
            raise HTTPException(404, "topic not found")
        return lifecycle_service.enrich_topic(topic_id)

    @router.post("/topics/{topic_id}/workflow")
    async def bootstrap_topic_workflow(
        topic_id: int,
        request: Request,
        payload: TopicWorkflowBootstrapRequest | None = None,
        principal: Principal = Depends(current_principal),
    ):
        bootstrap_service.audit_service = getattr(request.app.state, "audit_service", None)
        try:
            return bootstrap_service.ensure_topic_workflow(
                topic_id,
                principal,
                request,
                project_id=payload.project_id if payload else None,
            )
        except KeyError as exc:
            raise HTTPException(404, "topic not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

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
                "db_path": str(repository.db_path.resolve()),
                "runtime_dir": str(Path(runtime_dir).resolve()),
                "workspace_root": str(workspace_root),
                "transcript_coverage": coverage,
                "workflow_task_id": workflow_task_id,
                "created_by_user_id": (
                    principal.user_id if principal is not None else None
                ),
                "project_id": project_id,
            },
        }
        try:
            state = job_manager.create_job(job_payload)
        except JobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
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
        force: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        if not principal.is_system_admin or not force:
            raise HTTPException(410, "请提交版本并由项目负责人审批")
        try:
            workflow_adapter.audit_legacy_force(
                "breakdown", str(breakdown_id), principal
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        breakdown = repository.lock_breakdown(breakdown_id)
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.post("/projects")
    async def create_project(
        payload: ProjectCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = validate_task(
                payload.workflow_task_id,
                "brief",
                principal,
                topic_id=payload.topic_id,
            )
            breakdown = repository.get_breakdown(payload.breakdown_id)
            if breakdown is None or breakdown.get("status") != "approved":
                raise HTTPException(
                    409, "project creation requires an approved breakdown"
                )
            task = workflow_repository.get_task(workflow_task_id)
            if task is None:
                raise HTTPException(404, "workflow task not found")
            project = repository.get_project_by_creation_workflow_task_id(
                workflow_task_id
            )
            bound_id = task.get("project_id")
            is_existing_project = project is not None or bound_id is not None
            if bound_id is not None:
                try:
                    bound_project_id = int(bound_id)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        409, "workflow task has an invalid project binding"
                    ) from exc
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
            if project is None and task.get("project_id") is not None:
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
                    workflow_task_id=workflow_task_id,
                    created_by=principal.user_id,
                )
            if bound_id is None:
                try:
                    workflow_repository.bind_workflow_project(
                        workflow_task_id,
                        str(project["id"]),
                        principal,
                    )
                except Exception as exc:
                    raise HTTPException(
                        503,
                        "project created but workflow binding requires reconciliation",
                    ) from exc
            resolved_project = repository.get_project(int(project["id"])) or project
            if is_existing_project:
                assert_project_read_access(
                    request, int(resolved_project["id"]), principal
                )
                return project_view(resolved_project, principal)
            return resolved_project
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects")
    async def list_projects(
        request: Request,
        topic_id: int | None = None,
        principal: Principal = Depends(current_principal),
    ):
        items = repository.list_projects(topic_id=topic_id)
        for item in items:
            task_id = str(item.get("creation_workflow_task_id") or "")
            if task_id and item.get("created_by") == principal.user_id:
                try:
                    task = workflow_repository.get_task(task_id)
                    if task is None:
                        raise ValueError("workflow task not found")
                    if task["task_type"] != "brief":
                        repository.clear_project_creation_task(
                            int(item["id"]), task_id
                        )
                        continue
                    bound_id = task.get("project_id")
                    if bound_id is None:
                        workflow_repository.bind_workflow_project(
                            task_id, str(item["id"]), principal
                        )
                    elif int(bound_id) != int(item["id"]):
                        repository.reconcile_project_workflow_task(
                            task_id, int(bound_id)
                        )
                except Exception as exc:
                    raise HTTPException(
                        503, "project workflow reconciliation failed"
                    ) from exc
        items = repository.list_projects(topic_id=topic_id)
        for item in items:
            workflow_adapter.reconcile_project(int(item["id"]))
        items = repository.list_projects(topic_id=topic_id)
        if not principal.is_system_admin:
            permission_service = getattr(
                request.app.state, "identity_permission_service", None
            )
            items = [
                item
                for item in items
                if isinstance(permission_service, PermissionService)
                and (
                    permission_service.can_access_assigned_project(
                        principal, str(item["id"])
                    )
                    or permission_service.can_access_managed_project(
                        principal, str(item["id"])
                    )
                )
            ]
        return {"items": items}

    @router.get("/projects/{project_id}")
    async def get_project(
        project_id: int,
        principal: Principal = Depends(current_principal),
    ):
        return project_view(project_or_404(project_id), principal)

    @router.patch("/projects/{project_id}")
    async def update_project(
        project_id: int,
        payload: ProjectUpdateRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            project = project_or_404(project_id)
            if (
                not principal.is_system_admin
                and project.get("created_by") != principal.user_id
            ):
                raise HTTPException(403, "只能修改本人创建的项目草稿")
            return repository.update_project(project_id, payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

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
        return create_generation_job(
            project_id,
            "blueprints",
            principal,
            request,
            count=payload.count,
            workflow_task_id=workflow_task_id,
        )

    @router.patch("/blueprints/{blueprint_id}")
    async def update_blueprint(blueprint_id: int, payload: BlueprintUpdateRequest):
        current = repository.get_blueprint(blueprint_id)
        if not current:
            raise HTTPException(404, "blueprint not found")
        inputs = repository.get_project_inputs(int(current["project_id"]))
        risk = assess_similarity_risk(inputs["breakdown"].get("content") or {}, payload.content)
        try:
            workflow_adapter.assert_editable("brief", str(blueprint_id))
            return repository.update_blueprint(
                blueprint_id,
                payload.content,
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
            return repository.create_blueprint_version(
                blueprint_id, workflow_task_id=workflow_task_id
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/blueprints/{blueprint_id}/lock-brief")
    async def lock_blueprint_brief(
        blueprint_id: int,
        payload: BriefLockRequest,
        force: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        if not principal.is_system_admin or not force:
            raise HTTPException(410, "请提交版本并由项目负责人审批")
        try:
            workflow_adapter.audit_legacy_force(
                "brief", str(blueprint_id), principal
            )
            return repository.lock_brief(blueprint_id, payload.difference_note)
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
        )

    @router.post("/projects/{project_id}/characters")
    async def create_project_characters(
        project_id: int,
        payload: CharacterBatchRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = resolve_task_for_creation(
                payload.workflow_task_id,
                "characters",
                principal,
                project_id=project_id,
            )
            return repository.create_character_batch(
                project_id,
                payload.characters,
                payload.relationships,
                workflow_task_id=workflow_task_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/characters/{character_id}")
    async def update_character(character_id: int, payload: CreativeItemUpdateRequest):
        try:
            resource_id = batch_resource_for("creative_characters", character_id)
            if resource_id:
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
        force: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        if not principal.is_system_admin or not force:
            raise HTTPException(410, "请提交版本并由项目负责人审批")
        try:
            workflow_adapter.audit_legacy_force(
                "characters", f"{project_id}:{batch_id}", principal
            )
            return repository.lock_character_batch(project_id, batch_id)
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
        )

    @router.post("/projects/{project_id}/outlines")
    async def create_project_outlines(
        project_id: int,
        payload: OutlineBatchRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = validate_task(
                payload.workflow_task_id,
                "outline",
                principal,
                project_id=project_id,
            )
            return repository.create_outline_batch(
                project_id,
                payload.episodes,
                payload.series_arc,
                workflow_task_id=workflow_task_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/episodes/{episode_id}")
    async def update_episode(episode_id: int, payload: CreativeItemUpdateRequest):
        try:
            episode = repository.get_episode(episode_id)
            if episode:
                resource_type = "beats" if episode["stage"] == "beats" else "outline"
                resource_id = str(episode_id) if resource_type == "beats" else f"{episode['project_id']}:{episode['batch_id']}"
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
        force: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        if not principal.is_system_admin or not force:
            raise HTTPException(410, "请提交版本并由项目负责人审批")
        try:
            workflow_adapter.audit_legacy_force(
                "outline", f"{project_id}:{batch_id}", principal
            )
            return repository.lock_outline_batch(project_id, batch_id)
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
        )

    @router.post("/projects/{project_id}/episodes/{episode_number}/beats")
    async def create_project_episode_beats(
        project_id: int,
        episode_number: int,
        payload: EpisodeBeatsRequest,
        principal: Principal = Depends(current_principal),
    ):
        try:
            workflow_task_id = validate_task(
                payload.workflow_task_id,
                "beats",
                principal,
                project_id=project_id,
            )
            return repository.create_episode_beats(
                project_id,
                episode_number,
                payload.content,
                workflow_task_id=workflow_task_id,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/episodes/{episode_id}/lock")
    async def lock_episode_beats(
        episode_id: int,
        force: bool = False,
        principal: Principal = Depends(current_principal),
    ):
        if not principal.is_system_admin or not force:
            raise HTTPException(410, "请提交版本并由项目负责人审批")
        try:
            workflow_adapter.audit_legacy_force("beats", str(episode_id), principal)
            return repository.lock_episode_beats(episode_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/workflow/submit")
    async def submit_creative_resource(
        payload: CreativeSubmitRequest,
        principal: Principal = Depends(current_principal),
    ):
        permission_prefix = {
            "characters": "character",
        }.get(payload.resource_type, payload.resource_type)
        required_permission = f"{permission_prefix}.submit"
        if (
            not principal.is_system_admin
            and required_permission not in principal.permissions
        ):
            raise HTTPException(403, "没有提交此创作阶段的权限")
        try:
            return workflow_adapter.submit(
                payload.workflow_task_id,
                payload.resource_type,
                payload.resource_id,
                principal,
            )
        except KeyError as exc:
            raise HTTPException(404, "creative resource or workflow task not found") from exc
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
) -> None:
    app.include_router(build_creative_router(root=root, runtime_dir=runtime_dir, job_manager=job_manager))
