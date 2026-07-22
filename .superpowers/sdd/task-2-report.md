# Task 2 Report: Local Passwords, Setup, Sessions, and CSRF

## Status

Implemented local Argon2id passwords, first-run administrator setup, opaque
server-side sessions, CSRF validation, login/logout/current-user/password-change
APIs, and the login/setup pages.

## TDD Evidence

### Initial RED

Command:

```text
uv run pytest tests/test_identity_auth.py -q
```

Observed the expected collection failure before the auth modules existed:

```text
ModuleNotFoundError: No module named 'identity.api'
1 error in 0.59s
```

The first GREEN run exposed one implementation defect in the new
`disable_user` path: disabling a non-admin before first-run setup incorrectly
invoked the administrator-survival guard. The focused suite showed `1 failed,
14 passed`; the guard is now applied only when the target owns the enabled
`system_admin` role, and the final mutation is serialized with
`BEGIN IMMEDIATE`.

### Review Regression RED/GREEN

The password-policy review identified inconsistent ASCII/Unicode character
classification. A Unicode-letter regression test failed with:

```text
ValueError: 密码必须包含至少一个字母
1 failed in 0.58s
```

The policy now consistently uses `isalpha`, `isdigit`, and `isalnum`.

The session review identified that a random per-process CSRF derivation secret
invalidated persisted sessions after service recreation. The new cross-service
regression test failed against the original behavior with:

```text
assert restored is not None
1 failed in 0.83s
```

CSRF tokens are now derived with a stable, domain-separated HMAC over the
256-bit opaque session token. Only SHA-256 hashes of both tokens are persisted.

### Final GREEN

Command:

```text
uv run pytest tests/test_identity_auth.py tests/test_identity_repository.py -q
```

Result:

```text
.......................... [100%]
26 passed in 3.93s
```

Focused hooks:

```text
uv run pre-commit run --files identity/repository.py identity/password_service.py identity/session_service.py identity/api.py tests/test_identity_auth.py templates/auth/login.html templates/auth/setup.html
```

All applicable hooks passed, including file headers, whitespace, EOF, conflict,
case-conflict, large-file, and line-ending checks.

## Implementation

- `identity/password_service.py`: one shared Argon2id hasher; 10-128 character
  policy with letter, digit, and non-alphanumeric requirements; invalid hashes
  and passwords return `False` from verification.
- `identity/session_service.py`: 32-byte URL-safe opaque session tokens, hashed
  session/CSRF persistence, 12-hour default expiry, request IP/user-agent
  capture, live user/status/role lookup, expiry/revocation, and stable CSRF
  recovery for `/api/auth/me`.
- `identity/api.py`: first-run setup, uniform login failures, secure cookie
  configuration, logout, current user, password changes, per-route CSRF checks,
  and permission-derived landing routes.
- `identity/repository.py`: atomic user disablement, immediate session
  revocation, and last-active-system-administrator protection.
- `templates/auth/login.html` and `templates/auth/setup.html`: functional local
  account forms wired to the JSON APIs.
- `tests/test_identity_auth.py`: 18 auth tests covering password policy,
  token storage, metadata, restart persistence, setup, cookie flags, uniform
  login errors, landing routes, CSRF, logout, disabled users, expiry, password
  changes, pages, and last-admin protection.

## Self-Review

- Confirmed setup checks the invariant again inside a serialized SQLite
  transaction, preventing two sequential or concurrent first-admin inserts.
- Confirmed session lookup queries current user status and current enabled role
  grants on every request rather than caching a principal.
- Confirmed disabled users and password changes revoke all outstanding sessions.
- Confirmed the session cookie is `HttpOnly`, `SameSite=Lax`, path `/`, and
  conditionally `Secure`; tests cover both default and secure configurations.
- Confirmed missing users, incorrect passwords, and disabled users receive the
  same `用户名或密码错误` response.
- Confirmed no raw password, session token, or CSRF token is persisted.
- Independent review found no remaining Critical or Important issues after the
  Unicode-policy and cross-service-session fixes.
- Confirmed staging and commit contained only Task 2 identity, auth-template,
  and auth-test files; unrelated dirty-worktree changes were not touched.

## Commit

`225fc27` (`feat: add local account authentication`)

## Concerns

- Global CSRF middleware for non-identity write routes remains intentionally
  deferred to Task 4. Task 2 enforces CSRF on its authenticated write routes;
  login and first-run setup are intentionally exempt.
- Full application router registration in `WebGUI.py` is outside Task 2 and is
  planned with the later web integration work.

## Review Fix: Restore the Administrator Role During Setup

Review found that first-run setup trusted the existence of `system_admin` but
not its enabled state. Because default reseeding intentionally preserves
`roles.is_enabled`, a disabled administrator role produced a newly bound user
with no effective administrator role and left setup repeatable.

The regression test disables `system_admin` before setup. Against the original
implementation it produced this RED result:

```text
uv run pytest tests/test_identity_auth.py::test_setup_restores_disabled_system_admin_role -q
F [100%]
FAILED test_setup_restores_disabled_system_admin_role
assert [] == ['system_admin']
1 failed in 0.81s
```

Setup now performs role lookup, bound-user checks, conditional role
re-enablement, user creation, and role binding inside one `BEGIN IMMEDIATE`
transaction. Concurrent setup requests serialize so exactly one returns `201`
and the other returns `409`.

Additional coverage verifies that an incorrect CSRF token returns `403` without
revoking the valid session. The final focused result was:

```text
uv run pytest tests/test_identity_auth.py tests/test_identity_repository.py -q
............................ [100%]
28 passed in 4.32s
```

Focused pre-commit hooks for `identity/api.py` and
`tests/test_identity_auth.py` all passed. Independent re-review found no
Critical or Important issues in the fix.

Fix commit: `6576ab8` (`fix: restore admin role during setup`).

## Re-Review Fix: Keep an Existing Disabled Administrator Role Unchanged

Re-review found a security side effect in the first recovery fix: it enabled
`system_admin` before determining that an active, nondeleted user was already
bound to that role. An unauthenticated setup request could therefore receive
`409` while still reactivating an intentionally disabled administrator role.

The direct regression creates the initial administrator, disables the role,
then attempts setup again. Against the affected implementation it produced:

```text
uv run pytest tests/test_identity_auth.py::test_setup_does_not_reenable_role_with_existing_bound_admin -q
F [100%]
FAILED test_setup_does_not_reenable_role_with_existing_bound_admin
assert True is False
1 failed in 0.85s
```

Inside the serialized transaction, setup now first counts active, nondeleted
users bound to `system_admin` without considering the role's enabled state. If
one exists, setup returns `409` after a mutation-free transaction. Only when no
such bound user exists does the same transaction enable the role and create and
bind the first administrator. The regression also verifies that the user count
does not change. Disabled-role recovery with no bound administrator and
concurrent first-run setup remain covered.

Final verification:

```text
uv run pytest tests/test_identity_auth.py tests/test_identity_repository.py -q
............................. [100%]
29 passed in 4.60s
```

Focused pre-commit hooks passed. Independent re-review found no Critical or
Important issues and confirmed that the unauthenticated `409` path is
mutation-free.

Fix commit: `34368d5` (`fix: keep disabled admin roles unchanged`).
