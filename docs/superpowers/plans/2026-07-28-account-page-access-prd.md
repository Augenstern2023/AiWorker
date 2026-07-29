# 账号页面访问配置 PRD 与实施计划

> **2026-07-29 规则变更（优先于本文其余历史内容）**：页面授权改为“角色默认授权 + 账号例外覆盖”，不再以 `user_page_access` 为唯一来源。`/profile` 始终开放；`system_admin` 全量开放。普通账号有效权限按“页面停用 → 账号明确拒绝 → 账号明确允许 → 已分配角色页面授权并集 → 拒绝”计算。
>
> 默认角色页面：`selection_operator` 为选品工作台、今日榜单、今日爆款、选题库、剧目检索下载；`creative_planner` 为策划项目及其动态子页；`screenwriter` 为编剧项目及其动态子页；`project_owner` 为角色权限、审计日志、数据管理、已下载剧目播放及五个选品页面。项目负责人不再默认拥有导航配置、负责项目、团队账号或角色授权页面。
>
> `role_page_access` 保存角色页面授权；`user_page_access` 保存账号三态例外（允许、拒绝、未配置即继承）。系统管理员维护账号例外；项目负责人可在角色权限页维护角色页面授权。授权变更必须在下一次请求同时反映到导航、页面 URL 和页面绑定 API，无需重新登录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消角色的页面访问限制，登录账号默认仅访问个人信息页；由系统管理员或项目负责人按账号配置其他页面及其页面功能的访问权。

**Architecture:** 以账号-页面白名单 `user_page_access` 作为唯一的普通页面授权来源。页面路由、左侧导航和页面绑定 API 共用同一个页面访问判定；角色保留业务数据范围和操作能力，不再决定页面是否可进入。系统管理员拥有全量页面访问与配置能力，`project_owner` 是全局项目负责人，拥有全部项目的查看和配置能力，并拥有导航配置权限。

**Tech Stack:** FastAPI、SQLAlchemy Core、PostgreSQL、Alembic、Jinja2、Bootstrap 5、原生 JavaScript、pytest。

## 全局约束

- 个人信息页 `/profile` 与 `/api/profile` 对所有有效登录会话开放，不进入页面白名单。
- 所有其他页面默认拒绝，页面白名单仅由系统管理员或 `project_owner` 配置。
- 白名单按静态页面路由授权；同一路由的多个历史导航项共用同一授权结果。
- 页面授权必须同时覆盖页面 HTML、导航可见性和页面绑定 API，禁止交付“能打开但不能操作”的页面。
- 角色中的 `page.*` 权限不再参与页面判断；首版保留历史角色数据，避免破坏既有账号和审计记录。
- `project_owner` 是全局项目负责人，必须可访问和配置全部项目；普通项目成员仍受项目成员、任务归属及数据范围校验。
- 页面全局停用优先于账号白名单；系统管理员仍可进入导航配置完成恢复操作。
- 后端写操作继续使用现有 CSRF 与审计机制。

---

## 一、产品需求

### 1. 用户与权限规则

| 对象 | 默认可访问 | 其他页面 | 项目范围 |
| --- | --- | --- | --- |
| 普通账号 | `/profile` | 仅账号白名单中已启用页面 | 沿用自身项目/任务范围 |
| 项目负责人（`project_owner`） | `/profile`、`/admin/navigation` | 自己被配置的页面 | 全部项目可查看、编辑、配置 |
| 系统管理员 | 全部页面 | 无需白名单 | 全部数据 |

1. 普通账号首次登录或访问根路径 `/` 时，固定跳转到 `/profile`。
2. 已配置页面显示在左侧导航；未配置页面不显示且直接访问返回 `403`。
3. 取消页面勾选后立即失效，现有会话不需要重新登录。
4. 系统管理员和项目负责人可以配置任意账号的页面白名单；项目负责人可配置系统页面入口，但不因此向被配置账号额外放开未绑定的敏感接口或跨项目数据。
5. 项目负责人访问项目列表、项目成员、项目版本及项目配置时，视为全部项目管理者，不要求其先成为每个项目成员。

### 2. 导航配置工作台

导航配置页调整为“页面清单 + 账号授权”工作台：

- 名称：只读显示。
- 图标：显示预览；点击图标选择按钮打开受限 Bootstrap Icons 选择面板，保存图标代码。
- 页面地址：只读显示。
- 页面状态：使用单个开关，开启为普通账号可被授权，关闭为普通账号一律不可访问。
- 排序：仅在同一父导航分组中有效，使用上移/下移图标按钮，不显示或手填排序数字。
- 账号授权：每行使用“人员授权”图标按钮，打开弹窗。

授权弹窗包含账号名搜索、账号名、显示名、角色、状态和“允许访问此页面”复选框。仅显示启用且未删除的普通账号；系统管理员不显示为可编辑项。点击保存后，页面显示“已保存”，列表状态立即回显。

### 3. 页面与接口能力目录

建立代码常量 `PAGE_ACCESS_CATALOG`，每一项包含：

```python
{
    "page_route": "/admin/users",
    "api_routes": (
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/users/export"),
        ("POST", "/api/admin/users"),
        ("PATCH", "/api/admin/users/{user_id}"),
        ("DELETE", "/api/admin/users/{user_id}"),
        ("POST", "/api/admin/users/{user_id}/reset-password"),
        ("PUT", "/api/admin/users/{user_id}/roles"),
    ),
}
```

目录至少覆盖全部 `PAGE_ROUTES` 中的业务页和管理页。接口归属规则如下：

| 页面 | 首批必须绑定的接口范围 |
| --- | --- |
| 用户管理 | `/api/admin/users*`，含导出、角色和密码管理 |
| 角色权限 | `/api/admin/roles*`、`/api/admin/permissions` |
| 组织架构 | `/api/admin/departments*`、`/api/admin/users/{user_id}/departments*` |
| 导航配置 | `/api/admin/navigation*`，仅系统管理员和项目负责人可配置 |
| 审计日志 | `/api/admin/audit*` |
| 调度中心 | `scheduler.api` 暴露的 `/api/admin/schedules*` 及运行、告警接口 |
| 成果交付 | `creative.delivery` 暴露的 `/api/admin/deliveries*` |
| 系统后台任务 | `/api/jobs*`、`/api/logs*` 与该页使用的停止、重试接口 |
| 数据管理 | `/api/files*`、`/api/file/*`、`/api/media/*`、文件分析与操作接口 |
| 已下载剧目播放 | `/api/drama-player/*` |
| 选品、策划、编剧、项目管理页面 | 以 `creative`、`workflow`、抖音/红果检索及分析路由为页面归属，保留项目和任务范围校验 |

接口目录必须先由自动化测试覆盖，再替换原有角色页权限检查。未列入目录的 API 不会因任何页面勾选而开放，继续采用现有业务权限判断。

## 二、数据与接口契约

### 1. 数据表

新增 `user_page_access`：

```sql
CREATE TABLE user_page_access (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, route)
);
CREATE INDEX idx_user_page_access_route ON user_page_access(route);
```

`route` 只能由仓储层校验为 `PAGE_ROUTES` 中的静态地址。表中有行即允许；删除行即禁止。

### 2. 管理接口

| 方法与地址 | 请求 | 响应 | 权限 |
| --- | --- | --- | --- |
| `GET /api/admin/navigation` | 无 | 页面配置及已授权账号数 | 系统管理员或项目负责人 |
| `GET /api/admin/navigation/{item_id}/accounts` | 无 | 账号清单、当前是否允许、不可编辑原因 | 系统管理员或项目负责人 |
| `PUT /api/admin/navigation/{item_id}/accounts` | `{ "user_ids": ["..."] }` | 更新后的已授权账号 ID 列表 | 系统管理员或项目负责人 |
| `PATCH /api/admin/navigation/{item_id}` | 图标、启用状态或相邻排序操作 | 更新后的导航项 | 系统管理员或项目负责人 |

保存账号授权时采用全量替换语义：请求中的 `user_ids` 是该页面最终白名单。后端必须在一个事务中验证导航项、路由、用户存在性和账号状态，删除旧授权、写入新授权、记录审计事件。传入系统管理员、停用账号、已删除账号或未知用户 ID 返回 `422`，不写入任何记录。

### 3. 授权判定接口

新建 `PageAccessService`，公开以下接口：

```python
def can_open_page(principal: Principal, route: str) -> bool: ...
def can_call_page_api(principal: Principal, method: str, path: str) -> bool | None: ...
def pages_for(principal: Principal) -> frozenset[str]: ...
```

- `True`：页面或页面绑定 API 已授权。
- `False`：属于页面能力目录但账号无权访问。
- `None`：API 不属于页面能力目录，交回现有业务权限逻辑处理。

系统管理员对 `can_open_page()` 永远返回 `True`。`project_owner` 对 `/admin/navigation` 及其配置 API 返回 `True`；其他页面依赖账号白名单。`/profile` 不调用该服务。

## 三、实施任务

### Task 1：数据库白名单与仓储

**Files:**
- Modify: `system_database/schema.py`
- Create: `system_database/alembic/versions/0004_user_page_access.py`
- Modify: `web/navigation_repository.py`
- Create: `tests/test_user_page_access_repository.py`

**Interfaces:**
- Produces `NavigationRepository.list_page_access_user_ids(route) -> set[str]`。
- Produces `NavigationRepository.replace_page_access(route, user_ids) -> set[str]`。

- [ ] 编写迁移和仓储失败测试：未知路由、停用账号、系统管理员账号、未知账号必须拒绝，撤销账号后查询为空。
- [ ] 运行 `uv run pytest tests/test_user_page_access_repository.py -v`，确认失败原因是表和仓储接口不存在。
- [ ] 在 `schema.py` 定义 `user_page_access` 表、复合主键和路由索引；实现 Alembic 升降级迁移。
- [ ] 在 `NavigationRepository` 中使用单一事务实现全量替换，并通过 `users.status == 'active'`、`users.is_deleted == 0` 和系统管理员角色查询校验账号。
- [ ] 再次运行上述测试，确认全部通过。

### Task 2：页面能力目录与访问服务

**Files:**
- Create: `web/page_access_catalog.py`
- Create: `web/page_access_service.py`
- Create: `tests/test_page_access_service.py`

**Interfaces:**
- Consumes Task 1 的 `NavigationRepository` 白名单查询。
- Produces `PageAccessService.can_open_page()`、`can_call_page_api()`、`pages_for()`。

- [ ] 编写失败测试：普通账号只有 `/profile`；勾选 `/admin/users` 后只可访问该页及该页目录接口；系统管理员可访问全部页面；项目负责人可访问导航配置但未勾选的业务页仍拒绝。
- [ ] 运行 `uv run pytest tests/test_page_access_service.py -v`，确认失败原因是能力目录和服务不存在。
- [ ] 用 `PAGE_ROUTES` 定义页面项，并为每个已注册页面登记其使用的 API 方法与路径模板；路径模板匹配仅允许完整路径，禁止前缀误匹配。
- [ ] 实现访问服务。全局停用先拒绝普通账号；随后判断 `/profile`、系统管理员、项目负责人导航配置特权和账号白名单。
- [ ] 再次运行测试，确认通过。

### Task 3：页面路由、根路径与导航渲染

**Files:**
- Modify: `web/navigation_service.py`
- Modify: `web/page_router.py`
- Modify: `WebGUI.py`
- Modify: `tests/test_navigation_catalog.py`
- Modify: `tests/test_page_authorization.py`

**Interfaces:**
- Consumes Task 2 的 `PageAccessService`。
- Produces按账号过滤的 `NavigationService.items_for()` 和统一页面 `403` 响应。

- [ ] 编写失败测试：登录普通账号请求 `/` 重定向 `/profile`；未勾选页面的导航不可见且 URL 返回 `403`；勾选后导航和 URL 同时可用。
- [ ] 运行 `uv run pytest tests/test_navigation_catalog.py tests/test_page_authorization.py -v`，确认失败原因是仍使用 `page.*` 角色授权。
- [ ] 让 `NavigationService` 通过 `PageAccessService` 决定可见导航，不再读取 `principal.permissions` 中的 `page.*`。
- [ ] 将 `PAGE_ROUTES` 生成的页面端点由 `require_page()` 改为页面访问依赖；根路径忽略最近页面记录并固定重定向 `/profile`。
- [ ] 在 `WebGUI.py` 创建服务、挂入 `app.state`，并保证模板上下文使用同一服务实例。
- [ ] 再次运行测试，确认通过。

### Task 4：页面绑定 API 的统一拦截与项目负责人全局项目权

**Files:**
- Modify: `WebGUI.py`
- Modify: `identity/dependencies.py`
- Modify: `identity/permission_service.py`
- Modify: `identity/api.py`
- Modify: `identity/team_api.py`
- Modify: `organization/api.py`
- Modify: `audit/api.py`
- Modify: `scheduler/api.py`
- Modify: `creative/delivery.py`
- Create: `tests/test_page_api_authorization.py`
- Modify: `tests/test_identity_repository.py`

**Interfaces:**
- Consumes Task 2 的 `can_call_page_api()`。
- Produces页面绑定 API 的 `403` 与原有项目/任务数据范围的组合授权。

- [ ] 编写失败测试：未授权账号调用 `/api/admin/users` 返回 `403`；授权后可完成用户管理页 API；取消授权后立即再次返回 `403`；未归属接口仍使用原角色权限。
- [ ] 编写失败测试：`project_owner` 可访问和配置未加入成员表的任意项目；普通负责人或成员不可因此扩大范围。
- [ ] 运行 `uv run pytest tests/test_page_api_authorization.py tests/test_identity_repository.py -v`，确认失败原因是 API 未调用页面访问服务。
- [ ] 在安全中间件调用页面 API 服务：返回 `False` 时立即 `403`，返回 `True` 时允许继续执行，返回 `None` 时保留 `_api_permission()` 和各端点既有校验。
- [ ] 将管理 API 内仅依赖 `require_permission('*')` 的页面绑定端点改为页面授权依赖；保留 CSRF、审计、密码安全、删除保护及项目/任务范围校验。
- [ ] 明确 `project_owner` 全局范围：在 `PermissionService.can_access_assigned_project()`、`can_access_managed_project()` 和项目配置服务中统一以角色代码 `project_owner` 放行全部项目，而不改变普通 `owner` 的项目成员语义。
- [ ] 再次运行上述测试，确认通过。

### Task 5：导航配置管理 API

**Files:**
- Modify: `web/navigation_api.py`
- Modify: `web/navigation_repository.py`
- Create: `tests/test_navigation_account_access_api.py`

**Interfaces:**
- Consumes Task 1 白名单仓储和 Task 2 管理者访问服务。
- Produces账号授权清单与全量替换 API。

- [ ] 编写失败测试：系统管理员和 `project_owner` 可读取、保存账号授权；普通账号返回 `403`；无效账号请求返回 `422` 且白名单不改变；保存生成审计事件。
- [ ] 运行 `uv run pytest tests/test_navigation_account_access_api.py -v`，确认失败原因是接口不存在或仍只允许系统管理员。
- [ ] 为导航清单增加每页 `authorized_user_count`；新增账号清单和全量替换端点；通过 `AuditService` 记录变更前后账号 ID 集合。
- [ ] 将导航展示、图标、启用、排序配置权限改为“系统管理员或项目负责人”。
- [ ] 再次运行测试，确认通过。

### Task 6：导航配置交互整改

**Files:**
- Modify: `templates/admin/navigation.html`
- Modify: `static/js/admin/navigation.js`
- Create: `tests/test_navigation_configuration_page.py`

**Interfaces:**
- Consumes Task 5 的导航与账号授权 API。
- Produces图标选择、分组内上下排序、启用开关和账号授权弹窗。

- [ ] 编写页面测试：启用控件是单一开关而非文本框；图标字段显示预览和选择器；排序仅呈现上下按钮；账号弹窗保存提交全量 `user_ids`。
- [ ] 运行 `uv run pytest tests/test_navigation_configuration_page.py -v`，确认失败原因是现有表格仍渲染文本输入框和单行保存按钮。
- [ ] 重构模板为页面表格与授权弹窗，使用 Bootstrap Modal、原生复选框和 `bi-people`、`bi-arrow-up`、`bi-arrow-down`、`bi-palette` 图标按钮；所有图标按钮提供 `title` 与 `aria-label`。
- [ ] 重构 JavaScript：按父导航分组渲染、完成账号搜索、加载白名单、保存白名单、更新图标、切换启用和相邻排序；服务失败时保留弹窗状态并显示接口错误。
- [ ] 再次运行页面测试，确认通过。

### Task 7：全链路回归与迁移验证

**Files:**
- Modify: `tests/test_role_workflow_e2e.py`
- Modify: `tests/test_management_center_apis.py`
- Modify: `tests/test_drama_player.py`
- Create: `tests/test_account_page_access_e2e.py`

- [ ] 编写端到端测试：新账号登录后到 `/profile`；项目负责人配置“用户管理”和“已下载剧目播放”；目标账号完成对应页面操作；撤销后页面和 API 同时被拒绝。
- [ ] 编写回归测试：系统管理员不被全局停用页锁死，项目负责人访问全部项目，普通账号不能借页面授权读取未归属项目。
- [ ] 运行 `uv run pytest tests/test_account_page_access_e2e.py tests/test_management_center_apis.py tests/test_drama_player.py tests/test_role_workflow_e2e.py -v`，确认通过。
- [ ] 执行 `uv run alembic upgrade head` 于测试 PostgreSQL，确认迁移可重复执行且现有导航数据、角色数据和会话数据保留。
- [ ] 执行 `uv run pytest tests/ -q`；如外部 Redis 或 PostgreSQL 环境导致既有失败，单独记录失败项，不将其归因于本功能。

## 四、验收清单

- [ ] 新登录普通账号只看到个人信息页，根路径固定进入 `/profile`。
- [ ] 角色中是否含有 `page.selection`、`page.planning` 等不再改变页面可见性或 URL 访问结果。
- [ ] 系统管理员与项目负责人可进入导航配置，普通账号不能进入。
- [ ] 勾选页面后，目标账号的菜单、页面 URL 和页面操作接口同时可用。
- [ ] 取消勾选后，上述三处同时不可用。
- [ ] 项目负责人可访问和配置任意项目，普通账号仍受项目数据范围限制。
- [ ] 全局停用页面后，普通账号即使白名单已勾选也不可使用。
- [ ] 页面配置保存、账号授权保存和全局启停均产生可查询审计日志。
