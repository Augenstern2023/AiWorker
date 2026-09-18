# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Immutable production package export records for the web handoff flow.

Records live in the creative PostgreSQL store, in the Alembic-managed
``creative_production_exports`` table declared by ``creative.database_schema``;
package files live under the server-owned ``.runtime/production-exports``
directory and are named by server generated ids. The identity of a package is
``project id + fingerprint + contract version``: a repeated request for the same
version reuses the stored artifact and its original creation metadata, and a
stored file is never rebuilt from current project content. Integrity uses two
distinct values: the package fingerprint (locked creative content) and the file
SHA256 (bytes on disk); a missing, tampered or displaced file fails closed with
410 semantics.

Audit follows traceable at-least-once semantics. A record stays ``pending``
until its create/reuse audit lands, and a pending record cannot be downloaded.

The service owns neither schema nor connection. The export table is part of the
migrated creative schema contract, so nothing here creates or alters it; every
statement goes through the repository's production-export methods, or, for the
single lookup that has no repository method of its own, through
``connection_scope``. All filesystem work happens outside a transaction, and a
connection borrowed from the repository is never committed, rolled back or
closed by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from fastapi import Request
from sqlalchemy import select

from audit.service import AuditService
from creative.database_schema import creative_projects
from creative.production_export import _inspect_snapshot
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
    """Production package export bookkeeping over the PostgreSQL creative store.

    ``repository`` is the injected creative repository. The service uses its
    production-export surface (``record_production_export``,
    ``find_production_export``, ``get_production_export``,
    ``list_production_exports``, ``mark_production_export_audited``,
    ``production_export_public_record``) plus the connection boundaries every
    creative repository exposes (``connection_scope``).
    """

    def __init__(
        self,
        *,
        root: str | Path,
        repository: Any,  # the injected creative repository (duck typed, see above)
        audit_service: AuditService,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.export_dir = (Path(root).resolve() / ".runtime" / "production-exports")
        self.export_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- snapshots

    def _inspect(
        self,
        project_id: int,
        episode_numbers: Iterable[int] | None,
        version_manifest: dict[str, Any] | None,
    ) -> tuple[Any, dict[str, Any] | None]:
        """Resolve the locked snapshot from one consistent read of the store.

        Snapshot resolution belongs to ``creative.production_export``; its
        repository entry point takes the repository and, when the caller passes
        one, the borrowed connection it must read through, so the project, the
        resource heads and every selected version come from a single read
        transaction. The connection is borrowed for the duration of the call
        only and is never committed, rolled back or closed here.
        """

        with self.repository.connection_scope() as connection:
            return _inspect_snapshot(
                self.repository,
                project_id,
                episode_numbers,
                version_manifest,
                connection=connection,
            )

    # ---------------------------------------------------------------- preview

    def preview(
        self,
        project_id: int,
        episode_numbers: Iterable[int] | None = None,
        version_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requested = None if episode_numbers is None else list(episode_numbers)
        inspection, package = self._inspect(project_id, requested, version_manifest)
        result = inspection.preview(package)
        total = int(inspection.project["target_episode_count"])
        selected = (
            list(package["provenance"]["episode_numbers"])
            if package is not None
            else list(range(1, total + 1))
            if requested is None
            else sorted(set(int(number) for number in requested))
        )
        result["selection"] = {
            "export_scope": (
                "full" if selected == list(range(1, total + 1)) else "partial"
            ),
            "episode_numbers": selected,
            "project_total_episodes": total,
            "exported_episode_count": len(selected),
        }
        return result

    # ----------------------------------------------------------------- create

    def create(
        self,
        project_id: int,
        expected_fingerprint: str,
        actor: Principal,
        request: Request | None = None,
        *,
        episode_numbers: Iterable[int] | None = None,
        version_manifest: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Export the confirmed version, or reuse the stored artifact for it.

        Returns ``(record, created)``. Raises KeyError (unknown project),
        ExportBlocked/FingerprintConflict (409), ExportUnavailable (410) or
        AuditUnavailable (503). Never rebuilds a registered historical file.
        """
        inspection, package = self._inspect(
            project_id, episode_numbers, version_manifest
        )
        if package is None:
            raise ExportBlocked("production export: locked inputs are incomplete")
        if package["fingerprint"] != expected_fingerprint:
            raise FingerprintConflict("production export: version changed since preview")
        existing = self.repository.find_production_export(
            project_id,
            package["fingerprint"],
            contract_version=CONTRACT_VERSION,
        )
        if existing is not None:
            self._verified_path(existing)
            return (
                self._ensure_reuse_audit(
                    self.repository.production_export_public_record(existing),
                    actor,
                    request,
                ),
                False,
            )
        export_id = str(uuid.uuid4())
        content = json.dumps(package, ensure_ascii=False, indent=2).encode("utf-8")
        provenance = package["provenance"]
        selected_numbers = list(provenance["episode_numbers"])
        path = (self.export_dir / f"{export_id}.json").resolve()
        temporary = path.with_name(f"{export_id}.json.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        created_at = time.time()
        try:
            stored = self.repository.record_production_export(
                {
                    "id": export_id,
                    "project_id": project_id,
                    "fingerprint": package["fingerprint"],
                    "contract_version": CONTRACT_VERSION,
                    "created_by": actor.user_id,
                    "created_by_name": actor.display_name,
                    "created_at": created_at,
                    "episode_count": len(selected_numbers),
                    "episode_numbers_json": selected_numbers,
                    "export_scope": provenance["export_scope"],
                    "project_total_episodes": provenance["project_total_episodes"],
                    "workflow_mode": provenance["workflow_mode"],
                    "version_strategy": (
                        "custom" if version_manifest is not None else "current"
                    ),
                    "version_manifest_json": package["version_manifest"],
                    "path": str(path),
                    "size_bytes": len(content),
                    "file_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if str(stored["id"]) != export_id:
            # A concurrent winner registered this version first; the insert is
            # idempotent on ``(project_id, fingerprint, contract_version)`` and
            # returned the stored row. Keep its file and remove only the
            # artifact this request generated.
            path.unlink(missing_ok=True)
            self._verified_path(stored)
            return (
                self._ensure_reuse_audit(
                    self.repository.production_export_public_record(stored),
                    actor,
                    request,
                ),
                False,
            )
        record = self.repository.production_export_public_record(stored)
        self._audit(
            actor, "production_export.create", record, request,
            mark_recorded=export_id,
        )
        return (
            self.repository.production_export_public_record(
                self.repository.get_production_export(export_id) or stored
            ),
            True,
        )

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
            return self.repository.production_export_public_record(
                self.repository.get_production_export(record["id"]) or record
            )
        self._audit(actor, "production_export.reuse", record, request)
        return record

    # ---------------------------------------------------------------- history

    def list_for_project(
        self, project_id: int, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        return self.repository.list_production_exports(
            project_id, page=page, page_size=page_size
        )

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
        row = self.repository.get_production_export(export_id)
        if row is None:
            raise KeyError(export_id)
        record = self.repository.production_export_public_record(row)
        with self.repository.connection_scope() as connection:
            project_name = connection.execute(
                select(creative_projects.c.name).where(
                    creative_projects.c.id == int(row["project_id"]),
                    creative_projects.c.deleted.is_(False),
                )
            ).scalar_one_or_none()
        if project_name is None:
            raise KeyError(str(row["project_id"]))
        if can_access is not None and not can_access(int(row["project_id"])):
            raise PermissionError("production export project is outside the actor scope")
        if record["audit_status"] == "pending":
            raise AuditUnavailable("production export: create audit is still pending")
        path = self._verified_path(row)
        self._audit(actor, "production_export.download_started", record, request)
        name = _safe_project_name(project_name)
        scope = _episode_scope_label(
            record["episode_numbers"], record["export_scope"]
        )
        return path, f"{name}_{scope}_生产包_{str(record['fingerprint'])[:12]}.json"

    # -------------------------------------------------------------- internals

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
                    "episode_numbers": record["episode_numbers"],
                    "export_scope": record["export_scope"],
                    "workflow_mode": record["workflow_mode"],
                    "version_strategy": record["version_strategy"],
                    "version_manifest": record["version_manifest"],
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
            self.repository.mark_production_export_audited(mark_recorded)


def _safe_project_name(name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", str(name or "")).strip("_")
    return cleaned or "项目"


def _episode_scope_label(numbers: list[int], scope: str) -> str:
    if scope == "full":
        return "全剧"
    if not numbers:
        return "部分集"
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return f"第{'、'.join(ranges)}集"
