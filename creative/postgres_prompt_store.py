# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""PostgreSQL persistence for immutable prompt templates and project bindings."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import CursorResult

from creative.prompt_registry import (
    builtin_templates,
    compose_prompt_snapshot,
    content_hash,
    normalize_template_content,
)


class _PromptRow(dict[str, Any]):
    """SQLite-row compatible mapping returned by the PostgreSQL adapter."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _prompt_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


class _PromptCursor:
    def __init__(self, result: CursorResult[Any] | None, *, lastrowid: int = 0) -> None:
        self.result = result
        self.lastrowid = lastrowid
        self.rowcount = int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def _row(row: Any) -> _PromptRow | None:
        if row is None:
            return None
        values = row._mapping if hasattr(row, "_mapping") else row
        return _PromptRow({key: _prompt_value(value) for key, value in dict(values).items()})

    def fetchone(self) -> _PromptRow | None:
        return self._row(self.result.fetchone() if self.result is not None else None)

    def fetchall(self) -> list[_PromptRow]:
        if self.result is None:
            return []
        return [self._row(row) for row in self.result.fetchall()]


class _PostgresPromptConnection:
    """Small DB-API compatibility layer for the established prompt SQL."""

    def __init__(self, database: Any) -> None:
        self.database = database
        self._scope: Any = None
        self.connection: Any = None

    def __enter__(self) -> "_PostgresPromptConnection":
        self._scope = self.database.transaction()
        self.connection = self._scope.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._scope.__exit__(exc_type, exc, traceback)

    @staticmethod
    def _parameter(value: Any) -> Any:
        if isinstance(value, float) and value > 100_000_000:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return value

    def execute(self, statement: str, parameters: Any = ()) -> _PromptCursor:
        sql = str(statement).strip()
        if sql.upper() == "BEGIN IMMEDIATE":
            # Preserve the read/check/write serialization used by governance,
            # version saves and marketplace idempotency in the original store.
            return _PromptCursor(
                self.connection.exec_driver_sql(
                    "SELECT pg_advisory_xact_lock(593245723510639778)"
                )
            )
        if "sqlite_master" in sql:
            sql = sql.replace(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ?",
            )
        sql = sql.replace("SELECT rowid,", "SELECT ctid::text AS rowid,")
        insert_or_ignore = "INSERT OR IGNORE INTO" in sql.upper()
        if insert_or_ignore:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            sql = f"{sql.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
        wants_lastrowid = (
            "INSERT INTO creative_prompt_template_revisions" in sql
            and "RETURNING" not in sql.upper()
        )
        if wants_lastrowid:
            sql = f"{sql.rstrip().rstrip(';')} RETURNING id"
        sql = sql.replace("?", "%s")
        bound = tuple(self._parameter(value) for value in (parameters or ()))
        result = self.connection.exec_driver_sql(sql, bound)
        if wants_lastrowid:
            row = result.fetchone()
            return _PromptCursor(None, lastrowid=int(row[0]))
        return _PromptCursor(result)


class PromptStore:
    """Own prompt-library records without expanding CreativeRepository further."""

    def __init__(self, backend: Any) -> None:
        if not hasattr(backend, "database"):
            raise TypeError("PromptStore requires a PostgreSQL creative repository")
        self.repository = backend
        self.seed_builtin_templates()
        self._hide_legacy_builtin_aliases()
        self._backfill_governance_approved_revisions()
        self._ensure_enabled_project_stage_binding("brief")
        self._ensure_enabled_project_stage_binding("characters")
        self._ensure_enabled_project_stage_binding("outline")
        self._ensure_enabled_project_stage_binding("review")
        self._ensure_enabled_project_stage_binding("optimization")

    def _connect(self) -> _PostgresPromptConnection:
        return _PostgresPromptConnection(self.repository.database)

    def seed_builtin_templates(self) -> None:
        now = time.time()
        with self._connect() as conn:
            for item in builtin_templates():
                template_id = f"system:{item['key']}"
                content = normalize_template_content(item)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO creative_prompt_templates (
                        id, template_key, title, description, owner_scope,
                        visibility, status, catalog_visible, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'system', 'system', 'active', ?, ?, ?)
                    """,
                    (
                        template_id,
                        item["key"],
                        item["title"],
                        item["description"],
                        bool(item.get("catalog_visible", True)),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE creative_prompt_templates SET title = ?, description = ?, "
                    "catalog_visible = ?, updated_at = ? WHERE id = ?",
                    (
                        item["title"],
                        item["description"],
                        bool(item.get("catalog_visible", True)),
                        now,
                        template_id,
                    ),
                )
                latest = conn.execute(
                    "SELECT * FROM creative_prompt_template_revisions "
                    "WHERE template_id = ? ORDER BY revision DESC LIMIT 1",
                    (template_id,),
                ).fetchone()
                new_hash = content_hash(content)
                if latest is None:
                    conn.execute(
                        """
                        INSERT INTO creative_prompt_template_revisions (
                            template_id, revision, content_json, content_hash,
                            created_by, created_at
                        ) VALUES (?, 1, ?, ?, 'system', ?)
                        """,
                        (
                            template_id,
                            json.dumps(content, ensure_ascii=False),
                            new_hash,
                            now,
                        ),
                    )

                elif str(latest["content_hash"]) != new_hash:
                    conn.execute(
                        """
                        INSERT INTO creative_prompt_template_revisions (
                            template_id, revision, parent_revision_id,
                            content_json, content_hash, created_by, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'system', ?)
                        """,
                        (
                            template_id,
                            int(latest["revision"]) + 1,
                            int(latest["id"]),
                            json.dumps(content, ensure_ascii=False),
                            new_hash,
                            now,
                        ),
                    )

    def _hide_legacy_builtin_aliases(self) -> None:
        """Hide migrated aliases when their canonical built-in exists.

        Legacy project imports namespace every template key to avoid identity
        collisions.  Built-in templates therefore survive as ``legacy:*``
        aliases beside the canonical ``system:*`` rows.  Existing bindings may
        keep using those immutable aliases, but they must not appear as a
        second selectable system template.
        """

        now = time.time()
        with self._connect() as conn:
            for item in builtin_templates():
                canonical_id = f"system:{item['key']}"
                canonical = conn.execute(
                    "SELECT 1 FROM creative_prompt_templates "
                    "WHERE id = ? AND owner_scope = 'system'",
                    (canonical_id,),
                ).fetchone()
                if canonical is None:
                    continue
                conn.execute(
                    "UPDATE creative_prompt_templates "
                    "SET catalog_visible = FALSE, updated_at = ? "
                    "WHERE owner_scope = 'system' AND id != ? "
                    "AND template_key LIKE ? AND catalog_visible = TRUE",
                    (now, canonical_id, f"legacy:%:{item['key']}"),
                )

    def _backfill_governance_approved_revisions(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE creative_prompt_templates
                SET approved_revision = COALESCE((
                    SELECT MAX(revision)
                    FROM creative_prompt_template_revisions revisions
                    WHERE revisions.template_id = creative_prompt_templates.id
                ), 0)
                WHERE governance_status = 'approved'
                  AND (owner_scope = 'system' OR approved_revision = 0)
                """
            )

    @staticmethod
    def _supports_stage(revision: dict[str, Any], stage: str) -> bool:
        rules = (revision.get("content") or {}).get("stage_rules") or {}
        stage_rules = rules.get(stage)
        return isinstance(stage_rules, dict) and all(
            str(stage_rules.get(mode) or "").strip()
            for mode in ("single", "series", "repair", "rewrite")
        )

    def _ensure_enabled_project_stage_binding(self, stage: str) -> None:
        """Add one newly introduced stage without changing existing bindings."""

        if stage not in {"brief", "characters", "outline", "review", "optimization"}:
            raise ValueError("只允许迁移无单集维度的提示词阶段")

        with self._connect() as conn:
            projects = conn.execute(
                "SELECT project_id, default_template_id, default_template_revision "
                "FROM creative_project_prompt_settings WHERE enabled = TRUE AND NOT EXISTS ("
                "SELECT 1 FROM creative_project_prompt_bindings binding "
                "WHERE binding.project_id = creative_project_prompt_settings.project_id "
                "AND binding.stage = ? AND binding.episode_number = 0)",
                (stage,),
            ).fetchall()
        generic = self.get_template_by_key("generic-vertical-short")
        generic_revision = self.get_revision(generic["id"]) if generic else None
        for project in projects:
            candidates: list[tuple[str, dict[str, Any]]] = []
            template_id = str(project["default_template_id"] or "")
            if template_id:
                try:
                    template = self.get_template(template_id)
                    if template is None:
                        raise ValueError("默认模板不存在")
                    self._assert_template_selectable(template)
                    approved_revision = int(template["approved_revision"])
                    pinned = self.get_revision(
                        template_id, int(project["default_template_revision"])
                    )
                    if int(pinned["revision"]) <= approved_revision:
                        candidates.append((template_id, pinned))
                    approved = self.get_revision(template_id, approved_revision)
                    if int(approved["revision"]) != int(pinned["revision"]):
                        candidates.append((template_id, approved))
                except (TypeError, ValueError):
                    pass
            if generic is not None and generic_revision is not None:
                candidates.append((str(generic["id"]), generic_revision))
            selected = next(
                (
                    (candidate_id, revision)
                    for candidate_id, revision in candidates
                    if self._supports_stage(revision, stage)
                ),
                None,
            )
            if selected is None:
                continue
            candidate_id, revision = selected
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO creative_project_prompt_bindings ("
                    "project_id, stage, episode_number, template_id, "
                    "template_revision, override_json, updated_by, updated_at"
                    ") VALUES (?, ?, 0, ?, ?, '{}', ?, ?)",
                    (
                        int(project["project_id"]),
                        stage,
                        candidate_id,
                        int(revision["revision"]),
                        f"system:{stage}-migration",
                        time.time(),
                    ),
                )
    @staticmethod
    def _template_row(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _revision_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json") or "{}")
        return item

    def list_catalog(
        self, *, project_id: int | None = None, actor_user_id: str = ""
    ) -> list[dict[str, Any]]:
        clauses = ["(owner_scope = 'system' AND catalog_visible = TRUE)"]
        params: list[Any] = []
        if actor_user_id:
            clauses.append("(owner_scope = 'user' AND owner_user_id = ?)")
            params.append(actor_user_id)
        if project_id is not None:
            clauses.append("(owner_scope = 'project' AND project_id = ?)")
            params.append(int(project_id))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_prompt_templates "
                "WHERE status = 'active' AND governance_status = 'approved' AND ("
                + " OR ".join(clauses)
                + ") ORDER BY owner_scope, title",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            canonical_system_keys = {
                str(row["template_key"])
                for row in rows
                if row["owner_scope"] == "system"
                and not str(row["template_key"]).startswith("legacy:")
            }
            for row in rows:
                template = self._template_row(row)
                template_key = str(template["template_key"])
                if (
                    template["owner_scope"] == "system"
                    and template_key.startswith("legacy:")
                    and template_key.rsplit(":", 1)[-1] in canonical_system_keys
                ):
                    continue
                revision = self.get_revision(template["id"])
                template["revision"] = revision["revision"]
                template["content"] = revision["content"]
                template["content_hash"] = revision["content_hash"]
                template["supported_stages"] = [
                    stage
                    for stage in (
                        "brief", "characters", "outline", "beats", "script",
                        "review", "optimization",
                    )
                    if self._supports_stage(revision, stage)
                ]
                result.append(template)
        return result

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_prompt_templates WHERE id = ?",
                (str(template_id),),
            ).fetchone()
        return self._template_row(row) if row is not None else None

    def get_template_by_key(self, template_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_prompt_templates WHERE template_key = ?",
                (str(template_key),),
            ).fetchone()
        return self._template_row(row) if row is not None else None

    def get_revision(
        self, template_id: str, revision: int | None = None
    ) -> dict[str, Any]:
        sql = (
            "SELECT * FROM creative_prompt_template_revisions WHERE template_id = ? "
            + ("AND revision = ?" if revision is not None else "ORDER BY revision DESC LIMIT 1")
        )
        params: tuple[Any, ...] = (
            (str(template_id), int(revision))
            if revision is not None
            else (str(template_id),)
        )
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            raise ValueError("模板版本不存在")
        return self._revision_row(row)

    @staticmethod
    def _assert_template_selectable(
        template: dict[str, Any], *, revision: int | None = None
    ) -> None:
        if (
            str(template.get("status") or "active") != "active"
            or str(template.get("governance_status") or "approved") != "approved"
        ):
            raise ValueError("模板尚未通过审核或已停止使用")
        approved_revision = int(template.get("approved_revision") or 0)
        if revision is not None and approved_revision and int(revision) > approved_revision:
            raise ValueError("所选模板版本尚未通过审核")

    @staticmethod
    def _governance_event_row(
        row: Any
    ) -> dict[str, Any]:
        item = dict(row)
        item["impact"] = json.loads(item.pop("impact_json") or "{}")
        return item

    @staticmethod
    def _json_references_template(
        value: Any, *, template_id: str, template_key: str
    ) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"template_id", "prompt_template_id"} and str(item) == str(
                    template_id
                ):
                    return True
                if key in {"template_key", "prompt_template_key"} and str(item) == str(
                    template_key
                ):
                    return True
                if PromptStore._json_references_template(
                    item, template_id=template_id, template_key=template_key
                ):
                    return True
        elif isinstance(value, list):
            return any(
                PromptStore._json_references_template(
                    item, template_id=template_id, template_key=template_key
                )
                for item in value
            )
        return False

    def template_impact(
        self, project_id: int, template_id: str
    ) -> dict[str, Any]:
        template = self.get_template(template_id)
        if template is None or int(template.get("project_id") or 0) != int(project_id):
            raise ValueError("项目模板不存在")
        template_key = str(template["template_key"])
        with self._connect() as conn:
            default_rows = conn.execute(
                "SELECT project_id, default_template_revision AS template_revision "
                "FROM creative_project_prompt_settings "
                "WHERE project_id = ? AND default_template_id = ?",
                (int(project_id), str(template_id)),
            ).fetchall()
            binding_rows = conn.execute(
                "SELECT project_id, stage, episode_number, template_revision "
                "FROM creative_project_prompt_bindings "
                "WHERE project_id = ? AND template_id = ? "
                "ORDER BY stage, episode_number",
                (int(project_id), str(template_id)),
            ).fetchall()
            ab_rows = conn.execute(
                "SELECT arms.experiment_id, arms.arm, arms.template_revision "
                "FROM creative_prompt_ab_arms arms "
                "JOIN creative_prompt_ab_experiments experiments "
                "ON experiments.id = arms.experiment_id "
                "WHERE experiments.project_id = ? AND arms.template_key = ? "
                "ORDER BY experiments.created_at DESC",
                (int(project_id), template_key),
            ).fetchall()
            iteration_rows = conn.execute(
                "SELECT id, base_revision, published_revision, status "
                "FROM creative_prompt_iteration_drafts "
                "WHERE project_id = ? AND template_id = ? ORDER BY created_at DESC",
                (int(project_id), str(template_id)),
            ).fetchall()

            reference_sources: list[dict[str, Any]] = []
            for table_name, json_column in (
                ("creative_blueprints", "generation_meta_json"),
                ("creative_characters", "generation_meta_json"),
                ("creative_relationships", "generation_meta_json"),
                ("creative_episodes", "generation_meta_json"),
                ("creative_generation_contexts", "context_snapshot_json"),
                ("script_reviews", "source_payload_json"),
                ("script_optimizations", "prompt_snapshot_json"),
            ):
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table_name,),
                ).fetchone()
                if table is None:
                    continue
                rows = conn.execute(
                    f"SELECT rowid, {json_column} FROM {table_name} WHERE project_id = ?",
                    (int(project_id),),
                ).fetchall()
                matched = 0
                for row in rows:
                    try:
                        payload = json.loads(row[json_column] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if self._json_references_template(
                        payload,
                        template_id=str(template_id),
                        template_key=template_key,
                    ):
                        matched += 1
                if matched:
                    reference_sources.append(
                        {"source": table_name, "reference_count": matched}
                    )

        defaults = [dict(row) for row in default_rows]
        bindings = [dict(row) for row in binding_rows]
        active_binding_count = len(defaults) + len(bindings)
        return {
            "template_id": str(template_id),
            "template_key": template_key,
            "active_binding_count": active_binding_count,
            "default_bindings": defaults,
            "stage_bindings": bindings,
            "historical_references": {
                "ab_arms": [dict(row) for row in ab_rows],
                "iteration_drafts": [dict(row) for row in iteration_rows],
                "resource_sources": reference_sources,
                "resource_reference_count": sum(
                    int(item["reference_count"]) for item in reference_sources
                ),
            },
            "can_archive": active_binding_count == 0,
            "archive_blockers": (
                []
                if active_binding_count == 0
                else ["模板仍被项目默认设置、阶段或单集绑定使用，请先改绑"]
            ),
        }

    def list_governance_events(
        self, project_id: int, template_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_prompt_governance_events "
                "WHERE project_id = ? AND template_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (int(project_id), str(template_id), max(1, min(100, int(limit)))),
            ).fetchall()
        return [self._governance_event_row(row) for row in rows]

    def list_project_template_governance(
        self, project_id: int
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_prompt_templates "
                "WHERE owner_scope = 'project' AND project_id = ? "
                "ORDER BY updated_at DESC, title",
                (int(project_id),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            template = self._template_row(row)
            latest = self.get_revision(str(template["id"]))
            template.update(
                {
                    "revision": int(latest["revision"]),
                    "content_hash": str(latest["content_hash"]),
                    "impact": self.template_impact(project_id, str(template["id"])),
                    "governance_events": self.list_governance_events(
                        project_id, str(template["id"]), limit=20
                    ),
                }
            )
            result.append(template)
        return result

    def govern_project_template(
        self,
        *,
        project_id: int,
        template_id: str,
        action: str,
        note: str,
        expected_governance_revision: int,
        operation_id: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        transitions = {
            ("approved", "request_review"): "pending_review",
            ("pending_review", "approve"): "approved",
            ("approved", "deprecate"): "deprecated",
            ("deprecated", "restore"): "approved",
            ("archived", "restore"): "pending_review",
            ("pending_review", "archive"): "archived",
            ("deprecated", "archive"): "archived",
        }
        normalized_note = str(note or "").strip()
        normalized_operation_id = str(operation_id or "").strip()
        if not normalized_note:
            raise ValueError("模板治理必须填写原因")
        if not normalized_operation_id:
            raise ValueError("模板治理缺少操作标识")
        template = self.get_template(template_id)
        if (
            template is None
            or template.get("owner_scope") != "project"
            or int(template.get("project_id") or 0) != int(project_id)
        ):
            raise ValueError("项目模板不存在")
        latest = self.get_revision(template_id)
        request_payload = {
            "project_id": int(project_id),
            "template_id": str(template_id),
            "template_revision": int(latest["revision"]),
            "action": str(action),
            "note": normalized_note,
            "expected_governance_revision": int(expected_governance_revision),
        }
        request_hash = content_hash(request_payload)
        impact = self.template_impact(project_id, template_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM creative_prompt_governance_events WHERE operation_id = ?",
                (normalized_operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ValueError("模板治理操作标识已用于其他请求")
                return {
                    "template": self.get_template(template_id) or {},
                    "impact": self.template_impact(project_id, template_id),
                    "event": self._governance_event_row(existing),
                }
            current = conn.execute(
                "SELECT * FROM creative_prompt_templates WHERE id = ?",
                (str(template_id),),
            ).fetchone()
            if current is None:
                raise ValueError("项目模板不存在")
            if int(current["governance_revision"] or 0) != int(
                expected_governance_revision
            ):
                raise ValueError("模板治理状态已被其他成员更新，请刷新后重试")
            from_status = str(current["governance_status"] or "approved")
            to_status = transitions.get((from_status, str(action)))
            if to_status is None:
                raise ValueError("当前模板状态不允许执行该治理操作")
            if to_status == "archived":
                active_bindings = conn.execute(
                    "SELECT ("
                    "SELECT COUNT(*) FROM creative_project_prompt_settings "
                    "WHERE project_id = ? AND default_template_id = ?"
                    ") + ("
                    "SELECT COUNT(*) FROM creative_project_prompt_bindings "
                    "WHERE project_id = ? AND template_id = ?"
                    ")",
                    (
                        int(project_id),
                        str(template_id),
                        int(project_id),
                        str(template_id),
                    ),
                ).fetchone()[0]
                if int(active_bindings or 0) > 0:
                    raise ValueError(
                        "模板仍被项目默认设置、阶段或单集绑定使用，请先改绑"
                    )
            next_revision = int(current["governance_revision"] or 0) + 1
            now = time.time()
            approved_revision = int(current["approved_revision"] or 0)
            if to_status == "approved":
                approved_revision = int(latest["revision"])
            lifecycle_status = "archived" if to_status == "archived" else "active"
            cursor = conn.execute(
                """
                UPDATE creative_prompt_templates
                SET governance_status = ?, governance_revision = ?,
                    approved_revision = ?, governance_note = ?,
                    governance_updated_by = ?, governance_updated_at = ?,
                    status = ?, updated_at = ?
                WHERE id = ? AND governance_revision = ?
                """,
                (
                    to_status,
                    next_revision,
                    approved_revision,
                    normalized_note,
                    str(actor_user_id),
                    now,
                    lifecycle_status,
                    now,
                    str(template_id),
                    int(expected_governance_revision),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("模板治理状态已被其他成员更新，请刷新后重试")
            event_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO creative_prompt_governance_events (
                    id, operation_id, request_hash, project_id, template_id,
                    template_revision, action, from_status, to_status, note,
                    impact_json, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    normalized_operation_id,
                    request_hash,
                    int(project_id),
                    str(template_id),
                    int(latest["revision"]),
                    str(action),
                    from_status,
                    to_status,
                    normalized_note,
                    json.dumps(impact, ensure_ascii=False),
                    str(actor_user_id),
                    now,
                ),
            )
        return {
            "template": self.get_template(template_id) or {},
            "impact": self.template_impact(project_id, template_id),
            "event": self.list_governance_events(project_id, template_id, limit=1)[0],
        }

    def create_ab_experiment(
        self,
        *,
        operation_id: str,
        project_id: int,
        stage: str,
        episode_number: int,
        model_record_id: str,
        source_fingerprint: str,
        source_snapshot: dict[str, Any],
        preflight_hash: str,
        estimate: dict[str, Any],
        budget_amount: float,
        price_currency: str,
        prompt_snapshots: dict[str, dict[str, Any]],
        actor_user_id: str,
    ) -> dict[str, Any]:
        if set(prompt_snapshots) != {"A", "B"}:
            raise ValueError("A/B 实验必须包含 A、B 两个模板快照")
        normalized_operation_id = str(operation_id or "").strip()
        if not normalized_operation_id:
            raise ValueError("A/B 实验缺少操作标识")
        now = time.time()
        experiment_id = uuid.uuid4().hex
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, preflight_hash FROM creative_prompt_ab_experiments "
                "WHERE operation_id = ?",
                (normalized_operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["preflight_hash"]) != str(preflight_hash):
                    raise ValueError("A/B 操作标识已用于其他预检结果")
                return self.get_ab_experiment(str(existing["id"])) or {}
            conn.execute(
                """
                INSERT INTO creative_prompt_ab_experiments (
                    id, operation_id, project_id, stage, episode_number,
                    model_record_id, source_fingerprint, preflight_hash,
                    source_snapshot_json, estimate_json, budget_amount,
                    price_currency, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    normalized_operation_id,
                    int(project_id),
                    str(stage),
                    int(episode_number),
                    str(model_record_id),
                    str(source_fingerprint),
                    str(preflight_hash),
                    json.dumps(source_snapshot, ensure_ascii=False),
                    json.dumps(estimate, ensure_ascii=False),
                    float(budget_amount),
                    str(price_currency),
                    str(actor_user_id),
                    now,
                    now,
                ),
            )
            for arm in ("A", "B"):
                snapshot = prompt_snapshots[arm]
                conn.execute(
                    """
                    INSERT INTO creative_prompt_ab_arms (
                        experiment_id, arm, template_key, template_revision,
                        prompt_snapshot_json, job_id
                    ) VALUES (?, ?, ?, ?, ?, '')
                    """,
                    (
                        experiment_id,
                        arm,
                        str(snapshot.get("template_key") or ""),
                        int(snapshot.get("revision") or 0),
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
        return self.get_ab_experiment(experiment_id) or {}

    def set_ab_arm_job(
        self, experiment_id: str, arm: str, job_id: str
    ) -> dict[str, Any]:
        if arm not in {"A", "B"}:
            raise ValueError("A/B 实验分组无效")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT job_id FROM creative_prompt_ab_arms "
                "WHERE experiment_id = ? AND arm = ?",
                (str(experiment_id), arm),
            ).fetchone()
            if current is None:
                raise ValueError("A/B 实验分组不存在")
            existing_job_id = str(current["job_id"] or "")
            if existing_job_id and existing_job_id != str(job_id):
                raise ValueError("A/B 实验分组已绑定其他任务")
            conn.execute(
                "UPDATE creative_prompt_ab_arms SET job_id = ? "
                "WHERE experiment_id = ? AND arm = ?",
                (str(job_id), str(experiment_id), arm),
            )
            conn.execute(
                "UPDATE creative_prompt_ab_experiments SET updated_at = ? WHERE id = ?",
                (time.time(), str(experiment_id)),
            )
        return self.get_ab_experiment(experiment_id) or {}

    def get_ab_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_prompt_ab_experiments WHERE id = ?",
                (str(experiment_id),),
            ).fetchone()
            if row is None:
                return None
            arm_rows = conn.execute(
                "SELECT * FROM creative_prompt_ab_arms "
                "WHERE experiment_id = ? ORDER BY arm",
                (str(experiment_id),),
            ).fetchall()
        result = dict(row)
        result["estimate"] = json.loads(result.pop("estimate_json") or "{}")
        result["source_snapshot"] = json.loads(
            result.pop("source_snapshot_json", "{}") or "{}"
        )
        result["evaluation"] = json.loads(
            result.pop("evaluation_json", "{}") or "{}"
        )
        result["arms"] = [
            {
                **dict(arm),
                "prompt_snapshot": json.loads(arm["prompt_snapshot_json"] or "{}"),
            }
            for arm in arm_rows
        ]
        for arm in result["arms"]:
            arm.pop("prompt_snapshot_json", None)
        return result

    def save_ab_evaluation(
        self,
        experiment_id: str,
        *,
        evaluation: dict[str, Any],
        winner_arm: str,
        decision_note: str,
        expected_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        if winner_arm not in {"A", "B", "tie"}:
            raise ValueError("A/B 实验结论必须是 A、B 或平局")
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE creative_prompt_ab_experiments
                SET evaluation_json = ?, winner_arm = ?, decision_note = ?,
                    evaluation_revision = evaluation_revision + 1,
                    evaluated_by = ?, evaluated_at = ?, updated_at = ?
                WHERE id = ? AND evaluation_revision = ?
                """,
                (
                    json.dumps(evaluation, ensure_ascii=False),
                    winner_arm,
                    str(decision_note or "").strip(),
                    str(actor_user_id),
                    now,
                    now,
                    str(experiment_id),
                    int(expected_revision),
                ),
            )
            if cursor.rowcount != 1:
                current = conn.execute(
                    "SELECT evaluation_revision FROM creative_prompt_ab_experiments "
                    "WHERE id = ?",
                    (str(experiment_id),),
                ).fetchone()
                if current is None:
                    raise ValueError("A/B 实验不存在")
                raise ValueError("A/B 评价已被其他成员更新，请刷新后重试")
        return self.get_ab_experiment(experiment_id) or {}

    def list_ab_experiments(
        self, project_id: int, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM creative_prompt_ab_experiments "
                "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (int(project_id), max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            experiment
            for row in rows
            if (experiment := self.get_ab_experiment(str(row["id"]))) is not None
        ]

    def list_evaluated_ab_experiments(
        self,
        project_id: int,
        *,
        stage: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id = ?", "evaluation_revision > 0"]
        params: list[Any] = [int(project_id)]
        if stage is not None:
            clauses.append("stage = ?")
            params.append(str(stage))
        params.append(max(1, min(500, int(limit))))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM creative_prompt_ab_experiments WHERE "
                + " AND ".join(clauses)
                + " ORDER BY evaluated_at DESC, created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [
            experiment
            for row in rows
            if (experiment := self.get_ab_experiment(str(row["id"]))) is not None
        ]

    @staticmethod
    def _iteration_draft_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for stored_key, public_key, fallback in (
            ("report_snapshot_json", "report_snapshot", {}),
            ("proposed_content_json", "proposed_content", {}),
            ("changes_json", "changes", []),
            ("warnings_json", "warnings", []),
        ):
            result[public_key] = json.loads(result.pop(stored_key) or json.dumps(fallback))
        return result

    def create_iteration_draft(
        self,
        *,
        operation_id: str,
        request_fingerprint: str,
        project_id: int,
        template_id: str,
        template_key: str,
        base_revision: int,
        base_content_hash: str,
        proposed_content: dict[str, Any],
        stage: str,
        dimension_key: str,
        report_snapshot: dict[str, Any],
        changes: list[dict[str, Any]],
        warnings: list[str],
        actor_user_id: str,
    ) -> dict[str, Any]:
        normalized_operation_id = str(operation_id or "").strip()
        if not normalized_operation_id:
            raise ValueError("迭代草稿缺少操作标识")
        normalized_content = normalize_template_content(proposed_content)
        draft_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM creative_prompt_iteration_drafts WHERE operation_id = ?",
                (normalized_operation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_fingerprint"]) != str(request_fingerprint):
                    raise ValueError("迭代草稿操作标识已用于其他请求")
                return self._iteration_draft_row(existing)
            conn.execute(
                """
                INSERT INTO creative_prompt_iteration_drafts (
                    id, operation_id, request_fingerprint, project_id,
                    template_id, template_key, base_revision, base_content_hash,
                    proposed_content_hash, stage, dimension_key,
                    report_snapshot_json, proposed_content_json, changes_json,
                    warnings_json, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    normalized_operation_id,
                    str(request_fingerprint),
                    int(project_id),
                    str(template_id),
                    str(template_key),
                    int(base_revision),
                    str(base_content_hash),
                    content_hash(normalized_content),
                    str(stage),
                    str(dimension_key),
                    json.dumps(report_snapshot, ensure_ascii=False),
                    json.dumps(normalized_content, ensure_ascii=False),
                    json.dumps(changes, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                    str(actor_user_id),
                    now,
                ),
            )
        return self.get_iteration_draft(draft_id) or {}

    def get_iteration_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_prompt_iteration_drafts WHERE id = ?",
                (str(draft_id),),
            ).fetchone()
        return self._iteration_draft_row(row) if row is not None else None

    def list_iteration_drafts(
        self,
        project_id: int,
        *,
        template_keys: set[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id = ?"]
        params: list[Any] = [int(project_id)]
        normalized_keys = sorted(str(key) for key in (template_keys or set()) if key)
        if template_keys is not None and not normalized_keys:
            return []
        if normalized_keys:
            placeholders = ",".join("?" for _ in normalized_keys)
            clauses.append(f"template_key IN ({placeholders})")
            params.extend(normalized_keys)
        params.append(max(1, min(500, int(limit))))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_prompt_iteration_drafts WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._iteration_draft_row(row) for row in rows]

    def mark_iteration_draft_published(
        self,
        draft_id: str,
        *,
        published_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status, published_revision FROM creative_prompt_iteration_drafts "
                "WHERE id = ?",
                (str(draft_id),),
            ).fetchone()
            if current is None:
                raise ValueError("模板迭代草稿不存在")
            if str(current["status"]) == "published":
                return self.get_iteration_draft(draft_id) or {}
            if str(current["status"]) != "draft":
                raise ValueError("模板迭代草稿已失效")
            conn.execute(
                """
                UPDATE creative_prompt_iteration_drafts
                SET status = 'published', published_revision = ?,
                    published_by = ?, published_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (
                    int(published_revision),
                    str(actor_user_id),
                    time.time(),
                    str(draft_id),
                ),
            )
        return self.get_iteration_draft(draft_id) or {}

    def create_template(
        self,
        *,
        title: str,
        description: str,
        content: dict[str, Any],
        owner_scope: str,
        owner_user_id: str,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        if owner_scope not in {"user", "project"}:
            raise ValueError("只能创建个人或项目模板")
        if owner_scope == "user" and not owner_user_id:
            raise ValueError("个人模板必须有所有者")
        if owner_scope == "project" and project_id is None:
            raise ValueError("项目模板必须绑定项目")
        normalized = normalize_template_content(content)
        template_id = uuid.uuid4().hex
        template_key = f"{owner_scope}-{template_id}"
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO creative_prompt_templates (
                    id, template_key, title, description, owner_scope,
                    owner_user_id, project_id, visibility, status,
                    governance_status, approved_revision,
                    catalog_visible, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'approved', 1, TRUE, ?, ?)
                """,
                (
                    template_id,
                    template_key,
                    str(title).strip(),
                    str(description).strip(),
                    owner_scope,
                    owner_user_id,
                    int(project_id) if project_id is not None else None,
                    "project" if owner_scope == "project" else "private",
                    now,
                    now,
                ),
            )
            cursor = conn.execute(
                """
                INSERT INTO creative_prompt_template_revisions (
                    template_id, revision, content_json, content_hash,
                    created_by, created_at
                ) VALUES (?, 1, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    json.dumps(normalized, ensure_ascii=False),
                    content_hash(normalized),
                    owner_user_id,
                    now,
                ),
            )
        template = self.get_template(template_id) or {}
        revision_row = self.get_revision(template_id)
        template["revision"] = revision_row["revision"]
        template["content"] = revision_row["content"]
        template["content_hash"] = revision_row["content_hash"]
        template["revision_id"] = int(cursor.lastrowid)
        return template

    def save_revision(
        self,
        template_id: str,
        content: dict[str, Any],
        *,
        expected_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        template = self.get_template(template_id)
        if template is None:
            raise ValueError("模板不存在")
        if template["owner_scope"] == "system":
            raise ValueError("系统模板只读")
        normalized = normalize_template_content(content)
        governance_impact = (
            self.template_impact(int(template["project_id"]), str(template_id))
            if template["owner_scope"] == "project"
            else {}
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT * FROM creative_prompt_template_revisions "
                "WHERE template_id = ? ORDER BY revision DESC LIMIT 1",
                (str(template_id),),
            ).fetchone()
            if parent is None or int(parent["revision"]) != int(expected_revision):
                raise ValueError("模板版本已发生变化")
            revision = int(expected_revision) + 1
            cursor = conn.execute(
                """
                INSERT INTO creative_prompt_template_revisions (
                    template_id, revision, parent_revision_id, content_json,
                    content_hash, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(template_id),
                    revision,
                    int(parent["id"]),
                    json.dumps(normalized, ensure_ascii=False),
                    content_hash(normalized),
                    actor_user_id,
                    time.time(),
                ),
            )
            conn.execute(
                "UPDATE creative_prompt_templates SET updated_at = ?, "
                "governance_status = CASE WHEN owner_scope = 'project' "
                "THEN 'pending_review' ELSE governance_status END, "
                "governance_revision = CASE WHEN owner_scope = 'project' "
                "THEN governance_revision + 1 ELSE governance_revision END, "
                "governance_note = CASE WHEN owner_scope = 'project' "
                "THEN '新版本待审核' ELSE governance_note END, "
                "governance_updated_by = CASE WHEN owner_scope = 'project' "
                "THEN ? ELSE governance_updated_by END, "
                "governance_updated_at = CASE WHEN owner_scope = 'project' "
                "THEN ? ELSE governance_updated_at END, "
                "approved_revision = CASE WHEN owner_scope = 'project' "
                "THEN approved_revision ELSE ? END "
                "WHERE id = ?",
                (
                    time.time(),
                    str(actor_user_id),
                    time.time(),
                    int(revision),
                    str(template_id),
                ),
            )
            if template["owner_scope"] == "project":
                auto_operation_id = f"prompt-revision:{template_id}:{revision}"
                event_payload = {
                    "project_id": int(template["project_id"]),
                    "template_id": str(template_id),
                    "template_revision": int(revision),
                    "action": "submit_revision",
                }
                conn.execute(
                    """
                    INSERT INTO creative_prompt_governance_events (
                        id, operation_id, request_hash, project_id, template_id,
                        template_revision, action, from_status, to_status, note,
                        impact_json, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'submit_revision', ?,
                              'pending_review', '新版本待审核', ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        auto_operation_id,
                        content_hash(event_payload),
                        int(template["project_id"]),
                        str(template_id),
                        int(revision),
                        str(template.get("governance_status") or "approved"),
                        json.dumps(governance_impact, ensure_ascii=False),
                        str(actor_user_id),
                        time.time(),
                    ),
                )
        saved = self.get_revision(template_id, revision)
        saved["revision_id"] = int(cursor.lastrowid)
        return saved

    def personal_template_deletion_impact(
        self,
        template_id: str,
        *,
        actor_user_id: str,
        project_id: int | None = None,
    ) -> dict[str, Any]:
        template = self.get_template(template_id)
        if template is None:
            raise ValueError("模板不存在")
        if template.get("owner_scope") != "user":
            raise ValueError("只能删除我的模板")
        if str(template.get("owner_user_id") or "") != str(actor_user_id):
            raise PermissionError("只能删除自己的个人模板")
        with self._connect() as conn:
            default_rows = conn.execute(
                "SELECT settings.project_id, projects.name AS project_name "
                "FROM creative_project_prompt_settings settings "
                "LEFT JOIN creative_projects projects ON projects.id = settings.project_id "
                "WHERE settings.default_template_id = ? ORDER BY settings.project_id",
                (str(template_id),),
            ).fetchall()
            binding_rows = conn.execute(
                "SELECT bindings.project_id, projects.name AS project_name, "
                "bindings.stage, bindings.episode_number "
                "FROM creative_project_prompt_bindings bindings "
                "LEFT JOIN creative_projects projects ON projects.id = bindings.project_id "
                "WHERE bindings.template_id = ? "
                "ORDER BY bindings.project_id, bindings.stage, bindings.episode_number",
                (str(template_id),),
            ).fetchall()
            submission_rows = conn.execute(
                "SELECT id, status FROM creative_prompt_market_submissions "
                "WHERE template_id = ? ORDER BY created_at DESC",
                (str(template_id),),
            ).fetchall()
        defaults = [dict(row) for row in default_rows]
        bindings = [dict(row) for row in binding_rows]
        submissions = [dict(row) for row in submission_rows]
        active_binding_count = len(defaults) + len(bindings)
        current_project_id = int(project_id or 0)
        current_defaults = [
            row for row in defaults if int(row["project_id"]) == current_project_id
        ]
        current_bindings = [
            row for row in bindings if int(row["project_id"]) == current_project_id
        ]
        all_stages = {
            "brief", "characters", "outline", "beats", "script", "review",
            "optimization",
        }
        required_stages = (
            all_stages
            if current_defaults
            else {str(row["stage"]) for row in current_bindings}
        )
        replacement_candidates: list[dict[str, Any]] = []
        if current_project_id and (current_defaults or current_bindings):
            for candidate in self.list_catalog(
                project_id=current_project_id, actor_user_id=actor_user_id
            ):
                if str(candidate["id"]) == str(template_id):
                    continue
                supported = set(candidate.get("supported_stages") or [])
                if not required_stages.issubset(supported):
                    continue
                replacement_candidates.append(
                    {
                        "id": str(candidate["id"]),
                        "template_key": str(candidate["template_key"]),
                        "title": str(candidate["title"]),
                        "owner_scope": str(candidate["owner_scope"]),
                        "revision": int(candidate["revision"]),
                        "supported_stages": sorted(supported),
                    }
                )
        other_projects: dict[int, dict[str, Any]] = {}
        for row in defaults:
            row_project_id = int(row["project_id"])
            if row_project_id == current_project_id:
                continue
            item = other_projects.setdefault(
                row_project_id,
                {
                    "project_id": row_project_id,
                    "project_name": str(row.get("project_name") or f"项目 {row_project_id}"),
                    "default_binding_count": 0,
                    "stage_binding_count": 0,
                    "stages": [],
                },
            )
            item["default_binding_count"] += 1
        for row in bindings:
            row_project_id = int(row["project_id"])
            if row_project_id == current_project_id:
                continue
            item = other_projects.setdefault(
                row_project_id,
                {
                    "project_id": row_project_id,
                    "project_name": str(row.get("project_name") or f"项目 {row_project_id}"),
                    "default_binding_count": 0,
                    "stage_binding_count": 0,
                    "stages": [],
                },
            )
            item["stage_binding_count"] += 1
            stage = str(row["stage"])
            if stage not in item["stages"]:
                item["stages"].append(stage)
        return {
            "template_id": str(template_id),
            "title": str(template.get("title") or ""),
            "status": str(template.get("status") or ""),
            "active_binding_count": active_binding_count,
            "default_bindings": defaults,
            "stage_bindings": bindings,
            "current_project_binding_count": len(current_defaults) + len(current_bindings),
            "current_project_default_bindings": current_defaults,
            "current_project_stage_bindings": current_bindings,
            "other_project_references": list(other_projects.values()),
            "required_replacement_stages": sorted(required_stages),
            "replacement_candidates": replacement_candidates,
            "market_submission_count": len(submissions),
            "market_submissions": submissions,
            "historical_data_preserved": True,
            "can_delete": active_binding_count == 0,
            "delete_blockers": (
                []
                if active_binding_count == 0
                else ["模板仍被项目默认设置、阶段或单集绑定使用，请先改绑"]
            ),
        }

    def delete_personal_template(
        self,
        template_id: str,
        *,
        actor_user_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        impact = self.personal_template_deletion_impact(
            template_id, actor_user_id=actor_user_id
        )
        if impact["status"] == "archived":
            return {
                "deleted": True,
                "template": self.get_template(template_id),
                "impact": impact,
            }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT owner_scope, owner_user_id, status "
                "FROM creative_prompt_templates WHERE id = ?",
                (str(template_id),),
            ).fetchone()
            if current is None:
                raise ValueError("模板不存在")
            if current["owner_scope"] != "user":
                raise ValueError("只能删除我的模板")
            if str(current["owner_user_id"] or "") != str(actor_user_id):
                raise PermissionError("只能删除自己的个人模板")
            if current["status"] == "archived":
                return {
                    "deleted": True,
                    "template": self.get_template(template_id),
                    "impact": impact,
                }
            active_binding_count = int(
                conn.execute(
                    "SELECT ("
                    "(SELECT COUNT(*) FROM creative_project_prompt_settings "
                    "WHERE default_template_id = ?) + "
                    "(SELECT COUNT(*) FROM creative_project_prompt_bindings "
                    "WHERE template_id = ?)) AS total",
                    (str(template_id), str(template_id)),
                ).fetchone()["total"]
                or 0
            )
            if active_binding_count:
                raise ValueError(
                    "模板仍被项目默认设置、阶段或单集绑定使用，请先改绑"
                )
            latest = conn.execute(
                "SELECT revision FROM creative_prompt_template_revisions "
                "WHERE template_id = ? ORDER BY revision DESC LIMIT 1",
                (str(template_id),),
            ).fetchone()
            if latest is None or int(latest["revision"]) != int(expected_revision):
                raise ValueError("模板版本已发生变化，请刷新后重试")
            updated = conn.execute(
                "UPDATE creative_prompt_templates SET status = 'archived', "
                "catalog_visible = FALSE, updated_at = ? "
                "WHERE id = ? AND owner_scope = 'user' AND owner_user_id = ? "
                "AND status = 'active'",
                (time.time(), str(template_id), str(actor_user_id)),
            )
            if int(updated.rowcount or 0) != 1:
                raise ValueError("模板状态已发生变化，请刷新后重试")
        deleted_template = self.get_template(template_id) or {}
        impact["status"] = "archived"
        return {"deleted": True, "template": deleted_template, "impact": impact}

    def rebind_and_delete_personal_template(
        self,
        *,
        project_id: int,
        template_id: str,
        actor_user_id: str,
        expected_revision: int,
        replacement_template_id: str,
        replacement_template_revision: int,
    ) -> dict[str, Any]:
        if str(template_id) == str(replacement_template_id):
            raise ValueError("替代模板不能是待删除模板")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                "SELECT * FROM creative_prompt_templates WHERE id = ?",
                (str(template_id),),
            ).fetchone()
            if source is None:
                raise ValueError("模板不存在")
            if source["owner_scope"] != "user":
                raise ValueError("只能删除我的模板")
            if str(source["owner_user_id"] or "") != str(actor_user_id):
                raise PermissionError("只能删除自己的个人模板")
            if source["status"] != "active":
                raise ValueError("模板状态已发生变化，请刷新后重试")
            source_revision = conn.execute(
                "SELECT revision FROM creative_prompt_template_revisions "
                "WHERE template_id = ? ORDER BY revision DESC LIMIT 1",
                (str(template_id),),
            ).fetchone()
            if source_revision is None or int(source_revision["revision"]) != int(
                expected_revision
            ):
                raise ValueError("模板版本已发生变化，请刷新后重试")

            replacement_row = conn.execute(
                "SELECT * FROM creative_prompt_templates WHERE id = ?",
                (str(replacement_template_id),),
            ).fetchone()
            if replacement_row is None:
                raise ValueError("替代模板不存在")
            replacement = self._template_row(replacement_row)
            if replacement["owner_scope"] == "user" and str(
                replacement.get("owner_user_id") or ""
            ) != str(actor_user_id):
                raise PermissionError("无权使用该个人模板")
            if replacement["owner_scope"] == "project" and int(
                replacement.get("project_id") or 0
            ) != int(project_id):
                raise PermissionError("无权使用其他项目的模板")
            self._assert_template_selectable(replacement)
            replacement_revision_row = conn.execute(
                "SELECT * FROM creative_prompt_template_revisions "
                "WHERE template_id = ? ORDER BY revision DESC LIMIT 1",
                (str(replacement_template_id),),
            ).fetchone()
            if replacement_revision_row is None:
                raise ValueError("替代模板版本不存在")
            replacement_revision = self._revision_row(replacement_revision_row)
            if int(replacement_revision["revision"]) != int(
                replacement_template_revision
            ):
                raise ValueError("替代模板版本已发生变化，请刷新后重试")

            default_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM creative_project_prompt_settings "
                    "WHERE project_id = ? AND default_template_id = ?",
                    (int(project_id), str(template_id)),
                ).fetchone()["total"]
                or 0
            )
            binding_rows = conn.execute(
                "SELECT stage FROM creative_project_prompt_bindings "
                "WHERE project_id = ? AND template_id = ?",
                (int(project_id), str(template_id)),
            ).fetchall()
            binding_count = len(binding_rows)
            if default_count + binding_count == 0:
                raise ValueError("当前项目没有需要改绑的引用")
            required_stages = (
                {
                    "brief", "characters", "outline", "beats", "script",
                    "review", "optimization",
                }
                if default_count
                else {str(row["stage"]) for row in binding_rows}
            )
            unsupported = [
                stage
                for stage in sorted(required_stages)
                if not self._supports_stage(replacement_revision, stage)
            ]
            if unsupported:
                raise ValueError(
                    f"替代模板不支持所需阶段：{'、'.join(unsupported)}"
                )

            conn.execute(
                "UPDATE creative_project_prompt_settings "
                "SET default_template_id = ?, default_template_revision = ?, "
                "updated_by = ?, updated_at = ? "
                "WHERE project_id = ? AND default_template_id = ?",
                (
                    str(replacement_template_id),
                    int(replacement_template_revision),
                    str(actor_user_id),
                    time.time(),
                    int(project_id),
                    str(template_id),
                ),
            )
            conn.execute(
                "UPDATE creative_project_prompt_bindings "
                "SET template_id = ?, template_revision = ?, updated_by = ?, updated_at = ? "
                "WHERE project_id = ? AND template_id = ?",
                (
                    str(replacement_template_id),
                    int(replacement_template_revision),
                    str(actor_user_id),
                    time.time(),
                    int(project_id),
                    str(template_id),
                ),
            )
            remaining = int(
                conn.execute(
                    "SELECT ("
                    "(SELECT COUNT(*) FROM creative_project_prompt_settings "
                    "WHERE default_template_id = ?) + "
                    "(SELECT COUNT(*) FROM creative_project_prompt_bindings "
                    "WHERE template_id = ?)) AS total",
                    (str(template_id), str(template_id)),
                ).fetchone()["total"]
                or 0
            )
            deleted = remaining == 0
            if deleted:
                conn.execute(
                    "UPDATE creative_prompt_templates SET status = 'archived', "
                    "catalog_visible = FALSE, updated_at = ? WHERE id = ?",
                    (time.time(), str(template_id)),
                )
        return {
            "rebound": True,
            "deleted": deleted,
            "replaced_default_bindings": default_count,
            "replaced_stage_bindings": binding_count,
            "remaining_binding_count": remaining,
            "replacement_template_id": str(replacement_template_id),
            "replacement_template_revision": int(replacement_template_revision),
            "template": self.get_template(template_id),
        }

    def project_settings(self, project_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_project_prompt_settings WHERE project_id = ?",
                (int(project_id),),
            ).fetchone()
        if row is None:
            return {"project_id": int(project_id), "enabled": False}
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def enable_project(
        self,
        project_id: int,
        *,
        actor_user_id: str,
        template_key: str = "generic-vertical-short",
    ) -> dict[str, Any]:
        template = self.get_template_by_key(template_key)
        if template is None or template["status"] != "active":
            raise ValueError("默认模板不存在")
        if (
            template["owner_scope"] == "user"
            and template["owner_user_id"] != actor_user_id
        ):
            raise PermissionError("无权使用该个人模板")
        if (
            template["owner_scope"] == "project"
            and int(template["project_id"] or 0) != int(project_id)
        ):
            raise PermissionError("无权使用其他项目模板")
        self._assert_template_selectable(template)
        revision = self.get_revision(template["id"])
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT status, governance_status, approved_revision "
                "FROM creative_prompt_templates WHERE id = ?",
                (str(template["id"]),),
            ).fetchone()
            if current is None:
                raise ValueError("默认模板不存在")
            self._assert_template_selectable(dict(current))
            project = conn.execute(
                "SELECT id FROM creative_projects WHERE id = ? AND deleted = FALSE",
                (int(project_id),),
            ).fetchone()
            if project is None:
                raise ValueError("创作项目不存在")
            conn.execute(
                """
                INSERT INTO creative_project_prompt_settings (
                    project_id, enabled, default_template_id,
                    default_template_revision, updated_by, updated_at
                ) VALUES (?, TRUE, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    enabled = TRUE,
                    default_template_id = excluded.default_template_id,
                    default_template_revision = excluded.default_template_revision,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    int(project_id),
                    template["id"],
                    int(revision["revision"]),
                    actor_user_id,
                    time.time(),
                ),
            )
        return self.project_settings(project_id)

    def set_binding(
        self,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        template_id: str,
        template_revision: int,
        override: dict[str, Any] | None,
        actor_user_id: str,
    ) -> dict[str, Any]:
        if stage not in {
            "brief", "characters", "outline", "beats", "script", "review", "optimization",
        }:
            raise ValueError(
                "模板阶段只支持 brief、characters、outline、beats、script、review 或 optimization"
            )
        non_episode_stages = {
            "brief": "Brief 模板不支持单集覆盖",
            "characters": "人物模板不支持单集覆盖",
            "outline": "大纲模板不支持单集覆盖",
            "review": "审核模板不支持单集覆盖",
            "optimization": "优化模板不支持单集覆盖",
        }
        if stage in non_episode_stages and int(episode_number) != 0:
            raise ValueError(non_episode_stages[stage])
        template = self.get_template(template_id)
        if template is None:
            raise ValueError("模板不存在")
        if template["owner_scope"] == "user" and template["owner_user_id"] != actor_user_id:
            raise PermissionError("只能绑定自己的个人模板")
        if template["owner_scope"] == "project" and int(template["project_id"] or 0) != int(project_id):
            raise PermissionError("项目模板不属于当前项目")
        self._assert_template_selectable(template, revision=template_revision)
        revision = self.get_revision(template_id, template_revision)
        if not self._supports_stage(revision, stage):
            raise ValueError(f"所选模板版本不支持 {stage} 阶段")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT status, governance_status, approved_revision "
                "FROM creative_prompt_templates WHERE id = ?",
                (str(template_id),),
            ).fetchone()
            if current is None:
                raise ValueError("模板不存在")
            self._assert_template_selectable(
                dict(current), revision=int(template_revision)
            )
            conn.execute(
                """
                INSERT INTO creative_project_prompt_bindings (
                    project_id, stage, episode_number, template_id,
                    template_revision, override_json, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, stage, episode_number) DO UPDATE SET
                    template_id = excluded.template_id,
                    template_revision = excluded.template_revision,
                    override_json = excluded.override_json,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    int(project_id),
                    stage,
                    int(episode_number),
                    str(template_id),
                    int(template_revision),
                    json.dumps(override or {}, ensure_ascii=False),
                    actor_user_id,
                    time.time(),
                ),
            )
        return self.binding(project_id, stage, episode_number) or {}

    def binding(
        self, project_id: int, stage: str, episode_number: int = 0
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM creative_project_prompt_bindings "
                "WHERE project_id = ? AND stage = ? AND episode_number = ?",
                (int(project_id), str(stage), int(episode_number)),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["override"] = json.loads(result.pop("override_json") or "{}")
        return result

    def resolve_snapshot(
        self,
        *,
        project_id: int,
        stage: str,
        mode: str,
        episode_number: int = 0,
        run_template_key: str | None = None,
        run_template_revision: int | None = None,
        run_override: dict[str, Any] | None = None,
        actor_user_id: str = "",
    ) -> dict[str, Any] | None:
        settings = self.project_settings(project_id)
        if not settings.get("enabled") and not run_template_key:
            return None
        binding = self.binding(project_id, stage, episode_number)
        if binding is None:
            binding = self.binding(project_id, stage, 0)
        if run_template_key:
            template = self.get_template_by_key(run_template_key)
            if template is None:
                raise ValueError("所选模板不存在")
            if template["owner_scope"] == "user" and template["owner_user_id"] != actor_user_id:
                raise PermissionError("无权使用该个人模板")
            if template["owner_scope"] == "project" and int(template["project_id"] or 0) != int(project_id):
                raise PermissionError("无权使用其他项目模板")
            revision = self.get_revision(template["id"], run_template_revision)
            self._assert_template_selectable(
                template, revision=int(revision["revision"])
            )
            override = dict(run_override or {})
        elif binding is not None:
            template = self.get_template(binding["template_id"])
            if template is None:
                raise ValueError("项目绑定的模板不存在")
            revision = self.get_revision(template["id"], binding["template_revision"])
            override = {**dict(binding.get("override") or {}), **dict(run_override or {})}
        else:
            template = self.get_template(settings["default_template_id"])
            if template is None:
                raise ValueError("项目默认模板不存在")
            revision = self.get_revision(
                template["id"], int(settings["default_template_revision"])
            )
            override = dict(run_override or {})
        if template["owner_scope"] == "user" and template["owner_user_id"] != actor_user_id:
            raise PermissionError("无权使用该个人模板")
        if template["owner_scope"] == "project" and int(template["project_id"] or 0) != int(project_id):
            raise PermissionError("无权使用其他项目模板")
        if not self._supports_stage(revision, stage):
            raise ValueError(f"所选模板版本不支持 {stage} 阶段")
        snapshot = compose_prompt_snapshot(
            template_id=template["id"],
            template_key=template["template_key"],
            title=template["title"],
            revision=int(revision["revision"]),
            content=revision["content"],
            stage=stage,
            mode=mode,
            override=override,
        )
        snapshot.update(
            owner_scope=template["owner_scope"],
            owner_user_id=template["owner_user_id"],
            project_id=template["project_id"],
        )
        return snapshot
