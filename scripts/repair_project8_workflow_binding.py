# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Repair the verified project-8 rows that were rebound to project 25.

The command is read-only unless ``--apply`` is supplied. Apply mode locks the
repair, re-runs every assertion, copies the exact affected rows into a durable
backup schema, and updates only explicit primary keys in one transaction.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system_database import SystemDatabase, SystemDatabaseSettings


app = typer.Typer(add_completion=False)

CURRENT_STORE_ID = "67707ae7-e8ec-4198-8ffd-706ced485d40"
BACKUP_SCHEMA = "aiworker_scoped_backup_20260911_a262"
REPAIR_BACKUP_SCHEMA = "aiworker_repair_project8_20260921"
DESIRED_PROJECT_ID = f"creative:{CURRENT_STORE_ID}:project:8"
MISTAKEN_PROJECT_ID = f"creative:{CURRENT_STORE_ID}:project:25"
TASK_IDS = (
    "3decb3a5-987d-4092-8ce8-d3f5eb2e1c1e",
    "41400688-ccaf-43d7-a7fd-381c408f8fe7",
    "6618eab6-639f-4fc8-be7a-2932e2f74bb0",
    "7fa930a5-f957-4556-8cf7-7e4455ea551c",
    "bd86b0db-b067-4e8e-b12b-bc857d4f199f",
    "e2815ed6-764c-43bc-8b6b-cc3169ba39ef",
)
INSTANCE_IDS = ("project:8",)
EXPECTED_MEMBER_COUNT = 6
EXPECTED_EVENT_COUNT = 6


class RepairPreflightError(RuntimeError):
    pass


def _rows_by_ids(
    connection: Connection,
    table_name: str,
    ids: Sequence[str],
) -> list[dict[str, Any]]:
    statement = text(
        f'SELECT * FROM "{table_name}" WHERE id IN :row_ids ORDER BY id'
    ).bindparams(bindparam("row_ids", expanding=True))
    return [
        dict(row)
        for row in connection.execute(statement, {"row_ids": tuple(ids)})
        .mappings()
        .all()
    ]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RepairPreflightError(message)


def _preflight(connection: Connection) -> dict[str, Any]:
    store_id = connection.execute(
        text(
            "SELECT value FROM creative_store_metadata WHERE key = 'store_id'"
        )
    ).scalar_one()
    _expect(str(store_id) == CURRENT_STORE_ID, "unexpected creative store identity")

    projects = {
        int(row["id"]): dict(row)
        for row in connection.execute(
            text(
                "SELECT id, project_uid, name, created_by, workflow_task_id, "
                "creation_workflow_task_id FROM creative_projects "
                "WHERE id IN (8, 25) ORDER BY id"
            )
        ).mappings()
    }
    _expect(set(projects) == {8, 25}, "projects 8 and 25 must both exist")
    desired_uid = str(projects[8]["project_uid"])
    mistaken_uid = str(projects[25]["project_uid"])
    _expect(desired_uid != mistaken_uid, "project UUIDs must be distinct")
    linked_task_ids = {
        str(value)
        for value in (
            projects[8]["workflow_task_id"],
            projects[8]["creation_workflow_task_id"],
        )
        if value
    }
    _expect(
        linked_task_ids <= set(TASK_IDS),
        "project 8 points to a task outside the verified repair set",
    )

    membership_rows = connection.execute(
        text(
            f'SELECT id, project_id FROM "{BACKUP_SCHEMA}".project_members '
            "WHERE project_id LIKE '%:project:8' ORDER BY id"
        )
    ).mappings().all()
    membership_ids = tuple(str(row["id"]) for row in membership_rows)
    _expect(
        len(membership_ids) == EXPECTED_MEMBER_COUNT
        and len(set(membership_ids)) == EXPECTED_MEMBER_COUNT,
        "backup membership set is not the verified six-row set",
    )

    members = _rows_by_ids(connection, "project_members", membership_ids)
    _expect(len(members) == EXPECTED_MEMBER_COUNT, "current memberships are missing")
    _expect(
        all(
            row["project_id"] == MISTAKEN_PROJECT_ID
            and str(row["project_uid"]) == mistaken_uid
            for row in members
        ),
        "a membership no longer points to the expected mistaken project",
    )

    event_rows = connection.execute(
        text(
            "SELECT * FROM workflow_project_member_events "
            "WHERE membership_id IN :membership_ids ORDER BY id"
        ).bindparams(bindparam("membership_ids", expanding=True)),
        {"membership_ids": membership_ids},
    ).mappings().all()
    events = [dict(row) for row in event_rows]
    event_ids = tuple(str(row["id"]) for row in events)
    _expect(len(events) == EXPECTED_EVENT_COUNT, "member event set is not six rows")
    _expect(
        all(
            row["project_id"] == MISTAKEN_PROJECT_ID
            and str(row["project_uid"]) == mistaken_uid
            for row in events
        ),
        "a member event no longer points to the expected mistaken project",
    )

    tasks = _rows_by_ids(connection, "workflow_tasks", TASK_IDS)
    _expect(len(tasks) == len(TASK_IDS), "one or more verified tasks are missing")
    _expect(
        all(
            row["workflow_id"] == "project:8"
            and row["project_id"] == MISTAKEN_PROJECT_ID
            and str(row["project_uid"]) == mistaken_uid
            for row in tasks
        ),
        "a task no longer has the expected project-25 binding",
    )

    instances = _rows_by_ids(connection, "workflow_instances", INSTANCE_IDS)
    _expect(len(instances) == 1, "verified project:8 workflow instance is missing")
    instance = instances[0]
    _expect(
        instance["project_id"] == MISTAKEN_PROJECT_ID
        and instance["resource_id"] == MISTAKEN_PROJECT_ID
        and str(instance["project_uid"]) == mistaken_uid,
        "workflow instance no longer has the expected project-25 binding",
    )

    return {
        "desired_project": {
            "id": 8,
            "name": projects[8]["name"],
            "project_id": DESIRED_PROJECT_ID,
            "project_uid": desired_uid,
        },
        "mistaken_project": {
            "id": 25,
            "name": projects[25]["name"],
            "project_id": MISTAKEN_PROJECT_ID,
            "project_uid": mistaken_uid,
        },
        "membership_ids": membership_ids,
        "event_ids": event_ids,
        "task_ids": TASK_IDS,
        "instance_ids": INSTANCE_IDS,
    }


def _backup_rows(
    connection: Connection,
    table_name: str,
    row_ids: Sequence[str],
) -> None:
    statement = text(
        f'CREATE TABLE "{REPAIR_BACKUP_SCHEMA}"."{table_name}" AS '
        f'SELECT * FROM public."{table_name}" WHERE id IN :row_ids'
    ).bindparams(bindparam("row_ids", expanding=True))
    connection.execute(statement, {"row_ids": tuple(row_ids)})


def _update_explicit_rows(
    connection: Connection,
    table_name: str,
    row_ids: Sequence[str],
    desired_uid: str,
    mistaken_uid: str,
) -> None:
    statement = text(
        f'UPDATE public."{table_name}" '
        "SET project_id = :desired_project_id, "
        "project_uid = CAST(:desired_uid AS uuid) "
        "WHERE id IN :row_ids AND project_id = :mistaken_project_id "
        "AND project_uid = CAST(:mistaken_uid AS uuid)"
    ).bindparams(bindparam("row_ids", expanding=True))
    result = connection.execute(
        statement,
        {
            "row_ids": tuple(row_ids),
            "desired_project_id": DESIRED_PROJECT_ID,
            "desired_uid": desired_uid,
            "mistaken_project_id": MISTAKEN_PROJECT_ID,
            "mistaken_uid": mistaken_uid,
        },
    )
    _expect(
        result.rowcount == len(row_ids),
        f"{table_name} changed during repair; transaction rolled back",
    )


@app.command()
def main(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the verified repair; otherwise perform a read-only preflight.",
    ),
) -> None:
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    try:
        health = database.health()
        if not health.reachable or not health.schema_current:
            raise typer.BadParameter(
                "PostgreSQL target must be reachable and at Alembic head"
            )
        if not apply:
            with database.connection() as connection:
                plan = _preflight(connection)
            typer.echo(
                json.dumps(
                    {"mode": "preflight", "status": "ready", **plan},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
            return

        with database.transaction() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:repair_key))"),
                {"repair_key": "repair_project8_workflow_binding_20260921"},
            )
            plan = _preflight(connection)
            desired_uid = plan["desired_project"]["project_uid"]
            mistaken_uid = plan["mistaken_project"]["project_uid"]

            existing_backup = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :schema_name"
                ),
                {"schema_name": REPAIR_BACKUP_SCHEMA},
            ).first()
            _expect(existing_backup is None, "repair backup schema already exists")
            connection.execute(text(f'CREATE SCHEMA "{REPAIR_BACKUP_SCHEMA}"'))
            connection.execute(
                text(
                    f'CREATE TABLE "{REPAIR_BACKUP_SCHEMA}".creative_projects AS '
                    "SELECT * FROM public.creative_projects WHERE id IN (8, 25)"
                )
            )
            _backup_rows(connection, "project_members", plan["membership_ids"])
            _backup_rows(
                connection,
                "workflow_project_member_events",
                plan["event_ids"],
            )
            _backup_rows(connection, "workflow_tasks", plan["task_ids"])
            _backup_rows(connection, "workflow_instances", plan["instance_ids"])

            _update_explicit_rows(
                connection,
                "project_members",
                plan["membership_ids"],
                desired_uid,
                mistaken_uid,
            )
            _update_explicit_rows(
                connection,
                "workflow_project_member_events",
                plan["event_ids"],
                desired_uid,
                mistaken_uid,
            )
            _update_explicit_rows(
                connection,
                "workflow_tasks",
                plan["task_ids"],
                desired_uid,
                mistaken_uid,
            )
            instance_update = connection.execute(
                text(
                    "UPDATE public.workflow_instances "
                    "SET project_id = :desired_project_id, "
                    "resource_id = :desired_project_id, "
                    "project_uid = CAST(:desired_uid AS uuid), "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :instance_id "
                    "AND project_id = :mistaken_project_id "
                    "AND resource_id = :mistaken_project_id "
                    "AND project_uid = CAST(:mistaken_uid AS uuid)"
                ),
                {
                    "instance_id": INSTANCE_IDS[0],
                    "desired_project_id": DESIRED_PROJECT_ID,
                    "desired_uid": desired_uid,
                    "mistaken_project_id": MISTAKEN_PROJECT_ID,
                    "mistaken_uid": mistaken_uid,
                },
            )
            _expect(
                instance_update.rowcount == 1,
                "workflow instance changed during repair; transaction rolled back",
            )

        typer.echo(
            json.dumps(
                {
                    "mode": "apply",
                    "status": "completed",
                    "backup_schema": REPAIR_BACKUP_SCHEMA,
                    "members_repaired": len(plan["membership_ids"]),
                    "events_repaired": len(plan["event_ids"]),
                    "tasks_repaired": len(plan["task_ids"]),
                    "instances_repaired": len(plan["instance_ids"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        database.dispose()


if __name__ == "__main__":
    app()
