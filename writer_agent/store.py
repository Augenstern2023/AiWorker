# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

from .schema import WriterActionError


_ACTION_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "expired", "rolled_back"}
)
_STEP_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "blocked"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _message_id() -> str:
    return f"message-{uuid.uuid4().hex}"


def _material_message(name: str, content: str) -> dict[str, Any]:
    return {
        "message_id": _message_id(),
        "role": "user",
        "kind": "material",
        "content": f"已提交创作材料：{name}",
        "material_name": name,
        "material_preview": content[:1200],
        "material_length": len(content),
    }


class WriterAgentStore:
    """SQLite persistence boundary for writer sessions and durable actions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS writer_agent_sessions ("
                "id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, "
                "content TEXT NOT NULL, filename TEXT NOT NULL DEFAULT '', "
                "messages_json TEXT NOT NULL DEFAULT '[]', report_json TEXT NOT NULL DEFAULT '{}', "
                "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )
            self._migrate_sessions(conn)
            self._create_action_tables(conn)
            self._migrate_actions(conn)
            self._create_rollback_tables(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _migrate_sessions(self, conn: sqlite3.Connection) -> None:
        columns = self._columns(conn, "writer_agent_sessions")
        for name, statement in (
            ("reports_json", "ALTER TABLE writer_agent_sessions ADD COLUMN reports_json TEXT NOT NULL DEFAULT '[]'"),
            ("project_id", "ALTER TABLE writer_agent_sessions ADD COLUMN project_id INTEGER"),
            ("context_revision", "ALTER TABLE writer_agent_sessions ADD COLUMN context_revision INTEGER NOT NULL DEFAULT 0"),
            ("history_revision", "ALTER TABLE writer_agent_sessions ADD COLUMN history_revision INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                conn.execute(statement)

    @staticmethod
    def _create_action_tables(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_actions ("
            "id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, actor_user_id TEXT NOT NULL, "
            "action_type TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', "
            "revision INTEGER NOT NULL, project_id INTEGER, project_id_json TEXT, target_key TEXT NOT NULL DEFAULT '', "
            "idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, source_snapshot_id TEXT, "
            "expected_context_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT NOT NULL DEFAULT '{}', "
            "result_json TEXT, error_code TEXT, error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0, "
            "conflicting_action_id TEXT, "
            "job_id TEXT, decision_request_id TEXT, cancel_request_id TEXT, retry_of_action_id TEXT, root_action_id TEXT, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL, expires_at REAL, "
            "UNIQUE(actor_user_id, idempotency_key))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_action_steps ("
            "id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, step_key TEXT NOT NULL, stage TEXT NOT NULL, "
            "episode_start INTEGER, episode_end INTEGER, status TEXT NOT NULL, operation_id TEXT NOT NULL, "
            "job_id TEXT, depends_on_json TEXT NOT NULL DEFAULT '[]', resource_refs_json TEXT NOT NULL DEFAULT '[]', "
            "error_code TEXT, error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL, "
            "conflicting_action_id TEXT, execution_owner TEXT, lease_expires_at REAL, "
            "step_order INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "finished_at REAL, UNIQUE(action_id, step_key))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_source_snapshots ("
            "id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_message_claims ("
            "session_id INTEGER NOT NULL, user_id TEXT NOT NULL, message_id TEXT NOT NULL, "
            "content_hash TEXT NOT NULL, status TEXT NOT NULL, claim_token TEXT, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(session_id, user_id, message_id))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_agent_actions_session ON writer_agent_actions(session_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_agent_actions_active ON writer_agent_actions(status, project_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_agent_steps_action ON writer_agent_action_steps(action_id, step_order)"
        )

    def _migrate_actions(self, conn: sqlite3.Connection) -> None:
        if "project_id_json" not in self._columns(conn, "writer_agent_actions"):
            conn.execute(
                "ALTER TABLE writer_agent_actions ADD COLUMN project_id_json TEXT"
            )
        if "conflicting_action_id" not in self._columns(
            conn, "writer_agent_actions"
        ):
            conn.execute(
                "ALTER TABLE writer_agent_actions "
                "ADD COLUMN conflicting_action_id TEXT"
            )
        if "cancel_request_id" not in self._columns(
            conn, "writer_agent_actions"
        ):
            conn.execute(
                "ALTER TABLE writer_agent_actions ADD COLUMN cancel_request_id TEXT"
            )
        if "conflicting_action_id" not in self._columns(
            conn, "writer_agent_action_steps"
        ):
            conn.execute(
                "ALTER TABLE writer_agent_action_steps "
                "ADD COLUMN conflicting_action_id TEXT"
            )
        step_columns = self._columns(conn, "writer_agent_action_steps")
        if "execution_owner" not in step_columns:
            conn.execute(
                "ALTER TABLE writer_agent_action_steps "
                "ADD COLUMN execution_owner TEXT"
            )
        if "lease_expires_at" not in step_columns:
            conn.execute(
                "ALTER TABLE writer_agent_action_steps "
                "ADD COLUMN lease_expires_at REAL"
            )
        if "claim_token" not in self._columns(
            conn, "writer_agent_message_claims"
        ):
            conn.execute(
                "ALTER TABLE writer_agent_message_claims "
                "ADD COLUMN claim_token TEXT"
            )
        if "turn_checkpoint_id" not in self._columns(
            conn, "writer_agent_actions"
        ):
            conn.execute(
                "ALTER TABLE writer_agent_actions "
                "ADD COLUMN turn_checkpoint_id TEXT"
            )

    @staticmethod
    def _create_rollback_tables(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_turn_checkpoints ("
            "id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, "
            "actor_user_id TEXT NOT NULL, user_message_id TEXT NOT NULL, "
            "turn_sequence INTEGER NOT NULL, message_count_before INTEGER NOT NULL, "
            "report_count_before INTEGER NOT NULL, "
            "session_snapshot_json TEXT NOT NULL, "
            "original_message_json TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at REAL NOT NULL, "
            "completed_at REAL, rolled_back_at REAL, "
            "UNIQUE(session_id, user_message_id), "
            "UNIQUE(session_id, turn_sequence))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_turns_session_sequence "
            "ON writer_agent_turn_checkpoints(session_id, turn_sequence)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_action_undo ("
            "action_id TEXT PRIMARY KEY, turn_checkpoint_id TEXT NOT NULL, "
            "project_id INTEGER, preimage_json TEXT NOT NULL DEFAULT '{}', "
            "workflow_preimage_json TEXT NOT NULL DEFAULT '{}', "
            "postcondition_json TEXT NOT NULL DEFAULT '{}', "
            "status TEXT NOT NULL, created_at REAL NOT NULL, "
            "updated_at REAL NOT NULL, compensated_at REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_rollbacks ("
            "id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, "
            "actor_user_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL, "
            "mode TEXT NOT NULL, client_request_id TEXT NOT NULL, "
            "expected_history_revision INTEGER NOT NULL, "
            "stage TEXT NOT NULL, result_json TEXT, error_code TEXT, "
            "error_message TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "completed_at REAL, UNIQUE(actor_user_id, client_request_id))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_writer_rollbacks_session "
            "ON writer_agent_rollbacks(session_id, created_at DESC)"
        )

    def create(self, user_id: str, content: str, title: str = "", filename: str = "") -> dict[str, Any]:
        now = time.time()
        material_name = filename or title or "已粘贴创作材料"
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO writer_agent_sessions "
                "(user_id,title,content,filename,messages_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    user_id,
                    title or filename or "未命名创作会话",
                    content,
                    filename,
                    _json([_material_message(material_name, content)]),
                    now,
                    now,
                ),
            )
        return self.get(int(cursor.lastrowid), user_id) or {}

    def append_material(self, session_id: int, user_id: str | None, content: str, filename: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=?"
                if user_id is None
                else "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id,) if user_id is None else (session_id, user_id),
            ).fetchone()
            if row is None:
                return {}
            messages = _decode(row["messages_json"] or "[]", [])
            messages.append(_material_message(filename, content))
            combined = (
                f"{row['content']}\n\n[补充材料：{filename}]\n{content}"
            )
            conn.execute(
                "UPDATE writer_agent_sessions SET content=?, messages_json=?, "
                "context_revision=context_revision+1, "
                "history_revision=history_revision+1, updated_at=? WHERE id=?",
                (combined, _json(messages), time.time(), session_id),
            )
        return self.get(session_id, user_id) or {}

    def get(self, session_id: int, actor_user_id: str | None) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=?"
                if actor_user_id is None
                else "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id,) if actor_user_id is None else (session_id, actor_user_id),
            ).fetchone()
        return self._row(row) if row else None

    def list(self, actor_user_id: str | None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM writer_agent_sessions ORDER BY updated_at DESC"
                if actor_user_id is None
                else "SELECT * FROM writer_agent_sessions WHERE user_id=? ORDER BY updated_at DESC",
                () if actor_user_id is None else (actor_user_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def begin_turn(
        self,
        session_id: int,
        actor_user_id: str,
        user_message_id: str,
        *,
        original_message: dict[str, Any],
        content_hash: str,
    ) -> dict[str, Any]:
        """Persist the exact conversation boundary before a client turn."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id, actor_user_id),
            ).fetchone()
            if session is None:
                raise WriterActionError(
                    "PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问"
                )
            existing = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints "
                "WHERE session_id=? AND user_message_id=?",
                (session_id, user_message_id),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != str(content_hash):
                    raise WriterActionError(
                        "IDEMPOTENCY_KEY_REUSED", "消息 ID 已用于不同内容"
                    )
                return self._turn_row(existing)
            messages = _decode(session["messages_json"] or "[]", [])
            reports = _decode(session["reports_json"] or "[]", [])
            next_sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(turn_sequence), 0) + 1 "
                    "FROM writer_agent_turn_checkpoints WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
            )
            checkpoint_id = f"turn-{uuid.uuid4().hex}"
            now = time.time()
            session_snapshot = {
                "project_id": session["project_id"],
                "context_revision": int(session["context_revision"] or 0),
                "history_revision": int(session["history_revision"] or 0),
                "report": _decode(session["report_json"] or "{}", {}),
            }
            conn.execute(
                "INSERT INTO writer_agent_turn_checkpoints "
                "(id,session_id,actor_user_id,user_message_id,turn_sequence,"
                "message_count_before,report_count_before,session_snapshot_json,"
                "original_message_json,content_hash,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    checkpoint_id,
                    session_id,
                    actor_user_id,
                    user_message_id,
                    next_sequence,
                    len(messages),
                    len(reports),
                    _json(session_snapshot),
                    _json(original_message),
                    content_hash,
                    "open",
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
        return self._turn_row(row)

    def complete_turn(self, checkpoint_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
            if row is None:
                raise WriterActionError(
                    "TURN_CHECKPOINT_NOT_FOUND", "对话检查点不存在"
                )
            if row["status"] == "open":
                conn.execute(
                    "UPDATE writer_agent_turn_checkpoints SET status='completed', "
                    "completed_at=? WHERE id=? AND status='open'",
                    (time.time(), checkpoint_id),
                )
                row = conn.execute(
                    "SELECT * FROM writer_agent_turn_checkpoints WHERE id=?",
                    (checkpoint_id,),
                ).fetchone()
        return self._turn_row(row)

    def require_turn(
        self,
        session_id: int,
        user_message_id: str,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints "
                "WHERE session_id=? AND user_message_id=?"
                if actor_user_id is None
                else "SELECT * FROM writer_agent_turn_checkpoints "
                "WHERE session_id=? AND user_message_id=? AND actor_user_id=?",
                (session_id, user_message_id)
                if actor_user_id is None
                else (session_id, user_message_id, actor_user_id),
            ).fetchone()
        if row is None:
            raise WriterActionError(
                "TURN_CHECKPOINT_NOT_FOUND", "该消息没有可用的回档检查点"
            )
        return self._turn_row(row)

    def list_turns_from(self, checkpoint_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            checkpoint = conn.execute(
                "SELECT session_id,turn_sequence FROM "
                "writer_agent_turn_checkpoints WHERE id=?",
                (checkpoint_id,),
            ).fetchone()
            if checkpoint is None:
                raise WriterActionError(
                    "TURN_CHECKPOINT_NOT_FOUND", "对话检查点不存在"
                )
            rows = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints "
                "WHERE session_id=? AND turn_sequence>=? ORDER BY turn_sequence",
                (checkpoint["session_id"], checkpoint["turn_sequence"]),
            ).fetchall()
        return [self._turn_row(row) for row in rows]

    def begin_rollback(
        self,
        session_id: int,
        actor_user_id: str,
        checkpoint_id: str,
        *,
        mode: str,
        client_request_id: str,
        expected_history_revision: int,
    ) -> tuple[dict[str, Any], bool]:
        if mode not in {"rollback", "resend"}:
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED", "回档模式无效"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM writer_agent_rollbacks "
                "WHERE actor_user_id=? AND client_request_id=?",
                (actor_user_id, client_request_id),
            ).fetchone()
            if existing is not None:
                if (
                    int(existing["session_id"]) != int(session_id)
                    or str(existing["checkpoint_id"]) != str(checkpoint_id)
                    or str(existing["mode"]) != mode
                ):
                    raise WriterActionError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "回档请求 ID 已用于不同操作",
                    )
                return self._rollback_row(existing), False
            session = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id, actor_user_id),
            ).fetchone()
            if session is None:
                raise WriterActionError(
                    "PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问"
                )
            checkpoint = conn.execute(
                "SELECT * FROM writer_agent_turn_checkpoints "
                "WHERE id=? AND session_id=? AND actor_user_id=?",
                (checkpoint_id, session_id, actor_user_id),
            ).fetchone()
            if checkpoint is None:
                raise WriterActionError(
                    "TURN_CHECKPOINT_NOT_FOUND", "对话检查点不存在"
                )
            current_revision = int(session["history_revision"] or 0)
            if current_revision != int(expected_history_revision):
                raise WriterActionError(
                    "HISTORY_REVISION_MISMATCH",
                    "对话历史已变化，请刷新后重试",
                    current_revision=current_revision,
                )
            rollback_id = f"rollback-{uuid.uuid4().hex}"
            now = time.time()
            conn.execute(
                "INSERT INTO writer_agent_rollbacks "
                "(id,session_id,actor_user_id,checkpoint_id,mode,"
                "client_request_id,expected_history_revision,stage,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rollback_id,
                    session_id,
                    actor_user_id,
                    checkpoint_id,
                    mode,
                    client_request_id,
                    expected_history_revision,
                    "requested",
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM writer_agent_rollbacks WHERE id=?",
                (rollback_id,),
            ).fetchone()
        return self._rollback_row(row), True

    def advance_rollback(
        self, rollback_id: str, stage: str, **updates: Any
    ) -> dict[str, Any]:
        allowed = {
            "requested",
            "jobs_settled",
            "validated",
            "creative_restored",
            "workflow_restored",
            "conversation_restored",
            "completed",
        }
        if stage not in allowed:
            raise ValueError("unknown rollback stage")
        fields: dict[str, Any] = {"stage": stage, "updated_at": time.time()}
        for key in ("result_json", "error_code", "error_message"):
            if key in updates:
                fields[key] = (
                    _json(updates[key])
                    if key == "result_json" and updates[key] is not None
                    else updates[key]
                )
        if stage == "completed":
            fields["completed_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"UPDATE writer_agent_rollbacks SET {assignments} WHERE id=?",
                (*fields.values(), rollback_id),
            )
            row = conn.execute(
                "SELECT * FROM writer_agent_rollbacks WHERE id=?",
                (rollback_id,),
            ).fetchone()
        if row is None:
            raise WriterActionError("TURN_CHECKPOINT_NOT_FOUND", "回档请求不存在")
        return self._rollback_row(row)

    def restore_conversation(self, rollback_id: str) -> dict[str, Any]:
        """Restore session-visible state only after external stores are settled."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rollback = conn.execute(
                "SELECT * FROM writer_agent_rollbacks WHERE id=?", (rollback_id,)
            ).fetchone()
            if rollback is None:
                raise WriterActionError(
                    "TURN_CHECKPOINT_NOT_FOUND", "回档请求不存在"
                )
            session_id = int(rollback["session_id"])
            actor_user_id = str(rollback["actor_user_id"])
            if rollback["stage"] == "completed":
                pass
            else:
                checkpoint = conn.execute(
                    "SELECT * FROM writer_agent_turn_checkpoints WHERE id=?",
                    (rollback["checkpoint_id"],),
                ).fetchone()
                session = conn.execute(
                    "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                    (session_id, actor_user_id),
                ).fetchone()
                if checkpoint is None or session is None:
                    raise WriterActionError(
                        "TURN_CHECKPOINT_NOT_FOUND", "回档目标已不存在"
                    )
                snapshot = _decode(checkpoint["session_snapshot_json"], {})
                messages = _decode(session["messages_json"] or "[]", [])[
                    : int(checkpoint["message_count_before"])
                ]
                reports = _decode(session["reports_json"] or "[]", [])[
                    : int(checkpoint["report_count_before"])
                ]
                latest_report = (
                    reports[-1].get("report")
                    if reports
                    else snapshot.get("report") or {}
                )
                now = time.time()
                conn.execute(
                    "UPDATE writer_agent_sessions SET messages_json=?, "
                    "report_json=?, reports_json=?, project_id=?, "
                    "context_revision=?, history_revision=history_revision+1, "
                    "updated_at=? WHERE id=?",
                    (
                        _json(messages),
                        _json(latest_report),
                        _json(reports),
                        snapshot.get("project_id"),
                        int(snapshot.get("context_revision") or 0),
                        now,
                        session_id,
                    ),
                )
                conn.execute(
                    "UPDATE writer_agent_actions SET status='rolled_back', "
                    "phase='rolled_back', revision=revision+1, updated_at=?, "
                    "finished_at=COALESCE(finished_at, ?) "
                    "WHERE turn_checkpoint_id IN ("
                    "SELECT id FROM writer_agent_turn_checkpoints "
                    "WHERE session_id=? AND turn_sequence>=?) "
                    "AND status<>'rolled_back'",
                    (
                        now,
                        now,
                        session_id,
                        int(checkpoint["turn_sequence"]),
                    ),
                )
                conn.execute(
                    "UPDATE writer_agent_turn_checkpoints "
                    "SET status=CASE WHEN id=? THEN 'rolled_back' "
                    "ELSE 'superseded' END, rolled_back_at=? "
                    "WHERE session_id=? AND turn_sequence>=?",
                    (
                        checkpoint["id"],
                        now,
                        session_id,
                        int(checkpoint["turn_sequence"]),
                    ),
                )
                result = {
                    "session_id": session_id,
                    "history_revision": int(session["history_revision"] or 0)
                    + 1,
                }
                conn.execute(
                    "UPDATE writer_agent_rollbacks SET stage='completed', "
                    "result_json=?, updated_at=?, completed_at=? WHERE id=?",
                    (_json(result), now, now, rollback_id),
                )
        return self.get(session_id, actor_user_id) or {}

    @staticmethod
    def _turn_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["session_snapshot"] = _decode(
            item.pop("session_snapshot_json"), {}
        )
        item["original_message"] = _decode(
            item.pop("original_message_json"), {}
        )
        return item

    @staticmethod
    def _rollback_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["result"] = _decode(item.pop("result_json"), None)
        return item

    def save_turn(
        self,
        session_id: int,
        user_id: str | None,
        messages: list[dict[str, Any]],
        report: dict[str, Any] | None,
        *,
        client_message_id: str | None = None,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Atomically merge one client turn without replacing concurrent history."""

        if claim_token is not None and not client_message_id:
            raise ValueError("claim_token requires client_message_id")
        normalized_messages = [dict(message) for message in messages]
        for message in normalized_messages:
            message.setdefault("message_id", _message_id())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=?"
                if user_id is None
                else "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id,) if user_id is None else (session_id, user_id),
            ).fetchone()
            if row is None:
                return {}
            if claim_token is not None:
                claim = conn.execute(
                    "SELECT status, claim_token FROM writer_agent_message_claims "
                    "WHERE session_id=? AND user_id=? AND message_id=?",
                    (session_id, user_id, client_message_id),
                ).fetchone()
                if (
                    claim is None
                    or claim["status"] != "in_progress"
                    or claim["claim_token"] != claim_token
                ):
                    raise WriterActionError(
                        "MESSAGE_CLAIM_LOST", "消息处理租约已由其他请求接管"
                    )
            persisted_messages = _decode(row["messages_json"] or "[]", [])
            reports = _decode(row["reports_json"] or "[]", [])
            latest_report = _decode(row["report_json"] or "{}", {})
            turn_messages = normalized_messages
            if client_message_id:
                incoming_index = next(
                    (
                        index
                        for index, message in enumerate(normalized_messages)
                        if message.get("role") == "user"
                        and message.get("message_id") == client_message_id
                    ),
                    None,
                )
                if incoming_index is None:
                    raise WriterActionError(
                        "RESULT_VALIDATION_FAILED", "客户端消息未出现在待保存轮次中"
                    )
                incoming = normalized_messages[incoming_index]
                existing = next(
                    (
                        message
                        for message in persisted_messages
                        if message.get("role") == "user"
                        and message.get("message_id") == client_message_id
                    ),
                    None,
                )
                if existing is not None:
                    if existing.get("content") != incoming.get("content"):
                        raise WriterActionError(
                            "IDEMPOTENCY_KEY_REUSED",
                            "消息 ID 已用于不同内容",
                        )
                    turn_messages = []
                else:
                    turn_messages = normalized_messages[incoming_index:]
                    for offset, message in enumerate(turn_messages):
                        if message.get("role") == "assistant":
                            message["message_id"] = (
                                f"{client_message_id}:assistant:{offset}"
                            )
                    persisted_messages.extend(turn_messages)
            else:
                persisted_messages = normalized_messages
            if report is not None and turn_messages:
                entry = {
                    "id": f"report-{len(reports) + 1}",
                    "created_at": time.time(),
                    "report": report,
                }
                reports.append(entry)
                latest_report = report
                if turn_messages and turn_messages[-1].get("role") == "assistant":
                    turn_messages[-1]["report_id"] = entry["id"]
            if turn_messages:
                conn.execute(
                    "UPDATE writer_agent_sessions SET messages_json=?, report_json=?, "
                    "reports_json=?, history_revision=history_revision+1, "
                    "updated_at=? WHERE id=?",
                    (
                        _json(persisted_messages),
                        _json(latest_report),
                        _json(reports),
                        time.time(),
                        session_id,
                    ),
                )
            if claim_token is not None:
                completed = conn.execute(
                    "UPDATE writer_agent_message_claims SET status='completed', "
                    "updated_at=? WHERE session_id=? AND user_id=? AND message_id=? "
                    "AND status='in_progress' AND claim_token=?",
                    (
                        time.time(),
                        session_id,
                        user_id,
                        client_message_id,
                        claim_token,
                    ),
                ).rowcount
                if completed != 1:
                    raise WriterActionError(
                        "MESSAGE_CLAIM_LOST", "消息处理租约已由其他请求接管"
                    )
        return self.get(session_id, user_id) or {}

    def append_project_review_message_once(
        self,
        session_id: int,
        user_id: str,
        *,
        review_id: int,
        audit_scope: str,
    ) -> dict[str, Any]:
        """Append the durable review-reference message exactly once."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
            if row is None:
                return {}
            messages = _decode(row["messages_json"] or "[]", [])
            if not any(
                item.get("kind") == "project_review"
                and int(item.get("review_id") or 0) == int(review_id)
                for item in messages
                if isinstance(item, dict)
            ):
                messages.append({
                    "role": "assistant", "kind": "project_review",
                    "review_id": int(review_id), "audit_scope": audit_scope,
                    "content": "已创建完整项目审核任务，可在审核记录中查看进度。",
                    "message_id": f"project-review:{review_id}",
                })
                conn.execute(
                    "UPDATE writer_agent_sessions SET messages_json=?, "
                    "history_revision=history_revision+1, updated_at=? WHERE id=?",
                    (_json(messages), time.time(), session_id),
                )
        return self.get(session_id, user_id) or {}

    def claim_client_message(
        self,
        session_id: int,
        user_id: str,
        message_id: str,
        content_hash: str,
        *,
        claim_token: str | None = None,
        lease_seconds: float = 30.0,
    ) -> str:
        """Claim one model-backed client turn before any external side effect."""

        now = time.time()
        owner_token = claim_token or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute(
                "SELECT 1 FROM writer_agent_sessions WHERE id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
            if session is None:
                raise WriterActionError(
                    "PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问"
                )
            row = conn.execute(
                "SELECT * FROM writer_agent_message_claims "
                "WHERE session_id=? AND user_id=? AND message_id=?",
                (session_id, user_id, message_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO writer_agent_message_claims "
                    "(session_id,user_id,message_id,content_hash,status,claim_token,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        user_id,
                        message_id,
                        content_hash,
                        "in_progress",
                        owner_token,
                        now,
                        now,
                    ),
                )
                return "claimed"
            if str(row["content_hash"]) != content_hash:
                raise WriterActionError(
                    "IDEMPOTENCY_KEY_REUSED", "消息 ID 已用于不同内容"
                )
            if row["status"] == "completed":
                return "completed"
            if now - float(row["updated_at"]) < lease_seconds:
                return "in_progress"
            conn.execute(
                "UPDATE writer_agent_message_claims SET status='in_progress', "
                "claim_token=?, updated_at=? "
                "WHERE session_id=? AND user_id=? AND message_id=?",
                (owner_token, now, session_id, user_id, message_id),
            )
            return "claimed"

    def renew_client_message_claim(
        self,
        session_id: int,
        user_id: str,
        message_id: str,
        *,
        claim_token: str,
    ) -> bool:
        """Renew only the caller's live message lease."""

        with self._connect() as conn:
            renewed = conn.execute(
                "UPDATE writer_agent_message_claims SET updated_at=? "
                "WHERE session_id=? AND user_id=? AND message_id=? "
                "AND status='in_progress' AND claim_token=?",
                (time.time(), session_id, user_id, message_id, claim_token),
            ).rowcount
        return renewed == 1

    def client_message_claim_status(
        self, session_id: int, user_id: str, message_id: str
    ) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM writer_agent_message_claims "
                "WHERE session_id=? AND user_id=? AND message_id=?",
                (session_id, user_id, message_id),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def complete_client_message(
        self,
        session_id: int,
        user_id: str,
        message_id: str,
        *,
        claim_token: str,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            completed = conn.execute(
                "UPDATE writer_agent_message_claims SET status='completed', "
                "updated_at=? WHERE session_id=? AND user_id=? AND message_id=? "
                "AND status='in_progress' AND claim_token=?",
                (time.time(), session_id, user_id, message_id, claim_token),
            ).rowcount
        return completed == 1

    def abandon_client_message(
        self,
        session_id: int,
        user_id: str,
        message_id: str,
        *,
        claim_token: str,
    ) -> bool:
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM writer_agent_message_claims WHERE session_id=? "
                "AND user_id=? AND message_id=? AND status='in_progress' "
                "AND claim_token=?",
                (session_id, user_id, message_id, claim_token),
            ).rowcount
        return deleted == 1

    def bind_project(self, session_id: int, project_id: int, expected_context_revision: int) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM writer_agent_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作会话不存在")
            current = int(row["context_revision"] or 0)
            if row["project_id"] == project_id:
                return self._row(row)
            if current != expected_context_revision:
                raise WriterActionError("STALE_PROJECT_CONTEXT", "会话上下文已变化", current_revision=current)
            changed = conn.execute(
                "UPDATE writer_agent_sessions SET project_id=?, context_revision=?, "
                "history_revision=history_revision+1, updated_at=? "
                "WHERE id=? AND context_revision=?",
                (project_id, current + 1, time.time(), session_id, current),
            ).rowcount
            if changed != 1:
                raise WriterActionError("STALE_PROJECT_CONTEXT", "会话上下文已变化", current_revision=current)
        return self.get(session_id, None) or {}

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["messages"] = _decode(item.pop("messages_json") or "[]", [])
        item["report"] = _decode(item.pop("report_json") or "{}", {})
        item["reports"] = _decode(item.pop("reports_json", "[]") or "[]", [])
        if not item["reports"] and item["report"]:
            item["reports"] = [{"id": "report-1", "created_at": item["updated_at"], "report": item["report"]}]
        item["project_id"] = item.get("project_id")
        item["context_revision"] = int(item.get("context_revision") or 0)
        item["history_revision"] = int(item.get("history_revision") or 0)
        item["actions"] = self.list_session_actions(int(item["id"]))
        return item

    @staticmethod
    def _target_key(action_type: str, project_id: Any, payload: dict[str, Any]) -> str:
        if action_type != "creative.run_plan" or project_id is None:
            return ""
        aliases = {"blueprints": "brief", "outlines": "outline"}
        stage = aliases.get(str(payload.get("target_stage") or ""), str(payload.get("target_stage") or ""))
        if stage not in {"brief", "characters", "outline", "beats", "script"}:
            return ""
        if stage in {"brief", "characters", "outline"}:
            start, end = None, None
        else:
            start = payload.get("episode_start")
            end = payload.get("episode_end")
            if (start is None) != (end is None):
                raise WriterActionError(
                    "RESULT_VALIDATION_FAILED", "单集创作范围必须同时提供起止集数"
                )
            if start is not None and int(start) > int(end):
                raise WriterActionError(
                    "RESULT_VALIDATION_FAILED", "单集创作起始集数不能大于结束集数"
                )
        return _json({"project_id": project_id, "stage": stage, "start": start, "end": end})

    @staticmethod
    def _action_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key, default in (("expected_context_json", {}), ("payload_json", {}), ("result_json", None)):
            value = item.pop(key, None)
            output = {"expected_context_json": "expected_context", "payload_json": "payload", "result_json": "result"}[key]
            item[output] = _decode(value, default)
        raw_project_id = item.pop("project_id_json", None)
        if raw_project_id is not None:
            item["project_id"] = _decode(raw_project_id, item["project_id"])
        elif (
            item["action_type"] == "project.link"
            and item["status"] == "pending"
            and item["result"] is None
        ):
            # SQLite affinity cannot prove the original type of a legacy value.
            item["project_id"] = None
        item["revision"] = int(item["revision"])
        item["retryable"] = bool(item["retryable"])
        item["target_stage"] = item["payload"].get("target_stage")
        return item

    @staticmethod
    def _trusted_action_title(action: dict[str, Any]) -> str:
        payload = action.get("payload") or {}
        stage = str(payload.get("target_stage") or "")
        stage_name = {
            "brief": "Brief",
            "characters": "人物关系",
            "outline": "大纲",
            "beats": "Beats",
            "script": "剧本",
        }.get(stage, "创作成果")
        start, end = payload.get("episode_start"), payload.get("episode_end")
        scope = (
            f"第{start}-{end}集"
            if start is not None and end is not None
            else ""
        )
        if action.get("action_type") == "project.link":
            return f"关联项目 #{action.get('project_id')}"
        if action.get("action_type") == "project.create_original":
            return "创建原创项目" + (
                f"并生成{scope}{stage_name}" if stage else ""
            )
        if action.get("action_type") == "creative.lock":
            return f"锁定{scope}{stage_name}"
        prefix = "重做" if payload.get("rework_stage") == stage else "生成"
        return f"{prefix}{scope}{stage_name}"

    @staticmethod
    def _trusted_project_url(action: dict[str, Any]) -> str | None:
        result = action.get("result") or {}
        project_id = action.get("project_id") or result.get("project_id")
        if type(project_id) is not int or project_id < 1:
            return None
        payload = action.get("payload") or {}
        stage = str(payload.get("target_stage") or "brief")
        if stage == "brief":
            return f"/planning/projects/{project_id}/brief"
        if stage == "characters":
            return f"/planning/projects/{project_id}/characters"
        if stage == "outline":
            return f"/writing/projects/{project_id}/outlines"
        start, end = payload.get("episode_start"), payload.get("episode_end")
        if type(start) is int and start == end and stage in {"beats", "script"}:
            suffix = "beats" if stage == "beats" else "scripts"
            return f"/writing/projects/{project_id}/episodes/{start}/{suffix}"
        return "/writing/projects"

    def _action_view(
        self, action: dict[str, Any], steps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        sanitized_steps = [
            {
                "step_key": str(step["step_key"]),
                "stage": str(step["stage"]),
                "episode_start": step.get("episode_start"),
                "episode_end": step.get("episode_end"),
                "status": str(step["status"]),
                "resource_refs": list(step.get("resource_refs") or []),
                "error_code": step.get("error_code"),
            }
            for step in steps
        ]
        resource_refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        result = action.get("result") or {}
        candidates = [
            *(result.get("resource_refs") or []),
            *(
                reference
                for step in sanitized_steps
                if step["status"] == "succeeded"
                for reference in step["resource_refs"]
            ),
        ]
        for reference in candidates:
            if not isinstance(reference, dict):
                continue
            key = _json(reference)
            if key not in seen:
                seen.add(key)
                resource_refs.append(reference)
        title = self._trusted_action_title(action)
        total = len(sanitized_steps)
        completed = sum(
            step["status"] == "succeeded" for step in sanitized_steps
        )
        return {
            **action,
            "title": title,
            "summary": (
                f"共 {total} 个可审计步骤"
                if total
                else "动作状态由服务端账本记录"
            ),
            "steps": sanitized_steps,
            "completed_steps": completed,
            "total_steps": total,
            "resource_refs": resource_refs,
            "open_url": self._trusted_project_url(action),
        }

    @staticmethod
    def _step_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["depends_on"] = _decode(item.pop("depends_on_json") or "[]", [])
        item["resource_refs"] = _decode(item.pop("resource_refs_json") or "[]", [])
        item["retryable"] = bool(item["retryable"])
        item["revision"] = int(item["revision"])
        return item

    def _require_action_conn(self, conn: sqlite3.Connection, action_id: str, actor_user_id: str | None = None) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM writer_agent_actions WHERE id=?" if actor_user_id is None else "SELECT * FROM writer_agent_actions WHERE id=? AND actor_user_id=?",
            (action_id,) if actor_user_id is None else (action_id, actor_user_id),
        ).fetchone()
        if row is None:
            raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作不存在或无权访问")
        return row

    def create_action(self, *, session_id: int, actor_user_id: str, action_type: str, project_id: Any, idempotency_key: str, request_hash: str, payload: dict[str, Any], expected_context: dict[str, Any], source_snapshot_id: str | None, expires_at: float | None = None) -> dict[str, Any]:
        now = time.time()
        target_key = self._target_key(action_type, project_id, payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute("SELECT id FROM writer_agent_sessions WHERE id=? AND user_id=?", (session_id, actor_user_id)).fetchone()
            if session is None:
                raise WriterActionError("PROJECT_NOT_AVAILABLE", "创作会话不存在或无权访问")
            existing = conn.execute("SELECT * FROM writer_agent_actions WHERE actor_user_id=? AND idempotency_key=?", (actor_user_id, idempotency_key)).fetchone()
            if existing:
                if existing["request_hash"] == request_hash:
                    return self._action_row(existing)
                raise WriterActionError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于不同动作")
            action_id = f"action-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO writer_agent_actions (id,session_id,actor_user_id,action_type,status,revision,project_id,project_id_json,target_key,idempotency_key,request_hash,source_snapshot_id,expected_context_json,payload_json,created_at,updated_at,expires_at,root_action_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (action_id, session_id, actor_user_id, action_type, "pending", 1, project_id, _json(project_id), target_key, idempotency_key, request_hash, source_snapshot_id, _json(expected_context), _json(payload), now, now, expires_at, action_id),
            )
            row = self._require_action_conn(conn, action_id)
        return self._action_row(row)

    def require_action(self, action_id: str, actor_user_id: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            action = self._action_row(
                self._require_action_conn(conn, action_id, actor_user_id)
            )
            steps = conn.execute(
                "SELECT * FROM writer_agent_action_steps WHERE action_id=? "
                "ORDER BY step_order,id",
                (action_id,),
            ).fetchall()
        return self._action_view(action, [self._step_row(row) for row in steps])

    def revise_action(self, action_id: str, *, expected_revision: int, payload: dict[str, Any] | None = None, expected_context: dict[str, Any] | None = None, phase: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["status"] != "pending":
                raise WriterActionError("ACTION_NOT_PENDING", "动作已不在待确认状态", current_revision=int(row["revision"]))
            if int(row["revision"]) != expected_revision:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            next_payload = payload if payload is not None else _decode(row["payload_json"], {})
            next_context = expected_context if expected_context is not None else _decode(row["expected_context_json"], {})
            changed = conn.execute(
                "UPDATE writer_agent_actions SET payload_json=?, expected_context_json=?, target_key=?, phase=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                (_json(next_payload), _json(next_context), self._target_key(row["action_type"], row["project_id"], next_payload), phase if phase is not None else row["phase"], int(row["revision"]) + 1, time.time(), action_id, "pending", int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id))

    @staticmethod
    def _scopes_overlap(left: str, right: str) -> bool:
        if not left or not right:
            return False
        one, two = _decode(left, {}), _decode(right, {})
        if one.get("project_id") != two.get("project_id") or one.get("stage") != two.get("stage"):
            return False
        if one.get("start") is None or two.get("start") is None:
            return True
        return max(int(one["start"]), int(two["start"])) <= min(int(one["end"]), int(two["end"]))

    def claim_pending_action(self, action_id: str, *, expected_revision: int, client_request_id: str | None = None) -> dict[str, Any]:
        action, _claimed = self.claim_pending_action_once(
            action_id,
            expected_revision=expected_revision,
            client_request_id=client_request_id,
        )
        return action

    def claim_pending_action_once(
        self,
        action_id: str,
        *,
        expected_revision: int,
        client_request_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if client_request_id and row["decision_request_id"] == client_request_id:
                return self._action_row(row), False
            if row["status"] != "pending":
                raise WriterActionError("ACTION_NOT_PENDING", "动作已不在待确认状态", current_revision=int(row["revision"]))
            if int(row["revision"]) != expected_revision:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            active = conn.execute("SELECT id,target_key FROM writer_agent_actions WHERE status IN ('queued','running') AND project_id=? AND id<>? ORDER BY created_at", (row["project_id"], action_id)).fetchall()
            for candidate in active:
                if self._scopes_overlap(row["target_key"], candidate["target_key"]):
                    raise WriterActionError("ACTIVE_ACTION_CONFLICT", "目标范围已有进行中的动作", retryable=True, conflicting_action_id=candidate["id"])
            changed = conn.execute(
                "UPDATE writer_agent_actions SET status=?, decision_request_id=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                ("queued", client_request_id, int(row["revision"]) + 1, time.time(), action_id, "pending", int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id)), True

    def request_action_cancellation_once(
        self,
        action_id: str,
        *,
        expected_revision: int,
        client_request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Durably request cancellation for pending or active work exactly once."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["cancel_request_id"] == client_request_id:
                return self._action_row(row), False
            if row["status"] not in {"pending", "queued", "running"}:
                raise WriterActionError(
                    "ACTION_STATE_INVALID",
                    "动作当前状态不可取消",
                    current_revision=int(row["revision"]),
                )
            if int(row["revision"]) != expected_revision:
                raise WriterActionError(
                    "ACTION_REVISION_MISMATCH",
                    "动作版本已变化",
                    current_revision=int(row["revision"]),
                )
            next_revision = int(row["revision"]) + 1
            if row["status"] == "pending":
                changed = conn.execute(
                    "UPDATE writer_agent_actions SET status='cancelled', "
                    "cancel_request_id=?, revision=?, updated_at=?, finished_at=? "
                    "WHERE id=? AND status='pending' AND revision=?",
                    (
                        client_request_id,
                        next_revision,
                        time.time(),
                        time.time(),
                        action_id,
                        int(row["revision"]),
                    ),
                ).rowcount
            else:
                changed = conn.execute(
                    "UPDATE writer_agent_actions SET cancel_request_id=?, "
                    "phase='cancelling', "
                    "revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                    (
                        client_request_id,
                        next_revision,
                        time.time(),
                        action_id,
                        row["status"],
                        int(row["revision"]),
                    ),
                ).rowcount
            if changed != 1:
                raise WriterActionError(
                    "ACTION_REVISION_MISMATCH",
                    "动作版本已变化",
                    current_revision=int(row["revision"]),
                )
            return self._action_row(self._require_action_conn(conn, action_id)), True

    def _transition_action(self, action_id: str, *, from_status: str, to_status: str, **updates: Any) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["status"] != from_status:
                raise WriterActionError("ACTION_NOT_PENDING" if from_status == "pending" else "ACTION_STATE_INVALID", "动作当前状态不允许此操作", current_revision=int(row["revision"]))
            fields = {"status": to_status, "revision": int(row["revision"]) + 1, "updated_at": time.time(), **updates}
            assignments = ", ".join(f"{key}=?" for key in fields)
            values = [*fields.values(), action_id, from_status, int(row["revision"])]
            changed = conn.execute(f"UPDATE writer_agent_actions SET {assignments} WHERE id=? AND status=? AND revision=?", values).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id))

    def start_action(self, action_id: str) -> dict[str, Any]:
        return self._transition_action(action_id, from_status="queued", to_status="running", started_at=time.time())

    def set_action_phase(self, action_id: str, *, phase: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            changed = conn.execute(
                "UPDATE writer_agent_actions SET phase=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                (phase, int(row["revision"]) + 1, time.time(), action_id, row["status"], int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id))

    def advance_expected_context(self, action_id: str, *, project_revision: int, authority_refs: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            context = _decode(row["expected_context_json"], {})
            current_refs = context.get("authority_refs")
            merged_refs = dict(current_refs) if isinstance(current_refs, dict) else {}
            merged_refs.update(authority_refs)
            context.update({"project_revision": project_revision, "authority_refs": merged_refs})
            changed = conn.execute(
                "UPDATE writer_agent_actions SET expected_context_json=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                (_json(context), int(row["revision"]) + 1, time.time(), action_id, row["status"], int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id))

    def finish_action(self, action_id: str, *, status: str = "succeeded", result: dict[str, Any] | None = None) -> dict[str, Any]:
        if status not in {"succeeded", "cancelled"}:
            raise ValueError("finish_action only accepts succeeded or cancelled")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["status"] == status:
                return self._action_row(row)
            if row["status"] not in {"queued", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不允许完成", current_revision=int(row["revision"]))
            return self._transition_in_connection(conn, row, status, result_json=_json(result) if result is not None else row["result_json"], finished_at=time.time())

    def fail_action(self, action_id: str, *, code: str, message: str, retryable: bool = False, conflicting_action_id: str | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["status"] == "failed":
                return self._action_row(row)
            if row["status"] not in {"queued", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不允许失败", current_revision=int(row["revision"]))
            return self._transition_in_connection(conn, row, "failed", error_code=code, error_message=message, retryable=int(retryable), conflicting_action_id=conflicting_action_id, result_json=_json(result) if result is not None else row["result_json"], finished_at=time.time())

    def _transition_in_connection(self, conn: sqlite3.Connection, row: sqlite3.Row, status: str, **updates: Any) -> dict[str, Any]:
        fields = {"status": status, "revision": int(row["revision"]) + 1, "updated_at": time.time(), **updates}
        assignments = ", ".join(f"{key}=?" for key in fields)
        changed = conn.execute(f"UPDATE writer_agent_actions SET {assignments} WHERE id=? AND status=? AND revision=?", [*fields.values(), row["id"], row["status"], int(row["revision"])]).rowcount
        if changed != 1:
            raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
        return self._action_row(self._require_action_conn(conn, row["id"]))

    def record_action_result(self, action_id: str, result: dict[str, Any]) -> dict[str, Any]:
        encoded = _json(result)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["result_json"]:
                if _json(_decode(row["result_json"], {})) == encoded:
                    return self._action_row(row)
                raise WriterActionError("IDEMPOTENCY_KEY_REUSED", "动作已记录不同结果")
            changed = conn.execute(
                "UPDATE writer_agent_actions SET result_json=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                (encoded, int(row["revision"]) + 1, time.time(), action_id, row["status"], int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化", current_revision=int(row["revision"]))
            return self._action_row(self._require_action_conn(conn, action_id))

    def attach_project_to_action(
        self,
        action_id: str,
        project_id: int,
        *,
        project_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the project created by an action before materializing its plan."""

        if type(project_id) is not int or project_id < 1:
            raise WriterActionError("RESULT_VALIDATION_FAILED", "项目 ID 无效")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            current_project_id = row["project_id"]
            if current_project_id is not None and int(current_project_id) != project_id:
                raise WriterActionError(
                    "IDEMPOTENCY_KEY_REUSED", "动作已绑定到不同项目"
                )
            expected = _decode(row["expected_context_json"], {})
            expected.update(project_context)
            changed = conn.execute(
                "UPDATE writer_agent_actions SET project_id=?, project_id_json=?, "
                "expected_context_json=?, revision=?, updated_at=? WHERE id=? "
                "AND status=? AND revision=?",
                (
                    project_id,
                    _json(project_id),
                    _json(expected),
                    int(row["revision"]) + 1,
                    time.time(),
                    action_id,
                    row["status"],
                    int(row["revision"]),
                ),
            ).rowcount
            if changed != 1:
                raise WriterActionError(
                    "ACTION_REVISION_MISMATCH",
                    "动作版本已变化",
                    current_revision=int(row["revision"]),
                )
            return self._action_row(self._require_action_conn(conn, action_id))

    def expire_pending_actions(self, *, older_than: float) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM writer_agent_actions WHERE status='pending' AND created_at < ?", (older_than,)).fetchall()
            expired = 0
            for row in rows:
                expired += conn.execute(
                    "UPDATE writer_agent_actions SET status='expired', revision=?, updated_at=?, finished_at=? WHERE id=? AND status=? AND revision=?",
                    (int(row["revision"]) + 1, time.time(), time.time(), row["id"], "pending", int(row["revision"])),
                ).rowcount
        return expired

    def list_session_actions(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM writer_agent_actions WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
            actions = [self._action_row(row) for row in rows]
            steps_by_action = {
                str(action["id"]): [
                    self._step_row(step)
                    for step in conn.execute(
                        "SELECT * FROM writer_agent_action_steps WHERE action_id=? "
                        "ORDER BY step_order,id",
                        (action["id"],),
                    ).fetchall()
                ]
                for action in actions
            }
        return [
            self._action_view(action, steps_by_action[str(action["id"])])
            for action in actions
        ]

    def list_active_actions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM writer_agent_actions WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
        return [self._action_row(row) for row in rows]

    def upsert_step(self, *, action_id: str, step_key: str, stage: str, episode_start: int | None, episode_end: int | None, status: str, operation_id: str, depends_on: list[str], attempt: int, step_order: int, job_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        normalized_dependencies = sorted({str(key) for key in depends_on})
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_action_conn(conn, action_id)
            existing = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
            if existing:
                existing_dependencies = sorted(
                    {str(key) for key in _decode(existing["depends_on_json"], [])}
                )
                existing_plan = (
                    str(existing["operation_id"]),
                    str(existing["stage"]),
                    existing["episode_start"],
                    existing["episode_end"],
                    existing_dependencies,
                    int(existing["attempt"]),
                    int(existing["step_order"]),
                )
                requested_plan = (
                    operation_id,
                    stage,
                    episode_start,
                    episode_end,
                    normalized_dependencies,
                    int(attempt),
                    int(step_order),
                )
                if existing_plan != requested_plan or (
                    job_id is not None
                    and existing["job_id"] is not None
                    and str(existing["job_id"]) != job_id
                ):
                    raise WriterActionError(
                        "IDEMPOTENCY_KEY_REUSED", "步骤键已用于不同执行计划"
                    )
                if job_id is None or existing["job_id"] == job_id:
                    return self._step_row(existing)
                changed = conn.execute(
                    "UPDATE writer_agent_action_steps SET job_id=?, revision=?, updated_at=? WHERE action_id=? AND step_key=? AND status=? AND revision=?",
                    (job_id, int(existing["revision"]) + 1, now, action_id, step_key, existing["status"], int(existing["revision"])),
                ).rowcount
                if changed != 1:
                    raise WriterActionError("ACTION_REVISION_MISMATCH", "步骤版本已变化", current_revision=int(existing["revision"]))
                row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
                return self._step_row(row)
            conn.execute(
                "INSERT INTO writer_agent_action_steps (action_id,step_key,stage,episode_start,episode_end,status,operation_id,job_id,depends_on_json,attempt,step_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (action_id, step_key, stage, episode_start, episode_end, status, operation_id, job_id, _json(normalized_dependencies), attempt, step_order, now, now),
            )
            row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
        return self._step_row(row)

    def list_runnable_steps(self, action_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            action = self._require_action_conn(conn, action_id)
            rows = conn.execute(
                "SELECT * FROM writer_agent_action_steps WHERE action_id=? "
                "ORDER BY step_order, id",
                (action_id,),
            ).fetchall()
            states = self._dependency_states_conn(conn, action_id, action)
        steps = [self._step_row(row) for row in rows]
        now = time.time()
        return [
            step
            for step in steps
            if (
                step["status"] == "pending"
                or (
                    step["status"] == "running"
                    and not step.get("job_id")
                    and float(step.get("lease_expires_at") or 0) <= now
                )
            )
            and all(
                states.get(str(key)) == "succeeded" for key in step["depends_on"]
            )
        ]

    @staticmethod
    def _dependency_states_conn(
        conn: sqlite3.Connection,
        action_id: str,
        action: sqlite3.Row,
    ) -> dict[str, str]:
        states = {
            str(item["step_key"]): str(item["status"])
            for item in conn.execute(
                "SELECT step_key,status FROM writer_agent_action_steps "
                "WHERE action_id=?",
                (action_id,),
            ).fetchall()
        }
        ancestor_id = action["retry_of_action_id"]
        visited: set[str] = set()
        while ancestor_id and str(ancestor_id) not in visited:
            ancestor_key = str(ancestor_id)
            visited.add(ancestor_key)
            ancestor_steps = conn.execute(
                "SELECT step_key,status FROM writer_agent_action_steps "
                "WHERE action_id=?",
                (ancestor_key,),
            ).fetchall()
            for ancestor_step in ancestor_steps:
                states.setdefault(
                    str(ancestor_step["step_key"]), str(ancestor_step["status"])
                )
            ancestor = conn.execute(
                "SELECT retry_of_action_id FROM writer_agent_actions WHERE id=?",
                (ancestor_key,),
            ).fetchone()
            ancestor_id = ancestor["retry_of_action_id"] if ancestor else None
        return states

    def claim_runnable_step(
        self,
        action_id: str,
        step_key: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> dict[str, Any] | None:
        """Atomically claim one runnable step before executing its side effect."""

        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            action = self._require_action_conn(conn, action_id)
            if action["status"] != "running" or action["phase"] == "cancelling":
                return None
            row = conn.execute(
                "SELECT * FROM writer_agent_action_steps "
                "WHERE action_id=? AND step_key=?",
                (action_id, step_key),
            ).fetchone()
            if row is None:
                return None
            reclaimable = row["status"] == "pending" or (
                row["status"] == "running"
                and row["job_id"] is None
                and float(row["lease_expires_at"] or 0) <= now
            )
            if not reclaimable:
                return None
            dependencies = _decode(row["depends_on_json"], [])
            if dependencies:
                states = self._dependency_states_conn(conn, action_id, action)
                if any(states.get(str(key)) != "succeeded" for key in dependencies):
                    return None
            changed = conn.execute(
                "UPDATE writer_agent_action_steps SET status='running', "
                "execution_owner=?, lease_expires_at=?, revision=?, updated_at=? "
                "WHERE action_id=? AND step_key=? AND revision=?",
                (
                    owner_id,
                    now + lease_seconds,
                    int(row["revision"]) + 1,
                    now,
                    action_id,
                    step_key,
                    int(row["revision"]),
                ),
            ).rowcount
            if changed != 1:
                return None
            claimed = conn.execute(
                "SELECT * FROM writer_agent_action_steps "
                "WHERE action_id=? AND step_key=?",
                (action_id, step_key),
            ).fetchone()
            return self._step_row(claimed)

    def renew_step_claim(
        self,
        action_id: str,
        step_key: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE writer_agent_action_steps SET lease_expires_at=?, "
                "updated_at=? WHERE action_id=? AND step_key=? AND status='running' "
                "AND execution_owner=? AND job_id IS NULL",
                (
                    time.time() + lease_seconds,
                    time.time(),
                    action_id,
                    step_key,
                    owner_id,
                ),
            ).rowcount
        return changed == 1

    def has_running_steps(self, action_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM writer_agent_action_steps "
                "WHERE action_id=? AND status='running' LIMIT 1",
                (action_id,),
            ).fetchone()
        return row is not None

    def cancel_expired_unbound_steps(self, action_id: str) -> int:
        """Cancel abandoned non-job claims after an action enters cancellation."""

        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE writer_agent_action_steps SET status='cancelled', "
                "execution_owner=NULL, lease_expires_at=NULL, "
                "error_code='CANCELLED', error_message='生成任务已取消', "
                "retryable=0, revision=revision+1, updated_at=?, finished_at=? "
                "WHERE action_id=? AND status='running' AND job_id IS NULL "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at<=? "
                "AND EXISTS (SELECT 1 FROM writer_agent_actions "
                "WHERE id=? AND phase='cancelling')",
                (now, now, action_id, now, action_id),
            ).rowcount
        return changed

    def _transition_step(self, action_id: str, step_key: str, *, status: str, allowed: set[str], execution_owner: str | None = None, idempotent: bool = False, **updates: Any) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
            if row is None:
                raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作步骤不存在")
            if idempotent and row["status"] == status:
                return self._step_row(row)
            if row["status"] not in allowed:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤当前状态不允许此操作", current_revision=int(row["revision"]))
            if execution_owner is not None and row["execution_owner"] != execution_owner:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤执行租约已失效")
            fields = {"status": status, "execution_owner": None, "lease_expires_at": None, "revision": int(row["revision"]) + 1, "updated_at": time.time(), **updates}
            assignments = ", ".join(f"{key}=?" for key in fields)
            changed = conn.execute(f"UPDATE writer_agent_action_steps SET {assignments} WHERE action_id=? AND step_key=? AND status=? AND revision=?", [*fields.values(), action_id, step_key, row["status"], int(row["revision"])]).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "步骤版本已变化", current_revision=int(row["revision"]))
            return self._step_row(conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone())

    def finish_step(self, action_id: str, step_key: str, result: dict[str, Any], *, execution_owner: str | None = None) -> dict[str, Any]:
        return self._transition_step(action_id, step_key, status="succeeded", allowed={"pending", "running"}, execution_owner=execution_owner, resource_refs_json=_json(result.get("resource_refs", [])), finished_at=time.time())

    def complete_step_with_context(
        self,
        action_id: str,
        step_key: str,
        result: dict[str, Any],
        *,
        execution_owner: str | None = None,
    ) -> dict[str, Any]:
        """Persist a successful step and its authority advance atomically."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            step = conn.execute(
                "SELECT * FROM writer_agent_action_steps "
                "WHERE action_id=? AND step_key=?",
                (action_id, step_key),
            ).fetchone()
            if step is None or step["status"] not in {"pending", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤当前状态不允许完成")
            if execution_owner is not None and step["execution_owner"] != execution_owner:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤执行租约已失效")
            now = time.time()
            changed = conn.execute(
                "UPDATE writer_agent_action_steps SET status='succeeded', "
                "resource_refs_json=?, execution_owner=NULL, lease_expires_at=NULL, "
                "revision=?, updated_at=?, finished_at=? WHERE action_id=? "
                "AND step_key=? AND status=? AND revision=?",
                (
                    _json(result.get("resource_refs", [])),
                    int(step["revision"]) + 1,
                    now,
                    now,
                    action_id,
                    step_key,
                    step["status"],
                    int(step["revision"]),
                ),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "步骤版本已变化")
            authority_revision = result.get("authority_revision")
            if authority_revision is not None:
                action = self._require_action_conn(conn, action_id)
                context = _decode(action["expected_context_json"], {})
                current_revision = int(
                    context.get("project_revision", context.get("revision", 0)) or 0
                )
                next_revision = int(authority_revision)
                if next_revision < current_revision:
                    raise WriterActionError(
                        "STALE_PROJECT_CONTEXT", "项目权威版本不能回退"
                    )
                current_refs = context.get("authority_refs")
                merged_refs = dict(current_refs) if isinstance(current_refs, dict) else {}
                incoming_refs = result.get("authority_refs")
                if isinstance(incoming_refs, dict):
                    merged_refs.update(incoming_refs)
                context.update(
                    {
                        "project_revision": next_revision,
                        "authority_refs": merged_refs,
                    }
                )
                action_changed = conn.execute(
                    "UPDATE writer_agent_actions SET expected_context_json=?, "
                    "revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                    (
                        _json(context),
                        int(action["revision"]) + 1,
                        now,
                        action_id,
                        action["status"],
                        int(action["revision"]),
                    ),
                ).rowcount
                if action_changed != 1:
                    raise WriterActionError(
                        "ACTION_REVISION_MISMATCH", "动作版本已变化"
                    )
            return self._action_row(self._require_action_conn(conn, action_id))

    def fail_step(self, action_id: str, step_key: str, *, code: str, message: str, retryable: bool, conflicting_action_id: str | None = None, execution_owner: str | None = None) -> dict[str, Any]:
        return self._transition_step(action_id, step_key, status="failed", allowed={"pending", "running"}, execution_owner=execution_owner, error_code=code, error_message=message, retryable=int(retryable), conflicting_action_id=conflicting_action_id, finished_at=time.time())

    def cancel_step(self, action_id: str, step_key: str) -> dict[str, Any]:
        return self._transition_step(
            action_id,
            step_key,
            status="cancelled",
            allowed={"pending", "running"},
            idempotent=True,
            error_code="CANCELLED",
            error_message="生成任务已取消",
            retryable=0,
            finished_at=time.time(),
        )

    def assign_step_job(
        self,
        action_id: str,
        step_key: str,
        job_id: str,
        *,
        execution_owner: str | None = None,
    ) -> dict[str, Any]:
        """Persist a generated job identity before waiting for its terminal state."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            action = self._require_action_conn(conn, action_id)
            row = conn.execute(
                "SELECT * FROM writer_agent_action_steps "
                "WHERE action_id=? AND step_key=?",
                (action_id, step_key),
            ).fetchone()
            if row is None:
                raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作步骤不存在")
            if row["job_id"] is not None:
                if str(row["job_id"]) == str(job_id):
                    return self._step_row(row)
                raise WriterActionError(
                    "IDEMPOTENCY_KEY_REUSED", "步骤已绑定不同生成任务"
                )
            if row["status"] not in {"pending", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤当前状态不可绑定任务")
            if execution_owner is not None and row["execution_owner"] != execution_owner:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤执行租约已失效")
            changed = conn.execute(
                "UPDATE writer_agent_action_steps SET job_id=?, status='running', "
                "lease_expires_at=NULL, revision=?, updated_at=? "
                "WHERE action_id=? AND step_key=? AND status=? AND revision=?",
                (
                    str(job_id),
                    int(row["revision"]) + 1,
                    time.time(),
                    action_id,
                    step_key,
                    row["status"],
                    int(row["revision"]),
                ),
            ).rowcount
            if changed != 1:
                raise WriterActionError(
                    "ACTION_REVISION_MISMATCH",
                    "步骤版本已变化",
                    current_revision=int(row["revision"]),
                )
            updated = conn.execute(
                "SELECT * FROM writer_agent_action_steps "
                "WHERE action_id=? AND step_key=?",
                (action_id, step_key),
            ).fetchone()
            result = self._step_row(updated)
            result["action_cancelling"] = action["phase"] == "cancelling"
            return result

    def block_failed_dependents(self, action_id: str) -> int:
        """Mark pending steps blocked once any dependency cannot succeed."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM writer_agent_action_steps WHERE action_id=? "
                "ORDER BY step_order,id",
                (action_id,),
            ).fetchall()
            states = {str(row["step_key"]): str(row["status"]) for row in rows}
            changed = 0
            for row in rows:
                if row["status"] != "pending":
                    continue
                dependencies = _decode(row["depends_on_json"], [])
                if not any(
                    states.get(str(key)) in {"failed", "blocked", "cancelled"}
                    for key in dependencies
                ):
                    continue
                changed += conn.execute(
                    "UPDATE writer_agent_action_steps SET status='blocked', "
                    "error_code='DEPENDENCY_FAILED', error_message='依赖步骤失败', "
                    "revision=?, updated_at=?, finished_at=? "
                    "WHERE action_id=? AND step_key=? AND status='pending' "
                    "AND revision=?",
                    (
                        int(row["revision"]) + 1,
                        time.time(),
                        time.time(),
                        action_id,
                        row["step_key"],
                        int(row["revision"]),
                    ),
                ).rowcount
            return changed

    def list_steps(self, action_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? ORDER BY step_order, id", (action_id,)).fetchall()
        return [self._step_row(row) for row in rows]

    def find_action_id_by_operation(self, operation_id: str) -> str | None:
        """Return the active Writer action that owns a durable operation id."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT steps.action_id FROM writer_agent_action_steps AS steps "
                "JOIN writer_agent_actions AS actions ON actions.id=steps.action_id "
                "WHERE steps.operation_id=? "
                "ORDER BY CASE WHEN actions.status IN ('queued','running') "
                "THEN 0 ELSE 1 END, actions.created_at DESC LIMIT 1",
                (operation_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def active_job_ids(self, action_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT job_id FROM writer_agent_action_steps WHERE action_id=? AND job_id IS NOT NULL AND status NOT IN ('succeeded','failed','cancelled','blocked')", (action_id,)).fetchall()
        return [str(row["job_id"]) for row in rows]

    def refresh_job_steps(self, action_id: str, job_manager: Any) -> dict[str, Any]:
        for step in self.list_steps(action_id):
            if not step.get("job_id") or step["status"] in _STEP_TERMINAL_STATUSES:
                continue
            job = job_manager.get(step["job_id"]) if hasattr(job_manager, "get") else job_manager.get_job(step["job_id"])
            status = getattr(job, "status", None) if not isinstance(job, dict) else job.get("status")
            if status in {"succeeded", "completed"}:
                self.finish_step(action_id, step["step_key"], {"resource_refs": step["resource_refs"]})
            elif status in {"failed", "cancelled"}:
                self._transition_step(action_id, step["step_key"], status=str(status), allowed={"pending", "running"}, error_code="GENERATION_FAILED" if status == "failed" else "CANCELLED", error_message=f"job {status}", retryable=int(status == "failed"), finished_at=time.time())
        return self.require_action(action_id, None)

    def require_retryable_action(self, action_id: str, actor_user_id: str) -> dict[str, Any]:
        action = self.require_action(action_id, actor_user_id)
        if action["status"] != "failed" or not action["retryable"]:
            raise WriterActionError("ACTION_STATE_INVALID", "该动作不可重试")
        return action

    def create_retry_action(
        self,
        source: dict[str, Any],
        actor_user_id: str,
        *,
        retry_request_id: str,
    ) -> dict[str, Any]:
        retry, _created = self.create_retry_action_once(
            source, actor_user_id, retry_request_id=retry_request_id
        )
        return retry

    def create_retry_action_once(
        self,
        source: dict[str, Any],
        actor_user_id: str,
        *,
        retry_request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically create a retry or return the prior retry with its origin."""
        retry_request_id = retry_request_id.strip()
        if not retry_request_id:
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED", "重试请求必须提供稳定请求 ID"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._require_action_conn(conn, str(source["id"]), actor_user_id)
            if current["status"] != "failed" or not bool(current["retryable"]):
                raise WriterActionError("ACTION_STATE_INVALID", "该动作不可重试")
            retry_key = f"retry:{retry_request_id}"
            existing = conn.execute(
                "SELECT * FROM writer_agent_actions WHERE actor_user_id=? "
                "AND idempotency_key=?",
                (actor_user_id, retry_key),
            ).fetchone()
            if existing:
                if (
                    existing["request_hash"] != current["request_hash"]
                    or existing["retry_of_action_id"] != current["id"]
                ):
                    raise WriterActionError(
                        "IDEMPOTENCY_KEY_REUSED", "重试幂等键已用于不同动作"
                    )
                return self._action_row(existing), False
            active = conn.execute(
                "SELECT id,target_key FROM writer_agent_actions "
                "WHERE status IN ('queued','running') AND project_id=? "
                "ORDER BY created_at",
                (current["project_id"],),
            ).fetchall()
            for candidate in active:
                if self._scopes_overlap(
                    current["target_key"], candidate["target_key"]
                ):
                    raise WriterActionError(
                        "ACTIVE_ACTION_CONFLICT",
                        "目标范围已有进行中的动作",
                        retryable=True,
                        conflicting_action_id=candidate["id"],
                    )
            now = time.time()
            action_id = f"action-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO writer_agent_actions "
                "(id,session_id,actor_user_id,action_type,status,revision,project_id,project_id_json,"
                "target_key,idempotency_key,request_hash,source_snapshot_id,"
                "expected_context_json,payload_json,retry_of_action_id,root_action_id,"
                "result_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    current["session_id"],
                    actor_user_id,
                    current["action_type"],
                    "queued",
                    1,
                    current["project_id"],
                    current["project_id_json"],
                    current["target_key"],
                    retry_key,
                    current["request_hash"],
                    current["source_snapshot_id"],
                    current["expected_context_json"],
                    current["payload_json"],
                    current["id"],
                    current["root_action_id"] or current["id"],
                    current["result_json"],
                    now,
                    now,
                ),
            )
            source_steps = conn.execute(
                "SELECT * FROM writer_agent_action_steps WHERE action_id=? "
                "ORDER BY step_order, id",
                (current["id"],),
            ).fetchall()
            retry_step_keys = {
                str(step["step_key"])
                for step in source_steps
                if step["status"] == "failed" and bool(step["retryable"])
            }
            if not retry_step_keys:
                retry_step_keys = {
                    str(step["step_key"])
                    for step in source_steps
                    if step["status"] in {"failed", "pending"}
                }
            changed = True
            while changed:
                changed = False
                for step in source_steps:
                    step_key = str(step["step_key"])
                    if step_key in retry_step_keys or step["status"] != "blocked":
                        continue
                    dependencies = {
                        str(key) for key in _decode(step["depends_on_json"], [])
                    }
                    if dependencies & retry_step_keys:
                        retry_step_keys.add(step_key)
                        changed = True
            for step in source_steps:
                if str(step["step_key"]) not in retry_step_keys:
                    continue
                conn.execute(
                    "INSERT INTO writer_agent_action_steps "
                    "(action_id,step_key,stage,episode_start,episode_end,status,"
                    "operation_id,job_id,depends_on_json,attempt,step_order,created_at,"
                    "updated_at) VALUES (?,?,?,?,?,'pending',?,NULL,?,?,?,?,?)",
                    (
                        action_id,
                        step["step_key"],
                        step["stage"],
                        step["episode_start"],
                        step["episode_end"],
                        step["operation_id"],
                        step["depends_on_json"],
                        int(step["attempt"]) + 1,
                        int(step["step_order"]),
                        now,
                        now,
                    ),
                )
            retry = self._require_action_conn(conn, action_id, actor_user_id)
        return self._action_row(retry), True

    def save_source_snapshot(self, snapshot: dict[str, Any], *, snapshot_id: str | None = None) -> dict[str, Any]:
        encoded = _json(snapshot)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if snapshot_id is not None:
                identified = conn.execute(
                    "SELECT * FROM writer_agent_source_snapshots WHERE id=?",
                    (snapshot_id,),
                ).fetchone()
                if identified:
                    if identified["fingerprint"] != fingerprint:
                        raise WriterActionError(
                            "IDEMPOTENCY_KEY_REUSED", "材料快照 ID 已用于不同内容"
                        )
                    return {"id": identified["id"], "fingerprint": identified["fingerprint"], "snapshot": _decode(identified["snapshot_json"], {}), "created_at": identified["created_at"]}
            existing = conn.execute("SELECT * FROM writer_agent_source_snapshots WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return {"id": existing["id"], "fingerprint": existing["fingerprint"], "snapshot": _decode(existing["snapshot_json"], {}), "created_at": existing["created_at"]}
            identifier = snapshot_id or f"snapshot-{uuid.uuid4().hex}"
            now = time.time()
            conn.execute("INSERT INTO writer_agent_source_snapshots (id,fingerprint,snapshot_json,created_at) VALUES (?,?,?,?)", (identifier, fingerprint, encoded, now))
        return {"id": identifier, "fingerprint": fingerprint, "snapshot": snapshot, "created_at": now}

    def require_source_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM writer_agent_source_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise WriterActionError("REFERENCE_UNAVAILABLE", "材料快照不存在")
        return {"id": row["id"], "fingerprint": row["fingerprint"], "snapshot": _decode(row["snapshot_json"], {}), "created_at": row["created_at"]}
