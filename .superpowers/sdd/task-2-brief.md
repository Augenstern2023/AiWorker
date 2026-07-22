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

