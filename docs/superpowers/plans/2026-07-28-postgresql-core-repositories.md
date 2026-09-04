# PostgreSQL Core Repositories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove runtime SQLite and SQL translation dependencies from identity, session, audit, and workflow persistence by using SQLAlchemy Core against PostgreSQL.

**Architecture:** `system_database.schema` remains the table catalog. Repositories and services accept SQLAlchemy `Connection` objects and execute Core statements; PostgreSQL conflict handling uses `sqlalchemy.dialects.postgresql.insert`. The SQLite importer remains offline-only.

**Tech Stack:** Python 3.11, SQLAlchemy Core 2.x, PostgreSQL, Alembic, pytest.

## Global Constraints

- Runtime system database supports PostgreSQL only.
- SQLite remains an offline import source only.
- Preserve public return payloads, domain exception messages, audit payloads, and workflow transitions.
- Use `uv` for dependency and test commands.
- Do not modify unrelated dirty worktree files.

---

### Task 1: Establish PostgreSQL-only identity contracts

**Files:**
- Modify: `system_database/schema.py`
- Modify: `identity/repository.py`
- Test: `tests/test_postgres_identity_repository.py`

**Interfaces:** Produces `IdentityRepository.connect() -> Iterator[Connection]` and `transaction() -> Iterator[Connection]`.

- [ ] **Step 1: Write a failing repository-boundary test**

Assert that a repository created with `SystemDatabase` has a PostgreSQL backend and that the repository module contains neither `sqlite3` nor `system_database.compat`.

- [ ] **Step 2: Run the test**

Run `uv run pytest tests/test_postgres_identity_repository.py -v`. Expect failure because the repository imports `sqlite3` and `compat`.

- [ ] **Step 3: Replace SQLite plumbing**

Implement `connect()` by yielding `SystemDatabase.transaction()`'s `Connection`; remove `SCHEMA_SQL`, path initialization, PRAGMA/trigger helpers, `PostgreSQLCompatConnection`, and `BEGIN IMMEDIATE`. Keep schema changes in Alembic and declare functional unique indexes for lowercased usernames and roles.

- [ ] **Step 4: Re-run the test**

Run `uv run pytest tests/test_postgres_identity_repository.py -v`. Expect pass with `AIWORKER_TEST_SYSTEM_DATABASE_URL` or the established skip.

- [ ] **Step 5: Commit**

Commit only `system_database/schema.py`, `identity/repository.py`, and its focused test with message `refactor: make identity repository PostgreSQL Core only`.

### Task 2: Convert identity and session operations to Core

**Files:**
- Modify: `identity/repository.py`
- Modify: `identity/session_service.py`
- Modify: `identity/permission_service.py`
- Test: `tests/test_identity_repository.py`

**Interfaces:** Consumes `Connection` and system table objects; preserves `seed_defaults`, user/role methods, and session lifecycle APIs.

- [ ] **Step 1: Write failing behavior tests**

Cover case-insensitive `get_role_by_code("SYSTEM_ADMIN")`, idempotent user-role insert, and an authenticated session becoming invalid immediately after revoke.

- [ ] **Step 2: Run focused tests**

Run `uv run pytest tests/test_identity_repository.py -v`. Expect the current SQLite fixture path to fail under the new boundary.

- [ ] **Step 3: Implement Core operations**

Use `select(...).where(func.lower(roles.c.code) == role_code.casefold())`; issue user-role inserts with PostgreSQL `insert(...).on_conflict_do_nothing(index_elements=[user_roles.c.user_id, user_roles.c.role_id])`; use Core `insert`, `update`, `delete`, and joins for all user/role/session writes. Use `with_for_update()` for last-admin and single-project-owner checks. Translate SQLAlchemy `IntegrityError` only where the existing public error is contractual.

- [ ] **Step 4: Re-run focused tests**

Run `uv run pytest tests/test_identity_repository.py tests/test_postgres_identity_repository.py -v`. Expect pass or declared PostgreSQL skips.

- [ ] **Step 5: Commit**

Commit the three identity modules and focused tests with message `refactor: convert identity and session persistence to Core`.

### Task 3: Convert audit persistence and querying to Core

**Files:**
- Modify: `audit/repository.py`
- Modify: `audit/service.py`
- Test: `tests/test_audit_service.py`
- Test: `tests/test_audit_center.py`

**Interfaces:** Preserves `AuditRepository.record` and `query` contracts.

- [ ] **Step 1: Write a failing case-insensitive search test**

Record action `User.Created`, search with `user.created`, and assert that the row is returned with the existing payload shape.

- [ ] **Step 2: Run audit tests**

Run `uv run pytest tests/test_audit_service.py tests/test_audit_center.py -v`. Expect failure until SQLite-like predicates are replaced.

- [ ] **Step 3: Implement Core predicates and writes**

Build `select(audit_logs)` predicates with `func.lower(column).like(...)`, use `insert(audit_logs).values(**event)`, preserve pagination and joined actor fields, and use JSONB-native Python dict/list values. Replace all raw qmark SQL, `COLLATE NOCASE`, and SQLite annotations.

- [ ] **Step 4: Re-run audit tests**

Run `uv run pytest tests/test_audit_service.py tests/test_audit_center.py -v`. Expect pass or declared PostgreSQL skips.

- [ ] **Step 5: Commit**

Commit audit modules and tests with message `refactor: migrate audit persistence to Core`.

### Task 4: Convert workflow and approval persistence to Core

**Files:**
- Modify: `workflow/repository.py`
- Modify: `workflow/approval_service.py`
- Modify: `workflow/assignment_service.py`
- Test: `tests/test_role_workflow_migration.py`
- Test: `tests/test_selection_stage_snapshots.py`

**Interfaces:** Preserves task, project-member, submission, event, and approval APIs.

- [ ] **Step 1: Write failing idempotency and role tests**

Call `link_job(task_id, "job-1")` twice and assert one linked job. Also claim a task using an uppercased role code and assert the existing status transition.

- [ ] **Step 2: Run workflow tests**

Run `uv run pytest tests/test_role_workflow_migration.py tests/test_selection_stage_snapshots.py -v`. Expect failure until fixtures and repository use PostgreSQL.

- [ ] **Step 3: Implement Core workflow statements**

Use PostgreSQL conflict-ignore insert for `(workflow_task_jobs.task_id, job_id)`. Replace role comparisons with `func.lower(roles.c.code) == role_code.casefold()`. Convert task, membership, submission, event, and approval CRUD to Core; guard state-changing reads with `with_for_update()` and preserve current exception messages and event payloads.

- [ ] **Step 4: Re-run workflow tests**

Run `uv run pytest tests/test_role_workflow_migration.py tests/test_selection_stage_snapshots.py -v`. Expect pass or declared PostgreSQL skips.

- [ ] **Step 5: Commit**

Commit workflow modules and tests with message `refactor: migrate workflow persistence to Core`.

### Task 5: Remove compatibility code and verify the boundary

**Files:**
- Delete: `system_database/compat.py`
- Modify: `identity/api.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_system_database.py`

**Interfaces:** Produces no runtime `sqlite3` or `system_database.compat` imports in `identity`, `audit`, or `workflow`.

- [ ] **Step 1: Write a failing import-boundary test**

Recursively scan `identity`, `audit`, and `workflow` Python source and fail when it contains `sqlite3` or `system_database.compat`.

- [ ] **Step 2: Run the boundary test**

Run `uv run pytest tests/test_system_database.py -v`. Expect failure while compatibility imports exist.

- [ ] **Step 3: Remove compat and convert remaining direct callers**

Migrate `identity/api.py` direct persistence to Core or repository calls, import SQLAlchemy `Connection`/`IntegrityError` where required, delete `compat.py`, and retain `sqlite3` only in `system_database/sqlite_import.py`.

- [ ] **Step 4: Run final verification**

Run `uv run pytest tests/test_system_database.py tests/test_postgres_identity_repository.py tests/test_audit_service.py tests/test_role_workflow_migration.py -v`, then run `rg -n "system_database\.compat|import sqlite3" identity audit workflow`. Expect tests to pass or explicitly skip for absent PostgreSQL and no ripgrep results.

- [ ] **Step 5: Commit**

Commit only relevant migration files with message `refactor: remove SQLite compatibility from system repositories`.
