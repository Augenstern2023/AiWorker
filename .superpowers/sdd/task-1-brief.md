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

