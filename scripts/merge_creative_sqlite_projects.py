# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Merge project graphs from a legacy creative SQLite store into PostgreSQL.

The formal cutover importer intentionally accepts only an empty target.  This
operator tool handles the narrower recovery case where a frozen SQLite store
contains projects that must be appended to an already-live shared database.
Every identifier that can collide is remapped, and the creative rows plus the
old workflow/project identities are changed in one PostgreSQL transaction.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection

# Permit direct execution from the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creative.database_schema import CREATIVE_METADATA
from creative.sqlite_import import (
    BUSINESS_TABLE_SPECS,
    CreativeImportError,
    CreativeTableSpec,
    _reset_identity_sequences,
    inspect_sqlite_creative,
    normalize_for_target,
    open_read_only,
)
from system_database import SystemDatabase, SystemDatabaseSettings


app = typer.Typer(add_completion=False)

ALLOWED_SOURCE_PROBLEMS = frozenset(
    {
        "creative_deliveries.projection_source_json: column is missing from "
        "the legacy source table",
    }
)
SYSTEM_PROJECT_REFERENCES: tuple[tuple[str, str], ...] = (
    ("audit_logs", "project_id"),
    ("project_members", "project_id"),
    ("workflow_instances", "project_id"),
    ("workflow_instances", "resource_id"),
    ("workflow_project_member_events", "project_id"),
    ("workflow_tasks", "project_id"),
)
SPEC_BY_NAME = {spec.name: spec for spec in BUSINESS_TABLE_SPECS}
JSON_ID_KEYS: Mapping[str, str] = {
    "project_id": "creative_projects",
    "local_project_id": "creative_projects",
    "topic_id": "creative_topics",
    "breakdown_id": "creative_breakdowns",
    "brief_id": "creative_blueprints",
    "locked_brief_id": "creative_blueprints",
    "seed_id": "creative_seeds",
    "seed_version_id": "creative_seeds",
    "beats_id": "creative_episodes",
    "episode_id": "creative_episodes",
    "source_episode_id": "creative_episodes",
    "accepted_episode_id": "creative_episodes",
    "outline_id": "creative_episodes",
    "review_id": "script_reviews",
    "optimization_id": "script_optimizations",
    "template_id": "creative_prompt_templates",
    "prompt_template_id": "creative_prompt_templates",
    "generation_context_id": "creative_generation_contexts",
}
RESOURCE_HINTS: Mapping[str, str] = {
    "project": "creative_projects",
    "brief": "creative_blueprints",
    "blueprint": "creative_blueprints",
    "seed": "creative_seeds",
    "character": "creative_characters",
    "characters": "creative_characters",
    "relationship": "creative_relationships",
    "relationships": "creative_relationships",
    "outline": "creative_episodes",
    "beats": "creative_episodes",
    "script": "creative_episodes",
    "episode": "creative_episodes",
    "topic": "creative_topics",
    "breakdown": "creative_breakdowns",
}


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _source_rows(source: Path) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    with open_read_only(source) as connection:
        connection.row_factory = sqlite3.Row
        present = _source_tables(connection)
        for spec in BUSINESS_TABLE_SPECS:
            if spec.name not in present:
                continue
            rows[spec.name] = [
                dict(row)
                for row in connection.execute(f'SELECT * FROM "{spec.name}"')
            ]
    return rows


def _row_key(row: Mapping[str, Any], spec: CreativeTableSpec) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in spec.primary_key)


def _select_project_graphs(
    all_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    projects = [dict(row) for row in all_rows.get("creative_projects", ())]
    if not projects:
        raise CreativeImportError("legacy source has no creative projects")
    project_ids = {int(row["id"]) for row in projects}
    selected: dict[str, list[dict[str, Any]]] = {
        "creative_projects": projects,
    }

    for spec in BUSINESS_TABLE_SPECS:
        if spec.name in {"creative_store_metadata", "creative_projects"}:
            continue
        table_rows = all_rows.get(spec.name, ())
        if any("project_id" == column for column in CREATIVE_METADATA.tables[spec.name].columns.keys()):
            matches = [
                dict(row)
                for row in table_rows
                if row.get("project_id") is not None
                and int(row["project_id"]) in project_ids
            ]
            if matches:
                selected[spec.name] = matches

    # Follow declared foreign keys upward until every selected child has its
    # required parent.  This pulls in referenced topics, breakdowns, templates,
    # reviews and other non-project-keyed dependencies without global clutter.
    changed = True
    while changed:
        changed = False
        for spec in BUSINESS_TABLE_SPECS:
            child_rows = selected.get(spec.name, ())
            for child_column, parent_name, parent_column in spec.internal_references:
                wanted = {
                    row.get(child_column)
                    for row in child_rows
                    if row.get(child_column) is not None
                }
                if not wanted:
                    continue
                current = selected.setdefault(parent_name, [])
                current_keys = {
                    _row_key(row, SPEC_BY_NAME[parent_name]) for row in current
                }
                for parent in all_rows.get(parent_name, ()):
                    if parent.get(parent_column) not in wanted:
                        continue
                    key = _row_key(parent, SPEC_BY_NAME[parent_name])
                    if key not in current_keys:
                        current.append(dict(parent))
                        current_keys.add(key)
                        changed = True

        # Revisions and AB arms are owned children rather than referenced
        # parents, so include them after their owning rows enter the graph.
        template_ids = {
            row["id"] for row in selected.get("creative_prompt_templates", ())
        }
        revisions = [
            dict(row)
            for row in all_rows.get("creative_prompt_template_revisions", ())
            if row.get("template_id") in template_ids
        ]
        if revisions and not selected.get("creative_prompt_template_revisions"):
            selected["creative_prompt_template_revisions"] = revisions
            changed = True

        experiment_ids = {
            row["id"] for row in selected.get("creative_prompt_ab_experiments", ())
        }
        arms = [
            dict(row)
            for row in all_rows.get("creative_prompt_ab_arms", ())
            if row.get("experiment_id") in experiment_ids
        ]
        if arms and not selected.get("creative_prompt_ab_arms"):
            selected["creative_prompt_ab_arms"] = arms
            changed = True

    return {name: rows for name, rows in selected.items() if rows}


def _source_store_id(source: Path) -> str:
    with open_read_only(source) as connection:
        row = connection.execute(
            "SELECT value FROM creative_store_metadata WHERE key='store_id'"
        ).fetchone()
    if row is None:
        raise CreativeImportError("legacy source has no store_id")
    try:
        return str(uuid.UUID(str(row[0])))
    except ValueError as exc:
        raise CreativeImportError(f"legacy store_id is not a UUID: {row[0]!r}") from exc


def _target_store_id(connection: Connection) -> str:
    table = CREATIVE_METADATA.tables["creative_store_metadata"]
    value = connection.execute(
        select(table.c.value).where(table.c.key == "store_id")
    ).scalar_one()
    return str(uuid.UUID(str(value)))


def _stable_text_id(store_id: str, table: str, value: Any) -> str:
    namespace = uuid.UUID(store_id)
    return str(uuid.uuid5(namespace, f"{table}:{value}"))


def _existing_pk_values(
    connection: Connection, spec: CreativeTableSpec
) -> set[Any]:
    if len(spec.primary_key) != 1:
        return set()
    table = CREATIVE_METADATA.tables[spec.name]
    column = table.c[spec.primary_key[0]]
    return set(connection.execute(select(column)).scalars())


def _build_id_maps(
    connection: Connection,
    selected: Mapping[str, Sequence[Mapping[str, Any]]],
    source_store_id: str,
) -> dict[str, dict[Any, Any]]:
    maps: dict[str, dict[Any, Any]] = defaultdict(dict)
    for spec in BUSINESS_TABLE_SPECS:
        rows = selected.get(spec.name, ())
        if not rows or len(spec.primary_key) != 1:
            continue
        key = spec.primary_key[0]
        if key == "project_id" and spec.name != "creative_projects":
            # Project-owned singleton tables inherit the project mapping; they
            # do not own an independent integer identity sequence.
            continue
        values = sorted(
            {row[key] for row in rows},
            key=lambda value: (
                0 if isinstance(value, int) and not isinstance(value, bool) else 1,
                value if isinstance(value, int) and not isinstance(value, bool) else str(value),
            ),
        )
        existing = _existing_pk_values(connection, spec)
        if key in {"id", "project_id"} and all(
            isinstance(value, int) and not isinstance(value, bool) for value in values
        ):
            table = CREATIVE_METADATA.tables[spec.name]
            column = table.c[key]
            maximum = connection.execute(select(func.max(column))).scalar_one()
            start = int(maximum or 0) + 1
            maps[spec.name] = {
                old: start + index for index, old in enumerate(values)
            }
            continue
        for value in values:
            if spec.name == "creative_prompt_templates":
                maps[spec.name][value] = _stable_text_id(
                    source_store_id, spec.name, value
                )
            elif value in existing:
                maps[spec.name][value] = _stable_text_id(
                    source_store_id, spec.name, value
                )
            else:
                maps[spec.name][value] = value

    # operation_id is a logical identity shared by several tables even though
    # the legacy schema did not declare cross-table foreign keys.
    operation_values = {
        row["operation_id"]
        for rows in selected.values()
        for row in rows
        if row.get("operation_id") not in (None, "")
    }
    operation_tables = [
        spec
        for spec in BUSINESS_TABLE_SPECS
        if "operation_id" in CREATIVE_METADATA.tables[spec.name].columns
    ]
    for value in operation_values:
        collision = any(
            connection.execute(
                select(CREATIVE_METADATA.tables[spec.name].c.operation_id).where(
                    CREATIVE_METADATA.tables[spec.name].c.operation_id == value
                )
            ).first()
            is not None
            for spec in operation_tables
        )
        maps["__operation_id__"][value] = (
            _stable_text_id(source_store_id, "operation", value)
            if collision
            else value
        )
    return dict(maps)


def _mapped_value(value: Any, mapping: Mapping[Any, Any]) -> Any:
    if value in mapping:
        return mapping[value]
    if isinstance(value, str) and value.isdigit() and int(value) in mapping:
        mapped = mapping[int(value)]
        return str(mapped)
    return value


def _resource_table(hint: str | None) -> str | None:
    return RESOURCE_HINTS.get(str(hint or "").strip().lower())


def _remap_json(
    value: Any,
    maps: Mapping[str, Mapping[Any, Any]],
    *,
    hint: str | None = None,
) -> Any:
    if isinstance(value, list):
        return [_remap_json(item, maps, hint=hint) for item in value]
    if not isinstance(value, dict):
        return value

    local_hint = str(
        value.get("resource_type") or value.get("stage") or hint or ""
    ).lower()
    result: dict[str, Any] = {}
    for key, item in value.items():
        child_hint = key if key in RESOURCE_HINTS else local_hint
        table_name = JSON_ID_KEYS.get(key)
        if key == "operation_id":
            result[key] = _mapped_value(item, maps.get("__operation_id__", {}))
        elif table_name:
            result[key] = _mapped_value(item, maps.get(table_name, {}))
        elif key in {"id", "resource_id", "resource_ref", "source", "target"}:
            hinted_table = _resource_table(child_hint)
            result[key] = _mapped_value(item, maps.get(hinted_table or "", {}))
        elif key == "resource_ids" and isinstance(item, list):
            hinted_table = _resource_table(child_hint)
            result[key] = [
                _mapped_value(entry, maps.get(hinted_table or "", {}))
                for entry in item
            ]
        else:
            result[key] = _remap_json(item, maps, hint=child_hint)
    return result


def _transform_row(
    row: Mapping[str, Any],
    spec: CreativeTableSpec,
    maps: Mapping[str, Mapping[Any, Any]],
    source_store_id: str,
) -> dict[str, Any]:
    mapped = normalize_for_target(row, spec)
    table = CREATIVE_METADATA.tables[spec.name]
    mapped = {key: value for key, value in mapped.items() if key in table.columns}

    own_map = maps.get(spec.name, {})
    for key in spec.primary_key:
        if key in mapped:
            mapped[key] = _mapped_value(mapped[key], own_map)
    for child_column, parent_name, _parent_column in spec.internal_references:
        if child_column in mapped and mapped[child_column] is not None:
            mapped[child_column] = _mapped_value(
                mapped[child_column], maps.get(parent_name, {})
            )
    if "operation_id" in mapped:
        mapped["operation_id"] = _mapped_value(
            mapped["operation_id"], maps.get("__operation_id__", {})
        )

    if spec.name == "creative_projects" and mapped.get("locked_brief_id"):
        mapped["locked_brief_id"] = _mapped_value(
            mapped["locked_brief_id"], maps.get("creative_blueprints", {})
        )
    if spec.name == "creative_prompt_templates":
        mapped["template_key"] = (
            f"legacy:{source_store_id[:8]}:{mapped['template_key']}"
        )
    if spec.name == "creative_resource_heads":
        resource_table = _resource_table(str(mapped.get("stage") or ""))
        mapped["resource_ref"] = _mapped_value(
            mapped.get("resource_ref"), maps.get(resource_table or "", {})
        )

    for column in spec.json_columns:
        if column in mapped and mapped[column] is not None:
            mapped[column] = _remap_json(mapped[column], maps)
    return mapped


def _system_reference_counts(
    connection: Connection, identities: Mapping[str, str]
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for table_name, column_name in SYSTEM_PROJECT_REFERENCES:
        per_identity: dict[str, int] = {}
        for old_identity in identities:
            count = connection.execute(
                text(
                    f'SELECT count(*) FROM "{table_name}" '
                    f'WHERE "{column_name}" = :identity'
                ),
                {"identity": old_identity},
            ).scalar_one()
            if count:
                per_identity[old_identity] = int(count)
        if per_identity:
            counts[f"{table_name}.{column_name}"] = per_identity
    return counts


def _plan(
    connection: Connection,
    source: Path,
    selected: Mapping[str, Sequence[Mapping[str, Any]]],
    source_store_id: str,
) -> tuple[dict[str, Any], dict[str, dict[Any, Any]], dict[str, str]]:
    maps = _build_id_maps(connection, selected, source_store_id)
    target_store_id = _target_store_id(connection)
    project_map = maps["creative_projects"]
    identities = {
        f"creative:{source_store_id}:project:{old_id}":
        f"creative:{target_store_id}:project:{new_id}"
        for old_id, new_id in project_map.items()
    }
    system_counts = _system_reference_counts(connection, identities)
    membership_total = sum(
        counts
        for label, per_identity in system_counts.items()
        if label == "project_members.project_id"
        for counts in per_identity.values()
    )
    missing_members = [
        old_identity
        for old_identity in identities
        if system_counts.get("project_members.project_id", {}).get(old_identity, 0)
        == 0
    ]
    if missing_members:
        raise CreativeImportError(
            "projects have no recoverable shared membership: "
            + ", ".join(missing_members)
        )

    project_rows = {
        int(row["id"]): row for row in selected["creative_projects"]
    }
    project_summary = [
        {
            "source_id": old_id,
            "target_id": new_id,
            "name": project_rows[int(old_id)]["name"],
            "old_identity": old_identity,
            "new_identity": identities[old_identity],
            "members": system_counts["project_members.project_id"][old_identity],
        }
        for old_identity, new_identity in identities.items()
        for old_id, new_id in [
            (int(old_identity.rsplit(":", 1)[-1]), project_map[int(old_identity.rsplit(":", 1)[-1])])
        ]
    ]
    return (
        {
            "mode": "plan",
            "source": str(source.resolve()),
            "source_store_id": source_store_id,
            "target_store_id": target_store_id,
            "projects": project_summary,
            "row_counts": {
                name: len(rows) for name, rows in selected.items()
            },
            "id_maps": {
                table: {str(old): new for old, new in mapping.items()}
                for table, mapping in maps.items()
            },
            "system_reference_counts": system_counts,
            "membership_total": membership_total,
        },
        maps,
        identities,
    )


def _verify_inserted_rows(
    connection: Connection,
    selected: Mapping[str, Sequence[Mapping[str, Any]]],
    maps: Mapping[str, Mapping[Any, Any]],
    identities: Mapping[str, str],
    before_system_counts: Mapping[str, Mapping[str, int]],
) -> dict[str, int]:
    target_counts: dict[str, int] = {}
    project_ids = set(maps["creative_projects"].values())
    for spec in BUSINESS_TABLE_SPECS:
        source_rows = selected.get(spec.name, ())
        if not source_rows:
            continue
        table = CREATIVE_METADATA.tables[spec.name]
        if "project_id" in table.columns and all(
            row.get("project_id") is not None for row in source_rows
        ):
            count = connection.execute(
                select(func.count()).select_from(table).where(
                    table.c.project_id.in_(project_ids)
                )
            ).scalar_one()
        elif len(spec.primary_key) == 1 and maps.get(spec.name):
            key = spec.primary_key[0]
            count = connection.execute(
                select(func.count()).select_from(table).where(
                    table.c[key].in_(set(maps[spec.name].values()))
                )
            ).scalar_one()
        else:
            continue
        target_counts[spec.name] = int(count)
        if int(count) != len(source_rows):
            raise CreativeImportError(
                f"post-merge verification failed for {spec.name}: "
                f"source={len(source_rows)} target={count}"
            )

    after_old = _system_reference_counts(connection, identities)
    if after_old:
        raise CreativeImportError(
            "old workflow identities remain after merge: "
            + json.dumps(after_old, ensure_ascii=False, sort_keys=True)
        )
    for label, old_counts in before_system_counts.items():
        table_name, column_name = label.split(".", 1)
        for old_identity, expected in old_counts.items():
            new_identity = identities[old_identity]
            actual = connection.execute(
                text(
                    f'SELECT count(*) FROM "{table_name}" '
                    f'WHERE "{column_name}" = :identity'
                ),
                {"identity": new_identity},
            ).scalar_one()
            if int(actual) < int(expected):
                raise CreativeImportError(
                    f"system identity verification failed for {label}: "
                    f"expected at least {expected}, got {actual}"
                )
    return target_counts


@app.command()
def main(
    source: Path = typer.Option(..., exists=True, readable=True),
    apply: bool = typer.Option(
        False, "--apply", help="Commit the planned project merge."
    ),
) -> None:
    inspection = inspect_sqlite_creative(source, strict=False)
    unexpected = set(inspection.problems) - ALLOWED_SOURCE_PROBLEMS
    if unexpected:
        raise typer.BadParameter(
            "legacy source has unsupported problems: " + "; ".join(sorted(unexpected))
        )
    if not inspection.integrity_ok:
        raise typer.BadParameter("legacy source failed SQLite integrity_check")

    source = source.resolve()
    source_store_id = _source_store_id(source)
    selected = _select_project_graphs(_source_rows(source))
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    try:
        health = database.health()
        if not health.reachable or not health.schema_current:
            raise typer.BadParameter(
                "PostgreSQL target must be reachable and at Alembic head"
            )
        if not apply:
            with database.connection() as connection:
                plan, _maps, _identities = _plan(
                    connection, source, selected, source_store_id
                )
            typer.echo(json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str))
            return

        with database.transaction() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": "merge_creative_sqlite_projects"},
            )
            plan, maps, identities = _plan(
                connection, source, selected, source_store_id
            )
            before_system_counts = plan["system_reference_counts"]

            inserted: dict[str, int] = {}
            for spec in BUSINESS_TABLE_SPECS:
                rows = selected.get(spec.name, ())
                if not rows:
                    continue
                payload = [
                    _transform_row(row, spec, maps, source_store_id)
                    for row in rows
                ]
                connection.execute(CREATIVE_METADATA.tables[spec.name].insert(), payload)
                inserted[spec.name] = len(payload)

            for old_identity, new_identity in identities.items():
                for table_name, column_name in SYSTEM_PROJECT_REFERENCES:
                    connection.execute(
                        text(
                            f'UPDATE "{table_name}" SET "{column_name}" = :new '
                            f'WHERE "{column_name}" = :old'
                        ),
                        {"old": old_identity, "new": new_identity},
                    )

            _reset_identity_sequences(connection, BUSINESS_TABLE_SPECS)
            target_counts = _verify_inserted_rows(
                connection,
                selected,
                maps,
                identities,
                before_system_counts,
            )

        result = {
            **plan,
            "mode": "apply",
            "status": "completed",
            "inserted": inserted,
            "verified_counts": target_counts,
        }
        typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    finally:
        database.dispose()


if __name__ == "__main__":
    app()
