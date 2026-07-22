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

