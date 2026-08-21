# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable

from creative.outline_overview import normalize_series_overview, read_series_overview


def canonical_request_hash(payload: Any) -> str:
    """Return the stable SHA-256 for a JSON-compatible request payload."""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
from creative.script_service import normalize_script_content, validate_beats, validate_script


class DuplicateTopicError(ValueError):
    """Raised when a title has already been collected into the topic store."""


class CreativeRepository:
    _NON_GENRE_LABELS = {
        "短剧", "漫剧", "AI漫剧", "真人剧", "AI剧", "红果", "未分类",
        "新剧榜", "推荐榜", "热播榜", "热搜榜", "收藏榜", "预约榜", "必看榜", "高分臻果榜",
    }
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_project_table_if_needed(self.db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _migrate_project_table_if_needed(db_path: str | Path) -> None:
        """Rebuild legacy project constraints so originals can omit source records."""
        conn = sqlite3.connect(db_path)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_projects'"
            ).fetchone()
            if table is None:
                return
            columns = {
                row[1]: row for row in conn.execute("PRAGMA table_info(creative_projects)")
            }
            topic_is_not_null = bool(columns.get("topic_id") and columns["topic_id"][3])
            if not topic_is_not_null and "creation_mode" in columns:
                return

            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            for index in (
                "idx_creative_projects_topic",
                "uq_creative_projects_workflow_task",
                "uq_creative_projects_creation_workflow_task",
                "uq_creative_projects_creation_request",
            ):
                conn.execute(f"DROP INDEX IF EXISTS {index}")
            conn.execute("""
                CREATE TABLE creative_projects_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER,
                    breakdown_id INTEGER,
                    name TEXT NOT NULL,
                    genre TEXT NOT NULL DEFAULT '',
                    target_platform TEXT NOT NULL DEFAULT '',
                    target_audience TEXT NOT NULL DEFAULT '',
                    target_episode_count INTEGER NOT NULL DEFAULT 20,
                    episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
                    status TEXT NOT NULL DEFAULT 'created',
                    locked_brief_id INTEGER,
                    character_batch_id TEXT,
                    outline_batch_id TEXT,
                    workflow_task_id TEXT,
                    creation_workflow_task_id TEXT,
                    creation_request_id TEXT,
                    created_by TEXT NOT NULL DEFAULT 'local',
                    deleted INTEGER NOT NULL DEFAULT 0,
                    creation_mode TEXT NOT NULL DEFAULT 'imitation'
                        CHECK (creation_mode IN ('imitation', 'original')),
                    bootstrap_status TEXT NOT NULL DEFAULT 'ready'
                        CHECK (bootstrap_status IN ('pending', 'ready', 'failed')),
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES creative_topics(id),
                    FOREIGN KEY(breakdown_id) REFERENCES creative_breakdowns(id),
                    CHECK (
                        (creation_mode = 'imitation' AND topic_id IS NOT NULL AND breakdown_id IS NOT NULL)
                        OR
                        (creation_mode = 'original' AND topic_id IS NULL AND breakdown_id IS NULL)
                    )
                )
            """)
            conn.execute("""
                INSERT INTO creative_projects_new (
                    id, topic_id, breakdown_id, name, genre, target_platform,
                    target_audience, target_episode_count, episode_duration_seconds,
                    status, locked_brief_id, character_batch_id, outline_batch_id,
                    workflow_task_id, creation_workflow_task_id, creation_request_id,
                    created_by, deleted, creation_mode, bootstrap_status, created_at, updated_at
                )
                SELECT
                    id, topic_id, breakdown_id, name, genre, target_platform,
                    target_audience, target_episode_count, episode_duration_seconds,
                    status, locked_brief_id, character_batch_id, outline_batch_id,
                    workflow_task_id, creation_workflow_task_id, creation_request_id,
                    created_by, deleted, 'imitation', 'ready', created_at, updated_at
                FROM creative_projects
            """)
            conn.execute("DROP TABLE creative_projects")
            conn.execute("ALTER TABLE creative_projects_new RENAME TO creative_projects")
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("creative project migration introduced foreign key errors")
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, job_id TEXT NOT NULL, source_path TEXT NOT NULL,
                    captured_at TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', category TEXT,
                    rank_type TEXT NOT NULL DEFAULT '', rank_source TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending_review', score REAL NOT NULL,
                    confidence TEXT NOT NULL, score_details_json TEXT NOT NULL,
                    recommendation TEXT NOT NULL, evidence_json TEXT NOT NULL,
                    decision_note TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(platform, job_id, source_path, title)
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(creative_topics)").fetchall()}
            if "source_platforms_json" not in columns:
                conn.execute("ALTER TABLE creative_topics ADD COLUMN source_platforms_json TEXT NOT NULL DEFAULT '[]'")
            if "rank_type" not in columns:
                conn.execute("ALTER TABLE creative_topics ADD COLUMN rank_type TEXT NOT NULL DEFAULT ''")
            if "rank_source" not in columns:
                conn.execute("ALTER TABLE creative_topics ADD COLUMN rank_source TEXT NOT NULL DEFAULT ''")
            conn.execute("""
                UPDATE creative_topics
                SET rank_source = CASE
                    WHEN platform = 'hg'
                         AND TRIM(rank_type) <> ''
                         AND rank_type NOT LIKE '全部%'
                         AND rank_type NOT LIKE '真人剧%'
                         AND rank_type NOT LIKE '漫剧%'
                         AND rank_type NOT LIKE 'AI剧%'
                        THEN '全部' || rank_type
                    WHEN platform = 'hg' AND TRIM(rank_type) = '' THEN '全部'
                    ELSE rank_type
                END
                WHERE TRIM(rank_source) = ''
            """)
            if "description" not in columns:
                conn.execute("ALTER TABLE creative_topics ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            labels = tuple(self._NON_GENRE_LABELS)
            placeholders = ",".join("?" for _ in labels)
            conn.execute(
                f"UPDATE creative_topics SET category = '待补充' WHERE category IN ({placeholders})",
                labels,
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_breakdowns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL, version INTEGER NOT NULL,
                    parent_version_id INTEGER, status TEXT NOT NULL DEFAULT 'draft',
                    content_json TEXT NOT NULL, field_sources_json TEXT NOT NULL,
                    risks_json TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    locked_at REAL, UNIQUE(topic_id, version)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER, breakdown_id INTEGER,
                    name TEXT NOT NULL, genre TEXT NOT NULL DEFAULT '',
                    target_platform TEXT NOT NULL DEFAULT '', target_audience TEXT NOT NULL DEFAULT '',
                    target_episode_count INTEGER NOT NULL DEFAULT 20,
                    episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
                    status TEXT NOT NULL DEFAULT 'created',
                    locked_brief_id INTEGER, character_batch_id TEXT, outline_batch_id TEXT,
                    workflow_task_id TEXT, creation_workflow_task_id TEXT,
                    creation_request_id TEXT,
                    created_by TEXT NOT NULL DEFAULT 'local', deleted INTEGER NOT NULL DEFAULT 0,
                    creation_mode TEXT NOT NULL DEFAULT 'imitation'
                        CHECK (creation_mode IN ('imitation', 'original')),
                    bootstrap_status TEXT NOT NULL DEFAULT 'ready'
                        CHECK (bootstrap_status IN ('pending', 'ready', 'failed')),
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES creative_topics(id),
                    FOREIGN KEY(breakdown_id) REFERENCES creative_breakdowns(id),
                    CHECK (
                        (creation_mode = 'imitation' AND topic_id IS NOT NULL AND breakdown_id IS NOT NULL)
                        OR
                        (creation_mode = 'original' AND topic_id IS NULL AND breakdown_id IS NULL)
                    )
                )
            """)
            project_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(creative_projects)").fetchall()
            }
            if "revision" not in project_columns:
                conn.execute(
                    "ALTER TABLE creative_projects "
                    "ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_blueprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, candidate_group_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, parent_version_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'draft', content_json TEXT NOT NULL,
                    risk_score REAL NOT NULL DEFAULT 0, risk_level TEXT NOT NULL DEFAULT 'low',
                    difference_note TEXT NOT NULL DEFAULT '', source_refs_json TEXT NOT NULL DEFAULT '{}',
                    direction_type TEXT NOT NULL DEFAULT 'imitation',
                    generation_meta_json TEXT NOT NULL DEFAULT '{}', locked_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    UNIQUE(project_id, candidate_group_id, version)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_seeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    parent_version_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'frozen')),
                    seed_text TEXT NOT NULL,
                    constraints_json TEXT NOT NULL DEFAULT '{}',
                    reference_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    locked_at REAL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    FOREIGN KEY(parent_version_id) REFERENCES creative_seeds(id),
                    UNIQUE(project_id, version)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, batch_id TEXT NOT NULL, version INTEGER NOT NULL,
                    parent_version_id INTEGER, character_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', content_json TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL DEFAULT '{}', generation_meta_json TEXT NOT NULL DEFAULT '{}',
                    locked_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    UNIQUE(project_id, batch_id, character_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, batch_id TEXT NOT NULL, version INTEGER NOT NULL,
                    parent_version_id INTEGER, relationship_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', content_json TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL DEFAULT '{}', generation_meta_json TEXT NOT NULL DEFAULT '{}',
                    locked_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    UNIQUE(project_id, batch_id, relationship_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, stage TEXT NOT NULL,
                    episode_number INTEGER NOT NULL, batch_id TEXT NOT NULL, version INTEGER NOT NULL,
                    parent_version_id INTEGER, status TEXT NOT NULL DEFAULT 'draft',
                    content_json TEXT NOT NULL, upstream_refs_json TEXT NOT NULL DEFAULT '{}',
                    generation_meta_json TEXT NOT NULL DEFAULT '{}', locked_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    UNIQUE(project_id, stage, episode_number, version)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creative_projects_topic ON creative_projects(topic_id, deleted)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creative_blueprints_project ON creative_blueprints(project_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_creative_episodes_project ON creative_episodes(project_id, stage, episode_number)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_operation_results (
                    operation_id TEXT PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    resource_ref_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_stage_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, stage TEXT NOT NULL, format TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL, exported_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS selection_stage_snapshots (
                    id TEXT PRIMARY KEY, stage TEXT NOT NULL, topic_id INTEGER NOT NULL,
                    version INTEGER NOT NULL, outcome TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, content_fingerprint TEXT NOT NULL,
                    resource_type TEXT NOT NULL DEFAULT '', resource_id TEXT NOT NULL DEFAULT '',
                    actor_user_id TEXT NOT NULL DEFAULT '', source_job_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, UNIQUE(stage, topic_id, version)
                )
            """)
            snapshot_columns = {
                str(row[1]): str(row[2]).upper()
                for row in conn.execute(
                    "PRAGMA table_info(selection_stage_snapshots)"
                ).fetchall()
            }
            if snapshot_columns.get("id") != "TEXT":
                conn.execute(
                    "ALTER TABLE selection_stage_snapshots "
                    "RENAME TO selection_stage_snapshots_legacy"
                )
                conn.execute("""
                    CREATE TABLE selection_stage_snapshots (
                        id TEXT PRIMARY KEY, stage TEXT NOT NULL, topic_id INTEGER NOT NULL,
                        version INTEGER NOT NULL, outcome TEXT NOT NULL,
                        snapshot_json TEXT NOT NULL, content_fingerprint TEXT NOT NULL,
                        resource_type TEXT NOT NULL DEFAULT '', resource_id TEXT NOT NULL DEFAULT '',
                        actor_user_id TEXT NOT NULL DEFAULT '', source_job_id TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL, UNIQUE(stage, topic_id, version)
                    )
                """)
                conn.execute("""
                    INSERT INTO selection_stage_snapshots (
                        id, stage, topic_id, version, outcome, snapshot_json,
                        content_fingerprint, resource_type, resource_id,
                        actor_user_id, source_job_id, created_at
                    )
                    SELECT CAST(id AS TEXT), stage, topic_id, version, outcome, snapshot_json,
                           content_fingerprint, resource_type, resource_id,
                           actor_user_id, source_job_id, created_at
                    FROM selection_stage_snapshots_legacy
                """)
                conn.execute("DROP TABLE selection_stage_snapshots_legacy")
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS trg_selection_snapshot_no_update
                BEFORE UPDATE ON selection_stage_snapshots
                BEGIN SELECT RAISE(ABORT, 'selection snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS trg_selection_snapshot_no_delete
                BEFORE DELETE ON selection_stage_snapshots
                BEGIN SELECT RAISE(ABORT, 'selection snapshots are immutable'); END;
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS script_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER,
                    source_type TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    episode_numbers_json TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    source_payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    ai_verdict TEXT,
                    report_json TEXT NOT NULL DEFAULT '{}',
                    final_verdict TEXT,
                    confirmation_note TEXT NOT NULL DEFAULT '',
                    confirmed_by TEXT,
                    confirmed_at REAL,
                    created_by TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_script_reviews_visibility
                ON script_reviews(project_id, created_by, created_at DESC);
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS script_optimizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    selected_findings_json TEXT NOT NULL,
                    target_episode_numbers_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_by TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_script_optimizations_review
                ON script_optimizations(review_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS script_optimization_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    optimization_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    episode_number INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    source_episode_id INTEGER,
                    source_fingerprint TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    peer_draft_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'ready',
                    accepted_episode_id INTEGER,
                    accepted_by TEXT NOT NULL DEFAULT '',
                    accepted_at REAL,
                    rejection_note TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_script_optimization_drafts_task
                ON script_optimization_drafts(optimization_id, episode_number, kind);
            """)
            for table in (
                "creative_projects",
                "creative_breakdowns",
                "creative_blueprints",
                "creative_characters",
                "creative_relationships",
                "creative_episodes",
            ):
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "workflow_task_id" not in columns:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN workflow_task_id TEXT"
                    )
            project_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(creative_projects)"
                ).fetchall()
            }
            blueprint_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(creative_blueprints)"
                ).fetchall()
            }
            if "direction_type" not in blueprint_columns:
                conn.execute(
                    "ALTER TABLE creative_blueprints "
                    "ADD COLUMN direction_type TEXT NOT NULL DEFAULT 'imitation'"
                )
            if "creation_workflow_task_id" not in project_columns:
                conn.execute(
                    "ALTER TABLE creative_projects "
                    "ADD COLUMN creation_workflow_task_id TEXT"
                )
                conn.execute(
                    "UPDATE creative_projects "
                    "SET creation_workflow_task_id = workflow_task_id "
                    "WHERE workflow_task_id IS NOT NULL"
                )
            if "creation_request_id" not in project_columns:
                conn.execute(
                    "ALTER TABLE creative_projects "
                    "ADD COLUMN creation_request_id TEXT"
                )
            duplicate_project_tasks = conn.execute(
                """
                SELECT workflow_task_id
                FROM creative_projects
                WHERE workflow_task_id IS NOT NULL
                GROUP BY workflow_task_id HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in duplicate_project_tasks:
                keep = conn.execute(
                    "SELECT id FROM creative_projects WHERE workflow_task_id = ? "
                    "ORDER BY deleted, id DESC LIMIT 1",
                    (duplicate["workflow_task_id"],),
                ).fetchone()
                assert keep is not None
                conn.execute(
                    "UPDATE creative_projects SET workflow_task_id = NULL "
                    "WHERE workflow_task_id = ? AND id <> ?",
                    (duplicate["workflow_task_id"], keep["id"]),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_creative_projects_workflow_task "
                "ON creative_projects(workflow_task_id) "
                "WHERE workflow_task_id IS NOT NULL"
            )
            duplicate_creation_tasks = conn.execute(
                """
                SELECT creation_workflow_task_id
                FROM creative_projects
                WHERE creation_workflow_task_id IS NOT NULL
                GROUP BY creation_workflow_task_id HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in duplicate_creation_tasks:
                keep = conn.execute(
                    "SELECT id FROM creative_projects "
                    "WHERE creation_workflow_task_id = ? "
                    "ORDER BY deleted, id DESC LIMIT 1",
                    (duplicate["creation_workflow_task_id"],),
                ).fetchone()
                assert keep is not None
                conn.execute(
                    "UPDATE creative_projects "
                    "SET creation_workflow_task_id = NULL "
                    "WHERE creation_workflow_task_id = ? AND id <> ?",
                    (duplicate["creation_workflow_task_id"], keep["id"]),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_creative_projects_creation_workflow_task "
                "ON creative_projects(creation_workflow_task_id) "
                "WHERE creation_workflow_task_id IS NOT NULL"
            )
            duplicate_creation_requests = conn.execute(
                """
                SELECT created_by, creation_request_id
                FROM creative_projects
                WHERE creation_request_id IS NOT NULL
                  AND TRIM(creation_request_id) <> ''
                GROUP BY created_by, creation_request_id HAVING COUNT(*) > 1
                """
            ).fetchall()
            for duplicate in duplicate_creation_requests:
                keep = conn.execute(
                    "SELECT id FROM creative_projects "
                    "WHERE created_by = ? AND creation_request_id = ? "
                    "ORDER BY deleted, id LIMIT 1",
                    (duplicate["created_by"], duplicate["creation_request_id"]),
                ).fetchone()
                assert keep is not None
                conn.execute(
                    "UPDATE creative_projects SET creation_request_id = NULL "
                    "WHERE created_by = ? AND creation_request_id = ? AND id <> ?",
                    (
                        duplicate["created_by"],
                        duplicate["creation_request_id"],
                        keep["id"],
                    ),
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_creative_projects_creation_request "
                "ON creative_projects(created_by, creation_request_id) "
                "WHERE creation_request_id IS NOT NULL "
                "AND TRIM(creation_request_id) <> ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_creative_stage_exports_lookup "
                "ON creative_stage_exports(project_id, stage, content_fingerprint, exported_at)"
            )
            empty_audience_projects = conn.execute(
                """
                SELECT p.id, b.content_json
                FROM creative_projects p
                JOIN creative_breakdowns b ON b.id = p.breakdown_id
                WHERE TRIM(p.target_audience) = ''
                """
            ).fetchall()
            for project in empty_audience_projects:
                inherited_audience = self._breakdown_target_audience(project["content_json"])
                if inherited_audience:
                    conn.execute(
                        "UPDATE creative_projects SET target_audience = ? WHERE id = ?",
                        (inherited_audience, project["id"]),
                    )

    def upsert_topics(self, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as conn:
            for topic in topics:
                conn.execute("""
                    INSERT INTO creative_topics (
                        platform, job_id, source_path, captured_at, title, description, category, rank_type, rank_source, score,
                        confidence, score_details_json, recommendation, evidence_json, source_platforms_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, job_id, source_path, title) DO UPDATE SET
                        captured_at=excluded.captured_at, description=excluded.description, category=excluded.category, rank_type=excluded.rank_type, rank_source=excluded.rank_source, score=excluded.score, confidence=excluded.confidence,
                        score_details_json=excluded.score_details_json, recommendation=excluded.recommendation,
                        evidence_json=excluded.evidence_json, source_platforms_json=excluded.source_platforms_json,
                        updated_at=excluded.updated_at
                """, (
                    topic["platform"], topic["job_id"], topic["source_path"], topic["captured_at"],
                    topic["title"], topic.get("description", ""), topic.get("category", ""), topic.get("rank_type", ""),
                    topic.get("rank_source", topic.get("rank_type", "")), topic["score"], topic["confidence"],
                    json.dumps(topic["score_details"], ensure_ascii=False), topic["recommendation"],
                    json.dumps(topic["evidence"], ensure_ascii=False),
                    json.dumps(sorted({str(topic.get("platform") or "")}), ensure_ascii=False), now, now,
                ))
        return self.list_topics(limit=1000)

    def adopt_rank_topic(self, topic: dict[str, Any], actor_user_id: str) -> dict[str, Any]:
        title_key = self._title_key(topic.get("title"))
        if not title_key:
            raise ValueError("榜单剧目缺少标题")
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT title FROM creative_topics WHERE deleted = 0"
            ).fetchall()
            if any(self._title_key(row["title"]) == title_key for row in existing):
                raise DuplicateTopicError("剧目已收录")
            cursor = conn.execute(
                """
                INSERT INTO creative_topics (
                    platform, job_id, source_path, captured_at, title, description, category, rank_type, rank_source,
                    status, score, confidence, score_details_json, recommendation, evidence_json,
                    source_platforms_json, decision_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'adopted', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    topic["platform"], topic["job_id"], topic["source_path"], topic["captured_at"],
                    topic["title"], topic.get("description", ""), topic.get("category", ""),
                    topic.get("rank_type", ""), topic.get("rank_source", topic.get("rank_type", "")),
                    topic["score"], topic["confidence"],
                    json.dumps(topic["score_details"], ensure_ascii=False), topic["recommendation"],
                    json.dumps(topic["evidence"], ensure_ascii=False),
                    json.dumps(sorted({str(topic.get("platform") or "")}), ensure_ascii=False), now, now,
                ),
            )
            topic_id = int(cursor.lastrowid)
            snapshot = {
                "topic_id": topic_id, "title": topic["title"], "outcome": "adopted",
                "decision_note": "", "source": "today_rank",
            }
            canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute(
                """
                INSERT INTO selection_stage_snapshots (
                    id, stage, topic_id, version, outcome, snapshot_json, content_fingerprint,
                    resource_type, resource_id, actor_user_id, source_job_id, created_at
                ) VALUES (?, 'topic_decision', ?, 1, 'adopted', ?, ?, 'topic', ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), topic_id, canonical,
                    hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    str(topic_id), actor_user_id, topic["job_id"], now,
                ),
            )
        return self.get_topic(topic_id) or {}

    def decided_title_keys(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title FROM creative_topics "
                "WHERE deleted = 0 AND status IN ('adopted', 'rejected', 'archived')"
            ).fetchall()
        return {self._title_key(row["title"]) for row in rows if self._title_key(row["title"])}

    def list_topics(
        self,
        platform: str = "",
        status: str = "",
        rank_source: str = "",
        category: str = "",
        min_score: float | None = None,
        max_score: float | None = None,
        captured_date: str = "",
        has_locked_breakdown: bool | None = None,
        limit: int | None = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted = 0"]
        params: list[Any] = []
        if platform:
            clauses.append("(platform = ? OR source_platforms_json LIKE ?)")
            params.extend([platform, f'%"{platform}"%'])
        if category:
            clauses.append("category LIKE ?")
            params.append(f"%{category}%")
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if max_score is not None:
            clauses.append("score <= ?")
            params.append(max_score)
        if captured_date:
            clauses.append("captured_at LIKE ?")
            params.append(f"{captured_date}%")
        locked_expr = "(SELECT id FROM creative_breakdowns b WHERE b.topic_id = creative_topics.id AND b.status IN ('locked', 'approved') ORDER BY b.version DESC, b.id DESC LIMIT 1)"
        latest_id_expr = "(SELECT id FROM creative_breakdowns b WHERE b.topic_id = creative_topics.id ORDER BY b.version DESC, b.id DESC LIMIT 1)"
        latest_version_expr = "(SELECT version FROM creative_breakdowns b WHERE b.topic_id = creative_topics.id ORDER BY b.version DESC, b.id DESC LIMIT 1)"
        latest_status_expr = "(SELECT status FROM creative_breakdowns b WHERE b.topic_id = creative_topics.id ORDER BY b.version DESC, b.id DESC LIMIT 1)"
        limit_clause = ""
        if limit is not None and not rank_source:
            params.append(max(1, min(limit, 500)))
            limit_clause = "LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                    SELECT creative_topics.*,
                           {locked_expr} AS locked_breakdown_id,
                           {latest_id_expr} AS latest_breakdown_id,
                           {latest_version_expr} AS latest_breakdown_version,
                           {latest_status_expr} AS latest_breakdown_status
                    FROM creative_topics
                    WHERE {' AND '.join(clauses)}
                    ORDER BY score DESC, updated_at DESC
                    {limit_clause}
                """,
                params,
            ).fetchall()
        merged = self._merge_topics([self._topic(dict(row)) for row in rows])
        if rank_source:
            merged = [
                item for item in merged
                if self._matches_rank_source(item, rank_source)
            ]
        if status:
            merged = [item for item in merged if item.get("status") == status]
        if has_locked_breakdown is True:
            merged = [item for item in merged if item.get("locked_breakdown_id")]
        elif has_locked_breakdown is False:
            merged = [item for item in merged if not item.get("locked_breakdown_id")]
        return merged if limit is None else merged[: max(1, min(limit, 500))]

    def get_topic(self, topic_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_topics WHERE id = ? AND deleted = 0", (topic_id,)).fetchone()
        return self._topic(dict(row)) if row else None

    def update_topic(self, topic_id: int, status: str, decision_note: str = "") -> dict[str, Any] | None:
        if status not in {"pending_review", "adopted", "rejected", "archived"}:
            raise ValueError("invalid topic status")
        with self._connect() as conn:
            conn.execute("UPDATE creative_topics SET status = ?, decision_note = ?, updated_at = ? WHERE id = ? AND deleted = 0", (status, decision_note, time.time(), topic_id))
        return self.get_topic(topic_id)

    def decide_topic(
        self, topic_id: int, status: str, decision_note: str, actor_user_id: str
    ) -> dict[str, Any] | None:
        topic = self.update_topic(topic_id, status, decision_note)
        if topic is not None:
            self.append_selection_stage_snapshot(
                "topic_decision", topic_id, status,
                {"topic_id": topic_id, "title": topic["title"], "outcome": status,
                 "decision_note": decision_note},
                resource_type="topic", resource_id=topic_id,
                actor_user_id=actor_user_id,
            )
        return topic

    def append_selection_stage_snapshot(
        self, stage: str, topic_id: int, outcome: str, snapshot: dict[str, Any],
        *, resource_type: str = "", resource_id: str | int = "",
        actor_user_id: str = "", source_job_id: str = "", deduplicate: bool = False,
    ) -> dict[str, Any]:
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            if deduplicate:
                existing = conn.execute(
                    "SELECT * FROM selection_stage_snapshots WHERE stage = ? AND topic_id = ? "
                    "AND content_fingerprint = ? ORDER BY version DESC LIMIT 1",
                    (stage, topic_id, fingerprint),
                ).fetchone()
                if existing is not None:
                    return self._selection_snapshot(dict(existing))
            version = int(conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM selection_stage_snapshots "
                "WHERE stage = ? AND topic_id = ?", (stage, topic_id)
            ).fetchone()[0])
            snapshot_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO selection_stage_snapshots "
                "(id,stage,topic_id,version,outcome,snapshot_json,content_fingerprint,"
                "resource_type,resource_id,actor_user_id,source_job_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, stage, topic_id, version, outcome, canonical, fingerprint,
                 resource_type, str(resource_id), actor_user_id, source_job_id, time.time()),
            )
            row = conn.execute("SELECT * FROM selection_stage_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        assert row is not None
        return self._selection_snapshot(dict(row))

    def get_latest_selection_stage_snapshot(
        self, topic_id: int, stage: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM selection_stage_snapshots WHERE topic_id = ? AND stage = ? "
                "ORDER BY version DESC LIMIT 1", (topic_id, stage)
            ).fetchone()
        return self._selection_snapshot(dict(row)) if row else None

    @staticmethod
    def _selection_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        row["snapshot"] = json.loads(row.pop("snapshot_json"))
        return row

    def create_breakdown(
        self,
        topic_id: int,
        content: dict[str, Any],
        sources: dict[str, Any],
        risks: list[str],
        parent_version_id: int | None = None,
        workflow_task_id: str | None = None,
        source_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version, MAX(id) AS parent_id FROM creative_breakdowns WHERE topic_id = ?", (topic_id,)).fetchone()
            version = int(row["version"]) + 1
            now = time.time()
            cursor = conn.execute("""
                INSERT INTO creative_breakdowns (topic_id, version, parent_version_id, content_json, field_sources_json, risks_json, workflow_task_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (topic_id, version, parent_version_id if parent_version_id is not None else row["parent_id"], json.dumps(content, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), json.dumps(risks, ensure_ascii=False), workflow_task_id, now, now))
        result = self.get_breakdown(cursor.lastrowid) or {}
        result["source_snapshot_id"] = source_snapshot_id
        return result

    def get_latest_locked_breakdown(self, topic_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE topic_id = ? AND status IN ('locked', 'approved') ORDER BY version DESC LIMIT 1",
                (topic_id,),
            ).fetchone()
        return self._breakdown(dict(row)) if row else None

    def get_latest_breakdown(self, topic_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE topic_id = ? ORDER BY version DESC, id DESC LIMIT 1",
                (topic_id,),
            ).fetchone()
        return self._breakdown(dict(row)) if row else None

    def list_locked_breakdowns(self, topic_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE topic_id = ? AND status IN ('locked', 'approved') ORDER BY version DESC, id DESC",
                (topic_id,),
            ).fetchall()
        return [self._breakdown(dict(row)) for row in rows]

    def get_breakdown(self, breakdown_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_breakdowns WHERE id = ?", (breakdown_id,)).fetchone()
        return self._breakdown(dict(row)) if row else None

    def update_breakdown(self, breakdown_id: int, content: dict[str, Any]) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM creative_breakdowns WHERE id = ?", (breakdown_id,)).fetchone()
            if not row:
                return None
            if row["status"] != "draft":
                raise ValueError("immutable breakdown cannot be edited")
            conn.execute("UPDATE creative_breakdowns SET content_json = ?, updated_at = ? WHERE id = ?", (json.dumps(content, ensure_ascii=False), time.time(), breakdown_id))
        return self.get_breakdown(breakdown_id)

    def lock_breakdown(self, breakdown_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("UPDATE creative_breakdowns SET status = 'locked', locked_at = ?, updated_at = ? WHERE id = ? AND status = 'draft'", (time.time(), time.time(), breakdown_id))
        return self.get_breakdown(breakdown_id)

    def complete_breakdown(self, breakdown_id: int, actor_user_id: str) -> dict[str, Any]:
        result = self.lock_breakdown(breakdown_id)
        if result is None:
            raise ValueError("拆解不存在")
        self.append_selection_stage_snapshot(
            "breakdown", int(result["topic_id"]), "completed",
            {"breakdown_id": breakdown_id, "version": result["version"],
             "status": result["status"], "content": result["content"],
             "field_sources": result["field_sources"], "risks": result["risks"]},
            resource_type="breakdown", resource_id=breakdown_id,
            actor_user_id=actor_user_id, deduplicate=True,
        )
        return result

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
    ) -> dict[str, Any]:
        if not 1 <= int(target_episode_count) <= 100:
            raise ValueError("目标集数必须在 1-100 之间")
        if not 30 <= int(episode_duration_seconds) <= 1800:
            raise ValueError("单集时长必须在 30-1800 秒之间")
        with self._connect() as conn:
            normalized_request_id = creation_request_id.strip()
            if normalized_request_id:
                existing = conn.execute(
                    "SELECT id FROM creative_projects "
                    "WHERE created_by = ? AND creation_request_id = ? "
                    "AND deleted = 0",
                    (created_by, normalized_request_id),
                ).fetchone()
                if existing is not None:
                    return self.get_project(int(existing["id"])) or {}
            if workflow_task_id:
                existing = conn.execute(
                    "SELECT id FROM creative_projects "
                    "WHERE creation_workflow_task_id = ? AND deleted = 0",
                    (workflow_task_id,),
                ).fetchone()
                if existing is not None:
                    return self.get_project(int(existing["id"])) or {}
            topic = conn.execute(
                "SELECT * FROM creative_topics WHERE id = ? AND deleted = 0", (topic_id,),
            ).fetchone()
            if not topic:
                raise ValueError("选题不存在")
            if topic["status"] != "adopted":
                raise ValueError("只有已采纳选题可以创建创作项目")
            breakdown = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE id = ?",
                (breakdown_id,),
            ).fetchone()
            breakdown_topic = conn.execute(
                "SELECT * FROM creative_topics WHERE id = ? AND deleted = 0",
                (breakdown["topic_id"],),
            ).fetchone() if breakdown else None
            same_title = bool(
                breakdown_topic
                and self._title_key(topic["title"]) == self._title_key(breakdown_topic["title"])
            )
            if not breakdown or breakdown["status"] not in {"locked", "approved"} or not same_title:
                raise ValueError("创建项目需要该选题的已锁定或已批准拆解卡")
            resolved_target_audience = (
                target_audience.strip()
                or self._breakdown_target_audience(breakdown["content_json"])
            )
            now = time.time()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO creative_projects (
                        topic_id, breakdown_id, name, genre, target_platform, target_audience,
                        target_episode_count, episode_duration_seconds, workflow_task_id,
                        creation_workflow_task_id, creation_request_id, created_by,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        topic_id, breakdown_id, name.strip() or f"{topic['title']}创作项目",
                        genre.strip() or str(topic["category"] or ""), target_platform.strip(),
                        resolved_target_audience, int(target_episode_count),
                        int(episode_duration_seconds), workflow_task_id, workflow_task_id,
                        normalized_request_id or None, created_by, now, now,
                    ),
                )
            except sqlite3.IntegrityError:
                if normalized_request_id:
                    existing = conn.execute(
                        "SELECT id FROM creative_projects "
                        "WHERE created_by = ? AND creation_request_id = ?",
                        (created_by, normalized_request_id),
                    ).fetchone()
                    if existing is not None:
                        return self.get_project(int(existing["id"])) or {}
                if not workflow_task_id:
                    raise
                existing = conn.execute(
                    "SELECT id FROM creative_projects "
                    "WHERE creation_workflow_task_id = ?",
                    (workflow_task_id,),
                ).fetchone()
                if existing is None:
                    raise
                return self.get_project(int(existing["id"])) or {}
        return self.get_project(int(cursor.lastrowid)) or {}

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
    ) -> tuple[dict[str, Any], bool]:
        if not 1 <= int(target_episode_count) <= 100:
            raise ValueError("目标集数必须在 1-100 之间")
        if not 30 <= int(episode_duration_seconds) <= 1800:
            raise ValueError("单集时长必须在 30-1800 秒之间")
        if not seed_text.strip():
            raise ValueError("原创种子不能为空")
        now = time.time()
        normalized_request_id = creation_request_id.strip()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if normalized_request_id:
                existing = conn.execute(
                    "SELECT id, creation_mode FROM creative_projects "
                    "WHERE created_by = ? AND creation_request_id = ? AND deleted = 0",
                    (created_by, normalized_request_id),
                ).fetchone()
                if existing is not None:
                    if existing["creation_mode"] != "original":
                        raise ValueError("创建请求标识已用于其他项目模式")
                    project_id = int(existing["id"])
                    created = False
                else:
                    project_id, created = self._insert_original_project(
                        conn,
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
                    )
            else:
                project_id, created = self._insert_original_project(
                    conn,
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
                )
        return self.get_project(project_id) or {}, created

    @staticmethod
    def _insert_original_project(
        conn: sqlite3.Connection,
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
        now: float,
    ) -> tuple[int, bool]:
        cursor = conn.execute(
            """
            INSERT INTO creative_projects (
                topic_id, breakdown_id, name, genre, target_platform, target_audience,
                target_episode_count, episode_duration_seconds, creation_request_id,
                created_by, creation_mode, bootstrap_status, created_at, updated_at
            ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'original', 'pending', ?, ?)
            """,
            (
                name.strip(), genre.strip(), target_platform.strip(), target_audience.strip(),
                int(target_episode_count), int(episode_duration_seconds),
                creation_request_id or None, created_by, now, now,
            ),
        )
        project_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO creative_seeds (
                project_id, version, parent_version_id, status, seed_text,
                constraints_json, reference_refs_json, created_at, updated_at
            ) VALUES (?, 1, NULL, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                project_id, seed_text.strip(), json.dumps(constraints or {}, ensure_ascii=False),
                json.dumps(reference_refs or [], ensure_ascii=False), now, now,
            ),
        )
        return project_id, True

    def mark_project_bootstrap_status(
        self, project_id: int, status: str
    ) -> dict[str, Any]:
        if status not in {"pending", "ready", "failed"}:
            raise ValueError("invalid bootstrap status")
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_projects SET bootstrap_status = ?, updated_at = ? "
                "WHERE id = ? AND deleted = 0",
                (status, time.time(), project_id),
            )
        return self.get_project(project_id) or {}

    def get_current_seed(self, project_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_seeds WHERE project_id = ? "
                "ORDER BY version DESC, id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._seed(dict(row)) if row else None

    def get_seed(self, seed_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_seeds WHERE id = ?", (seed_id,)).fetchone()
        return self._seed(dict(row)) if row else None

    def list_seeds(self, project_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_seeds WHERE project_id = ? ORDER BY version, id",
                (project_id,),
            ).fetchall()
        return [self._seed(dict(row)) for row in rows]

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
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in updates.items():
            column = {
                "seed_text": "seed_text",
                "constraints": "constraints_json",
                "reference_refs": "reference_refs_json",
            }[key]
            assignments.append(f"{column} = ?")
            params.append(
                value.strip() if key == "seed_text" else json.dumps(value, ensure_ascii=False)
            )
        params.extend([time.time(), seed_id])
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE creative_seeds SET {', '.join(assignments)}, updated_at = ? "
                "WHERE id = ? AND status = 'draft'",
                params,
            )
        return self.get_seed(seed_id) if cursor.rowcount else None

    def freeze_seed(self, seed_id: int) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_seeds SET status = 'frozen', locked_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'draft'",
                (now, now, seed_id),
            )
        seed = self.get_seed(seed_id)
        if seed is None:
            raise ValueError("原创种子不存在")
        return seed

    def create_seed_version(self, project_id: int, **values: Any) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT * FROM creative_seeds WHERE project_id = ? ORDER BY version DESC, id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if parent is None:
                raise ValueError("原创项目种子不存在")
            now = time.time()
            cursor = conn.execute(
                """
                INSERT INTO creative_seeds (
                    project_id, version, parent_version_id, status, seed_text,
                    constraints_json, reference_refs_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    project_id, int(parent["version"]) + 1, int(parent["id"]),
                    str(values.get("seed_text", parent["seed_text"])).strip(),
                    json.dumps(values.get("constraints", json.loads(parent["constraints_json"])), ensure_ascii=False),
                    json.dumps(values.get("reference_refs", json.loads(parent["reference_refs_json"])), ensure_ascii=False),
                    now, now,
                ),
            )
        return self.get_seed(int(cursor.lastrowid)) or {}

    def get_project_by_creation_request_id(
        self, created_by: str, creation_request_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM creative_projects "
                "WHERE created_by = ? AND creation_request_id = ? AND deleted = 0",
                (created_by, creation_request_id.strip()),
            ).fetchone()
        return self.get_project(int(row["id"])) if row is not None else None

    def list_project_creator_audit_rows(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_by FROM creative_projects "
                "WHERE deleted = 0 AND created_by IS NOT NULL"
            ).fetchall()
        return [(str(row["id"]), str(row["created_by"])) for row in rows]

    @staticmethod
    def _breakdown_target_audience(content_json: str) -> str:
        try:
            content = json.loads(content_json)
        except (TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(content, dict):
            return ""
        return str(content.get("target_audience") or "").strip()

    def list_projects(self, topic_id: int | None = None) -> list[dict[str, Any]]:
        clauses = ["p.deleted = 0", "p.bootstrap_status = 'ready'"]
        params: list[Any] = []
        if topic_id is not None:
            clauses.append("p.topic_id = ?")
            params.append(topic_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.*, t.title AS topic_title
                FROM creative_projects p LEFT JOIN creative_topics t ON t.id = p.topic_id
                WHERE {' AND '.join(clauses)} ORDER BY p.updated_at DESC, p.id DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_projects_for_bootstrap(
        self, created_by: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["deleted = 0", "created_by = ?", "creation_mode = 'original'"]
        params: list[Any] = [created_by]
        if status is not None:
            if status not in {"pending", "ready", "failed"}:
                raise ValueError("invalid bootstrap status")
            clauses.append("bootstrap_status = ?")
            params.append(status)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM creative_projects WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at, id",
                params,
            ).fetchall()
        return [self.get_project(int(row["id"])) or {} for row in rows]

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

    def _operation_project(
        self, conn: sqlite3.Connection, project_id: int, *, include_episodes: bool = False
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM creative_projects WHERE id = ? AND deleted = 0",
            (project_id,),
        ).fetchone()
        if row is None:
            raise ValueError("创作项目不存在")
        project = dict(row)
        if include_episodes:
            project["episodes"] = [
                self._episode(dict(item))
                for item in conn.execute(
                    "SELECT * FROM creative_episodes WHERE project_id = ? "
                    "ORDER BY stage, episode_number, version",
                    (project_id,),
                ).fetchall()
            ]
        return project

    @staticmethod
    def _read_operation_result(
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT project_id, stage, request_hash, resource_ref_json "
            "FROM creative_operation_results WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            int(row["project_id"]) != int(project_id)
            or str(row["stage"]) != stage
            or str(row["request_hash"]) != request_hash
        ):
            raise ValueError("IDEMPOTENCY_KEY_REUSED")
        return json.loads(row["resource_ref_json"] or "{}")

    def _hydrate_resource_ref(
        self,
        conn: sqlite3.Connection,
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
                row = conn.execute(
                    "SELECT * FROM creative_blueprints WHERE id = ? AND project_id = ?",
                    (int(resource_id), project_id),
                ).fetchone()
                if row is not None:
                    resources.append(self._blueprint(dict(row)))
            return resources
        if resource_type in {"brief", "blueprint"}:
            row = conn.execute(
                "SELECT * FROM creative_blueprints WHERE id = ? AND project_id = ?",
                (int(resource_ref["resource_id"]), project_id),
            ).fetchone()
            return restore_operation_metadata(
                self._blueprint(dict(row)) if row is not None else None
            )
        if resource_type in {"characters", "character_batch"}:
            batch_ref = str(resource_ref.get("resource_id") or resource_ref["batch_id"])
            ref_project_id, separator, batch_id = batch_ref.partition(":")
            if not separator:
                batch_id = ref_project_id
            elif int(ref_project_id) != project_id:
                raise ValueError("operation resource project mismatch")
            characters = [
                self._creative_item(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_characters "
                    "WHERE project_id = ? AND batch_id = ? ORDER BY id",
                    (project_id, batch_id),
                ).fetchall()
            ]
            relationships = [
                self._creative_item(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_relationships "
                    "WHERE project_id = ? AND batch_id = ? ORDER BY id",
                    (project_id, batch_id),
                ).fetchall()
            ]
            return restore_operation_metadata({
                "batch_id": batch_id,
                "characters": characters,
                "relationships": relationships,
            })
        if resource_type in {"outline", "outline_batch"}:
            batch_ref = str(resource_ref.get("resource_id") or resource_ref["batch_id"])
            ref_project_id, separator, batch_id = batch_ref.partition(":")
            if not separator:
                batch_id = ref_project_id
            elif int(ref_project_id) != project_id:
                raise ValueError("operation resource project mismatch")
            episodes = [
                self._episode(dict(row))
                for row in conn.execute(
                    "SELECT * FROM creative_episodes "
                    "WHERE project_id = ? AND stage = 'outline' AND batch_id = ? "
                    "ORDER BY episode_number, version",
                    (project_id, batch_id),
                ).fetchall()
            ]
            overview = read_series_overview(
                (episodes[0].get("content") or {}).get("series_overview")
            ) if episodes else None
            return restore_operation_metadata({
                "batch_id": batch_id,
                "series_overview": overview,
                "episodes": episodes,
            })
        if resource_type in {"beats", "script", "episode"}:
            row = conn.execute(
                "SELECT * FROM creative_episodes WHERE id = ? AND project_id = ?",
                (int(resource_ref["resource_id"]), project_id),
            ).fetchone()
            return restore_operation_metadata(
                self._episode(dict(row)) if row is not None else None
            )
        return dict(resource_ref)

    def _run_idempotent_operation(
        self,
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        project_id: int | None,
        stage: str,
        request_hash: str | Callable[[int], str],
        create: Callable[[sqlite3.Connection], dict[str, Any]],
        resolve_project_id: Callable[[sqlite3.Connection], int] | None = None,
    ) -> Any:
        conn.execute("BEGIN IMMEDIATE")
        operation_row = conn.execute(
            "SELECT project_id FROM creative_operation_results WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if operation_row is not None and project_id is None:
            project_id = int(operation_row["project_id"])
        if project_id is None:
            if resolve_project_id is None:
                raise ValueError("operation project is required")
            project_id = int(resolve_project_id(conn))
        resolved_hash = request_hash(project_id) if callable(request_hash) else request_hash
        existing = self._read_operation_result(
            conn,
            operation_id=operation_id,
            project_id=project_id,
            stage=stage,
            request_hash=resolved_hash,
        )
        if existing is not None:
            return self._hydrate_resource_ref(conn, project_id, existing)
        resource_ref = create(conn)
        conn.execute(
            "INSERT INTO creative_operation_results "
            "(operation_id, project_id, stage, request_hash, resource_ref_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                project_id,
                stage,
                resolved_hash,
                json.dumps(
                    resource_ref,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                time.time(),
            ),
        )
        return self._hydrate_resource_ref(conn, project_id, resource_ref)

    def record_operation_result(
        self,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        request_hash: str,
        resource_ref: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._read_operation_result(
                conn,
                operation_id=operation_id,
                project_id=project_id,
                stage=stage,
                request_hash=request_hash,
            )
            if existing is not None:
                return existing
            conn.execute(
                "INSERT INTO creative_operation_results "
                "(operation_id, project_id, stage, request_hash, resource_ref_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    project_id,
                    stage,
                    request_hash,
                    json.dumps(
                        resource_ref,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    time.time(),
                ),
            )
        return dict(resource_ref)

    def hydrate_operation_result(self, operation_id: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id, resource_ref_json FROM creative_operation_results "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_resource_ref(
                conn,
                int(row["project_id"]),
                json.loads(row["resource_ref_json"] or "{}"),
            )

    def update_project(
        self,
        project_id: int,
        values: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        allowed = {
            "name", "genre", "target_platform", "target_audience",
            "target_episode_count", "episode_duration_seconds", "status",
        }
        updates = {key: value for key, value in values.items() if key in allowed and value is not None}
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
        if project.get("locked_brief_id") and content_updates:
            raise ValueError("锁定 Brief 后不能修改项目 Brief 参数")
        if content_updates:
            with self._connect() as conn:
                pending_brief = conn.execute(
                    "SELECT 1 FROM creative_blueprints WHERE project_id = ? "
                    "AND status = 'submitted' LIMIT 1",
                    (project_id,),
                ).fetchone()
            if pending_brief:
                raise ValueError("Brief 完成后不能修改项目 Brief 参数")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        if content_updates:
            assignments += ", revision = revision + 1"
        params = list(updates.values())
        params.extend([time.time(), project_id])
        revision_clause = ""
        if expected_revision is not None:
            revision_clause = " AND revision = ?"
            params.append(int(expected_revision))
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE creative_projects SET {assignments}, updated_at = ? "
                f"WHERE id = ? AND deleted = 0{revision_clause}",
                params,
            )
            if expected_revision is not None and cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

    def delete_project(self, project_id: int) -> dict[str, Any]:
        """Permanently remove a project and every project-scoped creative record."""

        with self._connect() as conn:
            project = conn.execute(
                "SELECT id, name, topic_id FROM creative_projects WHERE id = ? AND deleted = 0",
                (project_id,),
            ).fetchone()
            if project is None:
                raise ValueError("创作项目不存在")

            delivery_paths: list[str] = []
            delivery_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_deliveries'"
            ).fetchone()
            if delivery_table is not None:
                delivery_paths = [
                    str(row["path"])
                    for row in conn.execute(
                        "SELECT path FROM creative_deliveries WHERE project_id = ?", (project_id,)
                    ).fetchall()
                    if row["path"]
                ]
                conn.execute("DELETE FROM creative_deliveries WHERE project_id = ?", (project_id,))

            optimization_ids = [
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM script_optimizations WHERE project_id = ?", (project_id,)
                ).fetchall()
            ]
            if optimization_ids:
                placeholders = ",".join("?" for _ in optimization_ids)
                conn.execute(
                    f"DELETE FROM script_optimization_drafts WHERE project_id = ? OR optimization_id IN ({placeholders})",
                    [project_id, *optimization_ids],
                )
            else:
                conn.execute(
                    "DELETE FROM script_optimization_drafts WHERE project_id = ?", (project_id,)
                )
            conn.execute("DELETE FROM script_optimizations WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM script_reviews WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_stage_exports WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_operation_results WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_episodes WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_relationships WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_characters WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_blueprints WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_seeds WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_projects WHERE id = ?", (project_id,))

        result = dict(project)
        result["delivery_paths"] = delivery_paths
        return result

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, t.title AS topic_title, t.description AS topic_description,
                       t.source_platforms_json, b.version AS breakdown_version
                FROM creative_projects p
                LEFT JOIN creative_topics t ON t.id = p.topic_id
                LEFT JOIN creative_breakdowns b ON b.id = p.breakdown_id
                WHERE p.id = ? AND p.deleted = 0
                """,
                (project_id,),
            ).fetchone()
            if not row:
                return None
            project = dict(row)
            project["source_platforms"] = json.loads(project.pop("source_platforms_json") or "[]")
            project["blueprints"] = [
                self._blueprint(dict(item)) for item in conn.execute(
                    "SELECT * FROM creative_blueprints WHERE project_id = ? ORDER BY id", (project_id,),
                ).fetchall()
            ]
            project["characters"] = [
                self._creative_item(dict(item)) for item in conn.execute(
                    "SELECT * FROM creative_characters WHERE project_id = ? ORDER BY version, id", (project_id,),
                ).fetchall()
            ]
            project["relationships"] = [
                self._creative_item(dict(item)) for item in conn.execute(
                    "SELECT * FROM creative_relationships WHERE project_id = ? ORDER BY version, id", (project_id,),
                ).fetchall()
            ]
            project["episodes"] = [
                self._episode(dict(item)) for item in conn.execute(
                    "SELECT * FROM creative_episodes WHERE project_id = ? ORDER BY stage, episode_number, version",
                    (project_id,),
                ).fetchall()
            ]
            if project["creation_mode"] == "original":
                project["topic"] = {}
                project["breakdown"] = {}
                seed = conn.execute(
                    "SELECT * FROM creative_seeds WHERE project_id = ? "
                    "ORDER BY version DESC, id DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
                project["seed"] = self._seed(dict(seed)) if seed else None
        project["allowed_actions"] = self._allowed_project_actions(project)
        return project

    def get_project_by_workflow_task_id(
        self, workflow_task_id: str
    ) -> dict[str, Any] | None:
        return self.get_project_by_creation_workflow_task_id(workflow_task_id)

    def get_project_by_creation_workflow_task_id(
        self, workflow_task_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM creative_projects "
                "WHERE creation_workflow_task_id = ? AND deleted = 0",
                (workflow_task_id,),
            ).fetchone()
        return self.get_project(int(row["id"])) if row is not None else None

    def reconcile_project_workflow_task(
        self, workflow_task_id: str, project_id: int
    ) -> dict[str, Any]:
        with self._connect() as conn:
            target = conn.execute(
                "SELECT id FROM creative_projects WHERE id = ? AND deleted = 0",
                (project_id,),
            ).fetchone()
            if target is None:
                raise ValueError("system-bound creative project does not exist")
            conn.execute(
                "UPDATE creative_projects SET creation_workflow_task_id = NULL, "
                "workflow_task_id = CASE WHEN workflow_task_id = ? THEN NULL "
                "ELSE workflow_task_id END, updated_at = ? "
                "WHERE creation_workflow_task_id = ? AND id <> ?",
                (workflow_task_id, time.time(), workflow_task_id, project_id),
            )
            conn.execute(
                "UPDATE creative_projects SET workflow_task_id = NULL, updated_at = ? "
                "WHERE workflow_task_id = ? AND id <> ?",
                (time.time(), workflow_task_id, project_id),
            )
            conn.execute(
                "UPDATE creative_projects SET creation_workflow_task_id = ?, "
                "workflow_task_id = CASE WHEN workflow_task_id IS NULL "
                "OR workflow_task_id = ? THEN ? ELSE workflow_task_id END, "
                "updated_at = ? "
                "WHERE id = ?",
                (
                    workflow_task_id,
                    workflow_task_id,
                    workflow_task_id,
                    time.time(),
                    project_id,
                ),
            )
        project = self.get_project(project_id)
        if project is None:
            raise ValueError("system-bound creative project does not exist")
        return project

    def clear_project_creation_task(
        self, project_id: int, workflow_task_id: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_projects SET creation_workflow_task_id = NULL, "
                "updated_at = ? WHERE id = ? AND creation_workflow_task_id = ?",
                (time.time(), project_id, workflow_task_id),
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
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE creative_projects SET workflow_task_id = ?, updated_at = ? WHERE id = ?",
                (workflow_task_id, time.time(), project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("创作项目不存在")

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
            },
        )

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            if not 1 <= len(items) <= 5:
                raise ValueError("仿制方向数量必须在 1-5 之间")
            project = self._operation_project(conn, project_id)
            now = time.time()
            refs = source_refs or {
                "topic_id": project["topic_id"],
                "breakdown_id": project["breakdown_id"],
            }
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            ids: list[int] = []
            for item in items:
                cursor = conn.execute(
                    """
                    INSERT INTO creative_blueprints (
                        project_id, candidate_group_id, content_json, risk_score, risk_level,
                        difference_note, source_refs_json, direction_type, generation_meta_json,
                        workflow_task_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, uuid.uuid4().hex,
                        json.dumps(item.get("content") or item, ensure_ascii=False),
                        float(item.get("risk_score") or 0), str(item.get("risk_level") or "low"),
                        str(item.get("difference_note") or ""), json.dumps(refs, ensure_ascii=False),
                        direction_type, json.dumps(meta, ensure_ascii=False),
                        workflow_task_id or project.get("workflow_task_id"), now, now,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            conn.execute(
                "UPDATE creative_projects SET status = 'direction_review', updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            return {"resource_type": "brief", "resource_ids": ids}

        with self._connect() as conn:
            if operation_id:
                return self._run_idempotent_operation(
                    conn,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="brief",
                    request_hash=request_hash,
                    create=create,
                )
            conn.execute("BEGIN IMMEDIATE")
            resource_ref = create(conn)
            return self._hydrate_resource_ref(conn, project_id, resource_ref)

    def get_blueprint(self, blueprint_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_blueprints WHERE id = ?", (blueprint_id,)).fetchone()
        return self._blueprint(dict(row)) if row else None

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
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE creative_blueprints SET content_json = ?, risk_score = ?, risk_level = ?,
                    difference_note = ?, updated_at = ? WHERE id = ?
                """,
                (
                    json.dumps(content, ensure_ascii=False),
                    float(current["risk_score"] if risk_score is None else risk_score),
                    current["risk_level"] if risk_level is None else risk_level,
                    current["difference_note"] if difference_note is None else difference_note.strip(),
                    time.time(), blueprint_id,
                ),
            )
        return self.get_blueprint(blueprint_id)

    def create_blueprint_version(
        self, blueprint_id: int, workflow_task_id: str | None = None
    ) -> dict[str, Any]:
        current = self.get_blueprint(blueprint_id)
        if not current:
            raise ValueError("仿制方向不存在")
        now = time.time()
        with self._connect() as conn:
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM creative_blueprints WHERE project_id = ? AND candidate_group_id = ?",
                (current["project_id"], current["candidate_group_id"]),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO creative_blueprints (
                    project_id, candidate_group_id, version, parent_version_id, content_json,
                    risk_score, risk_level, difference_note, source_refs_json,
                    direction_type, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    current["project_id"], current["candidate_group_id"], int(version_row["version"]) + 1,
                    blueprint_id, json.dumps(current["content"], ensure_ascii=False),
                    current["risk_score"], current["risk_level"], current["difference_note"],
                    json.dumps(current["source_refs"], ensure_ascii=False),
                    current["direction_type"],
                    json.dumps(current["generation_meta"], ensure_ascii=False),
                    workflow_task_id or current.get("workflow_task_id"), now, now,
                ),
            )
        return self.get_blueprint(int(cursor.lastrowid)) or {}

    def create_blueprint_revision(
        self,
        blueprint_id: int,
        content: dict[str, Any],
        workflow_task_id: str | None,
        operation_id: str,
    ) -> dict[str, Any]:
        def request_hash(project_id: int) -> str:
            return self._operation_request_hash(
                project_id=project_id,
                stage="brief_revision",
                scope={"blueprint_id": blueprint_id},
                payload={"content": content, "workflow_task_id": workflow_task_id},
            )

        source_holder: dict[str, sqlite3.Row] = {}

        def resolve_project_id(conn: sqlite3.Connection) -> int:
            source = conn.execute(
                "SELECT * FROM creative_blueprints WHERE id = ?",
                (blueprint_id,),
            ).fetchone()
            if source is None:
                raise ValueError("仿制方向不存在")
            source_holder["source"] = source
            return int(source["project_id"])

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            source = source_holder.get("source")
            if source is None:
                raise ValueError("仿制方向不存在")
            project_id = int(source["project_id"])
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version "
                "FROM creative_blueprints WHERE project_id = ? AND candidate_group_id = ?",
                (project_id, source["candidate_group_id"]),
            ).fetchone()
            now = time.time()
            cursor = conn.execute(
                """
                INSERT INTO creative_blueprints (
                    project_id, candidate_group_id, version, parent_version_id, content_json,
                    risk_score, risk_level, difference_note, source_refs_json,
                    direction_type, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    source["candidate_group_id"],
                    int(version_row["version"]),
                    blueprint_id,
                    json.dumps(content, ensure_ascii=False),
                    source["risk_score"],
                    source["risk_level"],
                    source["difference_note"],
                    source["source_refs_json"],
                    source["direction_type"],
                    source["generation_meta_json"],
                    workflow_task_id or source["workflow_task_id"],
                    now,
                    now,
                ),
            )
            return {"resource_type": "brief", "resource_id": int(cursor.lastrowid)}

        with self._connect() as conn:
            return self._run_idempotent_operation(
                conn,
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
        self, resource: dict[str, Any], project_id: int
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        result = dict(resource)
        result["revision"] = int((project or {}).get("revision") or 0)
        return result

    def lock_brief(
        self,
        blueprint_id: int,
        difference_note: str = "",
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        current = self.get_blueprint(blueprint_id)
        if not current:
            raise ValueError("仿制方向不存在")
        project = self.get_project(int(current["project_id"]))
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        if current["status"] == "brief_locked":
            return self._resource_with_project_revision(current, int(current["project_id"]))
        note = difference_note.strip() or str(current.get("difference_note") or "").strip()
        if (
            current["direction_type"] == "imitation"
            and current["risk_level"] == "high"
            and not note
        ):
            raise ValueError("高风险方向必须填写差异化修改说明")
        content = dict(current["content"])
        content.update({
            "project_name": project["name"],
            "genre": project["genre"],
            "target_platform": project["target_platform"],
            "target_audience": content.get("audience") or project["target_audience"],
            "target_episode_count": project["target_episode_count"],
            "episode_duration_seconds": project["episode_duration_seconds"],
        })
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_blueprints SET status = 'brief_locked', content_json = ?, difference_note = ?, locked_at = ?, updated_at = ? WHERE id = ?",
                (json.dumps(content, ensure_ascii=False), note, now, now, blueprint_id),
            )
            params: list[Any] = [blueprint_id, now, current["project_id"]]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision = ?"
                params.append(int(expected_revision))
            cursor = conn.execute(
                "UPDATE creative_projects SET locked_brief_id = ?, character_batch_id = NULL, "
                "outline_batch_id = NULL, status = 'brief_locked', revision = revision + 1, "
                f"updated_at = ? WHERE id = ?{revision_clause}",
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_blueprint(blueprint_id) or {}, int(current["project_id"])
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
        if not brief or int(brief.get("project_id") or 0) != project_id:
            raise ValueError("仿制方向不存在")
        if brief.get("status") != "brief_locked":
            raise ValueError("只能选择已锁定的 Brief 版本")
        content = brief.get("content") or {}
        episode_count = project["target_episode_count"]
        episode_duration = project["episode_duration_seconds"]
        try:
            candidate_count = int(content.get("target_episode_count") or episode_count)
            if 1 <= candidate_count <= 100:
                episode_count = candidate_count
        except (TypeError, ValueError):
            pass
        try:
            candidate_duration = int(content.get("episode_duration_seconds") or episode_duration)
            if 30 <= candidate_duration <= 1800:
                episode_duration = candidate_duration
        except (TypeError, ValueError):
            pass

        if int(project.get("locked_brief_id") or 0) == blueprint_id:
            if (
                int(project["target_episode_count"]) == episode_count
                and int(project["episode_duration_seconds"]) == episode_duration
            ):
                return project
            with self._connect() as conn:
                params: list[Any] = [
                    episode_count, episode_duration, time.time(), project_id,
                ]
                revision_clause = ""
                if expected_revision is not None:
                    revision_clause = " AND revision = ?"
                    params.append(int(expected_revision))
                cursor = conn.execute(
                    "UPDATE creative_projects SET target_episode_count = ?, episode_duration_seconds = ?, "
                    f"revision = revision + 1, updated_at = ? WHERE id = ?{revision_clause}",
                    params,
                )
                if cursor.rowcount != 1:
                    raise ValueError("project revision changed")
            return self.get_project(project_id) or {}
        with self._connect() as conn:
            params = [
                blueprint_id, episode_count, episode_duration, time.time(), project_id,
            ]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision = ?"
                params.append(int(expected_revision))
            cursor = conn.execute(
                "UPDATE creative_projects SET locked_brief_id = ?, character_batch_id = NULL, "
                "outline_batch_id = NULL, target_episode_count = ?, episode_duration_seconds = ?, "
                "status = 'brief_locked', revision = revision + 1, "
                f"updated_at = ? WHERE id = ?{revision_clause}",
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

    def create_character_batch(
        self,
        project_id: int,
        characters: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        operation_id: str | None = None,
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
            },
        )

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            project = self._operation_project(conn, project_id)
            if not project.get("locked_brief_id"):
                raise ValueError("必须先锁定 Brief")
            batch_id = uuid.uuid4().hex
            now = time.time()
            refs = {"brief_id": project["locked_brief_id"]}
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version "
                "FROM creative_characters WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            version = int(version_row["version"])
            for index, content in enumerate(characters, start=1):
                key = str(content.get("name") or f"character-{index}").strip()
                parent = conn.execute(
                    "SELECT id FROM creative_characters WHERE project_id = ? AND character_key = ? ORDER BY version DESC LIMIT 1",
                    (project_id, key),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO creative_characters (
                        project_id, batch_id, version, parent_version_id, character_key, content_json,
                        source_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, batch_id, version, parent["id"] if parent else None, key, json.dumps(content, ensure_ascii=False),
                     json.dumps(refs, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now),
                )
            for index, content in enumerate(relationships, start=1):
                key = f"{content.get('from') or ''}>{content.get('to') or ''}:{index}"
                parent = conn.execute(
                    "SELECT id FROM creative_relationships WHERE project_id = ? AND relationship_key = ? ORDER BY version DESC LIMIT 1",
                    (project_id, key),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO creative_relationships (
                        project_id, batch_id, version, parent_version_id, relationship_key, content_json,
                        source_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, batch_id, version, parent["id"] if parent else None, key, json.dumps(content, ensure_ascii=False),
                     json.dumps(refs, ensure_ascii=False), json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now),
                )
            conn.execute(
                "UPDATE creative_projects SET status = 'characters_draft', updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            return {
                "resource_type": "characters",
                "resource_id": f"{project_id}:{batch_id}",
            }

        with self._connect() as conn:
            if operation_id:
                return self._run_idempotent_operation(
                    conn,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="characters",
                    request_hash=request_hash,
                    create=create,
                )
            conn.execute("BEGIN IMMEDIATE")
            resource_ref = create(conn)
            return self._hydrate_resource_ref(conn, project_id, resource_ref)

    def get_character_batch(self, project_id: int, batch_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        return {
            "batch_id": batch_id,
            "characters": [item for item in project["characters"] if item["batch_id"] == batch_id],
            "relationships": [item for item in project["relationships"] if item["batch_id"] == batch_id],
        }

    def get_character(self, character_id: int) -> dict[str, Any] | None:
        return self._get_creative_item("creative_characters", character_id)

    def get_relationship(self, relationship_id: int) -> dict[str, Any] | None:
        return self._get_creative_item("creative_relationships", relationship_id)

    def update_character(self, character_id: int, content: dict[str, Any]) -> dict[str, Any] | None:
        return self._update_creative_item("creative_characters", character_id, content)

    def update_relationship(self, relationship_id: int, content: dict[str, Any]) -> dict[str, Any] | None:
        return self._update_creative_item("creative_relationships", relationship_id, content)

    def lock_character_batch(
        self,
        project_id: int,
        batch_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        batch = self.get_character_batch(project_id, batch_id)
        if not batch["characters"]:
            raise ValueError("人物关系版本至少需要一个人物")
        if (
            project.get("character_batch_id") == batch_id
            and all(
                item.get("status") in {"locked", "approved"}
                for item in batch["characters"]
            )
        ):
            return self._resource_with_project_revision(batch, project_id)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_characters SET status = 'locked', locked_at = ?, updated_at = ? WHERE project_id = ? AND batch_id = ?",
                (now, now, project_id, batch_id),
            )
            conn.execute(
                "UPDATE creative_relationships SET status = 'locked', locked_at = ?, updated_at = ? WHERE project_id = ? AND batch_id = ?",
                (now, now, project_id, batch_id),
            )
            params: list[Any] = [batch_id, now, project_id]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision = ?"
                params.append(int(expected_revision))
            cursor = conn.execute(
                "UPDATE creative_projects SET character_batch_id = ?, outline_batch_id = NULL, "
                "status = 'characters_locked', revision = revision + 1, "
                f"updated_at = ? WHERE id = ?{revision_clause}",
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_character_batch(project_id, batch_id), project_id
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
        if any(item.get("status") not in {"locked", "approved"} for item in characters):
            raise ValueError("只能选择已锁定的人物关系版本")
        active_brief_id = int(project["locked_brief_id"])
        if any(int((item.get("source_refs") or {}).get("brief_id") or 0) != active_brief_id for item in characters):
            raise ValueError("所选人物关系版本不属于当前锁定 Brief")

        if project.get("character_batch_id") == batch_id:
            return project
        with self._connect() as conn:
            params: list[Any] = [batch_id, time.time(), project_id]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision = ?"
                params.append(int(expected_revision))
            cursor = conn.execute(
                "UPDATE creative_projects SET character_batch_id = ?, outline_batch_id = NULL, "
                "status = 'characters_locked', revision = revision + 1, "
                f"updated_at = ? WHERE id = ?{revision_clause}",
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self.get_project(project_id) or {}

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
            },
        )

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            project = self._operation_project(conn, project_id)
            if not project.get("character_batch_id"):
                raise ValueError("必须先锁定人物关系版本")
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
            now = time.time()
            normalized_overview = (
                normalize_series_overview(series_overview)
                if series_overview is not None
                else None
            )
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version "
                "FROM creative_episodes WHERE project_id = ? AND stage = 'outline'",
                (project_id,),
            ).fetchone()
            version = int(version_row["version"])
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
                if normalized_overview is not None:
                    content["series_overview"] = normalized_overview
                parent = conn.execute(
                    "SELECT id FROM creative_episodes WHERE project_id = ? AND stage = 'outline' AND episode_number = ? ORDER BY version DESC LIMIT 1",
                    (project_id, int(content["episode"])),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO creative_episodes (
                        project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                        upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, 'outline', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, int(content["episode"]), batch_id, version, parent["id"] if parent else None,
                        json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                        json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                    ),
                )
            conn.execute(
                "UPDATE creative_projects SET status = 'outline_draft', updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            return {
                "resource_type": "outline",
                "resource_id": f"{project_id}:{batch_id}",
            }

        with self._connect() as conn:
            if operation_id:
                return self._run_idempotent_operation(
                    conn,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="outline",
                    request_hash=request_hash,
                    create=create,
                )
            conn.execute("BEGIN IMMEDIATE")
            resource_ref = create(conn)
            return self._hydrate_resource_ref(conn, project_id, resource_ref)

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
                item for item in project["episodes"]
                if item["stage"] == "outline" and item["batch_id"] == batch_id
            ]
            if existing_rows and any(item["status"] != "draft" for item in existing_rows):
                raise ValueError("已锁定的大纲批次不能写入生成检查点")
        else:
            batch_id = uuid.uuid4().hex
        version = (
            int(existing_rows[0]["version"])
            if existing_rows
            else 1 + max(
                [int(item["version"]) for item in project["episodes"] if item["stage"] == "outline"] or [0]
            )
        )
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
        now = time.time()
        normalized_overview = (
            normalize_series_overview(series_overview)
            if series_overview is not None
            else None
        )
        with self._connect() as conn:
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
                if normalized_overview is not None:
                    content["series_overview"] = normalized_overview
                episode_number = int(content["episode"])
                existing = existing_by_episode.get(episode_number)
                if existing:
                    conn.execute(
                        """
                        UPDATE creative_episodes
                        SET content_json = ?, upstream_refs_json = ?, generation_meta_json = ?,
                            workflow_task_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                            json.dumps(meta, ensure_ascii=False),
                            workflow_task_id or project.get("workflow_task_id"), now, existing["id"],
                        ),
                    )
                    continue
                parent = conn.execute(
                    """
                    SELECT id FROM creative_episodes
                    WHERE project_id = ? AND stage = 'outline' AND episode_number = ? AND batch_id != ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (project_id, episode_number, batch_id),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO creative_episodes (
                        project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                        upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, 'outline', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, episode_number, batch_id, version, parent["id"] if parent else None,
                        json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                        json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                    ),
                )
            conn.execute(
                "UPDATE creative_projects SET status = 'outline_draft', updated_at = ? WHERE id = ?",
                (now, project_id),
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
            candidates.append((key, {
                "batch_id": batch_id,
                "episodes": rows,
                "series_arc": series_arc,
                "stages": list(stages),
                "series_overview": read_series_overview(content.get("series_overview")),
            }))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_outline_batch(self, project_id: int, batch_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        rows = [item for item in project["episodes"] if item["stage"] == "outline" and item["batch_id"] == batch_id]
        overview = read_series_overview((rows[0].get("content") or {}).get("series_overview")) if rows else None
        return {"batch_id": batch_id, "series_overview": overview, "episodes": rows}

    def _validated_outline_overview_rows(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        batch_id: str,
        *,
        require_missing: bool,
        allow_current_locked: bool = False,
    ) -> list[sqlite3.Row]:
        project = conn.execute(
            "SELECT locked_brief_id, character_batch_id, outline_batch_id, target_episode_count "
            "FROM creative_projects WHERE id = ? AND deleted = 0",
            (project_id,),
        ).fetchone()
        if project is None:
            raise ValueError("创作项目不存在")
        all_rows = conn.execute(
            "SELECT id, batch_id, version, status, episode_number, content_json, "
            "upstream_refs_json, updated_at FROM creative_episodes "
            "WHERE project_id = ? AND stage = 'outline'",
            (project_id,),
        ).fetchall()
        rows = [row for row in all_rows if str(row["batch_id"]) == batch_id]
        if not rows:
            raise ValueError("分集大纲批次不存在")
        is_draft = all(row["status"] == "draft" for row in rows)
        is_locked_backfill = bool(
            allow_current_locked
            and all(row["status"] in {"locked", "approved"} for row in rows)
        )
        if not is_draft and not is_locked_backfill:
            raise ValueError("已完成的版本不能原地修改")

        locked_brief_id = int(project["locked_brief_id"] or 0)
        character_batch_id = str(project["character_batch_id"] or "")

        def same_context(row: sqlite3.Row) -> bool:
            refs = json.loads(row["upstream_refs_json"] or "{}")
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
                    float(row["updated_at"] or 0),
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
                json.loads(row["content_json"] or "{}").get("series_overview")
            )
            for row in rows
        ):
            raise ValueError("当前大纲版本已存在全集剧情概览")
        return sorted(rows, key=lambda row: int(row["episode_number"]))

    def assert_outline_overview_generation_ready(
        self, project_id: int, batch_id: str
    ) -> dict[str, Any]:
        """Validate a current complete batch before spending a model request."""

        with self._connect() as conn:
            self._validated_outline_overview_rows(
                conn,
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
    ) -> dict[str, Any]:
        """Atomically update overview metadata, allowing locked one-time backfill."""

        normalized = normalize_series_overview(series_overview)
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            persisted = self._validated_outline_overview_rows(
                conn,
                project_id,
                batch_id,
                require_missing=only_if_missing,
                allow_current_locked=only_if_missing,
            )
            for row in persisted:
                content = json.loads(row["content_json"])
                content["series_overview"] = normalized
                conn.execute(
                    "UPDATE creative_episodes SET content_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), now, int(row["id"])),
                )
        return self.get_outline_batch(project_id, batch_id)

    def update_episode(self, episode_id: int, content: dict[str, Any]) -> dict[str, Any] | None:
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
        if json.dumps(episode.get("content") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ):
            return episode

        now = time.time()
        metadata = dict(episode.get("generation_meta") or {})
        metadata.update({"manual_revision": True, "revision_source_id": int(episode["id"])})
        with self._connect() as conn:
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM creative_episodes "
                "WHERE project_id = ? AND stage = 'script' AND episode_number = ?",
                (int(episode["project_id"]), int(episode["episode_number"])),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                    upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, 'script', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(episode["project_id"]), int(episode["episode_number"]), uuid.uuid4().hex,
                    int(version_row["version"]), int(episode["id"]),
                    json.dumps(content, ensure_ascii=False),
                    json.dumps(episode.get("upstream_refs") or {}, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False), episode.get("workflow_task_id"), now, now,
                ),
            )
        return self.get_episode(int(cursor.lastrowid)) or {}

    def lock_outline_batch(
        self,
        project_id: int,
        batch_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        self._assert_expected_revision(project, expected_revision)
        rows = [item for item in project["episodes"] if item["stage"] == "outline" and item["batch_id"] == batch_id]
        if len(rows) != int(project["target_episode_count"]):
            raise ValueError("分集大纲不完整，不能锁定")
        if (
            project.get("outline_batch_id") == batch_id
            and all(item.get("status") in {"locked", "approved"} for item in rows)
        ):
            return self._resource_with_project_revision(
                self.get_outline_batch(project_id, batch_id), project_id
            )
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
                (now, now, project_id, batch_id),
            )
            params: list[Any] = [batch_id, now, project_id]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision = ?"
                params.append(int(expected_revision))
            cursor = conn.execute(
                "UPDATE creative_projects SET outline_batch_id = ?, status = 'outline_locked', "
                f"revision = revision + 1, updated_at = ? WHERE id = ?{revision_clause}",
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("project revision changed")
        return self._resource_with_project_revision(
            self.get_outline_batch(project_id, batch_id), project_id
        )

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
            },
        )

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            project = self._operation_project(conn, project_id, include_episodes=True)
            if not project.get("outline_batch_id"):
                raise ValueError("必须先锁定分集大纲")
            if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            outline = self._locked_outline_for_episode(project, episode_number)
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            refs = {
                "brief_id": project["locked_brief_id"],
                "character_batch_id": project["character_batch_id"],
                "outline_batch_id": project["outline_batch_id"],
                "outline_id": outline["id"],
                "outline_version": outline["version"],
                "outline_content_fingerprint": self._outline_content_fingerprint(
                    outline.get("content") or {}
                ),
            }
            now = time.time()
            parent = conn.execute(
                "SELECT id, version FROM creative_episodes "
                "WHERE project_id = ? AND stage = 'beats' AND episode_number = ? "
                "ORDER BY version DESC, id DESC LIMIT 1",
                (project_id, int(episode_number)),
            ).fetchone()
            version = int(parent["version"]) + 1 if parent is not None else 1
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                    upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, 'beats', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, int(episode_number), uuid.uuid4().hex, version,
                    int(parent["id"]) if parent is not None else None,
                    json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                ),
            )
            if update_project_status:
                conn.execute(
                    "UPDATE creative_projects SET status = 'beats_in_progress', updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
            return {"resource_type": "beats", "resource_id": int(cursor.lastrowid)}

        with self._connect() as conn:
            if operation_id:
                return self._run_idempotent_operation(
                    conn,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="beats",
                    request_hash=request_hash,
                    create=create,
                )
            conn.execute("BEGIN IMMEDIATE")
            resource_ref = create(conn)
            return self._hydrate_resource_ref(conn, project_id, resource_ref)

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
            },
        )

        def create(conn: sqlite3.Connection) -> dict[str, Any]:
            project = self._operation_project(conn, project_id, include_episodes=True)
            if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
                raise ValueError("集数超出项目范围")
            beats = self._locked_beats_for_episode(project, episode_number)
            outline = self._locked_outline_for_episode(project, episode_number)
            meta = dict(generation_meta or {})
            if job_id:
                meta["job_id"] = job_id
            refs = {
                "brief_id": project.get("locked_brief_id"),
                "character_batch_id": project.get("character_batch_id"),
                "outline_batch_id": project.get("outline_batch_id"),
                "outline_id": outline["id"],
                "outline_version": outline["version"],
                "outline_content_fingerprint": self._outline_content_fingerprint(
                    outline.get("content") or {}
                ),
                "beats_id": beats["id"],
                "beats_version": beats["version"],
                "beats_content_fingerprint": hashlib.sha256(
                    json.dumps(
                        beats.get("content") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            now = time.time()
            parent = conn.execute(
                "SELECT id, version FROM creative_episodes "
                "WHERE project_id = ? AND stage = 'script' AND episode_number = ? "
                "ORDER BY version DESC, id DESC LIMIT 1",
                (project_id, int(episode_number)),
            ).fetchone()
            version = int(parent["version"]) + 1 if parent is not None else 1
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                    upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, 'script', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, int(episode_number), uuid.uuid4().hex, version,
                    int(parent["id"]) if parent is not None else None,
                    json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                ),
            )
            if update_project_status:
                conn.execute(
                    "UPDATE creative_projects SET status = 'scripts_in_progress', updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
            return {"resource_type": "script", "resource_id": int(cursor.lastrowid)}

        with self._connect() as conn:
            if operation_id:
                return self._run_idempotent_operation(
                    conn,
                    operation_id=operation_id,
                    project_id=project_id,
                    stage="script",
                    request_hash=request_hash,
                    create=create,
                )
            conn.execute("BEGIN IMMEDIATE")
            resource_ref = create(conn)
            return self._hydrate_resource_ref(conn, project_id, resource_ref)

    def _locked_outline_for_episode(self, project: dict[str, Any], episode_number: int) -> dict[str, Any]:
        outlines = [
            item for item in project["episodes"]
            if item["stage"] == "outline"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("batch_id") == project.get("outline_batch_id")
            and item.get("status") in {"locked", "approved"}
        ]
        if not outlines:
            raise ValueError("必须先锁定当前集分集大纲")
        return max(outlines, key=lambda item: (int(item.get("version") or 0), int(item["id"])))

    def _locked_beats_for_episode(self, project: dict[str, Any], episode_number: int) -> dict[str, Any]:
        outline = self._locked_outline_for_episode(project, episode_number)
        beats = [
            item for item in project["episodes"]
            if item["stage"] == "beats"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("status") in {"locked", "approved"}
            and item.get("upstream_refs", {}).get("outline_batch_id") == project.get("outline_batch_id")
            and int(item.get("upstream_refs", {}).get("outline_id") or 0) == int(outline["id"])
            and int(item.get("upstream_refs", {}).get("outline_version") or 0) == int(outline["version"])
            and self._outline_content_fingerprint_matches(
                item.get("upstream_refs", {}).get("outline_content_fingerprint"),
                outline.get("content") or {},
            )
        ]
        if not beats:
            raise ValueError("必须先锁定当前集 Beats")
        return max(beats, key=lambda item: (int(item.get("version") or 0), int(item["id"])))

    def missing_current_locked_beats(self, project_id: int) -> list[int]:
        """Return episodes without Beats locked against the current outline batch."""
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        return [
            episode_number
            for episode_number in range(1, int(project["target_episode_count"]) + 1)
            if self._missing_current_locked_beats(project, episode_number)
        ]

    def _missing_current_locked_beats(self, project: dict[str, Any], episode_number: int) -> bool:
        try:
            self._locked_beats_for_episode(project, episode_number)
        except ValueError:
            return True
        return False

    @staticmethod
    def _content_fingerprint(content: Any) -> str:
        return hashlib.sha256(
            json.dumps(content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _outline_content_fingerprint(cls, content: Any) -> str:
        """Fingerprint only outline fields that can invalidate downstream work."""

        normalized = dict(content) if isinstance(content, dict) else content
        if isinstance(normalized, dict):
            normalized.pop("series_overview", None)
        return cls._content_fingerprint(normalized)

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

    def _locked_generation_context_refs(self, project: dict[str, Any]) -> dict[str, Any]:
        brief = next(
            (item for item in project["blueprints"] if item["id"] == project.get("locked_brief_id")),
            None,
        )
        if not brief:
            raise ValueError("必须先锁定 Brief")
        character_batch_id = project.get("character_batch_id")
        if not character_batch_id:
            raise ValueError("必须先锁定人物关系")
        readable_statuses = {"locked", "approved"}
        characters = [
            item for item in project["characters"]
            if item.get("batch_id") == character_batch_id and item.get("status") in readable_statuses
        ]
        if not characters:
            raise ValueError("必须先锁定人物关系")
        relationships = [
            item for item in project["relationships"]
            if item.get("batch_id") == character_batch_id and item.get("status") in readable_statuses
        ]
        return {
            "brief_id": brief["id"],
            "brief_version": brief.get("version"),
            "brief_content_fingerprint": self._content_fingerprint(brief.get("content") or {}),
            "character_batch_id": character_batch_id,
            "character_content_fingerprint": self._content_fingerprint({
                "characters": [
                    {"id": item["id"], "version": item.get("version"), "content": item.get("content") or {}}
                    for item in sorted(characters, key=lambda item: int(item["id"]))
                ],
                "relationships": [
                    {"id": item["id"], "version": item.get("version"), "content": item.get("content") or {}}
                    for item in sorted(relationships, key=lambda item: int(item["id"]))
                ],
            }),
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
            refs.update({
                "beats_id": beats["id"],
                "beats_version": beats["version"],
                "beats_content_fingerprint": self._content_fingerprint(beats.get("content") or {}),
            })
        return refs

    def _episode_generation_refs_match(
        self,
        project: dict[str, Any],
        stage: str,
        episode_number: int,
        saved_refs: Any,
    ) -> bool:
        """Compare generation refs while accepting legacy outline hashes."""

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
        existing = project["episodes"]
        prepared = []
        for episode_number, content in sorted(items, key=lambda item: int(item[0])):
            episode_number = int(episode_number)
            normalized = validate_beats(content) if stage == "beats" else validate_script(content)
            refs = self._episode_generation_refs(project, stage, episode_number)
            prior = [
                item for item in existing
                if item["stage"] == stage and int(item["episode_number"]) == episode_number
            ]
            version = 1 + max([int(item["version"]) for item in prior] or [0])
            parent_id = max(prior, key=lambda item: (int(item["version"]), int(item["id"]))).get("id") if prior else None
            prepared.append((episode_number, normalized, refs, version, parent_id))

        now = time.time()
        ids: list[int] = []
        with self._connect() as conn:
            for episode_number, content, refs, version, parent_id in prepared:
                cursor = conn.execute(
                    """
                    INSERT INTO creative_episodes (
                        project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                        upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, stage, episode_number, batch_id, version, parent_id,
                        json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                        json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"),
                        now, now,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            project_status = "beats_in_progress" if stage == "beats" else "scripts_in_progress"
            conn.execute(
                "UPDATE creative_projects SET status = ?, updated_at = ? WHERE id = ?",
                (project_status, now, project_id),
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
                    self._episode_generation_refs_match(
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
            candidates.append((
                (len(rows), max(float(item.get("updated_at") or 0) for item in rows)),
                {"batch_id": batch_id, "episodes": rows, "completed_episode_count": len(rows)},
            ))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_episode(self, episode_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_episodes WHERE id = ?", (episode_id,)).fetchone()
        return self._episode(dict(row)) if row else None

    def list_episode_versions(
        self,
        project_id: int,
        stage: str,
        episode_number: int,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_episodes "
                "WHERE project_id = ? AND stage = ? AND episode_number = ? "
                "ORDER BY version ASC, id ASC",
                (project_id, stage, int(episode_number)),
            ).fetchall()
        return [self._episode(dict(row)) for row in rows]

    def create_script_review(
        self,
        *,
        source_type: str,
        mode: str,
        project_id: int | None,
        episode_numbers: list[int],
        source_fingerprint: str,
        source_payload: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO script_reviews (
                    project_id, source_type, mode, episode_numbers_json,
                    source_fingerprint, source_payload_json, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    source_type,
                    mode,
                    json.dumps(episode_numbers, ensure_ascii=False),
                    source_fingerprint,
                    json.dumps(source_payload, ensure_ascii=False),
                    created_by,
                    now,
                    now,
                ),
            )
        return self.get_script_review(int(cursor.lastrowid)) or {}

    def get_script_review(self, review_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM script_reviews WHERE id = ?", (review_id,)
            ).fetchone()
        return self._script_review(dict(row)) if row else None

    def list_script_reviews(
        self,
        *,
        created_by: str | None = None,
        project_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if created_by is not None:
            clauses.append("created_by = ?")
            params.append(created_by)
        if project_ids is not None:
            if project_ids:
                placeholders = ",".join("?" for _ in project_ids)
                clauses.append(f"project_id IN ({placeholders})")
                params.extend(sorted(project_ids))
            else:
                clauses.append("project_id IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM script_reviews{where} ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
        return [self._script_review(dict(row)) for row in rows]

    def delete_script_review(self, review_id: int) -> None:
        """Remove one review and its review-scoped optimization drafts."""
        with self._connect() as conn:
            review = conn.execute(
                "SELECT id FROM script_reviews WHERE id = ?", (review_id,)
            ).fetchone()
            if review is None:
                raise ValueError("审核记录不存在")
            optimization_ids = [
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM script_optimizations WHERE review_id = ?", (review_id,)
                ).fetchall()
            ]
            if optimization_ids:
                placeholders = ",".join("?" for _ in optimization_ids)
                conn.execute(
                    f"DELETE FROM script_optimization_drafts WHERE optimization_id IN ({placeholders})",
                    optimization_ids,
                )
            conn.execute(
                "DELETE FROM script_optimizations WHERE review_id = ?", (review_id,)
            )
            conn.execute("DELETE FROM script_reviews WHERE id = ?", (review_id,))

    def set_script_review_job(self, review_id: int, job_id: str) -> dict[str, Any]:
        return self._update_script_review(review_id, {"job_id": job_id})

    def start_script_review(self, review_id: int) -> dict[str, Any]:
        return self._update_script_review(review_id, {"status": "running", "error": ""})

    def complete_script_review(
        self, review_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        return self._update_script_review(
            review_id,
            {
                "status": "pending_confirmation",
                "ai_verdict": str(report["ai_verdict"]),
                "report_json": json.dumps(report, ensure_ascii=False),
                "error": "",
            },
        )

    def checkpoint_script_review(
        self, review_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a resumable, non-final report while a review is still running."""
        return self._update_script_review(
            review_id,
            {
                "status": "running",
                "report_json": json.dumps(report, ensure_ascii=False),
                "error": "",
            },
        )

    def fail_script_review(self, review_id: int, error: str) -> dict[str, Any]:
        return self._update_script_review(
            review_id, {"status": "failed", "error": str(error)[:2000]}
        )

    def cancel_script_review(self, review_id: int, reason: str = "") -> dict[str, Any]:
        return self._update_script_review(
            review_id,
            {
                "status": "cancelled",
                "error": str(reason or "审核任务已取消")[:2000],
            },
        )

    def confirm_script_review(
        self,
        review_id: int,
        final_verdict: str,
        confirmation_note: str,
        confirmed_by: str,
        current_fingerprint: str,
    ) -> dict[str, Any]:
        if final_verdict not in {"通过", "建议修改"}:
            raise ValueError("最终结论必须是通过或建议修改")
        review = self.get_script_review(review_id)
        if not review:
            raise ValueError("审核记录不存在")
        if review["source_fingerprint"] != current_fingerprint:
            self.mark_script_review_stale_if_changed(review_id, current_fingerprint)
            raise ValueError("剧本已更新，请重新审核后再确认")
        if review["status"] != "pending_confirmation":
            raise ValueError("该审核记录当前不能确认")
        return self._update_script_review(
            review_id,
            {
                "status": "confirmed",
                "final_verdict": final_verdict,
                "confirmation_note": confirmation_note.strip(),
                "confirmed_by": confirmed_by,
                "confirmed_at": time.time(),
            },
        )

    def mark_script_review_stale_if_changed(
        self, review_id: int, current_fingerprint: str
    ) -> dict[str, Any]:
        """Keep an unconfirmed project review from being presented as current."""
        review = self.get_script_review(review_id)
        if not review:
            raise ValueError("审核记录不存在")
        if (
            review["source_type"] != "project"
            or review["status"] != "pending_confirmation"
            or review["source_fingerprint"] == current_fingerprint
        ):
            return review
        return self._update_script_review(
            review_id,
            {"status": "stale", "error": "剧本已更新，请重新审核后再确认"},
        )

    def _update_script_review(
        self, review_id: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        assignments = [f"{key} = ?" for key in values]
        params = [*values.values(), time.time(), review_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE script_reviews SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                params,
            )
        return self.get_script_review(review_id) or {}

    def create_script_optimization(
        self,
        *,
        review_id: int,
        project_id: int,
        mode: str,
        source_fingerprint: str,
        selected_findings: list[dict[str, Any]],
        target_episode_numbers: list[int],
        created_by: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO script_optimizations (
                    review_id, project_id, mode, source_fingerprint,
                    selected_findings_json, target_episode_numbers_json,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    project_id,
                    mode,
                    source_fingerprint,
                    json.dumps(selected_findings, ensure_ascii=False),
                    json.dumps(target_episode_numbers, ensure_ascii=False),
                    created_by,
                    now,
                    now,
                ),
            )
        return self.get_script_optimization(int(cursor.lastrowid)) or {}

    def get_script_optimization(self, optimization_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM script_optimizations WHERE id = ?", (optimization_id,)
            ).fetchone()
            if not row:
                return None
            result = self._script_optimization(dict(row))
            drafts = conn.execute(
                "SELECT * FROM script_optimization_drafts WHERE optimization_id = ? ORDER BY episode_number, kind, id",
                (optimization_id,),
            ).fetchall()
        result["drafts"] = [self._script_optimization_draft(dict(item)) for item in drafts]
        return result

    def list_script_optimizations(self, review_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM script_optimizations WHERE review_id = ? ORDER BY created_at DESC, id DESC",
                (review_id,),
            ).fetchall()
        return [self.get_script_optimization(int(row["id"])) or {} for row in rows]

    def start_script_optimization(self, optimization_id: int) -> dict[str, Any]:
        return self._update_script_optimization(
            optimization_id, {"status": "running", "error": ""}
        )

    def set_script_optimization_job(
        self, optimization_id: int, job_id: str
    ) -> dict[str, Any]:
        return self._update_script_optimization(optimization_id, {"job_id": job_id})

    def complete_script_optimization(
        self, optimization_id: int, drafts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        optimization = self.get_script_optimization(optimization_id)
        if not optimization:
            raise ValueError("优化任务不存在")
        now = time.time()
        with self._connect() as conn:
            inserted: list[tuple[dict[str, Any], int]] = []
            for draft in drafts:
                cursor = conn.execute(
                    """
                    INSERT INTO script_optimization_drafts (
                        optimization_id, project_id, episode_number, kind,
                        source_episode_id, source_fingerprint, content_json,
                        peer_draft_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        optimization_id,
                        int(draft.get("project_id") or optimization["project_id"]),
                        int(draft["episode_number"]),
                        str(draft["kind"]),
                        draft.get("source_episode_id"),
                        str(draft.get("source_fingerprint") or ""),
                        json.dumps(draft.get("content") or {}, ensure_ascii=False),
                        draft.get("peer_draft_id"),
                        now,
                        now,
                    ),
                )
                inserted.append((draft, int(cursor.lastrowid)))
            if optimization["mode"] == "structure":
                pairs: dict[int, dict[str, int]] = {}
                for draft, draft_id in inserted:
                    kind = str(draft.get("kind") or "")
                    if kind in {"beats", "script"}:
                        pairs.setdefault(int(draft["episode_number"]), {})[kind] = draft_id
                for pair in pairs.values():
                    if "beats" in pair and "script" in pair:
                        conn.execute(
                            "UPDATE script_optimization_drafts SET peer_draft_id = ? WHERE id = ?",
                            (pair["script"], pair["beats"]),
                        )
                        conn.execute(
                            "UPDATE script_optimization_drafts SET peer_draft_id = ? WHERE id = ?",
                            (pair["beats"], pair["script"]),
                        )
            conn.execute(
                "UPDATE script_optimizations SET status = 'completed', error = '', updated_at = ? WHERE id = ?",
                (now, optimization_id),
            )
        return self.get_script_optimization(optimization_id) or {}

    def fail_script_optimization(
        self, optimization_id: int, error: str
    ) -> dict[str, Any]:
        return self._update_script_optimization(
            optimization_id, {"status": "failed", "error": str(error)[:2000]}
        )

    def get_script_optimization_draft(self, draft_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM script_optimization_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        return self._script_optimization_draft(dict(row)) if row else None

    def list_episode_script_optimization_drafts(
        self, project_id: int, episode_number: int, kind: str
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM script_optimization_drafts
                WHERE project_id = ? AND episode_number = ? AND kind = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id, episode_number, kind),
            ).fetchall()
        return [self._script_optimization_draft(dict(row)) for row in rows]

    def accept_script_optimization_draft(
        self, draft_id: int, episode_id: int, accepted_by: str
    ) -> dict[str, Any]:
        draft = self.get_script_optimization_draft(draft_id)
        if not draft:
            raise ValueError("优化草稿不存在")
        if draft["status"] != "ready":
            raise ValueError("该优化草稿当前不能采纳")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE script_optimization_drafts
                SET status = 'accepted', accepted_episode_id = ?, accepted_by = ?,
                    accepted_at = ?, updated_at = ? WHERE id = ?
                """,
                (episode_id, accepted_by, time.time(), time.time(), draft_id),
            )
        return self.get_script_optimization_draft(draft_id) or {}

    def reject_script_optimization_draft(
        self, draft_id: int, rejection_note: str
    ) -> dict[str, Any]:
        draft = self.get_script_optimization_draft(draft_id)
        if not draft:
            raise ValueError("优化草稿不存在")
        if draft["status"] != "ready":
            raise ValueError("该优化草稿当前不能拒绝")
        with self._connect() as conn:
            conn.execute(
                "UPDATE script_optimization_drafts SET status = 'rejected', rejection_note = ?, updated_at = ? WHERE id = ?",
                (rejection_note.strip(), time.time(), draft_id),
            )
        return self.get_script_optimization_draft(draft_id) or {}

    def _update_script_optimization(
        self, optimization_id: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        assignments = [f"{key} = ?" for key in values]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE script_optimizations SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
                [*values.values(), time.time(), optimization_id],
            )
        return self.get_script_optimization(optimization_id) or {}

    def lock_episode_beats(self, episode_id: int) -> dict[str, Any]:
        episode = self.get_episode(episode_id)
        if not episode or episode["stage"] != "beats":
            raise ValueError("单集 Beats 不存在")
        validate_beats(episode.get("content") or {}, allow_legacy=True)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? WHERE id = ?",
                (now, now, episode_id),
            )
        return self.get_episode(episode_id) or {}

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
                item for item in project.get("episodes", [])
                if item.get("stage") == "beats"
                and int(item.get("episode_number") or 0) == episode_number
                and str((item.get("upstream_refs") or {}).get("outline_batch_id") or "") == outline_batch_id
                and item.get("status") in {"draft", "locked", "approved"}
            ]
            if not candidates:
                missing.append(episode_number)
                continue
            item = max(candidates, key=lambda value: (int(value.get("version") or 0), int(value["id"])))
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
            raise ValueError(f"以下集数缺少当前版本 Beats 草稿：{'、'.join(map(str, missing))}")
        if stale:
            raise ValueError(f"以下集数 Beats 引用的分集大纲已过期：{'、'.join(map(str, stale))}")
        drafts = [item for item in selected if item.get("status") == "draft"]
        if not drafts:
            raise ValueError("当前没有可批量锁定的 Beats 草稿")
        for item in drafts:
            validate_beats(item.get("content") or {}, allow_legacy=True)
        return drafts

    def lock_current_episode_beats_batch(self, project_id: int) -> list[dict[str, Any]]:
        """Atomically lock all Beats drafts returned by batch_lockable_episode_beats."""
        drafts = self.batch_lockable_episode_beats(project_id)
        now = time.time()
        ids = [int(item["id"]) for item in drafts]
        placeholders = ", ".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? "
                f"WHERE stage = 'beats' AND status = 'draft' AND id IN ({placeholders})",
                (now, now, *ids),
            )
        return [self.get_episode(episode_id) or {} for episode_id in ids]

    def lock_episode_script(self, episode_id: int) -> dict[str, Any]:
        episode = self.get_episode(episode_id)
        if not episode or episode["stage"] != "script":
            raise ValueError("单集场次剧本不存在")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? WHERE id = ?",
                (now, now, episode_id),
            )
        return self.get_episode(episode_id) or {}

    def record_stage_export(
        self,
        project_id: int,
        stage: str,
        format: str,
        content_fingerprint: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creative_stage_exports (
                    project_id, stage, format, content_fingerprint, exported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, stage, format, content_fingerprint, time.time()),
            )

    def has_stage_export(self, project_id: int, stage: str, content_fingerprint: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM creative_stage_exports
                WHERE project_id = ? AND stage = ? AND content_fingerprint = ?
                LIMIT 1
                """,
                (project_id, stage, content_fingerprint),
            ).fetchone()
        return bool(row)

    def _update_creative_item(
        self,
        table: str,
        item_id: int,
        content: dict[str, Any],
        *,
        episode: bool = False,
        preserve_content_keys: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        if table not in {"creative_characters", "creative_relationships", "creative_episodes"}:
            raise ValueError("invalid creative table")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return None
            if row["status"] != "draft":
                raise ValueError("已完成的版本不能原地修改")
            resolved_content = dict(content)
            current_content = json.loads(row["content_json"] or "{}")
            for key in preserve_content_keys:
                if key in current_content:
                    resolved_content[key] = current_content[key]
                else:
                    resolved_content.pop(key, None)
            conn.execute(
                f"UPDATE {table} SET content_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(resolved_content, ensure_ascii=False), time.time(), item_id),
            )
            updated = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
        return self._episode(dict(updated)) if episode else self._creative_item(dict(updated))

    def _get_creative_item(
        self, table: str, item_id: int
    ) -> dict[str, Any] | None:
        if table not in {"creative_characters", "creative_relationships"}:
            raise ValueError("invalid creative table")
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (item_id,)
            ).fetchone()
        return self._creative_item(dict(row)) if row else None

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

    @staticmethod
    def _blueprint(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        row["source_refs"] = json.loads(row.pop("source_refs_json") or "{}")
        row["generation_meta"] = json.loads(row.pop("generation_meta_json") or "{}")
        return row

    @staticmethod
    def _seed(row: dict[str, Any]) -> dict[str, Any]:
        row["constraints"] = json.loads(row.pop("constraints_json") or "{}")
        row["reference_refs"] = json.loads(row.pop("reference_refs_json") or "[]")
        return row

    @staticmethod
    def _creative_item(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        row["source_refs"] = json.loads(row.pop("source_refs_json") or "{}")
        row["generation_meta"] = json.loads(row.pop("generation_meta_json") or "{}")
        return row

    @staticmethod
    def _script_review(row: dict[str, Any]) -> dict[str, Any]:
        row["episode_numbers"] = json.loads(row.pop("episode_numbers_json") or "[]")
        row["source_payload"] = json.loads(row.pop("source_payload_json") or "{}")
        row["report"] = json.loads(row.pop("report_json") or "{}")
        return row

    @staticmethod
    def _script_optimization(row: dict[str, Any]) -> dict[str, Any]:
        row["selected_findings"] = json.loads(row.pop("selected_findings_json") or "[]")
        row["target_episode_numbers"] = json.loads(
            row.pop("target_episode_numbers_json") or "[]"
        )
        return row

    @staticmethod
    def _script_optimization_draft(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        if row.get("kind") == "script":
            row["content"] = normalize_script_content(row["content"])
        return row

    @staticmethod
    def _episode(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        if row.get("stage") == "script":
            row["content"] = normalize_script_content(row["content"])
        row["upstream_refs"] = json.loads(row.pop("upstream_refs_json") or "{}")
        row["generation_meta"] = json.loads(row.pop("generation_meta_json") or "{}")
        return row

    @staticmethod
    def _topic(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("score_details_json", "evidence_json"):
            row[key.removesuffix("_json")] = json.loads(row.pop(key) or "{}")
        row["score_details"].pop("imitability", None)
        row["score_details"].pop("production_cost", None)
        raw_platforms = json.loads(row.pop("source_platforms_json", "[]") or "[]")
        row["source_platforms"] = sorted({str(item) for item in raw_platforms if str(item)}) or [str(row.get("platform") or "")]
        if not str(row.get("rank_source") or "").strip():
            row["rank_source"] = str(row.get("rank_type") or "").strip()
        if str(row.get("category") or "").strip() in CreativeRepository._NON_GENRE_LABELS:
            row["category"] = "待补充"
        return row

    @staticmethod
    def _merge_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse same-title records while retaining every platform and evidence."""
        merged: dict[str, dict[str, Any]] = {}
        for topic in topics:
            key = CreativeRepository._title_key(topic.get("title"))
            if not key:
                continue
            current = merged.get(key)
            if current is None:
                current = dict(topic)
                current["source_platforms"] = sorted(set(current.get("source_platforms") or [current.get("platform", "")]))
                current["source_paths"] = [current["source_path"]] if current.get("source_path") else []
                current["_decision_updated_at"] = (
                    float(current.get("updated_at") or 0)
                    if current.get("status") != "pending_review" else -1.0
                )
                merged[key] = current
                continue
            current["source_platforms"] = sorted(set(current.get("source_platforms", [])) | set(topic.get("source_platforms", [])) | {str(topic.get("platform") or "")})
            current["source_paths"] = sorted(set(current.get("source_paths", [])) | ({topic["source_path"]} if topic.get("source_path") else set()))
            current["rank_source"] = CreativeRepository._join_rank_sources(
                current.get("rank_source"), topic.get("rank_source")
            )
            evidence = current.setdefault("evidence", [])
            seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in evidence}
            for item in topic.get("evidence") or []:
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if marker not in seen:
                    evidence.append(item)
                    seen.add(marker)
            decision_updated_at = (
                float(topic.get("updated_at") or 0)
                if topic.get("status") != "pending_review" else -1.0
            )
            if decision_updated_at >= float(current.get("_decision_updated_at") or -1.0):
                current["status"] = topic.get("status") or "pending_review"
                current["decision_note"] = topic.get("decision_note") or ""
                current["_decision_updated_at"] = decision_updated_at
            if current.get("confidence") != "high" and topic.get("confidence") == "high":
                current["confidence"] = "high"
            if not current.get("locked_breakdown_id") and topic.get("locked_breakdown_id"):
                current["locked_breakdown_id"] = topic["locked_breakdown_id"]
            if topic.get("latest_breakdown_id") and int(topic["latest_breakdown_id"]) > int(current.get("latest_breakdown_id") or 0):
                current["id"] = topic["id"]
                current["latest_breakdown_id"] = topic["latest_breakdown_id"]
                current["latest_breakdown_version"] = topic.get("latest_breakdown_version")
                current["latest_breakdown_status"] = topic.get("latest_breakdown_status")
        for topic in merged.values():
            topic.pop("_decision_updated_at", None)
        return list(merged.values())

    @staticmethod
    def _join_rank_sources(*values: Any) -> str:
        sources: list[str] = []
        for value in values:
            for source in str(value or "").split("|"):
                source = source.strip()
                if source and source not in sources:
                    sources.append(source)
        return "|".join(sources)

    @staticmethod
    def _matches_rank_source(topic: dict[str, Any], rank_source: str) -> bool:
        selected = str(rank_source or "").strip()
        sources = [
            source.strip()
            for source in str(topic.get("rank_source") or topic.get("rank_type") or "").split("|")
            if source.strip()
        ]
        if str(topic.get("platform") or "") == "hg":
            return any(source.startswith(selected) for source in sources)
        return selected in sources

    @staticmethod
    def _title_key(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(char for char in normalized if char.isalnum() or "\u4e00" <= char <= "\u9fff")

    @staticmethod
    def _breakdown(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        row["field_sources"] = json.loads(row.pop("field_sources_json") or "{}")
        row["risks"] = json.loads(row.pop("risks_json") or "[]")
        return row
