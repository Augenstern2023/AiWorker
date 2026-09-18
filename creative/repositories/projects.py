# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Project, seed and workflow-identity behaviour for the PostgreSQL store.

This mixin is the PostgreSQL port of the ``creative_projects``,
``creative_seeds`` and project workflow-identity slices of
:mod:`creative.legacy_sqlite_repository`.  It assumes only the
:class:`~creative.repositories.base.CreativeRepositoryBase` contract
(``self.database``, ``self.store_id``, ``connection_scope``,
``transaction_scope``, ``row_dict``/``rows_dict`` and the workflow identity
helpers) and is combined with the other domain mixins by
:class:`creative.postgres_repository.PostgresCreativeRepository`.

Type mapping applied while porting:

* legacy epoch ``REAL`` timestamps are ``TIMESTAMPTZ`` here, so every write
  converts with :func:`~creative.repositories.base.utc_now` (or
  :func:`~creative.repositories.base.from_epoch_seconds` for a legacy epoch
  value) and every read converts back with :meth:`row_dict`;
* legacy ``*_json`` ``TEXT`` columns are ``JSONB``, so Python mappings and
  sequences are bound directly and parsed values are popped and assigned
  without ``json.loads``;
* the legacy ``deleted`` integer flag is ``BOOLEAN``, so ``False``/``True`` are
  written instead of ``0``/``1``.

Version and workflow-identity allocation used to rely on SQLite
``BEGIN IMMEDIATE`` plus ``MAX(version) + 1``.  In PostgreSQL every such write
takes exactly one transaction-scoped advisory lock *before* reading the rows
whose version/revision it computes; see :meth:`_lock_project_identity` for the
lock order that makes that deadlock free.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
    creative_resource_heads,
    creative_seeds,
    creative_topics,
)
from creative.repositories.base import from_epoch_seconds, utc_now
from creative.script_service import normalize_script_content


#: Partial unique indexes on ``creative_projects`` that make project creation
#: idempotent.  Only these may be recovered from; every other integrity error
#: keeps propagating exactly like the legacy ``sqlite3.IntegrityError`` did.
_IDEMPOTENT_PROJECT_CONSTRAINTS: tuple[str, ...] = (
    "uq_creative_projects_creation_request",
    "uq_creative_projects_creation_workflow_task",
    "uq_creative_projects_workflow_task",
)

#: The versioned-resource tables ``project_workflow_identity_evidence`` scans.
_IDENTITY_EVIDENCE_TABLES = (
    creative_blueprints,
    creative_characters,
    creative_relationships,
    creative_episodes,
)

#: Tables the private creative-item helpers are allowed to address.
_CREATIVE_ITEM_TABLES = {
    "creative_characters": creative_characters,
    "creative_relationships": creative_relationships,
    "creative_episodes": creative_episodes,
}


def _advisory_lock_key(identity: str) -> int:
    """Derive a stable signed 64-bit advisory lock key from an identity."""

    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


def _is_idempotent_project_conflict(exc: IntegrityError) -> bool:
    """True when a failed insert only collided with a creatable-identity index.

    Called only after PostgreSQL reports an integrity error, so an unrelated
    NOT NULL, foreign key or check violation must stay an error and must never
    be silently converted into "the project already exists".
    """

    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    if constraint:
        return constraint in _IDEMPOTENT_PROJECT_CONSTRAINTS
    message = str(getattr(exc, "orig", exc))
    return any(name in message for name in _IDEMPOTENT_PROJECT_CONSTRAINTS)


class ProjectsRepository:
    """Projects, seeds and project workflow identity on PostgreSQL."""

    # -- locking -------------------------------------------------------------

    def _lock_project_identity(self, connection: Connection, identity: str) -> None:
        """Serialise allocation for one creative identity.

        Lock order: an operation takes at most one advisory lock, and it takes
        it before reading the rows whose ``version``/``revision``/creation
        identity it is about to compute, so two concurrent callers can never
        compute the same next version.  Because a second advisory lock is never
        taken while holding one, no advisory-lock cycle can form; the only
        other locks are row locks (``SELECT ... FOR UPDATE``) taken after it.
        """

        connection.execute(
            select(func.pg_advisory_xact_lock(_advisory_lock_key(identity)))
        )

    def _lock_project_row_identity(
        self, connection: Connection, project_id: int
    ) -> None:
        """Lock the version namespace owned by one creative project."""

        self._lock_project_identity(
            connection, f"creative-project:{self.store_id}:{int(project_id)}"
        )

    def _lock_creation_request_identity(
        self, connection: Connection, created_by: str, creation_request_id: str
    ) -> None:
        """Lock the idempotency namespace of one project creation request."""

        self._lock_project_identity(
            connection,
            "creative-project-creation:"
            f"{self.store_id}:{created_by}:{creation_request_id}",
        )

    def _lock_store_identity(self, connection: Connection, purpose: str) -> None:
        """Lock one store-wide operation that sweeps more than one project."""

        self._lock_project_identity(
            connection, f"creative-store:{self.store_id}:{purpose}"
        )

    # -- project workflow identity ------------------------------------------

    def project_workflow_identity_evidence(
        self, project_id: int
    ) -> dict[str, Any]:
        """Return local task and creator evidence without modifying history."""

        with self.connection_scope() as connection:
            project = connection.execute(
                select(
                    creative_projects.c.created_by,
                    creative_projects.c.workflow_task_id,
                    creative_projects.c.creation_workflow_task_id,
                ).where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
            ).mappings().first()
            if project is None:
                return {"created_by": "", "task_ids": set()}
            task_ids = {
                str(value)
                for value in (
                    project["workflow_task_id"],
                    project["creation_workflow_task_id"],
                )
                if value
            }
            for table in _IDENTITY_EVIDENCE_TABLES:
                task_ids.update(
                    str(value)
                    for value in connection.execute(
                        select(table.c.workflow_task_id)
                        .where(
                            table.c.project_id == int(project_id),
                            table.c.workflow_task_id.is_not(None),
                        )
                        .order_by(table.c.id)
                    ).scalars()
                )
        return {
            "created_by": str(project["created_by"] or ""),
            "task_ids": task_ids,
        }

    def topic_workflow_task_ids(self, topic_id: int) -> set[str]:
        """Return local breakdown tasks that can prove a legacy topic scope."""

        with self.connection_scope() as connection:
            values = connection.execute(
                select(creative_breakdowns.c.workflow_task_id)
                .where(
                    creative_breakdowns.c.topic_id == int(topic_id),
                    creative_breakdowns.c.workflow_task_id.is_not(None),
                )
                .order_by(creative_breakdowns.c.id)
            ).scalars()
            return {str(value) for value in values}

    def get_project_by_workflow_task_id(
        self, workflow_task_id: str
    ) -> dict[str, Any] | None:
        return self.get_project_by_creation_workflow_task_id(workflow_task_id)

    def get_project_by_creation_workflow_task_id(
        self, workflow_task_id: str
    ) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_projects.c.id).where(
                    creative_projects.c.creation_workflow_task_id == workflow_task_id,
                    creative_projects.c.deleted.is_(False),
                )
            ).first()
        return self.get_project(int(row[0])) if row is not None else None

    def reconcile_project_workflow_task(
        self,
        workflow_task_id: str,
        project_id: int,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            # Lock order: this repair sweeps the rows of two different projects,
            # so it takes the single store-scoped advisory lock (never a
            # project-scoped one) and therefore cannot deadlock with another
            # reconcile or with a project-scoped writer.
            self._lock_store_identity(connection, "reconcile-project-workflow-task")
            target = connection.execute(
                select(creative_projects.c.id).where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
            ).first()
            if target is None:
                raise ValueError("system-bound creative project does not exist")
            connection.execute(
                update(creative_projects)
                .where(
                    creative_projects.c.creation_workflow_task_id == workflow_task_id,
                    creative_projects.c.id != int(project_id),
                )
                .values(
                    creation_workflow_task_id=None,
                    workflow_task_id=case(
                        (
                            creative_projects.c.workflow_task_id == workflow_task_id,
                            None,
                        ),
                        else_=creative_projects.c.workflow_task_id,
                    ),
                    updated_at=now,
                )
            )
            connection.execute(
                update(creative_projects)
                .where(
                    creative_projects.c.workflow_task_id == workflow_task_id,
                    creative_projects.c.id != int(project_id),
                )
                .values(workflow_task_id=None, updated_at=now)
            )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(
                    creation_workflow_task_id=workflow_task_id,
                    workflow_task_id=case(
                        (
                            creative_projects.c.workflow_task_id.is_(None)
                            | (creative_projects.c.workflow_task_id == workflow_task_id),
                            workflow_task_id,
                        ),
                        else_=creative_projects.c.workflow_task_id,
                    ),
                    updated_at=now,
                )
            )
        project = self.get_project(project_id, connection=caller_connection)
        if project is None:
            raise ValueError("system-bound creative project does not exist")
        return project

    def clear_project_creation_task(
        self, project_id: int, workflow_task_id: str
    ) -> None:
        with self.transaction_scope() as connection:
            connection.execute(
                update(creative_projects)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.creation_workflow_task_id == workflow_task_id,
                )
                .values(creation_workflow_task_id=None, updated_at=utc_now())
            )

    def get_project_inputs(self, project_id: int) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if project["creation_mode"] == "original":
            return {
                "project": project,
                "topic": {},
                "breakdown": {},
                "seed": project.get("seed"),
            }
        topic = self.get_topic(int(project["topic_id"]))
        breakdown = self.get_breakdown(int(project["breakdown_id"]))
        return {"project": project, "topic": topic or {}, "breakdown": breakdown or {}}

    def link_project_workflow_task(self, project_id: int, workflow_task_id: str) -> None:
        with self.transaction_scope() as connection:
            cursor = connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(workflow_task_id=workflow_task_id, updated_at=utc_now())
            )
            if cursor.rowcount != 1:
                raise ValueError("创作项目不存在")

    # -- project creation ----------------------------------------------------

    def create_project(
        self,
        topic_id: int,
        breakdown_id: int,
        name: str = "",
        target_episode_count: int = 20,
        *,
        genre: str = "",
        target_platform: str = "",
        target_audience: str = "",
        episode_duration_seconds: int = 120,
        workflow_task_id: str | None = None,
        creation_request_id: str = "",
        created_by: str = "local",
        workflow_mode: str = "sequential",
        seed_text: str = "",
    ) -> dict[str, Any]:
        if not 1 <= int(target_episode_count) <= 100:
            raise ValueError("目标集数必须在 1-100 之间")
        if not 30 <= int(episode_duration_seconds) <= 1800:
            raise ValueError("单集时长必须在 30-1800 秒之间")
        if workflow_mode not in {"sequential", "free"}:
            raise ValueError("invalid creative workflow mode")
        normalized_request_id = creation_request_id.strip()
        project_id: int | None = None
        with self.transaction_scope() as connection:
            if normalized_request_id:
                existing = connection.execute(
                    select(creative_projects.c.id).where(
                        creative_projects.c.created_by == created_by,
                        creative_projects.c.creation_request_id == normalized_request_id,
                        creative_projects.c.deleted.is_(False),
                    )
                ).mappings().first()
                if existing is not None:
                    return self.get_project(int(existing["id"])) or {}
            if workflow_task_id:
                existing = connection.execute(
                    select(creative_projects.c.id).where(
                        creative_projects.c.creation_workflow_task_id == workflow_task_id,
                        creative_projects.c.deleted.is_(False),
                    )
                ).mappings().first()
                if existing is not None:
                    return self.get_project(int(existing["id"])) or {}
            topic = connection.execute(
                select(creative_topics).where(
                    creative_topics.c.id == int(topic_id),
                    creative_topics.c.deleted.is_(False),
                )
            ).mappings().first()
            if topic is None:
                raise ValueError("选题不存在")
            if topic["status"] != "adopted":
                raise ValueError("只有已采纳选题可以创建创作项目")
            breakdown = connection.execute(
                select(creative_breakdowns).where(
                    creative_breakdowns.c.id == int(breakdown_id)
                )
            ).mappings().first()
            breakdown_topic = None
            if breakdown is not None:
                breakdown_topic = connection.execute(
                    select(creative_topics).where(
                        creative_topics.c.id == int(breakdown["topic_id"]),
                        creative_topics.c.deleted.is_(False),
                    )
                ).mappings().first()
            same_title = bool(
                breakdown_topic is not None
                and self._title_key(topic["title"])
                == self._title_key(breakdown_topic["title"])
            )
            if (
                breakdown is None
                or breakdown["status"] not in {"locked", "approved"}
                or not same_title
            ):
                raise ValueError("创建项目需要该选题的已锁定或已批准拆解卡")
            resolved_target_audience = (
                target_audience.strip()
                or self._breakdown_target_audience(breakdown["content_json"])
            )
            resolved_seed_text = seed_text.strip()
            if workflow_mode == "free" and len(resolved_seed_text) < 2:
                resolved_seed_text = self._topic_based_seed_text(topic, breakdown)
            now = utc_now()
            try:
                # The pre-checks above cannot be made atomic by a read lock, so
                # the insert runs inside a SAVEPOINT: a losing racer recovers
                # from the named partial unique index below without poisoning
                # the surrounding transaction.
                with connection.begin_nested():
                    project_id = connection.execute(
                        pg_insert(creative_projects)
                        .values(
                            topic_id=int(topic_id),
                            breakdown_id=int(breakdown_id),
                            name=name.strip() or f"{topic['title']}创作项目",
                            genre=genre.strip() or str(topic["category"] or ""),
                            target_platform=target_platform.strip(),
                            target_audience=resolved_target_audience,
                            target_episode_count=int(target_episode_count),
                            episode_duration_seconds=int(episode_duration_seconds),
                            workflow_task_id=workflow_task_id,
                            creation_workflow_task_id=workflow_task_id,
                            creation_request_id=normalized_request_id or None,
                            created_by=created_by,
                            workflow_mode=workflow_mode,
                            status="free_active" if workflow_mode == "free" else "created",
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(creative_projects.c.id)
                    ).scalar_one()
                    if workflow_mode == "free":
                        connection.execute(
                            pg_insert(creative_seeds).values(
                                project_id=int(project_id),
                                version=1,
                                parent_version_id=None,
                                status="frozen",
                                seed_text=resolved_seed_text,
                                constraints_json={},
                                reference_refs_json=[],
                                created_at=now,
                                updated_at=now,
                            )
                        )
            except IntegrityError as exc:
                if not _is_idempotent_project_conflict(exc):
                    raise
                recovered = None
                if normalized_request_id:
                    recovered = connection.execute(
                        select(creative_projects.c.id).where(
                            creative_projects.c.created_by == created_by,
                            creative_projects.c.creation_request_id
                            == normalized_request_id,
                        )
                    ).mappings().first()
                if recovered is None:
                    if not workflow_task_id:
                        raise
                    recovered = connection.execute(
                        select(creative_projects.c.id).where(
                            creative_projects.c.creation_workflow_task_id
                            == workflow_task_id
                        )
                    ).mappings().first()
                    if recovered is None:
                        raise
                return self.get_project(int(recovered["id"])) or {}
        return self.get_project(int(project_id)) or {}

    def create_original_project(
        self,
        *,
        seed_text: str,
        name: str,
        target_episode_count: int,
        target_platform: str = "",
        genre: str = "",
        target_audience: str = "",
        episode_duration_seconds: int = 120,
        constraints: dict[str, Any] | None = None,
        reference_refs: list[dict[str, Any]] | None = None,
        created_by: str,
        creation_request_id: str,
        workflow_mode: str = "sequential",
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        project, _ = self.ensure_original_project(
            seed_text=seed_text,
            name=name,
            target_episode_count=target_episode_count,
            target_platform=target_platform,
            genre=genre,
            target_audience=target_audience,
            episode_duration_seconds=episode_duration_seconds,
            constraints=constraints,
            reference_refs=reference_refs,
            created_by=created_by,
            creation_request_id=creation_request_id,
            workflow_mode=workflow_mode,
            connection=connection,
        )
        return project

    def ensure_original_project(
        self,
        *,
        seed_text: str,
        name: str,
        target_episode_count: int,
        target_platform: str = "",
        genre: str = "",
        target_audience: str = "",
        episode_duration_seconds: int = 120,
        constraints: dict[str, Any] | None = None,
        reference_refs: list[dict[str, Any]] | None = None,
        created_by: str,
        creation_request_id: str,
        workflow_mode: str = "sequential",
        connection: Connection | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if not 1 <= int(target_episode_count) <= 100:
            raise ValueError("目标集数必须在 1-100 之间")
        if not 30 <= int(episode_duration_seconds) <= 1800:
            raise ValueError("单集时长必须在 30-1800 秒之间")
        if not seed_text.strip():
            raise ValueError("原创种子不能为空")
        if workflow_mode not in {"sequential", "free"}:
            raise ValueError("invalid creative workflow mode")
        now = utc_now()
        normalized_request_id = creation_request_id.strip()
        # Joining the caller's connection puts the creative project row, its
        # seed and the workflow bootstrap in one transaction; the read-back at
        # the end of this method therefore has to run on that same connection.
        # ``caller_connection`` survives the ``as`` rebinding below: when it is
        # None this method owned and already closed its transaction, so the
        # read-back must open a fresh connection rather than reuse that handle.
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            if normalized_request_id:
                # Lock order: the creation-request advisory lock is taken before
                # the existence probe, so the find-or-insert pair is atomic and
                # a concurrent request returns the winner instead of colliding.
                self._lock_creation_request_identity(
                    connection, created_by, normalized_request_id
                )
                existing = connection.execute(
                    select(
                        creative_projects.c.id,
                        creative_projects.c.creation_mode,
                        creative_projects.c.workflow_mode,
                    ).where(
                        creative_projects.c.created_by == created_by,
                        creative_projects.c.creation_request_id == normalized_request_id,
                        creative_projects.c.deleted.is_(False),
                    )
                ).mappings().first()
                if existing is not None:
                    if existing["creation_mode"] != "original":
                        raise ValueError("创建请求标识已用于其他项目模式")
                    if str(existing["workflow_mode"] or "sequential") != workflow_mode:
                        raise ValueError("创建请求标识已用于其他工作流模式")
                    project_id = int(existing["id"])
                    created = False
                else:
                    project_id, created = self._insert_original_project(
                        connection,
                        seed_text=seed_text,
                        name=name,
                        target_episode_count=target_episode_count,
                        target_platform=target_platform,
                        genre=genre,
                        target_audience=target_audience,
                        episode_duration_seconds=episode_duration_seconds,
                        constraints=constraints,
                        reference_refs=reference_refs,
                        created_by=created_by,
                        creation_request_id=normalized_request_id,
                        now=now,
                        workflow_mode=workflow_mode,
                    )
            else:
                project_id, created = self._insert_original_project(
                    connection,
                    seed_text=seed_text,
                    name=name,
                    target_episode_count=target_episode_count,
                    target_platform=target_platform,
                    genre=genre,
                    target_audience=target_audience,
                    episode_duration_seconds=episode_duration_seconds,
                    constraints=constraints,
                    reference_refs=reference_refs,
                    created_by=created_by,
                    creation_request_id=normalized_request_id,
                    now=now,
                    workflow_mode=workflow_mode,
                )
        return (
            self.get_project(project_id, connection=caller_connection) or {},
            created,
        )

    @staticmethod
    def _insert_original_project(
        conn: Connection,
        *,
        seed_text: str,
        name: str,
        target_episode_count: int,
        target_platform: str,
        genre: str,
        target_audience: str,
        episode_duration_seconds: int,
        constraints: dict[str, Any] | None,
        reference_refs: list[dict[str, Any]] | None,
        created_by: str,
        creation_request_id: str,
        now: datetime | float,
        workflow_mode: str,
    ) -> tuple[int, bool]:
        created_at = now if isinstance(now, datetime) else from_epoch_seconds(now)
        project_id = conn.execute(
            pg_insert(creative_projects)
            .values(
                topic_id=None,
                breakdown_id=None,
                name=name.strip(),
                genre=genre.strip(),
                target_platform=target_platform.strip(),
                target_audience=target_audience.strip(),
                target_episode_count=int(target_episode_count),
                episode_duration_seconds=int(episode_duration_seconds),
                creation_request_id=creation_request_id or None,
                created_by=created_by,
                creation_mode="original",
                workflow_mode=workflow_mode,
                status="free_active" if workflow_mode == "free" else "created",
                bootstrap_status="pending",
                created_at=created_at,
                updated_at=created_at,
            )
            .returning(creative_projects.c.id)
        ).scalar_one()
        conn.execute(
            pg_insert(creative_seeds).values(
                project_id=int(project_id),
                version=1,
                parent_version_id=None,
                status="draft",
                seed_text=seed_text.strip(),
                constraints_json=constraints or {},
                reference_refs_json=reference_refs or [],
                created_at=created_at,
                updated_at=created_at,
            )
        )
        return int(project_id), True

    def mark_project_bootstrap_status(
        self,
        project_id: int,
        status: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        if status not in {"pending", "ready", "failed"}:
            raise ValueError("invalid bootstrap status")
        # The read-back runs on ``caller_connection`` when the caller supplied
        # one, so it observes the status this call just wrote inside the
        # caller's still-open transaction; with no caller connection it opens a
        # fresh connection after this method's own transaction committed,
        # exactly as before.
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            connection.execute(
                update(creative_projects)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
                .values(bootstrap_status=status, updated_at=utc_now())
            )
        return self.get_project(project_id, connection=caller_connection) or {}

    # -- seeds ---------------------------------------------------------------

    def get_current_seed(self, project_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_seeds)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version.desc(), creative_seeds.c.id.desc())
                .limit(1)
            ).mappings().first()
        return self._seed(self.row_dict(row)) if row else None

    def get_seed(self, seed_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_seeds).where(creative_seeds.c.id == int(seed_id))
            ).mappings().first()
        return self._seed(self.row_dict(row)) if row else None

    def list_seeds(self, project_id: int) -> list[dict[str, Any]]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_seeds)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version, creative_seeds.c.id)
            ).mappings().all()
        return [self._seed(self.row_dict(row)) for row in rows]

    def update_seed(
        self, seed_id: int, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {"seed_text", "constraints", "reference_refs"}
        updates = {key: value for key, value in values.items() if key in allowed}
        seed = self.get_seed(seed_id)
        if seed is None or seed["status"] != "draft":
            return None
        if not updates:
            return seed
        if "seed_text" in updates and not str(updates["seed_text"]).strip():
            raise ValueError("原创种子内容不能为空")
        resolved: dict[str, Any] = {}
        for key, value in updates.items():
            column = {
                "seed_text": "seed_text",
                "constraints": "constraints_json",
                "reference_refs": "reference_refs_json",
            }[key]
            resolved[column] = value.strip() if key == "seed_text" else value
        with self.transaction_scope() as connection:
            cursor = connection.execute(
                update(creative_seeds)
                .where(
                    creative_seeds.c.id == int(seed_id),
                    creative_seeds.c.status == "draft",
                )
                .values(**resolved, updated_at=utc_now())
            )
            updated_rows = cursor.rowcount
        return self.get_seed(seed_id) if updated_rows else None

    def freeze_seed(self, seed_id: int) -> dict[str, Any]:
        now = utc_now()
        with self.transaction_scope() as connection:
            connection.execute(
                update(creative_seeds)
                .where(
                    creative_seeds.c.id == int(seed_id),
                    creative_seeds.c.status == "draft",
                )
                .values(status="frozen", locked_at=now, updated_at=now)
            )
        seed = self.get_seed(seed_id)
        if seed is None:
            raise ValueError("原创种子不存在")
        return seed

    def create_seed_version(self, project_id: int, **values: Any) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            # Lock order: the project-scoped advisory lock is taken before the
            # MAX(version) read, so concurrent callers serialise and the
            # unique (project_id, version) index is only a backstop.
            self._lock_project_row_identity(connection, project_id)
            parent = connection.execute(
                select(creative_seeds)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version.desc(), creative_seeds.c.id.desc())
                .limit(1)
            ).mappings().first()
            if parent is None:
                raise ValueError("原创项目种子不存在")
            now = utc_now()
            seed_id = connection.execute(
                pg_insert(creative_seeds)
                .values(
                    project_id=int(project_id),
                    version=int(parent["version"]) + 1,
                    parent_version_id=int(parent["id"]),
                    status="draft",
                    seed_text=str(values.get("seed_text", parent["seed_text"])).strip(),
                    constraints_json=values.get(
                        "constraints", parent["constraints_json"] or {}
                    ),
                    reference_refs_json=values.get(
                        "reference_refs", parent["reference_refs_json"] or []
                    ),
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_seeds.c.id)
            ).scalar_one()
        return self.get_seed(int(seed_id)) or {}

    # -- project reads -------------------------------------------------------

    def get_project_by_creation_request_id(
        self, created_by: str, creation_request_id: str
    ) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_projects.c.id).where(
                    creative_projects.c.created_by == created_by,
                    creative_projects.c.creation_request_id
                    == creation_request_id.strip(),
                    creative_projects.c.deleted.is_(False),
                )
            ).first()
        return self.get_project(int(row[0])) if row is not None else None

    def list_project_creator_audit_rows(self) -> list[tuple[str, str]]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_projects.c.id, creative_projects.c.created_by)
                .where(
                    creative_projects.c.deleted.is_(False),
                    creative_projects.c.created_by.is_not(None),
                )
                .order_by(creative_projects.c.id)
            ).mappings().all()
        return [(str(row["id"]), str(row["created_by"])) for row in rows]

    def list_active_project_ids(self) -> list[int]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_projects.c.id)
                .where(creative_projects.c.deleted.is_(False))
                .order_by(creative_projects.c.id)
            ).scalars().all()
        return [int(value) for value in rows]

    def list_projects(self, topic_id: int | None = None) -> list[dict[str, Any]]:
        clauses = [
            creative_projects.c.deleted.is_(False),
            creative_projects.c.bootstrap_status == "ready",
        ]
        if topic_id is not None:
            clauses.append(creative_projects.c.topic_id == int(topic_id))
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(
                    creative_projects,
                    creative_topics.c.title.label("topic_title"),
                )
                .select_from(
                    creative_projects.outerjoin(
                        creative_topics,
                        creative_topics.c.id == creative_projects.c.topic_id,
                    )
                )
                .where(*clauses)
                .order_by(
                    creative_projects.c.updated_at.desc(),
                    creative_projects.c.id.desc(),
                )
            ).mappings().all()
        return self.rows_dict(rows)

    def list_projects_for_bootstrap(
        self, created_by: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = [
            creative_projects.c.deleted.is_(False),
            creative_projects.c.created_by == created_by,
            creative_projects.c.creation_mode == "original",
        ]
        if status is not None:
            if status not in {"pending", "ready", "failed"}:
                raise ValueError("invalid bootstrap status")
            clauses.append(creative_projects.c.bootstrap_status == status)
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_projects.c.id)
                .where(*clauses)
                .order_by(creative_projects.c.created_at, creative_projects.c.id)
            ).scalars().all()
        return [self.get_project(int(value)) or {} for value in rows]

    def get_project(
        self, project_id: int, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        # ``connection`` lets a caller inside its own transaction read back the
        # rows it has not committed yet (uncommitted writes are invisible to a
        # second connection, even inside the same transaction block).
        with self.connection_scope(connection) as connection:
            row = connection.execute(
                select(
                    creative_projects,
                    creative_topics.c.title.label("topic_title"),
                    creative_topics.c.description.label("topic_description"),
                    creative_topics.c.source_platforms_json,
                    creative_breakdowns.c.version.label("breakdown_version"),
                )
                .select_from(
                    creative_projects.outerjoin(
                        creative_topics,
                        creative_topics.c.id == creative_projects.c.topic_id,
                    ).outerjoin(
                        creative_breakdowns,
                        creative_breakdowns.c.id == creative_projects.c.breakdown_id,
                    )
                )
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
            ).mappings().first()
            if row is None:
                return None
            project = self.row_dict(row)
            resource_scope = (
                "free" if project.get("workflow_mode") == "free" else "sequential"
            )
            project["source_platforms"] = project.pop("source_platforms_json") or []
            project["blueprints"] = [
                self._blueprint(self.row_dict(item))
                for item in connection.execute(
                    select(creative_blueprints)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == resource_scope,
                    )
                    .order_by(creative_blueprints.c.id)
                ).mappings().all()
            ]
            project["characters"] = [
                self._creative_item(self.row_dict(item))
                for item in connection.execute(
                    select(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.resource_scope == resource_scope,
                    )
                    .order_by(creative_characters.c.version, creative_characters.c.id)
                ).mappings().all()
            ]
            project["relationships"] = [
                self._creative_item(self.row_dict(item))
                for item in connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.resource_scope == resource_scope,
                    )
                    .order_by(
                        creative_relationships.c.version, creative_relationships.c.id
                    )
                ).mappings().all()
            ]
            project["episodes"] = [
                self._episode(self.row_dict(item))
                for item in connection.execute(
                    select(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.resource_scope == resource_scope,
                    )
                    .order_by(
                        creative_episodes.c.stage,
                        creative_episodes.c.episode_number,
                        creative_episodes.c.version,
                    )
                ).mappings().all()
            ]
            project["resource_heads"] = [
                self.row_dict(item)
                for item in connection.execute(
                    select(creative_resource_heads)
                    .where(
                        creative_resource_heads.c.project_id == int(project_id),
                        creative_resource_heads.c.resource_scope == resource_scope,
                    )
                    .order_by(
                        creative_resource_heads.c.stage,
                        creative_resource_heads.c.episode_number,
                    )
                ).mappings().all()
            ]
            if project["creation_mode"] == "original":
                project["topic"] = {}
                project["breakdown"] = {}
                seed = connection.execute(
                    select(creative_seeds)
                    .where(creative_seeds.c.project_id == int(project_id))
                    .order_by(
                        creative_seeds.c.version.desc(), creative_seeds.c.id.desc()
                    )
                    .limit(1)
                ).mappings().first()
                project["seed"] = self._seed(self.row_dict(seed)) if seed else None
        project["allowed_actions"] = self._allowed_project_actions(project)
        return project

    def update_project(
        self,
        project_id: int,
        values: dict[str, Any],
        *,
        draft_brief_id: int | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        allowed = {
            "name", "genre", "target_platform", "target_audience",
            "target_episode_count", "episode_duration_seconds", "status",
        }
        updates = {
            key: value
            for key, value in values.items()
            if key in allowed and value is not None
        }
        if not updates:
            return project
        if (
            expected_revision is not None
            and int(project.get("revision") or 0) != int(expected_revision)
        ):
            raise ValueError("project revision changed")
        if "target_episode_count" in updates:
            count = int(updates["target_episode_count"])
            if not 1 <= count <= 100:
                raise ValueError("目标集数必须在 1-100 之间")
            updates["target_episode_count"] = count
        if "episode_duration_seconds" in updates:
            duration = int(updates["episode_duration_seconds"])
            if not 30 <= duration <= 1800:
                raise ValueError("单集时长必须在 30-1800 秒之间")
            updates["episode_duration_seconds"] = duration
        updates = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in updates.items()
        }
        if "name" in updates and not updates["name"]:
            raise ValueError("项目名称不能为空")
        if "status" in updates and updates["status"] != "archived":
            raise ValueError("项目仅支持归档状态更新")
        updates = {
            key: value
            for key, value in updates.items()
            if project.get(key) != value
        }
        if not updates:
            return project
        content_updates = set(updates) - {"status"}
        with self.transaction_scope() as connection:
            # Lock order: one project-scoped advisory lock, taken before the
            # Brief-state reads and before the revision compare-and-swap, so the
            # revision guard cannot be raced by another writer of this project.
            self._lock_project_row_identity(connection, project_id)
            if (
                project.get("workflow_mode", "sequential") == "sequential"
                and project.get("locked_brief_id")
                and content_updates
            ):
                if draft_brief_id is None or expected_revision is None:
                    raise ValueError("锁定 Brief 后不能修改项目 Brief 参数")
                draft_brief = connection.execute(
                    select(
                        creative_blueprints.c.project_id,
                        creative_blueprints.c.parent_version_id,
                        creative_blueprints.c.status,
                    ).where(creative_blueprints.c.id == int(draft_brief_id))
                ).mappings().first()
                if (
                    draft_brief is None
                    or int(draft_brief["project_id"]) != int(project_id)
                    or str(draft_brief["status"]) != "draft"
                    or int(draft_brief["parent_version_id"] or 0)
                    != int(project["locked_brief_id"])
                ):
                    raise ValueError("只能通过当前锁定 Brief 的新草稿版本修改项目参数")
            if content_updates and project.get("workflow_mode", "sequential") == "sequential":
                pending_brief = connection.execute(
                    select(creative_blueprints.c.id)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.status == "submitted",
                    )
                    .limit(1)
                ).first()
                if pending_brief:
                    raise ValueError("Brief 完成后不能修改项目 Brief 参数")
            statement = (
                update(creative_projects)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
                .values(**updates, updated_at=utc_now())
            )
            if content_updates:
                statement = statement.values(
                    revision=creative_projects.c.revision + 1
                )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(statement)
            if expected_revision is not None and cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _breakdown_target_audience(content_json: Any) -> str:
        content = content_json
        if isinstance(content, str) or content is None:
            try:
                content = json.loads(content or "{}")
            except (TypeError, ValueError):
                return ""
        if not isinstance(content, Mapping):
            return ""
        return str(content.get("target_audience") or "").strip()

    @staticmethod
    def _topic_based_seed_text(topic: Mapping[str, Any], breakdown: Mapping[str, Any]) -> str:
        parts = [f"选题：{str(topic['title'] or '').strip()}"]
        description = str(topic["description"] or "").strip()
        if description:
            parts.append(f"简介：{description}")
        content = breakdown["content_json"]
        if isinstance(content, str) or content is None:
            try:
                content = json.loads(content or "{}")
            except (TypeError, ValueError):
                content = {}
        if isinstance(content, Mapping):
            conflict = str(content.get("core_conflict") or "").strip()
            if conflict:
                parts.append(f"核心冲突：{conflict}")
        return "\n".join(parts)[:500]

    @staticmethod
    def _allowed_project_actions(project: dict[str, Any]) -> list[str]:
        actions = ["generate_blueprints"]
        if project.get("locked_brief_id"):
            actions.append("generate_characters")
        if project.get("character_batch_id"):
            actions.append("generate_outlines")
        if project.get("outline_batch_id"):
            actions.append("generate_beats")
        return actions

    # -- row transforms -----------------------------------------------------
    #
    # These duplicate the transforms in VersionsRepository on purpose: both
    # domains read the same tables and the mixin order decides which copy wins.
    # Both copies therefore go through ``row_dict`` and return a new mapping, so
    # the winner is interchangeable and a caller may pass either a RowMapping or
    # an already-converted dict. Do not collapse them into an in-place variant.

    @classmethod
    def _blueprint(cls, row: Any) -> dict[str, Any]:
        result = cls.row_dict(row)
        result["content"] = result.pop("content_json") or {}
        result["source_refs"] = result.pop("source_refs_json") or {}
        result["generation_meta"] = result.pop("generation_meta_json") or {}
        return result

    @classmethod
    def _seed(cls, row: Any) -> dict[str, Any]:
        result = cls.row_dict(row)
        result["constraints"] = result.pop("constraints_json") or {}
        result["reference_refs"] = result.pop("reference_refs_json") or []
        return result

    @classmethod
    def _creative_item(cls, row: Any) -> dict[str, Any]:
        result = cls.row_dict(row)
        result["content"] = result.pop("content_json") or {}
        result["source_refs"] = result.pop("source_refs_json") or {}
        result["generation_meta"] = result.pop("generation_meta_json") or {}
        return result

    @classmethod
    def _episode(cls, row: Any) -> dict[str, Any]:
        result = cls.row_dict(row)
        result["content"] = result.pop("content_json") or {}
        if result.get("stage") == "script":
            result["content"] = normalize_script_content(result["content"])
        result["upstream_refs"] = result.pop("upstream_refs_json") or {}
        result["generation_meta"] = result.pop("generation_meta_json") or {}
        return result

    @staticmethod
    def _title_key(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(
            char
            for char in normalized
            if char.isalnum() or "\u4e00" <= char <= "\u9fff"
        )

    def _update_creative_item(
        self,
        table: str,
        item_id: int,
        content: dict[str, Any],
        *,
        episode: bool = False,
        preserve_content_keys: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        if table not in _CREATIVE_ITEM_TABLES:
            raise ValueError("invalid creative table")
        target = _CREATIVE_ITEM_TABLES[table]
        with self.transaction_scope() as connection:
            # Lock order: a single row lock on the edited item, taken before the
            # read-modify-write of its JSONB content.
            row = connection.execute(
                select(target)
                .where(target.c.id == int(item_id))
                .with_for_update()
            ).mappings().first()
            if row is None:
                return None
            if row["status"] != "draft":
                raise ValueError("已完成的版本不能原地修改")
            resolved_content = dict(content)
            current_content = row["content_json"] or {}
            for key in preserve_content_keys:
                if key in current_content:
                    resolved_content[key] = current_content[key]
                else:
                    resolved_content.pop(key, None)
            connection.execute(
                update(target)
                .where(target.c.id == int(item_id))
                .values(content_json=resolved_content, updated_at=utc_now())
            )
            updated = connection.execute(
                select(target).where(target.c.id == int(item_id))
            ).mappings().one()
        converted = self.row_dict(updated)
        return self._episode(converted) if episode else self._creative_item(converted)

    def _get_creative_item(
        self, table: str, item_id: int
    ) -> dict[str, Any] | None:
        if table not in {"creative_characters", "creative_relationships"}:
            raise ValueError("invalid creative table")
        target = _CREATIVE_ITEM_TABLES[table]
        with self.connection_scope() as connection:
            row = connection.execute(
                select(target).where(target.c.id == int(item_id))
            ).mappings().first()
        return self._creative_item(self.row_dict(row)) if row else None


__all__ = ["ProjectsRepository"]
