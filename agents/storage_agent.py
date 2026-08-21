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


class OperationScopeConflictError(RuntimeError):
    """Identify the durable reservation that owns a conflicting scope."""

    def __init__(self, job_id: str, operation_id: str | None = None) -> None:
        super().__init__(job_id)
        self.job_id = job_id
        self.operation_id = operation_id


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
                    operation_id TEXT,
                    platform TEXT NOT NULL,
                    crawler_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    message TEXT,
                    pid INTEGER,
                    job_json TEXT NOT NULL,
                    scope_project_id TEXT,
                    scope_stage TEXT,
                    scope_start INTEGER,
                    scope_end INTEGER,
                    scope_active INTEGER NOT NULL DEFAULT 0,
                    launcher_owner TEXT,
                    launcher_lease_expires_at REAL,
                    launch_phase TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            if "operation_id" not in columns:
                conn.execute("ALTER TABLE agent_jobs ADD COLUMN operation_id TEXT")
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(agent_jobs)").fetchall()
            }
            for name, statement in (
                ("scope_project_id", "ALTER TABLE agent_jobs ADD COLUMN scope_project_id TEXT"),
                ("scope_stage", "ALTER TABLE agent_jobs ADD COLUMN scope_stage TEXT"),
                ("scope_start", "ALTER TABLE agent_jobs ADD COLUMN scope_start INTEGER"),
                ("scope_end", "ALTER TABLE agent_jobs ADD COLUMN scope_end INTEGER"),
                ("scope_active", "ALTER TABLE agent_jobs ADD COLUMN scope_active INTEGER NOT NULL DEFAULT 0"),
                ("pid", "ALTER TABLE agent_jobs ADD COLUMN pid INTEGER"),
                ("launcher_owner", "ALTER TABLE agent_jobs ADD COLUMN launcher_owner TEXT"),
                ("launcher_lease_expires_at", "ALTER TABLE agent_jobs ADD COLUMN launcher_lease_expires_at REAL"),
                ("launch_phase", "ALTER TABLE agent_jobs ADD COLUMN launch_phase TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(statement)
            operation_owners: dict[str, str] = {}
            legacy_bindings: list[tuple[str, str]] = []
            for job_id, stored_operation_id, job_json in conn.execute(
                "SELECT job_id, operation_id, job_json FROM agent_jobs"
            ).fetchall():
                try:
                    payload = json.loads(job_json or "{}")
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"invalid historical job_json for job {job_id}"
                    ) from exc
                embedded_operation_id = str(
                    (payload.get("extra") or {}).get("operation_id") or ""
                ).strip()
                column_operation_id = str(stored_operation_id or "").strip()
                if (
                    embedded_operation_id
                    and column_operation_id
                    and embedded_operation_id != column_operation_id
                ):
                    raise ValueError(
                        f"job {job_id} has conflicting operation_id values"
                    )
                operation_id = column_operation_id or embedded_operation_id
                if not operation_id:
                    continue
                owner = operation_owners.setdefault(operation_id, str(job_id))
                if owner != str(job_id):
                    raise ValueError(
                        f"duplicate operation_id {operation_id} belongs to multiple jobs"
                    )
                if not column_operation_id:
                    legacy_bindings.append((operation_id, str(job_id)))
            conn.executemany(
                "UPDATE agent_jobs SET operation_id=? WHERE job_id=?",
                legacy_bindings,
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_jobs_operation_id "
                "ON agent_jobs(operation_id) WHERE operation_id IS NOT NULL"
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
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_job_conn(conn, state)

    @staticmethod
    def _upsert_job_conn(conn: sqlite3.Connection, state: JobState) -> None:
        operation_id = str(state.job.extra.get("operation_id") or "").strip() or None
        existing = conn.execute(
            "SELECT operation_id FROM agent_jobs WHERE job_id=?", (state.job_id,)
        ).fetchone()
        if (
            existing is not None
            and existing[0] is not None
            and operation_id is not None
            and str(existing[0]) != operation_id
        ):
            raise ValueError("job_id is already bound to another operation_id")
        conn.execute(
            """
            INSERT INTO agent_jobs (
                job_id, operation_id, platform, crawler_type, status, created_at,
                started_at, finished_at, message, pid, job_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                operation_id=COALESCE(agent_jobs.operation_id, excluded.operation_id),
                platform=excluded.platform,
                crawler_type=excluded.crawler_type,
                status=excluded.status,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                message=excluded.message,
                pid=excluded.pid,
                job_json=excluded.job_json
            """,
            (
                state.job_id,
                operation_id,
                state.platform,
                state.job.crawler_type,
                state.status,
                state.created_at,
                state.started_at,
                state.finished_at,
                state.message,
                state.pid,
                json.dumps(state.job.to_dict(), ensure_ascii=False),
            ),
        )
        if operation_id is not None:
            conn.execute(
                "UPDATE agent_jobs SET scope_active=? WHERE job_id=?",
                (
                    int(state.status in {"pending", "running", "stopping"}),
                    state.job_id,
                ),
            )

    def sync_terminal_job_from_disk(self, state: JobState) -> bool:
        """Reconcile a durable terminal state without overwriting a live retry."""

        operation_id = str(state.job.extra.get("operation_id") or "").strip()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if operation_id:
                row = conn.execute(
                    "SELECT status,launch_phase,launcher_lease_expires_at,"
                    "started_at "
                    "FROM agent_jobs WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                live_launch = (
                    row is not None
                    and str(row[0]) in {"pending", "running"}
                    and str(row[1] or "") == "launching"
                    and float(row[2] or 0) > time.time()
                )
                newer_running_attempt = (
                    row is not None
                    and str(row[0]) == "running"
                    and row[3] is not None
                    and (
                        state.finished_at is None
                        or float(state.finished_at) < float(row[3])
                    )
                )
                if live_launch or newer_running_attempt:
                    return False
            self._upsert_job_conn(conn, state)
        return True

    def upsert_owned_job_launch(
        self,
        state: JobState,
        operation_id: str,
        *,
        launcher_owner: str,
    ) -> bool:
        """Persist launch state only while this launcher still owns a live lease."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE agent_jobs SET platform=?,crawler_type=?,status=?,"
                "started_at=?,finished_at=?,message=?,pid=?,job_json=?,scope_active=? "
                "WHERE job_id=? AND operation_id=? AND launcher_owner=? "
                "AND launch_phase='launching' "
                "AND launcher_lease_expires_at>?",
                (
                    state.platform,
                    state.job.crawler_type,
                    state.status,
                    state.started_at,
                    state.finished_at,
                    state.message,
                    state.pid,
                    json.dumps(state.job.to_dict(), ensure_ascii=False),
                    int(state.status in {"pending", "running", "stopping"}),
                    state.job_id,
                    operation_id,
                    launcher_owner,
                    time.time(),
                ),
            ).rowcount
        return changed == 1

    def reserve_job_operation(
        self,
        operation_id: str,
        state: JobState,
        *,
        scope: tuple[str, str, tuple[int, int] | None] | None = None,
        launcher_owner: str | None = None,
        launcher_lease_expires_at: float | None = None,
    ) -> tuple[bool, JobState]:
        """Reserve an idempotent job identity before any worker side effect."""

        operation_id = str(operation_id).strip()
        if not operation_id:
            raise ValueError("operation_id is required")
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM agent_jobs WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing is not None:
                return False, self._job_state_from_row(existing)
            scope_project_id = scope[0] if scope is not None else None
            scope_stage = scope[1] if scope is not None else None
            scope_range = scope[2] if scope is not None else None
            if scope is not None:
                self._assert_scope_available(
                    conn,
                    project_id=scope_project_id,
                    stage=scope_stage,
                    episode_range=scope_range,
                )
            conn.execute(
                "INSERT INTO agent_jobs ("
                "job_id,operation_id,platform,crawler_type,status,created_at,"
                "started_at,finished_at,message,pid,job_json,scope_project_id,"
                "scope_stage,scope_start,scope_end,scope_active,launcher_owner,"
                "launcher_lease_expires_at,launch_phase) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    state.job_id,
                    operation_id,
                    state.platform,
                    state.job.crawler_type,
                    state.status,
                    state.created_at,
                    state.started_at,
                    state.finished_at,
                    state.message,
                    state.pid,
                    json.dumps(state.job.to_dict(), ensure_ascii=False),
                    scope_project_id,
                    scope_stage,
                    scope_range[0] if scope_range is not None else None,
                    scope_range[1] if scope_range is not None else None,
                    int(scope is not None),
                    launcher_owner,
                    launcher_lease_expires_at,
                    "launching" if launcher_owner else "",
                ),
            )
            return True, state

    def renew_job_launch_lease(
        self,
        operation_id: str,
        *,
        launcher_owner: str,
        lease_expires_at: float,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE agent_jobs SET launcher_lease_expires_at=? "
                "WHERE operation_id=? AND launcher_owner=? "
                "AND launch_phase='launching' "
                "AND status IN ('pending','running')",
                (lease_expires_at, operation_id, launcher_owner),
            ).rowcount
        return changed == 1

    def finish_job_launch(
        self, operation_id: str, *, launcher_owner: str, phase: str
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE agent_jobs SET launch_phase=?, "
                "launcher_lease_expires_at=NULL WHERE operation_id=? "
                "AND launcher_owner=? AND launch_phase='launching' "
                "AND launcher_lease_expires_at>?",
                (phase, operation_id, launcher_owner, time.time()),
            ).rowcount
        return changed == 1

    def job_launch_lease(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT launcher_owner,launcher_lease_expires_at,launch_phase "
                "FROM agent_jobs WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "owner": row[0],
            "expires_at": row[1],
            "phase": str(row[2] or ""),
        }

    @staticmethod
    def _assert_scope_available(
        conn: sqlite3.Connection,
        *,
        project_id: str,
        stage: str,
        episode_range: tuple[int, int] | None,
        exclude_operation_id: str | None = None,
    ) -> None:
        rows = conn.execute(
            "SELECT job_id,operation_id,scope_start,scope_end,status,"
            "launch_phase,launcher_lease_expires_at FROM agent_jobs "
            "WHERE scope_active=1 AND scope_project_id=? AND scope_stage=?",
            (project_id, stage),
        ).fetchall()
        for row in rows:
            if exclude_operation_id and str(row[1] or "") == exclude_operation_id:
                continue
            if (
                str(row[4]) == "pending"
                and str(row[5] or "") == "launching"
                and float(row[6] or 0) <= time.time()
            ):
                conn.execute(
                    "UPDATE agent_jobs SET status='failed', scope_active=0, "
                    "finished_at=?, message='launcher lease expired', "
                    "launch_phase='failed' WHERE job_id=? AND status='pending' "
                    "AND launch_phase='launching'",
                    (time.time(), str(row[0])),
                )
                continue
            existing_range = (
                None
                if row[2] is None or row[3] is None
                else (int(row[2]), int(row[3]))
            )
            if (
                episode_range is None
                or existing_range is None
                or max(episode_range[0], existing_range[0])
                <= min(episode_range[1], existing_range[1])
            ):
                raise OperationScopeConflictError(
                    str(row[0]), str(row[1] or "") or None
                )

    def claim_failed_job_operation(
        self,
        operation_id: str,
        *,
        launcher_owner: str | None = None,
        launcher_lease_expires_at: float | None = None,
    ) -> tuple[bool, JobState | None]:
        """Move one failed operation reservation back to pending exactly once."""

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM agent_jobs WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                return False, None
            state = self._job_state_from_row(row)
            if str(row["status"]) != "failed":
                return False, state
            if row["scope_project_id"] and row["scope_stage"]:
                episode_range = (
                    None
                    if row["scope_start"] is None or row["scope_end"] is None
                    else (int(row["scope_start"]), int(row["scope_end"]))
                )
                self._assert_scope_available(
                    conn,
                    project_id=str(row["scope_project_id"]),
                    stage=str(row["scope_stage"]),
                    episode_range=episode_range,
                    exclude_operation_id=operation_id,
                )
            changed = conn.execute(
                "UPDATE agent_jobs SET status='pending', started_at=NULL, "
                "finished_at=NULL, message='', scope_active=1, "
                "launcher_owner=?, launcher_lease_expires_at=?, "
                "launch_phase='launching' WHERE operation_id=? AND status='failed'",
                (
                    launcher_owner,
                    launcher_lease_expires_at,
                    operation_id,
                ),
            ).rowcount
            if changed != 1:
                return False, state
            state.status = "pending"
            state.started_at = None
            state.finished_at = None
            state.message = ""
            return True, state

    def claim_expired_prelaunch_operation(
        self,
        operation_id: str,
        *,
        launcher_owner: str,
        launcher_lease_expires_at: float,
    ) -> tuple[bool, JobState | None]:
        """Take over a reservation whose launcher died before recording a worker."""

        now = time.time()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM agent_jobs WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                return False, None
            state = self._job_state_from_row(row)
            prelaunch = (
                str(row["status"]) == "pending"
                and str(row["launch_phase"] or "") == "launching"
                and float(row["launcher_lease_expires_at"] or 0) <= now
                and row["pid"] is None
                and row["started_at"] is None
            )
            if not prelaunch:
                return False, state
            changed = conn.execute(
                "UPDATE agent_jobs SET launcher_owner=?, "
                "launcher_lease_expires_at=? WHERE operation_id=? "
                "AND status='pending' AND launch_phase='launching' "
                "AND launcher_lease_expires_at<=? AND pid IS NULL "
                "AND started_at IS NULL",
                (
                    launcher_owner,
                    launcher_lease_expires_at,
                    operation_id,
                    now,
                ),
            ).rowcount
            return changed == 1, state

    @staticmethod
    def _job_state_from_row(row: sqlite3.Row) -> JobState:
        from core.job import CrawlJob

        job = CrawlJob.from_dict(json.loads(row["job_json"] or "{}"))
        state = JobState(job=job)
        state.status = str(row["status"])
        state.created_at = float(row["created_at"])
        state.started_at = row["started_at"]
        state.finished_at = row["finished_at"]
        state.message = str(row["message"] or "")
        state.pid = row["pid"]
        return state

    def get_job_by_operation(self, operation_id: str) -> JobState | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM agent_jobs WHERE operation_id=?", (operation_id,)
            ).fetchone()
        return self._job_state_from_row(row) if row is not None else None

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
