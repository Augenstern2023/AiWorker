# Task 1 Report: Identity Schema and Default RBAC Catalog

## Status

Implemented the local SQLite identity/RBAC foundation, permission catalog,
permission dependencies, default system roles, and administrator-survival guard.

## TDD Evidence

### RED

Command:

```text
uv run pytest tests/test_identity_repository.py -q
```

Observed expected collection failure before production files existed:

```text
ModuleNotFoundError: No module named 'identity'
1 error in 0.16s
```

### GREEN

Command:

```text
uv run pytest tests/test_identity_repository.py -q
```

Final result:

```text
...... [100%]
6 passed in 0.29s
```

Focused pre-commit verification:

```text
uv run pre-commit run --files pyproject.toml uv.lock identity/__init__.py identity/schema.py identity/permission_catalog.py identity/repository.py tests/test_identity_repository.py
```

All applicable hooks passed, including both Python file-header checks,
whitespace, EOF, conflict, case-conflict, and line-ending checks.

## Files

- `pyproject.toml`: added `argon2-cffi>=23.1.0,<26.0.0`.
- `uv.lock`: locked Argon2 and its binding dependencies.
- `identity/__init__.py`: exposed `IdentityRepository`.
- `identity/schema.py`: added the frozen `Principal`, status/scope types, scope
  ordering, and Pydantic user/role request and response models.
- `identity/permission_catalog.py`: added the approved permission strings,
  dependency closures, immutable role seeds, five system roles, and their
  default permission/scope pairs.
- `identity/repository.py`: added foreign-key-enabled SQLite initialization,
  fourteen required system tables, idempotent catalog/role seeding, role
  queries, permission queries, and last-active-admin protection.
- `tests/test_identity_repository.py`: covers table creation, exact default
  roles, admin wildcard scope, idempotent seeding, preservation of custom
  roles/bindings, dependency persistence, normalized username uniqueness, and
  last-admin protection.

## Self-Review

- Verified every required table name is created on repository initialization.
- Verified `seed_defaults()` can run repeatedly and uses SQLite upserts for
  permission labels and default role metadata.
- Verified custom roles and user-role bindings are not removed by reseeding.
- Verified `system_admin` receives only `('*', 'all')` on a fresh seed.
- Verified all seeded role permission codes and dependency codes exist in the
  permission catalog and all database writes run with foreign keys enabled.
- Verified the existing dirty workspace was not reverted or included in the
  Task 1 staging set.

## Commit

`0985027` (`feat: add identity and role persistence`)

The commit contains only the seven requested Task 1 source, test, and
dependency files. This report remains outside the commit as an SDD handoff
artifact.

## Concerns

- `uv lock` includes the resolver's current Python 3.14+ split for `cffi`
  alongside the requested Argon2 packages. This is generated lock metadata;
  Python 3.11 continues to resolve `cffi` 1.17.1.
- The task intentionally does not expose user/session mutation APIs yet; later
  identity tasks can build those operations on these tables and models.

## Review Fix: Exact System-Owned RBAC Synchronization

Review found that repeated seeding retained retired dependency edges and stale
grants on default roles. A regression test first injected both forms of stale
state and observed the expected RED result:

```text
uv run pytest tests/test_identity_repository.py -q
...F.... [100%]
FAILED test_seed_defaults_removes_stale_system_grants_and_dependencies
1 failed, 7 passed in 0.40s
```

`seed_defaults()` now replaces the system permission-dependency projection and
each default role's expanded permission set within the existing transaction.
It targets only catalog dependency edges and default-role grants; custom roles,
custom-role grants, and all user-role bindings remain intact. A separate
assertion confirms dependency closure grants such as `brief.read.approved` and
`outline.read.approved` are materialized. The permission-label comment now
accurately describes code strings as fallback display labels.

Final GREEN evidence before the review-fix commit:

```text
uv run pytest tests/test_identity_repository.py -q
........ [100%]
8 passed in 0.35s
```

Focused pre-commit hooks for the three modified Python files all passed. The
review fix is commit `e5a19f2` (`fix: synchronize default role grants`).
