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

