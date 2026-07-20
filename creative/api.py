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
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from creative.hit_breakdown import breakdown_field_guide, personalize_breakdown_field_guide
from creative.imitation_service import assess_similarity_risk
from creative.repository import CreativeRepository
from creative.schema import (
    BlueprintGenerateRequest,
    BlueprintUpdateRequest,
    BriefLockRequest,
    CharacterBatchRequest,
    CreativeItemUpdateRequest,
    EpisodeBeatsRequest,
    OutlineBatchRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)
from creative.topic_service import TopicService
from creative.transcript_assets import find_transcript_bundle
from runtime.job_manager import JobConflictError


class TopicGenerateRequest(BaseModel):
    platform: str = "dy"
    date: str = ""
    limit: int = 10


class TopicUpdateRequest(BaseModel):
    status: str
    decision_note: str = ""


class BreakdownCreateRequest(BaseModel):
    topic_id: int


class BreakdownUpdateRequest(BaseModel):
    content: dict[str, Any]


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
    router = APIRouter(prefix="/api/creative", tags=["creative"])
    repository = CreativeRepository(Path(runtime_dir) / "creative" / "creative.db")
    service = TopicService(str(root))
    workspace_root = Path(root).resolve()

    def project_or_404(project_id: int) -> dict[str, Any]:
        project = repository.get_project(project_id)
        if not project:
            raise HTTPException(404, "creative project not found")
        return project

    def create_generation_job(project_id: int, action: str, **extra: Any) -> dict[str, Any]:
        project_or_404(project_id)
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
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
                "db_path": str(repository.db_path.resolve()),
                "runtime_dir": str(Path(runtime_dir).resolve()),
                **extra,
            },
        }
        try:
            state = job_manager.create_job(payload)
        except JobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "success", "job": state.to_dict()}

    @router.post("/topics/generate")
    async def generate_topics(payload: TopicGenerateRequest):
        if payload.platform not in {"dy", "hg"}:
            raise HTTPException(400, "platform must be dy or hg")
        try:
            repository.upsert_topics(service.generate(payload.platform, payload.date, payload.limit))
            return {"status": "success", "items": repository.list_topics(platform=payload.platform, limit=payload.limit)}
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    @router.get("/topics")
    async def list_topics(
        platform: str = "",
        status: str = "",
        category: str = "",
        min_score: float | None = Query(None, ge=0, le=100),
        max_score: float | None = Query(None, ge=0, le=100),
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        limit: int = Query(50, ge=1, le=500),
    ):
        return {
            "items": repository.list_topics(
                platform=platform,
                status=status,
                category=category,
                min_score=min_score,
                max_score=max_score,
                captured_date=captured_date,
                has_locked_breakdown=has_locked_breakdown,
                limit=limit,
            )
        }

    @router.get("/topics/{topic_id}")
    async def get_topic(topic_id: int):
        topic = repository.get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        return topic

    @router.get("/topics/{topic_id}/breakdowns")
    async def list_topic_breakdowns(topic_id: int, status: str = "locked"):
        topic = repository.get_topic(topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        if status != "locked":
            raise HTTPException(400, "only locked breakdown versions are available for project creation")
        items = repository.list_locked_breakdowns(topic_id)
        for item in items:
            item["field_guide"] = personalize_breakdown_field_guide(item["content"])
        return {"items": items}

    @router.patch("/topics/{topic_id}")
    async def update_topic(topic_id: int, payload: TopicUpdateRequest):
        try:
            topic = repository.update_topic(topic_id, payload.status, payload.decision_note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not topic:
            raise HTTPException(404, "topic not found")
        return topic

    @router.post("/breakdowns")
    async def create_breakdown(payload: BreakdownCreateRequest):
        topic = repository.get_topic(payload.topic_id)
        if not topic:
            raise HTTPException(404, "topic not found")
        bundle = find_transcript_bundle(workspace_root, topic["title"], topic.get("platform") or "")
        episodes = bundle.get("episodes") or []
        if not episodes:
            raise HTTPException(409, detail={
                "code": "transcript_required",
                "message": "请先下载剧目脚本再拆解",
                "topic_id": payload.topic_id,
                "title": topic["title"],
            })
        if job_manager is None:
            raise HTTPException(503, "creative job manager is unavailable")
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
                "transcript_coverage": [int(item["episode_number"]) for item in episodes],
            },
        }
        try:
            state = job_manager.create_job(job_payload)
        except JobConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "status": "success",
            "job": state.to_dict(),
            "coverage": [int(item["episode_number"]) for item in episodes],
        }

    @router.get("/breakdowns/template")
    async def get_breakdown_template():
        return {"field_guide": breakdown_field_guide()}

    @router.get("/breakdowns/{breakdown_id}")
    async def get_breakdown(breakdown_id: int):
        breakdown = repository.get_breakdown(breakdown_id)
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.patch("/breakdowns/{breakdown_id}")
    async def update_breakdown(breakdown_id: int, payload: BreakdownUpdateRequest):
        try:
            breakdown = repository.update_breakdown(breakdown_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.post("/breakdowns/{breakdown_id}/lock")
    async def lock_breakdown(breakdown_id: int):
        breakdown = repository.lock_breakdown(breakdown_id)
        if not breakdown:
            raise HTTPException(404, "breakdown not found")
        breakdown["field_guide"] = personalize_breakdown_field_guide(breakdown["content"])
        return breakdown

    @router.post("/projects")
    async def create_project(payload: ProjectCreateRequest):
        try:
            return repository.create_project(
                payload.topic_id,
                payload.breakdown_id,
                payload.name,
                payload.target_episode_count,
                genre=payload.genre,
                target_platform=payload.target_platform,
                target_audience=payload.target_audience,
                episode_duration_seconds=payload.episode_duration_seconds,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects")
    async def list_projects(topic_id: int | None = None):
        return {"items": repository.list_projects(topic_id=topic_id)}

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int):
        return project_or_404(project_id)

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdateRequest):
        try:
            return repository.update_project(project_id, payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/blueprints/generate")
    async def generate_project_blueprints(project_id: int, payload: BlueprintGenerateRequest):
        return create_generation_job(project_id, "blueprints", count=payload.count)

    @router.patch("/blueprints/{blueprint_id}")
    async def update_blueprint(blueprint_id: int, payload: BlueprintUpdateRequest):
        current = repository.get_blueprint(blueprint_id)
        if not current:
            raise HTTPException(404, "blueprint not found")
        inputs = repository.get_project_inputs(int(current["project_id"]))
        risk = assess_similarity_risk(inputs["breakdown"].get("content") or {}, payload.content)
        try:
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
    async def create_blueprint_version(blueprint_id: int):
        try:
            return repository.create_blueprint_version(blueprint_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/blueprints/{blueprint_id}/lock-brief")
    async def lock_blueprint_brief(blueprint_id: int, payload: BriefLockRequest):
        try:
            return repository.lock_brief(blueprint_id, payload.difference_note)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/characters/generate")
    async def generate_project_characters(project_id: int):
        project = project_or_404(project_id)
        if not project.get("locked_brief_id"):
            raise HTTPException(409, "必须先锁定 Brief")
        return create_generation_job(project_id, "characters")

    @router.post("/projects/{project_id}/characters")
    async def create_project_characters(project_id: int, payload: CharacterBatchRequest):
        try:
            return repository.create_character_batch(project_id, payload.characters, payload.relationships)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/characters/{character_id}")
    async def update_character(character_id: int, payload: CreativeItemUpdateRequest):
        try:
            result = repository.update_character(character_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "character not found")
        return result

    @router.patch("/relationships/{relationship_id}")
    async def update_relationship(relationship_id: int, payload: CreativeItemUpdateRequest):
        try:
            result = repository.update_relationship(relationship_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "relationship not found")
        return result

    @router.post("/projects/{project_id}/characters/{batch_id}/lock")
    async def lock_project_characters(project_id: int, batch_id: str):
        try:
            return repository.lock_character_batch(project_id, batch_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/outlines/generate")
    async def generate_project_outlines(project_id: int):
        project = project_or_404(project_id)
        if not project.get("character_batch_id"):
            raise HTTPException(409, "必须先锁定人物关系版本")
        return create_generation_job(project_id, "outlines")

    @router.post("/projects/{project_id}/outlines")
    async def create_project_outlines(project_id: int, payload: OutlineBatchRequest):
        try:
            return repository.create_outline_batch(project_id, payload.episodes, payload.series_arc)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/episodes/{episode_id}")
    async def update_episode(episode_id: int, payload: CreativeItemUpdateRequest):
        try:
            result = repository.update_episode(episode_id, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result:
            raise HTTPException(404, "episode not found")
        return result

    @router.post("/projects/{project_id}/outlines/{batch_id}/lock")
    async def lock_project_outlines(project_id: int, batch_id: str):
        try:
            return repository.lock_outline_batch(project_id, batch_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/projects/{project_id}/episodes/{episode_number}/beats/generate")
    async def generate_episode_beats(project_id: int, episode_number: int):
        project = project_or_404(project_id)
        if not project.get("outline_batch_id"):
            raise HTTPException(409, "必须先锁定分集大纲")
        if not 1 <= episode_number <= int(project["target_episode_count"]):
            raise HTTPException(400, "集数超出项目范围")
        return create_generation_job(project_id, "beats", episode_number=episode_number)

    @router.post("/projects/{project_id}/episodes/{episode_number}/beats")
    async def create_project_episode_beats(
        project_id: int,
        episode_number: int,
        payload: EpisodeBeatsRequest,
    ):
        try:
            return repository.create_episode_beats(project_id, episode_number, payload.content)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/episodes/{episode_id}/lock")
    async def lock_episode_beats(episode_id: int):
        try:
            return repository.lock_episode_beats(episode_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/projects/{project_id}/export")
    async def export_project_stage(
        project_id: int,
        stage: str,
        format: str = Query("md", pattern="^(md|json)$"),
    ):
        project = project_or_404(project_id)
        try:
            payload = build_stage_export(project, stage)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if format == "json":
            return JSONResponse(payload)
        if stage == "beats":
            markdown = build_beats_markdown(project, payload["content"])
        else:
            markdown = "\n".join([f"# {project['name']}", "", f"- 阶段：{stage}", ""] + _markdown_value(payload["content"]))
        filename = f"creative-{project_id}-{stage}.md"
        return Response(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router


def register_creative_routes(
    app: FastAPI,
    root: str | Path = ".",
    runtime_dir: str | Path = ".runtime",
    job_manager: Any | None = None,
) -> None:
    app.include_router(build_creative_router(root=root, runtime_dir=runtime_dir, job_manager=job_manager))
