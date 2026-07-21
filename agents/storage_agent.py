# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.events import WorkerEvent
from runtime.job_state import JobState


class StorageAgent:
    def __init__(self, db_path: str | Path = ".runtime/agent_state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    crawler_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    message TEXT,
                    job_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT,
                    ts REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )

    def upsert_job(self, state: JobState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_jobs (
                    job_id, platform, crawler_type, status, created_at,
                    started_at, finished_at, message, job_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    platform=excluded.platform,
                    crawler_type=excluded.crawler_type,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    message=excluded.message,
                    job_json=excluded.job_json
                """,
                (
                    state.job_id,
                    state.platform,
                    state.job.crawler_type,
                    state.status,
                    state.created_at,
                    state.started_at,
                    state.finished_at,
                    state.message,
                    json.dumps(state.job.to_dict(), ensure_ascii=False),
                ),
            )

    def record_event(self, event: WorkerEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_events (job_id, event, level, message, ts, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.job_id,
                    event.event,
                    event.level,
                    event.message,
                    event.ts,
                    json.dumps(event.payload, ensure_ascii=False),
                ),
            )

    def record_artifact(
        self,
        job_id: str,
        artifact_type: str,
        path: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_artifacts (
                    job_id, artifact_type, path, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    artifact_type,
                    str(path),
                    time.time(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT artifact_type, path, created_at, metadata_json
                FROM agent_artifacts
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "artifact_type": row[0],
                "path": row[1],
                "created_at": row[2],
                "metadata": json.loads(row[3] or "{}"),
            }
            for row in rows
        ]
