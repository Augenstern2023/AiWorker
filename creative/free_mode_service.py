# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from creative.outline_fields import outline_field_source_fingerprint
from creative.prompt_templates import PROMPT_TEMPLATE_VERSION
from creative.repository import CreativeRepository, canonical_request_hash


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

    def __init__(self, repository: CreativeRepository) -> None:
        self.repository = repository

    @contextmanager
    def _migration_transaction(
        self,
        project_id: int,
        operation_id: str,
        compensations: list[dict[str, Any]],
        rollback_members: Callable[[dict[str, Any]], None] | None,
    ):
        try:
            with self.repository._connect() as conn:
                yield conn
        except BaseException as exc:
            if (
                compensations
                and self.completed_migration(project_id, operation_id) is None
            ):
                # Persist outside the rolled-back project transaction BEFORE cleanup.
                with self.repository._connect() as conn:
                    conn.execute(
                        "INSERT INTO creative_mode_migration_compensations "
                        "(operation_id, project_id, payload_json, last_error, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            operation_id,
                            project_id,
                            json.dumps(compensations),
                            str(exc),
                            time.time(),
                        ),
                    )
                self.recover_migration_compensation(
                    project_id, operation_id, rollback_members
                )
            raise

    def recover_migration_compensation(
        self,
        project_id: int,
        operation_id: str,
        rollback_members: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        with self.repository._connect() as conn:
            pending = conn.execute(
                "SELECT * FROM creative_mode_migration_compensations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if pending is None:
            return
        if int(pending["project_id"]) != int(project_id):
            raise ValueError("migration operation id belongs to another project")
        if rollback_members is None:
            raise ValueError("迁移成员补偿服务不可用，请稍后重试")
        try:
            for compensation in reversed(json.loads(pending["payload_json"])):
                rollback_members(compensation)
        except BaseException as exc:
            with self.repository._connect() as conn:
                conn.execute(
                    "UPDATE creative_mode_migration_compensations SET last_error = ? "
                    "WHERE operation_id = ?",
                    (str(exc), operation_id),
                )
            raise
        with self.repository._connect() as conn:
            conn.execute(
                "DELETE FROM creative_mode_migration_compensations "
                "WHERE operation_id = ? AND project_id = ? AND payload_json = ?",
                (operation_id, project_id, pending["payload_json"]),
            )

    @staticmethod
    def _loads(value: Any, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return fallback

    @classmethod
    def _native_json_row(cls, row: Any) -> dict[str, Any]:
        """Convert compatibility-query JSON text back to repository values."""

        result = dict(row)
        for key, fallback in (
            ("content_json", {}),
            ("source_refs_json", {}),
            ("generation_meta_json", {}),
            ("upstream_refs_json", {}),
        ):
            if key in result:
                result[key] = cls._loads(result[key], fallback)
        return result

    @staticmethod
    def _assert_stage(stage: str) -> str:
        normalized = str(stage or "").strip().lower()
        if normalized not in FREE_STAGES:
            raise ValueError("unsupported creative stage")
        return normalized

    def _raw_project(self, conn: Any, project_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM creative_projects WHERE id = ? AND deleted = 0",
            (int(project_id),),
        ).fetchone()
        if row is None:
            raise ValueError("创作项目不存在")
        return dict(row)

    def assert_free_project(self, project_id: int) -> dict[str, Any]:
        with self.repository._connect() as conn:
            project = self._raw_project(conn, project_id)
        if str(project.get("workflow_mode") or "sequential") != FREE_SCOPE:
            raise ValueError("该操作仅支持自由创作项目")
        return project

    def list_heads(self, project_id: int) -> list[dict[str, Any]]:
        self.assert_free_project(project_id)
        with self.repository._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM creative_resource_heads WHERE project_id = ? "
                    "AND resource_scope = 'free' ORDER BY stage, episode_number",
                    (int(project_id),),
                ).fetchall()
            ]

    def get_head(
        self, project_id: int, stage: str, episode_number: int | None = None
    ) -> dict[str, Any] | None:
        stage = self._assert_stage(stage)
        number = int(episode_number or 0)
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_resource_heads WHERE project_id = ? "
                "AND resource_scope = 'free' AND stage = ? AND episode_number = ?",
                (int(project_id), stage, number),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _set_head_conn(
        conn: Any,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        resource_ref: str,
        resource_version: int,
        expected_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT revision FROM creative_resource_heads WHERE project_id = ? "
            "AND resource_scope = 'free' AND stage = ? AND episode_number = ?",
            (int(project_id), stage, int(episode_number)),
        ).fetchone()
        now = time.time()
        if row is None:
            if int(expected_revision) != 0:
                raise ValueError("creative resource head changed")
            revision = 1
            conn.execute(
                """
                INSERT INTO creative_resource_heads (
                    project_id, resource_scope, stage, episode_number,
                    resource_ref, resource_version, revision, updated_at, updated_by
                ) VALUES (?, 'free', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(project_id), stage, int(episode_number), str(resource_ref),
                    int(resource_version), revision, now, actor_user_id,
                ),
            )
        else:
            if int(row["revision"] or 0) != int(expected_revision):
                raise ValueError("creative resource head changed")
            revision = int(expected_revision) + 1
            conn.execute(
                """
                UPDATE creative_resource_heads
                SET resource_ref = ?, resource_version = ?, revision = ?,
                    updated_at = ?, updated_by = ?
                WHERE project_id = ? AND resource_scope = 'free'
                    AND stage = ? AND episode_number = ?
                """,
                (
                    str(resource_ref), int(resource_version), revision, now,
                    actor_user_id, int(project_id), stage, int(episode_number),
                ),
            )
        return {
            "project_id": int(project_id),
            "resource_scope": FREE_SCOPE,
            "stage": stage,
            "episode_number": int(episode_number),
            "resource_ref": str(resource_ref),
            "resource_version": int(resource_version),
            "revision": revision,
            "updated_at": now,
            "updated_by": actor_user_id,
        }

    def _assert_head_resource(
        self,
        conn: Any,
        project_id: int,
        stage: str,
        episode_number: int,
        resource_ref: str,
    ) -> int:
        if stage == "brief":
            row = conn.execute(
                "SELECT version FROM creative_blueprints WHERE id = ? AND project_id = ? "
                "AND resource_scope = 'free'",
                (int(resource_ref), int(project_id)),
            ).fetchone()
        elif stage == "characters":
            row = conn.execute(
                "SELECT MAX(version) AS version FROM creative_characters "
                "WHERE project_id = ? AND batch_id = ? AND resource_scope = 'free'",
                (int(project_id), str(resource_ref)),
            ).fetchone()
            if row is not None and row["version"] is None:
                row = None
        elif stage == "outline":
            row = conn.execute(
                "SELECT MAX(version) AS version FROM creative_episodes WHERE project_id = ? "
                "AND stage = 'outline' AND batch_id = ? AND resource_scope = 'free'",
                (int(project_id), str(resource_ref)),
            ).fetchone()
            if row is not None and row["version"] is None:
                row = None
        else:
            row = conn.execute(
                "SELECT version FROM creative_episodes WHERE id = ? AND project_id = ? "
                "AND stage = ? AND episode_number = ? AND resource_scope = 'free'",
                (int(resource_ref), int(project_id), stage, int(episode_number)),
            ).fetchone()
        if row is None:
            raise ValueError("自由创作资源不存在或不属于当前模块")
        return int(row["version"] or 1)

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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            version = self._assert_head_resource(
                conn, project_id, stage, number, resource_ref
            )
            return self._set_head_conn(
                conn,
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
        conn: Any,
        *,
        project_id: int,
        content: dict[str, Any],
        source_refs: dict[str, Any],
        generation_meta: dict[str, Any],
        status: str,
        parent_id: int | None = None,
    ) -> tuple[int, int]:
        now = time.time()
        candidate_group_id = uuid.uuid4().hex
        version = 1
        if parent_id is not None:
            parent = conn.execute(
                "SELECT candidate_group_id, version FROM creative_blueprints "
                "WHERE id = ? AND project_id = ? AND resource_scope = 'free'",
                (int(parent_id), int(project_id)),
            ).fetchone()
            if parent is None:
                raise ValueError("自由 Brief 父版本不存在")
            candidate_group_id = str(parent["candidate_group_id"])
            version = int(parent["version"] or 0) + 1
        cursor = conn.execute(
            """
            INSERT INTO creative_blueprints (
                project_id, candidate_group_id, version, parent_version_id,
                status, content_json, risk_score, risk_level, difference_note,
                source_refs_json, direction_type, generation_meta_json,
                resource_scope, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 'low', '', ?, 'free', ?, 'free', ?, ?)
            """,
            (
                int(project_id), candidate_group_id, version, parent_id, status,
                json.dumps(content, ensure_ascii=False),
                json.dumps(source_refs, ensure_ascii=False),
                json.dumps(generation_meta, ensure_ascii=False), now, now,
            ),
        )
        return int(cursor.lastrowid), version

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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            current = conn.execute(
                "SELECT resource_ref FROM creative_resource_heads WHERE project_id = ? "
                "AND resource_scope = 'free' AND stage = 'brief' AND episode_number = 0",
                (int(project_id),),
            ).fetchone()
            parent_id = int(current["resource_ref"]) if current is not None else None
            resource_id, version = self._create_brief_conn(
                conn,
                project_id=project_id,
                content=dict(content or {}),
                source_refs=dict(source_refs or {}),
                generation_meta=dict(generation_meta or {}),
                status="draft",
                parent_id=parent_id,
            )
            head = self._set_head_conn(
                conn,
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
        select_as_head: bool = True,
    ) -> dict[str, Any]:
        """Save independent directions atomically and select the first as current."""
        if not 1 <= len(contents) <= 5:
            raise ValueError("单次自由 Brief 生成数量必须在 1-5 之间")
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            resources = [
                self._create_brief_conn(
                    conn,
                    project_id=project_id,
                    content=dict(content),
                    source_refs=dict(source_refs or {}),
                    generation_meta=dict(generation_meta or {}),
                    status="draft",
                )
                for content in contents
            ]
            resource_id, version = resources[0]
            head = (
                self._set_head_conn(
                    conn,
                    project_id=project_id,
                    stage="brief",
                    episode_number=0,
                    resource_ref=str(resource_id),
                    resource_version=version,
                    expected_revision=expected_head_revision,
                    actor_user_id=actor_user_id,
                )
                if select_as_head
                else None
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
        select_as_head: bool = True,
    ) -> dict[str, Any]:
        if not characters:
            characters = [{"name": "", "role": ""}]
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            head_row = conn.execute(
                "SELECT resource_ref FROM creative_resource_heads WHERE project_id = ? "
                "AND resource_scope = 'free' AND stage = 'characters' AND episode_number = 0",
                (int(project_id),),
            ).fetchone()
            parent_batch_id = str(head_row["resource_ref"]) if head_row is not None else ""
            parent_characters = {
                str(row["character_key"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, character_key FROM creative_characters WHERE project_id = ? "
                    "AND batch_id = ? AND resource_scope = 'free'",
                    (int(project_id), parent_batch_id),
                ).fetchall()
            } if parent_batch_id else {}
            parent_relationships = {
                str(row["relationship_key"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, relationship_key FROM creative_relationships WHERE project_id = ? "
                    "AND batch_id = ? AND resource_scope = 'free'",
                    (int(project_id), parent_batch_id),
                ).fetchall()
            } if parent_batch_id else {}
            version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version "
                "FROM creative_characters WHERE project_id = ?",
                (int(project_id),),
            ).fetchone()["version"])
            batch_id = uuid.uuid4().hex
            now = time.time()
            refs_json = json.dumps(source_refs or {}, ensure_ascii=False)
            meta_json = json.dumps(generation_meta or {}, ensure_ascii=False)
            for index, item in enumerate(characters, start=1):
                character_key = str(
                    item.get("character_key")
                    or f"character-{index}-{uuid.uuid4().hex}"
                )
                conn.execute(
                    """
                    INSERT INTO creative_characters (
                        project_id, batch_id, version, parent_version_id,
                        character_key, status,
                        content_json, source_refs_json, generation_meta_json,
                        resource_scope, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, 'free', ?, ?)
                    """,
                    (
                        int(project_id), batch_id, version,
                        parent_characters.get(character_key), character_key,
                        json.dumps(item.get("content") or item, ensure_ascii=False),
                        refs_json, meta_json, now, now,
                    ),
                )
            for index, item in enumerate(relationships, start=1):
                relationship_key = str(
                    item.get("relationship_key")
                    or f"relationship-{index}-{uuid.uuid4().hex}"
                )
                conn.execute(
                    """
                    INSERT INTO creative_relationships (
                        project_id, batch_id, version, parent_version_id,
                        relationship_key, status,
                        content_json, source_refs_json, generation_meta_json,
                        resource_scope, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, 'free', ?, ?)
                    """,
                    (
                        int(project_id), batch_id, version,
                        parent_relationships.get(relationship_key), relationship_key,
                        json.dumps(item.get("content") or item, ensure_ascii=False),
                        refs_json, meta_json, now, now,
                    ),
                )
            head = (
                self._set_head_conn(
                    conn,
                    project_id=project_id,
                    stage="characters",
                    episode_number=0,
                    resource_ref=batch_id,
                    resource_version=version,
                    expected_revision=expected_head_revision,
                    actor_user_id=actor_user_id,
                )
                if select_as_head
                else None
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
        select_as_head: bool = True,
    ) -> dict[str, Any]:
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            head_row = conn.execute(
                "SELECT resource_ref FROM creative_resource_heads WHERE project_id = ? "
                "AND resource_scope = 'free' AND stage = 'outline' AND episode_number = 0",
                (int(project_id),),
            ).fetchone()
            parent_batch_id = str(head_row["resource_ref"]) if head_row is not None else ""
            parent_episodes = {
                int(row["episode_number"]): int(row["id"])
                for row in conn.execute(
                    "SELECT id, episode_number FROM creative_episodes WHERE project_id = ? "
                    "AND stage = 'outline' AND batch_id = ? AND resource_scope = 'free'",
                    (int(project_id), parent_batch_id),
                ).fetchall()
            } if parent_batch_id else {}
            total = int(project["target_episode_count"])
            if not episodes:
                episodes = [self._blank_outline_episode(project, number) for number in range(1, total + 1)]
            numbers = sorted(int(item.get("episode") or 0) for item in episodes)
            if numbers != list(range(1, total + 1)):
                raise ValueError("自由模式全剧大纲必须覆盖项目全部集数")
            version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_episodes "
                "WHERE project_id = ? AND stage = 'outline'",
                (int(project_id),),
            ).fetchone()["version"])
            batch_id = uuid.uuid4().hex
            now = time.time()
            refs_json = json.dumps(source_refs or {}, ensure_ascii=False)
            meta_json = json.dumps(generation_meta or {}, ensure_ascii=False)
            for item in sorted(episodes, key=lambda value: int(value.get("episode") or 0)):
                number = int(item["episode"])
                content = dict(item)
                content["episode"] = number
                content["series_arc"] = series_arc
                if series_overview is not None:
                    content["series_overview"] = series_overview
                conn.execute(
                    """
                    INSERT INTO creative_episodes (
                        project_id, stage, episode_number, batch_id, version,
                        parent_version_id, status, content_json, upstream_refs_json,
                        generation_meta_json,
                        resource_scope, created_at, updated_at
                    ) VALUES (?, 'outline', ?, ?, ?, ?, 'draft', ?, ?, ?, 'free', ?, ?)
                    """,
                    (
                        int(project_id), number, batch_id, version,
                        parent_episodes.get(number),
                        json.dumps(content, ensure_ascii=False), refs_json, meta_json, now, now,
                    ),
                )
            head = (
                self._set_head_conn(
                    conn,
                    project_id=project_id,
                    stage="outline",
                    episode_number=0,
                    resource_ref=batch_id,
                    resource_version=version,
                    expected_revision=expected_head_revision,
                    actor_user_id=actor_user_id,
                )
                if select_as_head
                else None
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
        select_as_head: bool = True,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        if stage not in {"beats", "script"}:
            raise ValueError("episode resource stage must be beats or script")
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            number = int(episode_number)
            if not 1 <= number <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            head_row = conn.execute(
                "SELECT resource_ref FROM creative_resource_heads WHERE project_id = ? "
                "AND resource_scope = 'free' AND stage = ? AND episode_number = ?",
                (int(project_id), stage, number),
            ).fetchone()
            parent_id = int(head_row["resource_ref"]) if head_row is not None else None
            version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_episodes "
                "WHERE project_id = ? AND stage = ? AND episode_number = ?",
                (int(project_id), stage, number),
            ).fetchone()["version"])
            now = time.time()
            batch_id = uuid.uuid4().hex
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version,
                    parent_version_id, status, content_json, upstream_refs_json,
                    generation_meta_json, resource_scope, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, 'free', ?, ?)
                """,
                (
                    int(project_id), stage, number, batch_id, version, parent_id,
                    json.dumps(content or {}, ensure_ascii=False),
                    json.dumps(source_refs or {}, ensure_ascii=False),
                    json.dumps(generation_meta or {}, ensure_ascii=False), now, now,
                ),
            )
            resource_id = int(cursor.lastrowid)
            head = (
                self._set_head_conn(
                    conn,
                    project_id=project_id,
                    stage=stage,
                    episode_number=number,
                    resource_ref=str(resource_id),
                    resource_version=version,
                    expected_revision=expected_head_revision,
                    actor_user_id=actor_user_id,
                )
                if select_as_head
                else None
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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            now = time.time()
            if stage == "brief":
                cursor = conn.execute(
                    "UPDATE creative_blueprints SET content_json = ?, updated_at = ? "
                    "WHERE id = ? AND project_id = ? AND resource_scope = 'free' AND status = 'draft'",
                    (json.dumps(content, ensure_ascii=False), now, int(resource_ref), int(project_id)),
                )
                result_kind = "brief"
            elif stage in {"beats", "script"}:
                cursor = conn.execute(
                    "UPDATE creative_episodes SET content_json = ?, updated_at = ? "
                    "WHERE id = ? AND project_id = ? AND stage = ? "
                    "AND resource_scope = 'free' AND status = 'draft'",
                    (json.dumps(content, ensure_ascii=False), now, int(resource_ref), int(project_id), stage),
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
        tables = {
            "brief": ("creative_blueprints", "brief"),
            "character": ("creative_characters", "characters"),
            "relationship": ("creative_relationships", "characters"),
            "episode": ("creative_episodes", None),
        }
        if item_type not in tables:
            raise ValueError("unsupported free creative item type")
        table, expected_stage = tables[item_type]
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ? AND project_id = ? "
                "AND resource_scope = 'free'",
                (int(item_id), int(project_id)),
            ).fetchone()
            if row is None:
                raise ValueError("自由创作资源不存在")
            if expected_stage is not None and item_type == "brief":
                stage = "brief"
            elif expected_stage is not None:
                stage = expected_stage
            else:
                stage = str(row["stage"])
            if stage not in FREE_STAGES:
                raise ValueError("自由创作资源类型错误")
            if row["status"] != "draft":
                raise ValueError("已锁定的自由创作资源不能修改")
            conn.execute(
                f"UPDATE {table} SET content_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(content, ensure_ascii=False), time.time(), int(item_id)),
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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            version = self._assert_head_resource(conn, project_id, stage, number, resource_ref)
            now = time.time()
            if stage == "brief":
                cursor = conn.execute(
                    "UPDATE creative_blueprints SET status = 'brief_locked', locked_at = ?, updated_at = ? "
                    "WHERE id = ? AND project_id = ? AND resource_scope = 'free' "
                    "AND status IN ('draft', 'brief_locked')",
                    (now, now, int(resource_ref), int(project_id)),
                )
            elif stage == "characters":
                count = conn.execute(
                    "SELECT COUNT(*) AS count FROM creative_characters WHERE project_id = ? "
                    "AND batch_id = ? AND resource_scope = 'free'",
                    (int(project_id), str(resource_ref)),
                ).fetchone()["count"]
                if int(count) < 1:
                    raise ValueError("人物关系版本至少需要一个人物")
                cursor = conn.execute(
                    "UPDATE creative_characters SET status = 'locked', locked_at = ?, updated_at = ? "
                    "WHERE project_id = ? AND batch_id = ? AND resource_scope = 'free'",
                    (now, now, int(project_id), str(resource_ref)),
                )
                conn.execute(
                    "UPDATE creative_relationships SET status = 'locked', locked_at = ?, updated_at = ? "
                    "WHERE project_id = ? AND batch_id = ? AND resource_scope = 'free'",
                    (now, now, int(project_id), str(resource_ref)),
                )
            elif stage == "outline":
                count = conn.execute(
                    "SELECT COUNT(*) AS count FROM creative_episodes WHERE project_id = ? "
                    "AND stage = 'outline' AND batch_id = ? AND resource_scope = 'free'",
                    (int(project_id), str(resource_ref)),
                ).fetchone()["count"]
                if int(count) != int(project["target_episode_count"]):
                    raise ValueError("分集大纲不完整，不能锁定")
                cursor = conn.execute(
                    "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? "
                    "WHERE project_id = ? AND stage = 'outline' AND batch_id = ? "
                    "AND resource_scope = 'free'",
                    (now, now, int(project_id), str(resource_ref)),
                )
            else:
                cursor = conn.execute(
                    "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? "
                    "WHERE id = ? AND project_id = ? AND stage = ? AND episode_number = ? "
                    "AND resource_scope = 'free' AND status IN ('draft', 'locked')",
                    (now, now, int(resource_ref), int(project_id), stage, number),
                )
            if cursor.rowcount < 1:
                raise ValueError("自由创作资源不存在或无法锁定")
            head = self._set_head_conn(
                conn,
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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
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
                head_row = conn.execute(
                    "SELECT * FROM creative_resource_heads WHERE project_id = ? "
                    "AND resource_scope = 'free' AND stage = ? AND episode_number = ?",
                    (int(project_id), stage, episode_number),
                ).fetchone()
                if head_row is None:
                    raise ValueError(f"第 {episode_number} 集尚未生成 {label}")
                head = dict(head_row)
                if int(head["revision"]) != revisions[episode_number]:
                    raise ValueError(
                        f"第 {episode_number} 集{label} Head 已发生变化，请刷新后重试"
                    )
                resource_row = conn.execute(
                    "SELECT * FROM creative_episodes WHERE id = ? AND project_id = ? "
                    "AND resource_scope = 'free' AND stage = ? AND episode_number = ?",
                    (
                        int(head["resource_ref"]),
                        int(project_id),
                        stage,
                        episode_number,
                    ),
                ).fetchone()
                if resource_row is None:
                    raise ValueError(f"第 {episode_number} 集当前 {label} 不存在")
                resource = dict(resource_row)
                if resource["status"] not in {"draft", "locked", "approved"}:
                    raise ValueError(f"第 {episode_number} 集当前 {label} 无法锁定")
                records.append((episode_number, head, resource))

            now = time.time()
            locked_count = 0
            heads: list[dict[str, Any]] = []
            resource_ids: list[int] = []
            for episode_number, head, resource in records:
                resource_id = int(resource["id"])
                resource_ids.append(resource_id)
                if resource["status"] != "draft":
                    heads.append(head)
                    continue
                cursor = conn.execute(
                    "UPDATE creative_episodes SET status = 'locked', locked_at = ?, "
                    "updated_at = ? WHERE id = ? AND status = 'draft'",
                    (now, now, resource_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"第 {episode_number} 集{label}锁定失败")
                heads.append(
                    self._set_head_conn(
                        conn,
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

    def _latest_seed_conn(self, conn: Any, project_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM creative_seeds WHERE project_id = ? ORDER BY version DESC, id DESC LIMIT 1",
            (int(project_id),),
        ).fetchone()
        if row is None or len(str(row["seed_text"] or "").strip()) < 2:
            raise ValueError("自由创作项目缺少创作种子")
        return dict(row)

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
        prompt_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage = self._assert_stage(stage)
        supplied = {key for key, value in context_refs.items() if value not in {None, ""}}
        invalid = supplied - ALLOWED_CONTEXT_REFS[stage]
        if invalid:
            raise ValueError(f"{stage} 不支持引用：{', '.join(sorted(invalid))}")
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode") != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            number = int(episode_number or 0)
            if stage in {"beats", "script"} and not 1 <= number <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            seed = self._latest_seed_conn(conn, project_id)
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
            if prompt_snapshot:
                snapshot["prompt_snapshot"] = dict(prompt_snapshot)
            manifest: dict[str, Any] = {}
            brief_id = context_refs.get("brief_id")
            if brief_id:
                row = conn.execute(
                    "SELECT * FROM creative_blueprints WHERE id = ? AND project_id = ? "
                    "AND resource_scope = 'free'",
                    (int(brief_id), int(project_id)),
                ).fetchone()
                if row is None or row["status"] not in LOCKED_STATUSES:
                    raise ValueError("只能引用同项目已锁定的自由 Brief")
                content = self._loads(row["content_json"], {})
                snapshot["brief"] = content
                manifest["brief"] = self._manifest_item("brief", str(row["id"]), int(row["version"]), content)
            character_batch_id = context_refs.get("character_batch_id")
            if character_batch_id:
                character_rows = conn.execute(
                    "SELECT * FROM creative_characters WHERE project_id = ? AND batch_id = ? "
                    "AND resource_scope = 'free' ORDER BY id",
                    (int(project_id), str(character_batch_id)),
                ).fetchall()
                if not character_rows or any(row["status"] not in LOCKED_STATUSES for row in character_rows):
                    raise ValueError("只能引用同项目已锁定的自由人物版本")
                relationship_rows = conn.execute(
                    "SELECT * FROM creative_relationships WHERE project_id = ? AND batch_id = ? "
                    "AND resource_scope = 'free' ORDER BY id",
                    (int(project_id), str(character_batch_id)),
                ).fetchall()
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
                rows = conn.execute(
                    "SELECT * FROM creative_episodes WHERE project_id = ? AND stage = 'outline' "
                    "AND batch_id = ? AND resource_scope = 'free' ORDER BY episode_number",
                    (int(project_id), str(outline_batch_id)),
                ).fetchall()
                if not rows or any(row["status"] not in LOCKED_STATUSES for row in rows):
                    raise ValueError("只能引用同项目已锁定的自由大纲")
                outlines = [self._loads(row["content_json"], {}) for row in rows]
                snapshot["outlines"] = outlines
                manifest["outline"] = self._manifest_item(
                    "outline", str(outline_batch_id), int(rows[0]["version"]), outlines
                )
            beats_id = context_refs.get("beats_id")
            if beats_id:
                row = conn.execute(
                    "SELECT * FROM creative_episodes WHERE id = ? AND project_id = ? "
                    "AND stage = 'beats' AND resource_scope = 'free'",
                    (int(beats_id), int(project_id)),
                ).fetchone()
                if (
                    row is None
                    or row["status"] not in LOCKED_STATUSES
                    or int(row["episode_number"]) != number
                ):
                    raise ValueError("只能引用当前集已锁定的自由 Beats")
                content = self._loads(row["content_json"], {})
                snapshot["beats"] = content
                manifest["beats"] = self._manifest_item(
                    "beats", str(row["id"]), int(row["version"]), content
                )
            context_hash = canonical_request_hash(snapshot)
            existing = conn.execute(
                "SELECT * FROM creative_generation_contexts WHERE id = ?",
                (str(context_id),),
            ).fetchone()
            if existing is not None:
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
                return self._context_row(dict(existing))
            conn.execute(
                """
                INSERT INTO creative_generation_contexts (
                    id, project_id, target_stage, episode_number, seed_version_id,
                    instruction, context_manifest_json, context_snapshot_json,
                    context_hash, prompt_schema_version, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(context_id), int(project_id), stage,
                    number if stage in {"beats", "script"} else None,
                    int(seed["id"]), str(instruction or "").strip(),
                    json.dumps(manifest, ensure_ascii=False),
                    json.dumps(snapshot, ensure_ascii=False), context_hash,
                    str(
                        (prompt_snapshot or {}).get("schema_version")
                        or PROMPT_TEMPLATE_VERSION
                    ),
                    actor_user_id,
                    time.time(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM creative_generation_contexts WHERE id = ?",
                (str(context_id),),
            ).fetchone()
        return self._context_row(dict(row))

    @classmethod
    def _context_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["context_manifest"] = cls._loads(row.pop("context_manifest_json"), {})
        row["context_snapshot"] = cls._loads(row.pop("context_snapshot_json"), {})
        return row

    def get_generation_context(self, context_id: str) -> dict[str, Any]:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_generation_contexts WHERE id = ?",
                (str(context_id),),
            ).fetchone()
        if row is None:
            raise ValueError("自由创作生成上下文不存在")
        return self._context_row(dict(row))

    def context_options(self, project_id: int, episode_number: int | None = None) -> dict[str, Any]:
        locked = tuple(sorted(LOCKED_STATUSES))
        placeholders = ", ".join("?" for _ in locked)
        with self.repository._connect() as conn:
            project = self._raw_project(conn, project_id)
            if str(project.get("workflow_mode") or SEQUENTIAL_SCOPE) != FREE_SCOPE:
                raise ValueError("该操作仅支持自由创作项目")
            briefs = [
                self.repository._blueprint(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_blueprints WHERE project_id = ? "
                    f"AND resource_scope = 'free' AND status IN ({placeholders}) "
                    "ORDER BY id",
                    (int(project_id), *locked),
                ).fetchall()
            ]
            characters = [
                self.repository._creative_item(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_characters WHERE project_id = ? "
                    f"AND resource_scope = 'free' AND status IN ({placeholders}) "
                    "ORDER BY version, id",
                    (int(project_id), *locked),
                ).fetchall()
            ]
            episode_params: list[Any] = [int(project_id), *locked]
            episode_filter = ""
            if episode_number is not None:
                episode_filter = " AND (stage = 'outline' OR episode_number = ?)"
                episode_params.append(int(episode_number))
            episodes = [
                self.repository._episode(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_episodes WHERE project_id = ? "
                    f"AND resource_scope = 'free' AND status IN ({placeholders}) "
                    "AND stage IN ('outline', 'beats')"
                    f"{episode_filter} ORDER BY stage, episode_number, version",
                    tuple(episode_params),
                ).fetchall()
            ]
            heads = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM creative_resource_heads WHERE project_id = ? "
                    "AND resource_scope = 'free' ORDER BY stage, episode_number",
                    (int(project_id),),
                ).fetchall()
            ]
        character_batches: dict[str, dict[str, Any]] = {}
        for item in characters:
            batch = character_batches.setdefault(
                str(item["batch_id"]),
                {"batch_id": str(item["batch_id"]), "version": int(item.get("version") or 1), "characters": []},
            )
            batch["characters"].append(item)
        outline_batches: dict[str, dict[str, Any]] = {}
        beats: list[dict[str, Any]] = []
        for item in episodes:
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
            "heads": heads,
        }

    def _automatic_seed_text_conn(
        self, conn: Any, project: dict[str, Any], seed_text: str = ""
    ) -> str:
        supplied = str(seed_text or "").strip()
        if len(supplied) >= 2:
            return supplied
        topic_id = project.get("topic_id")
        breakdown_id = project.get("breakdown_id")
        if topic_id is not None and breakdown_id is not None:
            topic = conn.execute(
                "SELECT * FROM creative_topics WHERE id = ?", (int(topic_id),)
            ).fetchone()
            breakdown = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE id = ?",
                (int(breakdown_id),),
            ).fetchone()
            if topic is not None and breakdown is not None:
                resolved = self.repository._topic_based_seed_text(topic, breakdown)
                if len(resolved.strip()) >= 2:
                    return resolved
        return f"项目：{str(project.get('name') or '自由创作').strip()}"[:500]

    def ensure_seed(self, project_id: int, seed_text: str = "") -> dict[str, Any]:
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            row = conn.execute(
                "SELECT * FROM creative_seeds WHERE project_id = ? ORDER BY version DESC, id DESC LIMIT 1",
                (int(project_id),),
            ).fetchone()
            if row is None:
                resolved_seed_text = self._automatic_seed_text_conn(
                    conn, project, seed_text
                )
                now = time.time()
                cursor = conn.execute(
                    """
                    INSERT INTO creative_seeds (
                        project_id, version, status, seed_text, constraints_json,
                        reference_refs_json, created_at, updated_at, locked_at
                    ) VALUES (?, 1, 'frozen', ?, '{}', '[]', ?, ?, ?)
                    """,
                    (int(project_id), resolved_seed_text, now, now, now),
                )
                seed_id = int(cursor.lastrowid)
            else:
                seed_id = int(row["id"])
        return self.repository.get_seed(seed_id) or {}

    def has_creative_resources(self, project_id: int) -> bool:
        with self.repository._connect() as conn:
            total = 0
            for table in (
                "creative_blueprints", "creative_characters",
                "creative_relationships", "creative_episodes",
            ):
                total += int(conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE project_id = ?",
                    (int(project_id),),
                ).fetchone()["count"])
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
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._raw_project(conn, project_id)
            if int(project.get("mode_revision") or 0) != int(expected_mode_revision):
                raise ValueError("creative workflow mode changed")
            current = str(project.get("workflow_mode") or SEQUENTIAL_SCOPE)
            if current == workflow_mode:
                return self.repository.get_project(project_id) or project
            for table in (
                "creative_blueprints", "creative_characters",
                "creative_relationships", "creative_episodes",
            ):
                count = int(conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE project_id = ?",
                    (int(project_id),),
                ).fetchone()["count"])
                if count:
                    raise ValueError("项目已有创作资源，只能通过迁移进入自由模式")
            now = time.time()
            if workflow_mode == FREE_SCOPE:
                seed = conn.execute(
                    "SELECT id FROM creative_seeds WHERE project_id = ? "
                    "ORDER BY version DESC, id DESC LIMIT 1",
                    (int(project_id),),
                ).fetchone()
                if seed is None:
                    resolved_seed_text = self._automatic_seed_text_conn(
                        conn, project, seed_text
                    )
                    conn.execute(
                        """
                        INSERT INTO creative_seeds (
                            project_id, version, status, seed_text, constraints_json,
                            reference_refs_json, created_at, updated_at, locked_at
                        ) VALUES (?, 1, 'frozen', ?, '{}', '[]', ?, ?, ?)
                        """,
                        (int(project_id), resolved_seed_text, now, now, now),
                    )
            cursor = conn.execute(
                """
                UPDATE creative_projects
                SET workflow_mode = ?, mode_revision = mode_revision + 1,
                    mode_changed_at = ?, mode_changed_by = ?, status = ?, updated_at = ?
                WHERE id = ? AND mode_revision = ?
                """,
                (
                    workflow_mode, now, actor_user_id,
                    "free_active" if workflow_mode == FREE_SCOPE else "created",
                    now, int(project_id), int(expected_mode_revision),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("creative workflow mode changed")
        return self.repository.get_project(project_id) or {}

    def migration_preview(self, project_id: int) -> dict[str, Any]:
        with self.repository._connect() as conn:
            project = self._raw_project(conn, project_id)
            active_generation_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM creative_generation_operations "
                "WHERE project_id = ? AND status = 'active'",
                (int(project_id),),
            ).fetchone()["count"])
            active_review_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM script_reviews WHERE project_id = ? "
                "AND status NOT IN ('completed', 'confirmed', 'cancelled', 'failed')",
                (int(project_id),),
            ).fetchone()["count"])
            active_optimization_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM script_optimizations WHERE project_id = ? "
                "AND status NOT IN ('completed', 'cancelled', 'failed')",
                (int(project_id),),
            ).fetchone()["count"])
            resource_counts = {
                "brief": int(conn.execute(
                    "SELECT COUNT(*) AS count FROM creative_blueprints WHERE project_id = ? "
                    "AND resource_scope = 'sequential'", (int(project_id),)
                ).fetchone()["count"]),
                "characters": int(conn.execute(
                    "SELECT COUNT(DISTINCT batch_id) AS count FROM creative_characters "
                    "WHERE project_id = ? AND resource_scope = 'sequential'", (int(project_id),)
                ).fetchone()["count"]),
                "outline": int(conn.execute(
                    "SELECT COUNT(DISTINCT batch_id) AS count FROM creative_episodes "
                    "WHERE project_id = ? AND stage = 'outline' AND resource_scope = 'sequential'",
                    (int(project_id),),
                ).fetchone()["count"]),
                "beats": int(conn.execute(
                    "SELECT COUNT(*) AS count FROM creative_episodes WHERE project_id = ? "
                    "AND stage = 'beats' AND resource_scope = 'sequential'", (int(project_id),)
                ).fetchone()["count"]),
                "script": int(conn.execute(
                    "SELECT COUNT(*) AS count FROM creative_episodes WHERE project_id = ? "
                    "AND stage = 'script' AND resource_scope = 'sequential'", (int(project_id),)
                ).fetchone()["count"]),
            }
            seed = conn.execute(
                "SELECT id FROM creative_seeds WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (int(project_id),),
            ).fetchone()
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

    @staticmethod
    def _create_free_project_copy_conn(
        conn: Any,
        *,
        target_project_id: int,
        source_project: dict[str, Any],
        operation_id: str,
        actor_user_id: str,
        now: float,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO creative_projects (
                id, topic_id, breakdown_id, name, genre, target_platform,
                target_audience, target_episode_count, episode_duration_seconds,
                status, locked_brief_id, character_batch_id, outline_batch_id,
                workflow_task_id, creation_workflow_task_id, creation_request_id,
                created_by, deleted, creation_mode, bootstrap_status, revision,
                workflow_mode, mode_revision, mode_changed_at, mode_changed_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'free_active', NULL, NULL, NULL,
                      NULL, NULL, ?, ?, FALSE, ?, 'ready', 0, 'free', 0, ?, ?, ?, ?)
            """,
            (
                target_project_id,
                source_project.get("topic_id"),
                source_project.get("breakdown_id"),
                f"{str(source_project.get('name') or '未命名项目').strip()}（自由副本）",
                str(source_project.get("genre") or ""),
                str(source_project.get("target_platform") or ""),
                str(source_project.get("target_audience") or ""),
                int(source_project.get("target_episode_count") or 20),
                int(source_project.get("episode_duration_seconds") or 120),
                f"free-migration:{operation_id}",
                actor_user_id,
                str(source_project.get("creation_mode") or "imitation"),
                now,
                actor_user_id,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def completed_migration(
        self, project_id: int, operation_id: str
    ) -> dict[str, Any] | None:
        with self.repository._connect() as conn:
            prior = conn.execute(
                "SELECT * FROM creative_mode_migrations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
        if prior is not None:
            if int(prior["project_id"]) != int(project_id):
                raise ValueError("migration operation id belongs to another project")
            if prior["status"] != "completed":
                raise ValueError("creative mode migration is not complete")
            return self._completed_migration_result(prior, operation_id)
        return None

    def migrate_to_free(
        self,
        *,
        project_id: int,
        expected_mode_revision: int,
        operation_id: str,
        actor_user_id: str,
        seed_text: str = "",
        copy_members: Callable[[int], dict[str, Any]] | None = None,
        rollback_members: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        prior = self.completed_migration(project_id, operation_id)
        if prior is not None:
            return prior
        self.recover_migration_compensation(project_id, operation_id, rollback_members)
        preview = self.migration_preview(project_id)
        if preview["blockers"]:
            raise ValueError("；".join(preview["blockers"]))
        # Reserve and commit the sequence value before external memberships are
        # copied, so a rolled-back project ID can never be reused later.
        target_project_id = self.repository.reserve_project_identity()
        compensations: list[dict[str, Any]] = []
        with self._migration_transaction(
            project_id, operation_id, compensations, rollback_members
        ) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM creative_mode_migrations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing is not None:
                if int(existing["project_id"]) != int(project_id):
                    raise ValueError("migration operation id belongs to another project")
                if existing["status"] != "completed":
                    raise ValueError("creative mode migration is not complete")
                return self._completed_migration_result(existing, operation_id)
            project = self._raw_project(conn, project_id)
            if project.get("workflow_mode", "sequential") != "sequential":
                raise ValueError("项目已经是自由创作模式")
            if int(project.get("mode_revision") or 0) != int(expected_mode_revision):
                raise ValueError("creative workflow mode changed")
            seed = conn.execute(
                "SELECT * FROM creative_seeds WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                (int(project_id),),
            ).fetchone()
            now = time.time()
            conn.execute(
                """
                INSERT INTO creative_mode_migrations (
                    operation_id, project_id, from_mode, to_mode, source_mode_revision,
                    status, requested_by, created_at, updated_at
                ) VALUES (?, ?, 'sequential', 'free', ?, 'pending', ?, ?, ?)
                """,
                (
                    str(operation_id), int(project_id), int(expected_mode_revision),
                    actor_user_id, now, now,
                ),
            )
            target_project_id = self._create_free_project_copy_conn(
                conn,
                target_project_id=target_project_id,
                source_project=project,
                operation_id=operation_id,
                actor_user_id=actor_user_id,
                now=now,
            )
            resolved_seed_text = (
                str(seed["seed_text"] or "").strip()
                if seed is not None
                else self._automatic_seed_text_conn(conn, project, seed_text)
            )
            conn.execute(
                """
                INSERT INTO creative_seeds (
                    project_id, version, status, seed_text, constraints_json,
                    reference_refs_json, created_at, updated_at, locked_at
                ) VALUES (?, 1, 'frozen', ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_project_id,
                    resolved_seed_text,
                    str(seed["constraints_json"] or "{}") if seed is not None else "{}",
                    str(seed["reference_refs_json"] or "[]") if seed is not None else "[]",
                    now,
                    now,
                    now,
                ),
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
                brief_row = conn.execute(
                    "SELECT * FROM creative_blueprints WHERE id = ? AND project_id = ? "
                    "AND resource_scope = 'sequential'",
                    (int(project["locked_brief_id"]), int(project_id)),
                ).fetchone()
            if brief_row is None:
                brief_row = conn.execute(
                    "SELECT * FROM creative_blueprints WHERE project_id = ? "
                    "AND resource_scope = 'sequential' ORDER BY version DESC, id DESC LIMIT 1",
                    (int(project_id),),
                ).fetchone()
            if brief_row is not None:
                content = self._loads(brief_row["content_json"], {})
                new_id, version = self._create_brief_conn(
                    conn,
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
                    conn, project_id=target_project_id, stage="brief", episode_number=0,
                    resource_ref=str(new_id), resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["brief"] = {"source": int(brief_row["id"]), "target": new_id}

            character_rows = conn.execute(
                "SELECT * FROM creative_characters WHERE project_id = ? "
                "AND resource_scope = 'sequential' ORDER BY version, id",
                (int(project_id),),
            ).fetchall()
            if project.get("character_batch_id"):
                selected_characters = [
                    row for row in character_rows
                    if str(row["batch_id"]) == str(project["character_batch_id"])
                ]
            else:
                selected_characters = self._latest_group(list(character_rows), "batch_id")
            if selected_characters:
                source_batch = str(selected_characters[0]["batch_id"])
                new_batch = uuid.uuid4().hex
                version = int(conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_characters "
                    "WHERE project_id = ?", (target_project_id,)
                ).fetchone()["version"])
                for row in selected_characters:
                    conn.execute(
                        """
                        INSERT INTO creative_characters (
                            project_id, batch_id, version, character_key, status,
                            content_json, source_refs_json, generation_meta_json,
                            resource_scope, locked_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'free', ?, ?, ?)
                        """,
                        (
                            target_project_id, new_batch, version, str(row["character_key"]),
                            str(row["status"]), str(row["content_json"]),
                            json.dumps({"migration_source_id": int(row["id"])}, ensure_ascii=False),
                            json.dumps({"origin": "sequential_migration", "operation_id": operation_id}, ensure_ascii=False),
                            row["locked_at"], now, now,
                        ),
                    )
                relationship_rows = conn.execute(
                    "SELECT * FROM creative_relationships WHERE project_id = ? AND batch_id = ? "
                    "AND resource_scope = 'sequential' ORDER BY id",
                    (int(project_id), source_batch),
                ).fetchall()
                for row in relationship_rows:
                    conn.execute(
                        """
                        INSERT INTO creative_relationships (
                            project_id, batch_id, version, relationship_key, status,
                            content_json, source_refs_json, generation_meta_json,
                            resource_scope, locked_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'free', ?, ?, ?)
                        """,
                        (
                            target_project_id, new_batch, version, str(row["relationship_key"]),
                            str(row["status"]), str(row["content_json"]),
                            json.dumps({"migration_source_id": int(row["id"])}, ensure_ascii=False),
                            json.dumps({"origin": "sequential_migration", "operation_id": operation_id}, ensure_ascii=False),
                            row["locked_at"], now, now,
                        ),
                    )
                self._set_head_conn(
                    conn, project_id=target_project_id, stage="characters", episode_number=0,
                    resource_ref=new_batch, resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["characters"] = {"source": source_batch, "target": new_batch}

            outline_rows = conn.execute(
                "SELECT * FROM creative_episodes WHERE project_id = ? AND stage = 'outline' "
                "AND resource_scope = 'sequential' ORDER BY version, episode_number, id",
                (int(project_id),),
            ).fetchall()
            if project.get("outline_batch_id"):
                selected_outlines = [
                    row for row in outline_rows
                    if str(row["batch_id"]) == str(project["outline_batch_id"])
                ]
            else:
                selected_outlines = self._latest_group(list(outline_rows), "batch_id")
            if selected_outlines:
                source_batch = str(selected_outlines[0]["batch_id"])
                new_batch = uuid.uuid4().hex
                version = int(conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_episodes "
                    "WHERE project_id = ? AND stage = 'outline'", (target_project_id,)
                ).fetchone()["version"])
                for row in selected_outlines:
                    conn.execute(
                        """
                        INSERT INTO creative_episodes (
                            project_id, stage, episode_number, batch_id, version,
                            status, content_json, upstream_refs_json, generation_meta_json,
                            resource_scope, locked_at, created_at, updated_at
                        ) VALUES (?, 'outline', ?, ?, ?, ?, ?, ?, ?, 'free', ?, ?, ?)
                        """,
                        (
                            target_project_id, int(row["episode_number"]), new_batch, version,
                            str(row["status"]), str(row["content_json"]),
                            json.dumps({"migration_source_refs": self._loads(row["upstream_refs_json"], {})}, ensure_ascii=False),
                            json.dumps({"origin": "sequential_migration", "operation_id": operation_id, "migration_source_id": int(row["id"])}, ensure_ascii=False),
                            row["locked_at"], now, now,
                        ),
                    )
                self._set_head_conn(
                    conn, project_id=target_project_id, stage="outline", episode_number=0,
                    resource_ref=new_batch, resource_version=version,
                    expected_revision=0, actor_user_id=actor_user_id,
                )
                mapping["outline"] = {"source": source_batch, "target": new_batch}

            for stage in ("beats", "script"):
                rows = conn.execute(
                    "SELECT * FROM creative_episodes WHERE project_id = ? AND stage = ? "
                    "AND resource_scope = 'sequential' ORDER BY episode_number, version DESC, id DESC",
                    (int(project_id), stage),
                ).fetchall()
                selected: dict[int, Any] = {}
                for row in rows:
                    selected.setdefault(int(row["episode_number"]), row)
                for number, row in selected.items():
                    version = int(conn.execute(
                        "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_episodes "
                        "WHERE project_id = ? AND stage = ? AND episode_number = ?",
                        (target_project_id, stage, number),
                    ).fetchone()["version"])
                    cursor = conn.execute(
                        """
                        INSERT INTO creative_episodes (
                            project_id, stage, episode_number, batch_id, version,
                            status, content_json, upstream_refs_json, generation_meta_json,
                            resource_scope, locked_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'free', ?, ?, ?)
                        """,
                        (
                            target_project_id, stage, number, uuid.uuid4().hex, version,
                            str(row["status"]), str(row["content_json"]),
                            json.dumps({"migration_source_refs": self._loads(row["upstream_refs_json"], {})}, ensure_ascii=False),
                            json.dumps({"origin": "sequential_migration", "operation_id": operation_id, "migration_source_id": int(row["id"])}, ensure_ascii=False),
                            row["locked_at"], now, now,
                        ),
                    )
                    new_id = int(cursor.lastrowid)
                    self._set_head_conn(
                        conn, project_id=target_project_id, stage=stage, episode_number=number,
                        resource_ref=str(new_id), resource_version=version,
                        expected_revision=0, actor_user_id=actor_user_id,
                    )
                    mapping[stage][str(number)] = {"source": int(row["id"]), "target": new_id}

            conn.execute(
                "UPDATE creative_mode_migrations SET status = 'completed', mapping_json = ?, "
                "updated_at = ? WHERE operation_id = ?",
                (json.dumps(mapping, ensure_ascii=False), now, str(operation_id)),
            )
            if copy_members is not None:
                compensations.append(copy_members(target_project_id))
        return {
            "project": self.repository.get_project(target_project_id) or {},
            "mapping": mapping,
            "operation_id": operation_id,
            "created": True,
        }

    def list_legacy_resources(self, project_id: int) -> dict[str, Any]:
        self.assert_free_project(project_id)
        with self.repository._connect() as conn:
            return {
                "blueprints": [
                    self.repository._blueprint(self._native_json_row(row)) for row in conn.execute(
                        "SELECT * FROM creative_blueprints WHERE project_id = ? "
                        "AND resource_scope = 'sequential' ORDER BY id",
                        (int(project_id),),
                    ).fetchall()
                ],
                "characters": [
                    self.repository._creative_item(self._native_json_row(row)) for row in conn.execute(
                        "SELECT * FROM creative_characters WHERE project_id = ? "
                        "AND resource_scope = 'sequential' ORDER BY version, id",
                        (int(project_id),),
                    ).fetchall()
                ],
                "relationships": [
                    self.repository._creative_item(self._native_json_row(row)) for row in conn.execute(
                        "SELECT * FROM creative_relationships WHERE project_id = ? "
                        "AND resource_scope = 'sequential' ORDER BY version, id",
                        (int(project_id),),
                    ).fetchall()
                ],
                "episodes": [
                    self.repository._episode(self._native_json_row(row)) for row in conn.execute(
                        "SELECT * FROM creative_episodes WHERE project_id = ? "
                        "AND resource_scope = 'sequential' ORDER BY stage, episode_number, version",
                        (int(project_id),),
                    ).fetchall()
                ],
            }
