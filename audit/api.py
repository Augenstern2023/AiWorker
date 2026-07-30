# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from io import BytesIO, StringIO
from typing import Literal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook

from identity.dependencies import current_principal
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .service import AuditService


def build_audit_router(audit_service: AuditService) -> APIRouter:
    router = APIRouter()
    workflow_repository = WorkflowRepository(
        audit_service.repository.identity_repository
    )

    def project_scope_for(principal: Principal) -> set[str] | None:
        if (
            principal.is_system_admin
            or "project_owner" in principal.role_codes
            or "audit.read" in principal.permissions
        ):
            return None
        if "audit.read.project" not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")
        return workflow_repository.list_managed_project_ids(principal)

    def assert_can_read_audit(
        principal: Principal, project_id: str | None
    ) -> None:
        project_scope = project_scope_for(principal)
        if project_scope is None:
            return
        if project_id is None:
            raise HTTPException(403, "请选择负责的项目后查看审计日志")
        if project_id not in project_scope:
            raise HTTPException(403, "没有访问此项目审计的权限")

    def query_filters(
        *,
        actor_user_id: str | None,
        actor: str | None,
        action: str | None,
        target_type: str | None,
        target_id: str | None,
        project_id: str | None,
        result: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        return {
            "actor_user_id": actor_user_id,
            "actor": actor,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "project_id": project_id,
            "result": result,
            "created_from": created_from,
            "created_to": created_to,
            "limit": limit,
            "offset": offset,
        }

    @router.get("/api/admin/audit")
    def list_audit_events(
        actor_user_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        project_id: str | None = None,
        result: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        response: Response = None,  # type: ignore[assignment]
        principal: Principal = Depends(current_principal),
    ) -> list[dict[str, Any]]:
        assert_can_read_audit(principal, project_id)
        filters = query_filters(
            actor_user_id=actor_user_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
            result=result,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )
        response.headers["X-Total-Count"] = str(audit_service.count(**filters))
        return audit_service.query(**filters)

    @router.get("/api/admin/audit/filter-options")
    def list_audit_filter_options(
        project_id: str | None = None,
        target_type: str | None = None,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        project_scope = project_scope_for(principal)
        if project_scope is not None and project_id is not None:
            assert_can_read_audit(principal, project_id)
        options = audit_service.filter_options(
            project_id=project_id,
            target_type=target_type,
            project_ids=project_scope,
        )
        return {
            **options,
            "project_scope_required": project_scope is not None,
        }

    @router.get("/api/admin/audit/export")
    def export_audit_events(
        format: Literal["csv", "xlsx"] = "csv",
        actor_user_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        project_id: str | None = None,
        result: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = Query(default=500, ge=1, le=500),
        principal: Principal = Depends(current_principal),
    ) -> Response:
        assert_can_read_audit(principal, project_id)
        filters = query_filters(
            actor_user_id=actor_user_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_id=project_id,
            result=result,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=0,
        )
        events = audit_service.query(**filters)
        audit_service.record(
            actor=principal,
            action="audit.export",
            target_type="audit_log",
            target_id=None,
            project_id=project_id,
            before=None,
            after={"format": format, "filters": filters, "exported_count": len(events)},
            reason=None,
            result="success",
        )
        filename = _export_filename(format, project_id, created_from, created_to)
        if format == "csv":
            return Response(
                _audit_csv(events),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return Response(
            _audit_xlsx(events),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/admin/audit/{event_id}")
    def get_audit_event(
        event_id: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        event = audit_service.get(event_id)
        if event is None:
            raise HTTPException(404, "审计记录不存在")
        assert_can_read_audit(principal, event.get("project_id"))
        return event

    return router


_EXPORT_HEADERS = (
    "时间",
    "操作人",
    "操作人 ID",
    "动作",
    "目标类型",
    "目标 ID",
    "项目 ID",
    "结果",
    "原因",
    "变更前摘要",
    "变更后摘要",
    "请求 ID",
    "IP 地址",
    "用户代理",
)


def _export_rows(events: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    for event in events:
        rows.append(
            (
                _export_value(event["created_at"]),
                _export_value(event["actor_display_name"]),
                _export_value(event["actor_user_id"]),
                _export_value(event["action"]),
                _export_value(event["target_type"]),
                _export_value(event["target_id"]),
                _export_value(event["project_id"]),
                _export_value(event["result"]),
                _export_value(event["reason"]),
                _export_json(event["before_summary"]),
                _export_json(event["after_summary"]),
                _export_value(event["request_id"]),
                _export_value(event["ip_address"]),
                _export_value(event["user_agent"]),
            )
        )
    return rows


def _audit_csv(events: list[dict[str, Any]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(_EXPORT_HEADERS)
    writer.writerows(_export_rows(events))
    return output.getvalue().encode("utf-8-sig")


def _audit_xlsx(events: list[dict[str, Any]]) -> bytes:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("审计日志")
    sheet.append(_EXPORT_HEADERS)
    for row in _export_rows(events):
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _export_value(value: Any) -> str:
    return "" if value is None else str(value)


def _export_json(value: Any) -> str:
    return "" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _export_filename(
    format: Literal["csv", "xlsx"],
    project_id: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> str:
    scope = "all" if project_id is None else re.sub(r"[^A-Za-z0-9_-]+", "-", project_id)
    if created_from or created_to:
        scope = f"{scope}-{created_from or 'start'}-{created_to or 'end'}"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"audit-{format}-{scope}-{timestamp}.{format}"
