# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Immutable production package export records for the web handoff flow.

Records live in the creative SQLite database; package files live under the
server-owned ``.runtime/production-exports`` directory and are named by server
generated ids. The identity of a package is ``project id + fingerprint +
contract version``: a repeated request for the same version reuses the stored
artifact and its original creation metadata, and a stored file is never
rebuilt from current project content. Integrity uses two distinct values: the
package fingerprint (locked creative content) and the file SHA256 (bytes on
disk); a missing, tampered or displaced file fails closed with 410 semantics.

Audit follows traceable at-least-once semantics. A record stays ``pending``
until its create/reuse audit lands, and a pending record cannot be downloaded.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import Request

from audit.service import AuditService
from creative.production_export import _inspect_snapshot
from creative.repository import CreativeRepository
from identity.schema import Principal

CONTRACT_VERSION = "1.0"
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')


class ExportBlocked(ValueError):
    """Locked inputs are incomplete; nothing may be exported yet."""


class FingerprintConflict(ValueError):
    """Current locked content no longer matches the confirmed fingerprint."""


class ExportUnavailable(ValueError):
    """A registered artifact is missing, corrupted, or outside the store."""


class AuditUnavailable(RuntimeError):
    """Audit storage failed; the artifact is kept for a retryable write-back."""


class ProductionExportService:
    def __init__(
        self,
        *,
        root: str | Path,
        repository: CreativeRepository,
        audit_service: AuditService,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.export_dir = (Path(root).resolve() / ".runtime" / "production-exports")
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = self.repository._connect()
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creative_production_exports (
                    id TEXT PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_by_name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    episode_count INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    audit_recorded_at REAL,
                    UNIQUE(project_id, fingerprint, contract_version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_creative_production_exports_project "
                "ON creative_production_exports(project_id, created_at DESC, id)"
            )

    # ---------------------------------------------------------------- preview

    def preview(self, project_id: int) -> dict[str, Any]:
        inspection, package = _inspect_snapshot(self.repository.db_path, project_id)
        return inspection.preview(package)

    # ----------------------------------------------------------------- create

    def create(
        self,
        project_id: int,
        expected_fingerprint: str,
        actor: Principal,
        request: Request | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Export the confirmed version, or reuse the stored artifact for it.

        Returns ``(record, created)``. Raises KeyError (unknown project),
        ExportBlocked/FingerprintConflict (409), ExportUnavailable (410) or
        AuditUnavailable (503). Never rebuilds a registered historical file.
        """
        inspection, package = _inspect_snapshot(self.repository.db_path, project_id)
        if package is None:
            raise ExportBlocked("production export: locked inputs are incomplete")
        if package["fingerprint"] != expected_fingerprint:
            raise FingerprintConflict("production export: version changed since preview")
        existing = self._find_row(project_id, package["fingerprint"])
        if existing is not None:
            self._verified_path(existing)
            return self._ensure_reuse_audit(self._public(existing), actor, request), False
        export_id = str(uuid.uuid4())
        content = json.dumps(package, ensure_ascii=False, indent=2).encode("utf-8")
        path = (self.export_dir / f"{export_id}.json").resolve()
        temporary = path.with_name(f"{export_id}.json.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        created_at = time.time()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO creative_production_exports (
                        id, project_id, fingerprint, contract_version,
                        created_by, created_by_name, created_at, episode_count,
                        path, size_bytes, file_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        export_id,
                        project_id,
                        package["fingerprint"],
                        CONTRACT_VERSION,
                        actor.user_id,
                        actor.display_name,
                        created_at,
                        len(package["provenance"]["episode_numbers"]),
                        str(path),
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                    ),
                )
        except sqlite3.IntegrityError:
            # A concurrent winner registered this version first; keep its file
            # and remove only the artifact this request generated.
            path.unlink(missing_ok=True)
            winner = self._find_row(project_id, package["fingerprint"])
            if winner is None:
                raise
            self._verified_path(winner)
            return self._ensure_reuse_audit(self._public(winner), actor, request), False
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        record = self._public(self._row(export_id))
        self._audit(
            actor, "production_export.create", record, request,
            mark_recorded=export_id,
        )
        return self._public(self._row(export_id)), True

    def _ensure_reuse_audit(
        self,
        record: dict[str, Any],
        actor: Principal,
        request: Request | None,
    ) -> dict[str, Any]:
        if record["audit_status"] == "pending":
            self._audit(
                actor, "production_export.reuse", record, request,
                mark_recorded=record["id"],
            )
            return self._public(self._row(record["id"]))
        self._audit(actor, "production_export.reuse", record, request)
        return record

    # ---------------------------------------------------------------- history

    def list_for_project(
        self, project_id: int, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        with self._connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM creative_production_exports WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            rows = connection.execute(
                """
                SELECT * FROM creative_production_exports
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (project_id, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "items": [self._public(dict(row)) for row in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }

    # --------------------------------------------------------------- download

    def download_target(
        self,
        export_id: str,
        actor: Principal,
        request: Request | None = None,
        *,
        can_access: Callable[[int], bool] | None = None,
    ) -> tuple[Path, str]:
        """Locate the record, re-authorize by project, verify, audit.

        ``can_access`` is the caller's managed-project predicate; it is called
        with the record's project id so history downloads are reauthorized the
        same way as creation, keyed by the stored project rather than a client
        hint.
        """
        row = self._row(export_id)
        if row is None:
            raise KeyError(export_id)
        record = self._public(row)
        with self._connect() as connection:
            project = connection.execute(
                "SELECT id, name FROM creative_projects WHERE id = ? AND deleted = 0",
                (row["project_id"],),
            ).fetchone()
        if project is None:
            raise KeyError(str(row["project_id"]))
        if can_access is not None and not can_access(int(row["project_id"])):
            raise PermissionError("production export project is outside the actor scope")
        if record["audit_status"] == "pending":
            raise AuditUnavailable("production export: create audit is still pending")
        path = self._verified_path(row)
        self._audit(actor, "production_export.download_started", record, request)
        name = _safe_project_name(project["name"])
        return path, f"{name}_生产包_{str(record['fingerprint'])[:12]}.json"

    # -------------------------------------------------------------- internals

    def _find_row(self, project_id: int, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM creative_production_exports
                WHERE project_id = ? AND fingerprint = ? AND contract_version = ?
                """,
                (project_id, fingerprint, CONTRACT_VERSION),
            ).fetchone()
        return dict(row) if row is not None else None

    def _row(self, export_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM creative_production_exports WHERE id = ?", (export_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _verified_path(self, record: dict[str, Any]) -> Path:
        try:
            path = Path(str(record["path"])).resolve()
        except (OSError, ValueError) as exc:
            raise ExportUnavailable("production export: registered path is invalid") from exc
        if path.parent != self.export_dir or not path.is_file():
            raise ExportUnavailable("production export: file is unavailable")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != record["file_sha256"]:
            raise ExportUnavailable("production export: file integrity check failed")
        return path

    def _audit(
        self,
        actor: Principal,
        action: str,
        record: dict[str, Any],
        request: Request | None,
        *,
        mark_recorded: str | None = None,
    ) -> None:
        try:
            self.audit_service.record(
                actor=actor,
                action=action,
                target_type="production_export",
                target_id=record["id"],
                project_id=str(record["project_id"]),
                before=None,
                after={
                    "export_id": record["id"],
                    "fingerprint": record["fingerprint"],
                    "contract_version": record["contract_version"],
                    "episode_count": record["episode_count"],
                    "size_bytes": record["size_bytes"],
                    "file_sha256": record["file_sha256"],
                },
                reason=None,
                result="success",
                request=request,
            )
        except Exception as exc:
            raise AuditUnavailable("production export: audit storage is unavailable") from exc
        if mark_recorded is not None:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE creative_production_exports SET audit_recorded_at = ? WHERE id = ?",
                    (time.time(), mark_recorded),
                )

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "project_id": str(record["project_id"]),
            "fingerprint": record["fingerprint"],
            "contract_version": record["contract_version"],
            "created_by": record["created_by"],
            "created_by_name": record["created_by_name"],
            "created_at": record["created_at"],
            "episode_count": record["episode_count"],
            "size_bytes": record["size_bytes"],
            "file_sha256": record["file_sha256"],
            "audit_status": "recorded" if record.get("audit_recorded_at") is not None else "pending",
            "download_url": f"/api/creative/production-exports/{record['id']}/download",
        }


def _safe_project_name(name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", str(name or "")).strip("_")
    return cleaned or "项目"
