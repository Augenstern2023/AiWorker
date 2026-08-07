# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/scheduler\api.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from identity.dependencies import require_page_api
from identity.schema import Principal
from scheduler.local_service import ScheduleDefinition


class SchedulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    job_type: Literal["crawl", "ranking_update", "asset_download", "weekly_report"]
    payload: dict[str, Any]
    trigger_type: Literal["cron", "date"]
    trigger_config: dict[str, Any]
    timezone: str = "Asia/Shanghai"
    is_enabled: bool = True
    max_retries: int = Field(default=3, ge=0, le=3)
    max_concurrent_runs: int = Field(default=1, ge=1, le=8)


class ScheduleUpdate(SchedulePayload):
    """Full editable schedule definition; ID and creator remain immutable."""


def build_schedule_router() -> APIRouter:
    router = APIRouter()

    def resources(request: Request):
        service = getattr(request.app.state, "local_schedule_service", None)
        repository = getattr(request.app.state, "schedule_repository", None)
        if service is None or repository is None:
            raise HTTPException(503, "PostgreSQL 调度服务未就绪")
        return service, repository

    admin = require_page_api()

    def audit(request: Request, principal: Principal, action: str, identifier: str, after: Any) -> None:
        request.app.state.audit_service.record(actor=principal, action=action, target_type="schedule", target_id=identifier, before=None, after=after, reason=None, result="success")

    @router.get("/api/admin/schedules")
    def list_schedules(request: Request, _: Principal = Depends(admin)):
        _, repo = resources(request)
        return [item.__dict__ for item in repo.list_definitions()]

    @router.post("/api/admin/schedules", status_code=201)
    def create_schedule(payload: SchedulePayload, request: Request, principal: Principal = Depends(admin)):
        service, repo = resources(request)
        definition = ScheduleDefinition(id=str(uuid.uuid4()), name=payload.name, job_type=payload.job_type, payload=payload.payload, trigger_type=payload.trigger_type, trigger_config=payload.trigger_config, timezone=payload.timezone, is_enabled=payload.is_enabled, max_retries=payload.max_retries, max_concurrent_runs=payload.max_concurrent_runs, created_by=principal.user_id, updated_by=principal.user_id)
        try:
            service.validate_definition(definition)
            repo.create_definition(definition)
            if definition.is_enabled and service._started:
                service._schedule_definition(definition)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(request, principal, "schedule.create", definition.id, definition.__dict__)
        return definition.__dict__

    @router.patch("/api/admin/schedules/{schedule_id}")
    def update_schedule(schedule_id: str, payload: ScheduleUpdate, request: Request, principal: Principal = Depends(admin)):
        service, repo = resources(request)
        try:
            before = repo.get_definition(schedule_id)
        except (KeyError, LookupError) as exc:
            raise HTTPException(404, "调度计划不存在") from exc
        updated = ScheduleDefinition(
            id=before.id,
            name=payload.name,
            job_type=payload.job_type,
            payload=payload.payload,
            trigger_type=payload.trigger_type,
            trigger_config=payload.trigger_config,
            timezone=payload.timezone,
            is_enabled=payload.is_enabled,
            max_retries=payload.max_retries,
            max_concurrent_runs=payload.max_concurrent_runs,
            created_by=before.created_by,
            updated_by=principal.user_id,
        )
        try:
            service.validate_definition(updated)
            updated = repo.update_definition(schedule_id, updated)
            if service._started:
                try:
                    service.scheduler.remove_job(f"local-schedule:{schedule_id}")
                except Exception:
                    pass
                if updated.is_enabled:
                    service._schedule_definition(updated)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(request, principal, "schedule.update", schedule_id, updated.__dict__)
        return updated.__dict__

    @router.post("/api/admin/schedules/{schedule_id}/enabled")
    def set_enabled(schedule_id: str, enabled: bool, request: Request, principal: Principal = Depends(admin)):
        service, repo = resources(request)
        try:
            definition = repo.get_definition(schedule_id)
        except Exception as exc:
            raise HTTPException(404, "调度计划不存在") from exc
        repo.set_definition_enabled(schedule_id, enabled)
        definition = repo.get_definition(schedule_id)
        if enabled and service._started:
            service._schedule_definition(definition)
        elif not enabled and service._started:
            try:
                service.scheduler.remove_job(f"local-schedule:{schedule_id}")
            except Exception:
                pass
        audit(request, principal, "schedule.resume" if enabled else "schedule.pause", schedule_id, {"is_enabled": enabled})
        return {"is_enabled": enabled}

    @router.delete("/api/admin/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: str, request: Request, principal: Principal = Depends(admin)):
        service, repo = resources(request)
        if service._started:
            try: service.scheduler.remove_job(f"local-schedule:{schedule_id}")
            except Exception: pass
        try: repo.delete_definition(schedule_id)
        except KeyError as exc: raise HTTPException(404, "调度计划不存在") from exc
        audit(request, principal, "schedule.delete", schedule_id, None)
        return Response(status_code=204)

    @router.post("/api/admin/schedules/{schedule_id}/trigger")
    def trigger(schedule_id: str, request: Request, principal: Principal = Depends(admin)):
        service, _ = resources(request)
        try: run = service.trigger(schedule_id)
        except (KeyError, ValueError) as exc: raise HTTPException(422, str(exc)) from exc
        audit(request, principal, "schedule.trigger", schedule_id, run.__dict__)
        return run.__dict__

    @router.post("/api/admin/schedule-runs/{run_id}/retry")
    def retry(run_id: str, request: Request, principal: Principal = Depends(admin)):
        service, _ = resources(request)
        try: run = service.trigger_retry(run_id)
        except (KeyError, ValueError) as exc: raise HTTPException(422, str(exc)) from exc
        audit(request, principal, "schedule.retry", run.schedule_id, run.__dict__)
        return run.__dict__

    @router.post("/api/admin/schedule-runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request, principal: Principal = Depends(admin)):
        service, _ = resources(request)
        try:
            run = service.cancel_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "调度运行不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(request, principal, "schedule.cancel_run", run.schedule_id, run.__dict__)
        return run.__dict__

    @router.get("/api/admin/schedules/{schedule_id}/runs")
    def runs(schedule_id: str, request: Request, _: Principal = Depends(admin)):
        _, repo = resources(request)
        return [item.__dict__ for item in repo.list_runs(schedule_id)]

    @router.get("/api/admin/schedules/{schedule_id}/alerts")
    def alerts(schedule_id: str, request: Request, _: Principal = Depends(admin)):
        _, repo = resources(request)
        return [item.__dict__ for item in repo.list_alerts(schedule_id)]
    return router
