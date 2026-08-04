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
from typing import Any

from creative.script_service import validate_beats, validate_script


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
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, job_id TEXT NOT NULL, source_path TEXT NOT NULL,
                    captured_at TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', category TEXT,
                    rank_type TEXT NOT NULL DEFAULT '',
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
                    topic_id INTEGER NOT NULL, breakdown_id INTEGER NOT NULL,
                    name TEXT NOT NULL, genre TEXT NOT NULL DEFAULT '',
                    target_platform TEXT NOT NULL DEFAULT '', target_audience TEXT NOT NULL DEFAULT '',
                    target_episode_count INTEGER NOT NULL DEFAULT 20,
                    episode_duration_seconds INTEGER NOT NULL DEFAULT 120,
                    status TEXT NOT NULL DEFAULT 'created',
                    locked_brief_id INTEGER, character_batch_id TEXT, outline_batch_id TEXT,
                    workflow_task_id TEXT, creation_workflow_task_id TEXT,
                    creation_request_id TEXT,
                    created_by TEXT NOT NULL DEFAULT 'local', deleted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(topic_id) REFERENCES creative_topics(id),
                    FOREIGN KEY(breakdown_id) REFERENCES creative_breakdowns(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS creative_blueprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL, candidate_group_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1, parent_version_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'draft', content_json TEXT NOT NULL,
                    risk_score REAL NOT NULL DEFAULT 0, risk_level TEXT NOT NULL DEFAULT 'low',
                    difference_note TEXT NOT NULL DEFAULT '', source_refs_json TEXT NOT NULL DEFAULT '{}',
                    generation_meta_json TEXT NOT NULL DEFAULT '{}', locked_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id),
                    UNIQUE(project_id, candidate_group_id, version)
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
                        platform, job_id, source_path, captured_at, title, description, category, rank_type, score,
                        confidence, score_details_json, recommendation, evidence_json, source_platforms_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, job_id, source_path, title) DO UPDATE SET
                        captured_at=excluded.captured_at, description=excluded.description, category=excluded.category, rank_type=excluded.rank_type, score=excluded.score, confidence=excluded.confidence,
                        score_details_json=excluded.score_details_json, recommendation=excluded.recommendation,
                        evidence_json=excluded.evidence_json, source_platforms_json=excluded.source_platforms_json,
                        updated_at=excluded.updated_at
                """, (
                    topic["platform"], topic["job_id"], topic["source_path"], topic["captured_at"],
                    topic["title"], topic.get("description", ""), topic.get("category", ""), topic.get("rank_type", ""), topic["score"], topic["confidence"],
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
                    platform, job_id, source_path, captured_at, title, description, category, rank_type,
                    status, score, confidence, score_details_json, recommendation, evidence_json,
                    source_platforms_json, decision_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'adopted', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    topic["platform"], topic["job_id"], topic["source_path"], topic["captured_at"],
                    topic["title"], topic.get("description", ""), topic.get("category", ""),
                    topic.get("rank_type", ""), topic["score"], topic["confidence"],
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
        if limit is not None:
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
        clauses = ["p.deleted = 0"]
        params: list[Any] = []
        if topic_id is not None:
            clauses.append("p.topic_id = ?")
            params.append(topic_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.*, t.title AS topic_title
                FROM creative_projects p JOIN creative_topics t ON t.id = p.topic_id
                WHERE {' AND '.join(clauses)} ORDER BY p.updated_at DESC, p.id DESC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_project(self, project_id: int, values: dict[str, Any]) -> dict[str, Any]:
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
        if "name" in updates and not str(updates["name"]).strip():
            raise ValueError("项目名称不能为空")
        if "status" in updates and updates["status"] != "archived":
            raise ValueError("项目仅支持归档状态更新")
        assignments = ", ".join(f"{key} = ?" for key in updates)
        params = [str(value).strip() if isinstance(value, str) else value for value in updates.values()]
        params.extend([time.time(), project_id])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE creative_projects SET {assignments}, updated_at = ? WHERE id = ? AND deleted = 0",
                params,
            )
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
            conn.execute("DELETE FROM creative_episodes WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_relationships WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_characters WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM creative_blueprints WHERE project_id = ?", (project_id,))
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
                JOIN creative_topics t ON t.id = p.topic_id
                JOIN creative_breakdowns b ON b.id = p.breakdown_id
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
    ) -> list[dict[str, Any]]:
        if not 1 <= len(items) <= 5:
            raise ValueError("仿制方向数量必须在 1-5 之间")
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        now = time.time()
        refs = {"topic_id": project["topic_id"], "breakdown_id": project["breakdown_id"]}
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        ids: list[int] = []
        with self._connect() as conn:
            for item in items:
                cursor = conn.execute(
                    """
                    INSERT INTO creative_blueprints (
                        project_id, candidate_group_id, content_json, risk_score, risk_level,
                        difference_note, source_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, uuid.uuid4().hex,
                        json.dumps(item.get("content") or item, ensure_ascii=False),
                        float(item.get("risk_score") or 0), str(item.get("risk_level") or "low"),
                        str(item.get("difference_note") or ""), json.dumps(refs, ensure_ascii=False),
                        json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                    ),
                )
                ids.append(int(cursor.lastrowid))
            conn.execute(
                "UPDATE creative_projects SET status = 'direction_review', updated_at = ? WHERE id = ?",
                (now, project_id),
            )
        return [self.get_blueprint(item_id) or {} for item_id in ids]

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
                    generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    current["project_id"], current["candidate_group_id"], int(version_row["version"]) + 1,
                    blueprint_id, json.dumps(current["content"], ensure_ascii=False),
                    current["risk_score"], current["risk_level"], current["difference_note"],
                    json.dumps(current["source_refs"], ensure_ascii=False),
                    json.dumps(current["generation_meta"], ensure_ascii=False),
                    workflow_task_id or current.get("workflow_task_id"), now, now,
                ),
            )
        return self.get_blueprint(int(cursor.lastrowid)) or {}

    def lock_brief(self, blueprint_id: int, difference_note: str = "") -> dict[str, Any]:
        current = self.get_blueprint(blueprint_id)
        if not current:
            raise ValueError("仿制方向不存在")
        if current["status"] == "brief_locked":
            return current
        note = difference_note.strip() or str(current.get("difference_note") or "").strip()
        if current["risk_level"] == "high" and not note:
            raise ValueError("高风险方向必须填写差异化修改说明")
        project = self.get_project(int(current["project_id"]))
        if not project:
            raise ValueError("创作项目不存在")
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
            conn.execute(
                "UPDATE creative_projects SET locked_brief_id = ?, character_batch_id = NULL, outline_batch_id = NULL, status = 'brief_locked', updated_at = ? WHERE id = ?",
                (blueprint_id, now, current["project_id"]),
            )
        return self.get_blueprint(blueprint_id) or {}

    def create_character_batch(
        self,
        project_id: int,
        characters: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if not project.get("locked_brief_id"):
            raise ValueError("必须先锁定 Brief")
        batch_id = uuid.uuid4().hex
        version = 1 + max([int(item["version"]) for item in project["characters"]] or [0])
        now = time.time()
        refs = {"brief_id": project["locked_brief_id"]}
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        with self._connect() as conn:
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
        return self.get_character_batch(project_id, batch_id)

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

    def lock_character_batch(self, project_id: int, batch_id: str) -> dict[str, Any]:
        batch = self.get_character_batch(project_id, batch_id)
        if not batch["characters"]:
            raise ValueError("人物关系版本至少需要一个人物")
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
            conn.execute(
                "UPDATE creative_projects SET character_batch_id = ?, outline_batch_id = NULL, status = 'characters_locked', updated_at = ? WHERE id = ?",
                (batch_id, now, project_id),
            )
        return self.get_character_batch(project_id, batch_id)

    def create_outline_batch(
        self,
        project_id: int,
        episodes: list[dict[str, Any]],
        series_arc: str,
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if not project.get("character_batch_id"):
            raise ValueError("必须先锁定人物关系版本")
        expected = list(range(1, int(project["target_episode_count"]) + 1))
        actual = sorted(int(item.get("episode") or 0) for item in episodes)
        if actual != expected:
            raise ValueError(f"分集大纲必须完整覆盖 1-{project['target_episode_count']} 集")
        batch_id = uuid.uuid4().hex
        version = 1 + max(
            [int(item["version"]) for item in project["episodes"] if item["stage"] == "outline"] or [0]
        )
        refs = {
            "brief_id": project["locked_brief_id"],
            "character_batch_id": project["character_batch_id"],
        }
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        now = time.time()
        with self._connect() as conn:
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
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
        return self.get_outline_batch(project_id, batch_id)

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
        with self._connect() as conn:
            for episode in episodes:
                content = dict(episode)
                content["series_arc"] = series_arc
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

    def find_resumable_outline_batch(self, project_id: int) -> dict[str, Any] | None:
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
                and refs.get("brief_id") == project.get("locked_brief_id")
                and refs.get("character_batch_id") == project.get("character_batch_id")
            ):
                groups.setdefault(str(item["batch_id"]), []).append(item)

        candidates: list[tuple[tuple[int, float, int], dict[str, Any]]] = []
        for batch_id, rows in groups.items():
            rows.sort(key=lambda item: int(item["episode_number"]))
            numbers = [int(item["episode_number"]) for item in rows]
            if not rows or len(rows) >= total or numbers != list(range(1, len(rows) + 1)):
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
            }))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_outline_batch(self, project_id: int, batch_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        rows = [item for item in project["episodes"] if item["stage"] == "outline" and item["batch_id"] == batch_id]
        return {"batch_id": batch_id, "episodes": rows}

    def update_episode(self, episode_id: int, content: dict[str, Any]) -> dict[str, Any] | None:
        episode = self.get_episode(episode_id)
        if episode:
            if episode.get("stage") == "beats":
                content = validate_beats(content, allow_legacy=True)
            elif episode.get("stage") == "script":
                content = validate_script(content)
        return self._update_creative_item("creative_episodes", episode_id, content, episode=True)

    def lock_outline_batch(self, project_id: int, batch_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        rows = [item for item in project["episodes"] if item["stage"] == "outline" and item["batch_id"] == batch_id]
        if len(rows) != int(project["target_episode_count"]):
            raise ValueError("分集大纲不完整，不能锁定")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE creative_episodes SET status = 'locked', locked_at = ?, updated_at = ? WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
                (now, now, project_id, batch_id),
            )
            conn.execute(
                "UPDATE creative_projects SET outline_batch_id = ?, status = 'outline_locked', updated_at = ? WHERE id = ?",
                (batch_id, now, project_id),
            )
        return self.get_outline_batch(project_id, batch_id)

    def create_episode_beats(
        self,
        project_id: int,
        episode_number: int,
        content: dict[str, Any],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        update_project_status: bool = True,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if not project.get("outline_batch_id"):
            raise ValueError("必须先锁定分集大纲")
        if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
            raise ValueError("集数超出项目范围")
        outlines = [
            item for item in project["episodes"]
            if item["stage"] == "outline"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("batch_id") == project.get("outline_batch_id")
            and item.get("status") in {"locked", "approved"}
        ]
        if not outlines:
            raise ValueError("必须先锁定当前集分集大纲")
        outline = max(outlines, key=lambda item: (int(item.get("version") or 0), int(item["id"])))
        version = 1 + max(
            [int(item["version"]) for item in project["episodes"] if item["stage"] == "beats" and int(item["episode_number"]) == int(episode_number)] or [0]
        )
        parent_id = next(
            (
                int(item["id"]) for item in sorted(
                    [item for item in project["episodes"] if item["stage"] == "beats" and int(item["episode_number"]) == int(episode_number)],
                    key=lambda item: int(item["version"]),
                    reverse=True,
                )
            ),
            None,
        )
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        refs = {
            "brief_id": project["locked_brief_id"],
            "character_batch_id": project["character_batch_id"],
            "outline_batch_id": project["outline_batch_id"],
            "outline_id": outline["id"],
            "outline_version": outline["version"],
            "outline_content_fingerprint": hashlib.sha256(
                json.dumps(outline.get("content") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                    upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, 'beats', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, int(episode_number), uuid.uuid4().hex, version, parent_id,
                    json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                ),
            )
            if update_project_status:
                conn.execute(
                    "UPDATE creative_projects SET status = 'beats_in_progress', updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
        return self.get_episode(int(cursor.lastrowid)) or {}

    def create_episode_script(
        self,
        project_id: int,
        episode_number: int,
        content: dict[str, Any],
        job_id: str = "",
        generation_meta: dict[str, Any] | None = None,
        workflow_task_id: str | None = None,
        update_project_status: bool = True,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise ValueError("创作项目不存在")
        if not 1 <= int(episode_number) <= int(project["target_episode_count"]):
            raise ValueError("集数超出项目范围")
        locked_beats = sorted(
            [
                item for item in project["episodes"]
                if item["stage"] == "beats"
                and int(item["episode_number"]) == int(episode_number)
                and item.get("status") in {"locked", "approved"}
            ],
            key=lambda item: (int(item.get("version") or 0), int(item["id"])),
            reverse=True,
        )
        if not locked_beats:
            raise ValueError("必须先锁定当前集 Beats")
        beats = locked_beats[0]
        outlines = [
            item for item in project["episodes"]
            if item["stage"] == "outline"
            and int(item["episode_number"]) == int(episode_number)
            and item.get("batch_id") == project.get("outline_batch_id")
            and item.get("status") in {"locked", "approved"}
        ]
        if not outlines:
            raise ValueError("必须先锁定当前集分集大纲")
        outline = max(outlines, key=lambda item: (int(item.get("version") or 0), int(item["id"])))
        version = 1 + max(
            [
                int(item["version"]) for item in project["episodes"]
                if item["stage"] == "script" and int(item["episode_number"]) == int(episode_number)
            ] or [0]
        )
        parent_id = next(
            (
                int(item["id"]) for item in sorted(
                    [
                        item for item in project["episodes"]
                        if item["stage"] == "script" and int(item["episode_number"]) == int(episode_number)
                    ],
                    key=lambda item: int(item["version"]),
                    reverse=True,
                )
            ),
            None,
        )
        meta = dict(generation_meta or {})
        if job_id:
            meta["job_id"] = job_id
        refs = {
            "brief_id": project.get("locked_brief_id"),
            "character_batch_id": project.get("character_batch_id"),
            "outline_batch_id": project.get("outline_batch_id"),
            "outline_id": outline["id"],
            "outline_version": outline["version"],
            "outline_content_fingerprint": hashlib.sha256(
                json.dumps(outline.get("content") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "beats_id": beats["id"],
            "beats_version": beats["version"],
            "beats_content_fingerprint": hashlib.sha256(
                json.dumps(
                    beats.get("content") or {}, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO creative_episodes (
                    project_id, stage, episode_number, batch_id, version, parent_version_id, content_json,
                    upstream_refs_json, generation_meta_json, workflow_task_id, created_at, updated_at
                ) VALUES (?, 'script', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, int(episode_number), uuid.uuid4().hex, version, parent_id,
                    json.dumps(content, ensure_ascii=False), json.dumps(refs, ensure_ascii=False),
                    json.dumps(meta, ensure_ascii=False), workflow_task_id or project.get("workflow_task_id"), now, now,
                ),
            )
            if update_project_status:
                conn.execute(
                    "UPDATE creative_projects SET status = 'scripts_in_progress', updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
        return self.get_episode(int(cursor.lastrowid)) or {}

    def get_episode(self, episode_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM creative_episodes WHERE id = ?", (episode_id,)).fetchone()
        return self._episode(dict(row)) if row else None

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

    def fail_script_review(self, review_id: int, error: str) -> dict[str, Any]:
        return self._update_script_review(
            review_id, {"status": "failed", "error": str(error)[:2000]}
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
    ) -> dict[str, Any] | None:
        if table not in {"creative_characters", "creative_relationships", "creative_episodes"}:
            raise ValueError("invalid creative table")
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return None
            if row["status"] != "draft":
                raise ValueError("已完成的版本不能原地修改")
            conn.execute(
                f"UPDATE {table} SET content_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(content, ensure_ascii=False), time.time(), item_id),
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
        return row

    @staticmethod
    def _episode(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
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
    def _title_key(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return "".join(char for char in normalized if char.isalnum() or "\u4e00" <= char <= "\u9fff")

    @staticmethod
    def _breakdown(row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json") or "{}")
        row["field_sources"] = json.loads(row.pop("field_sources_json") or "{}")
        row["risks"] = json.loads(row.pop("risks_json") or "[]")
        return row
