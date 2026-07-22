# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MIGRATION_KEY = "role_workflow_v1"
_ACTIVE_TASK_STATUSES = ("pool", "assigned", "in_progress", "waiting_review", "returned")
_RESOURCE_ROLES = {
    "breakdown": "selection_operator",
    "brief": "creative_planner",
    "characters": "creative_planner",
    "outline": "screenwriter",
    "beats": "screenwriter",
}
_NEXT_ROLES = {
    "breakdown": "selection_operator",
    "brief": "creative_planner",
    "characters": "creative_planner",
    "outline": "screenwriter",
    "beats": "screenwriter",
    "delivery": "system_admin",
}
_UUID_NAMESPACE = uuid.UUID("8b69c792-16b1-5fc3-8bd0-56af20920f21")


class MigrationMappingError(RuntimeError):
    """Raised when legacy rows cannot be mapped without guessing."""


@dataclass(frozen=True)
class MigrationReport:
    dry_run: bool
    already_applied: bool
    approved_submissions: int
    draft_submissions: int
    next_tasks: int
    reassigned_projects: int
    backups: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _Resource:
    resource_type: str
    resource_id: str
    version: int
    legacy_status: str
    status: str
    project_id: str | None
    topic_id: int | None
    snapshot: dict[str, Any]
    created_at: float
    updated_at: float


def migrate(
    root: str | Path, admin_user_id: str, dry_run: bool = False
) -> MigrationReport:
    workspace = Path(root).resolve()
    system_db = workspace / ".runtime" / "system" / "system.db"
    creative_db = workspace / ".runtime" / "creative" / "creative.db"
    _require_database(system_db)
    _require_database(creative_db)
    ledger_state = (_has_ledger(system_db), _has_ledger(creative_db))
    if ledger_state[0] != ledger_state[1]:
        raise MigrationMappingError(
            "role workflow migration ledger differs between system and creative databases"
        )
    if ledger_state == (True, True):
        return MigrationReport(dry_run, True, 0, 0, 0, 0)

    _validate_admin(system_db, admin_user_id)
    resources, project_nodes, reassigned_projects = _plan_migration(creative_db)
    report = MigrationReport(
        dry_run=dry_run,
        already_applied=False,
        approved_submissions=sum(item.status == "approved" for item in resources),
        draft_submissions=sum(item.status == "draft" for item in resources),
        next_tasks=len(project_nodes),
        reassigned_projects=reassigned_projects,
    )
    if dry_run:
        return report

    backups = _create_backups(system_db, creative_db)
    try:
        _migrate_creative_database(
            creative_db, admin_user_id, resources, project_nodes
        )
        _migrate_system_database(
            system_db, admin_user_id, resources, project_nodes
        )
    except BaseException:
        _restore_database(backups[0], system_db)
        _restore_database(backups[1], creative_db)
        raise
    return MigrationReport(
        dry_run=False,
        already_applied=False,
        approved_submissions=report.approved_submissions,
        draft_submissions=report.draft_submissions,
        next_tasks=report.next_tasks,
        reassigned_projects=report.reassigned_projects,
        backups=backups,
    )


def _require_database(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _has_ledger(path: Path) -> bool:
    with _connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'role_workflow_migrations'"
        ).fetchone()
        if table is None:
            return False
        return connection.execute(
            "SELECT 1 FROM role_workflow_migrations WHERE migration_key = ?",
            (MIGRATION_KEY,),
        ).fetchone() is not None


def _validate_admin(system_db: Path, admin_user_id: str) -> None:
    with _connect(system_db) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE u.id = ? AND u.status = 'active' AND u.is_deleted = 0
              AND r.code = 'system_admin' COLLATE NOCASE AND r.is_enabled = 1
            """,
            (admin_user_id,),
        ).fetchone()
    if row is None:
        raise MigrationMappingError(
            "admin_user_id must identify an active local system administrator"
        )


def _plan_migration(
    creative_db: Path,
) -> tuple[list[_Resource], dict[str, str], int]:
    with _connect(creative_db) as connection:
        projects = connection.execute(
            "SELECT * FROM creative_projects WHERE deleted = 0 ORDER BY id"
        ).fetchall()
        resources = _collect_resources(connection, projects)
        project_nodes = {
            str(project["id"]): _next_project_node(connection, project)
            for project in projects
            if str(project["status"] or "").lower() not in {"archived", "deleted"}
        }
        reassigned = int(
            connection.execute(
                "SELECT COUNT(*) FROM creative_projects "
                "WHERE deleted = 0 AND created_by = 'local'"
            ).fetchone()[0]
        )
    return resources, project_nodes, reassigned


def _collect_resources(
    connection: sqlite3.Connection, projects: list[sqlite3.Row]
) -> list[_Resource]:
    resources: list[_Resource] = []
    project_by_breakdown = _unique_project_breakdown_map(projects)
    for row in connection.execute(
        "SELECT * FROM creative_breakdowns WHERE status IN ('draft', 'locked') "
        "ORDER BY topic_id, version, id"
    ):
        snapshot = {
            key: _json_or_value(row[key])
            for key in (
                "id",
                "topic_id",
                "version",
                "parent_version_id",
                "content_json",
                "field_sources_json",
                "risks_json",
            )
        }
        snapshot = {
            "id": snapshot["id"],
            "topic_id": snapshot["topic_id"],
            "version": snapshot["version"],
            "parent_version_id": snapshot["parent_version_id"],
            "content": snapshot["content_json"],
            "field_sources": snapshot["field_sources_json"],
            "risks": snapshot["risks_json"],
        }
        resources.append(
            _resource(
                "breakdown",
                str(row["id"]),
                row,
                snapshot,
                project_id=project_by_breakdown.get(int(row["id"])),
                topic_id=int(row["topic_id"]),
            )
        )
    for row in connection.execute(
        "SELECT * FROM creative_blueprints "
        "WHERE status IN ('draft', 'locked', 'brief_locked') ORDER BY project_id, version, id"
    ):
        project = next(
            (item for item in projects if int(item["id"]) == int(row["project_id"])),
            None,
        )
        if project is None:
            raise MigrationMappingError(
                f"brief {row['id']} references missing active project {row['project_id']}"
            )
        content = _json(row["content_json"], {})
        content.update(
            {
                "project_name": project["name"],
                "genre": project["genre"],
                "target_platform": project["target_platform"],
                "target_audience": content.get("audience")
                or project["target_audience"],
                "target_episode_count": project["target_episode_count"],
                "episode_duration_seconds": project["episode_duration_seconds"],
            }
        )
        snapshot = {
            "id": row["id"],
            "project_id": row["project_id"],
            "candidate_group_id": row["candidate_group_id"],
            "version": row["version"],
            "parent_version_id": row["parent_version_id"],
            "content": content,
            "risk_score": row["risk_score"],
            "risk_level": row["risk_level"],
            "difference_note": row["difference_note"],
            "source_refs": _json(row["source_refs_json"], {}),
            "generation_meta": _json(row["generation_meta_json"], {}),
        }
        resources.append(
            _resource("brief", str(row["id"]), row, snapshot, str(row["project_id"]))
        )
    resources.extend(_collect_batch_resources(connection, "characters"))
    resources.extend(_collect_batch_resources(connection, "outline"))
    for row in connection.execute(
        "SELECT * FROM creative_episodes "
        "WHERE stage = 'beats' AND status IN ('draft', 'locked') "
        "ORDER BY project_id, episode_number, version, id"
    ):
        resources.append(
            _resource(
                "beats",
                str(row["id"]),
                row,
                _episode_snapshot(row),
                str(row["project_id"]),
            )
        )
    return resources


def _unique_project_breakdown_map(projects: list[sqlite3.Row]) -> dict[int, str]:
    grouped: dict[int, list[str]] = {}
    for project in projects:
        grouped.setdefault(int(project["breakdown_id"]), []).append(str(project["id"]))
    duplicates = {key: values for key, values in grouped.items() if len(values) > 1}
    if duplicates:
        raise MigrationMappingError(
            f"legacy breakdown belongs to multiple projects: {duplicates}"
        )
    return {key: values[0] for key, values in grouped.items()}


def _collect_batch_resources(
    connection: sqlite3.Connection, resource_type: str
) -> list[_Resource]:
    if resource_type == "characters":
        rows = connection.execute(
            "SELECT * FROM creative_characters WHERE status IN ('draft', 'locked') "
            "ORDER BY project_id, batch_id, id"
        ).fetchall()
        relation_rows = connection.execute(
            "SELECT * FROM creative_relationships WHERE status IN ('draft', 'locked') "
            "ORDER BY project_id, batch_id, id"
        ).fetchall()
        grouped = _group_rows(rows)
        relations = _group_rows(relation_rows)
        keys = sorted(set(grouped) | set(relations))
        result = []
        for key in keys:
            characters = grouped.get(key, [])
            relationships = relations.get(key, [])
            if not characters:
                raise MigrationMappingError(
                    f"character batch {key[0]}:{key[1]} has relationships but no characters"
                )
            all_rows = characters + relationships
            status = _batch_status(all_rows, "character", key)
            first = characters[0]
            snapshot = {
                "project_id": key[0],
                "batch_id": key[1],
                "characters": [_creative_item_snapshot(row) for row in characters],
                "relationships": [_creative_item_snapshot(row) for row in relationships],
            }
            result.append(
                _resource(
                    resource_type,
                    f"{key[0]}:{key[1]}",
                    first,
                    snapshot,
                    str(key[0]),
                    forced_status=status,
                    legacy_ids=[int(row["id"]) for row in all_rows],
                )
            )
        return result
    rows = connection.execute(
        "SELECT * FROM creative_episodes "
        "WHERE stage = 'outline' AND status IN ('draft', 'locked') "
        "ORDER BY project_id, batch_id, episode_number, id"
    ).fetchall()
    result = []
    for key, episodes in _group_rows(rows).items():
        status = _batch_status(episodes, "outline", key)
        result.append(
            _resource(
                resource_type,
                f"{key[0]}:{key[1]}",
                episodes[0],
                {
                    "project_id": key[0],
                    "batch_id": key[1],
                    "episodes": [_episode_snapshot(row) for row in episodes],
                },
                str(key[0]),
                forced_status=status,
                legacy_ids=[int(row["id"]) for row in episodes],
            )
        )
    return result


def _group_rows(rows: list[sqlite3.Row]) -> dict[tuple[int, str], list[sqlite3.Row]]:
    grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault((int(row["project_id"]), str(row["batch_id"])), []).append(row)
    return grouped


def _batch_status(
    rows: list[sqlite3.Row], label: str, key: tuple[int, str]
) -> str:
    statuses = {str(row["status"]) for row in rows}
    if len(statuses) != 1:
        raise MigrationMappingError(
            f"legacy {label} batch {key[0]}:{key[1]} has mixed statuses {sorted(statuses)}"
        )
    return statuses.pop()


def _resource(
    resource_type: str,
    resource_id: str,
    row: sqlite3.Row,
    snapshot: dict[str, Any],
    project_id: str | None,
    topic_id: int | None = None,
    *,
    forced_status: str | None = None,
    legacy_ids: list[int] | None = None,
) -> _Resource:
    legacy_status = forced_status or str(row["status"])
    status = "approved" if legacy_status in {"locked", "brief_locked"} else "draft"
    ids = legacy_ids or [int(row["id"])]
    snapshot["migration"] = {
        "migration_key": MIGRATION_KEY,
        "legacy_resource_type": resource_type,
        "legacy_ids": ids,
        "legacy_status": legacy_status,
    }
    return _Resource(
        resource_type=resource_type,
        resource_id=resource_id,
        version=int(row["version"]),
        legacy_status=legacy_status,
        status=status,
        project_id=project_id,
        topic_id=topic_id,
        snapshot=snapshot,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _creative_item_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "batch_id": row["batch_id"],
        "version": row["version"],
        "parent_version_id": row["parent_version_id"],
        "character_key": (
            row["character_key"] if "character_key" in row.keys() else None
        ),
        "relationship_key": (
            row["relationship_key"] if "relationship_key" in row.keys() else None
        ),
        "content": _json(row["content_json"], {}),
        "source_refs": _json(row["source_refs_json"], {}),
        "generation_meta": _json(row["generation_meta_json"], {}),
    }


def _episode_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "stage": row["stage"],
        "episode_number": row["episode_number"],
        "batch_id": row["batch_id"],
        "version": row["version"],
        "parent_version_id": row["parent_version_id"],
        "content": _json(row["content_json"], {}),
        "upstream_refs": _json(row["upstream_refs_json"], {}),
        "generation_meta": _json(row["generation_meta_json"], {}),
    }


def _next_project_node(
    connection: sqlite3.Connection, project: sqlite3.Row
) -> str:
    project_id = int(project["id"])
    brief_id = project["locked_brief_id"]
    if brief_id:
        _require_pointer_rows(
            connection.execute(
                "SELECT status FROM creative_blueprints WHERE id = ? AND project_id = ?",
                (brief_id, project_id),
            ).fetchall(),
            project_id,
            "locked_brief_id",
        )
    character_batch_id = project["character_batch_id"]
    if character_batch_id:
        _require_pointer_rows(
            connection.execute(
                "SELECT status FROM creative_characters WHERE project_id = ? AND batch_id = ?",
                (project_id, character_batch_id),
            ).fetchall(),
            project_id,
            "character_batch_id",
        )
    outline_batch_id = project["outline_batch_id"]
    if outline_batch_id:
        _require_pointer_rows(
            connection.execute(
                "SELECT status FROM creative_episodes WHERE project_id = ? "
                "AND stage = 'outline' AND batch_id = ?",
                (project_id, outline_batch_id),
            ).fetchall(),
            project_id,
            "outline_batch_id",
        )
    if connection.execute(
        "SELECT 1 FROM creative_episodes WHERE project_id = ? AND stage = 'beats' "
        "AND status IN ('locked', 'approved') LIMIT 1",
        (project_id,),
    ).fetchone():
        return "delivery"
    if outline_batch_id and connection.execute(
        "SELECT 1 FROM creative_episodes WHERE project_id = ? AND stage = 'outline' "
        "AND batch_id = ? AND status IN ('locked', 'approved') LIMIT 1",
        (project_id, outline_batch_id),
    ).fetchone():
        return "beats"
    if character_batch_id and connection.execute(
        "SELECT 1 FROM creative_characters WHERE project_id = ? AND batch_id = ? "
        "AND status IN ('locked', 'approved') LIMIT 1",
        (project_id, character_batch_id),
    ).fetchone():
        return "outline"
    if brief_id and connection.execute(
        "SELECT 1 FROM creative_blueprints WHERE id = ? "
        "AND status IN ('locked', 'brief_locked', 'approved')",
        (brief_id,),
    ).fetchone():
        return "characters"
    if connection.execute(
        "SELECT 1 FROM creative_breakdowns WHERE id = ? "
        "AND status IN ('locked', 'approved')",
        (project["breakdown_id"],),
    ).fetchone():
        return "brief"
    return "breakdown"


def _require_pointer_rows(
    rows: list[sqlite3.Row], project_id: int, pointer_name: str
) -> None:
    if not rows:
        raise MigrationMappingError(
            f"project {project_id} {pointer_name} references a missing resource"
        )
    statuses = {str(row["status"]) for row in rows}
    if not statuses.issubset({"locked", "brief_locked", "approved"}):
        raise MigrationMappingError(
            f"project {project_id} {pointer_name} references non-approved statuses "
            f"{sorted(statuses)}"
        )


def _migrate_creative_database(
    creative_db: Path,
    admin_user_id: str,
    resources: list[_Resource],
    project_nodes: dict[str, str],
) -> None:
    del project_nodes
    with _connect(creative_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS role_workflow_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at REAL NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "UPDATE creative_projects SET created_by = ?, updated_at = updated_at "
            "WHERE created_by = 'local'",
            (admin_user_id,),
        )
        for item in resources:
            task_id = _stable_id("historical-task", item.resource_type, item.resource_id)
            _update_domain_resource(connection, item, task_id)
        connection.execute(
            "INSERT INTO role_workflow_migrations VALUES (?, ?, ?)",
            (
                MIGRATION_KEY,
                time.time(),
                _canonical({"admin_user_id": admin_user_id, "resources": len(resources)}),
            ),
        )
        connection.commit()


def _update_domain_resource(
    connection: sqlite3.Connection, item: _Resource, task_id: str
) -> None:
    if item.resource_type == "breakdown":
        connection.execute(
            "UPDATE creative_breakdowns SET status = ?, workflow_task_id = ? WHERE id = ?",
            (item.status, task_id, int(item.resource_id)),
        )
    elif item.resource_type == "brief":
        connection.execute(
            "UPDATE creative_blueprints SET status = ?, workflow_task_id = ? WHERE id = ?",
            (item.status, task_id, int(item.resource_id)),
        )
    elif item.resource_type == "characters":
        project_id, batch_id = item.resource_id.split(":", 1)
        for table in ("creative_characters", "creative_relationships"):
            connection.execute(
                f"UPDATE {table} SET status = ?, workflow_task_id = ? "
                "WHERE project_id = ? AND batch_id = ?",
                (item.status, task_id, int(project_id), batch_id),
            )
    elif item.resource_type == "outline":
        project_id, batch_id = item.resource_id.split(":", 1)
        connection.execute(
            "UPDATE creative_episodes SET status = ?, workflow_task_id = ? "
            "WHERE project_id = ? AND stage = 'outline' AND batch_id = ?",
            (item.status, task_id, int(project_id), batch_id),
        )
    else:
        connection.execute(
            "UPDATE creative_episodes SET status = ?, workflow_task_id = ? "
            "WHERE id = ? AND stage = 'beats'",
            (item.status, task_id, int(item.resource_id)),
        )


def _migrate_system_database(
    system_db: Path,
    admin_user_id: str,
    resources: list[_Resource],
    project_nodes: dict[str, str],
) -> None:
    now = time.time()
    with _connect(system_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS role_workflow_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at REAL NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        for item in resources:
            workflow_id = _workflow_id(item.project_id, item.topic_id)
            _insert_workflow(
                connection,
                workflow_id,
                item.resource_type,
                item.project_id,
                item.topic_id,
                admin_user_id,
            )
            task_id = _stable_id("historical-task", item.resource_type, item.resource_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO workflow_tasks (
                    id, workflow_id, task_type, role_code, topic_id, project_id,
                    assignee_id, assigned_by, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', CURRENT_TIMESTAMP,
                          CURRENT_TIMESTAMP)
                """,
                (
                    task_id,
                    workflow_id,
                    item.resource_type,
                    _RESOURCE_ROLES[item.resource_type],
                    item.topic_id,
                    item.project_id,
                    admin_user_id,
                    admin_user_id,
                ),
            )
            snapshot = _canonical(item.snapshot)
            submission_id = _stable_id(
                "submission", item.resource_type, item.resource_id, str(item.version)
            )
            reviewed = item.updated_at if item.status == "approved" else None
            connection.execute(
                """
                INSERT OR IGNORE INTO workflow_submissions (
                    id, task_id, resource_type, resource_id, version, status,
                    content_snapshot_json, content_fingerprint, created_by,
                    submitted_by, submitted_at, reviewed_by, reviewed_at,
                    review_comment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    task_id,
                    item.resource_type,
                    item.resource_id,
                    item.version,
                    item.status,
                    snapshot,
                    hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
                    admin_user_id,
                    admin_user_id if item.status == "approved" else None,
                    item.updated_at if item.status == "approved" else None,
                    admin_user_id if item.status == "approved" else None,
                    reviewed,
                    "Migrated from legacy locked state"
                    if item.status == "approved"
                    else "",
                    item.created_at,
                    item.updated_at,
                ),
            )
            if item.status == "approved":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO approval_records (
                        id, workflow_id, task_id, version_id, stage, decision,
                        reviewer_id, comment
                    ) VALUES (?, ?, ?, ?, ?, 'approved', ?, ?)
                    """,
                    (
                        _stable_id("approval", submission_id),
                        workflow_id,
                        task_id,
                        submission_id,
                        item.resource_type,
                        admin_user_id,
                        "Migrated from legacy locked state",
                    ),
                )
        for project_id, node in project_nodes.items():
            _ensure_one_next_task(
                connection, project_id, node, admin_user_id
            )
        connection.execute(
            "INSERT INTO role_workflow_migrations VALUES (?, ?, ?)",
            (
                MIGRATION_KEY,
                now,
                _canonical(
                    {
                        "admin_user_id": admin_user_id,
                        "resources": len(resources),
                        "projects": len(project_nodes),
                    }
                ),
            ),
        )
        connection.commit()


def _insert_workflow(
    connection: sqlite3.Connection,
    workflow_id: str,
    resource_type: str,
    project_id: str | None,
    topic_id: int | None,
    admin_user_id: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO workflow_instances (
            id, workflow_type, resource_type, resource_id, project_id,
            status, created_by
        ) VALUES (?, 'role_workflow', ?, ?, ?, 'active', ?)
        """,
        (
            workflow_id,
            "project" if project_id else resource_type,
            project_id or str(topic_id or "legacy"),
            project_id,
            admin_user_id,
        ),
    )


def _ensure_one_next_task(
    connection: sqlite3.Connection,
    project_id: str,
    node: str,
    admin_user_id: str,
) -> None:
    placeholders = ",".join("?" for _ in _ACTIVE_TASK_STATUSES)
    active = connection.execute(
        f"SELECT * FROM workflow_tasks WHERE project_id = ? "
        f"AND status IN ({placeholders}) ORDER BY created_at, id",
        (project_id, *_ACTIVE_TASK_STATUSES),
    ).fetchall()
    if active:
        matching = [row for row in active if row["task_type"] == node]
        if len(active) == 1 and len(matching) == 1 and active[0]["status"] == "pool":
            return
        raise MigrationMappingError(
            f"project {project_id} has active workflow tasks that conflict with next node {node}"
        )
    workflow_id = _workflow_id(project_id, None)
    _insert_workflow(
        connection, workflow_id, "project", project_id, None, admin_user_id
    )
    connection.execute(
        """
        INSERT INTO workflow_tasks (
            id, workflow_id, task_type, role_code, project_id, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'pool', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            _stable_id("next-task", project_id, node),
            workflow_id,
            node,
            _NEXT_ROLES[node],
            project_id,
        ),
    )


def _workflow_id(project_id: str | None, topic_id: int | None) -> str:
    if project_id:
        return _stable_id("workflow", "project", project_id)
    return _stable_id("workflow", "topic", str(topic_id or "legacy"))


def _create_backups(system_db: Path, creative_db: Path) -> tuple[Path, Path]:
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backups = tuple(
        path.with_name(f"{path.name}.{MIGRATION_KEY}.{suffix}.bak")
        for path in (system_db, creative_db)
    )
    for source, backup in zip((system_db, creative_db), backups, strict=True):
        shutil.copy2(source, backup)
    return backups  # type: ignore[return-value]


def _restore_database(backup: Path, target: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(backup, target)


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, ":".join(parts)))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(str(value))


def _json_or_value(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in {"{", "["}:
        return json.loads(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy creative role workflow data")
    parser.add_argument("--root", default=".")
    parser.add_argument("--admin-user-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = migrate(args.root, args.admin_user_id, dry_run=args.dry_run)
    print(json.dumps(report.__dict__, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
