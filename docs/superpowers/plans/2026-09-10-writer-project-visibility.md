# 编剧项目可见范围实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让普通账号的编剧项目列表和编剧页面只覆盖本人管理或被分配的项目，同时保留系统管理员、全局项目负责人查看全部项目的能力。

**架构：** 在 `PermissionService` 增加单一的“可参与项目”判定，项目列表过滤和编剧页面直访问校验共同调用它。前端只在编剧项目列表请求中启用 `participating_only=true`，策划及审核读取逻辑保持不变。

**技术栈：** FastAPI、SQLAlchemy、Jinja2、Alpine.js、pytest、Playwright

---

## 文件结构

- 修改 `identity/permission_service.py`：提供统一的项目参与范围判定。
- 修改 `creative/api.py`：让 `participating_only=true` 使用统一判定过滤项目。
- 修改 `web/page_router.py`：阻止普通账号直接进入范围外的编剧项目页面。
- 修改 `static/js/common/creative.js`：编剧项目页请求严格项目范围。
- 修改 `tests/test_identity_repository.py`：覆盖统一权限判定。
- 修改 `tests/test_original_project_api.py`：覆盖参与项目列表的接口行为。
- 修改 `tests/test_page_authorization.py`：覆盖编剧项目页面的直接访问校验。
- 修改 `tests/test_original_project_browser.py`：覆盖编剧与策划页面发出的不同列表请求。

### 任务 1：统一项目参与范围并用于列表过滤

**文件：**
- 修改：`identity/permission_service.py:147-197`
- 修改：`creative/api.py:2453-2520`
- 测试：`tests/test_identity_repository.py`
- 测试：`tests/test_original_project_api.py`

- [ ] **步骤 1：为统一权限判定编写失败测试**

在 `tests/test_identity_repository.py` 增加不依赖数据库的单元测试，明确“分配”和“管理”任一成立即可：

```python
def test_project_participation_accepts_assigned_or_managed_scope() -> None:
    permissions = object.__new__(PermissionService)
    permissions.can_access_assigned_project = (  # type: ignore[method-assign]
        lambda principal, project_id: project_id == "assigned"
    )
    permissions.can_access_managed_project = (  # type: ignore[method-assign]
        lambda principal, project_id: project_id == "managed"
    )
    principal = Principal(
        user_id="writer", username="writer", display_name="Writer",
        role_codes=("screenwriter",), permissions=frozenset(),
        data_scope="project", session_id="session",
    )

    assert permissions.can_participate_in_project(principal, "assigned")
    assert permissions.can_participate_in_project(principal, "managed")
    assert not permissions.can_participate_in_project(principal, "foreign")
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
uv run pytest tests/test_identity_repository.py::test_project_participation_accepts_assigned_or_managed_scope -q
```

预期：FAIL，`PermissionService` 尚无 `can_participate_in_project`。

- [ ] **步骤 3：实现最小统一判定**

在 `PermissionService` 中增加：

```python
def can_participate_in_project(
    self, principal: Principal, project_id: str
) -> bool:
    return self.can_access_assigned_project(
        principal, project_id
    ) or self.can_access_managed_project(principal, project_id)
```

现有 `can_access_assigned_project` 已保证系统管理员数据范围和 `project_owner` 全局角色直接通过，因此不复制特殊角色判断。

- [ ] **步骤 4：为参与项目列表编写失败测试**

在 `tests/test_original_project_api.py` 使用现有 `client` fixture 创建第二个普通编剧账号和另一个项目创建者；由另一个创建者创建“外部项目”，再通过 `client.workflow.add_project_member(...)` 仅将编剧加入一个项目。fixture 将 `identity` 和 `sessions` 暴露为 `client.identity`、`client.sessions`，测试用现有 `_client_for_user` 创建两个账号的客户端，然后调用：

```python
response = writer_client.get(
    "/api/creative/projects", params={"participating_only": "true"}
)

assert response.status_code == 200
assert [item["id"] for item in response.json()["items"]] == [assigned_project["id"]]
```

同一测试再用现有全局负责人 `client` 请求该参数，断言包含其没有成员记录的“外部项目”。普通编剧角色和会话沿用 fixture 中的 `IdentityRepository`、`SessionService` 与 `_client_for_user` helper，并在 finalizer 中删除新增测试账号及项目成员数据。

- [ ] **步骤 5：运行接口测试验证失败**

运行：

```powershell
uv run pytest tests/test_original_project_api.py::test_participating_project_list_only_returns_assigned_projects -q
```

预期：FAIL；当前 `participating_only` 只按成员记录处理，未通过统一权限判定表达系统管理员和全局项目负责人规则。

- [ ] **步骤 6：让列表过滤调用统一判定**

将 `creative/api.py` 的 `participating_only` 分支改为失败关闭，并统一使用权限服务：

```python
if participating_only:
    permission_service = getattr(
        request.app.state, "identity_permission_service", None
    )
    items = [
        item for item in items
        if isinstance(permission_service, PermissionService)
        and permission_service.can_participate_in_project(
            principal, str(item["id"])
        )
    ]
```

- [ ] **步骤 7：运行任务 1 测试确认通过**

运行：

```powershell
uv run pytest tests/test_identity_repository.py::test_project_participation_accepts_assigned_or_managed_scope tests/test_original_project_api.py::test_participating_project_list_only_returns_assigned_projects -q
```

预期：PASS。

- [ ] **步骤 8：提交任务 1**

```powershell
git add identity/permission_service.py creative/api.py tests/test_identity_repository.py tests/test_original_project_api.py
git commit -m "fix(projects): filter writer projects by participation"
```

### 任务 2：限制编剧项目直访问

**文件：**
- 修改：`web/page_router.py:75-105`
- 测试：`tests/test_page_authorization.py`

- [ ] **步骤 1：扩展页面测试权限替身并编写失败测试**

给 `FakeIdentityPermissionService` 增加可配置集合和统一判定：

```python
self.participating_project_ids: set[str] = set()

def can_participate_in_project(
    self, principal: Principal, project_id: str
) -> bool:
    return (
        principal.is_system_admin
        or "project_owner" in principal.role_codes
        or project_id in self.participating_project_ids
    )
```

新增直接访问测试：

```python
def test_writer_project_pages_require_project_participation(page_app):
    principal = _principal(roles=("screenwriter",))
    page_app[1].access_by_route["/writing/projects"] = {principal.user_id}
    client = _client(page_app, principal)

    assert client.get("/writing/projects/project-9/outlines").status_code == 403
    page_app[0].state.identity_permission_service.participating_project_ids.add(
        "project-9"
    )
    assert client.get("/writing/projects/project-9/outlines").status_code == 200
```

再参数化覆盖 Beats、scripts，并单独断言 `system_admin`、`project_owner` 无成员记录时仍通过。将现有 `test_managed_project_page_grant_keeps_project_preview_navigation_available` 拆清预期：策划预览页面仍允许打开；编剧详情页面只有把 `project-1` 加入 `participating_project_ids` 后才允许打开。

- [ ] **步骤 2：运行页面测试验证失败**

运行：

```powershell
uv run pytest tests/test_page_authorization.py::test_writer_project_pages_require_project_participation -q
```

预期：FAIL；当前页面路由只校验页面白名单。

- [ ] **步骤 3：在页面路由增加失败关闭的项目范围校验**

在 `require_page_access` 的页面白名单校验之后增加：

```python
project_id = request.path_params.get("project_id")
if project_id is not None and request.url.path.startswith("/writing/projects/"):
    service = getattr(request.app.state, "identity_permission_service", None)
    can_participate = getattr(service, "can_participate_in_project", None)
    if not callable(can_participate) or not can_participate(
        principal, str(project_id)
    ):
        raise HTTPException(status_code=403, detail="只能访问当前分配或管理的项目")
```

- [ ] **步骤 4：运行页面授权回归测试**

运行：

```powershell
uv run pytest tests/test_page_authorization.py -q
```

预期：PASS；策划与审核页面授权测试保持原行为。

- [ ] **步骤 5：提交任务 2**

```powershell
git add web/page_router.py tests/test_page_authorization.py
git commit -m "fix(writing): guard direct project page access"
```

### 任务 3：让编剧项目页请求严格列表范围

**文件：**
- 修改：`static/js/common/creative.js:183-210`
- 测试：`tests/test_original_project_browser.py`

- [ ] **步骤 1：编写浏览器失败测试**

在 `tests/test_original_project_browser.py` 增加 Playwright 测试，分别挂载 `writing-projects` 与 `planning-projects` 页面，记录 `aiworkerApi.request` URL：

```python
assert writing_urls[0] == "/api/creative/projects?participating_only=true"
assert planning_urls[0] == "/api/creative/projects"
```

测试替身对项目接口返回 `{"items": []}`，对选题接口返回 `{"items": []}`，然后调用 `await workspace.fetchCreativeProjects()`，断言真实请求行为而非源码字符串。

- [ ] **步骤 2：运行浏览器测试验证失败**

运行：

```powershell
uv run pytest tests/test_original_project_browser.py::test_writer_project_list_requests_participating_scope -q
```

预期：FAIL；两个页面目前都请求 `/api/creative/projects`。

- [ ] **步骤 3：按页面类型构造项目列表地址**

在 `fetchCreativeProjects` 中加入最小判断：

```javascript
const projectsUrl = document.body?.dataset.workspacePage === 'writing-projects'
    ? '/api/creative/projects?participating_only=true'
    : '/api/creative/projects';
```

并让项目请求使用 `projectsUrl`，不改变选题请求与其他工作空间逻辑。

- [ ] **步骤 4：运行浏览器和静态页面测试**

运行：

```powershell
uv run pytest tests/test_original_project_browser.py tests/test_screenwriter_dashboard.py tests/test_role_page_content.py -q
```

预期：PASS。

- [ ] **步骤 5：提交任务 3**

```powershell
git add static/js/common/creative.js tests/test_original_project_browser.py
git commit -m "fix(writing): request assigned project list"
```

### 任务 4：综合验证、部署与真实账号检查

**文件：**
- 不新增生产文件。

- [ ] **步骤 1：运行相关权限测试**

```powershell
uv run pytest tests/test_identity_repository.py tests/test_original_project_api.py tests/test_page_authorization.py tests/test_original_project_browser.py tests/test_screenwriter_dashboard.py -q
```

预期：全部 PASS 或仅因未配置隔离 PostgreSQL 而明确 SKIP，不允许失败。

- [ ] **步骤 2：运行全量测试与提交前检查**

```powershell
uv run pytest tests/ -q
uv run pre-commit run --files identity/permission_service.py creative/api.py web/page_router.py static/js/common/creative.js tests/test_identity_repository.py tests/test_original_project_api.py tests/test_page_authorization.py tests/test_original_project_browser.py
git diff --check
```

预期：命令退出码均为 0。

- [ ] **步骤 3：重启局域网服务并验证**

安全停止当前监听 `8899` 且命令行为本仓库 `WebGUI:app` 的进程，再以隐藏窗口启动：

```powershell
uv run uvicorn WebGUI:app --host 0.0.0.0 --port 8899
```

验证 `http://127.0.0.1:8899` 和 `http://192.168.1.34:8899` 均返回 HTTP 200。

- [ ] **步骤 4：核对工作区与提交范围**

```powershell
git status --short
git log -4 --oneline --decorate
```

预期：没有未提交源码修改，提交只包含本计划列出的权限、页面和测试文件。
