# Task 4 Report: Audit Service and Security Middleware

## Status

Implemented normalized audit persistence and filtering, identity-event audit
writes, the protected administrator audit API, request IDs, authenticated-write
CSRF enforcement, and explicit-origin CORS configuration.

## TDD Evidence

### RED

Initial command:

```text
uv run pytest tests/test_audit_service.py tests/test_web_security.py -q
```

Observed the expected missing audit and security infrastructure:

```text
ModuleNotFoundError: No module named 'audit'
ImportError: cannot import name 'configure_cors' from 'WebGUI'
2 errors during collection
```

After the audit service and Web security middleware were independently green,
the identity integration tests were run before changing the identity router:

```text
uv run pytest tests/test_audit_service.py::test_identity_mutations_and_login_results_are_audited tests/test_audit_service.py::test_admin_audit_api_requires_permission_and_applies_filters -q
FF
TypeError: build_identity_router() takes 2 positional arguments but 3 were given
```

### GREEN

Focused command:

```text
uv run pytest tests/test_audit_service.py tests/test_web_security.py -q
........... [100%]
11 passed, 1 warning in 4.28s
```

Identity and security regression command:

```text
uv run pytest tests/test_identity_repository.py tests/test_identity_auth.py tests/test_identity_permissions.py tests/test_identity_admin_api.py tests/test_audit_service.py tests/test_web_security.py -q
......................................................... [100%]
57 passed, 1 warning in 12.59s
```

The warning is the pre-existing SQLAlchemy 2.x `declarative_base()` deprecation
from `database/models.py`; the focused tests emitted no new warnings.

Focused hooks:

```text
uv run pre-commit run --files audit/__init__.py audit/repository.py audit/service.py audit/api.py identity/api.py WebGUI.py tests/test_audit_service.py tests/test_web_security.py
```

All configured header, whitespace, EOF, conflict, case-conflict, large-file,
and line-ending checks passed.

## Implementation

- `audit/repository.py` evolves the existing `audit_logs` table in place and
  stores normalized actors, immutable role snapshots, targets, bounded
  before/after summaries, request metadata, nullable reasons, results, and
  timestamps. Queries support actor, action, target, project, result, time,
  limit, and offset filters.
- `audit/service.py` provides `AuditService.record(...) -> str`, recursively
  redacts sensitive key families, bounds persisted JSON summaries to 8192
  bytes, and derives request ID, client IP, and user agent from FastAPI
  requests.
- `audit/api.py` exposes `GET /api/admin/audit`, protected by `audit.read` with
  the existing wildcard administrator bypass.
- `identity/api.py` keeps the audit service optional for backward
  compatibility and records setup, successful and failed login, logout,
  password change/reset, user create/update, role binding, role create/update/
  clone, and role-permission replacement events with before/after state.
- `WebGUI.py` assigns validated or generated request IDs before logging,
  includes IDs in access/error logs and response headers, enforces CSRF for
  authenticated unsafe requests except login/setup, removes wildcard
  credentialed CORS, and reads explicit origins from
  `AIWORKER_ALLOWED_ORIGINS`.

## Self-Review

- Confirmed secret redaction is recursive across mappings and sequences and
  covers password, token, secret, cookie, activation code, and API key names.
- Confirmed audit summaries remain valid JSON after bounding instead of
  persisting invalid string truncations.
- Confirmed failed login records never persist submitted passwords and retain a
  null actor without changing the uniform authentication response.
- Confirmed CSRF is applied only to live authenticated sessions, so expired or
  disabled sessions continue to reach normal 401 authorization behavior.
- Confirmed request IDs are installed as the outer middleware before logging,
  and cross-origin credential responses are absent unless an exact origin list
  is configured.
- Confirmed the commit contains only the eight Task 4 source and test files;
  unrelated dirty-worktree changes were neither staged nor reverted.

## Commit

`65d27d9` (`feat: add security audit and csrf protection`)

## Initial Concerns (Resolved In Review Fix)

- `install_security_middleware(app, session_service)` places the session
  service on application state. The global `WebGUI` app installs the middleware
  now, while Task 5's planned identity-router wiring must provide the shared
  session and permission services and include the identity/audit routers.
- Audit writes use the shared SQLite database but occur immediately after the
  corresponding identity transaction. A process failure in that narrow gap
  could commit the mutation without its audit event; making both writes one
  transaction would require a broader repository transaction interface.

## Review Fix: Production Wiring And Atomic Audit Writes

Review found that the real `WebGUI` app installed CSRF middleware without a
session service, did not register the identity or audit routers, referenced an
unassignable `audit.read` permission, omitted legacy `resource_id` backfill,
and committed identity changes before their audit events.

### RED

The new focused regressions failed before the fixes:

```text
uv run pytest tests/test_audit_service.py::test_legacy_resource_id_is_backfilled_as_target_id tests/test_audit_service.py::test_audit_read_permission_is_seeded_and_assignable tests/test_audit_service.py::test_user_mutation_rolls_back_when_atomic_audit_insert_fails -q
FFF [100%]
target_id was None
ValueError: 未知权限: audit.read
expected active, got disabled
```

The production probe also failed during collection:

```text
uv run pytest tests/test_web_security.py::test_production_webgui_wires_shared_identity_security_and_routes -q
ImportError: cannot import name 'SYSTEM_DB_PATH' from 'WebGUI'
```

### GREEN

Focused Task 4 verification:

```text
uv run pytest tests/test_audit_service.py tests/test_web_security.py -q
............... [100%]
15 passed, 1 warning in 4.64s
```

Identity, security, and WebGUI regression verification:

```text
uv run pytest tests/test_identity_repository.py tests/test_identity_auth.py tests/test_identity_permissions.py tests/test_identity_admin_api.py tests/test_audit_service.py tests/test_web_security.py tests/test_webgui_ui_changes.py -q
........................................................................ [ 67%]
.................................. [100%]
106 passed, 1 warning in 13.16s
```

Focused pre-commit hooks passed for all ten review-fix files.

### Changes

- `WebGUI.py` now creates and seeds
  `.runtime/system/system.db`, builds one shared password, session, permission,
  and audit service set, exposes those services on `app.state`, passes the real
  session service to CSRF middleware, and registers both identity and audit
  routers.
- `audit.read` is now part of the seeded permission catalog and can be assigned
  to custom roles. The global audit endpoint continues to allow wildcard
  administrators.
- Audit migration now copies legacy `resource_id` values into `target_id`.
- Identity, session, permission, and audit repositories can participate in one
  caller-owned SQLite connection. Every audited identity mutation now runs
  under one `BEGIN IMMEDIATE` transaction and writes its audit event through
  that same connection. Audit insertion failure therefore rolls back the user,
  role, session, and password mutation.
- The rollback regression injects an audit insert failure during user disable,
  requires HTTP 500, and verifies both the active user state and empty audit
  result remain unchanged.

Review-fix commit: `ad7a2a0` (`fix: wire atomic production security audit`).

## Remaining Concerns

None specific to the reviewed Task 4 scope. The test warning remains the
pre-existing SQLAlchemy `declarative_base()` deprecation in
`database/models.py`.
