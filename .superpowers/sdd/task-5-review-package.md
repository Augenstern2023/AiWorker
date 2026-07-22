# Commits

e764b9b feat: add role-specific page routing

# Stat

 WebGUI.py                        |  14 +--
 pyproject.toml                   |   1 +
 templates/common/403.html        |  10 ++
 templates/common/tasks.html      |  10 ++
 templates/layouts/base.html      |  67 +++++++++++++
 tests/test_page_authorization.py | 207 +++++++++++++++++++++++++++++++++++++++
 uv.lock                          |  14 +++
 web/__init__.py                  |   9 ++
 web/navigation_service.py        | 178 +++++++++++++++++++++++++++++++++
 web/page_router.py               | 153 +++++++++++++++++++++++++++++
 10 files changed, 654 insertions(+), 9 deletions(-)

# Diff

diff --git a/WebGUI.py b/WebGUI.py
index 203177b..c15f44c 100644
--- a/WebGUI.py
+++ b/WebGUI.py
@@ -34,20 +34,21 @@ from pathlib import Path
 from typing import List, Dict, Optional, Any
 from contextlib import asynccontextmanager
 from logging.handlers import RotatingFileHandler
 
 import uvicorn
 import httpx
 from fastapi import FastAPI, HTTPException, Request
 from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
 from fastapi.staticfiles import StaticFiles
 from fastapi.middleware.cors import CORSMiddleware
+from fastapi.templating import Jinja2Templates
 from pydantic import BaseModel
 
 import config
 from audit.api import build_audit_router
 from audit.service import AuditService
 from analysis.analytics_api import register_analytics_routes
 from analysis.asr import (
     ASRError,
     OpenAICompatibleASRProvider,
     SenseVoiceASRProvider,
@@ -62,20 +63,22 @@ from identity.permission_service import PermissionService
 from identity.repository import IdentityRepository
 from identity.session_service import SessionService
 from agents.douyin_analysis_agent import AnalyzeOptions as DouyinAnalyzeOptions
 from agents.douyin_analysis_agent import analyze_rank_file as analyze_douyin_rank_file
 from agents.hongguo_analysis_agent import AnalyzeOptions, analyze_rank_file
 from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, load_settings as load_hongguo_drama_settings, public_settings as public_hongguo_drama_settings, save_settings as save_hongguo_drama_settings
 from agents.douyin_drama_fallback import DouyinDramaFallbackClient, DouyinFallbackError
 from core.job import CrawlJob
 from rag.api import register_rag_routes
 from runtime.job_manager import JobConflictError, JobManager, JobNotFoundError
+from web.navigation_service import NavigationService
+from web.page_router import build_page_router
 
 
 WORKSPACE_ROOT = Path(__file__).resolve().parent
 WEB_LOG_LEVEL = os.getenv("AIWORKER_WEB_LOG_LEVEL", "INFO").strip().upper()
 WEB_LOG_MAX_BYTES = 5 * 1024 * 1024
 WEB_LOG_BACKUP_COUNT = 5
 WEB_LOG_PATH = WORKSPACE_ROOT / "logs" / "webgui.log"
 SYSTEM_DB_PATH = WORKSPACE_ROOT / ".runtime" / "system" / "system.db"
 UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
 CSRF_EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/setup"})
@@ -242,20 +245,21 @@ identity_password_service = PasswordService()
 identity_session_service = SessionService(identity_repository)
 identity_permission_service = PermissionService(
     identity_repository, identity_session_service
 )
 audit_service = AuditService(identity_repository)
 app.state.identity_repository = identity_repository
 app.state.identity_password_service = identity_password_service
 app.state.identity_session_service = identity_session_service
 app.state.identity_permission_service = identity_permission_service
 app.state.audit_service = audit_service
+templates = Jinja2Templates(directory=str(WORKSPACE_ROOT / "templates"))
 
 
 @app.middleware("http")
 async def log_backend_requests(request, call_next):
     started_at = time.perf_counter()
     try:
         response = await call_next(request)
     except Exception:
         backend_logger.exception(
             "Unhandled request error: %s %s request_id=%s",
@@ -281,20 +285,21 @@ configure_cors(app)
 app.include_router(
     build_identity_router(
         identity_repository,
         identity_session_service,
         audit_service,
         password_service=identity_password_service,
         permission_service=identity_permission_service,
     )
 )
 app.include_router(build_audit_router(audit_service))
+app.include_router(build_page_router(templates, NavigationService()))
 register_rag_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
 register_analytics_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
 register_today_rank_routes(app, root=Path(__file__).resolve().parent)
 register_creative_routes(
     app,
     root=Path(__file__).resolve().parent,
     runtime_dir=Path(__file__).resolve().parent / ".runtime",
     job_manager=job_manager,
 )
 
@@ -1296,29 +1301,20 @@ def build_comment_command(ids: List[str], project_name: str):
                 state.log("qrcode login state expired", "warning")
         else:
             state.log("qrcode login state not found", "warning")
     except Exception as e:
         state.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")
 
     return command
 
 # --- API Routes ---
 
-@app.get("/")
-async def index():
-    with open("templates/index.html", "r", encoding="utf-8") as f:
-        return HTMLResponse(
-            f.read(),
-            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
-        )
-
-
 @app.get("/favicon.ico")
 async def favicon():
     return Response(status_code=204)
 
 @app.get("/api/config")
 async def get_config():
     if os.path.exists(state.config_file):
         try:
             return normalize_config_root(read_config_data())
         except:
diff --git a/pyproject.toml b/pyproject.toml
index 43d2390..7a316d8 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -35,15 +35,16 @@ dependencies = [
     "uvicorn==0.29.0",
     "wordcloud==1.9.3",
     "pre-commit>=3.5.0",
     "openpyxl>=3.1.2",
     "pytest>=7.4.0",
     "pytest-asyncio>=0.21.0",
     "websockets>=15.0.1",
     "ttkbootstrap>=1.12.2",
     "imageio-ffmpeg>=0.6.0",
     "chromadb>=0.5.0",
+    "jinja2>=3.1.6",
 ]
 
 [[tool.uv.index]]
 url = "https://pypi.tuna.tsinghua.edu.cn/simple"
 default = true
diff --git a/templates/common/403.html b/templates/common/403.html
new file mode 100644
index 0000000..f8a63f1
--- /dev/null
+++ b/templates/common/403.html
@@ -0,0 +1,10 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="text-center py-5">
+    <p class="display-6 fw-semibold mb-2">403</p>
+    <h1 class="h3">无权访问</h1>
+    <p class="text-body-secondary">当前账号没有访问此页面的权限。</p>
+    <a class="btn btn-primary" href="/">返回工作台</a>
+</div>
+{% endblock %}
diff --git a/templates/common/tasks.html b/templates/common/tasks.html
new file mode 100644
index 0000000..21ef20e
--- /dev/null
+++ b/templates/common/tasks.html
@@ -0,0 +1,10 @@
+{% extends "layouts/base.html" %}
+
+{% block content %}
+<div class="d-flex align-items-center justify-content-between border-bottom pb-3 mb-4">
+    <h1 class="h3 mb-0">{{ page_title }}</h1>
+</div>
+<div class="alert alert-light border" role="status">
+    暂无待处理内容。
+</div>
+{% endblock %}
diff --git a/templates/layouts/base.html b/templates/layouts/base.html
new file mode 100644
index 0000000..50b2eec
--- /dev/null
+++ b/templates/layouts/base.html
@@ -0,0 +1,67 @@
+<!doctype html>
+<html lang="zh-CN">
+<head>
+    <meta charset="utf-8">
+    <meta name="viewport" content="width=device-width, initial-scale=1">
+    <title>{{ page_title }} - AiWorker</title>
+    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
+    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
+    {% block styles %}{% endblock %}
+</head>
+<body class="bg-body-tertiary">
+    <header class="navbar navbar-dark bg-dark px-3">
+        <a class="navbar-brand" href="/">AiWorker</a>
+        {% if principal %}
+        <div class="d-flex align-items-center gap-3 text-white">
+            <span>{{ principal.display_name }}</span>
+            <button class="btn btn-outline-light btn-sm" id="logout-button" type="button">
+                <i class="bi bi-box-arrow-right" aria-hidden="true"></i>
+                退出登录
+            </button>
+        </div>
+        {% endif %}
+    </header>
+    <div class="container-fluid">
+        <div class="row min-vh-100">
+            {% if principal %}
+            <nav class="col-12 col-md-3 col-xl-2 bg-white border-end py-3" aria-label="主导航">
+                <a class="nav-link mb-2{% if current_route == '/tasks' %} active{% endif %}" href="/tasks">
+                    <i class="bi bi-list-task me-2" aria-hidden="true"></i>我的任务
+                </a>
+                {% for group in navigation %}
+                <section class="mb-3">
+                    <div class="px-3 py-2 fw-semibold text-body-secondary">
+                        <i class="bi {{ group.icon }} me-2" aria-hidden="true"></i>{{ group.label }}
+                    </div>
+                    <div class="nav nav-pills flex-column">
+                        {% for item in group['items'] %}
+                        <a class="nav-link{% if current_route == item.route %} active{% endif %}" href="{{ item.route }}">
+                            {{ item.label }}
+                        </a>
+                        {% endfor %}
+                    </div>
+                </section>
+                {% endfor %}
+            </nav>
+            {% endif %}
+            <main class="col px-4 py-4">
+                {% block content %}{% endblock %}
+            </main>
+        </div>
+    </div>
+    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
+    {% if principal %}
+    <script>
+        document.getElementById("logout-button")?.addEventListener("click", async () => {
+            const session = await fetch("/api/auth/me").then((response) => response.json());
+            await fetch("/api/auth/logout", {
+                method: "POST",
+                headers: {"X-CSRF-Token": session.csrf_token}
+            });
+            window.location.assign("/login");
+        });
+    </script>
+    {% endif %}
+    {% block scripts %}{% endblock %}
+</body>
+</html>
diff --git a/tests/test_page_authorization.py b/tests/test_page_authorization.py
new file mode 100644
index 0000000..3b41f4d
--- /dev/null
+++ b/tests/test_page_authorization.py
@@ -0,0 +1,207 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+
+from __future__ import annotations
+
+from pathlib import Path
+
+import pytest
+from fastapi import FastAPI
+from fastapi.templating import Jinja2Templates
+from fastapi.testclient import TestClient
+
+from identity.api import build_identity_router
+from identity.password_service import PasswordService
+from identity.permission_service import PermissionService
+from identity.repository import IdentityRepository
+from identity.session_service import SessionService
+from web.navigation_service import PAGE_ROUTES, NavigationService
+from web.page_router import build_page_router
+
+
+ROLE_CASES = (
+    ("selection_operator", "/selection/ranks", {"选品工作台"}),
+    ("creative_planner", "/planning/projects", {"创意策划"}),
+    ("screenwriter", "/writing/projects", {"编剧工作台"}),
+    ("project_owner", "/review/tasks", {"审批中心"}),
+    (
+        "system_admin",
+        "/admin/users",
+        {"选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"},
+    ),
+)
+PROBE_ROUTES = {
+    "选品工作台": "/selection/ranks",
+    "创意策划": "/planning/projects",
+    "编剧工作台": "/writing/projects",
+    "审批中心": "/review/tasks",
+    "系统管理": "/admin/users",
+}
+PAGE_PERMISSION_BY_GROUP = {
+    "选品工作台": "page.selection",
+    "创意策划": "page.planning",
+    "编剧工作台": "page.writing",
+    "审批中心": "page.review",
+    "系统管理": "page.admin.delivery",
+}
+
+
+@pytest.fixture
+def page_app(tmp_path: Path) -> tuple[FastAPI, IdentityRepository]:
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    session_service = SessionService(repository, secure_cookie=False)
+    permission_service = PermissionService(repository, session_service)
+    app = FastAPI()
+    app.state.identity_permission_service = permission_service
+    app.include_router(
+        build_identity_router(
+            repository,
+            session_service,
+            permission_service=permission_service,
+        )
+    )
+    templates = Jinja2Templates(
+        directory=str(Path(__file__).resolve().parents[1] / "templates")
+    )
+    app.include_router(build_page_router(templates, NavigationService()))
+    return app, repository
+
+
+def _create_role_client(
+    page_app: tuple[FastAPI, IdentityRepository],
+    role_code: str,
+    *,
+    username: str | None = None,
+    client: TestClient | None = None,
+) -> TestClient:
+    app, repository = page_app
+    role = repository.get_role_by_code(role_code)
+    assert role is not None
+    username = username or role_code.replace("_", "-")
+    password = "Role-Access-123!"
+    user = repository.create_user(
+        username, role["name"], PasswordService().hash(password)
+    )
+    repository.replace_user_roles(user["id"], [role["id"]])
+    client = client or TestClient(app)
+    response = client.post(
+        "/api/auth/login", json={"username": username, "password": password}
+    )
+    assert response.status_code == 200
+    return client
+
+
+@pytest.fixture
+def writer_client(page_app: tuple[FastAPI, IdentityRepository]) -> TestClient:
+    return _create_role_client(page_app, "screenwriter")
+
+
+@pytest.fixture
+def admin_client(page_app: tuple[FastAPI, IdentityRepository]) -> TestClient:
+    return _create_role_client(page_app, "system_admin")
+
+
+@pytest.mark.parametrize(("role_code", "own_route", "visible_groups"), ROLE_CASES)
+def test_default_roles_have_exact_navigation_and_page_visibility(
+    page_app: tuple[FastAPI, IdentityRepository],
+    role_code: str,
+    own_route: str,
+    visible_groups: set[str],
+):
+    client = _create_role_client(page_app, role_code)
+
+    html = client.get(own_route).text
+
+    for label in PROBE_ROUTES:
+        assert (label in html) is (label in visible_groups)
+    allowed_permissions = {
+        PAGE_PERMISSION_BY_GROUP[label] for label in visible_groups
+    }
+    for route, permission, _ in PAGE_ROUTES:
+        concrete_route = route.replace("{project_id}", "project-1").replace(
+            "{episode_number}", "2"
+        )
+        response = client.get(concrete_route, follow_redirects=False)
+        expected_status = 200 if permission in allowed_permissions else 403
+        assert response.status_code == expected_status
+
+
+def test_screenwriter_cannot_open_selection_page(writer_client: TestClient):
+    response = writer_client.get("/selection/ranks", follow_redirects=False)
+    assert response.status_code == 403
+    assert "今日榜单" not in writer_client.get("/writing/projects").text
+
+
+def test_system_admin_sees_all_navigation(admin_client: TestClient):
+    html = admin_client.get("/admin/users").text
+    for label in ("选品工作台", "创意策划", "编剧工作台", "审批中心", "系统管理"):
+        assert label in html
+
+
+def test_root_uses_role_priority_then_last_authorized_page(
+    page_app: tuple[FastAPI, IdentityRepository],
+):
+    client = _create_role_client(page_app, "selection_operator")
+    assert client.get("/", follow_redirects=False).headers["location"] == (
+        "/selection/dashboard"
+    )
+
+    assert client.get("/selection/topics").status_code == 200
+    assert client.get("/", follow_redirects=False).headers["location"] == (
+        "/selection/topics"
+    )
+
+    assert client.get("/tasks").status_code == 200
+    assert client.get("/", follow_redirects=False).headers["location"] == "/tasks"
+
+    assert client.get("/selection/topics").status_code == 200
+    role = page_app[1].get_role_by_code("selection_operator")
+    assert role is not None
+    page_app[1].replace_role_permissions(role["id"], [])
+    assert client.get("/", follow_redirects=False).headers["location"] == "/tasks"
+
+
+def test_last_page_is_isolated_between_login_sessions(
+    page_app: tuple[FastAPI, IdentityRepository],
+):
+    client = _create_role_client(
+        page_app, "selection_operator", username="first-selector"
+    )
+    assert client.get("/selection/topics").status_code == 200
+    csrf_token = client.get("/api/auth/me").json()["csrf_token"]
+    assert client.post(
+        "/api/auth/logout", headers={"X-CSRF-Token": csrf_token}
+    ).status_code == 204
+
+    _create_role_client(
+        page_app,
+        "selection_operator",
+        username="second-selector",
+        client=client,
+    )
+
+    assert client.get("/", follow_redirects=False).headers["location"] == (
+        "/selection/dashboard"
+    )
+
+
+@pytest.mark.parametrize("route", ("/", "/tasks", "/writing/projects"))
+def test_unauthenticated_page_redirects_to_login(page_app, route: str):
+    app, _ = page_app
+    response = TestClient(app).get(route, follow_redirects=False)
+
+    assert response.status_code == 303
+    assert response.headers["location"] == "/login"
+
+
+def test_forbidden_page_uses_html_error_template(writer_client: TestClient):
+    response = writer_client.get("/admin/users")
+
+    assert response.status_code == 403
+    assert "无权访问" in response.text
diff --git a/uv.lock b/uv.lock
index ae7ee4d..773ca30 100644
--- a/uv.lock
+++ b/uv.lock
@@ -1282,20 +1282,32 @@ sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/72/34/14ca021ce8e5df
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/cb/b1/3846dd7f199d53cb17f49cba7e651e9ce294d8497c8c150530ed11865bb8/iniconfig-2.3.0-py3-none-any.whl", hash = "sha256:f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12", size = 7484, upload-time = "2025-10-18T21:55:41.639Z" },
 ]
 
 [[package]]
 name = "jieba"
 version = "0.42.1"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c6/cb/18eeb235f833b726522d7ebed54f2278ce28ba9438e3135ab0278d9792a2/jieba-0.42.1.tar.gz", hash = "sha256:055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2", size = 19214172, upload-time = "2020-01-20T14:27:23.5Z" }
 
+[[package]]
+name = "jinja2"
+version = "3.1.6"
+source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
+dependencies = [
+    { name = "markupsafe" },
+]
+sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/df/bf/f7da0350254c0ed7c72f3e33cef02e048281fec7ecec5f032d4aac52226b/jinja2-3.1.6.tar.gz", hash = "sha256:0137fb05990d35f1275a587e9aee6d56da821fc83491a0fb838183be43f66d6d", size = 245115, upload-time = "2025-03-05T20:05:02.478Z" }
+wheels = [
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/62/a1/3d680cbfd5f4b8f15abc1d571870c5fc3e594bb582bc3b64ea099db13e56/jinja2-3.1.6-py3-none-any.whl", hash = "sha256:85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67", size = 134899, upload-time = "2025-03-05T20:05:00.369Z" },
+]
+
 [[package]]
 name = "jmespath"
 version = "1.0.1"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/00/2a/e867e8531cf3e36b41201936b7fa7ba7b5702dbef42922193f05c8976cd6/jmespath-1.0.1.tar.gz", hash = "sha256:90261b206d6defd58fdd5e85f478bf633a2901798906be2ad389150c5c60edbe", size = 25843, upload-time = "2022-06-17T18:00:12.224Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/31/b4/b9b800c45527aadd64d5b442f9b932b00648617eb5d63d2c7a6587b7cafc/jmespath-1.0.1-py3-none-any.whl", hash = "sha256:02e2e4cc71b5bcab88332eebf907519190dd9e6e82107fa7f83b1003a6252980", size = 20256, upload-time = "2022-06-17T18:00:10.251Z" },
 ]
 
 [[package]]
@@ -1588,20 +1600,21 @@ dependencies = [
     { name = "aiosqlite" },
     { name = "alembic" },
     { name = "argon2-cffi" },
     { name = "asyncmy" },
     { name = "chromadb" },
     { name = "cryptography" },
     { name = "fastapi" },
     { name = "httpx" },
     { name = "imageio-ffmpeg" },
     { name = "jieba" },
+    { name = "jinja2" },
     { name = "matplotlib" },
     { name = "motor" },
     { name = "opencv-python" },
     { name = "openpyxl" },
     { name = "pandas" },
     { name = "parsel" },
     { name = "pillow" },
     { name = "playwright" },
     { name = "pre-commit" },
     { name = "pydantic" },
@@ -1628,20 +1641,21 @@ requires-dist = [
     { name = "aiosqlite", specifier = ">=0.21.0" },
     { name = "alembic", specifier = ">=1.16.5" },
     { name = "argon2-cffi", specifier = ">=23.1.0,<26.0.0" },
     { name = "asyncmy", specifier = ">=0.2.10" },
     { name = "chromadb", specifier = ">=0.5.0" },
     { name = "cryptography", specifier = ">=45.0.7" },
     { name = "fastapi", specifier = "==0.110.2" },
     { name = "httpx", specifier = "==0.28.1" },
     { name = "imageio-ffmpeg", specifier = ">=0.6.0" },
     { name = "jieba", specifier = "==0.42.1" },
+    { name = "jinja2", specifier = ">=3.1.6" },
     { name = "matplotlib", specifier = "==3.9.0" },
     { name = "motor", specifier = ">=3.3.0" },
     { name = "opencv-python", specifier = ">=4.11.0.86" },
     { name = "openpyxl", specifier = ">=3.1.2" },
     { name = "pandas", specifier = "==2.2.3" },
     { name = "parsel", specifier = "==1.9.1" },
     { name = "pillow", specifier = "==9.5.0" },
     { name = "playwright", specifier = "==1.45.0" },
     { name = "pre-commit", specifier = ">=3.5.0" },
     { name = "pydantic", specifier = "==2.5.2" },
diff --git a/web/__init__.py b/web/__init__.py
new file mode 100644
index 0000000..00fc09d
--- /dev/null
+++ b/web/__init__.py
@@ -0,0 +1,9 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+
+"""Web page routing and navigation services."""
diff --git a/web/navigation_service.py b/web/navigation_service.py
new file mode 100644
index 0000000..8c137cc
--- /dev/null
+++ b/web/navigation_service.py
@@ -0,0 +1,178 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+
+from __future__ import annotations
+
+import re
+import threading
+from collections import OrderedDict
+from copy import deepcopy
+from typing import Any
+
+from identity.schema import Principal
+
+
+NAVIGATION_CATALOG = (
+    {
+        "label": "选品工作台",
+        "icon": "bi-funnel",
+        "permission": "page.selection",
+        "route": "/selection/dashboard",
+        "items": (
+            {"label": "工作概览", "route": "/selection/dashboard"},
+            {"label": "今日榜单", "route": "/selection/ranks"},
+            {"label": "选题库", "route": "/selection/topics"},
+            {"label": "剧目素材", "route": "/selection/dramas"},
+            {"label": "剧目拆解", "route": "/selection/breakdowns"},
+        ),
+    },
+    {
+        "label": "创意策划",
+        "icon": "bi-lightbulb",
+        "permission": "page.planning",
+        "route": "/planning/projects",
+        "items": (
+            {"label": "策划项目", "route": "/planning/projects"},
+        ),
+    },
+    {
+        "label": "编剧工作台",
+        "icon": "bi-pencil-square",
+        "permission": "page.writing",
+        "route": "/writing/projects",
+        "items": (
+            {"label": "编剧项目", "route": "/writing/projects"},
+        ),
+    },
+    {
+        "label": "审批中心",
+        "icon": "bi-clipboard-check",
+        "permission": "page.review",
+        "route": "/review/tasks",
+        "items": (
+            {"label": "负责项目", "route": "/review/projects"},
+            {"label": "审批任务", "route": "/review/tasks"},
+            {"label": "版本审批", "route": "/review/approvals"},
+        ),
+    },
+    {
+        "label": "系统管理",
+        "icon": "bi-gear",
+        "permission": "page.admin.delivery",
+        "route": "/admin/users",
+        "items": (
+            {"label": "用户管理", "route": "/admin/users"},
+            {"label": "角色权限", "route": "/admin/roles"},
+            {"label": "系统配置", "route": "/admin/config"},
+            {"label": "数据管理", "route": "/admin/data"},
+            {"label": "数据分析", "route": "/admin/analytics"},
+            {"label": "机器任务", "route": "/admin/jobs"},
+            {"label": "成果交付", "route": "/admin/deliveries"},
+            {"label": "审计日志", "route": "/admin/audit"},
+        ),
+    },
+)
+
+PAGE_ROUTES = (
+    ("/selection/dashboard", "page.selection", "工作概览"),
+    ("/selection/ranks", "page.selection", "今日榜单"),
+    ("/selection/topics", "page.selection", "选题库"),
+    ("/selection/dramas", "page.selection", "剧目素材"),
+    ("/selection/breakdowns", "page.selection", "剧目拆解"),
+    ("/planning/projects", "page.planning", "策划项目"),
+    (
+        "/planning/projects/{project_id}/directions",
+        "page.planning",
+        "仿写方向",
+    ),
+    ("/planning/projects/{project_id}/brief", "page.planning", "创作任务书"),
+    ("/planning/projects/{project_id}/characters", "page.planning", "人物关系"),
+    ("/writing/projects", "page.writing", "编剧项目"),
+    ("/writing/projects/{project_id}/outlines", "page.writing", "全剧大纲"),
+    (
+        "/writing/projects/{project_id}/episodes/{episode_number}/beats",
+        "page.writing",
+        "单集节拍",
+    ),
+    ("/review/projects", "page.review", "负责项目"),
+    ("/review/tasks", "page.review", "审批任务"),
+    ("/review/approvals", "page.review", "版本审批"),
+    ("/review/projects/{project_id}/members", "page.review", "项目成员"),
+    ("/review/projects/{project_id}/versions", "page.review", "项目版本"),
+    ("/admin/users", "page.admin.delivery", "用户管理"),
+    ("/admin/roles", "page.admin.delivery", "角色权限"),
+    ("/admin/config", "page.admin.delivery", "系统配置"),
+    ("/admin/data", "page.admin.delivery", "数据管理"),
+    ("/admin/analytics", "page.admin.delivery", "数据分析"),
+    ("/admin/jobs", "page.admin.delivery", "机器任务"),
+    ("/admin/deliveries", "page.admin.delivery", "成果交付"),
+    ("/admin/audit", "page.admin.delivery", "审计日志"),
+)
+
+
+class NavigationService:
+    """Build navigation and landing choices from effective page grants."""
+
+    def __init__(self) -> None:
+        self._route_patterns = tuple(
+            (self._compile_route(route), permission)
+            for route, permission, _ in PAGE_ROUTES
+        )
+        self._last_routes: OrderedDict[str, str] = OrderedDict()
+        self._last_routes_lock = threading.Lock()
+
+    def items_for(self, principal: Principal) -> list[dict[str, Any]]:
+        return [
+            deepcopy(group)
+            for group in NAVIGATION_CATALOG
+            if principal.is_system_admin
+            or group["permission"] in principal.permissions
+        ]
+
+    def can_open(self, principal: Principal, route: str) -> bool:
+        permission = self.permission_for(route)
+        if permission is None:
+            return route == "/tasks"
+        return principal.is_system_admin or permission in principal.permissions
+
+    def permission_for(self, route: str) -> str | None:
+        path = route.split("?", 1)[0]
+        for pattern, permission in self._route_patterns:
+            if pattern.fullmatch(path):
+                return permission
+        return None
+
+    def default_route(self, principal: Principal) -> str:
+        items = self.items_for(principal)
+        return items[0]["route"] if items else "/tasks"
+
+    def remember_route(self, principal: Principal, route: str) -> None:
+        if not self.can_open(principal, route):
+            return
+        with self._last_routes_lock:
+            self._last_routes.pop(principal.session_id, None)
+            self._last_routes[principal.session_id] = route
+            if len(self._last_routes) > 4096:
+                self._last_routes.popitem(last=False)
+
+    def last_route_for(self, principal: Principal) -> str | None:
+        with self._last_routes_lock:
+            route = self._last_routes.get(principal.session_id)
+            if route is not None:
+                self._last_routes.move_to_end(principal.session_id)
+            return route
+
+    def forget_route(self, principal: Principal) -> None:
+        with self._last_routes_lock:
+            self._last_routes.pop(principal.session_id, None)
+
+    @staticmethod
+    def _compile_route(route: str) -> re.Pattern[str]:
+        escaped = re.escape(route)
+        dynamic = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
+        return re.compile(dynamic)
diff --git a/web/page_router.py b/web/page_router.py
new file mode 100644
index 0000000..b3939a5
--- /dev/null
+++ b/web/page_router.py
@@ -0,0 +1,153 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+
+from __future__ import annotations
+
+from collections.abc import Callable
+
+from fastapi import APIRouter, Depends, HTTPException, Request
+from fastapi.responses import HTMLResponse, RedirectResponse, Response
+from fastapi.routing import APIRoute
+from fastapi.templating import Jinja2Templates
+
+from identity.dependencies import current_principal, require_page
+from identity.schema import Principal
+
+from .navigation_service import PAGE_ROUTES, NavigationService
+
+
+def build_page_router(
+    templates: Jinja2Templates, navigation_service: NavigationService
+) -> APIRouter:
+    class PageRoute(APIRoute):
+        def get_route_handler(self) -> Callable:
+            handler = super().get_route_handler()
+
+            async def handle(request: Request) -> Response:
+                try:
+                    return await handler(request)
+                except HTTPException as exc:
+                    if exc.status_code == 401:
+                        return RedirectResponse("/login", status_code=303)
+                    if exc.status_code != 403:
+                        raise
+                    principal = _principal_from_request(request)
+                    return templates.TemplateResponse(
+                        request,
+                        "common/403.html",
+                        _template_context(
+                            request,
+                            principal,
+                            navigation_service,
+                            page_title="无权访问",
+                        ),
+                        status_code=403,
+                    )
+
+            return handle
+
+    router = APIRouter(route_class=PageRoute)
+
+    @router.get("/", include_in_schema=False)
+    def landing(
+        request: Request, principal: Principal = Depends(current_principal)
+    ) -> RedirectResponse:
+        last_route = navigation_service.last_route_for(principal)
+        route = (
+            last_route
+            if last_route and navigation_service.can_open(principal, last_route)
+            else navigation_service.default_route(principal)
+        )
+        if last_route and route != last_route:
+            navigation_service.forget_route(principal)
+        return RedirectResponse(route, status_code=303)
+
+    @router.get("/tasks", response_class=HTMLResponse)
+    def tasks(
+        request: Request, principal: Principal = Depends(current_principal)
+    ) -> Response:
+        return _page_response(
+            templates,
+            navigation_service,
+            request,
+            principal,
+            page_title="我的任务",
+        )
+
+    def page_endpoint(page_title: str, permission: str):
+        def endpoint(
+            request: Request,
+            principal: Principal = Depends(require_page(permission)),
+        ) -> Response:
+            return _page_response(
+                templates,
+                navigation_service,
+                request,
+                principal,
+                page_title=page_title,
+            )
+
+        return endpoint
+
+    for route, permission, title in PAGE_ROUTES:
+        router.add_api_route(
+            route,
+            page_endpoint(title, permission),
+            methods=["GET"],
+            response_class=HTMLResponse,
+            name=f"page_{permission}_{title}",
+            openapi_extra={"required_page_permission": permission},
+        )
+
+    return router
+
+
+def _page_response(
+    templates: Jinja2Templates,
+    navigation_service: NavigationService,
+    request: Request,
+    principal: Principal,
+    *,
+    page_title: str,
+) -> Response:
+    response = templates.TemplateResponse(
+        request,
+        "common/tasks.html",
+        _template_context(
+            request,
+            principal,
+            navigation_service,
+            page_title=page_title,
+        ),
+    )
+    navigation_service.remember_route(principal, request.url.path)
+    return response
+
+
+def _template_context(
+    request: Request,
+    principal: Principal | None,
+    navigation_service: NavigationService,
+    *,
+    page_title: str,
+) -> dict:
+    return {
+        "request": request,
+        "principal": principal,
+        "navigation": navigation_service.items_for(principal) if principal else [],
+        "current_route": request.url.path,
+        "page_title": page_title,
+    }
+
+
+def _principal_from_request(request: Request) -> Principal | None:
+    service = getattr(request.app.state, "identity_permission_service", None)
+    if service is None:
+        return None
+    raw_token = request.cookies.get(service.session_service.cookie_name)
+    return service.principal_for_session(raw_token)
