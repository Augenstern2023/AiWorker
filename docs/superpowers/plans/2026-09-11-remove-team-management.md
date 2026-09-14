# 移除团队管理与多系统管理员契约实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 彻底删除团队管理页面、API、导航和专用权限，清理历史页面授权，并固定多系统管理员与最后管理员保护契约。

**架构：** 保留唯一的系统级用户、角色和导航管理入口，删除 `/review/users`、`/review/roles` 与 `/api/team/*` 的整个垂直切片。通过连续 Alembic 迁移恢复生产库已经使用但仓库缺失的 `0021_llm_local_api_keys` 节点，再用 `0022_remove_team_management` 清理持久化团队配置。

**技术栈：** FastAPI、Jinja2、原生 JavaScript、SQLAlchemy、Alembic、PostgreSQL、pytest、uv、pre-commit。

---

## 文件结构

- 创建 `system_database/alembic/versions/0021_llm_local_api_keys.py`：恢复生产库现存迁移节点，保证空库和现有库使用同一迁移图。
- 创建 `system_database/alembic/versions/0022_remove_team_management.py`：删除团队导航、页面授权和专用权限数据。
- 删除 `identity/team_api.py`：移除团队用户与角色 API。
- 删除 `tests/test_identity_team_management.py`：移除已删除功能的行为测试。
- 修改 `WebGUI.py`：取消团队 API router 挂载。
- 修改 `identity/permission_catalog.py`：删除团队权限、导航和项目负责人旧权限。
- 修改 `system_database/schema.py`：声明生产库现存的 `api_key_env_var` 列。
- 修改 `web/page_routes.py`、`web/page_router.py`、`web/page_access_catalog.py`：删除团队页面和绑定 API。
- 修改 `templates/admin/users.html`、`templates/admin/roles.html`：只保留系统管理模板。
- 修改 `static/js/admin/users.js`、`static/js/admin/roles.js`：只使用 `/api/admin/*`。
- 修改 `tests/test_navigation_catalog.py`、`tests/test_page_authorization.py`、`tests/test_page_access_service.py`、`tests/test_web_security.py`、`tests/test_webgui_ui_changes.py`：建立删除后的路由、权限与前端契约。
- 修改 `tests/test_system_database_alembic.py`：验证连续迁移与数据清理。
- 修改 `tests/test_identity_admin_api.py`：固定多管理员分配和最后管理员保护。
- 修改 `tests/test_postgres_identity_repository.py`：删除团队 router 依赖，保留对应的系统管理员账号管理覆盖。
- 修改 `README.md`、`improve/AiWorker UI优化PRD工程实施文档.md`、`improve/UI优化方案.md`、`improve/扩展实施方案.md`：移除当前产品说明中的团队入口。

### 任务 1：先锁定团队功能必须消失的契约

**文件：**
- 修改：`tests/test_navigation_catalog.py`
- 修改：`tests/test_page_authorization.py`
- 修改：`tests/test_web_security.py`
- 修改：`tests/test_page_access_service.py`

- [ ] **步骤 1：编写失败的目录与路由测试**

在 `tests/test_navigation_catalog.py` 增加：

```python
def test_team_management_routes_and_permissions_are_removed():
    removed_routes = {"/review/users", "/review/roles"}
    removed_permissions = {
        "page.team_management",
        "identity.user.read",
        "identity.user.create",
        "identity.user.manage_created",
        "identity.role.read",
        "identity.role.create",
        "identity.role.manage_created",
    }

    assert removed_routes.isdisjoint({item[0] for item in PAGE_ROUTES})
    assert removed_routes.isdisjoint({item["route"] for item in NAVIGATION_CATALOG})
    assert removed_permissions.isdisjoint(PERMISSIONS)
    assert all(
        item["required_permission"] not in removed_permissions
        for item in NAVIGATION_CATALOG
    )
```

在 `tests/test_page_authorization.py` 增加系统管理员访问两个旧页面均为 404 的断言；在 `tests/test_web_security.py` 增加生产 app 的 route 集合与所有 `/api/team/` 路径不相交的断言；从 `tests/test_page_access_service.py` 的共享 API 参数中删除团队 API 正向案例，并断言页面 API 目录没有团队路径。

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
uv run pytest tests/test_navigation_catalog.py tests/test_page_authorization.py tests/test_page_access_service.py tests/test_web_security.py -q
```

预期：FAIL；现有目录仍包含两个 `/review/*` 页面、团队权限与 `/api/team/*` 路由。

- [ ] **步骤 3：提交失败测试检查点**

```powershell
git add tests/test_navigation_catalog.py tests/test_page_authorization.py tests/test_page_access_service.py tests/test_web_security.py
git commit -m "test(identity): require removal of team management surface"
```

### 任务 2：删除团队后端、页面注册和权限目录

**文件：**
- 删除：`identity/team_api.py`
- 删除：`tests/test_identity_team_management.py`
- 修改：`WebGUI.py`
- 修改：`identity/permission_catalog.py`
- 修改：`web/page_routes.py`
- 修改：`web/page_router.py`
- 修改：`web/page_access_catalog.py`
- 修改：`tests/test_postgres_identity_repository.py`

- [ ] **步骤 1：删除团队 router 及其挂载**

从 `WebGUI.py` 删除：

```python
from identity.team_api import build_team_management_router
```

以及对应的 `app.include_router(build_team_management_router(...))`。删除 `identity/team_api.py` 和仅覆盖该 router 的 `tests/test_identity_team_management.py`。

- [ ] **步骤 2：删除页面和 API 目录项**

从 `PAGE_ROUTES`、`PAGE_TEMPLATES` 和 `_PAGE_API_ROUTES` 删除 `/review/users`、`/review/roles`，删除 `_TEAM_USERS`、`_TEAM_ROLES` 常量及全部 `/api/team/*` 注册，不添加重定向或 404 占位 handler。

- [ ] **步骤 3：删除权限和导航目录项**

从 `identity/permission_catalog.py` 的权限代码集合、标签映射、非委派权限集合、项目负责人默认权限和 `NAVIGATION_CATALOG` 中删除设计列出的七个权限以及 `navigation:team*` 三个导航项。运行静态搜索确保生产代码无引用：

```powershell
rg -n "page\.team_management|identity\.(user|role)\.(read|create|manage_created)|navigation:team|/api/team/|/review/(users|roles)|build_team_management_router" identity web WebGUI.py
```

预期：无匹配。

- [ ] **步骤 4：移除 PostgreSQL 团队测试依赖**

从 `tests/test_postgres_identity_repository.py` 删除 `build_team_management_router` 导入，将 `test_postgres_team_user_list_resolves_creator_with_core_query` 删除；账号列表和密码重置继续由 `tests/test_identity_admin_api.py::test_admin_can_update_user_roles_and_reset_password` 覆盖。

- [ ] **步骤 5：运行任务 1 测试验证通过**

```powershell
uv run pytest tests/test_navigation_catalog.py tests/test_page_authorization.py tests/test_page_access_service.py tests/test_web_security.py tests/test_postgres_identity_repository.py -q
```

预期：PASS，PostgreSQL 专用案例在未配置测试库时只允许按既有条件 skip。

- [ ] **步骤 6：提交后端删除**

```powershell
git add -A identity WebGUI.py web tests/test_identity_team_management.py tests/test_postgres_identity_repository.py tests/test_navigation_catalog.py tests/test_page_authorization.py tests/test_page_access_service.py tests/test_web_security.py
git commit -m "refactor(identity): remove team management backend"
```

### 任务 3：收敛系统管理前端

**文件：**
- 修改：`templates/admin/users.html`
- 修改：`templates/admin/roles.html`
- 修改：`static/js/admin/users.js`
- 修改：`static/js/admin/roles.js`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：编写失败的前端静态契约测试**

在 `tests/test_webgui_ui_changes.py` 读取四个资源文件并断言：

```python
for source in (users_template, roles_template, users_script, roles_script):
    assert "team_mode" not in source
    assert "teamMode" not in source
    assert "/api/team/" not in source

assert "/api/admin/users" in users_script
assert "/api/admin/roles" in roles_script
assert "页面授权" in roles_template
```

- [ ] **步骤 2：运行测试验证失败**

```powershell
uv run pytest tests/test_webgui_ui_changes.py -q
```

预期：FAIL；共享资源仍包含团队模式分支。

- [ ] **步骤 3：删除模板条件分支**

将两个模板固定为系统管理版本：用户管理显示筛选、部门、联系方式、在线情况与导出；角色管理显示筛选、说明、创建时间、导出和页面授权弹窗。

- [ ] **步骤 4：删除 JavaScript 条件分支**

将用户脚本固定到 `/api/admin/users`、`/api/admin/roles`、`/api/admin/departments`；将角色脚本固定到 `/api/admin/roles`、`/api/admin/permissions` 和页面授权 API。删除所有团队列映射、受限角色过滤与团队条件请求。

- [ ] **步骤 5：运行前端与页面测试**

```powershell
uv run pytest tests/test_webgui_ui_changes.py tests/test_page_authorization.py -q
```

预期：PASS。

- [ ] **步骤 6：提交前端收敛**

```powershell
git add templates/admin/users.html templates/admin/roles.html static/js/admin/users.js static/js/admin/roles.js tests/test_webgui_ui_changes.py
git commit -m "refactor(admin): remove team mode frontend branches"
```

### 任务 4：恢复迁移链并清理持久化团队配置

**文件：**
- 创建：`system_database/alembic/versions/0021_llm_local_api_keys.py`
- 创建：`system_database/alembic/versions/0022_remove_team_management.py`
- 修改：`system_database/schema.py`
- 修改：`tests/test_system_database_alembic.py`

- [ ] **步骤 1：编写失败的迁移链测试**

增加断言，要求 `llm_providers` 元数据包含不可为空、默认空字符串的 `api_key_env_var`；将测试库降级到 `0021_llm_local_api_keys` 后插入团队导航、两个页面的账号/角色授权、七个专用权限和角色权限，再升级到 head，断言：

```python
assert connection.execute(
    text("SELECT count(*) FROM navigation_items WHERE id LIKE 'navigation:team%'")
).scalar_one() == 0
assert connection.execute(
    text("SELECT count(*) FROM user_page_access WHERE route IN ('/review/users', '/review/roles')")
).scalar_one() == 0
assert connection.execute(
    text("SELECT count(*) FROM role_page_access WHERE route IN ('/review/users', '/review/roles')")
).scalar_one() == 0
assert connection.execute(
    text("SELECT count(*) FROM permissions WHERE code = ANY(:codes)"),
    {"codes": list(REMOVED_TEAM_PERMISSIONS)},
).scalar_one() == 0
```

同一测试插入一个非团队页面授权和普通用户，升级后断言两者仍存在。

- [ ] **步骤 2：运行迁移测试验证失败**

```powershell
uv run pytest tests/test_system_database_alembic.py -q
```

预期：FAIL；仓库尚不认识 `0021_llm_local_api_keys`，也没有清理迁移。

- [ ] **步骤 3：恢复 `0021_llm_local_api_keys`**

迁移以 `0020_user_presence` 为父节点，升级时为 `llm_providers` 增加：

```python
sa.Column(
    "api_key_env_var",
    sa.Text(),
    nullable=False,
    server_default=sa.text("''"),
)
```

降级时删除该列；同步更新 `system_database/schema.py`。该结构与当前生产库已验证的列定义一致，不重写生产库版本号。

- [ ] **步骤 4：实现 `0022_remove_team_management`**

设置 `down_revision = "0021_llm_local_api_keys"`。升级函数使用参数化 `op.execute(sa.text(...).bindparams(...))`，依次删除页面授权、子导航、父导航、角色权限、权限依赖和权限目录。降级恢复七个权限目录项、项目负责人内置角色权限和三个导航目录项，但不伪造无法恢复的账号/角色页面授权。

- [ ] **步骤 5：验证空库升级和清理升级**

使用专用测试数据库，禁止指向 `aiworker_system`：

```powershell
uv run alembic upgrade head
uv run pytest tests/test_system_database_alembic.py -q
```

预期：空库 head 为 `0022_remove_team_management`；迁移测试 PASS。当前生产库只在最终验收阶段升级，不在开发测试中修改。

- [ ] **步骤 6：提交迁移**

```powershell
git add system_database/schema.py system_database/alembic/versions/0021_llm_local_api_keys.py system_database/alembic/versions/0022_remove_team_management.py tests/test_system_database_alembic.py
git commit -m "feat(database): remove persisted team management grants"
```

### 任务 5：固定多系统管理员安全契约

**文件：**
- 修改：`tests/test_identity_admin_api.py`

- [ ] **步骤 1：增加现有行为的特征测试**

增加 API 测试：初始化第一个管理员，创建两个普通账户，分别通过 `PUT /api/admin/users/{user_id}/roles` 分配同一个 `system_admin` 角色，断言三个账户都保留该角色且均可登录。该测试记录既有正确行为，因此允许首次运行即通过。

```python
for user_id in (second_admin["id"], third_admin["id"]):
    response = client.put(
        f"/api/admin/users/{user_id}/roles",
        headers={"X-CSRF-Token": csrf_token},
        json={"role_ids": [system_admin["id"]]},
    )
    assert response.status_code == 200
    assert response.json()["role_codes"] == ["system_admin"]

assert repository.count_active_system_admins() == 3
```

- [ ] **步骤 2：扩展最后管理员保护测试**

保留现有“最后一个管理员不能被停用或移除角色”测试，并增加多管理员存在时允许停用其中一个、但继续阻止停用最终剩余管理员的断言。

- [ ] **步骤 3：运行管理员测试**

```powershell
uv run pytest tests/test_identity_admin_api.py tests/test_page_authorization.py -q
```

预期：PASS；若多管理员分配失败，只修改阻止分配的最小生产逻辑，不放宽最后管理员保护，也不将仅有 `"*"` 的账户视为管理员。

- [ ] **步骤 4：提交契约测试**

```powershell
git add tests/test_identity_admin_api.py
git commit -m "test(identity): guarantee multiple system administrators"
```

### 任务 6：清理当前文档引用并完成验证

**文件：**
- 修改：`README.md`
- 修改：`improve/AiWorker UI优化PRD工程实施文档.md`
- 修改：`improve/UI优化方案.md`
- 修改：`improve/扩展实施方案.md`

- [ ] **步骤 1：删除当前产品文档中的团队入口说明**

移除把「团队管理」「团队账号」「角色授权」描述为现存入口的章节，统一指向「系统管理 → 用户管理 / 角色管理 / 导航配置」。`docs/superpowers/` 下的历史规格与计划保留作为决策记录，不当作当前产品说明。

- [ ] **步骤 2：执行源码残留扫描**

```powershell
rg -n "page\.team_management|identity\.(user|role)\.(read|create|manage_created)|navigation:team|/api/team/|/review/(users|roles)|build_team_management_router|team_mode|teamMode" identity web templates static/js WebGUI.py README.md improve tests
```

预期：除迁移测试中的删除目标字符串外无运行时或当前文档匹配。

- [ ] **步骤 3：运行全部定向测试**

```powershell
uv run pytest tests/test_navigation_catalog.py tests/test_page_authorization.py tests/test_page_access_service.py tests/test_web_security.py tests/test_webgui_ui_changes.py tests/test_identity_admin_api.py tests/test_postgres_identity_repository.py tests/test_system_database_alembic.py -q
```

预期：PASS，无测试收集错误。

- [ ] **步骤 4：运行完整测试与提交前检查**

先让独立测试库升级到 head，再执行：

```powershell
uv run pytest tests/
uv run pre-commit run --all-files
git diff --check
```

预期：全部测试通过；仅允许仓库既有、明确记录的 skip；pre-commit 和 diff 检查退出码均为 0。

- [ ] **步骤 5：验证运行服务未被中断**

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in 36732, 41312 } | Select-Object ProcessId, CommandLine
```

预期：当前 8899、8900 进程仍存在；不对其执行停止、重启或热更新操作。

- [ ] **步骤 6：提交文档并检查分支**

```powershell
git add README.md improve
git commit -m "docs: remove team management entry points"
git status --short --branch
git log --oneline --decorate -8
```

预期：`codex/remove-team-management` 工作树干净，提交仅包含本设计范围内的变更。
