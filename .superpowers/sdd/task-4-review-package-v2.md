# Commits

ad7a2a0 fix: wire atomic production security audit
65d27d9 feat: add security audit and csrf protection

# Stat

 WebGUI.py                      | 110 +++++++++-
 audit/__init__.py              |  15 ++
 audit/api.py                   |  53 +++++
 audit/repository.py            | 188 +++++++++++++++++
 audit/service.py               | 158 ++++++++++++++
 identity/api.py                | 462 ++++++++++++++++++++++++++++++++---------
 identity/permission_catalog.py |   1 +
 identity/permission_service.py |  16 +-
 identity/repository.py         | 155 +++++++++-----
 identity/session_service.py    |  56 +++--
 tests/test_audit_service.py    | 409 ++++++++++++++++++++++++++++++++++++
 tests/test_web_security.py     | 181 ++++++++++++++++
 12 files changed, 1632 insertions(+), 172 deletions(-)

# Diff

diff --git a/WebGUI.py b/WebGUI.py
index 879d733..203177b 100644
--- a/WebGUI.py
+++ b/WebGUI.py
@@ -22,59 +22,71 @@ import time
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
 from fastapi.middleware.cors import CORSMiddleware
 from pydantic import BaseModel
 
 import config
+from audit.api import build_audit_router
+from audit.service import AuditService
 from analysis.analytics_api import register_analytics_routes
 from analysis.asr import (
     ASRError,
     OpenAICompatibleASRProvider,
     SenseVoiceASRProvider,
     default_sensevoice_script_path,
 )
 from analysis.llm import LLMError, create_llm_provider_from_config
 from analysis.today_rank_api import register_today_rank_routes
 from creative.api import register_creative_routes
+from identity.api import build_identity_router
+from identity.password_service import PasswordService
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.session_service import SessionService
 from agents.douyin_analysis_agent import AnalyzeOptions as DouyinAnalyzeOptions
 from agents.douyin_analysis_agent import analyze_rank_file as analyze_douyin_rank_file
 from agents.hongguo_analysis_agent import AnalyzeOptions, analyze_rank_file
 from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, load_settings as load_hongguo_drama_settings, public_settings as public_hongguo_drama_settings, save_settings as save_hongguo_drama_settings
 from agents.douyin_drama_fallback import DouyinDramaFallbackClient, DouyinFallbackError
 from core.job import CrawlJob
 from rag.api import register_rag_routes
 from runtime.job_manager import JobConflictError, JobManager, JobNotFoundError
 
 
 WORKSPACE_ROOT = Path(__file__).resolve().parent
 WEB_LOG_LEVEL = os.getenv("AIWORKER_WEB_LOG_LEVEL", "INFO").strip().upper()
 WEB_LOG_MAX_BYTES = 5 * 1024 * 1024
 WEB_LOG_BACKUP_COUNT = 5
 WEB_LOG_PATH = WORKSPACE_ROOT / "logs" / "webgui.log"
+SYSTEM_DB_PATH = WORKSPACE_ROOT / ".runtime" / "system" / "system.db"
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
@@ -94,20 +106,77 @@ def configure_backend_logging(log_path: Path = WEB_LOG_PATH, log_level: str = WE
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
@@ -160,47 +229,72 @@ async def lifespan(app: FastAPI):
         if job["status"] in {"pending", "running", "stopping"}:
             try:
                 job_manager.stop(job["job_id"])
             except Exception:
                 pass
     if state.process:
         state.process.terminate()
 
 app = FastAPI(lifespan=lifespan)
 
+identity_repository = IdentityRepository(SYSTEM_DB_PATH)
+identity_repository.seed_defaults()
+identity_password_service = PasswordService()
+identity_session_service = SessionService(identity_repository)
+identity_permission_service = PermissionService(
+    identity_repository, identity_session_service
+)
+audit_service = AuditService(identity_repository)
+app.state.identity_repository = identity_repository
+app.state.identity_password_service = identity_password_service
+app.state.identity_session_service = identity_session_service
+app.state.identity_permission_service = identity_permission_service
+app.state.audit_service = audit_service
+
 
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
+install_security_middleware(app, identity_session_service)
+configure_cors(app)
+app.include_router(
+    build_identity_router(
+        identity_repository,
+        identity_session_service,
+        audit_service,
+        password_service=identity_password_service,
+        permission_service=identity_permission_service,
+    )
 )
+app.include_router(build_audit_router(audit_service))
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
index 0000000..4381021
--- /dev/null
+++ b/audit/repository.py
@@ -0,0 +1,188 @@
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
+import sqlite3
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
+                "UPDATE audit_logs SET target_id = resource_id "
+                "WHERE target_id IS NULL AND resource_id IS NOT NULL"
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
+    def insert(
+        self,
+        event: dict[str, Any],
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> str:
+        if connection is not None:
+            self._insert(connection, event)
+        else:
+            with self.identity_repository.connect() as owned_connection:
+                self._insert(owned_connection, event)
+        return str(event["id"])
+
+    @staticmethod
+    def _insert(connection: sqlite3.Connection, event: dict[str, Any]) -> None:
+        connection.execute(
+            """
+            INSERT INTO audit_logs (
+                id, actor_id, actor_user_id, actor_role_snapshot_json,
+                action, resource_type, target_type, resource_id, target_id,
+                project_id, reason, details_json, before_summary_json,
+                after_summary_json, request_id, ip_address, user_agent,
+                result, created_at
+            ) VALUES (
+                :id, :actor_user_id, :actor_user_id,
+                :actor_role_snapshot_json, :action, :target_type,
+                :target_type, :target_id, :target_id, :project_id, :reason,
+                '{}', :before_summary_json, :after_summary_json, :request_id,
+                :ip_address, :user_agent, :result, CURRENT_TIMESTAMP
+            )
+            """,
+            event,
+        )
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
index 0000000..17d78e0
--- /dev/null
+++ b/audit/service.py
@@ -0,0 +1,158 @@
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
+import sqlite3
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
+        connection: sqlite3.Connection | None = None,
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
+            },
+            connection=connection,
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
index 38042b4..f2ed675 100644
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
@@ -66,32 +68,65 @@ class RolePermissionsRequest(BaseModel):
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
+    *,
+    password_service: PasswordService | None = None,
+    permission_service: PermissionService | None = None,
 ) -> APIRouter:
-    permission_service = PermissionService(repository, session_service)
+    permission_service = permission_service or PermissionService(
+        repository, session_service
+    )
 
     def bind_permission_service(request: Request) -> None:
         request.state.identity_permission_service = permission_service
 
     router = APIRouter(dependencies=[Depends(bind_permission_service)])
-    password_service = PasswordService()
+    password_service = password_service or PasswordService()
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
+        connection: sqlite3.Connection | None = None,
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
+                connection=connection,
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
@@ -119,23 +154,23 @@ def build_identity_router(
         display_name = payload.display_name.strip()
         if not username or not display_name:
             raise HTTPException(status_code=422, detail="用户名和显示名称不能为空")
         try:
             password_hash = password_service.hash(payload.password)
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
 
         user_id = str(uuid.uuid4())
         setup_already_complete = False
+        response: JSONResponse | None = None
         try:
-            with repository.connect() as connection:
-                connection.execute("BEGIN IMMEDIATE")
+            with repository.transaction() as connection:
                 role = connection.execute(
                     "SELECT id FROM roles WHERE code = 'system_admin' COLLATE NOCASE"
                 ).fetchone()
                 if role is None:
                     raise HTTPException(
                         status_code=500, detail="系统管理员角色尚未初始化"
                     )
                 bound_active_admins = connection.execute(
                     """
                     SELECT COUNT(DISTINCT u.id)
@@ -168,59 +203,93 @@ def build_identity_router(
                             username,
                             username.casefold(),
                             display_name,
                             password_hash,
                         ),
                     )
                     connection.execute(
                         "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                         (user_id, role["id"]),
                     )
+                    response = authenticated_response(
+                        user_id,
+                        request,
+                        status_code=201,
+                        update_last_login=True,
+                        audit_action="user.setup",
+                        connection=connection,
+                    )
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="用户名已存在") from exc
         if setup_already_complete:
             raise HTTPException(status_code=409, detail="系统已经完成初始化")
-
-        return authenticated_response(
-            user_id, request, status_code=201, update_last_login=True
-        )
+        assert response is not None
+        return response
 
     @router.post("/api/auth/login")
     def login(payload: LoginRequest, request: Request) -> JSONResponse:
         normalized_username = payload.username.strip().casefold()
         with repository.connect() as connection:
             user = connection.execute(
                 """
                 SELECT id, password_hash, status, is_deleted
                 FROM users WHERE normalized_username = ? COLLATE NOCASE
                 """,
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
-        if raw_token:
-            session_service.revoke_session(raw_token)
+        with repository.transaction() as connection:
+            if raw_token:
+                session_service.revoke_session(
+                    raw_token, connection=connection
+                )
+            record_audit(
+                actor=context.principal,
+                action="auth.logout",
+                target_type="session",
+                target_id=context.principal.session_id,
+                before={"active": True},
+                after={"active": False},
+                result="success",
+                request=request,
+                connection=connection,
+            )
         response = Response(status_code=204)
         response.delete_cookie(
             session_service.cookie_name,
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
         return response
 
@@ -238,34 +307,45 @@ def build_identity_router(
                 (context.principal.user_id,),
             ).fetchone()
         if user is None or not password_service.verify(
             user["password_hash"], payload.current_password
         ):
             raise HTTPException(status_code=400, detail="当前密码错误")
         try:
             new_hash = password_service.hash(payload.new_password)
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        with repository.connect() as connection:
+        with repository.transaction() as connection:
             connection.execute(
                 """
                 UPDATE users
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
+            record_audit(
+                actor=context.principal,
+                action="auth.password.change",
+                target_type="user",
+                target_id=context.principal.user_id,
+                before=None,
+                after={"sessions_revoked": True},
+                result="success",
+                request=request,
+                connection=connection,
+            )
         response = Response(status_code=204)
         response.delete_cookie(
             session_service.cookie_name,
             path="/",
             secure=session_service.secure_cookie,
             httponly=True,
             samesite="lax",
         )
         return response
 
@@ -276,192 +356,350 @@ def build_identity_router(
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
-            user = repository.create_user(username, display_name, password_hash)
-            repository.replace_user_roles(user["id"], payload.role_ids)
+            with repository.transaction() as connection:
+                user = repository.create_user(
+                    username,
+                    display_name,
+                    password_hash,
+                    connection=connection,
+                )
+                repository.replace_user_roles(
+                    user["id"], payload.role_ids, connection=connection
+                )
+                created = _user_response(user["id"], connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="user.create",
+                    target_type="user",
+                    target_id=user["id"],
+                    before=None,
+                    after=created,
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="用户名已存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        return _user_response(user["id"])
+        return created
 
     @router.patch("/api/admin/users/{user_id}")
     def update_user(
         user_id: str,
         payload: UserUpdateRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         try:
-            repository.update_user(
-                user_id,
-                display_name=(
-                    payload.display_name.strip()
-                    if payload.display_name is not None
-                    else None
-                ),
-                status=payload.status,
-                role_ids=payload.role_ids,
-            )
+            with repository.transaction() as connection:
+                before = repository.get_user(user_id, connection=connection)
+                repository.update_user(
+                    user_id,
+                    display_name=(
+                        payload.display_name.strip()
+                        if payload.display_name is not None
+                        else None
+                    ),
+                    status=payload.status,
+                    role_ids=payload.role_ids,
+                    connection=connection,
+                )
+                updated = repository.get_user(user_id, connection=connection)
+                assert updated is not None
+                record_audit(
+                    actor=principal,
+                    action="user.update",
+                    target_type="user",
+                    target_id=user_id,
+                    before=before,
+                    after=updated,
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="用户不存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=409, detail=str(exc)) from exc
-        return _user_response(user_id)
+        return updated
 
     @router.post("/api/admin/users/{user_id}/reset-password", status_code=204)
     def reset_password(
         user_id: str,
         payload: ResetPasswordRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> Response:
-        if repository.get_user(user_id) is None:
-            raise HTTPException(status_code=404, detail="用户不存在")
         try:
             password_hash = password_service.hash(payload.password)
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        with repository.connect() as connection:
+        with repository.transaction() as connection:
+            if repository.get_user(user_id, connection=connection) is None:
+                raise HTTPException(status_code=404, detail="用户不存在")
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
+            record_audit(
+                actor=principal,
+                action="user.password.reset",
+                target_type="user",
+                target_id=user_id,
+                before=None,
+                after={"sessions_revoked": True},
+                result="success",
+                request=request,
+                connection=connection,
+            )
         return Response(status_code=204)
 
     @router.put("/api/admin/users/{user_id}/roles")
     def replace_user_roles(
         user_id: str,
         payload: UserRolesRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         try:
             _validate_role_ids(payload.role_ids)
-            repository.replace_user_roles(user_id, payload.role_ids)
+            with repository.transaction() as connection:
+                user_before = repository.get_user(user_id, connection=connection)
+                repository.replace_user_roles(
+                    user_id, payload.role_ids, connection=connection
+                )
+                user_after = _user_response(user_id, connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="user.roles.replace",
+                    target_type="user",
+                    target_id=user_id,
+                    before={
+                        "role_codes": (
+                            user_before["role_codes"] if user_before else []
+                        )
+                    },
+                    after={"role_codes": user_after["role_codes"]},
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="用户不存在") from exc
         except ValueError as exc:
             status_code = 409 if "系统管理员" in str(exc) else 422
             raise HTTPException(status_code=status_code, detail=str(exc)) from exc
-        return _user_response(user_id)
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
-            role = repository.create_role(
-                payload.code, payload.name.strip(), payload.description
-            )
-            repository.replace_role_permissions(role["id"], grants)
+            with repository.transaction() as connection:
+                role = repository.create_role(
+                    payload.code,
+                    payload.name.strip(),
+                    payload.description,
+                    connection=connection,
+                )
+                repository.replace_role_permissions(
+                    role["id"], grants, connection=connection
+                )
+                created = _role_response(role, connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="role.create",
+                    target_type="role",
+                    target_id=role["id"],
+                    before=None,
+                    after=created,
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="角色编码已存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        return _role_response(role)
+        return created
 
     @router.patch("/api/admin/roles/{role_id}")
     def update_role(
         role_id: str,
         payload: RoleUpdateRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         try:
-            if payload.permissions is not None:
-                _replace_permissions(role_id, payload.permissions)
-            role = repository.update_role(
-                role_id,
-                name=payload.name.strip() if payload.name is not None else None,
-                description=payload.description,
-                is_enabled=payload.is_enabled,
-            )
+            with repository.transaction() as connection:
+                existing_role = repository.get_role(role_id, connection=connection)
+                before = (
+                    _role_response(existing_role, connection=connection)
+                    if existing_role is not None
+                    else None
+                )
+                if payload.permissions is not None:
+                    _replace_permissions(
+                        role_id, payload.permissions, connection=connection
+                    )
+                role = repository.update_role(
+                    role_id,
+                    name=(
+                        payload.name.strip() if payload.name is not None else None
+                    ),
+                    description=payload.description,
+                    is_enabled=payload.is_enabled,
+                    connection=connection,
+                )
+                updated = _role_response(role, connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="role.update",
+                    target_type="role",
+                    target_id=role_id,
+                    before=before,
+                    after=updated,
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="角色不存在") from exc
         except ValueError as exc:
             status_code = 409 if "系统管理员" in str(exc) else 422
             raise HTTPException(status_code=status_code, detail=str(exc)) from exc
-        return _role_response(role)
+        return updated
 
     @router.post("/api/admin/roles/{role_id}/clone", status_code=201)
     def clone_role(
         role_id: str,
         payload: RoleCloneRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
-        source = repository.get_role(role_id)
-        if source is None:
-            raise HTTPException(status_code=404, detail="角色不存在")
         try:
-            role = repository.create_role(
-                payload.code,
-                payload.name.strip(),
-                source["description"]
-                if payload.description is None
-                else payload.description,
-            )
-            source_permissions = repository.list_role_permissions(role_id)
-            repository.replace_role_permissions(
-                role["id"],
-                [
-                    (item["permission_code"], item["data_scope"])
-                    for item in source_permissions
-                ],
-            )
+            with repository.transaction() as connection:
+                source = repository.get_role(role_id, connection=connection)
+                if source is None:
+                    raise KeyError(role_id)
+                role = repository.create_role(
+                    payload.code,
+                    payload.name.strip(),
+                    (
+                        source["description"]
+                        if payload.description is None
+                        else payload.description
+                    ),
+                    connection=connection,
+                )
+                source_permissions = repository.list_role_permissions(
+                    role_id, connection=connection
+                )
+                repository.replace_role_permissions(
+                    role["id"],
+                    [
+                        (item["permission_code"], item["data_scope"])
+                        for item in source_permissions
+                    ],
+                    connection=connection,
+                )
+                cloned = _role_response(role, connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="role.clone",
+                    target_type="role",
+                    target_id=role["id"],
+                    before={"source_role_id": role_id},
+                    after=cloned,
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
+        except KeyError as exc:
+            raise HTTPException(status_code=404, detail="角色不存在") from exc
         except sqlite3.IntegrityError as exc:
             raise HTTPException(status_code=409, detail="角色编码已存在") from exc
-        return _role_response(role)
+        return cloned
 
     @router.put("/api/admin/roles/{role_id}/permissions")
     def replace_role_permissions(
         role_id: str,
         payload: RolePermissionsRequest,
-        _: Principal = Depends(admin_write),
+        request: Request,
+        principal: Principal = Depends(admin_write),
     ) -> dict[str, Any]:
         try:
-            _replace_permissions(role_id, payload.permissions)
+            with repository.transaction() as connection:
+                before = repository.list_role_permissions(
+                    role_id, connection=connection
+                )
+                _replace_permissions(
+                    role_id, payload.permissions, connection=connection
+                )
+                role = repository.get_role(role_id, connection=connection)
+                assert role is not None
+                updated = _role_response(role, connection=connection)
+                record_audit(
+                    actor=principal,
+                    action="role.permissions.replace",
+                    target_type="role",
+                    target_id=role_id,
+                    before={"permissions": before},
+                    after={"permissions": updated["permissions"]},
+                    result="success",
+                    request=request,
+                    connection=connection,
+                )
         except KeyError as exc:
             raise HTTPException(status_code=404, detail="角色不存在") from exc
         except ValueError as exc:
             raise HTTPException(status_code=422, detail=str(exc)) from exc
-        role = repository.get_role(role_id)
-        assert role is not None
-        return _role_response(role)
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
@@ -469,81 +707,119 @@ def build_identity_router(
                 "dependencies": sorted(PERMISSION_DEPENDENCIES.get(row["code"], ())),
             }
             for row in rows
         ]
 
     def _validate_role_ids(role_ids: list[str]) -> None:
         known = {role["id"] for role in repository.list_roles()}
         if not set(role_ids) <= known:
             raise HTTPException(status_code=422, detail="角色不存在")
 
-    def _user_response(user_id: str) -> dict[str, Any]:
-        user = repository.get_user(user_id)
+    def _user_response(
+        user_id: str, *, connection: sqlite3.Connection | None = None
+    ) -> dict[str, Any]:
+        user = repository.get_user(user_id, connection=connection)
         if user is None:
             raise HTTPException(status_code=404, detail="用户不存在")
         return user
 
-    def _role_response(role: dict[str, Any]) -> dict[str, Any]:
+    def _role_response(
+        role: dict[str, Any], *, connection: sqlite3.Connection | None = None
+    ) -> dict[str, Any]:
         return {
             **role,
-            "permissions": repository.list_role_permissions(role["id"]),
+            "permissions": repository.list_role_permissions(
+                role["id"], connection=connection
+            ),
         }
 
     def _permission_pairs(
         permissions: list[RolePermissionRequest],
     ) -> list[tuple[str, str]]:
         return [
             (permission.permission_code, permission.data_scope)
             for permission in permissions
         ]
 
     def _validate_permission_pairs(grants: list[tuple[str, str]]) -> None:
         unknown = {code for code, _ in grants} - set(PERMISSIONS)
         if unknown:
             raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
 
     def _replace_permissions(
-        role_id: str, permissions: list[RolePermissionRequest]
+        role_id: str,
+        permissions: list[RolePermissionRequest],
+        *,
+        connection: sqlite3.Connection | None = None,
     ) -> None:
-        role = repository.get_role(role_id)
+        role = repository.get_role(role_id, connection=connection)
         if role is None:
             raise KeyError(role_id)
         grants = _permission_pairs(permissions)
         _validate_permission_pairs(grants)
         requested_codes = {code for code, _ in grants}
         if role["code"].casefold() == "system_admin" and "*" not in requested_codes:
             raise ValueError("系统管理员角色必须保留通配权限")
-        repository.replace_role_permissions(role_id, grants)
+        repository.replace_role_permissions(
+            role_id, grants, connection=connection
+        )
 
     def authenticated_response(
         user_id: str,
         request: Request,
         *,
         status_code: int,
         update_last_login: bool,
+        audit_action: str | None = None,
+        connection: sqlite3.Connection | None = None,
     ) -> JSONResponse:
-        raw_token, _ = session_service.create_session(user_id, request)
-        context = session_service.resolve_session(raw_token)
+        if connection is None:
+            with repository.transaction() as atomic_connection:
+                return authenticated_response(
+                    user_id,
+                    request,
+                    status_code=status_code,
+                    update_last_login=update_last_login,
+                    audit_action=audit_action,
+                    connection=atomic_connection,
+                )
+        raw_token, _ = session_service.create_session(
+            user_id, request, connection=connection
+        )
+        context = session_service.resolve_session(raw_token, connection=connection)
         if context is None:
             raise HTTPException(status_code=500, detail="无法创建登录会话")
         principal = permission_service.principal_for_user(
-            context.principal.user_id, context.principal.session_id
+            context.principal.user_id,
+            context.principal.session_id,
+            connection=connection,
         )
         if principal is None:
             raise HTTPException(status_code=500, detail="无法创建登录会话")
         context = SessionContext(principal=principal, csrf_token=context.csrf_token)
         if update_last_login:
-            with repository.connect() as connection:
-                connection.execute(
-                    "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
-                    (user_id,),
-                )
+            connection.execute(
+                "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
+                (user_id,),
+            )
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
+                connection=connection,
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
diff --git a/identity/permission_catalog.py b/identity/permission_catalog.py
index 2c605bf..ff6977b 100644
--- a/identity/permission_catalog.py
+++ b/identity/permission_catalog.py
@@ -80,20 +80,21 @@ _PERMISSION_CODES = (
     "version.read.managed",
     "version.compare",
     "version.approve",
     "version.return",
     "job.create.selection",
     "job.create.creative",
     "job.create.writing",
     "job.read.own",
     "job.read.project",
     "job.cancel.own",
+    "audit.read",
     "audit.read.project",
     "approved_content.read",
     "export.create",
 )
 
 # The catalog is intentionally a mapping: membership checks use permission codes,
 # while seeding has a stable fallback display label to update idempotently.
 PERMISSIONS = {code: code for code in _PERMISSION_CODES}
 
 PERMISSION_DEPENDENCIES = {
diff --git a/identity/permission_service.py b/identity/permission_service.py
index adfb989..c5f20e8 100644
--- a/identity/permission_service.py
+++ b/identity/permission_service.py
@@ -2,20 +2,22 @@
 # Copyright (c) 2025 relakkes@gmail.com
 #
 # This file is part of MediaCrawler project.
 # Repository: https://github.com/NanmiCoder/MediaCrawler
 # GitHub: https://github.com/NanmiCoder
 # Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
 #
 
 from __future__ import annotations
 
+import sqlite3
+
 from .permission_catalog import PERMISSION_DEPENDENCIES
 from .repository import IdentityRepository
 from .schema import DATA_SCOPE_ORDER, Principal
 from .session_service import SessionService
 
 
 _MANAGED_PROJECT_ROLES = frozenset(
     {"owner", "project_owner", "manager", "project_manager"}
 )
 
@@ -28,32 +30,38 @@ class PermissionService:
         self.session_service = session_service
 
     def principal_for_session(self, raw_token: str | None) -> Principal | None:
         context = self.session_service.resolve_session(raw_token)
         if context is None:
             return None
         return self.principal_for_user(
             context.principal.user_id, context.principal.session_id
         )
 
-    def principal_for_user(self, user_id: str, session_id: str) -> Principal | None:
-        with self.repository.connect() as connection:
-            user = connection.execute(
+    def principal_for_user(
+        self,
+        user_id: str,
+        session_id: str,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> Principal | None:
+        with self.repository.connection_scope(connection) as active_connection:
+            user = active_connection.execute(
                 """
                 SELECT id, username, display_name, status, is_deleted
                 FROM users WHERE id = ?
                 """,
                 (user_id,),
             ).fetchone()
             if user is None or user["status"] != "active" or user["is_deleted"]:
                 return None
-            rows = connection.execute(
+            rows = active_connection.execute(
                 """
                 SELECT r.code, rp.permission_code, rp.data_scope
                 FROM user_roles AS ur
                 JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                 LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
                 WHERE ur.user_id = ?
                 ORDER BY r.code, rp.permission_code
                 """,
                 (user_id,),
             ).fetchall()
diff --git a/identity/repository.py b/identity/repository.py
index c6b38ee..1ca5098 100644
--- a/identity/repository.py
+++ b/identity/repository.py
@@ -169,20 +169,37 @@ class IdentityRepository:
     def connect(self) -> Iterator[sqlite3.Connection]:
         connection = sqlite3.connect(self.db_path)
         connection.row_factory = sqlite3.Row
         connection.execute("PRAGMA foreign_keys = ON")
         try:
             with connection:
                 yield connection
         finally:
             connection.close()
 
+    @contextmanager
+    def transaction(self) -> Iterator[sqlite3.Connection]:
+        """Open a serialized transaction shared by identity and audit writes."""
+        with self.connect() as connection:
+            connection.execute("BEGIN IMMEDIATE")
+            yield connection
+
+    @contextmanager
+    def connection_scope(
+        self, connection: sqlite3.Connection | None
+    ) -> Iterator[sqlite3.Connection]:
+        if connection is not None:
+            yield connection
+            return
+        with self.connect() as owned_connection:
+            yield owned_connection
+
     def _init_schema(self) -> None:
         with self.connect() as connection:
             connection.executescript(SCHEMA_SQL)
 
     def seed_defaults(self) -> None:
         with self.connect() as connection:
             for code, label in PERMISSIONS.items():
                 connection.execute(
                     """
                     INSERT INTO permissions (code, label) VALUES (?, ?)
@@ -268,274 +285,308 @@ class IdentityRepository:
             rows = connection.execute("SELECT * FROM roles ORDER BY code").fetchall()
         return [self._role_dict(row) for row in rows]
 
     def get_role_by_code(self, code: str) -> dict[str, Any] | None:
         with self.connect() as connection:
             row = connection.execute(
                 "SELECT * FROM roles WHERE code = ? COLLATE NOCASE", (code,)
             ).fetchone()
         return self._role_dict(row) if row else None
 
-    def get_role(self, role_id: str) -> dict[str, Any] | None:
-        with self.connect() as connection:
-            row = connection.execute(
+    def get_role(
+        self, role_id: str, *, connection: sqlite3.Connection | None = None
+    ) -> dict[str, Any] | None:
+        with self.connection_scope(connection) as active_connection:
+            row = active_connection.execute(
                 "SELECT * FROM roles WHERE id = ?", (role_id,)
             ).fetchone()
         return self._role_dict(row) if row else None
 
     def create_role(
-        self, code: str, name: str, description: str, *, is_system: bool = False
+        self,
+        code: str,
+        name: str,
+        description: str,
+        *,
+        is_system: bool = False,
+        connection: sqlite3.Connection | None = None,
     ) -> dict[str, Any]:
         role_id = str(uuid.uuid4())
-        with self.connect() as connection:
-            connection.execute(
+        with self.connection_scope(connection) as active_connection:
+            active_connection.execute(
                 """
                 INSERT INTO roles (id, code, name, description, is_system)
                 VALUES (?, ?, ?, ?, ?)
                 """,
                 (role_id, code, name, description, int(is_system)),
             )
-        role = self.get_role(role_id)
+        role = self.get_role(role_id, connection=connection)
         assert role is not None
         return role
 
     def update_role(
         self,
         role_id: str,
         *,
         name: str | None = None,
         description: str | None = None,
         is_enabled: bool | None = None,
+        connection: sqlite3.Connection | None = None,
     ) -> dict[str, Any]:
-        with self.connect() as connection:
-            connection.execute("BEGIN IMMEDIATE")
-            role = connection.execute(
+        with self.connection_scope(connection) as active_connection:
+            if connection is None:
+                active_connection.execute("BEGIN IMMEDIATE")
+            role = active_connection.execute(
                 "SELECT * FROM roles WHERE id = ?", (role_id,)
             ).fetchone()
             if role is None:
                 raise KeyError(role_id)
             if (
                 role["code"].casefold() == "system_admin"
                 and role["is_enabled"]
                 and is_enabled is False
             ):
-                active_admins = connection.execute(
+                active_admins = active_connection.execute(
                     """
                     SELECT COUNT(DISTINCT u.id)
                     FROM users AS u
                     JOIN user_roles AS ur ON ur.user_id = u.id
                     WHERE ur.role_id = ? AND u.status = 'active' AND u.is_deleted = 0
                     """,
                     (role_id,),
                 ).fetchone()[0]
                 if active_admins:
                     raise ValueError("系统必须至少保留一个启用的系统管理员")
-            connection.execute(
+            active_connection.execute(
                 """
                 UPDATE roles
                 SET name = COALESCE(?, name),
                     description = COALESCE(?, description),
                     is_enabled = COALESCE(?, is_enabled),
                     updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                 """,
                 (
                     name,
                     description,
                     int(is_enabled) if is_enabled is not None else None,
                     role_id,
                 ),
             )
-        updated = self.get_role(role_id)
+        updated = self.get_role(role_id, connection=connection)
         assert updated is not None
         return updated
 
-    def list_role_permissions(self, role_id: str) -> list[dict[str, str]]:
-        with self.connect() as connection:
-            rows = connection.execute(
+    def list_role_permissions(
+        self, role_id: str, *, connection: sqlite3.Connection | None = None
+    ) -> list[dict[str, str]]:
+        with self.connection_scope(connection) as active_connection:
+            rows = active_connection.execute(
                 """
                 SELECT permission_code, data_scope
                 FROM role_permissions
                 WHERE role_id = ?
                 ORDER BY permission_code
                 """,
                 (role_id,),
             ).fetchall()
         return [dict(row) for row in rows]
 
     def replace_role_permissions(
-        self, role_id: str, permissions: list[tuple[str, str]]
+        self,
+        role_id: str,
+        permissions: list[tuple[str, str]],
+        *,
+        connection: sqlite3.Connection | None = None,
     ) -> list[dict[str, str]]:
         expanded = self._expanded_permissions(tuple(permissions))
-        with self.connect() as connection:
-            connection.execute("BEGIN IMMEDIATE")
-            if connection.execute(
+        with self.connection_scope(connection) as active_connection:
+            if connection is None:
+                active_connection.execute("BEGIN IMMEDIATE")
+            if active_connection.execute(
                 "SELECT 1 FROM roles WHERE id = ?", (role_id,)
             ).fetchone() is None:
                 raise KeyError(role_id)
             known_permissions = {
                 row["code"]
-                for row in connection.execute(
+                for row in active_connection.execute(
                     "SELECT code FROM permissions"
                 ).fetchall()
             }
             unknown = {code for code, _ in expanded} - known_permissions
             if unknown:
                 raise ValueError(f"未知权限: {', '.join(sorted(unknown))}")
-            connection.execute(
+            active_connection.execute(
                 "DELETE FROM role_permissions WHERE role_id = ?", (role_id,)
             )
-            connection.executemany(
+            active_connection.executemany(
                 """
                 INSERT INTO role_permissions (role_id, permission_code, data_scope)
                 VALUES (?, ?, ?)
                 """,
                 [(role_id, code, scope) for code, scope in expanded],
             )
-        return self.list_role_permissions(role_id)
+        return self.list_role_permissions(role_id, connection=connection)
 
     def list_users(self) -> list[dict[str, Any]]:
         with self.connect() as connection:
             rows = connection.execute(
                 "SELECT * FROM users WHERE is_deleted = 0 ORDER BY normalized_username"
             ).fetchall()
         return [self._user_dict(row) for row in rows]
 
-    def get_user(self, user_id: str) -> dict[str, Any] | None:
-        with self.connect() as connection:
-            row = connection.execute(
+    def get_user(
+        self, user_id: str, *, connection: sqlite3.Connection | None = None
+    ) -> dict[str, Any] | None:
+        with self.connection_scope(connection) as active_connection:
+            row = active_connection.execute(
                 "SELECT * FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
             ).fetchone()
-        return self._user_dict(row) if row else None
+        return self._user_dict(row, connection=connection) if row else None
 
     def create_user(
-        self, username: str, display_name: str, password_hash: str
+        self,
+        username: str,
+        display_name: str,
+        password_hash: str,
+        *,
+        connection: sqlite3.Connection | None = None,
     ) -> dict[str, Any]:
         user_id = str(uuid.uuid4())
-        with self.connect() as connection:
-            connection.execute(
+        with self.connection_scope(connection) as active_connection:
+            active_connection.execute(
                 """
                 INSERT INTO users
                     (id, username, normalized_username, display_name, password_hash)
                 VALUES (?, ?, ?, ?, ?)
                 """,
                 (user_id, username, username.casefold(), display_name, password_hash),
             )
-        user = self.get_user(user_id)
+        user = self.get_user(user_id, connection=connection)
         assert user is not None
         return user
 
     def update_user(
         self,
         user_id: str,
         *,
         display_name: str | None = None,
         status: str | None = None,
         role_ids: list[str] | None = None,
+        connection: sqlite3.Connection | None = None,
     ) -> dict[str, Any]:
         unique_role_ids = (
             list(dict.fromkeys(role_ids)) if role_ids is not None else None
         )
-        with self.connect() as connection:
-            connection.execute("BEGIN IMMEDIATE")
-            user = connection.execute(
+        with self.connection_scope(connection) as active_connection:
+            if connection is None:
+                active_connection.execute("BEGIN IMMEDIATE")
+            user = active_connection.execute(
                 "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
             ).fetchone()
             if user is None or user["is_deleted"]:
                 raise KeyError(user_id)
 
             roles: list[sqlite3.Row] | None = None
             if unique_role_ids is not None:
                 role_placeholders = ",".join("?" for _ in unique_role_ids)
-                roles = connection.execute(
+                roles = active_connection.execute(
                     f"SELECT id, code, is_enabled FROM roles "
                     f"WHERE id IN ({role_placeholders})"
                     if unique_role_ids
                     else "SELECT id, code, is_enabled FROM roles WHERE 0",
                     unique_role_ids,
                 ).fetchall()
                 if len(roles) != len(unique_role_ids):
                     raise ValueError("角色不存在")
 
-            currently_admin = connection.execute(
+            currently_admin = active_connection.execute(
                 """
                 SELECT 1 FROM user_roles AS ur
                 JOIN roles AS r ON r.id = ur.role_id
                 WHERE ur.user_id = ? AND r.code = 'system_admin' AND r.is_enabled = 1
                 """,
                 (user_id,),
             ).fetchone() is not None
             remains_admin = currently_admin
             if roles is not None:
                 remains_admin = any(
                     role["code"].casefold() == "system_admin"
                     and role["is_enabled"]
                     for role in roles
                 )
             resulting_status = status or user["status"]
             if (
                 currently_admin
                 and user["status"] == "active"
                 and (resulting_status != "active" or not remains_admin)
             ):
-                other_admins = connection.execute(
+                other_admins = active_connection.execute(
                     """
                     SELECT COUNT(DISTINCT u.id)
                     FROM users AS u
                     JOIN user_roles AS ur ON ur.user_id = u.id
                     JOIN roles AS r ON r.id = ur.role_id
                     WHERE u.status = 'active' AND u.is_deleted = 0
                       AND r.code = 'system_admin' AND r.is_enabled = 1
                       AND u.id <> ?
                     """,
                     (user_id,),
                 ).fetchone()[0]
                 if other_admins < 1:
                     raise ValueError("系统必须至少保留一个启用的系统管理员")
 
-            connection.execute(
+            active_connection.execute(
                 """
                 UPDATE users
                 SET display_name = COALESCE(?, display_name),
                     status = COALESCE(?, status),
                     updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                 """,
                 (display_name, status, user_id),
             )
             if unique_role_ids is not None:
-                connection.execute(
+                active_connection.execute(
                     "DELETE FROM user_roles WHERE user_id = ?", (user_id,)
                 )
-                connection.executemany(
+                active_connection.executemany(
                     "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
                     [(user_id, role_id) for role_id in unique_role_ids],
                 )
             if resulting_status == "disabled":
-                connection.execute(
+                active_connection.execute(
                     "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                     "WHERE user_id = ? AND revoked_at IS NULL",
                     (user_id,),
                 )
-        user = self.get_user(user_id)
+        user = self.get_user(user_id, connection=connection)
         assert user is not None
         return user
 
     def replace_user_roles(
-        self, user_id: str, role_ids: list[str]
+        self,
+        user_id: str,
+        role_ids: list[str],
+        *,
+        connection: sqlite3.Connection | None = None,
     ) -> list[str]:
-        user = self.update_user(user_id, role_ids=role_ids)
+        user = self.update_user(
+            user_id, role_ids=role_ids, connection=connection
+        )
         return user["role_codes"]
 
-    def user_role_codes(self, user_id: str) -> list[str]:
-        with self.connect() as connection:
-            rows = connection.execute(
+    def user_role_codes(
+        self, user_id: str, *, connection: sqlite3.Connection | None = None
+    ) -> list[str]:
+        with self.connection_scope(connection) as active_connection:
+            rows = active_connection.execute(
                 """
                 SELECT r.code FROM user_roles AS ur
                 JOIN roles AS r ON r.id = ur.role_id
                 WHERE ur.user_id = ? ORDER BY r.code
                 """,
                 (user_id,),
             ).fetchall()
         return [row["code"] for row in rows]
 
     def count_active_system_admins(
@@ -584,18 +635,22 @@ class IdentityRepository:
     def disable_user(self, user_id: str) -> None:
         self.update_user(user_id, status="disabled")
 
     @staticmethod
     def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
         role = dict(row)
         role["is_system"] = bool(role["is_system"])
         role["is_enabled"] = bool(role["is_enabled"])
         return role
 
-    def _user_dict(self, row: sqlite3.Row) -> dict[str, Any]:
+    def _user_dict(
+        self, row: sqlite3.Row, *, connection: sqlite3.Connection | None = None
+    ) -> dict[str, Any]:
         user = dict(row)
-        user["role_codes"] = self.user_role_codes(user["id"])
+        user["role_codes"] = self.user_role_codes(
+            user["id"], connection=connection
+        )
         user.pop("password_hash", None)
         user.pop("normalized_username", None)
         user.pop("is_deleted", None)
         user.pop("updated_at", None)
         return user
diff --git a/identity/session_service.py b/identity/session_service.py
index 21a6569..96ce10a 100644
--- a/identity/session_service.py
+++ b/identity/session_service.py
@@ -6,20 +6,21 @@
 # GitHub: https://github.com/NanmiCoder
 # Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
 #
 
 from __future__ import annotations
 
 import base64
 import hashlib
 import hmac
 import secrets
+import sqlite3
 import uuid
 from dataclasses import dataclass
 from datetime import datetime, timedelta, timezone
 
 from fastapi import Request
 
 from .repository import IdentityRepository
 from .schema import DATA_SCOPE_ORDER, Principal
 
 
@@ -41,97 +42,108 @@ class SessionService:
         *,
         session_ttl: timedelta = timedelta(hours=12),
         secure_cookie: bool = False,
         csrf_secret: bytes | None = None,
     ) -> None:
         self.repository = repository
         self.session_ttl = session_ttl
         self.secure_cookie = secure_cookie
         self._csrf_secret = csrf_secret or _CSRF_DERIVATION_KEY
 
-    def create_session(self, user_id: str, request: Request) -> tuple[str, str]:
-        with self.repository.connect() as connection:
-            user = connection.execute(
+    def create_session(
+        self,
+        user_id: str,
+        request: Request,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> tuple[str, str]:
+        with self.repository.connection_scope(connection) as active_connection:
+            user = active_connection.execute(
                 "SELECT status, is_deleted FROM users WHERE id = ?", (user_id,)
             ).fetchone()
             if user is None or user["status"] != "active" or user["is_deleted"]:
                 raise ValueError("用户不可登录")
 
             raw_token = secrets.token_urlsafe(32)
             csrf_token = self._csrf_for_token(raw_token)
             now = datetime.now(timezone.utc)
-            connection.execute(
+            active_connection.execute(
                 """
                 INSERT INTO sessions
                     (id, user_id, token_hash, csrf_token_hash, expires_at,
                      last_seen_at, user_agent, ip_address)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                 """,
                 (
                     str(uuid.uuid4()),
                     user_id,
                     self._hash_token(raw_token),
                     self._hash_token(csrf_token),
                     (now + self.session_ttl).isoformat(),
                     now.isoformat(),
                     request.headers.get("user-agent"),
                     request.client.host if request.client else None,
                 ),
             )
         return raw_token, csrf_token
 
-    def resolve_session(self, raw_token: str | None) -> SessionContext | None:
+    def resolve_session(
+        self,
+        raw_token: str | None,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> SessionContext | None:
         if not raw_token:
             return None
         token_hash = self._hash_token(raw_token)
-        with self.repository.connect() as connection:
-            row = connection.execute(
+        with self.repository.connection_scope(connection) as active_connection:
+            row = active_connection.execute(
                 """
                 SELECT s.id AS session_id, s.user_id, s.csrf_token_hash,
                        s.expires_at, s.revoked_at, u.username, u.display_name,
                        u.status, u.is_deleted
                 FROM sessions AS s
                 JOIN users AS u ON u.id = s.user_id
                 WHERE s.token_hash = ?
                 """,
                 (token_hash,),
             ).fetchone()
             if row is None or row["revoked_at"] is not None:
                 return None
 
             if row["status"] != "active" or row["is_deleted"]:
-                connection.execute(
+                active_connection.execute(
                     "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                     "WHERE user_id = ? AND revoked_at IS NULL",
                     (row["user_id"],),
                 )
                 return None
 
             if self._parse_datetime(row["expires_at"]) <= datetime.now(timezone.utc):
-                connection.execute(
+                active_connection.execute(
                     "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (row["session_id"],),
                 )
                 return None
 
-            role_rows = connection.execute(
+            role_rows = active_connection.execute(
                 """
                 SELECT r.code, rp.permission_code, rp.data_scope
                 FROM user_roles AS ur
                 JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                 LEFT JOIN role_permissions AS rp ON rp.role_id = r.id
                 WHERE ur.user_id = ?
                 ORDER BY r.code, rp.permission_code
                 """,
                 (row["user_id"],),
             ).fetchall()
-            connection.execute(
+            active_connection.execute(
                 "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), row["session_id"]),
             )
 
         role_codes = tuple(sorted({role["code"] for role in role_rows}))
         permissions = frozenset(
             role["permission_code"]
             for role in role_rows
             if role["permission_code"] is not None
         )
@@ -158,31 +170,41 @@ class SessionService:
             ),
             csrf_token=csrf_token,
         )
 
     def verify_csrf(self, raw_token: str, supplied_token: str | None) -> bool:
         if not supplied_token:
             return False
         expected = self._csrf_for_token(raw_token)
         return hmac.compare_digest(expected, supplied_token)
 
-    def revoke_session(self, raw_token: str) -> None:
-        with self.repository.connect() as connection:
-            connection.execute(
+    def revoke_session(
+        self,
+        raw_token: str,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> None:
+        with self.repository.connection_scope(connection) as active_connection:
+            active_connection.execute(
                 "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                 "WHERE token_hash = ? AND revoked_at IS NULL",
                 (self._hash_token(raw_token),),
             )
 
-    def revoke_user_sessions(self, user_id: str) -> None:
-        with self.repository.connect() as connection:
-            connection.execute(
+    def revoke_user_sessions(
+        self,
+        user_id: str,
+        *,
+        connection: sqlite3.Connection | None = None,
+    ) -> None:
+        with self.repository.connection_scope(connection) as active_connection:
+            active_connection.execute(
                 "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
                 "WHERE user_id = ? AND revoked_at IS NULL",
                 (user_id,),
             )
 
     @staticmethod
     def _hash_token(token: str) -> str:
         return hashlib.sha256(token.encode("utf-8")).hexdigest()
 
     def _csrf_for_token(self, raw_token: str) -> str:
diff --git a/tests/test_audit_service.py b/tests/test_audit_service.py
new file mode 100644
index 0000000..2173402
--- /dev/null
+++ b/tests/test_audit_service.py
@@ -0,0 +1,409 @@
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
+from identity.permission_catalog import PERMISSIONS
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
+
+
+def test_legacy_resource_id_is_backfilled_as_target_id(
+    repository: IdentityRepository,
+):
+    with repository.connect() as connection:
+        connection.execute(
+            """
+            INSERT INTO audit_logs (
+                id, action, resource_type, resource_id, details_json
+            ) VALUES ('legacy-event', 'legacy.update', 'legacy_resource', 'legacy-42', '{}')
+            """
+        )
+
+    event = AuditService(repository).get("legacy-event")
+
+    assert event is not None
+    assert event["target_type"] == "legacy_resource"
+    assert event["target_id"] == "legacy-42"
+
+
+def test_audit_read_permission_is_seeded_and_assignable(
+    repository: IdentityRepository,
+):
+    role = repository.create_role("auditor", "Auditor", "")
+
+    grants = repository.replace_role_permissions(
+        role["id"], [("audit.read", "all")]
+    )
+
+    assert "audit.read" in PERMISSIONS
+    assert grants == [{"permission_code": "audit.read", "data_scope": "all"}]
+
+
+def test_user_mutation_rolls_back_when_atomic_audit_insert_fails(
+    repository: IdentityRepository, monkeypatch: pytest.MonkeyPatch
+):
+    audit_service = AuditService(repository)
+    app = FastAPI()
+    app.include_router(
+        build_identity_router(
+            repository,
+            SessionService(repository, secure_cookie=False),
+            audit_service,
+        )
+    )
+    client = TestClient(app, raise_server_exceptions=False)
+    setup = client.post(
+        "/api/auth/setup",
+        json={
+            "username": "admin",
+            "display_name": "Admin",
+            "password": "Admin-Strong-123!",
+        },
+    )
+    csrf_token = setup.json()["csrf_token"]
+    user = repository.create_user(
+        "rollback-user",
+        "Rollback User",
+        PasswordService().hash("Rollback-Strong-123!"),
+    )
+
+    def fail_insert(event, *, connection=None):
+        del event
+        assert connection is not None
+        raise RuntimeError("forced audit insert failure")
+
+    monkeypatch.setattr(audit_service.repository, "insert", fail_insert)
+    response = client.patch(
+        f"/api/admin/users/{user['id']}",
+        headers={"X-CSRF-Token": csrf_token},
+        json={"status": "disabled"},
+    )
+
+    assert response.status_code == 500
+    assert repository.get_user(user["id"])["status"] == "active"
+    assert audit_service.query(action="user.update") == []
diff --git a/tests/test_web_security.py b/tests/test_web_security.py
new file mode 100644
index 0000000..ed62ac8
--- /dev/null
+++ b/tests/test_web_security.py
@@ -0,0 +1,181 @@
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
+from identity.password_service import PasswordService
+from identity.permission_service import PermissionService
+from WebGUI import (
+    SYSTEM_DB_PATH,
+    app as production_app,
+    configure_cors,
+    install_security_middleware,
+)
+from audit.service import AuditService
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
+
+
+def test_production_webgui_wires_shared_identity_security_and_routes():
+    repository = production_app.state.identity_repository
+
+    assert repository.db_path == SYSTEM_DB_PATH
+    assert SYSTEM_DB_PATH.parts[-3:] == (".runtime", "system", "system.db")
+    assert isinstance(production_app.state.identity_password_service, PasswordService)
+    assert isinstance(production_app.state.identity_session_service, SessionService)
+    assert isinstance(production_app.state.identity_permission_service, PermissionService)
+    assert isinstance(production_app.state.audit_service, AuditService)
+    assert production_app.state.identity_session_service.repository is repository
+    assert production_app.state.identity_permission_service.repository is repository
+
+    routes = {
+        (route.path, method)
+        for route in production_app.routes
+        for method in (getattr(route, "methods", None) or ())
+    }
+    assert ("/api/auth/login", "POST") in routes
+    assert ("/api/admin/audit", "GET") in routes
+
+    client = TestClient(production_app)
+    assert client.get("/api/auth/me").status_code == 401
+    assert client.get("/api/admin/audit").status_code == 401
