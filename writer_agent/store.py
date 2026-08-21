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


_ACTION_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "expired"})
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
        ):
            if name not in columns:
                conn.execute(statement)

    @staticmethod
    def _create_action_tables(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_actions ("
            "id TEXT PRIMARY KEY, session_id INTEGER NOT NULL, actor_user_id TEXT NOT NULL, "
            "action_type TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', "
            "revision INTEGER NOT NULL, project_id INTEGER, target_key TEXT NOT NULL DEFAULT '', "
            "idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, source_snapshot_id TEXT, "
            "expected_context_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT NOT NULL DEFAULT '{}', "
            "result_json TEXT, error_code TEXT, error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0, "
            "job_id TEXT, decision_request_id TEXT, retry_of_action_id TEXT, root_action_id TEXT, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL, expires_at REAL, "
            "UNIQUE(actor_user_id, idempotency_key))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_action_steps ("
            "id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, step_key TEXT NOT NULL, stage TEXT NOT NULL, "
            "episode_start INTEGER, episode_end INTEGER, status TEXT NOT NULL, operation_id TEXT NOT NULL, "
            "job_id TEXT, depends_on_json TEXT NOT NULL DEFAULT '[]', resource_refs_json TEXT NOT NULL DEFAULT '[]', "
            "error_code TEXT, error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL, "
            "step_order INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "finished_at REAL, UNIQUE(action_id, step_key))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS writer_agent_source_snapshots ("
            "id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL)"
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
        session = self.get(session_id, user_id)
        if not session:
            return {}
        messages = list(session.get("messages") or [])
        messages.append(_material_message(filename, content))
        combined = f"{session['content']}\n\n[补充材料：{filename}]\n{content}"
        now = time.time()
        with self._connect() as conn:
            sql = (
                "UPDATE writer_agent_sessions SET content=?, messages_json=?, context_revision=context_revision+1, updated_at=? WHERE id=?"
                if user_id is None
                else "UPDATE writer_agent_sessions SET content=?, messages_json=?, context_revision=context_revision+1, updated_at=? WHERE id=? AND user_id=?"
            )
            values: tuple[Any, ...] = (combined, _json(messages), now, session_id)
            if user_id is not None:
                values += (user_id,)
            conn.execute(sql, values)
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

    def save_turn(self, session_id: int, user_id: str | None, messages: list[dict[str, Any]], report: dict[str, Any] | None) -> dict[str, Any]:
        session = self.get(session_id, user_id)
        if not session:
            return {}
        normalized_messages = [dict(message) for message in messages]
        for message in normalized_messages:
            message.setdefault("message_id", _message_id())
        reports = list(session.get("reports") or [])
        latest_report = dict(session.get("report") or {})
        if report is not None:
            entry = {"id": f"report-{len(reports) + 1}", "created_at": time.time(), "report": report}
            reports.append(entry)
            latest_report = report
            if normalized_messages and normalized_messages[-1].get("role") == "assistant":
                normalized_messages[-1]["report_id"] = entry["id"]
        now = time.time()
        with self._connect() as conn:
            sql = (
                "UPDATE writer_agent_sessions SET messages_json=?, report_json=?, reports_json=?, updated_at=? WHERE id=?"
                if user_id is None
                else "UPDATE writer_agent_sessions SET messages_json=?, report_json=?, reports_json=?, updated_at=? WHERE id=? AND user_id=?"
            )
            values: tuple[Any, ...] = (_json(normalized_messages), _json(latest_report), _json(reports), now, session_id)
            if user_id is not None:
                values += (user_id,)
            conn.execute(sql, values)
        return self.get(session_id, user_id) or {}

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
                "UPDATE writer_agent_sessions SET project_id=?, context_revision=?, updated_at=? WHERE id=? AND context_revision=?",
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
        item["actions"] = self.list_session_actions(int(item["id"]))
        return item

    @staticmethod
    def _target_key(action_type: str, project_id: int | None, payload: dict[str, Any]) -> str:
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
        item["revision"] = int(item["revision"])
        item["retryable"] = bool(item["retryable"])
        item["target_stage"] = item["payload"].get("target_stage")
        return item

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

    def create_action(self, *, session_id: int, actor_user_id: str, action_type: str, project_id: int | None, idempotency_key: str, request_hash: str, payload: dict[str, Any], expected_context: dict[str, Any], source_snapshot_id: str | None, expires_at: float | None = None) -> dict[str, Any]:
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
                "INSERT INTO writer_agent_actions (id,session_id,actor_user_id,action_type,status,revision,project_id,target_key,idempotency_key,request_hash,source_snapshot_id,expected_context_json,payload_json,created_at,updated_at,expires_at,root_action_id) "
                "VALUES (?,?,?,?, 'pending',1,?,?,?,?,?,?,?,?,?,?,?)",
                (action_id, session_id, actor_user_id, action_type, project_id, target_key, idempotency_key, request_hash, source_snapshot_id, _json(expected_context), _json(payload), now, now, expires_at, action_id),
            )
            row = self._require_action_conn(conn, action_id)
        return self._action_row(row)

    def require_action(self, action_id: str, actor_user_id: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            return self._action_row(self._require_action_conn(conn, action_id, actor_user_id))

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
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if client_request_id and row["decision_request_id"] == client_request_id:
                return self._action_row(row)
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
            return self._action_row(self._require_action_conn(conn, action_id))

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
            context.update({"project_revision": project_revision, "authority_refs": authority_refs})
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
            if row["status"] not in {"queued", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不允许完成", current_revision=int(row["revision"]))
            return self._transition_in_connection(conn, row, status, result_json=_json(result) if result is not None else row["result_json"], finished_at=time.time())

    def fail_action(self, action_id: str, *, code: str, message: str, retryable: bool = False, result: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, action_id)
            if row["status"] not in {"queued", "running"}:
                raise WriterActionError("ACTION_STATE_INVALID", "动作当前状态不允许失败", current_revision=int(row["revision"]))
            return self._transition_in_connection(conn, row, "failed", error_code=code, error_message=message, retryable=int(retryable), result_json=_json(result) if result is not None else row["result_json"], finished_at=time.time())

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
        return [self._action_row(row) for row in rows]

    def list_active_actions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM writer_agent_actions WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
        return [self._action_row(row) for row in rows]

    def upsert_step(self, *, action_id: str, step_key: str, stage: str, episode_start: int | None, episode_end: int | None, status: str, operation_id: str, depends_on: list[str], attempt: int, step_order: int, job_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_action_conn(conn, action_id)
            existing = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
            if existing:
                if existing["operation_id"] != operation_id:
                    raise WriterActionError("IDEMPOTENCY_KEY_REUSED", "步骤键已用于不同 operation_id")
                changed = conn.execute(
                    "UPDATE writer_agent_action_steps SET job_id=COALESCE(?, job_id), revision=?, updated_at=? WHERE action_id=? AND step_key=? AND status=? AND revision=?",
                    (job_id, int(existing["revision"]) + 1, now, action_id, step_key, existing["status"], int(existing["revision"])),
                ).rowcount
                if changed != 1:
                    raise WriterActionError("ACTION_REVISION_MISMATCH", "步骤版本已变化", current_revision=int(existing["revision"]))
                row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
                return self._step_row(row)
            conn.execute(
                "INSERT INTO writer_agent_action_steps (action_id,step_key,stage,episode_start,episode_end,status,operation_id,job_id,depends_on_json,attempt,step_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (action_id, step_key, stage, episode_start, episode_end, status, operation_id, job_id, _json(depends_on), attempt, step_order, now, now),
            )
            row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
        return self._step_row(row)

    def list_runnable_steps(self, action_id: str) -> list[dict[str, Any]]:
        steps = self.list_steps(action_id)
        states = {step["step_key"]: step["status"] for step in steps}
        return [step for step in steps if step["status"] == "pending" and all(states.get(key, "succeeded") == "succeeded" for key in step["depends_on"])]

    def _transition_step(self, action_id: str, step_key: str, *, status: str, allowed: set[str], **updates: Any) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone()
            if row is None:
                raise WriterActionError("PROJECT_NOT_AVAILABLE", "动作步骤不存在")
            if row["status"] not in allowed:
                raise WriterActionError("ACTION_STATE_INVALID", "步骤当前状态不允许此操作", current_revision=int(row["revision"]))
            fields = {"status": status, "revision": int(row["revision"]) + 1, "updated_at": time.time(), **updates}
            assignments = ", ".join(f"{key}=?" for key in fields)
            changed = conn.execute(f"UPDATE writer_agent_action_steps SET {assignments} WHERE action_id=? AND step_key=? AND status=? AND revision=?", [*fields.values(), action_id, step_key, row["status"], int(row["revision"])]).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "步骤版本已变化", current_revision=int(row["revision"]))
            return self._step_row(conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? AND step_key=?", (action_id, step_key)).fetchone())

    def finish_step(self, action_id: str, step_key: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._transition_step(action_id, step_key, status="succeeded", allowed={"pending", "running"}, resource_refs_json=_json(result.get("resource_refs", [])), finished_at=time.time())

    def fail_step(self, action_id: str, step_key: str, *, code: str, message: str, retryable: bool) -> dict[str, Any]:
        return self._transition_step(action_id, step_key, status="failed", allowed={"pending", "running"}, error_code=code, error_message=message, retryable=int(retryable), finished_at=time.time())

    def list_steps(self, action_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM writer_agent_action_steps WHERE action_id=? ORDER BY step_order, id", (action_id,)).fetchall()
        return [self._step_row(row) for row in rows]

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
                self._transition_step(action_id, step["step_key"], status=str(status), allowed={"pending", "running"}, error_code="GENERATION_FAILED" if status == "failed" else "CANCELLED", error_message=f"job {status}", finished_at=time.time())
        return self.require_action(action_id, None)

    def require_retryable_action(self, action_id: str, actor_user_id: str) -> dict[str, Any]:
        action = self.require_action(action_id, actor_user_id)
        if action["status"] != "failed" or not action["retryable"]:
            raise WriterActionError("ACTION_STATE_INVALID", "该动作不可重试")
        return action

    def create_retry_action(self, source: dict[str, Any], actor_user_id: str) -> dict[str, Any]:
        if source["actor_user_id"] != actor_user_id:
            raise WriterActionError("PROJECT_ACCESS_DENIED", "无权重试该动作")
        retry_key = f"retry:{source['id']}:{uuid.uuid4().hex}"
        retry = self.create_action(
            session_id=int(source["session_id"]), actor_user_id=actor_user_id,
            action_type=str(source["action_type"]), project_id=source.get("project_id"),
            idempotency_key=retry_key, request_hash=str(source["request_hash"]),
            payload=dict(source["payload"]), expected_context=dict(source["expected_context"]),
            source_snapshot_id=source.get("source_snapshot_id"),
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_action_conn(conn, retry["id"])
            changed = conn.execute(
                "UPDATE writer_agent_actions SET retry_of_action_id=?, root_action_id=?, revision=?, updated_at=? WHERE id=? AND status=? AND revision=?",
                (source["id"], source.get("root_action_id") or source["id"], int(row["revision"]) + 1, time.time(), retry["id"], "pending", int(row["revision"])),
            ).rowcount
            if changed != 1:
                raise WriterActionError("ACTION_REVISION_MISMATCH", "动作版本已变化")
        for step in self.list_steps(source["id"]):
            if step["status"] not in {"failed", "pending", "blocked"}:
                continue
            self.upsert_step(
                action_id=retry["id"], step_key=step["step_key"], stage=step["stage"],
                episode_start=step["episode_start"], episode_end=step["episode_end"], status="pending",
                operation_id=step["operation_id"], depends_on=step["depends_on"], attempt=int(step["attempt"]) + 1,
                step_order=int(step["step_order"]), job_id=None,
            )
        return self.require_action(retry["id"], actor_user_id)

    def save_source_snapshot(self, snapshot: dict[str, Any], *, snapshot_id: str | None = None) -> dict[str, Any]:
        encoded = _json(snapshot)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
