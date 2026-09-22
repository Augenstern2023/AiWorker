# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Contract tests for the production snapshot reader against PostgreSQL.

The reader is the production-package snapshot boundary: one borrowed connection,
the frozen SQLite row shape, and the exact manifest/fingerprint contract the
handoff flow consumes. Every fixture here builds its locked inputs through the
real ``PostgresCreativeRepository`` on the migrated creative schema, so the
tests exercise the reader against the same store production uses.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select, text, update

from creative.database_schema import (
    creative_blueprints,
    creative_characters,
    creative_episodes,
    creative_projects,
    creative_relationships,
)
from creative.free_mode_service import FreeCreativeModeService
from creative.production_export import (
    _digest,
    _fingerprint_payload,
    _row,
    export_production_package,
    preview_production_package,
)
from creative.video_parameter_recommendation import (
    build_video_parameter_source,
    fallback_recommendation,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def test_snapshot_row_normalizes_postgresql_uuid_for_fingerprinting():
    project_uid = uuid.uuid4()

    row = _row({"id": 26, "project_uid": project_uid, "name": "自由创作项目"})

    assert row["project_uid"] == str(project_uid)
    assert len(_digest(_fingerprint_payload({"project": row}))) == 64


def _outline_content(number: int) -> dict:
    return {
        "episode": number,
        "core_event": f"主角发现线索 {number}",
        "conflict": f"冲突 {number}",
        "character_progress": f"推进 {number}",
        "highlight": f"亮点 {number}",
        "reversal": f"反转 {number}",
        "cliffhanger": f"悬念 {number}",
    }


def _script_content(number: int) -> dict:
    return {
        "episode_title": f"第 {number} 集",
        "scenes": [{
            "sequence_no": 1,
            "scene_no": 1,
            "location": "书房",
            "characters": ["小林"],
            "props": ["信笺"],
            "blocks": [
                {"type": "action", "text": "小林展开信笺。"},
                {"type": "dialogue", "speaker": "小林", "text": "我要寻找真相。"},
            ],
        }],
    }


def lock_sequential_chain(repository, project_id: int, *, episodes: int = 2) -> None:
    """Lock brief -> cast -> outline -> beats -> script through the repository."""

    brief = repository.create_blueprints(
        project_id, [{"content": {"premise": "寻找真相"}}],
    )[0]
    repository.lock_brief(int(brief["id"]), expected_revision=0)
    batch = repository.create_character_batch(
        project_id,
        [{"name": "小林", "role": "记者"}],
        [{"from": "小林", "to": "母亲", "relation": "母女"}],
    )
    repository.lock_character_batch(project_id, batch["batch_id"])
    outline = repository.create_outline_batch(
        project_id,
        [_outline_content(number) for number in range(1, episodes + 1)],
        "调查、受阻、反击",
    )
    repository.lock_outline_batch(project_id, outline["batch_id"])
    for number in range(1, episodes + 1):
        beats = repository.create_episode_beats(
            project_id, number, {"beats": [{"summary": f"第 {number} 集节拍"}]},
        )
        repository.lock_episode_beats(int(beats["id"]))
        script = repository.create_episode_script(
            project_id, number, _script_content(number),
        )
        repository.lock_episode_script(int(script["id"]))
    complete_video_parameter_recommendation(repository, project_id)


def complete_video_parameter_recommendation(repository, project_id: int) -> None:
    source = build_video_parameter_source(repository.get_project(project_id))
    repository.queue_video_parameter_recommendation(
        project_id, created_by="owner-1"
    )
    repository.complete_video_parameter_recommendation(
        project_id,
        source_fingerprint=source["fingerprint"],
        source_episode_numbers=source["episode_numbers"],
        source_complete=source["complete"],
        recommendation=fallback_recommendation(source),
    )


def _create_project(repository, *, name: str, request_id: str, episode_count: int = 2,
                    workflow_mode: str = "sequential") -> int:
    project = repository.create_original_project(
        seed_text="一名记者能听见旧物的记忆",
        name=name,
        target_episode_count=episode_count,
        created_by="owner-1",
        creation_request_id=request_id,
        workflow_mode=workflow_mode,
    )
    return int(repository.mark_project_bootstrap_status(project["id"], "ready")["id"])


@pytest.fixture
def sequential_project(postgres_creative) -> int:
    """A project with a locked brief, cast, outline, beats and scripts."""

    project_id = _create_project(
        postgres_creative, name="测试剧", request_id="production-export-sequential",
    )
    lock_sequential_chain(postgres_creative, project_id, episodes=2)
    return project_id


@pytest.fixture
def free_project(postgres_creative) -> int:
    """A free-mode project whose locked resources are reached through its heads."""

    repository = postgres_creative
    project_id = _create_project(
        repository, name="旧物回声", request_id="production-export-free",
        workflow_mode="free",
    )
    service = FreeCreativeModeService(repository)
    brief = service.create_brief(
        project_id, {"premise": "记者从旧物记忆中寻找失踪者"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project_id, stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    cast = service.create_characters(
        project_id, [{"name": "林夏", "role": "记者"}], [],
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project_id, stage="characters",
        resource_ref=cast["resource"]["batch_id"],
        expected_head_revision=cast["head"]["revision"],
        actor_user_id="owner-1",
    )
    outline = service.create_outline(
        project_id, [
            {"episode": 1, "core_event": "收到旧怀表"},
            {"episode": 2, "core_event": "找到失踪者"},
        ],
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project_id, stage="outline",
        resource_ref=outline["resource"]["batch_id"],
        expected_head_revision=outline["head"]["revision"],
        actor_user_id="owner-1",
    )
    for number in (1, 2):
        beats = service.create_episode_resource(
            project_id, "beats", number,
            {"beats": [{"summary": f"第 {number} 集节拍"}]},
            expected_head_revision=0, actor_user_id="owner-1",
        )
        service.lock_resource(
            project_id=project_id, stage="beats", episode_number=number,
            resource_ref=str(beats["resource"]["id"]),
            expected_head_revision=beats["head"]["revision"],
            actor_user_id="owner-1",
        )
        script = service.create_episode_resource(
            project_id, "script", number,
            {"scenes": [{"heading": f"第 {number} 集场景"}]},
            expected_head_revision=0, actor_user_id="owner-1",
        )
        service.lock_resource(
            project_id=project_id, stage="script", episode_number=number,
            resource_ref=str(script["resource"]["id"]),
            expected_head_revision=script["head"]["revision"],
            actor_user_id="owner-1",
        )
    complete_video_parameter_recommendation(repository, project_id)
    return project_id


def test_complete_package_preserves_source_rows_and_parsed_content(
    postgres_creative, sequential_project,
):
    package = export_production_package(postgres_creative, sequential_project)
    assert set(package) == {"schemaVersion", "sourceSystem", "project", "provenance", "brief",
                            "characters", "relationships", "outlines", "beats", "scripts",
                            "video_parameter_recommendation", "version_manifest", "fingerprint"}
    assert package["schemaVersion"] == "1.0"
    assert package["sourceSystem"] == "AiWorker"
    assert package["project"]["name"] == "测试剧"
    assert package["brief"]["content"] == json.loads(package["brief"]["content_json"])
    assert package["characters"][0]["source_refs"] == {
        "brief_id": package["brief"]["id"],
    }
    assert package["relationships"][0]["version"] == 1
    assert package["scripts"][0]["content"]["scenes"][0]["blocks"][1]["text"] == "我要寻找真相。"
    assert package["scripts"][0]["upstream_refs"]["beats_id"] == package["beats"][0]["id"]
    assert package["provenance"]["episode_numbers"] == [1, 2]
    assert package["provenance"]["export_scope"] == "full"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 2
    assert package["provenance"]["project_revision"] == package["project"]["revision"]
    assert package["version_manifest"]["brief"] == {
        "resource_ref": str(package["brief"]["id"]), "version": 1,
    }
    assert package["version_manifest"]["episodes"]["1"]["script"] == {
        "resource_ref": str(package["scripts"][0]["id"]), "version": 1,
    }
    assert len(package["fingerprint"]) == 64
    # Deterministic, and serializable: the handoff service writes this dict.
    assert package == export_production_package(postgres_creative, sequential_project)
    assert json.loads(json.dumps(package, ensure_ascii=False)) == package


def test_snapshot_rows_keep_legacy_epoch_timestamps_and_json_text(
    postgres_creative, sequential_project,
):
    package = export_production_package(postgres_creative, sequential_project)

    assert isinstance(package["project"]["created_at"], float)
    assert isinstance(package["project"]["updated_at"], float)
    assert isinstance(package["brief"]["created_at"], float)
    assert isinstance(package["brief"]["locked_at"], float)
    # The raw *_json column stays text exactly as SQLite stored it, and the
    # decoded alias is the parsed object.
    assert isinstance(package["brief"]["content_json"], str)
    assert package["brief"]["content"] == json.loads(package["brief"]["content_json"])
    assert package["characters"][0]["source_refs_json"] == json.dumps(
        package["characters"][0]["source_refs"], ensure_ascii=False,
    )
    assert not any(
        isinstance(value, datetime)
        for value in package["scripts"][0].values()
    )


def test_borrowed_connection_is_the_only_read_path(
    postgres_creative, sequential_project,
):
    """The snapshot must read through the caller's connection and release it untouched."""

    repository = postgres_creative
    original_name = repository.get_project(sequential_project)["name"]
    with repository.connection_scope() as connection:
        package = export_production_package(
            repository, sequential_project, connection=connection,
        )
        assert package["project"]["name"] == original_name
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == sequential_project)
            .values(name="未提交的新名字")
        )
        # Only the caller's own transaction can see this, so a snapshot that
        # opened its own connection could not report the uncommitted rename.
        uncommitted = export_production_package(
            repository, sequential_project, connection=connection,
        )
        assert uncommitted["project"]["name"] == "未提交的新名字"
        # The reader never closed the borrowed connection.
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    # ... and never committed it: the rename is gone once its owner releases it.
    assert repository.get_project(sequential_project)["name"] == original_name


def test_free_project_exports_complete_package_from_locked_resource_heads(
    postgres_creative, free_project,
):
    package = export_production_package(postgres_creative, free_project)

    assert package["project"]["workflow_mode"] == "free"
    assert package["project"]["locked_brief_id"] is None
    assert package["brief"]["resource_scope"] == "free"
    assert package["characters"][0]["content"]["name"] == "林夏"
    assert package["relationships"] == []
    assert [item["episode_number"] for item in package["outlines"]] == [1, 2]
    assert [item["episode_number"] for item in package["beats"]] == [1, 2]
    assert [item["episode_number"] for item in package["scripts"]] == [1, 2]
    assert package["provenance"]["workflow_mode"] == "free"
    heads = package["provenance"]["resource_heads"]
    assert [(head["stage"], head["episode_number"]) for head in heads] == [
        ("brief", 0), ("characters", 0), ("outline", 0),
        ("beats", 1), ("beats", 2), ("script", 1), ("script", 2),
    ]


def test_free_project_can_export_selected_episodes(postgres_creative, free_project):
    package = export_production_package(postgres_creative, free_project, [2])

    assert package["provenance"]["export_scope"] == "partial"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 1
    assert package["provenance"]["episode_numbers"] == [2]
    assert [item["episode_number"] for item in package["outlines"]] == [2]
    assert [item["episode_number"] for item in package["beats"]] == [2]
    assert [item["episode_number"] for item in package["scripts"]] == [2]


def test_free_project_does_not_fall_back_when_current_head_is_draft(
    postgres_creative, free_project,
):
    repository = postgres_creative
    service = FreeCreativeModeService(repository)
    head = service.get_head(free_project, "brief")
    service.create_brief(
        free_project, {"premise": "尚未锁定的新方向"},
        expected_head_revision=head["revision"], actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="brief"):
        export_production_package(repository, free_project)


def test_subset_is_sorted_deduplicated_and_requires_current_full_series_analysis(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    package = export_production_package(repository, sequential_project, [2, 2])
    assert package["provenance"]["episode_numbers"] == [2]
    assert package["provenance"]["export_scope"] == "partial"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 1
    for stage in ("outlines", "beats", "scripts"):
        assert [row["episode_number"] for row in package[stage]] == [2]

    with repository.transaction_scope() as connection:
        connection.execute(
            delete(creative_episodes).where(
                creative_episodes.c.stage == "script",
                creative_episodes.c.episode_number == 1,
            )
        )
    with pytest.raises(ValueError, match="video_parameters"):
        export_production_package(repository, sequential_project, [2])
    with pytest.raises(ValueError, match="script"):
        export_production_package(repository, sequential_project)


@pytest.mark.parametrize("numbers", [[], [0], [3], [-1], [True], [1.5], ["1"], "1"])
def test_invalid_episode_selection(postgres_creative, sequential_project, numbers):
    with pytest.raises(ValueError, match="episode_numbers"):
        export_production_package(postgres_creative, sequential_project, numbers)


def test_unknown_or_deleted_project(postgres_creative, sequential_project):
    repository = postgres_creative
    with pytest.raises(KeyError):
        export_production_package(repository, 999)
    with repository.transaction_scope() as connection:
        connection.execute(
            update(creative_projects)
            .where(creative_projects.c.id == sequential_project)
            .values(deleted=True)
        )
    with pytest.raises(KeyError):
        export_production_package(repository, sequential_project)


def _set_project(**values):
    return lambda connection: connection.execute(
        update(creative_projects).values(**values)
    )


def _set_table(table, values, **where):
    def mutate(connection):
        statement = update(table).values(**values)
        conditions = [table.c[name] == value for name, value in where.items()]
        if conditions:
            statement = statement.where(*conditions)
        connection.execute(statement)

    return mutate


@pytest.mark.parametrize("mutate, label", [
    (_set_project(locked_brief_id=None), "brief"),
    (_set_table(creative_blueprints, {"status": "draft"}), "brief"),
    (_set_table(creative_blueprints, {"content_json": None}), "content_json"),
    (_set_project(character_batch_id=None), "character"),
    (lambda connection: connection.execute(delete(creative_characters)), "character"),
    (_set_table(creative_relationships, {"status": "draft"}), "relationship"),
    (_set_project(outline_batch_id=None), "outline"),
    (
        lambda connection: connection.execute(
            delete(creative_episodes).where(creative_episodes.c.stage == "outline")
        ),
        "outline",
    ),
    (_set_table(creative_episodes, {"status": "draft"}, stage="beats"), "beats"),
    (_set_table(creative_episodes, {"status": "draft"}, stage="script"), "script"),
    (_set_table(creative_characters, {"source_refs_json": {}}), "character"),
    (_set_table(creative_relationships, {"source_refs_json": {}}), "relationship"),
    (_set_table(creative_episodes, {"upstream_refs_json": {}}, stage="outline"), "outline"),
    (_set_table(creative_episodes, {"upstream_refs_json": {}}, stage="beats"), "beats"),
    (_set_table(creative_episodes, {"upstream_refs_json": {}}, stage="script"), "script"),
    (_set_table(creative_blueprints, {"content_json": {}}), "beats"),
    (_set_table(creative_characters, {"content_json": {}}), "beats"),
    (_set_table(creative_episodes, {"content_json": {}}, stage="outline"), "beats"),
    (_set_table(creative_episodes, {"content_json": {}}, stage="beats"), "script"),
])
def test_missing_unlocked_or_stale_inputs_are_rejected(
    postgres_creative, sequential_project, mutate, label,
):
    repository = postgres_creative
    with repository.transaction_scope() as connection:
        mutate(connection)
    with pytest.raises(ValueError, match=label):
        export_production_package(repository, sequential_project)


def test_fingerprint_ignores_timestamps_but_tracks_content(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    original = export_production_package(repository, sequential_project)
    later = datetime(2031, 1, 1, tzinfo=timezone.utc)
    with repository.transaction_scope() as connection:
        for table in (
            creative_projects, creative_blueprints, creative_characters,
            creative_relationships, creative_episodes,
        ):
            connection.execute(update(table).values(created_at=later, updated_at=later))
    assert export_production_package(
        repository, sequential_project,
    )["fingerprint"] == original["fingerprint"]

    with repository.transaction_scope() as connection:
        connection.execute(
            update(creative_episodes)
            .where(
                creative_episodes.c.stage == "script",
                creative_episodes.c.episode_number == 1,
            )
            .values(content_json={"scenes": [{"blocks": [{"text": "关门"}]}]})
        )
    complete_video_parameter_recommendation(repository, sequential_project)
    assert export_production_package(
        repository, sequential_project,
    )["fingerprint"] != original["fingerprint"]


def test_fingerprint_keeps_story_fields_named_json(postgres_creative, sequential_project):
    repository = postgres_creative
    original = export_production_package(repository, sequential_project)["fingerprint"]
    with repository.transaction_scope() as connection:
        row = connection.execute(
            select(creative_episodes.c.id, creative_episodes.c.content_json)
            .where(creative_episodes.c.stage == "script")
            .order_by(creative_episodes.c.id)
            .limit(1)
        ).mappings().one()
        content = dict(row["content_json"])
        content["production_json"] = "story-specific field, not a raw SQL duplicate"
        connection.execute(
            update(creative_episodes)
            .where(creative_episodes.c.id == row["id"])
            .values(content_json=content)
        )
    complete_video_parameter_recommendation(repository, sequential_project)
    assert export_production_package(
        repository, sequential_project,
    )["fingerprint"] != original


def test_newer_stale_lock_is_not_silently_replaced_by_old_version(
    postgres_creative, sequential_project,
):
    """A newer locked outline that no longer matches its beats must fail closed."""

    repository = postgres_creative
    outline = repository.create_outline_batch(
        sequential_project,
        [_outline_content(1), _outline_content(2)],
        "调查、受阻、反击",
    )
    repository.lock_outline_batch(sequential_project, outline["batch_id"])

    with pytest.raises(ValueError, match="beats"):
        export_production_package(repository, sequential_project)


def test_custom_manifest_can_reuse_an_older_compatible_locked_chain(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    original = export_production_package(repository, sequential_project)
    manifest = original["version_manifest"]
    outline = repository.create_outline_batch(
        sequential_project,
        [_outline_content(1), _outline_content(2)],
        "调查、受阻、反击",
    )
    repository.lock_outline_batch(sequential_project, outline["batch_id"])

    with pytest.raises(ValueError, match="beats"):
        export_production_package(repository, sequential_project)
    selected = export_production_package(
        repository, sequential_project, version_manifest=manifest,
    )
    assert selected["beats"][0]["id"] == original["beats"][0]["id"]
    assert selected["scripts"][0]["id"] == original["scripts"][0]["id"]
    assert selected["version_manifest"] == manifest


def test_custom_manifest_rejects_a_version_that_no_longer_exists(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    manifest = json.loads(json.dumps(
        export_production_package(repository, sequential_project)["version_manifest"]
    ))
    manifest["brief"]["version"] = 99

    with pytest.raises(ValueError, match="brief"):
        export_production_package(
            repository, sequential_project, version_manifest=manifest,
        )


def test_draft_revision_does_not_replace_locked_script(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    locked = export_production_package(repository, sequential_project)
    draft = repository.create_episode_script(
        sequential_project, 1, _script_content(1),
    )
    assert draft["version"] == 2
    assert draft["status"] == "draft"

    assert export_production_package(
        repository, sequential_project,
    )["scripts"][0]["id"] == locked["scripts"][0]["id"]


def test_overview_only_outline_edit_remains_supported(
    postgres_creative, sequential_project,
):
    repository = postgres_creative
    with repository.transaction_scope() as connection:
        rows = connection.execute(
            select(creative_episodes.c.id, creative_episodes.c.content_json)
            .where(creative_episodes.c.stage == "outline")
        ).mappings().all()
        for row in rows:
            content = dict(row["content_json"])
            content["series_overview"] = {"theme": "家人"}
            connection.execute(
                update(creative_episodes)
                .where(creative_episodes.c.id == row["id"])
                .values(content_json=content)
            )

    assert len(export_production_package(repository, sequential_project)["scripts"]) == 2


def test_fingerprint_matches_the_producers_upstream_fingerprints(
    postgres_creative, sequential_project,
):
    """The reader re-derives the same fingerprint the repository saved upstream."""

    package = export_production_package(postgres_creative, sequential_project)
    beats = package["beats"][0]
    script = package["scripts"][0]
    assert beats["upstream_refs"]["brief_content_fingerprint"] == digest(
        package["brief"]["content"]
    )
    assert script["upstream_refs"]["beats_content_fingerprint"] == digest(
        beats["content"]
    )
    assert script["upstream_refs"]["outline_content_fingerprint"] == digest(
        package["outlines"][0]["content"]
    )


def test_export_service_preview_uses_the_real_snapshot_reader(
    postgres_creative, sequential_project, tmp_path,
):
    """The handoff service's own call site resolves against PostgreSQL."""

    from unittest.mock import Mock

    import creative.production_exports as production_exports

    service = production_exports.ProductionExportService(
        root=tmp_path, repository=postgres_creative, audit_service=Mock(),
    )
    result = service.preview(sequential_project)

    assert result["ready"] is True
    assert len(result["fingerprint"]) == 64
    assert result["selection"] == {
        "export_scope": "full",
        "episode_numbers": [1, 2],
        "project_total_episodes": 2,
        "exported_episode_count": 2,
    }
