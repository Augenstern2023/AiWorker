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
from typing import Any, Literal
from urllib.parse import unquote

import config
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from .semif_adapter import SemIfAdapter, SemIfError


class ScriptReviewCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    source_type: str
    mode: str
    project_id: int | None = None
    episode_numbers: list[int] = Field(default_factory=list)
    text: str = ""
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)
    engine: Literal["legacy", "semif"] | None = None
    model_profile: str | None = Field(None, min_length=1, max_length=120)

    @field_validator("model_profile")
    @classmethod
    def normalize_model_profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("model_profile 不能为空")
        return normalized


class ScriptReviewConfirmRequest(BaseModel):
    final_verdict: str
    confirmation_note: str = ""


class ScriptReviewRerunRequest(BaseModel):
    scope: Literal["failed_dimensions_only", "affected_episodes", "full"] = "full"
    request_key: str = Field(min_length=8, max_length=120)

    @field_validator("request_key")
    @classmethod
    def normalize_request_key(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("request_key 至少需要 8 个字符")
        return normalized


_TEXT_SCRIPT_EXTENSIONS = frozenset({
    ".txt", ".text", ".md", ".markdown", ".mdown", ".fountain", ".fd", ".rst",
})
_MAX_SCRIPT_UPLOAD_BYTES = 5 * 1024 * 1024


async def _require_semif_available() -> None:
    """Fail fast before enqueueing an explicitly requested SemIf review."""

    adapter: SemIfAdapter | None = None
    try:
        adapter = SemIfAdapter(
            str(config.SEMIF_BASE_URL),
            timeout_seconds=min(5.0, max(0.1, float(config.SEMIF_TIMEOUT_SECONDS))),
            max_retries=0,
        )
        await adapter.health()
    except (SemIfError, ValueError) as exc:
        raise HTTPException(503, "SemIf 评审服务当前不可用") from exc
    finally:
        if adapter is not None:
            await adapter.close()


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

    def expose_report_fields(review: dict[str, Any]) -> dict[str, Any]:
        """Project stable v2 result fields while retaining the nested report."""
        result = dict(review)
        report = review.get("report") if isinstance(review.get("report"), dict) else {}
        if str(report.get("schema_version") or "") == "screenplay-review-report-v2":
            for key in (
                "quality_status",
                "overall_score",
                "hard_gate",
                "dimensions",
                "blocking_issues",
                "revision_plan",
                "review_version",
                "engine",
                "model",
            ):
                result[key] = report.get(key)
        return result

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
        engine: str | None = None,
        model_profile: str | None = None,
        execution_overrides: dict[str, Any] | None = None,
        preserve_prompt_snapshot: bool = False,
    ) -> dict[str, Any]:
        source = dict(source)
        selected_engine = str(engine or config.SCREENPLAY_REVIEW_ENGINE or "legacy")
        if selected_engine not in {"legacy", "semif"}:
            raise ValueError("engine 必须是 legacy 或 semif")
        selected_profile = (
            str(model_profile or config.SEMIF_MODEL_PROFILE or "local-default").strip()
            or "local-default"
        )
        execution_snapshot = {
            "engine": selected_engine,
            "model_profile": selected_profile,
            "rule_package": "short-drama-semif-v1" if selected_engine == "semif" else "legacy",
        }
        execution_snapshot.update(dict(execution_overrides or {}))
        source["execution_snapshot"] = execution_snapshot
        if source_type == "project" and not preserve_prompt_snapshot:
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
        create_once = getattr(repository, "create_script_review_rerun_once", None)
        if (
            callable(create_once)
            and int(execution_snapshot.get("parent_review_id") or 0) > 0
            and str(execution_snapshot.get("request_key") or "")
        ):
            review, created = create_once(
                source_type=source_type,
                mode=str(source["mode"]),
                project_id=source.get("project_id"),
                episode_numbers=source["episode_numbers"],
                source_fingerprint=source["fingerprint"],
                source_payload=source,
                created_by=principal.user_id,
                parent_review_id=int(execution_snapshot["parent_review_id"]),
                request_key=str(execution_snapshot["request_key"]),
                rerun_scope=str(execution_snapshot.get("rerun_scope") or "full"),
            )
            if not created:
                return {
                    "review": expose_report_fields(review),
                    "job_id": str(review.get("job_id") or ""),
                }
        else:
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
                "review_engine": selected_engine,
                "semif_model_profile": selected_profile if selected_engine == "semif" else "",
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
        return {"review": expose_report_fields(review), "job_id": state.job_id}

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

        selected_engine = str(payload.engine or config.SCREENPLAY_REVIEW_ENGINE or "legacy")
        if selected_engine == "semif":
            await _require_semif_available()

        try:
            return queue_review(
                data["source_type"],
                source,
                principal,
                prompt_template_key=payload.prompt_template_key,
                prompt_template_revision=payload.prompt_template_revision,
                prompt_override=payload.prompt_override,
                engine=payload.engine,
                model_profile=payload.model_profile,
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
        engine = request.headers.get("X-Script-Review-Engine") or None
        profile = request.headers.get("X-SemIf-Model-Profile") or None
        if engine not in {None, "legacy", "semif"}:
            raise HTTPException(400, "engine 必须是 legacy 或 semif")
        if profile is not None and len(profile.strip()) > 120:
            raise HTTPException(400, "model_profile 不能超过 120 个字符")
        if str(engine or config.SCREENPLAY_REVIEW_ENGINE or "legacy") == "semif":
            await _require_semif_available()
        try:
            return queue_review(
                "text",
                build_text_source(text, source_filename=filename),
                principal,
                engine=engine,
                model_profile=profile,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

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
        return {"items": [expose_report_fields(item) for item in items]}

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
        return expose_report_fields(refresh_review(review))

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
        return {"review": expose_report_fields(resumed), "job_id": str(state.job_id)}

    @router.post("/{review_id}/reruns", status_code=201)
    async def rerun_review(
        review_id: int,
        payload: ScriptReviewRerunRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        """Create a new immutable review version; never overwrite the parent report."""
        assert_permission(principal, "script.review.generate")
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        if not can_read_review(request, principal, review):
            raise HTTPException(403, "没有访问此审核记录的权限")
        if bool((review.get("source_payload") or {}).get("audit_scope")):
            raise HTTPException(409, "完整项目审核不属于正式审核流程")
        report = review.get("report") if isinstance(review.get("report"), dict) else {}
        if str(report.get("schema_version") or "") != "screenplay-review-report-v2":
            raise HTTPException(409, "只有 SemIf v2 报告可以创建范围复审")
        if review.get("status") in {"queued", "running", "failed"}:
            raise HTTPException(409, "当前审核尚未形成可复审的最终报告")

        for candidate in repository.list_script_reviews(created_by=principal.user_id):
            execution = dict((candidate.get("source_payload") or {}).get("execution_snapshot") or {})
            if (
                str(execution.get("request_key") or "") == payload.request_key
                and int(execution.get("parent_review_id") or 0) == review_id
            ):
                if str(execution.get("rerun_scope") or "") != payload.scope:
                    raise HTTPException(409, "该幂等键已用于不同的复审范围")
                return {
                    "review": expose_report_fields(candidate),
                    "job_id": str(candidate.get("job_id") or ""),
                }

        frozen = dict(review.get("source_payload") or {})
        source = frozen
        if review.get("source_type") == "project":
            current = current_source(review)
            changed = str(current.get("fingerprint") or "") != str(
                review.get("source_fingerprint") or ""
            )
            if changed and payload.scope != "full":
                raise HTTPException(409, "剧本已更新，局部复审不可复用旧决策，请执行完整复审")
            if changed:
                source = current
                if frozen.get("prompt_snapshot"):
                    source["prompt_snapshot"] = frozen["prompt_snapshot"]

        previous_version = str(report.get("review_version") or "review-v001")
        try:
            version_number = int(previous_version.rsplit("v", 1)[-1]) + 1
        except (TypeError, ValueError):
            version_number = 2
        old_execution = dict(frozen.get("execution_snapshot") or {})
        await _require_semif_available()
        try:
            return queue_review(
                str(review.get("source_type") or "project"),
                source,
                principal,
                engine="semif",
                model_profile=str(
                    old_execution.get("model_profile") or config.SEMIF_MODEL_PROFILE
                ),
                execution_overrides={
                    "parent_review_id": review_id,
                    "review_version": f"review-v{version_number:03d}",
                    "rerun_scope": payload.scope,
                    "request_key": payload.request_key,
                },
                preserve_prompt_snapshot=True,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

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
    repository: CreativeRepository | None = None,
) -> None:
    if repository is None:
        raise ValueError(
            "register_script_review_routes requires an injected creative repository"
        )
    root = Path(runtime_dir)
    app.include_router(
        build_script_review_router(
            repository,
            job_manager,
            runtime_dir=root,
        )
    )
