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
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from creative.repository import CreativeRepository
from creative.project_prompt_service import ProjectPromptService
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobConflictError, JobNotFoundError

from .service import (
    build_project_source,
    build_review_markdown,
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
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class ScriptReviewConfirmRequest(BaseModel):
    final_verdict: str
    confirmation_note: str = ""


_TEXT_SCRIPT_EXTENSIONS = frozenset({
    ".txt", ".text", ".md", ".markdown", ".mdown", ".fountain", ".fd", ".rst",
})
_MAX_SCRIPT_UPLOAD_BYTES = 5 * 1024 * 1024


def decode_script_upload(filename: str, content: bytes) -> tuple[str, str]:
    """Validate and decode a plain-text screenplay upload without persisting its file."""
    safe_name = Path(unquote(str(filename or "").strip())).name
    if not safe_name or Path(safe_name).suffix.lower() not in _TEXT_SCRIPT_EXTENSIONS:
        raise ValueError("仅支持文本剧本文件：TXT、MD、Markdown、Fountain、RST")
    if not content:
        raise ValueError("上传的剧本文件为空")
    if len(content) > _MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError("剧本文件不能超过 5 MB")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
        if text:
            return safe_name, text
    raise ValueError("无法识别剧本文件编码，请保存为 UTF-8 或 GB18030 文本文件")


def build_script_review_router(
    repository: CreativeRepository,
    job_manager: Any,
    *,
    runtime_dir: str | Path = ".runtime",
) -> APIRouter:
    router = APIRouter(prefix="/api/script-reviews", tags=["script-reviews"])
    prompt_service = ProjectPromptService(repository)

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
        workflow_identity = getattr(
            request.app.state, "workflow_identity_resolver", None
        )
        resolver = getattr(workflow_identity, "project_id", None)
        try:
            workflow_project_id = (
                str(resolver(project_id))
                if callable(resolver)
                else repository.workflow_project_id(project_id)
            )
        except (TypeError, ValueError):
            workflow_project_id = repository.workflow_project_id(project_id)
        return service.is_active_project_member(principal, workflow_project_id)

    def assert_project_access(
        request: Request, principal: Principal, project_id: int
    ) -> None:
        if not can_access_project(request, principal, project_id):
            raise HTTPException(403, "只有有效项目成员可以发起或访问审核")

    def can_read_review(
        request: Request, principal: Principal, review: dict[str, Any]
    ) -> bool:
        if not prompt_service.can_access_review_prompt(
            review, principal.user_id
        ):
            return False
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

    def refresh_runtime_status(review: dict[str, Any]) -> dict[str, Any]:
        """Keep review history in sync when the worker is cancelled outside its process."""
        if review.get("status") not in {"queued", "running"} or not review.get("job_id"):
            return review
        try:
            job = job_manager.get_job(str(review["job_id"]))
        except JobNotFoundError:
            return repository.cancel_script_review(
                review["id"], "审核任务已不存在"
            )
        except Exception:
            return review
        job_status = str(job.get("status") or "")
        if job_status == "cancelled":
            return repository.cancel_script_review(
                review["id"], str(job.get("message") or "审核任务已取消")
            )
        if job_status == "failed":
            return repository.fail_script_review(
                review["id"], str(job.get("message") or "审核任务执行失败")
            )
        return review

    def refresh_review(review: dict[str, Any]) -> dict[str, Any]:
        return refresh_staleness(refresh_runtime_status(review))

    def queue_review(
        source_type: str,
        source: dict[str, Any],
        principal: Principal,
        *,
        prompt_template_key: str | None = None,
        prompt_template_revision: int | None = None,
        prompt_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = dict(source)
        if source_type == "project":
            prompt_snapshot = prompt_service.resolve_generation_snapshot(
                project_id=int(source["project_id"]),
                stage="review",
                series=str(source["mode"]) != "single",
                episode_number=None,
                template_key=prompt_template_key,
                template_revision=prompt_template_revision,
                run_override=prompt_override,
                actor_user_id=principal.user_id,
            )
            if prompt_snapshot is not None:
                source["prompt_snapshot"] = prompt_snapshot
        review = repository.create_script_review(
            source_type=source_type,
            mode=str(source["mode"]),
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
                "prompt_snapshot": source.get("prompt_snapshot"),
                "prompt_template_id": str(
                    dict(source.get("prompt_snapshot") or {}).get("template_id")
                    or ""
                ),
                "prompt_template_key": str(
                    dict(source.get("prompt_snapshot") or {}).get("template_key")
                    or ""
                ),
                "prompt_template_revision": int(
                    dict(source.get("prompt_snapshot") or {}).get("revision")
                    or 0
                ),
                "prompt_content_hash": str(
                    dict(source.get("prompt_snapshot") or {}).get("content_hash")
                    or ""
                ),
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

    @router.post("", status_code=201)
    async def create_review(
        payload: ScriptReviewCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        try:
            data = validate_review_request(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if data["source_type"] == "text":
            assert_permission(principal, "script.review.generate")
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

        try:
            return queue_review(
                data["source_type"],
                source,
                principal,
                prompt_template_key=payload.prompt_template_key,
                prompt_template_revision=payload.prompt_template_revision,
                prompt_override=payload.prompt_override,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/file", status_code=201)
    async def create_file_review(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.generate")
        try:
            filename, text = decode_script_upload(
                request.headers.get("X-Script-Filename", ""), await request.body()
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return queue_review("text", build_text_source(text, source_filename=filename), principal)

    @router.get("")
    async def list_reviews(
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, list[dict[str, Any]]]:
        candidates = repository.list_script_reviews()
        items = [
            refresh_review(review)
            for review in candidates
            if not bool((review.get("source_payload") or {}).get("audit_scope"))
            and can_read_review(request, principal, review)
        ]
        return {"items": items}

    @router.get("/{review_id}/export")
    async def export_review(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> PlainTextResponse:
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核不属于正式审核流程")
        review = refresh_review(review)
        return PlainTextResponse(
            build_review_markdown(review),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="script-review-{review_id}.md"'
            },
        )

    @router.get("/{review_id}")
    async def get_review(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核不属于正式审核流程")
        return refresh_review(review)

    @router.post("/{review_id}/retry")
    async def retry_review(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.generate")
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核必须从项目审核入口恢复")
        if review.get("status") != "failed" or not review.get("job_id"):
            raise HTTPException(409, "只有已失败且存在原任务的剧本审核可以继续")
        try:
            repository.prepare_script_review_resume(review_id)
            state = job_manager.retry(str(review["job_id"]))
        except (JobConflictError, JobNotFoundError) as exc:
            repository.fail_script_review(review_id, str(exc))
            raise HTTPException(409, "原审核任务尚不可重试或已不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            repository.fail_script_review(review_id, str(exc))
            raise HTTPException(503, "无法启动剧本审核续跑") from exc
        resumed = repository.set_script_review_job(review_id, str(state.job_id))
        return {"review": resumed, "job_id": str(state.job_id)}

    @router.delete("/{review_id}")
    async def delete_review(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.review.generate")
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核不属于正式审核流程")
        review = refresh_review(review)
        if review["status"] in {"queued", "running"}:
            raise HTTPException(409, "审核仍在执行，无法删除")
        repository.delete_script_review(review_id)
        return {"deleted": True, "id": review_id}

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
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核不进入人工确认流程")
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
