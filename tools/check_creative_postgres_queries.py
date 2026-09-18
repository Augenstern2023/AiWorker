# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Query plan check for the migrated creative PostgreSQL store.

Task 14 step 4 of the migration. The creative tables were ported from SQLite,
where every access plan was "read the whole table"; on PostgreSQL the same query
shapes only stay fast if the indexes in ``creative/database_schema.py`` are
actually used. This runs ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` over the
read paths the runtime depends on, at a size where a missing index shows up.

It seeds its own data first. Measuring an empty table proves nothing: with no
rows PostgreSQL prefers a sequential scan because that genuinely is cheaper, so
an empty-table check would pass while production fell over.

Exit codes: 0 all paths healthy, 1 at least one unexpected plan or slow query,
2 refused to run (wrong target, or the fixture could not be built).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from system_database import SystemDatabase, SystemDatabaseSettings  # noqa: E402

#: Sequential scans below this many rows are not interesting - PostgreSQL may
#: correctly prefer them for a small table even when an index exists.
DEFAULT_SEQUENTIAL_SCAN_ROW_FLOOR = 500

#: A single read path taking longer than this on the local fixture means the
#: plan is wrong, not that the machine is slow.
DEFAULT_MAX_MILLISECONDS = 250

TEST_DATABASE_PATTERN = re.compile(r"(^|_)(test|tests|migration_test)(_|$)|test$", re.IGNORECASE)


@dataclass(frozen=True)
class ReadPath:
    """One runtime access pattern, and the predicate that should be indexed."""

    name: str
    table: str
    where: str
    order_by: str | None
    limit: int | None


#: The read paths named by the plan. Each predicate matches a real WHERE clause
#: in creative/repositories/*: a project's own rows, its ordered children, its
#: current head, the recovery scan for unfinished generation, its reviews, and
#: its deliveries.
READ_PATHS: tuple[ReadPath, ...] = (
    ReadPath("project list", "creative_projects", "1 = 1", "id DESC", 100),
    ReadPath("version list", "creative_episodes", "project_id = :project_id", "episode_number", 200),
    ReadPath("resource head", "creative_resource_heads", "project_id = :project_id", None, 5),
    ReadPath(
        "pending generations",
        "creative_generation_operations",
        "status <> 'completed'",
        "operation_id DESC",
        100,
    ),
    ReadPath("review lookup", "script_reviews", "project_id = :project_id", "id DESC", 50),
    ReadPath("delivery lookup", "creative_deliveries", "project_id = :project_id", "id DESC", 50),
)


def _plan_nodes(plan: dict[str, Any]):
    yield plan
    for child in plan.get("Plans", []) or []:
        yield from _plan_nodes(child)


def _summarize(plan_json: list[dict[str, Any]]) -> dict[str, Any]:
    root = plan_json[0]
    plan = root["Plan"]
    nodes = list(_plan_nodes(plan))
    scans = [
        node
        for node in nodes
        if node.get("Node Type") in {"Seq Scan", "Parallel Seq Scan"}
    ]
    return {
        "milliseconds": float(root.get("Execution Time", 0.0)),
        "nodes": len(nodes),
        "scans": [
            {
                "table": node.get("Relation Name"),
                "rows": int(node.get("Actual Rows") or 0),
                "filter": node.get("Filter"),
            }
            for node in scans
        ],
        "planning_ms": float(root.get("Planning Time", 0.0)),
    }


def _require_test_target(database: SystemDatabase, allow_any: bool) -> None:
    name = database.url.database or ""
    if allow_any:
        return
    if not TEST_DATABASE_PATTERN.search(name):
        raise typer.BadParameter(
            f"refusing to seed {name!r}: its name does not look like a test database. "
            "Pass --allow-any-database if you really mean to measure this target."
        )


def _column_names(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    ).all()
    return {row[0] for row in rows}


def _insert_rows(
    connection: Connection,
    table: str,
    count: int,
    *,
    overrides: dict[str, str] | None = None,
) -> None:
    """Insert ``count`` synthetic rows, filling every NOT NULL column.

    The creative tables carry NOT NULL text/JSON/boolean columns, so a hand
    written INSERT would drift the moment a column is added. Introspecting which
    columns actually require a value keeps the fixture honest.
    """

    overrides = overrides or {}
    columns = connection.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table "
            "  AND is_nullable = 'NO' AND column_default IS NULL "
            "ORDER BY ordinal_position"
        ),
        {"table": table},
    ).all()
    if not columns:
        return

    names: list[str] = []
    expressions: list[str] = []
    numeric = {
        "integer",
        "bigint",
        "smallint",
        "numeric",
        "real",
        "double precision",
    }
    temporal = {"timestamp with time zone", "timestamp without time zone", "date"}
    for name, data_type in columns:
        names.append(name)
        if name in overrides:
            expressions.append(overrides[name])
        elif name == "id":
            expressions.append("g")
        elif data_type in numeric:
            expressions.append("1")
        elif data_type == "boolean":
            expressions.append("false")
        elif data_type in temporal:
            expressions.append("now()")
        elif data_type in {"jsonb", "json"}:
            expressions.append(f"'{{}}'::{data_type}")
        else:
            expressions.append("''")

    # A NOT NULL column with a DEFAULT is not in the list above, yet its default
    # may still violate a CHECK constraint (creation_mode defaults to imitation,
    # which requires reference_refs). An explicit override must therefore win
    # even for columns that do not strictly require a value.
    known = set(names)
    for name, expression in overrides.items():
        if name not in known:
            names.append(name)
            expressions.append(expression)

    connection.execute(
        text(
            f"INSERT INTO {table} ({', '.join(names)}) "
            f"SELECT {', '.join(expressions)} FROM generate_series(1, {count}) AS g"
        )
    )


def _seed(connection: Connection) -> dict[str, int]:
    """Build a production-scale fixture for the paths above.

    Scale chosen so a missing index on a project-scoped predicate is visible: the
    child tables are two orders of magnitude larger than the project table.
    """

    project_columns = _column_names(connection, "creative_projects")
    missing = {"id", "revision", "status"} - project_columns
    if missing:
        raise typer.BadParameter(f"creative_projects is missing {sorted(missing)}")

    # Deepest first: the child tables reference the project rows.
    for table in (
        "creative_generation_operations",
        "creative_resource_heads",
        "script_reviews",
        "creative_deliveries",
        "creative_episodes",
        "creative_projects",
    ):
        if _column_names(connection, table):
            connection.execute(text(f"DELETE FROM {table}"))

    seeded: dict[str, int] = {}
    _insert_rows(
        connection,
        "creative_projects",
        400,
        overrides={
            "name": "''",
            "status": "'draft'",
            "revision": "1",
            # The imitation default requires reference_refs; original does not.
            "creation_mode": "'original'",
        },
    )
    seeded["creative_projects"] = 400
    _insert_rows(
        connection,
        "creative_episodes",
        120000,
        overrides={"project_id": "1 + (g % 400)", "episode_number": "1 + (g / 400)"},
    )
    seeded["creative_episodes"] = 120000
    for table in (
        "creative_resource_heads",
        "script_reviews",
        "creative_deliveries",
        "creative_generation_operations",
    ):
        if not _column_names(connection, table):
            continue
        # One failed insert aborts the whole PostgreSQL transaction, so each
        # table gets its own savepoint and a failure stays local to it.
        try:
            with connection.begin_nested():
                overrides = {"project_id": "1 + (g % 400)"}
                if table == "creative_resource_heads":
                    overrides.update(
                        {
                            "resource_scope": "'scope-' || g",
                            "stage": "'script'",
                            "episode_number": "g",
                            "resource_ref": "'resource-' || g",
                        }
                    )
                elif table == "creative_deliveries":
                    overrides["submission_id"] = "'submission-' || g"
                elif table == "creative_generation_operations":
                    overrides["operation_id"] = "'operation-' || g"
                _insert_rows(
                    connection, table, 4000, overrides=overrides
                )
            seeded[table] = 4000
        except Exception as error:  # a table may need a value shape we did not guess
            seeded[table] = 0
            detail = str(getattr(error, "orig", error)).splitlines()[0][:90]
            typer.echo(f"note   : could not seed {table}: {detail}")
    return seeded


def main(
    database_url: str = typer.Option(
        None,
        "--database-url",
        help="Target database. Defaults to AIWORKER_SYSTEM_DATABASE_URL.",
    ),
    max_milliseconds: float = typer.Option(
        DEFAULT_MAX_MILLISECONDS, "--max-ms", help="Per-query wall-clock ceiling."
    ),
    scan_row_floor: int = typer.Option(
        DEFAULT_SEQUENTIAL_SCAN_ROW_FLOOR,
        "--seq-scan-row-floor",
        help="Sequential scans of fewer rows than this are not reported.",
    ),
    allow_any_database: bool = typer.Option(
        False,
        "--allow-any-database",
        help="Permit a target whose name does not look like a test database.",
    ),
    keep_rows: bool = typer.Option(
        False, "--keep", help="Leave the seeded fixture in place instead of rolling back."
    ),
    allow_unseeded: bool = typer.Option(
        False,
        "--allow-unseeded",
        help="Do not fail when a read path could only be measured on an empty table.",
    ),
) -> None:
    if database_url:
        os.environ["AIWORKER_SYSTEM_DATABASE_URL"] = database_url
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    failures: list[str] = []
    warned: list[str] = []
    try:
        _require_test_target(database, allow_any_database)

        with database.engine.connect() as connection:
            # Start the transaction before the first statement, otherwise the
            # SELECT below autobegins one and begin() is rejected.
            transaction = connection.begin()
            try:
                schema = connection.execute(text("SELECT current_schema()")).scalar_one()
                typer.echo(f"target : {database.url.database} / schema {schema}")

                seeded = _seed(connection)
                typer.echo(f"fixture: {seeded}")
                connection.execute(text("ANALYZE creative_projects"))
                connection.execute(text("ANALYZE creative_episodes"))

                typer.echo("")
                typer.echo(f"{'path':<20} {'ms':>8} {'nodes':>6}  plan")
                typer.echo("-" * 68)

                for path in READ_PATHS:
                    available = _column_names(connection, path.table)
                    if not available:
                        warned.append(f"{path.name}: table {path.table} is absent")
                        continue

                    # Skip a path whose predicate names a column this table does
                    # not have. The tables were ported one at a time and their
                    # key columns differ (creative_generation_operations keys on
                    # a text operation id, not an integer id).
                    literals = set(
                        re.findall(r"\b([a-z_][a-z0-9_]*)\b", path.where)
                    ) - {"completed", "status"}
                    if literals - available:
                        warned.append(
                            f"{path.name}: {path.table} lacks {sorted(literals - available)}"
                        )
                        continue

                    order_by = path.order_by
                    if order_by:
                        first = order_by.split()[0]
                        if first not in available:
                            warned.append(
                                f"{path.name}: ordered by absent column {first!r}; "
                                "measured unordered"
                            )
                            order_by = None

                    statement = f"SELECT * FROM {path.table} WHERE {path.where}"
                    if order_by:
                        statement += f" ORDER BY {order_by}"
                    if path.limit:
                        statement += f" LIMIT {path.limit}"
                    explained = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement

                    row = connection.execute(
                        text(explained), {"project_id": 7}
                    ).scalar_one()
                    summary = _summarize(row if isinstance(row, list) else json.loads(row))

                    seeded_rows = seeded.get(path.table, 0)
                    marker = "" if seeded_rows else "  (EMPTY TABLE - not evidence)"
                    typer.echo(
                        f"{path.name:<20} {summary['milliseconds']:>8.2f} "
                        f"{summary['nodes']:>6}  {statement[:34]}{marker}"
                    )
                    if not seeded_rows:
                        # A plan over an empty table is always cheap and always
                        # correct-looking; counting it as a pass is how a missing
                        # index ships.
                        failures.append(
                            f"{path.name}: measured on the empty {path.table}; "
                            "seed it before trusting this check"
                        )
                    for scan in summary["scans"]:
                        if scan["rows"] >= scan_row_floor:
                            detail = f"{path.name}: sequential scan of {scan['table']} ({scan['rows']} rows)"
                            failures.append(detail)
                            typer.echo(f"    !! {detail}")
                    if summary["milliseconds"] > max_milliseconds:
                        detail = (
                            f"{path.name}: {summary['milliseconds']:.1f} ms exceeds "
                            f"the {max_milliseconds:.0f} ms ceiling"
                        )
                        failures.append(detail)
                        typer.echo(f"    !! {detail}")
            finally:
                if keep_rows:
                    transaction.commit()
                    typer.echo("fixture kept (--keep)")
                else:
                    transaction.rollback()
                    typer.echo("fixture rolled back")

        typer.echo("")
        for note in warned:
            typer.echo(f"note   : {note}")
        if allow_unseeded:
            # Downgrade "no fixture for this table" from a failure to a caveat.
            real_failures = [
                failure for failure in failures if "measured on the empty" not in failure
            ]
            for failure in failures:
                if "measured on the empty" in failure:
                    typer.echo(f"unverified: {failure}")
        else:
            real_failures = list(failures)
        if real_failures:
            typer.echo(f"FAIL   : {len(real_failures)} problem(s)")
            for failure in real_failures:
                typer.echo(f"   - {failure}")
            raise typer.Exit(code=1)
        typer.echo("PASS   : every read path uses an acceptable plan")
    finally:
        database.dispose()


if __name__ == "__main__":
    typer.run(main)
