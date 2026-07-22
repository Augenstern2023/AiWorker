# Task 6 Report: Role Workspace Split

## Status

PASS

- Commit: `5b1b5b3 refactor: split web gui into role workspaces`
- Task files only were staged and committed. Pre-existing dirty creative, analysis, data, docs, and test changes remain untouched in the worktree.

## Implementation

- Routed every selection, planning, writing, review, and admin page to its own Jinja template, including dynamic project and episode context.
- Replaced the legacy `templates/index.html` monolith with a compatibility redirect through `/`.
- Preserved the latest monolith UI behavior in domain templates, partials, and external JS modules: ranks, topic lifecycle actions, transcript/download progress restoration, paid-item filtering, breakdown field guides/editor, project generation progress, pause/retry, autosized editors, and admin operations.
- Removed all `currentTab` dependencies and replaced cross-page transitions with real routes.
- Added `static/js/common/api.js` as the single fetch boundary. It reads the CSRF meta token, sets `X-CSRF-Token` on writes, redirects `401` to `/login`, and surfaces `403` as `无权执行此操作`.
- Injected the session CSRF token and external session/logout behavior through the base layout.
- Kept business modules separated: selection logic lives in selection scripts, admin operations in the admin workspace, and shared project-stage behavior in `common/creative.js` for planning/writing/delivery pages.
- Added real admin list rendering for users, roles, and audit events. Review pages intentionally remain route-aware placeholders for Tasks 7-10.

## TDD Evidence

### RED

Command:

```text
uv run pytest tests/test_role_page_content.py -q
```

Initial result: `5 failed, 1 passed`.

Expected failures confirmed missing role templates, missing `common/api.js`, and the legacy `currentTab` monolith still present.

### GREEN

Required focused command after commit:

```text
uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q
```

Result: `51 passed, 1 warning in 2.52s`.

Additional verification before commit:

```text
uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py tests/test_page_authorization.py -q
```

Result: `64 passed, 1 warning in 8.32s`.

```text
uv run pre-commit run --files <task files>
```

Result: all hooks passed.

All new JavaScript files passed `node --check`. A Node composition smoke test also verified that selection topics, selection ranks, creative project, and admin pages receive only their loaded domain methods.

## Self-Review

- Verified no `currentTab` remains in role templates or workspace JS.
- Verified raw `fetch()` exists only in `common/api.js`; domain scripts use `aiworkerApi.request()`.
- Verified role templates contain external scripts only and do not contain unrelated role workspaces.
- Verified authenticated role routing and forbidden-page behavior through `tests/test_page_authorization.py`.
- `git diff --cached --check` passed before commit.

## Concerns

- The single warning is the existing SQLAlchemy `declarative_base()` deprecation warning from `database/models.py`.
- Review task/approval/version views are placeholders by design; their repositories and workflows are scheduled for later tasks.
- No live browser walkthrough was run; verification covered template separation, route rendering/authorization, JS syntax/module composition, and the focused UI regression suite.

## Review Remediation

Commit: `a2a253b fix: complete role workspace navigation and admin flows`

All five blocking review findings were addressed:

1. Selection dashboard initialization now loads only topic data; rank initialization runs exclusively on `/selection/ranks`, matching loaded modules.
2. Planning projects use real links to `/planning/projects/{id}/directions`; successful creation redirects there, and directions, Brief, and characters pages have explicit stage navigation.
3. Every outline row links to its episode Beats route. Locking outlines no longer writes the obsolete monolith `step = 'beats'` state.
4. Red-fruit configuration URL and activation-code controls and write methods were removed from selection HTML/JS and moved to `/admin/config`.
5. Admin identity pages now implement user create/edit, enable/disable, password reset, multi-role binding, role create/edit, clone, enable/disable, and permission/data-scope editing against the existing APIs.

### Remediation RED

```text
uv run pytest tests/test_role_page_content.py -q
```

Initial review-regression result: `5 failed, 6 passed`. Each failure reproduced one blocking finding before implementation.

### Remediation GREEN

Post-commit required focused suite:

```text
uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q
```

Result: `56 passed, 1 warning in 2.70s`.

Broader verification:

```text
uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py tests/test_page_authorization.py tests/test_identity_admin_api.py -q
```

Result: `81 passed, 1 warning in 12.06s`.

All changed files passed targeted pre-commit hooks. Every workspace JS file passed `node --check`, and selection HTML/JS contains no configuration URL, activation-code, or settings-write behavior.

## Re-Review Remediation

Commit: `250572e fix: tighten admin pages and workspace errors`

The three re-review blockers were fixed:

1. `/admin/deliveries` remains guarded by `page.admin.delivery`; every other admin route now requires the wildcard system-admin grant. Navigation is filtered per item, so a delivery-only role sees only 成果交付 and lands on that route.
2. The base layout now owns a visible, dismissible permission alert. The API helper dispatches `aiworker:permission-denied` and returns the `403` response instead of rejecting the Alpine handler promise, eliminating the permission-denial unhandled-rejection path.
3. `/selection/dramas` no longer includes the unrelated selection modal bundle. A page-specific contract test verifies all callable Alpine expressions resolve against the scripts loaded by that route.

### Re-Review RED

```text
uv run pytest \
  tests/test_page_authorization.py::test_delivery_only_role_cannot_open_other_admin_pages \
  tests/test_role_page_content.py::test_base_layout_globally_surfaces_permission_denials_without_rejection \
  tests/test_role_page_content.py::test_selection_drama_page_only_references_loaded_methods -q
```

Initial result: `3 failed`. The failures reproduced cross-admin authorization, the missing permission alert/rejection contract, and the drama modal dependency mismatch.

### Re-Review GREEN

Post-commit focused/page/admin suite:

```text
uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py \
  tests/test_page_authorization.py tests/test_identity_admin_api.py -q
```

Result: `84 passed, 1 warning in 14.63s`.

All workspace JavaScript files passed `node --check`. A direct Node contract check confirmed a `403` dispatches the permission event, returns without rejection, and carries the visible `无权执行此操作` message. Targeted pre-commit hooks passed.
