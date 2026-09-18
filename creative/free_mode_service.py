# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Free-workflow resources on the PostgreSQL creative store.

This module used to open the legacy SQLite store through the repository's
private connection helper and drive it with raw positional-placeholder SQL. The
runtime repository is now
:class:`creative.postgres_repository.PostgresCreativeRepository`, which exposes
no such helper at all: it offers
:meth:`~creative.repositories.base.CreativeRepositoryBase.connection_scope` for
borrowed reads and
:meth:`~creative.repositories.base.CreativeRepositoryBase.transaction_scope` for
writes that this service owns.

The port keeps the legacy behaviour as the contract:

* read paths prefer the repository's public methods and only fall back to
  SQLAlchemy Core against :mod:`creative.database_schema` when the repository
  exposes no method for the exact query (targeted resource-head reads, counts,
  generation contexts, mode migrations);
* every write runs inside ``transaction_scope`` so the transaction is committed
  only when this service owns it; a caller-supplied connection is never
  committed, rolled back or closed;
* epoch ``time.time()`` timestamps become :func:`utc_now` aware datetimes, and
  JSON payloads are bound as native mappings instead of ``json.dumps`` text,
  because ``*_json`` columns are ``JSONB`` here;
* the resource head compare-and-swap is delegated to
  :meth:`~creative.repositories.versions.VersionsRepository._advance_resource_head_conn`,
  the repository's race-safe port of the legacy unguarded head write.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from creative.database_schema import (
    creative_blueprints,
    creative_breakdowns,
    creative_characters,
    creative_episodes,
    creative_generation_contexts,
    creative_generation_operations,
    creative_mode_migrations,
    creative_projects,
    creative_relationships,
    creative_resource_heads,
    creative_seeds,
    creative_topics,
    script_optimizations,
    script_reviews,
)
from creative.legacy_sqlite_repository import canonical_request_hash
from creative.outline_fields import outline_field_source_fingerprint
from creative.prompt_templates import PROMPT_TEMPLATE_VERSION
from creative.repositories.base import (
    CreativeRepositoryBase,
    from_epoch_seconds,
    utc_now,
)


FREE_SCOPE = "free"
SEQUENTIAL_SCOPE = "sequential"
FREE_STAGES = {"brief", "characters", "outline", "beats", "script"}
LOCKED_STATUSES = {"brief_locked", "locked", "approved"}
ALLOWED_CONTEXT_REFS = {
    "brief": set(),
    "characters": {"brief_id"},
    "outline": {"brief_id", "character_batch_id"},
    "beats": {"brief_id", "character_batch_id", "outline_batch_id"},
    "script": {
        "brief_id",
        "character_batch_id",
        "outline_batch_id",
        "beats_id",
    },
}


class FreeCreativeModeService:
    """Own free-mode resources without touching sequential authority pointers."""

    def __init__(self, repository: CreativeRepositoryBase) -> None:
        self.repository = repository

    @staticmethod
    def _loads(value: Any, fallback: Any) -> Any:
        """Decode a JSON payload, or pass an already decoded JSONB value through.

        The legacy store handed back ``*_json`` columns as text; PostgreSQL hands
        back the mapping/sequence the ``JSONB`` column already decoded. Both
        shapes must return the same value, so a mapping or list is returned
        as-is instead of being re-parsed.
        """

        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _assert_stage(stage: str) -> str:
        normalized = str(stage or "").strip().lower()
        if normalized not in FREE_STAGES:
            raise ValueError("unsupported creative stage")
        return normalized

    def _raw_project(self, connection: Connection, project_id: int) -> dict[str, Any]:
        """Read the one live project row the service authorises against."""

        row = connection.execute(
            select(creative_projects).where(
                creative_projects.c.id == int(project_id),
                creative_projects.c.deleted.is_(False),
            )
        ).mappings().first()
        if row is None:
            raise ValueError("创作项目不存在")
        return self.repository.row_dict(row)

    def assert_free_project(self, project_id: int) -> dict[str, Any]:
        with self.repository.connection_scope() as connection:
            project = self._raw_project(connection, project_id)
        if str(project.get("workflow_mode") or "sequential") != FREE_SCOPE:
            raise ValueError("该操作仅支持自由创作项目")
        return project

    def list_heads(self, project_id: int) -> list[dict[str, Any]]:
        self.assert_free_project(project_id)
        # The repository exposes heads through the project aggregate, already
        # scoped to the project's workflow mode (free here) and ordered by
        # (stage, episode_number) exactly like the legacy query.
        project = self.repository.get_project(project_id) or {}
        return [dict(row) for row in project.get("resource_heads") or []]

    def get_head(
        self, project_id: int, stage: str, episode_number: int | None = None
    ) -> dict[str, Any] | None:
        stage = self._assert_stage(stage)
        number = int(episode_number or 0)
        # A targeted read: the project aggregate would only expose heads of the
        # project's current mode, while this lookup must always filter
        # resource_scope = 'free' (and must not require the project to exist).
        with self.repository.connection_scope() as connection:
            row = connection.execute(
                select(creative_resource_heads).where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == FREE_SCOPE,
                    creative_resource_heads.c.stage == stage,
                    creative_resource_heads.c.episode_number == number,
                )
            ).mappings().first()
        return self.repository.row_dict(row) if row is not None else None

    def _set_head_conn(
        self,
        connection: Connection,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        resource_ref: str,
        resource_version: int,
        expected_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        """Advance one free-mode head inside the caller's transaction.

        Delegates to the repository's race-safe head writer: it takes the head
        advisory lock and a ``SELECT ... FOR UPDATE`` before the
        compare-and-swap, so concurrent writers cannot mint the same revision.
        The returned mapping has the same shape the legacy helper produced,
        including the epoch-second ``updated_at``.
        """

        return self.repository._advance_resource_head_conn(
            connection,
            project_id=int(project_id),
            stage=stage,
            resource_ref=str(resource_ref),
            resource_version=int(resource_version),
            expected_revision=int(expected_revision),
            resource_scope=FREE_SCOPE,
            episode_number=int(episode_number),
            updated_by=actor_user_id,
        )

    def _assert_head_resource(
        self,
        connection: Connection,
        project_id: int,
        stage: str,
        episode_number: int,
        resource_ref: str,
    ) -> int:
        if stage == "brief":
            version = connection.execute(
                select(creative_blueprints.c.version).where(
                    creative_blueprints.c.id == int(resource_ref),
                    creative_blueprints.c.project_id == int(project_id),
                    creative_blueprints.c.resource_scope == FREE_SCOPE,
                )
            ).scalar_one_or_none()
        elif stage == "characters":
            version = connection.execute(
                select(func.max(creative_characters.c.version)).where(
                    creative_characters.c.project_id == int(project_id),
                    creative_characters.c.batch_id == str(resource_ref),
                    creative_characters.c.resource_scope == FREE_SCOPE,
                )
            ).scalar_one()
        elif stage == "outline":
            version = connection.execute(
                select(func.max(creative_episodes.c.version)).where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == "outline",
                    creative_episodes.c.batch_id == str(resource_ref),
                    creative_episodes.c.resource_scope == FREE_SCOPE,
                )
            ).scalar_one()
        else:
            version = connection.execute(
                select(creative_episodes.c.version).where(
                    creative_episodes.c.id == int(resource_ref),
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == stage,
                    creative_episodes.c.episode_number == int(episode_number),
                    creative_episodes.c.resource_scope == FREE_SCOPE,
                )
            ).scalar_one_or_none()
        if version is None:
            raise ValueError("自由创作资源不存在或不属于当前模块")
        return int(version or 1)

    def set_head(
        self,
        *,
        project_id: int,
        stage: str,
        resource_ref: str,
        expected_revision: int,
        actor_user_id: str,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        number = int(episode_number or 0)
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            version = self._assert_head_resource(
                connection, project_id, stage, number, resource_ref
            )
            return self._set_head_conn(
                connection,
                project_id=project_id,
                stage=stage,
                episode_number=number,
                resource_ref=resource_ref,
                resource_version=version,
                expected_revision=expected_revision,
                actor_user_id=actor_user_id,
            )

    def _create_brief_conn(
        self,
        connection: Connection,
        *,
        project_id: int,
        content: dict[str, Any],
        source_refs: dict[str, Any],
        generation_meta: dict[str, Any],
        status: str,
        parent_id: int | None = None,
    ) -> tuple[int, int]:
        now = utc_now()
        candidate_group_id = uuid.uuid4().hex
        version = 1
        if parent_id is not None:
            parent = connection.execute(
                select(
                    creative_blueprints.c.candidate_group_id,
                    creative_blueprints.c.version,
                ).where(
                    creative_blueprints.c.id == int(parent_id),
                    creative_blueprints.c.project_id == int(project_id),
                    creative_blueprints.c.resource_scope == FREE_SCOPE,
                )
            ).mappings().first()
            if parent is None:
                raise ValueError("自由 Brief 父版本不存在")
            candidate_group_id = str(parent["candidate_group_id"])
            version = int(parent["version"] or 0) + 1
        resource_id = connection.execute(
            insert(creative_blueprints)
            .values(
                project_id=int(project_id),
                candidate_group_id=candidate_group_id,
                version=version,
                parent_version_id=parent_id,
                status=status,
                content_json=content,
                risk_score=0,
                risk_level="low",
                difference_note="",
                source_refs_json=source_refs,
                direction_type=FREE_SCOPE,
                generation_meta_json=generation_meta,
                resource_scope=FREE_SCOPE,
                created_at=now,
                updated_at=now,
            )
            .returning(creative_blueprints.c.id)
        ).scalar_one()
        return int(resource_id), version

    def create_brief(
        self,
        project_id: int,
        content: dict[str, Any],
        *,
        expected_head_revision: int,
        actor_user_id: str,
        source_refs: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            current_ref = connection.execute(
                select(creative_resource_heads.c.resource_ref).where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == FREE_SCOPE,
                    creative_resource_heads.c.stage == "brief",
                    creative_resource_heads.c.episode_number == 0,
                )
            ).scalar_one_or_none()
            parent_id = int(current_ref) if current_ref is not None else None
            resource_id, version = self._create_brief_conn(
                connection,
                project_id=project_id,
                content=dict(content or {}),
                source_refs=dict(source_refs or {}),
                generation_meta=dict(generation_meta or {}),
                status="draft",
                parent_id=parent_id,
            )
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage="brief",
                episode_number=0,
                resource_ref=str(resource_id),
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {"resource": self.repository.get_blueprint(resource_id), "head": head}

    def create_briefs(
        self,
        project_id: int,
        contents: list[dict[str, Any]],
        *,
        expected_head_revision: int,
        actor_user_id: str,
        source_refs: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save independent directions atomically and select the first as current."""
        if not 1 <= len(contents) <= 5:
            raise ValueError("单次自由 Brief 生成数量必须在 1-5 之间")
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            resources = [
                self._create_brief_conn(
                    connection,
                    project_id=project_id,
                    content=dict(content),
                    source_refs=dict(source_refs or {}),
                    generation_meta=dict(generation_meta or {}),
                    status="draft",
                )
                for content in contents
            ]
            resource_id, version = resources[0]
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage="brief",
                episode_number=0,
                resource_ref=str(resource_id),
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {
            "resources": [self.repository.get_blueprint(item_id) for item_id, _ in resources],
            "head": head,
        }

    def create_characters(
        self,
        project_id: int,
        characters: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        *,
        expected_head_revision: int,
        actor_user_id: str,
        source_refs: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not characters:
            characters = [{"name": "", "role": ""}]
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            head_ref = connection.execute(
                select(creative_resource_heads.c.resource_ref).where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == FREE_SCOPE,
                    creative_resource_heads.c.stage == "characters",
                    creative_resource_heads.c.episode_number == 0,
                )
            ).scalar_one_or_none()
            parent_batch_id = str(head_ref) if head_ref is not None else ""
            parent_characters = {
                str(row["character_key"]): int(row["id"])
                for row in connection.execute(
                    select(
                        creative_characters.c.id,
                        creative_characters.c.character_key,
                    ).where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == parent_batch_id,
                        creative_characters.c.resource_scope == FREE_SCOPE,
                    )
                ).mappings().all()
            } if parent_batch_id else {}
            parent_relationships = {
                str(row["relationship_key"]): int(row["id"])
                for row in connection.execute(
                    select(
                        creative_relationships.c.id,
                        creative_relationships.c.relationship_key,
                    ).where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == parent_batch_id,
                        creative_relationships.c.resource_scope == FREE_SCOPE,
                    )
                ).mappings().all()
            } if parent_batch_id else {}
            version = int(connection.execute(
                select(
                    func.coalesce(func.max(creative_characters.c.version), 0) + 1
                ).where(creative_characters.c.project_id == int(project_id))
            ).scalar_one())
            batch_id = uuid.uuid4().hex
            now = utc_now()
            refs_json = dict(source_refs or {})
            meta_json = dict(generation_meta or {})
            for index, item in enumerate(characters, start=1):
                character_key = str(
                    item.get("character_key")
                    or f"character-{index}-{uuid.uuid4().hex}"
                )
                connection.execute(
                    insert(creative_characters).values(
                        project_id=int(project_id),
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=parent_characters.get(character_key),
                        character_key=character_key,
                        status="draft",
                        content_json=dict(item.get("content") or item),
                        source_refs_json=refs_json,
                        generation_meta_json=meta_json,
                        resource_scope=FREE_SCOPE,
                        created_at=now,
                        updated_at=now,
                    )
                )
            for index, item in enumerate(relationships, start=1):
                relationship_key = str(
                    item.get("relationship_key")
                    or f"relationship-{index}-{uuid.uuid4().hex}"
                )
                connection.execute(
                    insert(creative_relationships).values(
                        project_id=int(project_id),
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=parent_relationships.get(relationship_key),
                        relationship_key=relationship_key,
                        status="draft",
                        content_json=dict(item.get("content") or item),
                        source_refs_json=refs_json,
                        generation_meta_json=meta_json,
                        resource_scope=FREE_SCOPE,
                        created_at=now,
                        updated_at=now,
                    )
                )
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage="characters",
                episode_number=0,
                resource_ref=batch_id,
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {"resource": self.repository.get_character_batch(project_id, batch_id), "head": head}

    @staticmethod
    def _blank_outline_episode(project: dict[str, Any], number: int) -> dict[str, Any]:
        return {
            "episode": int(number),
            "core_event": "",
            "conflict": "",
            "character_progress": "",
            "highlight": "",
            "reversal": "",
            "cliffhanger": "",
            "estimated_seconds": int(project.get("episode_duration_seconds") or 120),
        }

    def create_outline(
        self,
        project_id: int,
        episodes: list[dict[str, Any]],
        *,
        series_arc: str = "",
        series_overview: dict[str, Any] | None = None,
        expected_head_revision: int,
        actor_user_id: str,
        source_refs: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            head_ref = connection.execute(
                select(creative_resource_heads.c.resource_ref).where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == FREE_SCOPE,
                    creative_resource_heads.c.stage == "outline",
                    creative_resource_heads.c.episode_number == 0,
                )
            ).scalar_one_or_none()
            parent_batch_id = str(head_ref) if head_ref is not None else ""
            parent_episodes = {
                int(row["episode_number"]): int(row["id"])
                for row in connection.execute(
                    select(
                        creative_episodes.c.id,
                        creative_episodes.c.episode_number,
                    ).where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == parent_batch_id,
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                    )
                ).mappings().all()
            } if parent_batch_id else {}
            total = int(project["target_episode_count"])
            if not episodes:
                episodes = [self._blank_outline_episode(project, number) for number in range(1, total + 1)]
            numbers = sorted(int(item.get("episode") or 0) for item in episodes)
            if numbers != list(range(1, total + 1)):
                raise ValueError("自由模式全剧大纲必须覆盖项目全部集数")
            version = int(connection.execute(
                select(
                    func.coalesce(func.max(creative_episodes.c.version), 0) + 1
                ).where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == "outline",
                )
            ).scalar_one())
            batch_id = uuid.uuid4().hex
            now = utc_now()
            refs_json = dict(source_refs or {})
            meta_json = dict(generation_meta or {})
            for item in sorted(episodes, key=lambda value: int(value.get("episode") or 0)):
                number = int(item["episode"])
                content = dict(item)
                content["episode"] = number
                content["series_arc"] = series_arc
                if series_overview is not None:
                    content["series_overview"] = series_overview
                connection.execute(
                    insert(creative_episodes).values(
                        project_id=int(project_id),
                        stage="outline",
                        episode_number=number,
                        batch_id=batch_id,
                        version=version,
                        parent_version_id=parent_episodes.get(number),
                        status="draft",
                        content_json=content,
                        upstream_refs_json=refs_json,
                        generation_meta_json=meta_json,
                        resource_scope=FREE_SCOPE,
                        created_at=now,
                        updated_at=now,
                    )
                )
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage="outline",
                episode_number=0,
                resource_ref=batch_id,
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {"resource": self.repository.get_outline_batch(project_id, batch_id), "head": head}

    def create_episode_resource(
        self,
        project_id: int,
        stage: str,
        episode_number: int,
        content: dict[str, Any],
        *,
        expected_head_revision: int,
        actor_user_id: str,
        source_refs: dict[str, Any] | None = None,
        generation_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        if stage not in {"beats", "script"}:
            raise ValueError("episode resource stage must be beats or script")
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            number = int(episode_number)
            if not 1 <= number <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            head_ref = connection.execute(
                select(creative_resource_heads.c.resource_ref).where(
                    creative_resource_heads.c.project_id == int(project_id),
                    creative_resource_heads.c.resource_scope == FREE_SCOPE,
                    creative_resource_heads.c.stage == stage,
                    creative_resource_heads.c.episode_number == number,
                )
            ).scalar_one_or_none()
            parent_id = int(head_ref) if head_ref is not None else None
            version = int(connection.execute(
                select(
                    func.coalesce(func.max(creative_episodes.c.version), 0) + 1
                ).where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == stage,
                    creative_episodes.c.episode_number == number,
                )
            ).scalar_one())
            now = utc_now()
            batch_id = uuid.uuid4().hex
            resource_id = int(connection.execute(
                insert(creative_episodes)
                .values(
                    project_id=int(project_id),
                    stage=stage,
                    episode_number=number,
                    batch_id=batch_id,
                    version=version,
                    parent_version_id=parent_id,
                    status="draft",
                    content_json=dict(content or {}),
                    upstream_refs_json=dict(source_refs or {}),
                    generation_meta_json=dict(generation_meta or {}),
                    resource_scope=FREE_SCOPE,
                    created_at=now,
                    updated_at=now,
                )
                .returning(creative_episodes.c.id)
            ).scalar_one())
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage=stage,
                episode_number=number,
                resource_ref=str(resource_id),
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {"resource": self.repository.get_episode(resource_id), "head": head}

    def update_resource(
        self,
        *,
        project_id: int,
        stage: str,
        resource_ref: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            now = utc_now()
            if stage == "brief":
                cursor = connection.execute(
                    update(creative_blueprints)
                    .where(
                        creative_blueprints.c.id == int(resource_ref),
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == FREE_SCOPE,
                        creative_blueprints.c.status == "draft",
                    )
                    .values(content_json=dict(content), updated_at=now)
                )
                result_kind = "brief"
            elif stage in {"beats", "script"}:
                cursor = connection.execute(
                    update(creative_episodes)
                    .where(
                        creative_episodes.c.id == int(resource_ref),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == stage,
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                        creative_episodes.c.status == "draft",
                    )
                    .values(content_json=dict(content), updated_at=now)
                )
                result_kind = "episode"
            else:
                raise ValueError("人物与全剧大纲请使用批次保存接口")
            if cursor.rowcount != 1:
                raise ValueError("自由创作草稿不存在或已锁定")
        if result_kind == "brief":
            return self.repository.get_blueprint(int(resource_ref)) or {}
        return self.repository.get_episode(int(resource_ref)) or {}

    def update_item(
        self,
        *,
        project_id: int,
        item_type: str,
        item_id: int,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        tables: dict[str, tuple[Any, str | None]] = {
            "brief": (creative_blueprints, "brief"),
            "character": (creative_characters, "characters"),
            "relationship": (creative_relationships, "characters"),
            "episode": (creative_episodes, None),
        }
        if item_type not in tables:
            raise ValueError("unsupported free creative item type")
        table, expected_stage = tables[item_type]
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            row = connection.execute(
                select(table).where(
                    table.c.id == int(item_id),
                    table.c.project_id == int(project_id),
                    table.c.resource_scope == FREE_SCOPE,
                )
            ).mappings().first()
            if row is None:
                raise ValueError("自由创作资源不存在")
            stage = expected_stage if expected_stage is not None else str(row["stage"])
            if stage not in FREE_STAGES:
                raise ValueError("自由创作资源类型错误")
            if row["status"] != "draft":
                raise ValueError("已锁定的自由创作资源不能修改")
            connection.execute(
                update(table)
                .where(table.c.id == int(item_id))
                .values(content_json=dict(content), updated_at=utc_now())
            )
        if item_type == "brief":
            return self.repository.get_blueprint(item_id) or {}
        if item_type == "character":
            return self.repository.get_character(item_id) or {}
        if item_type == "relationship":
            return self.repository.get_relationship(item_id) or {}
        return self.repository.get_episode(item_id) or {}

    def apply_outline_field_rewrite(
        self,
        *,
        project_id: int,
        episode_id: int,
        field: str,
        candidate_value: str,
        source_fingerprint: str,
        expected_head_revision: int,
        actor_user_id: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        context = self.repository.get_outline_field_rewrite_context(episode_id)
        project = context["project"]
        episode = context["episode"]
        if (
            int(project["id"]) != int(project_id)
            or str(project.get("workflow_mode") or "sequential") != FREE_SCOPE
        ):
            raise ValueError("该操作仅支持当前自由创作项目")
        candidate = str(candidate_value or "").strip()
        if not candidate:
            raise ValueError("重写内容不能为空")
        if outline_field_source_fingerprint(episode, field) != source_fingerprint:
            raise ValueError("该字段已被更新，请重新生成")

        if episode["status"] == "draft":
            saved = self.repository.apply_outline_field_rewrite(
                episode_id,
                field,
                candidate,
                source_fingerprint,
                expected_head_revision=expected_head_revision,
            )
            return {
                "episode": saved,
                "batch_id": saved["batch_id"],
                "version": saved["version"],
                "created_new_version": False,
                "head": self.get_head(project_id, "outline"),
            }

        source_batch = self.repository.get_outline_batch(
            project_id, str(episode["batch_id"])
        )
        source_episodes = sorted(
            source_batch["episodes"], key=lambda item: int(item["episode_number"])
        )
        episodes = [dict(item["content"]) for item in source_episodes]
        target_number = int(episode["episode_number"])
        for item in episodes:
            if int(item.get("episode") or 0) == target_number:
                item[field] = candidate
                break
        created = self.create_outline(
            project_id,
            episodes,
            series_arc=str(
                (source_episodes[0].get("content") or {}).get("series_arc") or ""
            ),
            series_overview=source_batch.get("series_overview"),
            expected_head_revision=expected_head_revision,
            actor_user_id=actor_user_id,
            source_refs=dict(episode.get("upstream_refs") or {}),
            generation_meta={
                "source": "outline_field_rewrite",
                "source_batch_id": str(episode["batch_id"]),
                "source_episode_id": int(episode["id"]),
                "field": field,
                "operation_id": str(operation_id or ""),
            },
        )
        derived = created["resource"]
        saved = next(
            item
            for item in derived["episodes"]
            if int(item["episode_number"]) == target_number
        )
        return {
            "episode": saved,
            "batch_id": derived["batch_id"],
            "version": saved["version"],
            "created_new_version": True,
            "head": created["head"],
        }

    def lock_resource(
        self,
        *,
        project_id: int,
        stage: str,
        resource_ref: str,
        expected_head_revision: int,
        actor_user_id: str,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        number = int(episode_number or 0)
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            version = self._assert_head_resource(connection, project_id, stage, number, resource_ref)
            now = utc_now()
            if stage == "brief":
                cursor = connection.execute(
                    update(creative_blueprints)
                    .where(
                        creative_blueprints.c.id == int(resource_ref),
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == FREE_SCOPE,
                        creative_blueprints.c.status.in_(("draft", "brief_locked")),
                    )
                    .values(status="brief_locked", locked_at=now, updated_at=now)
                )
            elif stage == "characters":
                count = connection.execute(
                    select(func.count())
                    .select_from(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == str(resource_ref),
                        creative_characters.c.resource_scope == FREE_SCOPE,
                    )
                ).scalar_one()
                if int(count) < 1:
                    raise ValueError("人物关系版本至少需要一个人物")
                cursor = connection.execute(
                    update(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == str(resource_ref),
                        creative_characters.c.resource_scope == FREE_SCOPE,
                    )
                    .values(status="locked", locked_at=now, updated_at=now)
                )
                connection.execute(
                    update(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == str(resource_ref),
                        creative_relationships.c.resource_scope == FREE_SCOPE,
                    )
                    .values(status="locked", locked_at=now, updated_at=now)
                )
            elif stage == "outline":
                count = connection.execute(
                    select(func.count())
                    .select_from(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == str(resource_ref),
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                    )
                ).scalar_one()
                if int(count) != int(project["target_episode_count"]):
                    raise ValueError("分集大纲不完整，不能锁定")
                cursor = connection.execute(
                    update(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == str(resource_ref),
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                    )
                    .values(status="locked", locked_at=now, updated_at=now)
                )
            else:
                cursor = connection.execute(
                    update(creative_episodes)
                    .where(
                        creative_episodes.c.id == int(resource_ref),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == stage,
                        creative_episodes.c.episode_number == number,
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                        creative_episodes.c.status.in_(("draft", "locked")),
                    )
                    .values(status="locked", locked_at=now, updated_at=now)
                )
            if cursor.rowcount < 1:
                raise ValueError("自由创作资源不存在或无法锁定")
            head = self._set_head_conn(
                connection,
                project_id=project_id,
                stage=stage,
                episode_number=number,
                resource_ref=resource_ref,
                resource_version=version,
                expected_revision=expected_head_revision,
                actor_user_id=actor_user_id,
            )
        return {"head": head, "locked": True}

    def lock_episode_series(
        self,
        *,
        project_id: int,
        stage: str,
        expected_head_revisions: dict[int, int],
        actor_user_id: str,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        if stage not in {"beats", "script"}:
            raise ValueError("全集锁定仅支持 Beats 或场次剧本")
        label = "Beats" if stage == "beats" else "场次剧本"
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            total = int(project["target_episode_count"])
            required_episodes = set(range(1, total + 1))
            revisions = {
                int(number): int(revision)
                for number, revision in expected_head_revisions.items()
            }
            if set(revisions) != required_episodes:
                raise ValueError(f"必须提供项目全部集的{label} Head 版本")

            records: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
            for episode_number in range(1, total + 1):
                head_row = connection.execute(
                    select(creative_resource_heads).where(
                        creative_resource_heads.c.project_id == int(project_id),
                        creative_resource_heads.c.resource_scope == FREE_SCOPE,
                        creative_resource_heads.c.stage == stage,
                        creative_resource_heads.c.episode_number == episode_number,
                    )
                ).mappings().first()
                if head_row is None:
                    raise ValueError(f"第 {episode_number} 集尚未生成 {label}")
                head = self.repository.row_dict(head_row)
                if int(head["revision"]) != revisions[episode_number]:
                    raise ValueError(
                        f"第 {episode_number} 集{label} Head 已发生变化，请刷新后重试"
                    )
                resource_row = connection.execute(
                    select(creative_episodes).where(
                        creative_episodes.c.id == int(head["resource_ref"]),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                        creative_episodes.c.stage == stage,
                        creative_episodes.c.episode_number == episode_number,
                    )
                ).mappings().first()
                if resource_row is None:
                    raise ValueError(f"第 {episode_number} 集当前 {label} 不存在")
                resource = self.repository.row_dict(resource_row)
                if resource["status"] not in {"draft", "locked", "approved"}:
                    raise ValueError(f"第 {episode_number} 集当前 {label} 无法锁定")
                records.append((episode_number, head, resource))

            now = utc_now()
            locked_count = 0
            heads: list[dict[str, Any]] = []
            resource_ids: list[int] = []
            for episode_number, head, resource in records:
                resource_id = int(resource["id"])
                resource_ids.append(resource_id)
                if resource["status"] != "draft":
                    heads.append(head)
                    continue
                cursor = connection.execute(
                    update(creative_episodes)
                    .where(
                        creative_episodes.c.id == resource_id,
                        creative_episodes.c.status == "draft",
                    )
                    .values(status="locked", locked_at=now, updated_at=now)
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"第 {episode_number} 集{label}锁定失败")
                heads.append(
                    self._set_head_conn(
                        connection,
                        project_id=project_id,
                        stage=stage,
                        episode_number=episode_number,
                        resource_ref=str(resource_id),
                        resource_version=int(resource["version"]),
                        expected_revision=revisions[episode_number],
                        actor_user_id=actor_user_id,
                    )
                )
                locked_count += 1
        return {
            "stage": stage,
            "locked_count": locked_count,
            "resources": [
                self.repository.get_episode(resource_id) for resource_id in resource_ids
            ],
            "heads": heads,
        }

    def _latest_seed_conn(
        self, connection: Connection, project_id: int
    ) -> dict[str, Any]:
        row = connection.execute(
            select(creative_seeds)
            .where(creative_seeds.c.project_id == int(project_id))
            .order_by(creative_seeds.c.version.desc(), creative_seeds.c.id.desc())
            .limit(1)
        ).mappings().first()
        if row is None or len(str(row["seed_text"] or "").strip()) < 2:
            raise ValueError("自由创作项目缺少创作种子")
        return self.repository.row_dict(row)

    @staticmethod
    def _manifest_item(kind: str, ref: str, version: int, content: Any) -> dict[str, Any]:
        return {
            "kind": kind,
            "resource_ref": str(ref),
            "version": int(version),
            "resource_scope": FREE_SCOPE,
            "fingerprint": canonical_request_hash(content),
        }

    def create_generation_context(
        self,
        *,
        context_id: str,
        project_id: int,
        stage: str,
        episode_number: int | None,
        instruction: str,
        context_refs: dict[str, Any],
        actor_user_id: str,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        supplied = {key for key, value in context_refs.items() if value not in {None, ""}}
        invalid = supplied - ALLOWED_CONTEXT_REFS[stage]
        if invalid:
            raise ValueError(f"{stage} 不支持引用：{', '.join(sorted(invalid))}")
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            number = int(episode_number or 0)
            if stage in {"beats", "script"} and not 1 <= number <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            seed = self._latest_seed_conn(connection, project_id)
            snapshot: dict[str, Any] = {
                "project": {
                    key: project.get(key)
                    for key in (
                        "id", "name", "genre", "target_platform", "target_audience",
                        "target_episode_count", "episode_duration_seconds", "creation_mode",
                    )
                },
                "seed": {
                    "id": int(seed["id"]),
                    "version": int(seed["version"]),
                    "seed_text": str(seed["seed_text"]),
                    "constraints": self._loads(seed.get("constraints_json"), {}),
                },
                "instruction": str(instruction or "").strip(),
            }
            manifest: dict[str, Any] = {}
            brief_id = context_refs.get("brief_id")
            if brief_id:
                row = connection.execute(
                    select(creative_blueprints).where(
                        creative_blueprints.c.id == int(brief_id),
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == FREE_SCOPE,
                    )
                ).mappings().first()
                if row is None or row["status"] not in LOCKED_STATUSES:
                    raise ValueError("只能引用同项目已锁定的自由 Brief")
                row = self.repository.row_dict(row)
                content = self._loads(row["content_json"], {})
                snapshot["brief"] = content
                manifest["brief"] = self._manifest_item("brief", str(row["id"]), int(row["version"]), content)
            character_batch_id = context_refs.get("character_batch_id")
            if character_batch_id:
                character_rows = self.repository.rows_dict(connection.execute(
                    select(creative_characters).where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == str(character_batch_id),
                        creative_characters.c.resource_scope == FREE_SCOPE,
                    ).order_by(creative_characters.c.id)
                ).mappings().all())
                if not character_rows or any(row["status"] not in LOCKED_STATUSES for row in character_rows):
                    raise ValueError("只能引用同项目已锁定的自由人物版本")
                relationship_rows = self.repository.rows_dict(connection.execute(
                    select(creative_relationships).where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == str(character_batch_id),
                        creative_relationships.c.resource_scope == FREE_SCOPE,
                    ).order_by(creative_relationships.c.id)
                ).mappings().all())
                if any(row["status"] not in LOCKED_STATUSES for row in relationship_rows):
                    raise ValueError("只能引用同项目已锁定的自由人物版本")
                characters = [self._loads(row["content_json"], {}) for row in character_rows]
                relationships = [self._loads(row["content_json"], {}) for row in relationship_rows]
                bundle = {"characters": characters, "relationships": relationships}
                snapshot.update(bundle)
                manifest["characters"] = self._manifest_item(
                    "characters", str(character_batch_id), int(character_rows[0]["version"]), bundle
                )
            outline_batch_id = context_refs.get("outline_batch_id")
            if outline_batch_id:
                rows = self.repository.rows_dict(connection.execute(
                    select(creative_episodes).where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == str(outline_batch_id),
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                    ).order_by(creative_episodes.c.episode_number)
                ).mappings().all())
                if not rows or any(row["status"] not in LOCKED_STATUSES for row in rows):
                    raise ValueError("只能引用同项目已锁定的自由大纲")
                outlines = [self._loads(row["content_json"], {}) for row in rows]
                snapshot["outlines"] = outlines
                manifest["outline"] = self._manifest_item(
                    "outline", str(outline_batch_id), int(rows[0]["version"]), outlines
                )
            beats_id = context_refs.get("beats_id")
            if beats_id:
                row = connection.execute(
                    select(creative_episodes).where(
                        creative_episodes.c.id == int(beats_id),
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "beats",
                        creative_episodes.c.resource_scope == FREE_SCOPE,
                    )
                ).mappings().first()
                if (
                    row is None
                    or row["status"] not in LOCKED_STATUSES
                    or int(row["episode_number"]) != number
                ):
                    raise ValueError("只能引用当前集已锁定的自由 Beats")
                row = self.repository.row_dict(row)
                content = self._loads(row["content_json"], {})
                snapshot["beats"] = content
                manifest["beats"] = self._manifest_item(
                    "beats", str(row["id"]), int(row["version"]), content
                )
            context_hash = canonical_request_hash(snapshot)
            existing_row = connection.execute(
                select(creative_generation_contexts).where(
                    creative_generation_contexts.c.id == str(context_id)
                )
            ).mappings().first()
            if existing_row is not None:
                existing = self.repository.row_dict(existing_row)
                existing_episode = (
                    int(existing["episode_number"])
                    if existing["episode_number"] is not None
                    else None
                )
                requested_episode = number if stage in {"beats", "script"} else None
                if (
                    int(existing["project_id"]) != int(project_id)
                    or str(existing["target_stage"]) != stage
                    or existing_episode != requested_episode
                    or str(existing["context_hash"]) != context_hash
                ):
                    raise ValueError("generation operation id was reused with different context")
                return self._context_row(existing)
            now = utc_now()
            connection.execute(
                insert(creative_generation_contexts).values(
                    id=str(context_id),
                    project_id=int(project_id),
                    target_stage=stage,
                    episode_number=number if stage in {"beats", "script"} else None,
                    seed_version_id=int(seed["id"]),
                    instruction=str(instruction or "").strip(),
                    context_manifest_json=manifest,
                    context_snapshot_json=snapshot,
                    context_hash=context_hash,
                    prompt_schema_version=PROMPT_TEMPLATE_VERSION,
                    created_by=actor_user_id,
                    created_at=now,
                )
            )
            row = connection.execute(
                select(creative_generation_contexts).where(
                    creative_generation_contexts.c.id == str(context_id)
                )
            ).mappings().first()
        return self._context_row(self.repository.row_dict(row))

    @classmethod
    def _context_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["context_manifest"] = cls._loads(row.pop("context_manifest_json"), {})
        row["context_snapshot"] = cls._loads(row.pop("context_snapshot_json"), {})
        return row

    def get_generation_context(self, context_id: str) -> dict[str, Any]:
        with self.repository.connection_scope() as connection:
            row = connection.execute(
                select(creative_generation_contexts).where(
                    creative_generation_contexts.c.id == str(context_id)
                )
            ).mappings().first()
        if row is None:
            raise ValueError("自由创作生成上下文不存在")
        return self._context_row(self.repository.row_dict(row))

    def context_options(self, project_id: int, episode_number: int | None = None) -> dict[str, Any]:
        self.assert_free_project(project_id)
        project = self.repository.get_project(project_id) or {}
        locked = LOCKED_STATUSES
        briefs = [item for item in project.get("blueprints", []) if item.get("status") in locked]
        character_batches: dict[str, dict[str, Any]] = {}
        for item in project.get("characters", []):
            if item.get("status") not in locked:
                continue
            batch = character_batches.setdefault(
                str(item["batch_id"]),
                {"batch_id": str(item["batch_id"]), "version": int(item.get("version") or 1), "characters": []},
            )
            batch["characters"].append(item)
        outline_batches: dict[str, dict[str, Any]] = {}
        beats: list[dict[str, Any]] = []
        for item in project.get("episodes", []):
            if item.get("status") not in locked:
                continue
            if item.get("stage") == "outline":
                batch = outline_batches.setdefault(
                    str(item["batch_id"]),
                    {"batch_id": str(item["batch_id"]), "version": int(item.get("version") or 1), "episodes": []},
                )
                batch["episodes"].append(item)
            elif item.get("stage") == "beats" and (
                episode_number is None or int(item["episode_number"]) == int(episode_number)
            ):
                beats.append(item)
        return {
            "briefs": briefs,
            "character_batches": list(character_batches.values()),
            "outline_batches": list(outline_batches.values()),
            "beats": beats,
            "heads": self.list_heads(project_id),
        }

    def _automatic_seed_text_conn(
        self, connection: Connection, project: dict[str, Any], seed_text: str = ""
    ) -> str:
        supplied = str(seed_text or "").strip()
        if len(supplied) >= 2:
            return supplied
        topic_id = project.get("topic_id")
        breakdown_id = project.get("breakdown_id")
        if topic_id is not None and breakdown_id is not None:
            # Read inside the caller's transaction so the seed text is derived
            # from the same snapshot as the write that stores it.
            topic = connection.execute(
                select(creative_topics).where(creative_topics.c.id == int(topic_id))
            ).mappings().first()
            breakdown = connection.execute(
                select(creative_breakdowns).where(
                    creative_breakdowns.c.id == int(breakdown_id)
                )
            ).mappings().first()
            if topic is not None and breakdown is not None:
                resolved = self.repository._topic_based_seed_text(topic, breakdown)
                if len(resolved.strip()) >= 2:
                    return resolved
        return f"项目：{str(project.get('name') or '自由创作').strip()}"[:500]

    def ensure_seed(self, project_id: int, seed_text: str = "") -> dict[str, Any]:
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            seed_id = connection.execute(
                select(creative_seeds.c.id)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version.desc(), creative_seeds.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if seed_id is None:
                resolved_seed_text = self._automatic_seed_text_conn(
                    connection, project, seed_text
                )
                now = utc_now()
                seed_id = int(connection.execute(
                    insert(creative_seeds)
                    .values(
                        project_id=int(project_id),
                        version=1,
                        status="frozen",
                        seed_text=resolved_seed_text,
                        constraints_json={},
                        reference_refs_json=[],
                        created_at=now,
                        updated_at=now,
                        locked_at=now,
                    )
                    .returning(creative_seeds.c.id)
                ).scalar_one())
            else:
                seed_id = int(seed_id)
        return self.repository.get_seed(seed_id) or {}

    def has_creative_resources(self, project_id: int) -> bool:
        with self.repository.connection_scope() as connection:
            total = 0
            for table in (
                creative_blueprints,
                creative_characters,
                creative_relationships,
                creative_episodes,
            ):
                total += int(connection.execute(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.project_id == int(project_id))
                ).scalar_one())
        return total > 0

    def switch_empty_mode(
        self,
        *,
        project_id: int,
        workflow_mode: str,
        expected_mode_revision: int,
        actor_user_id: str,
        seed_text: str = "",
    ) -> dict[str, Any]:
        if workflow_mode not in {SEQUENTIAL_SCOPE, FREE_SCOPE}:
            raise ValueError("invalid creative workflow mode")
        with self.repository.transaction_scope() as connection:
            project = self._raw_project(connection, project_id)
            if int(project.get("mode_revision") or 0) != int(expected_mode_revision):
                raise ValueError("creative workflow mode changed")
            current = str(project.get("workflow_mode") or SEQUENTIAL_SCOPE)
            if current == workflow_mode:
                return self.repository.get_project(project_id) or project
            for table in (
                creative_blueprints,
                creative_characters,
                creative_relationships,
                creative_episodes,
            ):
                count = int(connection.execute(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.project_id == int(project_id))
                ).scalar_one())
                if count:
                    raise ValueError("项目已有创作资源，只能通过迁移进入自由模式")
            now = utc_now()
            if workflow_mode == FREE_SCOPE:
                seed = connection.execute(
                    select(creative_seeds.c.id)
                    .where(creative_seeds.c.project_id == int(project_id))
                    .order_by(creative_seeds.c.version.desc(), creative_seeds.c.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if seed is None:
                    resolved_seed_text = self._automatic_seed_text_conn(
                        connection, project, seed_text
                    )
                    connection.execute(
                        insert(creative_seeds).values(
                            project_id=int(project_id),
                            version=1,
                            status="frozen",
                            seed_text=resolved_seed_text,
                            constraints_json={},
                            reference_refs_json=[],
                            created_at=now,
                            updated_at=now,
                            locked_at=now,
                        )
                    )
            cursor = connection.execute(
                update(creative_projects)
                .where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.mode_revision == int(expected_mode_revision),
                )
                .values(
                    workflow_mode=workflow_mode,
                    mode_revision=creative_projects.c.mode_revision + 1,
                    mode_changed_at=now,
                    mode_changed_by=actor_user_id,
                    status="free_active" if workflow_mode == FREE_SCOPE else "created",
                    updated_at=now,
                )
            )
            if cursor.rowcount != 1:
                raise ValueError("creative workflow mode changed")
        return self.repository.get_project(project_id) or {}

    def migration_preview(self, project_id: int) -> dict[str, Any]:
        with self.repository.connection_scope() as connection:
            project = self._raw_project(connection, project_id)
            active_generation_count = int(connection.execute(
                select(func.count())
                .select_from(creative_generation_operations)
                .where(
                    creative_generation_operations.c.project_id == int(project_id),
                    creative_generation_operations.c.status == "active",
                )
            ).scalar_one())
            active_review_count = int(connection.execute(
                select(func.count())
                .select_from(script_reviews)
                .where(
                    script_reviews.c.project_id == int(project_id),
                    script_reviews.c.status.not_in(
                        ("completed", "confirmed", "cancelled", "failed")
                    ),
                )
            ).scalar_one())
            active_optimization_count = int(connection.execute(
                select(func.count())
                .select_from(script_optimizations)
                .where(
                    script_optimizations.c.project_id == int(project_id),
                    script_optimizations.c.status.not_in(
                        ("completed", "cancelled", "failed")
                    ),
                )
            ).scalar_one())
            resource_counts = {
                "brief": int(connection.execute(
                    select(func.count())
                    .select_from(creative_blueprints)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == "sequential",
                    )
                ).scalar_one()),
                "characters": int(connection.execute(
                    select(func.count(func.distinct(creative_characters.c.batch_id)))
                    .select_from(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.resource_scope == "sequential",
                    )
                ).scalar_one()),
                "outline": int(connection.execute(
                    select(func.count(func.distinct(creative_episodes.c.batch_id)))
                    .select_from(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.resource_scope == "sequential",
                    )
                ).scalar_one()),
                "beats": int(connection.execute(
                    select(func.count())
                    .select_from(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "beats",
                        creative_episodes.c.resource_scope == "sequential",
                    )
                ).scalar_one()),
                "script": int(connection.execute(
                    select(func.count())
                    .select_from(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "script",
                        creative_episodes.c.resource_scope == "sequential",
                    )
                ).scalar_one()),
            }
            seed = connection.execute(
                select(creative_seeds.c.id)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version.desc())
                .limit(1)
            ).scalar_one_or_none()
        blockers: list[str] = []
        if project.get("workflow_mode", "sequential") != "sequential":
            blockers.append("项目已经是自由创作模式")
        if active_generation_count:
            blockers.append("项目存在运行中的创作生成")
        if active_review_count:
            blockers.append("项目存在进行中的剧本审核")
        if active_optimization_count:
            blockers.append("项目存在进行中的剧本优化")
        return {
            "project_id": int(project_id),
            "workflow_mode": project.get("workflow_mode", "sequential"),
            "mode_revision": int(project.get("mode_revision") or 0),
            "resource_counts": resource_counts,
            "has_seed": seed is not None,
            "blockers": blockers,
            "can_migrate": not blockers,
        }

    @staticmethod
    def _latest_group(rows: list[Any], key: str) -> list[Any]:
        if not rows:
            return []
        latest = max(
            rows,
            key=lambda row: (
                int(row["version"] or 0),
                float(row["updated_at"] or 0),
                int(row["id"] or 0),
            ),
        )
        value = latest[key]
        return [row for row in rows if row[key] == value]

    def _completed_migration_result(
        self, row: Any, operation_id: str
    ) -> dict[str, Any]:
        mapping = self._loads(row["mapping_json"], {})
        project_mapping = mapping.get("project") or {}
        target_project_id = int(
            project_mapping.get("target") or row["project_id"]
        )
        return {
            "project": self.repository.get_project(target_project_id) or {},
            "mapping": mapping,
            "operation_id": operation_id,
            "created": False,
        }

    def _create_free_project_copy_conn(
        self,
        connection: Connection,
        *,
        source_project: dict[str, Any],
        operation_id: str,
        actor_user_id: str,
        now: Any,
    ) -> int:
        project_id = connection.execute(
            insert(creative_projects)
            .values(
                topic_id=source_project.get("topic_id"),
                breakdown_id=source_project.get("breakdown_id"),
                name=f"{str(source_project.get('name') or '未命名项目').strip()}（自由副本）",
                genre=str(source_project.get("genre") or ""),
                target_platform=str(source_project.get("target_platform") or ""),
                target_audience=str(source_project.get("target_audience") or ""),
                target_episode_count=int(source_project.get("target_episode_count") or 20),
                episode_duration_seconds=int(source_project.get("episode_duration_seconds") or 120),
                status="free_active",
                locked_brief_id=None,
                character_batch_id=None,
                outline_batch_id=None,
                workflow_task_id=None,
                creation_workflow_task_id=None,
                creation_request_id=f"free-migration:{operation_id}",
                created_by=actor_user_id,
                deleted=False,
                creation_mode=str(source_project.get("creation_mode") or "imitation"),
                bootstrap_status="ready",
                revision=0,
                workflow_mode=FREE_SCOPE,
                mode_revision=0,
                mode_changed_at=now,
                mode_changed_by=actor_user_id,
                created_at=now,
                updated_at=now,
            )
            .returning(creative_projects.c.id)
        ).scalar_one()
        return int(project_id)

    def migrate_to_free(
        self,
        *,
        project_id: int,
        expected_mode_revision: int,
        operation_id: str,
        actor_user_id: str,
        seed_text: str = "",
    ) -> dict[str, Any]:
        with self.repository.connection_scope() as connection:
            prior_row = connection.execute(
                select(creative_mode_migrations).where(
                    creative_mode_migrations.c.operation_id == str(operation_id)
                )
            ).mappings().first()
        prior = self.repository.row_dict(prior_row) if prior_row is not None else None
        if prior is not None:
            if int(prior["project_id"]) != int(project_id):
                raise ValueError("migration operation id belongs to another project")
            if prior["status"] != "completed":
                raise ValueError("creative mode migration is not complete")
            return self._completed_migration_result(prior, operation_id)
        preview = self.migration_preview(project_id)
        if preview["blockers"]:
            raise ValueError("；".join(preview["blockers"]))
        with self.repository.transaction_scope() as connection:
            existing_row = connection.execute(
                select(creative_mode_migrations).where(
                    creative_mode_migrations.c.operation_id == str(operation_id)
                )
            ).mappings().first()
            if existing_row is not None:
                existing = self.repository.row_dict(existing_row)
                if int(existing["project_id"]) != int(project_id):
                    raise ValueError("migration operation id belongs to another project")
                if existing["status"] != "completed":
                    raise ValueError("creative mode migration is not complete")
                return self._completed_migration_result(existing, operation_id)
            project = self._raw_project(connection, project_id)
            if project.get("workflow_mode", "sequential") != "sequential":
                raise ValueError("项目已经是自由创作模式")
            if int(project.get("mode_revision") or 0) != int(expected_mode_revision):
                raise ValueError("creative workflow mode changed")
            seed_row = connection.execute(
                select(creative_seeds)
                .where(creative_seeds.c.project_id == int(project_id))
                .order_by(creative_seeds.c.version.desc())
                .limit(1)
            ).mappings().first()
            seed = self.repository.row_dict(seed_row) if seed_row is not None else None
            now = utc_now()
            connection.execute(
                insert(creative_mode_migrations).values(
                    operation_id=str(operation_id),
                    project_id=int(project_id),
                    from_mode="sequential",
                    to_mode="free",
                    source_mode_revision=int(expected_mode_revision),
                    status="pending",
                    requested_by=actor_user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            target_project_id = self._create_free_project_copy_conn(
                connection,
                source_project=project,
                operation_id=operation_id,
                actor_user_id=actor_user_id,
                now=now,
            )
            resolved_seed_text = (
                str(seed["seed_text"] or "").strip()
                if seed is not None
                else self._automatic_seed_text_conn(connection, project, seed_text)
            )
            connection.execute(
                insert(creative_seeds).values(
                    project_id=target_project_id,
                    version=1,
                    status="frozen",
                    seed_text=resolved_seed_text,
                    constraints_json=(
                        seed["constraints_json"] if seed is not None else {}
                    ),
                    reference_refs_json=(
                        seed["reference_refs_json"] if seed is not None else []
                    ),
                    created_at=now,
                    updated_at=now,
                    locked_at=now,
                )
            )
            mapping: dict[str, Any] = {
                "project": {
                    "source": int(project_id),
                    "target": target_project_id,
                },
                "brief": None,
                "characters": None,
                "outline": None,
                "beats": {},
                "script": {},
            }

            brief_row = None
            if project.get("locked_brief_id"):
                brief_row = connection.execute(
                    select(creative_blueprints).where(
                        creative_blueprints.c.id == int(project["locked_brief_id"]),
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == "sequential",
                    )
                ).mappings().first()
                if brief_row is not None:
                    brief_row = self.repository.row_dict(brief_row)
            if brief_row is None:
                latest_brief = connection.execute(
                    select(creative_blueprints)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == "sequential",
                    )
                    .order_by(
                        creative_blueprints.c.version.desc(),
                        creative_blueprints.c.id.desc(),
                    )
                    .limit(1)
                ).mappings().first()
                brief_row = (
                    self.repository.row_dict(latest_brief)
                    if latest_brief is not None
                    else None
                )
            if brief_row is not None:
                content = self._loads(brief_row["content_json"], {})
                new_id, version = self._create_brief_conn(
                    connection,
                    project_id=target_project_id,
                    content=content,
                    source_refs={
                        "migration_source": self._manifest_item(
                            "brief", str(brief_row["id"]), int(brief_row["version"]), content
                        )
                    },
                    generation_meta={"origin": "sequential_migration", "operation_id": operation_id},
                    status=str(brief_row["status"]),
                )
                self._set_head_conn(
                    connection, project_id=target_project_id, stage="brief", episode_number=0,
                    resource_ref=str(new_id), resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["brief"] = {"source": int(brief_row["id"]), "target": new_id}

            character_rows = self.repository.rows_dict(connection.execute(
                select(creative_characters)
                .where(
                    creative_characters.c.project_id == int(project_id),
                    creative_characters.c.resource_scope == "sequential",
                )
                .order_by(creative_characters.c.version, creative_characters.c.id)
            ).mappings().all())
            if project.get("character_batch_id"):
                selected_characters = [
                    row for row in character_rows
                    if str(row["batch_id"]) == str(project["character_batch_id"])
                ]
            else:
                selected_characters = self._latest_group(character_rows, "batch_id")
            if selected_characters:
                source_batch = str(selected_characters[0]["batch_id"])
                new_batch = uuid.uuid4().hex
                version = int(connection.execute(
                    select(
                        func.coalesce(func.max(creative_characters.c.version), 0) + 1
                    ).where(creative_characters.c.project_id == target_project_id)
                ).scalar_one())
                for row in selected_characters:
                    connection.execute(
                        insert(creative_characters).values(
                            project_id=target_project_id,
                            batch_id=new_batch,
                            version=version,
                            character_key=str(row["character_key"]),
                            status=str(row["status"]),
                            content_json=row["content_json"],
                            source_refs_json={"migration_source_id": int(row["id"])},
                            generation_meta_json={
                                "origin": "sequential_migration",
                                "operation_id": operation_id,
                            },
                            resource_scope=FREE_SCOPE,
                            locked_at=from_epoch_seconds(row["locked_at"]),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                relationship_rows = self.repository.rows_dict(connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == source_batch,
                        creative_relationships.c.resource_scope == "sequential",
                    )
                    .order_by(creative_relationships.c.id)
                ).mappings().all())
                for row in relationship_rows:
                    connection.execute(
                        insert(creative_relationships).values(
                            project_id=target_project_id,
                            batch_id=new_batch,
                            version=version,
                            relationship_key=str(row["relationship_key"]),
                            status=str(row["status"]),
                            content_json=row["content_json"],
                            source_refs_json={"migration_source_id": int(row["id"])},
                            generation_meta_json={
                                "origin": "sequential_migration",
                                "operation_id": operation_id,
                            },
                            resource_scope=FREE_SCOPE,
                            locked_at=from_epoch_seconds(row["locked_at"]),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                self._set_head_conn(
                    connection, project_id=target_project_id, stage="characters", episode_number=0,
                    resource_ref=new_batch, resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["characters"] = {"source": source_batch, "target": new_batch}

            outline_rows = self.repository.rows_dict(connection.execute(
                select(creative_episodes)
                .where(
                    creative_episodes.c.project_id == int(project_id),
                    creative_episodes.c.stage == "outline",
                    creative_episodes.c.resource_scope == "sequential",
                )
                .order_by(
                    creative_episodes.c.version,
                    creative_episodes.c.episode_number,
                    creative_episodes.c.id,
                )
            ).mappings().all())
            if project.get("outline_batch_id"):
                selected_outlines = [
                    row for row in outline_rows
                    if str(row["batch_id"]) == str(project["outline_batch_id"])
                ]
            else:
                selected_outlines = self._latest_group(outline_rows, "batch_id")
            if selected_outlines:
                source_batch = str(selected_outlines[0]["batch_id"])
                new_batch = uuid.uuid4().hex
                version = int(connection.execute(
                    select(
                        func.coalesce(func.max(creative_episodes.c.version), 0) + 1
                    ).where(
                        creative_episodes.c.project_id == target_project_id,
                        creative_episodes.c.stage == "outline",
                    )
                ).scalar_one())
                for row in selected_outlines:
                    connection.execute(
                        insert(creative_episodes).values(
                            project_id=target_project_id,
                            stage="outline",
                            episode_number=int(row["episode_number"]),
                            batch_id=new_batch,
                            version=version,
                            status=str(row["status"]),
                            content_json=row["content_json"],
                            upstream_refs_json={
                                "migration_source_refs": self._loads(
                                    row["upstream_refs_json"], {}
                                )
                            },
                            generation_meta_json={
                                "origin": "sequential_migration",
                                "operation_id": operation_id,
                                "migration_source_id": int(row["id"]),
                            },
                            resource_scope=FREE_SCOPE,
                            locked_at=from_epoch_seconds(row["locked_at"]),
                            created_at=now,
                            updated_at=now,
                        )
                    )
                self._set_head_conn(
                    connection, project_id=target_project_id, stage="outline", episode_number=0,
                    resource_ref=new_batch, resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["outline"] = {"source": source_batch, "target": new_batch}

            for stage in ("beats", "script"):
                rows = self.repository.rows_dict(connection.execute(
                    select(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == stage,
                        creative_episodes.c.resource_scope == "sequential",
                    )
                    .order_by(
                        creative_episodes.c.episode_number,
                        creative_episodes.c.version.desc(),
                        creative_episodes.c.id.desc(),
                    )
                ).mappings().all())
                selected: dict[int, Any] = {}
                for row in rows:
                    selected.setdefault(int(row["episode_number"]), row)
                for number, row in selected.items():
                    version = int(connection.execute(
                        select(
                            func.coalesce(func.max(creative_episodes.c.version), 0) + 1
                        ).where(
                            creative_episodes.c.project_id == target_project_id,
                            creative_episodes.c.stage == stage,
                            creative_episodes.c.episode_number == number,
                        )
                    ).scalar_one())
                    new_id = int(connection.execute(
                        insert(creative_episodes)
                        .values(
                            project_id=target_project_id,
                            stage=stage,
                            episode_number=number,
                            batch_id=uuid.uuid4().hex,
                            version=version,
                            status=str(row["status"]),
                            content_json=row["content_json"],
                            upstream_refs_json={
                                "migration_source_refs": self._loads(
                                    row["upstream_refs_json"], {}
                                )
                            },
                            generation_meta_json={
                                "origin": "sequential_migration",
                                "operation_id": operation_id,
                                "migration_source_id": int(row["id"]),
                            },
                            resource_scope=FREE_SCOPE,
                            locked_at=from_epoch_seconds(row["locked_at"]),
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(creative_episodes.c.id)
                    ).scalar_one())
                    self._set_head_conn(
                        connection, project_id=target_project_id, stage=stage, episode_number=number,
                        resource_ref=str(new_id), resource_version=version,
                        expected_revision=0, actor_user_id=actor_user_id,
                    )
                    mapping[stage][str(number)] = {"source": int(row["id"]), "target": new_id}

            connection.execute(
                update(creative_mode_migrations)
                .where(creative_mode_migrations.c.operation_id == str(operation_id))
                .values(status="completed", mapping_json=mapping, updated_at=now)
            )
        return {
            "project": self.repository.get_project(target_project_id) or {},
            "mapping": mapping,
            "operation_id": operation_id,
            "created": True,
        }

    def list_legacy_resources(self, project_id: int) -> dict[str, Any]:
        self.assert_free_project(project_id)
        with self.repository.connection_scope() as connection:
            return {
                "blueprints": [
                    self.repository._blueprint(row) for row in connection.execute(
                        select(creative_blueprints)
                        .where(
                            creative_blueprints.c.project_id == int(project_id),
                            creative_blueprints.c.resource_scope == "sequential",
                        )
                        .order_by(creative_blueprints.c.id)
                    ).mappings().all()
                ],
                "characters": [
                    self.repository._creative_item(row) for row in connection.execute(
                        select(creative_characters)
                        .where(
                            creative_characters.c.project_id == int(project_id),
                            creative_characters.c.resource_scope == "sequential",
                        )
                        .order_by(
                            creative_characters.c.version,
                            creative_characters.c.id,
                        )
                    ).mappings().all()
                ],
                "relationships": [
                    self.repository._creative_item(row) for row in connection.execute(
                        select(creative_relationships)
                        .where(
                            creative_relationships.c.project_id == int(project_id),
                            creative_relationships.c.resource_scope == "sequential",
                        )
                        .order_by(
                            creative_relationships.c.version,
                            creative_relationships.c.id,
                        )
                    ).mappings().all()
                ],
                "episodes": [
                    self.repository._episode(row) for row in connection.execute(
                        select(creative_episodes)
                        .where(
                            creative_episodes.c.project_id == int(project_id),
                            creative_episodes.c.resource_scope == "sequential",
                        )
                        .order_by(
                            creative_episodes.c.stage,
                            creative_episodes.c.episode_number,
                            creative_episodes.c.version,
                        )
                    ).mappings().all()
                ],
            }
