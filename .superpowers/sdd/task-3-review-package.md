# Commits

afcdd3f feat: enforce customizable role permissions

# Stat

 identity/api.py                    | 308 ++++++++++++++++++++++++++++++++++++-
 identity/dependencies.py           | 109 +++++++++++++
 identity/permission_service.py     | 153 ++++++++++++++++++
 identity/repository.py             | 274 ++++++++++++++++++++++++++++++++-
 tests/test_identity_admin_api.py   | 285 ++++++++++++++++++++++++++++++++++
 tests/test_identity_permissions.py | 174 +++++++++++++++++++++
 6 files changed, 1293 insertions(+), 10 deletions(-)

# Diff

diff --git a/identity/api.py b/identity/api.py
index 992de4d..460ac86 100644
--- a/identity/api.py
+++ b/identity/api.py
@@ -7,26 +7,37 @@
 # Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
 #
 
 from __future__ import annotations
 
 import sqlite3
 import uuid
 from pathlib import Path
 from typing import Any
 
-from fastapi import APIRouter, HTTPException, Request, Response
+from fastapi import APIRouter, Depends, HTTPException, Request, Response
 from fastapi.responses import HTMLResponse, JSONResponse
 from pydantic import BaseModel, Field
 
+from .dependencies import require_permission
 from .password_service import PasswordService
+from .permission_catalog import PERMISSIONS, PERMISSION_DEPENDENCIES
+from .permission_service import PermissionService
 from .repository import IdentityRepository
+from .schema import (
+    Principal,
+    RoleCreateRequest,
+    RolePermissionRequest,
+    RoleUpdateRequest,
+    UserCreateRequest,
+    UserUpdateRequest,
+)
 from .session_service import SessionContext, SessionService
 
 
 AUTHENTICATION_ERROR = "用户名或密码错误"
 TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "auth"
 
 
 class SetupRequest(BaseModel):
     username: str = Field(min_length=1, max_length=128)
     display_name: str = Field(min_length=1, max_length=128)
@@ -36,38 +47,66 @@ class SetupRequest(BaseModel):
 class LoginRequest(BaseModel):
     username: str = Field(min_length=1, max_length=128)
     password: str
 
 
 class ChangePasswordRequest(BaseModel):
     current_password: str
     new_password: str
 
 
+class ResetPasswordRequest(BaseModel):
+    password: str
+
+
+class UserRolesRequest(BaseModel):
+    role_ids: list[str]
+
+
+class RolePermissionsRequest(BaseModel):
+    permissions: list[RolePermissionRequest]
+
+
+class RoleCloneRequest(BaseModel):
+    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
+    name: str = Field(min_length=1, max_length=128)
+    description: str | None = None
+
+
 def build_identity_router(
     repository: IdentityRepository, session_service: SessionService
 ) -> APIRouter:
-    router = APIRouter()
+    permission_service = PermissionService(repository, session_service)
+
+    def bind_permission_service(request: Request) -> None:
+        request.state.identity_permission_service = permission_service
+
+    router = APIRouter(dependencies=[Depends(bind_permission_service)])
     password_service = PasswordService()
     dummy_password_hash = password_service.hash("Dummy-Password-123!")
     repository.seed_defaults()
 
     def session_context(request: Request, *, require_csrf: bool = False) -> SessionContext:
         raw_token = request.cookies.get(session_service.cookie_name)
         context = session_service.resolve_session(raw_token)
         if context is None:
             raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
         if require_csrf and not session_service.verify_csrf(
             raw_token or "", request.headers.get("X-CSRF-Token")
         ):
             raise HTTPException(status_code=403, detail="CSRF 校验失败")
-        return context
+        principal = permission_service.principal_for_user(
+            context.principal.user_id, context.principal.session_id
+        )
+        if principal is None:
+            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
+        return SessionContext(principal=principal, csrf_token=context.csrf_token)
 
     @router.get("/login", response_class=HTMLResponse)
     def login_page() -> HTMLResponse:
         return HTMLResponse(_read_template("login.html"))
 
     @router.get("/setup", response_class=HTMLResponse)
     def setup_page() -> HTMLResponse:
         if repository.count_active_system_admins() > 0:
             raise HTTPException(status_code=404, detail="初始化页面不可用")
         return HTMLResponse(_read_template("setup.html"))
@@ -223,31 +262,294 @@ def build_identity_router(
         response = Response(status_code=204)
         response.delete_cookie(
             session_service.cookie_name,
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
         return response
 
+    admin_read = require_permission("*")
+
+    def admin_write(
+        request: Request, principal: Principal = Depends(admin_read)
+    ) -> Principal:
+        session_context(request, require_csrf=True)
+        return principal
+
+    @router.get("/api/admin/users")
+    def list_users(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
+        return repository.list_users()
+
+    @router.post("/api/admin/users", status_code=201)
+    def create_user(
+        payload: UserCreateRequest, _: Principal = Depends(admin_write)
+    ) -> dict[str, Any]:
+        username = payload.username.strip()
+        display_name = payload.display_name.strip()
+        if not username or not display_name:
+            raise HTTPException(
+                status_code=422, detail="用户名和显示名称不能为空"
+            )
+        _validate_role_ids(payload.role_ids)
+        try:
+            password_hash = password_service.hash(payload.password)
+            user = repository.create_user(username, display_name, password_hash)
+            repository.replace_user_roles(user["id"], payload.role_ids)
+        except sqlite3.IntegrityError as exc:
+            raise HTTPException(status_code=409, detail="用户名已存在") from exc
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+        return _user_response(user["id"])
+
+    @router.patch("/api/admin/users/{user_id}")
+    def update_user(
+        user_id: str,
+        payload: UserUpdateRequest,
+        _: Principal = Depends(admin_write),
+    ) -> dict[str, Any]:
+        try:
+            if payload.role_ids is not None:
+                _validate_role_ids(payload.role_ids)
+                repository.replace_user_roles(user_id, payload.role_ids)
+            repository.update_user(
+                user_id,
+                display_name=(
+                    payload.display_name.strip()
+                    if payload.display_name is not None
+                    else None
+                ),
+                status=payload.status,
+            )
+        except KeyError as exc:
+            raise HTTPException(status_code=404, detail="用户不存在") from exc
+        except ValueError as exc:
+            raise HTTPException(status_code=409, detail=str(exc)) from exc
+        return _user_response(user_id)
+
+    @router.post("/api/admin/users/{user_id}/reset-password", status_code=204)
+    def reset_password(
+        user_id: str,
+        payload: ResetPasswordRequest,
+        _: Principal = Depends(admin_write),
+    ) -> Response:
+        if repository.get_user(user_id) is None:
+            raise HTTPException(status_code=404, detail="用户不存在")
+        try:
+            password_hash = password_service.hash(payload.password)
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+        with repository.connect() as connection:
+            connection.execute(
+                """
+                UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
+                WHERE id = ?
+                """,
+                (password_hash, user_id),
+            )
+            connection.execute(
+                "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
+                "WHERE user_id = ? AND revoked_at IS NULL",
+                (user_id,),
+            )
+        return Response(status_code=204)
+
+    @router.put("/api/admin/users/{user_id}/roles")
+    def replace_user_roles(
+        user_id: str,
+        payload: UserRolesRequest,
+        _: Principal = Depends(admin_write),
+    ) -> dict[str, Any]:
+        try:
+            _validate_role_ids(payload.role_ids)
+            repository.replace_user_roles(user_id, payload.role_ids)
+        except KeyError as exc:
+            raise HTTPException(status_code=404, detail="用户不存在") from exc
+        except ValueError as exc:
+            status_code = 409 if "系统管理员" in str(exc) else 422
+            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
+        return _user_response(user_id)
+
+    @router.get("/api/admin/roles")
+    def list_roles(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
+        return [_role_response(role) for role in repository.list_roles()]
+
+    @router.post("/api/admin/roles", status_code=201)
+    def create_role(
+        payload: RoleCreateRequest, _: Principal = Depends(admin_write)
+    ) -> dict[str, Any]:
+        try:
+            grants = _permission_pairs(payload.permissions)
+            _validate_permission_pairs(grants)
+            role = repository.create_role(
+                payload.code, payload.name.strip(), payload.description
+            )
+            repository.replace_role_permissions(role["id"], grants)
+        except sqlite3.IntegrityError as exc:
+            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+        return _role_response(role)
+
+    @router.patch("/api/admin/roles/{role_id}")
+    def update_role(
+        role_id: str,
+        payload: RoleUpdateRequest,
+        _: Principal = Depends(admin_write),
+    ) -> dict[str, Any]:
+        try:
+            if payload.permissions is not None:
+                _replace_permissions(role_id, payload.permissions)
+            role = repository.update_role(
+                role_id,
+                name=payload.name.strip() if payload.name is not None else None,
+                description=payload.description,
+                is_enabled=payload.is_enabled,
+            )
+        except KeyError as exc:
+            raise HTTPException(status_code=404, detail="角色不存在") from exc
+        except ValueError as exc:
+            status_code = 409 if "系统管理员" in str(exc) else 422
+            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
+        return _role_response(role)
+
+    @router.post("/api/admin/roles/{role_id}/clone", status_code=201)
+    def clone_role(
+        role_id: str,
+        payload: RoleCloneRequest,
+        _: Principal = Depends(admin_write),
+    ) -> dict[str, Any]:
+        source = repository.get_role(role_id)
+        if source is None:
+            raise HTTPException(status_code=404, detail="角色不存在")
+        try:
+            role = repository.create_role(
+                payload.code,
+                payload.name.strip(),
+                source["description"]
+                if payload.description is None
+                else payload.description,
+            )
+            source_permissions = repository.list_role_permissions(role_id)
+            repository.replace_role_permissions(
+                role["id"],
+                [
+                    (item["permission_code"], item["data_scope"])
+                    for item in source_permissions
+                ],
+            )
+        except sqlite3.IntegrityError as exc:
+            raise HTTPException(status_code=409, detail="角色编码已存在") from exc
+        return _role_response(role)
+
+    @router.put("/api/admin/roles/{role_id}/permissions")
+    def replace_role_permissions(
+        role_id: str,
+        payload: RolePermissionsRequest,
+        _: Principal = Depends(admin_write),
+    ) -> dict[str, Any]:
+        try:
+            _replace_permissions(role_id, payload.permissions)
+        except KeyError as exc:
+            raise HTTPException(status_code=404, detail="角色不存在") from exc
+        except ValueError as exc:
+            raise HTTPException(status_code=422, detail=str(exc)) from exc
+        role = repository.get_role(role_id)
+        assert role is not None
+        return _role_response(role)
+
+    @router.get("/api/admin/permissions")
+    def list_permissions(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
+        with repository.connect() as connection:
+            rows = connection.execute(
+                "SELECT code, label FROM permissions ORDER BY code"
+            ).fetchall()
+        return [
+            {
+                "code": row["code"],
+                "label": row["label"],
+                "dependencies": sorted(PERMISSION_DEPENDENCIES.get(row["code"], ())),
+            }
+            for row in rows
+        ]
+
+    def _validate_role_ids(role_ids: list[str]) -> None:
+        known = {role["id"] for role in repository.list_roles()}
+        if not set(role_ids) <= known:
+            raise HTTPException(status_code=422, detail="角色不存在")
+
+    def _user_response(user_id: str) -> dict[str, Any]:
+        user = repository.get_user(user_id)
+        if user is None:
+            raise HTTPException(status_code=404, detail="用户不存在")
+        return user
+
+    def _role_response(role: dict[str, Any]) -> dict[str, Any]:
+        return {
+            **role,
+            "permissions": repository.list_role_permissions(role["id"]),
+        }
+
+    def _permission_pairs(
+        permissions: list[RolePermissionRequest],
+    ) -> list[tuple[str, str]]:
+        return [
+            (permission.permission_code, permission.data_scope)
+            for permission in permissions
+        ]
+
+    def _validate_permission_pairs(grants: list[tuple[str, str]]) -> None:
+        unknown = {code for code, _ in grants} - set(PERMISSIONS)
+        if unknown:
+            raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
+
+    def _replace_permissions(
+        role_id: str, permissions: list[RolePermissionRequest]
+    ) -> None:
+        role = repository.get_role(role_id)
+        if role is None:
+            raise KeyError(role_id)
+        grants = _permission_pairs(permissions)
+        _validate_permission_pairs(grants)
+        requested_codes = {code for code, _ in grants}
+        required_codes = set(permission_service.expand_permissions(grants)) - requested_codes
+        existing_codes = {
+            item["permission_code"]
+            for item in repository.list_role_permissions(role_id)
+        }
+        removed_dependencies = sorted(required_codes & existing_codes - requested_codes)
+        if removed_dependencies:
+            raise ValueError(
+                "仍被高级权限依赖，不能移除: " + ", ".join(removed_dependencies)
+            )
+        if role["code"].casefold() == "system_admin" and "*" not in requested_codes:
+            raise ValueError("系统管理员角色必须保留通配权限")
+        repository.replace_role_permissions(role_id, grants)
+
     def authenticated_response(
         user_id: str,
         request: Request,
         *,
         status_code: int,
         update_last_login: bool,
     ) -> JSONResponse:
         raw_token, _ = session_service.create_session(user_id, request)
         context = session_service.resolve_session(raw_token)
         if context is None:
             raise HTTPException(status_code=500, detail="无法创建登录会话")
+        principal = permission_service.principal_for_user(
+            context.principal.user_id, context.principal.session_id
+        )
+        if principal is None:
+            raise HTTPException(status_code=500, detail="无法创建登录会话")
+        context = SessionContext(principal=principal, csrf_token=context.csrf_token)
         if update_last_login:
             with repository.connect() as connection:
                 connection.execute(
                     "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (user_id,),
                 )
         response = JSONResponse(_auth_body(context), status_code=status_code)
         response.set_cookie(
             session_service.cookie_name,
             raw_token,
diff --git a/identity/dependencies.py b/identity/dependencies.py
new file mode 100644
index 0000000..fcc2d6d
--- /dev/null
+++ b/identity/dependencies.py
@@ -0,0 +1,109 @@
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
+import inspect
+from collections.abc import Awaitable, Callable
+
+from fastapi import Depends, HTTPException, Request
+
+from .permission_service import PermissionService
+from .schema import Principal
+
+
+ScopeResolver = Callable[
+    [PermissionService, Principal, Request], bool | Awaitable[bool]
+]
+
+
+def permission_service_for_request(request: Request) -> PermissionService:
+    service = getattr(request.state, "identity_permission_service", None)
+    if service is None:
+        service = getattr(request.app.state, "identity_permission_service", None)
+    if not isinstance(service, PermissionService):
+        raise RuntimeError("identity permission service is not configured")
+    return service
+
+
+def current_principal(
+    request: Request,
+    permission_service: PermissionService = Depends(permission_service_for_request),
+) -> Principal:
+    raw_token = request.cookies.get(permission_service.session_service.cookie_name)
+    principal = permission_service.principal_for_session(raw_token)
+    if principal is None:
+        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
+    return principal
+
+
+def require_permission(
+    code: str, scope_resolver: ScopeResolver | None = None
+) -> Callable[..., Awaitable[Principal]]:
+    async def dependency(
+        request: Request,
+        principal: Principal = Depends(current_principal),
+        permission_service: PermissionService = Depends(permission_service_for_request),
+    ) -> Principal:
+        if not principal.is_system_admin and code not in principal.permissions:
+            raise HTTPException(status_code=403, detail="没有执行此操作的权限")
+        if scope_resolver is not None:
+            allowed = scope_resolver(permission_service, principal, request)
+            if inspect.isawaitable(allowed):
+                allowed = await allowed
+            if not allowed:
+                raise HTTPException(status_code=403, detail="没有访问此数据的权限")
+        return principal
+
+    return dependency
+
+
+def require_page(code: str) -> Callable[..., Awaitable[Principal]]:
+    return require_permission(code)
+
+
+def task_scope(path_parameter: str = "task_id") -> ScopeResolver:
+    def resolve(
+        service: PermissionService, principal: Principal, request: Request
+    ) -> bool:
+        return service.can_access_task(principal, request.path_params[path_parameter])
+
+    return resolve
+
+
+def assigned_project_scope(path_parameter: str = "project_id") -> ScopeResolver:
+    def resolve(
+        service: PermissionService, principal: Principal, request: Request
+    ) -> bool:
+        return service.can_access_assigned_project(
+            principal, request.path_params[path_parameter]
+        )
+
+    return resolve
+
+
+def managed_project_scope(path_parameter: str = "project_id") -> ScopeResolver:
+    def resolve(
+        service: PermissionService, principal: Principal, request: Request
+    ) -> bool:
+        return service.can_access_managed_project(
+            principal, request.path_params[path_parameter]
+        )
+
+    return resolve
+
+
+def all_data_scope() -> ScopeResolver:
+    def resolve(
+        service: PermissionService, principal: Principal, request: Request
+    ) -> bool:
+        del service, request
+        return PermissionService.can_access_all(principal)
+
+    return resolve
diff --git a/identity/permission_service.py b/identity/permission_service.py
new file mode 100644
index 0000000..adfb989
--- /dev/null
+++ b/identity/permission_service.py
@@ -0,0 +1,153 @@
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
+from .permission_catalog import PERMISSION_DEPENDENCIES
+from .repository import IdentityRepository
+from .schema import DATA_SCOPE_ORDER, Principal
+from .session_service import SessionService
+
+
+_MANAGED_PROJECT_ROLES = frozenset(
+    {"owner", "project_owner", "manager", "project_manager"}
+)
+
+
+class PermissionService:
+    def __init__(
+        self, repository: IdentityRepository, session_service: SessionService
+    ) -> None:
+        self.repository = repository
+        self.session_service = session_service
+
+    def principal_for_session(self, raw_token: str | None) -> Principal | None:
+        context = self.session_service.resolve_session(raw_token)
+        if context is None:
+            return None
+        return self.principal_for_user(
+            context.principal.user_id, context.principal.session_id
+        )
+
+    def principal_for_user(self, user_id: str, session_id: str) -> Principal | None:
+        with self.repository.connect() as connection:
+            user = connection.execute(
+                """
+                SELECT id, username, display_name, status, is_deleted
+                FROM users WHERE id = ?
+                """,
+                (user_id,),
+            ).fetchone()
+            if user is None or user["status"] != "active" or user["is_deleted"]:
+                return None
+            rows = connection.execute(
+                """
+                SELECT r.code, rp.permission_code, rp.data_scope
+                FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
+                LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
+                WHERE ur.user_id = ?
+                ORDER BY r.code, rp.permission_code
+                """,
+                (user_id,),
+            ).fetchall()
+
+        role_codes = tuple(sorted({row["code"] for row in rows}))
+        grants = [
+            (row["permission_code"], row["data_scope"])
+            for row in rows
+            if row["permission_code"] is not None
+        ]
+        expanded = self.expand_permissions(grants)
+        if "*" in expanded:
+            expanded = {"*": "all"}
+        data_scope = (
+            max(expanded.values(), key=DATA_SCOPE_ORDER.get) if expanded else "task"
+        )
+        return Principal(
+            user_id=user["id"],
+            username=user["username"],
+            display_name=user["display_name"],
+            role_codes=role_codes,
+            permissions=frozenset(expanded),
+            data_scope=data_scope,
+            session_id=session_id,
+        )
+
+    @staticmethod
+    def expand_permissions(
+        grants: list[tuple[str, str]] | tuple[tuple[str, str], ...],
+    ) -> dict[str, str]:
+        expanded: dict[str, str] = {}
+        pending = sorted(grants, reverse=True)
+        while pending:
+            code, scope = pending.pop()
+            current = expanded.get(code)
+            if current is not None and DATA_SCOPE_ORDER[current] >= DATA_SCOPE_ORDER[scope]:
+                continue
+            expanded[code] = scope
+            for dependency in sorted(PERMISSION_DEPENDENCIES.get(code, ())):
+                pending.append((dependency, scope))
+        return dict(sorted(expanded.items()))
+
+    def can_access_task(self, principal: Principal, task_id: str) -> bool:
+        with self.repository.connect() as connection:
+            task = connection.execute(
+                "SELECT assignee_id, project_id FROM workflow_tasks WHERE id = ?",
+                (task_id,),
+            ).fetchone()
+        if task is None:
+            return False
+        if principal.data_scope == "all":
+            return True
+        if principal.data_scope == "task":
+            return task["assignee_id"] == principal.user_id
+        if not task["project_id"]:
+            return task["assignee_id"] == principal.user_id
+        if principal.data_scope == "project":
+            return self.can_access_assigned_project(principal, task["project_id"])
+        return self.can_access_managed_project(principal, task["project_id"])
+
+    def can_access_assigned_project(
+        self, principal: Principal, project_id: str
+    ) -> bool:
+        if principal.data_scope == "all":
+            return True
+        if DATA_SCOPE_ORDER[principal.data_scope] < DATA_SCOPE_ORDER["project"]:
+            return False
+        with self.repository.connect() as connection:
+            row = connection.execute(
+                """
+                SELECT 1 FROM project_members
+                WHERE project_id = ? AND user_id = ? AND status = 'active'
+                """,
+                (project_id, principal.user_id),
+            ).fetchone()
+        return row is not None
+
+    def can_access_managed_project(
+        self, principal: Principal, project_id: str
+    ) -> bool:
+        if principal.data_scope == "all":
+            return True
+        if DATA_SCOPE_ORDER[principal.data_scope] < DATA_SCOPE_ORDER["managed_projects"]:
+            return False
+        with self.repository.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT project_role FROM project_members
+                WHERE project_id = ? AND user_id = ? AND status = 'active'
+                """,
+                (project_id, principal.user_id),
+            ).fetchall()
+        return any(row["project_role"] in _MANAGED_PROJECT_ROLES for row in rows)
+
+    @staticmethod
+    def can_access_all(principal: Principal) -> bool:
+        return principal.data_scope == "all"
diff --git a/identity/repository.py b/identity/repository.py
index e579c48..e6fb28b 100644
--- a/identity/repository.py
+++ b/identity/repository.py
@@ -3,20 +3,21 @@
 #
 # This file is part of MediaCrawler project.
 # Repository: https://github.com/NanmiCoder/MediaCrawler
 # GitHub: https://github.com/NanmiCoder
 # Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
 #
 
 from __future__ import annotations
 
 import sqlite3
+import uuid
 from contextlib import contextmanager
 from pathlib import Path
 from typing import Any, Iterator
 
 from .permission_catalog import DEFAULT_ROLES, PERMISSIONS, PERMISSION_DEPENDENCIES
 
 
 SCHEMA_SQL = """
 CREATE TABLE IF NOT EXISTS users (
     id TEXT PRIMARY KEY,
@@ -237,55 +238,305 @@ class IdentityRepository:
                         ON CONFLICT(role_id, permission_code) DO UPDATE SET
                             data_scope = excluded.data_scope
                         """,
                         (stored_role_id, permission_code, data_scope),
                     )
 
     @staticmethod
     def _expanded_permissions(
         permissions: tuple[tuple[str, str], ...],
     ) -> tuple[tuple[str, str], ...]:
-        expanded = dict(permissions)
-        pending = list(permissions)
+        from .schema import DATA_SCOPE_ORDER
+
+        expanded: dict[str, str] = {}
+        pending = sorted(permissions, reverse=True)
         while pending:
             permission_code, data_scope = pending.pop()
-            for dependency in PERMISSION_DEPENDENCIES.get(permission_code, set()):
-                if dependency not in expanded:
-                    expanded[dependency] = data_scope
-                    pending.append((dependency, data_scope))
-        return tuple(expanded.items())
+            current_scope = expanded.get(permission_code)
+            if current_scope is not None and (
+                DATA_SCOPE_ORDER[current_scope] >= DATA_SCOPE_ORDER[data_scope]
+            ):
+                continue
+            expanded[permission_code] = data_scope
+            for dependency in sorted(PERMISSION_DEPENDENCIES.get(permission_code, ())):
+                pending.append((dependency, data_scope))
+        if "*" in expanded:
+            return (("*", "all"),)
+        return tuple(sorted(expanded.items()))
 
     def list_roles(self) -> list[dict[str, Any]]:
         with self.connect() as connection:
             rows = connection.execute("SELECT * FROM roles ORDER BY code").fetchall()
         return [self._role_dict(row) for row in rows]
 
     def get_role_by_code(self, code: str) -> dict[str, Any] | None:
         with self.connect() as connection:
             row = connection.execute(
                 "SELECT * FROM roles WHERE code = ? COLLATE NOCASE", (code,)
             ).fetchone()
         return self._role_dict(row) if row else None
 
+    def get_role(self, role_id: str) -> dict[str, Any] | None:
+        with self.connect() as connection:
+            row = connection.execute(
+                "SELECT * FROM roles WHERE id = ?", (role_id,)
+            ).fetchone()
+        return self._role_dict(row) if row else None
+
+    def create_role(
+        self, code: str, name: str, description: str, *, is_system: bool = False
+    ) -> dict[str, Any]:
+        role_id = str(uuid.uuid4())
+        with self.connect() as connection:
+            connection.execute(
+                """
+                INSERT INTO roles (id, code, name, description, is_system)
+                VALUES (?, ?, ?, ?, ?)
+                """,
+                (role_id, code, name, description, int(is_system)),
+            )
+        role = self.get_role(role_id)
+        assert role is not None
+        return role
+
+    def update_role(
+        self,
+        role_id: str,
+        *,
+        name: str | None = None,
+        description: str | None = None,
+        is_enabled: bool | None = None,
+    ) -> dict[str, Any]:
+        with self.connect() as connection:
+            connection.execute("BEGIN IMMEDIATE")
+            role = connection.execute(
+                "SELECT * FROM roles WHERE id = ?", (role_id,)
+            ).fetchone()
+            if role is None:
+                raise KeyError(role_id)
+            if (
+                role["code"].casefold() == "system_admin"
+                and role["is_enabled"]
+                and is_enabled is False
+            ):
+                active_admins = connection.execute(
+                    """
+                    SELECT COUNT(DISTINCT u.id)
+                    FROM users AS u
+                    JOIN user_roles AS ur ON ur.user_id = u.id
+                    WHERE ur.role_id = ? AND u.status = 'active' AND u.is_deleted = 0
+                    """,
+                    (role_id,),
+                ).fetchone()[0]
+                if active_admins:
+                    raise ValueError("系统必须至少保留一个启用的系统管理员")
+            connection.execute(
+                """
+                UPDATE roles
+                SET name = COALESCE(?, name),
+                    description = COALESCE(?, description),
+                    is_enabled = COALESCE(?, is_enabled),
+                    updated_at = CURRENT_TIMESTAMP
+                WHERE id = ?
+                """,
+                (
+                    name,
+                    description,
+                    int(is_enabled) if is_enabled is not None else None,
+                    role_id,
+                ),
+            )
+        updated = self.get_role(role_id)
+        assert updated is not None
+        return updated
+
     def list_role_permissions(self, role_id: str) -> list[dict[str, str]]:
         with self.connect() as connection:
             rows = connection.execute(
                 """
                 SELECT permission_code, data_scope
                 FROM role_permissions
                 WHERE role_id = ?
                 ORDER BY permission_code
                 """,
                 (role_id,),
             ).fetchall()
         return [dict(row) for row in rows]
 
+    def replace_role_permissions(
+        self, role_id: str, permissions: list[tuple[str, str]]
+    ) -> list[dict[str, str]]:
+        expanded = self._expanded_permissions(tuple(permissions))
+        with self.connect() as connection:
+            connection.execute("BEGIN IMMEDIATE")
+            if connection.execute(
+                "SELECT 1 FROM roles WHERE id = ?", (role_id,)
+            ).fetchone() is None:
+                raise KeyError(role_id)
+            known_permissions = {
+                row["code"]
+                for row in connection.execute(
+                    "SELECT code FROM permissions"
+                ).fetchall()
+            }
+            unknown = {code for code, _ in expanded} - known_permissions
+            if unknown:
+                raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
+            connection.execute(
+                "DELETE FROM role_permissions WHERE role_id = ?", (role_id,)
+            )
+            connection.executemany(
+                """
+                INSERT INTO role_permissions (role_id, permission_code, data_scope)
+                VALUES (?, ?, ?)
+                """,
+                [(role_id, code, scope) for code, scope in expanded],
+            )
+        return self.list_role_permissions(role_id)
+
+    def list_users(self) -> list[dict[str, Any]]:
+        with self.connect() as connection:
+            rows = connection.execute(
+                "SELECT * FROM users WHERE is_deleted = 0 ORDER BY normalized_username"
+            ).fetchall()
+        return [self._user_dict(row) for row in rows]
+
+    def get_user(self, user_id: str) -> dict[str, Any] | None:
+        with self.connect() as connection:
+            row = connection.execute(
+                "SELECT * FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
+            ).fetchone()
+        return self._user_dict(row) if row else None
+
+    def create_user(
+        self, username: str, display_name: str, password_hash: str
+    ) -> dict[str, Any]:
+        user_id = str(uuid.uuid4())
+        with self.connect() as connection:
+            connection.execute(
+                """
+                INSERT INTO users
+                    (id, username, normalized_username, display_name, password_hash)
+                VALUES (?, ?, ?, ?, ?)
+                """,
+                (user_id, username, username.casefold(), display_name, password_hash),
+            )
+        user = self.get_user(user_id)
+        assert user is not None
+        return user
+
+    def update_user(
+        self,
+        user_id: str,
+        *,
+        display_name: str | None = None,
+        status: str | None = None,
+    ) -> dict[str, Any]:
+        if status == "disabled":
+            self.disable_user(user_id)
+        else:
+            with self.connect() as connection:
+                if connection.execute(
+                    "SELECT 1 FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
+                ).fetchone() is None:
+                    raise KeyError(user_id)
+                connection.execute(
+                    """
+                    UPDATE users
+                    SET display_name = COALESCE(?, display_name),
+                        status = COALESCE(?, status),
+                        updated_at = CURRENT_TIMESTAMP
+                    WHERE id = ?
+                    """,
+                    (display_name, status, user_id),
+                )
+        if display_name is not None and status == "disabled":
+            with self.connect() as connection:
+                connection.execute(
+                    "UPDATE users SET display_name = ? WHERE id = ?",
+                    (display_name, user_id),
+                )
+        user = self.get_user(user_id)
+        assert user is not None
+        return user
+
+    def replace_user_roles(
+        self, user_id: str, role_ids: list[str]
+    ) -> list[str]:
+        unique_role_ids = list(dict.fromkeys(role_ids))
+        with self.connect() as connection:
+            connection.execute("BEGIN IMMEDIATE")
+            user = connection.execute(
+                "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
+            ).fetchone()
+            if user is None or user["is_deleted"]:
+                raise KeyError(user_id)
+            role_placeholders = ",".join("?" for _ in unique_role_ids)
+            roles = connection.execute(
+                f"SELECT id, code, is_enabled FROM roles "
+                f"WHERE id IN ({role_placeholders})"
+                if unique_role_ids
+                else "SELECT id, code, is_enabled FROM roles WHERE 0",
+                unique_role_ids,
+            ).fetchall()
+            if len(roles) != len(unique_role_ids):
+                raise ValueError("角色不存在")
+            currently_admin = connection.execute(
+                """
+                SELECT 1 FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id
+                WHERE ur.user_id = ? AND r.code = 'system_admin' AND r.is_enabled = 1
+                """,
+                (user_id,),
+            ).fetchone() is not None
+            remains_admin = any(
+                role["code"].casefold() == "system_admin" and role["is_enabled"]
+                for role in roles
+            )
+            if (
+                currently_admin
+                and user["status"] == "active"
+                and not remains_admin
+            ):
+                other_admins = connection.execute(
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
+                if other_admins < 1:
+                    raise ValueError("系统必须至少保留一个启用的系统管理员")
+            connection.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
+            connection.executemany(
+                "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
+                [(user_id, role_id) for role_id in unique_role_ids],
+            )
+        return sorted(role["code"] for role in roles)
+
+    def user_role_codes(self, user_id: str) -> list[str]:
+        with self.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT r.code FROM user_roles AS ur
+                JOIN roles AS r ON r.id = ur.role_id
+                WHERE ur.user_id = ? ORDER BY r.code
+                """,
+                (user_id,),
+            ).fetchall()
+        return [row["code"] for row in rows]
+
     def count_active_system_admins(
         self,
         excluding_user_id: str | None = None,
         removing_role_id: str | None = None,
     ) -> int:
         conditions = [
             "u.status = 'active'",
             "u.is_deleted = 0",
             "r.code = 'system_admin'",
             "r.is_enabled = 1",
@@ -367,10 +618,19 @@ class IdentityRepository:
                 "WHERE user_id = ? AND revoked_at IS NULL",
                 (user_id,),
             )
 
     @staticmethod
     def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
         role = dict(row)
         role["is_system"] = bool(role["is_system"])
         role["is_enabled"] = bool(role["is_enabled"])
         return role
+
+    def _user_dict(self, row: sqlite3.Row) -> dict[str, Any]:
+        user = dict(row)
+        user["role_codes"] = self.user_role_codes(user["id"])
+        user.pop("password_hash", None)
+        user.pop("normalized_username", None)
+        user.pop("is_deleted", None)
+        user.pop("updated_at", None)
+        return user
diff --git a/tests/test_identity_admin_api.py b/tests/test_identity_admin_api.py
new file mode 100644
index 0000000..54b4525
--- /dev/null
+++ b/tests/test_identity_admin_api.py
@@ -0,0 +1,285 @@
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
+from pathlib import Path
+
+import pytest
+from fastapi import FastAPI
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
+def app(repository: IdentityRepository) -> FastAPI:
+    app = FastAPI()
+    app.include_router(build_identity_router(repository, SessionService(repository)))
+    return app
+
+
+def setup_admin(client: TestClient, username: str = "admin") -> str:
+    response = client.post(
+        "/api/auth/setup",
+        json={
+            "username": username,
+            "display_name": "管理员",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    assert response.status_code == 201
+    return response.json()["csrf_token"]
+
+
+def login_writer(client: TestClient, repository: IdentityRepository) -> str:
+    writer = repository.create_user(
+        "writer", "编剧", PasswordService().hash("Writer-Strong-123!")
+    )
+    role = repository.get_role_by_code("screenwriter")
+    assert role is not None
+    repository.replace_user_roles(writer["id"], [role["id"]])
+    response = client.post(
+        "/api/auth/login",
+        json={"username": "writer", "password": "Writer-Strong-123!"},
+    )
+    assert response.status_code == 200
+    return response.json()["csrf_token"]
+
+
+@pytest.mark.parametrize(
+    ("method", "path", "json"),
+    [
+        ("get", "/api/admin/users", None),
+        (
+            "post",
+            "/api/admin/users",
+            {
+                "username": "new-user",
+                "display_name": "新用户",
+                "password": "New-User-123!",
+                "role_ids": [],
+            },
+        ),
+        (
+            "post",
+            "/api/admin/roles/role:screenwriter/clone",
+            {"code": "writer_copy", "name": "编剧副本"},
+        ),
+        (
+            "put",
+            "/api/admin/roles/role:screenwriter/permissions",
+            {"permissions": [{"permission_code": "rank.read", "data_scope": "task"}]},
+        ),
+    ],
+)
+def test_non_admin_cannot_manage_users_or_roles(
+    app: FastAPI,
+    repository: IdentityRepository,
+    method: str,
+    path: str,
+    json: dict | None,
+):
+    client = TestClient(app)
+    csrf_token = login_writer(client, repository)
+
+    response = client.request(
+        method, path, json=json, headers={"X-CSRF-Token": csrf_token}
+    )
+
+    assert response.status_code == 403
+    assert response.json() == {"detail": "没有执行此操作的权限"}
+
+
+def test_admin_can_create_and_list_users_and_roles(
+    app: FastAPI, repository: IdentityRepository
+):
+    client = TestClient(app)
+    csrf_token = setup_admin(client)
+    screenwriter = repository.get_role_by_code("screenwriter")
+    assert screenwriter is not None
+
+    created_user = client.post(
+        "/api/admin/users",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "username": "new-writer",
+            "display_name": "新编剧",
+            "password": "New-Writer-123!",
+            "role_ids": [screenwriter["id"]],
+        },
+    )
+    created_role = client.post(
+        "/api/admin/roles",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "code": "custom_writer",
+            "name": "定制编剧",
+            "permissions": [
+                {"permission_code": "beats.edit", "data_scope": "project"}
+            ],
+        },
+    )
+
+    assert created_user.status_code == 201
+    assert created_user.json()["role_codes"] == ["screenwriter"]
+    assert any(
+        user["username"] == "new-writer"
+        for user in client.get("/api/admin/users").json()
+    )
+    assert created_role.status_code == 201
+    grants = {
+        grant["permission_code"] for grant in created_role.json()["permissions"]
+    }
+    assert {"beats.edit", "beats.read", "outline.read.approved", "page.writing"} <= grants
+    assert any(
+        role["code"] == "custom_writer"
+        for role in client.get("/api/admin/roles").json()
+    )
+
+
+def test_admin_can_clone_role_and_replace_permissions(
+    app: FastAPI, repository: IdentityRepository
+):
+    client = TestClient(app)
+    csrf_token = setup_admin(client)
+    source = repository.get_role_by_code("screenwriter")
+    assert source is not None
+
+    cloned = client.post(
+        f"/api/admin/roles/{source['id']}/clone",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"code": "writer_copy", "name": "编剧副本"},
+    )
+    assert cloned.status_code == 201
+    assert cloned.json()["permissions"] == [
+        {
+            "permission_code": item["permission_code"],
+            "data_scope": item["data_scope"],
+        }
+        for item in repository.list_role_permissions(source["id"])
+    ]
+
+    replaced = client.put(
+        f"/api/admin/roles/{cloned.json()['id']}/permissions",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "permissions": [
+                {"permission_code": "breakdown.edit", "data_scope": "task"}
+            ]
+        },
+    )
+    assert replaced.status_code == 200
+    assert {
+        grant["permission_code"] for grant in replaced.json()["permissions"]
+    } >= {"breakdown.edit", "breakdown.read", "topic.read", "page.selection"}
+
+
+def test_required_dependency_cannot_be_removed_while_parent_remains(
+    app: FastAPI, repository: IdentityRepository
+):
+    client = TestClient(app)
+    csrf_token = setup_admin(client)
+    role = repository.create_role("dependency_role", "依赖角色", "")
+    repository.replace_role_permissions(role["id"], [("beats.edit", "project")])
+
+    response = client.put(
+        f"/api/admin/roles/{role['id']}/permissions",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "permissions": [
+                {"permission_code": "beats.edit", "data_scope": "project"},
+                {
+                    "permission_code": "outline.read.approved",
+                    "data_scope": "project",
+                },
+                {"permission_code": "page.writing", "data_scope": "project"},
+            ]
+        },
+    )
+
+    assert response.status_code == 422
+    assert "beats.read" in response.json()["detail"]
+
+
+def test_last_admin_cannot_be_disabled_or_stripped_of_admin_role(app: FastAPI):
+    client = TestClient(app)
+    csrf_token = setup_admin(client)
+    admin = client.get("/api/auth/me").json()["user"]
+
+    disabled = client.patch(
+        f"/api/admin/users/{admin['id']}",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"status": "disabled"},
+    )
+    stripped = client.put(
+        f"/api/admin/users/{admin['id']}/roles",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"role_ids": []},
+    )
+
+    assert disabled.status_code == 409
+    assert stripped.status_code == 409
+    assert client.get("/api/auth/me").status_code == 200
+
+
+def test_admin_can_update_user_roles_and_reset_password(
+    app: FastAPI, repository: IdentityRepository
+):
+    client = TestClient(app)
+    csrf_token = setup_admin(client)
+    user = repository.create_user(
+        "reset-user", "重置用户", PasswordService().hash("Original-123!")
+    )
+    role = repository.get_role_by_code("screenwriter")
+    assert role is not None
+
+    roles = client.put(
+        f"/api/admin/users/{user['id']}/roles",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"role_ids": [role["id"]]},
+    )
+    reset = client.post(
+        f"/api/admin/users/{user['id']}/reset-password",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"password": "Replacement-456!"},
+    )
+
+    assert roles.status_code == 200
+    assert roles.json()["role_codes"] == ["screenwriter"]
+    assert reset.status_code == 204
+    assert client.post(
+        "/api/auth/login",
+        json={"username": "reset-user", "password": "Replacement-456!"},
+    ).status_code == 200
+
+
+def test_permissions_catalog_is_available_to_admin(app: FastAPI):
+    client = TestClient(app)
+    setup_admin(client)
+
+    response = client.get("/api/admin/permissions")
+
+    assert response.status_code == 200
+    beats_edit = next(item for item in response.json() if item["code"] == "beats.edit")
+    assert beats_edit["dependencies"] == [
+        "beats.read",
+        "outline.read.approved",
+        "page.writing",
+    ]
diff --git a/tests/test_identity_permissions.py b/tests/test_identity_permissions.py
new file mode 100644
index 0000000..e9b4b30
--- /dev/null
+++ b/tests/test_identity_permissions.py
@@ -0,0 +1,174 @@
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
+from pathlib import Path
+
+import pytest
+from fastapi import Depends, FastAPI
+from fastapi.testclient import TestClient
+
+from identity.api import build_identity_router
+from identity.dependencies import assigned_project_scope, require_permission
+from identity.password_service import PasswordService
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.session_service import SessionService
+
+
+HASH = PasswordService().hash("Writer-Strong-123!")
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
+def session_service(repository: IdentityRepository) -> SessionService:
+    return SessionService(repository)
+
+
+@pytest.fixture
+def permission_service(
+    repository: IdentityRepository, session_service: SessionService
+) -> PermissionService:
+    return PermissionService(repository, session_service)
+
+
+def test_role_permission_dependencies_are_transitive(
+    repository: IdentityRepository, permission_service: PermissionService
+):
+    role = repository.create_role("custom_writer", "定制编剧", "")
+    repository.replace_role_permissions(role["id"], [("beats.edit", "project")])
+    user = repository.create_user("writer", "编剧", HASH)
+    repository.replace_user_roles(user["id"], [role["id"]])
+
+    principal = permission_service.principal_for_user(user["id"], "session-1")
+
+    assert principal is not None
+    assert {
+        "beats.edit",
+        "beats.read",
+        "outline.read.approved",
+        "page.writing",
+    } <= principal.permissions
+    assert principal.data_scope == "project"
+
+
+def test_multiple_roles_union_permissions_and_choose_maximum_scope(
+    repository: IdentityRepository, permission_service: PermissionService
+):
+    task_role = repository.create_role("task_reader", "任务读取", "")
+    project_role = repository.create_role("project_editor", "项目编辑", "")
+    repository.replace_role_permissions(task_role["id"], [("rank.read", "task")])
+    repository.replace_role_permissions(
+        project_role["id"], [("topic.edit", "managed_projects")]
+    )
+    user = repository.create_user("multi", "多角色", HASH)
+    repository.replace_user_roles(user["id"], [task_role["id"], project_role["id"]])
+
+    principal = permission_service.principal_for_user(user["id"], "session-1")
+
+    assert principal is not None
+    assert {"rank.read", "topic.edit"} <= principal.permissions
+    assert principal.data_scope == "managed_projects"
+
+
+def test_wildcard_always_has_all_scope(
+    repository: IdentityRepository, permission_service: PermissionService
+):
+    role = repository.create_role("operator_admin", "运维管理员", "")
+    repository.replace_role_permissions(role["id"], [("*", "task")])
+    user = repository.create_user("operator", "运维", HASH)
+    repository.replace_user_roles(user["id"], [role["id"]])
+
+    principal = permission_service.principal_for_user(user["id"], "session-1")
+
+    assert principal is not None
+    assert principal.permissions == frozenset({"*"})
+    assert principal.data_scope == "all"
+    assert principal.is_system_admin is True
+
+
+def test_role_changes_take_effect_for_an_existing_session(
+    repository: IdentityRepository, session_service: SessionService
+):
+    role = repository.create_role("live_role", "即时角色", "")
+    repository.replace_role_permissions(role["id"], [("rank.read", "task")])
+    user = repository.create_user("live", "即时用户", HASH)
+    repository.replace_user_roles(user["id"], [role["id"]])
+    app = FastAPI()
+    app.include_router(build_identity_router(repository, session_service))
+    client = TestClient(app)
+    login = client.post(
+        "/api/auth/login",
+        json={"username": "live", "password": "Writer-Strong-123!"},
+    )
+    raw_token = client.cookies.get(session_service.cookie_name)
+    assert login.status_code == 200
+    assert raw_token is not None
+    service = PermissionService(repository, session_service)
+    assert "topic.read" not in service.principal_for_session(raw_token).permissions
+
+    repository.replace_role_permissions(role["id"], [("topic.read", "project")])
+
+    principal = service.principal_for_session(raw_token)
+    assert principal is not None
+    assert "topic.read" in principal.permissions
+    assert "rank.read" not in principal.permissions
+    assert principal.data_scope == "project"
+
+
+def test_assigned_project_scope_queries_current_membership(
+    repository: IdentityRepository, session_service: SessionService
+):
+    role = repository.create_role("project_reader", "项目读取", "")
+    repository.replace_role_permissions(
+        role["id"], [("project.read.assigned", "project")]
+    )
+    user = repository.create_user("member", "项目成员", HASH)
+    repository.replace_user_roles(user["id"], [role["id"]])
+    app = FastAPI()
+    service = PermissionService(repository, session_service)
+    app.state.identity_permission_service = service
+    app.include_router(build_identity_router(repository, session_service))
+
+    @app.get("/projects/{project_id}")
+    async def project(
+        _=Depends(
+            require_permission(
+                "project.read.assigned", assigned_project_scope("project_id")
+            )
+        ),
+    ):
+        return {"ok": True}
+
+    client = TestClient(app)
+    login = client.post(
+        "/api/auth/login",
+        json={"username": "member", "password": "Writer-Strong-123!"},
+    )
+    assert login.status_code == 200
+    assert client.get("/projects/client-claimed-project").status_code == 403
+
+    with repository.connect() as connection:
+        connection.execute(
+            """
+            INSERT INTO project_members
+                (id, project_id, user_id, project_role, status)
+            VALUES ('membership-1', 'assigned-project', ?, 'writer', 'active')
+            """,
+            (user["id"],),
+        )
+
+    assert client.get("/projects/assigned-project").status_code == 200
