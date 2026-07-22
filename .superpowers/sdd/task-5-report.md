# Task 5 Report: Role-Specific Page Router and Navigation

## Status

Implemented permission-derived navigation, guarded role page routes, shared
Jinja layout/error/task templates, authenticated landing redirects, and
production WebGUI registration.

## TDD Evidence

### RED

Initial focused run:

```text
uv run pytest tests/test_page_authorization.py -q
ModuleNotFoundError: No module named 'web'
```

The first implementation run then exposed the missing runtime dependency:

```text
AssertionError: jinja2 must be installed to use Jinja2Templates
10 errors
```

A later session-isolation regression failed against the initial standalone
last-page cookie implementation:

```text
expected /selection/dashboard, got /selection/topics
1 failed
```

The task-center last-route regression also failed before `/tasks` was recorded:

```text
expected /tasks, got /selection/topics
1 failed
```

### GREEN

Focused task and identity regression command:

```text
uv run pytest tests/test_page_authorization.py tests/test_identity_auth.py -q
.................................. [100%]
34 passed in 9.55s
```

Production WebGUI regression checks also passed:

```text
uv run pytest tests/test_webgui_ui_changes.py -q
45 passed, 1 pre-existing SQLAlchemy warning
```

## Implementation

- `NavigationService` owns the five navigation groups, labels, icons,
  permissions, default routes, and the complete static/dynamic page catalog.
- Last-page state is bounded and keyed by the authenticated session ID, so a
  later login in the same browser cannot inherit another session's route.
- The page router applies `require_page` to every role route, redirects
  anonymous page requests to `/login`, and renders an HTML 403 response for
  authenticated denials.
- `/` rechecks the remembered route against current effective permissions and
  otherwise applies selection, planning, writing, review, admin priority.
- The base layout renders the current user, permitted navigation, logout,
  Bootstrap assets, and extensible style/content/script blocks.
- `WebGUI.py` configures `Jinja2Templates` and mounts the page router without
  removing existing business APIs. Jinja2 was added to `pyproject.toml` and
  `uv.lock` because bare FastAPI does not install that optional dependency.

## Self-Review

- All cataloged static and dynamic routes are exercised for each of the five
  default roles, not only one representative route per group.
- Cross-user logout/login behavior verifies session isolation for remembered
  routes; permission revocation verifies stale routes are rejected.
- Anonymous `/`, `/tasks`, and role pages are covered, along with explicit HTML
  403 behavior and the required writer/admin examples.
- Focused pre-commit hooks cover every Task 5 source, test, template, and
  dependency file.

## Commit

`e764b9b` (`feat: add role-specific page routing`)

Independent review approved the session-isolation fix and complete role/route
authorization matrix with no remaining blocking findings.

## Concerns

None specific to Task 5. Remembered routes are intentionally process-local and
bounded; they reset on server restart rather than adding a database migration
outside this task's ownership.
