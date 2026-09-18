# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Idempotent results, generation leases, deletion and Writer rollback state.

This mixin is the PostgreSQL port of the idempotency, reservation-lease,
project deletion and Writer rollback part of
:class:`creative.legacy_sqlite_repository.CreativeRepository`. Method names,
signatures, returned keys, value types, exception classes and Chinese error
strings are copied from the frozen SQLite implementation on purpose, so the two
back ends stay observationally equal during the cutover window.

Type mapping applied here (legacy SQLite -> PostgreSQL):

* epoch ``REAL`` timestamps -> ``TIMESTAMPTZ``; writes convert with
  :func:`creative.repositories.base.from_epoch_seconds` (or ``now()`` for lease
  columns) and reads convert back to epoch seconds through
  :meth:`creative.repositories.base.CreativeRepositoryBase.row_dict`.
* ``*_json TEXT`` -> ``JSONB``; Python ``dict``/``list`` values are handed
  straight to the Core statements, never ``json.dumps``. Reads pop the
  ``*_json`` key and use the value JSONB already decoded, exactly where the
  legacy helpers called ``json.loads(value or "{}")``.
* 0/1 ``INTEGER`` flags -> ``BOOLEAN`` (``deleted``).
* dynamic table names and ``PRAGMA table_info`` -> the explicit table-object
  whitelist in :mod:`creative.database_schema`.

Atomicity and lock order
------------------------

Reserving an idempotency key is the one place where a lost race would execute a
non-database side effect twice. Every read-modify-write over
``creative_operation_results`` and ``creative_generation_operations`` therefore
runs inside the caller-owned transaction (:meth:`transaction_scope`) and
serializes on a PostgreSQL transaction-level advisory lock:

1. operation scope -- ``pg_advisory_xact_lock(<ops ns>, hashtext('operation:<id>'))``
   is taken before the operation row is read, before ``create`` runs and before
   the row is inserted. A concurrent duplicate blocks here; because the winner
   commits before releasing the lock, the loser's next statement (READ
   COMMITTED) observes the winner's committed row, validates its request hash
   and replays it instead of executing ``create`` a second time.
2. generation scope -- ``pg_advisory_xact_lock(<gen ns>, hashtext('generation:<id>'))``
   serializes the same read-modify-write for a generation reservation lease.
3. the row itself -- ``SELECT ... FOR UPDATE`` on ``creative_operation_results``
   is only the fallback for an insert that lost a race the advisory lock
   already makes impossible; it turns "lost the unique key" into "replay the
   winner" instead of surfacing an ``IntegrityError``.
4. ``creative_projects`` and the version rows are read, locked or written by
   the rollback restore; they are always reached after scope 1 and 2.

No method acquires a lower-numbered scope after a higher-numbered one, and no
lock taken by :mod:`creative.repositories.versions` is ever acquired before
scope 1.

Timestamps and leases
---------------------

Lease fields keep the legacy representation (``lease_expires_at`` stays an
epoch float inside the JSONB payload, because callers read it back through the
returned dict). Every lease decision and every lease write uses a single clock,
the database clock: ``SELECT date_part('epoch', now())`` for JSONB leases and
``now()`` for the ``updated_at`` lease column. Client and server clocks are
never compared with each other.

Ordering
--------

Every list query states an explicit ``ORDER BY`` with ``NULLS LAST`` on
nullable columns; PostgreSQL default row order is never relied upon. The Writer
rollback export walks ``_WRITER_ROLLBACK_RESOURCE_TABLES`` and then
``_WRITER_ROLLBACK_TECHNICAL_TABLES`` in that fixed order (the resource group
first, and the project row last) and orders rows by ``id`` -- or by
``created_at`` plus the primary key for the operation tables -- because the
ordered payload feeds :func:`canonical_request_hash`.

File I/O
--------

No external call and no file I/O happens inside a transaction.
:meth:`delete_project` resolves the production-export candidates before it
opens its write transaction, where the legacy implementation did it inside its
``BEGIN IMMEDIATE`` block.

Deliberate divergences from the legacy implementation
-----------------------------------------------------

* A ``*_json`` value that is not a JSON object -- impossible for the columns
  this module reads, because every writer stores an object -- is treated as an
  empty object instead of raising ``AttributeError`` from a ``.get`` call on a
  decoded list.
* :meth:`delete_project` also removes the project-scoped tables that the legacy
  SQLite store did not have (prompt bindings/settings/owned templates,
  ``production_status`` and mode-migration compensations), because their
  foreign keys would otherwise reject the delete. Children declared
  ``ON DELETE CASCADE`` are left to the database.
* The rollback restore order is resource group, technical group, project row.
  The legacy order was technical, resource, project; the two groups have no
  foreign key between them, so the resulting state is identical.
* ``generation_authority_envelope``/``assert_generation_authority`` and their
  private helpers live in this mixin, next to the generation lease they guard,
  but three of the helpers they call are versions-domain members reached through
  ``self``: ``compatible_locked_beats``, ``_outline_authority_content`` and
  ``_outline_content_fingerprint``. The composed repository must therefore
  include the versions mixin as well.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Table,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from creative.database_schema import (
    CREATIVE_METADATA,
    creative_blueprints,
    creative_characters,
    creative_deliveries,
    creative_episodes,
    creative_generation_contexts,
    creative_generation_operations,
    creative_mode_migration_compensations,
    creative_mode_migrations,
    creative_operation_results,
    creative_production_exports,
    creative_project_deletions,
    creative_project_prompt_bindings,
    creative_project_prompt_settings,
    creative_projects,
    creative_prompt_template_revisions,
    creative_prompt_templates,
    creative_relationships,
    creative_resource_heads,
    creative_seeds,
    creative_stage_exports,
    production_status,
    script_optimization_drafts,
    script_optimizations,
    script_reviews,
)
from creative.legacy_sqlite_repository import (
    GENERATION_AUTHORITY_SCHEMA_VERSION,
    GENERATION_OPERATION_LEASE_SECONDS,
    SIDE_EFFECT_OPERATION_LEASE_SECONDS,
    canonical_request_hash,
)
from creative.outline_overview import read_series_overview
from creative.repositories.base import (
    CreativeRepositoryBase,
    epoch_seconds,
    from_epoch_seconds,
    jsonb_copy,
    utc_now,
)


# Advisory-lock namespaces ("CreO" for operations, "CreG" for generation
# leases). They are distinct from the version-chain namespace ("CreV") so an
# unrelated hashtext collision can only over-serialize, never deadlock.
_OPERATION_LOCK_NAMESPACE = 0x4372654F
_GENERATION_LOCK_NAMESPACE = 0x43726547

# Legacy iteration order. ``_export_writer_rollback_state_conn`` feeds the row
# lists of these tables -- in exactly this order -- into the rollback
# fingerprint, so the tuples must not be reordered.
_WRITER_ROLLBACK_RESOURCE_TABLES = (
    "creative_seeds",
    "creative_blueprints",
    "creative_characters",
    "creative_relationships",
    "creative_episodes",
)
_WRITER_ROLLBACK_TECHNICAL_TABLES = (
    "creative_operation_results",
    "creative_generation_operations",
)
_WRITER_PROJECT_DELETION_TABLES = (
    "creative_seeds",
    "creative_blueprints",
    "creative_characters",
    "creative_relationships",
    "creative_episodes",
    "creative_operation_results",
    "creative_generation_operations",
    "creative_stage_exports",
    "script_reviews",
    "script_optimizations",
    "script_optimization_drafts",
    "creative_deliveries",
    "creative_production_exports",
    "creative_video_parameter_recommendations",
    "creative_project_prompt_bindings",
    "creative_project_prompt_settings",
    "creative_prompt_templates",
    "creative_prompt_governance_events",
    "creative_prompt_iteration_drafts",
    "creative_prompt_market_installations",
    "creative_prompt_market_ratings",
    "creative_prompt_ab_experiments",
)
_WRITER_PROJECT_EXTERNAL_TABLES = (
    "creative_stage_exports",
    "script_reviews",
    "script_optimizations",
    "script_optimization_drafts",
    "creative_deliveries",
    "creative_production_exports",
)
# Only these tables carry a ``superseded`` lifecycle that the rollback export
# filters out; ``creative_seeds`` is deliberately absent (legacy behaviour).
_SUPERSEDED_FILTERED_TABLES = frozenset(
    {
        "creative_blueprints",
        "creative_characters",
        "creative_relationships",
        "creative_episodes",
    }
)


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------


def _creative_table(name: str) -> Table:
    """Return one whitelisted creative table object (never a raw table name)."""

    table = CREATIVE_METADATA.tables.get(name)
    if table is None:
        raise ValueError(f"unknown creative table: {name}")
    return table


def _optional_creative_table(name: str) -> Table | None:
    """Return a table object, or ``None`` when the table is not declared.

    The legacy deletion helpers probed ``sqlite_master`` for optional tables and
    recorded an empty row list when the table was absent.
    """

    return CREATIVE_METADATA.tables.get(name)


def _jsonb_object(value: Any) -> Any:
    """Reproduce the legacy ``json.loads(value or "{}")`` result.

    The legacy column held JSON *text*; PostgreSQL hands back the decoded value.
    ``None`` becomes ``{}`` exactly like an empty string did, every other value
    keeps its decoded shape (a JSONB array stays a list, so a following ``.get``
    fails the same way it did on SQLite), and the value is copied so callers
    cannot mutate stored state.
    """

    if value is None:
        return {}
    return jsonb_copy(value)


def _ordered(column: Column[Any]) -> Any:
    """Order by ``column`` with ``NULLS LAST`` whenever it can be null."""

    return column.nulls_last() if column.nullable else column


def _rollback_order(table: Table) -> list[Any]:
    """Deterministic row order for the Writer rollback export.

    Legacy ``ORDER BY rowid`` was SQLite insertion order. Identity tables keep
    that order through ``id``; the two operation tables are keyed by a text
    operation id, so ``created_at`` (the write timestamp) followed by the
    primary key is the insertion-order equivalent.
    """

    if "id" in table.c:
        return [_ordered(table.c.id)]
    order: list[Any] = []
    if "created_at" in table.c:
        order.append(_ordered(table.c.created_at))
    order.extend(_ordered(column) for column in table.primary_key.columns)
    return order


def _server_now_seconds(connection: Connection) -> float:
    """Read the database clock once, so leases never mix clocks."""

    return float(
        connection.execute(select(func.date_part("epoch", func.now()))).scalar_one()
    )


def _database_value(column: Column[Any], value: Any) -> Any:
    """Convert a rolled-back pre-image value back into its column type."""

    if (
        value is not None
        and isinstance(column.type, DateTime)
        and not isinstance(value, datetime)
    ):
        return from_epoch_seconds(value)
    return value


def _resolved_export_paths(
    raw_paths: Sequence[str], export_directory: Path | None
) -> list[str]:
    """Resolve production-export candidates outside the write transaction.

    :func:`pathlib.Path.resolve` touches the filesystem, and file I/O must never
    run inside a transaction, so the candidate list is read and resolved before
    :meth:`OperationsRepository.delete_project` opens its transaction.
    """

    if export_directory is None:
        return []
    resolved: list[str] = []
    for raw_path in raw_paths:
        if not raw_path:
            continue
        try:
            candidate = Path(raw_path).resolve()
        except OSError:
            continue
        if candidate.parent == export_directory:
            resolved.append(str(candidate))
    return resolved


class OperationsRepository:
    """Idempotency, generation leases, project deletion and Writer rollback.

    Plain mixin: it defines no base class and assumes the
    :class:`~creative.repositories.base.CreativeRepositoryBase` contract only.
    """

    # -- advisory locks -----------------------------------------------------

    @staticmethod
    def _lock_operation_scope(connection: Connection, operation_id: str) -> None:
        """Serialize every writer of one ``creative_operation_results`` key."""

        connection.execute(
            select(
                func.pg_advisory_xact_lock(
                    _OPERATION_LOCK_NAMESPACE,
                    func.hashtext(f"operation:{operation_id}"),
                )
            )
        )

    @staticmethod
    def _lock_generation_scope(connection: Connection, operation_id: str) -> None:
        """Serialize every writer of one generation reservation lease."""

        connection.execute(
            select(
                func.pg_advisory_xact_lock(
                    _GENERATION_LOCK_NAMESPACE,
                    func.hashtext(f"generation:{operation_id}"),
                )
            )
        )

    # -- request hashing ----------------------------------------------------

    @staticmethod
    def _operation_request_hash(
        *,
        project_id: int,
        stage: str,
        scope: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        return canonical_request_hash(
            {
                "project_id": int(project_id),
                "stage": stage,
                "scope": scope,
                "payload": payload,
            }
        )

    # -- project and result readers -----------------------------------------

    def _operation_project(
        self,
        connection: Connection,
        project_id: int,
        *,
        include_episodes: bool = False,
    ) -> dict[str, Any]:
        row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if row is None:
            raise ValueError("创作项目不存在")
        project = self.row_dict(row)
        if include_episodes:
            project["blueprints"] = [
                self._blueprint(self.row_dict(item))
                for item in connection.execute(
                    select(creative_blueprints)
                    .where(creative_blueprints.c.project_id == int(project_id))
                    .order_by(_ordered(creative_blueprints.c.id))
                ).mappings().all()
            ]
            project["characters"] = [
                self._creative_item(self.row_dict(item))
                for item in connection.execute(
                    select(creative_characters)
                    .where(creative_characters.c.project_id == int(project_id))
                    .order_by(
                        _ordered(creative_characters.c.version),
                        _ordered(creative_characters.c.id),
                    )
                ).mappings().all()
            ]
            project["relationships"] = [
                self._creative_item(self.row_dict(item))
                for item in connection.execute(
                    select(creative_relationships)
                    .where(creative_relationships.c.project_id == int(project_id))
                    .order_by(
                        _ordered(creative_relationships.c.version),
                        _ordered(creative_relationships.c.id),
                    )
                ).mappings().all()
            ]
            project["episodes"] = [
                self._episode(self.row_dict(item))
                for item in connection.execute(
                    select(creative_episodes)
                    .where(creative_episodes.c.project_id == int(project_id))
                    .order_by(
                        _ordered(creative_episodes.c.stage),
                        _ordered(creative_episodes.c.episode_number),
                        _ordered(creative_episodes.c.version),
                    )
                ).mappings().all()
            ]
        return project

    @staticmethod
    def _read_operation_result(
        connection: Connection,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            select(
                creative_operation_results.c.project_id,
                creative_operation_results.c.stage,
                creative_operation_results.c.request_hash,
                creative_operation_results.c.resource_ref_json,
            ).where(creative_operation_results.c.operation_id == operation_id)
        ).mappings().first()
        if row is None:
            return None
        if (
            int(row["project_id"]) != int(project_id)
            or str(row["stage"]) != stage
            or str(row["request_hash"]) != request_hash
        ):
            raise ValueError("IDEMPOTENCY_KEY_REUSED")
        return _jsonb_object(row["resource_ref_json"])

    def _hydrate_resource_ref(
        self,
        connection: Connection,
        project_id: int,
        resource_ref: dict[str, Any],
    ) -> Any:
        resource_type = str(resource_ref.get("resource_type") or "")

        def restore_operation_metadata(resource: Any) -> Any:
            if isinstance(resource, dict) and "revision" in resource_ref:
                resource = dict(resource)
                resource["revision"] = int(resource_ref["revision"])
            return resource

        if resource_type in {"brief", "blueprints"} and "resource_ids" in resource_ref:
            resources = []
            for resource_id in resource_ref.get("resource_ids") or []:
                row = connection.execute(
                    select(creative_blueprints).where(
                        creative_blueprints.c.id == int(resource_id),
                        creative_blueprints.c.project_id == int(project_id),
                    )
                ).mappings().first()
                if row is not None:
                    resources.append(self._blueprint(self.row_dict(row)))
            return resources
        if resource_type in {"brief", "blueprint"}:
            row = connection.execute(
                select(creative_blueprints).where(
                    creative_blueprints.c.id == int(resource_ref["resource_id"]),
                    creative_blueprints.c.project_id == int(project_id),
                )
            ).mappings().first()
            return restore_operation_metadata(
                self._blueprint(self.row_dict(row)) if row is not None else None
            )
        if resource_type in {"characters", "character_batch"}:
            batch_ref = str(resource_ref.get("resource_id") or resource_ref["batch_id"])
            ref_project_id, separator, batch_id = batch_ref.partition(":")
            if not separator:
                batch_id = ref_project_id
            elif int(ref_project_id) != int(project_id):
                raise ValueError("operation resource project mismatch")
            characters = [
                self._creative_item(self.row_dict(row))
                for row in connection.execute(
                    select(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == batch_id,
                    )
                    .order_by(_ordered(creative_characters.c.id))
                ).mappings().all()
            ]
            relationships = [
                self._creative_item(self.row_dict(row))
                for row in connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == batch_id,
                    )
                    .order_by(_ordered(creative_relationships.c.id))
                ).mappings().all()
            ]
            return restore_operation_metadata(
                {
                    "batch_id": batch_id,
                    "characters": characters,
                    "relationships": relationships,
                }
            )
        if resource_type in {"outline", "outline_batch"}:
            batch_ref = str(resource_ref.get("resource_id") or resource_ref["batch_id"])
            ref_project_id, separator, batch_id = batch_ref.partition(":")
            if not separator:
                batch_id = ref_project_id
            elif int(ref_project_id) != int(project_id):
                raise ValueError("operation resource project mismatch")
            episodes = [
                self._episode(self.row_dict(row))
                for row in connection.execute(
                    select(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == batch_id,
                    )
                    .order_by(
                        _ordered(creative_episodes.c.episode_number),
                        _ordered(creative_episodes.c.version),
                    )
                ).mappings().all()
            ]
            overview = (
                read_series_overview(
                    (episodes[0].get("content") or {}).get("series_overview")
                )
                if episodes
                else None
            )
            return restore_operation_metadata(
                {
                    "batch_id": batch_id,
                    "series_overview": overview,
                    "episodes": episodes,
                }
            )
        if resource_type in {"beats", "script", "episode"}:
            row = connection.execute(
                select(creative_episodes).where(
                    creative_episodes.c.id == int(resource_ref["resource_id"]),
                    creative_episodes.c.project_id == int(project_id),
                )
            ).mappings().first()
            return restore_operation_metadata(
                self._episode(self.row_dict(row)) if row is not None else None
            )
        return dict(resource_ref)

    # -- idempotent execution -----------------------------------------------

    def _run_idempotent_operation(
        self,
        connection: Connection,
        *,
        operation_id: str,
        project_id: int | None,
        stage: str,
        request_hash: str | Callable[[int], str],
        create: Callable[[Connection], dict[str, Any]],
        resolve_project_id: Callable[[Connection], int] | None = None,
    ) -> Any:
        """Run ``create`` at most once per ``operation_id``.

        Lock order (see the module docstring): scope 1 -- the operation
        advisory lock -- is taken before the row is read and is held until the
        caller's transaction ends, so a concurrent duplicate either waits and
        then replays the winner's committed row, or fails with the same
        exception the legacy implementation raised.
        """

        self._lock_operation_scope(connection, operation_id)
        operation_row = connection.execute(
            select(creative_operation_results.c.project_id).where(
                creative_operation_results.c.operation_id == operation_id
            )
        ).mappings().first()
        if operation_row is not None and project_id is None:
            project_id = int(operation_row["project_id"])
        if project_id is None:
            if resolve_project_id is None:
                raise ValueError("operation project is required")
            project_id = int(resolve_project_id(connection))
        resolved_hash = (
            request_hash(project_id) if callable(request_hash) else request_hash
        )
        existing = self._read_operation_result(
            connection,
            operation_id=operation_id,
            project_id=project_id,
            stage=stage,
            request_hash=resolved_hash,
        )
        if existing is not None:
            return self._hydrate_resource_ref(connection, project_id, existing)
        resource_ref = create(connection)
        inserted = connection.execute(
            pg_insert(creative_operation_results)
            .values(
                operation_id=operation_id,
                project_id=int(project_id),
                stage=stage,
                request_hash=resolved_hash,
                resource_ref_json=resource_ref,
                created_at=from_epoch_seconds(_server_now_seconds(connection)),
            )
            .on_conflict_do_nothing(index_elements=["operation_id"])
        ).rowcount
        if not inserted:
            # The advisory lock above already serializes this operation id, so
            # this branch only exists to turn a lost unique key into a replay
            # instead of an IntegrityError.
            winner = connection.execute(
                select(creative_operation_results)
                .where(creative_operation_results.c.operation_id == operation_id)
                .with_for_update()
            ).mappings().one()
            return self._hydrate_resource_ref(
                connection,
                int(winner["project_id"]),
                _jsonb_object(winner["resource_ref_json"]),
            )
        return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def record_operation_result(
        self,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
        resource_ref: dict[str, Any],
    ) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            self._lock_operation_scope(connection, operation_id)
            existing = self._read_operation_result(
                connection,
                operation_id=operation_id,
                project_id=project_id,
                stage=stage,
                request_hash=request_hash,
            )
            if existing is not None:
                return existing
            connection.execute(
                pg_insert(creative_operation_results)
                .values(
                    operation_id=operation_id,
                    project_id=int(project_id),
                    stage=stage,
                    request_hash=request_hash,
                    resource_ref_json=resource_ref,
                    created_at=from_epoch_seconds(_server_now_seconds(connection)),
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
            )
        return dict(resource_ref)

    def hydrate_operation_result(self, operation_id: str) -> Any:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(
                    creative_operation_results.c.project_id,
                    creative_operation_results.c.resource_ref_json,
                ).where(creative_operation_results.c.operation_id == operation_id)
            ).mappings().first()
            if row is None:
                return None
            return self._hydrate_resource_ref(
                connection,
                int(row["project_id"]),
                _jsonb_object(row["resource_ref_json"]),
            )

    def has_locked_stage_resource(
        self,
        project_id: int,
        stage: str,
        *,
        episode_number: int | None = None,
    ) -> bool:
        with self.connection_scope() as connection:
            if stage in {"characters", "outline"}:
                column = (
                    creative_projects.c.character_batch_id
                    if stage == "characters"
                    else creative_projects.c.outline_batch_id
                )
                row = connection.execute(
                    select(column.label("resource_id")).where(
                        creative_projects.c.id == int(project_id),
                        creative_projects.c.deleted.is_(False),
                    )
                ).mappings().first()
                return bool(row and row["resource_id"])
            if stage not in {"beats", "script"} or episode_number is None:
                return False
            row = connection.execute(
                select(creative_episodes.c.id)
                .where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == stage,
                    creative_episodes.c.episode_number == int(episode_number),
                    creative_episodes.c.status.in_(("locked", "approved")),
                )
                .order_by(_ordered(creative_episodes.c.id))
                .limit(1)
            ).first()
            return row is not None

    # -- side-effect reservations -------------------------------------------

    def reserve_side_effect_operation(
        self,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Reserve an idempotency key before a non-database side effect."""

        with self.transaction_scope() as connection:
            self._lock_operation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_operation_results).where(
                    creative_operation_results.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is not None:
                if (
                    int(row["project_id"]) != int(project_id)
                    or str(row["stage"]) != stage
                    or str(row["request_hash"]) != request_hash
                ):
                    raise ValueError("IDEMPOTENCY_KEY_REUSED")
                value = _jsonb_object(row["resource_ref_json"])
                if value.get("_operation_state") == "pending":
                    now = _server_now_seconds(connection)
                    raw_lease_expires_at = value.get("lease_expires_at")
                    lease_expires_at = float(
                        raw_lease_expires_at
                        if raw_lease_expires_at is not None
                        else epoch_seconds(row["created_at"])
                        + SIDE_EFFECT_OPERATION_LEASE_SECONDS
                    )
                    if lease_expires_at > now:
                        raise RuntimeError("OPERATION_IN_PROGRESS")
                    value = {
                        "_operation_state": "pending",
                        "owner_id": owner_id,
                        "lease_expires_at": now
                        + SIDE_EFFECT_OPERATION_LEASE_SECONDS,
                        "recovered_from_owner_id": value.get("owner_id"),
                    }
                    connection.execute(
                        update(creative_operation_results)
                        .where(creative_operation_results.c.operation_id == operation_id)
                        .values(
                            resource_ref_json=value,
                            created_at=from_epoch_seconds(now),
                        )
                    )
                    return {
                        "state": "reserved",
                        "owner_id": owner_id,
                        "recovered": True,
                    }
                return {"state": "complete", "result": value}
            now = _server_now_seconds(connection)
            connection.execute(
                pg_insert(creative_operation_results).values(
                    operation_id=operation_id,
                    project_id=int(project_id),
                    stage=stage,
                    request_hash=request_hash,
                    resource_ref_json={
                        "_operation_state": "pending",
                        "owner_id": owner_id,
                        "lease_expires_at": now
                        + SIDE_EFFECT_OPERATION_LEASE_SECONDS,
                    },
                    created_at=from_epoch_seconds(now),
                )
            )
        return {"state": "reserved", "owner_id": owner_id}

    def finalize_side_effect_operation(
        self, *, operation_id: str, owner_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            self._lock_operation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_operation_results.c.resource_ref_json).where(
                    creative_operation_results.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is None:
                raise RuntimeError("operation reservation is missing")
            current = _jsonb_object(row["resource_ref_json"])
            if current.get("_operation_state") != "pending":
                return current
            if current.get("owner_id") != owner_id:
                raise RuntimeError("OPERATION_IN_PROGRESS")
            connection.execute(
                update(creative_operation_results)
                .where(creative_operation_results.c.operation_id == operation_id)
                # Workflow results contain datetime values. Preserve the legacy
                # snapshot encoding before passing the object to JSONB.
                .values(resource_ref_json=json.loads(json.dumps(result, default=str)))
            )
        return dict(result)

    def renew_side_effect_operation(
        self, *, operation_id: str, owner_id: str
    ) -> bool:
        with self.transaction_scope() as connection:
            self._lock_operation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_operation_results.c.resource_ref_json).where(
                    creative_operation_results.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is None:
                return False
            current = _jsonb_object(row["resource_ref_json"])
            if (
                current.get("_operation_state") != "pending"
                or current.get("owner_id") != owner_id
            ):
                return False
            current["lease_expires_at"] = (
                _server_now_seconds(connection)
                + SIDE_EFFECT_OPERATION_LEASE_SECONDS
            )
            connection.execute(
                update(creative_operation_results)
                .where(creative_operation_results.c.operation_id == operation_id)
                .values(resource_ref_json=current)
            )
        return True

    def abandon_side_effect_operation(
        self, *, operation_id: str, owner_id: str
    ) -> None:
        with self.transaction_scope() as connection:
            self._lock_operation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_operation_results.c.resource_ref_json).where(
                    creative_operation_results.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is None:
                return
            current = _jsonb_object(row["resource_ref_json"])
            if (
                current.get("_operation_state") == "pending"
                and current.get("owner_id") == owner_id
            ):
                connection.execute(
                    delete(creative_operation_results).where(
                        creative_operation_results.c.operation_id == operation_id
                    )
                )

    # -- generation reservations --------------------------------------------

    def reserve_generation_operation(
        self,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
        owner_id: str,
        predecessor_owner_id: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            self._lock_generation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_generation_operations).where(
                    creative_generation_operations.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is not None:
                if (
                    int(row["project_id"]) != int(project_id)
                    or str(row["stage"]) != stage
                    or str(row["request_hash"]) != request_hash
                ):
                    raise ValueError("IDEMPOTENCY_KEY_REUSED")
                current_owner_id = str(row["owner_id"])
                status = str(row["status"])
                now = _server_now_seconds(connection)
                if status == "active" and current_owner_id != owner_id:
                    lease_expired = (
                        epoch_seconds(row["updated_at"])
                        + GENERATION_OPERATION_LEASE_SECONDS
                        <= now
                    )
                    if not lease_expired or predecessor_owner_id != current_owner_id:
                        raise RuntimeError("OPERATION_IN_PROGRESS")
                    connection.execute(
                        update(creative_generation_operations)
                        .where(
                            creative_generation_operations.c.operation_id
                            == operation_id
                        )
                        .values(owner_id=owner_id, updated_at=from_epoch_seconds(now))
                    )
                elif status == "active":
                    connection.execute(
                        update(creative_generation_operations)
                        .where(
                            creative_generation_operations.c.operation_id
                            == operation_id
                        )
                        .values(updated_at=from_epoch_seconds(now))
                    )
                if status == "failed" and current_owner_id != owner_id:
                    if predecessor_owner_id != current_owner_id:
                        raise RuntimeError("OPERATION_IN_PROGRESS")
                    connection.execute(
                        update(creative_generation_operations)
                        .where(
                            creative_generation_operations.c.operation_id
                            == operation_id
                        )
                        .values(
                            owner_id=owner_id,
                            status="active",
                            updated_at=from_epoch_seconds(now),
                        )
                    )
                    status = "active"
                elif status == "failed":
                    connection.execute(
                        update(creative_generation_operations)
                        .where(
                            creative_generation_operations.c.operation_id
                            == operation_id
                        )
                        .values(status="active", updated_at=from_epoch_seconds(now))
                    )
                    status = "active"
                return {
                    "batch_id": str(row["batch_id"]),
                    "status": status,
                    "result": _jsonb_object(row["result_json"]),
                }
            batch_id = uuid.uuid4().hex
            now = _server_now_seconds(connection)
            timestamp = from_epoch_seconds(now)
            connection.execute(
                pg_insert(creative_generation_operations).values(
                    operation_id=operation_id,
                    project_id=int(project_id),
                    stage=stage,
                    request_hash=request_hash,
                    owner_id=owner_id,
                    batch_id=batch_id,
                    status="active",
                    result_json={},
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return {"batch_id": batch_id, "status": "active", "result": {}}

    def renew_generation_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        owner_id: str,
    ) -> bool:
        with self.transaction_scope() as connection:
            cursor = connection.execute(
                update(creative_generation_operations)
                .where(
                    creative_generation_operations.c.operation_id == operation_id,
                    creative_generation_operations.c.request_hash == request_hash,
                    creative_generation_operations.c.owner_id == owner_id,
                    creative_generation_operations.c.status == "active",
                )
                .values(updated_at=func.now())
            )
        return cursor.rowcount == 1

    def fail_generation_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        owner_id: str,
    ) -> bool:
        with self.transaction_scope() as connection:
            cursor = connection.execute(
                update(creative_generation_operations)
                .where(
                    creative_generation_operations.c.operation_id == operation_id,
                    creative_generation_operations.c.request_hash == request_hash,
                    creative_generation_operations.c.owner_id == owner_id,
                    creative_generation_operations.c.status == "active",
                )
                .values(status="failed", updated_at=func.now())
            )
        return cursor.rowcount == 1

    def complete_generation_operation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        batch_id: str,
        result: dict[str, Any],
        owner_id: str | None = None,
        expected_authority: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction_scope() as connection:
            self._lock_generation_scope(connection, operation_id)
            row = connection.execute(
                select(creative_generation_operations.c.project_id).where(
                    creative_generation_operations.c.operation_id == operation_id
                )
            ).mappings().first()
            if row is None:
                raise ValueError("generation operation reservation changed")
            self._assert_generation_authority_conn(
                connection, int(row["project_id"]), expected_authority
            )
            statement = (
                update(creative_generation_operations)
                .where(
                    creative_generation_operations.c.operation_id == operation_id,
                    creative_generation_operations.c.request_hash == request_hash,
                    creative_generation_operations.c.batch_id == batch_id,
                )
                .values(status="complete", result_json=result, updated_at=func.now())
            )
            if owner_id is not None:
                statement = statement.where(
                    creative_generation_operations.c.owner_id == owner_id
                )
            cursor = connection.execute(statement)
            if cursor.rowcount != 1:
                raise ValueError("generation operation reservation changed")

    def validate_generation_reservation(
        self,
        *,
        operation_id: str,
        request_hash: str,
        batch_id: str,
        owner_id: str | None = None,
    ) -> None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(
                    creative_generation_operations.c.request_hash,
                    creative_generation_operations.c.batch_id,
                    creative_generation_operations.c.owner_id,
                ).where(
                    creative_generation_operations.c.operation_id == operation_id
                )
            ).mappings().first()
        if (
            row is None
            or str(row["request_hash"]) != request_hash
            or str(row["batch_id"]) != batch_id
            or (owner_id is not None and str(row["owner_id"]) != owner_id)
        ):
            raise ValueError("generation operation reservation changed")

    @staticmethod
    def _validate_generation_reservation_conn(
        connection: Connection,
        *,
        operation_id: str,
        request_hash: str,
        batch_id: str,
        owner_id: str | None = None,
    ) -> None:
        row = connection.execute(
            select(
                creative_generation_operations.c.request_hash,
                creative_generation_operations.c.batch_id,
                creative_generation_operations.c.status,
                creative_generation_operations.c.owner_id,
            ).where(creative_generation_operations.c.operation_id == operation_id)
        ).mappings().first()
        if (
            row is None
            or str(row["request_hash"]) != request_hash
            or str(row["batch_id"]) != batch_id
            or str(row["status"]) != "active"
            or (owner_id is not None and str(row["owner_id"]) != owner_id)
        ):
            raise ValueError("generation operation reservation changed")

    # -- generation authority envelopes -------------------------------------
    #
    # The authority envelope is the receipt a caller stores when it reserves a
    # generation lease: it fingerprints the project revision, the locked brief,
    # the character batch and the outline batch, plus the current compatible
    # locked Beats of the episodes in scope. Before a server-bound result is
    # accepted, ``assert_generation_authority`` recomputes the envelope and
    # refuses a mismatch.
    #
    # Cross-domain dependency: ``compatible_locked_beats``,
    # ``_outline_authority_content`` and ``_outline_content_fingerprint`` are the
    # versions-domain helpers (``creative/repositories/versions.py``); they are
    # reached through ``self`` exactly like the legacy call sites did, so the
    # composed repository must include that mixin.
    #
    # Fingerprints are byte-for-byte identical to the SQLite implementation
    # because every payload is hashed with :func:`canonical_request_hash`, whose
    # ``sort_keys=True`` makes the JSONB key normalization of PostgreSQL
    # irrelevant.

    def generation_authority_envelope(
        self,
        project_id: int,
        episode_number: int | None = None,
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        with self.connection_scope() as connection:
            return self._generation_authority_envelope_conn(
                connection, project_id, episode_number, stage=stage
            )

    def _generation_authority_envelope_conn(
        self,
        connection: Connection,
        project_id: int,
        episode_number: int | None = None,
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        if stage not in {None, "brief", "characters", "outline", "beats", "script"}:
            raise ValueError("invalid generation authority stage")
        project = connection.execute(
            select(
                creative_projects.c.revision,
                creative_projects.c.locked_brief_id,
                creative_projects.c.character_batch_id,
                creative_projects.c.outline_batch_id,
            ).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if project is None:
            raise ValueError("创作项目不存在")
        brief = connection.execute(
            select(creative_blueprints.c.id, creative_blueprints.c.content_json)
            .where(
                creative_blueprints.c.id == project["locked_brief_id"],
                creative_blueprints.c.project_id == int(project_id),
            )
        ).mappings().first()
        character_batch_id = project["character_batch_id"]
        outline_batch_id = project["outline_batch_id"]
        characters = connection.execute(
            select(creative_characters.c.content_json)
            .where(
                creative_characters.c.project_id == int(project_id),
                creative_characters.c.batch_id == character_batch_id,
            )
            .order_by(
                _ordered(creative_characters.c.version),
                _ordered(creative_characters.c.id),
            )
        ).scalars().all()
        relationships = connection.execute(
            select(creative_relationships.c.content_json)
            .where(
                creative_relationships.c.project_id == int(project_id),
                creative_relationships.c.batch_id == character_batch_id,
            )
            .order_by(
                _ordered(creative_relationships.c.version),
                _ordered(creative_relationships.c.id),
            )
        ).scalars().all()
        outlines = connection.execute(
            select(creative_episodes.c.content_json)
            .where(
                creative_episodes.c.project_id == int(project_id),
                creative_episodes.c.stage == "outline",
                creative_episodes.c.batch_id == outline_batch_id,
            )
            .order_by(
                _ordered(creative_episodes.c.episode_number),
                _ordered(creative_episodes.c.version),
            )
        ).scalars().all()
        authority_project = self._operation_project(
            connection, project_id, include_episodes=True
        )
        if episode_number is None:
            beat_numbers = sorted(
                {
                    int(item.get("episode_number") or 0)
                    for item in authority_project["episodes"]
                    if item.get("stage") == "beats"
                }
            )
        elif stage == "beats":
            beat_numbers = list(range(1, int(episode_number)))
        elif stage == "script":
            beat_numbers = list(range(1, int(episode_number) + 1))
        else:
            # Historical callers scoped only the target episode.
            beat_numbers = [int(episode_number)]
        latest_beats = {
            int(item["episode_number"]): item
            for item in self.compatible_locked_beats(
                authority_project, beat_numbers
            )
        }

        def content(value: Any) -> Any:
            return _jsonb_object(value)

        authority = {
            "project_id": int(project_id),
            "revision": int(project["revision"] or 0),
            "brief": {
                "id": int(brief["id"]) if brief is not None else None,
                "fingerprint": canonical_request_hash(content(brief["content_json"]))
                if brief is not None
                else None,
            },
            "characters": {
                "batch_id": character_batch_id,
                "fingerprint": canonical_request_hash(
                    {
                        "characters": [content(item) for item in characters],
                        "relationships": [content(item) for item in relationships],
                    }
                )
                if character_batch_id
                else None,
            },
            "outline": {
                "batch_id": outline_batch_id,
                "fingerprint": canonical_request_hash(
                    [
                        self._outline_authority_content(content(item))
                        for item in outlines
                    ]
                )
                if outline_batch_id
                else None,
            },
            "locked_beats": [
                {
                    "episode_number": number,
                    "id": int(item["id"]),
                    "fingerprint": canonical_request_hash(
                        item.get("content") or {}
                    ),
                }
                for number, item in sorted(latest_beats.items())
            ],
            "authority_schema_version": GENERATION_AUTHORITY_SCHEMA_VERSION,
        }
        if episode_number is not None:
            authority["episode_number"] = int(episode_number)
        if stage is not None:
            authority["stage"] = stage
        return authority

    def _outline_authority_fingerprints_conn(
        self,
        connection: Connection,
        project_id: int,
    ) -> set[str | None]:
        project = connection.execute(
            select(creative_projects.c.outline_batch_id).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).first()
        batch_id = project[0] if project is not None else None
        if not batch_id:
            return {None}
        rows = connection.execute(
            select(creative_episodes.c.content_json)
            .where(
                creative_episodes.c.project_id == int(project_id),
                creative_episodes.c.stage == "outline",
                creative_episodes.c.batch_id == batch_id,
            )
            .order_by(
                _ordered(creative_episodes.c.episode_number),
                _ordered(creative_episodes.c.version),
            )
        ).scalars().all()
        contents = [_jsonb_object(row) for row in rows]
        normalized = [
            self._outline_authority_content(content) for content in contents
        ]
        return {
            canonical_request_hash(contents),
            canonical_request_hash(normalized),
            canonical_request_hash(
                [self._outline_content_fingerprint(content) for content in contents]
            ),
        }

    def _generation_authority_matches_conn(
        self,
        connection: Connection,
        project_id: int,
        expected_authority: dict[str, Any],
    ) -> bool:
        scoped_episode = expected_authority.get("episode_number")
        scoped_stage = expected_authority.get("stage")
        if scoped_episode is not None and scoped_stage is None:
            raise ValueError("server source authority stage is required")
        current = self._generation_authority_envelope_conn(
            connection,
            project_id,
            int(scoped_episode) if scoped_episode is not None else None,
            stage=str(scoped_stage) if scoped_stage is not None else None,
        )
        if canonical_request_hash(current) == canonical_request_hash(
            expected_authority
        ):
            return True
        if expected_authority.get("authority_schema_version") is not None:
            return False

        # Pre-upgrade envelopes used the same authority fields but hashed the
        # full outline content (including overview metadata). Accept only a
        # known historical outline representation while every other authority
        # field remains byte-for-byte canonical. Stage-less episode envelopes
        # are rejected above because their old Beats scope was unsafe.
        expected_legacy = jsonb_copy(expected_authority)
        current_legacy = jsonb_copy(current)
        current_legacy.pop("authority_schema_version", None)
        expected_outline = expected_legacy.get("outline")
        current_outline = current_legacy.get("outline")
        if not isinstance(expected_outline, dict) or not isinstance(
            current_outline, dict
        ):
            return False
        expected_fingerprint = expected_outline.get("fingerprint")
        expected_outline["fingerprint"] = None
        current_outline["fingerprint"] = None
        return (
            canonical_request_hash(expected_legacy)
            == canonical_request_hash(current_legacy)
            and expected_fingerprint
            in self._outline_authority_fingerprints_conn(connection, project_id)
        )

    def assert_generation_authority(
        self,
        project_id: int,
        expected_authority: dict[str, Any],
    ) -> None:
        with self.connection_scope() as connection:
            if not self._generation_authority_matches_conn(
                connection, project_id, expected_authority
            ):
                raise ValueError("server source authority changed")

    def _assert_generation_authority_conn(
        self,
        connection: Connection,
        project_id: int,
        expected_authority: dict[str, Any] | None,
    ) -> None:
        if expected_authority is None:
            return
        if not self._generation_authority_matches_conn(
            connection, project_id, expected_authority
        ):
            raise ValueError("server source authority changed")

    # -- Writer rollback export ---------------------------------------------

    @staticmethod
    def _writer_rollback_table_rows(
        connection: Connection, table: str, project_id: int
    ) -> list[dict[str, Any]]:
        target = _creative_table(table)
        statement = select(target).where(target.c.project_id == int(project_id))
        if table in _SUPERSEDED_FILTERED_TABLES:
            statement = statement.where(target.c.status != "superseded")
        statement = statement.order_by(*_rollback_order(target))
        return [
            CreativeRepositoryBase.row_dict(row)
            for row in connection.execute(statement).mappings().all()
        ]

    def _export_writer_rollback_state_conn(
        self, connection: Connection, project_id: int
    ) -> dict[str, Any]:
        row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if row is None:
            raise ValueError("创作项目不存在")
        tables: dict[str, list[dict[str, Any]]] = {
            "creative_projects": [self.row_dict(row)]
        }
        for table in (
            *_WRITER_ROLLBACK_RESOURCE_TABLES,
            *_WRITER_ROLLBACK_TECHNICAL_TABLES,
        ):
            tables[table] = self._writer_rollback_table_rows(
                connection, table, project_id
            )
        payload = {"project_id": int(project_id), "tables": tables}
        return {**payload, "fingerprint": canonical_request_hash(payload)}

    def export_writer_rollback_state(self, project_id: int) -> dict[str, Any]:
        """Capture the project-local rows a Writer action is allowed to mutate."""

        with self.connection_scope() as connection:
            return self._export_writer_rollback_state_conn(connection, project_id)

    def writer_rollback_fingerprint(self, project_id: int) -> str:
        return str(self.export_writer_rollback_state(project_id)["fingerprint"])

    def _writer_project_deletion_state_conn(
        self, connection: Connection, project_id: int
    ) -> dict[str, Any]:
        row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if row is None:
            raise ValueError("创作项目不存在")
        tables: dict[str, list[dict[str, Any]]] = {
            "creative_projects": [self.row_dict(row)]
        }
        for table in _WRITER_PROJECT_DELETION_TABLES:
            target = _optional_creative_table(table)
            if target is None:
                tables[table] = []
                continue
            statement = (
                select(target)
                .where(target.c.project_id == int(project_id))
                .order_by(*_rollback_order(target))
            )
            tables[table] = [
                self.row_dict(item)
                for item in connection.execute(statement).mappings().all()
            ]

        # Project-owned prompt templates and A/B experiments have dependent
        # rows whose tables do not carry ``project_id`` themselves. Those rows
        # are nevertheless removed by the explicit template cleanup or by
        # ``ON DELETE CASCADE`` when the project is deleted, so they belong in
        # the optimistic deletion fingerprint as well.
        owned_template_ids = [
            str(item["id"])
            for item in tables.get("creative_prompt_templates", [])
        ]
        revision_table = _creative_table("creative_prompt_template_revisions")
        revision_statement = select(revision_table).where(
            revision_table.c.template_id.in_(owned_template_ids)
        )
        if owned_template_ids:
            revision_statement = revision_statement.order_by(
                *_rollback_order(revision_table)
            )
            revision_rows = connection.execute(revision_statement).mappings().all()
        else:
            revision_rows = []
        tables["creative_prompt_template_revisions"] = [
            self.row_dict(item) for item in revision_rows
        ]

        experiment_ids = [
            str(item["id"])
            for item in tables.get("creative_prompt_ab_experiments", [])
        ]
        arm_table = _creative_table("creative_prompt_ab_arms")
        arm_statement = select(arm_table).where(
            arm_table.c.experiment_id.in_(experiment_ids)
        )
        if experiment_ids:
            arm_statement = arm_statement.order_by(*_rollback_order(arm_table))
            arm_rows = connection.execute(arm_statement).mappings().all()
        else:
            arm_rows = []
        tables["creative_prompt_ab_arms"] = [
            self.row_dict(item) for item in arm_rows
        ]
        payload = {"project_id": int(project_id), "tables": tables}
        return {**payload, "fingerprint": canonical_request_hash(payload)}

    def writer_project_deletion_fingerprint(self, project_id: int) -> str:
        """Fingerprint every creative row that project deletion would remove."""

        with self.connection_scope() as connection:
            state = self._writer_project_deletion_state_conn(connection, project_id)
        return str(state["fingerprint"])

    def _writer_project_external_use_state_conn(
        self, connection: Connection, project_id: int, action_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if row is None:
            raise ValueError("创作项目不存在")
        project = self.row_dict(row)
        project_metadata = {
            key: project[key]
            for key in (
                "topic_id",
                "breakdown_id",
                "name",
                "genre",
                "target_platform",
                "target_audience",
                "target_episode_count",
                "episode_duration_seconds",
                "creation_workflow_task_id",
                "creation_request_id",
                "created_by",
                "creation_mode",
                "bootstrap_status",
            )
        }
        owned_refs: dict[str, set[Any]] = {
            "brief_ids": set(),
            "character_batches": set(),
            "outline_batches": set(),
            "episode_ids": set(),
        }
        owned_prefix = f"{action_id}:%"
        for item in connection.execute(
            select(creative_operation_results.c.resource_ref_json)
            .where(
                creative_operation_results.c.project_id == int(project_id),
                creative_operation_results.c.operation_id.like(owned_prefix),
            )
            .order_by(_ordered(creative_operation_results.c.operation_id))
        ).mappings().all():
            ref = _jsonb_object(item["resource_ref_json"])
            resource_type = str(ref.get("resource_type") or "")
            resource_ids = ref.get("resource_ids") or [ref.get("resource_id")]
            if resource_type == "brief":
                owned_refs["brief_ids"].update(
                    int(value) for value in resource_ids if str(value or "").isdigit()
                )
            elif resource_type == "characters":
                owned_refs["character_batches"].update(
                    str(value).rsplit(":", 1)[-1] for value in resource_ids if value
                )
            elif resource_type == "outline":
                owned_refs["outline_batches"].update(
                    str(value).rsplit(":", 1)[-1] for value in resource_ids if value
                )
            elif resource_type in {"beats", "script", "episode"}:
                owned_refs["episode_ids"].update(
                    int(value) for value in resource_ids if str(value or "").isdigit()
                )

        def rows(table: str) -> list[dict[str, Any]]:
            target = _optional_creative_table(table)
            if target is None:
                return []
            statement = (
                select(target)
                .where(target.c.project_id == int(project_id))
                .order_by(*_rollback_order(target))
            )
            return [
                self.row_dict(item)
                for item in connection.execute(statement).mappings().all()
            ]

        unowned_resources = {
            "creative_seeds": rows("creative_seeds"),
            "creative_blueprints": [
                item
                for item in rows("creative_blueprints")
                if int(item["id"]) not in owned_refs["brief_ids"]
            ],
            "creative_characters": [
                item
                for item in rows("creative_characters")
                if str(item["batch_id"]) not in owned_refs["character_batches"]
            ],
            "creative_relationships": [
                item
                for item in rows("creative_relationships")
                if str(item["batch_id"]) not in owned_refs["character_batches"]
            ],
            "creative_episodes": [
                item
                for item in rows("creative_episodes")
                if (
                    int(item["id"]) not in owned_refs["episode_ids"]
                    and str(item["batch_id"]) not in owned_refs["outline_batches"]
                )
            ],
        }
        technical = {
            table: [
                item
                for item in rows(table)
                if not str(item.get("operation_id") or "").startswith(f"{action_id}:")
            ]
            for table in (
                "creative_operation_results",
                "creative_generation_operations",
            )
        }
        payload = {
            "project_id": int(project_id),
            "project_metadata": project_metadata,
            "unowned_resources": unowned_resources,
            "unowned_operations": technical,
            "external_tables": {
                table: rows(table) for table in _WRITER_PROJECT_EXTERNAL_TABLES
            },
        }
        return {**payload, "fingerprint": canonical_request_hash(payload)}

    def writer_project_external_use_fingerprint(
        self, project_id: int, action_id: str
    ) -> str:
        """Fingerprint project rows not owned by one Writer create action."""

        with self.connection_scope() as connection:
            state = self._writer_project_external_use_state_conn(
                connection, project_id, action_id
            )
        return str(state["fingerprint"])

    def writer_action_workflow_task_ids(
        self, project_id: int, action_id: str
    ) -> set[str]:
        """Return workflow tasks referenced by one Writer action's durable rows."""

        with self.connection_scope() as connection:
            project = connection.execute(
                select(
                    creative_projects.c.workflow_task_id,
                    creative_projects.c.creation_workflow_task_id,
                ).where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
            ).mappings().first()
            if project is None:
                raise ValueError("创作项目不存在")
            task_ids = {
                str(value)
                for value in (
                    project["workflow_task_id"],
                    project["creation_workflow_task_id"],
                )
                if value
            }
            for item in connection.execute(
                select(creative_operation_results.c.resource_ref_json)
                .where(
                    creative_operation_results.c.project_id == int(project_id),
                    creative_operation_results.c.operation_id.like(f"{action_id}:%"),
                )
                .order_by(_ordered(creative_operation_results.c.operation_id))
            ).mappings().all():
                ref = _jsonb_object(item["resource_ref_json"])
                resource_type = str(ref.get("resource_type") or "")
                resource_ids = ref.get("resource_ids") or [ref.get("resource_id")]
                values = [value for value in resource_ids if value is not None]
                if not values:
                    continue
                if resource_type == "brief":
                    statement = (
                        select(creative_blueprints.c.workflow_task_id)
                        .where(
                            creative_blueprints.c.project_id == int(project_id),
                            creative_blueprints.c.id.in_(
                                [int(value) for value in values]
                            ),
                        )
                        .order_by(_ordered(creative_blueprints.c.id))
                    )
                elif resource_type == "characters":
                    batches = [str(value).rsplit(":", 1)[-1] for value in values]
                    statement = (
                        select(creative_characters.c.workflow_task_id)
                        .where(
                            creative_characters.c.project_id == int(project_id),
                            creative_characters.c.batch_id.in_(batches),
                        )
                        .order_by(_ordered(creative_characters.c.id))
                    )
                elif resource_type == "outline":
                    batches = [str(value).rsplit(":", 1)[-1] for value in values]
                    statement = (
                        select(creative_episodes.c.workflow_task_id)
                        .where(
                            creative_episodes.c.project_id == int(project_id),
                            creative_episodes.c.batch_id.in_(batches),
                        )
                        .order_by(_ordered(creative_episodes.c.id))
                    )
                elif resource_type in {"beats", "script", "episode"}:
                    statement = (
                        select(creative_episodes.c.workflow_task_id)
                        .where(
                            creative_episodes.c.project_id == int(project_id),
                            creative_episodes.c.id.in_(
                                [int(value) for value in values]
                            ),
                        )
                        .order_by(_ordered(creative_episodes.c.id))
                    )
                else:
                    continue
                task_ids.update(
                    str(item["workflow_task_id"])
                    for item in connection.execute(statement).mappings().all()
                    if item["workflow_task_id"]
                )
        return task_ids

    @staticmethod
    def _writer_rollback_primary_key(connection: Connection, table: str) -> str:
        del connection
        target = _creative_table(table)
        primary = list(target.primary_key.columns)
        if len(primary) != 1:
            raise ValueError(f"unsupported rollback primary key: {table}")
        return str(primary[0].name)

    def _restore_writer_rollback_rows(
        self, connection: Connection, table: str, rows: list[dict[str, Any]]
    ) -> None:
        target = _creative_table(table)
        primary_key = self._writer_rollback_primary_key(connection, table)
        for row in rows:
            columns = list(row)
            values = {
                column: _database_value(target.c[column], row[column])
                for column in columns
            }
            updated = connection.execute(
                update(target)
                .where(target.c[primary_key] == values[primary_key])
                .values(**values)
            ).rowcount
            if updated:
                continue
            inserted = connection.execute(
                pg_insert(target)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[primary_key])
            ).rowcount
            if inserted:
                continue
            # The row exists with the same primary key but was not matched by
            # the update. Overwrite only when the stored row is already the
            # snapshot row; never silently clobber different content.
            existing = connection.execute(
                select(target).where(
                    target.c[primary_key] == values[primary_key]
                )
            ).mappings().first()
            if existing is None or canonical_request_hash(
                CreativeRepositoryBase.row_dict(existing)
            ) != canonical_request_hash(row):
                raise ValueError("rollback row conflict")

    def restore_writer_rollback_state(
        self,
        project_id: int,
        preimage: dict[str, Any],
        *,
        expected_fingerprint: str,
    ) -> dict[str, Any]:
        """Restore one project iff it is still exactly at the action post-state."""

        if int(preimage.get("project_id") or 0) != int(project_id):
            raise ValueError("rollback snapshot belongs to another project")
        tables = preimage.get("tables")
        if not isinstance(tables, dict):
            raise ValueError("rollback snapshot is invalid")
        with self.transaction_scope() as connection:
            current = self._export_writer_rollback_state_conn(connection, project_id)
            if current["fingerprint"] != preimage.get("fingerprint"):
                if current["fingerprint"] != expected_fingerprint:
                    raise ValueError("project rollback state changed")

                # Resource group first, then the technical group, then the
                # project row: the two groups share no foreign key, so this is
                # the legacy restore order.
                for table in _WRITER_ROLLBACK_RESOURCE_TABLES:
                    rows = list(tables.get(table) or [])
                    target = _creative_table(table)
                    primary_key = self._writer_rollback_primary_key(connection, table)
                    identifiers = [row[primary_key] for row in rows]
                    if table == "creative_seeds":
                        if identifiers:
                            connection.execute(
                                delete(target).where(
                                    target.c.project_id == int(project_id),
                                    target.c[primary_key].not_in(identifiers),
                                )
                            )
                        else:
                            connection.execute(
                                delete(target).where(
                                    target.c.project_id == int(project_id)
                                )
                            )
                    elif identifiers:
                        connection.execute(
                            update(target)
                            .where(
                                target.c.project_id == int(project_id),
                                target.c[primary_key].not_in(identifiers),
                            )
                            .values(status="superseded")
                        )
                    else:
                        connection.execute(
                            update(target)
                            .where(target.c.project_id == int(project_id))
                            .values(status="superseded")
                        )
                    self._restore_writer_rollback_rows(connection, table, rows)

                for table in _WRITER_ROLLBACK_TECHNICAL_TABLES:
                    target = _creative_table(table)
                    connection.execute(
                        delete(target).where(target.c.project_id == int(project_id))
                    )
                    self._restore_writer_rollback_rows(
                        connection, table, list(tables.get(table) or [])
                    )

                project_rows = list(tables.get("creative_projects") or [])
                if len(project_rows) != 1:
                    raise ValueError("rollback project snapshot is invalid")
                self._restore_writer_rollback_rows(
                    connection, "creative_projects", project_rows
                )
                restored = self._export_writer_rollback_state_conn(
                    connection, project_id
                )
                if restored["fingerprint"] != preimage.get("fingerprint"):
                    raise ValueError("project rollback verification failed")
        return self.get_project(project_id) or {}

    # -- project deletion ---------------------------------------------------

    def get_pending_project_deletion(
        self, project_id: int
    ) -> dict[str, Any] | None:
        """Return a durable cross-database deletion tombstone, if any."""

        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_project_deletions).where(
                    creative_project_deletions.c.project_id == int(project_id)
                )
            ).mappings().first()
        if row is None:
            return None
        item = self.row_dict(row)
        item["project"] = _jsonb_object(item.pop("project_json"))
        raw_paths = item.pop("delivery_paths_json")
        item["delivery_paths"] = jsonb_copy(raw_paths) if raw_paths is not None else []
        return item

    def complete_project_deletion(self, project_id: int) -> None:
        """Acknowledge that the workflow database side of a deletion is clean."""

        with self.transaction_scope() as connection:
            connection.execute(
                delete(creative_project_deletions).where(
                    creative_project_deletions.c.project_id == int(project_id)
                )
            )

    def delete_project(
        self,
        project_id: int,
        *,
        requested_by: str = "",
        expected_writer_fingerprint: str | None = None,
        expected_writer_external_fingerprint: str | None = None,
        writer_action_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete creative rows and leave a durable workflow-cleanup tombstone."""

        project_id = int(project_id)
        # Resolving a production-export path touches the filesystem, so the
        # candidate list is read and resolved before the write transaction
        # opens; the legacy implementation did this inside its BEGIN IMMEDIATE
        # block.
        export_directory = getattr(self, "production_export_directory", None)
        raw_export_paths: list[str] = []
        if export_directory is not None:
            with self.connection_scope() as connection:
                raw_export_paths = [
                    str(item["path"] or "")
                    for item in connection.execute(
                        select(creative_production_exports.c.path)
                        .where(
                            creative_production_exports.c.project_id == project_id
                        )
                        .order_by(
                            _ordered(creative_production_exports.c.created_at),
                            _ordered(creative_production_exports.c.id),
                        )
                    ).mappings().all()
                ]
        resolved_export_paths = _resolved_export_paths(
            raw_export_paths, export_directory
        )

        with self.transaction_scope() as connection:
            if expected_writer_fingerprint is not None:
                current = self._writer_project_deletion_state_conn(
                    connection, project_id
                )
                if current["fingerprint"] != expected_writer_fingerprint:
                    raise ValueError("project deletion state changed")
            if expected_writer_external_fingerprint is not None:
                if not writer_action_id:
                    raise ValueError("writer action id is required")
                external = self._writer_project_external_use_state_conn(
                    connection, project_id, writer_action_id
                )
                if external["fingerprint"] != expected_writer_external_fingerprint:
                    raise ValueError("project has external use")
            row = connection.execute(
                select(
                    creative_projects.c.id,
                    creative_projects.c.name,
                    creative_projects.c.topic_id,
                ).where(
                    creative_projects.c.id == project_id,
                    creative_projects.c.deleted.is_(False),
                )
            ).mappings().first()
            if row is None:
                raise ValueError("创作项目不存在")
            project = dict(row)

            delivery_paths = [
                str(item["path"])
                for item in connection.execute(
                    select(creative_deliveries.c.path)
                    .where(creative_deliveries.c.project_id == project_id)
                    .order_by(
                        _ordered(creative_deliveries.c.created_at),
                        _ordered(creative_deliveries.c.id),
                    )
                ).mappings().all()
                if item["path"]
            ]
            connection.execute(
                delete(creative_deliveries).where(
                    creative_deliveries.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_production_exports).where(
                    creative_production_exports.c.project_id == project_id
                )
            )
            delivery_paths.extend(resolved_export_paths)

            now = utc_now()
            tombstone = {
                "project_json": project,
                "delivery_paths_json": delivery_paths,
                "requested_by": requested_by,
                "status": "creative_deleted",
                "created_at": now,
                "updated_at": now,
            }
            connection.execute(
                pg_insert(creative_project_deletions)
                .values(project_id=project_id, **tombstone)
                .on_conflict_do_update(
                    index_elements=["project_id"], set_=tombstone
                )
            )

            optimization_ids = [
                int(item["id"])
                for item in connection.execute(
                    select(script_optimizations.c.id)
                    .where(script_optimizations.c.project_id == project_id)
                    .order_by(_ordered(script_optimizations.c.id))
                ).mappings().all()
            ]
            if optimization_ids:
                connection.execute(
                    delete(script_optimization_drafts).where(
                        or_(
                            script_optimization_drafts.c.project_id == project_id,
                            script_optimization_drafts.c.optimization_id.in_(
                                optimization_ids
                            ),
                        )
                    )
                )
            else:
                connection.execute(
                    delete(script_optimization_drafts).where(
                        script_optimization_drafts.c.project_id == project_id
                    )
                )
            connection.execute(
                delete(script_optimizations).where(
                    script_optimizations.c.project_id == project_id
                )
            )
            connection.execute(
                delete(script_reviews).where(
                    script_reviews.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_stage_exports).where(
                    creative_stage_exports.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_operation_results).where(
                    creative_operation_results.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_generation_operations).where(
                    creative_generation_operations.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_generation_contexts).where(
                    creative_generation_contexts.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_resource_heads).where(
                    creative_resource_heads.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_mode_migrations).where(
                    creative_mode_migrations.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_episodes).where(
                    creative_episodes.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_relationships).where(
                    creative_relationships.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_characters).where(
                    creative_characters.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_blueprints).where(
                    creative_blueprints.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_seeds).where(creative_seeds.c.project_id == project_id)
            )

            # PostgreSQL-only, project-scoped tables. Their foreign keys are not
            # declared ON DELETE CASCADE, so they are removed explicitly and in
            # dependency order before the project row itself.
            connection.execute(
                delete(creative_project_prompt_bindings).where(
                    creative_project_prompt_bindings.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_project_prompt_settings).where(
                    creative_project_prompt_settings.c.project_id == project_id
                )
            )
            owned_template_ids = [
                str(item["id"])
                for item in connection.execute(
                    select(creative_prompt_templates.c.id)
                    .where(creative_prompt_templates.c.project_id == project_id)
                    .order_by(_ordered(creative_prompt_templates.c.id))
                ).mappings().all()
            ]
            if owned_template_ids:
                connection.execute(
                    delete(creative_prompt_template_revisions).where(
                        creative_prompt_template_revisions.c.template_id.in_(
                            owned_template_ids
                        )
                    )
                )
                connection.execute(
                    delete(creative_prompt_templates).where(
                        creative_prompt_templates.c.id.in_(owned_template_ids)
                    )
                )
            connection.execute(
                delete(production_status).where(
                    production_status.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_mode_migration_compensations).where(
                    creative_mode_migration_compensations.c.project_id == project_id
                )
            )
            connection.execute(
                delete(creative_projects).where(creative_projects.c.id == project_id)
            )

        result = dict(project)
        result["delivery_paths"] = delivery_paths
        return result


__all__ = ["OperationsRepository"]
