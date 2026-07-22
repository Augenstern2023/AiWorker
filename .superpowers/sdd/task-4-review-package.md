# Commits

65d27d9 feat: add security audit and csrf protection

# Stat

 WebGUI.py                   |  80 +++++++++--
 audit/__init__.py           |  15 ++
 audit/api.py                |  53 +++++++
 audit/repository.py         | 171 +++++++++++++++++++++++
 audit/service.py            | 155 +++++++++++++++++++++
 identity/api.py             | 220 ++++++++++++++++++++++++++---
 tests/test_audit_service.py | 331 ++++++++++++++++++++++++++++++++++++++++++++
 tests/test_web_security.py  | 148 ++++++++++++++++++++
 8 files changed, 1145 insertions(+), 28 deletions(-)

# Diff

diff --git a/WebGUI.py b/WebGUI.py
index 879d733..23fd9c5 100644
--- a/WebGUI.py
+++ b/WebGUI.py
@@ -22,20 +22,21 @@ import time
 import json
 import asyncio
 import logging
 import mimetypes
 import re
 import threading
 import subprocess
 import webbrowser
 import queue
 import shutil
+import uuid
 from pathlib import Path
 from typing import List, Dict, Optional, Any
 from contextlib import asynccontextmanager
 from logging.handlers import RotatingFileHandler
 
 import uvicorn
 import httpx
 from fastapi import FastAPI, HTTPException, Request
 from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
 from fastapi.staticfiles import StaticFiles
@@ -61,20 +62,23 @@ from agents.douyin_drama_fallback import DouyinDramaFallbackClient, DouyinFallba
 from core.job import CrawlJob
 from rag.api import register_rag_routes
 from runtime.job_manager import JobConflictError, JobManager, JobNotFoundError
 
 
 WORKSPACE_ROOT = Path(__file__).resolve().parent
 WEB_LOG_LEVEL = os.getenv("AIWORKER_WEB_LOG_LEVEL", "INFO").strip().upper()
 WEB_LOG_MAX_BYTES = 5 * 1024 * 1024
 WEB_LOG_BACKUP_COUNT = 5
 WEB_LOG_PATH = WORKSPACE_ROOT / "logs" / "webgui.log"
+UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
+CSRF_EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/setup"})
+REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
 
 
 def configure_backend_logging(log_path: Path = WEB_LOG_PATH, log_level: str = WEB_LOG_LEVEL) -> logging.Logger:
     """Configure the Web GUI's rotating server log without changing global loggers."""
     logger = logging.getLogger("aiworker.web")
     level = getattr(logging, log_level.upper(), logging.INFO)
     logger.setLevel(level if isinstance(level, int) else logging.INFO)
     logger.propagate = False
     resolved_log_path = log_path.resolve()
     resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
@@ -94,20 +98,77 @@ def configure_backend_logging(log_path: Path = WEB_LOG_PATH, log_level: str = WE
     handler.setFormatter(logging.Formatter(
         "%(asctime)s %(levelname)s %(name)s - %(message)s",
         datefmt="%Y-%m-%d %H:%M:%S",
     ))
     logger.addHandler(handler)
     return logger
 
 
 backend_logger = configure_backend_logging()
 
+
+def configure_cors(target_app: FastAPI, origins: str | None = None) -> list[str]:
+    """Enable credentialed CORS only for explicitly configured origins."""
+    configured = os.getenv("AIWORKER_ALLOWED_ORIGINS", "") if origins is None else origins
+    allowed_origins = list(dict.fromkeys(
+        origin.strip() for origin in configured.split(",") if origin.strip()
+    ))
+    if "*" in allowed_origins:
+        raise ValueError("AIWORKER_ALLOWED_ORIGINS must contain explicit origins")
+    if allowed_origins:
+        target_app.add_middleware(
+            CORSMiddleware,
+            allow_origins=allowed_origins,
+            allow_credentials=True,
+            allow_methods=["*"],
+            allow_headers=["*"],
+        )
+    return allowed_origins
+
+
+def install_security_middleware(target_app: FastAPI, session_service=None) -> None:
+    """Install authenticated-write CSRF and outer request-ID middleware."""
+    if session_service is not None:
+        target_app.state.identity_session_service = session_service
+
+    async def enforce_csrf(request: Request, call_next):
+        if (
+            request.method.upper() in UNSAFE_HTTP_METHODS
+            and request.url.path not in CSRF_EXEMPT_PATHS
+        ):
+            service = getattr(request.app.state, "identity_session_service", None)
+            cookie_name = getattr(service, "cookie_name", "aiworker_session")
+            raw_token = request.cookies.get(cookie_name)
+            if (
+                service is not None
+                and raw_token
+                and service.resolve_session(raw_token) is not None
+                and not service.verify_csrf(
+                    raw_token, request.headers.get("X-CSRF-Token")
+                )
+            ):
+                return JSONResponse(status_code=403, content={"detail": "CSRF 校验失败"})
+        return await call_next(request)
+
+    async def attach_request_id(request: Request, call_next):
+        supplied = request.headers.get("X-Request-ID", "")
+        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
+        request.state.request_id = request_id
+        response = await call_next(request)
+        response.headers["X-Request-ID"] = request_id
+        return response
+
+    # Starlette executes the last registered middleware first. Register the
+    # request ID last so logging and every inner route can rely on it.
+    target_app.middleware("http")(enforce_csrf)
+    target_app.middleware("http")(attach_request_id)
+
 # Try to import optional dependencies
 try:
     import dict_to_小鸡 as excel_converter
 except ImportError:
     excel_converter = None
 
 # Global State
 class AppState:
     def __init__(self):
         self.process: Optional[subprocess.Popen] = None
@@ -167,40 +228,41 @@ async def lifespan(app: FastAPI):
 
 app = FastAPI(lifespan=lifespan)
 
 
 @app.middleware("http")
 async def log_backend_requests(request, call_next):
     started_at = time.perf_counter()
     try:
         response = await call_next(request)
     except Exception:
-        backend_logger.exception("Unhandled request error: %s %s", request.method, request.url.path)
+        backend_logger.exception(
+            "Unhandled request error: %s %s request_id=%s",
+            request.method,
+            request.url.path,
+            getattr(request.state, "request_id", None),
+        )
         raise
 
     duration_ms = (time.perf_counter() - started_at) * 1000
     backend_logger.info(
-        "%s %s status=%s duration_ms=%.1f",
+        "%s %s status=%s duration_ms=%.1f request_id=%s",
         request.method,
         request.url.path,
         response.status_code,
         duration_ms,
+        getattr(request.state, "request_id", None),
     )
     return response
 
-app.add_middleware(
-    CORSMiddleware,
-    allow_origins=["*"],
-    allow_credentials=True,
-    allow_methods=["*"],
-    allow_headers=["*"],
-)
+install_security_middleware(app)
+configure_cors(app)
 register_rag_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
 register_analytics_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
 register_today_rank_routes(app, root=Path(__file__).resolve().parent)
 register_creative_routes(
     app,
     root=Path(__file__).resolve().parent,
     runtime_dir=Path(__file__).resolve().parent / ".runtime",
     job_manager=job_manager,
 )
 
diff --git a/audit/__init__.py b/audit/__init__.py
new file mode 100644
index 0000000..f43ce73
--- /dev/null
+++ b/audit/__init__.py
@@ -0,0 +1,15 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+"""Security and business audit persistence."""
+
+from .repository import AuditRepository
+from .service import AuditService
+
+__all__ = ["AuditRepository", "AuditService"]
diff --git a/audit/api.py b/audit/api.py
new file mode 100644
index 0000000..0741b5e
--- /dev/null
+++ b/audit/api.py
@@ -0,0 +1,53 @@
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
+from typing import Any
+
+from fastapi import APIRouter, Depends, Query
+
+from identity.dependencies import require_permission
+from identity.schema import Principal
+
+from .service import AuditService
+
+
+def build_audit_router(audit_service: AuditService) -> APIRouter:
+    router = APIRouter()
+    audit_read = require_permission("audit.read")
+
+    @router.get("/api/admin/audit")
+    def list_audit_events(
+        actor_user_id: str | None = None,
+        action: str | None = None,
+        target_type: str | None = None,
+        target_id: str | None = None,
+        project_id: str | None = None,
+        result: str | None = None,
+        created_from: str | None = None,
+        created_to: str | None = None,
+        limit: int = Query(default=100, ge=1, le=500),
+        offset: int = Query(default=0, ge=0),
+        _: Principal = Depends(audit_read),
+    ) -> list[dict[str, Any]]:
+        return audit_service.query(
+            actor_user_id=actor_user_id,
+            action=action,
+            target_type=target_type,
+            target_id=target_id,
+            project_id=project_id,
+            result=result,
+            created_from=created_from,
+            created_to=created_to,
+            limit=limit,
+            offset=offset,
+        )
+
+    return router
diff --git a/audit/repository.py b/audit/repository.py
new file mode 100644
index 0000000..f4dc7fb
--- /dev/null
+++ b/audit/repository.py
@@ -0,0 +1,171 @@
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
+import json
+from typing import Any
+
+from identity.repository import IdentityRepository
+
+
+_AUDIT_COLUMNS = {
+    "actor_user_id": "TEXT REFERENCES users(id) ON DELETE SET NULL",
+    "actor_role_snapshot_json": "TEXT NOT NULL DEFAULT '[]'",
+    "target_type": "TEXT",
+    "target_id": "TEXT",
+    "before_summary_json": "TEXT",
+    "after_summary_json": "TEXT",
+    "request_id": "TEXT",
+    "user_agent": "TEXT",
+    "result": "TEXT NOT NULL DEFAULT 'success'",
+}
+
+
+class AuditRepository:
+    """Persists normalized audit events in the shared system database."""
+
+    def __init__(self, identity_repository: IdentityRepository) -> None:
+        self.identity_repository = identity_repository
+        self._migrate_schema()
+
+    def _migrate_schema(self) -> None:
+        with self.identity_repository.connect() as connection:
+            existing = {
+                row["name"]
+                for row in connection.execute("PRAGMA table_info(audit_logs)")
+            }
+            for column, definition in _AUDIT_COLUMNS.items():
+                if column not in existing:
+                    connection.execute(
+                        f"ALTER TABLE audit_logs ADD COLUMN {column} {definition}"
+                    )
+            connection.execute(
+                "UPDATE audit_logs SET actor_user_id = actor_id "
+                "WHERE actor_user_id IS NULL AND actor_id IS NOT NULL"
+            )
+            connection.execute(
+                "UPDATE audit_logs SET target_type = resource_type "
+                "WHERE target_type IS NULL"
+            )
+            connection.execute(
+                "CREATE INDEX IF NOT EXISTS idx_audit_logs_action "
+                "ON audit_logs(action)"
+            )
+            connection.execute(
+                "CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user_id "
+                "ON audit_logs(actor_user_id)"
+            )
+
+    def insert(self, event: dict[str, Any]) -> str:
+        with self.identity_repository.connect() as connection:
+            connection.execute(
+                """
+                INSERT INTO audit_logs (
+                    id, actor_id, actor_user_id, actor_role_snapshot_json,
+                    action, resource_type, target_type, resource_id, target_id,
+                    project_id, reason, details_json, before_summary_json,
+                    after_summary_json, request_id, ip_address, user_agent,
+                    result, created_at
+                ) VALUES (
+                    :id, :actor_user_id, :actor_user_id,
+                    :actor_role_snapshot_json, :action, :target_type,
+                    :target_type, :target_id, :target_id, :project_id, :reason,
+                    '{}', :before_summary_json, :after_summary_json, :request_id,
+                    :ip_address, :user_agent, :result, CURRENT_TIMESTAMP
+                )
+                """,
+                event,
+            )
+        return str(event["id"])
+
+    def get(self, event_id: str) -> dict[str, Any] | None:
+        with self.identity_repository.connect() as connection:
+            row = connection.execute(
+                "SELECT * FROM audit_logs WHERE id = ?", (event_id,)
+            ).fetchone()
+        return self._event_dict(row) if row is not None else None
+
+    def query(
+        self,
+        *,
+        actor_user_id: str | None = None,
+        action: str | None = None,
+        target_type: str | None = None,
+        target_id: str | None = None,
+        project_id: str | None = None,
+        result: str | None = None,
+        created_from: str | None = None,
+        created_to: str | None = None,
+        limit: int = 100,
+        offset: int = 0,
+    ) -> list[dict[str, Any]]:
+        filters = {
+            "actor_user_id": actor_user_id,
+            "action": action,
+            "target_type": target_type,
+            "target_id": target_id,
+            "project_id": project_id,
+            "result": result,
+        }
+        clauses: list[str] = []
+        parameters: list[Any] = []
+        for column, value in filters.items():
+            if value is not None:
+                clauses.append(f"{column} = ?")
+                parameters.append(value)
+        if created_from is not None:
+            clauses.append("created_at >= ?")
+            parameters.append(created_from)
+        if created_to is not None:
+            clauses.append("created_at <= ?")
+            parameters.append(created_to)
+        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
+        parameters.extend((max(1, min(limit, 500)), max(0, offset)))
+        with self.identity_repository.connect() as connection:
+            rows = connection.execute(
+                f"""
+                SELECT * FROM audit_logs
+                {where}
+                ORDER BY created_at DESC, id DESC
+                LIMIT ? OFFSET ?
+                """,
+                parameters,
+            ).fetchall()
+        return [self._event_dict(row) for row in rows]
+
+    @staticmethod
+    def _event_dict(row: Any) -> dict[str, Any]:
+        return {
+            "id": row["id"],
+            "actor_user_id": row["actor_user_id"],
+            "actor_role_snapshot": json.loads(
+                row["actor_role_snapshot_json"] or "[]"
+            ),
+            "action": row["action"],
+            "target_type": row["target_type"],
+            "target_id": row["target_id"],
+            "project_id": row["project_id"],
+            "before_summary": (
+                json.loads(row["before_summary_json"])
+                if row["before_summary_json"] is not None
+                else None
+            ),
+            "after_summary": (
+                json.loads(row["after_summary_json"])
+                if row["after_summary_json"] is not None
+                else None
+            ),
+            "request_id": row["request_id"],
+            "ip_address": row["ip_address"],
+            "user_agent": row["user_agent"],
+            "reason": row["reason"],
+            "result": row["result"],
+            "created_at": row["created_at"],
+        }
diff --git a/audit/service.py b/audit/service.py
new file mode 100644
index 0000000..6063512
--- /dev/null
+++ b/audit/service.py
@@ -0,0 +1,155 @@
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
+import json
+import uuid
+from collections.abc import Mapping
+from typing import Any
+
+from fastapi import Request
+
+from identity.repository import IdentityRepository
+from identity.schema import Principal
+
+from .repository import AuditRepository
+
+
+MAX_SUMMARY_BYTES = 8192
+_SENSITIVE_KEY_PARTS = (
+    "password",
+    "token",
+    "secret",
+    "cookie",
+    "activation_code",
+    "api_key",
+)
+
+
+class AuditService:
+    def __init__(self, repository: IdentityRepository | AuditRepository) -> None:
+        self.repository = (
+            repository
+            if isinstance(repository, AuditRepository)
+            else AuditRepository(repository)
+        )
+
+    def record(
+        self,
+        *,
+        actor: Principal | None,
+        action: str,
+        target_type: str,
+        target_id: str | None,
+        before: Any,
+        after: Any,
+        reason: str | None,
+        result: str,
+        request: Request | None = None,
+        project_id: str | None = None,
+    ) -> str:
+        event_id = str(uuid.uuid4())
+        request_id, ip_address, user_agent = self._request_metadata(request)
+        self.repository.insert(
+            {
+                "id": event_id,
+                "actor_user_id": actor.user_id if actor is not None else None,
+                "actor_role_snapshot_json": json.dumps(
+                    list(actor.role_codes) if actor is not None else [],
+                    ensure_ascii=False,
+                    separators=(",", ":"),
+                ),
+                "action": action,
+                "target_type": target_type,
+                "target_id": target_id,
+                "project_id": project_id,
+                "before_summary_json": self._summary_json(before),
+                "after_summary_json": self._summary_json(after),
+                "request_id": request_id,
+                "ip_address": ip_address,
+                "user_agent": user_agent,
+                "reason": reason,
+                "result": result,
+            }
+        )
+        return event_id
+
+    def get(self, event_id: str) -> dict[str, Any] | None:
+        return self.repository.get(event_id)
+
+    def query(self, **filters: Any) -> list[dict[str, Any]]:
+        return self.repository.query(**filters)
+
+    @staticmethod
+    def _request_metadata(
+        request: Request | None,
+    ) -> tuple[str | None, str | None, str | None]:
+        if request is None:
+            return None, None, None
+        request_id = getattr(request.state, "request_id", None)
+        if request_id is None:
+            request_id = request.headers.get("X-Request-ID")
+        ip_address = request.client.host if request.client is not None else None
+        return request_id, ip_address, request.headers.get("user-agent")
+
+    @classmethod
+    def _summary_json(cls, value: Any) -> str | None:
+        if value is None:
+            return None
+        redacted = cls._redact(value)
+        serialized = json.dumps(
+            redacted, ensure_ascii=False, separators=(",", ":"), default=str
+        )
+        if len(serialized.encode("utf-8")) <= MAX_SUMMARY_BYTES:
+            return serialized
+
+        low = 0
+        high = len(serialized)
+        original_bytes = len(serialized.encode("utf-8"))
+        while low < high:
+            midpoint = (low + high + 1) // 2
+            bounded = json.dumps(
+                {
+                    "_truncated": True,
+                    "_original_bytes": original_bytes,
+                    "_preview": serialized[:midpoint],
+                },
+                ensure_ascii=False,
+            )
+            if len(bounded.encode("utf-8")) <= MAX_SUMMARY_BYTES:
+                low = midpoint
+            else:
+                high = midpoint - 1
+        return json.dumps(
+            {
+                "_truncated": True,
+                "_original_bytes": original_bytes,
+                "_preview": serialized[:low],
+            },
+            ensure_ascii=False,
+            separators=(",", ":"),
+        )
+
+    @classmethod
+    def _redact(cls, value: Any) -> Any:
+        if isinstance(value, Mapping):
+            redacted: dict[str, Any] = {}
+            for key, item in value.items():
+                key_text = str(key)
+                normalized = key_text.casefold()
+                redacted[key_text] = (
+                    "[REDACTED]"
+                    if any(part in normalized for part in _SENSITIVE_KEY_PARTS)
+                    else cls._redact(item)
+                )
+            return redacted
+        if isinstance(value, (list, tuple, set, frozenset)):
+            return [cls._redact(item) for item in value]
+        return value
diff --git a/identity/api.py b/identity/api.py
index 38042b4..63030df 100644
--- a/identity/api.py
+++ b/identity/api.py
@@ -11,20 +11,22 @@ from __future__ import annotations
 
 import sqlite3
 import uuid
 from pathlib import Path
 from typing import Any
 
 from fastapi import APIRouter, Depends, HTTPException, Request, Response
 from fastapi.responses import HTMLResponse, JSONResponse
 from pydantic import BaseModel, Field
 
+from audit.service import AuditService
+
 from .dependencies import require_permission
 from .password_service import PasswordService
 from .permission_catalog import PERMISSIONS, PERMISSION_DEPENDENCIES
 from .permission_service import PermissionService
 from .repository import IdentityRepository
 from .schema import (
     Principal,
     RoleCreateRequest,
     RolePermissionRequest,
     RoleUpdateRequest,
@@ -66,32 +68,58 @@ class RolePermissionsRequest(BaseModel):
     permissions: list[RolePermissionRequest]
 
 
 class RoleCloneRequest(BaseModel):
     code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
     name: str = Field(min_length=1, max_length=128)
     description: str | None = None
 
 
 def build_identity_router(
-    repository: IdentityRepository, session_service: SessionService
+    repository: IdentityRepository,
+    session_service: SessionService,
+    audit_service: AuditService | None = None,
 ) -> APIRouter:
     permission_service = PermissionService(repository, session_service)
 
     def bind_permission_service(request: Request) -> None:
         request.state.identity_permission_service = permission_service
 
     router = APIRouter(dependencies=[Depends(bind_permission_service)])
     password_service = PasswordService()
     dummy_password_hash = password_service.hash("Dummy-Password-123!")
     repository.seed_defaults()
 
+    def record_audit(
+        *,
+        actor: Principal | None,
+        action: str,
+        target_type: str,
+        target_id: str | None,
+        before: Any,
+        after: Any,
+        result: str,
+        request: Request,
+    ) -> None:
+        if audit_service is not None:
+            audit_service.record(
+                actor=actor,
+                action=action,
+                target_type=target_type,
+                target_id=target_id,
+                before=before,
+                after=after,
+                reason=None,
+                result=result,
+                request=request,
+            )
+
     def session_context(request: Request, *, require_csrf: bool = False) -> SessionContext:
         raw_token = request.cookies.get(session_service.cookie_name)
         context = session_service.resolve_session(raw_token)
         if context is None:
             raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
         if require_csrf and not session_service.verify_csrf(
             raw_token or "", request.headers.get("X-CSRF-Token")
         ):
             raise HTTPException(status_code=403, detail="CSRF 校验失败")
         principal = permission_service.principal_for_user(
@@ -174,21 +202,25 @@ def build_identity_router(
                     connection.execute(
                         "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                         (user_id, role["id"]),
                     )
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="用户名已存在") from exc
         if setup_already_complete:
             raise HTTPException(status_code=409, detail="系统已经完成初始化")
 
         return authenticated_response(
-            user_id, request, status_code=201, update_last_login=True
+            user_id,
+            request,
+            status_code=201,
+            update_last_login=True,
+            audit_action="user.setup",
         )
 
     @router.post("/api/auth/login")
     def login(payload: LoginRequest, request: Request) -> JSONResponse:
         normalized_username = payload.username.strip().casefold()
         with repository.connect() as connection:
             user = connection.execute(
                 """
                 SELECT id, password_hash, status, is_deleted
                 FROM users WHERE normalized_username = ? COLLATE NOCASE
@@ -196,31 +228,55 @@ def build_identity_router(
                 (normalized_username,),
             ).fetchone()
         password_hash = user["password_hash"] if user is not None else dummy_password_hash
         password_matches = password_service.verify(password_hash, payload.password)
         if (
             user is None
             or not password_matches
             or user["status"] != "active"
             or user["is_deleted"]
         ):
+            record_audit(
+                actor=None,
+                action="auth.login",
+                target_type="user",
+                target_id=user["id"] if user is not None else normalized_username,
+                before=None,
+                after={"username": normalized_username},
+                result="failure",
+                request=request,
+            )
             raise HTTPException(status_code=401, detail=AUTHENTICATION_ERROR)
         return authenticated_response(
-            user["id"], request, status_code=200, update_last_login=True
+            user["id"],
+            request,
+            status_code=200,
+            update_last_login=True,
+            audit_action="auth.login",
         )
 
     @router.post("/api/auth/logout", status_code=204)
     def logout(request: Request) -> Response:
-        session_context(request, require_csrf=True)
+        context = session_context(request, require_csrf=True)
         raw_token = request.cookies.get(session_service.cookie_name)
         if raw_token:
             session_service.revoke_session(raw_token)
+        record_audit(
+            actor=context.principal,
+            action="auth.logout",
+            target_type="session",
+            target_id=context.principal.session_id,
+            before={"active": True},
+            after={"active": False},
+            result="success",
+            request=request,
+        )
         response = Response(status_code=204)
         response.delete_cookie(
             session_service.cookie_name,
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
         return response
 
@@ -252,20 +308,30 @@ def build_identity_router(
                 SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                 """,
                 (new_hash, context.principal.user_id),
             )
             connection.execute(
                 "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                 "WHERE user_id = ? AND revoked_at IS NULL",
                 (context.principal.user_id,),
             )
+        record_audit(
+            actor=context.principal,
+            action="auth.password.change",
+            target_type="user",
+            target_id=context.principal.user_id,
+            before=None,
+            after={"sessions_revoked": True},
+            result="success",
+            request=request,
+        )
         response = Response(status_code=204)
         response.delete_cookie(
             session_service.cookie_name,
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
         return response
 
@@ -276,153 +342,233 @@ def build_identity_router(
     ) -> Principal:
         session_context(request, require_csrf=True)
         return principal
 
     @router.get("/api/admin/users")
     def list_users(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
         return repository.list_users()
 
     @router.post("/api/admin/users", status_code=201)
     def create_user(
-        payload: UserCreateRequest, _: Principal = Depends(admin_write)
+        payload: UserCreateRequest,
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         username = payload.username.strip()
         display_name = payload.display_name.strip()
         if not username or not display_name:
             raise HTTPException(
                 status_code=422, detail="用户名和显示名称不能为空"
             )
         _validate_role_ids(payload.role_ids)
         try:
             password_hash = password_service.hash(payload.password)
             user = repository.create_user(username, display_name, password_hash)
             repository.replace_user_roles(user["id"], payload.role_ids)
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="用户名已存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        return _user_response(user["id"])
+        created = _user_response(user["id"])
+        record_audit(
+            actor=principal,
+            action="user.create",
+            target_type="user",
+            target_id=user["id"],
+            before=None,
+            after=created,
+            result="success",
+            request=request,
+        )
+        return created
 
     @router.patch("/api/admin/users/{user_id}")
     def update_user(
         user_id: str,
         payload: UserUpdateRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
+        before = repository.get_user(user_id)
         try:
             repository.update_user(
                 user_id,
                 display_name=(
                     payload.display_name.strip()
                     if payload.display_name is not None
                     else None
                 ),
                 status=payload.status,
                 role_ids=payload.role_ids,
             )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="用户不存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=409, detail=str(exc)) from exc
-        return _user_response(user_id)
+        updated = _user_response(user_id)
+        record_audit(
+            actor=principal,
+            action="user.update",
+            target_type="user",
+            target_id=user_id,
+            before=before,
+            after=updated,
+            result="success",
+            request=request,
+        )
+        return updated
 
     @router.post("/api/admin/users/{user_id}/reset-password", status_code=204)
     def reset_password(
         user_id: str,
         payload: ResetPasswordRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> Response:
         if repository.get_user(user_id) is None:
             raise HTTPException(status_code=404, detail="用户不存在")
         try:
             password_hash = password_service.hash(payload.password)
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
         with repository.connect() as connection:
             connection.execute(
                 """
                 UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                 """,
                 (password_hash, user_id),
             )
             connection.execute(
                 "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                 "WHERE user_id = ? AND revoked_at IS NULL",
                 (user_id,),
             )
+        record_audit(
+            actor=principal,
+            action="user.password.reset",
+            target_type="user",
+            target_id=user_id,
+            before=None,
+            after={"sessions_revoked": True},
+            result="success",
+            request=request,
+        )
         return Response(status_code=204)
 
     @router.put("/api/admin/users/{user_id}/roles")
     def replace_user_roles(
         user_id: str,
         payload: UserRolesRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
+        user_before = repository.get_user(user_id)
         try:
             _validate_role_ids(payload.role_ids)
             repository.replace_user_roles(user_id, payload.role_ids)
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="用户不存在") from exc
         except ValueError as exc:
             status_code = 409 if "系统管理员" in str(exc) else 422
             raise HTTPException(status_code=status_code, detail=str(exc)) from exc
-        return _user_response(user_id)
+        user_after = _user_response(user_id)
+        record_audit(
+            actor=principal,
+            action="user.roles.replace",
+            target_type="user",
+            target_id=user_id,
+            before={
+                "role_codes": user_before["role_codes"] if user_before else []
+            },
+            after={"role_codes": user_after["role_codes"]},
+            result="success",
+            request=request,
+        )
+        return user_after
 
     @router.get("/api/admin/roles")
     def list_roles(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
         return [_role_response(role) for role in repository.list_roles()]
 
     @router.post("/api/admin/roles", status_code=201)
     def create_role(
-        payload: RoleCreateRequest, _: Principal = Depends(admin_write)
+        payload: RoleCreateRequest,
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         try:
             grants = _permission_pairs(payload.permissions)
             _validate_permission_pairs(grants)
             role = repository.create_role(
                 payload.code, payload.name.strip(), payload.description
             )
             repository.replace_role_permissions(role["id"], grants)
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="角色编码已存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        return _role_response(role)
+        created = _role_response(role)
+        record_audit(
+            actor=principal,
+            action="role.create",
+            target_type="role",
+            target_id=role["id"],
+            before=None,
+            after=created,
+            result="success",
+            request=request,
+        )
+        return created
 
     @router.patch("/api/admin/roles/{role_id}")
     def update_role(
         role_id: str,
         payload: RoleUpdateRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
+        existing_role = repository.get_role(role_id)
+        before = _role_response(existing_role) if existing_role is not None else None
         try:
             if payload.permissions is not None:
                 _replace_permissions(role_id, payload.permissions)
             role = repository.update_role(
                 role_id,
                 name=payload.name.strip() if payload.name is not None else None,
                 description=payload.description,
                 is_enabled=payload.is_enabled,
             )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="角色不存在") from exc
         except ValueError as exc:
             status_code = 409 if "系统管理员" in str(exc) else 422
             raise HTTPException(status_code=status_code, detail=str(exc)) from exc
-        return _role_response(role)
+        updated = _role_response(role)
+        record_audit(
+            actor=principal,
+            action="role.update",
+            target_type="role",
+            target_id=role_id,
+            before=before,
+            after=updated,
+            result="success",
+            request=request,
+        )
+        return updated
 
     @router.post("/api/admin/roles/{role_id}/clone", status_code=201)
     def clone_role(
         role_id: str,
         payload: RoleCloneRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         source = repository.get_role(role_id)
         if source is None:
             raise HTTPException(status_code=404, detail="角色不存在")
         try:
             role = repository.create_role(
                 payload.code,
                 payload.name.strip(),
                 source["description"]
                 if payload.description is None
@@ -431,37 +577,61 @@ def build_identity_router(
             source_permissions = repository.list_role_permissions(role_id)
             repository.replace_role_permissions(
                 role["id"],
                 [
                     (item["permission_code"], item["data_scope"])
                     for item in source_permissions
                 ],
             )
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="角色编码已存在") from exc
-        return _role_response(role)
+        cloned = _role_response(role)
+        record_audit(
+            actor=principal,
+            action="role.clone",
+            target_type="role",
+            target_id=role["id"],
+            before={"source_role_id": role_id},
+            after=cloned,
+            result="success",
+            request=request,
+        )
+        return cloned
 
     @router.put("/api/admin/roles/{role_id}/permissions")
     def replace_role_permissions(
         role_id: str,
         payload: RolePermissionsRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
+        before = repository.list_role_permissions(role_id)
         try:
             _replace_permissions(role_id, payload.permissions)
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="角色不存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
         role = repository.get_role(role_id)
         assert role is not None
-        return _role_response(role)
+        updated = _role_response(role)
+        record_audit(
+            actor=principal,
+            action="role.permissions.replace",
+            target_type="role",
+            target_id=role_id,
+            before={"permissions": before},
+            after={"permissions": updated["permissions"]},
+            result="success",
+            request=request,
+        )
+        return updated
 
     @router.get("/api/admin/permissions")
     def list_permissions(_: Principal = Depends(admin_read)) -> list[dict[str, Any]]:
         with repository.connect() as connection:
             rows = connection.execute(
                 "SELECT code, label FROM permissions ORDER BY code"
             ).fetchall()
         return [
             {
                 "code": row["code"],
@@ -513,37 +683,49 @@ def build_identity_router(
         if role["code"].casefold() == "system_admin" and "*" not in requested_codes:
             raise ValueError("系统管理员角色必须保留通配权限")
         repository.replace_role_permissions(role_id, grants)
 
     def authenticated_response(
         user_id: str,
         request: Request,
         *,
         status_code: int,
         update_last_login: bool,
+        audit_action: str | None = None,
     ) -> JSONResponse:
         raw_token, _ = session_service.create_session(user_id, request)
         context = session_service.resolve_session(raw_token)
         if context is None:
             raise HTTPException(status_code=500, detail="无法创建登录会话")
         principal = permission_service.principal_for_user(
             context.principal.user_id, context.principal.session_id
         )
         if principal is None:
             raise HTTPException(status_code=500, detail="无法创建登录会话")
         context = SessionContext(principal=principal, csrf_token=context.csrf_token)
         if update_last_login:
             with repository.connect() as connection:
                 connection.execute(
                     "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (user_id,),
                 )
+        if audit_action is not None:
+            record_audit(
+                actor=principal,
+                action=audit_action,
+                target_type="user",
+                target_id=user_id,
+                before=None,
+                after={"username": principal.username},
+                result="success",
+                request=request,
+            )
         response = JSONResponse(_auth_body(context), status_code=status_code)
         response.set_cookie(
             session_service.cookie_name,
             raw_token,
             max_age=int(session_service.session_ttl.total_seconds()),
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
diff --git a/tests/test_audit_service.py b/tests/test_audit_service.py
new file mode 100644
index 0000000..23325e8
--- /dev/null
+++ b/tests/test_audit_service.py
@@ -0,0 +1,331 @@
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
+import json
+from pathlib import Path
+
+import pytest
+from fastapi import FastAPI, Request
+from fastapi.testclient import TestClient
+
+from audit.api import build_audit_router
+from audit.service import AuditService
+from identity.api import build_identity_router
+from identity.password_service import PasswordService
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.schema import Principal
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
+def admin(repository: IdentityRepository) -> Principal:
+    user = repository.create_user("admin", "Admin", "unused-password-hash")
+    role = repository.get_role_by_code("system_admin")
+    assert role is not None
+    repository.replace_user_roles(user["id"], [role["id"]])
+    return Principal(
+        user_id=user["id"],
+        username=user["username"],
+        display_name=user["display_name"],
+        role_codes=("system_admin",),
+        permissions=frozenset({"*"}),
+        data_scope="all",
+        session_id="session-1",
+    )
+
+
+@pytest.fixture
+def audit_service(repository: IdentityRepository) -> AuditService:
+    return AuditService(repository)
+
+
+def _request(request_id: str = "request-123") -> Request:
+    request = Request(
+        {
+            "type": "http",
+            "method": "POST",
+            "path": "/api/test",
+            "headers": [(b"user-agent", b"audit-test")],
+            "client": ("127.0.0.1", 4321),
+        }
+    )
+    request.state.request_id = request_id
+    return request
+
+
+def test_system_admin_override_audit_allows_empty_reason(
+    audit_service: AuditService, admin: Principal
+):
+    event_id = audit_service.record(
+        actor=admin,
+        action="version.force_unlock",
+        target_type="creative_stage_version",
+        target_id="42",
+        before={"status": "submitted"},
+        after={"status": "draft"},
+        reason=None,
+        result="success",
+    )
+
+    event = audit_service.get(event_id)
+    assert event is not None
+    assert event["reason"] is None
+    assert event["actor_user_id"] == admin.user_id
+    assert event["actor_role_snapshot"] == ["system_admin"]
+    assert event["before_summary"] == {"status": "submitted"}
+    assert event["after_summary"] == {"status": "draft"}
+
+
+def test_audit_redacts_secrets_and_records_request_metadata(
+    audit_service: AuditService, admin: Principal
+):
+    event_id = audit_service.record(
+        actor=admin,
+        action="config.update",
+        target_type="settings",
+        target_id="llm",
+        before={"api_key": "old", "nested": {"access_token": "old-token"}},
+        after={
+            "password": "new-password",
+            "activation_code": "code",
+            "items": [{"session_cookie": "cookie"}, {"value": "kept"}],
+        },
+        reason="rotation",
+        result="success",
+        request=_request(),
+    )
+
+    event = audit_service.get(event_id)
+    assert event is not None
+    assert event["before_summary"] == {
+        "api_key": "[REDACTED]",
+        "nested": {"access_token": "[REDACTED]"},
+    }
+    assert event["after_summary"] == {
+        "password": "[REDACTED]",
+        "activation_code": "[REDACTED]",
+        "items": [{"session_cookie": "[REDACTED]"}, {"value": "kept"}],
+    }
+    assert event["request_id"] == "request-123"
+    assert event["ip_address"] == "127.0.0.1"
+    assert event["user_agent"] == "audit-test"
+
+
+def test_audit_summaries_are_bounded_and_queries_are_filterable(
+    audit_service: AuditService, admin: Principal
+):
+    matching_id = audit_service.record(
+        actor=admin,
+        action="user.update",
+        target_type="user",
+        target_id="user-2",
+        before=None,
+        after={"notes": "x" * 20_000},
+        reason=None,
+        result="success",
+    )
+    audit_service.record(
+        actor=None,
+        action="auth.login",
+        target_type="user",
+        target_id="unknown",
+        before=None,
+        after=None,
+        reason=None,
+        result="failure",
+    )
+
+    event = audit_service.get(matching_id)
+    assert event is not None
+    assert event["after_summary"]["_truncated"] is True
+    assert len(json.dumps(event["after_summary"]).encode("utf-8")) <= 8192
+    assert [item["id"] for item in audit_service.query(
+        action="user.update", target_type="user", result="success"
+    )] == [matching_id]
+
+
+def test_identity_mutations_and_login_results_are_audited(
+    repository: IdentityRepository, audit_service: AuditService
+):
+    session_service = SessionService(repository, secure_cookie=False)
+    app = FastAPI()
+    app.include_router(build_identity_router(repository, session_service, audit_service))
+    client = TestClient(app)
+    setup = client.post(
+        "/api/auth/setup",
+        headers={"X-Request-ID": "setup-request"},
+        json={
+            "username": "admin",
+            "display_name": "Admin",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    assert setup.status_code == 201
+    csrf_token = setup.json()["csrf_token"]
+    role = repository.get_role_by_code("screenwriter")
+    assert role is not None
+    created = client.post(
+        "/api/admin/users",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "username": "writer",
+            "display_name": "Writer",
+            "password": "Writer-Strong-123!",
+            "role_ids": [],
+        },
+    )
+    assert created.status_code == 201
+
+    failed_login = TestClient(app).post(
+        "/api/auth/login",
+        headers={"X-Request-ID": "failed-login-request"},
+        json={"username": "writer", "password": "wrong-password"},
+    )
+    successful_login = TestClient(app).post(
+        "/api/auth/login",
+        headers={"X-Request-ID": "successful-login-request"},
+        json={"username": "writer", "password": "Writer-Strong-123!"},
+    )
+    roles = client.put(
+        f"/api/admin/users/{created.json()['id']}/roles",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"role_ids": [role["id"]]},
+    )
+    disabled = client.patch(
+        f"/api/admin/users/{created.json()['id']}",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"status": "disabled"},
+    )
+    permissions = client.put(
+        f"/api/admin/roles/{role['id']}/permissions",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"permissions": [{"permission_code": "rank.read", "data_scope": "task"}]},
+    )
+
+    assert failed_login.status_code == 401
+    assert successful_login.status_code == 200
+    assert roles.status_code == 200
+    assert disabled.status_code == 200
+    assert permissions.status_code == 200
+
+    login_events = audit_service.query(action="auth.login")
+    assert {event["result"] for event in login_events} == {"success", "failure"}
+    failed_event = next(event for event in login_events if event["result"] == "failure")
+    successful_event = next(event for event in login_events if event["result"] == "success")
+    assert failed_event["actor_user_id"] is None
+    assert failed_event["request_id"] == "failed-login-request"
+    assert successful_event["actor_user_id"] == created.json()["id"]
+
+    user_event = audit_service.query(action="user.update")[0]
+    role_event = audit_service.query(action="user.roles.replace")[0]
+    grant_event = audit_service.query(action="role.permissions.replace")[0]
+    assert user_event["before_summary"]["status"] == "active"
+    assert user_event["after_summary"]["status"] == "disabled"
+    assert role_event["before_summary"]["role_codes"] == []
+    assert role_event["after_summary"]["role_codes"] == ["screenwriter"]
+    assert grant_event["before_summary"]["permissions"]
+    assert grant_event["after_summary"]["permissions"] == [
+        {"permission_code": "rank.read", "data_scope": "task"}
+    ]
+
+
+def test_admin_audit_api_requires_permission_and_applies_filters(
+    repository: IdentityRepository, audit_service: AuditService, admin: Principal
+):
+    session_service = SessionService(repository, secure_cookie=False)
+    permission_service = PermissionService(repository, session_service)
+    app = FastAPI()
+    app.state.identity_permission_service = permission_service
+    app.include_router(build_identity_router(repository, session_service, audit_service))
+    app.include_router(build_audit_router(audit_service))
+    audit_service.record(
+        actor=admin,
+        action="user.update",
+        target_type="user",
+        target_id="user-2",
+        before={"status": "active"},
+        after={"status": "disabled"},
+        reason=None,
+        result="success",
+    )
+    client = TestClient(app)
+
+    assert client.get("/api/admin/audit").status_code == 401
+    # The fixture user has a placeholder hash, so set a real password before login.
+    with repository.connect() as connection:
+        connection.execute(
+            "UPDATE users SET password_hash = ? WHERE id = ?",
+            (PasswordService().hash("Admin-Strong-123!"), admin.user_id),
+        )
+    login = client.post(
+        "/api/auth/login",
+        json={"username": "admin", "password": "Admin-Strong-123!"},
+    )
+    assert login.status_code == 200
+
+    response = client.get(
+        "/api/admin/audit",
+        params={"action": "user.update", "target_id": "user-2", "result": "success"},
+    )
+    assert response.status_code == 200
+    assert [event["action"] for event in response.json()] == ["user.update"]
+
+
+def test_logout_and_password_changes_are_audited(repository: IdentityRepository):
+    audit_service = AuditService(repository)
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(
+            repository,
+            SessionService(repository, secure_cookie=False),
+            audit_service,
+        )
+    )
+    client = TestClient(app)
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "Admin",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    csrf_token = setup.json()["csrf_token"]
+
+    changed = client.post(
+        "/api/auth/change-password",
+        headers={"X-CSRF-Token": csrf_token},
+        json={
+            "current_password": "Admin-Strong-123!",
+            "new_password": "Admin-New-Strong-456!",
+        },
+    )
+    assert changed.status_code == 204
+    login = client.post(
+        "/api/auth/login",
+        json={"username": "admin", "password": "Admin-New-Strong-456!"},
+    )
+    logged_out = client.post(
+        "/api/auth/logout",
+        headers={"X-CSRF-Token": login.json()["csrf_token"]},
+    )
+
+    assert logged_out.status_code == 204
+    assert len(audit_service.query(action="auth.password.change")) == 1
+    assert len(audit_service.query(action="auth.logout")) == 1
diff --git a/tests/test_web_security.py b/tests/test_web_security.py
new file mode 100644
index 0000000..9313d80
--- /dev/null
+++ b/tests/test_web_security.py
@@ -0,0 +1,148 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/WebGUI.py
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+from __future__ import annotations
+
+from pathlib import Path
+
+from fastapi import FastAPI, Request
+from fastapi.testclient import TestClient
+
+from identity.api import build_identity_router
+from identity.repository import IdentityRepository
+from identity.session_service import SessionService
+from WebGUI import configure_cors, install_security_middleware
+
+
+def _secured_app(tmp_path: Path) -> tuple[FastAPI, SessionService]:
+    repository = IdentityRepository(tmp_path / "system.db")
+    session_service = SessionService(repository, secure_cookie=False)
+    app = FastAPI()
+    seen_request_ids: list[str | None] = []
+
+    @app.middleware("http")
+    async def observe_request_id(request: Request, call_next):
+        seen_request_ids.append(getattr(request.state, "request_id", None))
+        return await call_next(request)
+
+    @app.post("/api/protected")
+    def protected() -> dict[str, bool]:
+        return {"ok": True}
+
+    @app.post("/api/public-write")
+    def public_write() -> dict[str, bool]:
+        return {"ok": True}
+
+    app.include_router(build_identity_router(repository, session_service))
+    install_security_middleware(app, session_service)
+    app.state.seen_request_ids = seen_request_ids
+    return app, session_service
+
+
+def test_request_id_is_available_before_logging_and_returned(tmp_path: Path):
+    app, _ = _secured_app(tmp_path)
+
+    response = TestClient(app).post(
+        "/api/public-write", headers={"X-Request-ID": "client-request-123"}
+    )
+
+    assert response.status_code == 200
+    assert response.headers["X-Request-ID"] == "client-request-123"
+    assert app.state.seen_request_ids == ["client-request-123"]
+
+
+def test_authenticated_write_requires_matching_csrf_token(tmp_path: Path):
+    app, _ = _secured_app(tmp_path)
+    client = TestClient(app)
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "Admin",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    assert setup.status_code == 201
+    csrf_token = setup.json()["csrf_token"]
+
+    missing = client.post("/api/protected")
+    incorrect = client.post(
+        "/api/protected", headers={"X-CSRF-Token": "incorrect"}
+    )
+    accepted = client.post(
+        "/api/protected", headers={"X-CSRF-Token": csrf_token}
+    )
+
+    assert missing.status_code == 403
+    assert missing.json() == {"detail": "CSRF 校验失败"}
+    assert incorrect.status_code == 403
+    assert accepted.status_code == 200
+
+
+def test_login_and_first_run_setup_are_csrf_exempt(tmp_path: Path):
+    app, _ = _secured_app(tmp_path)
+    client = TestClient(app)
+
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "Admin",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    client.cookies.clear()
+    login = client.post(
+        "/api/auth/login",
+        json={"username": "admin", "password": "Admin-Strong-123!"},
+    )
+
+    assert setup.status_code == 201
+    assert login.status_code == 200
+
+
+def test_unauthenticated_write_is_left_to_route_authorization(tmp_path: Path):
+    app, _ = _secured_app(tmp_path)
+
+    response = TestClient(app).post("/api/public-write")
+
+    assert response.status_code == 200
+
+
+def test_cors_defaults_to_same_origin_and_uses_explicit_origin_list():
+    same_origin_app = FastAPI()
+
+    @same_origin_app.get("/ping")
+    def ping() -> dict[str, bool]:
+        return {"ok": True}
+
+    configure_cors(same_origin_app, "")
+    cross_origin = TestClient(same_origin_app).get(
+        "/ping", headers={"Origin": "https://untrusted.example"}
+    )
+    assert "access-control-allow-origin" not in cross_origin.headers
+
+    configured_app = FastAPI()
+
+    @configured_app.get("/ping")
+    def configured_ping() -> dict[str, bool]:
+        return {"ok": True}
+
+    configure_cors(
+        configured_app,
+        "https://admin.example, https://review.example,https://admin.example",
+    )
+    allowed = TestClient(configured_app).get(
+        "/ping", headers={"Origin": "https://review.example"}
+    )
+    rejected = TestClient(configured_app).get(
+        "/ping", headers={"Origin": "https://untrusted.example"}
+    )
+    assert allowed.headers["access-control-allow-origin"] == "https://review.example"
+    assert "access-control-allow-origin" not in rejected.headers
