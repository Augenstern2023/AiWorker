# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Blueprint, character, outline, beats and script version chains.

This mixin is the PostgreSQL port of the versioned part of
:class:`creative.legacy_sqlite_repository.CreativeRepository`: the Brief
(blueprint) chain, character/relationship batches, outline batches and their
checkpoints, episode Beats and scene scripts, plus the optimistic project
``revision`` checks and the creative resource heads that pin a free-mode
version. Method names, signatures, return keys, exception classes and Chinese
error strings are copied from the frozen SQLite implementation on purpose, so
the two back ends stay observationally equal during the cutover window.

Type mapping applied here (legacy SQLite -> PostgreSQL):

* epoch ``REAL`` timestamps -> ``TIMESTAMPTZ``; writes use :func:`utc_now` and
  reads convert back to epoch seconds through ``self.row_dict``.
* ``*_json TEXT`` -> ``JSONB``; Python ``dict``/``list`` values are passed
  straight into the Core statements, never ``json.dumps``.
* 0/1 ``INTEGER`` flags -> ``BOOLEAN`` (``deleted``).
* ``_blueprint``/``_creative_item``/``_episode`` pop the ``*_json`` key and use
  the value that JSONB already decoded, exactly where the legacy helpers called
  ``json.loads``. ``_episode`` keeps normalising script content. Every internal
  call site passes ``self.row_dict(row)`` into them so the composed repository
  behaves identically whether these helpers resolve to this mixin or to the
  equivalent ``ProjectsRepository`` definitions (which also exist and would
  otherwise receive a ``RowMapping`` they cannot mutate).

Cross-domain dependencies
-------------------------

Only the :class:`~creative.repositories.base.CreativeRepositoryBase` contract is
assumed. The project/operation domain mixins own ``get_project``,
``_operation_project``, ``_operation_request_hash``, ``_run_idempotent_operation``,
``_hydrate_resource_ref``, ``_assert_generation_authority_conn``,
``_validate_generation_reservation_conn``, ``_get_creative_item`` and
``_update_creative_item``; every call to them mirrors the legacy call site.
``_assert_generation_authority_conn`` (legacy line 3615, with
``_generation_authority_matches_conn``/``_generation_authority_envelope_conn``)
is still unowned in ``creative/repositories`` while
:class:`~creative.repositories.operations.OperationsRepository` already calls it
too, so the composition must supply it.

Lock order
----------

Writers take PostgreSQL transaction-level advisory locks, and always in this
order (a method never re-acquires a lower-numbered scope after a higher one,
and per-episode scopes are always taken in ascending ``(stage, episode_number)``
order so concurrent checkpoint writers cannot deadlock):

1. resource head scope -- ``head:<project>:<scope>:<stage>:<episode>``
2. batch/group allocation scope -- blueprint candidate group, character batch
   or outline batch of a project
3. per-episode allocation scope -- ``episode:<project>:<stage>:<episode>``
4. the owning row itself -- ``SELECT ... FOR UPDATE`` on ``creative_projects``
   (or the rewritten episode) while an optimistic check is re-verified

Advisory locks are keyed by ``pg_advisory_xact_lock(namespace, hashtext(key))``
and released when the enclosing transaction ends, so they compose with a
caller-supplied connection through :meth:`transaction_scope`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from creative.database_schema import (
    creative_blueprints,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
    creative_resource_heads,
)
from creative.outline_fields import OUTLINE_TEXT_FIELDS, outline_field_source_fingerprint
from creative.outline_overview import normalize_series_overview, read_series_overview
from creative.repositories.base import epoch_seconds, utc_now
from creative.script_service import normalize_script_content, validate_beats, validate_script


# Namespace for the creative version-chain advisory locks ("CreV").
_ADVISORY_LOCK_NAMESPACE = 0x43726556

_FREE_SCOPE = "free"
_DEFAULT_SCOPE = "sequential"
_READABLE_STATUSES = frozenset({"locked", "approved"})


# ---------------------------------------------------------------------------
# advisory lock keys (see "Lock order" in the module docstring)
# ---------------------------------------------------------------------------


def _lock_scope(connection: Connection, scope: str) -> None:
    """Serialize every writer that allocates within ``scope``.

    ``hashtext`` collisions only over-serialize unrelated scopes; they can never
    let two writers into the same scope at once.
    """

    connection.execute(
        select(
            func.pg_advisory_xact_lock(
                _ADVISORY_LOCK_NAMESPACE, func.hashtext(scope)
            )
        )
    )


def _blueprint_scope(project_id: int, candidate_group_id: Any) -> str:
    return f"blueprint:{int(project_id)}:{candidate_group_id}"


def _character_scope(project_id: int) -> str:
    return f"characters:{int(project_id)}"


def _outline_batch_scope(project_id: int) -> str:
    return f"outline-batch:{int(project_id)}"


def _episode_scope(project_id: int, stage: str, episode_number: int) -> str:
    return f"episode:{int(project_id)}:{stage}:{int(episode_number)}"


def _head_scope(
    project_id: int, resource_scope: str, stage: str, episode_number: int
) -> str:
    return f"head:{int(project_id)}:{resource_scope}:{stage}:{int(episode_number)}"


def resource_head_lock_key(
    *, project_id: int, resource_scope: str, stage: str, episode_number: int = 0
) -> str:
    """Return the advisory-lock key that guards one ``creative_resource_heads`` row.

    Exposed so any other creative domain that advances a head can serialize with
    this module instead of inventing a second lock order.
    """

    return _head_scope(int(project_id), resource_scope, stage, int(episode_number))


class VersionsRepository:
    """Blueprint -> script version chains, resource heads and stage locks.

    Plain mixin: it defines no base class and assumes the
    :class:`~creative.repositories.base.CreativeRepositoryBase` contract only.
    """

    # -- brief / blueprints -------------------------------------------------

    def create_blueprints(
        self,
        project_id: int,
        items: list[dict[str, Any]],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        direction_type: str = "imitation",
        source_refs: dict[str, Any] | None = None,
        operation_id: str | None = None,
        first_root_only: bool = False,
        expected_authority: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        request_hash = self._operation_request_hash(
            project_id=project_id,
            stage="brief",
            scope={},
            payload={
                "items": items,
                "job_id": job_id,
                "generation_meta": generation_meta,
                "workflow_task_id": workflow_task_id,
                "direction_type": direction_type,
                "source_refs": source_refs,
                "first_root_only": first_root_only,
                "expected_authority": expected_authority,
            },
        )

        def create(connection: Connection) -> dict[str, Any]:
            if not 1 <= len(items) <= 5:
                raise ValueError("仿制方向数量必须在 1-5 之间")
            project = self._operation_project(connection, project_id)
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if first_root_only:
                if project.get("creation_mode") != "original":
                    raise ValueError(
                        "first Brief is only available for original projects"
                    )
                root = connection.execute(
                    select(creative_blueprints.c.id)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.parent_version_id.is_(None),
                    )
                    .order_by(creative_blueprints.c.id)
                    .limit(1)
                ).first()
                if root is not None:
                    raise ValueError("a root Brief already exists; create a revision")
            now = utc_now()
            refs = source_refs or {
                "topic_id": project["topic_id"],
                "breakdown_id": project["breakdown_id"],
            }
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            ids: list[int] = []
            for item in items:
                ids.append(
                    int(
                        connection.execute(
                            insert(creative_blueprints)
                            .values(
                                project_id=int(project_id),
                                candidate_group_id=uuid.uuid4().hex,
                                content_json=item.get("content") or item,
                                risk_score=float(item.get("risk_score") or 0),
                                risk_level=str(item.get("risk_level") or "low"),
                                difference_note=str(item.get("difference_note") or ""),
                                source_refs_json=refs,
                                direction_type=direction_type,
                                generation_meta_json=meta,
                                workflow_task_id=workflow_task_id
                                or project.get("workflow_task_id"),
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(creative_blueprints.c.id)
                        ).scalar_one()
                    )
                )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(status="direction_review", updated_at=now)
            )
            return {"resource_type": "brief", "resource_ids": ids}

        with self.transaction_scope() as connection:
            if operation_id:
                return self._run_idempotent_operation(
                    connection,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="brief",
                    request_hash=request_hash,
                    create=create,
                )
            resource_ref = create(connection)
            return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def get_blueprint(
        self, blueprint_id: int, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        with self.connection_scope(connection) as connection:
            row = connection.execute(
                select(creative_blueprints).where(
                    creative_blueprints.c.id == int(blueprint_id)
                )
            ).mappings().first()
        return (
            self._blueprint(self.row_dict(row)) if row is not None else None
        )

    def update_blueprint(
        self,
        blueprint_id: int,
        content: dict[str, Any],
        *,
        risk_score: float | None = None,
        risk_level: str | None = None,
        difference_note: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_blueprint(blueprint_id)
        if not current:
            return None
        if current["status"] != "draft":
            raise ValueError("已锁定的 Brief 不能原地修改")
        with self.transaction_scope() as connection:
            connection.execute(
                update(creative_blueprints)
                .where(creative_blueprints.c.id == int(blueprint_id))
                .values(
                    content_json=content,
                    risk_score=float(
                        current["risk_score"] if risk_score is None else risk_score
                    ),
                    risk_level=(
                        current["risk_level"] if risk_level is None else risk_level
                    ),
                    difference_note=(
                        current["difference_note"]
                        if difference_note is None
                        else difference_note.strip()
                    ),
                    updated_at=utc_now(),
                )
            )
        return self.get_blueprint(blueprint_id)

    def create_blueprint_version(
        self, blueprint_id: int, workflow_task_id: str | None = None
    ) -> dict[str, Any]:
        current = self.get_blueprint(blueprint_id)
        if not current:
            raise ValueError("仿制方向不存在")
        now = utc_now()
        with self.transaction_scope() as connection:
            _lock_scope(
                connection,
                _blueprint_scope(int(current["project_id"]), current["candidate_group_id"]),
            )
            new_id = int(
                connection.execute(
                    insert(creative_blueprints)
                    .values(
                        project_id=int(current["project_id"]),
                        candidate_group_id=current["candidate_group_id"],
                        version=self._next_blueprint_version(
                            connection,
                            int(current["project_id"]),
                            current["candidate_group_id"],
                        ),
                        parent_version_id=int(blueprint_id),
                        content_json=current["content"],
                        risk_score=current["risk_score"],
                        risk_level=current["risk_level"],
                        difference_note=current["difference_note"],
                        source_refs_json=current["source_refs"],
                        direction_type=current["direction_type"],
                        generation_meta_json=current["generation_meta"],
                        workflow_task_id=workflow_task_id
                        or current.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_blueprints.c.id)
                ).scalar_one()
            )
        return self.get_blueprint(new_id) or {}

    def create_blueprint_revision(
        self,
        blueprint_id: int,
        content: dict[str, Any],
        workflow_task_id: str | None,
        operation_id: str,
        *,
        expected_project_revision: int | None = None,
        expected_project_id: int | None = None,
    ) -> dict[str, Any]:
        def request_hash(project_id: int) -> str:
            return self._operation_request_hash(
                project_id=project_id,
                stage="brief_revision",
                scope={"blueprint_id": blueprint_id},
                payload={
                    "content": content,
                    "workflow_task_id": workflow_task_id,
                    "expected_project_revision": expected_project_revision,
                    "expected_project_id": expected_project_id,
                },
            )

        source_holder: dict[str, Any] = {}

        def resolve_project_id(connection: Connection) -> int:
            source = connection.execute(
                select(creative_blueprints).where(
                    creative_blueprints.c.id == int(blueprint_id)
                )
            ).mappings().first()
            if source is None:
                raise ValueError("仿制方向不存在")
            source_holder["source"] = source
            return int(source["project_id"])

        def create(connection: Connection) -> dict[str, Any]:
            source = source_holder.get("source")
            if source is None:
                raise ValueError("仿制方向不存在")
            project_id = int(source["project_id"])
            if expected_project_id is not None and project_id != int(expected_project_id):
                raise ValueError("replacement Brief belongs to another project")
            project = self._operation_project(connection, project_id)
            self._assert_expected_revision(project, expected_project_revision)
            _lock_scope(
                connection,
                _blueprint_scope(project_id, source["candidate_group_id"]),
            )
            now = utc_now()
            new_id = int(
                connection.execute(
                    insert(creative_blueprints)
                    .values(
                        project_id=project_id,
                        candidate_group_id=source["candidate_group_id"],
                        version=self._next_blueprint_version(
                            connection, project_id, source["candidate_group_id"]
                        ),
                        parent_version_id=int(blueprint_id),
                        content_json=content,
                        risk_score=source["risk_score"],
                        risk_level=source["risk_level"],
                        difference_note=source["difference_note"],
                        source_refs_json=source["source_refs_json"],
                        direction_type=source["direction_type"],
                        generation_meta_json=source["generation_meta_json"],
                        workflow_task_id=workflow_task_id or source["workflow_task_id"],
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_blueprints.c.id)
                ).scalar_one()
            )
            return {"resource_type": "brief", "resource_id": new_id}

        with self.transaction_scope() as connection:
            return self._run_idempotent_operation(
                connection,
                operation_id=operation_id,
                project_id=None,
                stage="brief_revision",
                request_hash=request_hash,
                create=create,
                resolve_project_id=resolve_project_id,
            )

    @staticmethod
    def _assert_expected_revision(
        project: dict[str, Any], expected_revision: int | None
    ) -> None:
        if (
            expected_revision is not None
            and int(project.get("revision") or 0) != int(expected_revision)
        ):
            raise ValueError("project revision changed")

    def _resource_with_project_revision(
        self,
        resource: dict[str, Any],
        project_id: int,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id, connection=connection)
        result = dict(resource)
        result["revision"] = int((project or {}).get("revision") or 0)
        return result

    def lock_brief(
        self,
        blueprint_id: int,
        difference_note: str = "",
        *,
        expected_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        current = self.get_blueprint(blueprint_id)
        if not current:
            raise ValueError("仿制方向不存在")
        project = self.get_project(int(current["project_id"]))
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        locked_brief_id = int(project.get("locked_brief_id") or 0)
        if locked_brief_id:
            if expected_revision is None:
                raise ValueError("锁定 Brief 新版本时必须提供项目修订号")
            if int(current.get("parent_version_id") or 0) != locked_brief_id:
                raise ValueError("只能锁定当前 Brief 的直接后继草稿")
        if current["status"] == "brief_locked":
            return self._resource_with_project_revision(
                current, int(current["project_id"]), connection=connection
            )
        note = difference_note.strip() or str(
            current.get("difference_note") or ""
        ).strip()
        if (
            current["direction_type"] == "imitation"
            and current["risk_level"] == "high"
            and not note
        ):
            raise ValueError("高风险方向必须填写差异化修改说明")
        content = dict(current["content"])
        content.update(
            {
                "project_name": project["name"],
                "genre": project["genre"],
                "target_platform": project["target_platform"],
                "target_audience": project["target_audience"],
                "target_episode_count": project["target_episode_count"],
                "episode_duration_seconds": project["episode_duration_seconds"],
            }
        )
        now = utc_now()
        # ``caller_connection`` keeps the caller's handle across the ``as``
        # rebinding below, where ``connection`` becomes the scoped one and dies
        # with the block when this call owned it.
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            authority = connection.execute(
                select(
                    creative_projects.c.revision, creative_projects.c.locked_brief_id
                )
                .where(
                    creative_projects.c.id == int(current["project_id"]),
                    creative_projects.c.deleted.is_(False),
                )
                .with_for_update()
            ).mappings().first()
            if authority is None:
                raise ValueError("创作项目不存在")
            if (
                expected_revision is not None
                and int(authority["revision"] or 0) != int(expected_revision)
            ):
                raise ValueError("project revision changed")
            authority_locked_brief_id = int(authority["locked_brief_id"] or 0)
            if authority_locked_brief_id:
                if expected_revision is None:
                    raise ValueError("锁定 Brief 新版本时必须提供项目修订号")
                if (
                    int(current.get("parent_version_id") or 0)
                    != authority_locked_brief_id
                ):
                    raise ValueError("只能锁定当前 Brief 的直接后继草稿")
            self._assert_generation_authority_conn(
                connection, int(current["project_id"]), expected_authority
            )
            connection.execute(
                update(creative_blueprints)
                .where(creative_blueprints.c.id == int(blueprint_id))
                .values(
                    status="brief_locked",
                    content_json=content,
                    difference_note=note,
                    locked_at=now,
                    updated_at=now,
                )
            )
            statement = update(creative_projects).where(
                creative_projects.c.id == int(current["project_id"])
            )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(
                statement.values(
                    locked_brief_id=int(blueprint_id),
                    character_batch_id=None,
                    outline_batch_id=None,
                    status="brief_locked",
                    revision=creative_projects.c.revision + 1,
                    updated_at=now,
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_blueprint(blueprint_id, connection=caller_connection) or {},
            int(current["project_id"]),
            connection=caller_connection,
        )

    def select_locked_brief(
        self,
        project_id: int,
        blueprint_id: int,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Switch the active story direction to another already locked Brief."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        brief = self.get_blueprint(blueprint_id)
        if not brief or int(brief.get("project_id") or 0) != int(project_id):
            raise ValueError("仿制方向不存在")
        if brief.get("status") != "brief_locked":
            raise ValueError("只能选择已锁定的 Brief 版本")
        content = brief.get("content") or {}
        project_name = str(content.get("project_name", project["name"]) or "").strip()
        genre = str(content.get("genre", project["genre"]) or "").strip()
        target_platform = str(
            content.get("target_platform", project["target_platform"]) or ""
        ).strip()
        target_audience = str(
            content.get("target_audience", project["target_audience"]) or ""
        ).strip()
        if not project_name:
            project_name = project["name"]
        episode_count = project["target_episode_count"]
        episode_duration = project["episode_duration_seconds"]
        try:
            candidate_count = int(content.get("target_episode_count") or episode_count)
            if 1 <= candidate_count <= 100:
                episode_count = candidate_count
        except (TypeError, ValueError):
            pass
        try:
            candidate_duration = int(
                content.get("episode_duration_seconds") or episode_duration
            )
            if 30 <= candidate_duration <= 1800:
                episode_duration = candidate_duration
        except (TypeError, ValueError):
            pass

        if int(project.get("locked_brief_id") or 0) == int(blueprint_id):
            if (
                project["name"] == project_name
                and project["genre"] == genre
                and project["target_platform"] == target_platform
                and project["target_audience"] == target_audience
                and int(project["target_episode_count"]) == episode_count
                and int(project["episode_duration_seconds"]) == episode_duration
            ):
                return project
            with self.transaction_scope() as connection:
                statement = update(creative_projects).where(
                    creative_projects.c.id == int(project_id)
                )
                if expected_revision is not None:
                    statement = statement.where(
                        creative_projects.c.revision == int(expected_revision)
                    )
                cursor = connection.execute(
                    statement.values(
                        name=project_name,
                        genre=genre,
                        target_platform=target_platform,
                        target_audience=target_audience,
                        target_episode_count=episode_count,
                        episode_duration_seconds=episode_duration,
                        revision=creative_projects.c.revision + 1,
                        updated_at=utc_now(),
                    )
                )
                if cursor.rowcount != 1:
                    raise ValueError("project revision changed")
            return self.get_project(project_id) or {}
        with self.transaction_scope() as connection:
            statement = update(creative_projects).where(
                creative_projects.c.id == int(project_id)
            )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(
                statement.values(
                    locked_brief_id=int(blueprint_id),
                    character_batch_id=None,
                    outline_batch_id=None,
                    name=project_name,
                    genre=genre,
                    target_platform=target_platform,
                    target_audience=target_audience,
                    target_episode_count=episode_count,
                    episode_duration_seconds=episode_duration,
                    status="brief_locked",
                    revision=creative_projects.c.revision + 1,
                    updated_at=utc_now(),
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

    # -- characters and relationships ---------------------------------------

    def create_character_batch(
        self,
        project_id: int,
        characters: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        operation_id: str | None = None,
        replaces_resource_id: str | None = None,
        expected_project_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        reject_if_locked: bool = False,
    ) -> dict[str, Any]:
        request_hash = self._operation_request_hash(
            project_id=project_id,
            stage="characters",
            scope={},
            payload={
                "characters": characters,
                "relationships": relationships,
                "job_id": job_id,
                "generation_meta": generation_meta,
                "workflow_task_id": workflow_task_id,
                "replaces_resource_id": replaces_resource_id,
                "expected_project_revision": expected_project_revision,
                "expected_authority": expected_authority,
                "reject_if_locked": reject_if_locked,
            },
        )

        def create(connection: Connection) -> dict[str, Any]:
            project = self._operation_project(connection, project_id)
            self._assert_expected_revision(project, expected_project_revision)
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reject_if_locked and project.get("character_batch_id"):
                raise ValueError("locked characters require an explicit revision")
            if not project.get("locked_brief_id"):
                raise ValueError("必须先锁定 Brief")
            replacement_batch_id: str | None = None
            if replaces_resource_id is not None:
                raw_project, separator, raw_batch = str(replaces_resource_id).partition(
                    ":"
                )
                replacement_batch_id = raw_batch if separator else raw_project
                if separator and int(raw_project) != int(project_id):
                    raise ValueError(
                        "replacement character batch belongs to another project"
                    )
                exists = connection.execute(
                    select(creative_characters.c.id)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == replacement_batch_id,
                    )
                    .limit(1)
                ).first()
                if exists is None:
                    raise ValueError("replacement character batch does not exist")
            batch_id = uuid.uuid4().hex
            now = utc_now()
            refs = {"brief_id": project["locked_brief_id"]}
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            _lock_scope(connection, _character_scope(project_id))
            version = self._next_character_version(connection, project_id)
            for index, content in enumerate(characters, start=1):
                key = str(content.get("name") or f"character-{index}").strip()
                if replacement_batch_id is not None:
                    parent = connection.execute(
                        select(creative_characters.c.id)
                        .where(
                            creative_characters.c.project_id == int(project_id),
                            creative_characters.c.batch_id == replacement_batch_id,
                            creative_characters.c.character_key == key,
                        )
                        .limit(1)
                    ).first()
                else:
                    parent = connection.execute(
                        select(creative_characters.c.id)
                        .where(
                            creative_characters.c.project_id == int(project_id),
                            creative_characters.c.character_key == key,
                        )
                        .order_by(
                            creative_characters.c.version.desc(),
                            creative_characters.c.id.desc(),
                        )
                        .limit(1)
                    ).first()
                connection.execute(
                    insert(creative_characters).values(
                        project_id=int(project_id),
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        character_key=key,
                        content_json=content,
                        source_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                )
            for index, content in enumerate(relationships, start=1):
                key = f"{content.get('from') or ''}>{content.get('to') or ''}:{index}"
                if replacement_batch_id is not None:
                    parent = connection.execute(
                        select(creative_relationships.c.id)
                        .where(
                            creative_relationships.c.project_id == int(project_id),
                            creative_relationships.c.batch_id == replacement_batch_id,
                            creative_relationships.c.relationship_key == key,
                        )
                        .limit(1)
                    ).first()
                else:
                    parent = connection.execute(
                        select(creative_relationships.c.id)
                        .where(
                            creative_relationships.c.project_id == int(project_id),
                            creative_relationships.c.relationship_key == key,
                        )
                        .order_by(
                            creative_relationships.c.version.desc(),
                            creative_relationships.c.id.desc(),
                        )
                        .limit(1)
                    ).first()
                connection.execute(
                    insert(creative_relationships).values(
                        project_id=int(project_id),
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        relationship_key=key,
                        content_json=content,
                        source_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(status="characters_draft", updated_at=now)
            )
            return {
                "resource_type": "characters",
                "resource_id": f"{int(project_id)}:{batch_id}",
            }

        with self.transaction_scope() as connection:
            if operation_id:
                return self._run_idempotent_operation(
                    connection,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="characters",
                    request_hash=request_hash,
                    create=create,
                )
            resource_ref = create(connection)
            return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def get_character_batch(
        self, project_id: int, batch_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        project = self.get_project(project_id, connection=connection)
        if not project:
            raise ValueError("创作项目不存在")
        return {
            "batch_id": batch_id,
            "characters": [
                item for item in project["characters"] if item["batch_id"] == batch_id
            ],
            "relationships": [
                item
                for item in project["relationships"]
                if item["batch_id"] == batch_id
            ],
        }

    def get_character(self, character_id: int) -> dict[str, Any] | None:
        return self._get_creative_item("creative_characters", character_id)

    def get_relationship(self, relationship_id: int) -> dict[str, Any] | None:
        return self._get_creative_item("creative_relationships", relationship_id)

    def update_character(
        self, character_id: int, content: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self._update_creative_item("creative_characters", character_id, content)

    def update_relationship(
        self, relationship_id: int, content: dict[str, Any]
    ) -> dict[str, Any] | None:
        return self._update_creative_item(
            "creative_relationships", relationship_id, content
        )

    def lock_character_batch(
        self,
        project_id: int,
        batch_id: str,
        *,
        expected_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        batch = self.get_character_batch(project_id, batch_id)
        if not batch["characters"]:
            raise ValueError("人物关系版本至少需要一个人物")
        if project.get("character_batch_id") == batch_id and all(
            item.get("status") in _READABLE_STATUSES for item in batch["characters"]
        ):
            return self._resource_with_project_revision(batch, project_id, connection=connection)
        now = utc_now()
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            authority = connection.execute(
                select(creative_projects.c.revision)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
                .with_for_update()
            ).mappings().first()
            if authority is None:
                raise ValueError("创作项目不存在")
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            connection.execute(
                update(creative_characters)
                .where(
                    creative_characters.c.project_id == int(project_id),
                    creative_characters.c.batch_id == batch_id,
                )
                .values(status="locked", locked_at=now, updated_at=now)
            )
            connection.execute(
                update(creative_relationships)
                .where(
                    creative_relationships.c.project_id == int(project_id),
                    creative_relationships.c.batch_id == batch_id,
                )
                .values(status="locked", locked_at=now, updated_at=now)
            )
            statement = update(creative_projects).where(
                creative_projects.c.id == int(project_id)
            )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(
                statement.values(
                    character_batch_id=batch_id,
                    outline_batch_id=None,
                    status="characters_locked",
                    revision=creative_projects.c.revision + 1,
                    updated_at=now,
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_character_batch(project_id, batch_id, connection=caller_connection),
            project_id,
            connection=caller_connection,
        )

    def select_locked_character_batch(
        self,
        project_id: int,
        batch_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Use a compatible locked character batch as the active outline context."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        if not project.get("locked_brief_id"):
            raise ValueError("必须先选择已锁定的 Brief")

        batch = self.get_character_batch(project_id, batch_id)
        characters = batch["characters"]
        if not characters:
            raise ValueError("人物关系版本至少需要一个人物")
        if any(
            item.get("status") not in _READABLE_STATUSES for item in characters
        ):
            raise ValueError("只能选择已锁定的人物关系版本")
        active_brief_id = int(project["locked_brief_id"])
        if any(
            int((item.get("source_refs") or {}).get("brief_id") or 0)
            != active_brief_id
            for item in characters
        ):
            raise ValueError("所选人物关系版本不属于当前锁定 Brief")

        if project.get("character_batch_id") == batch_id:
            return project
        with self.transaction_scope() as connection:
            statement = update(creative_projects).where(
                creative_projects.c.id == int(project_id)
            )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(
                statement.values(
                    character_batch_id=batch_id,
                    outline_batch_id=None,
                    status="characters_locked",
                    revision=creative_projects.c.revision + 1,
                    updated_at=utc_now(),
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

    # -- outline ------------------------------------------------------------

    def create_outline_batch(
        self,
        project_id: int,
        episodes: list[dict[str, Any]],
        series_arc: str,
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        series_overview: dict[str, Any] | None = None,
        operation_id: str | None = None,
        replaces_resource_id: str | None = None,
        expected_project_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        reject_if_locked: bool = False,
    ) -> dict[str, Any]:
        request_hash = self._operation_request_hash(
            project_id=project_id,
            stage="outline",
            scope={},
            payload={
                "episodes": episodes,
                "series_arc": series_arc,
                "job_id": job_id,
                "generation_meta": generation_meta,
                "workflow_task_id": workflow_task_id,
                "series_overview": series_overview,
                "replaces_resource_id": replaces_resource_id,
                "expected_project_revision": expected_project_revision,
                "expected_authority": expected_authority,
                "reject_if_locked": reject_if_locked,
            },
        )

        def create(connection: Connection) -> dict[str, Any]:
            project = self._operation_project(connection, project_id)
            self._assert_expected_revision(project, expected_project_revision)
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reject_if_locked and project.get("outline_batch_id"):
                raise ValueError("locked outline requires an explicit revision")
            if not project.get("character_batch_id"):
                raise ValueError("必须先锁定人物关系版本")
            replacement_batch_id: str | None = None
            if replaces_resource_id is not None:
                raw_project, separator, raw_batch = str(replaces_resource_id).partition(
                    ":"
                )
                replacement_batch_id = raw_batch if separator else raw_project
                if separator and int(raw_project) != int(project_id):
                    raise ValueError(
                        "replacement outline batch belongs to another project"
                    )
                exists = connection.execute(
                    select(creative_episodes.c.id)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == replacement_batch_id,
                    )
                    .limit(1)
                ).first()
                if exists is None:
                    raise ValueError("replacement outline batch does not exist")
            expected = list(range(1, int(project["target_episode_count"]) + 1))
            actual = sorted(int(item.get("episode") or 0) for item in episodes)
            if actual != expected:
                raise ValueError(
                    f"分集大纲必须完整覆盖 1-{project['target_episode_count']} 集"
                )
            batch_id = uuid.uuid4().hex
            refs = {
                "brief_id": project["locked_brief_id"],
                "character_batch_id": project["character_batch_id"],
            }
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            now = utc_now()
            normalized_overview = (
                normalize_series_overview(series_overview)
                if series_overview is not None
                else None
            )
            _lock_scope(connection, _outline_batch_scope(project_id))
            version = self._next_outline_version(connection, project_id)
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
                if normalized_overview is not None:
                    content["series_overview"] = normalized_overview
                if replacement_batch_id is not None:
                    parent = connection.execute(
                        select(creative_episodes.c.id)
                        .where(
                            creative_episodes.c.project_id == int(project_id),
                            creative_episodes.c.stage == "outline",
                            creative_episodes.c.batch_id == replacement_batch_id,
                            creative_episodes.c.episode_number
                            == int(content["episode"]),
                        )
                        .limit(1)
                    ).first()
                    if parent is None:
                        raise ValueError("replacement outline batch is incomplete")
                else:
                    parent = connection.execute(
                        select(creative_episodes.c.id)
                        .where(
                            creative_episodes.c.project_id == int(project_id),
                            creative_episodes.c.stage == "outline",
                            creative_episodes.c.episode_number
                            == int(content["episode"]),
                        )
                        .order_by(
                            creative_episodes.c.version.desc(),
                            creative_episodes.c.id.desc(),
                        )
                        .limit(1)
                    ).first()
                connection.execute(
                    insert(creative_episodes).values(
                        project_id=int(project_id),
                        stage="outline",
                        episode_number=int(content["episode"]),
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        content_json=content,
                        upstream_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(status="outline_draft", updated_at=now)
            )
            return {
                "resource_type": "outline",
                "resource_id": f"{int(project_id)}:{batch_id}",
            }

        with self.transaction_scope() as connection:
            if operation_id:
                return self._run_idempotent_operation(
                    connection,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="outline",
                    request_hash=request_hash,
                    create=create,
                )
            resource_ref = create(connection)
            return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def save_outline_batch_checkpoint(
        self,
        project_id: int,
        episodes: list[dict[str, Any]],
        series_arc: str,
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        *,
        batch_id: str | None = None,
        series_overview: dict[str, Any] | None = None,
        reservation_operation_id: str | None = None,
        reservation_request_hash: str | None = None,
        reservation_owner_id: str | None = None,
        expected_authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a contiguous generated outline prefix so a failed job is recoverable."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if not project.get("character_batch_id"):
            raise ValueError("必须先锁定人物关系版本")
        total = int(project["target_episode_count"])
        actual = sorted(int(item.get("episode") or 0) for item in episodes)
        expected = list(range(1, len(episodes) + 1))
        if not episodes or actual != expected or len(episodes) > total:
            raise ValueError(f"分集大纲检查点必须连续覆盖 1-{total} 集的前缀")

        existing_rows = []
        if batch_id:
            existing_rows = [
                item
                for item in project["episodes"]
                if item["stage"] == "outline" and item["batch_id"] == batch_id
            ]
            if existing_rows and any(
                item["status"] != "draft" for item in existing_rows
            ):
                raise ValueError("已锁定的大纲批次不能写入生成检查点")
        else:
            batch_id = uuid.uuid4().hex
        existing_by_episode = {
            int(item["episode_number"]): item for item in existing_rows
        }
        refs = {
            "brief_id": project["locked_brief_id"],
            "character_batch_id": project["character_batch_id"],
        }
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        now = utc_now()
        normalized_overview = (
            normalize_series_overview(series_overview)
            if series_overview is not None
            else None
        )
        with self.transaction_scope() as connection:
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reservation_operation_id is not None:
                if reservation_request_hash is None or batch_id is None:
                    raise ValueError("generation reservation identity is incomplete")
                self._validate_generation_reservation_conn(
                    connection,
                    operation_id=reservation_operation_id,
                    request_hash=reservation_request_hash,
                    batch_id=batch_id,
                    owner_id=reservation_owner_id,
                )
            _lock_scope(connection, _outline_batch_scope(project_id))
            if existing_rows:
                version = int(existing_rows[0]["version"])
            else:
                version = self._next_outline_version(connection, project_id)
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
                if normalized_overview is not None:
                    content["series_overview"] = normalized_overview
                episode_number = int(content["episode"])
                existing = existing_by_episode.get(episode_number)
                if existing:
                    connection.execute(
                        update(creative_episodes)
                        .where(creative_episodes.c.id == int(existing["id"]))
                        .values(
                            content_json=content,
                            upstream_refs_json=refs,
                            generation_meta_json=meta,
                            workflow_task_id=workflow_task_id
                            or project.get("workflow_task_id"),
                            updated_at=now,
                        )
                    )
                    continue
                parent = connection.execute(
                    select(creative_episodes.c.id)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.episode_number == episode_number,
                        creative_episodes.c.batch_id != batch_id,
                    )
                    .order_by(
                        creative_episodes.c.version.desc(),
                        creative_episodes.c.id.desc(),
                    )
                    .limit(1)
                ).first()
                connection.execute(
                    insert(creative_episodes).values(
                        project_id=int(project_id),
                        stage="outline",
                        episode_number=episode_number,
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        content_json=content,
                        upstream_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(status="outline_draft", updated_at=now)
            )
        return self.get_outline_batch(project_id, batch_id)

    def find_resumable_outline_batch(
        self,
        project_id: int,
        *,
        operation_id: str | None = None,
        include_complete: bool = False,
    ) -> dict[str, Any] | None:
        """Return the newest compatible incomplete draft batch, if it can safely continue."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        total = int(project["target_episode_count"])
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in project["episodes"]:
            refs = item.get("upstream_refs") or {}
            if (
                item["stage"] == "outline"
                and item.get("status") == "draft"
                and (
                    operation_id is None
                    or item.get("generation_meta", {}).get("operation_id")
                    == operation_id
                )
                and refs.get("brief_id") == project.get("locked_brief_id")
                and refs.get("character_batch_id") == project.get("character_batch_id")
            ):
                groups.setdefault(str(item["batch_id"]), []).append(item)

        candidates: list[tuple[tuple[int, float, int], dict[str, Any]]] = []
        for batch_id, rows in groups.items():
            rows.sort(key=lambda item: int(item["episode_number"]))
            numbers = [int(item["episode_number"]) for item in rows]
            if (
                not rows
                or len(rows) > total
                or (len(rows) == total and not include_complete)
                or numbers != list(range(1, len(rows) + 1))
            ):
                continue
            content = rows[0].get("content") or {}
            meta = rows[0].get("generation_meta") or {}
            series_arc = str(content.get("series_arc") or "").strip()
            stages = meta.get("stages")
            if not series_arc or not isinstance(stages, list) or not stages:
                continue
            key = (
                int(rows[0].get("version") or 0),
                max(float(item.get("updated_at") or 0) for item in rows),
                max(int(item["id"]) for item in rows),
            )
            candidates.append(
                (
                    key,
                    {
                        "batch_id": batch_id,
                        "episodes": rows,
                        "series_arc": series_arc,
                        "stages": list(stages),
                        "series_overview": read_series_overview(
                            content.get("series_overview")
                        ),
                    },
                )
            )
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_outline_batch(
        self, project_id: int, batch_id: str, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        project = self.get_project(project_id, connection=connection)
        if not project:
            raise ValueError("创作项目不存在")
        rows = [
            item
            for item in project["episodes"]
            if item["stage"] == "outline" and item["batch_id"] == batch_id
        ]
        overview = (
            read_series_overview((rows[0].get("content") or {}).get("series_overview"))
            if rows
            else None
        )
        return {"batch_id": batch_id, "series_overview": overview, "episodes": rows}

    def _outline_field_rewrite_context_conn(
        self, connection: Connection, episode_id: int
    ) -> dict[str, Any]:
        target_row = connection.execute(
            select(creative_episodes).where(
                creative_episodes.c.id == int(episode_id)
            )
        ).mappings().first()
        if target_row is None or target_row["stage"] != "outline":
            raise ValueError("分集大纲不存在")

        project_row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(target_row["project_id"]),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if project_row is None:
            raise ValueError("创作项目不存在")
        project = self.row_dict(project_row)
        if str(project.get("workflow_mode") or _DEFAULT_SCOPE) == _FREE_SCOPE:
            return self._free_outline_field_rewrite_context_conn(
                connection, project, target_row
            )
        locked_brief_id = int(project.get("locked_brief_id") or 0)
        character_batch_id = str(project.get("character_batch_id") or "")
        if not locked_brief_id:
            raise ValueError("必须先锁定 Brief")
        if not character_batch_id:
            raise ValueError("必须先锁定人物关系版本")

        brief_row = connection.execute(
            select(creative_blueprints).where(
                creative_blueprints.c.id == locked_brief_id,
                creative_blueprints.c.project_id == int(project["id"]),
                creative_blueprints.c.status == "brief_locked",
            )
        ).mappings().first()
        if brief_row is None:
            raise ValueError("必须先锁定 Brief")

        characters = [
            self._creative_item(self.row_dict(row))
            for row in connection.execute(
                select(creative_characters)
                .where(
                    creative_characters.c.project_id == int(project["id"]),
                    creative_characters.c.batch_id == character_batch_id,
                )
                .order_by(creative_characters.c.version, creative_characters.c.id)
            ).mappings().all()
        ]
        relationships = [
            self._creative_item(self.row_dict(row))
            for row in connection.execute(
                select(creative_relationships)
                .where(
                    creative_relationships.c.project_id == int(project["id"]),
                    creative_relationships.c.batch_id == character_batch_id,
                )
                .order_by(
                    creative_relationships.c.version, creative_relationships.c.id
                )
            ).mappings().all()
        ]

        outline_rows = connection.execute(
            select(creative_episodes)
            .where(
                creative_episodes.c.project_id == int(project["id"]),
                creative_episodes.c.stage == "outline",
            )
            .order_by(creative_episodes.c.version, creative_episodes.c.id)
        ).mappings().all()

        def matches_current_context(row: Any) -> bool:
            refs = row["upstream_refs_json"] or {}
            return (
                int(refs.get("brief_id") or 0) == locked_brief_id
                and str(refs.get("character_batch_id") or "") == character_batch_id
            )

        if not matches_current_context(target_row):
            raise ValueError("只能编辑当前创作上下文的大纲批次")

        compatible_drafts = [
            row
            for row in outline_rows
            if row["status"] == "draft" and matches_current_context(row)
        ]
        target_status = str(target_row["status"])
        if target_status == "draft":
            latest_draft = max(
                compatible_drafts,
                key=lambda row: (
                    int(row["version"] or 0),
                    float(epoch_seconds(row["updated_at"]) or 0),
                    int(row["id"] or 0),
                ),
                default=None,
            )
            if latest_draft is None or str(latest_draft["batch_id"]) != str(
                target_row["batch_id"]
            ):
                raise ValueError("只能编辑当前大纲批次")
        elif target_status in _READABLE_STATUSES:
            if str(project.get("outline_batch_id") or "") != str(
                target_row["batch_id"]
            ):
                raise ValueError("只能编辑当前已完成的大纲批次")
            if any(
                int(row["version"] or 0) > int(target_row["version"] or 0)
                for row in compatible_drafts
            ):
                raise ValueError("当前已完成大纲存在更高版本草稿")
        else:
            raise ValueError("已完成的版本不能原地修改")

        same_batch = [
            row
            for row in outline_rows
            if str(row["batch_id"]) == str(target_row["batch_id"])
        ]
        by_episode_number = {int(row["episode_number"]): row for row in same_batch}
        episode_number = int(target_row["episode_number"])
        previous = by_episode_number.get(episode_number - 1)
        following = by_episode_number.get(episode_number + 1)
        return {
            "project": project,
            "brief": self._blueprint(self.row_dict(brief_row)),
            "characters": characters,
            "relationships": relationships,
            "episode": self._episode(self.row_dict(target_row)),
            "previous_episode": (
                self._episode(self.row_dict(previous)) if previous else None
            ),
            "next_episode": (
                self._episode(self.row_dict(following)) if following else None
            ),
        }

    def _free_outline_field_rewrite_context_conn(
        self,
        connection: Connection,
        project: dict[str, Any],
        target_row: Any,
    ) -> dict[str, Any]:
        if str(target_row["resource_scope"] or _DEFAULT_SCOPE) != _FREE_SCOPE:
            raise ValueError("分集大纲不属于当前自由创作版本")
        head = connection.execute(
            select(creative_resource_heads.c.resource_ref).where(
                creative_resource_heads.c.project_id == int(project["id"]),
                creative_resource_heads.c.resource_scope == _FREE_SCOPE,
                creative_resource_heads.c.stage == "outline",
                creative_resource_heads.c.episode_number == 0,
            )
        ).mappings().first()
        if head is None or str(head["resource_ref"] or "") != str(
            target_row["batch_id"]
        ):
            raise ValueError("只能编辑当前自由创作大纲版本")
        if str(target_row["status"] or "") not in {
            "draft",
            "locked",
            "approved",
        }:
            raise ValueError("当前自由创作大纲版本不可重写")

        same_batch = connection.execute(
            select(creative_episodes)
            .where(
                creative_episodes.c.project_id == int(project["id"]),
                creative_episodes.c.stage == "outline",
                creative_episodes.c.batch_id == str(target_row["batch_id"]),
                creative_episodes.c.resource_scope == _FREE_SCOPE,
            )
            .order_by(creative_episodes.c.episode_number)
        ).mappings().all()
        by_episode_number = {int(row["episode_number"]): row for row in same_batch}
        episode_number = int(target_row["episode_number"])
        refs = target_row["upstream_refs_json"] or {}

        brief = None
        brief_id = int(refs.get("brief_id") or 0)
        if brief_id:
            brief_row = connection.execute(
                select(creative_blueprints).where(
                    creative_blueprints.c.id == brief_id,
                    creative_blueprints.c.project_id == int(project["id"]),
                    creative_blueprints.c.resource_scope == _FREE_SCOPE,
                )
            ).mappings().first()
            if brief_row is not None:
                brief = self._blueprint(self.row_dict(brief_row))

        characters: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        character_batch_id = str(refs.get("character_batch_id") or "")
        if character_batch_id:
            characters = [
                self._creative_item(self.row_dict(row))
                for row in connection.execute(
                    select(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project["id"]),
                        creative_characters.c.batch_id == character_batch_id,
                        creative_characters.c.resource_scope == _FREE_SCOPE,
                    )
                    .order_by(creative_characters.c.id)
                ).mappings().all()
            ]
            relationships = [
                self._creative_item(self.row_dict(row))
                for row in connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project["id"]),
                        creative_relationships.c.batch_id == character_batch_id,
                        creative_relationships.c.resource_scope == _FREE_SCOPE,
                    )
                    .order_by(creative_relationships.c.id)
                ).mappings().all()
            ]

        previous = by_episode_number.get(episode_number - 1)
        following = by_episode_number.get(episode_number + 1)
        return {
            "project": project,
            "brief": brief or {},
            "characters": characters,
            "relationships": relationships,
            "episode": self._episode(self.row_dict(target_row)),
            "previous_episode": (
                self._episode(self.row_dict(previous)) if previous else None
            ),
            "next_episode": (
                self._episode(self.row_dict(following)) if following else None
            ),
        }

    def get_outline_field_rewrite_context(self, episode_id: int) -> dict[str, Any]:
        with self.connection_scope() as connection:
            return self._outline_field_rewrite_context_conn(connection, episode_id)

    def apply_outline_field_rewrite(
        self,
        episode_id: int,
        field: str,
        candidate_value: str,
        expected_fingerprint: str,
        *,
        expected_head_revision: int | None = None,
    ) -> dict[str, Any]:
        if field not in OUTLINE_TEXT_FIELDS:
            raise ValueError("不支持的大纲字段")
        if not isinstance(candidate_value, str) or not candidate_value.strip():
            raise ValueError("重写内容不能为空")

        with self.transaction_scope() as connection:
            context = self._outline_field_rewrite_context_conn(connection, episode_id)
            if expected_head_revision is not None:
                # Lock order 1: the head scope is always taken before row locks.
                _lock_scope(
                    connection,
                    _head_scope(
                        int(context["project"]["id"]), _FREE_SCOPE, "outline", 0
                    ),
                )
                head = connection.execute(
                    select(creative_resource_heads.c.revision)
                    .where(
                        creative_resource_heads.c.project_id
                        == int(context["project"]["id"]),
                        creative_resource_heads.c.resource_scope == _FREE_SCOPE,
                        creative_resource_heads.c.stage == "outline",
                        creative_resource_heads.c.episode_number == 0,
                    )
                    .with_for_update()
                ).mappings().first()
                if head is None or int(head["revision"]) != int(
                    expected_head_revision
                ):
                    raise ValueError("creative resource head changed")
            row = connection.execute(
                select(creative_episodes).where(
                    creative_episodes.c.id == int(episode_id)
                )
            ).mappings().first()
            if row is None:
                raise ValueError("分集大纲不存在")
            episode = self._episode(self.row_dict(row))
            if episode["status"] != "draft":
                raise ValueError("已完成的版本不能原地修改")
            if outline_field_source_fingerprint(episode, field) != expected_fingerprint:
                raise ValueError("该字段已被更新，请重新生成")
            content = dict(row["content_json"] or {})
            content[field] = candidate_value.strip()
            now = utc_now()
            connection.execute(
                update(creative_episodes)
                .where(creative_episodes.c.id == int(episode_id))
                .values(content_json=content, updated_at=now)
            )
            updated = connection.execute(
                select(creative_episodes).where(
                    creative_episodes.c.id == int(episode_id)
                )
            ).mappings().first()
        return (
            self._episode(self.row_dict(updated)) if updated is not None else {}
        )

    def _validated_outline_overview_rows(
        self,
        connection: Connection,
        project_id: int,
        batch_id: str,
        *,
        require_missing: bool,
        allow_current_locked: bool = False,
    ) -> list[Any]:
        project = connection.execute(
            select(
                creative_projects.c.locked_brief_id,
                creative_projects.c.character_batch_id,
                creative_projects.c.outline_batch_id,
                creative_projects.c.target_episode_count,
            ).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if project is None:
            raise ValueError("创作项目不存在")
        all_rows = connection.execute(
            select(creative_episodes)
            .where(
                creative_episodes.c.project_id == int(project_id),
                creative_episodes.c.stage == "outline",
            )
            .order_by(creative_episodes.c.version, creative_episodes.c.id)
        ).mappings().all()
        rows = [row for row in all_rows if str(row["batch_id"]) == batch_id]
        if not rows:
            raise ValueError("分集大纲批次不存在")
        is_draft = all(row["status"] == "draft" for row in rows)
        is_locked_backfill = bool(
            allow_current_locked
            and all(row["status"] in _READABLE_STATUSES for row in rows)
        )
        if not is_draft and not is_locked_backfill:
            raise ValueError("已完成的版本不能原地修改")

        locked_brief_id = int(project["locked_brief_id"] or 0)
        character_batch_id = str(project["character_batch_id"] or "")

        def same_context(row: Any) -> bool:
            refs = row["upstream_refs_json"] or {}
            return (
                int(refs.get("brief_id") or 0) == locked_brief_id
                and str(refs.get("character_batch_id") or "") == character_batch_id
            )

        if any(not same_context(row) for row in rows):
            raise ValueError("只能编辑当前创作上下文的大纲批次")
        if is_locked_backfill:
            if str(project["outline_batch_id"] or "") != batch_id:
                raise ValueError("只能补充当前已完成的大纲批次")
        else:
            compatible_rows = [
                row
                for row in all_rows
                if row["status"] == "draft" and same_context(row)
            ]
            latest = max(
                compatible_rows,
                key=lambda row: (
                    int(row["version"] or 0),
                    float(epoch_seconds(row["updated_at"]) or 0),
                    int(row["id"] or 0),
                ),
                default=None,
            )
            if latest is None or str(latest["batch_id"]) != batch_id:
                raise ValueError("只能编辑当前大纲批次")

        expected = list(range(1, int(project["target_episode_count"]) + 1))
        actual = sorted(int(row["episode_number"]) for row in rows)
        if actual != expected:
            raise ValueError(
                f"分集大纲必须完整覆盖 1-{project['target_episode_count']} 集后才能生成全集剧情概览"
            )
        if require_missing and any(
            read_series_overview(
                (row["content_json"] or {}).get("series_overview")
            )
            for row in rows
        ):
            raise ValueError("当前大纲版本已存在全集剧情概览")
        return sorted(rows, key=lambda row: int(row["episode_number"]))

    def assert_outline_overview_generation_ready(
        self, project_id: int, batch_id: str
    ) -> dict[str, Any]:
        """Validate a current complete batch before spending a model request."""

        with self.connection_scope() as connection:
            self._validated_outline_overview_rows(
                connection,
                project_id,
                batch_id,
                require_missing=True,
                allow_current_locked=True,
            )
        return self.get_outline_batch(project_id, batch_id)

    def update_outline_overview(
        self,
        project_id: int,
        batch_id: str,
        series_overview: dict[str, Any],
        *,
        only_if_missing: bool = False,
        expected_authority: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically update overview metadata, allowing locked one-time backfill."""

        normalized = normalize_series_overview(series_overview)
        now = utc_now()
        with self.transaction_scope() as connection:
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            persisted = self._validated_outline_overview_rows(
                connection,
                project_id,
                batch_id,
                require_missing=only_if_missing,
                allow_current_locked=only_if_missing,
            )
            for row in persisted:
                content = dict(row["content_json"] or {})
                content["series_overview"] = normalized
                connection.execute(
                    update(creative_episodes)
                    .where(creative_episodes.c.id == int(row["id"]))
                    .values(content_json=content, updated_at=now)
                )
        return self.get_outline_batch(project_id, batch_id)

    def update_episode(
        self, episode_id: int, content: dict[str, Any]
    ) -> dict[str, Any] | None:
        episode = self.get_episode(episode_id)
        preserve_content_keys: tuple[str, ...] = ()
        if episode:
            if episode.get("stage") == "outline":
                preserve_content_keys = ("series_overview",)
            elif episode.get("stage") == "beats":
                content = validate_beats(content, allow_legacy=True)
            elif episode.get("stage") == "script":
                content = validate_script(content)
                return self._create_script_revision_if_changed(episode, content)
        return self._update_creative_item(
            "creative_episodes",
            episode_id,
            content,
            episode=True,
            preserve_content_keys=preserve_content_keys,
        )

    def _create_script_revision_if_changed(
        self, episode: dict[str, Any], content: dict[str, Any]
    ) -> dict[str, Any]:
        if episode.get("status") != "draft":
            raise ValueError("已完成的版本不能原地修改")
        if json.dumps(
            episode.get("content") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) == json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ):
            return episode

        now = utc_now()
        metadata = dict(episode.get("generation_meta") or {})
        metadata.update({"manual_revision": True, "revision_source_id": int(episode["id"])})
        project_id = int(episode["project_id"])
        episode_number = int(episode["episode_number"])
        with self.transaction_scope() as connection:
            _lock_scope(connection, _episode_scope(project_id, "script", episode_number))
            new_id = int(
                connection.execute(
                    insert(creative_episodes)
                    .values(
                        project_id=project_id,
                        stage="script",
                        episode_number=episode_number,
                        batch_id=uuid.uuid4().hex,
                        version=self._next_episode_version(
                            connection, project_id, "script", episode_number
                        ),
                        parent_version_id=int(episode["id"]),
                        content_json=content,
                        upstream_refs_json=episode.get("upstream_refs") or {},
                        generation_meta_json=metadata,
                        workflow_task_id=episode.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_episodes.c.id)
                ).scalar_one()
            )
        return self.get_episode(new_id) or {}

    def lock_outline_batch(
        self,
        project_id: int,
        batch_id: str,
        *,
        expected_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        rows = [
            item
            for item in project["episodes"]
            if item["stage"] == "outline" and item["batch_id"] == batch_id
        ]
        if len(rows) != int(project["target_episode_count"]):
            raise ValueError("分集大纲不完整，不能锁定")
        if project.get("outline_batch_id") == batch_id and all(
            item.get("status") in _READABLE_STATUSES for item in rows
        ):
            return self._resource_with_project_revision(
                self.get_outline_batch(project_id, batch_id, connection=connection),
                project_id,
                connection=connection,
            )
        now = utc_now()
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            authority = connection.execute(
                select(creative_projects.c.revision)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
                .with_for_update()
            ).mappings().first()
            if authority is None:
                raise ValueError("创作项目不存在")
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            connection.execute(
                update(creative_episodes)
                .where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == "outline",
                    creative_episodes.c.batch_id == batch_id,
                )
                .values(status="locked", locked_at=now, updated_at=now)
            )
            statement = update(creative_projects).where(
                creative_projects.c.id == int(project_id)
            )
            if expected_revision is not None:
                statement = statement.where(
                    creative_projects.c.revision == int(expected_revision)
                )
            cursor = connection.execute(
                statement.values(
                    outline_batch_id=batch_id,
                    status="outline_locked",
                    revision=creative_projects.c.revision + 1,
                    updated_at=now,
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_outline_batch(project_id, batch_id, connection=caller_connection),
            project_id,
            connection=caller_connection,
        )

    # -- beats and script ---------------------------------------------------

    def create_episode_beats(
        self,
        project_id: int,
        episode_number: int,
        content: dict[str, Any],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        update_project_status: bool = True,
        operation_id: str | None = None,
        replaces_resource_id: str | None = None,
        expected_project_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        reject_if_locked: bool = False,
    ) -> dict[str, Any]:
        request_hash = self._operation_request_hash(
            project_id=project_id,
            stage="beats",
            scope={"episode_number": int(episode_number)},
            payload={
                "content": content,
                "job_id": job_id,
                "generation_meta": generation_meta,
                "workflow_task_id": workflow_task_id,
                "update_project_status": update_project_status,
                "replaces_resource_id": replaces_resource_id,
                "expected_project_revision": expected_project_revision,
                "expected_authority": expected_authority,
                "reject_if_locked": reject_if_locked,
            },
        )

        def create(connection: Connection) -> dict[str, Any]:
            # Lock order 3: take the per-episode allocation scope before reading
            # the project snapshot, so concurrent writers of one episode
            # serialize exactly like the legacy ``BEGIN IMMEDIATE`` lock did.
            _lock_scope(
                connection, _episode_scope(int(project_id), "beats", int(episode_number))
            )
            project = self._operation_project(
                connection, project_id, include_episodes=True
            )
            self._assert_expected_revision(project, expected_project_revision)
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reject_if_locked and any(
                item["stage"] == "beats"
                and int(item["episode_number"]) == int(episode_number)
                and item.get("status") in _READABLE_STATUSES
                for item in project["episodes"]
            ):
                raise ValueError("locked Beats require an explicit revision")
            if not project.get("outline_batch_id"):
                raise ValueError("必须先锁定分集大纲")
            if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            refs = self._episode_generation_refs(project, "beats", episode_number)
            now = utc_now()
            if replaces_resource_id is not None:
                parent = connection.execute(
                    select(creative_episodes.c.id, creative_episodes.c.version)
                    .where(
                        creative_episodes.c.id == int(replaces_resource_id),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "beats",
                        creative_episodes.c.episode_number == int(episode_number),
                    )
                ).first()
                if parent is None:
                    raise ValueError(
                        "replacement Beats does not belong to this episode"
                    )
            else:
                parent = connection.execute(
                    select(creative_episodes.c.id, creative_episodes.c.version)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "beats",
                        creative_episodes.c.episode_number == int(episode_number),
                    )
                    .order_by(
                        creative_episodes.c.version.desc(),
                        creative_episodes.c.id.desc(),
                    )
                    .limit(1)
                ).first()
            new_id = int(
                connection.execute(
                    insert(creative_episodes)
                    .values(
                        project_id=int(project_id),
                        stage="beats",
                        episode_number=int(episode_number),
                        batch_id=uuid.uuid4().hex,
                        version=self._next_episode_version(
                            connection, int(project_id), "beats", int(episode_number)
                        ),
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        content_json=content,
                        upstream_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_episodes.c.id)
                ).scalar_one()
            )
            if update_project_status:
                connection.execute(
                    update(creative_projects)
                    .where(creative_projects.c.id == int(project_id))
                    .values(status="beats_in_progress", updated_at=now)
                )
            return {"resource_type": "beats", "resource_id": new_id}

        with self.transaction_scope() as connection:
            if operation_id:
                return self._run_idempotent_operation(
                    connection,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="beats",
                    request_hash=request_hash,
                    create=create,
                )
            resource_ref = create(connection)
            return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def create_episode_script(
        self,
        project_id: int,
        episode_number: int,
        content: dict[str, Any],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        update_project_status: bool = True,
        operation_id: str | None = None,
        replaces_resource_id: str | None = None,
        expected_project_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        reject_if_locked: bool = False,
    ) -> dict[str, Any]:
        request_hash = self._operation_request_hash(
            project_id=project_id,
            stage="script",
            scope={"episode_number": int(episode_number)},
            payload={
                "content": content,
                "job_id": job_id,
                "generation_meta": generation_meta,
                "workflow_task_id": workflow_task_id,
                "update_project_status": update_project_status,
                "replaces_resource_id": replaces_resource_id,
                "expected_project_revision": expected_project_revision,
                "expected_authority": expected_authority,
                "reject_if_locked": reject_if_locked,
            },
        )

        def create(connection: Connection) -> dict[str, Any]:
            # Lock order 3: see create_episode_beats.
            _lock_scope(
                connection,
                _episode_scope(int(project_id), "script", int(episode_number)),
            )
            project = self._operation_project(
                connection, project_id, include_episodes=True
            )
            self._assert_expected_revision(project, expected_project_revision)
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reject_if_locked and any(
                item["stage"] == "script"
                and int(item["episode_number"]) == int(episode_number)
                and item.get("status") in _READABLE_STATUSES
                for item in project["episodes"]
            ):
                raise ValueError("locked script requires an explicit revision")
            if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            refs = self._episode_generation_refs(project, "script", episode_number)
            now = utc_now()
            if replaces_resource_id is not None:
                parent = connection.execute(
                    select(creative_episodes.c.id, creative_episodes.c.version)
                    .where(
                        creative_episodes.c.id == int(replaces_resource_id),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "script",
                        creative_episodes.c.episode_number == int(episode_number),
                    )
                ).first()
                if parent is None:
                    raise ValueError(
                        "replacement script does not belong to this episode"
                    )
            else:
                parent = connection.execute(
                    select(creative_episodes.c.id, creative_episodes.c.version)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "script",
                        creative_episodes.c.episode_number == int(episode_number),
                    )
                    .order_by(
                        creative_episodes.c.version.desc(),
                        creative_episodes.c.id.desc(),
                    )
                    .limit(1)
                ).first()
            new_id = int(
                connection.execute(
                    insert(creative_episodes)
                    .values(
                        project_id=int(project_id),
                        stage="script",
                        episode_number=int(episode_number),
                        batch_id=uuid.uuid4().hex,
                        version=self._next_episode_version(
                            connection, int(project_id), "script", int(episode_number)
                        ),
                        parent_version_id=int(parent[0]) if parent is not None else None,
                        content_json=content,
                        upstream_refs_json=refs,
                        generation_meta_json=meta,
                        workflow_task_id=workflow_task_id
                        or project.get("workflow_task_id"),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(creative_episodes.c.id)
                ).scalar_one()
            )
            if update_project_status:
                connection.execute(
                    update(creative_projects)
                    .where(creative_projects.c.id == int(project_id))
                    .values(status="scripts_in_progress", updated_at=now)
                )
            return {"resource_type": "script", "resource_id": new_id}

        with self.transaction_scope() as connection:
            if operation_id:
                return self._run_idempotent_operation(
                    connection,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="script",
                    request_hash=request_hash,
                    create=create,
                )
            resource_ref = create(connection)
            return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def _locked_outline_for_episode(
        self, project: dict[str, Any], episode_number: int
    ) -> dict[str, Any]:
        outlines = [
            item
            for item in project["episodes"]
            if item["stage"] == "outline"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("batch_id") == project.get("outline_batch_id")
            and item.get("status") in _READABLE_STATUSES
        ]
        if not outlines:
            raise ValueError("必须先锁定当前集分集大纲")
        return max(
            outlines, key=lambda item: (int(item.get("version") or 0), int(item["id"]))
        )

    def _locked_beats_for_episode(
        self, project: dict[str, Any], episode_number: int
    ) -> dict[str, Any]:
        beats = [
            item
            for item in project["episodes"]
            if item["stage"] == "beats"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("status") in _READABLE_STATUSES
            and self.episode_generation_refs_match(
                project,
                "beats",
                episode_number,
                item.get("upstream_refs"),
            )
        ]
        if not beats:
            raise ValueError("必须先锁定当前集 Beats")
        return max(
            beats, key=lambda item: (int(item.get("version") or 0), int(item["id"]))
        )

    def compatible_locked_beats(
        self,
        project: dict[str, Any],
        episode_numbers: Iterable[int],
    ) -> list[dict[str, Any]]:
        """Select the exact current-compatible Beats used by authority and prompts."""

        selected: list[dict[str, Any]] = []
        for episode_number in sorted({int(number) for number in episode_numbers}):
            if episode_number < 1:
                continue
            try:
                selected.append(
                    self._locked_beats_for_episode(project, episode_number)
                )
            except ValueError:
                continue
        return selected

    def missing_current_locked_beats(self, project_id: int) -> list[int]:
        """Return episodes without Beats locked against the current outline batch."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        return [
            episode_number
            for episode_number in range(
                1, int(project["target_episode_count"]) + 1
            )
            if self._missing_current_locked_beats(project, episode_number)
        ]

    def _missing_current_locked_beats(
        self, project: dict[str, Any], episode_number: int
    ) -> bool:
        try:
            self._locked_beats_for_episode(project, episode_number)
        except ValueError:
            return True
        return False

    @staticmethod
    def _content_fingerprint(content: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                content or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _outline_authority_content(cls, content: Any) -> Any:
        """Return outline content without overview-only metadata."""

        normalized = dict(content) if isinstance(content, dict) else content
        if isinstance(normalized, dict):
            normalized.pop("series_overview", None)
        return normalized

    @classmethod
    def _outline_content_fingerprint(cls, content: Any) -> str:
        """Fingerprint only outline fields that can invalidate downstream work."""

        return cls._content_fingerprint(cls._outline_authority_content(content))

    @classmethod
    def _outline_content_fingerprint_matches(
        cls, fingerprint: Any, content: Any
    ) -> bool:
        """Accept both canonical and historical full-content outline hashes."""

        saved = str(fingerprint or "")
        return bool(saved) and saved in {
            cls._outline_content_fingerprint(content),
            cls._content_fingerprint(content),
        }

    def _locked_generation_context_refs(
        self, project: dict[str, Any]
    ) -> dict[str, Any]:
        brief = next(
            (
                item
                for item in project["blueprints"]
                if item["id"] == project.get("locked_brief_id")
            ),
            None,
        )
        if not brief:
            raise ValueError("必须先锁定 Brief")
        character_batch_id = project.get("character_batch_id")
        if not character_batch_id:
            raise ValueError("必须先锁定人物关系")
        readable_statuses = _READABLE_STATUSES
        characters = [
            item
            for item in project["characters"]
            if item.get("batch_id") == character_batch_id
            and item.get("status") in readable_statuses
        ]
        if not characters:
            raise ValueError("必须先锁定人物关系")
        relationships = [
            item
            for item in project["relationships"]
            if item.get("batch_id") == character_batch_id
            and item.get("status") in readable_statuses
        ]
        return {
            "brief_id": brief["id"],
            "brief_version": brief.get("version"),
            "brief_content_fingerprint": self._content_fingerprint(
                brief.get("content") or {}
            ),
            "character_batch_id": character_batch_id,
            "character_content_fingerprint": self._content_fingerprint(
                {
                    "characters": [
                        {
                            "id": item["id"],
                            "version": item.get("version"),
                            "content": item.get("content") or {},
                        }
                        for item in sorted(characters, key=lambda item: int(item["id"]))
                    ],
                    "relationships": [
                        {
                            "id": item["id"],
                            "version": item.get("version"),
                            "content": item.get("content") or {},
                        }
                        for item in sorted(
                            relationships, key=lambda item: int(item["id"])
                        )
                    ],
                }
            ),
        }

    def _episode_generation_refs(
        self, project: dict[str, Any], stage: str, episode_number: int
    ) -> dict[str, Any]:
        outline = self._locked_outline_for_episode(project, episode_number)
        refs = {
            **self._locked_generation_context_refs(project),
            "outline_batch_id": project.get("outline_batch_id"),
            "outline_id": outline["id"],
            "outline_version": outline["version"],
            "outline_content_fingerprint": self._outline_content_fingerprint(
                outline.get("content") or {}
            ),
        }
        if stage == "script":
            beats = self._locked_beats_for_episode(project, episode_number)
            refs.update(
                {
                    "beats_id": beats["id"],
                    "beats_version": beats["version"],
                    "beats_content_fingerprint": self._content_fingerprint(
                        beats.get("content") or {}
                    ),
                }
            )
        return refs

    def episode_generation_refs_match(
        self,
        project: dict[str, Any],
        stage: str,
        episode_number: int,
        saved_refs: Any,
    ) -> bool:
        """Strictly reject incomplete legacy authority; allow only known outline hashes."""

        if not isinstance(saved_refs, dict):
            return False
        expected = self._episode_generation_refs(project, stage, episode_number)
        saved = dict(saved_refs)
        saved_outline_fingerprint = saved.pop("outline_content_fingerprint", None)
        expected.pop("outline_content_fingerprint", None)
        outline = self._locked_outline_for_episode(project, episode_number)
        return saved == expected and self._outline_content_fingerprint_matches(
            saved_outline_fingerprint, outline.get("content") or {}
        )

    def save_episode_generation_checkpoint(
        self,
        project_id: int,
        stage: str,
        items: list[tuple[int, dict[str, Any]]],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        *,
        batch_id: str | None = None,
        reservation_operation_id: str | None = None,
        reservation_request_hash: str | None = None,
        reservation_owner_id: str | None = None,
        expected_authority: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically persist one full-series generation checkpoint of episode drafts."""
        if stage not in {"beats", "script"}:
            raise ValueError("批量检查点仅支持 Beats 或场次剧本")
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        total = int(project["target_episode_count"])
        episode_numbers = [int(number) for number, _ in items]
        if not items or len(set(episode_numbers)) != len(episode_numbers):
            raise ValueError("批量检查点必须包含不重复的剧集")
        if any(number < 1 or number > total for number in episode_numbers):
            raise ValueError("集数超出项目范围")
        if stage == "beats" and not project.get("outline_batch_id"):
            raise ValueError("必须先锁定分集大纲")
        batch_id = batch_id or uuid.uuid4().hex
        meta = {**(generation_meta or {}), "series_generation": True}
        if job_id:
            meta["job_id"] = job_id
        prepared: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for episode_number, content in sorted(items, key=lambda item: int(item[0])):
            episode_number = int(episode_number)
            normalized = (
                validate_beats(content) if stage == "beats" else validate_script(content)
            )
            refs = self._episode_generation_refs(project, stage, episode_number)
            prepared.append((episode_number, normalized, refs))

        now = utc_now()
        ids: list[int] = []
        with self.transaction_scope() as connection:
            self._assert_generation_authority_conn(
                connection, project_id, expected_authority
            )
            if reservation_operation_id is not None:
                if reservation_request_hash is None:
                    raise ValueError("generation reservation identity is incomplete")
                self._validate_generation_reservation_conn(
                    connection,
                    operation_id=reservation_operation_id,
                    request_hash=reservation_request_hash,
                    batch_id=batch_id,
                    owner_id=reservation_owner_id,
                )
            # Lock order 3: per-episode allocation scopes in ascending order.
            for episode_number, _, _ in prepared:
                _lock_scope(
                    connection,
                    _episode_scope(int(project_id), stage, episode_number),
                )
            for episode_number, content, refs in prepared:
                prior = connection.execute(
                    select(creative_episodes.c.id, creative_episodes.c.version)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == stage,
                        creative_episodes.c.episode_number == episode_number,
                    )
                    .order_by(
                        creative_episodes.c.version.desc(),
                        creative_episodes.c.id.desc(),
                    )
                ).all()
                version = 1 + max([int(row[1]) for row in prior] or [0])
                parent_id = int(prior[0][0]) if prior else None
                ids.append(
                    int(
                        connection.execute(
                            insert(creative_episodes)
                            .values(
                                project_id=int(project_id),
                                stage=stage,
                                episode_number=episode_number,
                                batch_id=batch_id,
                                version=version,
                                parent_version_id=parent_id,
                                content_json=content,
                                upstream_refs_json=refs,
                                generation_meta_json=meta,
                                workflow_task_id=workflow_task_id
                                or project.get("workflow_task_id"),
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(creative_episodes.c.id)
                        ).scalar_one()
                    )
                )
            project_status = (
                "beats_in_progress" if stage == "beats" else "scripts_in_progress"
            )
            connection.execute(
                update(creative_projects)
                .where(creative_projects.c.id == int(project_id))
                .values(status=project_status, updated_at=now)
            )
        return [self.get_episode(episode_id) or {} for episode_id in ids]

    def find_resumable_episode_series(
        self,
        project_id: int,
        stage: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if stage not in {"beats", "script"}:
            raise ValueError("批量续写仅支持 Beats 或场次剧本")
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        total = int(project["target_episode_count"])
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in project["episodes"]:
            if (
                item.get("stage") == stage
                and item.get("status") == "draft"
                and item.get("generation_meta", {}).get("series_generation")
                and (
                    operation_id is None
                    or item.get("generation_meta", {}).get("operation_id")
                    == operation_id
                )
            ):
                groups.setdefault(str(item["batch_id"]), []).append(item)
        candidates: list[tuple[tuple[int, float], dict[str, Any]]] = []
        for batch_id, rows in groups.items():
            rows.sort(key=lambda item: int(item["episode_number"]))
            numbers = [int(item["episode_number"]) for item in rows]
            if not rows or len(rows) > total or numbers != list(range(1, len(rows) + 1)):
                continue
            try:
                compatible = all(
                    self.episode_generation_refs_match(
                        project,
                        stage,
                        int(item["episode_number"]),
                        item.get("upstream_refs"),
                    )
                    for item in rows
                )
            except ValueError:
                compatible = False
            if not compatible:
                continue
            candidates.append(
                (
                    (
                        len(rows),
                        max(float(item.get("updated_at") or 0) for item in rows),
                    ),
                    {
                        "batch_id": batch_id,
                        "episodes": rows,
                        "completed_episode_count": len(rows),
                    },
                )
            )
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_episode(
        self, episode_id: int, *, connection: Connection | None = None
    ) -> dict[str, Any] | None:
        with self.connection_scope(connection) as connection:
            row = connection.execute(
                select(creative_episodes).where(
                    creative_episodes.c.id == int(episode_id)
                )
            ).mappings().first()
        return (
            self._episode(self.row_dict(row)) if row is not None else None
        )

    def list_episode_versions(
        self,
        project_id: int,
        stage: str,
        episode_number: int,
    ) -> list[dict[str, Any]]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(creative_episodes)
                .where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == stage,
                    creative_episodes.c.episode_number == int(episode_number),
                )
                .order_by(creative_episodes.c.version.asc(), creative_episodes.c.id.asc())
            ).mappings().all()
        return [self._episode(self.row_dict(row)) for row in rows]

    def lock_episode_beats(
        self,
        episode_id: int,
        *,
        expected_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        episode = self.get_episode(episode_id)
        if not episode or episode["stage"] != "beats":
            raise ValueError("单集 Beats 不存在")
        validate_beats(episode.get("content") or {}, allow_legacy=True)
        now = utc_now()
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            self._assert_expected_revision(
                self._operation_project(connection, int(episode["project_id"])),
                expected_revision,
            )
            self._assert_generation_authority_conn(
                connection, int(episode["project_id"]), expected_authority
            )
            connection.execute(
                update(creative_episodes)
                .where(creative_episodes.c.id == int(episode_id))
                .values(status="locked", locked_at=now, updated_at=now)
            )
        return self.get_episode(episode_id, connection=caller_connection) or {}

    def batch_lockable_episode_beats(self, project_id: int) -> list[dict[str, Any]]:
        """Return the latest compatible Beats draft for every project episode."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        outline_batch_id = str(project.get("outline_batch_id") or "")
        if not outline_batch_id:
            raise ValueError("必须先锁定分集大纲")

        selected: list[dict[str, Any]] = []
        missing: list[int] = []
        stale: list[int] = []
        for episode_number in range(1, int(project["target_episode_count"]) + 1):
            candidates = [
                item
                for item in project.get("episodes", [])
                if item.get("stage") == "beats"
                and int(item.get("episode_number") or 0) == episode_number
                and str(
                    (item.get("upstream_refs") or {}).get("outline_batch_id") or ""
                )
                == outline_batch_id
                and item.get("status") in {"draft", "locked", "approved"}
            ]
            if not candidates:
                missing.append(episode_number)
                continue
            item = max(
                candidates,
                key=lambda value: (int(value.get("version") or 0), int(value["id"])),
            )
            outline = self._locked_outline_for_episode(project, episode_number)
            refs = item.get("upstream_refs") or {}
            if (
                int(refs.get("outline_id") or 0) != int(outline["id"])
                or int(refs.get("outline_version") or 0) != int(outline["version"])
                or not self._outline_content_fingerprint_matches(
                    refs.get("outline_content_fingerprint"),
                    outline.get("content") or {},
                )
            ):
                stale.append(episode_number)
                continue
            selected.append(item)

        if missing:
            raise ValueError(
                f"以下集数缺少当前版本 Beats 草稿：{'、'.join(map(str, missing))}"
            )
        if stale:
            raise ValueError(
                f"以下集数 Beats 引用的分集大纲已过期：{'、'.join(map(str, stale))}"
            )
        drafts = [item for item in selected if item.get("status") == "draft"]
        if not drafts:
            raise ValueError("当前没有可批量锁定的 Beats 草稿")
        for item in drafts:
            validate_beats(item.get("content") or {}, allow_legacy=True)
        return drafts

    def lock_current_episode_beats_batch(
        self, project_id: int, *, connection: Connection | None = None
    ) -> list[dict[str, Any]]:
        """Atomically lock all Beats drafts returned by batch_lockable_episode_beats."""
        drafts = self.batch_lockable_episode_beats(project_id)
        now = utc_now()
        ids = [int(item["id"]) for item in drafts]
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            connection.execute(
                update(creative_episodes)
                .where(
                    creative_episodes.c.stage == "beats",
                    creative_episodes.c.status == "draft",
                    creative_episodes.c.id.in_(ids),
                )
                .values(status="locked", locked_at=now, updated_at=now)
            )
        return [
            self.get_episode(episode_id, connection=caller_connection) or {}
            for episode_id in ids
        ]

    def lock_episode_script(
        self,
        episode_id: int,
        *,
        expected_revision: int | None = None,
        expected_authority: dict[str, Any] | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        episode = self.get_episode(episode_id)
        if not episode or episode["stage"] != "script":
            raise ValueError("单集场次剧本不存在")
        now = utc_now()
        caller_connection = connection
        with self.transaction_scope(caller_connection) as connection:
            self._assert_expected_revision(
                self._operation_project(connection, int(episode["project_id"])),
                expected_revision,
            )
            self._assert_generation_authority_conn(
                connection, int(episode["project_id"]), expected_authority
            )
            connection.execute(
                update(creative_episodes)
                .where(creative_episodes.c.id == int(episode_id))
                .values(status="locked", locked_at=now, updated_at=now)
            )
        return self.get_episode(episode_id, connection=caller_connection) or {}

    # -- resource heads -----------------------------------------------------

    def _advance_resource_head_conn(
        self,
        connection: Connection,
        *,
        project_id: int,
        stage: str,
        resource_ref: str,
        resource_version: int,
        expected_revision: int,
        resource_scope: str = _FREE_SCOPE,
        episode_number: int = 0,
        updated_by: str = "",
    ) -> dict[str, Any]:
        """Advance one ``creative_resource_heads`` row, or raise if the head moved.

        This is the race-safe port of the free-mode head write: the advisory lock
        (lock order 1) plus ``SELECT ... FOR UPDATE`` guarantee that concurrent
        writers serialize, so ``revision`` increases exactly once per accepted
        writer and can never be duplicated. A stale ``expected_revision`` raises
        the legacy ``ValueError("creative resource head changed")``.
        """

        number = int(episode_number)
        _lock_scope(
            connection,
            _head_scope(int(project_id), resource_scope, stage, number),
        )
        row = connection.execute(
            select(creative_resource_heads.c.revision)
            .where(
                creative_resource_heads.c.project_id == int(project_id),
                creative_resource_heads.c.resource_scope == resource_scope,
                creative_resource_heads.c.stage == stage,
                creative_resource_heads.c.episode_number == number,
            )
            .with_for_update()
        ).mappings().first()
        now = utc_now()
        if row is None:
            if int(expected_revision) != 0:
                raise ValueError("creative resource head changed")
            revision = 1
            connection.execute(
                insert(creative_resource_heads).values(
                    project_id=int(project_id),
                    resource_scope=resource_scope,
                    stage=stage,
                    episode_number=number,
                    resource_ref=str(resource_ref),
                    resource_version=int(resource_version),
                    revision=revision,
                    updated_at=now,
                    updated_by=updated_by,
                )
            )
        else:
            if int(row["revision"] or 0) != int(expected_revision):
                raise ValueError("creative resource head changed")
            revision = int(expected_revision) + 1
            connection.execute(
                update(creative_resource_heads)
                .where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == resource_scope,
                    creative_resource_heads.c.stage == stage,
                    creative_resource_heads.c.episode_number == number,
                )
                .values(
                    resource_ref=str(resource_ref),
                    resource_version=int(resource_version),
                    revision=revision,
                    updated_at=now,
                    updated_by=updated_by,
                )
            )
        return {
            "project_id": int(project_id),
            "resource_scope": resource_scope,
            "stage": stage,
            "episode_number": number,
            "resource_ref": str(resource_ref),
            "resource_version": int(resource_version),
            "revision": revision,
            "updated_at": now.timestamp(),
            "updated_by": updated_by,
        }

    # -- version allocation ------------------------------------------------

    @staticmethod
    def _next_blueprint_version(
        connection: Connection, project_id: int, candidate_group_id: Any
    ) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_blueprints.c.version), 0)).where(
                creative_blueprints.c.project_id == int(project_id),
                creative_blueprints.c.candidate_group_id == candidate_group_id,
            )
        ).scalar_one()
        return int(latest) + 1

    @staticmethod
    def _next_character_version(connection: Connection, project_id: int) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_characters.c.version), 0)).where(
                creative_characters.c.project_id == int(project_id)
            )
        ).scalar_one()
        return int(latest) + 1

    @staticmethod
    def _next_outline_version(connection: Connection, project_id: int) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_episodes.c.version), 0)).where(
                creative_episodes.c.project_id == int(project_id),
                creative_episodes.c.stage == "outline",
            )
        ).scalar_one()
        return int(latest) + 1

    @staticmethod
    def _next_episode_version(
        connection: Connection, project_id: int, stage: str, episode_number: int
    ) -> int:
        latest = connection.execute(
            select(func.coalesce(func.max(creative_episodes.c.version), 0)).where(
                creative_episodes.c.project_id == int(project_id),
                creative_episodes.c.stage == stage,
                creative_episodes.c.episode_number == int(episode_number),
            )
        ).scalar_one()
        return int(latest) + 1

    # -- row transforms -----------------------------------------------------

    @classmethod
    def _blueprint(cls, row: Any) -> dict[str, Any]:
        result = cls.row_dict(row)
        result["content"] = result.pop("content_json") or {}
        result["source_refs"] = result.pop("source_refs_json") or {}
        result["generation_meta"] = result.pop("generation_meta_json") or {}
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


__all__ = ["VersionsRepository", "resource_head_lock_key"]
