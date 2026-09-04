# 项目负责人注册与多账号分配实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 允许多个账号拥有项目负责人角色，并允许用户在注册时申请该角色；系统管理员仍保持唯一保护。

**架构：** 注册接口仅接受一组明确允许的角色代码，项目负责人加入该集合。管理员用户创建和编辑继续走统一角色分配接口，不再将项目负责人作为专用接任角色拦截。系统管理员的存活校验不改动。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、PostgreSQL、pytest。

---

### 任务 1：允许项目负责人注册申请

**文件：**
- 修改：`tests/test_postgres_identity_repository.py`
- 修改：`identity/api.py`
- 修改：`templates/auth/register.html`

- [ ] **步骤 1：编写失败的注册申请测试**

```python
payload["role_codes"] = ["project_owner"]
response = client.post("/api/auth/register", json=payload)
assert response.status_code == 201
assert repository.list_users(username=username)[0]["role_codes"] == ["project_owner"]
assert repository.list_users(username=username)[0]["status"] == "disabled"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_postgres_identity_repository.py -k project_owner_registration -q`

预期：FAIL，接口返回 `422 所选角色不可注册`。

- [ ] **步骤 3：实现注册白名单和页面选项**

```python
REGISTRATION_ROLE_CODES = frozenset(
    {"selection_operator", "creative_planner", "screenwriter", "project_owner"}
)
```

在注册表单角色区域加入：

```html
<label class="auth-role-option"><input type="checkbox" name="role_codes" value="project_owner">项目负责人</label>
```

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_postgres_identity_repository.py -k project_owner_registration -q`

预期：PASS。

### 任务 2：允许管理员分配多个项目负责人

**文件：**
- 修改：`tests/test_identity_admin_api.py`
- 修改：`identity/api.py`

- [ ] **步骤 1：编写失败的管理员角色分配测试**

```python
response = admin_client.patch(
    f"/api/admin/users/{target_user_id}",
    json={"role_ids": [project_owner_role_id]},
)
assert response.status_code == 200
assert response.json()["role_codes"] == ["project_owner"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run pytest tests/test_identity_admin_api.py -k project_owner -q`

预期：FAIL，接口返回“项目负责人角色只能通过专用接任操作变更”。

- [ ] **步骤 3：移除项目负责人专用角色拦截**

```python
def _validate_role_ids(role_ids: list[str]) -> None:
    roles = {role["id"]: role for role in repository.list_roles()}
    if not set(role_ids) <= set(roles):
        raise HTTPException(status_code=422, detail="角色不存在")
```

保留系统管理员最后一个启用账号保护，且不将 `system_admin` 加入 `REGISTRATION_ROLE_CODES`。

- [ ] **步骤 4：运行测试验证通过**

运行：`uv run pytest tests/test_identity_admin_api.py -k project_owner -q`

预期：PASS。

### 任务 3：全量验证

**文件：**
- 修改：无

- [ ] **步骤 1：运行相关认证和管理测试**

运行：`uv run pytest tests/test_identity_admin_api.py tests/test_postgres_identity_repository.py -q`

预期：PASS。

- [ ] **步骤 2：运行静态检查**

运行：`uv run pre-commit run --files identity/api.py tests/test_identity_admin_api.py tests/test_postgres_identity_repository.py`

预期：所有检查通过。
