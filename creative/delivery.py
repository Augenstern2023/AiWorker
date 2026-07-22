# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from audit.service import AuditService
from identity.dependencies import (
    current_principal,
    permission_service_for_request,
)
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .api import _markdown_value, build_beats_markdown
from .repository import CreativeRepository


STAGE_RESOURCE_TYPES = {
    "brief": "brief",
    "characters": "characters",
    "outline": "outline",
    "beats": "beats",
}


class DeliveryCreateRequest(BaseModel):
    project_id: int = Field(gt=0)
    stage: str = Field(pattern="^(brief|characters|outline|beats)$")
    submission_id: str = Field(min_length=1)
    format: str = Field(pattern="^(md|json)$")


class DeliveryService:
    def __init__(
        self,
        *,
        root: str | Path,
        creative_repository: CreativeRepository,
        workflow_repository: WorkflowRepository,
        audit_service: AuditService,
    ) -> None:
        self.root = Path(root).resolve()
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.audit_service = audit_service
        self.delivery_dir = self.root / ".runtime" / "deliveries"
        self.delivery_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.creative_repository._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creative_deliveries (
                    id TEXT PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_version INTEGER NOT NULL,
                    submission_id TEXT NOT NULL,
                    submission_version INTEGER NOT NULL,
                    format TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_creative_deliveries_created "
                "ON creative_deliveries(created_at DESC, id)"
            )

    def create(
        self,
        *,
        project_id: int,
        stage: str,
        submission_id: str,
        format: str,
        actor: Principal,
        request: Request | None = None,
    ) -> dict[str, Any]:
        if not actor.is_system_admin:
            raise PermissionError("only system administrators may create deliveries")
        resource_type = STAGE_RESOURCE_TYPES.get(stage)
        if resource_type is None:
            raise ValueError("stage must be brief, characters, outline, or beats")
        if format not in {"md", "json"}:
            raise ValueError("format must be md or json")
        submission = self.workflow_repository.get_submission(submission_id)
        if submission is None:
            raise KeyError(submission_id)
        if submission["status"] != "approved":
            raise ValueError("delivery requires an approved submission")
        if submission["resource_type"] != resource_type:
            raise ValueError("approved submission does not match the requested stage")
        snapshot = submission["content_snapshot"]
        snapshot_project_id = self._snapshot_project_id(resource_type, snapshot)
        if snapshot_project_id != int(project_id):
            raise ValueError("approved submission does not belong to the requested project")
        project = self.creative_repository.get_project(project_id)
        if project is None:
            raise KeyError(str(project_id))
        resource_version = self._resource_version(snapshot)
        payload = self._payload(project, stage, snapshot)
        content = self._serialize(project, payload, stage, format)
        fingerprint = hashlib.sha256(content).hexdigest()
        delivery_id = str(uuid.uuid4())
        extension = "json" if format == "json" else "md"
        path = (self.delivery_dir / f"{delivery_id}.{extension}").resolve()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        created_at = time.time()
        record = {
            "id": delivery_id,
            "project_id": str(project_id),
            "project_name": project["name"],
            "stage": stage,
            "resource_type": resource_type,
            "resource_id": submission["resource_id"],
            "resource_version": resource_version,
            "submission_id": submission_id,
            "submission_version": int(submission["version"]),
            "format": format,
            "fingerprint": fingerprint,
            "actor_user_id": actor.user_id,
            "path": str(path),
            "created_at": created_at,
        }
        try:
            with self.creative_repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO creative_deliveries (
                        id, project_id, stage, resource_type, resource_id,
                        resource_version, submission_id, submission_version,
                        format, content_fingerprint, actor_id, path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        delivery_id,
                        project_id,
                        stage,
                        resource_type,
                        submission["resource_id"],
                        resource_version,
                        submission_id,
                        int(submission["version"]),
                        format,
                        fingerprint,
                        actor.user_id,
                        str(path),
                        created_at,
                    ),
                )
            self.audit_service.record(
                actor=actor,
                action="delivery.create",
                target_type="delivery",
                target_id=delivery_id,
                project_id=str(project_id),
                before=None,
                after=record,
                reason=None,
                result="success",
                request=request,
            )
        except BaseException:
            path.unlink(missing_ok=True)
            with self.creative_repository._connect() as connection:
                connection.execute(
                    "DELETE FROM creative_deliveries WHERE id = ?", (delivery_id,)
                )
            raise
        return record

    def list(self) -> list[dict[str, Any]]:
        with self.creative_repository._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, p.name AS project_name
                FROM creative_deliveries d
                JOIN creative_projects p ON p.id = d.project_id
                ORDER BY d.created_at DESC, d.id DESC
                """
            ).fetchall()
        return [self._public_record(dict(row)) for row in rows]

    def options(self) -> list[dict[str, Any]]:
        with self.workflow_repository.identity_repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id AS submission_id, s.resource_type AS stage,
                       s.resource_id, s.version AS submission_version,
                       s.reviewed_at, t.project_id
                FROM workflow_submissions AS s
                JOIN workflow_tasks AS t ON t.id = s.task_id
                WHERE s.status = 'approved'
                  AND s.resource_type IN ('brief', 'characters', 'outline', 'beats')
                  AND t.project_id IS NOT NULL
                ORDER BY s.reviewed_at DESC, s.id DESC
                """
            ).fetchall()
        options: list[dict[str, Any]] = []
        for row in rows:
            project = self.creative_repository.get_project(int(row["project_id"]))
            if project is None:
                continue
            options.append(
                {
                    **dict(row),
                    "project_id": str(row["project_id"]),
                    "project_name": project["name"],
                }
            )
        return options

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        with self.creative_repository._connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, p.name AS project_name
                FROM creative_deliveries d
                JOIN creative_projects p ON p.id = d.project_id
                WHERE d.id = ?
                """,
                (delivery_id,),
            ).fetchone()
        return self._public_record(dict(row)) if row is not None else None

    def download_path(self, delivery_id: str) -> Path:
        record = self.get(delivery_id)
        if record is None:
            raise KeyError(delivery_id)
        path = Path(record["path"]).resolve()
        if path.parent != self.delivery_dir.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _public_record(record: dict[str, Any]) -> dict[str, Any]:
        record["project_id"] = str(record["project_id"])
        record["fingerprint"] = record.pop("content_fingerprint")
        record["actor_user_id"] = record.pop("actor_id")
        return record

    @staticmethod
    def _snapshot_project_id(resource_type: str, snapshot: Any) -> int:
        if not isinstance(snapshot, dict) or "project_id" not in snapshot:
            raise ValueError(f"approved {resource_type} snapshot has no project_id")
        return int(snapshot["project_id"])

    @staticmethod
    def _resource_version(snapshot: Any) -> int:
        if isinstance(snapshot, dict) and "version" in snapshot:
            return int(snapshot["version"])
        if isinstance(snapshot, dict):
            items = snapshot.get("characters") or snapshot.get("episodes") or []
            versions = {int(item.get("version") or 1) for item in items}
            if len(versions) == 1:
                return versions.pop()
        raise ValueError("approved snapshot has no unambiguous resource version")

    @staticmethod
    def _payload(
        project: dict[str, Any], stage: str, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        if stage == "outline":
            content: Any = snapshot.get("episodes") or []
        elif stage == "beats":
            content = [snapshot]
        elif stage == "characters":
            content = {
                "batch_id": snapshot.get("batch_id"),
                "characters": snapshot.get("characters") or [],
                "relationships": snapshot.get("relationships") or [],
            }
        else:
            content = snapshot
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

    @staticmethod
    def _serialize(
        project: dict[str, Any], payload: dict[str, Any], stage: str, format: str
    ) -> bytes:
        if format == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if stage == "beats":
            markdown = build_beats_markdown(project, payload["content"])
        else:
            markdown = "\n".join(
                [f"# {project['name']}", "", f"- 阶段：{stage}", ""]
                + _markdown_value(payload["content"])
            )
        return markdown.encode("utf-8")


def build_delivery_router(service: DeliveryService) -> APIRouter:
    router = APIRouter(prefix="/api/admin/deliveries", tags=["admin-deliveries"])

    def admin(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_system_admin:
            raise HTTPException(403, "only system administrators may manage deliveries")
        return principal

    def verify_csrf(request: Request) -> None:
        permission_service = permission_service_for_request(request)
        raw_token = request.cookies.get(permission_service.session_service.cookie_name)
        if not permission_service.session_service.verify_csrf(
            raw_token or "", request.headers.get("X-CSRF-Token")
        ):
            raise HTTPException(403, "CSRF 校验失败")

    @router.get("")
    def list_deliveries(principal: Principal = Depends(admin)):
        del principal
        return {"deliveries": service.list()}

    @router.get("/options")
    def list_options(principal: Principal = Depends(admin)):
        del principal
        return {"options": service.options()}

    @router.post("", status_code=201)
    def create_delivery(
        payload: DeliveryCreateRequest,
        request: Request,
        principal: Principal = Depends(admin),
    ):
        verify_csrf(request)
        try:
            delivery = service.create(
                project_id=payload.project_id,
                stage=payload.stage,
                submission_id=payload.submission_id,
                format=payload.format,
                actor=principal,
                request=request,
            )
        except KeyError as exc:
            raise HTTPException(404, "project or submission not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"delivery": delivery}

    @router.get("/{delivery_id}/download")
    def download_delivery(
        delivery_id: str, principal: Principal = Depends(admin)
    ) -> FileResponse:
        del principal
        try:
            path = service.download_path(delivery_id)
        except KeyError as exc:
            raise HTTPException(404, "delivery not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(410, "delivery file is unavailable") from exc
        record = service.get(delivery_id)
        assert record is not None
        media_type = (
            "application/json"
            if record["format"] == "json"
            else "text/markdown; charset=utf-8"
        )
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"creative-{record['project_id']}-{record['stage']}.{record['format']}",
        )

    return router
