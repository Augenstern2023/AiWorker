# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Real SQLite contract tests; schema SQL comes from the actual repository."""
import ast
import hashlib
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from creative.free_mode_service import FreeCreativeModeService
from creative.repository import CreativeRepository


def export(*args, **kwargs):
    # A missing implementation is an assertion failure, not a collection error.
    spec = importlib.util.find_spec("creative.production_export")
    assert spec is not None, "production exporter has not been implemented"
    return importlib.import_module("creative.production_export").export_production_package(*args, **kwargs)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def insert(conn, table, **row):
    keys = ",".join(row)
    placeholders = ",".join("?" for _ in row)
    return conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", tuple(row.values())).lastrowid


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "creative # 测试.sqlite"
    # The legacy DDL now lives in the frozen SQLite module: creative/repository.py
    # is a thin facade over it while the PostgreSQL cutover is in flight.
    tree = ast.parse(
        (
            Path(__file__).parents[1] / "creative/legacy_sqlite_repository.py"
        ).read_text(encoding="utf-8")
    )
    tables = {"creative_projects", "creative_blueprints", "creative_characters",
              "creative_relationships", "creative_episodes"}
    with sqlite3.connect(path) as conn:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                words = node.value.split()
                if words[:5] == ["CREATE", "TABLE", "IF", "NOT", "EXISTS"] and words[5] in tables:
                    conn.execute(node.value)
        insert(conn, "creative_projects", id=1, name="测试剧", creation_mode="original",
               target_episode_count=2, locked_brief_id=1, character_batch_id="cast",
               outline_batch_id="outline", created_at=10, updated_at=10)
        common = dict(project_id=1, version=1, status="locked", created_at=10, updated_at=10)
        insert(conn, "creative_blueprints", **common, id=1, candidate_group_id="brief",
               content_json=json.dumps({"premise": "寻找真相"}), source_refs_json="{}")
        for table, key, content in [
            ("creative_characters", "character_key", {"name": "小林"}),
            ("creative_relationships", "relationship_key", {"from": "小林", "to": "母亲"}),
        ]:
            insert(conn, table, **common, id=1, batch_id="cast", **{key: "a"},
                   content_json=json.dumps(content), source_refs_json='{"brief_id":1}')
        context = dict(brief_id=1, brief_version=1,
                       brief_content_fingerprint=digest({"premise": "寻找真相"}), character_batch_id="cast",
                       character_content_fingerprint=digest({
                           "characters": [{"id": 1, "version": 1, "content": {"name": "小林"}}],
                           "relationships": [{"id": 1, "version": 1, "content": {"from": "小林", "to": "母亲"}}],
                       }))
        for number in (1, 2):
            outline = {"episode": number, "summary": "发现线索"}
            oid = insert(conn, "creative_episodes", **common, stage="outline", episode_number=number,
                         batch_id="outline", content_json=json.dumps(outline),
                         upstream_refs_json=json.dumps({"brief_id": 1, "character_batch_id": "cast"}))
            refs = dict(context, outline_batch_id="outline", outline_id=oid, outline_version=1,
                        outline_content_fingerprint=digest(outline))
            beats = {"beats": [{"summary": "线索出现"}]}
            bid = insert(conn, "creative_episodes", **common, stage="beats", episode_number=number,
                         batch_id=f"beats-{number}", content_json=json.dumps(beats), upstream_refs_json=json.dumps(refs))
            refs.update(beats_id=bid, beats_version=1, beats_content_fingerprint=digest(beats))
            insert(conn, "creative_episodes", **common, stage="script", episode_number=number,
                   batch_id=f"script-{number}", content_json=json.dumps({"scenes": [{"blocks": [{"type": "action", "text": "开门"}]}]}),
                   upstream_refs_json=json.dumps(refs))
    return path


@pytest.fixture
def free_db(tmp_path):
    path = tmp_path / "free-creative.sqlite"
    repository = CreativeRepository(path)
    project = repository.create_original_project(
        seed_text="一名记者能听见旧物的记忆",
        name="旧物回声",
        target_episode_count=2,
        created_by="owner-1",
        creation_request_id="free-export-project",
        workflow_mode="free",
    )
    service = FreeCreativeModeService(repository)

    brief = service.create_brief(
        project["id"], {"premise": "记者从旧物记忆中寻找失踪者"},
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="brief",
        resource_ref=str(brief["resource"]["id"]),
        expected_head_revision=brief["head"]["revision"],
        actor_user_id="owner-1",
    )
    cast = service.create_characters(
        project["id"], [{"name": "林夏", "role": "记者"}], [],
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="characters",
        resource_ref=cast["resource"]["batch_id"],
        expected_head_revision=cast["head"]["revision"],
        actor_user_id="owner-1",
    )
    outline = service.create_outline(
        project["id"], [
            {"episode": 1, "core_event": "收到旧怀表"},
            {"episode": 2, "core_event": "找到失踪者"},
        ],
        expected_head_revision=0, actor_user_id="owner-1",
    )
    service.lock_resource(
        project_id=project["id"], stage="outline",
        resource_ref=outline["resource"]["batch_id"],
        expected_head_revision=outline["head"]["revision"],
        actor_user_id="owner-1",
    )
    for number in (1, 2):
        beats = service.create_episode_resource(
            project["id"], "beats", number,
            {"beats": [{"summary": f"第 {number} 集节拍"}]},
            expected_head_revision=0, actor_user_id="owner-1",
        )
        service.lock_resource(
            project_id=project["id"], stage="beats", episode_number=number,
            resource_ref=str(beats["resource"]["id"]),
            expected_head_revision=beats["head"]["revision"],
            actor_user_id="owner-1",
        )
        script = service.create_episode_resource(
            project["id"], "script", number,
            {"scenes": [{"heading": f"第 {number} 集场景"}]},
            expected_head_revision=0, actor_user_id="owner-1",
        )
        service.lock_resource(
            project_id=project["id"], stage="script", episode_number=number,
            resource_ref=str(script["resource"]["id"]),
            expected_head_revision=script["head"]["revision"],
            actor_user_id="owner-1",
        )
    return path, project["id"]


def test_complete_package_preserves_source_rows_and_parsed_content(db):
    package = export(db, 1)
    assert set(package) == {"schemaVersion", "sourceSystem", "project", "provenance", "brief",
                            "characters", "relationships", "outlines", "beats", "scripts",
                            "version_manifest", "fingerprint"}
    assert package["schemaVersion"] == "1.0"
    assert package["sourceSystem"] == "AiWorker"
    assert package["project"]["name"] == "测试剧"
    assert package["brief"]["content"] == json.loads(package["brief"]["content_json"])
    assert package["characters"][0]["source_refs"] == {"brief_id": 1}
    assert package["relationships"][0]["version"] == 1
    assert package["scripts"][0]["content"]["scenes"][0]["blocks"][0]["text"] == "开门"
    assert package["scripts"][0]["upstream_refs"]["beats_id"] == package["beats"][0]["id"]
    assert package["provenance"]["episode_numbers"] == [1, 2]
    assert package["provenance"]["export_scope"] == "full"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 2
    assert package["provenance"]["project_revision"] == package["project"]["revision"]
    assert package["version_manifest"]["brief"] == {"resource_ref": "1", "version": 1}
    assert package["version_manifest"]["episodes"]["1"]["script"] == {
        "resource_ref": str(package["scripts"][0]["id"]), "version": 1,
    }
    assert len(package["fingerprint"]) == 64
    assert package == export(db, 1)


def test_free_project_exports_complete_package_from_locked_resource_heads(free_db):
    path, project_id = free_db

    package = export(path, project_id)

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


def test_free_project_can_export_selected_episodes(free_db):
    path, project_id = free_db

    package = export(path, project_id, [2])

    assert package["provenance"]["export_scope"] == "partial"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 1
    assert package["provenance"]["episode_numbers"] == [2]
    assert [item["episode_number"] for item in package["outlines"]] == [2]
    assert [item["episode_number"] for item in package["beats"]] == [2]
    assert [item["episode_number"] for item in package["scripts"]] == [2]


def test_free_project_does_not_fall_back_when_current_head_is_draft(free_db):
    path, project_id = free_db
    repository = CreativeRepository(path)
    service = FreeCreativeModeService(repository)
    head = service.get_head(project_id, "brief")
    service.create_brief(
        project_id, {"premise": "尚未锁定的新方向"},
        expected_head_revision=head["revision"], actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="brief"):
        export(path, project_id)


def test_subset_is_sorted_deduplicated_and_does_not_require_other_scripts(db):
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM creative_episodes WHERE stage='script' AND episode_number=1")
    package = export(db, 1, [2, 2])
    assert package["provenance"]["episode_numbers"] == [2]
    assert package["provenance"]["export_scope"] == "partial"
    assert package["provenance"]["project_total_episodes"] == 2
    assert package["provenance"]["exported_episode_count"] == 1
    for stage in ("outlines", "beats", "scripts"):
        assert [row["episode_number"] for row in package[stage]] == [2]
    with pytest.raises(ValueError, match="script"):
        export(db, 1)


@pytest.mark.parametrize("numbers", [[], [0], [3], [-1], [True], [1.5], ["1"], "1"])
def test_invalid_episode_selection(db, numbers):
    with pytest.raises(ValueError, match="episode_numbers"):
        export(db, 1, numbers)


@pytest.mark.parametrize("sql, label", [
    ("UPDATE creative_projects SET locked_brief_id=NULL", "brief"),
    ("UPDATE creative_blueprints SET status='draft'", "brief"),
    ("UPDATE creative_projects SET character_batch_id=NULL", "character"),
    ("DELETE FROM creative_characters", "character"),
    ("UPDATE creative_relationships SET status='draft'", "relationship"),
    ("UPDATE creative_projects SET outline_batch_id=NULL", "outline"),
    ("DELETE FROM creative_episodes WHERE stage='outline'", "outline"),
    ("UPDATE creative_episodes SET status='draft' WHERE stage='beats'", "beats"),
    ("UPDATE creative_episodes SET status='draft' WHERE stage='script'", "script"),
    ("UPDATE creative_characters SET source_refs_json='{}'", "character"),
    ("UPDATE creative_relationships SET source_refs_json='{}'", "relationship"),
    ("UPDATE creative_episodes SET upstream_refs_json='{}' WHERE stage='outline'", "outline"),
    ("UPDATE creative_episodes SET upstream_refs_json='{}' WHERE stage='beats'", "beats"),
    ("UPDATE creative_episodes SET upstream_refs_json='{}' WHERE stage='script'", "script"),
    ("UPDATE creative_blueprints SET content_json='{}'", "beats"),
    ("UPDATE creative_characters SET content_json='{}'", "beats"),
    ("UPDATE creative_episodes SET content_json='{}' WHERE stage='outline'", "beats"),
    ("UPDATE creative_episodes SET content_json='{}' WHERE stage='beats'", "script"),
])
def test_missing_unlocked_or_stale_inputs_are_rejected(db, sql, label):
    with sqlite3.connect(db) as conn:
        conn.execute(sql)
    with pytest.raises(ValueError, match=label):
        export(db, 1)


def test_unknown_or_deleted_project(db):
    with pytest.raises(KeyError):
        export(db, 999)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creative_projects SET deleted=1")
    with pytest.raises(KeyError):
        export(db, 1)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        export(path, 1)
    assert not path.exists()


def test_fingerprint_ignores_timestamps_json_formatting_but_tracks_content(db):
    original = export(db, 1)
    with sqlite3.connect(db) as conn:
        for table in ("creative_projects", "creative_blueprints", "creative_characters", "creative_relationships", "creative_episodes"):
            conn.execute(f"UPDATE {table} SET updated_at=999, created_at=998")
        for row_id, content in conn.execute("SELECT id, content_json FROM creative_episodes").fetchall():
            conn.execute("UPDATE creative_episodes SET content_json=? WHERE id=?", (json.dumps(json.loads(content), indent=4), row_id))
    assert export(db, 1)["fingerprint"] == original["fingerprint"]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creative_episodes SET content_json=? WHERE stage='script' AND episode_number=1",
                     (json.dumps({"scenes": [{"blocks": [{"text": "关门"}]}]}),))
    assert export(db, 1)["fingerprint"] != original["fingerprint"]


def test_export_is_single_readonly_snapshot_during_concurrent_write(db, monkeypatch):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    real_connect = sqlite3.connect
    connections, traces = [], []
    changed = False

    def connect(*args, **kwargs):
        nonlocal changed
        assert kwargs.get("uri") is True and "mode=ro" in str(args[0])
        conn = real_connect(*args, **kwargs)
        connections.append(conn)

        def trace(sql):
            nonlocal changed
            traces.append(sql)
            if "SELECT" in sql.upper() and "creative_episodes" in sql and not changed:
                changed = True
                assert conn.in_transaction
                with real_connect(db) as writer:
                    writer.execute("UPDATE creative_projects SET name='changed'")
                    writer.execute("UPDATE creative_episodes SET content_json='{}' WHERE stage='script'")
        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    package = export(db, 1)
    assert changed and len(connections) == 1
    assert package["project"]["name"] == "测试剧"
    assert "scenes" in package["scripts"][0]["content"]
    assert sum(sql.strip().upper() == "BEGIN" for sql in traces) == 1
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")) for sql in traces)
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_fingerprint_keeps_story_fields_named_json(db):
    original = export(db, 1)["fingerprint"]
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT id, content_json FROM creative_episodes WHERE stage='script' LIMIT 1").fetchone()
        content = json.loads(row[1])
        content["production_json"] = "story-specific field, not a raw SQL duplicate"
        conn.execute("UPDATE creative_episodes SET content_json=? WHERE id=?", (json.dumps(content), row[0]))
    assert export(db, 1)["fingerprint"] != original


@pytest.mark.parametrize("stage", ["beats", "script"])
def test_newer_stale_lock_is_not_silently_replaced_by_old_version(db, stage):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM creative_episodes WHERE stage=? AND episode_number=1", (stage,)).fetchone())
        row.pop("id")
        row.update(version=2, upstream_refs_json="{}")
        insert(conn, "creative_episodes", **row)
    with pytest.raises(ValueError, match=stage):
        export(db, 1)


def test_custom_manifest_can_reuse_an_older_compatible_locked_chain(db):
    original = export(db, 1)
    manifest = original["version_manifest"]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM creative_episodes WHERE stage='beats' AND episode_number=1"
        ).fetchone())
        row.pop("id")
        row.update(version=2, upstream_refs_json="{}")
        insert(conn, "creative_episodes", **row)

    with pytest.raises(ValueError, match="beats"):
        export(db, 1)
    selected = export(db, 1, version_manifest=manifest)
    assert selected["beats"][0]["id"] == original["beats"][0]["id"]
    assert selected["scripts"][0]["id"] == original["scripts"][0]["id"]
    assert selected["version_manifest"] == manifest


def test_draft_revision_does_not_replace_locked_script(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM creative_episodes WHERE stage='script' AND episode_number=1").fetchone())
        old_id = row.pop("id")
        row.update(version=2, status="draft", upstream_refs_json="{}")
        insert(conn, "creative_episodes", **row)
    assert export(db, 1)["scripts"][0]["id"] == old_id


def test_numeric_upstream_version_change_is_rejected(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creative_blueprints SET version=2")
    with pytest.raises(ValueError, match="beats"):
        export(db, 1)


def test_malformed_json_is_rejected(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE creative_blueprints SET content_json='not-json'")
    with pytest.raises(ValueError, match="content_json"):
        export(db, 1)


def test_overview_only_outline_edit_remains_supported(db):
    with sqlite3.connect(db) as conn:
        for row_id, raw in conn.execute("SELECT id, content_json FROM creative_episodes WHERE stage='outline'").fetchall():
            content = json.loads(raw)
            content["series_overview"] = {"theme": "家人"}
            conn.execute("UPDATE creative_episodes SET content_json=? WHERE id=?", (json.dumps(content), row_id))
    assert len(export(db, 1)["scripts"]) == 2
