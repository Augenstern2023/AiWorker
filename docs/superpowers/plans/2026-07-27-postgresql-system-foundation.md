# PostgreSQL System Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested PostgreSQL 17.10 system-database foundation that later identity, workflow, audit, organization, navigation, and scheduling work can share without switching current WebGUI persistence yet.

**Architecture:** A new `system_database` package owns environment configuration, a synchronous SQLAlchemy Core engine, transaction scopes, and redacted health state. Alembic owns the PostgreSQL schema revision chain; the foundation exposes its current/head revision but does not migrate or switch existing SQLite data in this task.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x Core, Alembic 1.16+, psycopg 3, pytest, PostgreSQL 17.10.

## Global Constraints

- Use `uv`, never `pip`, for dependency and lockfile changes.
- PostgreSQL is local version 17.10 and the target URL comes only from `AIWORKER_SYSTEM_DATABASE_URL`.
- The current SQLite system database remains the runtime source of truth during this foundation task.
- System IDs remain `TEXT`; do not coerce existing values such as `role:<code>` or `workflow:system` to UUID database types.
- Never log or return a database password; health output contains a redacted URL only.
- All new Python files require the repository copyright header.
- Do not add runtime `CREATE TABLE`, `ALTER TABLE`, `PRAGMA`, or SQLite compatibility behavior to the new PostgreSQL path.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `system_database/config.py` | Parse, validate, and redact the system database URL. |
| `system_database/database.py` | Create the synchronous engine and provide connection/transaction scopes. |
| `system_database/health.py` | Define the immutable health response contract. |
| `system_database/schema.py` | SQLAlchemy metadata for the initial PostgreSQL system schema, using `TEXT` IDs. |
| `system_database/alembic/env.py` | Alembic engine/metadata configuration. |
| `system_database/alembic/versions/0001_system_schema_baseline.py` | Initial PostgreSQL schema revision. |
| `alembic.ini` | Alembic entry point; URL remains supplied by environment. |
| `tests/test_system_database.py` | Unit tests for settings, redaction, health, and transaction rollback. |
| `tests/test_system_database_alembic.py` | PostgreSQL integration tests for upgrade and revision status. |
| `pyproject.toml`, `uv.lock` | psycopg dependency. |

### Task 1: Add Configuration and Driver Dependency

**Files:**
- Create: `system_database/__init__.py`
- Create: `system_database/config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_system_database.py`

**Interfaces:**
- Produces: `SystemDatabaseSettings.from_environment(environ: Mapping[str, str] | None = None) -> SystemDatabaseSettings`
- Produces: `SystemDatabaseSettings.redacted_url -> str`
- Consumed by: `SystemDatabase` and Alembic environment configuration.

- [ ] **Step 1: Write the failing settings tests**

```python
def test_settings_require_a_postgresql_sqlalchemy_url(monkeypatch):
    monkeypatch.delenv("AIWORKER_SYSTEM_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="AIWORKER_SYSTEM_DATABASE_URL"):
        SystemDatabaseSettings.from_environment()


def test_settings_redact_database_password(monkeypatch):
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://app:secret@127.0.0.1:5432/aiworker_system",
    )
    settings = SystemDatabaseSettings.from_environment()
    assert settings.redacted_url == "postgresql+psycopg://app:***@127.0.0.1:5432/aiworker_system"
```

- [ ] **Step 2: Run the settings tests to verify they fail**

Run: `uv run pytest tests/test_system_database.py -k settings -v`

Expected: FAIL because `system_database` does not exist.

- [ ] **Step 3: Add the driver and minimal settings implementation**

```python
@dataclass(frozen=True)
class SystemDatabaseSettings:
    url: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "SystemDatabaseSettings":
        value = (environ or os.environ).get("AIWORKER_SYSTEM_DATABASE_URL", "").strip()
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("AIWORKER_SYSTEM_DATABASE_URL must use postgresql+psycopg://")
        return cls(url=value)
```

Add `psycopg[binary]>=3.2,<4` to `pyproject.toml`, then run `uv lock`.

- [ ] **Step 4: Run the settings tests to verify they pass**

Run: `uv run pytest tests/test_system_database.py -k settings -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock system_database/__init__.py system_database/config.py tests/test_system_database.py
git commit -m "feat: add PostgreSQL system database settings"
```

### Task 2: Implement the Shared SystemDatabase API

**Files:**
- Create: `system_database/database.py`
- Create: `system_database/health.py`
- Modify: `system_database/__init__.py`
- Test: `tests/test_system_database.py`

**Interfaces:**
- Consumes: `SystemDatabaseSettings`
- Produces: `SystemDatabase(settings: SystemDatabaseSettings)`
- Produces: `connection() -> Iterator[Connection]`, `transaction() -> Iterator[Connection]`, `health() -> DatabaseHealth`
- Consumed by: future identity/workflow/audit repository migration.

- [ ] **Step 1: Write failing transaction and health tests**

```python
def test_transaction_rolls_back_when_block_raises(database):
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute(text("CREATE TEMP TABLE rollback_probe (id integer)"))
            connection.execute(text("INSERT INTO rollback_probe VALUES (1)"))
            raise RuntimeError("rollback")

    with database.connection() as connection:
        assert connection.execute(text("SELECT to_regclass('pg_temp.rollback_probe')")).scalar_one() is None


def test_health_never_exposes_password(database):
    health = database.health()
    assert health.reachable is True
    assert "secret" not in health.database_url_redacted
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_system_database.py -k "transaction or health" -v`

Expected: FAIL because `SystemDatabase` and `DatabaseHealth` do not exist.

- [ ] **Step 3: Implement the smallest shared database API**

```python
class SystemDatabase:
    def __init__(self, settings: SystemDatabaseSettings) -> None:
        self.settings = settings
        self.engine = create_engine(settings.url, pool_pre_ping=True)

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.begin() as connection:
            yield connection
```

`health()` must execute `SELECT 1`, expose a redacted URL, and report the current Alembic revision once Task 3 adds the version table.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_system_database.py -k "transaction or health" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add system_database/database.py system_database/health.py system_database/__init__.py tests/test_system_database.py
git commit -m "feat: add shared system database transactions"
```

### Task 3: Establish Alembic and the TEXT-ID PostgreSQL Baseline

**Files:**
- Create: `alembic.ini`
- Create: `system_database/schema.py`
- Create: `system_database/alembic/env.py`
- Create: `system_database/alembic/versions/0001_system_schema_baseline.py`
- Test: `tests/test_system_database_alembic.py`

**Interfaces:**
- Consumes: `SystemDatabaseSettings`, `SystemDatabase`, SQLAlchemy `MetaData`
- Produces: `SYSTEM_METADATA`, Alembic revision `0001_system_schema_baseline`
- Consumed by: later SQLite import, identity/workflow/audit PostgreSQL adapters, governance revisions.

- [ ] **Step 1: Write failing Alembic baseline tests**

```python
def test_upgrade_head_creates_system_tables(postgres_url):
    run_alembic_upgrade(postgres_url)
    engine = create_engine(postgres_url)
    assert inspect(engine).has_table("users")
    assert inspect(engine).has_table("workflow_tasks")
    assert inspect(engine).has_table("audit_logs")


def test_baseline_keeps_text_primary_keys(postgres_url):
    run_alembic_upgrade(postgres_url)
    columns = inspect(create_engine(postgres_url)).get_columns("users")
    assert next(column for column in columns if column["name"] == "id")["type"].__class__.__name__ == "TEXT"
```

- [ ] **Step 2: Run the Alembic tests to verify they fail**

Run: `uv run pytest tests/test_system_database_alembic.py -v`

Expected: FAIL because the Alembic configuration and baseline revision do not exist.

- [ ] **Step 3: Define schema metadata and Alembic baseline**

Mirror the current system schema in `identity/repository.py` as SQLAlchemy `Table` definitions: users, roles, permissions, role/user mappings, sessions, project members, workflow tables, submissions, approval/events, and `audit_logs`. Keep all existing identifier columns as `Text`; use named indexes and PostgreSQL-compatible `ON DELETE`, checks, and unique constraints. The baseline contains no runtime DDL logic and no SQLite trigger/PRAGMA code.

- [ ] **Step 4: Run the Alembic tests to verify they pass**

Run: `uv run pytest tests/test_system_database_alembic.py -v`

Expected: PASS. Re-run the command against the same test database to prove `upgrade head` is idempotent.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini system_database/schema.py system_database/alembic tests/test_system_database_alembic.py
git commit -m "feat: add PostgreSQL system schema baseline"
```

### Task 4: Add Readiness Reporting Without Switching Runtime Persistence

**Files:**
- Modify: `system_database/database.py`
- Modify: `tests/test_system_database.py`
- Modify: `tests/test_system_database_alembic.py`

**Interfaces:**
- Consumes: Alembic `ScriptDirectory` and `alembic_version`
- Produces: `DatabaseHealth(reachable, current_revision, head_revision, schema_current, database_url_redacted)`
- Consumed by: the later WebGUI startup integration and `LocalScheduleService` gate.

- [ ] **Step 1: Write failing revision-state tests**

```python
def test_health_reports_schema_current_after_upgrade(database):
    run_alembic_upgrade(database.settings.url)
    health = database.health()
    assert health.reachable is True
    assert health.schema_current is True
    assert health.current_revision == health.head_revision
```

- [ ] **Step 2: Run the readiness test to verify it fails**

Run: `uv run pytest tests/test_system_database.py -k schema_current -v`

Expected: FAIL because health does not compare current and head revisions.

- [ ] **Step 3: Implement revision comparison**

Use Alembic `ScriptDirectory.from_config()` to resolve the configured head revision and query `alembic_version` with SQLAlchemy Core. Missing tables or an unavailable database must return `reachable=False` or `schema_current=False` without exposing credentials.

- [ ] **Step 4: Run focused and combined verification**

Run: `uv run pytest tests/test_system_database.py tests/test_system_database_alembic.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add system_database/database.py tests/test_system_database.py tests/test_system_database_alembic.py
git commit -m "feat: report PostgreSQL schema readiness"
```

## Plan Self-Review

- Scope is intentionally limited to the PostgreSQL public foundation; `WebGUI.py`, identity/workflow/audit query migration, SQLite data import, departments, navigation, audit export, and scheduler are deferred to their dependent stages.
- Every task has a failing-test command before production implementation and a passing-test command after it.
- `SystemDatabase`, `DatabaseHealth`, `SystemDatabaseSettings`, and `SYSTEM_METADATA` have a single definition and consistent later consumers.
