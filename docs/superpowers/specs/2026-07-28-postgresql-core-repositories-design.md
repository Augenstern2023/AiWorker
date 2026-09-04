# PostgreSQL Core Repository Convergence

**Date:** 2026-07-28  
**Scope:** `identity`, `audit`, `session`, `workflow`, and `system_database`

## Decision

The runtime system database supports PostgreSQL only. Identity, audit, session,
and workflow repositories will use SQLAlchemy Core directly and will not use
the `system_database.compat.PostgreSQLCompatConnection` SQL translation layer.
SQLite remains supported solely as an offline import source through
`system_database.sqlite_import`.

## Architecture

`SystemDatabase` remains the owner of the SQLAlchemy engine and transaction
boundaries. Repository methods receive or acquire SQLAlchemy `Connection`
objects, execute Core statements, and consume `RowMapping` results.

Shared Core table definitions are the source of truth for the system tables.
Repositories import those definitions instead of owning SQLite-flavoured DDL or
raw SQL strings. PostgreSQL constraints continue to enforce primary keys,
foreign keys, status checks, and uniqueness.

## Repository Rules

- Replace `sqlite3.Connection` annotations and SQLite-specific connection code
  with SQLAlchemy `Connection`.
- Replace `COLLATE NOCASE` equality/search predicates with
  `func.lower(column) == func.lower(value)` or the equivalent Core predicate.
- Replace `INSERT OR IGNORE` with PostgreSQL
  `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing()`.
- Replace positional `?` parameters and raw SQL execution with Core
  `select`, `insert`, `update`, `delete`, joins, predicates, ordering, and
  explicit projections.
- Preserve existing repository return values, domain exceptions, audit payloads,
  session invalidation, and workflow state transitions.

## Compatibility Removal

All runtime imports and usage of `PostgreSQLCompatConnection` are removed from
the four repository domains. Once no runtime consumer remains, delete
`system_database/compat.py` and its focused tests. SQLite-specific repository
tests are replaced with PostgreSQL integration coverage gated by
`AIWORKER_TEST_SYSTEM_DATABASE_URL`.

## Migration and Validation

No production data transformation is required: the target database is already
PostgreSQL. Alembic schema definitions stay PostgreSQL-compatible and any
missing case-insensitive uniqueness required by current behavior is represented
through a PostgreSQL functional unique index on `lower(...)`.

Validation covers identity and role seeding, case-insensitive lookups and
uniqueness, session create/revoke, audit insert/query, workflow creation and
task updates, conflict-ignore behavior, and an import scan confirming the four
domains no longer depend on `compat` or `sqlite3`.

## Non-Goals

- Do not restore SQLite as a runtime system database.
- Do not change the offline SQLite-to-PostgreSQL import workflow.
- Do not redesign identity, audit, session, or workflow business rules.
