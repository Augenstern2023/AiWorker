# Commits

e5a19f2 fix: synchronize default role grants
0985027 feat: add identity and role persistence

# Stat

 identity/__init__.py              |  14 ++
 identity/permission_catalog.py    | 193 ++++++++++++++++++++++
 identity/repository.py            | 330 ++++++++++++++++++++++++++++++++++++++
 identity/schema.py                |  91 +++++++++++
 pyproject.toml                    |   1 +
 tests/test_identity_repository.py | 215 +++++++++++++++++++++++++
 uv.lock                           | 165 ++++++++++++++++++-
 7 files changed, 1007 insertions(+), 2 deletions(-)

# Diff

diff --git a/identity/__init__.py b/identity/__init__.py
new file mode 100644
index 0000000..b21b425
--- /dev/null
+++ b/identity/__init__.py
@@ -0,0 +1,14 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+"""Local identity, role, and permission persistence."""
+
+from .repository import IdentityRepository
+
+__all__ = ["IdentityRepository"]
diff --git a/identity/permission_catalog.py b/identity/permission_catalog.py
new file mode 100644
index 0000000..2c605bf
--- /dev/null
+++ b/identity/permission_catalog.py
@@ -0,0 +1,193 @@
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
+from dataclasses import dataclass
+
+
+_PERMISSION_CODES = (
+    "*",
+    "page.selection",
+    "page.planning",
+    "page.writing",
+    "page.review",
+    "page.admin.delivery",
+    "rank.read",
+    "analytics.read",
+    "topic.read",
+    "topic.generate",
+    "topic.edit",
+    "topic.submit",
+    "drama.search",
+    "drama.download",
+    "drama.transcript.read",
+    "data.media.preview",
+    "breakdown.read",
+    "breakdown.read.approved",
+    "breakdown.generate",
+    "breakdown.edit",
+    "breakdown.version.create",
+    "breakdown.submit",
+    "project.read.assigned",
+    "project.read.managed",
+    "project.create",
+    "project.edit.own_draft",
+    "project.manage",
+    "project.member.manage",
+    "project.archive",
+    "blueprint.read",
+    "blueprint.generate",
+    "blueprint.edit",
+    "blueprint.version.create",
+    "brief.read",
+    "brief.read.approved",
+    "brief.edit",
+    "brief.submit",
+    "character.read",
+    "character.read.approved",
+    "character.generate",
+    "character.edit",
+    "character.submit",
+    "relationship.read",
+    "relationship.read.approved",
+    "relationship.edit",
+    "outline.read",
+    "outline.read.approved",
+    "outline.generate",
+    "outline.edit",
+    "outline.version.create",
+    "outline.submit",
+    "beats.read",
+    "beats.generate",
+    "beats.edit",
+    "beats.version.create",
+    "beats.submit",
+    "workflow.pool.read",
+    "workflow.claim",
+    "workflow.task.update",
+    "workflow.read.managed",
+    "workflow.assign",
+    "workflow.reassign",
+    "workflow.reclaim",
+    "workflow.cancel",
+    "version.read.managed",
+    "version.compare",
+    "version.approve",
+    "version.return",
+    "job.create.selection",
+    "job.create.creative",
+    "job.create.writing",
+    "job.read.own",
+    "job.read.project",
+    "job.cancel.own",
+    "audit.read.project",
+    "approved_content.read",
+    "export.create",
+)
+
+# The catalog is intentionally a mapping: membership checks use permission codes,
+# while seeding has a stable fallback display label to update idempotently.
+PERMISSIONS = {code: code for code in _PERMISSION_CODES}
+
+PERMISSION_DEPENDENCIES = {
+    "breakdown.edit": {"breakdown.read", "topic.read", "page.selection"},
+    "brief.edit": {"brief.read", "project.read.assigned", "page.planning"},
+    "character.edit": {"character.read", "brief.read.approved", "page.planning"},
+    "outline.edit": {
+        "outline.read",
+        "brief.read.approved",
+        "character.read.approved",
+        "page.writing",
+    },
+    "beats.edit": {"beats.read", "outline.read.approved", "page.writing"},
+    "version.approve": {"version.read.managed", "version.compare", "page.review"},
+    "workflow.assign": {"workflow.read.managed", "project.read.managed", "page.review"},
+    "export.create": {"approved_content.read", "page.admin.delivery"},
+}
+
+
+@dataclass(frozen=True)
+class RoleSeed:
+    code: str
+    name: str
+    description: str
+    is_system: bool
+    permissions: tuple[tuple[str, str], ...]
+
+
+def _scoped(codes: str, scope: str) -> tuple[tuple[str, str], ...]:
+    return tuple((code, scope) for code in codes.split())
+
+
+DEFAULT_ROLES = (
+    RoleSeed(
+        code="selection_operator",
+        name="选品运营",
+        description="整理榜单、选题、素材与剧目拆解，并提交成果审批。",
+        is_system=True,
+        permissions=_scoped(
+            """page.selection rank.read analytics.read topic.read topic.generate topic.edit
+            topic.submit drama.search drama.download drama.transcript.read data.media.preview
+            breakdown.read breakdown.generate breakdown.edit breakdown.version.create
+            breakdown.submit workflow.pool.read workflow.claim workflow.task.update
+            job.create.selection job.read.own job.cancel.own""",
+            "task",
+        ),
+    ),
+    RoleSeed(
+        code="creative_planner",
+        name="创意策划",
+        description="将批准的拆解转化为创作方案、任务书和人物关系。",
+        is_system=True,
+        permissions=_scoped(
+            """page.planning project.read.assigned project.create project.edit.own_draft
+            breakdown.read.approved blueprint.read blueprint.generate blueprint.edit
+            blueprint.version.create brief.read brief.edit brief.submit character.read
+            character.generate character.edit character.submit relationship.read
+            relationship.edit workflow.pool.read workflow.claim workflow.task.update
+            job.create.creative job.read.project job.cancel.own""",
+            "project",
+        ),
+    ),
+    RoleSeed(
+        code="screenwriter",
+        name="编剧",
+        description="依据批准的创作方案完成人物、全剧大纲与单集节拍。",
+        is_system=True,
+        permissions=_scoped(
+            """page.writing project.read.assigned brief.read.approved character.read.approved
+            relationship.read.approved outline.read outline.generate outline.edit
+            outline.version.create outline.submit beats.read beats.generate beats.edit
+            beats.version.create beats.submit workflow.pool.read workflow.claim
+            workflow.task.update job.create.writing job.read.project job.cancel.own""",
+            "project",
+        ),
+    ),
+    RoleSeed(
+        code="project_owner",
+        name="项目负责人",
+        description="负责项目组织、任务分配、版本审批和阶段流转。",
+        is_system=True,
+        permissions=_scoped(
+            """page.review project.read.managed project.manage project.member.manage
+            project.archive workflow.read.managed workflow.assign workflow.reassign
+            workflow.reclaim workflow.cancel version.read.managed version.compare
+            version.approve version.return job.read.project audit.read.project""",
+            "managed_projects",
+        ),
+    ),
+    RoleSeed(
+        code="system_admin",
+        name="系统管理员",
+        description="负责系统权限、平台运维、数据治理、成果交付和异常恢复。",
+        is_system=True,
+        permissions=(("*", "all"),),
+    ),
+)
diff --git a/identity/repository.py b/identity/repository.py
new file mode 100644
index 0000000..ee59ffe
--- /dev/null
+++ b/identity/repository.py
@@ -0,0 +1,330 @@
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
+from contextlib import contextmanager
+from pathlib import Path
+from typing import Any, Iterator
+
+from .permission_catalog import DEFAULT_ROLES, PERMISSIONS, PERMISSION_DEPENDENCIES
+
+
+SCHEMA_SQL = """
+CREATE TABLE IF NOT EXISTS users (
+    id TEXT PRIMARY KEY,
+    username TEXT NOT NULL,
+    normalized_username TEXT NOT NULL COLLATE NOCASE UNIQUE,
+    display_name TEXT NOT NULL,
+    password_hash TEXT NOT NULL,
+    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
+    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0, 1)),
+    last_login_at TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS roles (
+    id TEXT PRIMARY KEY,
+    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
+    name TEXT NOT NULL,
+    description TEXT NOT NULL DEFAULT '',
+    is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
+    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS permissions (
+    code TEXT PRIMARY KEY,
+    label TEXT NOT NULL,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS user_roles (
+    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
+    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    PRIMARY KEY (user_id, role_id)
+);
+CREATE TABLE IF NOT EXISTS role_permissions (
+    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
+    permission_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE RESTRICT,
+    data_scope TEXT NOT NULL CHECK (data_scope IN ('task', 'project', 'managed_projects', 'all')),
+    PRIMARY KEY (role_id, permission_code)
+);
+CREATE TABLE IF NOT EXISTS permission_dependencies (
+    permission_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
+    depends_on_code TEXT NOT NULL REFERENCES permissions(code) ON DELETE RESTRICT,
+    PRIMARY KEY (permission_code, depends_on_code),
+    CHECK (permission_code <> depends_on_code)
+);
+CREATE TABLE IF NOT EXISTS sessions (
+    id TEXT PRIMARY KEY,
+    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
+    token_hash TEXT NOT NULL UNIQUE,
+    csrf_token_hash TEXT NOT NULL,
+    expires_at TEXT NOT NULL,
+    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    revoked_at TEXT,
+    user_agent TEXT,
+    ip_address TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS project_members (
+    id TEXT PRIMARY KEY,
+    project_id TEXT NOT NULL,
+    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
+    project_role TEXT NOT NULL,
+    status TEXT NOT NULL DEFAULT 'active',
+    added_by TEXT REFERENCES users(id) ON DELETE SET NULL,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    UNIQUE (project_id, user_id, project_role)
+);
+CREATE TABLE IF NOT EXISTS workflow_instances (
+    id TEXT PRIMARY KEY,
+    workflow_type TEXT NOT NULL,
+    resource_type TEXT NOT NULL,
+    resource_id TEXT NOT NULL,
+    project_id TEXT,
+    status TEXT NOT NULL DEFAULT 'active',
+    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS workflow_tasks (
+    id TEXT PRIMARY KEY,
+    workflow_id TEXT NOT NULL REFERENCES workflow_instances(id) ON DELETE CASCADE,
+    task_type TEXT NOT NULL,
+    role_code TEXT NOT NULL,
+    topic_id INTEGER,
+    project_id TEXT,
+    assignee_id TEXT REFERENCES users(id) ON DELETE SET NULL,
+    assigned_by TEXT REFERENCES users(id) ON DELETE SET NULL,
+    status TEXT NOT NULL DEFAULT 'pending',
+    due_at TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS workflow_task_jobs (
+    task_id TEXT NOT NULL REFERENCES workflow_tasks(id) ON DELETE CASCADE,
+    job_id TEXT NOT NULL,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
+    PRIMARY KEY (task_id, job_id)
+);
+CREATE TABLE IF NOT EXISTS workflow_task_events (
+    id TEXT PRIMARY KEY,
+    task_id TEXT NOT NULL REFERENCES workflow_tasks(id) ON DELETE CASCADE,
+    event_type TEXT NOT NULL,
+    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
+    from_status TEXT,
+    to_status TEXT,
+    details_json TEXT NOT NULL DEFAULT '{}',
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS approval_records (
+    id TEXT PRIMARY KEY,
+    workflow_id TEXT REFERENCES workflow_instances(id) ON DELETE SET NULL,
+    task_id TEXT REFERENCES workflow_tasks(id) ON DELETE SET NULL,
+    version_id TEXT NOT NULL,
+    stage TEXT NOT NULL,
+    decision TEXT NOT NULL,
+    reviewer_id TEXT REFERENCES users(id) ON DELETE SET NULL,
+    comment TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE TABLE IF NOT EXISTS audit_logs (
+    id TEXT PRIMARY KEY,
+    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
+    action TEXT NOT NULL,
+    resource_type TEXT NOT NULL,
+    resource_id TEXT,
+    project_id TEXT,
+    reason TEXT,
+    details_json TEXT NOT NULL DEFAULT '{}',
+    ip_address TEXT,
+    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
+);
+CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
+CREATE INDEX IF NOT EXISTS idx_project_members_project_id ON project_members(project_id);
+CREATE INDEX IF NOT EXISTS idx_workflow_tasks_pool ON workflow_tasks(role_code, status);
+CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);
+"""
+
+
+class IdentityRepository:
+    def __init__(self, db_path: str | Path):
+        self.db_path = Path(db_path)
+        self.db_path.parent.mkdir(parents=True, exist_ok=True)
+        self._init_schema()
+
+    @contextmanager
+    def connect(self) -> Iterator[sqlite3.Connection]:
+        connection = sqlite3.connect(self.db_path)
+        connection.row_factory = sqlite3.Row
+        connection.execute("PRAGMA foreign_keys = ON")
+        try:
+            with connection:
+                yield connection
+        finally:
+            connection.close()
+
+    def _init_schema(self) -> None:
+        with self.connect() as connection:
+            connection.executescript(SCHEMA_SQL)
+
+    def seed_defaults(self) -> None:
+        with self.connect() as connection:
+            for code, label in PERMISSIONS.items():
+                connection.execute(
+                    """
+                    INSERT INTO permissions (code, label) VALUES (?, ?)
+                    ON CONFLICT(code) DO UPDATE SET
+                        label = excluded.label,
+                        updated_at = CURRENT_TIMESTAMP
+                    """,
+                    (code, label),
+                )
+
+            # Dependency edges belong to the system catalog, so reseeding makes
+            # this table an exact projection rather than retaining retired edges.
+            connection.execute("DELETE FROM permission_dependencies")
+            for permission_code, dependencies in PERMISSION_DEPENDENCIES.items():
+                for depends_on_code in dependencies:
+                    connection.execute(
+                        """
+                        INSERT INTO permission_dependencies (permission_code, depends_on_code)
+                        VALUES (?, ?)
+                        ON CONFLICT(permission_code, depends_on_code) DO NOTHING
+                        """,
+                        (permission_code, depends_on_code),
+                    )
+
+            for role in DEFAULT_ROLES:
+                role_id = f"role:{role.code}"
+                connection.execute(
+                    """
+                    INSERT INTO roles (id, code, name, description, is_system)
+                    VALUES (?, ?, ?, ?, ?)
+                    ON CONFLICT(code) DO UPDATE SET
+                        name = excluded.name,
+                        description = excluded.description,
+                        is_system = excluded.is_system,
+                        updated_at = CURRENT_TIMESTAMP
+                    """,
+                    (role_id, role.code, role.name, role.description, int(role.is_system)),
+                )
+                stored_role_id = connection.execute(
+                    "SELECT id FROM roles WHERE code = ?", (role.code,)
+                ).fetchone()["id"]
+                # Default-role grants are system-owned. Custom-role grants and
+                # every user-role binding remain untouched.
+                connection.execute(
+                    "DELETE FROM role_permissions WHERE role_id = ?",
+                    (stored_role_id,),
+                )
+                for permission_code, data_scope in self._expanded_permissions(role.permissions):
+                    connection.execute(
+                        """
+                        INSERT INTO role_permissions (role_id, permission_code, data_scope)
+                        VALUES (?, ?, ?)
+                        ON CONFLICT(role_id, permission_code) DO UPDATE SET
+                            data_scope = excluded.data_scope
+                        """,
+                        (stored_role_id, permission_code, data_scope),
+                    )
+
+    @staticmethod
+    def _expanded_permissions(
+        permissions: tuple[tuple[str, str], ...],
+    ) -> tuple[tuple[str, str], ...]:
+        expanded = dict(permissions)
+        pending = list(permissions)
+        while pending:
+            permission_code, data_scope = pending.pop()
+            for dependency in PERMISSION_DEPENDENCIES.get(permission_code, set()):
+                if dependency not in expanded:
+                    expanded[dependency] = data_scope
+                    pending.append((dependency, data_scope))
+        return tuple(expanded.items())
+
+    def list_roles(self) -> list[dict[str, Any]]:
+        with self.connect() as connection:
+            rows = connection.execute("SELECT * FROM roles ORDER BY code").fetchall()
+        return [self._role_dict(row) for row in rows]
+
+    def get_role_by_code(self, code: str) -> dict[str, Any] | None:
+        with self.connect() as connection:
+            row = connection.execute(
+                "SELECT * FROM roles WHERE code = ? COLLATE NOCASE", (code,)
+            ).fetchone()
+        return self._role_dict(row) if row else None
+
+    def list_role_permissions(self, role_id: str) -> list[dict[str, str]]:
+        with self.connect() as connection:
+            rows = connection.execute(
+                """
+                SELECT permission_code, data_scope
+                FROM role_permissions
+                WHERE role_id = ?
+                ORDER BY permission_code
+                """,
+                (role_id,),
+            ).fetchall()
+        return [dict(row) for row in rows]
+
+    def count_active_system_admins(
+        self,
+        excluding_user_id: str | None = None,
+        removing_role_id: str | None = None,
+    ) -> int:
+        conditions = [
+            "u.status = 'active'",
+            "u.is_deleted = 0",
+            "r.code = 'system_admin'",
+            "r.is_enabled = 1",
+        ]
+        parameters: list[str] = []
+        if excluding_user_id is not None and removing_role_id is None:
+            conditions.append("u.id <> ?")
+            parameters.append(excluding_user_id)
+        elif excluding_user_id is not None and removing_role_id is not None:
+            conditions.append("NOT (u.id = ? AND r.id = ?)")
+            parameters.extend((excluding_user_id, removing_role_id))
+
+        with self.connect() as connection:
+            row = connection.execute(
+                f"""
+                SELECT COUNT(DISTINCT u.id)
+                FROM users AS u
+                JOIN user_roles AS ur ON ur.user_id = u.id
+                JOIN roles AS r ON r.id = ur.role_id
+                WHERE {' AND '.join(conditions)}
+                """,
+                parameters,
+            ).fetchone()
+        return int(row[0])
+
+    def assert_admin_survives(
+        self, user_id: str, removing_role_id: str | None = None
+    ) -> None:
+        if (
+            self.count_active_system_admins(
+                excluding_user_id=user_id, removing_role_id=removing_role_id
+            )
+            < 1
+        ):
+            raise ValueError("系统必须至少保留一个启用的系统管理员")
+
+    @staticmethod
+    def _role_dict(row: sqlite3.Row) -> dict[str, Any]:
+        role = dict(row)
+        role["is_system"] = bool(role["is_system"])
+        role["is_enabled"] = bool(role["is_enabled"])
+        return role
diff --git a/identity/schema.py b/identity/schema.py
new file mode 100644
index 0000000..b1a87dd
--- /dev/null
+++ b/identity/schema.py
@@ -0,0 +1,91 @@
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
+from dataclasses import dataclass
+from typing import Literal
+
+from pydantic import BaseModel, ConfigDict, Field
+
+
+UserStatus = Literal["active", "disabled"]
+DataScope = Literal["task", "project", "managed_projects", "all"]
+DATA_SCOPE_ORDER = {"task": 1, "project": 2, "managed_projects": 3, "all": 4}
+
+
+@dataclass(frozen=True)
+class Principal:
+    user_id: str
+    username: str
+    display_name: str
+    role_codes: tuple[str, ...]
+    permissions: frozenset[str]
+    data_scope: str
+    session_id: str
+
+    @property
+    def is_system_admin(self) -> bool:
+        return "system_admin" in self.role_codes or "*" in self.permissions
+
+
+class RolePermissionRequest(BaseModel):
+    permission_code: str
+    data_scope: DataScope = "task"
+
+
+class UserCreateRequest(BaseModel):
+    username: str = Field(min_length=1, max_length=128)
+    display_name: str = Field(min_length=1, max_length=128)
+    password: str = Field(min_length=1)
+    role_ids: list[str] = Field(default_factory=list)
+
+
+class UserUpdateRequest(BaseModel):
+    display_name: str | None = Field(default=None, min_length=1, max_length=128)
+    status: UserStatus | None = None
+    role_ids: list[str] | None = None
+
+
+class UserResponse(BaseModel):
+    model_config = ConfigDict(from_attributes=True)
+
+    id: str
+    username: str
+    display_name: str
+    status: UserStatus
+    role_codes: list[str] = Field(default_factory=list)
+    last_login_at: str | None = None
+    created_at: str
+
+
+class RoleCreateRequest(BaseModel):
+    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")
+    name: str = Field(min_length=1, max_length=128)
+    description: str = ""
+    permissions: list[RolePermissionRequest] = Field(default_factory=list)
+
+
+class RoleUpdateRequest(BaseModel):
+    name: str | None = Field(default=None, min_length=1, max_length=128)
+    description: str | None = None
+    is_enabled: bool | None = None
+    permissions: list[RolePermissionRequest] | None = None
+
+
+class RoleResponse(BaseModel):
+    model_config = ConfigDict(from_attributes=True)
+
+    id: str
+    code: str
+    name: str
+    description: str
+    is_system: bool
+    is_enabled: bool
+    permissions: list[RolePermissionRequest] = Field(default_factory=list)
diff --git a/pyproject.toml b/pyproject.toml
index 6603168..43d2390 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -3,20 +3,21 @@ name = "mediacrawler"
 author = "- <relakkes@gmail.com>"
 version = "0.1.0"
 description = "A social media crawler project, support Xiaohongshu, Weibo, Zhihu, Bilibili, Douyin, BaiduTieBa etc."
 readme = "README.md"
 requires-python = ">=3.11"
 dependencies = [
     "aiofiles~=23.2.1",
     "aiomysql==0.2.0",
     "aiosqlite>=0.21.0",
     "alembic>=1.16.5",
+    "argon2-cffi>=23.1.0,<26.0.0",
     "asyncmy>=0.2.10",
     "cryptography>=45.0.7",
     "fastapi==0.110.2",
     "httpx==0.28.1",
     "jieba==0.42.1",
     "matplotlib==3.9.0",
     "motor>=3.3.0",
     "opencv-python>=4.11.0.86",
     "pandas==2.2.3",
     "parsel==1.9.1",
diff --git a/tests/test_identity_repository.py b/tests/test_identity_repository.py
new file mode 100644
index 0000000..abe1be5
--- /dev/null
+++ b/tests/test_identity_repository.py
@@ -0,0 +1,215 @@
+# -*- coding: utf-8 -*-
+# Copyright (c) 2025 relakkes@gmail.com
+#
+# This file is part of MediaCrawler project.
+# Repository: https://github.com/NanmiCoder/MediaCrawler
+# GitHub: https://github.com/NanmiCoder
+# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
+#
+
+import sqlite3
+from pathlib import Path
+
+import pytest
+
+from identity.permission_catalog import PERMISSION_DEPENDENCIES
+from identity.repository import IdentityRepository
+
+
+SYSTEM_TABLES = {
+    "users",
+    "roles",
+    "permissions",
+    "user_roles",
+    "role_permissions",
+    "permission_dependencies",
+    "sessions",
+    "project_members",
+    "workflow_instances",
+    "workflow_tasks",
+    "workflow_task_jobs",
+    "workflow_task_events",
+    "approval_records",
+    "audit_logs",
+}
+
+
+def test_identity_repository_creates_all_system_tables(tmp_path: Path):
+    db_path = tmp_path / "system.db"
+    IdentityRepository(db_path)
+
+    with sqlite3.connect(db_path) as connection:
+        tables = {
+            row[0]
+            for row in connection.execute(
+                "SELECT name FROM sqlite_master WHERE type = 'table'"
+            )
+        }
+
+    assert SYSTEM_TABLES <= tables
+
+
+def test_identity_repository_seeds_exact_default_roles(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+
+    assert {role["code"] for role in repository.list_roles()} == {
+        "selection_operator",
+        "creative_planner",
+        "screenwriter",
+        "project_owner",
+        "system_admin",
+    }
+    admin = repository.get_role_by_code("system_admin")
+    assert admin is not None
+    assert admin["is_system"] is True
+    assert repository.list_role_permissions(admin["id"]) == [
+        {"permission_code": "*", "data_scope": "all"},
+    ]
+
+
+def test_seed_defaults_is_idempotent_and_preserves_custom_bindings(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+
+    with repository.connect() as connection:
+        connection.execute(
+            "INSERT INTO roles (id, code, name, description, is_system) "
+            "VALUES ('custom-role', 'custom', 'Custom', 'Keep me', 0)"
+        )
+        connection.execute(
+            "INSERT INTO users "
+            "(id, username, normalized_username, display_name, password_hash) "
+            "VALUES ('user-1', 'Alice', 'alice', 'Alice', 'hash')"
+        )
+        connection.execute(
+            "INSERT INTO user_roles (user_id, role_id) VALUES ('user-1', 'custom-role')"
+        )
+        connection.execute(
+            "UPDATE roles SET description = 'stale' WHERE code = 'selection_operator'"
+        )
+
+    repository.seed_defaults()
+
+    assert repository.get_role_by_code("custom") is not None
+    with repository.connect() as connection:
+        binding_count = connection.execute(
+            "SELECT COUNT(*) FROM user_roles WHERE user_id = 'user-1'"
+        ).fetchone()[0]
+    assert binding_count == 1
+    assert repository.get_role_by_code("selection_operator")["description"] != "stale"
+
+
+def test_seed_defaults_removes_stale_system_grants_and_dependencies(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    admin_role = repository.get_role_by_code("system_admin")
+    assert admin_role is not None
+
+    with repository.connect() as connection:
+        connection.execute(
+            "INSERT INTO permissions (code, label) VALUES ('stale.permission', 'Stale')"
+        )
+        connection.execute(
+            "INSERT INTO role_permissions (role_id, permission_code, data_scope) "
+            "VALUES (?, 'stale.permission', 'all')",
+            (admin_role["id"],),
+        )
+        connection.execute(
+            "INSERT INTO permission_dependencies (permission_code, depends_on_code) "
+            "VALUES ('breakdown.edit', 'stale.permission')"
+        )
+
+    repository.seed_defaults()
+
+    assert repository.list_role_permissions(admin_role["id"]) == [
+        {"permission_code": "*", "data_scope": "all"},
+    ]
+    with repository.connect() as connection:
+        stale_dependency_count = connection.execute(
+            """
+            SELECT COUNT(*) FROM permission_dependencies
+            WHERE permission_code = 'breakdown.edit'
+              AND depends_on_code = 'stale.permission'
+            """
+        ).fetchone()[0]
+    assert stale_dependency_count == 0
+
+
+def test_default_roles_materialize_permission_dependency_closure(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    planner = repository.get_role_by_code("creative_planner")
+    writer = repository.get_role_by_code("screenwriter")
+    assert planner is not None
+    assert writer is not None
+
+    planner_permissions = {
+        permission["permission_code"]
+        for permission in repository.list_role_permissions(planner["id"])
+    }
+    writer_permissions = {
+        permission["permission_code"]
+        for permission in repository.list_role_permissions(writer["id"])
+    }
+
+    assert {"brief.read.approved", "page.planning"} <= planner_permissions
+    assert {"outline.read.approved", "page.writing"} <= writer_permissions
+
+
+def test_permission_dependencies_are_seeded(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+
+    with repository.connect() as connection:
+        actual = {
+            (row["permission_code"], row["depends_on_code"])
+            for row in connection.execute(
+                "SELECT permission_code, depends_on_code FROM permission_dependencies"
+            )
+        }
+
+    expected = {
+        (permission, dependency)
+        for permission, dependencies in PERMISSION_DEPENDENCIES.items()
+        for dependency in dependencies
+    }
+    assert actual == expected
+
+
+def test_usernames_are_unique_after_normalization(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    with repository.connect() as connection:
+        connection.execute(
+            "INSERT INTO users "
+            "(id, username, normalized_username, display_name, password_hash) "
+            "VALUES ('user-1', 'Alice', 'alice', 'Alice', 'hash')"
+        )
+        with pytest.raises(sqlite3.IntegrityError):
+            connection.execute(
+                "INSERT INTO users "
+                "(id, username, normalized_username, display_name, password_hash) "
+                "VALUES ('user-2', 'ALICE', 'alice', 'Other Alice', 'hash')"
+            )
+
+
+def test_last_active_system_admin_cannot_be_removed(tmp_path: Path):
+    repository = IdentityRepository(tmp_path / "system.db")
+    repository.seed_defaults()
+    admin_role = repository.get_role_by_code("system_admin")
+    assert admin_role is not None
+
+    with repository.connect() as connection:
+        connection.execute(
+            "INSERT INTO users "
+            "(id, username, normalized_username, display_name, password_hash) "
+            "VALUES ('admin-1', 'admin', 'admin', 'Admin', 'hash')"
+        )
+        connection.execute(
+            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
+            ("admin-1", admin_role["id"]),
+        )
+
+    assert repository.count_active_system_admins() == 1
+    with pytest.raises(ValueError, match="系统必须至少保留一个启用的系统管理员"):
+        repository.assert_admin_survives("admin-1", admin_role["id"])
diff --git a/uv.lock b/uv.lock
index b1ae7fb..ae7ee4d 100644
--- a/uv.lock
+++ b/uv.lock
@@ -219,20 +219,64 @@ source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 dependencies = [
     { name = "idna" },
     { name = "sniffio" },
     { name = "typing-extensions", marker = "python_full_version < '3.13'" },
 ]
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/95/7d/4c1bd541d4dffa1b52bd83fb8527089e097a106fc90b467a7313b105f840/anyio-4.9.0.tar.gz", hash = "sha256:673c0c244e15788651a4ff38710fea9675823028a6f08a5eda409e0c9840a028", size = 190949, upload-time = "2025-03-17T00:02:54.77Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a1/ee/48ca1a7c89ffec8b6a0c5d02b89c305671d5ffd8d3c94acf8b8c408575bb/anyio-4.9.0-py3-none-any.whl", hash = "sha256:9f76d541cad6e36af7beb62e978876f3b41e3e04f2c1fbf0884604c0a9c4d93c", size = 100916, upload-time = "2025-03-17T00:02:52.713Z" },
 ]
 
+[[package]]
+name = "argon2-cffi"
+version = "25.1.0"
+source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
+dependencies = [
+    { name = "argon2-cffi-bindings" },
+]
+sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/0e/89/ce5af8a7d472a67cc819d5d998aa8c82c5d860608c4db9f46f1162d7dab9/argon2_cffi-25.1.0.tar.gz", hash = "sha256:694ae5cc8a42f4c4e2bf2ca0e64e51e23a040c6a517a85074683d3959e1346c1", size = 45706, upload-time = "2025-06-03T06:55:32.073Z" }
+wheels = [
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/4f/d3/a8b22fa575b297cd6e3e3b0155c7e25db170edf1c74783d6a31a2490b8d9/argon2_cffi-25.1.0-py3-none-any.whl", hash = "sha256:fdc8b074db390fccb6eb4a3604ae7231f219aa669a2652e0f20e16ba513d5741", size = 14657, upload-time = "2025-06-03T06:55:30.804Z" },
+]
+
+[[package]]
+name = "argon2-cffi-bindings"
+version = "25.1.0"
+source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
+dependencies = [
+    { name = "cffi", version = "1.17.1", source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }, marker = "python_full_version < '3.14'" },
+    { name = "cffi", version = "2.1.0", source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }, marker = "python_full_version >= '3.14'" },
+]
+sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5c/2d/db8af0df73c1cf454f71b2bbe5e356b8c1f8041c979f505b3d3186e520a9/argon2_cffi_bindings-25.1.0.tar.gz", hash = "sha256:b957f3e6ea4d55d820e40ff76f450952807013d361a65d7f28acc0acbf29229d", size = 1783441, upload-time = "2025-07-30T10:02:05.147Z" }
+wheels = [
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/60/97/3c0a35f46e52108d4707c44b95cfe2afcafc50800b5450c197454569b776/argon2_cffi_bindings-25.1.0-cp314-cp314t-macosx_10_13_universal2.whl", hash = "sha256:3d3f05610594151994ca9ccb3c771115bdb4daef161976a266f0dd8aa9996b8f", size = 54393, upload-time = "2025-07-30T10:01:40.97Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/9d/f4/98bbd6ee89febd4f212696f13c03ca302b8552e7dbf9c8efa11ea4a388c3/argon2_cffi_bindings-25.1.0-cp314-cp314t-macosx_10_13_x86_64.whl", hash = "sha256:8b8efee945193e667a396cbc7b4fb7d357297d6234d30a489905d96caabde56b", size = 29328, upload-time = "2025-07-30T10:01:41.916Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/43/24/90a01c0ef12ac91a6be05969f29944643bc1e5e461155ae6559befa8f00b/argon2_cffi_bindings-25.1.0-cp314-cp314t-macosx_11_0_arm64.whl", hash = "sha256:3c6702abc36bf3ccba3f802b799505def420a1b7039862014a65db3205967f5a", size = 31269, upload-time = "2025-07-30T10:01:42.716Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d4/d3/942aa10782b2697eee7af5e12eeff5ebb325ccfb86dd8abda54174e377e4/argon2_cffi_bindings-25.1.0-cp314-cp314t-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:a1c70058c6ab1e352304ac7e3b52554daadacd8d453c1752e547c76e9c99ac44", size = 86558, upload-time = "2025-07-30T10:01:43.943Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/0d/82/b484f702fec5536e71836fc2dbc8c5267b3f6e78d2d539b4eaa6f0db8bf8/argon2_cffi_bindings-25.1.0-cp314-cp314t-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:e2fd3bfbff3c5d74fef31a722f729bf93500910db650c925c2d6ef879a7e51cb", size = 92364, upload-time = "2025-07-30T10:01:44.887Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c9/c1/a606ff83b3f1735f3759ad0f2cd9e038a0ad11a3de3b6c673aa41c24bb7b/argon2_cffi_bindings-25.1.0-cp314-cp314t-musllinux_1_2_aarch64.whl", hash = "sha256:c4f9665de60b1b0e99bcd6be4f17d90339698ce954cfd8d9cf4f91c995165a92", size = 85637, upload-time = "2025-07-30T10:01:46.225Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/44/b4/678503f12aceb0262f84fa201f6027ed77d71c5019ae03b399b97caa2f19/argon2_cffi_bindings-25.1.0-cp314-cp314t-musllinux_1_2_x86_64.whl", hash = "sha256:ba92837e4a9aa6a508c8d2d7883ed5a8f6c308c89a4790e1e447a220deb79a85", size = 91934, upload-time = "2025-07-30T10:01:47.203Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/f0/c7/f36bd08ef9bd9f0a9cff9428406651f5937ce27b6c5b07b92d41f91ae541/argon2_cffi_bindings-25.1.0-cp314-cp314t-win32.whl", hash = "sha256:84a461d4d84ae1295871329b346a97f68eade8c53b6ed9a7ca2d7467f3c8ff6f", size = 28158, upload-time = "2025-07-30T10:01:48.341Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b3/80/0106a7448abb24a2c467bf7d527fe5413b7fdfa4ad6d6a96a43a62ef3988/argon2_cffi_bindings-25.1.0-cp314-cp314t-win_amd64.whl", hash = "sha256:b55aec3565b65f56455eebc9b9f34130440404f27fe21c3b375bf1ea4d8fbae6", size = 32597, upload-time = "2025-07-30T10:01:49.112Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/05/b8/d663c9caea07e9180b2cb662772865230715cbd573ba3b5e81793d580316/argon2_cffi_bindings-25.1.0-cp314-cp314t-win_arm64.whl", hash = "sha256:87c33a52407e4c41f3b70a9c2d3f6056d88b10dad7695be708c5021673f55623", size = 28231, upload-time = "2025-07-30T10:01:49.92Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/1d/57/96b8b9f93166147826da5f90376e784a10582dd39a393c99bb62cfcf52f0/argon2_cffi_bindings-25.1.0-cp39-abi3-macosx_10_9_universal2.whl", hash = "sha256:aecba1723ae35330a008418a91ea6cfcedf6d31e5fbaa056a166462ff066d500", size = 54121, upload-time = "2025-07-30T10:01:50.815Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/0a/08/a9bebdb2e0e602dde230bdde8021b29f71f7841bd54801bcfd514acb5dcf/argon2_cffi_bindings-25.1.0-cp39-abi3-macosx_10_9_x86_64.whl", hash = "sha256:2630b6240b495dfab90aebe159ff784d08ea999aa4b0d17efa734055a07d2f44", size = 29177, upload-time = "2025-07-30T10:01:51.681Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b6/02/d297943bcacf05e4f2a94ab6f462831dc20158614e5d067c35d4e63b9acb/argon2_cffi_bindings-25.1.0-cp39-abi3-macosx_11_0_arm64.whl", hash = "sha256:7aef0c91e2c0fbca6fc68e7555aa60ef7008a739cbe045541e438373bc54d2b0", size = 31090, upload-time = "2025-07-30T10:01:53.184Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c1/93/44365f3d75053e53893ec6d733e4a5e3147502663554b4d864587c7828a7/argon2_cffi_bindings-25.1.0-cp39-abi3-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl", hash = "sha256:1e021e87faa76ae0d413b619fe2b65ab9a037f24c60a1e6cc43457ae20de6dc6", size = 81246, upload-time = "2025-07-30T10:01:54.145Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/09/52/94108adfdd6e2ddf58be64f959a0b9c7d4ef2fa71086c38356d22dc501ea/argon2_cffi_bindings-25.1.0-cp39-abi3-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl", hash = "sha256:d3e924cfc503018a714f94a49a149fdc0b644eaead5d1f089330399134fa028a", size = 87126, upload-time = "2025-07-30T10:01:55.074Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/72/70/7a2993a12b0ffa2a9271259b79cc616e2389ed1a4d93842fac5a1f923ffd/argon2_cffi_bindings-25.1.0-cp39-abi3-musllinux_1_2_aarch64.whl", hash = "sha256:c87b72589133f0346a1cb8d5ecca4b933e3c9b64656c9d175270a000e73b288d", size = 80343, upload-time = "2025-07-30T10:01:56.007Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/78/9a/4e5157d893ffc712b74dbd868c7f62365618266982b64accab26bab01edc/argon2_cffi_bindings-25.1.0-cp39-abi3-musllinux_1_2_x86_64.whl", hash = "sha256:1db89609c06afa1a214a69a462ea741cf735b29a57530478c06eb81dd403de99", size = 86777, upload-time = "2025-07-30T10:01:56.943Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/74/cd/15777dfde1c29d96de7f18edf4cc94c385646852e7c7b0320aa91ccca583/argon2_cffi_bindings-25.1.0-cp39-abi3-win32.whl", hash = "sha256:473bcb5f82924b1becbb637b63303ec8d10e84c8d241119419897a26116515d2", size = 27180, upload-time = "2025-07-30T10:01:57.759Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/e2/c6/a759ece8f1829d1f162261226fbfd2c6832b3ff7657384045286d2afa384/argon2_cffi_bindings-25.1.0-cp39-abi3-win_amd64.whl", hash = "sha256:a98cd7d17e9f7ce244c0803cad3c23a7d379c301ba618a5fa76a67d116618b98", size = 31715, upload-time = "2025-07-30T10:01:58.56Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/42/b9/f8d6fa329ab25128b7e98fd83a3cb34d9db5b059a9847eddb840a0af45dd/argon2_cffi_bindings-25.1.0-cp39-abi3-win_arm64.whl", hash = "sha256:b0fdbcf513833809c882823f98dc2f931cf659d9a1429616ac3adebb49f5db94", size = 27149, upload-time = "2025-07-30T10:01:59.329Z" },
+]
+
 [[package]]
 name = "async-timeout"
 version = "5.0.1"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a5/ae/136395dfbfe00dfc94da3f3e136d0b13f394cba8f4841120e34226265780/async_timeout-5.0.1.tar.gz", hash = "sha256:d9321a7a3d5a6a5e187e824d2fa0793ce379a202935782d555d6e9d2735677d3", size = 9274, upload-time = "2024-11-06T16:41:39.6Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/fe/ba/e2081de779ca30d473f21f5b30e0e737c438205440784c7dfc81efc2b029/async_timeout-5.0.1-py3-none-any.whl", hash = "sha256:39e3809566ff85354557ec2398b55e096c8364bacac9405a7a1fa429e77fe76c", size = 6233, upload-time = "2024-11-06T16:41:37.9Z" },
 ]
 
 [[package]]
@@ -358,22 +402,33 @@ version = "2025.6.15"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/73/f7/f14b46d4bcd21092d7d3ccef689615220d8a08fb25e564b65d20738e672e/certifi-2025.6.15.tar.gz", hash = "sha256:d747aa5a8b9bbbb1bb8c22bb13e22bd1f18e9796defa16bab421f7f7a317323b", size = 158753, upload-time = "2025-06-15T02:45:51.329Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/84/ae/320161bd181fc06471eed047ecce67b693fd7515b16d495d8932db763426/certifi-2025.6.15-py3-none-any.whl", hash = "sha256:2e0c7ce7cb5d8f8634ca55d2ba7e6ec2689a2fd6537d8dec1296a477a4910057", size = 157650, upload-time = "2025-06-15T02:45:49.977Z" },
 ]
 
 [[package]]
 name = "cffi"
 version = "1.17.1"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
+resolution-markers = [
+    "python_full_version == '3.13.*' and sys_platform == 'darwin'",
+    "python_full_version == '3.12.*' and sys_platform == 'darwin'",
+    "python_full_version == '3.13.*' and platform_machine == 'aarch64' and sys_platform == 'linux'",
+    "python_full_version == '3.12.*' and platform_machine == 'aarch64' and sys_platform == 'linux'",
+    "(python_full_version == '3.13.*' and platform_machine != 'aarch64' and sys_platform == 'linux') or (python_full_version == '3.13.*' and sys_platform != 'darwin' and sys_platform != 'linux')",
+    "(python_full_version == '3.12.*' and platform_machine != 'aarch64' and sys_platform == 'linux') or (python_full_version == '3.12.*' and sys_platform != 'darwin' and sys_platform != 'linux')",
+    "python_full_version < '3.12' and sys_platform == 'darwin'",
+    "python_full_version < '3.12' and platform_machine == 'aarch64' and sys_platform == 'linux'",
+    "(python_full_version < '3.12' and platform_machine != 'aarch64' and sys_platform == 'linux') or (python_full_version < '3.12' and sys_platform != 'darwin' and sys_platform != 'linux')",
+]
 dependencies = [
-    { name = "pycparser" },
+    { name = "pycparser", marker = "python_full_version < '3.14'" },
 ]
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/fc/97/c783634659c2920c3fc70419e3af40972dbaf758daa229a7d6ea6135c90d/cffi-1.17.1.tar.gz", hash = "sha256:1c39c6016c32bc48dd54561950ebd6836e1670f2ae46128f67cf49e789c52824", size = 516621, upload-time = "2024-09-04T20:45:21.852Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/6b/f4/927e3a8899e52a27fa57a48607ff7dc91a9ebe97399b357b85a0c7892e00/cffi-1.17.1-cp311-cp311-macosx_10_9_x86_64.whl", hash = "sha256:a45e3c6913c5b87b3ff120dcdc03f6131fa0065027d0ed7ee6190736a74cd401", size = 182264, upload-time = "2024-09-04T20:43:51.124Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/6c/f5/6c3a8efe5f503175aaddcbea6ad0d2c96dad6f5abb205750d1b3df44ef29/cffi-1.17.1-cp311-cp311-macosx_11_0_arm64.whl", hash = "sha256:30c5e0cb5ae493c04c8b42916e52ca38079f1b235c2f8ae5f4527b963c401caf", size = 178651, upload-time = "2024-09-04T20:43:52.872Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/94/dd/a3f0118e688d1b1a57553da23b16bdade96d2f9bcda4d32e7d2838047ff7/cffi-1.17.1-cp311-cp311-manylinux_2_12_i686.manylinux2010_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:f75c7ab1f9e4aca5414ed4d8e5c0e303a34f4421f8a0d47a4d019ceff0ab6af4", size = 445259, upload-time = "2024-09-04T20:43:56.123Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2e/ea/70ce63780f096e16ce8588efe039d3c4f91deb1dc01e9c73a287939c79a6/cffi-1.17.1-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:a1ed2dd2972641495a3ec98445e09766f077aee98a1c896dcb4ad0d303628e41", size = 469200, upload-time = "2024-09-04T20:43:57.891Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/1c/a0/a4fa9f4f781bda074c3ddd57a572b060fa0df7655d2a4247bbe277200146/cffi-1.17.1-cp311-cp311-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:46bf43160c1a35f7ec506d254e5c890f3c03648a4dbac12d624e4490a7046cd1", size = 477235, upload-time = "2024-09-04T20:44:00.18Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/62/12/ce8710b5b8affbcdd5c6e367217c242524ad17a02fe5beec3ee339f69f85/cffi-1.17.1-cp311-cp311-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:a24ed04c8ffd54b0729c07cee15a81d964e6fee0e3d4d342a27b020d22959dc6", size = 459721, upload-time = "2024-09-04T20:44:01.585Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ff/6b/d45873c5e0242196f042d555526f92aa9e0c32355a1be1ff8c27f077fd37/cffi-1.17.1-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:610faea79c43e44c71e1ec53a554553fa22321b65fae24889706c0a84d4ad86d", size = 467242, upload-time = "2024-09-04T20:44:03.467Z" },
@@ -399,20 +454,123 @@ wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/75/b2/fbaec7c4455c604e29388d55599b99ebcc250a60050610fadde58932b7ee/cffi-1.17.1-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:706510fe141c86a69c8ddc029c7910003a17353970cff3b904ff0686a5927683", size = 478893, upload-time = "2024-09-04T20:44:33.606Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/4f/b7/6e4a2162178bf1935c336d4da8a9352cccab4d3a5d7914065490f08c0690/cffi-1.17.1-cp313-cp313-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl", hash = "sha256:de55b766c7aa2e2a3092c51e0483d700341182f08e67c63630d5b6f200bb28e5", size = 485810, upload-time = "2024-09-04T20:44:35.191Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c7/8a/1d0e4a9c26e54746dc08c2c6c037889124d4f59dffd853a659fa545f1b40/cffi-1.17.1-cp313-cp313-manylinux_2_17_s390x.manylinux2014_s390x.whl", hash = "sha256:c59d6e989d07460165cc5ad3c61f9fd8f1b4796eacbd81cee78957842b834af4", size = 471200, upload-time = "2024-09-04T20:44:36.743Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/26/9f/1aab65a6c0db35f43c4d1b4f580e8df53914310afc10ae0397d29d697af4/cffi-1.17.1-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:dd398dbc6773384a17fe0d3e7eeb8d1a21c2200473ee6806bb5e6a8e62bb73dd", size = 479447, upload-time = "2024-09-04T20:44:38.492Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5f/e4/fb8b3dd8dc0e98edf1135ff067ae070bb32ef9d509d6cb0f538cd6f7483f/cffi-1.17.1-cp313-cp313-musllinux_1_1_aarch64.whl", hash = "sha256:3edc8d958eb099c634dace3c7e16560ae474aa3803a5df240542b305d14e14ed", size = 484358, upload-time = "2024-09-04T20:44:40.046Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/f1/47/d7145bf2dc04684935d57d67dff9d6d795b2ba2796806bb109864be3a151/cffi-1.17.1-cp313-cp313-musllinux_1_1_x86_64.whl", hash = "sha256:72e72408cad3d5419375fc87d289076ee319835bdfa2caad331e377589aebba9", size = 488469, upload-time = "2024-09-04T20:44:41.616Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/bf/ee/f94057fa6426481d663b88637a9a10e859e492c73d0384514a17d78ee205/cffi-1.17.1-cp313-cp313-win32.whl", hash = "sha256:e03eab0a8677fa80d646b5ddece1cbeaf556c313dcfac435ba11f107ba117b5d", size = 172475, upload-time = "2024-09-04T20:44:43.733Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/7c/fc/6a8cb64e5f0324877d503c854da15d76c1e50eb722e320b15345c4d0c6de/cffi-1.17.1-cp313-cp313-win_amd64.whl", hash = "sha256:f6a16c31041f09ead72d69f583767292f750d24913dadacf5756b966aacb3f1a", size = 182009, upload-time = "2024-09-04T20:44:45.309Z" },
 ]
 
+[[package]]
+name = "cffi"
+version = "2.1.0"
+source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
+resolution-markers = [
+    "python_full_version >= '3.14' and sys_platform == 'darwin'",
+    "python_full_version >= '3.14' and platform_machine == 'aarch64' and sys_platform == 'linux'",
+    "(python_full_version >= '3.14' and platform_machine != 'aarch64' and sys_platform == 'linux') or (python_full_version >= '3.14' and sys_platform != 'darwin' and sys_platform != 'linux')",
+]
+dependencies = [
+    { name = "pycparser", marker = "python_full_version >= '3.14' and implementation_name != 'PyPy'" },
+]
+sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/57/5f/ff100cae70ebe9d8df1c01a00e510e45d9adb5c1fdda84791b199141de97/cffi-2.1.0.tar.gz", hash = "sha256:efc1cdd798b1aaf39b4610bba7aad28c9bea9b910f25c784ccf9ec1fa719d1f9", size = 531036, upload-time = "2026-07-06T21:34:30.382Z" }
+wheels = [
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d3/67/85c89a59ba36a671e79638f44d466749f08179266a57e4f2ffdf92174072/cffi-2.1.0-cp311-cp311-macosx_10_15_x86_64.whl", hash = "sha256:02cb7ff33ded4f1532476731f89ede53e2e488a8e6205515a82144246ffa7dcc", size = 183845, upload-time = "2026-07-06T21:32:26.32Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ea/dd/e3b0baa2d3d6a857ac72b7efbf18e32e487c9cdafcc13049ad765495b15e/cffi-2.1.0-cp311-cp311-macosx_11_0_arm64.whl", hash = "sha256:f5bce581e6b8c235e566a14768a943b172ada3ed73537bb0c0be1edee312d4e7", size = 184186, upload-time = "2026-07-06T21:32:28.025Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/65/68/9f3ef890cf3c6ab97bd531c5677f67613d302165d16f8142b2811782a614/cffi-2.1.0-cp311-cp311-manylinux1_i686.manylinux2014_i686.manylinux_2_17_i686.manylinux_2_5_i686.whl", hash = "sha256:30b65779d598c370374fefabf138d456fd6f3216bfa7bedfab1ba82025b0cd93", size = 211892, upload-time = "2026-07-06T21:32:29.565Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/22/d7/1a74539db16d8bfd839ff1515948948efbb162e574650fd3d846896eea95/cffi-2.1.0-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:88023dfe18799507b73f1dbb0d14326a17465de1bc9c9c7655c22845e9ddc3a2", size = 218793, upload-time = "2026-07-06T21:32:30.951Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ec/d1/9a5b7169499e8e8d8e636de70b97ac7c9447104d2ff1a2cd94790cea5162/cffi-2.1.0-cp311-cp311-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:0a96b74cda968eebbad56d973efe5098974f0a9fb323865bf99ea1fd24e3e64c", size = 205737, upload-time = "2026-07-06T21:32:32.216Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ba/b0/e131a9c41f10607926278453d9596163594fe1c4ebc46efe3b5e5b34eb84/cffi-2.1.0-cp311-cp311-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:a5781494d4d400a3f47f8f1da94b324f6e6b440a53387774002890a2a2f4b50f", size = 204909, upload-time = "2026-07-06T21:32:33.655Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/fb/d2/4398416cd699b35167947c6e22aca52c47e69ad5695073c9f1f2c52e04aa/cffi-2.1.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:aa7a1b53a2a4452ada2d1b5dade9960b2522f1e61293a811a077439e39029565", size = 217883, upload-time = "2026-07-06T21:32:35.173Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a2/a5/d4fe77b589e5e82d43ebc809bf2e6474afe8e48e32ea050b9357645b6471/cffi-2.1.0-cp311-cp311-musllinux_1_2_aarch64.whl", hash = "sha256:9d8272c0e483b024e1b9ad029821470ed8ec65631dbd90217469da0e7cd89f1c", size = 221251, upload-time = "2026-07-06T21:32:36.527Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/22/f0/a2fc43084c0433caf7f461bccc013e28f848d04ee1c5ed7fce71423cf4d9/cffi-2.1.0-cp311-cp311-musllinux_1_2_i686.whl", hash = "sha256:7762faa47e8ff7eb80bd261d9a7d8eea2d8baa69de5e95b70c1f338bbe712f02", size = 214250, upload-time = "2026-07-06T21:32:37.852Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/04/8c/b925975448cf20634a9fbd5efceb807219db452653648d2897c0989cab2d/cffi-2.1.0-cp311-cp311-musllinux_1_2_x86_64.whl", hash = "sha256:89095c1968b4ba8285840e131bf2891b09ae137fe2146905acae0354fbce1b5e", size = 219441, upload-time = "2026-07-06T21:32:39.146Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/eb/da/5c4918a2d61d86fa927d716cb3d8e4626ef8dc8f605a599d32f33897f59a/cffi-2.1.0-cp311-cp311-win32.whl", hash = "sha256:64c753a0f87a256020004f37a1c8c02c480e725f910f0b2a0f3f07debd1b2479", size = 174496, upload-time = "2026-07-06T21:32:40.467Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/f9/c8/6c2de1d55cf35ef8b92885d5ef280790f0fb9634d87ea1cc315176aecd61/cffi-2.1.0-cp311-cp311-win_amd64.whl", hash = "sha256:4f26194e3d95e06501b942642855aed4f953d55e95d7d01b7c4483db3ecff458", size = 185113, upload-time = "2026-07-06T21:32:41.761Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/9e/4e/e8d7cb5783f1841a3c8fb3a7735838d7484d08ec08c9f984b14cac1ac0e9/cffi-2.1.0-cp311-cp311-win_arm64.whl", hash = "sha256:35aaea0c7ee0e58a5cd8c2fd1a48fdf7ece0d2699b7ecdda08194e9ce5dd9b3d", size = 179927, upload-time = "2026-07-06T21:32:42.961Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/1e/85/990925db5df586ec90beb97529c853497e7f85ba0234830447faf41c3057/cffi-2.1.0-cp312-cp312-macosx_10_15_x86_64.whl", hash = "sha256:df2b82571a1b30f58a87bf4e5a9e78d2b1eff6c6ce8fd3aa3757221f93f0863f", size = 184829, upload-time = "2026-07-06T21:32:44.324Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/4b/92/e7bb136ad6b5352603732cf907ef862ca103f20f2031c1735a46300c20c9/cffi-2.1.0-cp312-cp312-macosx_11_0_arm64.whl", hash = "sha256:78474632761faa0fb96f30b1c928c84ebcf68713cbb80d15bab09dfe61640fde", size = 184728, upload-time = "2026-07-06T21:32:45.683Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c3/c0/d1ec30ffb370f748f2fb54425972bfef9871e0132e82fb589c46b6676049/cffi-2.1.0-cp312-cp312-manylinux1_i686.manylinux2014_i686.manylinux_2_17_i686.manylinux_2_5_i686.whl", hash = "sha256:5972433ad71a9e46516584ef60a0fda12d9dc459938d1539c3ddecf9bdc1368d", size = 214815, upload-time = "2026-07-06T21:32:48.557Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/1b/dc/5620cf930688be01f2d673804291de757a934c90b946dbdc3d84130c2ea4/cffi-2.1.0-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:b6422532152adf4e59b110cb2808cee7a033800952f5c036b4af047ee43199e7", size = 222429, upload-time = "2026-07-06T21:32:49.848Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/4b/a4/77b53abbf7a1e0beb9637edbef2a94d15f9c822f591e85d439ffd91519a6/cffi-2.1.0-cp312-cp312-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:46b1c8db8f6122420f32d02fffb924c2fe9bc772d228c7c711748fff56aabb2b", size = 210315, upload-time = "2026-07-06T21:32:51.221Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/58/0c/f528df19cc94b675087324d4760d9e6d5bfae97d6217aa4fac43de4f5fcc/cffi-2.1.0-cp312-cp312-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:d9fafc5aa2e2a39aaf7f8cc0c1f044a9b07fca12e558dca53a3cc5c654ad67a7", size = 208859, upload-time = "2026-07-06T21:32:52.512Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/62/f2/c9522a81c32132799a1972c39f5c5f8b4c8b9f00488a23feaa6c06f07741/cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:1e9f50d192a3e525b15a75ab5114e442d83d657b7ec29182a991bc9a88fd3a66", size = 221844, upload-time = "2026-07-06T21:32:53.704Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/6e/28/bd53988b9833e8f8ad539d26f4c07a6b3f6bcb1e9e02e7ca038250b3428d/cffi-2.1.0-cp312-cp312-musllinux_1_2_aarch64.whl", hash = "sha256:98fff996e983a36d3aa2eca83af40c5821202e7e6f32d13ae94e3d2286f10cfe", size = 225287, upload-time = "2026-07-06T21:32:54.907Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/79/99/0d0fd37f055224085f42bbb2c022d002e17dde4a97972822327b07d84101/cffi-2.1.0-cp312-cp312-musllinux_1_2_x86_64.whl", hash = "sha256:379de10ce1ba048b1448599d1b37b24caee16309d1ac98d3982fc997f768700b", size = 223681, upload-time = "2026-07-06T21:32:56.329Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b0/80/c138990aa2a70b1a269f6e06348729836d733d6f970867943f61d367f8cc/cffi-2.1.0-cp312-cp312-win32.whl", hash = "sha256:9b8f0f26ca4e7513c534d351eca551947d053fac438f2a04ac96d882909b0d3a", size = 175269, upload-time = "2026-07-06T21:32:57.777Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a8/eb/f636456ff21a83fc13c032b58cc5dde061691546ac79efa284b2989b7982/cffi-2.1.0-cp312-cp312-win_amd64.whl", hash = "sha256:c97f080ea627e2863524c5af3836e2270b5f5dfff1f104392b959f8df0c5d384", size = 185881, upload-time = "2026-07-06T21:32:59.253Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/dd/2c/400ea43e721727dca8a65c4521390e9196757caba4a45643acb2b63271b8/cffi-2.1.0-cp312-cp312-win_arm64.whl", hash = "sha256:6d194185eabd279f1c05ebe3504265ddfc5ad2b58d0714f7db9f01da592e9eb6", size = 180088, upload-time = "2026-07-06T21:33:02.278Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/96/88/a996879e2eeccb815f6e3a5967b12a308257412acec882039d386bd2aa7b/cffi-2.1.0-cp313-cp313-ios_13_0_arm64_iphoneos.whl", hash = "sha256:10537b1df4967ca26d21e5072d7d54188354483b91dc75058968d3f0cf13fbda", size = 194331, upload-time = "2026-07-06T21:33:03.697Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/58/85/7ae00d5c8dd6266f4e944c3db630f3c5c9a98b61d469c714d848b1d8138a/cffi-2.1.0-cp313-cp313-ios_13_0_arm64_iphonesimulator.whl", hash = "sha256:a95b05f9baf29b91171b3a8bd2020b028835243e7b0ff6bb23e2a3c228518b1b", size = 196966, upload-time = "2026-07-06T21:33:05.353Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/8c/e9/45c3a76ad8d43ad9261f4c95436da61128d3ca545d72b9612c0ab5be0b1c/cffi-2.1.0-cp313-cp313-macosx_10_15_x86_64.whl", hash = "sha256:15faec4adfff450819f3aee0e2e02c812de6edb88203aa58807955db2003472a", size = 184795, upload-time = "2026-07-06T21:33:06.699Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/84/4c/82f132cb4418ee6d953d982b19191e87e2a6372c8a4ce36e50b69d6ade4a/cffi-2.1.0-cp313-cp313-macosx_11_0_arm64.whl", hash = "sha256:716ff8ec22f20b4d988b12884086bcef0fc99737043e503f7a3935a6be99b1ea", size = 184746, upload-time = "2026-07-06T21:33:08.071Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a0/1c/4ed5a0e5bdca6cbc275556de3328dd1b76fd0c11cc13c88fe66d1d8715f2/cffi-2.1.0-cp313-cp313-manylinux1_i686.manylinux2014_i686.manylinux_2_17_i686.manylinux_2_5_i686.whl", hash = "sha256:63960549e4f8dc41e31accb97b975abaecfc44c03e396c093a6436763c2ea7db", size = 214747, upload-time = "2026-07-06T21:33:09.671Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/3a/a6/e879bb68cc23a2bc9ba8f4b7d8019f0c2694bad2ab6c4a3701d429439f58/cffi-2.1.0-cp313-cp313-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:ff067a8d8d880e7809e4ac88eb009bb848870115317b306666502ccad30b147f", size = 222392, upload-time = "2026-07-06T21:33:10.896Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/88/f6/01890cfd63c08f8eb96a8319b0443690197d240a8bd6346048cf7bde9190/cffi-2.1.0-cp313-cp313-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:3b926723c13eba9f81d2ef3820d63aeceec3b2d4639906047bf675cb8a7a500d", size = 210285, upload-time = "2026-07-06T21:33:12.251Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a6/cf/2b684132056f438567b61e19d690dd31cd0921ace051e0a458be6074369e/cffi-2.1.0-cp313-cp313-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:47ff3a8bfd8cb9da1af7524b965127095055654c177fcfc7578debcb015eecd0", size = 208801, upload-time = "2026-07-06T21:33:13.617Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/6f/08/f2e7d62c460faae0926f2d6e423694aa409ced3bc1fe2927a0a6e5f05416/cffi-2.1.0-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:799416bae98336e400981ff6e532d67d5c709cfb30afb79865a1315f94b0e224", size = 221808, upload-time = "2026-07-06T21:33:15.466Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/38/37/04f54b8e63a02f3d908332c9effbf8c366167c6f733ed8a3d4f79b7e2a1e/cffi-2.1.0-cp313-cp313-musllinux_1_2_aarch64.whl", hash = "sha256:961be50688f7fba2fa65f63712d3b9b341a22311f5253460ce933f52f0de1c8c", size = 225241, upload-time = "2026-07-06T21:33:16.869Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a9/d6/c72eecca433cd3e681c65ed313ab4835d9d4a379704d0f628a6a05f51c2e/cffi-2.1.0-cp313-cp313-musllinux_1_2_x86_64.whl", hash = "sha256:bf5c6cf48238b0eb4c086978c492ad1cbc22373fc5b2d7353b3a598ce6db887a", size = 223588, upload-time = "2026-07-06T21:33:18.239Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c6/4b/e706f67279140f92939da3475ad610df18bfd52d50f14953a8e5fede71d5/cffi-2.1.0-cp313-cp313-win32.whl", hash = "sha256:db3eb7d46527159a878ec3460e9d40615bc25ba337d477db681aea6e4f05c5d2", size = 175248, upload-time = "2026-07-06T21:33:19.799Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5a/47/59eb7975cb0e4ef0afa764ea945b29a5bb4537a9f771cb7d6c8a5dd74c95/cffi-2.1.0-cp313-cp313-win_amd64.whl", hash = "sha256:8e74a6135550c4748af665b1b1118b6aab33b1fc6a16f9aff630af107c3b4512", size = 185717, upload-time = "2026-07-06T21:33:21.47Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5a/af/34fee85c48f8d94efc8597bc09470c9dd274c145f1c12e0fbc6ab6d38d74/cffi-2.1.0-cp313-cp313-win_arm64.whl", hash = "sha256:2282cd5e38aa8accd03e99d1256af8411c84cdbee6a89d841b563fdbd1f3e50f", size = 180114, upload-time = "2026-07-06T21:33:22.515Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d8/f0/81478e482afa03f6d18dc8f2afb5edc45b3080853b634b5ed91961be0998/cffi-2.1.0-cp314-cp314-ios_13_0_arm64_iphoneos.whl", hash = "sha256:d2117334c3af3bdcb9a88522b844a2bdb5efdc4f71c6c822df55486ae1c3347a", size = 194142, upload-time = "2026-07-06T21:33:23.657Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/7d/95/8de304305cd9204974b0ca051b86d307cafca13aa575a0ef1b44d92c0d8c/cffi-2.1.0-cp314-cp314-ios_13_0_arm64_iphonesimulator.whl", hash = "sha256:702c436735fbe99d59ada02a1f65cfc0d31c0ee8b7290912f8fbc5cd1e4b16c3", size = 196819, upload-time = "2026-07-06T21:33:25.007Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/20/71/7c8372d30e42415602ed9f268f7cfd66f1b855fed881ecd168bcb45dbc0b/cffi-2.1.0-cp314-cp314-macosx_10_15_x86_64.whl", hash = "sha256:1ff3456eab0d889592d1936d6125bbfbc7ae4d3354a700f8bd80450a66445d4d", size = 184965, upload-time = "2026-07-06T21:33:26.605Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d6/5c/584e626835f0375c928176c04137c96927165cb8733cdb3150ec04e5ee5e/cffi-2.1.0-cp314-cp314-macosx_11_0_arm64.whl", hash = "sha256:c4165821e131d6d4ca444347c2b694e2311bcfa3fe5a861cc72968f28867beac", size = 184952, upload-time = "2026-07-06T21:33:27.823Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2e/d2/065fcae1c73979fac8e054462478d0ff8a29c40cdc2ed7ea5676a061df53/cffi-2.1.0-cp314-cp314-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:276f20fffd7b396e12516ba8edf9509210ac248cbbc5acbc39cd512f9f59ebe6", size = 222353, upload-time = "2026-07-06T21:33:29.178Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ed/a5/e8bbb1ce5b3ac2f53ad6a10bde44318a5a8d99d4f4a000d44a6e39aeb3e4/cffi-2.1.0-cp314-cp314-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:7d5980a3433d4b71a5e120f9dd551403d7824e31e2e67124fe2769c404c06913", size = 210051, upload-time = "2026-07-06T21:33:30.534Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/28/ed/c127d3ac36e899c965e3361357c3befacd6578c03f40125183e41c3b219e/cffi-2.1.0-cp314-cp314-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:6ca4919c6e4f89aa99c42510b42cf54596892c00b3f9077f6bdd1505e24b9c8d", size = 208630, upload-time = "2026-07-06T21:33:31.753Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/cc/d7/97d3136f81db489ec8d1d67748c110d6c994268fd7528014aa9f2b085e4e/cffi-2.1.0-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:d53d10f7da99ae46f7373b9150393e9c5eab9b224909982b43832668de4779f5", size = 221593, upload-time = "2026-07-06T21:33:33.044Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d3/27/93195977168ee63aed233a1a0993a2178798654d1f4bddcdd321d6fd3b21/cffi-2.1.0-cp314-cp314-musllinux_1_2_aarch64.whl", hash = "sha256:c351efb95e832a853a29361675f33a7ce53de1a109cd73fd47af0712213aa4ce", size = 225146, upload-time = "2026-07-06T21:33:34.224Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b3/c1/6dbd291ee2ae5a50a034aa057207081f545923bbf15dad4511e985aafff5/cffi-2.1.0-cp314-cp314-musllinux_1_2_x86_64.whl", hash = "sha256:dbf7c7a88e2bac086f06d14577332760bdeecc42bdec8ac4077f6260557d9326", size = 223240, upload-time = "2026-07-06T21:33:35.57Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/0f/6f/ade5ce9863a57992a6ea3d0d10d7e29b8749fc127204b3d493d667b2815f/cffi-2.1.0-cp314-cp314-win32.whl", hash = "sha256:1854b724d00f6654c742097d5387569021be12d3a0f770eae1df8f8acfcc6acd", size = 177723, upload-time = "2026-07-06T21:33:51.626Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/41/de/92b9eeed4ae4a21d6fd9b2a2c8505cbed573299902ea73981cc13f7ff62c/cffi-2.1.0-cp314-cp314-win_amd64.whl", hash = "sha256:1b96bfe2c4bd825681b7d311ad6d9b7280a091f43e8f63da5729638083cd3bfb", size = 187937, upload-time = "2026-07-06T21:33:53.403Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2e/1a/cc6ae6c2913a03aab8898eee57963cf1035b8df5872ed8b9115fcc7e2be8/cffi-2.1.0-cp314-cp314-win_arm64.whl", hash = "sha256:7d28dff1db6764108bc30788d85d61c876beff416d9a49cb9dd7c5a9f34f5804", size = 183001, upload-time = "2026-07-06T21:33:54.74Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/14/f0/134c00ce0779ec86dea2aa1aac69339c2741a8045072676763512363a2ea/cffi-2.1.0-cp314-cp314t-macosx_10_15_x86_64.whl", hash = "sha256:7ea6b3e2c4250ff1de21c630fe72d0f63eb95c2c32ffbf64a358cf4a8836d714", size = 188538, upload-time = "2026-07-06T21:33:36.792Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/50/d8/3b86aba791cb610d24e8a3e1b2cd529e71fa15096b04e4d4e360049d4a4c/cffi-2.1.0-cp314-cp314t-macosx_11_0_arm64.whl", hash = "sha256:6af371f3767faeffc6ac1ef57cdfd25844403e9d3f476c5537caee499de96376", size = 188230, upload-time = "2026-07-06T21:33:38.011Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/14/d0/117dcd9209255ad8571fbc8c92ef32593a1d294dcec91ddc4e4db50606f2/cffi-2.1.0-cp314-cp314t-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:eb4e8997a49aa2c08a3e43c9045d224448b8941d88e7ac163c7d383e560cbf98", size = 223899, upload-time = "2026-07-06T21:33:39.514Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b6/3d/f20f8b886b254e3ad10e15cd4186d3aed49f3e6a35ab37aab9f8f25f7c03/cffi-2.1.0-cp314-cp314t-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:bf01d8c84cbea96b944c73b22182e6c7c432b3475632b8111dbfdc95ddad6e13", size = 211652, upload-time = "2026-07-06T21:33:40.851Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/28/3b/fad54de07260b93ddeef4b96d0131d57ea900675df1d410ae1deee52d7a6/cffi-2.1.0-cp314-cp314t-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:33eb1ad83ebe8f313e0df035c406227d55a79456704a863fad9842136af5ad7d", size = 210755, upload-time = "2026-07-06T21:33:42.183Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/cc/82/3d5c705acb7abbba9bbd7d79b8e62e0f25b6120eb7ae6ac49f1b721722fe/cffi-2.1.0-cp314-cp314t-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:ac0f1a2d0cfa7eea3f2aaf006ab6e70e8feeb16b75d65b7e5939982ca2f11056", size = 223933, upload-time = "2026-07-06T21:33:43.603Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/6c/d0/47e338384ab6b1004241002fa616301020cea4fc95f283506565d252f276/cffi-2.1.0-cp314-cp314t-musllinux_1_2_aarch64.whl", hash = "sha256:c16914df9fb7f500e440e6875fa23ff5e0b31db01fa9c06af98d59a91f0dc2e4", size = 226749, upload-time = "2026-07-06T21:33:45.046Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/70/25/65bd5b58ea4bfdfc15cde02cb5365f89ef8ab8b2adfb8fe5c4bd4233382f/cffi-2.1.0-cp314-cp314t-musllinux_1_2_x86_64.whl", hash = "sha256:5ecbd0499275d57506d397eebe1981cee87b47fcd9ef5c22cab7ed7644a39a94", size = 225703, upload-time = "2026-07-06T21:33:46.374Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/dc/78/aa01ac599a8a4322533d45a1f9bc93b338276d2d59dabbe7c6d92a775c81/cffi-2.1.0-cp314-cp314t-win32.whl", hash = "sha256:7d034dcffa09e9a46c93fa3a3be402096cb5354ac6e41ab8e5cc9cd8b642ad76", size = 182857, upload-time = "2026-07-06T21:33:47.696Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b9/26/d00496b22de4d4228f32dde94ad996f350c8aad676d63bcca0743c8dea4d/cffi-2.1.0-cp314-cp314t-win_amd64.whl", hash = "sha256:0582a58f3051372229ca8e7f5f589f9e5632678208d8636fea3676711fdf7fe5", size = 194065, upload-time = "2026-07-06T21:33:48.953Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d5/dd/0c7dbf815a579ff005008a2d815a55d6bb047c349eef536d9dc53d3f0a8d/cffi-2.1.0-cp314-cp314t-win_arm64.whl", hash = "sha256:510aeeeac94811b138077451da1fb18b308a5feab47dd2b603af55804155e1c8", size = 186404, upload-time = "2026-07-06T21:33:50.309Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/55/c7/8c8c50cb11c6750051daf12164098a9a6f027ac4356967fd4d800a07f242/cffi-2.1.0-cp315-cp315-ios_13_0_arm64_iphoneos.whl", hash = "sha256:2e9dabb9abcb7ad15938c7196ad5c1718a4e6d33cc79b4c0209bdb64c4a54a5c", size = 194121, upload-time = "2026-07-06T21:33:56.109Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/99/e2/67680bf19a6b60d2bb7ff83baefa2a4c3d2d7dc0f3277034b802e1fc504c/cffi-2.1.0-cp315-cp315-ios_13_0_arm64_iphonesimulator.whl", hash = "sha256:37f525a7e7e50c017fdebe58b787be310ad59357ae43a053943a6e1a6c526001", size = 196820, upload-time = "2026-07-06T21:33:57.288Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ed/da/4bbe583a3b3a5c8c60892124fe17f3fa3656523faf0d3484eae90f091853/cffi-2.1.0-cp315-cp315-macosx_10_15_x86_64.whl", hash = "sha256:95f2954c2c9473d892eca6e0409f3568b37ab62a8eedb122461f73cc273476e3", size = 184936, upload-time = "2026-07-06T21:33:58.765Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/e5/4b/1f4c36ab273980d7aa75bb126ea4f8971f24a96108acad3a0a084028c57b/cffi-2.1.0-cp315-cp315-macosx_11_0_arm64.whl", hash = "sha256:cdf2448aab5f661c9315308ec8b93f4e8a1a67a3c733f8631067a2b67d5913dc", size = 185045, upload-time = "2026-07-06T21:34:00.085Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ef/c3/ad299dc38f3583f8d916b299f028af418a9ec98bc695fcbebeae7420691c/cffi-2.1.0-cp315-cp315-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:90bec57cf82089383bd06a605b3eb8daebf7e5a668520beaf6e327a83a947699", size = 222342, upload-time = "2026-07-06T21:34:01.814Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/eb/d8/df4543cc087245044ed02ef3ad8e0a26619d0075ac7a77a12dc81177851b/cffi-2.1.0-cp315-cp315-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:6274dcb2d15cef48daa73ed1be5a40d501d74dccd0cd6db364776d12cb6ba022", size = 210073, upload-time = "2026-07-06T21:34:03.255Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2c/0e/fac738d73728c6cea2a88a2883dca54892496cbba88a1dc1f2909cb8a6f5/cffi-2.1.0-cp315-cp315-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:2b71d409cccee78310ab5dec549aed052aaea483346e282c7b02362596e01bb0", size = 208551, upload-time = "2026-07-06T21:34:04.433Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/e6/3f/0b04a700dd64f465c93020253a793a82c9b4dff9961f48facd0df945d9b8/cffi-2.1.0-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:7d3538f9c0e50670f4deb93dbb696576e60590369cae2faf7de681e597a8a1f1", size = 221649, upload-time = "2026-07-06T21:34:06.157Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5d/7c/b7379a5704c79eda57ce075869ba70a0368d1c850f803b3c0d078d39dcaf/cffi-2.1.0-cp315-cp315-musllinux_1_2_aarch64.whl", hash = "sha256:8f9ec95b8a043d3dfbc74d9abc6f7baf524dd27a8dc160b0a32ff9cdab650c28", size = 225203, upload-time = "2026-07-06T21:34:07.489Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5a/02/d5e6c43ea85c41bda2a184a3418f195fe7cf602967a8d2b94e085b83deef/cffi-2.1.0-cp315-cp315-musllinux_1_2_x86_64.whl", hash = "sha256:af5e2915d41fe6c961694d7bfdc8562942638200f3ce2765dfb8b745cf997629", size = 223263, upload-time = "2026-07-06T21:34:08.712Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2c/d8/772b8259bf75749adffb1c546828978381fb516f60cf701f6c83daf60c85/cffi-2.1.0-cp315-cp315-win32.whl", hash = "sha256:0a42c688d19fca6e095a53c6a6e2295a5b050a8b289f109adab02a9e61a25de6", size = 177696, upload-time = "2026-07-06T21:34:26.355Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/2f/dd/afa2191fc6d57fedd26e5844a2fe2fcc0bbfa00961bbaa5a41e4921e7cca/cffi-2.1.0-cp315-cp315-win_amd64.whl", hash = "sha256:bccbbb5ee76a61f9d99b5bf3846a51d7fca4b6a732fe46f89295610edaf41853", size = 187914, upload-time = "2026-07-06T21:34:27.58Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/05/ef/6cd4f8c671517162379dc79cfae5aea9106bc38abb89628d5c16adf6a838/cffi-2.1.0-cp315-cp315-win_arm64.whl", hash = "sha256:8d35c139744adb3e727cd51b1a18324bbe44b8bd41bf8322bca4d41289f48eda", size = 183004, upload-time = "2026-07-06T21:34:28.905Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/11/b6/12fc55092817a5faa26fb8c40c7f9d662e11a46ee248c137aafc42517d92/cffi-2.1.0-cp315-cp315t-macosx_10_15_x86_64.whl", hash = "sha256:f9912624a0c0b834b7520d7769b3644453aabc0a7e1c839da7359f050750e9bc", size = 188378, upload-time = "2026-07-06T21:34:09.926Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/8d/2e/cdac88979f295fde5daa69622c7d2111e56e7ceb94f211357fbe452339e4/cffi-2.1.0-cp315-cp315t-macosx_11_0_arm64.whl", hash = "sha256:df92f2aba50eb4d96718b68ef76f2e57a57b54f2fa62333496d16c6d585a85ca", size = 188319, upload-time = "2026-07-06T21:34:11.101Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/e0/27/1d0b408497e41a74795af122d7b603c418c5fed0171450f899afd04e594f/cffi-2.1.0-cp315-cp315t-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:0520e1f4c35f44e209cbbb421b67eec42e6a157f59444dfb6058874ff3610e5d", size = 223904, upload-time = "2026-07-06T21:34:12.606Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/8b/31/e115c985105dd7ffb32444505f18ceb874bb42d992af05d5dced7ecf1980/cffi-2.1.0-cp315-cp315t-manylinux2014_ppc64le.manylinux_2_17_ppc64le.whl", hash = "sha256:3681e031db29958a7502f5c0c9d6bbc4c36cb20f7b104086fa642d1799631ff8", size = 211554, upload-time = "2026-07-06T21:34:13.987Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/5a/67/9e6e09409336d9e515c58367e7cfcf4f89df06ad25252675595a58eb59d5/cffi-2.1.0-cp315-cp315t-manylinux2014_s390x.manylinux_2_17_s390x.whl", hash = "sha256:762f99479dcb369f60ab9017ad4ab97a36a1dd7c1ee5a3b15db0f4b8659120cd", size = 210795, upload-time = "2026-07-06T21:34:15.972Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/19/e5/d3cc82a4a0be7902af279c04181ad038449c096734464a5ae1de3e1401bd/cffi-2.1.0-cp315-cp315t-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:0611e7ebf90573a535ebdc33ae9da222d037853983e13359f580fab781ca017f", size = 223843, upload-time = "2026-07-06T21:34:17.509Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b9/65/b434abc97ce7cecc2c640fde160507c0ecc7e21544b483ba3325d2e2ea17/cffi-2.1.0-cp315-cp315t-musllinux_1_2_aarch64.whl", hash = "sha256:86cf8755a791f72c85dc287128cc62d4f24d392e3f1e15837245623f4a33cccc", size = 226773, upload-time = "2026-07-06T21:34:19.05Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/b5/9f/d4dc66ca651eb1145a133314cda721abf13cfac3d28c4a0402263ae6ad75/cffi-2.1.0-cp315-cp315t-musllinux_1_2_x86_64.whl", hash = "sha256:ba00f661f8ba35d075c937174e27c2c421cec3942fd2e0ea3e66996757c0fdd9", size = 225719, upload-time = "2026-07-06T21:34:20.576Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/68/5a/e536c528bc8057496c360c0978559a2dc45653f89dd6151078aa7d8fca1a/cffi-2.1.0-cp315-cp315t-win32.whl", hash = "sha256:cb96698e3c7413d906ce83f8ffd245ec1bd94707541f299d0ce4d6b0193e982b", size = 182760, upload-time = "2026-07-06T21:34:22.059Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d3/0b/0ffe8b82d3875bced5fa1e7986a7a46b748262a40ab7f60b475eb9fb1bb3/cffi-2.1.0-cp315-cp315t-win_amd64.whl", hash = "sha256:f146d154428a2523f9cc7936c02353c2459b8f6cf07d3cd1ee1c0a611109c5d5", size = 193769, upload-time = "2026-07-06T21:34:23.589Z" },
+    { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a0/17/1073b53b68c9b5ca6914adf5f8bf55aacc2d3be102418c90700160ea8605/cffi-2.1.0-cp315-cp315t-win_arm64.whl", hash = "sha256:cbb7640ce37159548d2147b5b8c241f962143d4c71231431820783f4dc78f210", size = 186405, upload-time = "2026-07-06T21:34:24.857Z" },
+]
+
 [[package]]
 name = "cfgv"
 version = "3.4.0"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/11/74/539e56497d9bd1d484fd863dd69cbbfa653cd2aa27abfe35653494d85e94/cfgv-3.4.0.tar.gz", hash = "sha256:e52591d4c5f5dead8e0f673fb16db7949d2cfb3f7da4582893288f0ded8fe560", size = 7114, upload-time = "2023-08-12T20:38:17.776Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/c5/55/51844dd50c4fc7a33b653bfaba4c2456f06955289ca770a5dbd5fd267374/cfgv-3.4.0-py2.py3-none-any.whl", hash = "sha256:b7265b1f29fd3316bfcd2b330d63d024f2bfd8bcb8b0272f8e19a504856c48f9", size = 7249, upload-time = "2023-08-12T20:38:16.269Z" },
 ]
 
 [[package]]
@@ -578,21 +736,22 @@ wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/ff/c0/91f1215d0d9f9f343e4773ba6c9b89e8c0cc7a64a6263f21139da639d848/contourpy-1.3.2-pp311-pypy311_pp73-macosx_10_15_x86_64.whl", hash = "sha256:5f5964cdad279256c084b69c3f412b7801e15356b16efa9d78aa974041903da0", size = 266807, upload-time = "2025-04-15T17:45:15.535Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/d4/79/6be7e90c955c0487e7712660d6cead01fa17bff98e0ea275737cc2bc8e71/contourpy-1.3.2-pp311-pypy311_pp73-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:49b65a95d642d4efa8f64ba12558fcb83407e58a2dfba9d796d77b63ccfcaff5", size = 318729, upload-time = "2025-04-15T17:45:20.166Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/87/68/7f46fb537958e87427d98a4074bcde4b67a70b04900cfc5ce29bc2f556c1/contourpy-1.3.2-pp311-pypy311_pp73-win_amd64.whl", hash = "sha256:8c5acb8dddb0752bf252e01a3035b21443158910ac16a3b0d20e7fed7d534ce5", size = 221791, upload-time = "2025-04-15T17:45:24.794Z" },
 ]
 
 [[package]]
 name = "cryptography"
 version = "45.0.7"
 source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }
 dependencies = [
-    { name = "cffi", marker = "platform_python_implementation != 'PyPy'" },
+    { name = "cffi", version = "1.17.1", source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }, marker = "python_full_version < '3.14' and platform_python_implementation != 'PyPy'" },
+    { name = "cffi", version = "2.1.0", source = { registry = "https://pypi.tuna.tsinghua.edu.cn/simple" }, marker = "python_full_version >= '3.14' and platform_python_implementation != 'PyPy'" },
 ]
 sdist = { url = "https://pypi.tuna.tsinghua.edu.cn/packages/a7/35/c495bffc2056f2dadb32434f1feedd79abde2a7f8363e1974afa9c33c7e2/cryptography-45.0.7.tar.gz", hash = "sha256:4b1654dfc64ea479c242508eb8c724044f1e964a47d1d1cacc5132292d851971", size = 744980, upload-time = "2025-09-01T11:15:03.146Z" }
 wheels = [
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/0c/91/925c0ac74362172ae4516000fe877912e33b5983df735ff290c653de4913/cryptography-45.0.7-cp311-abi3-macosx_10_9_universal2.whl", hash = "sha256:3be4f21c6245930688bd9e162829480de027f8bf962ede33d4f8ba7d67a00cee", size = 7041105, upload-time = "2025-09-01T11:13:59.684Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/fc/63/43641c5acce3a6105cf8bd5baeceeb1846bb63067d26dae3e5db59f1513a/cryptography-45.0.7-cp311-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.whl", hash = "sha256:67285f8a611b0ebc0857ced2081e30302909f571a46bfa7a3cc0ad303fe015c6", size = 4205799, upload-time = "2025-09-01T11:14:02.517Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/bc/29/c238dd9107f10bfde09a4d1c52fd38828b1aa353ced11f358b5dd2507d24/cryptography-45.0.7-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl", hash = "sha256:577470e39e60a6cd7780793202e63536026d9b8641de011ed9d8174da9ca5339", size = 4430504, upload-time = "2025-09-01T11:14:04.522Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/62/62/24203e7cbcc9bd7c94739428cd30680b18ae6b18377ae66075c8e4771b1b/cryptography-45.0.7-cp311-abi3-manylinux_2_28_aarch64.whl", hash = "sha256:4bd3e5c4b9682bc112d634f2c6ccc6736ed3635fc3319ac2bb11d768cc5a00d8", size = 4209542, upload-time = "2025-09-01T11:14:06.309Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/cd/e3/e7de4771a08620eef2389b86cd87a2c50326827dea5528feb70595439ce4/cryptography-45.0.7-cp311-abi3-manylinux_2_28_armv7l.manylinux_2_31_armv7l.whl", hash = "sha256:465ccac9d70115cd4de7186e60cfe989de73f7bb23e8a7aa45af18f7412e75bf", size = 3889244, upload-time = "2025-09-01T11:14:08.152Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/96/b8/bca71059e79a0bb2f8e4ec61d9c205fbe97876318566cde3b5092529faa9/cryptography-45.0.7-cp311-abi3-manylinux_2_28_x86_64.whl", hash = "sha256:16ede8a4f7929b4b7ff3642eba2bf79aa1d71f24ab6ee443935c0d269b6bc513", size = 4461975, upload-time = "2025-09-01T11:14:09.755Z" },
     { url = "https://pypi.tuna.tsinghua.edu.cn/packages/58/67/3f5b26937fe1218c40e95ef4ff8d23c8dc05aa950d54200cc7ea5fb58d28/cryptography-45.0.7-cp311-abi3-manylinux_2_34_aarch64.whl", hash = "sha256:8978132287a9d3ad6b54fcd1e08548033cc09dc6aacacb6c004c73c3eb5d3ac3", size = 4209082, upload-time = "2025-09-01T11:14:11.229Z" },
@@ -1421,20 +1580,21 @@ wheels = [
 
 [[package]]
 name = "mediacrawler"
 version = "0.1.0"
 source = { virtual = "." }
 dependencies = [
     { name = "aiofiles" },
     { name = "aiomysql" },
     { name = "aiosqlite" },
     { name = "alembic" },
+    { name = "argon2-cffi" },
     { name = "asyncmy" },
     { name = "chromadb" },
     { name = "cryptography" },
     { name = "fastapi" },
     { name = "httpx" },
     { name = "imageio-ffmpeg" },
     { name = "jieba" },
     { name = "matplotlib" },
     { name = "motor" },
     { name = "opencv-python" },
@@ -1460,20 +1620,21 @@ dependencies = [
     { name = "websockets" },
     { name = "wordcloud" },
 ]
 
 [package.metadata]
 requires-dist = [
     { name = "aiofiles", specifier = "~=23.2.1" },
     { name = "aiomysql", specifier = "==0.2.0" },
     { name = "aiosqlite", specifier = ">=0.21.0" },
     { name = "alembic", specifier = ">=1.16.5" },
+    { name = "argon2-cffi", specifier = ">=23.1.0,<26.0.0" },
     { name = "asyncmy", specifier = ">=0.2.10" },
     { name = "chromadb", specifier = ">=0.5.0" },
     { name = "cryptography", specifier = ">=45.0.7" },
     { name = "fastapi", specifier = "==0.110.2" },
     { name = "httpx", specifier = "==0.28.1" },
     { name = "imageio-ffmpeg", specifier = ">=0.6.0" },
     { name = "jieba", specifier = "==0.42.1" },
     { name = "matplotlib", specifier = "==3.9.0" },
     { name = "motor", specifier = ">=3.3.0" },
     { name = "opencv-python", specifier = ">=4.11.0.86" },
