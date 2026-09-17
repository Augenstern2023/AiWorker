# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL behaviour of the creative version chains (``versions.py``).

Every test goes through the shared ``postgres_creative`` fixture. While the
domain mixins are still being landed, ``PostgresCreativeRepository`` does not yet
expose ``create_blueprints``; in that state the fixture instance is upgraded to
an equivalent class that composes :class:`VersionsRepository` with the test-only
project/operation stand-ins defined below. Once the coordinating agent wires the
real mixins, ``hasattr`` makes the tests run against the wired repository and the
stand-ins are never consulted.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import select, text, update

from creative.database_schema import (
    creative_blueprints,
    creative_characters,
    creative_episodes,
    creative_generation_operations,
    creative_operation_results,
    creative_projects,
    creative_relationships,
    creative_resource_heads,
)
from creative.legacy_sqlite_repository import canonical_request_hash
from creative.outline_fields import outline_field_source_fingerprint
from creative.outline_overview import SERIES_OVERVIEW_FIELDS, read_series_overview
from creative.postgres_repository import PostgresCreativeRepository
from creative.repositories.base import epoch_seconds, utc_now
from creative.repositories.versions import VersionsRepository


# ---------------------------------------------------------------------------
# test-only stand-ins for the not-yet-wired project/operation mixins
# ---------------------------------------------------------------------------


_ITEM_TABLES = {
    "creative_characters": creative_characters,
    "creative_relationships": creative_relationships,
    "creative_episodes": creative_episodes,
}


class _ProjectSupport:
    """Minimum faithful project/operation layer, used only while unwired.

    It mirrors the legacy ``get_project``/``_operation_project`` shape (hydrated
    ``blueprints``/``characters``/``relationships``/``episodes``), the
    idempotency table and the two creative-item helpers, so this domain can be
    exercised without depending on another agent's deliverable.
    """

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(creative_projects).where(
                    creative_projects.c.id == int(project_id),
                    creative_projects.c.deleted.is_(False),
                )
            ).mappings().first()
            if row is None:
                return None
            project = self.row_dict(row)
            scope = (
                "free"
                if str(project.get("workflow_mode") or "sequential") == "free"
                else "sequential"
            )
            project["source_platforms"] = []
            project["blueprints"] = [
                self._blueprint(item)
                for item in connection.execute(
                    select(creative_blueprints)
                    .where(
                        creative_blueprints.c.project_id == int(project_id),
                        creative_blueprints.c.resource_scope == scope,
                    )
                    .order_by(creative_blueprints.c.id)
                ).mappings().all()
            ]
            project["characters"] = [
                self._creative_item(item)
                for item in connection.execute(
                    select(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.resource_scope == scope,
                    )
                    .order_by(creative_characters.c.version, creative_characters.c.id)
                ).mappings().all()
            ]
            project["relationships"] = [
                self._creative_item(item)
                for item in connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.resource_scope == scope,
                    )
                    .order_by(
                        creative_relationships.c.version, creative_relationships.c.id
                    )
                ).mappings().all()
            ]
            project["episodes"] = [
                self._episode(item)
                for item in connection.execute(
                    select(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.resource_scope == scope,
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
                        creative_resource_heads.c.resource_scope == scope,
                    )
                    .order_by(
                        creative_resource_heads.c.stage,
                        creative_resource_heads.c.episode_number,
                    )
                ).mappings().all()
            ]
        return project

    def _operation_project(
        self,
        connection: Any,
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
                self._blueprint(item)
                for item in connection.execute(
                    select(creative_blueprints)
                    .where(creative_blueprints.c.project_id == int(project_id))
                    .order_by(creative_blueprints.c.id)
                ).mappings().all()
            ]
            project["characters"] = [
                self._creative_item(item)
                for item in connection.execute(
                    select(creative_characters)
                    .where(creative_characters.c.project_id == int(project_id))
                    .order_by(creative_characters.c.version, creative_characters.c.id)
                ).mappings().all()
            ]
            project["relationships"] = [
                self._creative_item(item)
                for item in connection.execute(
                    select(creative_relationships)
                    .where(creative_relationships.c.project_id == int(project_id))
                    .order_by(
                        creative_relationships.c.version, creative_relationships.c.id
                    )
                ).mappings().all()
            ]
            project["episodes"] = [
                self._episode(item)
                for item in connection.execute(
                    select(creative_episodes)
                    .where(creative_episodes.c.project_id == int(project_id))
                    .order_by(
                        creative_episodes.c.stage,
                        creative_episodes.c.episode_number,
                        creative_episodes.c.version,
                    )
                ).mappings().all()
            ]
        return project

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

    @staticmethod
    def _read_operation_result(
        connection: Any,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            select(creative_operation_results).where(
                creative_operation_results.c.operation_id == operation_id
            )
        ).mappings().first()
        if row is None:
            return None
        if (
            int(row["project_id"]) != int(project_id)
            or str(row["stage"]) != stage
            or str(row["request_hash"]) != request_hash
        ):
            raise ValueError("IDEMPOTENCY_KEY_REUSED")
        return dict(row["resource_ref_json"] or {})

    def _run_idempotent_operation(
        self,
        connection: Any,
        *,
        operation_id: str,
        project_id: int | None,
        stage: str,
        request_hash: Any,
        create: Any,
        resolve_project_id: Any = None,
    ) -> Any:
        operation = connection.execute(
            select(creative_operation_results).where(
                creative_operation_results.c.operation_id == operation_id
            )
        ).mappings().first()
        if operation is not None and project_id is None:
            project_id = int(operation["project_id"])
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
        connection.execute(
            creative_operation_results.insert().values(
                operation_id=operation_id,
                project_id=project_id,
                stage=stage,
                request_hash=resolved_hash,
                resource_ref_json=resource_ref,
                created_at=utc_now(),
            )
        )
        return self._hydrate_resource_ref(connection, project_id, resource_ref)

    def _hydrate_resource_ref(
        self, connection: Any, project_id: int, resource_ref: dict[str, Any]
    ) -> Any:
        resource_type = str(resource_ref.get("resource_type") or "")

        def restore_operation_metadata(resource: Any) -> Any:
            if isinstance(resource, dict) and "revision" in resource_ref:
                resource = dict(resource)
                resource["revision"] = int(resource_ref["revision"])
            return resource

        if resource_type in {"brief", "blueprints"} and "resource_ids" in resource_ref:
            ids = [int(value) for value in resource_ref.get("resource_ids") or []]
            if not ids:
                return []
            rows = connection.execute(
                select(creative_blueprints)
                .where(
                    creative_blueprints.c.id.in_(ids),
                    creative_blueprints.c.project_id == int(project_id),
                )
                .order_by(creative_blueprints.c.id)
            ).mappings().all()
            return [self._blueprint(row) for row in rows]
        if resource_type in {"brief", "blueprint"}:
            row = connection.execute(
                select(creative_blueprints).where(
                    creative_blueprints.c.id == int(resource_ref["resource_id"]),
                    creative_blueprints.c.project_id == int(project_id),
                )
            ).mappings().first()
            return restore_operation_metadata(
                self._blueprint(row) if row is not None else None
            )
        if resource_type in {"characters", "character_batch"}:
            batch_ref = str(resource_ref.get("resource_id") or resource_ref["batch_id"])
            ref_project_id, separator, batch_id = batch_ref.partition(":")
            if not separator:
                batch_id = ref_project_id
            elif int(ref_project_id) != int(project_id):
                raise ValueError("operation resource project mismatch")
            characters = [
                self._creative_item(row)
                for row in connection.execute(
                    select(creative_characters)
                    .where(
                        creative_characters.c.project_id == int(project_id),
                        creative_characters.c.batch_id == batch_id,
                    )
                    .order_by(creative_characters.c.id)
                ).mappings().all()
            ]
            relationships = [
                self._creative_item(row)
                for row in connection.execute(
                    select(creative_relationships)
                    .where(
                        creative_relationships.c.project_id == int(project_id),
                        creative_relationships.c.batch_id == batch_id,
                    )
                    .order_by(creative_relationships.c.id)
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
            # Read on the caller's connection: ``get_outline_batch`` would open a
            # second connection and could not see this uncommitted transaction.
            episodes = [
                self._episode(row)
                for row in connection.execute(
                    select(creative_episodes)
                    .where(
                        creative_episodes.c.project_id == int(project_id),
                        creative_episodes.c.stage == "outline",
                        creative_episodes.c.batch_id == batch_id,
                    )
                    .order_by(
                        creative_episodes.c.episode_number, creative_episodes.c.version
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
                self._episode(row) if row is not None else None
            )
        return dict(resource_ref)

    def _assert_generation_authority_conn(
        self,
        connection: Any,
        project_id: int,
        expected_authority: dict[str, Any] | None,
    ) -> None:
        # The real authority envelope belongs to the project domain; the version
        # tests never send one, and a sent envelope must not be silently trusted.
        del connection, project_id
        if expected_authority is None:
            return
        raise ValueError("server source authority changed")

    def _validate_generation_reservation_conn(
        self,
        connection: Any,
        *,
        operation_id: str,
        request_hash: str,
        batch_id: str,
        owner_id: str | None = None,
    ) -> None:
        row = connection.execute(
            select(creative_generation_operations).where(
                creative_generation_operations.c.operation_id == operation_id
            )
        ).mappings().first()
        if (
            row is None
            or str(row["request_hash"]) != request_hash
            or str(row["batch_id"]) != batch_id
            or str(row["status"]) != "active"
            or (owner_id is not None and str(row["owner_id"]) != owner_id)
        ):
            raise ValueError("generation operation reservation changed")

    def _get_creative_item(self, table: str, item_id: int) -> dict[str, Any] | None:
        if table not in {"creative_characters", "creative_relationships"}:
            raise ValueError("invalid creative table")
        mapped = _ITEM_TABLES[table]
        with self.connection_scope() as connection:
            row = connection.execute(
                select(mapped).where(mapped.c.id == int(item_id))
            ).mappings().first()
        return self._creative_item(row) if row is not None else None

    def _update_creative_item(
        self,
        table: str,
        item_id: int,
        content: dict[str, Any],
        *,
        episode: bool = False,
        preserve_content_keys: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        if table not in _ITEM_TABLES:
            raise ValueError("invalid creative table")
        mapped = _ITEM_TABLES[table]
        with self.transaction_scope() as connection:
            row = connection.execute(
                select(mapped).where(mapped.c.id == int(item_id))
            ).mappings().first()
            if row is None:
                return None
            if row["status"] != "draft":
                raise ValueError("已完成的版本不能原地修改")
            resolved_content = dict(content)
            current_content = dict(row["content_json"] or {})
            for key in preserve_content_keys:
                if key in current_content:
                    resolved_content[key] = current_content[key]
                else:
                    resolved_content.pop(key, None)
            connection.execute(
                update(mapped)
                .where(mapped.c.id == int(item_id))
                .values(content_json=resolved_content, updated_at=utc_now())
            )
            updated = connection.execute(
                select(mapped).where(mapped.c.id == int(item_id))
            ).mappings().first()
        return self._episode(updated) if episode else self._creative_item(updated)


_STAND_IN_NAMES = (
    "get_project",
    "_operation_project",
    "_operation_request_hash",
    "_read_operation_result",
    "_run_idempotent_operation",
    "_hydrate_resource_ref",
    "_assert_generation_authority_conn",
    "_validate_generation_reservation_conn",
    "_get_creative_item",
    "_update_creative_item",
)


def _composed_repository() -> type:
    """Compose this domain with the shared repository.

    The coordinating agent wires the mixins incrementally, so this returns the
    real repository once it exposes every cross-domain helper this domain calls.
    In a partially wired state a generated class supplies exactly the helpers
    that are still missing and inherits everything else, so the version tests
    stay meaningful instead of erroring on an unrelated half-landed mixin. The
    generated class deliberately does not inherit ``_ProjectSupport``: doing so
    would shadow helpers the real mixins already provide.
    """

    missing = tuple(
        name for name in _STAND_IN_NAMES if not hasattr(PostgresCreativeRepository, name)
    )
    if not missing:
        return PostgresCreativeRepository
    # ``vars`` keeps staticmethod descriptors static on the generated class.
    stand_in = type(
        "_MissingProjectSupport",
        (),
        {name: vars(_ProjectSupport)[name] for name in missing},
    )
    # Once this domain is wired into the shared repository, re-listing it as a
    # separate base makes the MRO inconsistent, so only add it while unwired.
    bases = (PostgresCreativeRepository,)
    if not issubclass(PostgresCreativeRepository, VersionsRepository):
        bases = (VersionsRepository, PostgresCreativeRepository)
    return type(
        "PartiallyWiredVersionsRepository",
        (stand_in, *bases),
        {"__doc__": "VersionsRepository plus the still-unwired project helpers."},
    )


@pytest.fixture
def versions_repo(postgres_creative):
    """The shared PostgreSQL repository, upgraded only while the mixin is unwired."""

    if all(hasattr(postgres_creative, name) for name in _STAND_IN_NAMES):
        return postgres_creative
    return _composed_repository()(postgres_creative.database)


# ---------------------------------------------------------------------------
# fixtures and builders
# ---------------------------------------------------------------------------


def _create_project(
    database, *, target_episode_count: int = 2, name: str = "版本链路项目"
) -> int:
    """Insert the prerequisite project row with raw SQL, as the plan requires."""

    with database.transaction() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO creative_projects ("
                    " name, genre, target_platform, target_audience,"
                    " target_episode_count, episode_duration_seconds,"
                    " creation_mode, created_at, updated_at"
                    ") VALUES ("
                    " :name, '悬疑', 'dy', '年轻观众', :count, 120, 'original', now(), now()"
                    ") RETURNING id"
                ),
                {"name": name, "count": int(target_episode_count)},
            ).scalar_one()
        )


def _outline_content(episode_number: int) -> dict[str, Any]:
    return {
        "episode": episode_number,
        "core_event": f"主角发现线索 {episode_number}",
        "conflict": f"冲突 {episode_number}",
        "character_progress": f"推进 {episode_number}",
        "highlight": f"亮点 {episode_number}",
        "reversal": f"反转 {episode_number}",
        "cliffhanger": f"悬念 {episode_number}",
    }


def _enhanced_beats(order: int = 1) -> dict[str, Any]:
    """A Beats payload that passes the strict (non-legacy) validation."""

    return {
        "beats": [
            {
                "order": order,
                "scene": "旧仓库",
                "characters": ["林昭"],
                "goal": "找到账本",
                "obstacle": "门被反锁",
                "emotion_change": "紧张到坚定",
                "turn": "发现脚印",
                "hook": "门外传来脚步",
                "state_change": "进入仓库",
                "cost": "暴露行踪",
                "opposition_tactic": "断电",
                "continuity_updates": [],
            }
        ]
    }


def _legacy_beats(order: int = 1) -> dict[str, Any]:
    return {"beats": [{"order": order}]}


def _series_overview() -> dict[str, str]:
    return {field: f"概览 {field}" for field in SERIES_OVERVIEW_FIELDS}


def _script_content(title: str = "第一集") -> dict[str, Any]:
    return {
        "episode_title": title,
        "scenes": [
            {
                "sequence_no": 1,
                "scene_no": 1,
                "time_of_day": "夜",
                "interior_exterior": "内",
                "location": "旧仓库",
                "props": ["手电"],
                "characters": ["林昭"],
                "blocks": [{"type": "action", "text": "林昭推开门，灰尘落下。"}],
            }
        ],
    }


def _lock_brief(
    repo, project_id: int, *, title: str = "方向 A", expected_revision: int = 0
) -> dict[str, Any]:
    blueprint = repo.create_blueprints(project_id, [{"content": {"title": title}}])[0]
    return repo.lock_brief(int(blueprint["id"]), expected_revision=expected_revision)


def _lock_characters(repo, project_id: int, *, name: str = "林昭") -> dict[str, Any]:
    batch = repo.create_character_batch(
        project_id,
        [{"name": name, "role": "主角"}, {"name": "陈默", "role": "对手"}],
        [{"from": name, "to": "陈默", "relation": "宿敌"}],
    )
    return repo.lock_character_batch(project_id, batch["batch_id"])


def _lock_outline(repo, project_id: int, *, episodes: int = 2) -> dict[str, Any]:
    batch = repo.create_outline_batch(
        project_id,
        [_outline_content(number) for number in range(1, episodes + 1)],
        "调查、受阻、反击",
    )
    return repo.lock_outline_batch(project_id, batch["batch_id"])


def _outline_ready_project(repo, database, *, episodes: int = 2) -> int:
    """A project with a locked Brief, locked characters and a locked outline."""

    project_id = _create_project(database, target_episode_count=episodes)
    _lock_brief(repo, project_id)
    _lock_characters(repo, project_id)
    _lock_outline(repo, project_id, episodes=episodes)
    return project_id


def _outline_draft_project(
    repo, database, *, episodes: int = 2
) -> tuple[int, str]:
    """A project whose outline batch is still an editable draft."""

    project_id = _create_project(database, target_episode_count=episodes)
    _lock_brief(repo, project_id)
    _lock_characters(repo, project_id)
    batch = repo.create_outline_batch(
        project_id,
        [_outline_content(number) for number in range(1, episodes + 1)],
        "调查、受阻、反击",
    )
    return project_id, str(batch["batch_id"])


def _active_outline_batch(repo, project_id: int) -> str:
    return str(repo.get_project(project_id)["outline_batch_id"])


# ---------------------------------------------------------------------------
# five-stage version chain
# ---------------------------------------------------------------------------


def test_blueprint_brief_chain_allocates_versions_and_locks(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)

    created = versions_repo.create_blueprints(
        project_id,
        [{"content": {"title": "方向一"}, "risk_score": 3, "risk_level": "low"}],
    )
    assert len(created) == 1
    first = created[0]
    assert first["version"] == 1
    assert first["status"] == "draft"
    assert first["content"] == {"title": "方向一"}
    assert first["source_refs"] == {"topic_id": None, "breakdown_id": None}
    assert first["risk_score"] == 3.0
    assert isinstance(first["created_at"], float)
    assert versions_repo.get_project(project_id)["status"] == "direction_review"

    locked = versions_repo.lock_brief(int(first["id"]), expected_revision=0)
    assert locked["status"] == "brief_locked"
    assert locked["revision"] == 1
    assert locked["content"]["project_name"] == "版本链路项目"
    assert locked["content"]["target_episode_count"] == 2
    assert locked["content"]["episode_duration_seconds"] == 120
    assert isinstance(locked["locked_at"], float)

    second = versions_repo.create_blueprint_version(int(first["id"]))
    assert second["version"] == 2
    assert second["parent_version_id"] == first["id"]
    assert second["status"] == "draft"
    assert second["content"] == locked["content"]

    project = versions_repo.get_project(project_id)
    assert project["locked_brief_id"] == first["id"]
    assert project["status"] == "brief_locked"


def test_character_outline_beats_script_chain(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    _lock_brief(versions_repo, project_id)

    batch = versions_repo.create_character_batch(
        project_id,
        [{"name": "林昭", "role": "主角"}],
        [{"from": "林昭", "to": "陈默", "relation": "宿敌"}],
    )
    assert [item["version"] for item in batch["characters"]] == [1]
    assert [item["version"] for item in batch["relationships"]] == [1]
    assert batch["characters"][0]["parent_version_id"] is None
    assert batch["characters"][0]["source_refs"] == {"brief_id": 1}

    locked_characters = versions_repo.lock_character_batch(
        project_id, batch["batch_id"]
    )
    assert [item["status"] for item in locked_characters["characters"]] == ["locked"]
    assert [item["status"] for item in locked_characters["relationships"]] == ["locked"]
    assert locked_characters["revision"] == 2

    outline = versions_repo.create_outline_batch(
        project_id, [_outline_content(1), _outline_content(2)], "调查、受阻、反击"
    )
    assert [item["version"] for item in outline["episodes"]] == [1, 1]
    locked_outline = versions_repo.lock_outline_batch(project_id, outline["batch_id"])
    assert [item["status"] for item in locked_outline["episodes"]] == ["locked", "locked"]
    assert locked_outline["revision"] == 3

    beats = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    assert beats["stage"] == "beats"
    assert beats["version"] == 1
    assert beats["upstream_refs"]["outline_id"] == outline["episodes"][0]["id"]
    assert beats["upstream_refs"]["outline_batch_id"] == outline["batch_id"]
    assert beats["upstream_refs"]["brief_id"] == 1

    locked_beats = versions_repo.lock_episode_beats(int(beats["id"]))
    assert locked_beats["status"] == "locked"
    assert isinstance(locked_beats["locked_at"], float)

    script = versions_repo.create_episode_script(project_id, 1, _script_content())
    assert script["stage"] == "script"
    assert script["version"] == 1
    assert script["upstream_refs"]["beats_id"] == locked_beats["id"]
    assert script["upstream_refs"]["beats_version"] == 1
    assert script["upstream_refs"]["outline_version"] == 1

    locked_script = versions_repo.lock_episode_script(int(script["id"]))
    assert locked_script["status"] == "locked"

    second_script = versions_repo.create_episode_script(
        project_id, 1, _script_content("第一集 v2")
    )
    assert second_script["version"] == 2
    assert second_script["parent_version_id"] == script["id"]

    versions = versions_repo.list_episode_versions(project_id, "script", 1)
    assert [item["version"] for item in versions] == [1, 2]
    assert [item["id"] for item in versions] == [script["id"], second_script["id"]]
    assert versions[0]["content"]["episode_title"] == "第一集"
    assert versions[1]["content"]["episode_title"] == "第一集 v2"

    # A new outline batch is a new version of the whole series, chained to the
    # previous batch per episode.
    second_outline = versions_repo.create_outline_batch(
        project_id,
        [_outline_content(1), _outline_content(2)],
        "第二版弧线",
    )
    assert [item["version"] for item in second_outline["episodes"]] == [2, 2]
    assert (
        second_outline["episodes"][0]["parent_version_id"]
        == outline["episodes"][0]["id"]
    )


def test_character_batch_versions_increment_per_project(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    _lock_brief(versions_repo, project_id)
    first = versions_repo.create_character_batch(project_id, [{"name": "林昭"}], [])
    second = versions_repo.create_character_batch(
        project_id, [{"name": "林昭"}], [], replaces_resource_id=first["batch_id"]
    )
    assert first["batch_id"] != second["batch_id"]
    assert second["characters"][0]["version"] == 2
    assert second["characters"][0]["parent_version_id"] == first["characters"][0]["id"]

    with pytest.raises(ValueError, match="replacement character batch does not exist"):
        versions_repo.create_character_batch(
            project_id, [{"name": "林昭"}], [], replaces_resource_id="missing"
        )
    with pytest.raises(ValueError, match="replacement character batch belongs to another project"):
        versions_repo.create_character_batch(
            project_id, [{"name": "林昭"}], [], replaces_resource_id="999999:missing"
        )


def test_project_revision_conflicts_are_rejected(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    blueprints = versions_repo.create_blueprints(
        project_id,
        [{"content": {"title": "方向一"}}, {"content": {"title": "方向二"}}],
    )
    versions_repo.lock_brief(int(blueprints[0]["id"]), expected_revision=0)

    with pytest.raises(ValueError, match="project revision changed"):
        versions_repo.create_character_batch(
            project_id, [{"name": "林昭"}], [], expected_project_revision=0
        )

    with pytest.raises(ValueError, match="project revision changed"):
        versions_repo.lock_brief(int(blueprints[1]["id"]), expected_revision=0)

    with pytest.raises(ValueError, match="锁定 Brief 新版本时必须提供项目修订号"):
        versions_repo.lock_brief(int(blueprints[1]["id"]))

    with pytest.raises(ValueError, match="只能锁定当前 Brief 的直接后继草稿"):
        versions_repo.lock_brief(int(blueprints[1]["id"]), expected_revision=1)


# ---------------------------------------------------------------------------
# locked brief / locked character selection
# ---------------------------------------------------------------------------


def test_locked_brief_selection_moves_the_active_direction(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    root = versions_repo.create_blueprints(project_id, [{"content": {"title": "方向一"}}])[0]
    versions_repo.lock_brief(int(root["id"]), expected_revision=0)

    version_two = versions_repo.create_blueprint_version(int(root["id"]))
    locked_two = versions_repo.lock_brief(int(version_two["id"]), expected_revision=1)
    assert locked_two["revision"] == 2
    assert locked_two["parent_version_id"] == root["id"]
    assert versions_repo.get_project(project_id)["locked_brief_id"] == version_two["id"]

    # Renaming the project makes the metadata retained by the locked Brief
    # observable again, so switching the active direction must copy it back.
    with isolated_system_database.transaction() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == project_id)
            .values(name="改名项目")
        )

    selected = versions_repo.select_locked_brief(
        project_id, int(root["id"]), expected_revision=2
    )
    assert selected["locked_brief_id"] == root["id"]
    assert selected["name"] == "版本链路项目"
    assert selected["status"] == "brief_locked"
    assert selected["revision"] == 3

    unchanged = versions_repo.select_locked_brief(
        project_id, int(root["id"]), expected_revision=3
    )
    assert unchanged["revision"] == 3

    with pytest.raises(ValueError, match="仿制方向不存在"):
        versions_repo.select_locked_brief(project_id, 999999)

    draft = versions_repo.create_blueprints(project_id, [{"content": {"title": "方向二"}}])[0]
    with pytest.raises(ValueError, match="只能选择已锁定的 Brief 版本"):
        versions_repo.select_locked_brief(project_id, int(draft["id"]))


def test_locked_character_batch_selection_requires_the_active_brief(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    root = versions_repo.create_blueprints(project_id, [{"content": {"title": "方向一"}}])[0]
    versions_repo.lock_brief(int(root["id"]), expected_revision=0)
    version_two = versions_repo.create_blueprint_version(int(root["id"]))
    versions_repo.lock_brief(int(version_two["id"]), expected_revision=1)

    batch = versions_repo.create_character_batch(
        project_id, [{"name": "林昭"}], []
    )
    locked_batch = versions_repo.lock_character_batch(project_id, batch["batch_id"])
    assert locked_batch["revision"] == 3
    assert (
        versions_repo.get_character_batch(project_id, batch["batch_id"])["characters"][0][
            "source_refs"
        ]["brief_id"]
        == version_two["id"]
    )

    other = versions_repo.select_locked_brief(
        project_id, int(root["id"]), expected_revision=3
    )
    assert other["locked_brief_id"] == root["id"]
    assert other["revision"] == 4

    with pytest.raises(ValueError, match="所选人物关系版本不属于当前锁定 Brief"):
        versions_repo.select_locked_character_batch(project_id, batch["batch_id"])

    back = versions_repo.select_locked_brief(
        project_id, int(version_two["id"]), expected_revision=4
    )
    assert back["locked_brief_id"] == version_two["id"]
    assert back["revision"] == 5

    selected = versions_repo.select_locked_character_batch(
        project_id, batch["batch_id"], expected_revision=5
    )
    assert selected["character_batch_id"] == batch["batch_id"]
    assert selected["revision"] == 6

    draft_batch = versions_repo.create_character_batch(
        project_id, [{"name": "新人物"}], []
    )
    with pytest.raises(ValueError, match="只能选择已锁定的人物关系版本"):
        versions_repo.select_locked_character_batch(project_id, draft_batch["batch_id"])

    second_batch = versions_repo.create_character_batch(
        project_id, [{"name": "新人物"}], []
    )
    versions_repo.lock_character_batch(project_id, second_batch["batch_id"])
    switched = versions_repo.select_locked_character_batch(
        project_id, batch["batch_id"], expected_revision=7
    )
    assert switched["character_batch_id"] == batch["batch_id"]
    assert switched["revision"] == 8


# ---------------------------------------------------------------------------
# outline checkpoints and overview
# ---------------------------------------------------------------------------


def test_outline_checkpoint_resumes_the_same_batch(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database, target_episode_count=2)
    _lock_brief(versions_repo, project_id)
    _lock_characters(versions_repo, project_id)

    first = versions_repo.save_outline_batch_checkpoint(
        project_id,
        [_outline_content(1)],
        "调查、受阻、反击",
        generation_meta={"stages": ["outline"]},
    )
    assert [item["episode_number"] for item in first["episodes"]] == [1]
    assert first["episodes"][0]["version"] == 1
    assert first["episodes"][0]["status"] == "draft"
    assert versions_repo.get_project(project_id)["status"] == "outline_draft"

    resumable = versions_repo.find_resumable_outline_batch(project_id)
    assert resumable is not None
    assert resumable["batch_id"] == first["batch_id"]
    assert resumable["series_arc"] == "调查、受阻、反击"
    assert resumable["stages"] == ["outline"]
    assert len(resumable["episodes"]) == 1

    second = versions_repo.save_outline_batch_checkpoint(
        project_id,
        [_outline_content(1), _outline_content(2)],
        "调查、受阻、反击",
        batch_id=first["batch_id"],
        generation_meta={"stages": ["outline"]},
    )
    assert [item["episode_number"] for item in second["episodes"]] == [1, 2]
    assert {item["version"] for item in second["episodes"]} == {1}
    assert second["episodes"][1]["parent_version_id"] is None

    assert versions_repo.find_resumable_outline_batch(project_id) is None
    complete = versions_repo.find_resumable_outline_batch(
        project_id, include_complete=True
    )
    assert complete is not None and complete["batch_id"] == first["batch_id"]

    versions_repo.lock_outline_batch(project_id, first["batch_id"])
    with pytest.raises(ValueError, match="已锁定的大纲批次不能写入生成检查点"):
        versions_repo.save_outline_batch_checkpoint(
            project_id,
            [_outline_content(1)],
            "调查、受阻、反击",
            batch_id=first["batch_id"],
        )


def test_outline_checkpoint_rejects_a_non_prefix(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database, target_episode_count=2)
    _lock_brief(versions_repo, project_id)
    _lock_characters(versions_repo, project_id)

    with pytest.raises(ValueError, match="分集大纲检查点必须连续覆盖 1-2 集的前缀"):
        versions_repo.save_outline_batch_checkpoint(
            project_id, [_outline_content(2)], "弧线"
        )

    other = _create_project(isolated_system_database)
    _lock_brief(versions_repo, other)
    with pytest.raises(ValueError, match="必须先锁定人物关系版本"):
        versions_repo.create_outline_batch(
            other, [_outline_content(1), _outline_content(2)], "弧线"
        )
    with pytest.raises(ValueError, match="分集大纲必须完整覆盖 1-2 集"):
        versions_repo.create_outline_batch(
            project_id, [_outline_content(1)], "弧线"
        )


def test_outline_overview_backfill_and_locked_protection(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    batch_id = versions_repo.get_project(project_id)["outline_batch_id"]

    ready = versions_repo.assert_outline_overview_generation_ready(project_id, batch_id)
    assert ready["series_overview"] is None

    updated = versions_repo.update_outline_overview(
        project_id, batch_id, _series_overview(), only_if_missing=True
    )
    assert updated["series_overview"] == _series_overview()
    assert [
        item["content"]["series_overview"] for item in updated["episodes"]
    ] == [_series_overview(), _series_overview()]

    with pytest.raises(ValueError, match="当前大纲版本已存在全集剧情概览"):
        versions_repo.update_outline_overview(
            project_id, batch_id, _series_overview(), only_if_missing=True
        )
    with pytest.raises(ValueError, match="分集大纲批次不存在"):
        versions_repo.assert_outline_overview_generation_ready(
            project_id, "missing-batch"
        )


def test_outline_overview_requires_a_complete_batch(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database, target_episode_count=2)
    _lock_brief(versions_repo, project_id)
    _lock_characters(versions_repo, project_id)
    partial = versions_repo.save_outline_batch_checkpoint(
        project_id, [_outline_content(1)], "弧线"
    )
    with pytest.raises(
        ValueError, match="分集大纲必须完整覆盖 1-2 集后才能生成全集剧情概览"
    ):
        versions_repo.assert_outline_overview_generation_ready(
            project_id, partial["batch_id"]
        )
    with pytest.raises(ValueError, match="分集大纲不完整，不能锁定"):
        versions_repo.lock_outline_batch(project_id, partial["batch_id"])


def test_outline_field_rewrite_context_and_fingerprint(
    versions_repo, isolated_system_database
):
    project_id, batch_id = _outline_draft_project(
        versions_repo, isolated_system_database
    )
    batch = versions_repo.get_outline_batch(project_id, batch_id)
    target = batch["episodes"][0]
    fingerprint = outline_field_source_fingerprint(target, "core_event")

    context = versions_repo.get_outline_field_rewrite_context(int(target["id"]))
    assert context["project"]["id"] == project_id
    assert context["brief"]["id"] == context["project"]["locked_brief_id"]
    assert context["episode"]["id"] == target["id"]
    assert [item["content"]["name"] for item in context["characters"]] == ["林昭", "陈默"]
    assert context["previous_episode"] is None
    assert context["next_episode"]["episode_number"] == 2

    saved = versions_repo.apply_outline_field_rewrite(
        int(target["id"]), "core_event", "重写后的核心事件", fingerprint
    )
    assert saved["content"]["core_event"] == "重写后的核心事件"
    assert saved["content"]["conflict"] == target["content"]["conflict"]
    assert saved["content"]["series_arc"] == "调查、受阻、反击"

    with pytest.raises(ValueError, match="该字段已被更新，请重新生成"):
        versions_repo.apply_outline_field_rewrite(
            int(target["id"]), "core_event", "过期重写", fingerprint
        )
    with pytest.raises(ValueError, match="不支持的大纲字段"):
        versions_repo.apply_outline_field_rewrite(
            int(target["id"]), "unknown_field", "x", fingerprint
        )
    with pytest.raises(ValueError, match="重写内容不能为空"):
        versions_repo.apply_outline_field_rewrite(
            int(target["id"]), "core_event", "   ", fingerprint
        )


def test_outline_field_rewrite_rejects_a_non_outline_episode(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    target = versions_repo.get_outline_batch(
        project_id, _active_outline_batch(versions_repo, project_id)
    )["episodes"][0]
    beats = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())

    with pytest.raises(ValueError, match="分集大纲不存在"):
        versions_repo.apply_outline_field_rewrite(
            int(beats["id"]),
            "core_event",
            "不能写入 Beats",
            outline_field_source_fingerprint(target, "core_event"),
        )


def test_outline_field_rewrite_checks_the_resource_head(
    versions_repo, isolated_system_database
):
    project_id, batch_id = _outline_draft_project(
        versions_repo, isolated_system_database
    )
    batch = versions_repo.get_outline_batch(project_id, batch_id)
    target = batch["episodes"][0]
    fingerprint = outline_field_source_fingerprint(target, "core_event")

    with pytest.raises(ValueError, match="creative resource head changed"):
        versions_repo.apply_outline_field_rewrite(
            int(target["id"]),
            "core_event",
            "无头写入",
            fingerprint,
            expected_head_revision=1,
        )

    with versions_repo.transaction_scope() as connection:
        head = versions_repo._advance_resource_head_conn(
            connection,
            project_id=project_id,
            stage="outline",
            resource_ref=str(batch["batch_id"]),
            resource_version=1,
            expected_revision=0,
        )
    assert head["revision"] == 1
    assert head["resource_ref"] == batch["batch_id"]
    assert isinstance(head["updated_at"], float)

    saved = versions_repo.apply_outline_field_rewrite(
        int(target["id"]),
        "core_event",
        "带头写入",
        fingerprint,
        expected_head_revision=1,
    )
    assert saved["content"]["core_event"] == "带头写入"

    with pytest.raises(ValueError, match="creative resource head changed"):
        versions_repo.apply_outline_field_rewrite(
            int(target["id"]),
            "conflict",
            "过期头写入",
            outline_field_source_fingerprint(saved, "conflict"),
            expected_head_revision=9,
        )


# ---------------------------------------------------------------------------
# beats and script lock semantics
# ---------------------------------------------------------------------------


def test_batch_beats_locking_requires_current_outline_refs(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    assert versions_repo.missing_current_locked_beats(project_id) == [1, 2]

    with pytest.raises(ValueError, match="以下集数缺少当前版本 Beats 草稿：1、2"):
        versions_repo.batch_lockable_episode_beats(project_id)

    first = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    second = versions_repo.create_episode_beats(project_id, 2, _legacy_beats())

    drafts = versions_repo.batch_lockable_episode_beats(project_id)
    assert [item["id"] for item in drafts] == [first["id"], second["id"]]

    locked = versions_repo.lock_current_episode_beats_batch(project_id)
    assert [item["status"] for item in locked] == ["locked", "locked"]
    assert versions_repo.missing_current_locked_beats(project_id) == []

    project = versions_repo.get_project(project_id)
    assert [
        item["id"] for item in versions_repo.compatible_locked_beats(project, [2, 1])
    ] == [first["id"], second["id"]]
    assert versions_repo.compatible_locked_beats(project, [0, 7]) == []

    with pytest.raises(ValueError, match="当前没有可批量锁定的 Beats 草稿"):
        versions_repo.lock_current_episode_beats_batch(project_id)

    versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    with isolated_system_database.transaction() as connection:
        connection.execute(
            update(creative_episodes)
            .where(
                creative_episodes.c.project_id == project_id,
                creative_episodes.c.stage == "outline",
                creative_episodes.c.episode_number == 1,
            )
            .values(content_json={**_outline_content(1), "core_event": "被改过的核心事件"})
        )
    with pytest.raises(ValueError, match="以下集数 Beats 引用的分集大纲已过期：1"):
        versions_repo.batch_lockable_episode_beats(project_id)


def test_beats_and_script_lock_semantics(versions_repo, isolated_system_database):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    beats = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    versions_repo.lock_episode_beats(int(beats["id"]))

    script = versions_repo.create_episode_script(project_id, 1, _script_content())
    assert script["status"] == "draft"

    with pytest.raises(ValueError, match="单集场次剧本不存在"):
        versions_repo.lock_episode_script(int(beats["id"]))
    with pytest.raises(ValueError, match="单集 Beats 不存在"):
        versions_repo.lock_episode_beats(int(script["id"]))

    locked = versions_repo.lock_episode_script(int(script["id"]))
    assert locked["status"] == "locked" and isinstance(locked["locked_at"], float)

    with pytest.raises(ValueError, match="locked script requires an explicit revision"):
        versions_repo.create_episode_script(
            project_id, 1, _script_content("第三集"), reject_if_locked=True
        )
    with pytest.raises(ValueError, match="locked Beats require an explicit revision"):
        versions_repo.create_episode_beats(project_id, 1, _legacy_beats(), reject_if_locked=True)
    with pytest.raises(ValueError, match="集数超出项目范围"):
        versions_repo.create_episode_script(project_id, 9, _script_content())
    with pytest.raises(ValueError, match="集数超出项目范围"):
        versions_repo.create_episode_beats(project_id, 9, _legacy_beats())
    with pytest.raises(ValueError, match="必须先锁定分集大纲"):
        versions_repo.create_episode_beats(
            _create_project(isolated_system_database), 1, _legacy_beats()
        )


def test_script_manual_revision_via_update_episode(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    beats = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    versions_repo.lock_episode_beats(int(beats["id"]))
    script = versions_repo.create_episode_script(project_id, 1, _script_content())

    unchanged = versions_repo.update_episode(int(script["id"]), _script_content())
    assert unchanged is not None and unchanged["id"] == script["id"]

    revised = versions_repo.update_episode(
        int(script["id"]), _script_content("第一集（手改）")
    )
    assert revised is not None
    assert revised["id"] != script["id"]
    assert revised["version"] == 2
    assert revised["parent_version_id"] == script["id"]
    assert revised["generation_meta"]["manual_revision"] is True
    assert revised["generation_meta"]["revision_source_id"] == script["id"]
    assert revised["content"]["episode_title"] == "第一集（手改）"

    versions_repo.lock_episode_script(int(revised["id"]))
    with pytest.raises(ValueError, match="已完成的版本不能原地修改"):
        versions_repo.update_episode(int(revised["id"]), _script_content("不能写"))


def test_episode_checkpoint_and_resume_series(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    saved = versions_repo.save_episode_generation_checkpoint(
        project_id, "beats", [(1, _enhanced_beats())]
    )
    assert [item["episode_number"] for item in saved] == [1]
    assert saved[0]["generation_meta"]["series_generation"] is True
    assert saved[0]["upstream_refs"]["outline_batch_id"]
    assert versions_repo.get_project(project_id)["status"] == "beats_in_progress"

    resumed = versions_repo.find_resumable_episode_series(project_id, "beats")
    assert resumed is not None
    assert resumed["completed_episode_count"] == 1
    assert [item["episode_number"] for item in resumed["episodes"]] == [1]

    assert versions_repo.find_resumable_episode_series(project_id, "script") is None
    with pytest.raises(ValueError, match="批量续写仅支持 Beats 或场次剧本"):
        versions_repo.find_resumable_episode_series(project_id, "outline")
    with pytest.raises(ValueError, match="批量检查点仅支持 Beats 或场次剧本"):
        versions_repo.save_episode_generation_checkpoint(project_id, "outline", [(1, {})])
    with pytest.raises(ValueError, match="批量检查点必须包含不重复的剧集"):
        versions_repo.save_episode_generation_checkpoint(
            project_id, "beats", [(1, _enhanced_beats()), (1, _enhanced_beats())]
        )
    with pytest.raises(ValueError, match="集数超出项目范围"):
        versions_repo.save_episode_generation_checkpoint(
            project_id, "beats", [(5, _enhanced_beats())]
        )

    # A script checkpoint requires locked Beats for the episode.
    versions_repo.lock_episode_beats(int(saved[0]["id"]))
    series = versions_repo.save_episode_generation_checkpoint(
        project_id, "script", [(1, _script_content())]
    )
    assert [item["episode_number"] for item in series] == [1]
    assert versions_repo.get_project(project_id)["status"] == "scripts_in_progress"
    assert series[0]["upstream_refs"]["beats_id"] == saved[0]["id"]


def test_characters_and_relationship_updates(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    _lock_brief(versions_repo, project_id)
    batch = versions_repo.create_character_batch(
        project_id,
        [{"name": "林昭", "role": "主角"}],
        [{"from": "林昭", "to": "陈默", "relation": "宿敌"}],
    )
    character_id = int(batch["characters"][0]["id"])
    relationship_id = int(batch["relationships"][0]["id"])

    character = versions_repo.get_character(character_id)
    assert character["content"] == {"name": "林昭", "role": "主角"}
    assert character["source_refs"] == {
        "brief_id": versions_repo.get_project(project_id)["locked_brief_id"]
    }
    assert versions_repo.get_character(999999) is None
    assert versions_repo.get_blueprint(999999) is None
    assert versions_repo.get_episode(999999) is None

    updated = versions_repo.update_character(
        character_id, {"name": "林昭", "role": "侦探"}
    )
    assert updated["content"]["role"] == "侦探"
    assert versions_repo.get_relationship(relationship_id)["content"]["relation"] == "宿敌"
    assert versions_repo.update_relationship(
        relationship_id, {"from": "林昭", "to": "陈默", "relation": "盟友"}
    )["content"]["relation"] == "盟友"

    versions_repo.lock_character_batch(project_id, batch["batch_id"])
    with pytest.raises(ValueError, match="已完成的版本不能原地修改"):
        versions_repo.update_character(character_id, {"name": "不能改"})

    fetched = versions_repo.get_character_batch(project_id, batch["batch_id"])
    assert fetched["batch_id"] == batch["batch_id"]
    assert len(fetched["characters"]) == 1 and len(fetched["relationships"]) == 1

    with pytest.raises(ValueError, match="人物关系版本至少需要一个人物"):
        versions_repo.lock_character_batch(project_id, "missing-batch")


def test_blueprint_update_is_blocked_once_locked(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    draft = versions_repo.create_blueprints(project_id, [{"content": {"title": "草稿"}}])[0]
    changed = versions_repo.update_blueprint(
        int(draft["id"]), {"title": "改过"}, risk_score=5, difference_note="  备注  "
    )
    assert changed["content"] == {"title": "改过"}
    assert changed["risk_score"] == 5.0
    assert changed["difference_note"] == "备注"
    assert versions_repo.update_blueprint(999999, {}) is None

    versions_repo.lock_brief(int(draft["id"]))
    with pytest.raises(ValueError, match="已锁定的 Brief 不能原地修改"):
        versions_repo.update_blueprint(int(draft["id"]), {"title": "不能改"})


def test_concurrent_version_allocation_and_head_updates(
    versions_repo, isolated_system_database
):
    project_id = _outline_ready_project(versions_repo, isolated_system_database)
    repository_class = type(versions_repo)
    workers = 6

    baseline = versions_repo.create_episode_beats(project_id, 1, _legacy_beats())
    versions_repo.lock_episode_beats(int(baseline["id"]))

    def create_beats(_: int) -> int:
        repo = repository_class(isolated_system_database)
        return int(repo.create_episode_beats(project_id, 1, _legacy_beats())["id"])

    def create_script(_: int) -> int:
        repo = repository_class(isolated_system_database)
        return int(repo.create_episode_script(project_id, 1, _script_content())["id"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        beats_ids = list(pool.map(create_beats, range(workers)))
        script_ids = list(pool.map(create_script, range(workers)))

    assert len(set(beats_ids)) == workers
    assert len(set(script_ids)) == workers
    beats_versions = [
        int(item["version"])
        for item in versions_repo.list_episode_versions(project_id, "beats", 1)
    ]
    script_versions = [
        int(item["version"])
        for item in versions_repo.list_episode_versions(project_id, "script", 1)
    ]
    assert sorted(beats_versions) == list(range(1, workers + 2))
    assert sorted(script_versions) == list(range(1, workers + 1))

    # Every worker retries the head CAS until it wins, so a lost increment would
    # leave the head revision below the worker count.
    def advance(_: int) -> None:
        repo = repository_class(isolated_system_database)
        for _ in range(200):
            try:
                with repo.transaction_scope() as connection:
                    row = connection.execute(
                        select(creative_resource_heads.c.revision).where(
                            creative_resource_heads.c.project_id == int(project_id),
                            creative_resource_heads.c.resource_scope == "free",
                            creative_resource_heads.c.stage == "outline",
                            creative_resource_heads.c.episode_number == 0,
                        )
                    ).first()
                    current = int(row[0]) if row is not None else 0
                    repo._advance_resource_head_conn(
                        connection,
                        project_id=project_id,
                        stage="outline",
                        resource_ref="outline-batch",
                        resource_version=1,
                        expected_revision=current,
                    )
                return
            except ValueError:
                continue
        raise AssertionError("head advance never won the revision race")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(advance, range(workers)))

    with isolated_system_database.connection() as connection:
        heads = connection.execute(
            select(
                creative_resource_heads.c.revision,
                creative_resource_heads.c.resource_ref,
            ).where(creative_resource_heads.c.project_id == int(project_id))
        ).mappings().all()
    assert len(heads) == 1
    assert heads[0]["revision"] == workers
    assert heads[0]["resource_ref"] == "outline-batch"


def test_concurrent_head_updates_with_a_stale_revision_fail_once(
    versions_repo, isolated_system_database
):
    project_id = _create_project(isolated_system_database)
    repository_class = type(versions_repo)

    def advance(_: int) -> str:
        repo = repository_class(isolated_system_database)
        try:
            with repo.transaction_scope() as connection:
                repo._advance_resource_head_conn(
                    connection,
                    project_id=project_id,
                    stage="brief",
                    resource_ref="brief-1",
                    resource_version=1,
                    expected_revision=0,
                )
            return "won"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(advance, range(4)))

    assert outcomes.count("won") == 1
    assert outcomes.count("creative resource head changed") == 3
    with isolated_system_database.connection() as connection:
        revision = connection.execute(
            select(creative_resource_heads.c.revision).where(
                creative_resource_heads.c.project_id == int(project_id),
                creative_resource_heads.c.resource_scope == "free",
                creative_resource_heads.c.stage == "brief",
                creative_resource_heads.c.episode_number == 0,
            )
        ).scalar_one()
    assert revision == 1


# ---------------------------------------------------------------------------
# type mapping spot checks
# ---------------------------------------------------------------------------


def test_jsonb_and_timestamptz_round_trip(versions_repo, isolated_system_database):
    project_id = _create_project(isolated_system_database)
    blueprint = versions_repo.create_blueprints(
        project_id, [{"content": {"title": "类型", "tags": ["a", "b"]}}]
    )[0]
    assert blueprint["content"]["tags"] == ["a", "b"]

    with isolated_system_database.connection() as connection:
        raw = connection.execute(
            select(
                creative_blueprints.c.content_json,
                creative_blueprints.c.created_at,
                creative_blueprints.c.locked_at,
            ).where(creative_blueprints.c.id == int(blueprint["id"]))
        ).one()
    assert raw[0] == {"title": "类型", "tags": ["a", "b"]}
    assert raw[2] is None
    assert epoch_seconds(raw[1]) == pytest.approx(blueprint["created_at"], abs=1e-3)

    locked = versions_repo.lock_brief(int(blueprint["id"]))
    assert isinstance(locked["locked_at"], float)
    assert locked["revision"] == 1
