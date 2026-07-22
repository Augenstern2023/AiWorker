# Role Responsibility and Approval Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local users, customizable RBAC, role-specific page routes, human task pools, a unified submit-and-approve version state machine, and complete auditability to the existing selection-to-Beats workflow.

**Architecture:** Keep the existing creative and `JobManager` domains, add a dedicated `.runtime/system/system.db` for identity, sessions, RBAC, workflow, approvals, and audits, and add `creative_stage_versions` to the creative database for immutable stage aggregates. FastAPI dependencies enforce page, API, project, and version access; server-rendered role routes replace the monolithic `templates/index.html`, while machine jobs remain separate and link to human tasks.

**Tech Stack:** Python 3.11, FastAPI 0.110.2, Pydantic 2.5.2, SQLite, Argon2id, Jinja2, Alpine.js, Bootstrap 5, pytest, Playwright, `uv`.

## Global Constraints

- Use local accounts only; do not add enterprise WeChat, Feishu, LDAP, OIDC, or any external identity provider.
- Keep exactly five default roles: selection operator, creative planner, screenwriter, project owner, and system administrator.
- The system administrator is the highest-privilege role and uses wildcard permission `*`; do not add a separate super-administrator role.
- Merge drama analyst capabilities into selection operator.
- Do not create data-operations or production-operations roles; their duties belong to system administrator.
- Automatic assignment means role-derived permissions and permission dependencies, not workload-based selection of a concrete user.
- Human tasks enter a role pool and are claimed or manually assigned.
- Submitted versions are immutable; only approved versions can unlock the next workflow node.
- Returned submissions remain immutable and produce a new draft version.
- Unauthorized pages must not appear in navigation and direct access must return `403`.
- Split role pages into separate routes and templates; do not continue adding business sections to `templates/index.html`.
- Keep human workflow tasks separate from `JobManager` machine jobs and link them by ID.
- System-administrator overrides must be audited, but audit `reason` is nullable.
- Preserve all existing dy/hg data, creative history, generated files, and unrelated dirty-worktree changes.
- Do not implement full episode prose, storyboards, external video generation, or ZIP production packages in this plan.
- Use `uv`, not `pip`.
- New Python files must carry the repository copyright header.

---

## File Map

### New backend modules

```text
identity/
  __init__.py
  schema.py                 # User, role, session, permission DTOs
  repository.py             # system.db identity/RBAC persistence and seed data
  password_service.py       # Argon2id hash/verify
  session_service.py        # opaque server-side sessions and CSRF tokens
  permission_catalog.py     # default roles, permission codes, dependencies
  permission_service.py     # effective permission and dependency closure
  dependencies.py           # FastAPI current-user/page/API guards
  api.py                    # login, setup, user, role, permission APIs

workflow/
  __init__.py
  schema.py                 # task, workflow, approval request/response models
  repository.py             # task pools, assignments, events, approvals, job links
  state_machine.py          # human task transition validation
  transition_service.py     # create-next-task and review orchestration
  api.py                    # /api/workflow and /api/versions routes

audit/
  __init__.py
  repository.py             # audit persistence and filtering
  service.py                # normalized audit records
  api.py                    # /api/admin/audit

web/
  __init__.py
  page_router.py            # login, tasks, role page routes
  navigation_service.py     # permission-derived navigation

creative/
  version_service.py        # unified draft/submit/approve/return/fork behavior

templates/
  layouts/base.html
  auth/login.html
  auth/setup.html
  common/403.html
  common/tasks.html
  selection/dashboard.html
  selection/ranks.html
  selection/topics.html
  selection/dramas.html
  selection/breakdowns.html
  planning/projects.html
  planning/directions.html
  planning/brief.html
  planning/characters.html
  writing/projects.html
  writing/outlines.html
  writing/beats.html
  review/projects.html
  review/tasks.html
  review/approvals.html
  review/versions.html
  admin/users.html
  admin/roles.html
  admin/config.html
  admin/data.html
  admin/analytics.html
  admin/jobs.html
  admin/deliveries.html
  admin/audit.html

static/js/
  common/api.js
  common/session.js
  common/tasks.js
  selection/ranks.js
  selection/topics.js
  selection/dramas.js
  selection/breakdowns.js
  planning/projects.js
  planning/brief.js
  planning/characters.js
  writing/outlines.js
  writing/beats.js
  review/tasks.js
  review/approvals.js
  admin/users.js
  admin/roles.js
  admin/jobs.js
  admin/deliveries.js
  admin/audit.js

scripts/
  migrate_role_workflow.py
```

### Existing files to modify

```text
pyproject.toml
uv.lock
WebGUI.py
creative/api.py
creative/repository.py
creative/schema.py
creative/lifecycle_service.py
runtime/job_manager.py
templates/index.html             # reduced to a temporary compatibility redirect, then removed
tests/test_webgui_ui_changes.py
tests/test_creative_phase4.py
tests/test_job_manager.py
```

---

### Task 1: System Database, Permission Catalog, and Default Roles

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `identity/__init__.py`
- Create: `identity/schema.py`
- Create: `identity/permission_catalog.py`
- Create: `identity/repository.py`
- Test: `tests/test_identity_repository.py`

**Interfaces:**
- Produces: `IdentityRepository(db_path: str | Path)`.
- Produces: `IdentityRepository.seed_defaults() -> None`.
- Produces: `DEFAULT_ROLES`, `PERMISSIONS`, and `PERMISSION_DEPENDENCIES`.
- Produces role codes: `selection_operator`, `creative_planner`, `screenwriter`, `project_owner`, `system_admin`.

- [ ] **Step 1: Add the Argon2 dependency and failing schema tests**

Add `argon2-cffi>=23.1.0,<26.0.0` to `pyproject.toml`, run `uv lock`, and create tests that initialize a temporary repository and assert all system tables and default roles exist:

```python
def test_identity_repository_seeds_exact_default_roles(tmp_path: Path):
    repository = IdentityRepository(tmp_path / "system.db")
    repository.seed_defaults()

    assert {role["code"] for role in repository.list_roles()} == {
        "selection_operator",
        "creative_planner",
        "screenwriter",
        "project_owner",
        "system_admin",
    }
    admin = repository.get_role_by_code("system_admin")
    assert admin["is_system"] is True
    assert repository.list_role_permissions(admin["id"]) == [
        {"permission_code": "*", "data_scope": "all"},
    ]
```

Also assert these tables exist: `users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `permission_dependencies`, `sessions`, `project_members`, `workflow_instances`, `workflow_tasks`, `workflow_task_jobs`, `workflow_task_events`, `approval_records`, and `audit_logs`.

- [ ] **Step 2: Run the tests and verify the repository is missing**

Run: `uv run pytest tests/test_identity_repository.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'identity'`.

- [ ] **Step 3: Define permission and role records**

In `identity/schema.py`, define immutable records used across later tasks:

```python
@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role_codes: tuple[str, ...]
    permissions: frozenset[str]
    data_scope: str
    session_id: str

    @property
    def is_system_admin(self) -> bool:
        return "system_admin" in self.role_codes or "*" in self.permissions
```

Define `UserStatus = Literal["active", "disabled"]`, `DATA_SCOPE_ORDER = {"task": 1, "project": 2, "managed_projects": 3, "all": 4}`, and Pydantic request/response models for users and roles.

- [ ] **Step 4: Implement the permission catalog**

In `identity/permission_catalog.py`, define the exact permission strings from the approved design. Include page permissions and these dependency closures:

```python
PERMISSION_DEPENDENCIES = {
    "breakdown.edit": {"breakdown.read", "topic.read", "page.selection"},
    "brief.edit": {"brief.read", "project.read.assigned", "page.planning"},
    "character.edit": {"character.read", "brief.read.approved", "page.planning"},
    "outline.edit": {
        "outline.read", "brief.read.approved", "character.read.approved", "page.writing",
    },
    "beats.edit": {"beats.read", "outline.read.approved", "page.writing"},
    "version.approve": {"version.read.managed", "version.compare", "page.review"},
    "workflow.assign": {"workflow.read.managed", "project.read.managed", "page.review"},
    "export.create": {"approved_content.read", "page.admin.delivery"},
}
```

Define each default role as a role seed with `code`, `name`, `description`, `is_system`, and permission/scope pairs. `system_admin` must contain only `("*", "all")`.

- [ ] **Step 5: Implement idempotent SQLite schema and seed logic**

Implement `IdentityRepository._init_schema()` with foreign keys enabled, unique normalized usernames, soft-disabled users, server-side sessions, workflow tables, and audit tables. `seed_defaults()` must use `INSERT ... ON CONFLICT DO UPDATE` for permission labels and role descriptions without deleting custom roles or user-role bindings.

Protect the system administrator invariant with repository methods:

```python
def assert_admin_survives(self, user_id: str, removing_role_id: str | None = None) -> None:
    if self.count_active_system_admins(excluding_user_id=user_id, removing_role_id=removing_role_id) < 1:
        raise ValueError("系统必须至少保留一个启用的系统管理员")
```

- [ ] **Step 6: Run focused tests and commit**

Run: `uv run pytest tests/test_identity_repository.py -q`

Expected: PASS.

Commit:

```bash
git add pyproject.toml uv.lock identity tests/test_identity_repository.py
git commit -m "feat: add identity and role persistence"
```

---

### Task 2: Local Passwords, Setup, Sessions, and CSRF

**Files:**
- Create: `identity/password_service.py`
- Create: `identity/session_service.py`
- Create: `identity/api.py`
- Create: `templates/auth/login.html`
- Create: `templates/auth/setup.html`
- Test: `tests/test_identity_auth.py`

**Interfaces:**
- Consumes: `IdentityRepository` and `Principal` from Task 1.
- Produces: `PasswordService.hash(password) -> str` and `verify(hash, password) -> bool`.
- Produces: `SessionService.create_session(user_id, request) -> (raw_token, csrf_token)`.
- Produces: `build_identity_router(repository, session_service) -> APIRouter`.

- [ ] **Step 1: Write failing password and session tests**

Cover Argon2 hashes, invalid passwords, setup-only-first-admin, login, logout, disabled users, session expiry, and last-admin protection:

```python
def test_first_setup_creates_system_admin_and_disables_setup(client):
    response = client.post("/api/auth/setup", json={
        "username": "admin", "display_name": "管理员", "password": "Admin-Strong-123!",
    })
    assert response.status_code == 201
    assert response.json()["user"]["role_codes"] == ["system_admin"]
    assert client.post("/api/auth/setup", json={
        "username": "other", "display_name": "其他", "password": "Admin-Strong-456!",
    }).status_code == 409
```

```python
def test_disabled_user_sessions_are_revoked(client, repository):
    login = client.post("/api/auth/login", json={"username": "writer", "password": "Writer-123!"})
    assert login.status_code == 200
    repository.disable_user(login.json()["user"]["id"])
    assert client.get("/api/auth/me").status_code == 401
```

- [ ] **Step 2: Run tests and verify auth services are absent**

Run: `uv run pytest tests/test_identity_auth.py -q`

Expected: FAIL because password, session, and auth APIs do not exist.

- [ ] **Step 3: Implement password validation and Argon2id**

Use `argon2.PasswordHasher` with a single service instance. Enforce 10-128 characters, at least one letter, one digit, and one non-alphanumeric character. Return the same `用户名或密码错误` response for missing users and bad passwords.

- [ ] **Step 4: Implement opaque sessions and CSRF tokens**

Generate a 32-byte URL-safe session token, store only `sha256(token)` in `sessions`, and set `aiworker_session` with `HttpOnly`, `SameSite=Lax`, and configurable `Secure`. Generate a per-session CSRF token and return it from `/api/auth/me`; write requests must send it as `X-CSRF-Token`.

Session creation must record IP and user agent, default to 12 hours, and reject disabled users on every lookup rather than trusting a cached principal.

- [ ] **Step 5: Implement setup/login/logout/me endpoints and pages**

Implement:

```text
GET  /login
GET  /setup
POST /api/auth/setup
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/change-password
```

`/setup` is available only when no active system administrator exists. A successful login sets the session cookie and returns the CSRF token and permission-derived landing route.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_identity_auth.py -q`

Expected: PASS.

Commit:

```bash
git add identity templates/auth tests/test_identity_auth.py
git commit -m "feat: add local account authentication"
```

---

### Task 3: Permission Closure, FastAPI Guards, and User/Role APIs

**Files:**
- Create: `identity/permission_service.py`
- Create: `identity/dependencies.py`
- Modify: `identity/api.py`
- Test: `tests/test_identity_permissions.py`
- Test: `tests/test_identity_admin_api.py`

**Interfaces:**
- Consumes: catalog, repository, sessions, and `Principal`.
- Produces: `PermissionService.principal_for_session(raw_token) -> Principal | None`.
- Produces: `require_permission(code, scope_resolver=None)` FastAPI dependency.
- Produces: `require_page(code)` FastAPI dependency.

- [ ] **Step 1: Write failing effective-permission tests**

Test multi-role unions, transitive dependencies, maximum scope, immediate role updates, wildcard access, and removal protection:

```python
def test_role_permission_dependencies_are_transitive(repository, permission_service):
    role = repository.create_role("custom_writer", "定制编剧", "")
    repository.replace_role_permissions(role["id"], [("beats.edit", "project")])
    user = repository.create_user("writer", "编剧", HASH)
    repository.replace_user_roles(user["id"], [role["id"]])

    principal = permission_service.principal_for_user(user["id"], "session-1")
    assert {"beats.edit", "beats.read", "outline.read.approved", "page.writing"} <= principal.permissions
    assert principal.data_scope == "project"
```

- [ ] **Step 2: Write failing admin API authorization tests**

Assert only system administrators can list users, create users, clone roles, and replace permissions. Assert the last administrator cannot be disabled or stripped of `system_admin`.

- [ ] **Step 3: Run tests and verify guards are missing**

Run: `uv run pytest tests/test_identity_permissions.py tests/test_identity_admin_api.py -q`

Expected: FAIL with missing permission service and admin routes.

- [ ] **Step 4: Implement permission dependency closure and scope merge**

Use a deterministic stack/visited traversal over `PERMISSION_DEPENDENCIES`. If `*` is present, return wildcard access with `all` scope. Compute on every authenticated request so role changes take effect without recreating the user or session.

- [ ] **Step 5: Implement current-user and permission dependencies**

Provide:

```python
def require_permission(code: str):
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_system_admin and code not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")
        return principal
    return dependency
```

Add scope helpers for task, assigned project, managed project, and all data. Scope checks must query current membership rather than accept project IDs from client claims.

- [ ] **Step 6: Implement user, role, and permission APIs**

Implement the approved endpoints under `/api/admin/users`, `/api/admin/roles`, and `/api/admin/permissions`. `PUT /roles/{id}/permissions` must expand dependencies before persistence and reject removal of required dependencies. `PUT /users/{id}/roles` must enforce the last-admin invariant.

- [ ] **Step 7: Run tests and commit**

Run: `uv run pytest tests/test_identity_permissions.py tests/test_identity_admin_api.py -q`

Expected: PASS.

Commit:

```bash
git add identity tests/test_identity_permissions.py tests/test_identity_admin_api.py
git commit -m "feat: enforce customizable role permissions"
```

---

### Task 4: Audit Service and Security Middleware

**Files:**
- Create: `audit/__init__.py`
- Create: `audit/repository.py`
- Create: `audit/service.py`
- Create: `audit/api.py`
- Modify: `identity/api.py`
- Modify: `WebGUI.py`
- Test: `tests/test_audit_service.py`
- Test: `tests/test_web_security.py`

**Interfaces:**
- Consumes: `IdentityRepository`, `Principal`, and request metadata.
- Produces: `AuditService.record(...) -> str`.
- Produces: `GET /api/admin/audit` with filters.

- [ ] **Step 1: Write failing audit and CSRF middleware tests**

Cover login events, user disable, role changes, nullable reason, request ID, before/after summaries, and CSRF rejection:

```python
def test_system_admin_override_audit_allows_empty_reason(audit_service, admin):
    event_id = audit_service.record(
        actor=admin,
        action="version.force_unlock",
        target_type="creative_stage_version",
        target_id="42",
        before={"status": "submitted"},
        after={"status": "draft"},
        reason=None,
        result="success",
    )
    assert audit_service.get(event_id)["reason"] is None
```

- [ ] **Step 2: Run tests and verify audit infrastructure is absent**

Run: `uv run pytest tests/test_audit_service.py tests/test_web_security.py -q`

Expected: FAIL with missing audit modules and CSRF enforcement.

- [ ] **Step 3: Implement normalized audit writes and queries**

Store actor role snapshots, action, target, bounded JSON summaries, request ID, IP, user agent, nullable reason, result, and timestamp. Redact keys matching `password`, `token`, `secret`, `cookie`, `activation_code`, and `api_key` before persistence.

- [ ] **Step 4: Wire request IDs, CSRF, and same-origin behavior**

Add a request ID middleware before logging. Remove `allow_origins=["*"]` with credentials from `WebGUI.py`; default to same-origin and accept an explicit `AIWORKER_ALLOWED_ORIGINS` list only when configured. Enforce CSRF for authenticated `POST`, `PUT`, `PATCH`, and `DELETE`, excluding `/api/auth/login` and first-run setup.

- [ ] **Step 5: Audit identity mutations and expose admin audit API**

Record successful and failed logins, logout, password changes, user state changes, role binding, and role permission changes. Protect `/api/admin/audit` with `audit.read` or wildcard permission.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_audit_service.py tests/test_web_security.py -q`

Expected: PASS.

Commit:

```bash
git add audit identity/api.py WebGUI.py tests/test_audit_service.py tests/test_web_security.py
git commit -m "feat: add security audit and csrf protection"
```

---

### Task 5: Role-Specific Page Router and Navigation

**Files:**
- Create: `web/__init__.py`
- Create: `web/navigation_service.py`
- Create: `web/page_router.py`
- Create: `templates/layouts/base.html`
- Create: `templates/common/403.html`
- Create: `templates/common/tasks.html`
- Modify: `WebGUI.py`
- Test: `tests/test_page_authorization.py`

**Interfaces:**
- Consumes: `require_page`, `Principal`, and Jinja2 templates.
- Produces: `build_page_router(templates, navigation_service) -> APIRouter`.
- Produces: `NavigationService.items_for(principal) -> list[dict]`.

- [ ] **Step 1: Write failing page visibility and direct-access tests**

Parameterize the five default roles and assert exact route visibility. Include these checks:

```python
def test_screenwriter_cannot_open_selection_page(writer_client):
    response = writer_client.get("/selection/ranks", follow_redirects=False)
    assert response.status_code == 403
    assert "今日榜单" not in writer_client.get("/writing/projects").text

def test_system_admin_sees_all_navigation(admin_client):
    html = admin_client.get("/admin/users").text
    for label in ("选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"):
        assert label in html
```

- [ ] **Step 2: Run tests and verify all content still comes from index.html**

Run: `uv run pytest tests/test_page_authorization.py -q`

Expected: FAIL because role page routes and navigation do not exist.

- [ ] **Step 3: Implement the navigation catalog**

Map each navigation group to one required page permission. System administrators receive every item; other principals receive only matching items. Keep route labels and icons in Python so templates do not infer permissions independently.

- [ ] **Step 4: Implement base layout and page guards**

`base.html` must render current user, allowed navigation, logout, a content block, Bootstrap assets, and per-page script blocks. Add explicit `403` handling. `/` redirects to the last still-authorized route stored in session, otherwise to role priority: selection, planning, writing, review, admin.

- [ ] **Step 5: Register page routing in WebGUI.py**

Mount `/static`, configure `Jinja2Templates`, include identity and page routers, and change the legacy `/` handler to the permission-derived redirect. Do not remove business APIs in this task.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_page_authorization.py tests/test_identity_auth.py -q`

Expected: PASS.

Commit:

```bash
git add web templates/layouts templates/common WebGUI.py tests/test_page_authorization.py
git commit -m "feat: add role-specific page routing"
```

---

### Task 6: Split Selection, Planning, Writing, Review, and Admin Pages

**Files:**
- Create: `templates/selection/dashboard.html`
- Create: `templates/selection/ranks.html`
- Create: `templates/selection/topics.html`
- Create: `templates/selection/dramas.html`
- Create: `templates/selection/breakdowns.html`
- Create: `templates/planning/projects.html`
- Create: `templates/planning/directions.html`
- Create: `templates/planning/brief.html`
- Create: `templates/planning/characters.html`
- Create: `templates/writing/projects.html`
- Create: `templates/writing/outlines.html`
- Create: `templates/writing/beats.html`
- Create: `templates/review/projects.html`
- Create: `templates/review/tasks.html`
- Create: `templates/review/approvals.html`
- Create: `templates/review/versions.html`
- Create: `templates/admin/users.html`
- Create: `templates/admin/roles.html`
- Create: `templates/admin/config.html`
- Create: `templates/admin/data.html`
- Create: `templates/admin/analytics.html`
- Create: `templates/admin/jobs.html`
- Create: `templates/admin/deliveries.html`
- Create: `templates/admin/audit.html`
- Create: `static/js/common/api.js`
- Create: `static/js/common/session.js`
- Create: `static/js/common/tasks.js`
- Create: `static/js/selection/ranks.js`
- Create: `static/js/selection/topics.js`
- Create: `static/js/selection/dramas.js`
- Create: `static/js/selection/breakdowns.js`
- Create: `static/js/planning/projects.js`
- Create: `static/js/planning/brief.js`
- Create: `static/js/planning/characters.js`
- Create: `static/js/writing/outlines.js`
- Create: `static/js/writing/beats.js`
- Create: `static/js/review/tasks.js`
- Create: `static/js/review/approvals.js`
- Create: `static/js/admin/users.js`
- Create: `static/js/admin/roles.js`
- Create: `static/js/admin/jobs.js`
- Create: `static/js/admin/deliveries.js`
- Create: `static/js/admin/audit.js`
- Modify: `templates/index.html`
- Modify: `tests/test_webgui_ui_changes.py`
- Test: `tests/test_role_page_content.py`

**Interfaces:**
- Consumes: role routes, base layout, existing APIs, and existing Alpine behavior.
- Produces: standalone role pages with no dependency on a global `currentTab`.

- [ ] **Step 1: Write failing template separation tests**

Assert each route template contains only its domain, scripts are external, and the legacy monolith no longer contains business sections:

```python
def test_role_templates_do_not_share_unrelated_workspaces():
    selection = Path("templates/selection/topics.html").read_text("utf-8")
    writing = Path("templates/writing/beats.html").read_text("utf-8")
    assert "今日爆款" in selection
    assert "单集节拍表" not in selection
    assert "单集节拍表" in writing
    assert "用户管理" not in writing

def test_legacy_index_is_only_a_compatibility_redirect():
    html = Path("templates/index.html").read_text("utf-8")
    assert "currentTab ===" not in html
    assert "创作项目" not in html
```

- [ ] **Step 2: Run tests and verify the monolith fails them**

Run: `uv run pytest tests/test_role_page_content.py -q`

Expected: FAIL because role templates and scripts do not exist.

- [ ] **Step 3: Extract shared authenticated fetch behavior**

Create `static/js/common/api.js` with one request helper that reads the CSRF token injected by `base.html`, sets `X-CSRF-Token` for writes, converts `401` to `/login`, and surfaces `403` as a permission message. Do not duplicate fetch wrappers in role files.

- [ ] **Step 4: Move selection pages without behavior regressions**

Move current rank, today-hit, topic-library, drama-search/download, transcript-preview, breakdown-generation, and breakdown-editor UI into selection templates and scripts. Replace tab changes with real links. Preserve current progress restoration, paid-item skipping, multi-episode download, dynamic breakdown field guide, and lifecycle labels.

- [ ] **Step 5: Move planning and writing pages**

Split project listing, directions/Brief, characters/relationships, outlines, and Beats into their routes. Each detail route reads `project_id` and `episode_number` from the URL or template context. Preserve autosized fields, save indicators, job progress, retry, pause, and version history display.

- [ ] **Step 6: Move review and admin operational pages**

Create review placeholders backed by real route context for tasks/approvals implemented in later tasks. Move crawler control, config editor, data management, analytics, machine jobs, and current stage exports under admin templates. Do not expose these fragments in non-admin HTML.

- [ ] **Step 7: Reduce index.html and update template tests**

Replace `templates/index.html` with a minimal compatibility page that redirects through `/`. Update old tests to inspect the new domain template or external JS file rather than expecting every string in one file.

- [ ] **Step 8: Run focused UI tests and commit**

Run: `uv run pytest tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q`

Expected: PASS.

Commit:

```bash
git add templates static/js tests/test_role_page_content.py tests/test_webgui_ui_changes.py
git commit -m "refactor: split web gui into role workspaces"
```

---

### Task 7: Human Task Repository and Role Pools

**Files:**
- Create: `workflow/__init__.py`
- Create: `workflow/schema.py`
- Create: `workflow/repository.py`
- Create: `workflow/state_machine.py`
- Create: `workflow/api.py`
- Test: `tests/test_workflow_tasks.py`
- Test: `tests/test_workflow_api.py`

**Interfaces:**
- Consumes: system database, principals, roles, project membership, permission guards.
- Produces: `WorkflowRepository.create_task(...) -> dict`.
- Produces: `WorkflowStateMachine.transition(task, action, actor) -> str`.
- Produces: `/api/workflow/tasks` claim/assign/reassign/start/cancel endpoints.
- Produces: `/api/workflow/projects/{project_id}/members` list/add/remove endpoints.

- [ ] **Step 1: Write failing task-pool and transition tests**

Cover pool creation, role eligibility, claim races, assignment, reassignment, project-member add/remove, managed-project scope, cancellation, event history, and no workload auto-assignment:

```python
def test_role_pool_task_is_not_auto_assigned(repository):
    task = repository.create_task(
        workflow_id="wf-1", task_type="breakdown.create", role_code="selection_operator",
        project_id=None, topic_id=12,
    )
    assert task["status"] == "pool"
    assert task["assignee_user_id"] is None

def test_only_matching_role_can_claim(repository, selection_user, writer_user):
    task = repository.create_task("wf-1", "breakdown.create", "selection_operator", topic_id=12)
    with pytest.raises(PermissionError):
        repository.claim_task(task["id"], writer_user)
    assert repository.claim_task(task["id"], selection_user)["status"] == "assigned"
```

- [ ] **Step 2: Run tests and verify workflow modules are missing**

Run: `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py -q`

Expected: FAIL with missing workflow modules.

- [ ] **Step 3: Implement task schemas and state machine**

Use exact states `pool`, `assigned`, `in_progress`, `waiting_review`, `returned`, `completed`, and `cancelled`. Define allowed actions and actor requirements in one mapping; repository methods must call the state machine rather than update status strings directly.

- [ ] **Step 4: Implement atomic role-pool claim and assignment**

Use `BEGIN IMMEDIATE` and `UPDATE ... WHERE status='pool' AND assignee_user_id IS NULL` to avoid two users claiming one task. Check current effective role codes before the update. Project owners and system administrators may assign or reassign; ordinary users may only claim matching pools.

- [ ] **Step 5: Implement task list and mutation APIs**

`GET /api/workflow/tasks` returns only claimable pools, assigned tasks, or managed-project tasks according to principal scope. Add `GET`, `POST`, and `DELETE` member operations under `/api/workflow/projects/{project_id}/members`; require `project.member.manage`, prevent removal of the current project owner until another owner is assigned, and store membership role/status in `project_members`. Every mutation records `workflow_task_events` with actor, before/after state, and timestamp.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_workflow_tasks.py tests/test_workflow_api.py -q`

Expected: PASS.

Commit:

```bash
git add workflow tests/test_workflow_tasks.py tests/test_workflow_api.py
git commit -m "feat: add human task pools and assignment"
```

---

### Task 8: Creative Stage Version Registry and Unified State Machine

**Files:**
- Modify: `creative/repository.py`
- Modify: `creative/schema.py`
- Create: `creative/version_service.py`
- Test: `tests/test_creative_version_state.py`

**Interfaces:**
- Consumes: existing creative resource records and current user IDs.
- Produces: `CreativeRepository.create_stage_version(...)`.
- Produces: `CreativeVersionService.submit(version_id, actor_id)`.
- Produces: `approve`, `return_version`, `fork`, `force_unlock`, and `assert_editable`.

- [ ] **Step 1: Write failing version-state tests**

Cover every approved state transition, immutability, self-approval rejection, return/fork behavior, content hashes, aggregate batches, upstream references, and system-admin override:

```python
def test_submit_freezes_version_and_approve_unlocks_next_stage(version_service, author, reviewer):
    version = version_service.create_draft("breakdown", topic_id=1, actor_id=author.id)
    submitted = version_service.submit(version["id"], author.id)
    assert submitted["status"] == "submitted"
    with pytest.raises(ValueError, match="已提交版本不能修改"):
        version_service.assert_editable(version["id"], author.id)
    approved = version_service.approve(version["id"], reviewer.id)
    assert approved["status"] == "approved"

def test_return_keeps_submission_and_creates_new_draft(version_service, author, reviewer):
    submitted = seed_submitted(version_service, author)
    returned, draft = version_service.return_version(submitted["id"], reviewer.id, "补充前三集证据")
    assert returned["status"] == "returned"
    assert draft["status"] == "draft"
    assert draft["parent_version_id"] == returned["id"]
```

- [ ] **Step 2: Run tests and verify unified versions are missing**

Run: `uv run pytest tests/test_creative_version_state.py -q`

Expected: FAIL because `creative_stage_versions` and version service do not exist.

- [ ] **Step 3: Add creative_stage_versions and stage_version_id migrations**

Create the schema from the approved design. Add nullable `stage_version_id` columns to breakdowns, blueprints, characters, relationships, and episodes. Add unique indexes `uq_stage_version_topic` on `(topic_id, stage, version)` when `topic_id IS NOT NULL` and `uq_stage_version_project` on `(project_id, stage, resource_group_id, version)` when `project_id IS NOT NULL`.

- [ ] **Step 4: Implement immutable submit/approve/return/fork behavior**

On submit, hash a canonical JSON snapshot of all aggregate resources, persist `submitted_by/at`, and set `submitted`. On every subsequent read or approval, recompute and reject if the frozen content hash changed. On return, preserve the submitted row as `returned` and clone both version metadata and underlying resource rows into a new `draft` aggregate.

- [ ] **Step 5: Implement approved-version supersession and upstream references**

Approving a newer version changes the previous approved version to `superseded` but does not delete downstream resources. New downstream drafts store exact upstream stage-version IDs. Only `approved` stage versions satisfy generation gates.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_creative_version_state.py -q`

Expected: PASS.

Commit:

```bash
git add creative/repository.py creative/schema.py creative/version_service.py tests/test_creative_version_state.py
git commit -m "feat: add unified creative version state machine"
```

---

### Task 9: Approval Orchestration and Automatic Next-Node Tasks

**Files:**
- Create: `workflow/transition_service.py`
- Modify: `workflow/api.py`
- Modify: `creative/version_service.py`
- Test: `tests/test_workflow_transitions.py`

**Interfaces:**
- Consumes: `WorkflowRepository`, `CreativeVersionService`, and project/topic repositories.
- Produces: `TransitionService.submit_task_version(task_id, version_id, actor)`.
- Produces: `approve_task`, `return_task`, and deterministic next-node creation.

- [ ] **Step 1: Write failing end-to-end transition tests**

Test this exact node sequence:

```text
topic_decision
source_package
breakdown
brief
characters
outline
beats
delivery
```

Assert approval creates one and only one next task in the correct role pool, return reopens author work with a new draft, and repeated approval requests are idempotent.

- [ ] **Step 2: Run tests and verify orchestration is absent**

Run: `uv run pytest tests/test_workflow_transitions.py -q`

Expected: FAIL with missing `TransitionService`.

- [ ] **Step 3: Define the workflow node catalog**

Use one immutable mapping:

```python
WORKFLOW_NODES = {
    "topic_decision": ("selection_operator", "project_owner", "source_package"),
    "source_package": ("selection_operator", "project_owner", "breakdown"),
    "breakdown": ("selection_operator", "project_owner", "brief"),
    "brief": ("creative_planner", "project_owner", "characters"),
    "characters": ("creative_planner", "project_owner", "outline"),
    "outline": ("screenwriter", "project_owner", "beats"),
    "beats": ("screenwriter", "project_owner", "delivery"),
    "delivery": ("system_admin", None, None),
}
```

- [ ] **Step 4: Implement transactional submission and review**

Submission must freeze the stage version and move the execution task to `waiting_review` in one service operation. Approval must approve the version, complete execution/review tasks, and create the next pool task. Return must require a non-empty review comment, return the submitted version, clone a new draft, and reopen the execution task.

- [ ] **Step 5: Add version submit/approve/return/fork APIs**

Expose the approved `/api/versions/{id}` routes. Require author/task ownership for submit, managed-project `version.approve` for review, and wildcard for force unlock. Audit each transition.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/test_workflow_transitions.py tests/test_creative_version_state.py -q`

Expected: PASS.

Commit:

```bash
git add workflow creative/version_service.py tests/test_workflow_transitions.py
git commit -m "feat: connect approvals to workflow transitions"
```

---

### Task 10: Integrate Existing Creative APIs with Users, Versions, and Gates

**Files:**
- Modify: `creative/api.py`
- Modify: `creative/repository.py`
- Modify: `creative/lifecycle_service.py`
- Modify: `creative/breakdown_worker.py`
- Modify: `creative/phase4_worker.py`
- Modify: `tests/test_creative_phase4.py`
- Test: `tests/test_creative_permissions.py`
- Test: `tests/test_creative_approval_flow.py`

**Interfaces:**
- Consumes: principals, permission guards, stage versions, workflow tasks, and approved upstream IDs.
- Produces: permission-protected current creative routes and compatibility responses.

- [ ] **Step 1: Write failing permission and approval-gate tests**

Assert selection operators can generate/edit breakdown drafts but not approve; planners can edit Brief/characters only on assigned projects; writers can edit outlines/Beats only after approved upstream stages; project owners approve managed projects; admins bypass role restrictions but are audited.

Replace old lock assertions with submit/approve assertions:

```python
submit = selection_client.post(f"/api/versions/{breakdown_version_id}/submit")
assert submit.status_code == 200
assert selection_client.patch(
    f"/api/creative/breakdowns/{breakdown_id}", json={"content": changed}
).status_code == 409
assert owner_client.post(f"/api/versions/{breakdown_version_id}/approve").status_code == 200
```

- [ ] **Step 2: Run tests and verify existing routes are unprotected**

Run: `uv run pytest tests/test_creative_permissions.py tests/test_creative_approval_flow.py -q`

Expected: FAIL because routes do not require users or unified versions.

- [ ] **Step 3: Add current-user IDs and permission dependencies to creative routes**

Protect read and write routes with the exact role permissions. Pass `principal.user_id` into project creation and all version-creating repository methods. Filter list routes by task/project scope unless principal is system administrator.

- [ ] **Step 4: Replace direct locks with approval workflow**

Keep old `/lock` routes for one compatibility release but return `410 Gone` with `请提交版本并由项目负责人审批`. Update UI callers to `/api/versions/{id}/submit`; review pages use approve/return APIs.

- [ ] **Step 5: Change generation gates to approved stage versions**

Breakdown generation creates a draft stage version. Character generation requires approved Brief; outline generation requires approved characters; Beats generation requires approved outline. Workers persist author/task/version metadata from `job.extra` but never approve outputs automatically.

- [ ] **Step 6: Preserve lifecycle labels while adding approval states**

Keep `TopicLifecycleService` business labels for downloaded/generated/exported progress, add `pending_approval` and `returned_for_revision` display metadata from the active workflow task, and do not replace human task status with machine Job status.

- [ ] **Step 7: Run creative regressions and commit**

Run: `uv run pytest tests/test_creative_phase3.py tests/test_creative_phase4.py tests/test_creative_phase4_generation.py tests/test_creative_permissions.py tests/test_creative_approval_flow.py -q`

Expected: PASS.

Commit:

```bash
git add creative tests/test_creative_phase4.py tests/test_creative_permissions.py tests/test_creative_approval_flow.py
git commit -m "feat: enforce creative approval workflow"
```

---

### Task 11: Link JobManager Jobs to Human Tasks and Enforce Job Permissions

**Files:**
- Modify: `runtime/job_manager.py`
- Modify: `WebGUI.py`
- Modify: `workflow/repository.py`
- Modify: `creative/api.py`
- Modify: drama download job creation routes in `WebGUI.py`
- Modify: `tests/test_job_manager.py`
- Test: `tests/test_job_permissions.py`

**Interfaces:**
- Consumes: current principal, workflow task IDs, and existing `CrawlJob.extra`.
- Produces: job fields `created_by_user_id`, `workflow_task_id`, and `project_id`.
- Produces: permission-filtered list/get/log/stop/pause/resume/retry/delete behavior.

- [ ] **Step 1: Write failing job ownership and linkage tests**

Cover selection download jobs, creative generation jobs, project visibility, owner cancellation, unauthorized cancellation, admin wildcard, and audit records:

```python
def test_machine_job_links_to_human_task(selection_client):
    response = selection_client.post("/api/hongguo/drama/download/jobs", json={
        "title": "测试剧", "book_id": "book-1", "episodes": [1], "workflow_task_id": "task-1",
    })
    job = response.json()["job"]
    assert job["created_by_user_id"] == selection_client.user_id
    assert job["workflow_task_id"] == "task-1"
```

- [ ] **Step 2: Run tests and verify job ownership is absent**

Run: `uv run pytest tests/test_job_permissions.py -q`

Expected: FAIL because JobManager does not expose user/task ownership.

- [ ] **Step 3: Persist ownership in CrawlJob.extra and public state**

Add normalized ownership fields at job creation without changing worker command selection. Record the link in `workflow_task_jobs`. Existing jobs without ownership are visible only to system administrators after auth is enabled.

- [ ] **Step 4: Protect all job operations**

Creators may control their own active jobs when they retain the matching role permission. Project members may read project jobs. Project owners may cancel managed-project jobs. Only system administrators may delete job history or control legacy unowned jobs.

- [ ] **Step 5: Audit machine task mutations and run tests**

Audit create, stop, pause, resume, retry, and delete. Run:

`uv run pytest tests/test_job_manager.py tests/test_job_permissions.py tests/test_webgui_ui_changes.py -q`

Expected: PASS.

Commit:

```bash
git add runtime/job_manager.py WebGUI.py workflow creative/api.py tests/test_job_manager.py tests/test_job_permissions.py
git commit -m "feat: secure and link machine jobs"
```

---

### Task 12: Admin Users, Roles, Operations, Delivery, and Audit Pages

**Files:**
- Modify: `templates/admin/users.html`
- Modify: `templates/admin/roles.html`
- Modify: `templates/admin/config.html`
- Modify: `templates/admin/data.html`
- Modify: `templates/admin/analytics.html`
- Modify: `templates/admin/jobs.html`
- Modify: `templates/admin/deliveries.html`
- Modify: `templates/admin/audit.html`
- Modify: `static/js/admin/users.js`
- Modify: `static/js/admin/roles.js`
- Modify: `static/js/admin/jobs.js`
- Modify: `static/js/admin/deliveries.js`
- Modify: `static/js/admin/audit.js`
- Modify: `identity/api.py`
- Modify: `audit/api.py`
- Modify: `creative/api.py`
- Modify: `WebGUI.py`
- Test: `tests/test_admin_pages.py`
- Test: `tests/test_delivery_permissions.py`

**Interfaces:**
- Consumes: admin APIs, permission catalog, audit API, JobManager, data/config APIs, approved creative versions.
- Produces: usable `/admin/*` workspaces and approved-version delivery records.

- [ ] **Step 1: Write failing admin page and delivery tests**

Assert only system administrators can open every `/admin/*` route, user/role forms use the actual APIs, critical actions succeed without a reason, and delivery rejects non-approved versions.

- [ ] **Step 2: Run tests and verify admin workspaces are incomplete**

Run: `uv run pytest tests/test_admin_pages.py tests/test_delivery_permissions.py -q`

Expected: FAIL because admin pages and protected delivery APIs are absent.

- [ ] **Step 3: Implement user and role management pages**

Provide user creation, disable/restore, password reset, multi-role binding, custom-role clone, grouped permission checkboxes, automatic dependency display, and last-admin error handling. Use CSRF-protected JSON requests and never display password hashes or session tokens.

- [ ] **Step 4: Complete operations pages**

Move crawler/config, data files, analytics, and machine-job controls under admin routes. Preserve existing online preview, file classification, batch delete, rank sync, logs, pause/resume, retry, and configuration backup behavior while requiring wildcard access.

- [ ] **Step 5: Implement approved-stage delivery**

Replace unrestricted stage download links with delivery creation. `POST /api/admin/deliveries` accepts project, stage, approved version ID, and format; validates approval, builds the current Markdown/JSON output, stores content fingerprint, actor, timestamp, and path, then returns a protected download ID.

- [ ] **Step 6: Implement audit browsing**

Support filters for actor, action, target type, result, and time range. Show before/after summaries with secret fields already redacted by the backend.

- [ ] **Step 7: Run tests and commit**

Run: `uv run pytest tests/test_admin_pages.py tests/test_delivery_permissions.py tests/test_audit_service.py -q`

Expected: PASS.

Commit:

```bash
git add templates/admin static/js/admin identity audit creative/api.py WebGUI.py tests/test_admin_pages.py tests/test_delivery_permissions.py
git commit -m "feat: add administrator operations and delivery"
```

---

### Task 13: Idempotent Migration for Existing Local Data

**Files:**
- Create: `scripts/migrate_role_workflow.py`
- Modify: `creative/repository.py`
- Test: `tests/test_role_workflow_migration.py`

**Interfaces:**
- Consumes: existing `.runtime/creative/creative.db` and new system repository.
- Produces: `migrate(root, admin_user_id, dry_run=False) -> MigrationReport`.

- [ ] **Step 1: Write failing migration tests with a legacy database fixture**

Create legacy draft/locked breakdown, Brief, character batch, outline batch, Beats, and `created_by='local'` records. Assert:

- locked/brief_locked become approved stage versions;
- drafts remain drafts;
- current pointers reference approved stage versions;
- one next human task is created per active project;
- rerunning produces no duplicates;
- dry-run changes nothing;
- failures leave both databases unchanged.

- [ ] **Step 2: Run tests and verify migration is absent**

Run: `uv run pytest tests/test_role_workflow_migration.py -q`

Expected: FAIL because migration script does not exist.

- [ ] **Step 3: Implement backup, dry-run, and transactional migration**

Create timestamped backups before write mode. Use a migration ledger key `role_workflow_v1` in both databases. Build deterministic resource-group IDs from legacy table/type/ID so reruns use `ON CONFLICT DO NOTHING`.

- [ ] **Step 4: Map legacy states and create next tasks**

Map `locked` and `brief_locked` to `approved`, `draft` to `draft`, and preserve old IDs in `source_refs_json`. Attribute `local` records to the bootstrap administrator with a migration note. Use current project pointers to select exactly one next workflow node and role pool.

- [ ] **Step 5: Run migration tests and commit**

Run: `uv run pytest tests/test_role_workflow_migration.py -q`

Expected: PASS.

Commit:

```bash
git add scripts/migrate_role_workflow.py creative/repository.py tests/test_role_workflow_migration.py
git commit -m "feat: migrate legacy creative workflow data"
```

---

### Task 14: Full Integration, Browser Verification, and Documentation Update

**Files:**
- Modify: `improve/壹凡短剧选品平台PRD实施文档.md`
- Modify: `docs/index.md`
- Test: `tests/test_role_workflow_e2e.py`

**Interfaces:**
- Consumes: identity, audit, page routing, role templates, workflow, versioning, creative integration, secured jobs, admin delivery, and migration interfaces from Tasks 1-13.
- Produces: a verified end-to-end local multi-user workflow and updated product documentation.

- [ ] **Step 1: Add a complete multi-user API scenario**

Create five users, bind default roles, and execute:

```text
selection operator submits topic decision
project owner approves
selection operator completes source package and breakdown
project owner approves breakdown
creative planner submits Brief and characters
project owner approves both
screenwriter submits outline and one Beats version
project owner approves
system administrator creates and downloads delivery
```

Assert every submitted version is frozen, every next task appears in the correct pool, unauthorized users receive `403`, and all critical actions produce audit records.

- [ ] **Step 2: Run focused backend suites**

Run:

```powershell
uv run pytest tests/test_identity_repository.py tests/test_identity_auth.py tests/test_identity_permissions.py tests/test_identity_admin_api.py tests/test_audit_service.py tests/test_workflow_tasks.py tests/test_workflow_api.py tests/test_creative_version_state.py tests/test_workflow_transitions.py tests/test_creative_permissions.py tests/test_creative_approval_flow.py tests/test_job_permissions.py tests/test_delivery_permissions.py tests/test_role_workflow_migration.py tests/test_role_workflow_e2e.py -q
```

Expected: PASS.

- [ ] **Step 3: Run existing regression suites**

Run:

```powershell
uv run pytest tests/test_creative_phase3.py tests/test_creative_phase4.py tests/test_creative_phase4_generation.py tests/test_creative_breakdown_worker.py tests/test_hongguo_drama.py tests/test_job_manager.py tests/test_webgui_ui_changes.py tests/test_today_rank_phase.py -q
```

Expected: PASS after intentional lock-to-approval test updates.

- [ ] **Step 4: Parse every external JavaScript module**

Run Node syntax checks over every `static/js/**/*.js` file and fail on the first parse error. Expected: every module parses without error.

- [ ] **Step 5: Browser-check all five roles**

Start the Web GUI on an unused port. With Playwright, log in as each default role at desktop and mobile widths and verify:

- only authorized navigation is visible;
- direct unauthorized routes return the 403 page;
- role pages have no internal fixed-height workspace scrollbars;
- selection download and breakdown progress remains usable;
- planning and writing forms save without layout overlap;
- review submit/approve/return updates without full-page errors;
- admin user/role/audit/delivery pages work;
- no browser console errors occur.

- [ ] **Step 6: Update product documentation**

Replace the old five-role table and single-user caveat in the PRD with the final roles, unified version state machine, role task pools, page-route isolation, and local-auth assumptions. Add the design and this plan to `docs/index.md`.

- [ ] **Step 7: Run formatting and full project tests**

Run:

```powershell
uv run pre-commit run --all-files
uv run pytest tests/ -q
```

Expected: pre-commit passes and all unit tests pass. Record any integration tests under `test/` that require unavailable Redis/MongoDB instead of silently skipping attempted verification.

- [ ] **Step 8: Commit the verified integration**

```bash
git add improve/壹凡短剧选品平台PRD实施文档.md docs/index.md tests/test_role_workflow_e2e.py
git commit -m "docs: finalize role workflow implementation"
```

---

## Completion Criteria

- A fresh installation can create its first local system administrator and log in.
- The last active system administrator cannot be disabled or stripped of admin access.
- Custom roles can be cloned and edited; permission dependencies are applied automatically.
- Each default role sees only its authorized page routes, and direct unauthorized access returns `403`.
- Human tasks enter role pools and are claimed or manually assigned, never workload-auto-assigned.
- Every content stage uses `draft -> submitted -> approved|returned` with immutable submissions.
- Approval is the only normal action that unlocks the next workflow node.
- Existing locked creative history migrates to approved versions without data loss.
- Existing machine jobs remain operational, carry user/task ownership, and obey permissions.
- System administrators can access all pages and operations; overrides are audited with nullable reasons.
- Stage delivery uses approved versions and records actor, version, fingerprint, path, and time.
- Focused identity/workflow/creative tests, existing regressions, pre-commit, JavaScript parsing, and five-role Playwright checks all pass.
