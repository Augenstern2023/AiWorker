# Task 3 Report: Permission Closure, Guards, and Administration APIs

## Status

Implemented effective permission closure, current-session FastAPI guards,
server-queried data-scope checks, and the approved user, role, and permission
administration endpoints.

## TDD Evidence

### RED

Command:

```text
uv run pytest tests/test_identity_permissions.py tests/test_identity_admin_api.py -q
```

Observed the expected collection failure before the permission modules and
administration routes existed:

```text
ModuleNotFoundError: No module named 'identity.dependencies'
1 error in 0.63s
```

### GREEN

Focused command:

```text
uv run pytest tests/test_identity_permissions.py tests/test_identity_admin_api.py -q
```

Result:

```text
............... [100%]
15 passed in 3.92s
```

Identity regression command:

```text
uv run pytest tests/test_identity_repository.py tests/test_identity_auth.py tests/test_identity_permissions.py tests/test_identity_admin_api.py -q
```

Final result:

```text
............................................ [100%]
44 passed in 8.30s
```

Focused hooks:

```text
uv run pre-commit run --files identity/api.py identity/repository.py identity/permission_service.py identity/dependencies.py tests/test_identity_permissions.py tests/test_identity_admin_api.py
```

All applicable hooks passed, including both Python header checks, whitespace,
EOF, conflict, case-conflict, large-file, and line-ending checks.

## Implementation

- `identity/permission_service.py`: recomputes enabled roles and grants for
  every request, performs deterministic transitive dependency expansion,
  merges scopes by the approved ordering, normalizes wildcard access to
  `all`, and queries current task/project membership for scope decisions.
- `identity/dependencies.py`: provides `current_principal`,
  `require_permission`, `require_page`, and task, assigned-project,
  managed-project, and all-data scope resolvers with the approved 401/403
  responses.
- `identity/repository.py`: adds atomic user/role mutations, role-permission
  replacement with dependency materialization, live role binding updates, and
  serialized last-administrator enforcement.
- `identity/api.py`: adds every approved `/api/admin/users`,
  `/api/admin/roles`, and `/api/admin/permissions` route. All routes require
  system-administrator access; writes also require CSRF. Password reset revokes
  sessions, role clones copy expanded grants, and dependency removal is
  rejected while its parent permission remains.
- Focused tests cover multi-role unions, transitive dependencies, maximum
  scope, wildcard access, live session updates, server-side project membership,
  administrator-only APIs, role cloning, dependency persistence/removal,
  password reset, and the last-administrator invariant.

## Self-Review

- Confirmed permission checks never trust role or project membership stored in
  client claims; principals and memberships are read from SQLite at request
  time.
- Confirmed disabled roles disappear from effective permissions without
  recreating a session and wildcard grants always resolve to `all` scope.
- Confirmed permission dependencies inherit the strongest applicable scope and
  repeated or overlapping grants remain deterministic.
- Confirmed all administration writes pass both live wildcard authorization and
  CSRF validation.
- Confirmed last-administrator checks execute inside `BEGIN IMMEDIATE`
  transactions for user disablement and role replacement.
- Confirmed the commit contains only the six Task 3 source and test files; the
  pre-existing dirty workspace was neither staged nor reverted.

## Commit

`afcdd3f` (`feat: enforce customizable role permissions`)

## Concerns

- Downstream routers that use the generic dependencies must expose the shared
  `PermissionService` as `app.state.identity_permission_service`; the identity
  router binds it automatically for its own routes. Full application wiring is
  intentionally deferred to the integration task.
- Managed-project membership currently recognizes `owner`, `project_owner`,
  `manager`, and `project_manager`. The later workflow task should reuse these
  canonical stored role values rather than introduce a different spelling.

## Review Fix: Atomic User Patches and Idempotent Permission PUTs

Review found that `PATCH /api/admin/users/{user_id}` applied role replacement
in a separate transaction before profile/status changes. A request that added a
secondary role while retaining `system_admin`, then attempted to disable the
last administrator, returned `409` after the role change had already committed.
The regression snapshots the user and active session IDs, sends that combined
PATCH, and verifies the rejected operation leaves the profile, role bindings,
and session revocation state unchanged.

Review also found that a minimal permission PUT was not idempotent. The first
`beats.edit` request persisted its dependency closure, but the identical retry
misclassified those stored implicit dependencies as removals and returned
`422`. The regression sends the identical payload twice and requires two `200`
responses with this exact stable closure: `beats.edit`, `beats.read`,
`outline.read.approved`, and `page.writing`, all at `project` scope. A separate
regression omits a required dependency from an expanded-looking request and
confirms the server restores it rather than persisting an unsafe grant set.

Initial RED command:

```text
uv run pytest tests/test_identity_admin_api.py::test_required_dependency_is_restored_while_parent_remains tests/test_identity_admin_api.py::test_replacing_role_permissions_is_idempotent tests/test_identity_admin_api.py::test_rejected_admin_patch_leaves_profile_roles_and_sessions_unchanged -q
```

Result:

```text
FFF [100%]
3 failed in 1.28s
```

The repository now performs profile, status, optional role replacement,
last-administrator validation, and session revocation inside one
`BEGIN IMMEDIATE` transaction. `disable_user()` and `replace_user_roles()` use
the same atomic mutation path. Permission dependencies are consistently
implicit: every PUT expands the requested roots before replacing stored grants,
so retries are stable and required grants cannot actually be removed while the
parent remains.

Final verification:

```text
uv run pytest tests/test_identity_repository.py tests/test_identity_auth.py tests/test_identity_permissions.py tests/test_identity_admin_api.py -q
.............................................. [100%]
46 passed in 8.71s
```

Focused pre-commit hooks passed for `identity/api.py`,
`identity/repository.py`, and `tests/test_identity_admin_api.py`.

Review-fix commit: `4f28833` (`fix: make identity admin updates atomic`).
