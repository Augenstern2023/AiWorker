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

from creative.free_mode_service import FreeCreativeModeService
from creative.project_prompt_service import ProjectPromptService
from creative.repository import CreativeRepository
from creative.script_service import validate_beats, validate_script
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.schema import Principal
from runtime.job_manager import JobConflictError, JobNotFoundError
from screenplay_review.service import build_project_source, source_fingerprint

from .service import content_fingerprint, validate_optimization_request


class OptimizationFindingRequest(BaseModel):
    ref: str
    episode_numbers: list[int] = Field(default_factory=list)


class OptimizationCreateRequest(BaseModel):
    mode: str
    findings: list[OptimizationFindingRequest] = Field(default_factory=list)
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


class OptimizationRejectRequest(BaseModel):
    rejection_note: str = ""


class OptimizationDraftAcceptRequest(BaseModel):
    content: dict[str, Any] | None = None
    lock_after_accept: bool = True


class OptimizationDraftUpdateRequest(BaseModel):
    content: dict[str, Any]


def build_script_optimization_router(
    repository: CreativeRepository,
    job_manager: Any,
    *,
    runtime_dir: str | Path = ".runtime",
) -> APIRouter:
    router = APIRouter(tags=["script-optimizations"])
    prompt_service = ProjectPromptService(repository)

    def assert_prompt_access(record: dict[str, Any], principal: Principal) -> None:
        if not prompt_service.can_access_review_prompt(record, principal.user_id):
            raise HTTPException(403, "无权访问或使用该记录的个人模板快照")

    def assert_permission(principal: Principal, permission: str) -> None:
        if not principal.is_system_admin and permission not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")

    def can_access_project(request: Request, principal: Principal, project_id: int) -> bool:
        if principal.is_system_admin:
            return True
        service = getattr(request.app.state, "identity_permission_service", None)
        workflow_identity = getattr(
            request.app.state, "workflow_identity_resolver", None
        )
        resolver = getattr(workflow_identity, "project_id", None)
        try:
            scoped_project_id = (
                str(resolver(project_id))
                if callable(resolver)
                else repository.workflow_project_id(project_id)
            )
        except (TypeError, ValueError):
            scoped_project_id = repository.workflow_project_id(project_id)
        return isinstance(service, PermissionService) and (
            service.can_access_assigned_project(principal, scoped_project_id)
            or service.can_access_managed_project(principal, scoped_project_id)
        )

    def get_review_or_404(request: Request, principal: Principal, review_id: int) -> dict[str, Any]:
        review = repository.get_script_review(review_id)
        if not review:
            raise HTTPException(404, "审核记录不存在")
        assert_prompt_access(review, principal)
        project_id = review.get("project_id")
        if project_id is None:
            if principal.is_system_admin or review.get("created_by") == principal.user_id:
                return review
            raise HTTPException(403, "没有访问此审核记录的权限")
        if not can_access_project(request, principal, int(project_id)):
            raise HTTPException(403, "没有访问此审核记录的权限")
        return review

    def assert_fresh_review(review: dict[str, Any]) -> dict[str, Any]:
        if not bool((review.get("source_payload") or {}).get("formal")):
            raise HTTPException(409, "该审核缺少完整分集上下文，请重新发起正式项目审核")
        project = repository.get_project(int(review.get("project_id") or 0))
        if not project:
            raise HTTPException(409, "创作项目已不存在")
        try:
            source = build_project_source(
                project, str(review["mode"]), [int(item) for item in review["episode_numbers"]]
            )
        except ValueError as exc:
            raise HTTPException(409, "剧本已更新，请重新审核后再生成优化草稿") from exc
        if source_fingerprint(source) != review["source_fingerprint"]:
            raise HTTPException(409, "剧本已更新，请重新审核后再生成优化草稿")
        return project

    def latest_episode(project: dict[str, Any], stage: str, episode_number: int) -> dict[str, Any] | None:
        if str(project.get("workflow_mode") or "sequential") == "free":
            head = next(
                (
                    item
                    for item in project.get("resource_heads") or []
                    if str(item.get("stage") or "") == stage
                    and int(item.get("episode_number") or 0) == int(episode_number)
                ),
                None,
            )
            if head is None:
                return None
            return next(
                (
                    item
                    for item in project.get("episodes") or []
                    if item.get("stage") == stage
                    and int(item.get("episode_number") or 0) == int(episode_number)
                    and str(item.get("id") or "")
                    == str(head.get("resource_ref") or "")
                ),
                None,
            )
        candidates = [
            item for item in project.get("episodes") or []
            if item.get("stage") == stage and int(item.get("episode_number") or 0) == episode_number
        ]
        return max(
            candidates,
            key=lambda item: (int(item.get("version") or 0), int(item.get("id") or 0)),
            default=None,
        )

    def free_head(
        project: dict[str, Any], stage: str, episode_number: int
    ) -> dict[str, Any]:
        head = next(
            (
                item
                for item in project.get("resource_heads") or []
                if str(item.get("stage") or "") == stage
                and int(item.get("episode_number") or 0) == int(episode_number)
            ),
            None,
        )
        if head is None:
            raise ValueError(f"第 {episode_number} 集尚未选择当前 {stage} 版本")
        return head

    def accepted_generation_meta(
        optimization: dict[str, Any], draft_id: int
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "source": "script_optimization",
            "optimization_id": optimization["id"],
            "draft_id": draft_id,
        }
        snapshot = dict(optimization.get("prompt_snapshot") or {})
        if snapshot:
            meta.update({
                "prompt_snapshot": snapshot,
                "prompt_template_id": str(snapshot.get("template_id") or ""),
                "prompt_template_key": str(snapshot.get("template_key") or ""),
                "prompt_template_revision": int(snapshot.get("revision") or 0),
                "prompt_content_hash": str(snapshot.get("content_hash") or ""),
            })
        return meta

    def optimization_job_payload(
        optimization: dict[str, Any], *, actor_user_id: str
    ) -> dict[str, Any]:
        prompt_snapshot = dict(optimization.get("prompt_snapshot") or {})
        return {
            "platform": "creative",
            "crawler_type": "screenplay_optimization",
            "keywords": [],
            "save_data_option": "json",
            "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "optimization_id": optimization["id"],
                "review_id": optimization["review_id"],
                "project_id": optimization["project_id"],
                "created_by_user_id": actor_user_id,
                "runtime_dir": str(Path(runtime_dir).resolve()),
                "prompt_snapshot": prompt_snapshot,
                "prompt_template_id": str(prompt_snapshot.get("template_id") or ""),
                "prompt_template_key": str(prompt_snapshot.get("template_key") or ""),
                "prompt_template_revision": int(prompt_snapshot.get("revision") or 0),
                "prompt_content_hash": str(prompt_snapshot.get("content_hash") or ""),
            },
        }

    def create_optimization_job(
        optimization: dict[str, Any], *, actor_user_id: str
    ) -> Any:
        return job_manager.create_job(
            optimization_job_payload(optimization, actor_user_id=actor_user_id)
        )

    @router.post("/api/script-reviews/{review_id}/optimizations", status_code=201)
    async def create_optimization(
        review_id: int,
        payload: OptimizationCreateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.generate")
        review = get_review_or_404(request, principal, review_id)
        try:
            request_data = validate_optimization_request(
                review, payload.mode, [item.model_dump() for item in payload.findings]
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        project = assert_fresh_review(review)
        try:
            prompt_snapshot = ProjectPromptService(repository).resolve_generation_snapshot(
                project_id=int(project["id"]),
                stage="optimization",
                series=request_data["mode"] == "structure",
                episode_number=None,
                template_key=payload.prompt_template_key,
                template_revision=payload.prompt_template_revision,
                run_override=payload.prompt_override,
                actor_user_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        optimization = repository.create_script_optimization(
            review_id=review_id,
            project_id=int(project["id"]),
            mode=request_data["mode"],
            source_fingerprint=review["source_fingerprint"],
            selected_findings=request_data["selected_findings"],
            target_episode_numbers=request_data["target_episode_numbers"],
            created_by=principal.user_id,
            prompt_snapshot=prompt_snapshot,
        )
        try:
            state = create_optimization_job(
                optimization, actor_user_id=principal.user_id
            )
        except JobConflictError as exc:
            failed = repository.fail_script_optimization(optimization["id"], str(exc))
            return {"optimization": failed, "job_id": "", "start_error": str(exc)}
        except Exception as exc:
            failed = repository.fail_script_optimization(optimization["id"], str(exc))
            return {
                "optimization": failed,
                "job_id": "",
                "start_error": "无法创建剧本优化任务",
            }
        optimization = repository.set_script_optimization_job(optimization["id"], str(state.job_id))
        return {"optimization": optimization, "job_id": state.job_id}

    @router.get("/api/script-reviews/{review_id}/optimizations")
    async def list_optimizations(
        review_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, list[dict[str, Any]]]:
        assert_permission(principal, "script.optimize.read")
        get_review_or_404(request, principal, review_id)
        return {"items": [
            item for item in repository.list_script_optimizations(review_id)
            if prompt_service.can_access_review_prompt(item, principal.user_id)
        ]}

    @router.get("/api/creative/projects/{project_id}/episodes/{episode_number}/optimization-drafts")
    async def list_episode_drafts(
        project_id: int,
        episode_number: int,
        stage: str,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, list[dict[str, Any]]]:
        assert_permission(principal, "script.optimize.read")
        if stage not in {"beats", "script"}:
            raise HTTPException(400, "stage 必须是 beats 或 script")
        project = repository.get_project(project_id)
        if not project:
            raise HTTPException(404, "创作项目不存在")
        if not can_access_project(request, principal, project_id):
            raise HTTPException(403, "没有访问此创作项目的权限")
        return {
            "items": repository.list_episode_script_optimization_drafts(
                project_id, episode_number, stage
            )
        }

    @router.get("/api/script-optimizations/{optimization_id}")
    async def get_optimization(
        optimization_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.read")
        optimization = repository.get_script_optimization(optimization_id)
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        assert_prompt_access(optimization, principal)
        get_review_or_404(request, principal, int(optimization["review_id"]))
        return optimization

    @router.post("/api/script-optimizations/{optimization_id}/retry")
    async def retry_optimization(
        optimization_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.generate")
        optimization = repository.get_script_optimization(optimization_id)
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        assert_prompt_access(optimization, principal)
        review = get_review_or_404(request, principal, int(optimization["review_id"]))
        if optimization["status"] != "failed":
            raise HTTPException(409, "只有已失败的优化任务可以继续生成")
        assert_fresh_review(review)
        try:
            repository.prepare_script_optimization_resume(optimization_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        try:
            if optimization.get("job_id"):
                try:
                    state = job_manager.retry(str(optimization["job_id"]))
                except JobNotFoundError:
                    state = create_optimization_job(
                        optimization, actor_user_id=principal.user_id
                    )
            else:
                state = create_optimization_job(
                    optimization, actor_user_id=principal.user_id
                )
        except JobConflictError as exc:
            repository.fail_script_optimization(optimization_id, str(exc))
            raise HTTPException(409, "优化任务正在运行或暂不可重试") from exc
        except Exception as exc:
            repository.fail_script_optimization(optimization_id, str(exc))
            raise HTTPException(503, "无法启动优化任务续跑") from exc
        resumed = repository.set_script_optimization_job(optimization_id, str(state.job_id))
        return {"optimization": resumed, "job_id": str(state.job_id)}

    @router.delete("/api/script-optimizations/{optimization_id}")
    async def delete_failed_optimization(
        optimization_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.generate")
        optimization = repository.get_script_optimization(optimization_id)
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        assert_prompt_access(optimization, principal)
        get_review_or_404(request, principal, int(optimization["review_id"]))
        try:
            result = repository.delete_failed_script_optimization(optimization_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deleted": True, **result}

    @router.patch("/api/script-optimization-drafts/{draft_id}")
    async def update_draft(
        draft_id: int,
        payload: OptimizationDraftUpdateRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.accept")
        draft = repository.get_script_optimization_draft(draft_id)
        if not draft:
            raise HTTPException(404, "优化草稿不存在")
        optimization = repository.get_script_optimization(int(draft["optimization_id"]))
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        assert_prompt_access(optimization, principal)
        get_review_or_404(request, principal, int(optimization["review_id"]))
        try:
            content = (
                validate_beats(payload.content, allow_legacy=True)
                if draft["kind"] == "beats"
                else validate_script(payload.content)
            )
            return repository.update_script_optimization_draft_content(
                draft_id, content
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/api/script-optimization-drafts/{draft_id}/regenerate", status_code=201
    )
    async def regenerate_draft(
        draft_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.generate")
        draft = repository.get_script_optimization_draft(draft_id)
        if not draft:
            raise HTTPException(404, "优化草稿不存在")
        source = repository.get_script_optimization(int(draft["optimization_id"]))
        if not source:
            raise HTTPException(404, "优化任务不存在")
        assert_prompt_access(source, principal)
        review = get_review_or_404(request, principal, int(source["review_id"]))
        project = assert_fresh_review(review)
        episode_number = int(draft["episode_number"])
        selected_findings = [
            {
                "ref": str(item.get("ref") or ""),
                "episode_numbers": [episode_number],
            }
            for item in source.get("selected_findings") or []
            if episode_number in {
                int(number) for number in item.get("episode_numbers") or []
            }
        ]
        if not selected_findings:
            raise HTTPException(409, "该集没有可复用的审核问题")
        optimization = repository.create_script_optimization(
            review_id=int(source["review_id"]),
            project_id=int(project["id"]),
            mode=str(source["mode"]),
            source_fingerprint=str(review["source_fingerprint"]),
            selected_findings=selected_findings,
            target_episode_numbers=[episode_number],
            created_by=principal.user_id,
            prompt_snapshot=dict(source.get("prompt_snapshot") or {}),
        )
        try:
            state = create_optimization_job(
                optimization, actor_user_id=principal.user_id
            )
        except JobConflictError as exc:
            failed = repository.fail_script_optimization(optimization["id"], str(exc))
            return {"optimization": failed, "job_id": "", "start_error": str(exc)}
        except Exception as exc:
            failed = repository.fail_script_optimization(optimization["id"], str(exc))
            return {
                "optimization": failed,
                "job_id": "",
                "start_error": "无法创建剧本优化任务",
            }
        optimization = repository.set_script_optimization_job(
            optimization["id"], str(state.job_id)
        )
        return {"optimization": optimization, "job_id": str(state.job_id)}

    @router.get("/api/script-optimization-drafts/{draft_id}")
    async def get_draft(
        draft_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.read")
        draft = repository.get_script_optimization_draft(draft_id)
        if not draft:
            raise HTTPException(404, "优化草稿不存在")
        optimization = repository.get_script_optimization(int(draft["optimization_id"]))
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        get_review_or_404(request, principal, int(optimization["review_id"]))
        return draft

    @router.post("/api/script-optimization-drafts/{draft_id}/accept")
    async def accept_draft(
        draft_id: int,
        request: Request,
        payload: OptimizationDraftAcceptRequest | None = None,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.accept")
        draft = repository.get_script_optimization_draft(draft_id)
        if not draft:
            raise HTTPException(404, "优化草稿不存在")
        optimization = repository.get_script_optimization(int(draft["optimization_id"]))
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        review = get_review_or_404(request, principal, int(optimization["review_id"]))
        project = repository.get_project(int(optimization["project_id"]))
        if not project:
            raise HTTPException(409, "创作项目已不存在")
        try:
            if payload is not None and not payload.lock_after_accept:
                raise ValueError("优化草稿必须锁定并采纳")
            accepted_content = payload.content if payload and payload.content is not None else draft["content"]
            if draft["kind"] == "beats":
                accepted_content = validate_beats(accepted_content, allow_legacy=True)
            free_mode = str(project.get("workflow_mode") or "sequential") == "free"
            free_service = FreeCreativeModeService(repository) if free_mode else None
            created_head: dict[str, Any] | None = None
            if draft["kind"] == "beats":
                build_project_source(project, "single", [int(draft["episode_number"])])
                source = repository.get_episode(int(draft["source_episode_id"] or 0))
                latest = latest_episode(project, "beats", int(draft["episode_number"]))
                if (
                    not source
                    or not latest
                    or int(latest["id"]) != int(source["id"])
                    or content_fingerprint(source["content"]) != draft["source_fingerprint"]
                ):
                    raise ValueError("来源 Beats 已更新，请重新生成优化草稿")
                if free_service is not None:
                    head = free_head(project, "beats", int(draft["episode_number"]))
                    result = free_service.create_episode_resource(
                        int(project["id"]), "beats", int(draft["episode_number"]),
                        accepted_content,
                        expected_head_revision=int(head["revision"]),
                        actor_user_id=principal.user_id,
                        source_refs=dict(source.get("upstream_refs") or {}),
                        generation_meta=accepted_generation_meta(optimization, draft_id),
                    )
                    created = result["resource"]
                    created_head = result["head"]
                else:
                    created = repository.create_episode_beats(
                        int(project["id"]), int(draft["episode_number"]), accepted_content,
                        generation_meta=accepted_generation_meta(optimization, draft_id),
                        update_project_status=False,
                    )
            else:
                source_refs: dict[str, Any] = {}
                if optimization["mode"] == "structure":
                    peer = next((item for item in optimization["drafts"] if item["episode_number"] == draft["episode_number"] and item["kind"] == "beats"), None)
                    if not peer or peer["status"] not in {"locked", "accepted"} or not peer.get("accepted_episode_id"):
                        raise ValueError("请先锁定并采纳对应 Beats 草稿")
                    beats = repository.get_episode(int(peer["accepted_episode_id"]))
                    if not beats or beats.get("status") not in {"locked", "approved"} or content_fingerprint(beats["content"]) != draft["source_fingerprint"]:
                        raise ValueError("对应 Beats 未锁定或已变化，请重新生成场次剧本草稿")
                    if free_mode:
                        current_beats = latest_episode(
                            project, "beats", int(draft["episode_number"])
                        )
                        if not current_beats or int(current_beats["id"]) != int(beats["id"]):
                            raise ValueError("对应 Beats 已不再是当前版本，请重新生成场次剧本草稿")
                    source_refs = dict(beats.get("upstream_refs") or {})
                    source_refs.update({
                        "beats_id": int(beats["id"]),
                        "beats_version": int(beats.get("version") or 1),
                        "beats_content_fingerprint": content_fingerprint(beats["content"]),
                    })
                else:
                    build_project_source(project, "single", [int(draft["episode_number"])])
                    source = repository.get_episode(int(draft["source_episode_id"] or 0))
                    latest = latest_episode(project, "script", int(draft["episode_number"]))
                    if (
                        not source
                        or not latest
                        or int(latest["id"]) != int(source["id"])
                        or content_fingerprint(source["content"]) != draft["source_fingerprint"]
                    ):
                        raise ValueError("来源场次剧本已更新，请重新生成优化草稿")
                    source_refs = dict(source.get("upstream_refs") or {})
                if free_service is not None:
                    head = free_head(project, "script", int(draft["episode_number"]))
                    result = free_service.create_episode_resource(
                        int(project["id"]), "script", int(draft["episode_number"]),
                        accepted_content,
                        expected_head_revision=int(head["revision"]),
                        actor_user_id=principal.user_id,
                        source_refs=source_refs,
                        generation_meta=accepted_generation_meta(optimization, draft_id),
                    )
                    created = result["resource"]
                    created_head = result["head"]
                else:
                    created = repository.create_episode_script(
                        int(project["id"]), int(draft["episode_number"]), accepted_content,
                        generation_meta=accepted_generation_meta(optimization, draft_id),
                        update_project_status=False,
                    )
            if free_service is not None:
                assert created_head is not None
                free_service.lock_resource(
                    project_id=int(project["id"]),
                    stage=str(draft["kind"]),
                    resource_ref=str(created["id"]),
                    episode_number=int(draft["episode_number"]),
                    expected_head_revision=int(created_head["revision"]),
                    actor_user_id=principal.user_id,
                )
                created = repository.get_episode(int(created["id"])) or created
            elif draft["kind"] == "beats":
                created = repository.lock_episode_beats(int(created["id"]))
            else:
                created = repository.lock_episode_script(int(created["id"]))
            return repository.accept_script_optimization_draft(draft_id, int(created["id"]), principal.user_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/script-optimization-drafts/{draft_id}/reject")
    async def reject_draft(
        draft_id: int,
        payload: OptimizationRejectRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_permission(principal, "script.optimize.accept")
        draft = repository.get_script_optimization_draft(draft_id)
        if not draft:
            raise HTTPException(404, "优化草稿不存在")
        optimization = repository.get_script_optimization(int(draft["optimization_id"]))
        if not optimization:
            raise HTTPException(404, "优化任务不存在")
        get_review_or_404(request, principal, int(optimization["review_id"]))
        try:
            return repository.reject_script_optimization_draft(draft_id, payload.rejection_note)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router


def register_script_optimization_routes(
    app: Any,
    *,
    runtime_dir: str | Path = ".runtime",
    job_manager: Any,
    repository: CreativeRepository | None = None,
) -> None:
    if repository is None:
        raise ValueError(
            "register_script_optimization_routes requires an injected "
            "creative repository"
        )
    root = Path(runtime_dir)
    app.include_router(
        build_script_optimization_router(
            repository,
            job_manager,
            runtime_dir=root,
        )
    )
