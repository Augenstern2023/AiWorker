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
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from audit.service import AuditService
from identity.dependencies import (
    permission_service_for_request,
    require_page_api,
)
from identity.schema import Principal
from workflow.repository import WorkflowRepository

from .api import _markdown_value, build_beats_markdown, build_script_text
from .lifecycle_service import (
    beats_resource_fingerprint,
    script_resource_fingerprint,
    content_fingerprint as stage_content_fingerprint,
)
from .postgres_repository import PostgresCreativeRepository
from .workflow_adapter import CreativeWorkflowAdapter


STAGE_RESOURCE_TYPES = {
    "brief": "brief",
    "characters": "characters",
    "outline": "outline",
    "beats": "beats",
    "script": "script",
}


class DeliveryCreateRequest(BaseModel):
    project_id: int = Field(gt=0)
    stage: str = Field(pattern="^(brief|characters|outline|beats|script)$")
    resource_id: str = Field(min_length=1)
    format: str = Field(pattern="^(md|txt|json)$")
    workflow_task_id: str | None = None


class DeliveryService:
    def __init__(
        self,
        *,
        root: str | Path,
        creative_repository: PostgresCreativeRepository,
        workflow_repository: WorkflowRepository,
        audit_service: AuditService,
    ) -> None:
        self.root = Path(root).resolve()
        self.creative_repository = creative_repository
        self.workflow_repository = workflow_repository
        self.audit_service = audit_service
        self.delivery_dir = self.root / ".runtime" / "deliveries"
        self.delivery_dir.mkdir(parents=True, exist_ok=True)

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
        page_authorized: bool = False,
    ) -> dict[str, Any]:
        if not actor.is_system_admin and not page_authorized:
            raise PermissionError(
                "delivery requires system administrator or page authorization"
            )
        resource_type = STAGE_RESOURCE_TYPES.get(stage)
        if resource_type is None:
            raise ValueError("stage must be brief, characters, outline, beats, or script")
        if format not in {"md", "txt", "json"}:
            raise ValueError("format must be md, txt or json")
        project = self.creative_repository.get_project(project_id)
        if project is None:
            raise KeyError(str(project_id))
        if project.get("workflow_mode", "sequential") != "sequential":
            raise ValueError("自由创作项目首版不接入正式交付")
        adapter = CreativeWorkflowAdapter(
            self.creative_repository, self.workflow_repository
        )
        resource_status = adapter.domain_status(resource_type, resource_id)
        is_formal = self._is_formal_resource(resource_status)
        snapshot = adapter.snapshot(resource_type, resource_id, reconcile=False)
        snapshot_project_id = self._snapshot_project_id(resource_type, snapshot)
        if snapshot_project_id != int(project_id):
            raise ValueError("resource does not belong to the requested project")
        resource_version = self._resource_version(snapshot)
        payload = self._payload(project, stage, snapshot)
        content = self._serialize(project, payload, stage, format)
        fingerprint = hashlib.sha256(content).hexdigest()
        lifecycle_fingerprint = stage_content_fingerprint(
            payload["content"]
        )
        if stage == "beats":
            lifecycle_fingerprint = beats_resource_fingerprint(snapshot)
        elif stage == "script":
            lifecycle_fingerprint = script_resource_fingerprint(snapshot)
        source_key = f"direct:{stage}:{resource_id}"
        existing = self._find_existing(
            project_id, stage, source_key, format, is_formal=is_formal
        )
        if existing is not None:
            if (
                workflow_task_id
                and existing.get("workflow_task_id")
                and existing["workflow_task_id"] != workflow_task_id
            ):
                raise ValueError("delivery already belongs to another workflow task")
            return self._reconcile_delivery(
                existing, page_authorized=page_authorized
            )
        task = (
            self._resolve_delivery_task(project_id, workflow_task_id)
            if is_formal else None
        )
        delivery_id = str(uuid.uuid4())
        extension = "json" if format == "json" else format
        path = (self.delivery_dir / f"{delivery_id}.{extension}").resolve()
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        created_at = time.time()
        values = {
            "id": delivery_id,
            "project_id": project_id,
            "stage": stage,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "resource_version": resource_version,
            "submission_id": source_key,
            "submission_version": resource_version,
            "format": format,
            "content_fingerprint": fingerprint,
            "actor_id": actor.user_id,
            "workflow_task_id": task["id"] if task else None,
            "stage_content_fingerprint": lifecycle_fingerprint,
            "resource_status": resource_status,
            "is_formal": is_formal,
            "path": str(path),
            "created_at": created_at,
        }
        try:
            stored = self.creative_repository.record_delivery(values)
        except IntegrityError:
            path.unlink(missing_ok=True)
            existing = self._find_existing(
                project_id, stage, source_key, format, is_formal=is_formal
            )
            if existing is None:
                raise
            return self._reconcile_delivery(
                existing, page_authorized=page_authorized
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if stored["id"] != delivery_id:
            # A concurrent writer owns the named unique key, so the row that was
            # just stored is the winner and the artifact written above is an
            # orphan. The winner already points at its own file.
            path.unlink(missing_ok=True)
            return self._reconcile_delivery(
                stored, page_authorized=page_authorized
            )
        return self._reconcile_delivery(
            stored, request=request, page_authorized=page_authorized
        )

    def list(
        self,
        *,
        page_authorized: bool = False,
        project_authorizer: Callable[[str], bool] | None = None,
    ) -> list[dict[str, Any]]:
        records = self.creative_repository.list_deliveries()
        if project_authorizer is not None:
            records = [
                record
                for record in records
                if project_authorizer(str(record["project_id"]))
            ]
        return [
            self._reconcile_delivery(
                record, page_authorized=page_authorized
            )
            for record in records
        ]

    def options(
        self, *, project_authorizer: Callable[[str], bool] | None = None
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for project_summary in self.creative_repository.list_projects():
            if project_authorizer is not None and not project_authorizer(
                str(project_summary["id"])
            ):
                continue
            project = self.creative_repository.get_project(int(project_summary["id"]))
            if project is None:
                continue
            if project.get("workflow_mode", "sequential") != "sequential":
                continue
            resources: list[tuple[str, str, int, str]] = []
            resources.extend(
                ("brief", str(item["id"]), int(item["version"]), str(item["status"]))
                for item in project.get("blueprints", [])
            )
            character_batches = {
                str(item["batch_id"]): item
                for item in project.get("characters", [])
                if item.get("batch_id")
            }
            resources.extend(
                (
                    "characters", f"{project['id']}:{batch_id}",
                    int(item["version"]), str(item["status"]),
                )
                for batch_id, item in character_batches.items()
            )
            outline_batches = {
                str(item["batch_id"]): item
                for item in project.get("episodes", [])
                if item.get("stage") == "outline" and item.get("batch_id")
            }
            resources.extend(
                (
                    "outline", f"{project['id']}:{batch_id}",
                    int(item["version"]), str(item["status"]),
                )
                for batch_id, item in outline_batches.items()
            )
            for episode in project.get("episodes", []):
                if episode.get("stage") in {"beats", "script"}:
                    resources.append((
                        str(episode["stage"]), str(episode["id"]),
                        int(episode["version"]), str(episode["status"]),
                    ))
            for stage, resource_id, version, resource_status in resources:
                is_formal = self._is_formal_resource(resource_status)
                workflow_task_id = None
                if is_formal:
                    try:
                        workflow_task_id = self._resolve_delivery_task(
                            int(project["id"]), None
                        )["id"]
                    except (KeyError, ValueError):
                        pass
                options.append({
                    "resource_id": resource_id,
                    "stage": stage,
                    "resource_version": version,
                    "project_id": str(project["id"]),
                    "project_name": project["name"],
                    "resource_status": resource_status,
                    "is_formal": is_formal,
                    "workflow_task_id": workflow_task_id,
                })
        return options

    def get(
        self,
        delivery_id: str,
        *,
        page_authorized: bool = False,
        project_authorizer: Callable[[str], bool] | None = None,
    ) -> dict[str, Any] | None:
        record = self.creative_repository.get_delivery(delivery_id)
        if record is None:
            return None
        if project_authorizer is not None and not project_authorizer(
            str(record["project_id"])
        ):
            raise PermissionError("delivery project is outside the actor scope")
        return self._reconcile_delivery(
            record, page_authorized=page_authorized
        )

    def _find_existing(
        self, project_id: int, stage: str, submission_id: str, format: str,
        *, is_formal: bool,
    ) -> dict[str, Any] | None:
        return self.creative_repository.find_delivery(
            project_id,
            stage,
            submission_id,
            format,
            is_formal=is_formal,
        )

    def _resolve_delivery_task(
        self, project_id: int, workflow_task_id: str | None
    ) -> dict[str, Any]:
        scoped_project_id = self.creative_repository.workflow_project_id(project_id)
        with self.workflow_repository.identity_repository.connect() as connection:
            if workflow_task_id:
                row = connection.execute(
                    text("SELECT * FROM workflow_tasks WHERE id = :workflow_task_id"),
                    {"workflow_task_id": workflow_task_id},
                ).fetchone()
                rows = [row] if row is not None else []
            else:
                rows = connection.execute(
                    text("""
                    SELECT * FROM workflow_tasks
                    WHERE project_id = :project_id AND task_type = 'delivery'
                      AND role_code = 'system_admin'
                      AND status IN ('pool', 'assigned', 'in_progress')
                    ORDER BY created_at, id
                    """),
                    {"project_id": scoped_project_id},
                ).fetchall()
                if not rows:
                    rows = connection.execute(
                        text("""
                        SELECT t.* FROM workflow_tasks AS t
                        WHERE t.project_id = :project_id AND t.task_type = 'delivery'
                          AND t.role_code = 'system_admin'
                          AND t.status = 'completed'
                          AND EXISTS (
                              SELECT 1 FROM workflow_task_events AS e
                              WHERE e.task_id = t.id
                                AND e.event_type = 'delivery.complete'
                          )
                        ORDER BY t.updated_at DESC, t.id DESC
                        """),
                        {"project_id": scoped_project_id},
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
        if str(task["project_id"] or "") != scoped_project_id:
            raise ValueError("delivery workflow task belongs to another project")
        if task["status"] not in {"pool", "assigned", "in_progress", "completed"}:
            raise ValueError("delivery workflow task is not active")
        if task["status"] == "completed":
            with self.workflow_repository.identity_repository.connect() as connection:
                completed_by_delivery = connection.execute(
                    text(
                        "SELECT 1 FROM workflow_task_events "
                        "WHERE task_id = :task_id "
                        "AND event_type = 'delivery.complete' LIMIT 1"
                    ),
                    {"task_id": task["id"]},
                ).fetchone()
            if completed_by_delivery is None:
                raise ValueError("completed task was not consumed by delivery")
        return task

    def _reconcile_delivery(
        self,
        record: dict[str, Any],
        *,
        request: Request | None = None,
        page_authorized: bool = False,
    ) -> dict[str, Any]:
        path = Path(record["path"]).resolve()
        if path.parent != self.delivery_dir.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        is_formal = bool(record.get("is_formal"))
        if is_formal and not record.get("workflow_task_id"):
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
            elif record["stage"] == "script":
                fingerprint = script_resource_fingerprint(snapshot)
            self._mark_reconciled(
                record["id"], "stage_content_fingerprint", fingerprint
            )
            record["stage_content_fingerprint"] = fingerprint
        if is_formal:
            self._record_stage_export(record)
            self._complete_workflow_task(record, page_authorized=page_authorized)
        self._record_audit(record, request=request)
        refreshed = self._get_unreconciled(record["id"])
        assert refreshed is not None
        return refreshed

    def _record_stage_export(self, record: dict[str, Any]) -> None:
        if record.get("stage_export_recorded_at") is not None:
            return
        if record["stage"] in {"beats", "script"}:
            fingerprint = record["stage_content_fingerprint"]
            if not self.creative_repository.has_stage_export(
                int(record["project_id"]), record["stage"], fingerprint
            ):
                self.creative_repository.record_stage_export(
                    int(record["project_id"]),
                    record["stage"],
                    record["format"],
                    fingerprint,
                )
        self._mark_reconciled(
            record["id"], "stage_export_recorded_at", time.time()
        )

    def _complete_workflow_task(
        self, record: dict[str, Any], *, page_authorized: bool = False
    ) -> None:
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
                or str(task["project_id"] or "")
                != self.creative_repository.workflow_project_id(record["project_id"])
            ):
                raise ValueError("delivery registry references a mismatched workflow task")
            if task["status"] != "completed":
                if task["status"] not in {"pool", "assigned", "in_progress"}:
                    raise ValueError("delivery workflow task cannot be completed")
                actor = self.workflow_repository._live_actor(
                    connection, record["actor_user_id"]
                )
                if not actor["is_system_admin"] and not page_authorized:
                    raise PermissionError(
                        "delivery actor no longer has page authorization"
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
                    authorization_context=(
                        {"permissions": {"*"}} if page_authorized else None
                    ),
                )
            else:
                event = connection.execute(
                    text(
                        "SELECT 1 FROM workflow_task_events "
                        "WHERE task_id = :task_id "
                        "AND event_type = 'delivery.complete' LIMIT 1"
                    ),
                    {"task_id": task["id"]},
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
        self.creative_repository.mark_delivery_reconciled(
            delivery_id, column, value
        )

    def _get_unreconciled(self, delivery_id: str) -> dict[str, Any] | None:
        return self.creative_repository.get_delivery(delivery_id)

    def download_path(
        self,
        delivery_id: str,
        *,
        page_authorized: bool = False,
        project_authorizer: Callable[[str], bool] | None = None,
    ) -> Path:
        record = self.get(
            delivery_id,
            page_authorized=page_authorized,
            project_authorizer=project_authorizer,
        )
        if record is None:
            raise KeyError(delivery_id)
        path = Path(record["path"]).resolve()
        if path.parent != self.delivery_dir.resolve() or not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _is_formal_resource(resource_status: str) -> bool:
        return resource_status in {"locked", "brief_locked", "approved"}

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
        elif stage == "script":
            content = snapshot
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
        if stage == "script" and format == "txt":
            script = payload["content"]
            return build_script_text(
                project,
                script.get("content") or {},
                episode_number=int(script.get("episode_number") or 0),
            ).encode("utf-8")
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

    admin = require_page_api()

    def can_access_project(
        request: Request, principal: Principal, project_id: str
    ) -> bool:
        if principal.is_system_admin:
            return True
        permission_service = permission_service_for_request(request)
        scoped_project_id = service.creative_repository.workflow_project_id(
            project_id
        )
        return permission_service.can_access_assigned_project(
            principal, scoped_project_id
        ) or permission_service.can_access_managed_project(
            principal, scoped_project_id
        )

    def assert_project_access(
        request: Request, principal: Principal, project_id: str
    ) -> None:
        if not can_access_project(request, principal, project_id):
            raise HTTPException(403, "没有访问此项目交付成果的权限")

    def verify_csrf(request: Request) -> None:
        permission_service = permission_service_for_request(request)
        raw_token = request.cookies.get(permission_service.session_service.cookie_name)
        if not permission_service.session_service.verify_csrf(
            raw_token or "", request.headers.get("X-CSRF-Token")
        ):
            raise HTTPException(403, "CSRF 校验失败")

    @router.get("")
    def list_deliveries(
        request: Request, principal: Principal = Depends(admin)
    ):
        return {
            "deliveries": service.list(
                page_authorized=not principal.is_system_admin,
                project_authorizer=lambda project_id: can_access_project(
                    request, principal, project_id
                ),
            )
        }

    @router.get("/options")
    def list_options(
        request: Request, principal: Principal = Depends(admin)
    ):
        return {
            "options": service.options(
                project_authorizer=lambda project_id: can_access_project(
                    request, principal, project_id
                )
            )
        }

    @router.post("", status_code=201)
    def create_delivery(
        payload: DeliveryCreateRequest,
        request: Request,
        principal: Principal = Depends(admin),
    ):
        verify_csrf(request)
        assert_project_access(request, principal, str(payload.project_id))
        try:
            delivery = service.create(
                project_id=payload.project_id,
                stage=payload.stage,
                resource_id=payload.resource_id,
                format=payload.format,
                actor=principal,
                request=request,
                workflow_task_id=payload.workflow_task_id,
                page_authorized=not principal.is_system_admin,
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
        delivery_id: str,
        request: Request,
        principal: Principal = Depends(admin),
    ) -> FileResponse:
        try:
            page_authorized = not principal.is_system_admin
            path = service.download_path(
                delivery_id,
                page_authorized=page_authorized,
                project_authorizer=lambda project_id: can_access_project(
                    request, principal, project_id
                ),
            )
        except KeyError as exc:
            raise HTTPException(404, "delivery not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(410, "delivery file is unavailable") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        record = service.get(
            delivery_id,
            page_authorized=page_authorized,
            project_authorizer=lambda project_id: can_access_project(
                request, principal, project_id
            ),
        )
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
