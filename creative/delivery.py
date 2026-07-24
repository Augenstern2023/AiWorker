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
import sqlite3
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
from .lifecycle_service import (
    beats_resource_fingerprint,
    content_fingerprint as stage_content_fingerprint,
)
from .repository import CreativeRepository
from .workflow_adapter import CreativeWorkflowAdapter


STAGE_RESOURCE_TYPES = {
    "brief": "brief",
    "characters": "characters",
    "outline": "outline",
    "beats": "beats",
}


class DeliveryCreateRequest(BaseModel):
    project_id: int = Field(gt=0)
    stage: str = Field(pattern="^(brief|characters|outline|beats)$")
    resource_id: str = Field(min_length=1)
    format: str = Field(pattern="^(md|json)$")
    workflow_task_id: str | None = None


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
                    workflow_task_id TEXT,
                    stage_content_fingerprint TEXT NOT NULL DEFAULT '',
                    stage_export_recorded_at REAL,
                    workflow_completed_at REAL,
                    audit_recorded_at REAL,
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
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(creative_deliveries)"
                ).fetchall()
            }
            additions = {
                "workflow_task_id": "TEXT",
                "stage_content_fingerprint": "TEXT NOT NULL DEFAULT ''",
                "stage_export_recorded_at": "REAL",
                "workflow_completed_at": "REAL",
                "audit_recorded_at": "REAL",
            }
            for column, definition in additions.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE creative_deliveries ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_creative_delivery_request "
                "ON creative_deliveries(project_id, stage, submission_id, format)"
            )
            connection.execute(
                "DROP INDEX IF EXISTS uq_creative_delivery_task"
            )

    def create(
        self,
        *,
        project_id: int,
        stage: str,
        resource_id: str,
        format: str,
        actor: Principal,
        request: Request | None = None,
        workflow_task_id: str | None = None,
    ) -> dict[str, Any]:
        if not actor.is_system_admin:
            raise PermissionError("only system administrators may create deliveries")
        resource_type = STAGE_RESOURCE_TYPES.get(stage)
        if resource_type is None:
            raise ValueError("stage must be brief, characters, outline, or beats")
        if format not in {"md", "json"}:
            raise ValueError("format must be md or json")
        adapter = CreativeWorkflowAdapter(
            self.creative_repository, self.workflow_repository
        )
        if adapter.domain_status(resource_type, resource_id) not in {
            "locked", "brief_locked", "approved"
        }:
            raise ValueError("delivery requires a completed resource")
        snapshot = adapter.snapshot(resource_type, resource_id, reconcile=False)
        snapshot_project_id = self._snapshot_project_id(resource_type, snapshot)
        if snapshot_project_id != int(project_id):
            raise ValueError("completed resource does not belong to the requested project")
        project = self.creative_repository.get_project(project_id)
        if project is None:
            raise KeyError(str(project_id))
        resource_version = self._resource_version(snapshot)
        payload = self._payload(project, stage, snapshot)
        content = self._serialize(project, payload, stage, format)
        fingerprint = hashlib.sha256(content).hexdigest()
        lifecycle_fingerprint = stage_content_fingerprint(
            payload["content"]
        )
        if stage == "beats":
            lifecycle_fingerprint = beats_resource_fingerprint(snapshot)
        source_key = f"direct:{stage}:{resource_id}"
        existing = self._find_existing(project_id, stage, source_key, format)
        if existing is not None:
            if (
                workflow_task_id
                and existing.get("workflow_task_id")
                and existing["workflow_task_id"] != workflow_task_id
            ):
                raise ValueError("delivery already belongs to another workflow task")
            return self._reconcile_delivery(existing)
        task = self._resolve_delivery_task(project_id, workflow_task_id)
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
            "resource_id": resource_id,
            "resource_version": resource_version,
            "submission_id": source_key,
            "submission_version": resource_version,
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
                        format, content_fingerprint, actor_id, workflow_task_id,
                        stage_content_fingerprint, path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        delivery_id,
                        project_id,
                        stage,
                        resource_type,
                        resource_id,
                        resource_version,
                        source_key,
                        resource_version,
                        format,
                        fingerprint,
                        actor.user_id,
                        task["id"],
                        lifecycle_fingerprint,
                        str(path),
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError:
            path.unlink(missing_ok=True)
            existing = self._find_existing(
                project_id, stage, source_key, format
            )
            if existing is None:
                raise
            return self._reconcile_delivery(existing)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        record.update(
            {
                "workflow_task_id": task["id"],
                "stage_content_fingerprint": lifecycle_fingerprint,
                "stage_export_recorded_at": None,
                "workflow_completed_at": None,
                "audit_recorded_at": None,
            }
        )
        return self._reconcile_delivery(record, request=request)

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
        return [
            self._reconcile_delivery(self._public_record(dict(row)))
            for row in rows
        ]

    def options(self) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for project in self.creative_repository.list_projects():
            try:
                task = self._resolve_delivery_task(int(project["id"]), None)
            except (KeyError, ValueError):
                continue
            resources: list[tuple[str, str, int]] = []
            if project.get("locked_brief_id"):
                brief = self.creative_repository.get_blueprint(
                    int(project["locked_brief_id"])
                )
                if brief:
                    resources.append(("brief", str(brief["id"]), int(brief["version"])))
            if project.get("character_batch_id"):
                batch = self.creative_repository.get_character_batch(
                    int(project["id"]), str(project["character_batch_id"])
                )
                if batch["characters"]:
                    resources.append(("characters", f"{project['id']}:{project['character_batch_id']}", int(batch["characters"][0]["version"])))
            if project.get("outline_batch_id"):
                batch = self.creative_repository.get_outline_batch(
                    int(project["id"]), str(project["outline_batch_id"])
                )
                if batch["episodes"]:
                    resources.append(("outline", f"{project['id']}:{project['outline_batch_id']}", int(batch["episodes"][0]["version"])))
            for episode in project.get("episodes", []):
                if episode.get("stage") == "beats" and episode.get("status") in {"locked", "approved"}:
                    resources.append(("beats", str(episode["id"]), int(episode["version"])))
            for stage, resource_id, version in resources:
                options.append({
                    "resource_id": resource_id,
                    "stage": stage,
                    "resource_version": version,
                    "project_id": str(project["id"]),
                    "project_name": project["name"],
                    "workflow_task_id": task["id"],
                })
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
        return (
            self._reconcile_delivery(self._public_record(dict(row)))
            if row is not None
            else None
        )

    def _find_existing(
        self, project_id: int, stage: str, submission_id: str, format: str
    ) -> dict[str, Any] | None:
        with self.creative_repository._connect() as connection:
            row = connection.execute(
                """
                SELECT d.*, p.name AS project_name
                FROM creative_deliveries d
                JOIN creative_projects p ON p.id = d.project_id
                WHERE d.project_id = ? AND d.stage = ?
                  AND d.submission_id = ? AND d.format = ?
                """,
                (project_id, stage, submission_id, format),
            ).fetchone()
        return self._public_record(dict(row)) if row is not None else None

    def _resolve_delivery_task(
        self, project_id: int, workflow_task_id: str | None
    ) -> dict[str, Any]:
        with self.workflow_repository.identity_repository.connect() as connection:
            if workflow_task_id:
                row = connection.execute(
                    "SELECT * FROM workflow_tasks WHERE id = ?",
                    (workflow_task_id,),
                ).fetchone()
                rows = [row] if row is not None else []
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM workflow_tasks
                    WHERE project_id = ? AND task_type = 'delivery'
                      AND role_code = 'system_admin'
                      AND status IN ('pool', 'assigned', 'in_progress')
                    ORDER BY created_at, id
                    """,
                    (str(project_id),),
                ).fetchall()
                if not rows:
                    rows = connection.execute(
                        """
                        SELECT t.* FROM workflow_tasks AS t
                        WHERE t.project_id = ? AND t.task_type = 'delivery'
                          AND t.role_code = 'system_admin'
                          AND t.status = 'completed'
                          AND EXISTS (
                              SELECT 1 FROM workflow_task_events AS e
                              WHERE e.task_id = t.id
                                AND e.event_type = 'delivery.complete'
                          )
                        ORDER BY t.updated_at DESC, t.id DESC
                        """,
                        (str(project_id),),
                    ).fetchall()
        if not rows:
            raise ValueError("an active terminal delivery workflow task is required")
        if len(rows) != 1:
            raise ValueError("workflow_task_id is required when delivery tasks are ambiguous")
        task = self.workflow_repository._task_dict(rows[0])
        if task["task_type"] != "delivery":
            raise ValueError("workflow task must be the terminal delivery node")
        if task["role_code"] != "system_admin":
            raise ValueError("delivery workflow task must belong to system_admin")
        if str(task["project_id"] or "") != str(project_id):
            raise ValueError("delivery workflow task belongs to another project")
        if task["status"] not in {"pool", "assigned", "in_progress", "completed"}:
            raise ValueError("delivery workflow task is not active")
        if task["status"] == "completed":
            with self.workflow_repository.identity_repository.connect() as connection:
                completed_by_delivery = connection.execute(
                    "SELECT 1 FROM workflow_task_events "
                    "WHERE task_id = ? AND event_type = 'delivery.complete' LIMIT 1",
                    (task["id"],),
                ).fetchone()
            if completed_by_delivery is None:
                raise ValueError("completed task was not consumed by delivery")
        return task

    def _reconcile_delivery(
        self, record: dict[str, Any], *, request: Request | None = None
    ) -> dict[str, Any]:
        path = Path(record["path"]).resolve()
        if path.parent != self.delivery_dir.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        if not record.get("workflow_task_id"):
            task = self._resolve_delivery_task(int(record["project_id"]), None)
            self._mark_reconciled(
                record["id"], "workflow_task_id", task["id"]
            )
            record["workflow_task_id"] = task["id"]
        if not record.get("stage_content_fingerprint"):
            project = self.creative_repository.get_project(
                int(record["project_id"])
            )
            if project is None:
                raise ValueError("delivery source is no longer available")
            snapshot = CreativeWorkflowAdapter(
                self.creative_repository, self.workflow_repository
            ).snapshot(record["stage"], record["resource_id"], reconcile=False)
            payload = self._payload(project, record["stage"], snapshot)
            fingerprint = stage_content_fingerprint(
                payload["content"]
            )
            if record["stage"] == "beats":
                fingerprint = beats_resource_fingerprint(snapshot)
            self._mark_reconciled(
                record["id"], "stage_content_fingerprint", fingerprint
            )
            record["stage_content_fingerprint"] = fingerprint
        self._record_stage_export(record)
        self._complete_workflow_task(record)
        self._record_audit(record, request=request)
        refreshed = self._get_unreconciled(record["id"])
        assert refreshed is not None
        return refreshed

    def _record_stage_export(self, record: dict[str, Any]) -> None:
        if record.get("stage_export_recorded_at") is not None:
            return
        if record["stage"] == "beats":
            fingerprint = record["stage_content_fingerprint"]
            if not self.creative_repository.has_stage_export(
                int(record["project_id"]), "beats", fingerprint
            ):
                self.creative_repository.record_stage_export(
                    int(record["project_id"]),
                    "beats",
                    record["format"],
                    fingerprint,
                )
        self._mark_reconciled(
            record["id"], "stage_export_recorded_at", time.time()
        )

    def _complete_workflow_task(self, record: dict[str, Any]) -> None:
        if record.get("workflow_completed_at") is not None:
            return
        repository = self.workflow_repository.identity_repository
        with repository.transaction() as connection:
            task = self.workflow_repository._require_task(
                connection, record["workflow_task_id"]
            )
            if (
                task["task_type"] != "delivery"
                or task["role_code"] != "system_admin"
                or str(task["project_id"] or "") != str(record["project_id"])
            ):
                raise ValueError("delivery registry references a mismatched workflow task")
            if task["status"] != "completed":
                if task["status"] not in {"pool", "assigned", "in_progress"}:
                    raise ValueError("delivery workflow task cannot be completed")
                actor = self.workflow_repository._live_actor(
                    connection, record["actor_user_id"]
                )
                if not actor["is_system_admin"]:
                    raise PermissionError(
                        "delivery actor is no longer a system administrator"
                    )
                self.workflow_repository.transition_task_in_transaction(
                    task["id"],
                    "deliver",
                    actor,
                    connection=connection,
                    event_type="delivery.complete",
                    details={
                        "delivery_id": record["id"],
                        "stage": record["stage"],
                        "submission_id": record["submission_id"],
                    },
                )
            else:
                event = connection.execute(
                    "SELECT 1 FROM workflow_task_events "
                    "WHERE task_id = ? AND event_type = 'delivery.complete' LIMIT 1",
                    (task["id"],),
                ).fetchone()
                if event is None:
                    actor = self.workflow_repository._live_actor(
                        connection, record["actor_user_id"]
                    )
                    self.workflow_repository.record_task_event_in_transaction(
                        task["id"],
                        "delivery.complete",
                        actor,
                        connection=connection,
                        details={
                            "delivery_id": record["id"],
                            "stage": record["stage"],
                            "submission_id": record["submission_id"],
                            "reconciled": True,
                        },
                    )
        self._mark_reconciled(
            record["id"], "workflow_completed_at", time.time()
        )

    def _record_audit(
        self, record: dict[str, Any], *, request: Request | None
    ) -> None:
        if record.get("audit_recorded_at") is not None:
            return
        existing = self.audit_service.query(
            action="delivery.create", target_id=record["id"]
        )
        if not existing:
            repository = self.workflow_repository.identity_repository
            with repository.connect() as connection:
                actor_data = self.workflow_repository._live_actor(
                    connection, record["actor_user_id"]
                )
            actor = Principal(
                user_id=actor_data["user_id"],
                username="delivery-actor",
                display_name="Delivery Actor",
                role_codes=tuple(actor_data["role_codes"]),
                permissions=frozenset(actor_data["permissions"]),
                data_scope="all",
                session_id="delivery-reconciliation",
            )
            self.audit_service.record(
                actor=actor,
                action="delivery.create",
                target_type="delivery",
                target_id=record["id"],
                project_id=str(record["project_id"]),
                before=None,
                after=record,
                reason=None,
                result="success",
                request=request,
            )
        self._mark_reconciled(record["id"], "audit_recorded_at", time.time())

    def _mark_reconciled(
        self, delivery_id: str, column: str, value: Any
    ) -> None:
        allowed = {
            "workflow_task_id",
            "stage_content_fingerprint",
            "stage_export_recorded_at",
            "workflow_completed_at",
            "audit_recorded_at",
        }
        if column not in allowed:
            raise ValueError("invalid delivery reconciliation column")
        with self.creative_repository._connect() as connection:
            connection.execute(
                f"UPDATE creative_deliveries SET {column} = ? WHERE id = ?",
                (value, delivery_id),
            )

    def _get_unreconciled(self, delivery_id: str) -> dict[str, Any] | None:
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
            raise ValueError(f"completed {resource_type} snapshot has no project_id")
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
        raise ValueError("completed snapshot has no unambiguous resource version")

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
                resource_id=payload.resource_id,
                format=payload.format,
                actor=principal,
                request=request,
                workflow_task_id=payload.workflow_task_id,
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
