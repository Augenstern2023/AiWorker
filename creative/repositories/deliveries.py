# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Stage exports, formal deliveries, production packages and production status.

Persistence and query surface only. Everything that touches the filesystem --
writing a delivery artifact, hashing a production package, verifying a
registered file, copying a package -- stays in the service layer, because the
migration plan forbids file I/O inside a database transaction. These methods
therefore never open, read, write or unlink a file, and callers must perform
their file work before entering the transaction that calls them.

Type changes applied to every statement here:

* legacy epoch ``REAL`` timestamps are ``TIMESTAMPTZ``, so writes convert with
  :func:`creative.repositories.base.from_epoch_seconds` (or
  :func:`creative.repositories.base.utc_now`) and reads go through
  :meth:`creative.repositories.base.CreativeRepositoryBase.row_dict`, which
  renders them back as epoch seconds;
* legacy ``*_json TEXT`` columns are ``JSONB``: Python ``list``/``dict`` values
  are bound directly and never ``json.dumps``-ed, and reads already carry the
  parsed value, so nothing is ``json.loads``-ed;
* legacy 0/1 ``INTEGER`` flags are ``BOOLEAN``.

Replacement map (what each method replaces in the frozen SQLite code):

===================================  ==================================================
Method                               Replaces
===================================  ==================================================
``record_stage_export``              ``creative.legacy_sqlite_repository``
                                     ``CreativeRepository.record_stage_export``
                                     (line 6403)
``has_stage_export``                 ``creative.legacy_sqlite_repository``
                                     ``CreativeRepository.has_stage_export``
                                     (line 6420)
``record_delivery``                  ``creative.delivery.DeliveryService.create``
                                     INSERT plus its ``sqlite3.IntegrityError``
                                     replay branch (delivery.py 226-267); the name
                                     ``record_delivery(values, connection=None)``
                                     is the one the migration plan pins
``find_delivery``                    ``DeliveryService._find_existing``
                                     (delivery.py 414-429)
``get_delivery``                     ``DeliveryService.get`` row query and
                                     ``DeliveryService._get_unreconciled``
                                     (delivery.py 386-412 and 686-697)
``list_deliveries``                  ``DeliveryService.list`` row query
                                     (delivery.py 286-313)
``mark_delivery_reconciled``         ``DeliveryService._mark_reconciled``
                                     (delivery.py 668-684)
``delivery_public_record``           ``DeliveryService._public_record``
                                     (delivery.py 718-724)
``record_production_export``         ``ProductionExportService.create`` INSERT and
                                     its ``sqlite3.IntegrityError`` branch
                                     (production_exports.py 203-246)
``find_production_export``           ``ProductionExportService._find_row``
                                     (production_exports.py 339-348)
``get_production_export``            ``ProductionExportService._row``
                                     (production_exports.py 350-355)
``list_production_exports``          ``ProductionExportService.list_for_project``
                                     (production_exports.py 271-295)
``mark_production_export_audited``   the ``audit_recorded_at`` UPDATE at the end of
                                     ``ProductionExportService._audit``
                                     (production_exports.py 405-410)
``production_export_public_record``  ``ProductionExportService._public``
                                     (production_exports.py 412-459), with the
                                     ``episode_numbers_json`` /
                                     ``version_manifest_json`` values already
                                     parsed by JSONB
``record_production_status``         ``creative.production_status``
                                     ``sync_production_status`` write block
                                     (production_status.py 89-94), upsert kept
``get_production_status``            ``creative.production_status``
                                     ``get_production_status``
                                     (production_status.py 97-102)
===================================  ==================================================

Deliberate interface details for the later cutover:

* The delivery readers return the *public* record shape that
  ``DeliveryService._public_record`` produced (``project_id`` as ``str``,
  ``fingerprint`` instead of ``content_fingerprint``, ``actor_user_id`` instead
  of ``actor_id``, ``is_formal`` as ``bool``), plus ``project_name`` from the
  ``creative_projects`` join, so the service can drop its own SQL and its own
  projection helper. ``created_at`` and the three reconciliation timestamps are
  epoch seconds, exactly as ``dict(sqlite3.Row)`` used to yield them.
* The production-export readers return the *raw* column row (including ``path``,
  ``size_bytes`` and ``file_sha256``) because ``ProductionExportService`` still
  has to locate and verify the artifact; ``production_export_public_record``
  then produces the public projection. ``episode_numbers_json`` and
  ``version_manifest_json`` are already the JSONB list/object.
* ``record_delivery`` and ``record_production_export`` are idempotent by their
  named unique keys: the insert uses ``on_conflict_do_nothing`` and the existing
  winner is read back, so a replay returns the original row. A conflict on any
  other constraint still raises :class:`sqlalchemy.exc.IntegrityError`, which is
  what the legacy ``sqlite3.IntegrityError`` handling expected.
* ``record_stage_export`` cannot name a conflict target: the migrated schema
  carries only the non-unique index ``idx_creative_stage_exports_lookup``, and
  runtime DDL is forbidden. It therefore writes through a guarded
  ``INSERT ... SELECT ... WHERE NOT EXISTS`` (plus a targetless
  ``ON CONFLICT DO NOTHING``), which makes a replay a no-op. Two *concurrent*
  first writes can still both pass the guard; a unique index on
  ``(project_id, stage, content_fingerprint)`` would close that window and is a
  schema change, not a repository change.
* ``record_production_status`` is an ``ON CONFLICT ... DO UPDATE`` upsert, not a
  ``DO NOTHING``: the legacy statement refreshed ``payload`` on conflict and
  ``sync_production_status`` returns the freshly stored document, so dropping
  the repeat write would change the observed result. The row count stays one per
  ``(project_id, fingerprint)``.
* ``record_delivery`` needs no file handling; the service must still unlink the
  artifact it wrote when the insert raises, exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Text,
    exists,
    func,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from creative.database_schema import (
    creative_deliveries,
    creative_production_exports,
    creative_projects,
    creative_stage_exports,
    production_status,
)
from creative.repositories.base import from_epoch_seconds, utc_now

#: Mirrors ``creative.production_exports.CONTRACT_VERSION``; the production
#: export identity is ``project id + fingerprint + contract version``.
PRODUCTION_EXPORT_CONTRACT_VERSION = "2.4"

#: Columns of the named unique index ``uq_creative_delivery_request``.
_DELIVERY_REQUEST_KEY: tuple[str, ...] = (
    "project_id",
    "stage",
    "submission_id",
    "format",
    "is_formal",
)

#: Columns of the named unique constraint ``uq_creative_production_exports``.
_PRODUCTION_EXPORT_KEY: tuple[str, ...] = (
    "project_id",
    "fingerprint",
    "contract_version",
)

#: Primary key of ``production_status``.
_PRODUCTION_STATUS_KEY: tuple[str, ...] = ("project_id", "fingerprint")

# The legacy ``creative_stage_exports`` identity is ``(project_id, stage,
# content_fingerprint)``; the migrated schema only carries the non-unique lookup
# index ``idx_creative_stage_exports_lookup``, so there is no index to name in an
# ``ON CONFLICT`` target and the idempotent write is instead a guarded
# ``INSERT ... SELECT ... WHERE NOT EXISTS``.

#: The only delivery columns ``DeliveryService._mark_reconciled`` may touch.
_DELIVERY_RECONCILIATION_COLUMNS: frozenset[str] = frozenset(
    {
        "workflow_task_id",
        "stage_content_fingerprint",
        "stage_export_recorded_at",
        "workflow_completed_at",
        "audit_recorded_at",
    }
)

_DELIVERY_RECONCILIATION_TIMESTAMPS: frozenset[str] = frozenset(
    {
        "stage_export_recorded_at",
        "workflow_completed_at",
        "audit_recorded_at",
    }
)

#: ``creative_deliveries`` columns in the legacy ``SELECT d.*`` order. The
#: PostgreSQL-only ``projection_source_json`` column is deliberately excluded so
#: callers keep observing exactly the legacy dictionary keys.
_DELIVERY_COLUMNS: tuple[Any, ...] = (
    creative_deliveries.c.id,
    creative_deliveries.c.project_id,
    creative_deliveries.c.stage,
    creative_deliveries.c.resource_type,
    creative_deliveries.c.resource_id,
    creative_deliveries.c.resource_version,
    creative_deliveries.c.submission_id,
    creative_deliveries.c.submission_version,
    creative_deliveries.c.format,
    creative_deliveries.c.content_fingerprint,
    creative_deliveries.c.actor_id,
    creative_deliveries.c.workflow_task_id,
    creative_deliveries.c.stage_content_fingerprint,
    creative_deliveries.c.stage_export_recorded_at,
    creative_deliveries.c.workflow_completed_at,
    creative_deliveries.c.audit_recorded_at,
    creative_deliveries.c.path,
    creative_deliveries.c.created_at,
    creative_deliveries.c.resource_status,
    creative_deliveries.c.is_formal,
)

_DELIVERY_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "project_id",
    "stage",
    "resource_type",
    "resource_id",
    "resource_version",
    "submission_id",
    "format",
    "content_fingerprint",
    "actor_id",
    "path",
)

_PRODUCTION_EXPORT_COLUMNS: tuple[Any, ...] = (
    creative_production_exports.c.id,
    creative_production_exports.c.project_id,
    creative_production_exports.c.fingerprint,
    creative_production_exports.c.contract_version,
    creative_production_exports.c.created_by,
    creative_production_exports.c.created_by_name,
    creative_production_exports.c.created_at,
    creative_production_exports.c.episode_count,
    creative_production_exports.c.path,
    creative_production_exports.c.size_bytes,
    creative_production_exports.c.file_sha256,
    creative_production_exports.c.audit_recorded_at,
    creative_production_exports.c.episode_numbers_json,
    creative_production_exports.c.export_scope,
    creative_production_exports.c.project_total_episodes,
    creative_production_exports.c.workflow_mode,
    creative_production_exports.c.version_strategy,
    creative_production_exports.c.version_manifest_json,
    creative_production_exports.c.artifact_format,
    creative_production_exports.c.media_type,
)

_PRODUCTION_EXPORT_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "project_id",
    "fingerprint",
    "created_by",
    "episode_count",
    "path",
    "size_bytes",
    "file_sha256",
)


def _to_timestamp(value: Any) -> datetime | None:
    """Convert a legacy epoch float (or a datetime) into a TIMESTAMPTZ value."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return from_epoch_seconds(value)


class DeliveriesRepository:
    """Stage exports, deliveries, production packages and production status.

    Plain mixin: it assumes only the
    :class:`~creative.repositories.base.CreativeRepositoryBase` contract
    (``connection_scope``, ``transaction_scope``, ``row_dict``, ``rows_dict``)
    and declares no base class of its own.
    """

    # ------------------------------------------------------------ stage export

    def record_stage_export(
        self,
        project_id: int,
        stage: str,
        format: str,
        content_fingerprint: str,
        *,
        connection: Connection | None = None,
    ) -> None:
        """Record one stage export, idempotently.

        Replaces ``CreativeRepository.record_stage_export``. The legacy store
        appended a row on every call and relied on callers checking
        :meth:`has_stage_export` first; the PostgreSQL store keeps the same
        observable rows while making a replay of the same
        ``(project_id, stage, content_fingerprint)`` a no-op.
        """

        local_project_id = int(project_id)
        local_stage = str(stage)
        local_format = str(format)
        local_fingerprint = str(content_fingerprint)
        candidate = (
            select(
                literal(local_project_id, type_=BigInteger).label("project_id"),
                literal(local_stage, type_=Text).label("stage"),
                literal(local_format, type_=Text).label("format"),
                literal(local_fingerprint, type_=Text).label("content_fingerprint"),
                literal(utc_now(), type_=DateTime(timezone=True)).label("exported_at"),
            )
            .where(
                ~exists(
                    select(creative_stage_exports.c.id).where(
                        creative_stage_exports.c.project_id == local_project_id,
                        creative_stage_exports.c.stage == local_stage,
                        creative_stage_exports.c.content_fingerprint
                        == local_fingerprint,
                    )
                )
            )
        )
        statement = (
            pg_insert(creative_stage_exports)
            .from_select(
                [
                    "project_id",
                    "stage",
                    "format",
                    "content_fingerprint",
                    "exported_at",
                ],
                candidate,
            )
            .on_conflict_do_nothing()
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)

    def has_stage_export(
        self,
        project_id: int,
        stage: str,
        content_fingerprint: str,
        *,
        connection: Connection | None = None,
    ) -> bool:
        """Report whether this exact stage export was recorded before."""

        statement = (
            select(creative_stage_exports.c.id)
            .where(
                creative_stage_exports.c.project_id == int(project_id),
                creative_stage_exports.c.stage == str(stage),
                creative_stage_exports.c.content_fingerprint
                == str(content_fingerprint),
            )
            .order_by(creative_stage_exports.c.id)
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            found = active.execute(statement).first()
        return found is not None

    # -------------------------------------------------------------- deliveries

    def record_delivery(
        self,
        values: Mapping[str, Any],
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        """Persist one delivery row and return the stored public record.

        ``values`` is keyed by ``creative_deliveries`` column names, which is
        exactly the value set the legacy
        ``DeliveryService.create`` INSERT bound (``content_fingerprint``,
        ``actor_id``, ``is_formal``, epoch ``created_at``, ...). A replay of the
        same ``(project_id, stage, submission_id, format, is_formal)`` returns
        the row the first attempt stored instead of raising, so the caller's
        artifact handling can stay unchanged.
        """

        payload = self._delivery_insert_values(values)
        statement = (
            pg_insert(creative_deliveries)
            .values(**payload)
            .on_conflict_do_nothing(index_elements=list(_DELIVERY_REQUEST_KEY))
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)
            row = active.execute(
                self._delivery_statement()
                .where(
                    creative_deliveries.c.project_id == payload["project_id"],
                    creative_deliveries.c.stage == payload["stage"],
                    creative_deliveries.c.submission_id == payload["submission_id"],
                    creative_deliveries.c.format == payload["format"],
                    creative_deliveries.c.is_formal == payload["is_formal"],
                )
                .order_by(
                    creative_deliveries.c.created_at.desc().nullslast(),
                    creative_deliveries.c.id.desc().nullslast(),
                )
                .limit(1)
            ).mappings().one_or_none()
        if row is None:  # pragma: no cover - defensive, the row cannot vanish
            raise LookupError("delivery record is unavailable after write")
        return self.delivery_public_record(self.row_dict(row))

    def find_delivery(
        self,
        project_id: int,
        stage: str,
        submission_id: str,
        format: str,
        *,
        is_formal: bool,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Return the stored public record for one delivery request, if any."""

        statement = (
            self._delivery_statement()
            .where(
                creative_deliveries.c.project_id == int(project_id),
                creative_deliveries.c.stage == str(stage),
                creative_deliveries.c.submission_id == str(submission_id),
                creative_deliveries.c.format == str(format),
                creative_deliveries.c.is_formal == bool(is_formal),
            )
            .order_by(
                creative_deliveries.c.created_at.desc().nullslast(),
                creative_deliveries.c.id.desc().nullslast(),
            )
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            row = active.execute(statement).mappings().one_or_none()
        return self.delivery_public_record(self.row_dict(row)) if row is not None else None

    def get_delivery(
        self,
        delivery_id: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Return one delivery public record by id, joined to its project name."""

        statement = (
            self._delivery_statement()
            .where(creative_deliveries.c.id == str(delivery_id))
            .order_by(
                creative_deliveries.c.created_at.desc().nullslast(),
                creative_deliveries.c.id.desc().nullslast(),
            )
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            row = active.execute(statement).mappings().one_or_none()
        return self.delivery_public_record(self.row_dict(row)) if row is not None else None

    def get_delivery_projection_source(
        self, delivery_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        """Return the immutable payload used for reader-specific projection."""

        with self.connection_scope(connection) as active:
            source = active.execute(
                select(creative_deliveries.c.projection_source_json).where(
                    creative_deliveries.c.id == str(delivery_id)
                )
            ).scalar_one_or_none()
        if not isinstance(source, Mapping):
            return {}
        if source.get("schema_version") == "delivery-projection-v1":
            try:
                project = json.loads(str(source["project_json"]))
                payload = json.loads(str(source["payload_json"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return {}
            if not isinstance(project, dict) or not isinstance(payload, dict):
                return {}
            return {"project": project, "payload": payload}
        project = source.get("project")
        payload = source.get("payload")
        if not isinstance(project, Mapping) or not isinstance(payload, Mapping):
            return {}
        return {"project": dict(project), "payload": dict(payload)}

    def list_deliveries(
        self,
        *,
        connection: Connection | None = None,
    ) -> list[dict[str, Any]]:
        """List every delivery newest first, with its project name."""

        statement = self._delivery_statement().order_by(
            creative_deliveries.c.created_at.desc().nullslast(),
            creative_deliveries.c.id.desc().nullslast(),
        )
        with self.connection_scope(connection) as active:
            rows = active.execute(statement).mappings().all()
        return [
            self.delivery_public_record(record) for record in self.rows_dict(rows)
        ]

    def mark_delivery_reconciled(
        self,
        delivery_id: str,
        column: str,
        value: Any,
        *,
        connection: Connection | None = None,
    ) -> None:
        """Write one reconciliation column, refusing any other column name.

        ``stage_export_recorded_at``, ``workflow_completed_at`` and
        ``audit_recorded_at`` accept epoch seconds, as ``time.time()`` handed
        them to the legacy ``UPDATE``.
        """

        if column not in _DELIVERY_RECONCILIATION_COLUMNS:
            raise ValueError("invalid delivery reconciliation column")
        stored = (
            _to_timestamp(value)
            if column in _DELIVERY_RECONCILIATION_TIMESTAMPS
            else value
        )
        statement = (
            update(creative_deliveries)
            .where(creative_deliveries.c.id == str(delivery_id))
            .values({column: stored})
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)

    @staticmethod
    def delivery_public_record(record: Mapping[str, Any]) -> dict[str, Any]:
        """Project a delivery row onto the shape legacy callers received."""

        public = dict(record)
        public["project_id"] = str(public["project_id"])
        public["fingerprint"] = public.pop("content_fingerprint")
        public["actor_user_id"] = public.pop("actor_id")
        public["is_formal"] = bool(public.get("is_formal"))
        return public

    @staticmethod
    def _delivery_statement() -> Any:
        return select(
            *_DELIVERY_COLUMNS,
            creative_projects.c.name.label("project_name"),
        ).select_from(
            creative_deliveries.join(
                creative_projects,
                creative_projects.c.id == creative_deliveries.c.project_id,
            )
        )

    @staticmethod
    def _delivery_insert_values(values: Mapping[str, Any]) -> dict[str, Any]:
        missing = [name for name in _DELIVERY_REQUIRED_FIELDS if values.get(name) is None]
        if missing:
            raise ValueError(f"delivery record is missing {', '.join(missing)}")
        resource_version = int(values["resource_version"])
        payload: dict[str, Any] = {
            "id": str(values["id"]),
            "project_id": int(values["project_id"]),
            "stage": str(values["stage"]),
            "resource_type": str(values["resource_type"]),
            "resource_id": str(values["resource_id"]),
            "resource_version": resource_version,
            "submission_id": str(values["submission_id"]),
            "submission_version": int(
                values.get("submission_version")
                if values.get("submission_version") is not None
                else resource_version
            ),
            "format": str(values["format"]),
            "content_fingerprint": str(values["content_fingerprint"]),
            "actor_id": str(values["actor_id"]),
            "path": str(values["path"]),
            "created_at": _to_timestamp(values.get("created_at")) or utc_now(),
            "workflow_task_id": values.get("workflow_task_id"),
            "stage_content_fingerprint": str(
                values.get("stage_content_fingerprint") or ""
            ),
            "resource_status": str(values.get("resource_status") or ""),
            "is_formal": bool(values.get("is_formal", True)),
        }
        for column in _DELIVERY_RECONCILIATION_TIMESTAMPS:
            if values.get(column) is not None:
                payload[column] = _to_timestamp(values[column])
        if values.get("projection_source_json") is not None:
            source = values["projection_source_json"]
            if not isinstance(source, dict):
                raise ValueError("projection_source_json must be a JSON object")
            payload["projection_source_json"] = source
        return payload

    # ------------------------------------------------------- production export

    def record_production_export(
        self,
        values: Mapping[str, Any],
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        """Persist one production package record and return the stored row.

        ``values`` is keyed by ``creative_production_exports`` column names. The
        JSONB columns take native values: ``episode_numbers_json`` a list,
        ``version_manifest_json`` a dict. A replay of the same ``(project_id,
        fingerprint, contract_version)`` returns the registered winner rather
        than raising, so the caller can detect reuse by comparing the returned
        ``id`` with the id it generated.
        """

        payload = self._production_export_insert_values(values)
        statement = (
            pg_insert(creative_production_exports)
            .values(**payload)
            .on_conflict_do_nothing(index_elements=list(_PRODUCTION_EXPORT_KEY))
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)
            row = active.execute(
                self._production_export_statement()
                .where(
                    creative_production_exports.c.project_id == payload["project_id"],
                    creative_production_exports.c.fingerprint == payload["fingerprint"],
                    creative_production_exports.c.contract_version
                    == payload["contract_version"],
                )
                .order_by(
                    creative_production_exports.c.created_at.desc().nullslast(),
                    creative_production_exports.c.id.desc().nullslast(),
                )
                .limit(1)
            ).mappings().one_or_none()
        if row is None:  # pragma: no cover - defensive, the row cannot vanish
            raise LookupError("production export record is unavailable after write")
        return self.row_dict(row)

    def find_production_export(
        self,
        project_id: int,
        fingerprint: str,
        *,
        contract_version: str = PRODUCTION_EXPORT_CONTRACT_VERSION,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Return the registered package row for one creative fingerprint."""

        statement = (
            self._production_export_statement()
            .where(
                creative_production_exports.c.project_id == int(project_id),
                creative_production_exports.c.fingerprint == str(fingerprint),
                creative_production_exports.c.contract_version
                == str(contract_version),
            )
            .order_by(
                creative_production_exports.c.created_at.desc().nullslast(),
                creative_production_exports.c.id.desc().nullslast(),
            )
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            row = active.execute(statement).mappings().one_or_none()
        return self.row_dict(row) if row is not None else None

    def get_production_export(
        self,
        export_id: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Return one production package row, including its file metadata."""

        statement = (
            self._production_export_statement()
            .where(creative_production_exports.c.id == str(export_id))
            .order_by(
                creative_production_exports.c.created_at.desc().nullslast(),
                creative_production_exports.c.id.desc().nullslast(),
            )
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            row = active.execute(statement).mappings().one_or_none()
        return self.row_dict(row) if row is not None else None

    def list_production_exports(
        self,
        project_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        """Page through one project's package history, newest first."""

        local_page = max(1, int(page))
        local_page_size = min(max(1, int(page_size)), 100)
        total_statement = (
            select(func.count())
            .select_from(creative_production_exports)
            .where(creative_production_exports.c.project_id == int(project_id))
        )
        rows_statement = (
            self._production_export_statement()
            .where(creative_production_exports.c.project_id == int(project_id))
            .order_by(
                creative_production_exports.c.created_at.desc().nullslast(),
                creative_production_exports.c.id.desc().nullslast(),
            )
            .limit(local_page_size)
            .offset((local_page - 1) * local_page_size)
        )
        with self.connection_scope(connection) as active:
            total = active.execute(total_statement).scalar_one()
            rows = active.execute(rows_statement).mappings().all()
        return {
            "items": [
                self.production_export_public_record(record)
                for record in self.rows_dict(rows)
            ],
            "total": int(total),
            "page": local_page,
            "page_size": local_page_size,
        }

    def mark_production_export_audited(
        self,
        export_id: str,
        *,
        connection: Connection | None = None,
    ) -> None:
        """Stamp ``audit_recorded_at`` after the create/reuse audit landed."""

        statement = (
            update(creative_production_exports)
            .where(creative_production_exports.c.id == str(export_id))
            .values(audit_recorded_at=utc_now())
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)

    @staticmethod
    def production_export_public_record(record: Mapping[str, Any]) -> dict[str, Any]:
        """Project a package row onto the shape the handoff flow returns.

        Local replacement for ``ProductionExportService._public``: the two JSON
        columns already arrive as a list and a dict from JSONB, so the legacy
        ``json.loads`` calls disappear while every empty-value default is kept.
        """

        numbers = record.get("episode_numbers_json") or []
        try:
            episode_numbers = [int(number) for number in numbers]
        except (TypeError, ValueError):
            episode_numbers = []
        episode_count = int(record["episode_count"])
        if not episode_numbers and episode_count:
            episode_numbers = list(range(1, episode_count + 1))
        project_total = int(record.get("project_total_episodes") or episode_count)
        export_scope = str(record.get("export_scope") or "")
        if export_scope not in {"full", "partial"}:
            export_scope = (
                "full"
                if episode_numbers == list(range(1, project_total + 1))
                else "partial"
            )
        version_manifest = record.get("version_manifest_json") or {}
        if not isinstance(version_manifest, dict):
            version_manifest = {}
        artifact_format = str(record.get("artifact_format") or "").lower()
        if artifact_format not in {"json", "zip"}:
            artifact_format = (
                "zip" if str(record.get("path") or "").lower().endswith(".zip") else "json"
            )
        media_type = str(record.get("media_type") or "")
        if not media_type:
            media_type = (
                "application/zip" if artifact_format == "zip" else "application/json"
            )
        return {
            "id": record["id"],
            "project_id": str(record["project_id"]),
            "fingerprint": record["fingerprint"],
            "contract_version": record["contract_version"],
            "created_by": record["created_by"],
            "created_by_name": record["created_by_name"],
            "created_at": record["created_at"],
            "episode_count": episode_count,
            "episode_numbers": episode_numbers,
            "export_scope": export_scope,
            "project_total_episodes": project_total,
            "workflow_mode": str(record.get("workflow_mode") or "sequential"),
            "version_strategy": str(record.get("version_strategy") or "current"),
            "version_manifest": version_manifest,
            "artifact_format": artifact_format,
            "media_type": media_type,
            "size_bytes": record["size_bytes"],
            "file_sha256": record["file_sha256"],
            "audit_status": (
                "recorded"
                if record.get("audit_recorded_at") is not None
                else "pending"
            ),
            "download_url": f"/api/creative/production-exports/{record['id']}/download",
        }

    @staticmethod
    def _production_export_statement() -> Any:
        return select(*_PRODUCTION_EXPORT_COLUMNS)

    @staticmethod
    def _production_export_insert_values(values: Mapping[str, Any]) -> dict[str, Any]:
        missing = [
            name
            for name in _PRODUCTION_EXPORT_REQUIRED_FIELDS
            if values.get(name) is None
        ]
        if missing:
            raise ValueError(f"production export record is missing {', '.join(missing)}")
        episode_numbers = values.get("episode_numbers_json") or []
        if not isinstance(episode_numbers, (list, tuple)):
            raise ValueError("episode_numbers_json must be a JSON array")
        version_manifest = values.get("version_manifest_json") or {}
        if not isinstance(version_manifest, dict):
            raise ValueError("version_manifest_json must be a JSON object")
        return {
            "id": str(values["id"]),
            "project_id": int(values["project_id"]),
            "fingerprint": str(values["fingerprint"]),
            "contract_version": str(
                values.get("contract_version") or PRODUCTION_EXPORT_CONTRACT_VERSION
            ),
            "created_by": str(values["created_by"]),
            "created_by_name": str(values.get("created_by_name") or ""),
            "created_at": _to_timestamp(values.get("created_at")) or utc_now(),
            "episode_count": int(values["episode_count"]),
            "path": str(values["path"]),
            "size_bytes": int(values["size_bytes"]),
            "file_sha256": str(values["file_sha256"]),
            "audit_recorded_at": _to_timestamp(values.get("audit_recorded_at")),
            "episode_numbers_json": [int(number) for number in episode_numbers],
            "export_scope": str(values.get("export_scope") or "full"),
            "project_total_episodes": int(values.get("project_total_episodes") or 0),
            "workflow_mode": str(values.get("workflow_mode") or "sequential"),
            "version_strategy": str(values.get("version_strategy") or "current"),
            "version_manifest_json": version_manifest,
            "artifact_format": str(values.get("artifact_format") or "json"),
            "media_type": str(values.get("media_type") or "application/json"),
        }

    # ------------------------------------------------------- production status

    def record_production_status(
        self,
        project_id: int,
        fingerprint: str,
        payload: Mapping[str, Any],
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        """Upsert one downstream status document and return the stored payload.

        Replaces the SQLite write block of
        ``creative.production_status.sync_production_status``. The legacy
        statement was ``ON CONFLICT(project_id, fingerprint) DO UPDATE SET
        payload = excluded.payload``, so a repeat write for the same fingerprint
        refreshes one single row instead of creating a second one, and a
        different fingerprint creates a new row. ``payload`` is a Python mapping
        bound straight into the JSONB column.
        """

        if not isinstance(payload, Mapping):
            raise ValueError("production status payload must be a JSON object")
        local_project_id = int(project_id)
        local_fingerprint = str(fingerprint)
        insert_statement = pg_insert(production_status).values(
            project_id=local_project_id,
            fingerprint=local_fingerprint,
            payload=dict(payload),
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=list(_PRODUCTION_STATUS_KEY),
            set_={"payload": insert_statement.excluded.payload},
        )
        with self.transaction_scope(connection) as active:
            active.execute(statement)
            row = active.execute(
                select(
                    production_status.c.project_id,
                    production_status.c.fingerprint,
                    production_status.c.payload,
                )
                .where(
                    production_status.c.project_id == local_project_id,
                    production_status.c.fingerprint == local_fingerprint,
                )
                .order_by(
                    production_status.c.project_id,
                    production_status.c.fingerprint,
                )
                .limit(1)
            ).mappings().one()
        stored = self.row_dict(row)["payload"]
        return stored if isinstance(stored, dict) else {}

    def get_production_status(
        self,
        project_id: int,
        fingerprint: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Return the stored status document, or ``None`` when never written."""

        statement = (
            select(
                production_status.c.project_id,
                production_status.c.fingerprint,
                production_status.c.payload,
            )
            .where(
                production_status.c.project_id == int(project_id),
                production_status.c.fingerprint == str(fingerprint),
            )
            .order_by(production_status.c.project_id, production_status.c.fingerprint)
            .limit(1)
        )
        with self.connection_scope(connection) as active:
            row = active.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        stored = self.row_dict(row)["payload"]
        return stored if isinstance(stored, dict) else {}


__all__ = [
    "DeliveriesRepository",
    "PRODUCTION_EXPORT_CONTRACT_VERSION",
]
