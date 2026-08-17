# 保留后台角色权限配置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让所有已存在内置角色的后台权限配置在初始化和服务重启后保持不变，同时保证新内置角色仍获得代码默认权限。

**架构：** 保留 `IdentityRepository.seed_defaults()` 对权限目录、权限依赖和角色元数据的同步职责，把默认角色权限的写入生命周期改为与默认页面授权一致：只在角色首次创建时写入。用真实 PostgreSQL 测试数据库执行两次初始化，验证第二次初始化不会覆盖后台配置。

**技术栈：** Python 3.11、SQLAlchemy、PostgreSQL、pytest、uv

---

## 文件结构

- 修改：`identity/repository.py` — 调整内置角色默认权限的初始化条件。
- 修改：`tests/test_postgres_identity_repository.py` — 增加生产数据库回归测试，并在测试结束后恢复测试角色权限。

### 任务 1：保留已有内置角色的后台权限

**文件：**
- 修改：`tests/test_postgres_identity_repository.py`
- 修改：`identity/repository.py:120-165`

- [ ] **步骤 1：编写失败的 PostgreSQL 回归测试**

在 `tests/test_postgres_identity_repository.py` 中新增测试。该测试要抓住的生产代码破坏是：`seed_defaults()` 对已存在内置角色执行 `DELETE role_permissions` 并重新写入默认权限。

```python
def test_seed_defaults_preserves_configured_permissions_for_existing_system_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
    if not postgres_url:
        pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required for PostgreSQL tests")
    if make_url(postgres_url).database == "aiworker_system":
        raise ValueError("tests must not run against the aiworker_system database")

    monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
    command.upgrade(Config("alembic.ini"), "head")
    database = SystemDatabase(SystemDatabaseSettings(postgres_url))
    repository = IdentityRepository(database)
    repository.seed_defaults()
    role = repository.get_role_by_code("screenwriter")
    assert role is not None
    original = repository.list_role_permissions(role["id"])
    configured = [{"permission_code": "topic.read", "data_scope": "project"}]

    try:
        repository.replace_role_permissions(
            role["id"], [("topic.read", "project")]
        )
        repository.seed_defaults()

        assert repository.list_role_permissions(role["id"]) == configured
    finally:
        repository.replace_role_permissions(
            role["id"],
            [
                (item["permission_code"], item["data_scope"])
                for item in original
            ],
        )
        database.dispose()
```

- [ ] **步骤 2：运行测试并确认因权限被重置而失败**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_postgres_identity_repository.py::test_seed_defaults_preserves_configured_permissions_for_existing_system_role -q
```

预期：FAIL；实际权限恢复为 `screenwriter` 的默认权限集合，而不是仅保留 `topic.read/project`。

- [ ] **步骤 3：编写最少实现**

在 `IdentityRepository.seed_defaults()` 中保留角色元数据 upsert 和 `stored_role_id` 查询，把默认权限插入与默认页面授权放进同一个 `if existing_role_id is None:` 分支：

```python
if existing_role_id is None:
    for permission_code, data_scope in self._expanded_permissions(
        role.permissions
    ):
        connection.execute(
            pg_insert(role_permissions)
            .values(
                role_id=stored_role_id,
                permission_code=permission_code,
                data_scope=data_scope,
            )
            .on_conflict_do_update(
                index_elements=["role_id", "permission_code"],
                set_={"data_scope": data_scope},
            )
        )
    for route in DEFAULT_ROLE_PAGE_ROUTES.get(role.code, ()):
        connection.execute(
            pg_insert(role_page_access)
            .values(role_id=stored_role_id, route=route)
            .on_conflict_do_nothing()
        )
```

删除已有角色分支中的 `delete(role_permissions)`，不增加迁移、强制重置参数或额外管理接口。

- [ ] **步骤 4：运行目标测试并确认通过**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_postgres_identity_repository.py::test_seed_defaults_preserves_configured_permissions_for_existing_system_role -q
```

预期：`1 passed`。

- [ ] **步骤 5：运行相关回归验证**

运行：

```powershell
$env:AIWORKER_SYSTEM_DATABASE_URL = $env:AIWORKER_TEST_SYSTEM_DATABASE_URL
uv run pytest tests/test_postgres_identity_repository.py -q
uv run pre-commit run --files identity/repository.py tests/test_postgres_identity_repository.py
```

预期：PostgreSQL 身份仓库测试全部通过；文件头和静态检查全部通过。

- [ ] **步骤 6：检查差异并提交实现**

```powershell
git diff --check -- identity/repository.py tests/test_postgres_identity_repository.py
git add -- identity/repository.py tests/test_postgres_identity_repository.py
git commit -m "fix: preserve configured role permissions on startup"
```

只暂存上述两个文件，保留工作区中其他未提交改动。
