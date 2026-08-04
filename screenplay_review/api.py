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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobConflictError

from .service import (
    build_project_source,
    build_text_source,
    source_fingerprint,
    validate_review_request,
)


class ScriptReviewCreateRequest(BaseModel):
    source_type: str
    mode: str
    project_id: int | None = None
    episode_numbers: list[int] = Field(default_factory=list)
    text: str = ""


class ScriptReviewConfirmRequest(BaseModel):
    final_verdict: str
    confirmation_note: str = ""


def build_script_review_router(
    repository: CreativeRepository,
    job_manager: Any,
    *,
    runtime_dir: str | Path = ".runtime",
) -> APIRouter:
    router = APIRouter(prefix="/api/script-reviews", tags=["script-reviews"])

    def assert_permission(principal: Principal, permission: str) -> None:
        if not principal.is_system_admin and permission not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")

    def can_access_project(
        request: Request, principal: Principal, project_id: int
    ) -> bool:
        if principal.is_system_admin:
            return True
        service = getattr(request.app.state, "identity_permission_service", None)
        if not isinstance(service, PermissionService):
            return False
        return service.can_access_assigned_project(
            principal, str(project_id)
        ) or service.can_access_managed_project(principal, str(project_id))

    def assert_project_access(
        request: Request, principal: Principal, project_id: int
    ) -> None:
        if not can_access_project(request, principal, project_id):
            raise HTTPException(403, "只能访问当前分配或管理的项目")

    def can_read_review(
        request: Request, principal: Principal, review: dict[str, Any]
    ) -> bool:
        project_id = review.get("project_id")
        if project_id is None:
            return principal.is_system_admin or review.get("created_by") == principal.user_id
        return can_access_project(request, principal, int(project_id))

    def current_source(review: dict[str, Any]) -> dict[str, Any]:
        if review["source_type"] == "text":
            return review["source_payload"]
        project_id = int(review.get("project_id") or 0)
        project = repository.get_project(project_id)
        if not project:
            raise HTTPException(409, "创作项目已不存在，无法确认审核")
        return build_project_source(
            project,
            str(review["mode"]),
            [int(number) for number in review["episode_numbers"]],
        )

    def refresh_staleness(review: dict[str, Any]) -> dict[str, Any]:
        if review["source_type"] != "project" or review["status"] != "pending_confirmation":
            return review
        if not bool((review.get("source_payload") or {}).get("formal")):
            return repository.mark_script_review_stale_if_changed(
                review["id"], "__missing_formal_context__"
            )
        try:
            fingerprint = source_fingerprint(current_source(review))
        except (HTTPException, ValueError):
            fingerprint = "__source_unavailable__"
        return repository.mark_script_review_stale_if_changed(review["id"], fingerprint)

    @router.post("", status_code=201)
    async def create_review(
        payload: ScriptReviewCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.generate")
        try:
            data = validate_review_request(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if data["source_type"] == "text":
            source = build_text_source(data["text"])
        else:
            project = repository.get_project(int(data["project_id"]))
            if not project:
                raise HTTPException(404, "创作项目不存在")
            assert_project_access(request, principal, int(project["id"]))
            try:
                source = build_project_source(
                    project, data["mode"], data["episode_numbers"]
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

        review = repository.create_script_review(
            source_type=data["source_type"],
            mode=data["mode"],
            project_id=source.get("project_id"),
            episode_numbers=source["episode_numbers"],
            source_fingerprint=source["fingerprint"],
            source_payload=source,
            created_by=principal.user_id,
        )
        job_payload = {
            "platform": "creative",
            "crawler_type": "screenplay_review",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "review_id": review["id"],
                "project_id": source.get("project_id"),
                "created_by_user_id": principal.user_id,
                "db_path": str(repository.db_path.resolve()),
                "runtime_dir": str(Path(runtime_dir).resolve()),
            },
        }
        try:
            state = job_manager.create_job(job_payload)
        except JobConflictError as exc:
            repository.fail_script_review(review["id"], str(exc))
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            repository.fail_script_review(review["id"], str(exc))
            raise HTTPException(503, "无法创建剧本审核任务") from exc
        review = repository.set_script_review_job(review["id"], str(state.job_id))
        return {"review": review, "job_id": state.job_id}

    @router.get("")
    async def list_reviews(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, list[dict[str, Any]]]:
        assert_permission(principal, "script.review.read")
        candidates = repository.list_script_reviews()
        items = [
            refresh_staleness(review)
            for review in candidates
            if can_read_review(request, principal, review)
        ]
        return {"items": items}

    @router.get("/{review_id}")
    async def get_review(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.read")
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        return refresh_staleness(review)

    @router.post("/{review_id}/confirm")
    async def confirm_review(
        review_id: int,
        payload: ScriptReviewConfirmRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.generate")
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if review["source_type"] != "project" or not bool(
            (review.get("source_payload") or {}).get("formal")
        ):
            raise HTTPException(409, "非正式文本诊断或旧版审核不能人工确认")
        try:
            source = current_source(review)
            review = repository.mark_script_review_stale_if_changed(
                review_id, source_fingerprint(source)
            )
            if review["status"] == "stale":
                raise ValueError("剧本已更新，请重新审核后再确认")
            return repository.confirm_script_review(
                review_id,
                payload.final_verdict,
                payload.confirmation_note,
                principal.user_id,
                source_fingerprint(source),
            )
        except (HTTPException, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return router


def register_script_review_routes(
    app: Any,
    *,
    runtime_dir: str | Path = ".runtime",
    job_manager: Any,
) -> None:
    root = Path(runtime_dir)
    app.include_router(
        build_script_review_router(
            CreativeRepository(root / "creative" / "creative.db"),
            job_manager,
            runtime_dir=root,
        )
    )
