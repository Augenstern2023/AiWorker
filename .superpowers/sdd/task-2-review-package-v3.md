# Commits

34368d5 fix: keep disabled admin roles unchanged
6576ab8 fix: restore admin role during setup
225fc27 feat: add local account authentication

# Stat

 identity/api.py              | 297 +++++++++++++++++++++++++++++
 identity/password_service.py |  46 +++++
 identity/repository.py       |  46 +++++
 identity/session_service.py  | 199 ++++++++++++++++++++
 templates/auth/login.html    |  33 ++++
 templates/auth/setup.html    |  34 ++++
 tests/test_identity_auth.py  | 433 +++++++++++++++++++++++++++++++++++++++++++
 7 files changed, 1088 insertions(+)

# Diff

diff --git a/identity/api.py b/identity/api.py
new file mode 100644
index 0000000..992de4d
--- /dev/null
+++ b/identity/api.py
@@ -0,0 +1,297 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+import sqlite3
+import uuid
+from pathlib import Path
+from typing import Any
+
+from fastapi import APIRouter, HTTPException, Request, Response
+from fastapi.responses import HTMLResponse, JSONResponse
+from pydantic import BaseModel, Field
+
+from .password_service import PasswordService
+from .repository import IdentityRepository
+from .session_service import SessionContext, SessionService
+
+
+AUTHENTICATION_ERROR = "用户名或密码错误"
+TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "auth"
+
+
+class SetupRequest(BaseModel):
+    username: str = Field(min_length=1, max_length=128)
+    display_name: str = Field(min_length=1, max_length=128)
+    password: str
+
+
+class LoginRequest(BaseModel):
+    username: str = Field(min_length=1, max_length=128)
+    password: str
+
+
+class ChangePasswordRequest(BaseModel):
+    current_password: str
+    new_password: str
+
+
+def build_identity_router(
+    repository: IdentityRepository, session_service: SessionService
+) -> APIRouter:
+    router = APIRouter()
+    password_service = PasswordService()
+    dummy_password_hash = password_service.hash("Dummy-Password-123!")
+    repository.seed_defaults()
+
+    def session_context(request: Request, *, require_csrf: bool = False) -> SessionContext:
+        raw_token = request.cookies.get(session_service.cookie_name)
+        context = session_service.resolve_session(raw_token)
+        if context is None:
+            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
+        if require_csrf and not session_service.verify_csrf(
+            raw_token or "", request.headers.get("X-CSRF-Token")
+        ):
+            raise HTTPException(status_code=403, detail="CSRF 校验失败")
+        return context
+
+    @router.get("/login", response_class=HTMLResponse)
+    def login_page() -> HTMLResponse:
+        return HTMLResponse(_read_template("login.html"))
+
+    @router.get("/setup", response_class=HTMLResponse)
+    def setup_page() -> HTMLResponse:
+        if repository.count_active_system_admins() > 0:
+            raise HTTPException(status_code=404, detail="初始化页面不可用")
+        return HTMLResponse(_read_template("setup.html"))
+
+    @router.post("/api/auth/setup", status_code=201)
+    def setup(payload: SetupRequest, request: Request) -> JSONResponse:
+        if repository.count_active_system_admins() > 0:
+            raise HTTPException(status_code=409, detail="系统已经完成初始化")
+        username = payload.username.strip()
+        display_name = payload.display_name.strip()
+        if not username or not display_name:
+            raise HTTPException(status_code=422, detail="用户名和显示名称不能为空")
+        try:
+            password_hash = password_service.hash(payload.password)
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+
+        user_id = str(uuid.uuid4())
+        setup_already_complete = False
+        try:
+            with repository.connect() as connection:
+                connection.execute("BEGIN IMMEDIATE")
+                role = connection.execute(
+                    "SELECT id FROM roles WHERE code = 'system_admin' COLLATE NOCASE"
+                ).fetchone()
+                if role is None:
+                    raise HTTPException(
+                        status_code=500, detail="系统管理员角色尚未初始化"
+                    )
+                bound_active_admins = connection.execute(
+                    """
+                    SELECT COUNT(DISTINCT u.id)
+                    FROM users AS u
+                    JOIN user_roles AS ur ON ur.user_id = u.id
+                    WHERE u.status = 'active' AND u.is_deleted = 0
+                      AND ur.role_id = ?
+                    """,
+                    (role["id"],),
+                ).fetchone()[0]
+                if bound_active_admins:
+                    setup_already_complete = True
+                else:
+                    connection.execute(
+                        """
+                        UPDATE roles
+                        SET is_enabled = 1, updated_at = CURRENT_TIMESTAMP
+                        WHERE id = ?
+                        """,
+                        (role["id"],),
+                    )
+                    connection.execute(
+                        """
+                        INSERT INTO users
+                            (id, username, normalized_username, display_name, password_hash)
+                        VALUES (?, ?, ?, ?, ?)
+                        """,
+                        (
+                            user_id,
+                            username,
+                            username.casefold(),
+                            display_name,
+                            password_hash,
+                        ),
+                    )
+                    connection.execute(
+                        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
+                        (user_id, role["id"]),
+                    )
+        except sqlite3.IntegrityError as exc:
+            raise HTTPException(status_code=409, detail="用户名已存在") from exc
+        if setup_already_complete:
+            raise HTTPException(status_code=409, detail="系统已经完成初始化")
+
+        return authenticated_response(
+            user_id, request, status_code=201, update_last_login=True
+        )
+
+    @router.post("/api/auth/login")
+    def login(payload: LoginRequest, request: Request) -> JSONResponse:
+        normalized_username = payload.username.strip().casefold()
+        with repository.connect() as connection:
+            user = connection.execute(
+                """
+                SELECT id, password_hash, status, is_deleted
+                FROM users WHERE normalized_username = ? COLLATE NOCASE
+                """,
+                (normalized_username,),
+            ).fetchone()
+        password_hash = user["password_hash"] if user is not None else dummy_password_hash
+        password_matches = password_service.verify(password_hash, payload.password)
+        if (
+            user is None
+            or not password_matches
+            or user["status"] != "active"
+            or user["is_deleted"]
+        ):
+            raise HTTPException(status_code=401, detail=AUTHENTICATION_ERROR)
+        return authenticated_response(
+            user["id"], request, status_code=200, update_last_login=True
+        )
+
+    @router.post("/api/auth/logout", status_code=204)
+    def logout(request: Request) -> Response:
+        session_context(request, require_csrf=True)
+        raw_token = request.cookies.get(session_service.cookie_name)
+        if raw_token:
+            session_service.revoke_session(raw_token)
+        response = Response(status_code=204)
+        response.delete_cookie(
+            session_service.cookie_name,
+            path="/",
+            secure=session_service.secure_cookie,
+            httponly=True,
+            samesite="lax",
+        )
+        return response
+
+    @router.get("/api/auth/me")
+    def me(request: Request) -> dict[str, Any]:
+        context = session_context(request)
+        return _auth_body(context)
+
+    @router.post("/api/auth/change-password", status_code=204)
+    def change_password(payload: ChangePasswordRequest, request: Request) -> Response:
+        context = session_context(request, require_csrf=True)
+        with repository.connect() as connection:
+            user = connection.execute(
+                "SELECT password_hash FROM users WHERE id = ?",
+                (context.principal.user_id,),
+            ).fetchone()
+        if user is None or not password_service.verify(
+            user["password_hash"], payload.current_password
+        ):
+            raise HTTPException(status_code=400, detail="当前密码错误")
+        try:
+            new_hash = password_service.hash(payload.new_password)
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+        with repository.connect() as connection:
+            connection.execute(
+                """
+                UPDATE users
+                SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
+                WHERE id = ?
+                """,
+                (new_hash, context.principal.user_id),
+            )
+            connection.execute(
+                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                "WHERE user_id = ? AND revoked_at IS NULL",
+                (context.principal.user_id,),
+            )
+        response = Response(status_code=204)
+        response.delete_cookie(
+            session_service.cookie_name,
+            path="/",
+            secure=session_service.secure_cookie,
+            httponly=True,
+            samesite="lax",
+        )
+        return response
+
+    def authenticated_response(
+        user_id: str,
+        request: Request,
+        *,
+        status_code: int,
+        update_last_login: bool,
+    ) -> JSONResponse:
+        raw_token, _ = session_service.create_session(user_id, request)
+        context = session_service.resolve_session(raw_token)
+        if context is None:
+            raise HTTPException(status_code=500, detail="无法创建登录会话")
+        if update_last_login:
+            with repository.connect() as connection:
+                connection.execute(
+                    "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
+                    (user_id,),
+                )
+        response = JSONResponse(_auth_body(context), status_code=status_code)
+        response.set_cookie(
+            session_service.cookie_name,
+            raw_token,
+            max_age=int(session_service.session_ttl.total_seconds()),
+            path="/",
+            secure=session_service.secure_cookie,
+            httponly=True,
+            samesite="lax",
+        )
+        return response
+
+    return router
+
+
+def _auth_body(context: SessionContext) -> dict[str, Any]:
+    principal = context.principal
+    return {
+        "user": {
+            "id": principal.user_id,
+            "username": principal.username,
+            "display_name": principal.display_name,
+            "role_codes": list(principal.role_codes),
+            "permissions": sorted(principal.permissions),
+            "data_scope": principal.data_scope,
+        },
+        "csrf_token": context.csrf_token,
+        "landing_route": _landing_route(principal.permissions),
+    }
+
+
+def _landing_route(permissions: frozenset[str]) -> str:
+    if "*" in permissions:
+        return "/tasks"
+    for permission, route in (
+        ("page.selection", "/selection/dashboard"),
+        ("page.planning", "/planning/projects"),
+        ("page.writing", "/writing/projects"),
+        ("page.review", "/review/tasks"),
+        ("page.admin.delivery", "/admin/deliveries"),
+    ):
+        if permission in permissions:
+            return route
+    return "/tasks"
+
+
+def _read_template(name: str) -> str:
+    return (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
diff --git a/identity/password_service.py b/identity/password_service.py
new file mode 100644
index 0000000..ab32129
--- /dev/null
+++ b/identity/password_service.py
@@ -0,0 +1,46 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from argon2 import PasswordHasher
+from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
+
+
+_PASSWORD_HASHER = PasswordHasher()
+
+
+class PasswordService:
+    """Validate and hash local-account passwords with one Argon2id hasher."""
+
+    def __init__(self) -> None:
+        self._hasher = _PASSWORD_HASHER
+
+    def hash(self, password: str) -> str:
+        self._validate(password)
+        return self._hasher.hash(password)
+
+    def verify(self, password_hash: str, password: str) -> bool:
+        if not 10 <= len(password) <= 128:
+            return False
+        try:
+            return self._hasher.verify(password_hash, password)
+        except (InvalidHashError, VerificationError, VerifyMismatchError):
+            return False
+
+    @staticmethod
+    def _validate(password: str) -> None:
+        if not 10 <= len(password) <= 128:
+            raise ValueError("密码长度必须为 10 到 128 个字符")
+        if not any(character.isalpha() for character in password):
+            raise ValueError("密码必须包含至少一个字母")
+        if not any(character.isdigit() for character in password):
+            raise ValueError("密码必须包含至少一个数字")
+        if not any(not character.isalnum() for character in password):
+            raise ValueError("密码必须包含至少一个非字母数字字符")
diff --git a/identity/repository.py b/identity/repository.py
index ee59ffe..e579c48 100644
--- a/identity/repository.py
+++ b/identity/repository.py
@@ -315,16 +315,62 @@ class IdentityRepository:
         self, user_id: str, removing_role_id: str | None = None
     ) -> None:
         if (
             self.count_active_system_admins(
                 excluding_user_id=user_id, removing_role_id=removing_role_id
             )
             < 1
         ):
             raise ValueError("系统必须至少保留一个启用的系统管理员")
 
+    def disable_user(self, user_id: str) -> None:
+        with self.connect() as connection:
+            connection.execute("BEGIN IMMEDIATE")
+            user = connection.execute(
+                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
+            ).fetchone()
+            if user is None:
+                raise KeyError(user_id)
+            if user["status"] == "disabled":
+                return
+
+            is_system_admin = connection.execute(
+                """
+                SELECT 1
+                FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id
+                WHERE ur.user_id = ? AND r.code = 'system_admin' AND r.is_enabled = 1
+                """,
+                (user_id,),
+            ).fetchone()
+            if is_system_admin is not None:
+                surviving_admins = connection.execute(
+                    """
+                    SELECT COUNT(DISTINCT u.id)
+                    FROM users AS u
+                    JOIN user_roles AS ur ON ur.user_id = u.id
+                    JOIN roles AS r ON r.id = ur.role_id
+                    WHERE u.status = 'active' AND u.is_deleted = 0
+                      AND r.code = 'system_admin' AND r.is_enabled = 1
+                      AND u.id <> ?
+                    """,
+                    (user_id,),
+                ).fetchone()[0]
+                if surviving_admins < 1:
+                    raise ValueError("系统必须至少保留一个启用的系统管理员")
+            connection.execute(
+                "UPDATE users SET status = 'disabled', updated_at = CURRENT_TIMESTAMP "
+                "WHERE id = ?",
+                (user_id,),
+            )
+            connection.execute(
+                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                "WHERE user_id = ? AND revoked_at IS NULL",
+                (user_id,),
+            )
+
     @staticmethod
     def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
         role = dict(row)
         role["is_system"] = bool(role["is_system"])
         role["is_enabled"] = bool(role["is_enabled"])
         return role
diff --git a/identity/session_service.py b/identity/session_service.py
new file mode 100644
index 0000000..21a6569
--- /dev/null
+++ b/identity/session_service.py
@@ -0,0 +1,199 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+import base64
+import hashlib
+import hmac
+import secrets
+import uuid
+from dataclasses import dataclass
+from datetime import datetime, timedelta, timezone
+
+from fastapi import Request
+
+from .repository import IdentityRepository
+from .schema import DATA_SCOPE_ORDER, Principal
+
+
+_CSRF_DERIVATION_KEY = b"aiworker-session-csrf-v1"
+
+
+@dataclass(frozen=True)
+class SessionContext:
+    principal: Principal
+    csrf_token: str
+
+
+class SessionService:
+    cookie_name = "aiworker_session"
+
+    def __init__(
+        self,
+        repository: IdentityRepository,
+        *,
+        session_ttl: timedelta = timedelta(hours=12),
+        secure_cookie: bool = False,
+        csrf_secret: bytes | None = None,
+    ) -> None:
+        self.repository = repository
+        self.session_ttl = session_ttl
+        self.secure_cookie = secure_cookie
+        self._csrf_secret = csrf_secret or _CSRF_DERIVATION_KEY
+
+    def create_session(self, user_id: str, request: Request) -> tuple[str, str]:
+        with self.repository.connect() as connection:
+            user = connection.execute(
+                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
+            ).fetchone()
+            if user is None or user["status"] != "active" or user["is_deleted"]:
+                raise ValueError("用户不可登录")
+
+            raw_token = secrets.token_urlsafe(32)
+            csrf_token = self._csrf_for_token(raw_token)
+            now = datetime.now(timezone.utc)
+            connection.execute(
+                """
+                INSERT INTO sessions
+                    (id, user_id, token_hash, csrf_token_hash, expires_at,
+                     last_seen_at, user_agent, ip_address)
+                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
+                """,
+                (
+                    str(uuid.uuid4()),
+                    user_id,
+                    self._hash_token(raw_token),
+                    self._hash_token(csrf_token),
+                    (now + self.session_ttl).isoformat(),
+                    now.isoformat(),
+                    request.headers.get("user-agent"),
+                    request.client.host if request.client else None,
+                ),
+            )
+        return raw_token, csrf_token
+
+    def resolve_session(self, raw_token: str | None) -> SessionContext | None:
+        if not raw_token:
+            return None
+        token_hash = self._hash_token(raw_token)
+        with self.repository.connect() as connection:
+            row = connection.execute(
+                """
+                SELECT s.id AS session_id, s.user_id, s.csrf_token_hash,
+                       s.expires_at, s.revoked_at, u.username, u.display_name,
+                       u.status, u.is_deleted
+                FROM sessions AS s
+                JOIN users AS u ON u.id = s.user_id
+                WHERE s.token_hash = ?
+                """,
+                (token_hash,),
+            ).fetchone()
+            if row is None or row["revoked_at"] is not None:
+                return None
+
+            if row["status"] != "active" or row["is_deleted"]:
+                connection.execute(
+                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                    "WHERE user_id = ? AND revoked_at IS NULL",
+                    (row["user_id"],),
+                )
+                return None
+
+            if self._parse_datetime(row["expires_at"]) <= datetime.now(timezone.utc):
+                connection.execute(
+                    "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
+                    (row["session_id"],),
+                )
+                return None
+
+            role_rows = connection.execute(
+                """
+                SELECT r.code, rp.permission_code, rp.data_scope
+                FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
+                LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
+                WHERE ur.user_id = ?
+                ORDER BY r.code, rp.permission_code
+                """,
+                (row["user_id"],),
+            ).fetchall()
+            connection.execute(
+                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
+                (datetime.now(timezone.utc).isoformat(), row["session_id"]),
+            )
+
+        role_codes = tuple(sorted({role["code"] for role in role_rows}))
+        permissions = frozenset(
+            role["permission_code"]
+            for role in role_rows
+            if role["permission_code"] is not None
+        )
+        scopes = [
+            role["data_scope"]
+            for role in role_rows
+            if role["data_scope"] is not None
+        ]
+        data_scope = max(scopes, key=DATA_SCOPE_ORDER.get) if scopes else "task"
+        csrf_token = self._csrf_for_token(raw_token)
+        if not hmac.compare_digest(
+            row["csrf_token_hash"], self._hash_token(csrf_token)
+        ):
+            return None
+        return SessionContext(
+            principal=Principal(
+                user_id=row["user_id"],
+                username=row["username"],
+                display_name=row["display_name"],
+                role_codes=role_codes,
+                permissions=permissions,
+                data_scope=data_scope,
+                session_id=row["session_id"],
+            ),
+            csrf_token=csrf_token,
+        )
+
+    def verify_csrf(self, raw_token: str, supplied_token: str | None) -> bool:
+        if not supplied_token:
+            return False
+        expected = self._csrf_for_token(raw_token)
+        return hmac.compare_digest(expected, supplied_token)
+
+    def revoke_session(self, raw_token: str) -> None:
+        with self.repository.connect() as connection:
+            connection.execute(
+                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                "WHERE token_hash = ? AND revoked_at IS NULL",
+                (self._hash_token(raw_token),),
+            )
+
+    def revoke_user_sessions(self, user_id: str) -> None:
+        with self.repository.connect() as connection:
+            connection.execute(
+                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                "WHERE user_id = ? AND revoked_at IS NULL",
+                (user_id,),
+            )
+
+    @staticmethod
+    def _hash_token(token: str) -> str:
+        return hashlib.sha256(token.encode("utf-8")).hexdigest()
+
+    def _csrf_for_token(self, raw_token: str) -> str:
+        digest = hmac.new(
+            self._csrf_secret, raw_token.encode("utf-8"), hashlib.sha256
+        ).digest()
+        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
+
+    @staticmethod
+    def _parse_datetime(value: str) -> datetime:
+        parsed = datetime.fromisoformat(value.replace(" ", "T"))
+        if parsed.tzinfo is None:
+            parsed = parsed.replace(tzinfo=timezone.utc)
+        return parsed.astimezone(timezone.utc)
diff --git a/templates/auth/login.html b/templates/auth/login.html
new file mode 100644
index 0000000..a232e4b
--- /dev/null
+++ b/templates/auth/login.html
@@ -0,0 +1,33 @@
+<!doctype html>
+<html lang="zh-CN">
+<head>
+  <meta charset="utf-8">
+  <meta name="viewport" content="width=device-width, initial-scale=1">
+  <title>登录 - AiWorker</title>
+</head>
+<body>
+  <main>
+    <h1>登录</h1>
+    <form id="login-form">
+      <label>用户名 <input name="username" autocomplete="username" required></label>
+      <label>密码 <input name="password" type="password" autocomplete="current-password" required></label>
+      <button type="submit">登录</button>
+      <p id="error" role="alert"></p>
+    </form>
+  </main>
+  <script>
+    document.getElementById('login-form').addEventListener('submit', async (event) => {
+      event.preventDefault();
+      const values = new FormData(event.currentTarget);
+      const response = await fetch('/api/auth/login', {
+        method: 'POST',
+        headers: {'Content-Type': 'application/json'},
+        body: JSON.stringify(Object.fromEntries(values)),
+      });
+      const result = await response.json();
+      if (response.ok) window.location.assign(result.landing_route);
+      else document.getElementById('error').textContent = result.detail || '登录失败';
+    });
+  </script>
+</body>
+</html>
diff --git a/templates/auth/setup.html b/templates/auth/setup.html
new file mode 100644
index 0000000..bb20d70
--- /dev/null
+++ b/templates/auth/setup.html
@@ -0,0 +1,34 @@
+<!doctype html>
+<html lang="zh-CN">
+<head>
+  <meta charset="utf-8">
+  <meta name="viewport" content="width=device-width, initial-scale=1">
+  <title>初始化 - AiWorker</title>
+</head>
+<body>
+  <main>
+    <h1>初始化系统管理员</h1>
+    <form id="setup-form">
+      <label>用户名 <input name="username" autocomplete="username" required></label>
+      <label>显示名称 <input name="display_name" autocomplete="name" required></label>
+      <label>密码 <input name="password" type="password" autocomplete="new-password" required></label>
+      <button type="submit">创建管理员</button>
+      <p id="error" role="alert"></p>
+    </form>
+  </main>
+  <script>
+    document.getElementById('setup-form').addEventListener('submit', async (event) => {
+      event.preventDefault();
+      const values = new FormData(event.currentTarget);
+      const response = await fetch('/api/auth/setup', {
+        method: 'POST',
+        headers: {'Content-Type': 'application/json'},
+        body: JSON.stringify(Object.fromEntries(values)),
+      });
+      const result = await response.json();
+      if (response.ok) window.location.assign(result.landing_route);
+      else document.getElementById('error').textContent = result.detail || '初始化失败';
+    });
+  </script>
+</body>
+</html>
diff --git a/tests/test_identity_auth.py b/tests/test_identity_auth.py
new file mode 100644
index 0000000..776dfc2
--- /dev/null
+++ b/tests/test_identity_auth.py
@@ -0,0 +1,433 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+import hashlib
+import threading
+from concurrent.futures import ThreadPoolExecutor
+from pathlib import Path
+
+import pytest
+from fastapi import FastAPI, Request
+from fastapi.testclient import TestClient
+
+from identity.api import build_identity_router
+from identity.password_service import PasswordService
+from identity.repository import IdentityRepository
+from identity.session_service import SessionService
+
+
+@pytest.fixture
+def repository(tmp_path: Path) -> IdentityRepository:
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    return repository
+
+
+@pytest.fixture
+def client(repository: IdentityRepository) -> TestClient:
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(repository, SessionService(repository, secure_cookie=False))
+    )
+    return TestClient(app)
+
+
+def create_user(
+    repository: IdentityRepository,
+    username: str = "writer",
+    password: str = "Writer-123!",
+    role_code: str = "screenwriter",
+) -> dict[str, str]:
+    password_hash = PasswordService().hash(password)
+    role = repository.get_role_by_code(role_code)
+    assert role is not None
+    user_id = f"user:{username}"
+    with repository.connect() as connection:
+        connection.execute(
+            """
+            INSERT INTO users
+                (id, username, normalized_username, display_name, password_hash)
+            VALUES (?, ?, ?, ?, ?)
+            """,
+            (user_id, username, username.casefold(), username.title(), password_hash),
+        )
+        connection.execute(
+            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
+            (user_id, role["id"]),
+        )
+    return {"id": user_id, "username": username}
+
+
+def test_password_service_uses_argon2id_and_rejects_wrong_password():
+    service = PasswordService()
+
+    password_hash = service.hash("Strong-Pass-123!")
+
+    assert password_hash.startswith("$argon2id$")
+    assert service.verify(password_hash, "Strong-Pass-123!") is True
+    assert service.verify(password_hash, "Wrong-Pass-123!") is False
+    assert service.verify("not-an-argon-hash", "Strong-Pass-123!") is False
+
+
+def test_password_service_accepts_unicode_letters():
+    service = PasswordService()
+
+    password_hash = service.hash("强密码安全-123!")
+
+    assert service.verify(password_hash, "强密码安全-123!") is True
+
+
+@pytest.mark.parametrize(
+    "password",
+    [
+        "Short-1!",
+        "a" * 129 + "1!",
+        "NoDigitsHere!",
+        "NoSymbols123",
+        "123456789!",
+    ],
+)
+def test_password_service_rejects_passwords_outside_policy(password: str):
+    with pytest.raises(ValueError, match="密码"):
+        PasswordService().hash(password)
+
+
+def test_session_stores_only_hashes_and_request_metadata(
+    repository: IdentityRepository,
+):
+    user = create_user(repository)
+    app = FastAPI()
+    service = SessionService(repository)
+
+    @app.get("/create")
+    async def create(request: Request):
+        return service.create_session(user["id"], request)
+
+    raw_token, csrf_token = TestClient(app).get(
+        "/create",
+        headers={"user-agent": "identity-test"},
+    ).json()
+
+    with repository.connect() as connection:
+        session = dict(connection.execute("SELECT * FROM sessions").fetchone())
+    assert session["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
+    assert session["csrf_token_hash"] == hashlib.sha256(csrf_token.encode()).hexdigest()
+    assert raw_token not in session.values()
+    assert csrf_token not in session.values()
+    assert session["user_agent"] == "identity-test"
+    assert session["ip_address"] == "testclient"
+
+
+def test_persisted_session_survives_service_recreation(
+    repository: IdentityRepository,
+):
+    create_user(repository)
+    first_service = SessionService(repository)
+    app = FastAPI()
+    app.include_router(build_identity_router(repository, first_service))
+    client = TestClient(app)
+    login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    raw_token = client.cookies.get("aiworker_session")
+    assert login.status_code == 200
+    assert raw_token is not None
+
+    restored = SessionService(repository).resolve_session(raw_token)
+
+    assert restored is not None
+    assert restored.principal.username == "writer"
+    assert restored.csrf_token == login.json()["csrf_token"]
+
+
+def test_first_setup_creates_system_admin_and_disables_setup(client: TestClient):
+    response = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+
+    assert response.status_code == 201
+    assert response.json()["user"]["role_codes"] == ["system_admin"]
+    assert response.json()["landing_route"] == "/tasks"
+    assert response.json()["csrf_token"]
+    assert "aiworker_session=" in response.headers["set-cookie"]
+    assert "HttpOnly" in response.headers["set-cookie"]
+    assert "SameSite=lax" in response.headers["set-cookie"]
+    assert client.post(
+        "/api/auth/setup",
+        json={
+            "username": "other",
+            "display_name": "其他",
+            "password": "Admin-Strong-456!",
+        },
+    ).status_code == 409
+    assert client.get("/setup").status_code == 404
+
+
+def test_setup_restores_disabled_system_admin_role(
+    repository: IdentityRepository,
+):
+    with repository.connect() as connection:
+        connection.execute(
+            "UPDATE roles SET is_enabled = 0 WHERE code = 'system_admin'"
+        )
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(repository, SessionService(repository, secure_cookie=False))
+    )
+    client = TestClient(app)
+
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+
+    assert setup.status_code == 201
+    assert setup.json()["user"]["role_codes"] == ["system_admin"]
+    assert repository.get_role_by_code("system_admin")["is_enabled"] is True
+    assert repository.count_active_system_admins() == 1
+    assert client.post(
+        "/api/auth/setup",
+        json={
+            "username": "other",
+            "display_name": "其他",
+            "password": "Admin-Strong-456!",
+        },
+    ).status_code == 409
+
+
+def test_setup_does_not_reenable_role_with_existing_bound_admin(
+    client: TestClient, repository: IdentityRepository
+):
+    first_setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    assert first_setup.status_code == 201
+    with repository.connect() as connection:
+        connection.execute(
+            "UPDATE roles SET is_enabled = 0 WHERE code = 'system_admin'"
+        )
+        user_count_before = connection.execute(
+            "SELECT COUNT(*) FROM users"
+        ).fetchone()[0]
+
+    repeated_setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "other",
+            "display_name": "其他",
+            "password": "Admin-Strong-456!",
+        },
+    )
+
+    assert repeated_setup.status_code == 409
+    assert repository.get_role_by_code("system_admin")["is_enabled"] is False
+    with repository.connect() as connection:
+        user_count_after = connection.execute(
+            "SELECT COUNT(*) FROM users"
+        ).fetchone()[0]
+    assert user_count_after == user_count_before
+
+
+def test_concurrent_setup_creates_exactly_one_admin(repository: IdentityRepository):
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(repository, SessionService(repository, secure_cookie=False))
+    )
+    barrier = threading.Barrier(2)
+
+    def submit_setup(index: int) -> int:
+        barrier.wait()
+        with TestClient(app) as thread_client:
+            response = thread_client.post(
+                "/api/auth/setup",
+                json={
+                    "username": f"admin{index}",
+                    "display_name": f"管理员{index}",
+                    "password": f"Admin-Strong-{index}23!",
+                },
+            )
+        return response.status_code
+
+    with ThreadPoolExecutor(max_workers=2) as executor:
+        statuses = list(executor.map(submit_setup, (1, 2)))
+
+    assert sorted(statuses) == [201, 409]
+    assert repository.count_active_system_admins() == 1
+
+
+def test_session_cookie_sets_secure_when_configured(repository: IdentityRepository):
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(repository, SessionService(repository, secure_cookie=True))
+    )
+
+    response = TestClient(app, base_url="https://testserver").post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+
+    assert response.status_code == 201
+    assert "Secure" in response.headers["set-cookie"]
+
+
+def test_login_has_uniform_errors_and_returns_permission_landing_route(
+    client: TestClient, repository: IdentityRepository
+):
+    create_user(repository)
+
+    missing = client.post(
+        "/api/auth/login", json={"username": "missing", "password": "Writer-123!"}
+    )
+    invalid = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Wrong-123!"}
+    )
+    login = client.post(
+        "/api/auth/login", json={"username": "WRITER", "password": "Writer-123!"}
+    )
+
+    assert missing.status_code == invalid.status_code == 401
+    assert missing.json() == invalid.json() == {"detail": "用户名或密码错误"}
+    assert login.status_code == 200
+    assert login.json()["landing_route"] == "/writing/projects"
+    assert login.json()["user"]["role_codes"] == ["screenwriter"]
+    assert login.json()["csrf_token"]
+
+
+def test_me_returns_csrf_and_logout_requires_it(
+    client: TestClient, repository: IdentityRepository
+):
+    create_user(repository)
+    login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    csrf_token = login.json()["csrf_token"]
+
+    me = client.get("/api/auth/me")
+    assert me.status_code == 200
+    assert me.json()["csrf_token"] == csrf_token
+    assert client.post("/api/auth/logout").status_code == 403
+    assert client.post(
+        "/api/auth/logout", headers={"X-CSRF-Token": "incorrect-token"}
+    ).status_code == 403
+    assert client.get("/api/auth/me").status_code == 200
+    logout = client.post(
+        "/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
+    )
+    assert logout.status_code == 204
+    assert client.get("/api/auth/me").status_code == 401
+
+
+def test_disabled_user_sessions_are_revoked(
+    client: TestClient, repository: IdentityRepository
+):
+    create_user(repository)
+    login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    assert login.status_code == 200
+
+    repository.disable_user(login.json()["user"]["id"])
+
+    assert client.get("/api/auth/me").status_code == 401
+    disabled_login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    assert disabled_login.status_code == 401
+    assert disabled_login.json() == {"detail": "用户名或密码错误"}
+    with repository.connect() as connection:
+        revoked_at = connection.execute(
+            "SELECT revoked_at FROM sessions WHERE user_id = ?",
+            (login.json()["user"]["id"],),
+        ).fetchone()["revoked_at"]
+    assert revoked_at is not None
+
+
+def test_expired_session_is_rejected(client: TestClient, repository: IdentityRepository):
+    create_user(repository)
+    login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    assert login.status_code == 200
+    with repository.connect() as connection:
+        connection.execute(
+            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
+        )
+
+    assert client.get("/api/auth/me").status_code == 401
+
+
+def test_change_password_requires_csrf_and_revokes_existing_sessions(
+    client: TestClient, repository: IdentityRepository
+):
+    create_user(repository)
+    login = client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    )
+    csrf_token = login.json()["csrf_token"]
+    assert client.post(
+        "/api/auth/change-password",
+        json={"current_password": "Writer-123!", "new_password": "Writer-New-456!"},
+    ).status_code == 403
+
+    changed = client.post(
+        "/api/auth/change-password",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"current_password": "Writer-123!", "new_password": "Writer-New-456!"},
+    )
+
+    assert changed.status_code == 204
+    assert client.get("/api/auth/me").status_code == 401
+    assert client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-123!"}
+    ).status_code == 401
+    assert client.post(
+        "/api/auth/login", json={"username": "writer", "password": "Writer-New-456!"}
+    ).status_code == 200
+
+
+def test_last_active_system_admin_cannot_be_disabled(
+    client: TestClient, repository: IdentityRepository
+):
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    assert setup.status_code == 201
+
+    with pytest.raises(ValueError, match="系统必须至少保留一个启用的系统管理员"):
+        repository.disable_user(setup.json()["user"]["id"])
+
+
+def test_login_and_initial_setup_pages_are_served(client: TestClient):
+    assert "登录" in client.get("/login").text
+    assert "初始化" in client.get("/setup").text
