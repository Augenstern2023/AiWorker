# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from creative.repository import CreativeRepository
from identity.repository import IdentityRepository
from scripts.migrate_role_workflow import migrate


def _seed_legacy_root(root: Path) -> tuple[str, int]:
    runtime = root / ".runtime"
    identity = IdentityRepository(runtime / "system" / "system.db")
    identity.seed_defaults()
    admin_role = identity.get_role_by_code("system_admin")
    assert admin_role is not None
    admin = identity.create_user("bootstrap", "Bootstrap", "hash")
    identity.replace_user_roles(admin["id"], [admin_role["id"]])

    creative = CreativeRepository(runtime / "creative" / "creative.db")
    with creative._connect() as connection:
        now = 1000.0
        topic_id = connection.execute(
            """
            INSERT INTO creative_topics (
                platform, job_id, source_path, captured_at, title, score,
                confidence, score_details_json, recommendation, evidence_json,
                created_at, updated_at
            ) VALUES ('dy', 'job-1', 'source.json', '2026-07-22', 'Legacy',
                      90, 'high', '{}', 'adopt', '[]', ?, ?)
            """,
            (now, now),
        ).lastrowid
        approved_breakdown_id = connection.execute(
            """
            INSERT INTO creative_breakdowns (
                topic_id, version, status, content_json, field_sources_json,
                risks_json, created_at, updated_at, locked_at
            ) VALUES (?, 1, 'locked', '{"hook":"approved"}', '{}', '[]', ?, ?, ?)
            """,
            (topic_id, now, now, now),
        ).lastrowid
        draft_breakdown_id = connection.execute(
            """
            INSERT INTO creative_breakdowns (
                topic_id, version, parent_version_id, status, content_json,
                field_sources_json, risks_json, created_at, updated_at
            ) VALUES (?, 2, ?, 'draft', '{"hook":"draft"}', '{}', '[]', ?, ?)
            """,
            (topic_id, approved_breakdown_id, now, now),
        ).lastrowid
        project_id = connection.execute(
            """
            INSERT INTO creative_projects (
                topic_id, breakdown_id, name, target_episode_count,
                created_by, created_at, updated_at
            ) VALUES (?, ?, 'Legacy Project', 1, 'local', ?, ?)
            """,
            (topic_id, approved_breakdown_id, now, now),
        ).lastrowid
        brief_id = connection.execute(
            """
            INSERT INTO creative_blueprints (
                project_id, candidate_group_id, version, status, content_json,
                source_refs_json, generation_meta_json, locked_at,
                created_at, updated_at
            ) VALUES (?, 'brief-group', 1, 'brief_locked', '{"premise":"legacy"}',
                      '{}', '{}', ?, ?, ?)
            """,
            (project_id, now, now, now),
        ).lastrowid
        connection.execute(
            "UPDATE creative_projects SET locked_brief_id = ? WHERE id = ?",
            (brief_id, project_id),
        )
        connection.execute(
            """
            INSERT INTO creative_characters (
                project_id, batch_id, version, character_key, status,
                content_json, source_refs_json, generation_meta_json,
                locked_at, created_at, updated_at
            ) VALUES (?, 'characters-v1', 1, 'lead', 'locked', '{"name":"Lead"}',
                      ?, '{}', ?, ?, ?)
            """,
            (project_id, json.dumps({"brief_id": brief_id}), now, now, now),
        )
        connection.execute(
            """
            INSERT INTO creative_relationships (
                project_id, batch_id, version, relationship_key, status,
                content_json, source_refs_json, generation_meta_json,
                locked_at, created_at, updated_at
            ) VALUES (?, 'characters-v1', 1, 'lead>rival', 'locked',
                      '{"from":"Lead","to":"Rival"}', ?, '{}', ?, ?, ?)
            """,
            (project_id, json.dumps({"brief_id": brief_id}), now, now, now),
        )
        connection.execute(
            "UPDATE creative_projects SET character_batch_id = 'characters-v1' WHERE id = ?",
            (project_id,),
        )
        connection.execute(
            """
            INSERT INTO creative_episodes (
                project_id, stage, episode_number, batch_id, version, status,
                content_json, upstream_refs_json, generation_meta_json,
                locked_at, created_at, updated_at
            ) VALUES (?, 'outline', 1, 'outline-v1', 1, 'locked',
                      '{"episode":1}', ?, '{}', ?, ?, ?)
            """,
            (
                project_id,
                json.dumps(
                    {"brief_id": brief_id, "character_batch_id": "characters-v1"}
                ),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE creative_projects SET outline_batch_id = 'outline-v1' WHERE id = ?",
            (project_id,),
        )
        beats_id = connection.execute(
            """
            INSERT INTO creative_episodes (
                project_id, stage, episode_number, batch_id, version, status,
                content_json, upstream_refs_json, generation_meta_json,
                locked_at, created_at, updated_at
            ) VALUES (?, 'beats', 1, 'beats-v1', 1, 'locked',
                      '{"beats":[]}', ?, '{}', ?, ?, ?)
            """,
            (
                project_id,
                json.dumps(
                    {
                        "brief_id": brief_id,
                        "character_batch_id": "characters-v1",
                        "outline_batch_id": "outline-v1",
                    }
                ),
                now,
                now,
                now,
            ),
        ).lastrowid
    assert draft_breakdown_id and beats_id
    return admin["id"], int(project_id)


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_migration_maps_legacy_resources_and_creates_one_next_task(tmp_path: Path):
    admin_id, project_id = _seed_legacy_root(tmp_path)

    report = migrate(tmp_path, admin_id)

    assert report.approved_submissions == 5
    assert report.draft_submissions == 1
    assert report.next_tasks == 1
    assert len(report.backups) == 2
    assert all(path.exists() for path in report.backups)
    system_db = tmp_path / ".runtime/system/system.db"
    creative_db = tmp_path / ".runtime/creative/creative.db"
    with sqlite3.connect(system_db) as connection:
        connection.row_factory = sqlite3.Row
        submissions = connection.execute(
            "SELECT resource_type, status, content_snapshot_json FROM workflow_submissions"
        ).fetchall()
        assert {(row["resource_type"], row["status"]) for row in submissions} == {
            ("breakdown", "approved"),
            ("breakdown", "draft"),
            ("brief", "approved"),
            ("characters", "approved"),
            ("outline", "approved"),
            ("beats", "approved"),
        }
        assert all(
            json.loads(row["content_snapshot_json"])["migration"]["legacy_ids"]
            for row in submissions
        )
        character_snapshot = next(
            json.loads(row["content_snapshot_json"])
            for row in submissions
            if row["resource_type"] == "characters"
        )
        assert character_snapshot["characters"][0]["character_key"] == "lead"
        assert character_snapshot["relationships"][0]["relationship_key"] == (
            "lead>rival"
        )
        tasks = connection.execute(
            "SELECT task_type, role_code, status FROM workflow_tasks "
            "WHERE project_id = ? AND status = 'pool'",
            (str(project_id),),
        ).fetchall()
        assert [tuple(row) for row in tasks] == [("delivery", "system_admin", "pool")]
    with sqlite3.connect(creative_db) as connection:
        assert connection.execute(
            "SELECT created_by FROM creative_projects WHERE id = ?", (project_id,)
        ).fetchone()[0] == admin_id
        assert connection.execute(
            "SELECT status FROM creative_breakdowns WHERE version = 1"
        ).fetchone()[0] == "approved"
        assert connection.execute(
            "SELECT status FROM creative_blueprints WHERE id = "
            "(SELECT locked_brief_id FROM creative_projects WHERE id = ?)",
            (project_id,),
        ).fetchone()[0] == "approved"


def test_migration_is_idempotent_and_dry_run_is_read_only(tmp_path: Path):
    admin_id, _ = _seed_legacy_root(tmp_path)
    system_db = tmp_path / ".runtime/system/system.db"
    creative_db = tmp_path / ".runtime/creative/creative.db"
    before = (system_db.read_bytes(), creative_db.read_bytes())

    dry_report = migrate(tmp_path, admin_id, dry_run=True)

    assert dry_report.dry_run is True
    assert dry_report.backups == ()
    assert (system_db.read_bytes(), creative_db.read_bytes()) == before

    migrate(tmp_path, admin_id)
    counts = (
        _table_count(system_db, "workflow_submissions"),
        _table_count(system_db, "workflow_tasks"),
    )
    second = migrate(tmp_path, admin_id)
    assert second.already_applied is True
    assert (
        _table_count(system_db, "workflow_submissions"),
        _table_count(system_db, "workflow_tasks"),
    ) == counts


def test_migration_failure_restores_both_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    admin_id, _ = _seed_legacy_root(tmp_path)
    system_db = tmp_path / ".runtime/system/system.db"
    creative_db = tmp_path / ".runtime/creative/creative.db"
    before = (system_db.read_bytes(), creative_db.read_bytes())

    def fail_after_creative_write(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(
        "scripts.migrate_role_workflow._migrate_system_database",
        fail_after_creative_write,
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        migrate(tmp_path, admin_id)

    assert (system_db.read_bytes(), creative_db.read_bytes()) == before


def test_migration_blocks_on_missing_current_project_pointer(tmp_path: Path):
    admin_id, project_id = _seed_legacy_root(tmp_path)
    creative_db = tmp_path / ".runtime/creative/creative.db"
    system_db = tmp_path / ".runtime/system/system.db"
    with sqlite3.connect(creative_db) as connection:
        connection.execute(
            "UPDATE creative_projects SET outline_batch_id = 'missing-outline' WHERE id = ?",
            (project_id,),
        )
    before = (system_db.read_bytes(), creative_db.read_bytes())

    with pytest.raises(RuntimeError, match="outline_batch_id"):
        migrate(tmp_path, admin_id)

    assert (system_db.read_bytes(), creative_db.read_bytes()) == before
